# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Evaluate caller-attested physical A/B calibration outcomes.

The private manifest combines the existing closed numeric acoustic telemetry
with categorical baseline/candidate outcomes.  It accepts no audio, text,
device identity, or application context.  Only aggregate counts and bounded
settings enter the report or runtime receipt.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from acoustic_calibration import recommend_calibration
from acoustic_calibration_activation import (
    CONDITIONS,
    MIN_CASES_PER_CONDITION,
    MIN_IMPROVEMENTS,
    MIN_PHYSICAL_CASES,
    ActivationError,
    build_activation_receipt,
    write_activation_receipt,
)


SCHEMA_VERSION = 1
MANIFEST_KIND = "whisper-face/acoustic-calibration-activation-manifest"
REPORT_KIND = "whisper-face/acoustic-calibration-activation-report"
PHYSICAL_SOURCE = "physical-caller-attested"
MAX_CASES = 256

_ROOT_KEYS = frozenset({
    "schema_version", "kind", "telemetry", "cases",
})
_CASE_KEYS = frozenset({
    "case_token", "evidence_source", "condition", "baseline", "candidate",
})
_OUTCOME_KEYS = frozenset({"recognition_correct", "endpoint_correct"})


class BenchmarkError(ValueError):
    """Physical calibration input violated the closed contract."""


def _valid_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 21
        and value.startswith("case-")
        and all(char in "0123456789abcdef" for char in value[5:])
    )


def _outcome(value: Any) -> dict[str, bool] | None:
    if (not isinstance(value, Mapping)
            or set(value) != _OUTCOME_KEYS
            or not all(isinstance(value[key], bool)
                       for key in _OUTCOME_KEYS)):
        return None
    return dict(value)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BenchmarkError("manifest is unavailable or invalid") from exc
    if (not isinstance(root, Mapping) or set(root) != _ROOT_KEYS
            or root["schema_version"] != SCHEMA_VERSION
            or root["kind"] != MANIFEST_KIND
            or not isinstance(root["telemetry"], list)
            or not isinstance(root["cases"], list)
            or len(root["cases"]) > MAX_CASES):
        raise BenchmarkError("manifest is invalid")
    cases = []
    seen = set()
    for supplied in root["cases"]:
        if not isinstance(supplied, Mapping) or set(supplied) != _CASE_KEYS:
            raise BenchmarkError("manifest case is invalid")
        token = supplied["case_token"]
        baseline = _outcome(supplied["baseline"])
        candidate = _outcome(supplied["candidate"])
        if (not _valid_token(token) or token in seen
                or supplied["evidence_source"] != PHYSICAL_SOURCE
                or supplied["condition"] not in CONDITIONS
                or baseline is None or candidate is None):
            raise BenchmarkError("manifest case is invalid")
        seen.add(token)
        cases.append({
            "condition": supplied["condition"],
            "baseline": baseline,
            "candidate": candidate,
        })
    return {"telemetry": root["telemetry"], "cases": cases}


def evaluate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    recommendation = recommend_calibration(manifest["telemetry"])
    cases = manifest["cases"]
    counts = Counter(case["condition"] for case in cases)
    recognition_improvements = sum(
        not case["baseline"]["recognition_correct"]
        and case["candidate"]["recognition_correct"]
        for case in cases)
    recognition_regressions = sum(
        case["baseline"]["recognition_correct"]
        and not case["candidate"]["recognition_correct"]
        for case in cases)
    endpoint_improvements = sum(
        not case["baseline"]["endpoint_correct"]
        and case["candidate"]["endpoint_correct"]
        for case in cases)
    endpoint_regressions = sum(
        case["baseline"]["endpoint_correct"]
        and not case["candidate"]["endpoint_correct"]
        for case in cases)
    evidence = {
        "physical_cases": len(cases),
        "condition_counts": {
            condition: int(counts[condition])
            for condition in CONDITIONS
        },
        "recognition_improvements": recognition_improvements,
        "recognition_regressions": recognition_regressions,
        "endpoint_improvements": endpoint_improvements,
        "endpoint_regressions": endpoint_regressions,
    }
    candidate = (
        recommendation["verdict"] == "keep"
        and len(cases) >= MIN_PHYSICAL_CASES
        and all(counts[condition] >= MIN_CASES_PER_CONDITION
                for condition in CONDITIONS)
        and recognition_regressions == 0
        and endpoint_regressions == 0
        and recognition_improvements + endpoint_improvements
        >= MIN_IMPROVEMENTS
    )
    decisions = recommendation["decisions"]
    settings = {
        "gain_ceiling": decisions["gain_ceiling"]["value"],
        "noise_gate": decisions["noise_gate"]["value"],
        "vad_threshold": decisions["vad_threshold"]["value"],
        "end_silence_ms": decisions["end_silence"]["value"],
        "reverb": None,
    } if recommendation["verdict"] == "keep" else {
        "gain_ceiling": None,
        "noise_gate": None,
        "vad_threshold": None,
        "end_silence_ms": None,
        "reverb": None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "privacy": "aggregate-categorical-and-numeric-only",
        "settings": settings,
        "evidence": evidence,
        "telemetry_policy_verdict": recommendation["verdict"],
        "activation_candidate": candidate,
        "verdict": (
            "manual-review-required" if candidate
            else "evidence-required"),
        "activation_claim": False,
        "quality_claim": False,
    }


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--approve-runtime", type=Path)
    parser.add_argument("--confirm-manual-review", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = evaluate(load_manifest(args.manifest))
        if args.approve_runtime is not None:
            receipt = build_activation_receipt(
                report,
                manual_review_approved=args.confirm_manual_review)
            write_activation_receipt(args.approve_runtime, receipt)
    except (ActivationError, BenchmarkError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_json(report))
    return 0 if report["activation_candidate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
