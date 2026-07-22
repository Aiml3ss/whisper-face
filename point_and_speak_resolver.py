"""Pure, non-writing Point-and-Speak target resolution foundation.

The resolver consumes a closed, caller-supplied accessibility snapshot and
returns an opaque target identifier only when lexical and positional evidence
clear strict confidence and margin gates.  It has no Accessibility API,
pointer, keyboard, clipboard, network, or write capability.

``title`` and ``label`` are accessibility names, never element values or
document contents.  The closed snapshot schema intentionally has no value,
description, document-text, callback, or automation field.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
EVIDENCE_SCOPE = "synthetic-resolution-only"
MIN_CONFIDENCE = 0.82
MIN_MARGIN = 0.12
MAX_TARGETS = 256
MAX_ACCESSIBLE_NAME_CHARS = 128

_TARGET_KEYS = frozenset({
    "schema_version", "target_id", "role", "title", "label", "geometry",
    "visible", "enabled", "focused", "selection",
})
_GEOMETRY_KEYS = frozenset({"x", "y", "width", "height"})
_CORPUS_KEYS = frozenset({
    "schema_version", "evidence_scope", "physical_validation", "scenes",
    "cases",
})
_SCENE_KEYS = frozenset({"scene_id", "targets"})
_CASE_KEYS = frozenset({
    "case_id", "scene_id", "phrase", "expected_state",
    "expected_target_id",
})


class TargetRole(str, Enum):
    BUTTON = "button"
    CHECKBOX = "checkbox"
    LINK = "link"
    MENU_ITEM = "menu_item"
    RADIO_BUTTON = "radio_button"
    TAB = "tab"
    TEXT_FIELD = "text_field"


class SelectionState(str, Enum):
    SELECTED = "selected"
    UNSELECTED = "unselected"
    NOT_APPLICABLE = "not_applicable"


class ResolutionState(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class ResolutionError(ValueError):
    """Raised when a snapshot or synthetic corpus expands the v1 schema."""


def _closed_mapping(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ResolutionError(f"invalid {label} schema")
    return dict(value)


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 96
        and value[0].isalnum()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _plain_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and -1_000_000 <= value <= 1_000_000
    )


def _supported_schema_version(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value == SCHEMA_VERSION
    )


@dataclass(frozen=True)
class TargetGeometry:
    """Bounded screen geometry; it is used for ordering, never exported."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(_plain_number(value) for value in (
                self.x, self.y, self.width, self.height)):
            raise ResolutionError("invalid target geometry")
        if self.width <= 0 or self.height <= 0:
            raise ResolutionError("target geometry dimensions must be positive")

    @classmethod
    def from_mapping(cls, value: Any) -> "TargetGeometry":
        geometry = _closed_mapping(value, _GEOMETRY_KEYS, "geometry")
        return cls(**{key: geometry[key] for key in _GEOMETRY_KEYS})

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True)
class TargetSnapshot:
    """One inert, closed-schema accessibility target observation."""

    target_id: str
    role: TargetRole
    title: str
    label: str
    geometry: TargetGeometry
    visible: bool
    enabled: bool
    focused: bool
    selection: SelectionState

    def __post_init__(self) -> None:
        if not _identifier(self.target_id):
            raise ResolutionError("invalid target identifier")
        if not isinstance(self.role, TargetRole) or not isinstance(
                self.selection, SelectionState):
            raise ResolutionError("unsupported target fact")
        if not isinstance(self.geometry, TargetGeometry):
            raise ResolutionError("invalid target geometry")
        for text in (self.title, self.label):
            if (not isinstance(text, str)
                    or len(text) > MAX_ACCESSIBLE_NAME_CHARS
                    or any(ord(character) < 32 for character in text)):
                raise ResolutionError("invalid accessibility name")
        if not (self.title.strip() or self.label.strip()):
            raise ResolutionError("target needs an accessibility title or label")
        for key in ("visible", "enabled", "focused"):
            if not isinstance(getattr(self, key), bool):
                raise ResolutionError(f"{key} must be a boolean")
        if self.focused and not self.visible:
            raise ResolutionError("an invisible target cannot be focused")

    @classmethod
    def from_mapping(cls, value: Any) -> "TargetSnapshot":
        target = _closed_mapping(value, _TARGET_KEYS, "target snapshot")
        if not _supported_schema_version(target["schema_version"]):
            raise ResolutionError("unsupported target snapshot schema")
        if not _identifier(target["target_id"]):
            raise ResolutionError("invalid target identifier")
        try:
            role = TargetRole(target["role"])
            selection = SelectionState(target["selection"])
        except (TypeError, ValueError) as error:
            raise ResolutionError("unsupported target fact") from error
        return cls(
            target_id=target["target_id"],
            role=role,
            title=target["title"],
            label=target["label"],
            geometry=TargetGeometry.from_mapping(target["geometry"]),
            visible=target["visible"],
            enabled=target["enabled"],
            focused=target["focused"],
            selection=selection,
        )


