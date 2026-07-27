"""Closed runtime receipt for physically reviewed acoustic calibration.

The candidate half of the A/B this receipt rests on can only be recorded while
the runtime is applying the candidate settings, which before measurement mode
required the very receipt the A/B was meant to authorize.  A corpus whose
candidate pass ran under measurement mode measures the real calibrated front
end, so it is acceptable evidence; the receipt records that it did, so an
override-derived receipt is visibly distinct from an ordinary-path one.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from acoustic_calibration import (
    END_SILENCE_BOUNDS_MS,
    GAIN_CEILING_BOUNDS,
    NOISE_GATE_BOUNDS,
    VAD_THRESHOLD_BOUNDS,
)
from measurement_mode import (
    CALIBRATION_LABEL,
    EVIDENCE_KEY,
    ORDINARY_PATH,
    MeasurementModeError,
    evidence_label,
)


SCHEMA_VERSION = 1
RECEIPT_KIND = "whisper-face/acoustic-calibration-activation"
MIN_PHYSICAL_CASES = 40
MIN_CASES_PER_CONDITION = 8
MIN_IMPROVEMENTS = 3
CONDITIONS = ("clean", "quiet", "noisy", "long-pause")

_ROOT_KEYS = frozenset({
    "schema_version", "kind", "settings", "evidence", "policy",
    "manual_review", "measurement_mode", "source_report_sha256",
})
_SETTING_KEYS = frozenset({
    "gain_ceiling", "noise_gate", "vad_threshold", "end_silence_ms",
    "reverb",
})
_EVIDENCE_KEYS = frozenset({
    "physical_cases", "condition_counts", "recognition_improvements",
    "recognition_regressions", "endpoint_improvements",
    "endpoint_regressions",
})
_POLICY_KEYS = frozenset({
    "minimum_physical_cases", "minimum_cases_per_condition",
    "minimum_improvements", "conditions",
})


class ActivationError(ValueError):
    """Calibration evidence or receipt violated the closed policy."""


@dataclass(frozen=True)
class CalibrationSettings:
    gain_ceiling: float
    noise_gate: float
    vad_threshold: float
    end_silence_ms: int


@dataclass(frozen=True)
class ActivationStatus:
    settings: CalibrationSettings | None
    reason: str
    measurement_mode: str = ORDINARY_PATH

    @property
    def ready(self) -> bool:
        return self.settings is not None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bounded(value: Any, bounds: tuple[float, float]) -> float | None:
    number = _finite(value)
    if number is None or not bounds[0] <= number <= bounds[1]:
        return None
    return number


def _plain_int(value: Any, minimum: int = 0) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
    )


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception as exc:
        raise ActivationError("calibration report is not canonical JSON") \
            from exc


def build_activation_receipt(
    report: Mapping[str, Any],
    *,
    manual_review_approved: bool,
) -> dict[str, Any]:
    if manual_review_approved is not True:
        raise ActivationError("manual review is required")
    if (not isinstance(report, Mapping)
            or report.get("activation_candidate") is not True
            or report.get("verdict") != "manual-review-required"):
        raise ActivationError("passing physical calibration is required")
    settings = report.get("settings")
    evidence = report.get("evidence")
    if (not isinstance(settings, Mapping)
            or set(settings) != _SETTING_KEYS
            or not isinstance(evidence, Mapping)
            or set(evidence) != _EVIDENCE_KEYS):
        raise ActivationError("calibration report is invalid")
    try:
        measurement = evidence_label(
            report.get(EVIDENCE_KEY), arm=CALIBRATION_LABEL)
    except MeasurementModeError as exc:
        raise ActivationError("calibration measurement label is invalid") \
            from exc
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "settings": dict(settings),
        "evidence": dict(evidence),
        "policy": {
            "minimum_physical_cases": MIN_PHYSICAL_CASES,
            "minimum_cases_per_condition": MIN_CASES_PER_CONDITION,
            "minimum_improvements": MIN_IMPROVEMENTS,
            "conditions": list(CONDITIONS),
        },
        "manual_review": True,
        # Disclosure, not a threshold: a candidate pass recorded under
        # measurement mode measured the real calibrated front end.
        "measurement_mode": measurement,
        "source_report_sha256": sha256(
            _canonical_bytes(report)).hexdigest(),
    }
    status = validate_activation_receipt(receipt)
    if not status.ready:
        raise ActivationError(status.reason)
    return receipt


def validate_activation_receipt(value: Any) -> ActivationStatus:
    try:
        if (not isinstance(value, Mapping) or set(value) != _ROOT_KEYS
                or value["schema_version"] != SCHEMA_VERSION
                or value["kind"] != RECEIPT_KIND
                or value["manual_review"] is not True):
            raise ActivationError
        settings = value["settings"]
        evidence = value["evidence"]
        policy = value["policy"]
        digest = value["source_report_sha256"]
        measurement = value["measurement_mode"]
        if measurement not in (ORDINARY_PATH, CALIBRATION_LABEL):
            raise ActivationError
        if (not isinstance(settings, Mapping)
                or set(settings) != _SETTING_KEYS
                or not isinstance(evidence, Mapping)
                or set(evidence) != _EVIDENCE_KEYS
                or not isinstance(policy, Mapping)
                or set(policy) != _POLICY_KEYS
                or dict(policy) != {
                    "minimum_physical_cases": MIN_PHYSICAL_CASES,
                    "minimum_cases_per_condition":
                        MIN_CASES_PER_CONDITION,
                    "minimum_improvements": MIN_IMPROVEMENTS,
                    "conditions": list(CONDITIONS),
                }
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ActivationError
        gain = _bounded(settings["gain_ceiling"], GAIN_CEILING_BOUNDS)
        noise = _bounded(settings["noise_gate"], NOISE_GATE_BOUNDS)
        vad = _bounded(settings["vad_threshold"], VAD_THRESHOLD_BOUNDS)
        end = settings["end_silence_ms"]
        if (gain is None or noise is None or vad is None
                or not _plain_int(end)
                or not END_SILENCE_BOUNDS_MS[0] <= end
                <= END_SILENCE_BOUNDS_MS[1]
                or settings["reverb"] is not None
                or noise >= vad):
            raise ActivationError
        counts = evidence["condition_counts"]
        physical = evidence["physical_cases"]
        if (not isinstance(counts, Mapping)
                or set(counts) != set(CONDITIONS)
                or not all(_plain_int(counts[name],
                                      MIN_CASES_PER_CONDITION)
                           for name in CONDITIONS)
                or not _plain_int(physical, MIN_PHYSICAL_CASES)
                or sum(counts.values()) != physical
                or evidence["recognition_regressions"] != 0
                or evidence["endpoint_regressions"] != 0
                or not _plain_int(evidence["recognition_improvements"])
                or not _plain_int(evidence["endpoint_improvements"])
                or evidence["recognition_improvements"]
                    + evidence["endpoint_improvements"]
                    < MIN_IMPROVEMENTS):
            raise ActivationError
        return ActivationStatus(
            CalibrationSettings(gain, noise, vad, end), "ready", measurement)
    except Exception:
        return ActivationStatus(None, "receipt-invalid")


def load_activation_receipt(path: Path) -> ActivationStatus:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ActivationStatus(None, "receipt-missing")
    except Exception:
        return ActivationStatus(None, "receipt-invalid")
    return validate_activation_receipt(value)


def write_activation_receipt(
    path: Path,
    receipt: Mapping[str, Any],
) -> None:
    if not validate_activation_receipt(receipt).ready:
        raise ActivationError("activation receipt is invalid")
    parent = path.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(json.dumps(
                receipt, ensure_ascii=False, sort_keys=True, indent=2,
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
