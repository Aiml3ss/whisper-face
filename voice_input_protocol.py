"""Versioned, transcript-local Voice Input Protocol conformance foundation.

This module is deliberately in-process.  It defines no socket, IPC, SDK, or
platform automation transport.  Platform-facing commit behavior is exercised
through the pure contracts in :mod:`insertion_integrity`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from insertion_integrity import (
    DestinationObservation,
    InsertionCoordinator,
    InsertionLease,
    ReadbackResult,
    ReceiptReason,
    ReceiptState,
)


SCHEMA_VERSION = 1
EVIDENCE_SCOPE = "in-process-conformance-only"
MAX_TRANSCRIPT_CHARS = 100_000
_MESSAGE_KEYS = frozenset({
    "schema_version", "utterance_id", "sequence", "kind", "payload",
})


class ProtocolError(ValueError):
    """Raised when a message or transcript violates the v1 contract."""


class MessageKind(str, Enum):
    CAPTURE_PROPOSAL = "capture_proposal"
    STABLE_PREFIX = "stable_prefix"
    FINAL_TEXT = "final_text"
    COMMIT_RECEIPT = "commit_receipt"
    ACK_RECEIPT = "ack_receipt"
    CANCELLATION = "cancellation"


@dataclass(frozen=True)
class AdapterProfile:
    """One synthetic destination capability used by the conformance slice."""

    profile_id: str
    target: str
    paste: str
    readback: str

    @property
    def selection_bound(self) -> bool:
        return self.target == "readable"

    def capability_payload(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "target": self.target,
            "paste": self.paste,
            "readback": self.readback,
            "selection_bound": self.selection_bound,
            "evidence_scope": EVIDENCE_SCOPE,
        }


ADAPTER_PROFILES = (
    AdapterProfile("readable-complete", "readable", "available", "verified"),
    AdapterProfile(
        "readable-no-readback", "readable", "available", "unavailable"),
    AdapterProfile("opaque-reviewed", "opaque", "available", "unavailable"),
    AdapterProfile(
        "clipboard-unavailable", "readable", "unavailable", "verified"),
    AdapterProfile(
        "target-unavailable", "unavailable", "unavailable", "unavailable"),
)
_PROFILES_BY_ID = {profile.profile_id: profile for profile in ADAPTER_PROFILES}

_PAYLOAD_KEYS = {
    MessageKind.CAPTURE_PROPOSAL: frozenset({
        "profile_id", "target", "paste", "readback", "selection_bound",
        "evidence_scope",
    }),
    MessageKind.STABLE_PREFIX: frozenset({"text", "stable_through_ms"}),
    MessageKind.FINAL_TEXT: frozenset({"text"}),
    MessageKind.COMMIT_RECEIPT: frozenset({
        "state", "reason", "paste_attempted", "recoverable",
    }),
    MessageKind.ACK_RECEIPT: frozenset({
        "commit_sequence", "accepted", "outbox_dismissed",
    }),
    MessageKind.CANCELLATION: frozenset({"reason"}),
}
_CANCELLATION_REASONS = frozenset({
    "user_cancelled", "capture_failed", "superseded",
})
_RECEIPT_PAIRS = frozenset({
    (state.value, reason.value)
    for state, reasons in {
        ReceiptState.VERIFIED: (ReceiptReason.COMMIT_VERIFIED,),
        ReceiptState.UNVERIFIABLE: (
            ReceiptReason.TARGET_UNREADABLE,
            ReceiptReason.READBACK_UNAVAILABLE,
        ),
        ReceiptState.CONFLICT: (
            ReceiptReason.FOCUS_DRIFT,
            ReceiptReason.SELECTION_DRIFT,
            ReceiptReason.SURROUNDING_TEXT_DRIFT,
            ReceiptReason.READBACK_CONFLICT,
        ),
        ReceiptState.UNRESOLVED: (
            ReceiptReason.PASTE_OUTCOME_UNKNOWN,
        ),
    }.items()
    for reason in reasons
})


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isalnum()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _bounded_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= MAX_TRANSCRIPT_CHARS


def _plain_int(value: Any, *, maximum: int = 2_147_483_647) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )


def _validate_payload(kind: MessageKind, payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS[kind]:
        raise ProtocolError(f"invalid {kind.value} payload schema")

    if kind == MessageKind.CAPTURE_PROPOSAL:
        profile_id = payload["profile_id"]
        profile = (
            _PROFILES_BY_ID.get(profile_id)
            if isinstance(profile_id, str) else None
        )
        if profile is None or dict(payload) != profile.capability_payload():
            raise ProtocolError("unsupported destination capability")
    elif kind == MessageKind.STABLE_PREFIX:
        if (not _bounded_text(payload["text"])
                or not _plain_int(payload["stable_through_ms"])):
            raise ProtocolError("invalid stable prefix")
    elif kind == MessageKind.FINAL_TEXT:
        if not _bounded_text(payload["text"]):
            raise ProtocolError("invalid final text")
    elif kind == MessageKind.COMMIT_RECEIPT:
        pair = (payload["state"], payload["reason"])
        non_attempt_reasons = {
            ReceiptReason.FOCUS_DRIFT.value,
            ReceiptReason.SELECTION_DRIFT.value,
            ReceiptReason.SURROUNDING_TEXT_DRIFT.value,
            ReceiptReason.TARGET_UNREADABLE.value,
        }
        pair_is_text = all(isinstance(value, str) for value in pair)
        expected_attempt = (
            payload["reason"] not in non_attempt_reasons
            if pair_is_text else None
        )
        if (not pair_is_text
                or pair not in _RECEIPT_PAIRS
                or not isinstance(payload["paste_attempted"], bool)
                or payload["paste_attempted"] != expected_attempt
                or not isinstance(payload["recoverable"], bool)
                or payload["recoverable"] != (
                    payload["state"] != ReceiptState.VERIFIED.value)):
            raise ProtocolError("invalid commit receipt")
    elif kind == MessageKind.ACK_RECEIPT:
        if (not _plain_int(payload["commit_sequence"])
                or not isinstance(payload["accepted"], bool)
                or not isinstance(payload["outbox_dismissed"], bool)
                or payload["outbox_dismissed"] and not payload["accepted"]):
            raise ProtocolError("invalid acknowledgement receipt")
    elif (not isinstance(payload["reason"], str)
          or payload["reason"] not in _CANCELLATION_REASONS):
        raise ProtocolError("invalid cancellation reason")


@dataclass(frozen=True)
class ProtocolMessage:
    """One closed-schema v1 protocol message."""

    utterance_id: str
    sequence: int
    kind: MessageKind
    payload: Mapping[str, Any]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (not _plain_int(self.schema_version, maximum=SCHEMA_VERSION)
                or self.schema_version != SCHEMA_VERSION):
            raise ProtocolError("unsupported protocol schema version")
        if not _identifier(self.utterance_id):
            raise ProtocolError("invalid utterance id")
        if not _plain_int(self.sequence):
            raise ProtocolError("invalid message sequence")
        if not isinstance(self.kind, MessageKind):
            raise ProtocolError("invalid message kind")
        payload = dict(self.payload) if isinstance(self.payload, Mapping) else None
        if payload is None:
            raise ProtocolError("payload must be a mapping")
        _validate_payload(self.kind, payload)
        object.__setattr__(self, "payload", MappingProxyType(payload))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "utterance_id": self.utterance_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProtocolMessage":
        if not isinstance(value, Mapping) or set(value) != _MESSAGE_KEYS:
            raise ProtocolError("invalid protocol message schema")
        try:
            kind = MessageKind(value["kind"])
        except (TypeError, ValueError) as error:
            raise ProtocolError("invalid message kind") from error
        return cls(
            utterance_id=value["utterance_id"],
            sequence=value["sequence"],
            kind=kind,
            payload=value["payload"],
            schema_version=value["schema_version"],
        )


def validate_transcript(
    values: Iterable[ProtocolMessage | Mapping[str, Any]],
    *,
    require_terminal: bool = True,
) -> tuple[ProtocolMessage, ...]:
    """Validate message schemas, ordering, stable-prefix, and terminal state."""
    messages = tuple(
        value if isinstance(value, ProtocolMessage)
        else ProtocolMessage.from_mapping(value)
        for value in values
    )
    if not messages:
        raise ProtocolError("protocol transcript is empty")

    utterance_id = messages[0].utterance_id
    stable_prefix = ""
    stable_through_ms = 0
    final_text: str | None = None
    commit_sequence: int | None = None
    terminal = False
    for expected_sequence, message in enumerate(messages):
        if message.utterance_id != utterance_id:
            raise ProtocolError("protocol transcript mixes utterances")
        if message.sequence != expected_sequence:
            raise ProtocolError("protocol message sequence is not contiguous")
        if terminal:
            raise ProtocolError("message follows terminal protocol state")
        if expected_sequence == 0:
            if message.kind != MessageKind.CAPTURE_PROPOSAL:
                raise ProtocolError("capture proposal must be first")
            continue
        if message.kind == MessageKind.CAPTURE_PROPOSAL:
            raise ProtocolError("capture proposal may only appear once")
        if message.kind == MessageKind.STABLE_PREFIX:
            text = message.payload["text"]
            through = message.payload["stable_through_ms"]
            if final_text is not None or not text.startswith(stable_prefix):
                raise ProtocolError("stable prefix regressed or followed final text")
            if through < stable_through_ms:
                raise ProtocolError("stable prefix time regressed")
            stable_prefix = text
            stable_through_ms = through
        elif message.kind == MessageKind.FINAL_TEXT:
            if final_text is not None or not message.payload["text"].startswith(
                    stable_prefix):
                raise ProtocolError("final text invalidates the stable prefix")
            final_text = message.payload["text"]
        elif message.kind == MessageKind.COMMIT_RECEIPT:
            if final_text is None or commit_sequence is not None:
                raise ProtocolError("commit receipt requires one final text")
            commit_sequence = message.sequence
        elif message.kind == MessageKind.ACK_RECEIPT:
            if (commit_sequence is None
                    or message.payload["commit_sequence"] != commit_sequence):
                raise ProtocolError("acknowledgement does not bind the commit")
            terminal = True
        elif message.kind == MessageKind.CANCELLATION:
            if commit_sequence is not None:
                raise ProtocolError("a committed transcript cannot be cancelled")
            terminal = True

    if require_terminal and not terminal:
        raise ProtocolError("protocol transcript has no terminal receipt")
    return messages


class VoiceInputProtocolSession:
    """Generate one deterministic in-process conformance transcript."""

    def __init__(self, utterance_id: str, profile_id: str):
        if not _identifier(utterance_id):
            raise ProtocolError("invalid utterance id")
        try:
            self.profile = _PROFILES_BY_ID[profile_id]
        except (KeyError, TypeError) as error:
            raise ProtocolError("unknown adapter profile") from error
        self.utterance_id = utterance_id
        self._messages: list[ProtocolMessage] = []
        self._stable_prefix = ""
        self._stable_through_ms = 0
        self._final_text: str | None = None
        self._commit_message: ProtocolMessage | None = None
        self._ack_message: ProtocolMessage | None = None
        self._cancel_message: ProtocolMessage | None = None
        self._paste_attempts = 0
        self._pasted_text: list[str] = []
        self._coordinator = InsertionCoordinator()
        destination = f"vip-local.{profile_id}.{utterance_id}"
        if self.profile.target == "opaque":
            self._lease = InsertionLease.capture_opaque(
                utterance_id, destination, "synthetic-composer")
            self._current = DestinationObservation.capture(
                destination, (0, 0), "synthetic-composer")
        else:
            self._lease = InsertionLease.capture(
                utterance_id, destination, (0, 0), "synthetic-context")
            self._current = DestinationObservation.capture(
                destination, (0, 0), "synthetic-context")
        if self.profile.target == "unavailable":
            self._current = DestinationObservation.capture(None, None, None)

    @property
    def messages(self) -> tuple[ProtocolMessage, ...]:
        return tuple(self._messages)

    @property
    def paste_attempts(self) -> int:
        return self._paste_attempts

    @property
    def pasted_text(self) -> tuple[str, ...]:
        return tuple(self._pasted_text)

    def _emit(self, kind: MessageKind, payload: Mapping[str, Any]
              ) -> ProtocolMessage:
        message = ProtocolMessage(
            self.utterance_id, len(self._messages), kind, payload)
        self._messages.append(message)
        return message

    def capture_proposal(self) -> ProtocolMessage:
        if self._messages:
            if self._messages[0].kind == MessageKind.CAPTURE_PROPOSAL:
                return self._messages[0]
            raise ProtocolError("capture proposal must be first")
        return self._emit(
            MessageKind.CAPTURE_PROPOSAL, self.profile.capability_payload())

    def publish_stable_prefix(
        self, text: str, stable_through_ms: int,
    ) -> ProtocolMessage:
        self._require_open_capture()
        if (self._final_text is not None or not _bounded_text(text)
                or not text.startswith(self._stable_prefix)
                or not _plain_int(stable_through_ms)
                or stable_through_ms < self._stable_through_ms):
            raise ProtocolError("stable prefix regressed or followed final text")
        self._stable_prefix = text
        self._stable_through_ms = stable_through_ms
        return self._emit(MessageKind.STABLE_PREFIX, {
            "text": text,
            "stable_through_ms": stable_through_ms,
        })

    def publish_final_text(self, text: str) -> ProtocolMessage:
        self._require_open_capture()
        if (self._final_text is not None or not _bounded_text(text)
                or not text.startswith(self._stable_prefix)):
            raise ProtocolError("final text invalidates the stable prefix")
        self._final_text = text
        return self._emit(MessageKind.FINAL_TEXT, {"text": text})

    def commit(self) -> ProtocolMessage:
        if self._commit_message is not None:
            return self._commit_message
        self._require_open_capture()
        if self._final_text is None:
            raise ProtocolError("final text is required before commit")
        self._coordinator.stage(self._lease, self._final_text)

        def paste(text: str) -> None:
            self._paste_attempts += 1
            if self.profile.paste == "unavailable":
                raise RuntimeError("synthetic paste unavailable")
            self._pasted_text.append(text)

        def readback() -> ReadbackResult:
            if self.profile.readback == "unavailable":
                return ReadbackResult.unverifiable()
            return ReadbackResult.verified()

        receipt = self._coordinator.commit(
            self.utterance_id, self._current, paste, readback)
        self._commit_message = self._emit(MessageKind.COMMIT_RECEIPT, {
            "state": receipt.state.value,
            "reason": receipt.reason.value,
            "paste_attempted": receipt.paste_attempted,
            "recoverable": receipt.state != ReceiptState.VERIFIED,
        })
        return self._commit_message

    def acknowledge(self) -> ProtocolMessage:
        if self._ack_message is not None:
            return self._ack_message
        self._require_open_capture()
        if self._commit_message is None:
            raise ProtocolError("commit receipt is required before acknowledgement")
        dismissed = self._coordinator.acknowledge(self.utterance_id)
        self._ack_message = self._emit(MessageKind.ACK_RECEIPT, {
            "commit_sequence": self._commit_message.sequence,
            "accepted": True,
            "outbox_dismissed": dismissed,
        })
        return self._ack_message

    def cancel(self, reason: str = "user_cancelled") -> ProtocolMessage:
        if self._cancel_message is not None:
            return self._cancel_message
        self._require_open_capture()
        if self._commit_message is not None:
            raise ProtocolError("a committed transcript cannot be cancelled")
        self._cancel_message = self._emit(
            MessageKind.CANCELLATION, {"reason": reason})
        return self._cancel_message

    def _require_open_capture(self) -> None:
        if (not self._messages
                or self._messages[0].kind != MessageKind.CAPTURE_PROPOSAL):
            raise ProtocolError("capture proposal is required")
        if self._ack_message is not None or self._cancel_message is not None:
            raise ProtocolError("protocol transcript is terminal")


__all__ = [
    "ADAPTER_PROFILES",
    "EVIDENCE_SCOPE",
    "MAX_TRANSCRIPT_CHARS",
    "SCHEMA_VERSION",
    "AdapterProfile",
    "MessageKind",
    "ProtocolError",
    "ProtocolMessage",
    "VoiceInputProtocolSession",
    "validate_transcript",
]
