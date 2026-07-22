"""Aggregate checked-in synthetic evidence into a public JSON scorecard.

The report is deliberately aggregate-only.  It contains no transcripts, case
identifiers, target identifiers, paths, timings, or model output, and it never
claims physical validation.  Existing benchmark functions remain the source of
truth for every measured result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmark_consequence_routing import (
    DEFAULT_CASES as CONSEQUENCE_CASES,
    evaluate_case as evaluate_consequence_case,
    load_cases as load_consequence_cases,
)
from benchmark_insertion_reliability import (
    DEFAULT_CASES as INSERTION_CASES,
    build_report as build_insertion_report,
)
from benchmark_voice_compiler import (
    DEFAULT_CASES as COMPILER_CASES,
    evaluate_golden_cases,
    load_cases as load_compiler_cases,
)
from drop_to_target import measure_synthetic_corpus as measure_drop_corpus
from point_and_speak_resolver import (
    measure_synthetic_corpus as measure_point_corpus,
)


SCHEMA_VERSION = 1
REPORT_KIND = "whisper-face/public-synthetic-scorecard"
EVIDENCE_SCOPE = "checked-in-synthetic-corpora-only"
PRIVACY = "transcript-free-aggregate-only"
HERE = Path(__file__).resolve().parent
POINT_CASES = HERE / "benchmarks" / "point_and_speak_cases.json"
DROP_CASES = HERE / "benchmarks" / "drop_to_target_cases.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("synthetic corpus must be a JSON object")
    return value


def _count(value: Any, label: str) -> int:
    if (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _suite(
    suite_id: str,
    evidence_scope: str,
    *,
    cases: Any,
    passed: Any,
    critical_metric: str,
    critical_failures: Any,
) -> dict[str, Any]:
    case_count = _count(cases, f"{suite_id} cases")
    passed_count = _count(passed, f"{suite_id} passed")
    failures = _count(critical_failures, f"{suite_id} critical failures")
    if passed_count > case_count:
        raise ValueError(f"{suite_id} passed count exceeds case count")
    return {
        "suite_id": suite_id,
        "evidence_scope": evidence_scope,
        "physical_validation": False,
        "cases": case_count,
        "passed": passed_count,
        "failed": case_count - passed_count,
        "critical_metric": critical_metric,
        "critical_failures": failures,
    }


def _compiler_suite() -> dict[str, Any]:
    report = evaluate_golden_cases(load_compiler_cases(COMPILER_CASES))
    return _suite(
        "voice_compiler",
        "checked-in-golden-corpus",
        cases=report["total"],
        passed=report["passed"],
        critical_metric="case_expectation_failures",
        critical_failures=report["total"] - report["passed"],
    )


def _consequence_suite() -> dict[str, Any]:
    results = [
        evaluate_consequence_case(case)
        for case in load_consequence_cases(CONSEQUENCE_CASES)
    ]
    passed = sum(result["passed"] is True for result in results)
    return _suite(
        "consequence_routing",
        "synthetic-selector-only",
        cases=len(results),
        passed=passed,
        critical_metric="case_expectation_failures",
        critical_failures=len(results) - passed,
    )


def _insertion_suite() -> dict[str, Any]:
    report = build_insertion_report(INSERTION_CASES, iterations=1)
    if (report.get("evidence_scope") != "adapter-simulation-only"
            or report.get("physical_evidence") is not False
            or report.get("real_apps_exercised") != 0):
        raise ValueError("insertion report exceeded synthetic evidence scope")
    invariant = report.get("attempt_invariant")
    if not isinstance(invariant, dict):
        raise ValueError("insertion report omitted attempt invariant")
    return _suite(
        "insertion_reliability",
        report["evidence_scope"],
        cases=report["cases"],
        passed=report["passed"],
        critical_metric="at_most_once_invariant_violations",
        critical_failures=invariant.get("violations"),
    )


def _target_suite(
    suite_id: str,
    report: dict[str, Any],
    expected_scope: str,
) -> dict[str, Any]:
    if (report.get("evidence_scope") != expected_scope
            or report.get("physical_validation") is not False):
        raise ValueError(f"{suite_id} exceeded synthetic evidence scope")
    return _suite(
        suite_id,
        report["evidence_scope"],
        cases=report["cases"],
        passed=report["correct_outcomes"],
        critical_metric="wrong_target_resolutions",
        critical_failures=report["wrong_target_resolutions"],
    )


def build_public_scorecard() -> dict[str, Any]:
    """Build a deterministic, transcript-free report from fixed local corpora."""
    point = measure_point_corpus(_load_json(POINT_CASES))
    drop = measure_drop_corpus(_load_json(DROP_CASES))
    suites = (
        _compiler_suite(),
        _consequence_suite(),
        _insertion_suite(),
        _target_suite(
            "point_and_speak", point, "synthetic-resolution-only"),
        _target_suite(
            "drop_to_target", drop, "synthetic-decision-only"),
    )
    total_cases = sum(suite["cases"] for suite in suites)
    total_passed = sum(suite["passed"] for suite in suites)
    critical_failures = sum(
        suite["critical_failures"] for suite in suites)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "privacy": PRIVACY,
        "evidence_scope": EVIDENCE_SCOPE,
        "physical_validation": False,
        "real_apps_exercised": 0,
        "audio_or_model_runs": False,
        "suites": list(suites),
        "totals": {
            "suites": len(suites),
            "cases": total_cases,
            "passed": total_passed,
            "failed": total_cases - total_passed,
            "critical_failures": critical_failures,
            "all_passed": (
                total_passed == total_cases and critical_failures == 0),
        },
    }


def render_json(report: dict[str, Any] | None = None) -> str:
    """Render stable machine-readable JSON without adding environment data."""
    return json.dumps(
        report if report is not None else build_public_scorecard(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    print(render_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
