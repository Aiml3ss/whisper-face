"""Pure insertion-integrity contracts for platform adapters.

The module deliberately knows nothing about Accessibility, clipboard APIs, or
keyboard synthesis.  A platform adapter captures observations, performs the
single paste attempt, and reports what its readback could prove.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ReceiptState(str, Enum):
    VERIFIED = "verified"
    UNVERIFIABLE = "unverifiable"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class ReceiptReason(str, Enum):
    PENDING = "pending"
    COMMIT_VERIFIED = "commit_verified"
    COMMIT_VERIFIED_EDGE_WHITESPACE = "commit_verified_edge_whitespace"
    FOCUS_DRIFT = "focus_drift"
    SELECTION_DRIFT = "selection_drift"
    SURROUNDING_TEXT_DRIFT = "surrounding_text_drift"
    TARGET_UNREADABLE = "target_unreadable"
    READBACK_UNAVAILABLE = "readback_unavailable"
    READBACK_CONFLICT = "readback_conflict"
    PASTE_OUTCOME_UNKNOWN = "paste_outcome_unknown"


def fingerprint_surrounding(text: str) -> str:
    """Return a deterministic fingerprint without retaining nearby text."""
    if not isinstance(text, str):
        raise TypeError("surrounding text must be a string")
    return hashlib.sha256(
        b"whisper-face/insertion-lease/v1\0" + text.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class DestinationObservation:
    destination_id: str | None
    selection: tuple[int, int] | None
    surrounding_fingerprint: str | None

    @classmethod
    def capture(cls, destination_id: str | None,
                selection: tuple[int, int] | None,
                surrounding_text: str | None) -> "DestinationObservation":
        return cls(
            destination_id=destination_id,
            selection=selection,
            surrounding_fingerprint=(
                fingerprint_surrounding(surrounding_text)
                if surrounding_text is not None else None
            ),
        )


@dataclass(frozen=True)
class InsertionLease:
    utterance_id: str
    destination_id: str
    selection: tuple[int, int]
    surrounding_fingerprint: str
    opaque: bool = False

    @classmethod
    def capture(cls, utterance_id: str, destination_id: str,
                selection: tuple[int, int],
                surrounding_text: str) -> "InsertionLease":
        if not utterance_id:
            raise ValueError("utterance_id is required")
        if not destination_id:
            raise ValueError("destination_id is required")
        if len(selection) != 2 or selection[0] < 0 or selection[1] < 0:
            raise ValueError("selection must be a non-negative (start, length)")
        return cls(
            utterance_id,
            destination_id,
            (int(selection[0]), int(selection[1])),
            fingerprint_surrounding(surrounding_text),
        )

    @classmethod
    def capture_opaque(cls, utterance_id: str, destination_id: str,
                       destination_context: str = "") -> "InsertionLease":
        """Lease a field whose text/range is hidden by Accessibility."""
        if not utterance_id:
            raise ValueError("utterance_id is required")
        if not destination_id:
            raise ValueError("destination_id is required")
        return cls(
            utterance_id,
            destination_id,
            (0, 0),
            fingerprint_surrounding(destination_context),
            True,
        )


# How an observed destination differed from the expected one. These are
# categories, never content: a conflict has to be diagnosable without anyone
# reading the user's text. "unclassified" covers a caller that did not supply
# a shape, so an older caller stays valid.
READBACK_CONFLICT_SHAPES = frozenset({
    "unclassified",
    "observed-empty",          # the field read back as nothing at all
    "trailing-whitespace",     # equal once edge whitespace is ignored
    "internal-whitespace",     # equal once runs of whitespace collapse
    "unicode-form",            # equal under NFC normalization
    "expected-is-substring",   # the app kept text around what we inserted
    "observed-is-prefix",      # the field was still filling in: a timing loss
    "divergent",               # genuinely different content
})


@dataclass(frozen=True)
class ReadbackResult:
    """A terminal readback outcome, plus a content-free shape for conflicts.

    ``detail`` names *how* the observed field differed, drawn from
    ``READBACK_CONFLICT_SHAPES``. It never carries destination text, so a
    conflict can be diagnosed without a transcript. Empty for non-conflicts.
    """
    state: ReceiptState
    reason: ReceiptReason
    detail: str = ""

    @classmethod
    def verified(cls) -> "ReadbackResult":
        return cls(ReceiptState.VERIFIED, ReceiptReason.COMMIT_VERIFIED)

    @classmethod
    def verified_edge_whitespace(cls) -> "ReadbackResult":
        """Delivery proven where only leading/trailing whitespace differs.

        Some editors trim or add an edge newline when text arrives. Every
        character of the intended mutation is present and in order, so
        delivery is proven; the receipt keeps its own reason so the
        difference stays visible and is never mistaken for a byte-exact
        match. A wrong target, a partial paste, or reordered content cannot
        reach this state: they survive stripping and stay conflicts.
        """
        return cls(ReceiptState.VERIFIED,
                   ReceiptReason.COMMIT_VERIFIED_EDGE_WHITESPACE,
                   "trailing-whitespace")

    @classmethod
    def unverifiable(cls) -> "ReadbackResult":
        return cls(ReceiptState.UNVERIFIABLE,
                   ReceiptReason.READBACK_UNAVAILABLE)

    @classmethod
    def conflict(cls, detail: str = "unclassified") -> "ReadbackResult":
        if detail not in READBACK_CONFLICT_SHAPES:
            raise ValueError(f"unknown readback conflict shape: {detail}")
        return cls(ReceiptState.CONFLICT, ReceiptReason.READBACK_CONFLICT,
                   detail)


@dataclass(frozen=True)
class InsertionReceipt:
    utterance_id: str
    state: ReceiptState
    reason: ReceiptReason
    paste_attempted: bool


@dataclass(frozen=True)
class OutboxItem:
    """Persistable recovery payload; nearby destination text is never stored."""
    lease: InsertionLease
    text: str
    receipt: InsertionReceipt


@dataclass
class _OutboxEntry:
    lease: InsertionLease
    text: str
    receipt: InsertionReceipt
    terminal: bool = False
    in_flight: bool = False


class InsertionCoordinator:
    """Stage text, validate its destination, and permit one paste attempt."""

    def __init__(self, *, max_recoverable: int = 20,
                 max_tombstones: int = 256):
        self._entries: OrderedDict[str, _OutboxEntry] = OrderedDict()
        self._tombstones: OrderedDict[str, InsertionReceipt] = OrderedDict()
        self._max_recoverable = max(1, int(max_recoverable))
        self._max_tombstones = max(1, int(max_tombstones))
        self._lock = threading.RLock()

    def stage(self, lease: InsertionLease, text: str) -> InsertionReceipt:
        with self._lock:
            if (lease.utterance_id in self._entries
                    or lease.utterance_id in self._tombstones):
                raise ValueError(
                    f"utterance already staged: {lease.utterance_id}")
            receipt = InsertionReceipt(
                lease.utterance_id,
                ReceiptState.UNRESOLVED,
                ReceiptReason.PENDING,
                False,
            )
            self._entries[lease.utterance_id] = _OutboxEntry(
                lease, text, receipt)
            return receipt

    def commit(self, utterance_id: str,
               current: DestinationObservation,
               paste: Callable[[str], None],
               readback: Callable[[], ReadbackResult]) -> InsertionReceipt:
        with self._lock:
            tombstone = self._tombstones.get(utterance_id)
            if tombstone is not None:
                return tombstone
            entry = self._entries[utterance_id]
            if entry.terminal:
                return entry.receipt

            reason = self._validate(entry.lease, current)
            if reason is not None:
                state = (ReceiptState.UNVERIFIABLE
                         if reason == ReceiptReason.TARGET_UNREADABLE
                         else ReceiptState.CONFLICT)
                entry.receipt = InsertionReceipt(
                    utterance_id, state, reason, False)
                entry.terminal = True
                self._prune_recoverable_locked()
                return entry.receipt

            # Mark terminal before invoking platform code. A reentrant or
            # concurrent callback can therefore never create another attempt.
            entry.receipt = InsertionReceipt(
                utterance_id,
                ReceiptState.UNRESOLVED,
                ReceiptReason.PASTE_OUTCOME_UNKNOWN,
                True,
            )
            entry.terminal = True
            entry.in_flight = True
            text = entry.text

        # Platform callbacks run outside the coordinator lock. The entry is
        # already terminal, so concurrent/reentrant commits cannot repaste it,
        # while independent utterances are not serialized behind readback.
        receipt = InsertionReceipt(
            utterance_id,
            ReceiptState.UNRESOLVED,
            ReceiptReason.PASTE_OUTCOME_UNKNOWN,
            True,
        )
        try:
            paste(text)
            proof = readback()
            if not isinstance(proof, ReadbackResult):
                raise TypeError("readback must return ReadbackResult")
            receipt = InsertionReceipt(
                utterance_id, proof.state, proof.reason, True)
        except Exception:
            # Delivery may precede failure. Retrying risks duplication, so
            # the unresolved entry remains recoverable.
            pass
        with self._lock:
            entry = self._entries.get(utterance_id)
            if entry is None:  # Defensive: acknowledge refuses in-flight.
                return receipt
            entry.in_flight = False
            entry.receipt = receipt
            if receipt.state == ReceiptState.VERIFIED:
                self._entries.pop(utterance_id, None)
                self._remember_tombstone_locked(receipt)
            else:
                self._prune_recoverable_locked()
            return receipt

    def receipt(self, utterance_id: str) -> InsertionReceipt:
        with self._lock:
            entry = self._entries.get(utterance_id)
            if entry is not None:
                return entry.receipt
            return self._tombstones[utterance_id]

    def recoverable(self) -> tuple[OutboxItem, ...]:
        with self._lock:
            return tuple(
                OutboxItem(entry.lease, entry.text, entry.receipt)
                for entry in self._entries.values()
                if (entry.terminal and not entry.in_flight
                    and entry.receipt.state != ReceiptState.VERIFIED)
            )

    def recoverable_count(self) -> int:
        """Count recovery entries without constructing payload-bearing items."""
        with self._lock:
            return sum(
                1 for entry in self._entries.values()
                if (entry.terminal and not entry.in_flight
                    and entry.receipt.state != ReceiptState.VERIFIED)
            )

    def acknowledge(self, utterance_id: str) -> bool:
        """Dismiss one recoverable payload while retaining bounded dedupe."""
        with self._lock:
            entry = self._entries.get(utterance_id)
            if entry is None or entry.in_flight:
                return False
            self._entries.pop(utterance_id, None)
            self._remember_tombstone_locked(entry.receipt)
            return True

    def _remember_tombstone_locked(self, receipt: InsertionReceipt) -> None:
        self._tombstones[receipt.utterance_id] = receipt
        self._tombstones.move_to_end(receipt.utterance_id)
        while len(self._tombstones) > self._max_tombstones:
            self._tombstones.popitem(last=False)

    def _prune_recoverable_locked(self) -> None:
        terminal = [
            key for key, entry in self._entries.items()
            if entry.terminal and not entry.in_flight]
        while len(terminal) > self._max_recoverable:
            key = terminal.pop(0)
            expired = self._entries.pop(key)
            self._remember_tombstone_locked(expired.receipt)

    @staticmethod
    def _validate(lease: InsertionLease,
                  current: DestinationObservation) -> ReceiptReason | None:
        if lease.opaque:
            if (current.destination_id is None
                    or current.selection is None
                    or current.surrounding_fingerprint is None):
                return ReceiptReason.TARGET_UNREADABLE
            if current.destination_id != lease.destination_id:
                return ReceiptReason.FOCUS_DRIFT
            if current.surrounding_fingerprint \
                    != lease.surrounding_fingerprint:
                return ReceiptReason.SURROUNDING_TEXT_DRIFT
            return None
        if (current.destination_id is None
                or current.selection is None
                or current.surrounding_fingerprint is None):
            return ReceiptReason.TARGET_UNREADABLE
        if current.destination_id != lease.destination_id:
            return ReceiptReason.FOCUS_DRIFT
        if current.selection != lease.selection:
            return ReceiptReason.SELECTION_DRIFT
        if current.surrounding_fingerprint != lease.surrounding_fingerprint:
            return ReceiptReason.SURROUNDING_TEXT_DRIFT
        return None


__all__ = [
    "DestinationObservation",
    "InsertionCoordinator",
    "InsertionLease",
    "InsertionReceipt",
    "OutboxItem",
    "ReadbackResult",
    "ReceiptReason",
    "ReceiptState",
    "fingerprint_surrounding",
]
