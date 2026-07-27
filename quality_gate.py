# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

"""The release dictation-quality gate: quality moves only on purpose.

Every deterministic quality suite in the repository already measures
something -- the Voice Compiler golden corpus, the five synthetic scorecard
suites, the cleanup proof-recovery mediator, and the compiler stress loop.
What none of them did was *remember*: a regression only failed a release if
some expectation inside a test happened to cover it. This gate replays the
deterministic collectors, flattens the numbers that describe dictation
quality, and compares every one against the pinned baseline in
``benchmarks/quality_baseline.json``.

Any tracked metric that differs from the baseline fails the gate, in either
direction: a drop is a regression, and an unrecorded improvement is a
baseline someone forgot to move. Both are resolved the same deliberate way --
rerun with ``--rebaseline`` and commit the diff, so the change is visible in
review next to the code that caused it.

Three disciplines, inherited from the rest of the repository:

* **Deterministic only.** The collectors here run from checked-in synthetic
  corpora with no model loads, no audio, and no network. Hardware-dependent
  numbers (latency percentiles, live-transcript metrics such as Correction
  Burden over real use) are deliberately excluded: they belong to
  ``performance_lab.py`` traces and the physical-evidence pipeline, and a
  regression gate that varies by machine is a gate nobody trusts.
  ``benchmark_voice_compiler.py`` in particular also reports over the live
  ``transcripts.jsonl``; this gate reads only its golden-corpus half.
* **Content-free.** The baseline stores counts and rates, never case text.
* **Fail closed.** A collector that cannot run, emits an unexpected shape,
  or disagrees with the baseline exits non-zero with the metric named.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
DEFAULT_BASELINE = HERE / "benchmarks" / "quality_baseline.json"
SCHEMA_VERSION = 1

# Deterministic, clone-only stress: two passes over the corpus with a
# restart interval low enough that the warmed compiler is genuinely torn
# down and replaced mid-run (44 operations / every 15 -> restarts at 15
# and 30 beyond the initial construction). The default interval of 50
# would never be reached, and a lifecycle regression that only appears
# when a warm compiler is replaced would sail through.
STRESS_CYCLES = 2
STRESS_RESTART_EVERY = 15

# The gate never reads private runtime history. benchmark_voice_compiler
# defaults its transcript half to the live transcripts.jsonl, so point it
# at a path that cannot exist; only the checked-in golden half is used.
NO_TRANSCRIPTS = "quality-gate-uses-no-private-transcripts.jsonl.absent"


def _run_json(script: str, *args: str,
              allow_nonzero: bool = False) -> dict[str, Any]:
    """Run a repository collector and parse its JSON report.

    ``allow_nonzero`` is for collectors whose exit code also enforces their
    own hardware-dependent latency budgets: on a slow runner they exit
    non-zero with a perfectly valid report. This gate tracks deterministic
    counts only, so for those collectors the report is authoritative and
    the exit code is not -- but an unparseable report still fails.
    """
    result = subprocess.run(
        [sys.executable, str(HERE / script), *args],
        capture_output=True, text=True, timeout=600)
    if result.returncode != 0 and not allow_nonzero:
        raise RuntimeError(
            f"{script} exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[-400:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{script} did not emit JSON: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{script} emitted a non-object report")
    return payload


def collect_voice_compiler() -> dict[str, Any]:
    report = _run_json(
        "benchmark_voice_compiler.py", "--format", "json",
        "--transcripts", str(HERE / NO_TRANSCRIPTS),
        allow_nonzero=True)
    if report.get("transcripts", {}).get("available") is not False:
        raise RuntimeError(
            "the voice-compiler collector read a transcript file; the "
            "quality gate must never consume private runtime history")
    golden = report.get("golden")
    if not isinstance(golden, dict):
        raise RuntimeError("voice compiler report has no golden section")
    metrics = {
        "golden.passed": golden["passed"],
        "golden.total": golden["total"],
    }
    for category, cells in sorted(golden.get("categories", {}).items()):
        metrics[f"golden.{category}.passed"] = cells["passed"]
        metrics[f"golden.{category}.total"] = cells["total"]
    return metrics


def collect_synthetic_scorecard() -> dict[str, Any]:
    report = _run_json("public_scorecard.py")
    if report.get("physical_validation") is not False:
        raise RuntimeError(
            "the synthetic scorecard claims physical validation; this gate "
            "must never consume physical evidence")
    metrics = {
        "scorecard.cases": report["totals"]["cases"],
        "scorecard.passed": report["totals"]["passed"],
        "scorecard.failed": report["totals"]["failed"],
        "scorecard.critical_failures": report["totals"]["critical_failures"],
    }
    for suite in report.get("suites", ()):
        prefix = f"scorecard.{suite['suite_id']}"
        metrics[f"{prefix}.passed"] = suite["passed"]
        metrics[f"{prefix}.failed"] = suite["failed"]
        metrics[f"{prefix}.critical_failures"] = suite["critical_failures"]
    return metrics


def collect_proof_recovery() -> dict[str, Any]:
    report = _run_json("benchmark_cleanup_proof_recovery.py",
                       "--format", "json")
    metrics = {
        "proof_recovery.cases": report["cases"],
        "proof_recovery.recovered": report["recovered"],
        "proof_recovery.rejected": report["rejected"],
        "proof_recovery.replay_verified": report["replay_verified"],
        "proof_recovery.abandoned_anchor_count":
            report["abandoned_anchor_count"],
        # Edit and anchor granularity: the same 27/3 split can hide a
        # materially different mediator, so the finer deterministic
        # counters are pinned too.
        "proof_recovery.edit_count": report["edit_count"],
        "proof_recovery.anchor_count": report["anchor_count"],
    }
    for reason, count in sorted(report.get("reason_counts", {}).items()):
        metrics[f"proof_recovery.reason.{reason}"] = count
    return metrics


def collect_compiler_stress() -> dict[str, Any]:
    report = _run_json(
        "performance_lab.py", "stress",
        "--cycles", str(STRESS_CYCLES),
        "--restart-every", str(STRESS_RESTART_EVERY),
        "--format", "json",
        allow_nonzero=True)
    return {
        "stress.operations": report["operations"],
        "stress.failures": report["failures"],
        "stress.nondeterministic_outputs":
            report["nondeterministic_outputs"],
        "stress.compiler_restarts": report["compiler_restarts"],
    }


COLLECTORS: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("voice_compiler", collect_voice_compiler),
    ("synthetic_scorecard", collect_synthetic_scorecard),
    ("proof_recovery", collect_proof_recovery),
    ("compiler_stress", collect_compiler_stress),
)


def collect_metrics(
        collectors=COLLECTORS) -> tuple[dict[str, Any], list[str]]:
    """Run every collector; return (metrics, errors). Never raises."""
    metrics: dict[str, Any] = {}
    errors: list[str] = []
    for name, collector in collectors:
        try:
            collected = collector()
        except Exception as error:  # fail closed, with the collector named
            errors.append(f"{name}: {error}")
            continue
        for key, value in collected.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{name}: metric {key} is not a number")
            elif key in metrics:
                errors.append(f"{name}: duplicate metric {key}")
            else:
                metrics[key] = value
    return metrics, errors


def compare(metrics: dict[str, Any],
            baseline: dict[str, Any]) -> list[str]:
    """Every difference from the baseline, spelled out. Empty means pass.

    Equality, not thresholds: these collectors are deterministic, so the
    honest expectation is exactness. A drop is a regression; a rise is an
    improvement the baseline must record; a metric that appears or
    disappears means the corpus changed and the baseline must say so.
    """
    problems = []
    pinned = baseline.get("metrics")
    if baseline.get("schema_version") != SCHEMA_VERSION \
            or not isinstance(pinned, dict) or not pinned:
        return ["baseline is missing, empty, or an unsupported schema; "
                "run --rebaseline and commit the result"]
    for key in sorted(set(pinned) - set(metrics)):
        problems.append(
            f"{key}: pinned at {pinned[key]} but no longer measured")
    for key in sorted(set(metrics) - set(pinned)):
        problems.append(
            f"{key}: measured at {metrics[key]} but not pinned; "
            "rebaseline to record it")
    for key in sorted(set(pinned) & set(metrics)):
        if metrics[key] != pinned[key]:
            problems.append(
                f"{key}: measured {metrics[key]}, baseline {pinned[key]}")
    return problems


def write_baseline(path: Path, metrics: dict[str, Any]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "description": (
            "Pinned deterministic dictation-quality metrics. The release "
            "gate (quality_gate.py) fails on any difference, so quality "
            "changes are always visible in review beside the change that "
            "caused them. Content-free: counts and rates only."),
        "metrics": {key: metrics[key] for key in sorted(metrics)},
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=DEFAULT_BASELINE,
        help="pinned metrics file (default benchmarks/quality_baseline.json)")
    parser.add_argument(
        "--rebaseline", action="store_true",
        help="rewrite the baseline from the current measurement; commit the "
             "diff so the quality change is reviewed with its cause")
    args = parser.parse_args(argv)

    metrics, errors = collect_metrics()
    for error in errors:
        print(f"!! collector failed: {error}")
    if errors:
        return 2

    if args.rebaseline:
        write_baseline(args.baseline, metrics)
        print(f"baseline rewritten with {len(metrics)} metrics -> "
              f"{args.baseline}")
        return 0

    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"!! could not read baseline: {error}")
        return 2

    problems = compare(metrics, baseline)
    if problems:
        print("dictation-quality gate FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        print("If this change is intended, rerun with --rebaseline and "
              "commit the updated baseline in the same change.")
        return 1
    print(f"dictation-quality gate passed: {len(metrics)} pinned metrics "
          "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
