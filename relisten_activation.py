"""Closed activation receipts for Selective Re-listen.

The benchmark report contains only aggregate evidence.  Runtime consumes an
even smaller deterministic receipt and never reads manifests, audio, expected
text, or verifier transcripts.
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


SCHEMA_VERSION = 1
RECEIPT_KIND = "whisper-face/relisten-runtime-activation"
ENGINE_ID = "prewarmed_whisper_tiny"
MODEL_REPO = "mlx-community/whisper-tiny"
MODEL_REVISION = "78c52ab98ca87f570bc57ad852e15ef7060f9f76"

MIN_REAL_SAMPLES = 40
MIN_REAL_SAMPLES_PER_OUTCOME = 20
MIN_EXACT_ACCURACY_PCT = 95.0
MAX_P95_LATENCY_MS = 650.0
MAX_REFUSALS = 0

_ROOT_KEYS = frozenset({
    "schema_version", "kind", "engine_id", "model_repo", "model_revision",
    "evidence", "limits", "manual_review",
})
_EVIDENCE_KEYS = frozenset({
    "real_samples", "real_confirmed_cases", "real_contradicted_cases",
    "exact_accuracy_pct", "p95_latency_ms", "refusals",
    "source_report_sha256",
})
_LIMIT_KEYS = frozenset({
    "minimum_real_samples", "minimum_real_samples_per_outcome",
    "minimum_exact_accuracy_pct", "maximum_p95_latency_ms",
    "maximum_refusals",
})
_MANUAL_REVIEW_KEYS = frozenset({"approved"})


class ActivationError(ValueError):
    """Evidence cannot authorize runtime activation."""


@dataclass(frozen=True)
class ActivationStatus:
    ready: bool
    reason: str


def _plain_int(value: Any, minimum: int = 0) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
    )


def _finite_number(value: Any, minimum: float = 0.0) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= minimum
    )


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception as exc:
        raise ActivationError("activation report is not canonical JSON") \
            from exc


def activation_candidate(report: Mapping[str, Any]) -> ActivationStatus:
    """Evaluate closed aggregate evidence without making an activation claim."""
    try:
        if not isinstance(report, Mapping):
            raise ActivationError
        activation = report["activation_evidence"]
        counts = report["evidence_counts"]
        engines = report["engines"]
        if (not isinstance(activation, Mapping)
                or not isinstance(counts, Mapping)
                or not isinstance(engines, list)):
            raise ActivationError
        if counts.get("synthetic-test") != 0:
            return ActivationStatus(False, "synthetic-evidence-present")
        real_samples = activation.get("real_samples")
        confirmed = activation.get("real_confirmed_cases")
        contradicted = activation.get("real_contradicted_cases")
        if (not _plain_int(real_samples)
                or not _plain_int(confirmed)
                or not _plain_int(contradicted)
                or real_samples < MIN_REAL_SAMPLES
                or confirmed < MIN_REAL_SAMPLES_PER_OUTCOME
                or contradicted < MIN_REAL_SAMPLES_PER_OUTCOME
                or confirmed + contradicted != real_samples):
            return ActivationStatus(False, "insufficient-real-evidence")

        candidates = [
            engine for engine in engines
            if isinstance(engine, Mapping)
            and engine.get("engine_id") == ENGINE_ID
        ]
        if len(candidates) != 1:
            return ActivationStatus(False, "engine-evidence-unavailable")
        engine = candidates[0]
        latency = engine.get("latency_ms")
        refusals = engine.get("refusals")
        if (engine.get("availability") != "measured"
                or engine.get("cases") != real_samples
                or not _finite_number(engine.get("exact_case_accuracy_pct"))
                or not isinstance(latency, Mapping)
                or not _finite_number(latency.get("p95"))
                or not isinstance(refusals, Mapping)
                or not all(_plain_int(value) for value in refusals.values())):
            return ActivationStatus(False, "engine-evidence-invalid")
        if float(engine["exact_case_accuracy_pct"]) \
                < MIN_EXACT_ACCURACY_PCT:
            return ActivationStatus(False, "accuracy-below-threshold")
        if float(latency["p95"]) > MAX_P95_LATENCY_MS:
            return ActivationStatus(False, "latency-above-threshold")
        if sum(refusals.values()) > MAX_REFUSALS:
            return ActivationStatus(False, "refusals-above-threshold")
        return ActivationStatus(True, "manual-review-required")
    except Exception:
        return ActivationStatus(False, "report-invalid")


def build_activation_receipt(
    report: Mapping[str, Any],
    *,
    manual_review_approved: bool,
) -> dict[str, Any]:
    """Create a deterministic receipt only after evidence and human review."""
    status = activation_candidate(report)
    if not status.ready:
        raise ActivationError(status.reason)
    if manual_review_approved is not True:
        raise ActivationError("manual-review-required")
    activation = report["activation_evidence"]
    engine = next(
        item for item in report["engines"]
        if isinstance(item, Mapping) and item.get("engine_id") == ENGINE_ID
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "engine_id": ENGINE_ID,
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "evidence": {
            "real_samples": activation["real_samples"],
            "real_confirmed_cases": activation["real_confirmed_cases"],
            "real_contradicted_cases": activation[
                "real_contradicted_cases"],
            "exact_accuracy_pct": engine["exact_case_accuracy_pct"],
            "p95_latency_ms": engine["latency_ms"]["p95"],
            "refusals": sum(engine["refusals"].values()),
            "source_report_sha256": sha256(
                _canonical_bytes(report)).hexdigest(),
        },
        "limits": {
            "minimum_real_samples": MIN_REAL_SAMPLES,
            "minimum_real_samples_per_outcome":
                MIN_REAL_SAMPLES_PER_OUTCOME,
            "minimum_exact_accuracy_pct": MIN_EXACT_ACCURACY_PCT,
            "maximum_p95_latency_ms": MAX_P95_LATENCY_MS,
            "maximum_refusals": MAX_REFUSALS,
        },
        "manual_review": {"approved": True},
    }


def validate_activation_receipt(value: Any) -> ActivationStatus:
    """Validate exact schema, current model identity, and current thresholds."""
    try:
        if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
            raise ActivationError
        evidence = value["evidence"]
        limits = value["limits"]
        review = value["manual_review"]
        if (not isinstance(evidence, Mapping)
                or set(evidence) != _EVIDENCE_KEYS
                or not isinstance(limits, Mapping)
                or set(limits) != _LIMIT_KEYS
                or not isinstance(review, Mapping)
                or set(review) != _MANUAL_REVIEW_KEYS):
            raise ActivationError
        if (value["schema_version"] != SCHEMA_VERSION
                or value["kind"] != RECEIPT_KIND
                or value["engine_id"] != ENGINE_ID
                or value["model_repo"] != MODEL_REPO
                or value["model_revision"] != MODEL_REVISION
                or dict(limits) != {
                    "minimum_real_samples": MIN_REAL_SAMPLES,
                    "minimum_real_samples_per_outcome":
                        MIN_REAL_SAMPLES_PER_OUTCOME,
                    "minimum_exact_accuracy_pct":
                        MIN_EXACT_ACCURACY_PCT,
                    "maximum_p95_latency_ms": MAX_P95_LATENCY_MS,
                    "maximum_refusals": MAX_REFUSALS,
                }
                or review.get("approved") is not True):
            return ActivationStatus(False, "receipt-policy-mismatch")
        source_hash = evidence.get("source_report_sha256")
        if (not isinstance(source_hash, str) or len(source_hash) != 64
                or any(char not in "0123456789abcdef"
                       for char in source_hash)):
            return ActivationStatus(False, "receipt-invalid")
        if (not _plain_int(evidence.get("real_samples"), MIN_REAL_SAMPLES)
                or not _plain_int(
                    evidence.get("real_confirmed_cases"),
                    MIN_REAL_SAMPLES_PER_OUTCOME,
                )
                or not _plain_int(
                    evidence.get("real_contradicted_cases"),
                    MIN_REAL_SAMPLES_PER_OUTCOME,
                )
                or evidence["real_confirmed_cases"]
                    + evidence["real_contradicted_cases"]
                    != evidence["real_samples"]
                or not _finite_number(evidence.get("exact_accuracy_pct"))
                or float(evidence["exact_accuracy_pct"])
                    < MIN_EXACT_ACCURACY_PCT
                or not _finite_number(evidence.get("p95_latency_ms"))
                or float(evidence["p95_latency_ms"])
                    > MAX_P95_LATENCY_MS
                or not _plain_int(evidence.get("refusals"))
                or evidence["refusals"] > MAX_REFUSALS):
            return ActivationStatus(False, "receipt-evidence-insufficient")
        return ActivationStatus(True, "ready")
    except Exception:
        return ActivationStatus(False, "receipt-invalid")


def load_activation_receipt(path: Path) -> ActivationStatus:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ActivationStatus(False, "receipt-missing")
    except Exception:
        return ActivationStatus(False, "receipt-invalid")
    return validate_activation_receipt(value)


def write_activation_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    """Atomically write one validated content-free local activation receipt."""
    status = validate_activation_receipt(receipt)
    if not status.ready:
        raise ActivationError(status.reason)
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
