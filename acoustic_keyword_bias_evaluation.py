"""Offline, transcript-free evaluation of acoustic keyword recognition bias.

The evaluator consumes one :class:`KeywordCandidate` from the existing
acoustic keyword memory schema plus caller-supplied categorical ASR evidence.
It compares unbiased and keyword-biased candidate/selection outcomes without
accepting audio, transcripts, surrounding text, application identifiers, or
keyword text in an evidence record.  Returned receipts contain aggregate
counts only and never echo the candidate keyword or case tokens.

This module has no persistence, model, recognizer, or runtime hook.  A
``keep`` verdict means only that a bounded offline candidate survived the
evaluation policy; the separate activation layer still requires explicit
manual review before granting bounded prompt priority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
import unicodedata

from acoustic_keyword_memory import (
    MAX_KEYWORD_CHARS,
    MIN_CONFIRMATIONS,
    MIN_OBSERVATIONS,
    RECOGNITION_EFFECT,
    KeywordCandidate,
)


SCHEMA_VERSION = 1
REPORT_KIND = "whisper-face/acoustic-keyword-bias-evaluation"
PRIVACY = "aggregate-categorical-keyword-outcomes-only"
RUNTIME_EFFECT = "none"

MIN_PHYSICAL_POSITIVE_CASES = 20
MIN_PHYSICAL_NEGATIVE_CASES = 20
MIN_SELECTION_IMPROVEMENTS = 3
MAX_CASES = 256

SYNTHETIC_SOURCE = "synthetic"
PHYSICAL_SOURCE = "physical-caller-attested"

_RECORD_KEYS = frozenset({
    "case_token", "evidence_source", "reference", "unbiased", "biased",
})
_REFERENCE_KEYS = frozenset({"keyword_expected"})
_HYPOTHESIS_KEYS = frozenset({
    "keyword_candidate_present", "keyword_selected",
})
_HEX = frozenset("0123456789abcdef")


def _plain_int(value: Any, *, minimum: int = 0,
               maximum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _closed_mapping(value: Any, keys: frozenset[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != keys:
        return None
    return value


def _valid_case_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 21
        and value.startswith("case-")
        and all(character in _HEX for character in value[5:])
    )


def _valid_candidate(candidate: Any) -> bool:
    if not isinstance(candidate, KeywordCandidate):
        return False
    normalized = (
        " ".join(unicodedata.normalize("NFKC", candidate.keyword).split())
        if isinstance(candidate.keyword, str) else None
    )
    if (normalized != candidate.keyword or not normalized
            or len(normalized) > MAX_KEYWORD_CHARS
            or any(unicodedata.category(character) == "Cc"
                   for character in normalized)):
        return False
    scope = candidate.app_scope
    if not (scope is None or (
            isinstance(scope, str)
            and len(scope) == 20
            and scope.startswith("app-")
            and all(character in _HEX for character in scope[4:]))):
        return False
    if not (
        _plain_int(
            candidate.observations, maximum=MIN_OBSERVATIONS)
        and _plain_int(
            candidate.confirmations, maximum=MIN_CONFIRMATIONS)
        and isinstance(candidate.eligible, bool)
        and isinstance(candidate.status, str)
    ):
        return False
    eligible = (
        candidate.observations >= MIN_OBSERVATIONS
        and candidate.confirmations >= MIN_CONFIRMATIONS
    )
    if eligible:
        expected_status = "eligible-not-connected-to-recognition"
    else:
        expected_status = (
            f"needs-{max(0, MIN_OBSERVATIONS - candidate.observations)}-"
            f"observations-and-"
            f"{max(0, MIN_CONFIRMATIONS - candidate.confirmations)}-"
            "confirmations"
        )
    return candidate.eligible == eligible and candidate.status == expected_status


def _valid_hypothesis(value: Any) -> Mapping[str, bool] | None:
    hypothesis = _closed_mapping(value, _HYPOTHESIS_KEYS)
    if hypothesis is None or any(
            not isinstance(hypothesis[field], bool)
            for field in _HYPOTHESIS_KEYS):
        return None
    if (hypothesis["keyword_selected"]
            and not hypothesis["keyword_candidate_present"]):
        return None
    return hypothesis  # type: ignore[return-value]


def _valid_record(value: Any) -> dict[str, Any] | None:
    record = _closed_mapping(value, _RECORD_KEYS)
    if record is None or not _valid_case_token(record["case_token"]):
        return None
    if record["evidence_source"] not in {SYNTHETIC_SOURCE, PHYSICAL_SOURCE}:
        return None
    reference = _closed_mapping(record["reference"], _REFERENCE_KEYS)
    if (reference is None
            or not isinstance(reference["keyword_expected"], bool)):
        return None
    unbiased = _valid_hypothesis(record["unbiased"])
    biased = _valid_hypothesis(record["biased"])
    if unbiased is None or biased is None:
        return None
    return {
        "case_token": record["case_token"],
        "evidence_source": record["evidence_source"],
        "keyword_expected": reference["keyword_expected"],
        "unbiased_candidate": unbiased["keyword_candidate_present"],
        "unbiased_selected": unbiased["keyword_selected"],
        "biased_candidate": biased["keyword_candidate_present"],
        "biased_selected": biased["keyword_selected"],
    }


def _empty_evidence() -> dict[str, Any]:
    return {
        "cases": 0,
        "evidence_scope": "none",
        "synthetic_cases": 0,
        "caller_attested_physical_cases": 0,
        "independently_verified_physical_cases": 0,
        "positive_reference_cases": 0,
        "negative_reference_cases": 0,
        "unbiased_correct": 0,
        "biased_correct": 0,
        "selection_improvements": 0,
        "selection_regressions": 0,
        "positive_candidate_recoveries": 0,
        "positive_candidate_losses": 0,
        "negative_candidate_introductions": 0,
    }


def _receipt(
    verdict: str,
    reason: str,
    *,
    candidate: KeywordCandidate | None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_evidence = {
        "observations": candidate.observations if candidate is not None else 0,
        "confirmations": (
            candidate.confirmations if candidate is not None else 0),
        "eligible": candidate.eligible if candidate is not None else False,
        "recognition_effect": RECOGNITION_EFFECT,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "privacy": PRIVACY,
        "runtime_effect": RUNTIME_EFFECT,
        "verdict": verdict,
        "reason": reason,
        "policy": {
            "minimum_physical_positive_cases": MIN_PHYSICAL_POSITIVE_CASES,
            "minimum_physical_negative_cases": MIN_PHYSICAL_NEGATIVE_CASES,
            "minimum_selection_improvements": MIN_SELECTION_IMPROVEMENTS,
            "maximum_cases": MAX_CASES,
            "synthetic_keep_allowed": False,
        },
        "candidate_evidence": candidate_evidence,
        "evidence": dict(evidence or _empty_evidence()),
        "activation_claim": False,
        "recognition_authority": False,
        "recognition_quality_claim": False,
    }


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    synthetic_cases = sum(
        record["evidence_source"] == SYNTHETIC_SOURCE for record in records)
    physical_cases = len(records) - synthetic_cases
    if synthetic_cases and physical_cases:
        evidence_scope = "mixed-must-be-separated"
    elif synthetic_cases:
        evidence_scope = "synthetic-only"
    elif physical_cases:
        evidence_scope = "caller-attested-physical-only"
    else:
        evidence_scope = "none"

    positive = sum(record["keyword_expected"] for record in records)
    negative = len(records) - positive
    unbiased_correct = sum(
        record["unbiased_selected"] == record["keyword_expected"]
        for record in records
    )
    biased_correct = sum(
        record["biased_selected"] == record["keyword_expected"]
        for record in records
    )
    selection_improvements = sum(
        record["unbiased_selected"] != record["keyword_expected"]
        and record["biased_selected"] == record["keyword_expected"]
        for record in records
    )
    selection_regressions = sum(
        record["unbiased_selected"] == record["keyword_expected"]
        and record["biased_selected"] != record["keyword_expected"]
        for record in records
    )
    positive_candidate_recoveries = sum(
        record["keyword_expected"]
        and not record["unbiased_candidate"]
        and record["biased_candidate"]
        for record in records
    )
    positive_candidate_losses = sum(
        record["keyword_expected"]
        and record["unbiased_candidate"]
        and not record["biased_candidate"]
        for record in records
    )
    negative_candidate_introductions = sum(
        not record["keyword_expected"]
        and not record["unbiased_candidate"]
        and record["biased_candidate"]
        for record in records
    )
    return {
        "cases": len(records),
        "evidence_scope": evidence_scope,
        "synthetic_cases": synthetic_cases,
        "caller_attested_physical_cases": physical_cases,
        "independently_verified_physical_cases": 0,
        "positive_reference_cases": positive,
        "negative_reference_cases": negative,
        "unbiased_correct": unbiased_correct,
        "biased_correct": biased_correct,
        "selection_improvements": selection_improvements,
        "selection_regressions": selection_regressions,
        "positive_candidate_recoveries": positive_candidate_recoveries,
        "positive_candidate_losses": positive_candidate_losses,
        "negative_candidate_introductions": negative_candidate_introductions,
    }


def evaluate_keyword_bias(
    candidate: KeywordCandidate,
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a conservative aggregate receipt without retaining inputs."""
    if not _valid_candidate(candidate):
        return _receipt(
            "kill", "invalid-memory-candidate", candidate=None)
    if (isinstance(evidence, (str, bytes, bytearray))
            or not isinstance(evidence, Sequence)):
        return _receipt(
            "kill", "invalid-evidence", candidate=candidate)
    if len(evidence) > MAX_CASES:
        return _receipt(
            "kill", "evidence-batch-out-of-bounds", candidate=candidate)

    records: list[dict[str, Any]] = []
    case_tokens: set[str] = set()
    for supplied in evidence:
        record = _valid_record(supplied)
        if record is None:
            return _receipt(
                "kill", "invalid-evidence", candidate=candidate)
        if record["case_token"] in case_tokens:
            return _receipt(
                "kill", "duplicate-evidence", candidate=candidate)
        case_tokens.add(record["case_token"])
        records.append(record)

    aggregate = _aggregate(records)
    if aggregate["evidence_scope"] == "mixed-must-be-separated":
        return _receipt(
            "insufficient-evidence", "evidence-sources-must-be-separated",
            candidate=candidate, evidence=aggregate)
    if not candidate.eligible:
        return _receipt(
            "insufficient-evidence", "memory-eligibility-not-met",
            candidate=candidate, evidence=aggregate)
    if (aggregate["selection_regressions"]
            or aggregate["positive_candidate_losses"]
            or aggregate["negative_candidate_introductions"]):
        return _receipt(
            "kill", "recognition-regression-observed",
            candidate=candidate, evidence=aggregate)
    if aggregate["evidence_scope"] != "caller-attested-physical-only":
        return _receipt(
            "insufficient-evidence", "physical-evidence-required",
            candidate=candidate, evidence=aggregate)
    if (aggregate["positive_reference_cases"] < MIN_PHYSICAL_POSITIVE_CASES
            or aggregate["negative_reference_cases"]
            < MIN_PHYSICAL_NEGATIVE_CASES):
        return _receipt(
            "insufficient-evidence", "minimum-physical-evidence-not-met",
            candidate=candidate, evidence=aggregate)
    improvements = aggregate["selection_improvements"]
    if improvements == 0:
        return _receipt(
            "kill", "no-measured-selection-benefit",
            candidate=candidate, evidence=aggregate)
    if improvements < MIN_SELECTION_IMPROVEMENTS:
        return _receipt(
            "insufficient-evidence", "minimum-improvement-not-met",
            candidate=candidate, evidence=aggregate)
    return _receipt(
        "keep", "caller-attested-physical-gain-without-regression",
        candidate=candidate, evidence=aggregate)
