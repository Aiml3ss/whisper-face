#!/usr/bin/env python3
"""Guided, resumable capture of the 50-case physical delayed-cleanup suite.

Ledger row 25 is fail-closed: `delayed_cleanup_activation.py` will not install
an activation receipt without at least 50 manually reviewed caller-attested
physical cases, ten per editor surface, eight per drift/duplicate scenario, at
least fifteen applied and fifteen rejected, zero safety failures, and p95 apply
latency within 150 ms. No such suite has been run, so the feature is off.

This tool walks that grid case by case. It reads what the runtime itself
printed — `dictate.py` logs one text-free `[delayed-cleanup] <outcome>; <n>
applied, <m> held` line per delayed pass to `dictate.log` — and asks the
operator only for the closed safety observations a human has to make: did a
write land somewhere it should not have, did it overwrite an edit the operator
had just made, did the selection jump.

Three things this tool deliberately does not do:

* It never writes an activation receipt and never passes `--manual-reviewed`.
  It prints the exact command for the operator to run, and stops there.
* It never decides an outcome. A case with no runtime line, an ambiguous set of
  lines, or no runtime-reported apply latency is recorded as blocked with a
  closed reason.
* It never invents `apply_ms`. The runtime does not currently report a
  delayed-apply duration anywhere an external tool can read (see
  `--timing-source`), so today every case blocks on `no-runtime-timing` and the
  session prints exactly what the runtime has to emit for the gate to be
  satisfiable at all.

    uv run scripts/capture_delayed_cleanup_cases.py plan
    uv run scripts/capture_delayed_cleanup_cases.py run
    uv run scripts/capture_delayed_cleanup_cases.py emit --out cases.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_session_support import (  # noqa: E402
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_RUNTIME_LOG,
    CaptureError,
    Choice,
    Session,
    SessionAborted,
    ask_choice,
    atomic_write_json,
    progress_line,
    utc_now,
    wait_for_enter,
)

# The gate is the single source of truth for the vocabulary and the thresholds.
# Importing it (rather than restating it) means a threshold change cannot drift
# away from the session that produces the evidence.
from delayed_cleanup_activation import (  # noqa: E402
    MAX_P95_APPLY_MS,
    MIN_CASES,
    MIN_SCENARIO_CASES,
    MIN_SURFACE_CASES,
    OUTCOMES,
    PHYSICAL_SOURCE,
    SCENARIOS,
    SURFACES,
)


TOOL = "capture_delayed_cleanup_cases"
ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_SESSION = DEFAULT_EVIDENCE_DIR / "delayed-cleanup-session.json"
DEFAULT_RECORDS_OUT = "delayed_cleanup_physical_cases.json"
ACTIVATION_COMMAND = (
    "uv run delayed_cleanup_activation.py {records} "
    "--manual-reviewed --write-receipt delayed_cleanup_activation.json")

# `dictate.py` prints exactly this, text-free, once per delayed pass.
RUNTIME_LINE = re.compile(
    r"\[delayed-cleanup\]\s+(?P<outcome>[a-z_]+);\s+"
    r"(?P<applied>\d+)\s+applied,\s+(?P<held>\d+)\s+held"
    r"(?:;\s*(?P<apply_ms>\d+(?:\.\d+)?)\s*ms)?")

SURFACE_ORDER = ("native-text", "web-text", "electron-editor",
                 "terminal-editor")
SCENARIO_ORDER = ("unchanged", "edit-elsewhere", "edit-overlap", "focus-drift",
                  "duplicate-callback")

SURFACE_SETUP = {
    "native-text": "a stock Cocoa text view (TextEdit, Notes, or Stickies)",
    "web-text": "a browser text area or contenteditable block",
    "electron-editor": "an Electron editor (Obsidian, Slack, or VS Code)",
    "terminal-editor": "a terminal editor buffer in insert mode",
}

# Dictate a deliberately disfluent phrase so the Voice Compiler's delayed pass
# has something to propose. A phrase that needs no cleanup produces
# `proposal_failed` or `no_safe_changes` no matter how the destination behaves.
DICTATION_PHRASE = (
    "um so the prototype is like ready for review you know")

SCENARIO_ACTION = {
    "unchanged": (
        "Do nothing at all after the first text lands. Leave the caret alone "
        "until the delayed pass reports."),
    "edit-elsewhere": (
        "As soon as the first text lands, type one word at the far end of the "
        "same field, away from the dictated span, then stop."),
    "edit-overlap": (
        "As soon as the first text lands, edit inside the dictated span "
        "itself — retype or delete a word the runtime just inserted."),
    "focus-drift": (
        "As soon as the first text lands, click into a different field, "
        "window, or application and stay there."),
    "duplicate-callback": (
        "There is no operator action that forces this. `dictate.py` derives "
        "the proposal id from the per-utterance event id, so two delayed "
        "passes never share one id and the adapter's duplicate paths are "
        "unreachable from the shipped runtime. Attempt the case, record what "
        "the runtime actually does, and expect it to block."),
}

# What the suite predicts, derived from
# `DelayedCleanupTransactionAdapter._apply_once`: an edit that lands before the
# first snapshot is merged against, so an overlapping edit rejects every
# proposal edit and yields `no_safe_changes` rather than `text_drift`, while an
# edit away from the dictated span still merges.
SCENARIO_EXPECTATION = {
    "unchanged": "applied",
    "edit-elsewhere": "applied",
    "edit-overlap": "no_safe_changes",
    "focus-drift": "focus_drift",
    "duplicate-callback": "proposal_in_flight",
}

UNREACHABLE_SCENARIOS = {
    "duplicate-callback": (
        "proposal ids are unique per utterance, so no physical action reaches "
        "the adapter's in-flight or completed-duplicate paths"),
}

SAFETY_QUESTIONS = (
    ("wrong_target_write",
     "Did any text land in a field, window, or app other than the one you "
     "dictated into?"),
    ("user_edit_overwritten",
     "Did the delayed pass overwrite or discard an edit you made yourself?"),
    ("selection_disrupted",
     "Did your caret or selection move somewhere you did not put it?"),
    ("duplicate_write",
     "Did the same text get written more than once?"),
)

YES_NO = (
    Choice("y", "yes", "yes"),
    Choice("n", "no", "no"),
)

BLOCKED_REASONS = frozenset({
    "surface-unavailable",
    "operator-skipped",
    "no-runtime-line",
    "ambiguous-runtime-lines",
    "no-runtime-timing",
    "runtime-outcome-unknown",
    "delayed-cleanup-inactive",
})

TIMING_SOURCES = ("runtime-log", "none")


# ------------------------------- the plan ---------------------------------


def build_plan() -> tuple[dict[str, Any], ...]:
    """Build a 50-case grid that satisfies every coverage floor in the gate.

    Four surfaces by five scenarios is twenty cells. Two cases per cell covers
    the per-surface floor of ten and the per-scenario floor of eight; ten extra
    cases are spread so each scenario reaches ten and the surfaces land on
    13/12/13/12. Expected outcomes make the applied/rejected split 20/30, above
    the fifteen-each balance the gate requires in both directions.
    """
    cases: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(SCENARIO_ORDER):
        for surface_index, surface in enumerate(SURFACE_ORDER):
            repeats = 2 + int(
                (surface_index + scenario_index) % len(SURFACE_ORDER) < 2)
            for repeat in range(repeats):
                cases.append({
                    "id": f"{surface}-{scenario}-{repeat + 1}",
                    "surface": surface,
                    "scenario": scenario,
                    "expected_outcome": SCENARIO_EXPECTATION[scenario],
                })
    validate_plan(cases)
    return tuple(cases)


def validate_plan(cases: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a plan that cannot reach the gate even if every case passes."""
    if len(cases) < MIN_CASES:
        raise CaptureError(
            f"the plan has {len(cases)} cases; the gate needs {MIN_CASES}")
    ids = [str(case["id"]) for case in cases]
    if len(set(ids)) != len(ids):
        raise CaptureError("duplicate case identifier in the plan")
    surfaces = Counter(str(case["surface"]) for case in cases)
    scenarios = Counter(str(case["scenario"]) for case in cases)
    if set(surfaces) != set(SURFACES):
        raise CaptureError("the plan must cover exactly the gate's surfaces")
    if set(scenarios) != set(SCENARIOS):
        raise CaptureError("the plan must cover exactly the gate's scenarios")
    for surface, count in surfaces.items():
        if count < MIN_SURFACE_CASES:
            raise CaptureError(
                f"surface {surface} has {count} cases, needs "
                f"{MIN_SURFACE_CASES}")
    for scenario, count in scenarios.items():
        if count < MIN_SCENARIO_CASES:
            raise CaptureError(
                f"scenario {scenario} has {count} cases, needs "
                f"{MIN_SCENARIO_CASES}")
    expected_applied = sum(
        1 for case in cases if case["expected_outcome"] == "applied")
    if expected_applied < 15 or len(cases) - expected_applied < 15:
        raise CaptureError(
            "the plan must expect at least 15 applied and 15 rejected cases")
    unknown = {str(case["expected_outcome"]) for case in cases} - set(OUTCOMES)
    if unknown:
        raise CaptureError(f"expected outcomes outside the gate: {sorted(unknown)}")


