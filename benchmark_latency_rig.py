# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Opt-in physical end-to-end latency measurement rig.

Two modes, both purely observational.

``trace`` aggregates the runtime's closed-schema ``warm_path`` latency traces
from an operator-supplied ``dictate.log`` into per-stage p50/p95/max
milliseconds. A log with fewer dictations than the minimum sample count
reports ``insufficient-samples`` instead of confident-looking percentiles.
The tool cannot know whether a log came from a live physical session, so every
report says ``source: operator-supplied-trace-log`` and ``physical_evidence``
becomes true only through the explicit ``--operator-attestation`` flag — the
operator's personal statement, mirroring the caller-attested pattern of the
capture harnesses, never something the tool infers.
``physical_conditions_verified`` stays false either way.

``observe`` is an interactive stopwatch for the neutral competitor task
protocol in ``benchmarks/competitor_tasks.json``. The operator runs the
product entirely by hand; the rig records monotonic wall-clock milliseconds
between two Enter presses, prompts for the protocol's closed counts, and
writes an observation file that ``competitor_benchmark.py`` validates. Every
write is re-validated through that evaluator first, and tasks not yet run are
recorded as ``unavailable``/``not_run`` so the file is evaluator-valid at
every point in a sitting.

The rig runs no product, injects no key events, automates no UI, and
generates no audio. Outputs are content-free: stage latencies, counts, task
identifiers, and an operator-supplied artifact reference — never transcript
text. It emits descriptive numbers only; cross-product comparison stays with
the evaluator, which deliberately declares none.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from competitor_benchmark import (
    MEASUREMENT_DEFINITIONS,
    Observation,
    Protocol,
    ProtocolError,
    Task,
    UnavailableReason,
    evaluate_product_run,
)
from performance_lab import WARM_PATH_STAGES, summarize_warm_path


HERE = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = HERE / "benchmarks" / "competitor_tasks.json"
DEFAULT_RUN_DIR = HERE / ".evidence" / "competitor-runs"

SCHEMA_VERSION = 1
TRACE_SOURCE = "operator-supplied-trace-log"
ATTESTED_SCOPE = "physical-operator-attested"
UNATTESTED_SCOPE = "unattested-trace-log"
# Matches the warm_path_stage budget profile: quoting a tail from fewer than
# twenty dictations is exactly the false confidence the gate exists to block.
DEFAULT_MINIMUM_SAMPLES = 20
MAX_MINIMUM_SAMPLES = 100_000
# The protocol's own latency upper bound (one hour, in milliseconds).
MAX_LATENCY_MS = 3_600_000.0
MAX_COUNT = 1_000_000
MAX_REFERENCE_CHARACTERS = 256
PLACEHOLDER_REASON = "not_run"

STAGE_SUMMARY_KEYS = ("samples", "p50", "p95", "max")
# Fixed strings describing what each warm_path field timed, carried in the
# report so the artifact itself says what was measured. `release` is not a
# disjoint stage: dictate.py computes it from hotkey release to pipeline
# completion, so it is the end-to-end number and the other five are component
# timings inside it.
STAGE_DEFINITIONS = {
    "release": ("hotkey release until the dictation pipeline finished, "
                "insertion included; this is the end-to-end wall clock"),
    "asr": "final speech recognition",
    "compiler": "voice compiler pass",
    "cleanup": "cleanup pass",
    "context": "context firewall pass",
    "insertion": "insertion commit",
}


class RigError(ValueError):
    """Raised for invalid input; the command exits 2 and writes nothing."""


class RigAborted(RuntimeError):
    """Raised when the operator quits; the command exits 1 and writes nothing."""


# ------------------------------- trace mode --------------------------------