@dataclass(frozen=True)
class ResolutionReceipt:
    """Content-free, aggregate evidence for one resolution decision."""

    state: ResolutionState
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
class Resolution:
    """Resolution result.  ``target_id`` exists only for a gated winner."""

    state: ResolutionState
    target_id: str | None
    receipt: ResolutionReceipt


_ROLE_TERMS = {
    "button": TargetRole.BUTTON,
    "buttons": TargetRole.BUTTON,
    "checkbox": TargetRole.CHECKBOX,
    "checkboxes": TargetRole.CHECKBOX,
    "link": TargetRole.LINK,
    "links": TargetRole.LINK,
    "menuitem": TargetRole.MENU_ITEM,
    "tab": TargetRole.TAB,
    "tabs": TargetRole.TAB,
    "radio": TargetRole.RADIO_BUTTON,
    "field": TargetRole.TEXT_FIELD,
    "input": TargetRole.TEXT_FIELD,
    "textfield": TargetRole.TEXT_FIELD,
}
_ORDINAL_TERMS = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6,
    "last": -1,
}
_SPATIAL_TERMS = {
    "left": "left", "right": "right",
    "top": "top", "upper": "top",
    "bottom": "bottom", "lower": "bottom",
}
_SELECTION_TERMS = {
    "selected": SelectionState.SELECTED,
    "checked": SelectionState.SELECTED,
    "unselected": SelectionState.UNSELECTED,
    "unchecked": SelectionState.UNSELECTED,
}
_FOCUS_TERMS = frozenset({"focused", "active"})
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(_TOKEN_RE.findall(normalized))


