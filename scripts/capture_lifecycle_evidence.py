#!/usr/bin/env python3
"""Guided, resumable capture of the physical lifecycle and stress evidence.

Ledger row 16 says the deterministic adapter simulation is done and that
"physical device-switch, sleep/wake, thermal, memory, and long-audio evidence
remains". `performance_lab.py lifecycle` says the same thing in machine terms:
it emits `physical_evidence: false` and a `requires_physical_validation` list
of three ids it cannot discharge by itself.

This tool runs the session that discharges them. It uses the same five scenario
names the simulation uses — `long-form`, `back-to-back`, `process-restart`,
`sleep-wake`, `audio-device-switch` — so a physical run and a simulated run can
be read side by side.

Each run has two halves:

* the runtime's own report, read from the transcript-free keys of
  `transcripts.jsonl` (how many utterances survived, what insertion state each
  one reached, whether any was diverted to the Voice Outbox) plus the count of
  `[audio] capture ready` lines the process printed while re-opening a stream;
* the operator's own closed observations — whether every spoken utterance
  produced text, whether the runtime recovered without being restarted, and how
  the machine itself behaved thermally.

The tool decides nothing. A run whose utterance count does not match what the
operator was asked to speak is recorded as observed, not corrected, and a run
the runtime did not log at all is recorded as blocked.

    uv run scripts/capture_lifecycle_evidence.py plan
    uv run scripts/capture_lifecycle_evidence.py run
    uv run scripts/capture_lifecycle_evidence.py emit --out lifecycle.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_session_support import (  # noqa: E402
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_RUNTIME_LOG,
    DEFAULT_TRANSCRIPTS,
    CaptureError,
    Choice,
    Session,
    SessionAborted,
    ask_choice,
    atomic_write_json,
    new_transcript_receipts,
    progress_line,
    transcript_baseline,
    utc_now,
    wait_for_enter,
)


TOOL = "capture_lifecycle_evidence"
ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_SESSION = DEFAULT_EVIDENCE_DIR / "lifecycle-session.json"
CAPTURE_READY_MARKER = "[audio] capture ready"

# Exactly the scenario keys `performance_lab.run_lifecycle_simulation` reports.
SCENARIOS = (
    "long-form", "back-to-back", "process-restart", "sleep-wake",
    "audio-device-switch")

# Exactly the ids `performance_lab.run_lifecycle_simulation` lists under
# `requires_physical_validation`, mapped to the scenario that discharges each.
PHYSICAL_VALIDATION_IDS = {
    "physical-long-audio-memory-thermal": "long-form",
    "physical-operating-system-sleep-wake": "sleep-wake",
    "physical-audio-device-switch": "audio-device-switch",
}

SCENARIO_GUIDE: dict[str, dict[str, Any]] = {
    "long-form": {
        "runs": 3,
        "utterances": 1,
        "action": (
            "Hold the hotkey and dictate continuously for at least three "
            "minutes without releasing it. Read anything neutral aloud — a "
            "product changelog, a licence, a recipe."),
        "watch": "memory growth, fan noise, and whether the machine gets hot",
    },
    "back-to-back": {
        "runs": 3,
        "utterances": 5,
        "action": (
            "Dictate five short utterances one after another with no pause "
            "between them: release the hotkey and press it again immediately."),
        "watch": "whether any utterance is swallowed by the previous tail",
    },
    "process-restart": {
        "runs": 3,
        "utterances": 2,
        "action": (
            "Dictate once. Restart the dictation service "
            "(`launchctl kickstart -k gui/$UID/com.berg.dictate`). Wait for it "
            "to come back, then dictate once more."),
        "watch": "whether the restart strands a lock, a semaphore, or a stream",
    },
    "sleep-wake": {
        "runs": 3,
        "utterances": 2,
        "action": (
            "Dictate once. Put the Mac to sleep and let it settle for at "
            "least sixty seconds. Wake it, unlock, then dictate once more "
            "without touching the app."),
        "watch": "whether the first post-wake press captures audio at all",
    },
    "audio-device-switch": {
        "runs": 4,
        "utterances": 2,
        "action": (
            "Dictate once. Change the default input device — plug in or "
            "unplug wired headphones, or connect or disconnect AirPods — and "
            "wait for macOS to switch. Then dictate once more."),
        "watch": "whether the stream re-opens on the new default input",
    },
}

SURVIVAL = (
    Choice("1", "all-utterances-produced-text",
           "every utterance you spoke produced text"),
    Choice("2", "some-utterances-missing",
           "at least one utterance produced no text"),
    Choice("3", "no-utterances-produced-text",
           "nothing produced text after the action"),
)

RECOVERY = (
    Choice("1", "recovered-without-intervention",
           "the runtime worked again on the next press, untouched"),
    Choice("2", "recovered-after-retry",
           "it worked after you pressed the hotkey again"),
    Choice("3", "recovered-after-manual-restart",
           "you had to restart the service yourself"),
    Choice("4", "did-not-recover",
           "it never worked again during this run"),
)

MACHINE_BEHAVIOR = (
    Choice("1", "normal", "nothing unusual"),
    Choice("2", "fans-audible", "fans became audible and stayed on"),
    Choice("3", "chassis-hot", "the chassis became noticeably hot"),
    Choice("4", "system-slowed", "the whole system became sluggish"),
    Choice("5", "process-terminated", "the dictation process died"),
)

BLOCKED_REASONS = frozenset({
    "hardware-unavailable",
    "operator-skipped",
    "no-runtime-record",
})


def build_plan() -> tuple[dict[str, Any], ...]:
    runs: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        guide = SCENARIO_GUIDE[scenario]
        for index in range(int(guide["runs"])):
            runs.append({
                "id": f"{scenario}-{index + 1}",
                "scenario": scenario,
                "expected_utterances": int(guide["utterances"]),
            })
    return tuple(runs)


def plan_digest(runs: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        [[run["id"], run["scenario"], run["expected_utterances"]]
         for run in runs],
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def count_capture_ready(path: Path) -> int:
    """Count the runtime's own text-free stream-ready lines."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 0
    except OSError as error:
        raise CaptureError(f"cannot read {path}: {error}") from error
    return sum(1 for line in text.splitlines()
               if CAPTURE_READY_MARKER in line)


