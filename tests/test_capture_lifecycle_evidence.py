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
                    route="fast", ts=1_800_000_000.0, press_s=None):
    metrics = {
        "insertion_state": state,
        "insertion_reason": reason,
        "paste_attempted": True,
        "insertion_verified": state == "verified",
        "delayed_cleanup_scheduled": False,
        "insertion_s": 0.0202,
    }
    if press_s is not None:
        metrics["press_s"] = press_s
    return json.dumps({
        "ts": ts,
        "app": "com.apple.TextEdit",
        "raw": POISON_RAW,
        "clean": POISON_CLEAN,
        "observed_text": POISON_CLEAN,
        "path": route,
        "id": event_id,
        "metrics": metrics,
    })


def trace_line(event):
    payload = {"event": event, "schema_version": 1}
    if event == support.RUNTIME_START_TRACE_EVENT:
        payload.update({"duration_ms": 1.0, "success": 1.0})
    else:
        payload.update({"release_ms": 1.0, "asr_ms": 1.0})
    return support.PERFORMANCE_TRACE_PREFIX + json.dumps(
        payload, sort_keys=True, separators=(",", ":"))


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


class PassiveSignalTests(unittest.TestCase):
    """Only a signal the runtime actually leaves may be claimed."""

    def test_the_five_scenarios_split_into_observable_and_blind(self):
        self.assertEqual(
            set(lifecycle.PASSIVE_OBSERVABLE_SCENARIOS)
            | set(lifecycle.PASSIVE_BLIND_SCENARIOS),
            set(lifecycle.SCENARIOS))
        self.assertEqual(
            set(lifecycle.PASSIVE_OBSERVABLE_SCENARIOS)
            & set(lifecycle.PASSIVE_BLIND_SCENARIOS), set())
        self.assertEqual(set(lifecycle.PASSIVE_BLIND_SCENARIOS),
                         {"sleep-wake", "audio-device-switch"})
        for entry in lifecycle.PASSIVE_BLIND_SCENARIOS.values():
            self.assertTrue(entry["reason"].strip())
            self.assertTrue(entry["detail"].strip())

    def test_the_recovery_path_really_is_silent(self):
        """The refusal above is only honest while these classes stay quiet.

        `sleep-wake` and `audio-device-switch` are declared unobservable
        because the wake notification and the CoreAudio default-input
        listener both run through handlers that print nothing. If that ever
        changes there *is* a signal, and this refusal must be revisited
        rather than left standing out of habit.
        """
        tree = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))
        silent = {"MacAudioRecoveryNotifications",
                  "_CoreAudioDefaultInputListener"}
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name not in silent:
                continue
            found.add(node.name)
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)):
                    self.assertNotEqual(inner.func.id, "print", node.name)
        self.assertEqual(found, silent)

    def test_the_trace_events_are_ones_the_runtime_emits(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        for event in (support.RUNTIME_START_TRACE_EVENT,
                      support.UTTERANCE_TRACE_EVENT):
            self.assertIn(f'"{event}"', source, event)
        self.assertIn(support.PERFORMANCE_TRACE_PREFIX, source)

    def test_long_form_needs_a_hold_of_at_least_the_threshold(self):
        just_under = lifecycle.LONG_FORM_MIN_PRESS_MS / 1000.0 - 1
        receipts = [
            support.project_transcript_record(json.loads(transcript_line(
                "evt-1", press_s=just_under))),
            support.project_transcript_record(json.loads(transcript_line(
                "evt-2", press_s=lifecycle.LONG_FORM_MIN_PRESS_MS / 1000.0))),
        ]
        signal = lifecycle.observe_long_form(receipts)
        self.assertEqual(signal["long_form_captures"], 1)
        self.assertEqual(signal["utterances_with_a_hold_duration"], 2)
        self.assertEqual(signal["insertion_states"], {"verified": 1})
        self.assertEqual(
            signal["unobservable_half"], "memory-growth-and-thermal-behaviour")

    def test_long_form_reports_nothing_when_no_hold_was_long(self):
        receipts = [support.project_transcript_record(json.loads(
            transcript_line("evt-1", press_s=4.2)))]
        signal = lifecycle.observe_long_form(receipts)
        self.assertEqual(signal["long_form_captures"], 0)
        self.assertEqual(signal["insertion_states"], {})

    def test_back_to_back_needs_a_run_of_utterances_without_a_pause(self):
        base = 1_800_000_000.0
        spacing = [0, 2, 2, 2, 600, 2]
        receipts = []
        stamp = base
        for index, gap in enumerate(spacing):
            stamp += gap
            receipts.append(support.project_transcript_record(json.loads(
                transcript_line(f"evt-{index}", ts=stamp, press_s=1.0))))
        signal = lifecycle.observe_back_to_back(receipts)

        self.assertEqual(signal["bursts"], 1)
        self.assertEqual(signal["longest_burst"], 4)
        self.assertEqual(signal["utterances_in_bursts"], 4)
        self.assertEqual(
            signal["unobservable_half"],
            "how-many-utterances-the-operator-actually-spoke")

    def test_a_long_pause_never_counts_as_back_to_back(self):
        base = 1_800_000_000.0
        receipts = [
            support.project_transcript_record(json.loads(transcript_line(
                f"evt-{index}", ts=base + index * 600, press_s=1.0)))
            for index in range(4)]
        self.assertEqual(lifecycle.observe_back_to_back(receipts)["bursts"], 0)

    def test_a_restart_needs_utterances_on_both_sides_of_it(self):
        start = support.RUNTIME_START_TRACE_EVENT
        utterance = support.UTTERANCE_TRACE_EVENT
        signal = lifecycle.observe_process_restart(
            [start, utterance, start, utterance, start])
        self.assertEqual(signal["runtime_starts"], 3)
        self.assertEqual(signal["utterance_traces"], 2)
        # Only the middle start has utterances before and after it.
        self.assertEqual(signal["restarts_with_utterances_on_both_sides"], 1)
        self.assertEqual(signal["utterances_after_the_last_start"], 0)

    def test_a_runtime_that_never_restarted_evidences_no_restart(self):
        signal = lifecycle.observe_process_restart(
            [support.RUNTIME_START_TRACE_EVENT]
            + [support.UTTERANCE_TRACE_EVENT] * 5)
        self.assertEqual(signal["restarts_with_utterances_on_both_sides"], 0)
        self.assertEqual(signal["utterances_after_the_last_start"], 5)


class PassiveObservationTests(unittest.TestCase):
    """Passive lifecycle evidence must never discharge a physical id."""

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

    def write(self, *lines):
        with self.transcripts.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")

    def write_log(self, *events):
        with self.log.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(trace_line(event) + "\n")

    def observe(self):
        return lifecycle.observe_runtime(
            self.session(), transcripts=self.transcripts,
            runtime_log=self.log)

    def artifact(self):
        return lifecycle.build_artifact(self.runs, self.session().payload())

    def test_passive_observation_discharges_nothing_at_all(self):
        self.write(transcript_line("evt-1", press_s=600.0))
        self.write_log(support.RUNTIME_START_TRACE_EVENT,
                       support.UTTERANCE_TRACE_EVENT,
                       support.RUNTIME_START_TRACE_EVENT,
                       support.UTTERANCE_TRACE_EVENT)
        self.observe()
        artifact = self.artifact()

        self.assertEqual(artifact["discharges_physical_validation"], [])
        self.assertEqual(
            artifact["still_requires_physical_validation"],
            ["physical-audio-device-switch",
             "physical-long-audio-memory-thermal",
             "physical-operating-system-sleep-wake"])
        self.assertEqual(artifact["passive_observation"]["discharges"], [])
        self.assertEqual(
            artifact["discharges_physical_validation_basis"],
            "operator-attested-runs-only")
        # A long capture really was observed; it still discharges nothing.
        self.assertEqual(
            artifact["scenarios"]["long-form"]["passive_observation"][
                "long_form_captures"], 1)
        self.assertEqual(artifact["scenarios"]["long-form"]["runs_recorded"], 0)

    def test_a_blind_scenario_never_gets_a_case_or_a_number(self):
        self.write(transcript_line("evt-1", press_s=600.0))
        self.observe()
        session = self.session()
        for scenario in lifecycle.PASSIVE_BLIND_SCENARIOS:
            self.assertNotIn(
                lifecycle.observed_case_id(scenario), session.records)
            entry = self.artifact()["scenarios"][scenario]
            self.assertIs(entry["passively_observable"], False)
            self.assertIsNone(entry["passive_observation"])
            self.assertTrue(entry["passively_not_observable_reason"])
        summary = lifecycle.render_summary(self.artifact())
        self.assertIn("not observable", summary)

    def test_an_operator_attested_run_still_discharges_its_id(self):
        self.write(transcript_line("evt-1", press_s=600.0))
        self.observe()
        session = self.session()
        session.record("sleep-wake-1", {
            "evidence_scope": support.ATTESTED_EVIDENCE_SCOPE,
            "scenario": "sleep-wake",
            "expected_utterances": 2,
            "recorded_utc": "2026-07-27T00:00:00+00:00",
            "runtime": {"source": "transcripts-jsonl+dictate-log",
                        "utterances_logged": 2,
                        "insertion_states": {"verified": 2},
                        "outbox_diversions": 0, "capture_ready_events": 1,
                        "insertion_ms_max": 20.2},
            "operator": {"utterance_survival": "all-utterances-produced-text",
                         "recovery": "recovered-without-intervention",
                         "machine_behavior": "normal"}})
        artifact = self.artifact()

        self.assertEqual(artifact["discharges_physical_validation"],
                         ["physical-operating-system-sleep-wake"])
        self.assertEqual(artifact["coverage"]["runs_recorded"], 1)
        self.assertEqual(artifact["evidence_scope"],
                         lifecycle.MIXED_ARTIFACT_SCOPE)

    def test_passive_runs_never_inflate_the_recorded_run_count(self):
        self.write(transcript_line("evt-1", press_s=1.0))
        self.observe()
        coverage = self.artifact()["coverage"]

        self.assertEqual(coverage["runs_recorded"], 0)
        self.assertEqual(coverage["runs_not_attempted"], 16)
        self.assertIs(coverage["extrapolated"], False)
        self.assertEqual(coverage["scenarios_passively_observed"],
                         sorted(lifecycle.PASSIVE_OBSERVABLE_SCENARIOS))
        self.assertEqual(self.artifact()["evidence_scope"],
                         lifecycle.OBSERVED_ARTIFACT_SCOPE)

    def test_an_attested_run_is_never_replaced_by_a_passive_one(self):
        session = self.session()
        session.record(lifecycle.observed_case_id("long-form"), {
            "evidence_scope": support.ATTESTED_EVIDENCE_SCOPE,
            "scenario": "long-form",
            "operator": {"utterance_survival": "all-utterances-produced-text",
                         "recovery": "recovered-without-intervention",
                         "machine_behavior": "chassis-hot"}})
        self.write(transcript_line("evt-1", press_s=600.0))
        outcome = self.observe()

        self.assertIn(lifecycle.observed_case_id("long-form"),
                      outcome["cases_protected_by_operator"])
        record = self.session().records[
            lifecycle.observed_case_id("long-form")]
        self.assertEqual(record["evidence_scope"],
                         support.ATTESTED_EVIDENCE_SCOPE)
        self.assertEqual(record["operator"]["machine_behavior"], "chassis-hot")

    def test_the_operator_answers_are_the_not_asked_value(self):
        self.write(transcript_line("evt-1", press_s=1.0))
        self.observe()
        record = self.session().records[
            lifecycle.observed_case_id("process-restart")]

        self.assertEqual(record["evidence_scope"],
                         support.OBSERVED_EVIDENCE_SCOPE)
        for answer in record["operator"].values():
            self.assertEqual(answer, support.NOT_ASKED_VERDICT)
        for choices in (lifecycle.SURVIVAL, lifecycle.RECOVERY,
                        lifecycle.MACHINE_BEHAVIOR):
            for choice in choices:
                self.assertNotEqual(choice.value, support.NOT_ASKED_VERDICT)

    def test_re_observing_restates_rather_than_doubling(self):
        self.write(transcript_line("evt-1", press_s=600.0))
        self.write_log(support.RUNTIME_START_TRACE_EVENT,
                       support.UTTERANCE_TRACE_EVENT,
                       support.RUNTIME_START_TRACE_EVENT,
                       support.UTTERANCE_TRACE_EVENT)
        first = self.observe()["signals"]
        second = self.observe()["signals"]

        self.assertEqual(first["process-restart"], second["process-restart"])
        self.assertEqual(first["long-form"]["long_form_captures"],
                         second["long-form"]["long_form_captures"])
        self.assertEqual(len(self.session().records),
                         len(lifecycle.PASSIVE_OBSERVABLE_SCENARIOS))

    def test_the_passive_artifact_carries_no_dictated_text(self):
        self.write(transcript_line(
            "evt-1", press_s=600.0, route=f"llm/{POISON_TONE}"))
        self.write_log(support.RUNTIME_START_TRACE_EVENT,
                       support.UTTERANCE_TRACE_EVENT)
        self.observe()
        blob = json.dumps(self.artifact())

        for poison in (POISON_RAW, POISON_CLEAN, POISON_TONE):
            self.assertNotIn(poison, blob)
        for key in support.TEXT_BEARING_TRANSCRIPT_KEYS:
            self.assertNotIn(f'"{key}"', blob)
        self.assertNotIn('"path"', blob)

    def test_the_observe_command_writes_an_owner_only_artifact(self):
        self.write(transcript_line("evt-1", press_s=600.0))
        target = self.dir / "artifact.json"
        writer = io.StringIO()
        code = lifecycle.main(
            ["--session", str(self.session_path), "observe",
             "--transcripts", str(self.transcripts),
             "--runtime-log", str(self.log), "--out", str(target)],
            reader=ScriptedReader([]), writer=writer)

        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        output = writer.getvalue()
        self.assertIn("PASSIVE LIFECYCLE OBSERVATION", output)
        self.assertIn("not observable passively", output)
        self.assertIn("discharges: nothing yet", output)

    def test_observe_refuses_a_transcript_that_does_not_exist(self):
        writer = io.StringIO()
        code = lifecycle.main(
            ["--session", str(self.session_path), "observe",
             "--transcripts", str(self.dir / "missing.jsonl"),
             "--runtime-log", str(self.log)],
            reader=ScriptedReader([]), writer=writer)
        self.assertEqual(code, 2)
        self.assertIn("does not exist yet", writer.getvalue())


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