def _raw_form(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class _Query:
    raw: str
    content_tokens: tuple[str, ...]
    role: TargetRole | None
    ordinal: int | None
    horizontal: str | None
    vertical: str | None
    selection: SelectionState | None
    focused: bool
    contradictions: int


def _one_or_contradiction(values: set[Any]) -> tuple[Any | None, int]:
    if len(values) > 1:
        return None, 1
    return (next(iter(values)) if values else None), 0


def _parse_query(phrase: str) -> _Query:
    if not isinstance(phrase, str) or len(phrase) > 256:
        raise ResolutionError("spoken target phrase must be bounded text")
    tokens = _tokens(phrase)
    roles = {_ROLE_TERMS[token] for token in tokens if token in _ROLE_TERMS}
    compound_role_terms: set[str] = set()
    if "radio" in tokens and "button" in tokens:
        roles.discard(TargetRole.BUTTON)
        roles.add(TargetRole.RADIO_BUTTON)
    if "menu" in tokens and "item" in tokens:
        roles.add(TargetRole.MENU_ITEM)
        compound_role_terms.update({"menu", "item"})
    if "text" in tokens and "field" in tokens:
        compound_role_terms.add("text")
    ordinals = {_ORDINAL_TERMS[token] for token in tokens if token in _ORDINAL_TERMS}
    horizontals = {
        _SPATIAL_TERMS[token] for token in tokens
        if _SPATIAL_TERMS.get(token) in {"left", "right"}
    }
    verticals = {
        _SPATIAL_TERMS[token] for token in tokens
        if _SPATIAL_TERMS.get(token) in {"top", "bottom"}
    }
    selections = {
        _SELECTION_TERMS[token] for token in tokens if token in _SELECTION_TERMS
    }
    role, role_conflict = _one_or_contradiction(roles)
    ordinal, ordinal_conflict = _one_or_contradiction(ordinals)
    horizontal, horizontal_conflict = _one_or_contradiction(horizontals)
    vertical, vertical_conflict = _one_or_contradiction(verticals)
    selection, selection_conflict = _one_or_contradiction(selections)
    focused = any(token in _FOCUS_TERMS for token in tokens)
    directives = (
        set(_ROLE_TERMS) | set(_ORDINAL_TERMS) | set(_SPATIAL_TERMS)
        | set(_SELECTION_TERMS) | set(_FOCUS_TERMS) | compound_role_terms
    )
    return _Query(
        raw=_raw_form(phrase),
        content_tokens=tuple(token for token in tokens if token not in directives),
        role=role,
        ordinal=ordinal,
        horizontal=horizontal,
        vertical=vertical,
        selection=selection,
        focused=focused,
        contradictions=sum((
            role_conflict, ordinal_conflict, horizontal_conflict,
            vertical_conflict, selection_conflict,
        )),
    )


@dataclass(frozen=True)
class _Candidate:
    target: TargetSnapshot
    score: float
    evidence: tuple[str, ...]


def _target_forms(target: TargetSnapshot) -> tuple[tuple[str, tuple[str, ...]], ...]:
    forms: list[tuple[str, tuple[str, ...]]] = []
    role_words = {
        token for token, role in _ROLE_TERMS.items()
        if role == target.role
    }
    for value in (target.title, target.label):
        if not value.strip():
            continue
        tokens = _tokens(value)
        forms.append((_raw_form(value), tokens))
        trimmed = tuple(token for token in tokens if token not in role_words)
        if trimmed and trimmed != tokens:
            forms.append((" ".join(trimmed), trimmed))
    return tuple(dict.fromkeys(forms))


def _lexical_candidate(query: _Query, target: TargetSnapshot) -> _Candidate | None:
    evidence: list[str] = []
    if query.role is not None and target.role != query.role:
        return None
    if query.selection is not None and target.selection != query.selection:
        return None
    if query.focused and not target.focused:
        return None

    forms = _target_forms(target)
    if query.content_tokens:
        normalized_query = " ".join(query.content_tokens)
        if query.raw in {raw for raw, _tokens_value in forms}:
            score = 0.97
            evidence.append("exact")
        elif normalized_query in {
                " ".join(tokens_value) for _raw, tokens_value in forms}:
            score = 0.90
            evidence.append("normalized")
        else:
            query_set = set(query.content_tokens)
            best_overlap = 0
            best_target_size = 1
            for _raw, target_tokens in forms:
                overlap = len(query_set & set(target_tokens))
                if overlap / len(query_set) > best_overlap / len(query_set):
                    best_overlap = overlap
                    best_target_size = len(set(target_tokens)) or 1
                elif overlap == best_overlap:
                    best_target_size = min(
                        best_target_size, len(set(target_tokens)) or 1)
            if best_overlap == 0:
                return None
            coverage = best_overlap / len(query_set)
            precision = best_overlap / best_target_size
            score = 0.42 + 0.33 * coverage + 0.12 * precision
            evidence.append("token")
    else:
        score = 0.60

    if query.role is not None:
        score += 0.08
        evidence.append("role")
    if query.selection is not None:
        score += 0.10
        evidence.append("selection")
    if query.focused:
        score += 0.10
        evidence.append("focus")
    return _Candidate(target, min(score, 0.99), tuple(evidence))


def _reading_order(candidate: _Candidate) -> tuple[float, float, str]:
    geometry = candidate.target.geometry
    return geometry.center_y, geometry.center_x, candidate.target.target_id


def _apply_directives(
    candidates: list[_Candidate], query: _Query,
) -> tuple[list[_Candidate], int]:
    contradictions = 0
    selected = candidates
    if query.ordinal is not None and selected:
        ordered = sorted(selected, key=_reading_order)
        index = len(ordered) - 1 if query.ordinal == -1 else query.ordinal - 1
        if not 0 <= index < len(ordered):
            return [], contradictions + len(ordered)
        winner = ordered[index]
        contradictions += len(ordered) - 1
        selected = [
            _Candidate(
                winner.target, min(winner.score + 0.15, 0.99),
                winner.evidence + ("ordinal",),
            )
        ]
    for axis, direction in (
        ("horizontal", query.horizontal), ("vertical", query.vertical),
    ):
        if direction is None or not selected:
            continue
        coordinate = (
            (lambda candidate: candidate.target.geometry.center_x)
            if axis == "horizontal"
            else (lambda candidate: candidate.target.geometry.center_y)
        )
        extreme = (
            min(coordinate(candidate) for candidate in selected)
            if direction in {"left", "top"}
            else max(coordinate(candidate) for candidate in selected)
        )
        matches = [
            candidate for candidate in selected
            if coordinate(candidate) == extreme
        ]
        contradictions += len(selected) - len(matches)
        selected = [
            _Candidate(
                candidate.target, min(candidate.score + 0.10, 0.99),
                candidate.evidence + ("spatial",),
            )
            for candidate in matches
        ]
    return selected, contradictions


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


def resolve_point_and_speak(
    phrase: str,
    targets: Iterable[TargetSnapshot | Mapping[str, Any]],
) -> Resolution:
    """Resolve an inert snapshot, failing closed on weak or conflicting evidence."""
    query = _parse_query(phrase)
    snapshots = tuple(
        target if isinstance(target, TargetSnapshot)
        else TargetSnapshot.from_mapping(target)
        for target in targets
    )
    if len(snapshots) > MAX_TARGETS:
        raise ResolutionError("target snapshot exceeds bounded target count")
    if len({target.target_id for target in snapshots}) != len(snapshots):
        raise ResolutionError("target identifiers must be unique")
    eligible = tuple(target for target in snapshots if target.visible and target.enabled)

    def finish(
        state: ResolutionState,
        target_id: str | None,
        contradictions: int,
        evidence: tuple[str, ...] = (),
        score: float = 0.0,
        margin: float = 0.0,
    ) -> Resolution:
        receipt = ResolutionReceipt(
            state=state,
            observed_targets=len(snapshots),
            eligible_targets=len(eligible),
            contradiction_count=contradictions,
            evidence=tuple(sorted(set(evidence))),
            confidence_bucket=_confidence_bucket(score),
            margin_bucket=_margin_bucket(margin),
        )
        return Resolution(state=state, target_id=target_id, receipt=receipt)

    if query.contradictions:
        return finish(ResolutionState.UNAVAILABLE, None, query.contradictions)
    if not query.raw or not eligible:
        return finish(ResolutionState.UNAVAILABLE, None, 0)

    candidates: list[_Candidate] = []
    fact_contradictions = 0
    for target in eligible:
        candidate = _lexical_candidate(query, target)
        if candidate is None:
            fact_contradictions += 1
        else:
            candidates.append(candidate)
    candidates, directive_contradictions = _apply_directives(candidates, query)
    contradictions = fact_contradictions + directive_contradictions
    if not candidates:
        return finish(ResolutionState.UNAVAILABLE, None, contradictions)

    ranked = sorted(
        candidates,
        key=lambda candidate: (-candidate.score, _reading_order(candidate)),
    )
    best = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    margin = best.score - runner_up
    if best.score < MIN_CONFIDENCE or margin < MIN_MARGIN:
        return finish(
            ResolutionState.AMBIGUOUS, None, contradictions,
            best.evidence, best.score, margin,
        )
    return finish(
        ResolutionState.RESOLVED, best.target.target_id, contradictions,
        best.evidence, best.score, margin,
    )


def measure_synthetic_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and measure the versioned synthetic corpus without case leakage."""
    envelope = _closed_mapping(corpus, _CORPUS_KEYS, "synthetic corpus")
    if (not _supported_schema_version(envelope["schema_version"])
            or envelope["evidence_scope"] != EVIDENCE_SCOPE
            or envelope["physical_validation"] is not False):
        raise ResolutionError("unsupported synthetic corpus declaration")
    if (not isinstance(envelope["scenes"], list)
            or not isinstance(envelope["cases"], list)):
        raise ResolutionError("synthetic scenes and cases must be lists")

    scenes: dict[str, tuple[TargetSnapshot, ...]] = {}
    for raw_scene in envelope["scenes"]:
        scene = _closed_mapping(raw_scene, _SCENE_KEYS, "synthetic scene")
        if not _identifier(scene["scene_id"]) or scene["scene_id"] in scenes:
            raise ResolutionError("invalid or duplicate synthetic scene")
        if not isinstance(scene["targets"], list):
            raise ResolutionError("synthetic scene targets must be a list")
        scenes[scene["scene_id"]] = tuple(
            TargetSnapshot.from_mapping(target) for target in scene["targets"]
        )
        if len({target.target_id for target in scenes[scene["scene_id"]]}) != len(
                scenes[scene["scene_id"]]):
            raise ResolutionError("synthetic target identifiers must be unique")

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
            raise ResolutionError("invalid or duplicate synthetic case")
        seen_cases.add(case["case_id"])
        if case["scene_id"] not in scenes:
            raise ResolutionError("synthetic case references an unknown scene")
        try:
            expected_state = ResolutionState(case["expected_state"])
        except (TypeError, ValueError) as error:
            raise ResolutionError("invalid expected synthetic state") from error
        expected_target = case["expected_target_id"]
        if ((expected_state == ResolutionState.RESOLVED) != _identifier(expected_target)):
            raise ResolutionError("expected target must exist only for resolved cases")
        if (expected_target is not None
                and expected_target not in {
                    target.target_id for target in scenes[case["scene_id"]]
                }):
            raise ResolutionError("expected synthetic target is not in the scene")
        result = resolve_point_and_speak(case["phrase"], scenes[case["scene_id"]])
        totals["cases"] += 1
        totals[result.state.value] += 1
        if result.state == expected_state and result.target_id == expected_target:
            totals["correct_outcomes"] += 1
        if result.state == ResolutionState.RESOLVED and result.target_id != expected_target:
            totals["wrong_target_resolutions"] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": EVIDENCE_SCOPE,
        "physical_validation": False,
        **totals,
    }
