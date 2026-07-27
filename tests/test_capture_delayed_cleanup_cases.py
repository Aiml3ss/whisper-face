# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""The delayed-cleanup session must produce evidence, never a receipt."""

import ast
import io
import json
import stat
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capture_delayed_cleanup_cases as capture  # noqa: E402
import capture_session_support as support  # noqa: E402
from delayed_cleanup_activation import (  # noqa: E402
    MAX_P95_APPLY_MS,
    MIN_CASES,
    MIN_SCENARIO_CASES,
    MIN_SURFACE_CASES,
    SCENARIOS,
    SURFACES,
    evaluate_activation,
    validate_activation_receipt,
)
from delayed_cleanup_merge import DelayedApplyOutcome  # noqa: E402
from measurement_mode import (  # noqa: E402
    DELAYED_CLEANUP_LABEL,
    ORDINARY_PATH,
)


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


def runtime_line(outcome="applied", applied=2, held=0, apply_ms=42.5,
                 measured=False):
    tail = "" if apply_ms is None else f"; {apply_ms} ms"
    if measured:
        tail += "; measurement-mode"
    return f"[delayed-cleanup] {outcome}; {applied} applied, {held} held{tail}"


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.cases = capture.build_plan()

    def test_the_plan_clears_every_floor_in_the_real_gate(self):
        self.assertEqual(len(self.cases), MIN_CASES)
        surfaces = Counter(case["surface"] for case in self.cases)
        scenarios = Counter(case["scenario"] for case in self.cases)
        self.assertEqual(set(surfaces), set(SURFACES))
        self.assertEqual(set(scenarios), set(SCENARIOS))
        for surface, count in surfaces.items():
            self.assertGreaterEqual(count, MIN_SURFACE_CASES, surface)
        for scenario, count in scenarios.items():
            self.assertGreaterEqual(count, MIN_SCENARIO_CASES, scenario)
        self.assertEqual(
            len({case["id"] for case in self.cases}), len(self.cases))

    def test_the_predicted_split_would_satisfy_the_balance_rule(self):
        applied = sum(1 for case in self.cases
                      if case["expected_outcome"] == "applied")
        self.assertGreaterEqual(applied, 15)
        self.assertGreaterEqual(len(self.cases) - applied, 15)

    def test_the_predictions_name_real_adapter_outcomes(self):
        adapter_outcomes = {item.value for item in DelayedApplyOutcome}
        for scenario, expected in capture.SCENARIO_EXPECTATION.items():
            self.assertIn(expected, adapter_outcomes, scenario)

    def test_a_plan_that_cannot_reach_the_gate_is_refused(self):
        with self.assertRaises(support.CaptureError):
            capture.validate_plan(list(self.cases)[:10])

    def test_the_unreachable_scenario_is_gone_from_plan_and_gate(self):
        # `duplicate-callback` was demanded by the gate and reachable by no
        # operator action. It is dropped from both, not quietly tolerated.
        self.assertNotIn("duplicate-callback", SCENARIOS)
        self.assertNotIn("duplicate-callback", capture.SCENARIO_ORDER)
        self.assertNotIn("duplicate-callback", capture.SCENARIO_EXPECTATION)
        self.assertEqual(capture.UNREACHABLE_SCENARIOS, {})
        self.assertNotIn(
            "duplicate-callback", capture.render_plan(self.cases))

    def test_the_plan_tells_the_operator_how_to_make_the_feature_run(self):
        plan = capture.render_plan(self.cases)
        self.assertIn("--measure delayed-cleanup", plan)
        self.assertIn("grants no authority", plan)
        self.assertIn("no-runtime-line", plan)