def build_trace_report(
    trace_log: Path,
    *,
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
    operator_attested: bool = False,
) -> dict[str, Any]:
    """Aggregate warm_path traces with sample gating and honest labelling.

    Parsing is delegated to ``performance_lab.summarize_warm_path``, whose
    trace schema is held identical to ``dictate.py`` by a parity test, so the
    result is transcript-free by construction and this rig cannot drift from
    the runtime's emission format. The input path is never reflected into the
    report.
    """
    if (isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int)
            or not 1 <= minimum_samples <= MAX_MINIMUM_SAMPLES):
        raise RigError(
            f"minimum samples must be between 1 and {MAX_MINIMUM_SAMPLES}")
    try:
        summary = summarize_warm_path(Path(trace_log))
    except OSError as error:
        # Mirror performance_lab: never reflect the path or OS error text.
        raise RigError("runtime trace input unavailable") from error
    records = summary["records"]
    status = "measured" if records >= minimum_samples else "insufficient-samples"
    latency_ms: dict[str, Any] | None = None
    if status == "measured":
        latency_ms = {}
        for label, _field in WARM_PATH_STAGES:
            distribution = summary["latency_ms"].get(label)
            if distribution is not None:
                latency_ms[label] = {
                    key: distribution[key] for key in STAGE_SUMMARY_KEYS}
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_schema_version": summary["trace_schema_version"],
        "privacy": "numeric-aggregates-only",
        "source": TRACE_SOURCE,
        "evidence_scope": ATTESTED_SCOPE if operator_attested else UNATTESTED_SCOPE,
        "operator_attested": bool(operator_attested),
        "physical_evidence": bool(operator_attested),
        "physical_conditions_verified": False,
        "records": records,
        "minimum_samples": minimum_samples,
        "status": status,
        "rejected_records": summary["rejected_records"],
        "rejected_by_reason": summary["rejected_by_reason"],
        "ignored_non_trace_lines": summary["ignored_non_trace_lines"],
        "ignored_non_warm_path_records":
            summary["ignored_non_warm_path_records"],
        "stage_definitions": dict(STAGE_DEFINITIONS),
        "latency_ms": latency_ms,
    }


def render_trace_table(report: dict[str, Any]) -> str:
    lines = [
        "PHYSICAL LATENCY RIG · warm-path traces",
        f"records: {report['records']} (minimum {report['minimum_samples']}) · "
        f"status: {report['status']}",
        f"source: {report['source']} · operator attested: "
        f"{'yes' if report['operator_attested'] else 'no'} · physical evidence: "
        f"{'yes' if report['physical_evidence'] else 'no'}",
        f"rejected traces: {report['rejected_records']} · other trace events "
        f"ignored: {report['ignored_non_warm_path_records']} · non-trace lines "
        f"ignored: {report['ignored_non_trace_lines']}",
    ]
    if report["latency_ms"] is None:
        lines.append(
            f"collect at least {report['minimum_samples']} dictations before "
            "quoting any latency number from this log")
    else:
        for label, _field in WARM_PATH_STAGES:
            stage = report["latency_ms"].get(label)
            if stage is None:
                continue
            lines.append(
                f"{label:<9} p50 {stage['p50']:.1f} ms · "
                f"p95 {stage['p95']:.1f} ms · max {stage['max']:.1f} ms · "
                f"n={stage['samples']}")
        lines.append(
            "release is the end-to-end wall clock; the other stages are "
            "component timings inside it")
    if not report["operator_attested"]:
        lines.append(
            "unattested: rerun with --operator-attestation only if every trace "
            "came from a live dictation session you ran yourself")
    return "\n".join(lines)


# ------------------------------ observe mode -------------------------------


def load_protocol(path: Path) -> Protocol:
    try:
        corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RigError(
            f"cannot read protocol ({error.__class__.__name__})") from error
    try:
        return Protocol.from_mapping(corpus)
    except ProtocolError as error:
        raise RigError(f"invalid protocol: {error}") from error


def placeholder_observation(task_id: str) -> dict[str, Any]:
    """The explicit not-run record the protocol defines for unrun tasks."""
    return {
        "task_id": task_id,
        "evidence_state": "unavailable",
        "completed": None,
        "error_count": None,
        "latency_ms": None,
        "interaction_count": None,
        "unavailable_reason": PLACEHOLDER_REASON,
        "source_reference": None,
    }


def is_placeholder(entry: Mapping[str, Any]) -> bool:
    return (entry.get("evidence_state") == "unavailable"
            and entry.get("unavailable_reason") == PLACEHOLDER_REASON)


def new_run_envelope(protocol: Protocol, product_id: str, run_id: str,
                     environment_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": protocol.protocol_id,
        "product_id": product_id,
        "run_id": run_id,
        "environment_id": environment_id,
        "observations": [
            placeholder_observation(task.task_id) for task in protocol.tasks],
    }


