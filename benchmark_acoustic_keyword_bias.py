# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic policy fixtures for acoustic keyword bias evaluation.

All records are constructed categorical fixtures.  Some fixtures deliberately
exercise the caller-attested physical branch, but this benchmark performs no
recording, ASR, or physical run and makes no recognition-quality claim.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from typing import Any, Sequence

from acoustic_keyword_bias_evaluation import (
    PHYSICAL_SOURCE,
    SYNTHETIC_SOURCE,
    evaluate_keyword_bias,
)
from acoustic_keyword_memory import KeywordCandidate


SCHEMA_VERSION = 1
REPORT_KIND = "whisper-face/acoustic-keyword-bias-synthetic-benchmark"


def _candidate() -> KeywordCandidate:
    return KeywordCandidate(
        keyword="FixtureLexeme",
        app_scope=None,
        observations=3,
        confirmations=2,
        eligible=True,
        status="eligible-not-connected-to-recognition",
    )


def _record(
    index: int,
    *,
    source: str,
    expected: bool,
    unbiased_candidate: bool,
    unbiased_selected: bool,
    biased_candidate: bool,
    biased_selected: bool,
) -> dict[str, Any]:
    return {
        "case_token": f"case-{index:016x}",
        "evidence_source": source,
        "reference": {"keyword_expected": expected},
        "unbiased": {
            "keyword_candidate_present": unbiased_candidate,
            "keyword_selected": unbiased_selected,
        },
        "biased": {
            "keyword_candidate_present": biased_candidate,
            "keyword_selected": biased_selected,
        },
    }


def _balanced(source: str, *, improvements: int = 0,
              regression: bool = False) -> list[dict[str, Any]]:
    records = []
    for index in range(20):
        improved = index < improvements
        records.append(_record(
            index,
            source=source,
            expected=True,
            unbiased_candidate=not improved,
            unbiased_selected=not improved,
            biased_candidate=True,
            biased_selected=True,
        ))
    for index in range(20, 40):
        regressed = regression and index == 20
        records.append(_record(
            index,
            source=source,
            expected=False,
            unbiased_candidate=False,
            unbiased_selected=False,
            biased_candidate=regressed,
            biased_selected=regressed,
        ))
    return records


def synthetic_cases() -> tuple[tuple[str, str, list[dict[str, Any]]], ...]:
    physical_gain = _balanced(PHYSICAL_SOURCE, improvements=3)
    return (
        ("constructed-physical-gain", "keep", physical_gain),
        ("constructed-physical-regression", "kill", _balanced(
            PHYSICAL_SOURCE, improvements=3, regression=True)),
        ("constructed-physical-no-benefit", "kill", _balanced(
            PHYSICAL_SOURCE)),
        ("synthetic-gain", "insufficient-evidence", _balanced(
            SYNTHETIC_SOURCE, improvements=3)),
        ("too-few-constructed-physical", "insufficient-evidence",
         physical_gain[:10] + physical_gain[20:30]),
        ("mixed-sources", "insufficient-evidence",
         physical_gain[:20] + _balanced(SYNTHETIC_SOURCE)[20:]),
    )


def run_synthetic_benchmark() -> dict[str, Any]:
    results = []
    counts: Counter[str] = Counter()
    matched = 0
    for case_id, expected, evidence in synthetic_cases():
        receipt = evaluate_keyword_bias(_candidate(), evidence)
        actual = receipt["verdict"]
        matches = actual == expected
        matched += int(matches)
        counts[actual] += 1
        results.append({
            "case": case_id,
            "expected": expected,
            "actual": actual,
            "matches": matches,
            "reason": receipt["reason"],
            "evidence_scope": receipt["evidence"]["evidence_scope"],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "privacy": "constructed-categorical-fixtures-only",
        "evidence_scope": "deterministic-policy-conformance-only",
        "cases": len(results),
        "matched": matched,
        "counts": {
            verdict: counts[verdict]
            for verdict in ("keep", "kill", "insufficient-evidence")
        },
        "results": results,
        "activation_claim": False,
        "recognition_quality_claim": False,
        "physical_evidence": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    report = run_synthetic_benchmark()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["matched"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
