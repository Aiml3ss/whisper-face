"""Pure insertion-integrity contracts for platform adapters.

The module deliberately knows nothing about Accessibility, clipboard APIs, or
keyboard synthesis.  A platform adapter captures observations, performs the
single paste attempt, and reports what its readback could prove.
"""

from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
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
    COMMIT_VERIFIED_WHITESPACE_FORM = "commit_verified_whitespace_form"
    COMMIT_VERIFIED_POSITION = "commit_verified_position"
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

# What the field read back as, measured against the paste itself rather than
# the whole field: categories only, never text, a length or a position. The
# first two are proofs (the readback verified); the rest are conflicts.
READBACK_DIFFERENCES = frozenset({
    "edge-whitespace",   # verified: equal once edge whitespace is ignored
    "whitespace-form",   # verified: equal once no-break spaces and
                         # line-break forms are written one way
    "field-empty",       # the field read back as nothing, or only spaces
    "unchanged",         # the field still held what it held before
    "spacing",           # equal once runs of whitespace collapse
    "typographic",       # equal once quotes, dashes, ellipses and Unicode
                         # compatibility forms are folded
    "leading-part",      # the text around the insertion point is intact and
                         # only a leading part of the paste stands there
    "extra-at-place",    # the paste stands at the insertion point with
                         # something more beside it
    "inserted-alone",    # the paste is all the field holds: what was there
                         # before is gone
    "elsewhere",         # the paste is in the field, not where expected
    "absent",            # the paste is not in the field
})
# How the field behaved over the readback window that ended in a conflict.
# A readback returns on its first match, so a conflict never follows one.
READBACK_STAGES = frozenset({
    "no-change-seen",    # every read was the text from before the paste
    "still-changing",    # the last two reads differed when the window closed
    "settled",           # it changed, and held something else
})
# What a check by position saw: where the cursor of a field that hides its
# text went once the paste was sent. Never a length or an offset.
POSITION_CHECKS = frozenset({
    "moved-exactly",     # by the paste's length, the old selection replaced
    "moved-other",       # somewhere else
    "did-not-move",      # still where it was before the paste
    "unreadable-after",  # the range could not be read after the paste
})
# Whether a character count the element offers agrees with the paste.
POSITION_COUNTS = frozenset({
    "agrees",            # it changed by exactly what the paste should add
    "disagrees",         # it changed by something else, or stopped (or
                         # started) being offered
    "not-offered",       # the element offers no count
})

_LINE_BREAK_FORMS = re.compile("\r\n|[\r\u2028\u2029\x85]")
_WHITESPACE_RUN = re.compile(r"\s+")
_TYPOGRAPHIC_FORMS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u2032": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u201f": '"', "\u2033": '"', "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
})


def whitespace_form(text: str) -> str:
    """Write every line break as ``\\n`` and every no-break space as a space.

    These are the only substitutions that are the same text by construction:
    an editor may store a typed space as U+00A0 or a line break as CR LF,
    U+2028 or U+2029, and still hold exactly what was pasted. Nothing else
    (a tab, a thin space, a dropped or doubled space) is folded here.
    """
    return _LINE_BREAK_FORMS.sub("\n", text).replace("\u00a0", " ")


def same_but_whitespace_form(observed: str | None, expected: str) -> bool:
    """True when ``observed`` is ``expected`` in another whitespace form.

    Also true where the forms differ and, on top of that, only edge
    whitespace does (one trailing line break, say), which the edge rule
    proves on its own. Never true of an expectation that is all whitespace,
    so a field that reads as blank cannot pass.
    """
    if observed is None or not expected.strip():
        return False
    left, right = whitespace_form(observed), whitespace_form(expected)
    return left == right or left.strip() == right.strip()


def _collapsed(text: str) -> str:
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _typographic(text: str) -> str:
    return _collapsed(
        unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHIC_FORMS))


