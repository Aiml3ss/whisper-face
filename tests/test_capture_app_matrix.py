# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""The physical app-matrix session must never invent an observation."""

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

import capture_app_matrix as matrix  # noqa: E402
import capture_session_support as support  # noqa: E402
from compatibility_fingerprint import (  # noqa: E402
    REASON_BUCKETS,
    STATE_BUCKETS,
    CompatibilityFingerprintAggregator,
    CompatibilityObservation,
)
from insertion_integrity import ReceiptReason, ReceiptState  # noqa: E402


POISON_RAW = "MY-PRIVATE-DICTATED-WORDS"
POISON_CLEAN = "MY-PRIVATE-CLEANED-WORDS"
POISON_OBSERVED = "MY-PRIVATE-OBSERVED-WORDS"
POISON_TONE = "my-secret-tone-name"


def transcript_line(event_id, *, state="verified", reason="commit_verified",
                    paste_attempted=True, bundle="com.apple.TextEdit",
                    extra_metrics=None):
    metrics = {
        "insertion_state": state,
        "insertion_reason": reason,
        "paste_attempted": paste_attempted,
        "insertion_verified": state == "verified",
        "delayed_cleanup_scheduled": False,
        "insertion_s": 0.0181,
    }
    metrics.update(extra_metrics or {})
    return json.dumps({
        "ts": 1_800_000_000.0,
        "app": bundle,
        "raw": POISON_RAW,
        "clean": POISON_CLEAN,
        "observed_text": POISON_OBSERVED,
        "path": f"llm/{POISON_TONE}",
        "id": event_id,
        "metrics": metrics,
    })


class ScriptedReader:
    """A stdin stand-in whose answers may have deliberate side effects."""

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


class PlanTests(unittest.TestCase):
    def test_default_plan_is_fifty_unique_apps_over_every_category(self):
        apps = matrix.load_apps(None)
        self.assertEqual(len(apps), matrix.FIFTY_APP_TARGET)
        self.assertEqual(len({app["id"] for app in apps}), len(apps))
        self.assertEqual(
            set(app["category"] for app in apps), set(matrix.CATEGORIES))
        for app in apps:
            self.assertIn(app["phrase"], matrix.PHRASES)

    def test_plan_digest_changes_with_the_plan(self):
        apps = list(matrix.load_apps(None))
        first = matrix.plan_digest(apps)
        self.assertEqual(first, matrix.plan_digest(list(apps)))
        self.assertNotEqual(first, matrix.plan_digest(apps[:10]))

    def test_a_malformed_app_list_is_refused(self):
        with self.assertRaises(support.CaptureError):
            matrix.validate_apps([{"id": "x", "name": "X"}])
        with self.assertRaises(support.CaptureError):
            matrix.validate_apps([
                {"id": "x", "name": "X", "category": "not-a-category",
                 "bundle_id": None, "phrase": "neutral-sentence",
                 "target_hint": "somewhere"}])


