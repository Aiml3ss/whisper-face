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

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum


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