def readback_difference(before: str, selection: tuple[int, int],
                        inserted: str, observed: str) -> str:
    """Name how a conflicting readback differed from the paste, as a category.

    ``before`` and ``selection`` are the field and its selection when the
    paste was sent (code points), ``inserted`` what was pasted and
    ``observed`` the last value read. The answer is drawn from
    ``READBACK_DIFFERENCES`` and carries no text, length or position.
    """
    start, length = selection
    # Compared in one whitespace form throughout, so a paste that is short
    # or misplaced is named for that and not for its no-break spaces.
    prefix = whitespace_form(before[:start])
    suffix = whitespace_form(before[start + length:])
    before, inserted = whitespace_form(before), whitespace_form(inserted)
    observed = whitespace_form(observed)
    expected = prefix + inserted + suffix
    if not observed.strip():
        return "field-empty"
    if observed == before:
        return "unchanged"
    if _collapsed(observed) == _collapsed(expected):
        return "spacing"
    if _typographic(observed) == _typographic(expected):
        return "typographic"
    if (len(observed) >= len(prefix) + len(suffix)
            and observed.startswith(prefix) and observed.endswith(suffix)):
        middle = observed[len(prefix):len(observed) - len(suffix)]
        if middle and inserted.startswith(middle):
            return "leading-part"
        if inserted in middle:
            return "extra-at-place"
    if before.strip() and observed.strip() == inserted.strip():
        return "inserted-alone"
    if inserted.strip() and inserted.strip() in observed:
        return "elsewhere"
    return "absent"


def readback_stage(changed: bool, still_changing: bool) -> str:
    """Name how the field behaved over a readback window that conflicted."""
    if not changed:
        return "no-change-seen"
    return "still-changing" if still_changing else "settled"


