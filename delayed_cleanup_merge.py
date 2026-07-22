"""Conservative three-way merge for cleanup that finishes after insertion.

The module is deliberately pure.  It compares the text originally inserted,
the delayed cleanup proposal, and a fresh read of the destination.  It returns
a candidate string and an explainable receipt; it never reads from or writes
to an application.

Only proposal edits whose original span and local boundary anchor are still
unchanged may be applied.  User edits always win.  Ambiguous anchors and
reordered destination text fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from threading import Lock
from typing import Callable


class DelayedMergeReason(str, Enum):
    """Why one delayed cleanup edit was applied or rejected."""

    APPLIED = "applied"
    CURRENT_SPAN_TOUCHED = "current_span_touched"
    ANCHOR_CHANGED = "anchor_changed"
    AMBIGUOUS_ANCHOR = "ambiguous_anchor"
    DESTINATION_REORDERED = "destination_reordered"
    DESTINATION_OVERLAP = "destination_overlap"
    INSUFFICIENT_ANCHOR = "insufficient_anchor"


@dataclass(frozen=True)
class DelayedMergeDecision:
    """One edit derived from the original-to-proposal comparison."""

    original_start: int
    original_end: int
    replacement: str
    current_start: int | None
    current_end: int | None
    applied: bool
    reason: DelayedMergeReason


@dataclass(frozen=True)
class DelayedMergeReceipt:
    """Pure merge result and the evidence behind it."""

    merged_text: str
    applied_count: int
    rejected_count: int
    decisions: tuple[DelayedMergeDecision, ...]

    @property
    def changed(self) -> bool:
        return self.applied_count > 0


class DelayedApplyOutcome(str, Enum):
    """Fixed outcomes from the transactional destination boundary."""

    APPLIED = "applied"
    UNREADABLE_TARGET = "unreadable_target"
    FOCUS_DRIFT = "focus_drift"
    REVISION_DRIFT = "revision_drift"
    TEXT_DRIFT = "text_drift"
    AMBIGUOUS_MERGE = "ambiguous_merge"
    NO_SAFE_CHANGES = "no_safe_changes"
    COMPARE_AND_SWAP_REJECTED = "compare_and_swap_rejected"
    ADAPTER_EXCEPTION = "adapter_exception"
    PROPOSAL_IN_FLIGHT = "proposal_in_flight"


@dataclass(frozen=True)
class DestinationSnapshot:
    """Exact adapter-provided state for one focused destination."""

    destination_id: str
    revision: str
    text: str = field(repr=False)
    focused: bool = True


@dataclass(frozen=True)
class DelayedApplyReceipt:
    """Content-free, fixed-shape evidence for one apply attempt."""

    outcome: DelayedApplyOutcome
    applied: bool
    merge_applied_count: int = 0
    merge_rejected_count: int = 0


_AMBIGUOUS_MERGE_REASONS = frozenset({
    DelayedMergeReason.AMBIGUOUS_ANCHOR,
    DelayedMergeReason.DESTINATION_REORDERED,
    DelayedMergeReason.DESTINATION_OVERLAP,
    DelayedMergeReason.INSUFFICIENT_ANCHOR,
})


class DelayedCleanupTransactionAdapter:
    """Apply a delayed merge through an injected compare-and-swap boundary.

    ``read_snapshot`` must read the current focused destination without using
    the clipboard. ``apply_if_unchanged`` must atomically verify every field
    in the supplied snapshot before applying the replacement, and must leave
    the destination unchanged when it returns ``False`` or raises. This class
    does not read or write any application itself and makes no live-runtime
    claim.

    Proposal IDs are single-use for the lifetime of this adapter. A completed
    duplicate returns the original receipt without invoking either callback;
    a concurrent or re-entrant duplicate fails closed as in flight.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._in_flight: set[str] = set()
        self._receipts: dict[str, DelayedApplyReceipt] = {}

    @staticmethod
    def _valid_snapshot(value: object) -> bool:
        return (
            isinstance(value, DestinationSnapshot)
            and isinstance(value.destination_id, str)
            and bool(value.destination_id)
            and isinstance(value.revision, str)
            and bool(value.revision)
            and isinstance(value.text, str)
            and isinstance(value.focused, bool)
        )

    def apply(
            self,
            proposal_id: str,
            original: str,
            proposal: str,
            read_snapshot: Callable[[], DestinationSnapshot | None],
            apply_if_unchanged: Callable[
                [DestinationSnapshot, str], bool],
    ) -> DelayedApplyReceipt:
        """Merge and conditionally apply one uniquely identified proposal."""
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("proposal_id must be a non-empty string")
        if not isinstance(original, str) or not isinstance(proposal, str):
            raise TypeError("original and proposal must be strings")
        if not callable(read_snapshot) or not callable(apply_if_unchanged):
            raise TypeError("destination callbacks must be callable")

        with self._lock:
            completed = self._receipts.get(proposal_id)
            if completed is not None:
                return completed
            if proposal_id in self._in_flight:
                return DelayedApplyReceipt(
                    DelayedApplyOutcome.PROPOSAL_IN_FLIGHT, False)
            self._in_flight.add(proposal_id)

        try:
            receipt = self._apply_once(
                original, proposal, read_snapshot, apply_if_unchanged)
        except Exception:
            # Adapters are an external trust boundary. Their exception text is
            # deliberately not reflected into the fixed receipt.
            receipt = DelayedApplyReceipt(
                DelayedApplyOutcome.ADAPTER_EXCEPTION, False)
        with self._lock:
            # Only this call can finalize the ID: concurrent duplicates return
            # while it is present in ``_in_flight``.
            self._in_flight.remove(proposal_id)
            self._receipts[proposal_id] = receipt
        return receipt

    def _apply_once(
            self,
            original: str,
            proposal: str,
            read_snapshot: Callable[[], DestinationSnapshot | None],
            apply_if_unchanged: Callable[
                [DestinationSnapshot, str], bool],
    ) -> DelayedApplyReceipt:
        captured = read_snapshot()
        if not self._valid_snapshot(captured):
            return DelayedApplyReceipt(
                DelayedApplyOutcome.UNREADABLE_TARGET, False)
        assert isinstance(captured, DestinationSnapshot)
        if not captured.focused:
            return DelayedApplyReceipt(
                DelayedApplyOutcome.FOCUS_DRIFT, False)

        merged = merge_delayed_cleanup(original, proposal, captured.text)
        counts = {
            "merge_applied_count": merged.applied_count,
            "merge_rejected_count": merged.rejected_count,
        }
        if any(decision.reason in _AMBIGUOUS_MERGE_REASONS
               for decision in merged.decisions):
            return DelayedApplyReceipt(
                DelayedApplyOutcome.AMBIGUOUS_MERGE, False, **counts)
        if not merged.changed or merged.merged_text == captured.text:
            return DelayedApplyReceipt(
                DelayedApplyOutcome.NO_SAFE_CHANGES, False, **counts)

        current = read_snapshot()
        if not self._valid_snapshot(current):
            return DelayedApplyReceipt(
                DelayedApplyOutcome.UNREADABLE_TARGET, False, **counts)
        assert isinstance(current, DestinationSnapshot)
        if (not current.focused
                or current.destination_id != captured.destination_id):
            return DelayedApplyReceipt(
                DelayedApplyOutcome.FOCUS_DRIFT, False, **counts)
        if current.revision != captured.revision:
            return DelayedApplyReceipt(
                DelayedApplyOutcome.REVISION_DRIFT, False, **counts)
        if current.text != captured.text:
            return DelayedApplyReceipt(
                DelayedApplyOutcome.TEXT_DRIFT, False, **counts)

        # This injected callback is the only apply path. It receives the fresh
        # snapshot so its implementation can atomically compare identity,
        # revision, and text before writing.
        applied = apply_if_unchanged(current, merged.merged_text)
        if applied is not True:
            return DelayedApplyReceipt(
                DelayedApplyOutcome.COMPARE_AND_SWAP_REJECTED,
                False,
                **counts,
            )
        return DelayedApplyReceipt(
            DelayedApplyOutcome.APPLIED, True, **counts)


