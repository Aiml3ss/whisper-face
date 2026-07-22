"""Small, durable queue for explicitly deferred voice payloads.

The inbox is a storage boundary, not an agent boundary.  It preserves payload
text locally and exposes it only through explicit item reads.  Receipts contain
an opaque item identifier and state, never payload text or payload-derived
metadata.  This module performs no execution, networking, subprocess, app,
clipboard, or dictation-runtime work.

One process should own a given inbox file.  Every mutation is written to a
same-directory temporary file and atomically replaced before in-memory state
is committed.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


SCHEMA_VERSION = 1
STATE_KIND = "whisper-face/voice-inbox"
RECEIPT_KIND = "whisper-face/voice-inbox-receipt"
MAX_ITEM_ID_CHARS = 128
MAX_PAYLOAD_CHARS = 100_000
MAX_ITEMS = 256

_ROOT_KEYS = frozenset({
    "schema_version", "kind", "next_sequence", "items",
})
_ITEM_KEYS = frozenset({
    "item_id", "source_id", "payload", "state", "sequence",
})


class InboxError(ValueError):
    """Base class for closed-boundary inbox failures."""


class InboxFormatError(InboxError):
    """Persisted state did not match the closed schema."""


class InboxConflictError(InboxError):
    """An item identifier was reused for different content or provenance."""


class InboxNotFoundError(InboxError):
    """The requested item does not exist."""


class InboxTransitionError(InboxError):
    """A terminal item was asked to enter a different terminal state."""


class InboxState(str, Enum):
    """Explicit lifecycle states for one deferred payload."""

    QUEUED = "queued"
    ACKNOWLEDGED = "acknowledged"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class InboxItem:
    """An explicit local read of one item, including its private payload."""

    item_id: str
    source_id: str
    payload: str
    state: InboxState
    sequence: int


@dataclass(frozen=True, slots=True)
class InboxReceipt:
    """Fixed, content-free evidence of an item's current state."""

    sequence: int
    state: InboxState

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": RECEIPT_KIND,
            "sequence": self.sequence,
            "state": self.state.value,
        }


def _plain_int(value: Any, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) \
        and value >= minimum


