"""Private activation state for physically evaluated acoustic keywords.

Keyword memory alone never changes recognition.  This module grants a bounded
prompt-ordering effect only when one eligible memory candidate has a balanced
caller-attested physical evaluation, no observed regression, and explicit
manual approval.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
import unicodedata

from acoustic_keyword_bias_evaluation import (
    MIN_PHYSICAL_NEGATIVE_CASES,
    MIN_PHYSICAL_POSITIVE_CASES,
    MIN_SELECTION_IMPROVEMENTS,
)
from acoustic_keyword_memory import AcousticKeywordMemory, KeywordCandidate


SCHEMA_VERSION = 1
STATE_KIND = "whisper-face/acoustic-keyword-activation"
RUNTIME_EFFECT = "prompt-priority"
MAX_ACTIVE_KEYWORDS = 64

_ROOT_KEYS = frozenset({
    "schema_version", "kind", "runtime_effect", "entries",
})
_ENTRY_KEYS = frozenset({
    "keyword", "app_scope", "evidence", "source_report_sha256",
    "manual_review",
})
_EVIDENCE_KEYS = frozenset({
    "physical_cases", "positive_reference_cases",
    "negative_reference_cases", "selection_improvements",
    "selection_regressions", "positive_candidate_losses",
    "negative_candidate_introductions",
})


class ActivationError(ValueError):
    """Activation state or evidence violated the closed contract."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception as exc:
        raise ActivationError("evaluation is not canonical JSON") from exc


def _normalize_keyword(value: Any) -> str:
    if not isinstance(value, str):
        raise ActivationError("keyword is invalid")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if (not normalized or len(normalized) > 80
            or any(unicodedata.category(char) == "Cc"
                   for char in normalized)):
        raise ActivationError("keyword is invalid")
    return normalized


def _valid_scope(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and len(value) == 20
        and value.startswith("app-")
        and all(char in "0123456789abcdef" for char in value[4:])
    )


def _plain_int(value: Any, minimum: int = 0) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
    )


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": STATE_KIND,
        "runtime_effect": RUNTIME_EFFECT,
        "entries": [],
    }


def build_activation_entry(
    candidate: KeywordCandidate,
    evaluation: Mapping[str, Any],
    *,
    manual_review_approved: bool,
) -> dict[str, Any]:
    """Bind one passing aggregate evaluation to one eligible private term."""
    if not isinstance(candidate, KeywordCandidate) or not candidate.eligible:
        raise ActivationError("eligible keyword memory is required")
    keyword = _normalize_keyword(candidate.keyword)
    if keyword != candidate.keyword or not _valid_scope(candidate.app_scope):
        raise ActivationError("keyword memory candidate is invalid")
    if manual_review_approved is not True:
        raise ActivationError("manual review is required")
    if (not isinstance(evaluation, Mapping)
            or evaluation.get("verdict") != "keep"
            or evaluation.get("reason") !=
            "caller-attested-physical-gain-without-regression"
            or evaluation.get("activation_claim") is not False
            or evaluation.get("recognition_authority") is not False):
        raise ActivationError("passing physical evaluation is required")
    evidence = evaluation.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ActivationError("evaluation evidence is invalid")
    physical = evidence.get("caller_attested_physical_cases")
    positive = evidence.get("positive_reference_cases")
    negative = evidence.get("negative_reference_cases")
    improvements = evidence.get("selection_improvements")
    regressions = evidence.get("selection_regressions")
    losses = evidence.get("positive_candidate_losses")
    introductions = evidence.get("negative_candidate_introductions")
    if (not _plain_int(physical)
            or not _plain_int(positive, MIN_PHYSICAL_POSITIVE_CASES)
            or not _plain_int(negative, MIN_PHYSICAL_NEGATIVE_CASES)
            or physical != positive + negative
            or not _plain_int(improvements, MIN_SELECTION_IMPROVEMENTS)
            or regressions != 0 or losses != 0 or introductions != 0
            or evidence.get("synthetic_cases") != 0):
        raise ActivationError("evaluation evidence is insufficient")
    return {
        "keyword": keyword,
        "app_scope": candidate.app_scope,
        "evidence": {
            "physical_cases": physical,
            "positive_reference_cases": positive,
            "negative_reference_cases": negative,
            "selection_improvements": improvements,
            "selection_regressions": regressions,
            "positive_candidate_losses": losses,
            "negative_candidate_introductions": introductions,
        },
        "source_report_sha256": sha256(
            _canonical_bytes(evaluation)).hexdigest(),
        "manual_review": True,
    }