def plan_digest(cases: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        [[case["id"], case["surface"], case["scenario"],
          case["expected_outcome"]] for case in cases],
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


# --------------------------- runtime log reading ---------------------------


def read_runtime_lines(path: Path) -> list[dict[str, Any]]:
    """Parse the runtime's own text-free delayed-cleanup report lines."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    except OSError as error:
        raise CaptureError(f"cannot read {path}: {error}") from error
    found: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = RUNTIME_LINE.search(line)
        if match is None:
            continue
        found.append({
            "outcome": match.group("outcome"),
            "applied": int(match.group("applied")),
            "held": int(match.group("held")),
            "apply_ms": (float(match.group("apply_ms"))
                         if match.group("apply_ms") is not None else None),
        })
    return found


# ------------------------------ the session -------------------------------


def _group_progress(cases: Sequence[Mapping[str, Any]],
                    session: Session, key: str) -> dict[str, tuple[int, int]]:
    planned = Counter(str(case[key]) for case in cases)
    recorded = Counter(
        str(session.records[case_id][key]) for case_id in session.records)
    return {name: (recorded[name], planned[name]) for name in planned}


def run_session(cases: Sequence[Mapping[str, Any]], session: Session, *,
                runtime_log: Path, timing_source: str,
                reader: TextIO, writer: TextIO) -> int:
    writer.write(
        "\nPHYSICAL DELAYED-CLEANUP SUITE\n"
        f"session file: {session.path}\n"
        f"runtime log:  {runtime_log}\n"
        f"timing source: {timing_source}\n"
        f"dictate this phrase every time: \"{DICTATION_PHRASE}\"\n"
        "Every recorded value comes from the runtime's own report or from a\n"
        "closed answer you type. Nothing is defaulted.\n")

    if timing_source == "none":
        writer.write(
            "\nWARNING: with --timing-source none there is no honest source "
            "for apply_ms,\n so every case will be recorded as blocked. The "
            "gate cannot be satisfied\n until the runtime reports a "
            "delayed-apply duration.\n")
    for scenario, why in sorted(UNREACHABLE_SCENARIOS.items()):
        writer.write(f"\nNOTE: scenario {scenario} is unreachable: {why}.\n")

    remaining = [case for case in cases
                 if not session.answered(str(case["id"]))]
    if not remaining:
        writer.write("\nEvery planned case is already answered.\n")
        return 0

    try:
        for case in remaining:
            case_id = str(case["id"])
            writer.write(
                "\n" + "-" * 68 + "\n"
                + progress_line(
                    len(session.records), len(cases),
                    _group_progress(cases, session, "surface")) + "\n"
                + "scenarios: " + progress_line(
                    len(session.records), len(cases),
                    _group_progress(cases, session, "scenario")) + "\n"
                f"NEXT: {case_id}\n"
                f"  surface : {SURFACE_SETUP[str(case['surface'])]}\n"
                f"  scenario: {SCENARIO_ACTION[str(case['scenario'])]}\n"
                f"  the suite expects: {case['expected_outcome']}\n")
            ready = ask_choice(
                "Is that surface open and focused?",
                (Choice("1", "ready", "yes, ready to dictate"),
                 Choice("2", "surface-unavailable",
                        "that surface is not available on this Mac"),
                 Choice("3", "operator-skipped", "skip this case")),
                reader=reader, writer=writer)
            if ready != "ready":
                session.block(case_id, ready, _case_facets(case))
                continue

            baseline = len(read_runtime_lines(runtime_log))
            wait_for_enter(
                "Dictate, perform the scenario action above, wait for the "
                "delayed pass, then press Return.",
                reader=reader, writer=writer)
            lines = read_runtime_lines(runtime_log)[baseline:]
            if not lines:
                writer.write(
                    "  The runtime printed no delayed-cleanup line. If the "
                    "activation receipt is\n  absent the runtime never "
                    "schedules a delayed pass at all.\n")
                session.block(case_id, "no-runtime-line", _case_facets(case))
                continue
            if len(lines) != 1:
                writer.write(
                    f"  The runtime printed {len(lines)} delayed-cleanup "
                    "lines; a case needs exactly one.\n")
                session.block(case_id, "ambiguous-runtime-lines",
                              {**_case_facets(case),
                               "lines_observed": len(lines)})
                continue
            observed = lines[0]
            if observed["outcome"] not in OUTCOMES:
                writer.write(
                    f"  The runtime reported {observed['outcome']!r}, which is "
                    "not one of the gate's outcomes.\n")
                session.block(case_id, "runtime-outcome-unknown",
                              {**_case_facets(case),
                               "runtime_outcome": observed["outcome"]})
                continue

            apply_ms = observed["apply_ms"] if timing_source == "runtime-log" \
                else None
            if apply_ms is None:
                writer.write(
                    "  The runtime reported no apply duration for this pass.\n")
                session.block(case_id, "no-runtime-timing",
                              {**_case_facets(case),
                               "runtime_outcome": observed["outcome"]})
                continue

            writer.write(
                f"  Runtime reported: {observed['outcome']}; "
                f"{observed['applied']} applied, {observed['held']} held, "
                f"{apply_ms} ms\n")
            safety: dict[str, bool] = {}
            for field, question in SAFETY_QUESTIONS:
                safety[field] = ask_choice(
                    question, YES_NO, reader=reader, writer=writer) == "yes"
            session.record(case_id, {
                **_case_facets(case),
                "recorded_utc": utc_now(),
                "runtime": {
                    "source": "dictate-log-delayed-cleanup-line",
                    "outcome": observed["outcome"],
                    "merge_applied": observed["applied"],
                    "merge_held": observed["held"],
                    "apply_ms": apply_ms,
                },
                "operator": safety,
            })
    except SessionAborted as stop:
        writer.write(f"\nSession paused ({stop}). Re-run to resume.\n")

    writer.write("\n" + render_progress(cases, session) + "\n")
    return 0


def _case_facets(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "surface": case["surface"],
        "scenario": case["scenario"],
        "expected_outcome": case["expected_outcome"],
    }


def render_progress(cases: Sequence[Mapping[str, Any]],
                    session: Session) -> str:
    return "\n".join((
        "surfaces:  " + progress_line(
            len(session.records), len(cases),
            _group_progress(cases, session, "surface")),
        "scenarios: " + progress_line(
            len(session.records), len(cases),
            _group_progress(cases, session, "scenario")),
        f"blocked:   {len(session.blocked)}",
        f"session saved to {session.path}",
    ))


# ------------------------------- artifacts --------------------------------


def build_records(session_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the `{"records": [...]}` file `delayed_cleanup_activation` reads."""
    records = []
    for entry in session_payload.get("records") or ():
        runtime = entry["runtime"]
        operator = entry["operator"]
        records.append({
            "id": str(entry["case_id"]),
            "source": PHYSICAL_SOURCE,
            "surface": entry["surface"],
            "scenario": entry["scenario"],
            "expected_outcome": entry["expected_outcome"],
            "actual_outcome": runtime["outcome"],
            "wrong_target_write": bool(operator["wrong_target_write"]),
            "user_edit_overwritten": bool(operator["user_edit_overwritten"]),
            "selection_disrupted": bool(operator["selection_disrupted"]),
            "duplicate_write": bool(operator["duplicate_write"]),
            "apply_ms": float(runtime["apply_ms"]),
        })
    return {"records": records}


def coverage_report(cases: Sequence[Mapping[str, Any]],
                    session_payload: Mapping[str, Any]) -> dict[str, Any]:
    records = list(session_payload.get("records") or ())
    blocked = list(session_payload.get("blocked") or ())
    surfaces = Counter(str(item["surface"]) for item in records)
    scenarios = Counter(str(item["scenario"]) for item in records)
    applied = sum(
        1 for item in records if item["runtime"]["outcome"] == "applied")
    latencies = sorted(float(item["runtime"]["apply_ms"]) for item in records)
    shortfalls = []
    if len(records) < MIN_CASES:
        shortfalls.append(f"case-count-{len(records)}-of-{MIN_CASES}")
    for surface in sorted(SURFACES):
        if surfaces[surface] < MIN_SURFACE_CASES:
            shortfalls.append(
                f"surface-{surface}-{surfaces[surface]}-of-"
                f"{MIN_SURFACE_CASES}")
    for scenario in sorted(SCENARIOS):
        if scenarios[scenario] < MIN_SCENARIO_CASES:
            shortfalls.append(
                f"scenario-{scenario}-{scenarios[scenario]}-of-"
                f"{MIN_SCENARIO_CASES}")
    if applied < 15 or len(records) - applied < 15:
        shortfalls.append(
            f"apply-reject-balance-{applied}-applied-"
            f"{len(records) - applied}-rejected")
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact": "physical-delayed-cleanup-coverage",
        "privacy": "transcript-free",
        "evidence_scope": "operator-attested-physical-session",
        "generated_utc": utc_now(),
        "cases_planned": len(cases),
        "cases_recorded": len(records),
        "cases_blocked": len(blocked),
        "surface_counts": {key: surfaces[key] for key in sorted(SURFACES)},
        "scenario_counts": {key: scenarios[key] for key in sorted(SCENARIOS)},
        "applied_count": applied,
        "rejected_count": len(records) - applied,
        "observed_apply_ms_max": latencies[-1] if latencies else None,
        "max_p95_apply_ms_allowed": MAX_P95_APPLY_MS,
        "blocked_reasons": dict(sorted(Counter(
            str(item["reason"]) for item in blocked).items())),
        "gate_shortfalls": shortfalls,
        "known_unreachable_scenarios": dict(sorted(
            UNREACHABLE_SCENARIOS.items())),
        "receipt_written_by_this_tool": False,
        "manual_review_flag_set_by_this_tool": False,
    }