def load_run_envelope(path: Path, protocol: Protocol, product_id: str,
                      run_id: str, environment_id: str | None) -> dict[str, Any]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RigError(
            f"cannot read run file ({error.__class__.__name__})") from error
    try:
        evaluate_product_run(protocol, envelope)
    except ProtocolError as error:
        raise RigError(f"existing run file is invalid: {error}") from error
    for field, expected in (("product_id", product_id), ("run_id", run_id)):
        if envelope[field] != expected:
            raise RigError(
                f"run file records {field} {envelope[field]!r}; pass matching "
                "flags or a different --run-file")
    if environment_id is not None and envelope["environment_id"] != environment_id:
        raise RigError(
            f"run file was recorded in environment "
            f"{envelope['environment_id']!r}; a different environment needs a "
            "new run file")
    return envelope


def record_observation(envelope: dict[str, Any], observation: dict[str, Any],
                       *, redo: bool = False) -> None:
    """Replace one task's entry. A recorded task is never silently rewritten."""
    for index, entry in enumerate(envelope["observations"]):
        if entry.get("task_id") == observation["task_id"]:
            if not is_placeholder(entry) and not redo:
                raise RigError(
                    f"task {observation['task_id']!r} is already recorded; "
                    "pass --redo to replace it")
            envelope["observations"][index] = observation
            return
    raise RigError("run file does not cover this task")


def _read_answer(reader: TextIO, writer: TextIO) -> str:
    writer.write("> ")
    writer.flush()
    line = reader.readline()
    if line == "":
        raise RigAborted(
            "input ended before the observation was complete; nothing was "
            "written")
    stripped = line.strip()
    if stripped == "q":
        raise RigAborted("operator quit; nothing was written")
    return stripped


def _press_enter(message: str, *, reader: TextIO, writer: TextIO) -> None:
    writer.write(f"\n{message}\n")
    _read_answer(reader, writer)


def _ask_yes_no(question: str, *, reader: TextIO, writer: TextIO) -> bool:
    while True:
        writer.write(
            f"\n{question}\n  [y] yes\n  [n] no\n"
            "  [q] quit without writing\n")
        answer = _read_answer(reader, writer)
        if answer in ("y", "n"):
            return answer == "y"
        writer.write(f"  '{answer}' is not one of the listed keys.\n")


def _ask_count(question: str, *, reader: TextIO, writer: TextIO) -> int:
    while True:
        writer.write(
            f"\n{question}\n  (a whole number; q quits without writing)\n")
        answer = _read_answer(reader, writer)
        if answer.isdigit() and int(answer) <= MAX_COUNT:
            return int(answer)
        writer.write(
            f"  '{answer}' is not a whole number between 0 and {MAX_COUNT}.\n")


def _ask_reference(question: str, *, reader: TextIO, writer: TextIO) -> str:
    while True:
        writer.write(
            f"\n{question}\n  (1-{MAX_REFERENCE_CHARACTERS} printable "
            "characters; q quits without writing)\n")
        answer = _read_answer(reader, writer)
        if 1 <= len(answer) <= MAX_REFERENCE_CHARACTERS and answer.isprintable():
            return answer
        writer.write(
            "  the reference must be short printable text naming your "
            "artifact.\n")


