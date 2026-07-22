"""Pure, non-writing Drop-to-Target decision prototype.

The module accepts only caller-supplied synthetic target facts and a proposal
that describes a source *kind*, never source content.  It can return an opaque
target identifier after strict confidence, margin, and compatibility gates.
It has no Accessibility, drag/drop, clipboard, pointer, keyboard, filesystem,
network, callback, or runtime integration surface.

This is synthetic decision evidence only.  It is not physical-app validation
and makes no accuracy claim.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
EVIDENCE_SCOPE = "synthetic-decision-only"
MIN_CONFIDENCE = 0.84
MIN_MARGIN = 0.12
MAX_TARGETS = 128
MAX_NAME_CHARS = 128

_TARGET_KEYS = frozenset({
    "schema_version", "target_id", "title", "label", "accepted_kinds",
    "accepted_effects", "visible", "enabled", "drop_enabled",
})
_PROPOSAL_KEYS = frozenset({
    "schema_version", "target_hint", "source_kind", "effect",
})
_CORPUS_KEYS = frozenset({
    "schema_version", "evidence_scope", "physical_validation", "scenes",
    "cases",
})
_SCENE_KEYS = frozenset({"scene_id", "targets"})
_CASE_KEYS = frozenset({
    "case_id", "scene_id", "proposal", "expected_state",
    "expected_target_id",
})
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SourceKind(str, Enum):
    FILE_REFERENCE = "file_reference"
    IMAGE_REFERENCE = "image_reference"
    TEXT_SELECTION = "text_selection"
    URL_REFERENCE = "url_reference"


class DropEffect(str, Enum):
    COPY = "copy"
    LINK = "link"
    MOVE = "move"


class DecisionState(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class DropDecisionError(ValueError):
    """Raised when input expands or violates the closed v1 schema."""


def _closed_mapping(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise DropDecisionError(f"invalid {label} schema")
    return dict(value)


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 96
        and value[0].isalnum()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _supported_version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _bounded_text(value: Any, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value.strip()))
        and len(value) <= MAX_NAME_CHARS
        and not any(ord(character) < 32 for character in value)
    )


def _enum_list(value: Any, enum_type: type[Enum], label: str) -> tuple[Enum, ...]:
    if not isinstance(value, list) or not value or len(value) > len(enum_type):
        raise DropDecisionError(f"invalid {label}")
    try:
        parsed = tuple(enum_type(item) for item in value)
    except (TypeError, ValueError) as error:
        raise DropDecisionError(f"invalid {label}") from error
    if len(set(parsed)) != len(parsed):
        raise DropDecisionError(f"invalid {label}")
    return parsed


@dataclass(frozen=True)
class DropTargetFact:
    """One inert target observation with capability facts only."""

    target_id: str
    title: str
    label: str
    accepted_kinds: tuple[SourceKind, ...]
    accepted_effects: tuple[DropEffect, ...]
    visible: bool
    enabled: bool
    drop_enabled: bool

    def __post_init__(self) -> None:
        if not _identifier(self.target_id):
            raise DropDecisionError("invalid target identifier")
        if not _bounded_text(self.title, allow_empty=True) or not _bounded_text(
                self.label, allow_empty=True):
            raise DropDecisionError("invalid target name")
        if not self.title.strip() and not self.label.strip():
            raise DropDecisionError("target needs a title or label")
        if (not self.accepted_kinds
                or not all(isinstance(item, SourceKind) for item in self.accepted_kinds)
                or len(set(self.accepted_kinds)) != len(self.accepted_kinds)):
            raise DropDecisionError("invalid accepted source kinds")
        if (not self.accepted_effects
                or not all(isinstance(item, DropEffect) for item in self.accepted_effects)
                or len(set(self.accepted_effects)) != len(self.accepted_effects)):
            raise DropDecisionError("invalid accepted effects")
        for name in ("visible", "enabled", "drop_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise DropDecisionError(f"{name} must be a boolean")

    @classmethod
    def from_mapping(cls, value: Any) -> "DropTargetFact":
        target = _closed_mapping(value, _TARGET_KEYS, "target fact")
        if not _supported_version(target["schema_version"]):
            raise DropDecisionError("unsupported target fact schema")
        return cls(
            target_id=target["target_id"],
            title=target["title"],
            label=target["label"],
            accepted_kinds=tuple(_enum_list(
                target["accepted_kinds"], SourceKind, "accepted source kinds")),
            accepted_effects=tuple(_enum_list(
                target["accepted_effects"], DropEffect, "accepted effects")),
            visible=target["visible"],
            enabled=target["enabled"],
            drop_enabled=target["drop_enabled"],
        )


@dataclass(frozen=True)
class DropProposal:
    """A content-free proposed drop; it cannot carry a payload or path."""

    target_hint: str
    source_kind: SourceKind
    effect: DropEffect

    def __post_init__(self) -> None:
        if not _bounded_text(self.target_hint):
            raise DropDecisionError("target hint must be bounded text")
        if not isinstance(self.source_kind, SourceKind):
            raise DropDecisionError("invalid source kind")
        if not isinstance(self.effect, DropEffect):
            raise DropDecisionError("invalid drop effect")

    @classmethod
    def from_mapping(cls, value: Any) -> "DropProposal":
        proposal = _closed_mapping(value, _PROPOSAL_KEYS, "drop proposal")
        if not _supported_version(proposal["schema_version"]):
            raise DropDecisionError("unsupported drop proposal schema")
        try:
            source_kind = SourceKind(proposal["source_kind"])
            effect = DropEffect(proposal["effect"])
        except (TypeError, ValueError) as error:
            raise DropDecisionError("unsupported drop proposal fact") from error
        return cls(proposal["target_hint"], source_kind, effect)


@dataclass(frozen=True)
class DropDecisionReceipt:
    """Content-free aggregate evidence for one decision."""

    state: DecisionState
    observed_targets: int
    eligible_targets: int
    contradiction_count: int
    evidence: tuple[str, ...]
    confidence_bucket: str
    margin_bucket: str
    schema_version: int = SCHEMA_VERSION

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "observed_targets": self.observed_targets,
            "eligible_targets": self.eligible_targets,
            "contradiction_count": self.contradiction_count,
            "evidence": list(self.evidence),
            "confidence_bucket": self.confidence_bucket,
            "margin_bucket": self.margin_bucket,
        }


@dataclass(frozen=True)
class DropDecision:
    """Decision result; only a gated winner exposes an opaque target id."""

    state: DecisionState
    target_id: str | None
    receipt: DropDecisionReceipt


@dataclass(frozen=True)
class _Candidate:
    target: DropTargetFact
    score: float
    evidence: tuple[str, ...]


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def _raw_form(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _lexical_candidate(hint: str, target: DropTargetFact) -> _Candidate | None:
    hint_raw = _raw_form(hint)
    hint_tokens = _tokens(hint)
    forms = tuple(
        (_raw_form(value), _tokens(value))
        for value in (target.title, target.label)
        if value.strip()
    )
    if hint_raw in {raw for raw, _ in forms}:
        return _Candidate(target, 0.97, ("exact_name",))
    normalized_hint = " ".join(hint_tokens)
    if normalized_hint in {" ".join(tokens) for _, tokens in forms}:
        return _Candidate(target, 0.92, ("normalized_name",))
    if not hint_tokens:
        return None
    hint_set = set(hint_tokens)
    best_score = 0.0
    for _, target_tokens in forms:
        target_set = set(target_tokens)
        overlap = len(hint_set & target_set)
        if overlap:
            coverage = overlap / len(hint_set)
            precision = overlap / len(target_set)
            best_score = max(best_score, 0.50 + 0.30 * coverage + 0.12 * precision)
    if best_score == 0:
        return None
    return _Candidate(target, min(best_score, 0.91), ("token_name",))


def _confidence_bucket(score: float) -> str:
    if score >= 0.92:
        return "very_high"
    if score >= MIN_CONFIDENCE:
        return "high"
    if score > 0:
        return "below_threshold"
    return "none"


def _margin_bucket(margin: float) -> str:
    if margin >= 0.25:
        return "wide"
    if margin >= MIN_MARGIN:
        return "sufficient"
    if margin > 0:
        return "narrow"
    return "none"


def decide_drop_to_target(
    proposal: DropProposal | Mapping[str, Any],
    targets: Iterable[DropTargetFact | Mapping[str, Any]],
) -> DropDecision:
    """Resolve or refuse a proposal without performing any drop operation."""
    parsed_proposal = (
        proposal if isinstance(proposal, DropProposal)
        else DropProposal.from_mapping(proposal)
    )
    snapshots = tuple(
        target if isinstance(target, DropTargetFact)
        else DropTargetFact.from_mapping(target)
        for target in targets
    )
    if len(snapshots) > MAX_TARGETS:
        raise DropDecisionError("target facts exceed bounded target count")
    if len({target.target_id for target in snapshots}) != len(snapshots):
        raise DropDecisionError("target identifiers must be unique")

    def operational(target: DropTargetFact) -> bool:
        return (
            target.visible and target.enabled and target.drop_enabled
            and parsed_proposal.source_kind in target.accepted_kinds
            and parsed_proposal.effect in target.accepted_effects
        )

    eligible = tuple(target for target in snapshots if operational(target))

    def finish(
        state: DecisionState,
        *,
        target_id: str | None = None,
        contradictions: int = 0,
        evidence: tuple[str, ...] = (),
        score: float = 0.0,
        margin: float = 0.0,
    ) -> DropDecision:
        return DropDecision(
            state=state,
            target_id=target_id,
            receipt=DropDecisionReceipt(
                state=state,
                observed_targets=len(snapshots),
                eligible_targets=len(eligible),
                contradiction_count=contradictions,
                evidence=tuple(sorted(set(evidence))),
                confidence_bucket=_confidence_bucket(score),
                margin_bucket=_margin_bucket(margin),
            ),
        )

    candidates = tuple(
        candidate for target in snapshots
        if (candidate := _lexical_candidate(parsed_proposal.target_hint, target))
        is not None
    )
    if not candidates:
        return finish(DecisionState.UNAVAILABLE)

    compatible: list[_Candidate] = []
    contradicted: list[tuple[_Candidate, int]] = []
    for candidate in candidates:
        target = candidate.target
        conflicts = sum((
            not target.visible,
            not target.enabled,
            not target.drop_enabled,
            parsed_proposal.source_kind not in target.accepted_kinds,
            parsed_proposal.effect not in target.accepted_effects,
        ))
        if conflicts:
            contradicted.append((candidate, conflicts))
        else:
            compatible.append(candidate)

    relevant_conflicts = tuple(
        (candidate, count) for candidate, count in contradicted
        if candidate.score >= MIN_CONFIDENCE
    )
    contradictions = sum(count for _, count in relevant_conflicts)
    ranked = sorted(compatible, key=lambda item: (-item.score, item.target.target_id))
    best = ranked[0] if ranked else None
    best_conflict = max(
        (candidate.score for candidate, _ in relevant_conflicts), default=0.0)
    if best is None:
        strongest = max(candidates, key=lambda item: item.score)
        return finish(
            DecisionState.UNAVAILABLE,
            contradictions=contradictions,
            evidence=strongest.evidence + (("constraint_conflict",)
                                            if contradictions else ()),
            score=strongest.score,
        )
    if best.score < MIN_CONFIDENCE:
        return finish(
            DecisionState.UNAVAILABLE,
            contradictions=contradictions,
            evidence=best.evidence,
            score=best.score,
        )
    if best_conflict >= best.score:
        return finish(
            DecisionState.UNAVAILABLE,
            contradictions=contradictions,
            evidence=best.evidence + ("constraint_conflict",),
            score=best.score,
        )

    compatible_runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    margin = best.score - max(compatible_runner_up, best_conflict)
    evidence = best.evidence + ("source_compatible", "effect_compatible")
    if best_conflict and margin < MIN_MARGIN:
        return finish(
            DecisionState.UNAVAILABLE,
            contradictions=contradictions,
            evidence=evidence + ("constraint_conflict",),
            score=best.score,
            margin=margin,
        )
    if margin < MIN_MARGIN:
        return finish(
            DecisionState.AMBIGUOUS,
            contradictions=contradictions,
            evidence=evidence,
            score=best.score,
            margin=margin,
        )
    return finish(
        DecisionState.RESOLVED,
        target_id=best.target.target_id,
        contradictions=contradictions,
        evidence=evidence,
        score=best.score,
        margin=margin,
    )


def measure_synthetic_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and measure a synthetic corpus without physical-app claims."""
    envelope = _closed_mapping(corpus, _CORPUS_KEYS, "synthetic corpus")
    if (not _supported_version(envelope["schema_version"])
            or envelope["evidence_scope"] != EVIDENCE_SCOPE
            or envelope["physical_validation"] is not False):
        raise DropDecisionError("unsupported synthetic corpus declaration")
    if not isinstance(envelope["scenes"], list) or not isinstance(
            envelope["cases"], list):
        raise DropDecisionError("synthetic scenes and cases must be lists")

    scenes: dict[str, tuple[DropTargetFact, ...]] = {}
    for raw_scene in envelope["scenes"]:
        scene = _closed_mapping(raw_scene, _SCENE_KEYS, "synthetic scene")
        if not _identifier(scene["scene_id"]) or scene["scene_id"] in scenes:
            raise DropDecisionError("invalid or duplicate synthetic scene")
        if not isinstance(scene["targets"], list):
            raise DropDecisionError("synthetic scene targets must be a list")
        scenes[scene["scene_id"]] = tuple(
            DropTargetFact.from_mapping(target) for target in scene["targets"]
        )

    totals = {
        "cases": 0,
        "resolved": 0,
        "ambiguous": 0,
        "unavailable": 0,
        "correct_outcomes": 0,
        "wrong_target_resolutions": 0,
    }
    seen_cases: set[str] = set()
    for raw_case in envelope["cases"]:
        case = _closed_mapping(raw_case, _CASE_KEYS, "synthetic case")
        if not _identifier(case["case_id"]) or case["case_id"] in seen_cases:
            raise DropDecisionError("invalid or duplicate synthetic case")
        seen_cases.add(case["case_id"])
        if case["scene_id"] not in scenes:
            raise DropDecisionError("synthetic case references an unknown scene")
        try:
            expected_state = DecisionState(case["expected_state"])
        except (TypeError, ValueError) as error:
            raise DropDecisionError("invalid expected synthetic state") from error
        expected_target = case["expected_target_id"]
        if ((expected_state == DecisionState.RESOLVED) != _identifier(expected_target)):
            raise DropDecisionError("expected target must exist only for resolved cases")
        target_ids = {target.target_id for target in scenes[case["scene_id"]]}
        if expected_target is not None and expected_target not in target_ids:
            raise DropDecisionError("expected synthetic target is not in the scene")

        result = decide_drop_to_target(
            DropProposal.from_mapping(case["proposal"]), scenes[case["scene_id"]]
        )
        totals["cases"] += 1
        totals[result.state.value] += 1
        if result.state == expected_state and result.target_id == expected_target:
            totals["correct_outcomes"] += 1
        if result.state == DecisionState.RESOLVED and result.target_id != expected_target:
            totals["wrong_target_resolutions"] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": EVIDENCE_SCOPE,
        "physical_validation": False,
        **totals,
    }
