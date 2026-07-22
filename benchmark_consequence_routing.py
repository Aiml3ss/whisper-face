# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic privacy-safe benchmark for consequence routing."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from voice_compiler import (
    ContextCandidate,
    ContextPack,
    RecognitionHypothesis,
    TOKEN_RE,
    VoiceIR,
    WordEvidence,
    build_consequence_plan,
    execute_consequence_plan,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "benchmarks" / "consequence_routing_cases.json"
DEFAULT_ITERATIONS = 100
DEFAULT_P95_BUDGET_MS = 5.0
CASE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CASE_KEYS = frozenset({
    "id", "text", "expect", "confidence", "engine", "timing", "mode",
    "alternative", "audio_duration", "word_start", "word_end", "context",
})
EXPECT_KEYS = frozenset({
    "categories", "allowed_extra_categories", "excluded_categories", "route",
    "relisten_min", "skip_reason",
})
RISK_CATEGORIES = frozenset({
    "name", "number", "currency", "date", "time", "recipient", "contact",
    "url", "path", "command", "action",
})
ROUTES = frozenset({"standard", "protected", "review"})
SKIP_REASONS = frozenset({
    "timing-unavailable", "span-not-micro", "selection-limit",
    "verifier-unavailable",
})
TIMINGS = frozenset({"native", "segment"})
MODES = frozenset({"capture", "compose", "edit", "reply", "command", "code"})
ENGINES = frozenset({
    "parakeet-unified", "tiny", "turbo", "whisper-tiny", "whisper-turbo",
})


def _finite_number(value: Any, *, minimum: float, maximum: float) -> bool:
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and minimum <= float(value) <= maximum)


def _closed_categories(value: Any) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, str) and item in RISK_CATEGORIES
                    for item in value)
            and len(value) == len(set(value)))


def _valid_case(case: Any) -> bool:
    if (not isinstance(case, dict) or not set(case) <= CASE_KEYS
            or not isinstance(case.get("id"), str)
            or CASE_ID_RE.fullmatch(case["id"]) is None
            or not isinstance(case.get("text"), str)
            or not 0 < len(case["text"]) <= 4000
            or not isinstance(case.get("expect"), dict)
            or not set(case["expect"]) <= EXPECT_KEYS
            or not _finite_number(
                case.get("audio_duration"), minimum=0.05, maximum=600.0)):
        return False
    if "confidence" in case and not _finite_number(
            case["confidence"], minimum=0.0, maximum=1.0):
        return False
    if case.get("timing", "native") not in TIMINGS \
            or case.get("mode", "capture") not in MODES \
            or case.get("engine", "parakeet-unified") not in ENGINES:
        return False
    if "alternative" in case and (
            not isinstance(case["alternative"], str)
            or len(case["alternative"]) > 4000):
        return False
    for key in ("word_start", "word_end"):
        if key in case and not _finite_number(
                case[key], minimum=0.0, maximum=600.0):
            return False
    if ("word_start" in case and "word_end" in case
            and float(case["word_end"]) <= float(case["word_start"])):
        return False
    context = case.get("context", [])
    if not isinstance(context, list) or len(context) > 50:
        return False
    for item in context:
        if (not isinstance(item, dict)
                or not set(item) <= {"text", "weight", "source"}
                or not isinstance(item.get("text"), str)
                or not 0 < len(item["text"]) <= 200
                or not _finite_number(
                    item.get("weight", 1.0), minimum=0.0, maximum=10.0)
                or item.get("source", "context") not in {
                    "context", "document", "repository", "active-context"}):
            return False
    expect = case["expect"]
    if not _closed_categories(expect.get("categories")):
        return False
    for key in ("allowed_extra_categories", "excluded_categories"):
        if key in expect and not _closed_categories(expect[key]):
            return False
    if expect.get("route") not in ROUTES:
        return False
    if "relisten_min" in expect and (
            isinstance(expect["relisten_min"], bool)
            or not isinstance(expect["relisten_min"], int)
            or not 0 <= expect["relisten_min"] <= 2):
        return False
    if "skip_reason" in expect and expect["skip_reason"] not in SKIP_REASONS:
        return False
    return True


