"""Bounded, fail-closed proof recovery for cleanup candidates.

This module has no runtime authority.  It derives an exact replay script only
after an independent lexical and protected-anchor policy has found the
candidate eligible.  Receipts contain fixed categories and counts, never the
source, candidate, edit text, or a content-derived digest.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from voice_compiler import (
    CORRECTION_BLOCKLIST,
    EditProposal,
    PROVABLE_FILLER_WORDS,
    VoiceCompiler,
    _proof_words,
    protected_anchors,
)

MAX_TEXT_CHARS = 1_000
MAX_EDITS = 12
MAX_BEFORE_CHARS = 240
MAX_AFTER_CHARS = 280
MAX_EXPANSION_CHARS = 64
MAX_SCRATCH_REMOVAL_WORDS = 8
SAFE_ADDED_PUNCTUATION = frozenset(".,?!;:")


@dataclass(frozen=True)
class RecoveredEdit:
    """One exact replacement against offsets in the original source."""

    start: int
    end: int
    before: str
    after: str


@dataclass(frozen=True)
class RecoveryReceipt:
    """Content-free evidence about one recovery attempt."""

    schema_version: int
    disposition: str
    reason: str
    edit_count: int
    transformation_count: int
    anchor_count: int
    abandoned_anchor_count: int
    replay_verified: bool
    output_guard_passed: bool


@dataclass(frozen=True)
class RecoveryResult:
    """Recovered text and proof; rejected results return the source unchanged."""

    text: str
    edits: tuple[RecoveredEdit, ...]
    receipt: RecoveryReceipt


@dataclass(frozen=True)
class _Eligibility:
    accepted: bool
    reason: str
    transformations: int = 0
    abandoned_anchors: tuple[str, ...] = ()


def replay_edits(source: str, edits: tuple[RecoveredEdit, ...]) -> str | None:
    """Replay original-offset edits, returning ``None`` on any contradiction."""
    output = source
    previous_start = len(source) + 1
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        if (edit.start < 0 or edit.end < edit.start
                or edit.end > len(source) or edit.end > previous_start
                or source[edit.start:edit.end] != edit.before):
            return None
        output = output[:edit.start] + edit.after + output[edit.end:]
        previous_start = edit.start
    return output


def _exact_edits(source: str, candidate: str) -> tuple[RecoveredEdit, ...]:
    """Derive deterministic, bounded character hunks from matching blocks."""
    opcodes = difflib.SequenceMatcher(
        None, source, candidate, autojunk=False).get_opcodes()
    hunks: list[tuple[int, int, int, int]] = []
    for tag, source_start, source_end, candidate_start, candidate_end in opcodes:
        if tag == "equal":
            continue
        # The replay contract forbids zero-width source spans.  Attach a pure
        # insertion to one unchanged neighbouring character so it remains an
        # exact replacement rather than implicit insertion authority.
        if source_start == source_end:
            if source_start > 0:
                source_start -= 1
                candidate_start -= 1
            elif source_end < len(source):
                source_end += 1
                candidate_end += 1
        hunks.append((source_start, source_end, candidate_start, candidate_end))

    merged: list[list[int]] = []
    for hunk in hunks:
        if merged and hunk[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hunk[1])
            merged[-1][3] = max(merged[-1][3], hunk[3])
        else:
            merged.append(list(hunk))
    return tuple(RecoveredEdit(
        source_start, source_end, source[source_start:source_end],
        candidate[candidate_start:candidate_end],
    ) for source_start, source_end, candidate_start, candidate_end in merged)


def _candidate_symbols_eligible(source: str, candidate: str) -> bool:
    """Constrain characters that the lexical proof stream intentionally omits."""
    lower = source.casefold()
    layout_intent = "new line" in lower or "new paragraph" in lower
    source_words = _proof_words(source)
    counted_list_intent = (
        len(source_words) >= 4
        and source_words[0] in {
            "two", "three", "four", "five", "six", "seven", "eight",
            "nine", "ten", "2", "3", "4", "5", "6", "7", "8", "9",
            "10",
        }
        and source_words[1] in {"ideas", "items", "points", "things"}
        and sum(word in {"first", "second", "third", "fourth", "fifth"}
                for word in source_words) >= 2
    )
    explicit_list_intent = any(marker in lower for marker in (
        "here's a list", "here is a list", "here are some feedback items",
        "here are a few", "i have a few",
    ))
    list_intent = counted_list_intent or explicit_list_intent
    source_counts = Counter(source)
    candidate_counts = Counter(candidate)
    added = candidate_counts - source_counts
    for character, count in added.items():
        if character.isalnum():
            continue
        if character.isspace():
            if character == " ":
                continue
            if character == "\n" and (layout_intent or list_intent):
                continue
            return False
        category = unicodedata.category(character)
        if category.startswith(("S", "C")):
            return False
        if (character not in SAFE_ADDED_PUNCTUATION
                and not (character == "-" and list_intent)):
            return False
        if count > (8 if character == "-" and list_intent else 2):
            return False
    # Repeated punctuation can smuggle tone or structure through an otherwise
    # valid filler/correction proof. Ordinary single sentence marks remain in
    # the compiler's existing punctuation contract.
    if re.search(r"[.,?!;:]{2,}", candidate):
        return False

    if "\n" in candidate and "\n" not in source \
            and not (layout_intent or list_intent):
        return False
    if re.search(r"(?m)^\s*-\s+", candidate) and not list_intent:
        return False
    return True


def _lexically_eligible(source: str, candidate: str) -> _Eligibility:
    source_words = _proof_words(source)
    candidate_words = _proof_words(candidate)

    # The existing compiler remains the authority for its already-proved
    # punctuation, filler, correction, layout, and enumeration contracts.
    whole = VoiceCompiler().verify_edits(
        source, (EditProposal("proof_recovery", source, candidate),))
    if whole.text == candidate and whole.edits[0].accepted:
        return _Eligibility(True, "eligible", transformations=1)

    # Composite cleanup often combines filler deletion with an explicit
    # one-word correction, which a single whole-span proposal cannot express.
    # This bounded dynamic proof admits only literal equality, proven fillers,
    # spoken layout markers, and the compiler's existing correction grammar.
    memo: dict[tuple[int, int], tuple[int, tuple[str, ...]] | None] = {}

    def prove(index: int, target: int) -> tuple[int, tuple[str, ...]] | None:
        key = (index, target)
        if key in memo:
            return memo[key]
        if index == len(source_words) and target == len(candidate_words):
            return (0, ())
        answer = None
        if (index < len(source_words) and target < len(candidate_words)
                and source_words[index] == candidate_words[target]):
            answer = prove(index + 1, target + 1)
        if answer is None and index < len(source_words) \
                and source_words[index] in PROVABLE_FILLER_WORDS:
            tail = prove(index + 1, target)
            if tail is not None:
                answer = (tail[0] + 1, tail[1])
        if (answer is None and index + 1 < len(source_words)
                and source_words[index] == "new"
                and source_words[index + 1] in {"line", "paragraph"}):
            tail = prove(index + 2, target)
            if tail is not None:
                answer = (tail[0] + 1, tail[1])
        if (answer is None and index + 2 < len(source_words)
                and target < len(candidate_words)
                and source_words[index + 1] == "actually"
                and source_words[index] not in CORRECTION_BLOCKLIST
                and source_words[index + 2] == candidate_words[target]):
            tail = prove(index + 3, target + 1)
            if tail is not None:
                answer = (tail[0] + 1,
                          (source_words[index], *tail[1]))
        memo[key] = answer
        return answer

    composite = prove(0, 0)
    if composite is not None:
        return _Eligibility(
            True, "eligible", composite[0], composite[1])

    # "scratch that" can prove one contiguous abandonment only.  The target
    # must be exactly the source with a short marker-containing span removed;
    # no lexical replacement or insertion is inferred. Repeated boundary words
    # can make two spans replay to the same target ("at six ... at seven");
    # that boundary ambiguity does not broaden the single admitted output.
    scratch = next((index for index in range(len(source_words) - 1)
                    if source_words[index:index + 2] == ["scratch", "that"]),
                   None)
    matches: list[tuple[int, int]] = []
    if scratch is not None:
        for start in range(max(0, scratch - MAX_SCRATCH_REMOVAL_WORDS),
                           scratch + 1):
            for end in range(scratch + 2,
                             min(len(source_words), start
                                 + MAX_SCRATCH_REMOVAL_WORDS) + 1):
                if source_words[:start] + source_words[end:] == candidate_words:
                    matches.append((start, end))
    if matches:
        # Unlike the compiler's narrow one-word "actually" correction, a
        # scratch span receives no protected-anchor exception.
        return _Eligibility(True, "eligible", 1)
    return _Eligibility(False, "unproved-transformation")


def recover_cleanup_proof(
    source: str,
    candidate: str,
    *,
    output_guard: Callable[[str, str], str | None] | None,
) -> RecoveryResult:
    """Recover an exact proof only through conservative independent gates.

    ``output_guard`` is required.  It should wrap the caller's current output
    and semantic guards and return a fixed reason on failure.  Passing no guard
    is deliberately fail-closed.
    """
    guard_passed = False

    def reject(reason: str, *, anchors: int = 0,
               abandoned: int = 0) -> RecoveryResult:
        return RecoveryResult(source, (), RecoveryReceipt(
            1, "rejected", reason, 0, 0, anchors, abandoned, False,
            guard_passed))

    if not isinstance(source, str) or not isinstance(candidate, str):
        return reject("invalid-input")
    if not source or not candidate or output_guard is None:
        return reject("missing-required-evidence")
    if len(source) > MAX_TEXT_CHARS or len(candidate) > MAX_TEXT_CHARS:
        return reject("text-out-of-bounds")
    try:
        guard_reason = output_guard(source, candidate)
    except Exception:
        return reject("output-guard-error")
    if guard_reason is not None:
        return reject("output-guard-rejected")
    guard_passed = True
    if len(candidate) > len(source) + MAX_EXPANSION_CHARS:
        return reject("candidate-expansion")
    if not _candidate_symbols_eligible(source, candidate):
        return reject("candidate-symbol-policy")
    if candidate == source:
        return RecoveryResult(source, (), RecoveryReceipt(
            1, "no-effect", "identical", 0, 0,
            len(protected_anchors(source)), 0, True, True))

    eligibility = _lexically_eligible(source, candidate)
    anchors = protected_anchors(source)
    abandoned = {item.casefold() for item in eligibility.abandoned_anchors}
    missing = tuple(anchor for anchor in anchors
                    if anchor.casefold() not in candidate.casefold())
    allowed_missing = tuple(anchor for anchor in missing
                            if anchor.casefold() in abandoned)
    if missing != allowed_missing:
        return reject("protected-anchor-removed", anchors=len(anchors),
                      abandoned=len(allowed_missing))
    if not eligibility.accepted:
        return reject(eligibility.reason, anchors=len(anchors),
                      abandoned=len(allowed_missing))

    edits = _exact_edits(source, candidate)
    if not edits or len(edits) > MAX_EDITS:
        return reject("edit-count-out-of-bounds", anchors=len(anchors),
                      abandoned=len(allowed_missing))
    if any(not edit.before or len(edit.before) > MAX_BEFORE_CHARS
           or len(edit.after) > MAX_AFTER_CHARS for edit in edits):
        return reject("edit-span-out-of-bounds", anchors=len(anchors),
                      abandoned=len(allowed_missing))
    if replay_edits(source, edits) != candidate:
        return reject("replay-mismatch", anchors=len(anchors),
                      abandoned=len(allowed_missing))
    return RecoveryResult(candidate, edits, RecoveryReceipt(
        1, "recovered", "eligible", len(edits),
        eligibility.transformations, len(anchors), len(allowed_missing),
        True, True))