def render_summary(coverage: Mapping[str, Any], records_path: str) -> str:
    lines = [
        "PHYSICAL DELAYED-CLEANUP SUITE",
        f"cases recorded: {coverage['cases_recorded']}/"
        f"{coverage['cases_planned']} · blocked: {coverage['cases_blocked']}",
        "surfaces:  " + ", ".join(
            f"{key} {value}"
            for key, value in coverage["surface_counts"].items()),
        "scenarios: " + ", ".join(
            f"{key} {value}"
            for key, value in coverage["scenario_counts"].items()),
        f"applied {coverage['applied_count']} · "
        f"rejected {coverage['rejected_count']} · "
        f"p95 budget {coverage['max_p95_apply_ms_allowed']} ms",
    ]
    if coverage["blocked_reasons"]:
        lines.append("blocked: " + ", ".join(
            f"{key} {value}"
            for key, value in coverage["blocked_reasons"].items()))
    for scenario, why in coverage["known_unreachable_scenarios"].items():
        lines.append(f"unreachable scenario {scenario}: {why}")
    if coverage["gate_shortfalls"]:
        lines.append("gate shortfalls: " + ", ".join(
            coverage["gate_shortfalls"]))
        lines.append(
            "This suite is not yet sufficient. Do not run the activation "
            "command until the shortfalls above are gone.")
    else:
        lines.append("Coverage floors are met. Review every case yourself,")
        lines.append("then run exactly this command:")
        lines.append("")
        lines.append("  " + ACTIVATION_COMMAND.format(records=records_path))
        lines.append("")
        lines.append(
            "This tool does not run it, does not write the receipt, and does "
            "not set --manual-reviewed on your behalf.")
    return "\n".join(lines)


