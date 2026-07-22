# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic synthetic policy cases for acoustic calibration.

The benchmark constructs closed numeric telemetry directly.  It does not
generate audio, exercise a microphone, measure recognition, or activate any
recommendation.  Its keep/kill/insufficient-evidence labels are policy
conformance outcomes, not product-quality claims.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from typing import Any, Sequence

from acoustic_calibration import recommend_calibration


SCHEMA_VERSION = 1
REPORT_KIND = "whisper-face/acoustic-calibration-synthetic-benchmark"


def _record(**changes: float) -> dict[str, float]:
    record = {
        "adaptive_threshold": 0.014,
        "clipped_ratio": 0.0,
        "derived_gain_factor": 1.35,
        "duration_ms": 2000.0,
        "frame_rms_p20": 0.006,
        "frame_rms_p50": 0.022,
        "frame_rms_p95": 0.062,
        "nonfinite_ratio": 0.0,
        "peak_amplitude": 0.42,
        "peak_rms": 0.075,
        "rms": 0.034,
        "sample_count": 32000.0,
        "sample_rate_hz": 16000.0,
        "silence_ratio": 0.24,
        "trailing_silence_ms": 240.0,
        "voiced_fraction": 0.62,
    }
    record.update(changes)
    return record


def synthetic_cases() -> tuple[tuple[str, str, list[dict[str, float]]], ...]:
    """Return fixed numeric shapes spanning representative policy branches."""
    clean = [_record(
        derived_gain_factor=1.25 + index * 0.02,
        trailing_silence_ms=210.0 + index * 10.0,
    ) for index in range(8)]
    return (
        ("stable-separated-signal", "keep", clean),
        ("nonfinite-marker", "kill", [
            _record(nonfinite_ratio=0.001) for _ in range(8)
        ]),
        ("clipping-marker", "kill", [
            _record(clipped_ratio=0.01, peak_amplitude=1.0)
            for _ in range(8)
        ]),
        ("silence-shape", "insufficient-evidence", [
            _record(
                frame_rms_p20=0.0, frame_rms_p50=0.0,
                frame_rms_p95=0.0, peak_rms=0.0, rms=0.0,
                silence_ratio=1.0, voiced_fraction=0.0,
            ) for _ in range(8)
        ]),
        ("noise-shape", "insufficient-evidence", [
            _record(
                adaptive_threshold=0.065,
                frame_rms_p20=0.026, frame_rms_p50=0.035,
                frame_rms_p95=0.045, peak_rms=0.05, rms=0.036,
                silence_ratio=0.01, voiced_fraction=0.95,
            ) for _ in range(8)
        ]),
        ("quiet-speech-shape", "insufficient-evidence", [
            _record(
                adaptive_threshold=0.008,
                derived_gain_factor=6.7,
                frame_rms_p20=0.002, frame_rms_p50=0.006,
                frame_rms_p95=0.012, peak_rms=0.011, rms=0.006,
            ) for _ in range(8)
        ]),
        ("too-few-records", "insufficient-evidence", clean[:2]),
    )


def run_synthetic_benchmark() -> dict[str, Any]:
    results = []
    counts: Counter[str] = Counter()
    matched = 0
    for case_id, expected, telemetry in synthetic_cases():
        policy = recommend_calibration(telemetry)
        actual = policy["verdict"]
        matches = actual == expected
        matched += int(matches)
        counts[actual] += 1
        results.append({
            "case": case_id,
            "expected": expected,
            "actual": actual,
            "matches": matches,
            "reason": policy["reason"],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "privacy": "synthetic-numeric-telemetry-only",
        "evidence_scope": "deterministic-policy-conformance-only",
        "cases": len(results),
        "matched": matched,
        "counts": {
            verdict: counts[verdict]
            for verdict in ("keep", "kill", "insufficient-evidence")
        },
        "results": results,
        "activation_claim": False,
        "quality_claim": False,
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
