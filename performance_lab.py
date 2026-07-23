# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Offline, privacy-safe performance and accuracy laboratory.

The committed corpus contains synthetic text and scenario labels, never audio
or personal dictation. Runtime observations use counters, route labels, and
timings only; reports never need transcript contents.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE / "benchmarks" / "representative_dictation_cases.json"
DEFAULT_BUDGETS = HERE / "benchmarks" / "performance_budgets.json"
DEFAULT_MODEL_SCORECARD = HERE / "benchmarks" / "model_scorecard.json"
_MAX_HUB_RESPONSE_BYTES = 1_000_000


def load_corpus(path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    """Load and validate the public synthetic dictation corpus."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported representative corpus schema")
    if payload.get("privacy") != "synthetic-text-only":
        raise ValueError("representative corpus must be synthetic-text-only")
    dimensions = payload.get("required_dimensions")
    cases = payload.get("cases")
    if not isinstance(dimensions, list) or not dimensions:
        raise ValueError("representative corpus needs required dimensions")
    if not isinstance(cases, list) or not cases:
        raise ValueError("representative corpus needs cases")
    identifiers: set[str] = set()
    allowed_dimensions = set(dimensions)
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("every representative case must be an object")
        identifier = case.get("id")
        if (not isinstance(identifier, str) or not identifier.strip()
                or identifier in identifiers):
            raise ValueError("every representative case needs a unique id")
        identifiers.add(identifier)
        reference = case.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError(f"{identifier}: non-empty synthetic reference required")
        labels = case.get("dimensions")
        if (not isinstance(labels, list) or not labels
                or any(label not in allowed_dimensions for label in labels)):
            raise ValueError(f"{identifier}: invalid dimensions")
        scenario = case.get("scenario")
        if not isinstance(scenario, dict):
            raise ValueError(f"{identifier}: scenario metadata required")
        if any(key in scenario for key in (
                "audio", "audio_path", "raw_audio", "clipboard",
                "surrounding_text")):
            raise ValueError(f"{identifier}: private source data is forbidden")
    return payload


def summarize_corpus(corpus: dict[str, Any]) -> dict[str, Any]:
    """Return coverage counts without returning any reference text."""
    counts = {dimension: 0 for dimension in corpus["required_dimensions"]}
    for case in corpus["cases"]:
        for dimension in case["dimensions"]:
            counts[dimension] += 1
    return {
        "cases": len(corpus["cases"]),
        "privacy": corpus["privacy"],
        "dimension_counts": counts,
        "missing_dimensions": sorted(
            dimension for dimension, count in counts.items() if count == 0),
    }


_OBSERVATION_FIELDS = {
    "case_id",
    "latency_ms",
    "edit_characters",
    "pasted_words",
    "zero_edit",
    "selected_route",
    "expected_route",
    "receipt",
    "lifecycle",
}
_RECEIPTS = {"verified", "unverifiable", "conflict", "unresolved"}
_LATENCY_STAGES = {
    "ready", "tail", "asr", "compiler", "cleanup", "insertion",
    "end_to_end", "release", "press", "context", "consequence",
}
_ROUTE_IDS = {
    "tiny",
    "turbo",
    "parakeet",
    "parakeet-unified",
    "speculative-tiny",
    "primary-parakeet",
    "fallback-turbo",
    "unknown",
}
_LIFECYCLE_IDS = {
    "cold-start",
    "warm-path",
    "back-to-back",
    "compiler-restart",
    "long-form",
    "sleep-wake",
    "audio-device-switch",
}

RUNTIME_TRACE_PREFIX = "[trace] "
RUNTIME_TRACE_SCHEMA_VERSION = 1
# This is intentionally duplicated from dictate.py: the lab must be importable
# without loading platform audio/UI dependencies. A parity test prevents drift.
RUNTIME_TRACE_SCHEMAS = {
    "warmup_audio_pool": ("duration_ms", "success"),
    "warmup_asr_tiny": ("duration_ms", "success"),
    "warmup_asr_final": ("duration_ms", "success"),
    "warmup_ollama": ("duration_ms", "success"),
    "warmup_total": ("duration_ms", "success"),
    "utterance_acoustic": (
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
    ),
    "warm_path": (
        "release_ms", "asr_ms", "compiler_ms",
        "cleanup_ms", "context_ms", "insertion_ms",
    ),
}
STARTUP_TRACE_EVENTS = (
    "warmup_audio_pool",
    "warmup_asr_tiny",
    "warmup_asr_final",
    "warmup_ollama",
    "warmup_total",
)
# Ordered (public stage label, closed-schema trace field) pairs for the
# warm_path latency trace. The label names a stage in the aggregate report; the
# field is the numeric millisecond key carried by the trace.
WARM_PATH_STAGES = (
    ("release", "release_ms"),
    ("asr", "asr_ms"),
    ("compiler", "compiler_ms"),
    ("cleanup", "cleanup_ms"),
    ("context", "context_ms"),
    ("insertion", "insertion_ms"),
)
_TRACE_RATIO_FIELDS = {
    "clipped_ratio", "nonfinite_ratio", "silence_ratio", "voiced_fraction",
}
_MAX_TRACE_LINE_CHARACTERS = 16_384


def _number(value: Any, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) and result >= minimum else None


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


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50": round(_percentile(values, 0.50), 4),
        "p90": round(_percentile(values, 0.90), 4),
        "p95": round(_percentile(values, 0.95), 4),
        "p99": round(_percentile(values, 0.99), 4),
        "mean": round(statistics.fmean(values), 4),
        "max": round(max(values), 4),
    }


def _validate_observation(
        value: Any, case_ids: set[str]) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(value, dict):
        return None, "not-an-object"
    unknown = set(value) - _OBSERVATION_FIELDS
    if unknown:
        return None, "unknown-or-private-field"
    case_id = value.get("case_id")
    if not isinstance(case_id, str) or case_id not in case_ids:
        return None, "unknown-case"
    latency = value.get("latency_ms")
    if latency is not None:
        if not isinstance(latency, dict) or not latency:
            return None, "invalid-latency"
        for stage, duration in latency.items():
            if (stage not in _LATENCY_STAGES
                    or _number(duration) is None):
                return None, "invalid-latency"
    for field in ("edit_characters", "pasted_words"):
        if (field in value and (
                isinstance(value[field], bool)
                or not isinstance(value[field], int)
                or value[field] < 0)):
            return None, f"invalid-{field.replace('_', '-')}"
    if "zero_edit" in value and not isinstance(value["zero_edit"], bool):
        return None, "invalid-zero-edit"
    for field in ("selected_route", "expected_route"):
        if field in value and (
                not isinstance(value[field], str)
                or value[field] not in _ROUTE_IDS):
            return None, f"invalid-{field.replace('_', '-')}"
    if "lifecycle" in value and (
            not isinstance(value["lifecycle"], str)
            or value["lifecycle"] not in _LIFECYCLE_IDS):
        return None, "invalid-lifecycle"
    if "receipt" in value and (
            not isinstance(value["receipt"], str)
            or value["receipt"] not in _RECEIPTS):
        return None, "invalid-receipt"
    return value, ""


def evaluate_observations(
        path: Path, corpus: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate a transcript-free JSONL outcome log.

    Unknown fields are rejected so callers cannot accidentally feed raw
    transcript, clipboard, application-context, or audio data into a report.
    """
    corpus = corpus or load_corpus()
    case_ids = {case["id"] for case in corpus["cases"]}
    case_dimensions = {
        case["id"]: tuple(case.get("dimensions", ()))
        for case in corpus["cases"]
    }
    records: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = None
            reason = "invalid-json"
        else:
            value, reason = _validate_observation(value, case_ids)
        if value is None:
            rejected[reason] = rejected.get(reason, 0) + 1
        else:
            records.append(value)

    latencies: dict[str, list[float]] = {}
    zero_edits: list[bool] = []
    edit_characters = 0.0
    pasted_words = 0.0
    burden_samples = 0
    route_results: list[bool] = []
    per_route: dict[str, list[bool]] = {}
    receipts: list[bool] = []
    observed_cases: set[str] = set()
    dim_samples: dict[str, int] = {}
    dim_zero_edits: dict[str, list[bool]] = {}
    dim_edit_characters: dict[str, float] = {}
    dim_pasted_words: dict[str, float] = {}
    dim_route_results: dict[str, list[bool]] = {}
    for record in records:
        observed_cases.add(record["case_id"])
        for stage, duration in record.get("latency_ms", {}).items():
            latencies.setdefault(stage, []).append(float(duration))
        if isinstance(record.get("zero_edit"), bool):
            zero_edits.append(record["zero_edit"])
        edits = _number(record.get("edit_characters"))
        words = _number(record.get("pasted_words"))
        if edits is not None and words is not None and words > 0:
            edit_characters += edits
            pasted_words += words
            burden_samples += 1
        selected = record.get("selected_route")
        expected = record.get("expected_route")
        if isinstance(selected, str) and isinstance(expected, str):
            correct = selected == expected
            route_results.append(correct)
            per_route.setdefault(expected, []).append(correct)
        if "receipt" in record:
            receipts.append(record["receipt"] == "verified")
        for dimension in case_dimensions.get(record["case_id"], ()):
            dim_samples[dimension] = dim_samples.get(dimension, 0) + 1
            if isinstance(record.get("zero_edit"), bool):
                dim_zero_edits.setdefault(dimension, []).append(
                    record["zero_edit"])
            if edits is not None and words is not None and words > 0:
                dim_edit_characters[dimension] = (
                    dim_edit_characters.get(dimension, 0.0) + edits)
                dim_pasted_words[dimension] = (
                    dim_pasted_words.get(dimension, 0.0) + words)
            if isinstance(selected, str) and isinstance(expected, str):
                dim_route_results.setdefault(dimension, []).append(
                    selected == expected)

    by_dimension: dict[str, dict[str, Any]] = {}
    for dimension in sorted(dim_samples):
        dim_zero = dim_zero_edits.get(dimension, [])
        dim_words = dim_pasted_words.get(dimension, 0.0)
        dim_routes = dim_route_results.get(dimension, [])
        by_dimension[dimension] = {
            "samples": dim_samples[dimension],
            "zero_edit_rate": round(sum(dim_zero) / len(dim_zero), 4)
            if dim_zero else None,
            "correction_burden_c100w": round(
                dim_edit_characters.get(dimension, 0.0) / dim_words * 100, 4)
            if dim_words else None,
            "route_quality_rate": round(sum(dim_routes) / len(dim_routes), 4)
            if dim_routes else None,
        }

    return {
        "schema_version": 1,
        "privacy": "transcript-free-outcomes",
        "records": len(records),
        "rejected_records": sum(rejected.values()),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "corpus_coverage": {
            "observed_cases": len(observed_cases),
            "total_cases": len(case_ids),
            "rate": round(len(observed_cases) / len(case_ids), 4),
        },
        "latency_ms": {
            stage: _distribution(values)
            for stage, values in sorted(latencies.items())
        },
        "zero_edit": {
            "available": bool(zero_edits),
            "samples": len(zero_edits),
            "rate": round(sum(zero_edits) / len(zero_edits), 4)
            if zero_edits else None,
        },
        "correction_burden": {
            "available": bool(burden_samples),
            "samples": burden_samples,
            "edit_characters": round(edit_characters, 4),
            "pasted_words": round(pasted_words, 4),
            "characters_per_100_words": round(
                edit_characters / pasted_words * 100, 4)
            if pasted_words else None,
        },
        "route_quality": {
            "available": bool(route_results),
            "samples": len(route_results),
            "rate": round(sum(route_results) / len(route_results), 4)
            if route_results else None,
            "by_expected_route": {
                route: {
                    "samples": len(results),
                    "rate": round(sum(results) / len(results), 4),
                }
                for route, results in sorted(per_route.items())
            },
        },
        "verified_delivery": {
            "available": bool(receipts),
            "samples": len(receipts),
            "rate": round(sum(receipts) / len(receipts), 4)
            if receipts else None,
        },
        "by_dimension": by_dimension,
    }


def _validate_runtime_trace(
        value: Any) -> tuple[tuple[str, dict[str, float]] | None, str]:
    """Validate one trace without reflecting untrusted values in a report."""
    if not isinstance(value, dict):
        return None, "not-an-object"
    if (not isinstance(value.get("schema_version"), int)
            or isinstance(value.get("schema_version"), bool)
            or value["schema_version"] != RUNTIME_TRACE_SCHEMA_VERSION):
        return None, "unsupported-schema"
    event = value.get("event")
    if not isinstance(event, str) or event not in RUNTIME_TRACE_SCHEMAS:
        return None, "unknown-event"
    fields = RUNTIME_TRACE_SCHEMAS[event]
    if set(value) != {"event", "schema_version", *fields}:
        return None, "unknown-or-private-field"

    metrics: dict[str, float] = {}
    for field in fields:
        number = _number(value[field], minimum=(
            1.0 if field == "derived_gain_factor" else 0.0))
        if number is None:
            return None, "invalid-numeric-field"
        if field in _TRACE_RATIO_FIELDS and number > 1.0:
            return None, "invalid-numeric-field"
        if field == "success" and number not in (0.0, 1.0):
            return None, "invalid-numeric-field"
        if field == "adaptive_threshold" and number > 0.1:
            return None, "invalid-numeric-field"
        if field == "derived_gain_factor" and number > 8.0:
            return None, "invalid-numeric-field"
        metrics[field] = number
    if event == "utterance_acoustic":
        if not (metrics["frame_rms_p20"] <= metrics["frame_rms_p50"]
                <= metrics["frame_rms_p95"]):
            return None, "invalid-numeric-field"
        if metrics["trailing_silence_ms"] > metrics["duration_ms"]:
            return None, "invalid-numeric-field"
    return (event, metrics), ""


def evaluate_runtime_traces(path: Path) -> dict[str, Any]:
    """Aggregate closed-schema numeric traces from a mixed application log.

    Non-trace lines are counted then discarded. Invalid traces contribute only
    a fixed rejection category, so raw log text, paths, application metadata,
    and transcript-like fields can never be reflected into the result.
    """
    accepted: dict[str, dict[str, list[float]]] = {}
    rejected: dict[str, int] = {}
    ignored_lines = 0
    with path.open(encoding="utf-8", errors="replace") as source:
        for raw_line in source:
            line = raw_line.rstrip("\r\n")
            if not line.startswith(RUNTIME_TRACE_PREFIX):
                if line:
                    ignored_lines += 1
                continue
            if len(line) > _MAX_TRACE_LINE_CHARACTERS:
                reason = "trace-line-too-large"
                validated = None
            else:
                try:
                    value = json.loads(line[len(RUNTIME_TRACE_PREFIX):])
                except json.JSONDecodeError:
                    validated, reason = None, "invalid-json"
                else:
                    validated, reason = _validate_runtime_trace(value)
            if validated is None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            event, metrics = validated
            event_metrics = accepted.setdefault(
                event, {field: [] for field in RUNTIME_TRACE_SCHEMAS[event]})
            for field, number in metrics.items():
                event_metrics[field].append(number)

    events: dict[str, Any] = {}
    for event, metrics in sorted(accepted.items()):
        samples = len(next(iter(metrics.values())))
        event_report: dict[str, Any] = {
            "records": samples,
            "metrics": {
                field: _distribution(values)
                for field, values in sorted(metrics.items())
            },
        }
        if "success" in metrics:
            event_report["success_rate"] = round(
                statistics.fmean(metrics["success"]), 4)
        events[event] = event_report
    records = sum(event["records"] for event in events.values())
    return {
        "schema_version": 1,
        "trace_schema_version": RUNTIME_TRACE_SCHEMA_VERSION,
        "privacy": "numeric-aggregates-only",
        "records": records,
        "rejected_records": sum(rejected.values()),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "ignored_non_trace_lines": ignored_lines,
        "events": events,
    }


def evaluate_startup_traces(
        cold_trace_log: Path, warm_trace_log: Path) -> dict[str, Any]:
    """Compare caller-separated cold and warm closed-schema trace logs.

    Phase labels come only from the two explicit inputs. The evaluator never
    guesses process or cache state from event order and does not claim that the
    caller established physical cold-start conditions.
    """
    phase_reports: dict[str, Any] = {}
    rejected: dict[str, int] = {}
    ignored_lines = 0
    ignored_non_startup = 0
    for phase, path in (
            ("cold", cold_trace_log), ("warm", warm_trace_log)):
        aggregate = evaluate_runtime_traces(path)
        events = {
            event: aggregate["events"][event]
            for event in STARTUP_TRACE_EVENTS
            if event in aggregate["events"]
        }
        startup_records = sum(event["records"] for event in events.values())
        ignored_non_startup += aggregate["records"] - startup_records
        ignored_lines += aggregate["ignored_non_trace_lines"]
        for reason, count in aggregate["rejected_by_reason"].items():
            rejected[reason] = rejected.get(reason, 0) + count
        phase_reports[phase] = {
            "records": startup_records,
            "events": events,
        }
    return {
        "schema_version": 1,
        "trace_schema_version": RUNTIME_TRACE_SCHEMA_VERSION,
        "privacy": "numeric-aggregates-only",
        "phase_classification": "caller-separated-trace-logs",
        "physical_conditions_verified": False,
        "records": sum(
            phase["records"] for phase in phase_reports.values()),
        "rejected_records": sum(rejected.values()),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "ignored_non_trace_lines": ignored_lines,
        "ignored_non_startup_records": ignored_non_startup,
        "phases": phase_reports,
    }


def summarize_warm_path(path: Path) -> dict[str, Any]:
    """Aggregate warm_path latency traces into per-stage percentile tails.

    Reuses evaluate_runtime_traces (and therefore _distribution) so the result
    is transcript-free by construction: only numeric millisecond aggregates and
    fixed rejection categories are ever reflected. Traces for other events are
    counted as ignored records and never contribute a value.
    """
    aggregate = evaluate_runtime_traces(path)
    event = aggregate["events"].get("warm_path")
    latency_ms: dict[str, Any] = {}
    warm_path_records = 0
    if event is not None:
        warm_path_records = event["records"]
        for label, field in WARM_PATH_STAGES:
            distribution = event["metrics"].get(field)
            if distribution is not None:
                latency_ms[label] = distribution
    return {
        "schema_version": 1,
        "trace_schema_version": RUNTIME_TRACE_SCHEMA_VERSION,
        "privacy": "numeric-aggregates-only",
        "records": warm_path_records,
        "rejected_records": aggregate["rejected_records"],
        "rejected_by_reason": aggregate["rejected_by_reason"],
        "ignored_non_trace_lines": aggregate["ignored_non_trace_lines"],
        "ignored_non_warm_path_records":
            aggregate["records"] - warm_path_records,
        "latency_ms": latency_ms,
    }


def load_budgets(path: Path = DEFAULT_BUDGETS) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported performance budget schema")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("performance budgets need profiles")
    for name, profile in profiles.items():
        checks = profile.get("checks") if isinstance(profile, dict) else None
        if not isinstance(name, str) or not isinstance(checks, list) or not checks:
            raise ValueError("every performance profile needs checks")
        identifiers: set[str] = set()
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError(f"{name}: invalid budget check")
            identifier = check.get("id")
            if (not isinstance(identifier, str) or identifier in identifiers
                    or check.get("operator") not in {"<=", ">="}
                    or not isinstance(check.get("metric"), str)
                    or _number(check.get("threshold")) is None
                    or _number(check.get("minimum_samples", 1)) is None):
                raise ValueError(f"{name}: invalid budget check")
            identifiers.add(identifier)
    return payload


def _value_at(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for segment in path.split("."):
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value


def evaluate_budgets(
        report: dict[str, Any], budgets: dict[str, Any],
        profile: str) -> dict[str, Any]:
    """Apply a deterministic budget profile to an aggregate report."""
    profile_data = budgets["profiles"].get(profile)
    if not isinstance(profile_data, dict):
        raise ValueError(f"unknown performance budget profile: {profile}")
    results: list[dict[str, Any]] = []
    for check in profile_data["checks"]:
        actual = _number(_value_at(report, check["metric"]))
        samples_path = check.get("samples_metric")
        samples = _number(_value_at(report, samples_path)) \
            if isinstance(samples_path, str) else None
        minimum_samples = float(check.get("minimum_samples", 1))
        threshold = float(check["threshold"])
        if actual is None:
            passed = False
            reason = "metric-unavailable"
        elif samples_path and (samples is None or samples < minimum_samples):
            passed = False
            reason = "insufficient-samples"
        else:
            passed = actual <= threshold if check["operator"] == "<=" \
                else actual >= threshold
            reason = "passed" if passed else "threshold-exceeded"
        results.append({
            "id": check["id"],
            "metric": check["metric"],
            "actual": actual,
            "operator": check["operator"],
            "threshold": threshold,
            "samples": samples,
            "minimum_samples": minimum_samples,
            "passed": passed,
            "reason": reason,
        })
    failed = sum(not result["passed"] for result in results)
    return {
        "profile": profile,
        "description": profile_data.get("description", ""),
        "passed": failed == 0,
        "failed": failed,
        "checks": results,
    }


def load_model_scorecard(
        path: Path = DEFAULT_MODEL_SCORECARD) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported model scorecard schema")
    criteria = payload.get("criteria")
    candidates = payload.get("candidates")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("model scorecard needs criteria")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("model scorecard needs candidates")
    metrics: set[str] = set()
    for criterion in criteria:
        if (not isinstance(criterion, dict)
                or not isinstance(criterion.get("metric"), str)
                or criterion["metric"] in metrics
                or criterion.get("direction") not in {"min", "max"}
                or _number(criterion.get("weight")) is None
                or criterion["weight"] <= 0
                or not isinstance(criterion.get("dimension"), str)
                or not isinstance(criterion.get("required"), bool)):
            raise ValueError("invalid model scorecard criterion")
        metrics.add(criterion["metric"])
    identifiers: set[str] = set()
    for candidate in candidates:
        identifier = candidate.get("model_id") if isinstance(candidate, dict) else None
        if (not isinstance(identifier, str) or not identifier.strip()
                or identifier in identifiers
                or candidate.get("license_status") not in {
                    "approved", "review-required", "restricted"}
                or not isinstance(candidate.get("metrics"), dict)):
            raise ValueError("invalid model scorecard candidate")
        identifiers.add(identifier)
        for field in (
                "benchmark_engine", "runtime_role", "revision",
                "repository_head", "model_card_url", "revision_api_url",
                "provenance", "artifact_license", "upstream_license",
                "license"):
            if (not isinstance(candidate.get(field), str)
                    or not candidate[field].strip()):
                raise ValueError(f"{identifier}: missing {field}")
        current = candidate.get("pinned_is_current_head")
        if (not isinstance(current, bool)
                or current != (candidate["revision"]
                               == candidate["repository_head"])):
            raise ValueError(f"{identifier}: inconsistent repository head status")
        upstream = candidate.get("upstream_model_id")
        if upstream is not None and (
                not isinstance(upstream, str) or not upstream.strip()):
            raise ValueError(f"{identifier}: invalid upstream model id")
        expected_metadata = candidate.get("expected_hub_metadata")
        if (not isinstance(expected_metadata, dict)
                or set(expected_metadata) != {"license", "base_models"}
                or (expected_metadata["license"] is not None
                    and not isinstance(expected_metadata["license"], str))
                or not isinstance(expected_metadata["base_models"], list)
                or any(not isinstance(model, str) or not model.strip()
                       for model in expected_metadata["base_models"])):
            raise ValueError(f"{identifier}: invalid expected Hub metadata")
        for metric, value in candidate["metrics"].items():
            if metric not in metrics or (value is not None and _number(value) is None):
                raise ValueError(f"{identifier}: invalid metric {metric}")
    return payload


def generate_model_scorecard(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize heterogeneous model evidence into an explainable ranking."""
    criteria = source["criteria"]
    candidates = source["candidates"]
    ranges: dict[str, tuple[float, float]] = {}
    for criterion in criteria:
        values = [
            float(candidate["metrics"][criterion["metric"]])
            for candidate in candidates
            if _number(candidate["metrics"].get(criterion["metric"])) is not None
        ]
        if values:
            ranges[criterion["metric"]] = (min(values), max(values))

    ranked: list[dict[str, Any]] = []
    present_cells = 0
    for candidate in candidates:
        weighted_score = 0.0
        available_weight = 0.0
        contributions: dict[str, float] = {}
        missing_required: list[str] = []
        for criterion in criteria:
            metric = criterion["metric"]
            value = _number(candidate["metrics"].get(metric))
            if value is None:
                if criterion["required"]:
                    missing_required.append(metric)
                continue
            present_cells += 1
            low, high = ranges[metric]
            if high == low:
                normalized = 1.0
            elif criterion["direction"] == "min":
                normalized = (high - value) / (high - low)
            else:
                normalized = (value - low) / (high - low)
            weight = float(criterion["weight"])
            contributions[metric] = round(normalized, 6)
            weighted_score += normalized * weight
            available_weight += weight
        reasons = [f"missing-required:{metric}" for metric in missing_required]
        if candidate["license_status"] != "approved":
            reasons.append(f"license-{candidate['license_status']}")
        ranked.append({
            "model_id": candidate["model_id"],
            "benchmark_engine": candidate["benchmark_engine"],
            "runtime_role": candidate["runtime_role"],
            "revision": candidate.get("revision"),
            "repository_head": candidate["repository_head"],
            "pinned_is_current_head": candidate["pinned_is_current_head"],
            "currentness": "current-head"
            if candidate["pinned_is_current_head"] else "pinned-not-head",
            "model_card_url": candidate["model_card_url"],
            "revision_api_url": candidate["revision_api_url"],
            "upstream_model_id": candidate.get("upstream_model_id"),
            "provenance": candidate["provenance"],
            "artifact_license": candidate["artifact_license"],
            "upstream_license": candidate["upstream_license"],
            "license": candidate.get("license"),
            "license_status": candidate["license_status"],
            "eligible": not reasons,
            "ineligibility_reasons": reasons,
            "score": round(weighted_score / available_weight * 100, 2)
            if available_weight else None,
            "available_weight": round(available_weight, 4),
            "normalized_metrics": contributions,
        })
    ranked.sort(key=lambda item: (
        not item["eligible"],
        -(item["score"] if item["score"] is not None else -1),
        item["model_id"],
    ))
    for index, candidate in enumerate(ranked, 1):
        candidate["rank"] = index
    missing_measurements = sorted(
        criterion["metric"] for criterion in criteria
        if not any(_number(candidate["metrics"].get(criterion["metric"]))
                   is not None for candidate in candidates)
    )
    total_cells = len(criteria) * len(candidates)
    return {
        "schema_version": 1,
        "evidence": source.get("evidence", {}),
        "ranked": ranked,
        "measurement_coverage": round(present_cells / total_cells, 4),
        "missing_measurements": missing_measurements,
        "recommendation": next(
            (candidate["model_id"] for candidate in ranked
             if candidate["eligible"]), None),
        "warning": (
            "Scores are cohort-relative. Missing measurements are excluded, "
            "never treated as zero; license eligibility is a separate gate."
        ),
    }


def _hub_base_models(card_data: Any) -> list[str]:
    if not isinstance(card_data, dict):
        return []
    value = card_data.get("base_model")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return sorted(item for item in value if isinstance(item, str))
    return []


def _validate_hub_api_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "huggingface.co"
            or not parsed.path.startswith("/api/models/")):
        raise ValueError("model audit only permits the public Hugging Face API")


def fetch_hub_json(
        url: str, *, timeout: float = 10.0, attempts: int = 3,
        opener=None, sleeper=time.sleep) -> dict[str, Any]:
    """Fetch a bounded public Hugging Face API response with short retries."""
    _validate_hub_api_url(url)
    if not isinstance(attempts, int) or isinstance(attempts, bool) \
            or not 1 <= attempts <= 5:
        raise ValueError("attempts must be between 1 and 5")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0 < float(timeout) <= 30):
        raise ValueError("timeout must be between 0 and 30 seconds")
    opener = opener or urllib.request.urlopen
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener(url, timeout=float(timeout)) as response:
                _validate_hub_api_url(response.geturl())
                raw = response.read(_MAX_HUB_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_HUB_RESPONSE_BYTES:
                raise ValueError("Hugging Face API response exceeded size limit")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Hugging Face API response must be an object")
            return payload
        except (OSError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            last_error = error
            if attempt < attempts:
                sleeper(round(0.1 * attempt, 1))
    assert last_error is not None
    raise last_error


def audit_model_sources(
        source: dict[str, Any], *, fetch_json=None,
        checked_at=None) -> dict[str, Any]:
    """Compare reviewed Hub metadata with live public model metadata."""
    fetch_json = fetch_json or fetch_hub_json
    if checked_at is None:
        checked_at = lambda: datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z")
    results: list[dict[str, Any]] = []
    for candidate in source["candidates"]:
        model_id = candidate["model_id"]
        head_url = f"https://huggingface.co/api/models/{model_id}"
        expected_metadata = candidate["expected_hub_metadata"]
        reasons: list[str] = []
        try:
            head = fetch_json(head_url)
            pinned = fetch_json(candidate["revision_api_url"])
            observed_head = head.get("sha") if isinstance(head, dict) else None
            pinned_sha = pinned.get("sha") if isinstance(pinned, dict) else None
            card_data = head.get("cardData") if isinstance(head, dict) else None
            observed_metadata = {
                "license": card_data.get("license")
                if isinstance(card_data, dict) else None,
                "base_models": _hub_base_models(card_data),
            }
            if observed_head != candidate["repository_head"]:
                reasons.append("repository-head-changed")
            if pinned_sha != candidate["revision"]:
                reasons.append("immutable-revision-unavailable")
            if observed_metadata["license"] != expected_metadata["license"]:
                reasons.append("license-metadata-changed")
            if (observed_metadata["base_models"]
                    != sorted(expected_metadata["base_models"])):
                reasons.append("base-model-metadata-changed")
            status = "drift" if reasons else "pass"
            result = {
                "model_id": model_id,
                "pinned_revision": candidate["revision"],
                "expected_head": candidate["repository_head"],
                "observed_head": observed_head,
                "pinned_revision_resolved": pinned_sha == candidate["revision"],
                "expected_metadata": expected_metadata,
                "observed_metadata": observed_metadata,
                "status": status,
                "reasons": reasons,
            }
        except Exception as error:
            result = {
                "model_id": model_id,
                "pinned_revision": candidate["revision"],
                "expected_head": candidate["repository_head"],
                "observed_head": None,
                "pinned_revision_resolved": False,
                "expected_metadata": expected_metadata,
                "observed_metadata": None,
                "status": "error",
                "reasons": ["metadata-check-failed"],
                "error_type": type(error).__name__,
            }
        results.append(result)
    passed = sum(result["status"] == "pass" for result in results)
    drifted = sum(result["status"] == "drift" for result in results)
    errors = sum(result["status"] == "error" for result in results)
    status = "error" if errors else "drift" if drifted else "pass"
    return {
        "schema_version": 1,
        "privacy": "public-model-metadata-only",
        "checked_at": checked_at(),
        "status": status,
        "summary": {
            "candidates": len(results),
            "passed": passed,
            "drifted": drifted,
            "errors": errors,
        },
        "candidates": results,
    }


class LifecycleSimulationUnavailable(RuntimeError):
    """A deterministic adapter fault blocked a simulated operation."""


class CompilerLifecycleSimulationAdapter:
    """Isolate lifecycle fault simulation from the Voice Compiler itself."""

    def __init__(self, compiler_factory, compile_case):
        self._compiler_factory = compiler_factory
        self._compile_case = compile_case
        self._compiler = compiler_factory()
        self._sleeping = False
        self._audio_device_available = True

    def compile(self, case: dict[str, Any]) -> str:
        if self._sleeping or not self._audio_device_available:
            raise LifecycleSimulationUnavailable("simulated lifecycle fault")
        return self._compile_case(self._compiler, case)

    def restart(self) -> None:
        self._compiler = self._compiler_factory()

    def inject_sleep(self) -> None:
        self._sleeping = True

    def wake(self) -> None:
        self._sleeping = False

    def inject_audio_device_loss(self) -> None:
        self._audio_device_available = False

    def restore_audio_device(self) -> None:
        self._audio_device_available = True


def run_lifecycle_simulation(
        corpus: dict[str, Any] | None = None, *, iterations: int = 10,
        adapter_factory=None) -> dict[str, Any]:
    """Exercise compiler recovery through deterministic adapter-only faults.

    Sleep/wake and audio-device changes are state injected at an adapter seam;
    they never pretend to operate the OS, microphone, driver, or real device.
    """
    if (isinstance(iterations, bool) or not isinstance(iterations, int)
            or iterations <= 0):
        raise ValueError("iterations must be a positive integer")
    from voice_compiler import RecognitionHypothesis, VoiceCompiler, VoiceIR

    corpus = corpus or load_corpus()
    cases = corpus["cases"]
    long_form_cases = [
        case for case in cases
        if case["scenario"].get("delivery") == "long-form"
    ]
    if not long_form_cases:
        raise ValueError("lifecycle simulation requires a long-form case")

    def compile_case(compiler: Any, case: dict[str, Any]) -> str:
        voice = VoiceIR(hypotheses=(RecognitionHypothesis(
            text=case["reference"],
            confidence=0.95,
            engine="synthetic-lifecycle-simulation",
        ),))
        return compiler.compile(voice).text

    baseline_compiler = VoiceCompiler()
    baseline = {
        case["id"]: compile_case(baseline_compiler, case)
        for case in cases
    }
    factory = adapter_factory or CompilerLifecycleSimulationAdapter
    adapter = factory(VoiceCompiler, compile_case)
    scenario_names = (
        "back-to-back",
        "long-form",
        "process-restart",
        "sleep-wake",
        "audio-device-switch",
    )
    scenarios = {
        name: {"operations": 0, "blocked_operations": 0, "failures": 0}
        for name in scenario_names
    }
    failures = 0
    nondeterministic = 0
    faults_injected = 0
    faults_observed = 0
    recoveries = 0

    def compile_and_compare(name: str, case: dict[str, Any]) -> None:
        nonlocal failures, nondeterministic
        scenarios[name]["operations"] += 1
        try:
            actual = adapter.compile(case)
        except Exception:
            failures += 1
            scenarios[name]["failures"] += 1
            return
        if actual != baseline[case["id"]]:
            nondeterministic += 1
            scenarios[name]["failures"] += 1

    def expect_blocked(name: str, case: dict[str, Any]) -> None:
        nonlocal failures, faults_observed
        try:
            adapter.compile(case)
        except LifecycleSimulationUnavailable:
            faults_observed += 1
            scenarios[name]["blocked_operations"] += 1
        except Exception:
            failures += 1
            scenarios[name]["failures"] += 1
        else:
            failures += 1
            scenarios[name]["failures"] += 1

    for index in range(iterations):
        case = cases[index % len(cases)]
        compile_and_compare("back-to-back", case)
        compile_and_compare("back-to-back", case)

        long_case = long_form_cases[index % len(long_form_cases)]
        compile_and_compare("long-form", long_case)

        adapter.restart()
        compile_and_compare("process-restart", case)

        faults_injected += 1
        adapter.inject_sleep()
        expect_blocked("sleep-wake", case)
        adapter.wake()
        recoveries += 1
        compile_and_compare("sleep-wake", case)

        faults_injected += 1
        adapter.inject_audio_device_loss()
        expect_blocked("audio-device-switch", case)
        adapter.restore_audio_device()
        recoveries += 1
        compile_and_compare("audio-device-switch", case)

    return {
        "schema_version": 1,
        "privacy": "synthetic-text-only",
        "evidence_scope": "adapter-simulation-only",
        "physical_evidence": False,
        "iterations": iterations,
        "operations": sum(
            scenario["operations"] for scenario in scenarios.values()),
        "blocked_operations": sum(
            scenario["blocked_operations"]
            for scenario in scenarios.values()),
        "faults_injected": faults_injected,
        "faults_observed": faults_observed,
        "recoveries": recoveries,
        "failures": failures,
        "nondeterministic_outputs": nondeterministic,
        "scenarios": scenarios,
        "requires_physical_validation": [
            "physical-audio-device-switch",
            "physical-long-audio-memory-thermal",
            "physical-operating-system-sleep-wake",
        ],
    }


def run_compiler_stress(
        corpus: dict[str, Any] | None = None, *, cycles: int = 10,
        restart_every: int = 50) -> dict[str, Any]:
    """Exercise deterministic Voice Compiler lifecycle behavior offline.

    A cycle compiles every synthetic case once. Re-instantiation simulates
    process lifecycle boundaries without pretending to exercise audio capture,
    operating-system sleep, or physical device changes.
    """
    if cycles <= 0 or restart_every <= 0:
        raise ValueError("cycles and restart_every must be positive")
    from voice_compiler import RecognitionHypothesis, VoiceCompiler, VoiceIR

    corpus = corpus or load_corpus()

    def compile_case(compiler: Any, case: dict[str, Any]) -> str:
        voice = VoiceIR(hypotheses=(RecognitionHypothesis(
            text=case["reference"],
            confidence=0.95,
            engine="synthetic-stress",
        ),))
        return compiler.compile(voice).text

    baseline_compiler = VoiceCompiler()
    baseline = {
        case["id"]: compile_case(baseline_compiler, case)
        for case in corpus["cases"]
    }
    total = cycles * len(corpus["cases"])
    failures = 0
    nondeterministic = 0
    failure_ids: set[str] = set()
    latencies: list[float] = []
    restarts = 0
    compiler: Any = None
    tracemalloc.start()
    try:
        for index in range(total):
            if index % restart_every == 0:
                compiler = VoiceCompiler()
                restarts += 1
            case = corpus["cases"][index % len(corpus["cases"])]
            started = time.perf_counter_ns()
            try:
                actual = compile_case(compiler, case)
            except Exception:  # The privacy-safe report records no exception text.
                failures += 1
                failure_ids.add(case["id"])
            else:
                if actual != baseline[case["id"]]:
                    nondeterministic += 1
                    failure_ids.add(case["id"])
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        gc.collect()
    exercised = ["back-to-back", "compiler-restart", "warm-path"]
    if any(case["scenario"].get("delivery") == "long-form"
           for case in corpus["cases"]):
        exercised.append("long-form")
    return {
        "schema_version": 1,
        "privacy": "synthetic-text-only",
        "operations": total,
        "warmup_operations": len(corpus["cases"]),
        "compiler_restarts": restarts,
        "restart_every": restart_every,
        "failures": failures,
        "nondeterministic_outputs": nondeterministic,
        "failed_case_ids": sorted(failure_ids),
        "peak_python_bytes": peak_bytes,
        "latency_ms": {"compiler": _distribution(latencies)},
        "exercised": exercised,
        "requires_physical_validation": [
            "audio-device-switch",
            "microphone-gain-and-noise",
            "operating-system-sleep-wake",
            "thermal-and-energy",
        ],
    }


def _rate(value: float | None) -> str:
    return "unavailable" if value is None else f"{value * 100:.1f}%"


def render_corpus(summary: dict[str, Any]) -> str:
    lines = [
        "REPRESENTATIVE DICTATION CORPUS",
        f"cases: {summary['cases']}",
        f"privacy: {summary['privacy']}",
        "dimension                       cases",
    ]
    lines.extend(
        f"{dimension:<31} {count:>5}"
        for dimension, count in sorted(summary["dimension_counts"].items())
    )
    lines.append(
        "coverage: " + ("PASS" if not summary["missing_dimensions"] else
                         "MISSING " + ", ".join(summary["missing_dimensions"])))
    return "\n".join(lines)


def render_dashboard(report: dict[str, Any]) -> str:
    lines = [
        "PRIVACY-SAFE OUTCOME DASHBOARD",
        f"records: {report['records']} (rejected: {report['rejected_records']})",
        "stage                         p50 ms     p90 ms     p95 ms     "
        "p99 ms     max ms",
    ]
    lines.extend(
        f"{stage:<28} {result['p50']:>10.2f} {result['p90']:>10.2f} "
        f"{result['p95']:>10.2f} {result['p99']:>10.2f} {result['max']:>10.2f}"
        for stage, result in report["latency_ms"].items()
    )
    lines.extend((
        f"zero-edit: {_rate(report['zero_edit']['rate'])} "
        f"({report['zero_edit']['samples']} samples)",
        "correction burden: "
        f"{report['correction_burden']['characters_per_100_words']} "
        "characters/100 words",
        f"route quality: {_rate(report['route_quality']['rate'])} "
        f"({report['route_quality']['samples']} samples)",
        f"verified delivery: {_rate(report['verified_delivery']['rate'])} "
        f"({report['verified_delivery']['samples']} samples)",
    ))
    by_dimension = report.get("by_dimension", {})
    if by_dimension:
        lines.append(
            f"{'dimension':<31} {'samples':>7} {'zero-edit':>10} "
            f"{'burden':>7} {'route-quality':>13}")
        for dimension, stats in by_dimension.items():
            burden = stats["correction_burden_c100w"]
            burden_label = "n/a" if burden is None else f"{burden:.1f}"
            lines.append(
                f"{dimension:<31} {stats['samples']:>7} "
                f"{_rate(stats['zero_edit_rate']):>10} {burden_label:>7} "
                f"{_rate(stats['route_quality_rate']):>13}")
    return "\n".join(lines)


def render_runtime_traces(report: dict[str, Any]) -> str:
    lines = [
        "PRIVACY-SAFE RUNTIME TRACE AGGREGATES",
        f"records: {report['records']} (rejected: {report['rejected_records']}; "
        f"non-trace lines ignored: {report['ignored_non_trace_lines']})",
        "event                         samples   p95 duration ms   success",
    ]
    for event, result in report["events"].items():
        duration = result["metrics"]["duration_ms"]["p95"]
        success = result.get("success_rate")
        success_label = "n/a" if success is None else f"{success * 100:.1f}%"
        lines.append(
            f"{event:<29} {result['records']:>7} {duration:>17.2f} "
            f"{success_label:>9}")
    return "\n".join(lines)


def render_startup_traces(report: dict[str, Any]) -> str:
    lines = [
        "CALLER-LABELLED STARTUP TRACE BUDGETS",
        "phase  event                         samples   p95 duration ms   success",
    ]
    for phase, phase_report in report["phases"].items():
        for event, result in phase_report["events"].items():
            duration = result["metrics"]["duration_ms"]["p95"]
            success = result.get("success_rate")
            success_label = "n/a" if success is None else f"{success * 100:.1f}%"
            lines.append(
                f"{phase:<6} {event:<29} {result['records']:>7} "
                f"{duration:>17.2f} {success_label:>9}")
    budget = report.get("budget")
    lines.extend((
        "budget: " + ("PASS" if budget and budget["passed"] else "FAIL"),
        "physical cold/warm conditions verified: no",
    ))
    return "\n".join(lines)


def render_warm_path(report: dict[str, Any]) -> str:
    lines = [
        "PRIVACY-SAFE WARM-PATH LATENCY AGGREGATES",
        f"records: {report['records']} (rejected: "
        f"{report['rejected_records']}; non-warm-path traces ignored: "
        f"{report['ignored_non_warm_path_records']}; non-trace lines ignored: "
        f"{report['ignored_non_trace_lines']})",
        "stage        samples   p50 ms   p90 ms   p95 ms   p99 ms",
    ]
    for stage, distribution in report["latency_ms"].items():
        lines.append(
            f"{stage:<12} {distribution['samples']:>7} "
            f"{distribution['p50']:>8.2f} {distribution['p90']:>8.2f} "
            f"{distribution['p95']:>8.2f} {distribution['p99']:>8.2f}")
    budget = report.get("budget")
    if budget is not None:
        lines.append("budget: " + ("PASS" if budget["passed"] else "FAIL"))
    return "\n".join(lines)


def render_lifecycle_simulation(report: dict[str, Any]) -> str:
    lines = [
        "DETERMINISTIC LIFECYCLE ADAPTER SIMULATION",
        f"iterations: {report['iterations']}",
        f"operations: {report['operations']}",
        f"blocked operations: {report['blocked_operations']}",
        f"faults observed: {report['faults_observed']}/"
        f"{report['faults_injected']}",
        f"recoveries: {report['recoveries']}",
        f"failures: {report['failures']}",
        f"nondeterministic outputs: {report['nondeterministic_outputs']}",
        "physical evidence: no",
        "physical validation still required: "
        + ", ".join(report["requires_physical_validation"]),
    ]
    return "\n".join(lines)


def render_stress(report: dict[str, Any]) -> str:
    latency = report["latency_ms"]["compiler"]
    budget = report.get("budget")
    return "\n".join((
        "VOICE COMPILER LIFECYCLE STRESS",
        f"operations: {report['operations']}",
        f"restarts: {report['compiler_restarts']}",
        f"failures: {report['failures']}",
        f"nondeterministic outputs: {report['nondeterministic_outputs']}",
        f"compiler latency: p50 {latency['p50']:.3f}ms, "
        f"p95 {latency['p95']:.3f}ms, p99 {latency['p99']:.3f}ms",
        "budget: " + (
            "PASS" if budget and budget["passed"] else "FAIL"),
        "physical validation still required: "
        + ", ".join(report["requires_physical_validation"]),
    ))


def render_scorecard(report: dict[str, Any]) -> str:
    lines = [
        "MODEL SCORECARD",
        f"{'rank':>4} {'model':<54} {'score':>8} {'eligible':>10} license",
    ]
    for candidate in report["ranked"]:
        score = "n/a" if candidate["score"] is None \
            else f"{candidate['score']:.2f}"
        lines.append(
            f"{candidate['rank']:>4} {candidate['model_id']:<54} "
            f"{score:>8} {str(candidate['eligible']):>10} "
            f"{candidate['license_status']}")
    lines.extend((
        f"recommendation: {report['recommendation'] or 'none'}",
        f"measurement coverage: {report['measurement_coverage'] * 100:.1f}%",
        "UNMEASURED: " + (", ".join(report["missing_measurements"])
                            if report["missing_measurements"] else "none"),
        "metric verification: "
        + str(report.get("evidence", {}).get("metric_verification", "unknown")),
        report["warning"],
    ))
    return "\n".join(lines)


def render_model_audit(report: dict[str, Any]) -> str:
    lines = [
        "MODEL SOURCE AUDIT",
        f"status: {report['status']}",
        "model                                                     status reasons",
    ]
    for candidate in report["candidates"]:
        lines.append(
            f"{candidate['model_id']:<57} {candidate['status']:<6} "
            + (", ".join(candidate["reasons"]) or "none"))
    summary = report["summary"]
    lines.append(
        f"passed: {summary['passed']}; drifted: {summary['drifted']}; "
        f"errors: {summary['errors']}")
    return "\n".join(lines)


def write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a machine-readable evidence artifact."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    corpus = commands.add_parser("corpus", help="validate corpus coverage")
    corpus.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    corpus.add_argument("--format", choices=("table", "json"), default="table")

    evaluate = commands.add_parser(
        "evaluate", help="aggregate transcript-free outcome JSONL")
    evaluate.add_argument("--observations", type=Path, required=True)
    evaluate.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    evaluate.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    evaluate.add_argument("--budget-profile")
    evaluate.add_argument(
        "--format", choices=("table", "json"), default="table")

    traces = commands.add_parser(
        "traces", help="aggregate closed-schema traces from a runtime log")
    traces.add_argument("--trace-log", type=Path, required=True)
    traces.add_argument(
        "--format", choices=("table", "json"), default="table")

    startup = commands.add_parser(
        "startup", help="compare caller-separated cold and warm trace logs")
    startup.add_argument("--cold-trace-log", type=Path, required=True)
    startup.add_argument("--warm-trace-log", type=Path, required=True)
    startup.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    startup.add_argument("--budget-profile", default="startup_readiness")
    startup.add_argument(
        "--format", choices=("table", "json"), default="table")

    warm_path = commands.add_parser(
        "warm-path",
        help="aggregate warm-path latency traces from a runtime log")
    warm_path.add_argument("--trace-log", type=Path, required=True)
    warm_path.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    warm_path.add_argument("--budget-profile")
    warm_path.add_argument(
        "--format", choices=("table", "json"), default="table")

    lifecycle = commands.add_parser(
        "lifecycle", help="run deterministic lifecycle adapter simulation")
    lifecycle.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    lifecycle.add_argument("--iterations", type=int, default=10)
    lifecycle.add_argument(
        "--format", choices=("table", "json"), default="table")

    stress = commands.add_parser(
        "stress", help="run deterministic compiler lifecycle stress")
    stress.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    stress.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    stress.add_argument("--budget-profile", default="ci_warm_path")
    stress.add_argument("--cycles", type=int, default=10)
    stress.add_argument("--restart-every", type=int, default=50)
    stress.add_argument("--format", choices=("table", "json"), default="table")

    scorecard = commands.add_parser(
        "scorecard", help="rank models from versioned benchmark evidence")
    scorecard.add_argument(
        "--scorecard", type=Path, default=DEFAULT_MODEL_SCORECARD)
    scorecard.add_argument(
        "--format", choices=("table", "json"), default="table")

    audit = commands.add_parser(
        "audit-models", help="check reviewed public model metadata for drift")
    audit.add_argument(
        "--scorecard", type=Path, default=DEFAULT_MODEL_SCORECARD)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument(
        "--format", choices=("table", "json"), default="table")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "corpus":
            payload = summarize_corpus(load_corpus(args.corpus))
            rendered = render_corpus(payload)
            status = int(bool(payload["missing_dimensions"]))
        elif args.command == "evaluate":
            payload = evaluate_observations(
                args.observations, load_corpus(args.corpus))
            if args.budget_profile:
                payload["budget"] = evaluate_budgets(
                    payload, load_budgets(args.budgets), args.budget_profile)
            rendered = render_dashboard(payload)
            status = int(
                payload["records"] == 0
                or payload["rejected_records"] > 0
                or ("budget" in payload and not payload["budget"]["passed"])
            )
        elif args.command == "traces":
            payload = evaluate_runtime_traces(args.trace_log)
            rendered = render_runtime_traces(payload)
            status = int(
                payload["records"] == 0 or payload["rejected_records"] > 0)
        elif args.command == "startup":
            payload = evaluate_startup_traces(
                args.cold_trace_log, args.warm_trace_log)
            payload["budget"] = evaluate_budgets(
                payload, load_budgets(args.budgets), args.budget_profile)
            rendered = render_startup_traces(payload)
            status = int(
                payload["records"] == 0
                or payload["rejected_records"] > 0
                or not payload["budget"]["passed"])
        elif args.command == "warm-path":
            payload = summarize_warm_path(args.trace_log)
            if args.budget_profile:
                payload["budget"] = evaluate_budgets(
                    payload, load_budgets(args.budgets), args.budget_profile)
            rendered = render_warm_path(payload)
            status = int(
                payload["records"] == 0
                or payload["rejected_records"] > 0
                or ("budget" in payload and not payload["budget"]["passed"]))
        elif args.command == "lifecycle":
            payload = run_lifecycle_simulation(
                load_corpus(args.corpus), iterations=args.iterations)
            rendered = render_lifecycle_simulation(payload)
            status = int(
                payload["failures"] > 0
                or payload["nondeterministic_outputs"] > 0
                or payload["faults_observed"] != payload["faults_injected"]
                or payload["recoveries"] != payload["faults_injected"])
        elif args.command == "stress":
            payload = run_compiler_stress(
                load_corpus(args.corpus), cycles=args.cycles,
                restart_every=args.restart_every)
            payload["budget"] = evaluate_budgets(
                payload, load_budgets(args.budgets), args.budget_profile)
            rendered = render_stress(payload)
            status = int(
                payload["failures"] > 0
                or payload["nondeterministic_outputs"] > 0
                or not payload["budget"]["passed"])
        elif args.command == "scorecard":
            payload = generate_model_scorecard(
                load_model_scorecard(args.scorecard))
            rendered = render_scorecard(payload)
            status = int(payload["recommendation"] is None)
        else:
            payload = audit_model_sources(load_model_scorecard(args.scorecard))
            rendered = render_model_audit(payload)
            write_json_artifact(args.output, payload)
            status = {"pass": 0, "drift": 1, "error": 2}[payload["status"]]
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        if args.command == "audit-models":
            payload = {
                "schema_version": 1,
                "privacy": "public-model-metadata-only",
                "checked_at": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"),
                "status": "error",
                "summary": {
                    "candidates": 0, "passed": 0, "drifted": 0, "errors": 1,
                },
                "candidates": [],
                "error_type": type(exc).__name__,
            }
            try:
                write_json_artifact(args.output, payload)
            except OSError:
                pass
        detail = "runtime trace input unavailable" \
            if args.command in {"traces", "startup", "warm-path"} else str(exc)
        print(f"performance lab configuration error: {detail}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True)
          if args.format == "json" else rendered)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