class RuntimeLineTests(unittest.TestCase):
    def test_a_line_without_timing_reports_no_timing(self):
        parsed = capture.RUNTIME_LINE.search(runtime_line(apply_ms=None))
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed.group("apply_ms"))

    def test_the_runtime_line_shape_matches_what_dictate_prints(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn('print("[delayed-cleanup] "', source)
        self.assertIn('f"{outcome}; {applied_count} applied, '
                      '{rejected_count} held"', source)
        self.assertIn('f"; {apply_ms:.3f} ms"', source)
        self.assertIn('"; measurement-mode"', source)

    def test_a_measured_line_is_distinguished_from_an_ordinary_one(self):
        measured = capture.RUNTIME_LINE.search(runtime_line(measured=True))
        ordinary = capture.RUNTIME_LINE.search(runtime_line())
        self.assertEqual(measured.group("measured"), "measurement-mode")
        self.assertIsNone(ordinary.group("measured"))
        # The apply duration still parses either way.
        self.assertEqual(measured.group("apply_ms"), "42.5")

    def test_only_closed_fields_are_lifted_from_a_log_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dictate.log"
            path.write_text(
                "SECRET-USER-TEXT should not be lifted\n"
                + runtime_line() + " SECRET-TRAILING-TEXT\n",
                encoding="utf-8")
            parsed = capture.read_runtime_lines(path)
        self.assertEqual(parsed, [{
            "outcome": "applied", "applied": 2, "held": 0, "apply_ms": 42.5,
            "measurement_mode": ORDINARY_PATH}])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.log = self.dir / "dictate.log"
        self.log.write_text("", encoding="utf-8")
        self.session_path = self.dir / "session.json"
        self.cases = capture.build_plan()[:3]

    def session(self):
        return support.Session.load(
            self.session_path, capture.TOOL,
            plan_digest=capture.plan_digest(capture.build_plan()),
            blocked_reasons=capture.BLOCKED_REASONS)

    def drive(self, answers, timing_source="runtime-log"):
        writer = io.StringIO()
        code = capture.run_session(
            self.cases, self.session(), runtime_log=self.log,
            timing_source=timing_source,
            reader=ScriptedReader(answers), writer=writer)
        return code, writer.getvalue()

    def payload(self):
        return json.loads(self.session_path.read_text(encoding="utf-8"))

    def test_a_case_records_the_runtime_outcome_and_operator_answers(self):
        code, _ = self.drive([
            "1\n", appender(self.log, runtime_line()),
            "n\n", "n\n", "n\n", "n\n", "q\n"])
        self.assertEqual(code, 0)
        record = self.payload()["records"][0]
        self.assertEqual(record["runtime"]["outcome"], "applied")
        self.assertEqual(record["runtime"]["apply_ms"], 42.5)
        self.assertEqual(record["runtime"]["merge_applied"], 2)
        self.assertEqual(record["operator"], {
            "wrong_target_write": False, "user_edit_overwritten": False,
            "selection_disrupted": False, "duplicate_write": False})

    def test_a_missing_runtime_line_blocks_instead_of_scoring(self):
        self.drive(["1\n", "\n", "q\n"])
        self.assertEqual(self.payload()["records"], [])
        self.assertEqual(
            self.payload()["blocked"][0]["reason"], "no-runtime-line")

    def test_two_runtime_lines_block_instead_of_guessing(self):
        self.drive([
            "1\n", appender(self.log, runtime_line(), runtime_line()), "q\n"])
        self.assertEqual(
            self.payload()["blocked"][0]["reason"], "ambiguous-runtime-lines")

    def test_a_line_without_timing_blocks_and_never_defaults_apply_ms(self):
        self.drive([
            "1\n", appender(self.log, runtime_line(apply_ms=None)), "q\n"])
        self.assertEqual(self.payload()["records"], [])
        blocked = self.payload()["blocked"][0]
        self.assertEqual(blocked["reason"], "no-runtime-timing")
        self.assertNotIn("apply_ms", blocked)

    def test_timing_source_none_blocks_every_case(self):
        self.drive(
            ["1\n", appender(self.log, runtime_line()), "q\n"],
            timing_source="none")
        self.assertEqual(self.payload()["records"], [])
        self.assertEqual(
            self.payload()["blocked"][0]["reason"], "no-runtime-timing")

    def test_an_outcome_outside_the_gate_blocks(self):
        self.drive([
            "1\n", appender(self.log, runtime_line(outcome="something_new")),
            "q\n"])
        self.assertEqual(
            self.payload()["blocked"][0]["reason"], "runtime-outcome-unknown")

    def test_resume_skips_answered_cases_and_never_overwrites(self):
        self.drive(["2\n", "q\n"])
        first = self.payload()
        _, output = self.drive(["q\n"])
        self.assertNotIn(f"NEXT: {self.cases[0]['id']}\n", output)
        self.assertIn(f"NEXT: {self.cases[1]['id']}\n", output)
        self.assertEqual(first["blocked"], self.payload()["blocked"])
        with self.assertRaises(support.CaptureError):
            self.session().block(self.cases[0]["id"], "operator-skipped")

    def test_a_blocked_reason_outside_the_closed_set_is_refused(self):
        session = self.session()
        with self.assertRaises(support.CaptureError):
            session.block(self.cases[0]["id"], "seemed-slow")
        self.assertEqual(session.blocked, {})

    def test_the_session_file_is_owner_only(self):
        self.drive(["2\n", "q\n"])
        self.assertEqual(
            stat.S_IMODE(self.session_path.stat().st_mode), 0o600)


class GateConformanceTests(unittest.TestCase):
    """The emitted records file must be exactly what the gate parses."""

    def complete_session(self, *, apply_ms=40.0, measured=False):
        cases = capture.build_plan()
        records = []
        for case in cases:
            records.append({
                "case_id": case["id"],
                "surface": case["surface"],
                "scenario": case["scenario"],
                "expected_outcome": case["expected_outcome"],
                "recorded_utc": "2026-07-26T00:00:00+00:00",
                "runtime": {
                    "source": "dictate-log-delayed-cleanup-line",
                    "outcome": case["expected_outcome"],
                    "merge_applied": 1,
                    "merge_held": 0,
                    "apply_ms": apply_ms,
                    "measurement_mode": (DELAYED_CLEANUP_LABEL if measured
                                         else ORDINARY_PATH),
                },
                "operator": {
                    "wrong_target_write": False,
                    "user_edit_overwritten": False,
                    "selection_disrupted": False,
                    "duplicate_write": False,
                },
            })
        return {"records": records, "blocked": []}

    def test_emitted_records_are_accepted_by_the_real_evaluator(self):
        payload = capture.build_records(self.complete_session())
        self.assertEqual(set(payload), {"records"})
        receipt = evaluate_activation(
            payload["records"], manual_reviewed=True)
        self.assertIs(receipt["active"], True)
        self.assertEqual(receipt["reason"], "physical-evidence-passed")
        self.assertIs(validate_activation_receipt(receipt), True)

    def test_the_tool_never_attests_the_manual_review_itself(self):
        payload = capture.build_records(self.complete_session())
        receipt = evaluate_activation(
            payload["records"], manual_reviewed=False)
        self.assertIs(receipt["active"], False)
        self.assertEqual(receipt["reason"], "manual-review-required")
        self.assertIs(validate_activation_receipt(receipt), False)

    def test_a_slow_suite_still_fails_the_latency_budget(self):
        payload = capture.build_records(
            self.complete_session(apply_ms=MAX_P95_APPLY_MS + 1))
        receipt = evaluate_activation(payload["records"], manual_reviewed=True)
        self.assertIs(receipt["active"], False)
        self.assertEqual(receipt["reason"], "apply-latency-budget-exceeded")

    def test_records_carry_only_the_closed_gate_schema(self):
        payload = capture.build_records(self.complete_session())
        for record in payload["records"]:
            self.assertEqual(set(record), {
                "id", "source", "surface", "scenario", "expected_outcome",
                "actual_outcome", "wrong_target_write",
                "user_edit_overwritten", "selection_disrupted",
                "duplicate_write", "apply_ms", "measurement_mode"})
            self.assertEqual(record["source"], "caller-attested-physical")
            self.assertEqual(record["measurement_mode"], ORDINARY_PATH)

    def test_a_measured_corpus_passes_the_gate_and_says_it_was_measured(self):
        # Measurement mode runs the real transaction against the real
        # destination, so the gate accepts the corpus -- and the receipt
        # discloses exactly how much of it came from there.
        payload = capture.build_records(
            self.complete_session(measured=True))
        for record in payload["records"]:
            self.assertEqual(
                record["measurement_mode"], DELAYED_CLEANUP_LABEL)
        receipt = evaluate_activation(
            payload["records"], manual_reviewed=True)
        self.assertIs(receipt["active"], True)
        self.assertEqual(
            receipt["measurement_mode_cases"], len(payload["records"]))
        self.assertIs(validate_activation_receipt(receipt), True)

    def test_an_ordinary_corpus_declares_zero_measured_cases(self):
        payload = capture.build_records(self.complete_session())
        receipt = evaluate_activation(
            payload["records"], manual_reviewed=True)
        self.assertEqual(receipt["measurement_mode_cases"], 0)
        self.assertIs(validate_activation_receipt(receipt), True)

    def test_a_receipt_that_hides_the_measured_count_is_refused(self):
        payload = capture.build_records(
            self.complete_session(measured=True))
        receipt = evaluate_activation(
            payload["records"], manual_reviewed=True)
        stripped = {key: value for key, value in receipt.items()
                    if key != "measurement_mode_cases"}
        self.assertIs(validate_activation_receipt(stripped), False)
        self.assertIs(validate_activation_receipt(
            {**receipt, "measurement_mode_cases":
             receipt["case_count"] + 1}), False)


class CoverageTests(unittest.TestCase):
    def test_an_empty_session_claims_nothing(self):
        cases = capture.build_plan()
        coverage = capture.coverage_report(cases, {"records": [], "blocked": []})
        self.assertEqual(coverage["cases_recorded"], 0)
        for surface in SURFACES:
            self.assertEqual(coverage["surface_counts"][surface], 0)
        for scenario in SCENARIOS:
            self.assertEqual(coverage["scenario_counts"][scenario], 0)
        self.assertIn(f"case-count-0-of-{MIN_CASES}",
                      coverage["gate_shortfalls"])
        self.assertIs(coverage["receipt_written_by_this_tool"], False)
        self.assertIs(coverage["manual_review_flag_set_by_this_tool"], False)

    def test_a_partial_session_names_every_shortfall(self):
        cases = capture.build_plan()
        session = GateConformanceTests().complete_session()
        session["records"] = session["records"][:12]
        coverage = capture.coverage_report(cases, session)
        self.assertEqual(coverage["cases_recorded"], 12)
        joined = " ".join(coverage["gate_shortfalls"])
        self.assertIn("case-count-12-of-50", joined)
        self.assertIn("apply-reject-balance", joined)
        summary = capture.render_summary(coverage, "records.json")
        self.assertIn("not yet sufficient", summary)
        self.assertNotIn("--manual-reviewed", summary)

    def test_a_complete_session_prints_the_command_for_the_operator(self):
        cases = capture.build_plan()
        coverage = capture.coverage_report(
            cases, GateConformanceTests().complete_session())
        self.assertEqual(coverage["gate_shortfalls"], [])
        summary = capture.render_summary(coverage, "cases.json")
        self.assertIn(
            "uv run delayed_cleanup_activation.py cases.json "
            "--manual-reviewed --write-receipt delayed_cleanup_activation.json",
            summary)
        self.assertIn("does not run it", summary)


class NoReceiptWritingTests(unittest.TestCase):
    MODULES = (
        ROOT / "scripts" / "capture_delayed_cleanup_cases.py",
        ROOT / "scripts" / "capture_session_support.py",
    )
    FORBIDDEN_IMPORTS = {"subprocess", "runpy", "pty"}
    FORBIDDEN_CALLS = {
        "write_activation_receipt", "evaluate_activation",
        "validate_activation_receipt", "eval", "exec", "system", "popen",
        "execv", "execvp", "spawnl", "spawnv", "fork", "check_call",
        "check_output", "run",
    }
    RECEIPT_WORDS = ("manual_reviewed", "activation_receipt", "write_receipt")
    ALLOWED_ACTIVATION_IMPORTS = {
        "MAX_P95_APPLY_MS", "MIN_CASES", "MIN_SCENARIO_CASES",
        "MIN_SURFACE_CASES", "OUTCOMES", "PHYSICAL_SOURCE", "SCENARIOS",
        "SURFACES",
    }

    def trees(self):
        for path in self.MODULES:
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_the_gate_module_is_imported_for_constants_only(self):
        tree = ast.parse(self.MODULES[0].read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "delayed_cleanup_activation")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "delayed_cleanup_activation":
                    imported.update(alias.name for alias in node.names)
                else:
                    self.assertNotIn(
                        (node.module or "").split(".")[0],
                        self.FORBIDDEN_IMPORTS)
        self.assertEqual(imported, self.ALLOWED_ACTIVATION_IMPORTS)

    def test_no_process_execution_module_is_imported(self):
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

    def test_manual_reviewed_appears_only_inside_a_printed_string(self):
        source = self.MODULES[0].read_text(encoding="utf-8")
        tree = ast.parse(source)
        constants = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "--manual-reviewed" in node.value]
        self.assertTrue(constants)
        self.assertEqual(
            source.count("--manual-reviewed"),
            sum(item.count("--manual-reviewed") for item in constants))

    def test_the_tool_never_reads_a_text_bearing_transcript_key(self):
        source = self.MODULES[0].read_text(encoding="utf-8")
        for key in support.TEXT_BEARING_TRANSCRIPT_KEYS:
            self.assertNotIn(f'"{key}"', source, key)
            self.assertNotIn(f"'{key}'", source, key)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, argv, answers=()):
        writer = io.StringIO()
        code = capture.main(
            argv, reader=ScriptedReader(answers), writer=writer)
        return code, writer.getvalue()

    def test_plan_needs_no_hardware(self):
        code, output = self.run_cli(["plan"])
        self.assertEqual(code, 0)
        self.assertIn("cases: 50", output)
        self.assertIn("gate floors:", output)

    def test_emit_writes_an_owner_only_records_file(self):
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / "session.json"
            session = support.Session.load(
                session_path, capture.TOOL,
                plan_digest=capture.plan_digest(capture.build_plan()))
            session.save()
            target = Path(directory) / "cases.json"
            code, output = self.run_cli(
                ["--session", str(session_path), "emit", "--out", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"records": []})
            self.assertIn("not yet sufficient", output)

    def test_summary_refuses_a_foreign_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            path.write_text(json.dumps({"tool": "other"}), encoding="utf-8")
            code, output = self.run_cli(["--session", str(path), "summary"])
        self.assertEqual(code, 2)
        self.assertIn("is not a capture_delayed_cleanup_cases session", output)


if __name__ == "__main__":
    unittest.main()
