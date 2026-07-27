# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Contract tests for the physical latency rig.

The rig has two jobs and one hard limit. Trace mode aggregates the runtime's
own ``warm_path`` traces and must never turn a thin or unattested log into a
confident physical claim. Observe mode is a stopwatch whose only output is an
observation file the real ``competitor_benchmark.py`` evaluator accepts, so
the round-trip tests here import that evaluator and feed it the emitted file.

The last group is the load-bearing one: the rig observes and records only.
It must be structurally incapable of running a product, injecting key events,
generating audio, or inventing a number.
"""

import ast
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_latency_rig as rig  # noqa: E402
from competitor_benchmark import (  # noqa: E402
    Protocol,
    ProtocolError,
    evaluate_product_run,
)
from performance_lab import WARM_PATH_STAGES  # noqa: E402


SOURCE_PATH = ROOT / "benchmark_latency_rig.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

PROTOCOL_PATH = ROOT / "benchmarks" / "competitor_tasks.json"

WARM_FIELDS = (
    "release_ms", "asr_ms", "compiler_ms",
    "cleanup_ms", "context_ms", "insertion_ms",
)


def warm_line(**overrides) -> str:
    """One trace line shaped exactly as dictate.py emits it."""
    payload = {field: 100.0 for field in WARM_FIELDS}
    payload.update(overrides)
    payload["event"] = "warm_path"
    payload["schema_version"] = 1
    return "[trace] " + json.dumps(
        payload, sort_keys=True, separators=(",", ":"))


def fake_clock(values):
    iterator = iter(values)
    return lambda: next(iterator)


class RigHarness(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write_log(self, lines) -> Path:
        path = self.root / "dictate.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def run_main(self, argv, *, inputs="", times=(0.0, 0.0)):
        reader = io.StringIO(inputs)
        writer = io.StringIO()
        code = rig.main(
            argv, reader=reader, writer=writer, clock=fake_clock(times))
        return code, writer.getvalue()


class TraceParsingTests(RigHarness):
    def test_only_valid_warm_path_traces_are_aggregated(self):
        secret = "the operator typed something private here"
        log = self.write_log([
            "ordinary console noise",
            secret,
            "[trace] {not json",
            '[trace] {"event":"warmup_total","schema_version":1,'
            '"duration_ms":1.0,"success":1.0}',
            warm_line(),
            warm_line(asr_ms=250.0),
        ])
        report = rig.build_trace_report(log, minimum_samples=2)
        self.assertEqual(report["records"], 2)
        self.assertEqual(report["ignored_non_trace_lines"], 2)
        self.assertEqual(report["ignored_non_warm_path_records"], 1)
        self.assertEqual(report["rejected_by_reason"], {"invalid-json": 1})
        serialized = json.dumps(report)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(str(log), serialized)
        self.assertNotIn("dictate.log", serialized)

    def test_aggregation_reports_exact_p50_p95_max_per_stage(self):
        log = self.write_log(
            [warm_line(release_ms=index * 10.0) for index in range(20)])
        report = rig.build_trace_report(log, minimum_samples=20)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["latency_ms"]["release"], {
            "samples": 20, "p50": 95.0, "p95": 180.5, "max": 190.0,
        })
        # A constant stage collapses to itself at every summarized point.
        self.assertEqual(report["latency_ms"]["asr"], {
            "samples": 20, "p50": 100.0, "p95": 100.0, "max": 100.0,
        })
        self.assertEqual(
            set(report["latency_ms"]),
            {label for label, _field in WARM_PATH_STAGES})

    def test_stage_labels_match_the_lab_and_carry_definitions(self):
        self.assertEqual(
            set(rig.STAGE_DEFINITIONS),
            {label for label, _field in WARM_PATH_STAGES})
        log = self.write_log([warm_line() for _ in range(20)])
        report = rig.build_trace_report(log)
        self.assertEqual(report["stage_definitions"], rig.STAGE_DEFINITIONS)
        self.assertIn("end-to-end", report["stage_definitions"]["release"])

    def test_a_thin_log_reports_insufficient_samples_not_percentiles(self):
        log = self.write_log([warm_line() for _ in range(3)])
        report = rig.build_trace_report(log)
        self.assertEqual(report["minimum_samples"], 20)
        self.assertEqual(report["status"], "insufficient-samples")
        self.assertIsNone(report["latency_ms"])
        self.assertEqual(report["records"], 3)

        code, output = self.run_main(
            ["trace", "--trace-log", str(log)])
        self.assertEqual(code, 1)
        self.assertIn("insufficient-samples", output)
        self.assertNotIn("p50", output)

    def test_minimum_samples_is_bounded(self):
        log = self.write_log([warm_line()])
        for bad in (0, -1, True, 100_001):
            with self.assertRaises(rig.RigError):
                rig.build_trace_report(log, minimum_samples=bad)

    def test_a_missing_log_never_reflects_the_path(self):
        code, output = self.run_main(
            ["trace", "--trace-log", str(self.root / "absent.log")])
        self.assertEqual(code, 2)
        self.assertIn("runtime trace input unavailable", output)
        self.assertNotIn("absent.log", output)


class AttestationTests(RigHarness):
    def test_default_report_is_unattested_and_not_physical_evidence(self):
        log = self.write_log([warm_line() for _ in range(20)])
        report = rig.build_trace_report(log)
        self.assertEqual(report["source"], "operator-supplied-trace-log")
        self.assertEqual(report["evidence_scope"], "unattested-trace-log")
        self.assertIs(report["operator_attested"], False)
        self.assertIs(report["physical_evidence"], False)
        self.assertIs(report["physical_conditions_verified"], False)

    def test_the_flag_is_the_only_way_to_physical_evidence(self):
        log = self.write_log([warm_line() for _ in range(20)])
        code, output = self.run_main([
            "trace", "--trace-log", str(log),
            "--operator-attestation", "--format", "json",
        ])
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertIs(report["operator_attested"], True)
        self.assertIs(report["physical_evidence"], True)
        self.assertEqual(report["evidence_scope"], "physical-operator-attested")
        # The tool still never claims it verified the session itself.
        self.assertIs(report["physical_conditions_verified"], False)
        self.assertEqual(report["source"], "operator-supplied-trace-log")

    def test_attestation_does_not_relax_the_sample_gate(self):
        log = self.write_log([warm_line() for _ in range(2)])
        code, output = self.run_main([
            "trace", "--trace-log", str(log),
            "--operator-attestation", "--format", "json",
        ])
        self.assertEqual(code, 1)
        report = json.loads(output)
        self.assertEqual(report["status"], "insufficient-samples")
        self.assertIsNone(report["latency_ms"])

    def test_the_written_report_matches_the_printed_one(self):
        log = self.write_log([warm_line() for _ in range(20)])
        out = self.root / "report.json"
        code, output = self.run_main([
            "trace", "--trace-log", str(log), "--format", "json",
            "--output", str(out),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text()), json.loads(output))


MEASURED_INPUTS = "\n\ny\n0\n3\nrecording-042\n"


class ObserveTests(RigHarness):
    def setUp(self):
        super().setUp()
        self.run_file = self.root / "wispr-flow-run-1.json"
        self.corpus = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        self.protocol = Protocol.from_mapping(self.corpus)

    def observe(self, *extra, inputs="", times=(0.0, 0.0)):
        argv = [
            "observe", "--product", "wispr-flow", "--task", "short-message",
            "--protocol", str(PROTOCOL_PATH),
            "--run-file", str(self.run_file),
            "--environment", "mac-test-fixture",
        ]
        argv.extend(extra)
        return self.run_main(argv, inputs=inputs, times=times)

    def test_a_measured_stopwatch_run_round_trips_the_real_evaluator(self):
        code, output = self.observe(
            inputs=MEASURED_INPUTS, times=(100.0, 100.8532))
        self.assertEqual(code, 0, output)

        envelope = json.loads(self.run_file.read_text(encoding="utf-8"))
        self.assertEqual(set(envelope), {
            "schema_version", "protocol_id", "product_id", "run_id",
            "environment_id", "observations",
        })
        result = evaluate_product_run(self.corpus, envelope)
        self.assertEqual(result["coverage"], {
            "tasks": 6, "measured": 1, "unavailable": 5, "claimed_only": 0,
        })
        [measured] = result["measured"]["per_task"]
        self.assertEqual(measured["task_id"], "short-message")
        self.assertAlmostEqual(measured["latency_ms"], 853.2)
        self.assertIs(measured["completed"], True)
        self.assertEqual(measured["error_count"], 0)
        self.assertEqual(measured["interaction_count"], 3)
        self.assertEqual(result["unavailable"]["reasons"], {"not_run": 5})

    def test_latency_is_the_monotonic_interval_not_a_typed_number(self):
        code, _ = self.observe(inputs=MEASURED_INPUTS, times=(5.0, 6.25))
        self.assertEqual(code, 0)
        envelope = json.loads(self.run_file.read_text(encoding="utf-8"))
        entry = next(item for item in envelope["observations"]
                     if item["task_id"] == "short-message")
        self.assertEqual(entry["latency_ms"], 1250.0)
        self.assertEqual(entry["source_reference"], "recording-042")

    def test_an_incomplete_attempt_requires_at_least_one_error(self):
        inputs = "\n\nn\n0\n1\n5\nnote-7\n"
        code, output = self.observe(inputs=inputs, times=(0.0, 1.0))
        self.assertEqual(code, 0, output)
        self.assertIn("at least one error", output)
        envelope = json.loads(self.run_file.read_text(encoding="utf-8"))
        entry = next(item for item in envelope["observations"]
                     if item["task_id"] == "short-message")
        self.assertIs(entry["completed"], False)
        self.assertEqual(entry["error_count"], 1)
        self.assertEqual(entry["interaction_count"], 5)
        evaluate_product_run(self.corpus, envelope)

    def test_a_recorded_task_is_never_silently_rewritten(self):
        code, _ = self.observe(inputs=MEASURED_INPUTS, times=(0.0, 0.5))
        self.assertEqual(code, 0)
        before = self.run_file.read_text(encoding="utf-8")

        code, output = self.observe(inputs=MEASURED_INPUTS, times=(0.0, 9.0))
        self.assertEqual(code, 2)
        self.assertIn("--redo", output)
        self.assertEqual(self.run_file.read_text(encoding="utf-8"), before)

        code, _ = self.observe(
            "--redo", inputs=MEASURED_INPUTS, times=(0.0, 9.0))
        self.assertEqual(code, 0)
        envelope = json.loads(self.run_file.read_text(encoding="utf-8"))
        entry = next(item for item in envelope["observations"]
                     if item["task_id"] == "short-message")
        self.assertEqual(entry["latency_ms"], 9000.0)

    def test_claimed_only_and_unavailable_states_are_recorded_closed(self):
        code, _ = self.run_main([
            "observe", "--product", "wispr-flow", "--task", "fresh-install",
            "--protocol", str(PROTOCOL_PATH),
            "--run-file", str(self.run_file),
            "--environment", "mac-test-fixture",
            "--state", "claimed-only",
            "--source-reference", "vendor-page-snapshot",
        ])
        self.assertEqual(code, 0)
        code, _ = self.run_main([
            "observe", "--product", "wispr-flow", "--task", "ready-from-launch",
            "--protocol", str(PROTOCOL_PATH),
            "--run-file", str(self.run_file),
            "--state", "unavailable", "--reason", "product_unavailable",
        ])
        self.assertEqual(code, 0)
        envelope = json.loads(self.run_file.read_text(encoding="utf-8"))
        result = evaluate_product_run(self.corpus, envelope)
        self.assertEqual(result["coverage"], {
            "tasks": 6, "measured": 0, "unavailable": 5, "claimed_only": 1,
        })
        self.assertEqual(
            result["unavailable"]["reasons"],
            {"not_run": 4, "product_unavailable": 1})

    def test_state_flags_cannot_be_mixed(self):
        for extra in (
            ("--state", "claimed-only"),  # missing reference
            ("--state", "unavailable"),  # missing reason
            ("--state", "unavailable", "--source-reference", "x"),
            ("--state", "claimed-only", "--source-reference", "x",
             "--reason", "not_run"),
            ("--source-reference", "x"),  # measured takes no flags
        ):
            code, output = self.observe(*extra)
            self.assertEqual(code, 2, output)
        self.assertFalse(self.run_file.exists())

    def test_a_new_run_file_requires_an_environment(self):
        code, output = self.run_main([
            "observe", "--product", "wispr-flow", "--task", "short-message",
            "--protocol", str(PROTOCOL_PATH),
            "--run-file", str(self.run_file),
        ])
        self.assertEqual(code, 2)
        self.assertIn("--environment", output)
        self.assertFalse(self.run_file.exists())

    def test_an_environment_mismatch_is_refused(self):
        code, _ = self.observe(inputs=MEASURED_INPUTS, times=(0.0, 0.5))
        self.assertEqual(code, 0)
        code, output = self.run_main([
            "observe", "--product", "wispr-flow", "--task", "two-sentences",
            "--protocol", str(PROTOCOL_PATH),
            "--run-file", str(self.run_file),
            "--environment", "different-machine",
            "--state", "unavailable", "--reason", "not_run",
        ])
        self.assertEqual(code, 2)
        self.assertIn("environment", output)

    def test_an_unknown_task_lists_the_protocol_tasks(self):
        code, output = self.run_main([
            "observe", "--product", "wispr-flow", "--task", "no-such-task",
            "--protocol", str(PROTOCOL_PATH),
            "--run-file", str(self.run_file),
            "--environment", "mac-test-fixture",
        ])
        self.assertEqual(code, 2)
        self.assertIn("short-message", output)
        self.assertFalse(self.run_file.exists())

    def test_quitting_writes_nothing(self):
        code, _ = self.observe(inputs="q\n")
        self.assertEqual(code, 1)
        self.assertFalse(self.run_file.exists())
        code, _ = self.observe(inputs="\n\ny\n")  # EOF mid-questions
        self.assertEqual(code, 1)
        self.assertFalse(self.run_file.exists())

    def test_an_overlong_reference_is_reasked_not_truncated(self):
        overlong = "r" * 300
        inputs = f"\n\ny\n0\n3\n{overlong}\nok-note\n"
        code, _ = self.observe(inputs=inputs, times=(0.0, 0.5))
        self.assertEqual(code, 0)
        envelope = json.loads(self.run_file.read_text(encoding="utf-8"))
        entry = next(item for item in envelope["observations"]
                     if item["task_id"] == "short-message")
        self.assertEqual(entry["source_reference"], "ok-note")

    def test_the_dictated_phrase_never_reaches_the_run_file(self):
        code, _ = self.observe(inputs=MEASURED_INPUTS, times=(0.0, 0.5))
        self.assertEqual(code, 0)
        serialized = json.loads(self.run_file.read_text(encoding="utf-8"))
        self.assertNotIn(
            "revised agenda", json.dumps(serialized).casefold())

    def test_the_placeholder_is_the_protocols_own_not_run_state(self):
        entry = rig.placeholder_observation("short-message")
        with self.assertRaises(ProtocolError):
            # A placeholder is a real unavailable observation, so mutating it
            # into a measured one without values must fail validation.
            from competitor_benchmark import Observation
            broken = dict(entry)
            broken["evidence_state"] = "measured"
            Observation.from_mapping(broken)
        self.assertTrue(rig.is_placeholder(entry))
        self.assertFalse(rig.is_placeholder(
            rig.unavailable_observation("short-message", "product_unavailable")))


class ObservationalOnlyTests(unittest.TestCase):
    """The rig must be structurally incapable of driving anything."""

    FORBIDDEN_MODULES = frozenset({
        "subprocess", "runpy", "ctypes", "sounddevice", "pynput", "pyautogui",
        "Quartz", "AppKit", "ApplicationServices", "webbrowser", "random",
        "secrets", "math", "dictate",
    })

    FORBIDDEN_CALLS = frozenset({
        "eval", "exec", "compile", "__import__",
        "os.system", "os.popen", "os.execv", "os.execvp", "os.execve",
        "os.spawnv", "os.spawnl", "os.posix_spawn", "os.fork",
        "subprocess.run", "subprocess.call", "subprocess.Popen",
        "subprocess.check_call", "subprocess.check_output",
        "runpy.run_path", "runpy.run_module",
        "time.sleep",
    })

    def imported_modules(self):
        names = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    @staticmethod
    def dotted(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
        return None

    def test_no_automation_or_audio_module_is_imported(self):
        self.assertEqual(self.imported_modules() & self.FORBIDDEN_MODULES,
                         set())
        self.assertNotIn("CGEvent", SOURCE)

    def test_no_code_path_executes_a_process_or_sleeps(self):
        offenders = []
        for node in ast.walk(TREE):
            if not isinstance(node, ast.Call):
                continue
            name = self.dotted(node.func)
            if name in self.FORBIDDEN_CALLS:
                offenders.append((name, node.lineno))
        self.assertEqual(offenders, [])

    def test_the_only_clock_is_monotonic(self):
        time_calls = {
            self.dotted(node.func)
            for node in ast.walk(TREE)
            if isinstance(node, ast.Call)
            and (self.dotted(node.func) or "").startswith("time.")
        }
        self.assertLessEqual(time_calls, {"time.monotonic"})

    def test_the_rig_never_writes_a_receipt_or_ranks_products(self):
        for name in (
            "build_activation_receipt", "write_activation_receipt",
            "upsert_activation", "--manual-reviewed", "--approve-runtime",
        ):
            self.assertNotIn(name, SOURCE, name)
        for name in ("winner", "rank(", "fastest"):
            self.assertNotIn(name, SOURCE.casefold(), name)

    def test_attestation_default_is_false_in_the_signature(self):
        function = next(
            node for node in ast.walk(TREE)
            if isinstance(node, ast.FunctionDef)
            and node.name == "build_trace_report")
        defaults = {
            keyword.arg: keyword
            for keyword in function.args.kwonlyargs
        }
        self.assertIn("operator_attested", defaults)
        index = [item.arg for item in function.args.kwonlyargs].index(
            "operator_attested")
        default = function.args.kw_defaults[index]
        self.assertIsInstance(default, ast.Constant)
        self.assertIs(default.value, False)


class TasksCommandTests(RigHarness):
    def test_tasks_prints_every_protocol_task(self):
        code, output = self.run_main(
            ["tasks", "--protocol", str(PROTOCOL_PATH)])
        self.assertEqual(code, 0)
        corpus = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        for task in corpus["tasks"]:
            self.assertIn(task["task_id"], output)
            self.assertIn(task["completion_rule"], output)


if __name__ == "__main__":
    unittest.main()