# ------------------------------ the session -------------------------------


def _group_progress(runs: Sequence[Mapping[str, Any]],
                    session: Session) -> dict[str, tuple[int, int]]:
    planned = Counter(str(run["scenario"]) for run in runs)
    recorded = Counter(
        str(session.records[key]["scenario"]) for key in session.records)
    return {name: (recorded[name], planned[name]) for name in planned}


def run_session(runs: Sequence[Mapping[str, Any]], session: Session, *,
                transcripts: Path, runtime_log: Path,
                reader: TextIO, writer: TextIO) -> int:
    writer.write(
        "\nPHYSICAL LIFECYCLE AND STRESS SESSION\n"
        f"session file: {session.path}\n"
        f"runtime record: {transcripts}\n"
        f"runtime log:    {runtime_log}\n"
        "Dictate neutral content only. No dictated words reach the artifact.\n")
    if not transcripts.exists():
        writer.write(
            f"\n{transcripts} does not exist yet. Start Whisper Face and "
            "dictate once before running this session.\n")
        return 2

    remaining = [run for run in runs if not session.answered(str(run["id"]))]
    if not remaining:
        writer.write("\nEvery planned run is already answered.\n")
        return 0

    try:
        for run in remaining:
            run_id = str(run["id"])
            scenario = str(run["scenario"])
            guide = SCENARIO_GUIDE[scenario]
            writer.write(
                "\n" + "-" * 68 + "\n"
                + progress_line(len(session.records), len(runs),
                                _group_progress(runs, session)) + "\n"
                f"NEXT: {run_id}\n"
                f"  do: {guide['action']}\n"
                f"  watch: {guide['watch']}\n"
                f"  the runtime should log {run['expected_utterances']} "
                "utterance(s)\n")
            ready = ask_choice(
                "Can you perform that physical action now?",
                (Choice("1", "ready", "yes"),
                 Choice("2", "hardware-unavailable",
                        "the hardware for it is not available"),
                 Choice("3", "operator-skipped", "skip this run")),
                reader=reader, writer=writer)
            if ready != "ready":
                session.block(run_id, ready, {"scenario": scenario})
                continue

            seen_ids, baseline = transcript_baseline(transcripts)
            ready_baseline = count_capture_ready(runtime_log)
            wait_for_enter(
                "Perform the run now, then press Return here.",
                reader=reader, writer=writer)
            fresh = new_transcript_receipts(transcripts, seen_ids, baseline)
            capture_ready_events = max(
                0, count_capture_ready(runtime_log) - ready_baseline)
            if not fresh:
                writer.write(
                    "  The runtime logged no utterance for this run.\n")
                session.block(run_id, "no-runtime-record", {
                    "scenario": scenario,
                    "capture_ready_events": capture_ready_events,
                })
                continue

            states = Counter(
                str(item.insertion_state) for item in fresh
                if item.insertion_state is not None)
            outboxed = sum(1 for item in fresh if item.route_outbox is True)
            latencies = [item.insertion_ms for item in fresh
                         if item.insertion_ms is not None]
            writer.write(
                f"  Runtime logged {len(fresh)} utterance(s); states: "
                + (", ".join(f"{key} {value}"
                             for key, value in sorted(states.items()))
                   or "none")
                + f"; outbox diversions: {outboxed}; "
                f"capture-ready events: {capture_ready_events}\n")

            survival = ask_choice(
                "Did every utterance you spoke produce text?", SURVIVAL,
                reader=reader, writer=writer)
            recovery = ask_choice(
                "How did the runtime come back after the action?", RECOVERY,
                reader=reader, writer=writer)
            machine = ask_choice(
                "How did the machine itself behave during this run?",
                MACHINE_BEHAVIOR, reader=reader, writer=writer)
            session.record(run_id, {
                "scenario": scenario,
                "expected_utterances": run["expected_utterances"],
                "recorded_utc": utc_now(),
                "runtime": {
                    "source": "transcripts-jsonl+dictate-log",
                    "utterances_logged": len(fresh),
                    "insertion_states": dict(sorted(states.items())),
                    "outbox_diversions": outboxed,
                    "capture_ready_events": capture_ready_events,
                    "insertion_ms_max": max(latencies) if latencies else None,
                },
                "operator": {
                    "utterance_survival": survival,
                    "recovery": recovery,
                    "machine_behavior": machine,
                },
            })
    except SessionAborted as stop:
        writer.write(f"\nSession paused ({stop}). Re-run to resume.\n")

    writer.write(
        "\n" + progress_line(len(session.records), len(runs),
                             _group_progress(runs, session)) + "\n"
        f"blocked: {len(session.blocked)}\n"
        f"session saved to {session.path}\n")
    return 0


