# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""The lifecycle session must report only what a machine or a human said."""

import os
import ast
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lifecycle_evidence as lifecycle  # noqa: E402
import capture_session_support as support  # noqa: E402


POISON_RAW = "MY-PRIVATE-DICTATED-WORDS"
POISON_CLEAN = "MY-PRIVATE-CLEANED-WORDS"
POISON_TONE = "my-secret-tone-name"


def transcript_line(event_id, *, state="verified", reason="commit_verified",
                    route="fast"):
    return json.dumps({
        "ts": 1_800_000_000.0,
        "app": "com.apple.TextEdit",
        "raw": POISON_RAW,
        "clean": POISON_CLEAN,
        "observed_text": POISON_CLEAN,
        "path": route,
        "id": event_id,
        "metrics": {
            "insertion_state": state,
            "insertion_reason": reason,
            "paste_attempted": True,
            "insertion_verified": state == "verified",
            "delayed_cleanup_scheduled": False,
            "insertion_s": 0.0202,
        },
    })


class ScriptedReader:
    def __init__(self, answers):
        self._answers = list(answers)

    def readline(self):
        if not self._answers:
            return ""
        answer = self._answers.pop(0)
        if callable(answer):
            answer = answer()
        return answer


def appender(path, *lines):
    def _append():
        with path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        return "\n"
    return _append