def validate_state(value: Any) -> dict[str, Any]:
    if (not isinstance(value, Mapping) or set(value) != _ROOT_KEYS
            or value.get("schema_version") != SCHEMA_VERSION
            or value.get("kind") != STATE_KIND
            or value.get("runtime_effect") != RUNTIME_EFFECT):
        raise ActivationError("keyword activation state is invalid")
    entries = value.get("entries")
    if (not isinstance(entries, list)
            or len(entries) > MAX_ACTIVE_KEYWORDS):
        raise ActivationError("keyword activation entries are invalid")
    normalized_entries = []
    seen: set[tuple[str, str | None]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != _ENTRY_KEYS:
            raise ActivationError("keyword activation entry is invalid")
        keyword = _normalize_keyword(entry["keyword"])
        scope = entry["app_scope"]
        evidence = entry["evidence"]
        digest = entry["source_report_sha256"]
        if (keyword != entry["keyword"] or not _valid_scope(scope)
                or not isinstance(evidence, Mapping)
                or set(evidence) != _EVIDENCE_KEYS
                or entry["manual_review"] is not True
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ActivationError("keyword activation entry is invalid")
        physical = evidence["physical_cases"]
        positive = evidence["positive_reference_cases"]
        negative = evidence["negative_reference_cases"]
        if (not _plain_int(physical)
                or not _plain_int(positive, MIN_PHYSICAL_POSITIVE_CASES)
                or not _plain_int(negative, MIN_PHYSICAL_NEGATIVE_CASES)
                or physical != positive + negative
                or not _plain_int(
                    evidence["selection_improvements"],
                    MIN_SELECTION_IMPROVEMENTS,
                )
                or evidence["selection_regressions"] != 0
                or evidence["positive_candidate_losses"] != 0
                or evidence["negative_candidate_introductions"] != 0):
            raise ActivationError("keyword activation evidence is invalid")
        key = (keyword.casefold(), scope)
        if key in seen:
            raise ActivationError("duplicate keyword activation")
        seen.add(key)
        normalized_entries.append(dict(entry))
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": STATE_KIND,
        "runtime_effect": RUNTIME_EFFECT,
        "entries": normalized_entries,
    }


def load_state(path: Path) -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_state(value), "ready"
    except FileNotFoundError:
        return empty_state(), "missing"
    except Exception:
        return empty_state(), "invalid"


def active_keywords(
    path: Path,
    memory: AcousticKeywordMemory,
) -> tuple[tuple[str, ...], str]:
    """Return only globally scoped activations still backed by memory."""
    state, status = load_state(path)
    eligible = {
        (candidate.keyword.casefold(), candidate.app_scope): candidate.keyword
        for candidate in memory.candidates
        if candidate.eligible
    }
    active = tuple(
        eligible[(entry["keyword"].casefold(), entry["app_scope"])]
        for entry in state["entries"]
        if entry["app_scope"] is None
        and (entry["keyword"].casefold(), entry["app_scope"]) in eligible
    )
    return active, status


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    normalized = validate_state(state)
    parent = path.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(json.dumps(
                normalized, ensure_ascii=False, sort_keys=True, indent=2,
                allow_nan=False) + "\n")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def upsert_activation(path: Path, entry: Mapping[str, Any]) -> None:
    state, status = load_state(path)
    if status == "invalid":
        raise ActivationError("keyword activation state is invalid")
    candidate_state = {
        **state,
        "entries": [
            existing for existing in state["entries"]
            if (existing["keyword"].casefold(), existing["app_scope"])
            != (str(entry.get("keyword", "")).casefold(),
                entry.get("app_scope"))
        ] + [dict(entry)],
    }
    _write_state(path, candidate_state)


def remove_activation(
    path: Path,
    keyword: str,
    app_scope: str | None = None,
) -> bool:
    state, status = load_state(path)
    if status == "invalid":
        raise ActivationError("keyword activation state is invalid")
    key = (_normalize_keyword(keyword).casefold(), app_scope)
    retained = [
        entry for entry in state["entries"]
        if (entry["keyword"].casefold(), entry["app_scope"]) != key
    ]
    if len(retained) == len(state["entries"]):
        return False
    _write_state(path, {**state, "entries": retained})
    return True


def clear_activations(path: Path) -> int:
    state, status = load_state(path)
    removed = len(state["entries"]) if status != "invalid" else 0
    _write_state(path, empty_state())
    return removed