def measured_observation(task: Task, *, reader: TextIO, writer: TextIO,
                         clock: Callable[[], float]) -> dict[str, Any]:
    """Stopwatch one attempt. Latency comes only from the monotonic clock.

    There is deliberately no flag that accepts a typed-in latency: the number
    in the observation is the interval between the two Enter presses, nothing
    else. Counts are operator-counted because the protocol defines them that
    way, and the artifact reference names a recording or note — never
    dictated text.
    """
    writer.write(
        f"\nTask {task.task_id} — {task.title}\n"
        f"Procedure: {task.procedure}\n"
        f"Complete when: {task.completion_rule}\n\n"
        f"Latency rule: {MEASUREMENT_DEFINITIONS['latency_ms']}\n"
        f"Interaction rule: {MEASUREMENT_DEFINITIONS['interaction_count']}\n")
    _press_enter(
        "Get the product ready, then press Enter EXACTLY as you perform the "
        "first user action of the written procedure. The clock starts on that "
        "Enter. (q quits without writing.)", reader=reader, writer=writer)
    started = clock()
    _press_enter(
        "Press Enter the INSTANT the completion rule is met, or the moment "
        "you end the attempt. The clock stops on that Enter.",
        reader=reader, writer=writer)
    latency_ms = (clock() - started) * 1000.0
    if not 0.0 <= latency_ms <= MAX_LATENCY_MS:
        raise RigError(
            "the measured attempt fell outside the protocol's one-hour "
            "latency bound")
    completed = _ask_yes_no(
        "Was the completion rule met before you ended the attempt?",
        reader=reader, writer=writer)
    while True:
        error_count = _ask_count(
            "Error count — " + MEASUREMENT_DEFINITIONS["error_count"],
            reader=reader, writer=writer)
        if completed or error_count >= 1:
            break
        writer.write(
            "\nAn incomplete measured task must record at least one error.\n")
    interaction_count = _ask_count(
        "Interaction count — " + MEASUREMENT_DEFINITIONS["interaction_count"]
        + " Enter presses for this rig do not count.",
        reader=reader, writer=writer)
    reference = _ask_reference(
        "Artifact reference backing this attempt (a screen-recording file "
        "name or note id — never dictated text)", reader=reader, writer=writer)
    writer.write(f"\nrecorded latency: {latency_ms:.1f} ms (monotonic clock)\n")
    return {
        "task_id": task.task_id,
        "evidence_state": "measured",
        "completed": completed,
        "error_count": error_count,
        "latency_ms": round(latency_ms, 1),
        "interaction_count": interaction_count,
        "unavailable_reason": None,
        "source_reference": reference,
    }


def unavailable_observation(task_id: str, reason: str) -> dict[str, Any]:
    entry = placeholder_observation(task_id)
    entry["unavailable_reason"] = reason
    return entry


def claimed_only_observation(task_id: str, reference: str) -> dict[str, Any]:
    entry = placeholder_observation(task_id)
    entry["evidence_state"] = "claimed_only"
    entry["unavailable_reason"] = None
    entry["source_reference"] = reference
    return entry


# ------------------------------ private writes -----------------------------


def _secure_parent(path: Path) -> None:
    """Create missing parent directories owner-only; never loosen existing."""
    missing = []
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        for created in missing:
            os.chmod(created, 0o700)


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    _secure_parent(path)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp")
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if os.name == "posix":
            os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


# -------------------------------- commands ---------------------------------


def trace_command(args: argparse.Namespace, writer: TextIO) -> int:
    report = build_trace_report(
        args.trace_log,
        minimum_samples=args.minimum_samples,
        operator_attested=args.operator_attestation,
    )
    if args.output is not None:
        atomic_write_json(args.output, report)
    if args.format == "json":
        writer.write(json.dumps(report, sort_keys=True) + "\n")
    else:
        writer.write(render_trace_table(report) + "\n")
    return 0 if report["status"] == "measured" else 1


def tasks_command(args: argparse.Namespace, writer: TextIO) -> int:
    protocol = load_protocol(args.protocol)
    writer.write(
        f"protocol {protocol.protocol_id} · {len(protocol.tasks)} tasks\n")
    for task in protocol.tasks:
        writer.write(
            f"\n{task.task_id} ({task.category}) — {task.title}\n"
            f"  procedure: {task.procedure}\n"
            f"  complete when: {task.completion_rule}\n")
    return 0