# ------------------------------- artifacts --------------------------------


def build_artifact(runs: Sequence[Mapping[str, Any]],
                   session_payload: Mapping[str, Any]) -> dict[str, Any]:
    records = list(session_payload.get("records") or ())
    blocked = list(session_payload.get("blocked") or ())
    planned = Counter(str(run["scenario"]) for run in runs)
    recorded = Counter(str(item["scenario"]) for item in records)
    blocked_by_scenario = Counter(str(item["scenario"]) for item in blocked)

    scenarios = {}
    for scenario in SCENARIOS:
        subset = [item for item in records if item["scenario"] == scenario]
        scenarios[scenario] = {
            "runs_planned": planned[scenario],
            "runs_recorded": recorded[scenario],
            "runs_blocked": blocked_by_scenario[scenario],
            "utterances_logged": sum(
                int(item["runtime"]["utterances_logged"]) for item in subset),
            "utterances_expected": sum(
                int(item["expected_utterances"]) for item in subset),
            "outbox_diversions": sum(
                int(item["runtime"]["outbox_diversions"]) for item in subset),
            "capture_ready_events": sum(
                int(item["runtime"]["capture_ready_events"])
                for item in subset),
            "utterance_survival": dict(sorted(Counter(
                str(item["operator"]["utterance_survival"])
                for item in subset).items())),
            "recovery": dict(sorted(Counter(
                str(item["operator"]["recovery"]) for item in subset).items())),
            "machine_behavior": dict(sorted(Counter(
                str(item["operator"]["machine_behavior"])
                for item in subset).items())),
        }

    discharged = sorted(
        validation_id for validation_id, scenario
        in PHYSICAL_VALIDATION_IDS.items() if recorded[scenario])
    still_required = sorted(
        set(PHYSICAL_VALIDATION_IDS) - set(discharged))
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact": "physical-lifecycle-evidence",
        "privacy": "transcript-free",
        "evidence_scope": "operator-attested-physical-session",
        "physical_evidence": bool(records),
        "generated_utc": utc_now(),
        "plan_digest": session_payload.get("plan_digest"),
        "scenario_vocabulary": "performance_lab.run_lifecycle_simulation",
        "coverage": {
            "runs_planned": len(runs),
            "runs_recorded": len(records),
            "runs_blocked": len(blocked),
            "runs_not_attempted": len(runs) - len(records) - len(blocked),
            "extrapolated": False,
        },
        "scenarios": scenarios,
        "discharges_physical_validation": discharged,
        "still_requires_physical_validation": still_required,
        "blocked_reasons": dict(sorted(Counter(
            str(item["reason"]) for item in blocked).items())),
    }