@dataclass(frozen=True)
class _ProposalEdit:
    original_start: int
    original_end: int
    replacement: str


def _opcodes(before: str, after: str):
    return SequenceMatcher(
        None, before, after, autojunk=False).get_opcodes()


def _proposal_edits(original: str, proposal: str) -> tuple[_ProposalEdit, ...]:
    return tuple(
        _ProposalEdit(start, end, proposal[replacement_start:replacement_end])
        for tag, start, end, replacement_start, replacement_end
        in _opcodes(original, proposal)
        if tag != "equal"
    )


def _occurrences(text: str, needle: str) -> tuple[int, ...]:
    if not needle:
        return ()
    found: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return tuple(found)
        found.append(index)
        start = index + 1


def _unique_boundary_anchor(original: str, start: int, end: int) \
        -> tuple[int, int] | None:
    """Find the smallest deterministic unique window around an edit.

    One unchanged character is required on every side that exists.  An edit
    replacing the entire original string therefore has no safe boundary and
    is rejected.
    """
    size = len(original)
    if size == 0 or (start == 0 and end == size):
        return None

    base_left = start - 1 if start > 0 else start
    base_right = end + 1 if end < size else end
    if base_left == base_right:
        return None

    available_left = base_left
    available_right = size - base_right
    for extra in range(available_left + available_right + 1):
        candidates: list[tuple[int, int]] = []
        for add_left in range(extra + 1):
            add_right = extra - add_left
            if add_left > available_left or add_right > available_right:
                continue
            left = base_left - add_left
            right = base_right + add_right
            if len(_occurrences(original, original[left:right])) == 1:
                candidates.append((left, right))
        if candidates:
            # Prefer balanced context, then the earlier boundary.  This makes
            # the receipt stable even when either side could disambiguate.
            return min(
                candidates,
                key=lambda item: (
                    abs((base_left - item[0]) - (item[1] - base_right)),
                    item[0],
                ),
            )
    return None


