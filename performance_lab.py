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
import json
import gc
import math
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Sequence


HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE / "benchmarks" / "representative_dictation_cases.json"
DEFAULT_BUDGETS = HERE / "benchmarks" / "performance_budgets.json"
DEFAULT_MODEL_SCORECARD = HERE / "benchmarks" / "model_scorecard.json"


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
    "end_to_end", "release", "press",
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


def _number(value: Any, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
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
        if field in value and value[field] not in _ROUTE_IDS:
            return None, f"invalid-{field.replace('_', '-')}"
    if "lifecycle" in value and value["lifecycle"] not in _LIFECYCLE_IDS:
        return None, "invalid-lifecycle"
    if "receipt" in value and value["receipt"] not in _RECEIPTS:
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
        "stage                         p50 ms     p95 ms     p99 ms     max ms",
    ]
    lines.extend(
        f"{stage:<28} {result['p50']:>10.2f} {result['p95']:>10.2f} "
        f"{result['p99']:>10.2f} {result['max']:>10.2f}"
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
        else:
            payload = generate_model_scorecard(
                load_model_scorecard(args.scorecard))
            rendered = render_scorecard(payload)
            status = int(payload["recommendation"] is None)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"performance lab configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True)
          if args.format == "json" else rendered)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