class VocabularyTests(unittest.TestCase):
    def test_recorded_states_and_reasons_are_the_real_enums(self):
        self.assertEqual(
            support.RECEIPT_STATES, {item.value for item in ReceiptState})
        self.assertEqual(
            support.RECEIPT_REASONS, {item.value for item in ReceiptReason})
        self.assertEqual(support.RECEIPT_STATES, set(STATE_BUCKETS))

    def test_every_terminal_reason_translates_into_a_closed_bucket(self):
        translated = support.COMPATIBILITY_REASON_BY_RECEIPT_REASON
        self.assertLessEqual(set(translated.values()), set(REASON_BUCKETS))
        self.assertEqual(
            set(translated),
            support.RECEIPT_REASONS - {ReceiptReason.PENDING.value})

    def test_the_runtime_sentinels_are_not_receipt_values(self):
        self.assertNotIn(support.NO_RECEIPT_STATE, support.RECEIPT_STATES)
        self.assertNotIn(support.NO_RECEIPT_REASON, support.RECEIPT_REASONS)

    def test_emitted_outcomes_feed_the_real_aggregator(self):
        aggregator = CompatibilityFingerprintAggregator(minimum_count=2)
        for reason in sorted(support.COMPATIBILITY_REASON_BY_RECEIPT_REASON):
            outcome = matrix._compatibility_outcome({
                "insertion_state": "unverifiable",
                "insertion_reason": reason,
                "paste_attempted": True,
            })
            self.assertIsNotNone(outcome, reason)
            observation = CompatibilityObservation.from_buckets(
                {"target": "readable", "paste": "available",
                 "readback": "unavailable"},
                outcome,
            )
            self.assertRegex(observation.fingerprint(), r"^[0-9a-f]{16}$")
            aggregator.record(
                {"target": "readable", "paste": "available",
                 "readback": "unavailable"},
                outcome)

    def test_a_pending_or_sentinel_receipt_has_no_outcome(self):
        for reason in (ReceiptReason.PENDING.value, support.NO_RECEIPT_REASON):
            self.assertIsNone(matrix._compatibility_outcome({
                "insertion_state": "unresolved",
                "insertion_reason": reason,
                "paste_attempted": True,
            }))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.transcripts = self.dir / "transcripts.jsonl"
        self.transcripts.write_text("", encoding="utf-8")
        self.session_path = self.dir / "session.json"
        self.apps = matrix.load_apps(None)[:4]

    def session(self):
        return support.Session.load(
            self.session_path, matrix.TOOL,
            plan_digest=matrix.plan_digest(self.apps),
            blocked_reasons=matrix.BLOCKED_REASONS)

    def drive(self, answers):
        writer = io.StringIO()
        code = matrix.run_session(
            self.apps, self.session(), transcripts=self.transcripts,
            reader=ScriptedReader(answers), writer=writer)
        return code, writer.getvalue()

    def test_one_clean_case_records_exactly_what_was_reported(self):
        code, output = self.drive([
            "1\n",
            appender(self.transcripts, transcript_line("evt-1")),
            "1\n", "1\n", "q\n",
        ])
        self.assertEqual(code, 0)
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["records"]), 1)
        record = payload["records"][0]
        self.assertEqual(record["case_id"], self.apps[0]["id"])
        self.assertEqual(record["runtime"]["insertion_state"], "verified")
        self.assertEqual(
            record["runtime"]["insertion_reason"], "commit_verified")
        self.assertIs(record["runtime"]["paste_attempted"], True)
        self.assertIs(record["runtime"]["bundle_matches_plan"], True)
        self.assertEqual(
            record["operator"]["text_verdict"],
            "correct-text-in-intended-target")
        self.assertEqual(record["operator"]["app_behavior"], "normal")
        self.assertIsNone(record["capabilities"])
        self.assertIn(f"{self.apps[0]['name']}", output)
        self.assertEqual(
            support.progress_line(
                31, 50, {"electron-chromium": (4, 8), "terminal": (2, 5)}),
            "31/50 · electron-chromium 4/8 · terminal 2/5")

    def test_no_runtime_record_blocks_instead_of_scoring(self):
        self.drive(["1\n", "\n", "q\n"])
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["records"], [])
        self.assertEqual(payload["blocked"][0]["reason"], "no-runtime-record")

    def test_two_runtime_records_block_instead_of_guessing(self):
        self.drive([
            "1\n",
            appender(self.transcripts,
                     transcript_line("evt-a"), transcript_line("evt-b")),
            "q\n",
        ])
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["records"], [])
        self.assertEqual(
            payload["blocked"][0]["reason"], "ambiguous-runtime-records")
        self.assertEqual(payload["blocked"][0]["records_observed"], 2)

    def test_a_runtime_sentinel_receipt_blocks(self):
        self.drive([
            "1\n",
            appender(self.transcripts, transcript_line(
                "evt-legacy", state=support.NO_RECEIPT_STATE,
                reason=support.NO_RECEIPT_REASON)),
            "q\n",
        ])
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["records"], [])
        self.assertEqual(
            payload["blocked"][0]["reason"], "runtime-reported-no-receipt")

    def test_could_not_judge_blocks_rather_than_passing(self):
        self.drive([
            "1\n",
            appender(self.transcripts, transcript_line("evt-2")),
            "7\n", "q\n",
        ])
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["records"], [])
        self.assertEqual(
            payload["blocked"][0]["reason"], "operator-could-not-judge")

    def test_resume_skips_answered_cases_and_never_overwrites(self):
        self.drive(["2\n", "q\n"])
        first = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(first["blocked"][0]["case_id"], self.apps[0]["id"])

        _, output = self.drive(["q\n"])
        self.assertNotIn(f"NEXT: {self.apps[0]['name']} ", output)
        self.assertIn(f"NEXT: {self.apps[1]['name']} ", output)
        second = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(first["blocked"], second["blocked"])

        session = self.session()
        with self.assertRaises(support.CaptureError):
            session.block(self.apps[0]["id"], "operator-skipped")
        with self.assertRaises(support.CaptureError):
            session.record(self.apps[0]["id"], {"category": "native-cocoa"})

    def test_a_blocked_reason_outside_the_closed_set_is_refused(self):
        session = self.session()
        with self.assertRaises(support.CaptureError):
            session.block(self.apps[1]["id"], "it-felt-wrong")
        self.assertEqual(session.blocked, {})
        for reason in matrix.BLOCKED_REASONS:
            self.assertIsInstance(reason, str)

    def test_a_session_recorded_against_another_plan_is_refused(self):
        self.drive(["2\n", "q\n"])
        with self.assertRaises(support.CaptureError):
            support.Session.load(
                self.session_path, matrix.TOOL, plan_digest="0000000000000000")

    def test_session_and_artifact_files_are_owner_only(self):
        self.drive(["2\n", "q\n"])
        artifact = self.dir / "artifact.json"
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        support.atomic_write_json(
            artifact, matrix.build_artifact(self.apps, payload))
        for path in (self.session_path, artifact):
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600, path)

    def test_free_text_is_never_accepted_as_an_answer(self):
        writer = io.StringIO()
        reader = ScriptedReader(["something else\n", "1\n"])
        answer = support.ask_choice(
            "pick", (support.Choice("1", "chosen", "the only choice"),),
            reader=reader, writer=writer)
        self.assertEqual(answer, "chosen")
        self.assertIn("is not one of the listed keys", writer.getvalue())


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.transcripts = self.dir / "transcripts.jsonl"
        self.transcripts.write_text("", encoding="utf-8")
        self.session_path = self.dir / "session.json"
        self.apps = matrix.load_apps(None)

    def record_two(self, first_metrics=None, second_metrics=None):
        session = support.Session.load(
            self.session_path, matrix.TOOL,
            plan_digest=matrix.plan_digest(self.apps))
        writer = io.StringIO()
        matrix.run_session(
            self.apps, session, transcripts=self.transcripts,
            reader=ScriptedReader([
                "1\n",
                appender(self.transcripts, transcript_line(
                    "evt-1", extra_metrics=first_metrics)),
                "1\n", "1\n",
                "1\n",
                appender(self.transcripts, transcript_line(
                    "evt-2", state="conflict", reason="focus_drift",
                    paste_attempted=False, bundle="com.apple.Notes",
                    extra_metrics=second_metrics)),
                "5\n", "5\n",
                "2\n",
                "q\n",
            ]),
            writer=writer)
        return json.loads(self.session_path.read_text(encoding="utf-8"))

    def test_coverage_reports_only_what_was_measured(self):
        artifact = matrix.build_artifact(self.apps, self.record_two())
        coverage = artifact["coverage"]
        self.assertEqual(coverage["apps_planned"], 50)
        self.assertEqual(coverage["apps_recorded"], 2)
        self.assertEqual(coverage["apps_blocked"], 1)
        self.assertEqual(coverage["apps_not_attempted"], 47)
        self.assertIs(coverage["extrapolated"], False)
        self.assertEqual(artifact["claims"]["real_apps_exercised"], 2)
        self.assertIs(artifact["claims"]["fifty_app_claim"], False)
        self.assertIs(artifact["claims"]["four_nines_claim"], False)

    def test_untested_categories_are_never_credited(self):
        artifact = matrix.build_artifact(self.apps, self.record_two())
        by_category = artifact["coverage"]["by_category"]
        self.assertEqual(by_category["native-cocoa"]["recorded"], 2)
        for name in matrix.CATEGORIES:
            if name == "native-cocoa":
                continue
            self.assertEqual(by_category[name]["recorded"], 0, name)
            self.assertEqual(
                by_category[name]["not_attempted"],
                by_category[name]["planned"], name)
        totals = sum(entry["recorded"] for entry in by_category.values())
        self.assertEqual(totals, artifact["coverage"]["apps_recorded"])

    def test_the_fifty_app_claim_needs_fifty_recorded_apps(self):
        session = {"records": [
            {"case_id": f"app-{index}", "category": "native-cocoa",
             "runtime": {"insertion_state": "verified",
                         "insertion_reason": "commit_verified",
                         "paste_attempted": True},
             "operator": {"text_verdict": "correct-text-in-intended-target",
                          "app_behavior": "normal"}}
            for index in range(50)], "blocked": []}
        artifact = matrix.build_artifact(self.apps, session)
        self.assertIs(artifact["claims"]["fifty_app_claim"], True)
        self.assertIs(artifact["claims"]["four_nines_claim"], False)

    def test_the_artifact_carries_no_dictated_text(self):
        artifact = matrix.build_artifact(self.apps, self.record_two())
        blob = json.dumps(artifact)
        for poison in (POISON_RAW, POISON_CLEAN, POISON_OBSERVED,
                       POISON_TONE):
            self.assertNotIn(poison, blob)
        for key in support.TEXT_BEARING_TRANSCRIPT_KEYS:
            self.assertNotIn(f'"{key}"', blob)
        self.assertIn("com.apple.TextEdit", blob)  # app identity is allowed

    def test_a_windows_window_title_is_withheld(self):
        receipt = support.project_transcript_record(json.loads(
            transcript_line("evt-w", bundle="windows:Quarterly plan.docx")))
        self.assertIsNone(receipt.app_bundle)
        self.assertIs(receipt.app_identity_withheld, True)

    def test_the_capability_half_is_reported_as_unavailable(self):
        artifact = matrix.build_artifact(self.apps, self.record_two())
        compatibility = artifact["compatibility"]
        self.assertEqual(len(compatibility["outcomes"]), 2)
        self.assertEqual(compatibility["observations"], [])
        self.assertIs(compatibility["capability_buckets_available"], False)
        self.assertEqual(
            compatibility["capability_blocked_reason"],
            support.CAPABILITY_UNAVAILABLE_REASON)
        self.assertEqual(
            compatibility["required_runtime_metric_keys"],
            list(support.CAPABILITY_METRIC_KEYS))

    def test_a_runtime_that_reports_buckets_produces_observations(self):
        receipt = support.project_transcript_record(json.loads(
            transcript_line("evt-c", extra_metrics={
                "insertion_target": "opaque",
                "insertion_paste": "available",
                "insertion_readback": "unavailable"})))
        self.assertEqual(
            receipt.capabilities,
            {"target": "opaque", "paste": "available",
             "readback": "unavailable"})
        observation = CompatibilityObservation.from_buckets(
            receipt.capabilities, receipt.compatibility_outcome())
        self.assertRegex(observation.fingerprint(), r"^[0-9a-f]{16}$")

    def test_a_physical_session_now_yields_full_compatibility_pairs(self):
        # The runtime writes the capability triple into `transcripts.jsonl`,
        # so a recorded session carries both halves of every observation and
        # can feed the fingerprint aggregator directly.
        artifact = matrix.build_artifact(self.apps, self.record_two(
            first_metrics={
                "insertion_target": "readable",
                "insertion_paste": "available",
                "insertion_readback": "available"},
            second_metrics={
                "insertion_target": "opaque",
                "insertion_paste": "available",
                "insertion_readback": "unavailable"}))
        compatibility = artifact["compatibility"]

        self.assertIs(compatibility["capability_buckets_available"], True)
        self.assertIsNone(compatibility["capability_blocked_reason"])
        self.assertEqual(len(compatibility["observations"]), 2)

        aggregator = CompatibilityFingerprintAggregator(minimum_count=2)
        for pair in compatibility["observations"]:
            observation = CompatibilityObservation.from_buckets(
                pair["capabilities"], pair["outcome"])
            self.assertRegex(observation.fingerprint(), r"^[0-9a-f]{16}$")
            aggregator.record(pair["capabilities"], pair["outcome"])
        self.assertIsNone(aggregator.export_payload())

    def test_the_runtime_emits_exactly_the_metric_keys_this_tool_reads(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        for key in support.CAPABILITY_METRIC_KEYS:
            self.assertIn(f'"{key}"', source, key)

    def test_a_record_without_buckets_still_reports_the_outcome_half(self):
        artifact = matrix.build_artifact(self.apps, self.record_two())
        compatibility = artifact["compatibility"]
        self.assertEqual(len(compatibility["outcomes"]), 2)
        self.assertEqual(compatibility["observations"], [])
        self.assertIs(compatibility["capability_buckets_available"], False)

    def test_summary_states_the_measured_counts(self):
        artifact = matrix.build_artifact(self.apps, self.record_two())
        summary = matrix.render_summary(artifact)
        self.assertIn("apps recorded: 2/50", summary)
        self.assertIn("real apps exercised: 2", summary)
        self.assertIn("50-app claim: no", summary)
        self.assertIn("four-nines claim: no", summary)


class NoReceiptWritingTests(unittest.TestCase):
    """No capture tool may install a receipt or attest a manual review."""

    MODULES = (
        ROOT / "scripts" / "capture_app_matrix.py",
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

    def test_no_manual_review_identifier_is_bound_or_passed(self):
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
        source = (ROOT / "scripts" / "capture_app_matrix.py").read_text(
            encoding="utf-8")
        for key in support.TEXT_BEARING_TRANSCRIPT_KEYS:
            self.assertNotIn(f'"{key}"', source, key)
            self.assertNotIn(f"'{key}'", source, key)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, argv, answers=()):
        writer = io.StringIO()
        code = matrix.main(
            argv, reader=ScriptedReader(answers), writer=writer)
        return code, writer.getvalue()

    def test_plan_needs_no_hardware(self):
        code, output = self.run_cli(["plan"])
        self.assertEqual(code, 0)
        self.assertIn("apps: 50", output)
        self.assertIn("category totals:", output)

    def test_export_apps_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "apps.json"
            code, _ = self.run_cli(["export-apps", "--out", str(target)])
            self.assertEqual(code, 0)
            reloaded = matrix.load_apps(target)
            self.assertEqual(len(reloaded), matrix.FIFTY_APP_TARGET)

    def test_run_refuses_without_a_runtime_record(self):
        with tempfile.TemporaryDirectory() as directory:
            code, output = self.run_cli([
                "--session", str(Path(directory) / "s.json"), "run",
                "--transcripts", str(Path(directory) / "missing.jsonl")])
        self.assertEqual(code, 2)
        self.assertIn("does not exist yet", output)

    def test_summary_refuses_a_foreign_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            path.write_text(json.dumps({"tool": "something-else"}),
                            encoding="utf-8")
            code, output = self.run_cli(["--session", str(path), "summary"])
        self.assertEqual(code, 2)
        self.assertIn("is not a capture_app_matrix session", output)


if __name__ == "__main__":
    unittest.main()
