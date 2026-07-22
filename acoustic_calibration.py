"""Conservative policy over privacy-safe acoustic capture telemetry.

This module is an offline policy foundation only.  It accepts the existing
closed numeric ``utterance_acoustic`` trace payload, returns bounded candidate
settings or fixed refusals, and never receives audio, text, device identity, or
application context.  It does not persist state or change capture behavior.

The available telemetry cannot measure room impulse response or recognition
quality.  Reverb therefore remains explicitly unavailable, and a ``keep``
verdict means only that a synthetic/offline policy candidate survived the
numeric safety gates.  It is not an activation or quality claim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any


SCHEMA_VERSION = 1
REPORT_KIND = "whisper-face/acoustic-calibration-policy"
PRIVACY = "closed-numeric-telemetry-only"
RUNTIME_EFFECT = "none"

# Intentionally matches dictate.py PERFORMANCE_TRACE_SCHEMAS and
# performance_lab.py RUNTIME_TRACE_SCHEMAS.  Focused tests prevent drift while
# keeping this policy importable without runtime audio, UI, or model packages.
ACOUSTIC_TELEMETRY_FIELDS = (
    "adaptive_threshold",
    "clipped_ratio",
    "derived_gain_factor",
    "duration_ms",
    "frame_rms_p20",
    "frame_rms_p50",
    "frame_rms_p95",
    "nonfinite_ratio",
    "peak_amplitude",
    "peak_rms",
    "rms",
    "sample_count",
    "sample_rate_hz",
    "silence_ratio",
    "trailing_silence_ms",
    "voiced_fraction",
)

MIN_RECORDS = 8
MAX_RECORDS = 256
MIN_TOTAL_DURATION_MS = 8_000.0
MAX_RECORD_DURATION_MS = 600_000.0
SAMPLE_RATE_BOUNDS_HZ = (8_000.0, 192_000.0)

GAIN_CEILING_BOUNDS = (1.0, 4.0)
NOISE_GATE_BOUNDS = (0.004, 0.03)
VAD_THRESHOLD_BOUNDS = (0.006, 0.05)
END_SILENCE_BOUNDS_MS = (180, 600)

_RATIO_FIELDS = frozenset({
    "clipped_ratio", "nonfinite_ratio", "silence_ratio", "voiced_fraction",
})
_VERDICTS = frozenset({"keep", "kill", "insufficient-evidence"})
_CONTROL_ORDER = (
    "gain_ceiling", "noise_gate", "vad_threshold", "end_silence",
)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _validate_record(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) != set(ACOUSTIC_TELEMETRY_FIELDS):
        return None
    record: dict[str, float] = {}
    for field in ACOUSTIC_TELEMETRY_FIELDS:
        number = _finite_number(value[field])
        if number is None:
            return None
        if field in _RATIO_FIELDS and number > 1.0:
            return None
        record[field] = number
    if not (0.0 <= record["adaptive_threshold"] <= 0.1):
        return None
    if not (1.0 <= record["derived_gain_factor"] <= 8.0):
        return None
    if not (record["frame_rms_p20"] <= record["frame_rms_p50"]
            <= record["frame_rms_p95"]):
        return None
    if record["peak_amplitude"] < max(
            record["frame_rms_p95"], record["peak_rms"], record["rms"]):
        return None
    if record["trailing_silence_ms"] > record["duration_ms"]:
        return None
    if (record["sample_count"] <= 0.0
            or not (SAMPLE_RATE_BOUNDS_HZ[0] <= record["sample_rate_hz"]
                    <= SAMPLE_RATE_BOUNDS_HZ[1])
            or not 0.0 < record["duration_ms"] <= MAX_RECORD_DURATION_MS):
        return None
    expected_duration = (
        record["sample_count"] / record["sample_rate_hz"] * 1000.0)
    # One sample of tolerance handles telemetry serialization rounding while
    # rejecting internally inconsistent or spliced metric objects.
    tolerance = max(0.001, 1000.0 / record["sample_rate_hz"])
    if abs(expected_duration - record["duration_ms"]) > tolerance:
        return None
    return record


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return min(bounds[1], max(bounds[0], value))


def _refused_decisions(reason: str) -> dict[str, dict[str, Any]]:
    decisions = {
        control: {
            "state": "refused",
            "value": None,
            "reason": reason,
        }
        for control in _CONTROL_ORDER
    }
    decisions["reverb"] = {
        "state": "unavailable",
        "value": None,
        "reason": "reverb-metric-unavailable",
    }
    return decisions


def _base_report(
        verdict: str, reason: str, *, records: int,
        duration_ms: float) -> dict[str, Any]:
    if verdict not in _VERDICTS:  # pragma: no cover - internal invariant
        raise ValueError("unsupported policy verdict")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "privacy": PRIVACY,
        "runtime_effect": RUNTIME_EFFECT,
        "verdict": verdict,
        "reason": reason,
        "evidence": {
            "records": records,
            "duration_ms": round(duration_ms, 3),
            "synthetic_or_caller_supplied_numeric_only": True,
            "physical_conditions_verified": False,
            "recognition_quality_measured": False,
        },
        "activation_claim": False,
        "quality_claim": False,
    }


def _refusal(
        verdict: str, reason: str, *, records: int,
        duration_ms: float) -> dict[str, Any]:
    report = _base_report(
        verdict, reason, records=records, duration_ms=duration_ms)
    report["decisions"] = _refused_decisions(reason)
    return report


def recommend_calibration(telemetry: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate a caller-bounded batch without echoing any input value.

    Every rejection uses a fixed reason vocabulary.  The returned evidence is
    limited to counts and total duration; no per-record values are retained.
    """
    if (isinstance(telemetry, (str, bytes, bytearray))
            or not isinstance(telemetry, Sequence)):
        return _refusal(
            "kill", "invalid-telemetry", records=0, duration_ms=0.0)
    supplied_records = len(telemetry)
    if supplied_records > MAX_RECORDS:
        return _refusal(
            "kill", "telemetry-batch-out-of-bounds",
            records=0, duration_ms=0.0)

    records: list[dict[str, float]] = []
    for value in telemetry:
        record = _validate_record(value)
        if record is None:
            return _refusal(
                "kill", "invalid-telemetry", records=0, duration_ms=0.0)
        records.append(record)

    duration_ms = sum(record["duration_ms"] for record in records)
    if any(record["nonfinite_ratio"] > 0.0 for record in records):
        return _refusal(
            "kill", "nonfinite-samples-observed",
            records=len(records), duration_ms=duration_ms)
    if any(record["clipped_ratio"] > 0.0
           or record["peak_amplitude"] >= 0.99 for record in records):
        return _refusal(
            "kill", "clipping-observed",
            records=len(records), duration_ms=duration_ms)
    if len(records) < MIN_RECORDS or duration_ms < MIN_TOTAL_DURATION_MS:
        return _refusal(
            "insufficient-evidence", "minimum-evidence-not-met",
            records=len(records), duration_ms=duration_ms)
    if any(record["silence_ratio"] >= 0.98
           or record["voiced_fraction"] <= 0.02
           for record in records):
        return _refusal(
            "insufficient-evidence", "silence-ambiguous",
            records=len(records), duration_ms=duration_ms)
    if any(record["frame_rms_p95"] < 0.015
           or record["peak_rms"] < 0.012 for record in records):
        return _refusal(
            "insufficient-evidence", "quiet-speech-ambiguous",
            records=len(records), duration_ms=duration_ms)
    if any(record["frame_rms_p20"] >= 0.025
           or record["frame_rms_p95"]
           < record["frame_rms_p20"] * 3.0 for record in records):
        return _refusal(
            "insufficient-evidence", "noise-separation-ambiguous",
            records=len(records), duration_ms=duration_ms)
    if any(record["trailing_silence_ms"] < 80.0 for record in records):
        return _refusal(
            "insufficient-evidence", "end-silence-ambiguous",
            records=len(records), duration_ms=duration_ms)

    headroom_ceiling = 0.85 / max(
        record["peak_amplitude"] for record in records)
    if headroom_ceiling < GAIN_CEILING_BOUNDS[0]:
        return _refusal(
            "insufficient-evidence", "gain-headroom-ambiguous",
            records=len(records), duration_ms=duration_ms)
    gain = round(_clamp(min(
        _percentile(
            [record["derived_gain_factor"] for record in records], 0.90),
        headroom_ceiling,
    ), GAIN_CEILING_BOUNDS), 3)
    noise_gate = round(_clamp(
        _percentile(
            [record["frame_rms_p20"] for record in records], 0.90) * 1.5,
        NOISE_GATE_BOUNDS), 4)
    vad_threshold = round(_clamp(max(
        noise_gate * 1.5,
        _percentile(
            [record["adaptive_threshold"] for record in records], 0.90),
    ), VAD_THRESHOLD_BOUNDS), 4)
    end_silence = int(round(_clamp(
        _percentile(
            [record["trailing_silence_ms"] for record in records], 0.75),
        END_SILENCE_BOUNDS_MS)))

    report = _base_report(
        "keep", "bounded-candidate-only",
        records=len(records), duration_ms=duration_ms)
    report["decisions"] = {
        "gain_ceiling": {
            "state": "recommend",
            "value": gain,
            "unit": "factor",
            "bounds": list(GAIN_CEILING_BOUNDS),
        },
        "noise_gate": {
            "state": "recommend",
            "value": noise_gate,
            "unit": "linear-rms",
            "bounds": list(NOISE_GATE_BOUNDS),
        },
        "vad_threshold": {
            "state": "recommend",
            "value": vad_threshold,
            "unit": "linear-rms",
            "bounds": list(VAD_THRESHOLD_BOUNDS),
        },
        "end_silence": {
            "state": "recommend",
            "value": end_silence,
            "unit": "milliseconds",
            "bounds": list(END_SILENCE_BOUNDS_MS),
        },
        "reverb": {
            "state": "unavailable",
            "value": None,
            "reason": "reverb-metric-unavailable",
        },
    }
    return report