def _destination_was_reordered(original: str, current: str, opcodes) -> bool:
    """Conservatively recognize moved text represented as delete + insert."""
    removed: set[str] = set()
    inserted: set[str] = set()
    for tag, start, end, current_start, current_end in opcodes:
        if tag == "equal":
            continue
        old = original[start:end].strip()
        new = current[current_start:current_end].strip()
        if old:
            removed.add(old)
        if new:
            inserted.add(new)
    return bool(removed & inserted)


def _current_touches_edit(edit: _ProposalEdit, opcodes) -> bool:
    """Return whether a user change touches the edit or either boundary."""
    start = edit.original_start
    end = edit.original_end
    for tag, changed_start, changed_end, _current_start, _current_end in opcodes:
        if tag == "equal":
            continue
        if changed_start == changed_end:
            if start <= changed_start <= end:
                return True
        elif start == end:
            if changed_start <= start <= changed_end:
                return True
        elif changed_start < end and changed_end > start:
            return True
    return False


def merge_delayed_cleanup(
        original: str, proposal: str, current: str) -> DelayedMergeReceipt:
    """Merge safe delayed cleanup edits into a current destination snapshot.

    ``original`` is the exact text inserted, ``proposal`` is cleanup of that
    text, and ``current`` is a fresh destination read.  The returned text is a
    candidate only; callers remain responsible for destination validation and
    any write transaction.
    """
    if not all(isinstance(value, str)
               for value in (original, proposal, current)):
        raise TypeError("original, proposal, and current must be strings")

    edits = _proposal_edits(original, proposal)
    if not edits:
        return DelayedMergeReceipt(current, 0, 0, ())

    current_opcodes = _opcodes(original, current)
    reordered = _destination_was_reordered(
        original, current, current_opcodes)
    decisions: list[DelayedMergeDecision] = []
    accepted: list[tuple[int, int, str, int]] = []

    for edit_index, edit in enumerate(edits):
        reason: DelayedMergeReason | None = None
        mapped_start: int | None = None
        mapped_end: int | None = None
        anchor = _unique_boundary_anchor(
            original, edit.original_start, edit.original_end)

        if reordered:
            reason = DelayedMergeReason.DESTINATION_REORDERED
        elif anchor is None:
            reason = DelayedMergeReason.INSUFFICIENT_ANCHOR
        elif _current_touches_edit(edit, current_opcodes):
            reason = DelayedMergeReason.CURRENT_SPAN_TOUCHED
        else:
            anchor_start, anchor_end = anchor
            anchor_text = original[anchor_start:anchor_end]
            locations = _occurrences(current, anchor_text)
            if not locations:
                reason = DelayedMergeReason.ANCHOR_CHANGED
            elif len(locations) != 1:
                reason = DelayedMergeReason.AMBIGUOUS_ANCHOR
            else:
                mapped_start = (
                    locations[0] + edit.original_start - anchor_start)
                mapped_end = (
                    locations[0] + edit.original_end - anchor_start)
                if current[mapped_start:mapped_end] != original[
                        edit.original_start:edit.original_end]:
                    reason = DelayedMergeReason.ANCHOR_CHANGED

        if reason is None:
            accepted.append((
                mapped_start, mapped_end, edit.replacement, edit_index))
            decisions.append(DelayedMergeDecision(
                edit.original_start,
                edit.original_end,
                edit.replacement,
                mapped_start,
                mapped_end,
                True,
                DelayedMergeReason.APPLIED,
            ))
        else:
            decisions.append(DelayedMergeDecision(
                edit.original_start,
                edit.original_end,
                edit.replacement,
                mapped_start,
                mapped_end,
                False,
                reason,
            ))

    # A changed destination can map otherwise-disjoint source edits onto the
    # same range.  Refuse all colliding edits rather than choosing a winner.
    accepted.sort(key=lambda item: (item[0], item[1], item[3]))
    overlapping_indexes: set[int] = set()
    for previous, following in zip(accepted, accepted[1:]):
        previous_start, previous_end, _replacement, previous_index = previous
        next_start, next_end, _replacement, next_index = following
        if (next_start < previous_end
                or (next_start == previous_start
                    and next_end == previous_end)):
            overlapping_indexes.update((previous_index, next_index))

    if overlapping_indexes:
        accepted = [entry for entry in accepted
                    if entry[3] not in overlapping_indexes]
        for index in overlapping_indexes:
            decision = decisions[index]
            decisions[index] = DelayedMergeDecision(
                decision.original_start,
                decision.original_end,
                decision.replacement,
                decision.current_start,
                decision.current_end,
                False,
                DelayedMergeReason.DESTINATION_OVERLAP,
            )

    merged = current
    for start, end, replacement, _index in reversed(accepted):
        merged = merged[:start] + replacement + merged[end:]

    applied_count = len(accepted)
    return DelayedMergeReceipt(
        merged,
        applied_count,
        len(decisions) - applied_count,
        tuple(decisions),
    )