def observe_command(args: argparse.Namespace, *, reader: TextIO,
                    writer: TextIO, clock: Callable[[], float]) -> int:
    protocol = load_protocol(args.protocol)
    task = next(
        (item for item in protocol.tasks if item.task_id == args.task), None)
    if task is None:
        known = ", ".join(item.task_id for item in protocol.tasks)
        raise RigError(f"unknown task {args.task!r}; protocol tasks: {known}")
    run_path = args.run_file
    if run_path is None:
        run_path = DEFAULT_RUN_DIR / f"{args.product}-{args.run_id}.json"
    if Path(run_path).exists():
        envelope = load_run_envelope(
            Path(run_path), protocol, args.product, args.run_id,
            args.environment)
    else:
        if not args.environment:
            raise RigError(
                "a new run file needs --environment naming the machine, "
                "microphone, and room conditions in identifier form, e.g. "
                "mac-m2-quiet-room")
        envelope = new_run_envelope(
            protocol, args.product, args.run_id, args.environment)
        try:
            evaluate_product_run(protocol, envelope)
        except ProtocolError as error:
            raise RigError(f"invalid run identity: {error}") from error

    # Check overwrite rules before the stopwatch so no attempt is wasted.
    current = next(
        entry for entry in envelope["observations"]
        if entry.get("task_id") == task.task_id)
    if not is_placeholder(current) and not args.redo:
        raise RigError(
            f"task {task.task_id!r} is already recorded in this run file; "
            "pass --redo to replace it")

    if args.state == "measured":
        if args.reason is not None or args.source_reference is not None:
            raise RigError(
                "measured observations take no --reason or --source-reference "
                "flags; the rig prompts for the artifact reference")
        observation = measured_observation(
            task, reader=reader, writer=writer, clock=clock)
    elif args.state == "unavailable":
        if args.reason is None:
            reasons = ", ".join(sorted(item.value for item in UnavailableReason))
            raise RigError(f"--state unavailable needs --reason ({reasons})")
        if args.source_reference is not None:
            raise RigError(
                "unavailable observations cannot carry a source reference")
        observation = unavailable_observation(task.task_id, args.reason)
    else:
        if args.source_reference is None:
            raise RigError(
                "--state claimed-only needs --source-reference naming the "
                "official published claim")
        if args.reason is not None:
            raise RigError("claimed-only observations take no --reason")
        observation = claimed_only_observation(
            task.task_id, args.source_reference)

    try:
        Observation.from_mapping(observation)
    except ProtocolError as error:
        raise RigError(f"observation failed protocol validation: {error}") \
            from error
    record_observation(envelope, observation, redo=args.redo)
    try:
        result = evaluate_product_run(protocol, envelope)
    except ProtocolError as error:
        raise RigError(f"run failed protocol validation: {error}") from error
    atomic_write_json(Path(run_path), envelope)
    coverage = result["coverage"]
    writer.write(
        f"\nrecorded {args.state} observation for {task.task_id} in "
        f"{run_path}\n"
        f"coverage: {coverage['measured']} measured · "
        f"{coverage['unavailable']} unavailable · "
        f"{coverage['claimed_only']} claimed-only of {coverage['tasks']} "
        "tasks\n"
        "evaluate with:\n"
        f"  uv run competitor_benchmark.py --protocol {args.protocol} "
        f"{run_path}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_latency_rig.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    trace = commands.add_parser(
        "trace",
        help="aggregate warm_path traces from an operator-supplied runtime log")
    trace.add_argument("--trace-log", type=Path, required=True)
    trace.add_argument(
        "--minimum-samples", type=int, default=DEFAULT_MINIMUM_SAMPLES)
    trace.add_argument(
        "--operator-attestation", action="store_true",
        help="your personal statement that every trace in the log came from a "
             "live dictation session you ran; the tool cannot verify this")
    trace.add_argument("--format", choices=("table", "json"), default="table")
    trace.add_argument("--output", type=Path, default=None)

    observe = commands.add_parser(
        "observe",
        help="stopwatch one competitor-protocol task by hand and record the "
             "observation")
    observe.add_argument("--product", required=True)
    observe.add_argument("--task", required=True)
    observe.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    observe.add_argument("--run-file", type=Path, default=None)
    observe.add_argument("--run-id", default="run-1")
    observe.add_argument("--environment", default=None)
    observe.add_argument(
        "--state", choices=("measured", "unavailable", "claimed-only"),
        default="measured")
    observe.add_argument(
        "--reason",
        choices=tuple(sorted(item.value for item in UnavailableReason)),
        default=None)
    observe.add_argument("--source-reference", default=None)
    observe.add_argument(
        "--redo", action="store_true",
        help="replace a task that is already recorded in the run file")

    tasks = commands.add_parser("tasks", help="print the protocol's tasks")
    tasks.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    return parser


def main(argv: Sequence[str] | None = None, *,
         reader: TextIO | None = None, writer: TextIO | None = None,
         clock: Callable[[], float] | None = None) -> int:
    reader = reader or sys.stdin
    writer = writer or sys.stdout
    clock = clock or time.monotonic
    args = build_parser().parse_args(argv)
    try:
        if args.command == "trace":
            return trace_command(args, writer)
        if args.command == "tasks":
            return tasks_command(args, writer)
        return observe_command(
            args, reader=reader, writer=writer, clock=clock)
    except RigError as error:
        writer.write(f"benchmark_latency_rig: {error}\n")
        return 2
    except RigAborted as stop:
        writer.write(f"benchmark_latency_rig: {stop}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