def _closed_mapping(value: Any, expected: frozenset[str],
                    label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise InboxFormatError(
            f"{label} must contain exactly {sorted(expected)!r}")
    return dict(value)


def _validate_identifier(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value
            or len(value) > MAX_ITEM_ID_CHARS
            or any(not (character.isalnum() or character in "._:-")
                   for character in value)):
        raise InboxError(
            f"{label} must be an opaque 1-128 character identifier")
    return value


def _validate_item_id(value: Any) -> str:
    return _validate_identifier(value, "item_id")


def _validate_payload(value: Any) -> str:
    if (not isinstance(value, str) or not value
            or len(value) > MAX_PAYLOAD_CHARS):
        raise InboxError(
            f"payload must be a non-empty string up to {MAX_PAYLOAD_CHARS} characters")
    return value


def _encode_state(items: Mapping[str, InboxItem],
                  next_sequence: int) -> str:
    body = {
        "schema_version": SCHEMA_VERSION,
        "kind": STATE_KIND,
        "next_sequence": next_sequence,
        "items": [
            {
                "item_id": item.item_id,
                "source_id": item.source_id,
                "payload": item.payload,
                "state": item.state.value,
                "sequence": item.sequence,
            }
            for item in sorted(items.values(), key=lambda entry: entry.sequence)
        ],
    }
    return json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n"


def _decode_state(text: str) -> tuple[dict[str, InboxItem], int]:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InboxFormatError("inbox state must be valid JSON") from exc

    root = _closed_mapping(raw, _ROOT_KEYS, "inbox state")
    if root["schema_version"] != SCHEMA_VERSION \
            or isinstance(root["schema_version"], bool):
        raise InboxFormatError("unsupported inbox schema version")
    if root["kind"] != STATE_KIND:
        raise InboxFormatError("unsupported inbox state kind")
    if not _plain_int(root["next_sequence"], minimum=1):
        raise InboxFormatError("next_sequence must be a positive integer")
    raw_items = root["items"]
    if not isinstance(raw_items, list):
        raise InboxFormatError("items must be a list")
    if len(raw_items) > MAX_ITEMS:
        raise InboxFormatError("inbox item limit exceeded")

    items: dict[str, InboxItem] = {}
    sequences: set[int] = set()
    for index, raw_item in enumerate(raw_items):
        data = _closed_mapping(raw_item, _ITEM_KEYS, f"items[{index}]")
        try:
            item_id = _validate_item_id(data["item_id"])
            source_id = _validate_identifier(data["source_id"], "source_id")
            payload = _validate_payload(data["payload"])
        except InboxError as exc:
            raise InboxFormatError(str(exc)) from exc
        try:
            state = InboxState(data["state"])
        except (TypeError, ValueError) as exc:
            raise InboxFormatError("item state is unsupported") from exc
        sequence = data["sequence"]
        if not _plain_int(sequence, minimum=1):
            raise InboxFormatError("item sequence must be a positive integer")
        if item_id in items:
            raise InboxFormatError("item identifiers must be unique")
        if sequence in sequences:
            raise InboxFormatError("item sequences must be unique")
        if sequence >= root["next_sequence"]:
            raise InboxFormatError("item sequence must precede next_sequence")
        items[item_id] = InboxItem(
            item_id, source_id, payload, state, sequence)
        sequences.add(sequence)
    return items, root["next_sequence"]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        descriptor_chmod = getattr(os, "fchmod", None)
        if descriptor_chmod is not None:
            descriptor_chmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


class VoiceInbox:
    """Single-owner local queue with durable explicit transitions."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = RLock()
        if self.path.exists():
            self._items, self._next_sequence = _decode_state(
                self.path.read_text(encoding="utf-8"))
        else:
            self._items = {}
            self._next_sequence = 1

    def enqueue(self, item_id: str, payload: str, *,
                source_id: str) -> InboxReceipt:
        """Persist one payload; an exact duplicate has no additional effect."""
        item_id = _validate_item_id(item_id)
        source_id = _validate_identifier(source_id, "source_id")
        payload = _validate_payload(payload)
        with self._lock:
            existing = self._items.get(item_id)
            if existing is not None:
                if (existing.payload != payload
                        or existing.source_id != source_id):
                    raise InboxConflictError(
                        "item_id is already bound to different content or "
                        "provenance")
                return InboxReceipt(existing.sequence, existing.state)
            if len(self._items) >= MAX_ITEMS:
                raise OverflowError(
                    "voice inbox is full; purge terminal items before enqueueing")
            item = InboxItem(
                item_id, source_id, payload, InboxState.QUEUED,
                self._next_sequence)
            proposed = dict(self._items)
            proposed[item_id] = item
            next_sequence = self._next_sequence + 1
            _atomic_write(
                self.path, _encode_state(proposed, next_sequence))
            self._items = proposed
            self._next_sequence = next_sequence
            return InboxReceipt(item.sequence, item.state)

    def ack(self, item_id: str) -> InboxReceipt:
        """Acknowledge one queued item; repeated acknowledgement is inert."""
        return self._transition(item_id, InboxState.ACKNOWLEDGED)

    def cancel(self, item_id: str) -> InboxReceipt:
        """Cancel one queued item; repeated cancellation is inert."""
        return self._transition(item_id, InboxState.CANCELLED)

    def _transition(self, item_id: str,
                    target: InboxState) -> InboxReceipt:
        item_id = _validate_item_id(item_id)
        with self._lock:
            existing = self._items.get(item_id)
            if existing is None:
                raise InboxNotFoundError("voice inbox item was not found")
            if existing.state == target:
                return InboxReceipt(existing.sequence, target)
            if existing.state != InboxState.QUEUED:
                raise InboxTransitionError(
                    "terminal voice inbox state cannot be changed")
            updated = InboxItem(
                existing.item_id, existing.source_id, existing.payload,
                target, existing.sequence)
            proposed = dict(self._items)
            proposed[item_id] = updated
            _atomic_write(
                self.path, _encode_state(proposed, self._next_sequence))
            self._items = proposed
            return InboxReceipt(updated.sequence, target)

    def purge_terminal(self) -> int:
        """Erase acknowledged/cancelled payloads and return the removed count."""
        with self._lock:
            proposed = {
                item_id: item for item_id, item in self._items.items()
                if item.state == InboxState.QUEUED
            }
            removed = len(self._items) - len(proposed)
            if not removed:
                return 0
            _atomic_write(
                self.path, _encode_state(proposed, self._next_sequence))
            self._items = proposed
            return removed

    def get(self, item_id: str) -> InboxItem:
        """Explicitly read one local item, including its private payload."""
        item_id = _validate_item_id(item_id)
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise InboxNotFoundError("voice inbox item was not found")
            return item

    def items(self, *, state: InboxState | None = None) -> tuple[InboxItem, ...]:
        """Explicitly inspect local items in stable enqueue order."""
        if state is not None and not isinstance(state, InboxState):
            raise TypeError("state must be an InboxState or None")
        with self._lock:
            return tuple(
                item for item in sorted(
                    self._items.values(), key=lambda entry: entry.sequence)
                if state is None or item.state == state
            )