def render_plan(cases: Sequence[Mapping[str, Any]]) -> str:
    surfaces = Counter(str(case["surface"]) for case in cases)
    scenarios = Counter(str(case["scenario"]) for case in cases)
    expected = Counter(str(case["expected_outcome"]) for case in cases)
    lines = [
        "PHYSICAL DELAYED-CLEANUP SUITE — SESSION PLAN",
        f"cases: {len(cases)} · plan digest: {plan_digest(cases)}",
        "estimated operator time: about 3.5 minutes per case "
        f"(~{round(len(cases) * 3.5 / 60, 1)} h), resumable at any point",
        "",
        "gate floors: "
        f"{MIN_CASES} cases, {MIN_SURFACE_CASES} per surface, "
        f"{MIN_SCENARIO_CASES} per scenario, 15 applied and 15 rejected, "
        f"p95 apply <= {MAX_P95_APPLY_MS} ms",
        "",
        "surfaces:  " + ", ".join(
            f"{key} {surfaces[key]}" for key in SURFACE_ORDER),
        "scenarios: " + ", ".join(
            f"{key} {scenarios[key]}" for key in SCENARIO_ORDER),
        "expected:  " + ", ".join(
            f"{key} {value}" for key, value in sorted(expected.items())),
        "",
        f"dictate every case: \"{DICTATION_PHRASE}\"",
        "",
    ]
    for scenario in SCENARIO_ORDER:
        lines.append(f"{scenario}: {SCENARIO_ACTION[scenario]}")
    lines.append("")
    lines.append("preconditions the operator must check first:")
    lines.append(
        "  * delayed cleanup only schedules when the activation receipt is "
        "already valid,")
    lines.append(
        "    so an unreceipted runtime prints no line and every case blocks.")
    lines.append(
        "  * the runtime prints no apply duration, so --timing-source "
        "runtime-log finds")
    lines.append("    no apply_ms and every case blocks on no-runtime-timing.")
    for scenario, why in sorted(UNREACHABLE_SCENARIOS.items()):
        lines.append(f"  * scenario {scenario} is unreachable: {why}.")
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
        prog="capture_delayed_cleanup_cases.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION,
                        help=f"private session file (default: {DEFAULT_SESSION})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="print the 50-case plan and exit")
    run = commands.add_parser("run", help="run or resume the guided session")
    run.add_argument("--runtime-log", type=Path, default=DEFAULT_RUNTIME_LOG)
    run.add_argument("--timing-source", choices=TIMING_SOURCES,
                     default="runtime-log",
                     help="where apply_ms comes from; 'none' blocks every case")
    emit = commands.add_parser(
        "emit", help="write the records file the activation gate reads")
    emit.add_argument("--out", type=Path,
                      default=Path(DEFAULT_RECORDS_OUT))
    commands.add_parser("summary", help="print coverage against the gate")
    return parser


def main(argv: Sequence[str] | None = None, *,
         reader: TextIO | None = None, writer: TextIO | None = None) -> int:
    reader = reader or sys.stdin
    writer = writer or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        cases = build_plan()
        if args.command == "plan":
            writer.write(render_plan(cases) + "\n")
            return 0
        if args.command == "run":
            session = Session.load(
                args.session, TOOL, plan_digest=plan_digest(cases),
                blocked_reasons=BLOCKED_REASONS)
            return run_session(cases, session, runtime_log=args.runtime_log,
                               timing_source=args.timing_source,
                               reader=reader, writer=writer)
        payload = _load_session_payload(args.session)
        coverage = coverage_report(cases, payload)
        if args.command == "emit":
            atomic_write_json(args.out, build_records(payload))
            writer.write(render_summary(coverage, str(args.out)) + "\n")
            writer.write(f"\nwrote {args.out}\n")
            return 0
        writer.write(
            render_summary(coverage, DEFAULT_RECORDS_OUT) + "\n")
        return 0
    except CaptureError as error:
        writer.write(f"capture_delayed_cleanup_cases: {error}\n")
        return 2
    except SessionAborted as stop:
        writer.write(f"capture_delayed_cleanup_cases: {stop}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