class VocabularyTests(unittest.TestCase):
    """The physical session must speak the simulation's own scenario names."""

    def setUp(self):
        self.simulation = (ROOT / "performance_lab.py").read_text(
            encoding="utf-8")

    def test_scenario_names_match_the_lifecycle_simulation(self):
        for scenario in lifecycle.SCENARIOS:
            self.assertIn(f'"{scenario}"', self.simulation, scenario)
        self.assertEqual(len(set(lifecycle.SCENARIOS)), 5)

    def test_the_discharged_ids_are_the_simulation_s_own_requirements(self):
        for validation_id in lifecycle.PHYSICAL_VALIDATION_IDS:
            self.assertIn(f'"{validation_id}"', self.simulation, validation_id)
        self.assertEqual(set(lifecycle.PHYSICAL_VALIDATION_IDS), {
            "physical-audio-device-switch",
            "physical-long-audio-memory-thermal",
            "physical-operating-system-sleep-wake",
        })
        self.assertLessEqual(
            set(lifecycle.PHYSICAL_VALIDATION_IDS.values()),
            set(lifecycle.SCENARIOS))

    def test_the_capture_ready_marker_is_one_the_runtime_prints(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn(lifecycle.CAPTURE_READY_MARKER, source)

    def test_every_scenario_has_a_guide_and_a_run_count(self):
        for scenario in lifecycle.SCENARIOS:
            guide = lifecycle.SCENARIO_GUIDE[scenario]
            self.assertGreaterEqual(guide["runs"], 3)
            self.assertGreaterEqual(guide["utterances"], 1)
            self.assertTrue(guide["action"].strip())
            self.assertTrue(guide["watch"].strip())

    def test_the_plan_covers_every_scenario_with_unique_ids(self):
        runs = lifecycle.build_plan()
        self.assertEqual(len(runs), 16)
        self.assertEqual(len({run["id"] for run in runs}), len(runs))
        self.assertEqual(
            {run["scenario"] for run in runs}, set(lifecycle.SCENARIOS))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.transcripts = self.dir / "transcripts.jsonl"
        self.transcripts.write_text("", encoding="utf-8")
        self.log = self.dir / "dictate.log"
        self.log.write_text("", encoding="utf-8")
        self.session_path = self.dir / "session.json"
        self.runs = lifecycle.build_plan()

    def session(self):
        return support.Session.load(
            self.session_path, lifecycle.TOOL,
            plan_digest=lifecycle.plan_digest(self.runs),
            blocked_reasons=lifecycle.BLOCKED_REASONS)

    def drive(self, answers):
        writer = io.StringIO()
        code = lifecycle.run_session(
            self.runs, self.session(), transcripts=self.transcripts,
            runtime_log=self.log, reader=ScriptedReader(answers),
            writer=writer)
        return code, writer.getvalue()

    def payload(self):
        return json.loads(self.session_path.read_text(encoding="utf-8"))

    def append_run(self, *lines, capture_ready=0):
        def _append():
            with self.transcripts.open("a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line + "\n")
            with self.log.open("a", encoding="utf-8") as handle:
                for _ in range(capture_ready):
                    handle.write(
                        f"{lifecycle.CAPTURE_READY_MARKER} in 0.42s\n")
            return "\n"
        return _append

    def test_a_run_records_the_runtime_counts_and_operator_answers(self):
        code, output = self.drive([
            "1\n",
            self.append_run(
                transcript_line("evt-1"),
                transcript_line("evt-2", state="unverifiable",
                                reason="readback_unavailable",
                                route="outbox/fast"),
                capture_ready=2),
            "1\n", "1\n", "2\n", "q\n"])
        self.assertEqual(code, 0)
        record = self.payload()["records"][0]
        self.assertEqual(record["scenario"], "long-form")
        self.assertEqual(record["runtime"]["utterances_logged"], 2)
        self.assertEqual(record["runtime"]["insertion_states"],
                         {"unverifiable": 1, "verified": 1})
        self.assertEqual(record["runtime"]["outbox_diversions"], 1)
        self.assertEqual(record["runtime"]["capture_ready_events"], 2)
        self.assertEqual(record["operator"], {
            "utterance_survival": "all-utterances-produced-text",
            "recovery": "recovered-without-intervention",
            "machine_behavior": "fans-audible"})
        self.assertIn("capture-ready events: 2", output)

    def test_a_mismatched_utterance_count_is_reported_not_corrected(self):
        self.drive([
            "1\n", self.append_run(transcript_line("evt-1")),
            "2\n", "2\n", "1\n", "q\n"])
        record = self.payload()["records"][0]
        self.assertEqual(record["expected_utterances"], 1)
        self.assertEqual(record["runtime"]["utterances_logged"], 1)
        self.assertEqual(
            record["operator"]["utterance_survival"],
            "some-utterances-missing")

    def test_no_runtime_record_blocks_instead_of_scoring(self):
        self.drive(["1\n", "\n", "q\n"])
        self.assertEqual(self.payload()["records"], [])
        self.assertEqual(
            self.payload()["blocked"][0]["reason"], "no-runtime-record")

    def test_missing_hardware_is_recorded_as_blocked(self):
        self.drive(["2\n", "q\n"])
        self.assertEqual(
            self.payload()["blocked"][0]["reason"], "hardware-unavailable")

    def test_resume_skips_answered_runs_and_never_overwrites(self):
        self.drive(["3\n", "q\n"])
        first = self.payload()
        _, output = self.drive(["q\n"])
        self.assertNotIn("NEXT: long-form-1\n", output)
        self.assertIn("NEXT: long-form-2\n", output)
        self.assertEqual(first["blocked"], self.payload()["blocked"])
        with self.assertRaises(support.CaptureError):
            self.session().block("long-form-1", "operator-skipped")

    def test_a_blocked_reason_outside_the_closed_set_is_refused(self):
        session = self.session()
        with self.assertRaises(support.CaptureError):
            session.block("long-form-2", "machine-was-noisy")
        self.assertEqual(session.blocked, {})

    def test_the_session_file_is_owner_only(self):
        self.drive(["3\n", "q\n"])
        self.assertEqual(
            stat.S_IMODE(self.session_path.stat().st_mode), 0o600)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.runs = lifecycle.build_plan()

    def session_with(self, *scenarios):
        return {
            "plan_digest": lifecycle.plan_digest(self.runs),
            "records": [
                {
                    "case_id": f"{scenario}-1",
                    "scenario": scenario,
                    "expected_utterances": 2,
                    "recorded_utc": "2026-07-26T00:00:00+00:00",
                    "runtime": {
                        "source": "transcripts-jsonl+dictate-log",
                        "utterances_logged": 2,
                        "insertion_states": {"verified": 2},
                        "outbox_diversions": 0,
                        "capture_ready_events": 1,
                        "insertion_ms_max": 20.2,
                    },
                    "operator": {
                        "utterance_survival": "all-utterances-produced-text",
                        "recovery": "recovered-without-intervention",
                        "machine_behavior": "normal",
                    },
                }
                for scenario in scenarios
            ],
            "blocked": [],
        }

    def test_only_recorded_scenarios_discharge_a_requirement(self):
        artifact = lifecycle.build_artifact(
            self.runs, self.session_with("sleep-wake"))
        self.assertEqual(
            artifact["discharges_physical_validation"],
            ["physical-operating-system-sleep-wake"])
        self.assertEqual(
            artifact["still_requires_physical_validation"],
            ["physical-audio-device-switch",
             "physical-long-audio-memory-thermal"])

    def test_an_empty_session_discharges_nothing(self):
        artifact = lifecycle.build_artifact(
            self.runs, {"records": [], "blocked": []})
        self.assertEqual(artifact["discharges_physical_validation"], [])
        self.assertEqual(
            len(artifact["still_requires_physical_validation"]), 3)
        self.assertIs(artifact["physical_evidence"], False)
        for scenario in lifecycle.SCENARIOS:
            self.assertEqual(
                artifact["scenarios"][scenario]["runs_recorded"], 0)

    def test_coverage_never_credits_unattempted_runs(self):
        artifact = lifecycle.build_artifact(
            self.runs, self.session_with("long-form", "back-to-back"))
        coverage = artifact["coverage"]
        self.assertEqual(coverage["runs_planned"], 16)
        self.assertEqual(coverage["runs_recorded"], 2)
        self.assertEqual(coverage["runs_not_attempted"], 14)
        self.assertIs(coverage["extrapolated"], False)
        self.assertEqual(
            artifact["scenarios"]["long-form"]["runs_recorded"], 1)
        self.assertEqual(
            artifact["scenarios"]["sleep-wake"]["runs_recorded"], 0)

    def test_the_artifact_carries_no_dictated_text(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            transcripts = directory / "transcripts.jsonl"
            transcripts.write_text("", encoding="utf-8")
            log = directory / "dictate.log"
            log.write_text("", encoding="utf-8")
            session_path = directory / "session.json"
            session = support.Session.load(
                session_path, lifecycle.TOOL,
                plan_digest=lifecycle.plan_digest(self.runs))

            def append():
                with transcripts.open("a", encoding="utf-8") as handle:
                    handle.write(
                        transcript_line(
                            "evt-1", route=f"llm/{POISON_TONE}") + "\n")
                return "\n"

            lifecycle.run_session(
                self.runs, session, transcripts=transcripts, runtime_log=log,
                reader=ScriptedReader(["1\n", append, "1\n", "1\n", "1\n",
                                       "q\n"]),
                writer=io.StringIO())
            payload = json.loads(session_path.read_text(encoding="utf-8"))
        artifact = lifecycle.build_artifact(self.runs, payload)
        blob = json.dumps(artifact)
        for poison in (POISON_RAW, POISON_CLEAN, POISON_TONE):
            self.assertNotIn(poison, blob)
        for key in support.TEXT_BEARING_TRANSCRIPT_KEYS:
            self.assertNotIn(f'"{key}"', blob)

    def test_summary_names_what_is_still_required(self):
        artifact = lifecycle.build_artifact(
            self.runs, self.session_with("audio-device-switch"))
        summary = lifecycle.render_summary(artifact)
        self.assertIn("runs recorded: 1/16", summary)
        self.assertIn("discharges: physical-audio-device-switch", summary)
        self.assertIn("physical-long-audio-memory-thermal", summary)


class NoReceiptWritingTests(unittest.TestCase):
    MODULES = (
        ROOT / "scripts" / "capture_lifecycle_evidence.py",
        ROOT / "scripts" / "capture_session_support.py",
    )
    FORBIDDEN_IMPORTS = {
        "subprocess", "runpy", "pty", "delayed_cleanup_activation",
        "acoustic_calibration_activation", "acoustic_keyword_activation",
        "relisten_activation",
    }
    FORBIDDEN_CALLS = {
        "write_activation_receipt", "evaluate_activation",
        "validate_activation_receipt", "eval", "exec", "system", "popen",
        "execv", "execvp", "spawnl", "spawnv", "fork", "check_call",
        "check_output",
    }
    RECEIPT_WORDS = ("manual_reviewed", "activation_receipt", "write_receipt")

    def trees(self):
        for path in self.MODULES:
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_no_process_execution_or_activation_module_is_imported(self):
        for path, tree in self.trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0], self.FORBIDDEN_IMPORTS,
                            path)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        (node.module or "").split(".")[0],
                        self.FORBIDDEN_IMPORTS, path)

    def test_no_receipt_writing_or_process_spawning_call_exists(self):
        for path, tree in self.trees():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = (function.id if isinstance(function, ast.Name)
                        else function.attr if isinstance(function, ast.Attribute)
                        else None)
                self.assertNotIn(name, self.FORBIDDEN_CALLS, f"{path}: {name}")

    def test_no_manual_review_identifier_or_flag_appears(self):
        source = self.MODULES[0].read_text(encoding="utf-8")
        self.assertNotIn("--manual-reviewed", source)
        for path, tree in self.trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, self.RECEIPT_WORDS, path)
                elif isinstance(node, ast.keyword):
                    self.assertNotIn(node.arg, self.RECEIPT_WORDS, path)
                elif isinstance(node, ast.arg):
                    self.assertNotIn(node.arg, self.RECEIPT_WORDS, path)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotIn(node.name, self.RECEIPT_WORDS, path)

    def test_the_tool_never_reads_a_text_bearing_transcript_key(self):
        source = self.MODULES[0].read_text(encoding="utf-8")
        for key in support.TEXT_BEARING_TRANSCRIPT_KEYS:
            self.assertNotIn(f'"{key}"', source, key)
            self.assertNotIn(f"'{key}'", source, key)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, argv, answers=()):
        writer = io.StringIO()
        code = lifecycle.main(
            argv, reader=ScriptedReader(answers), writer=writer)
        return code, writer.getvalue()

    def test_plan_needs_no_hardware(self):
        code, output = self.run_cli(["plan"])
        self.assertEqual(code, 0)
        self.assertIn("runs: 16", output)
        self.assertIn("physical-audio-device-switch <- audio-device-switch",
                      output)

    def test_run_refuses_without_a_runtime_record(self):
        with tempfile.TemporaryDirectory() as directory:
            code, output = self.run_cli([
                "--session", str(Path(directory) / "s.json"), "run",
                "--transcripts", str(Path(directory) / "missing.jsonl"),
                "--runtime-log", str(Path(directory) / "missing.log")])
        self.assertEqual(code, 2)
        self.assertIn("does not exist yet", output)

    def test_emit_writes_an_owner_only_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / "session.json"
            support.Session.load(
                session_path, lifecycle.TOOL,
                plan_digest=lifecycle.plan_digest(
                    lifecycle.build_plan())).save()
            target = Path(directory) / "artifact.json"
            code, output = self.run_cli(
                ["--session", str(session_path), "emit", "--out", str(target)])
            self.assertEqual(code, 0)
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(target.stat().st_mode), 0o600)
            artifact = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(artifact["coverage"]["runs_recorded"], 0)
        self.assertIn("still required:", output)


if __name__ == "__main__":
    unittest.main()