def utf16_units(text: str) -> int:
    """Length as Accessibility counts it: UTF-16 code units, as NSString."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def position_check(before: tuple[int, int],
                   after: tuple[int, int] | None,
                   inserted_units: int) -> str:
    """Where a paste of ``inserted_units`` took a selection, as a category.

    Both ranges are ``(location, length)`` in UTF-16 units. A paste that
    replaced the selection leaves a bare cursor at the selection's start
    plus exactly what it inserted; nothing else is "moved-exactly".
    """
    if after is None:
        return "unreadable-after"
    start, _length = before
    if inserted_units > 0 and tuple(after) == (start + inserted_units, 0):
        return "moved-exactly"
    if tuple(after) == tuple(before):
        return "did-not-move"
    return "moved-other"


def position_count(before: int | None, after: int | None,
                   selection_units: int, inserted_units: int) -> str:
    """Whether a character count the element offers agrees with the paste."""
    if before is None and after is None:
        return "not-offered"
    if before is None or after is None:
        return "disagrees"
    agrees = after - before == inserted_units - selection_units
    return "agrees" if agrees else "disagrees"


@dataclass(frozen=True)
class ReadbackResult:
    """A terminal readback outcome, plus content-free detail.

    ``detail`` names *how* the observed field differed, drawn from
    ``READBACK_CONFLICT_SHAPES``. It never carries destination text, so a
    conflict can be diagnosed without a transcript. ``difference`` and
    ``stage`` say the same against the paste itself and over time
    (``READBACK_DIFFERENCES``, ``READBACK_STAGES``); ``position`` and
    ``position_count`` say what a check by position saw
    (``POSITION_CHECKS``, ``POSITION_COUNTS``). Each is empty when it does
    not apply.
    """
    state: ReceiptState
    reason: ReceiptReason
    detail: str = ""
    difference: str = ""
    stage: str = ""
    position: str = ""
    position_count: str = ""

    def __post_init__(self):
        for value, allowed in (
                (self.difference, READBACK_DIFFERENCES),
                (self.stage, READBACK_STAGES),
                (self.position, POSITION_CHECKS),
                (self.position_count, POSITION_COUNTS)):
            if value and value not in allowed:
                raise ValueError(f"unknown readback category: {value}")

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
                   "trailing-whitespace", difference="edge-whitespace")

    @classmethod
    def verified_whitespace_form(cls) -> "ReadbackResult":
        """Delivery proven where only the form of whitespace differs.

        A no-break space read back for a space, or a line break written as
        CR LF, U+2028 or U+2029, is the same text by construction (see
        ``whitespace_form``). Its own reason keeps it apart from a
        byte-exact match. A missing, extra or reordered character survives
        the folding and stays a conflict.
        """
        return cls(ReceiptState.VERIFIED,
                   ReceiptReason.COMMIT_VERIFIED_WHITESPACE_FORM,
                   difference="whitespace-form")

    @classmethod
    def verified_by_position(cls, count: str) -> "ReadbackResult":
        """A field that hides its text moved its cursor by exactly the paste.

        Proof that the right amount went in at the right place, not of which
        characters: weaker than reading the text back, and its own reason
        says so, so that nothing which needs the text (undo, a later rewrite
        of the destination, learning from corrections) treats it as read.
        """
        if count not in ("agrees", "not-offered"):
            raise ValueError("a disagreeing count is no proof")
        return cls(ReceiptState.VERIFIED,
                   ReceiptReason.COMMIT_VERIFIED_POSITION,
                   position="moved-exactly", position_count=count)

    @classmethod
    def unverifiable(cls, position: str = "",
                     position_count: str = "") -> "ReadbackResult":
        """No readback proved anything; a position check may say why not."""
        if position == "moved-exactly" and position_count != "disagrees":
            raise ValueError("an exact move with no disagreement is proof")
        return cls(ReceiptState.UNVERIFIABLE,
                   ReceiptReason.READBACK_UNAVAILABLE,
                   position=position, position_count=position_count)

    @classmethod
    def conflict(cls, detail: str = "unclassified", difference: str = "",
                 stage: str = "") -> "ReadbackResult":
        if detail not in READBACK_CONFLICT_SHAPES:
            raise ValueError(f"unknown readback conflict shape: {detail}")
        if difference in ("edge-whitespace", "whitespace-form"):
            raise ValueError("that difference is a proof, not a conflict")
        return cls(ReceiptState.CONFLICT, ReceiptReason.READBACK_CONFLICT,
                   detail, difference=difference, stage=stage)


@dataclass(frozen=True)
class InsertionReceipt:
    utterance_id: str
    state: ReceiptState
    reason: ReceiptReason
    paste_attempted: bool
    # The readback's shape for this utterance alone, so a record written
    # later can never pick up another utterance's. Empty when none ran.
    readback_shape: str = ""
    # The lease this receipt settles was opaque: the destination hid its
    # text when the dictation began, so no readback was ever possible.
    # False unless the coordinator says so, which is the careful reading.
    destination_opaque: bool = False
    # The readback's content-free detail for this utterance alone, as
    # ``ReadbackResult`` carries it. Empty when it does not apply.
    readback_difference: str = ""
    readback_stage: str = ""
    position_check: str = ""
    position_count: str = ""

    @property
    def checked_by_position(self) -> bool:
        """Verified by where the cursor went, not by reading the text.

        Enough to say the paste landed whole and in place, so it is shown
        and kept like any checked paste. Not enough for anything that needs
        the text itself: undo, rewriting the destination later, or learning
        from what the person changed there.
        """
        return (self.state == ReceiptState.VERIFIED
                and self.reason == ReceiptReason.COMMIT_VERIFIED_POSITION)

    @property
    def log_reason(self) -> str:
        """The reason, with its content-free detail, for a log line."""
        parts = [self.reason.value]
        if self.readback_difference:
            parts.append(f"difference {self.readback_difference}")
        if self.readback_stage:
            parts.append(f"stage {self.readback_stage}")
        if self.position_check:
            parts.append(f"position {self.position_check}")
        if self.position_count:
            parts.append(f"count {self.position_count}")
        return "; ".join(parts)

    @property
    def pasted_unchecked(self) -> bool:
        """The one paste was sent into a destination that hides its text.

        Not proof of delivery, and not a failure either: there was never
        anything to compare. It is never true of a destination that could
        be read when the dictation began and answered nothing after the
        paste (something changed there, and the person should look), of a
        readback that read different text (a conflict), of a paste whose
        own outcome is unknown, or of a paste that was never attempted;
        those stay problems.
        """
        return (self.paste_attempted
                and self.destination_opaque
                and self.state == ReceiptState.UNVERIFIABLE)


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
                destination_opaque=lease.opaque,
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
                    utterance_id, state, reason, False,
                    destination_opaque=entry.lease.opaque)
                entry.terminal = True
                self._settle_locked(utterance_id, entry)
                return entry.receipt

            # Mark terminal before invoking platform code. A reentrant or
            # concurrent callback can therefore never create another attempt.
            opaque = entry.lease.opaque
            entry.receipt = InsertionReceipt(
                utterance_id,
                ReceiptState.UNRESOLVED,
                ReceiptReason.PASTE_OUTCOME_UNKNOWN,
                True,
                destination_opaque=opaque,
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
            destination_opaque=opaque,
        )
        try:
            paste(text)
            proof = readback()
            if not isinstance(proof, ReadbackResult):
                raise TypeError("readback must return ReadbackResult")
            receipt = InsertionReceipt(
                utterance_id, proof.state, proof.reason, True, proof.detail,
                opaque,
                readback_difference=proof.difference,
                readback_stage=proof.stage,
                position_check=proof.position,
                position_count=proof.position_count)
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
            self._settle_locked(utterance_id, entry)
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

    def recoverable_counts(self) -> tuple[int, int]:
        """``(recoverable, needs a home)`` from one look at the queue.

        For a caller that words one line from both: read apart, an entry
        that settles between the two reads is counted as the wrong kind.
        """
        with self._lock:
            return self.recoverable_count(), self.needs_home_count()

    def needs_home_count(self) -> int:
        """Count the recoverable entries that are still looking for a home.

        Everything recoverable except a paste that was sent into a
        destination that hides its text: text that was never pasted, a
        paste whose outcome is unknown, a readback that read something
        else, and a readable destination that answered nothing after the
        paste. Like ``recoverable_count`` it builds no payload-bearing
        item.
        """
        with self._lock:
            return sum(
                1 for entry in self._entries.values()
                if (entry.terminal and not entry.in_flight
                    and entry.receipt.state != ReceiptState.VERIFIED
                    and not entry.receipt.pasted_unchecked)
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

    @staticmethod
    def validate(lease: InsertionLease,
                 current: DestinationObservation) -> ReceiptReason | None:
        """Why ``current`` no longer matches ``lease``, or None if it does.

        The exact check ``commit`` makes, for a caller that has to refuse
        before it stages anything.
        """
        return InsertionCoordinator._validate(lease, current)

    def _settle_locked(self, utterance_id: str, entry: _OutboxEntry) -> None:
        """Keep a terminal entry recoverable only when it holds text.

        A verified paste needs nothing more, and blank text gives the user
        nothing to recover, so both keep just their dedupe receipt.
        """
        if (entry.receipt.state == ReceiptState.VERIFIED
                or not entry.text.strip()):
            self._entries.pop(utterance_id, None)
            self._remember_tombstone_locked(entry.receipt)
        else:
            self._prune_recoverable_locked()

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
    "POSITION_CHECKS",
    "POSITION_COUNTS",
    "READBACK_DIFFERENCES",
    "READBACK_STAGES",
    "ReadbackResult",
    "ReceiptReason",
    "ReceiptState",
    "fingerprint_surrounding",
    "position_check",
    "position_count",
    "readback_difference",
    "readback_stage",
    "same_but_whitespace_form",
    "utf16_units",
    "whitespace_form",
]