def render_summary(artifact: Mapping[str, Any]) -> str:
    coverage = artifact["coverage"]
    lines = [
        "PHYSICAL LIFECYCLE AND STRESS EVIDENCE",
        f"runs recorded: {coverage['runs_recorded']}/"
        f"{coverage['runs_planned']} · blocked: {coverage['runs_blocked']} · "
        f"not attempted: {coverage['runs_not_attempted']}",
    ]
    for scenario in SCENARIOS:
        entry = artifact["scenarios"][scenario]
        lines.append(
            f"  {scenario:<20} {entry['runs_recorded']}/"
            f"{entry['runs_planned']} runs · "
            f"{entry['utterances_logged']}/{entry['utterances_expected']} "
            f"utterances logged · outbox {entry['outbox_diversions']} · "
            f"capture-ready {entry['capture_ready_events']}")
        for label in ("utterance_survival", "recovery", "machine_behavior"):
            if entry[label]:
                lines.append("      " + label + ": " + ", ".join(
                    f"{key} {value}" for key, value in entry[label].items()))
    lines.append("discharges: " + (", ".join(
        artifact["discharges_physical_validation"]) or "nothing yet"))
    lines.append("still required: " + (", ".join(
        artifact["still_requires_physical_validation"]) or "nothing"))
    if artifact["blocked_reasons"]:
        lines.append("blocked: " + ", ".join(
            f"{key} {value}"
            for key, value in artifact["blocked_reasons"].items()))
    return "\n".join(lines)


def render_plan(runs: Sequence[Mapping[str, Any]]) -> str:
    counts = Counter(str(run["scenario"]) for run in runs)
    lines = [
        "PHYSICAL LIFECYCLE AND STRESS SESSION — PLAN",
        f"runs: {len(runs)} · plan digest: {plan_digest(runs)}",
        "estimated operator time: about 80 minutes, resumable at any point",
        "",
    ]
    for scenario in SCENARIOS:
        guide = SCENARIO_GUIDE[scenario]
        lines.append(f"{scenario} · {counts[scenario]} runs · "
                     f"{guide['utterances']} utterance(s) each")
        lines.append(f"  do:    {guide['action']}")
        lines.append(f"  watch: {guide['watch']}")
    lines.append("")
    lines.append("discharges these performance_lab ids when recorded:")
    for validation_id, scenario in sorted(PHYSICAL_VALIDATION_IDS.items()):
        lines.append(f"  {validation_id} <- {scenario}")
    return "\n".join(lines)


# --------------------------------- cli ------------------------------------


def _load_session_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CaptureError(f"cannot read session {path}: {error}") from error
    if not isinstance(payload, Mapping) or payload.get("tool") != TOOL:
        raise CaptureError(f"{path} is not a {TOOL} session")
    return dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture_lifecycle_evidence.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION,
                        help=f"private session file (default: {DEFAULT_SESSION})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="print the run plan and exit")
    run = commands.add_parser("run", help="run or resume the guided session")
    run.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    run.add_argument("--runtime-log", type=Path, default=DEFAULT_RUNTIME_LOG)
    emit = commands.add_parser(
        "emit", help="build the lifecycle artifact from a recorded session")
    emit.add_argument("--out", type=Path, required=True)
    commands.add_parser("summary", help="print the session summary")
    return parser


def main(argv: Sequence[str] | None = None, *,
         reader: TextIO | None = None, writer: TextIO | None = None) -> int:
    reader = reader or sys.stdin
    writer = writer or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        runs = build_plan()
        if args.command == "plan":
            writer.write(render_plan(runs) + "\n")
            return 0
        if args.command == "run":
            session = Session.load(
                args.session, TOOL, plan_digest=plan_digest(runs),
                blocked_reasons=BLOCKED_REASONS)
            return run_session(runs, session, transcripts=args.transcripts,
                               runtime_log=args.runtime_log,
                               reader=reader, writer=writer)
        payload = _load_session_payload(args.session)
        artifact = build_artifact(runs, payload)
        if args.command == "emit":
            atomic_write_json(args.out, artifact)
            writer.write(render_summary(artifact) + "\n")
            writer.write(f"\nwrote {args.out}\n")
            return 0
        writer.write(render_summary(artifact) + "\n")
        return 0
    except CaptureError as error:
        writer.write(f"capture_lifecycle_evidence: {error}\n")
        return 2
    except SessionAborted as stop:
        writer.write(f"capture_lifecycle_evidence: {stop}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
