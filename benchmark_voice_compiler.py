# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Reproducible quality and telemetry benchmark for the Voice Compiler.

The golden corpus exercises deterministic compiler behavior.  When a local
``transcripts.jsonl`` is present, the report also summarizes performance
telemetry. User-quality metrics are deliberately gated on a safe post-paste
observation; comparing ``raw`` with ``clean`` is not evidence that the pasted
result survived unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from voice_compiler import (
    ContextCandidate,
    ContextPack,
    EditProposal,
    PersonalPrior,
    ProsodyEvent,
    RecognitionHypothesis,
    VoiceCompiler,
    VoiceIR,
    WordEvidence,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "benchmarks" / "voice_compiler_cases.json"
DEFAULT_TRANSCRIPTS = HERE / "transcripts.jsonl"
PERFORMANCE_FIELDS = (
    "release_s", "asr_s", "compiler_s", "cleanup_s", "confidence",
)
COMPILER_CASE_BUDGET_MS = 50.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sequence."""
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
        "mean": round(statistics.fmean(values), 4),
    }


def _word(item: dict[str, Any]) -> WordEvidence:
    return WordEvidence(
        text=str(item["text"]),
        start=float(item.get("start", 0.0)),
        end=float(item.get("end", 0.0)),
        confidence=float(item.get("confidence", 0.5)),
        engine=str(item.get("engine", "")),
    )


def _hypothesis(item: dict[str, Any]) -> RecognitionHypothesis:
    return RecognitionHypothesis(
        text=str(item["text"]),
        confidence=float(item.get("confidence", 0.5)),
        engine=str(item.get("engine", "")),
        words=tuple(_word(word) for word in item.get("words", ())),
    )


def _context(item: dict[str, Any]) -> ContextPack:
    return ContextPack(
        candidates=tuple(ContextCandidate(
            text=str(candidate["text"]),
            weight=float(candidate.get("weight", 1.0)),
            source=str(candidate.get("source", "context")),
        ) for candidate in item.get("candidates", ())),
        style=item.get("style"),
        constraints=tuple(str(value) for value in item.get("constraints", ())),
    )


def _voice(item: dict[str, Any]) -> VoiceIR:
    return VoiceIR(
        hypotheses=tuple(_hypothesis(value)
                         for value in item.get("hypotheses", ())),
        context=_context(item.get("context", {})),
        personal_priors=tuple(PersonalPrior(
            heard=str(prior["heard"]),
            preferred=str(prior["preferred"]),
            count=int(prior.get("count", 1)),
            apps=tuple((str(app), int(count))
                       for app, count in prior.get("apps", ())),
        ) for prior in item.get("personal_priors", ())),
        prosody=tuple(ProsodyEvent(
            kind=str(event["kind"]),
            at=float(event.get("at", 0.0)),
            duration=float(event.get("duration", 0.0)),
            strength=float(event.get("strength", 1.0)),
        ) for event in item.get("prosody", ())),
        app_bundle=str(item.get("app_bundle", "")),
        mode=str(item.get("mode", "capture")),
        finalized=bool(item.get("finalized", True)),
    )


def _expect_equal(errors: list[str], label: str,
                  actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label}: expected {expected!r}, got {actual!r}")


def evaluate_case(case: dict[str, Any],
                  compiler: VoiceCompiler | None = None) -> dict[str, Any]:
    """Evaluate one declarative golden case and return an explainable result."""
    compiler = compiler or VoiceCompiler()
    errors: list[str] = []
    observed: dict[str, Any] = {}
    operation = case.get("operation", "compile")
    expected = case.get("expect", {})
    started = time.perf_counter()
    try:
        if operation == "compile":
            result = compiler.compile(_voice(case["voice"]))
            observed = {
                "text": result.text,
                "stable_prefix": result.stable_prefix,
                "anchors": list(result.anchors),
                "decision_sources": [item.source for item in result.decisions],
                "decision_reasons": [item.reason for item in result.decisions],
            }
            for key in ("text", "stable_prefix"):
                if key in expected:
                    _expect_equal(errors, key, observed[key], expected[key])
            for key in ("anchors", "decision_sources", "decision_reasons"):
                wanted = expected.get(key)
                if wanted is not None:
                    missing = [value for value in wanted
                               if value not in observed[key]]
                    if missing:
                        errors.append(f"{key}: missing {missing!r}")
        elif operation == "verify_edits":
            proposals = tuple(EditProposal(
                kind=str(item["kind"]),
                before=str(item["before"]),
                after=str(item["after"]),
            ) for item in case.get("proposals", ()))
            result = compiler.verify_edits(
                str(case["source"]), proposals,
                _context(case.get("context", {})).candidates,
            )
            accepted = [index for index, edit in enumerate(result.edits)
                        if edit.accepted]
            rejected = [index for index, edit in enumerate(result.edits)
                        if not edit.accepted]
            observed = {
                "text": result.text,
                "accepted_edits": accepted,
                "rejected_edits": rejected,
                "edit_reasons": [edit.reason for edit in result.edits],
            }
            for key in ("text", "accepted_edits", "rejected_edits"):
                if key in expected:
                    _expect_equal(errors, key, observed[key], expected[key])
            for raw_index, fragment in expected.get(
                    "rejection_contains", {}).items():
                index = int(raw_index)
                reason = result.edits[index].reason \
                    if 0 <= index < len(result.edits) else ""
                if str(fragment) not in reason:
                    errors.append(
                        f"edit {index} reason: missing {fragment!r} in {reason!r}")
        else:
            errors.append(f"unsupported operation: {operation!r}")
    except Exception as exc:  # A bad fixture should be a result, not a crash.
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "id": str(case.get("id", "unnamed")),
        "category": str(case.get("category", "uncategorized")),
        "passed": not errors,
        "errors": errors,
        "observed": observed,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 4),
    }


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported benchmark case schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark corpus must contain at least one case")
    identifiers = [case.get("id") for case in cases if isinstance(case, dict)]
    valid_identifiers = all(
        isinstance(identifier, str) and identifier.strip()
        for identifier in identifiers)
    if (len(identifiers) != len(cases) or not valid_identifiers
            or len(set(identifiers)) != len(cases)):
        raise ValueError("every benchmark case needs a unique id")
    return cases


def evaluate_golden_cases(
        cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    compiler = VoiceCompiler()
    results = [evaluate_case(case, compiler) for case in cases]
    categories: dict[str, dict[str, int]] = {}
    for result in results:
        category = categories.setdefault(
            result["category"], {"passed": 0, "total": 0})
        category["total"] += 1
        category["passed"] += int(result["passed"])
    passed = sum(int(result["passed"]) for result in results)
    latency = _distribution([result["elapsed_ms"] for result in results])
    latency["max"] = round(max(
        result["elapsed_ms"] for result in results), 4)
    latency["budget_ms"] = COMPILER_CASE_BUDGET_MS
    latency["passed"] = latency["max"] <= COMPILER_CASE_BUDGET_MS
    return {
        "passed": passed,
        "total": len(results),
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "categories": categories,
        "compiler_latency": latency,
        "cases": results,
    }


def _accepted_text(entry: dict[str, Any]) -> str | None:
    """Find observed post-paste text without guessing from raw/clean fields."""
    for key in ("observed_text", "accepted_text", "revised_text",
                "post_edit_text"):
        if isinstance(entry.get(key), str):
            return entry[key]
    correction = entry.get("correction")
    if isinstance(correction, dict):
        for key in ("accepted_text", "revised_text", "text"):
            if isinstance(correction.get(key), str):
                return correction[key]
    return None


def _explicit_zero_edit(entry: dict[str, Any]) -> bool | None:
    for container in (entry, entry.get("metrics")):
        if isinstance(container, dict) and isinstance(
                container.get("zero_edit"), bool):
            return container["zero_edit"]
    return None


def character_edit_distance(left: str, right: str) -> int:
    """Levenshtein character distance using O(min(n, m)) working memory."""
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, right_char in enumerate(right, 1):
        current = [row]
        for column, left_char in enumerate(left, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def evaluate_transcripts(path: Path | None) -> dict[str, Any]:
    """Summarize runtime and safe post-paste observations in JSONL."""
    if path is None or not path.exists():
        return {
            "available": False,
            "path": str(path) if path is not None else None,
            "records": 0,
            "malformed_records": 0,
            "performance": {},
            "zero_edit_proxy": {
                "available": False,
                "observations": 0,
                "reason": "transcript log not found",
            },
            "correction_burden": {
                "available": False,
                "observations": 0,
                "reason": "transcript log not found",
            },
        }

    entries: list[dict[str, Any]] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(value, dict):
            malformed += 1
            continue
        entries.append(value)

    metric_values: dict[str, list[float]] = {
        field: [] for field in PERFORMANCE_FIELDS}
    verified: list[bool] = []
    for entry in entries:
        metrics = entry.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for field in PERFORMANCE_FIELDS:
            value = _number(metrics.get(field))
            if value is not None:
                metric_values[field].append(value)
        if isinstance(metrics.get("verified"), bool):
            verified.append(metrics["verified"])
    performance = {
        field: _distribution(values)
        for field, values in metric_values.items() if values
    }
    if verified:
        performance["verified_rate"] = {
            "samples": len(verified),
            "rate": round(sum(verified) / len(verified), 4),
        }

    zero_observations: list[bool] = []
    edit_characters = 0
    edit_words = 0
    burden_observations = 0
    for entry in entries:
        clean = entry.get("clean")
        accepted = _accepted_text(entry)
        explicit_zero = _explicit_zero_edit(entry)
        if isinstance(clean, str) and accepted is not None:
            observed_zero = clean == accepted
            zero_observations.append(
                explicit_zero if explicit_zero is not None else observed_zero)
            words = len(clean.split())
            if words > 0:
                edit_characters += character_edit_distance(clean, accepted)
                edit_words += words
                burden_observations += 1
        elif explicit_zero is not None:
            zero_observations.append(explicit_zero)

    if zero_observations:
        zero_edit: dict[str, Any] = {
            "available": True,
            "observations": len(zero_observations),
            "rate": round(sum(zero_observations) / len(zero_observations), 4),
        }
    else:
        zero_edit = {
            "available": False,
            "observations": 0,
            "reason": "no safe post-paste observation",
        }
    if burden_observations and edit_words:
        burden: dict[str, Any] = {
            "available": True,
            "observations": burden_observations,
            "edit_characters": edit_characters,
            "pasted_words": edit_words,
            "characters_per_100_words": round(
                edit_characters / edit_words * 100, 4),
        }
    else:
        burden = {
            "available": False,
            "observations": 0,
            "reason": "observed text is required to measure edit distance",
        }
    return {
        "available": True,
        "path": str(path),
        "records": len(entries),
        "malformed_records": malformed,
        "performance": performance,
        "zero_edit_proxy": zero_edit,
        "correction_burden": burden,
    }


def build_report(cases_path: Path = DEFAULT_CASES,
                 transcripts_path: Path | None = DEFAULT_TRANSCRIPTS
                 ) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "golden": evaluate_golden_cases(load_cases(cases_path)),
        "transcripts": evaluate_transcripts(transcripts_path),
    }


def _rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_table(report: dict[str, Any]) -> str:
    golden = report["golden"]
    lines = [
        "VOICE COMPILER GOLDEN CORPUS",
        f"{'category':<24} {'passed':>8} {'total':>8} {'rate':>9}",
    ]
    for category, result in sorted(golden["categories"].items()):
        rate = result["passed"] / result["total"] if result["total"] else 0
        lines.append(
            f"{category:<24} {result['passed']:>8} {result['total']:>8} "
            f"{_rate(rate):>9}")
    lines.append(
        f"{'TOTAL':<24} {golden['passed']:>8} {golden['total']:>8} "
        f"{_rate(golden['pass_rate']):>9}")
    latency = golden["compiler_latency"]
    lines.append(
        "compiler case latency: "
        f"p95 {latency['p95']:.4f}ms, max {latency['max']:.4f}ms "
        f"(budget {latency['budget_ms']:.1f}ms: "
        f"{'PASS' if latency['passed'] else 'FAIL'})")
    for case in golden["cases"]:
        if not case["passed"]:
            lines.append(f"FAIL {case['id']}: {'; '.join(case['errors'])}")

    transcripts = report["transcripts"]
    lines.extend(("", "TRANSCRIPT TELEMETRY"))
    if not transcripts["available"]:
        lines.append("log: unavailable")
    else:
        lines.append(
            f"records: {transcripts['records']} "
            f"(malformed skipped: {transcripts['malformed_records']})")
        for metric, result in transcripts["performance"].items():
            if metric == "verified_rate":
                lines.append(
                    f"verified: {_rate(result['rate'])} "
                    f"({result['samples']} samples)")
            else:
                suffix = "" if metric == "confidence" else "s"
                lines.append(
                    f"{metric}: p50 {result['p50']}{suffix}, "
                    f"p95 {result['p95']}{suffix} "
                    f"({result['samples']} samples)")
    zero_edit = transcripts["zero_edit_proxy"]
    if zero_edit["available"]:
        lines.append(
            f"zero-edit proxy: {_rate(zero_edit['rate'])} "
            f"({zero_edit['observations']} observed outcomes)")
    else:
        lines.append(f"zero-edit proxy: unavailable - {zero_edit['reason']}")
    burden = transcripts["correction_burden"]
    if burden["available"]:
        lines.append(
            "correction burden: "
            f"{burden['characters_per_100_words']:.2f} characters/100 words "
            f"({burden['observations']} observed outcomes)")
    else:
        lines.append(f"correction burden: unavailable - {burden['reason']}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument(
        "--format", choices=("table", "json", "both"), default="table")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(args.cases, args.transcripts)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"benchmark configuration error: {exc}", file=sys.stderr)
        return 2
    if args.format in {"table", "both"}:
        print(render_table(report))
    if args.format == "both":
        print()
    if args.format in {"json", "both"}:
        print(json.dumps(report, indent=2, sort_keys=True))
    golden = report["golden"]
    return 0 if (golden["passed"] == golden["total"]
                 and golden["compiler_latency"]["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