def load_cases(path: Path = DEFAULT_CASES) -> tuple[dict[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("privacy") != "synthetic-text-only"
            or not isinstance(cases, list) or not cases):
        raise ValueError("unsupported consequence benchmark corpus")
    identifiers: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for case in cases:
        if not _valid_case(case):
            raise ValueError("invalid consequence benchmark case")
        if case["id"] in identifiers:
            raise ValueError("consequence benchmark case ids must be unique")
        identifiers.add(case["id"])
        normalized.append(case)
    return tuple(normalized)


def _voice(case: dict[str, Any]) -> VoiceIR:
    text = case["text"]
    confidence = float(case.get("confidence", 0.8))
    engine = str(case.get("engine", "parakeet-unified"))
    timing = str(case.get("timing", "native"))
    words: list[WordEvidence] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if not any(character.isalnum() for character in token):
            continue
        index = len(words)
        start = float(case.get("word_start", 1.0)) + index * 0.34
        end = (float(case["word_end"]) if len(words) == 0
               and "word_end" in case else start + 0.22)
        words.append(WordEvidence(
            token, start, end, confidence, engine, timing))
    hypotheses = [RecognitionHypothesis(
        text, confidence, engine, tuple(words))]
    alternative = case.get("alternative")
    if isinstance(alternative, str) and alternative:
        hypotheses.append(RecognitionHypothesis(
            alternative, min(1.0, confidence + 0.05), "whisper-turbo"))
    context = ContextPack(tuple(ContextCandidate(
        str(item["text"]), float(item.get("weight", 1.0)),
        str(item.get("source", "context")))
        for item in case.get("context", ()) if isinstance(item, dict)))
    return VoiceIR(
        tuple(hypotheses), context=context,
        mode=str(case.get("mode", "capture")))


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    voice = _voice(case)
    plan = build_consequence_plan(
        voice, audio_duration=float(case["audio_duration"]))
    receipt = execute_consequence_plan(voice, plan)
    categories = sorted({risk.category for risk in plan.risks})
    expected = case["expect"]
    errors: list[str] = []
    expected_categories = set(expected.get("categories", ()))
    allowed_extra = set(expected.get("allowed_extra_categories", ()))
    missing = sorted(expected_categories - set(categories))
    unexpected = sorted(
        (set(categories) - expected_categories - allowed_extra)
        | (set(expected.get("excluded_categories", ())) & set(categories)))
    if missing:
        errors.append("missing categories: " + ", ".join(missing))
    if unexpected:
        errors.append("unexpected categories: " + ", ".join(unexpected))
    if receipt.route != expected.get("route"):
        errors.append(
            f"expected route {expected.get('route')}, got {receipt.route}")
    minimum = int(expected.get("relisten_min", 0))
    if len(plan.relisten_requests) < minimum:
        errors.append(
            f"expected at least {minimum} re-listens, got "
            f"{len(plan.relisten_requests)}")
    skip_reason = expected.get("skip_reason")
    if skip_reason and skip_reason not in dict(receipt.relisten_skipped):
        errors.append(f"missing skip reason: {skip_reason}")
    return {
        "id": case["id"],
        "passed": not errors,
        "errors": errors,
        "risk_counts": dict(receipt.risk_counts),
        "route": receipt.route,
        "relisten_selected": receipt.relisten_selected,
        "relisten_status": receipt.relisten_status,
    }


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


def build_report(
        path: Path = DEFAULT_CASES, *, iterations: int = DEFAULT_ITERATIONS,
        budget_ms: float = DEFAULT_P95_BUDGET_MS) -> dict[str, Any]:
    if not isinstance(iterations, int) or isinstance(iterations, bool) \
            or not 1 <= iterations <= 10_000:
        raise ValueError("iterations must be between 1 and 10000")
    if isinstance(budget_ms, bool) or not isinstance(budget_ms, (int, float)) \
            or not 0.0 < float(budget_ms) <= 1000.0:
        raise ValueError("budget_ms must be between 0 and 1000")
    cases = load_cases(path)
    results = [evaluate_case(case) for case in cases]
    public_identifiers = {
        case["id"]: f"case-{index:03d}"
        for index, case in enumerate(cases, start=1)
    }
    for result in results:
        result["id"] = public_identifiers[result["id"]]
    durations: list[float] = []
    per_case_durations: dict[str, list[float]] = {
        public_identifiers[case["id"]]: [] for case in cases}
    # One untimed warmup prevents import/cache initialization from being
    # mistaken for a steady-state selector request.
    for case in cases:
        evaluate_case(case)
    for _ in range(iterations):
        for case in cases:
            started = time.perf_counter()
            evaluate_case(case)
            duration = (time.perf_counter() - started) * 1000.0
            durations.append(duration)
            per_case_durations[public_identifiers[case["id"]]].append(duration)
    p95 = _percentile(durations, 0.95)
    per_case_latency = {
        identifier: {
            "samples": len(samples),
            "p95": round(_percentile(samples, 0.95), 4),
            "max": round(max(samples), 4),
        }
        for identifier, samples in per_case_durations.items()
    }
    worst_case_p95 = max(
        item["p95"] for item in per_case_latency.values())
    return {
        "schema_version": 1,
        "privacy": "synthetic-input-transcript-free-results",
        "scope": "synthetic-selector-only",
        "verifier_exercised": False,
        "audio_exercised": False,
        "runtime_backend_exercised": False,
        "physical_evidence": False,
        "cases": len(results),
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "results": results,
        "latency_ms": {
            "samples": len(durations),
            "p50": round(statistics.median(durations), 4),
            "p95": round(p95, 4),
            "max": round(max(durations), 4),
            "worst_case_p95": round(worst_case_p95, 4),
            "budget": float(budget_ms),
            "passed": worst_case_p95 <= float(budget_ms),
            "gate": "worst-case-per-case-p95",
            "per_case": per_case_latency,
        },
    }


def render_table(report: dict[str, Any]) -> str:
    latency = report["latency_ms"]
    lines = [
        "CONSEQUENCE ROUTING BENCHMARK",
        f"cases: {report['passed']}/{report['cases']} passed",
        f"selector latency: p50 {latency['p50']:.3f} ms · "
        f"request p95 {latency['p95']:.3f} ms · "
        f"worst case p95 {latency['worst_case_p95']:.3f} ms · "
        f"budget {latency['budget']:.1f} ms",
    ]
    for result in report["results"]:
        lines.append(
            f"{'PASS' if result['passed'] else 'FAIL'} {result['id']} · "
            f"{result['route']} · {sum(result['risk_counts'].values())} risks")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--budget-ms", type=float,
                        default=DEFAULT_P95_BUDGET_MS)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)
    try:
        report = build_report(
            args.cases, iterations=args.iterations, budget_ms=args.budget_ms)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"consequence benchmark configuration error: {error}",
              file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True)
          if args.format == "json" else render_table(report))
    return int(report["failed"] > 0 or not report["latency_ms"]["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
