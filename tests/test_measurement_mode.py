# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Measurement mode must measure the real path and authorize nothing."""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import measurement_mode as mm  # noqa: E402
from acoustic_calibration import (  # noqa: E402
    END_SILENCE_BOUNDS_MS,
    GAIN_CEILING_BOUNDS,
    NOISE_GATE_BOUNDS,
    VAD_THRESHOLD_BOUNDS,
)
from acoustic_calibration_activation import (  # noqa: E402
    ActivationError as CalibrationActivationError,
    build_activation_receipt as build_calibration_receipt,
    validate_activation_receipt as validate_calibration_receipt,
)
from acoustic_keyword_activation import (  # noqa: E402
    ActivationError as KeywordActivationError,
    build_activation_entry,
    validate_state as validate_keyword_state,
)
from acoustic_keyword_bias_evaluation import (  # noqa: E402
    evaluate_keyword_bias,
)
from acoustic_keyword_memory import AcousticKeywordMemory  # noqa: E402
from benchmark_acoustic_calibration_activation import (  # noqa: E402
    BenchmarkError as CalibrationBenchmarkError,
    evaluate as evaluate_calibration,
    load_manifest as load_calibration_manifest,
)
from benchmark_acoustic_keyword_bias import synthetic_cases  # noqa: E402
from delayed_cleanup_activation import evaluate_activation  # noqa: E402

GOOD_CALIBRATION = "calibration:gain=2.5,noise=0.008,vad=0.012,end-silence=280"


class ParsingTests(unittest.TestCase):
    def test_no_argument_leaves_every_arm_off(self):
        mode = mm.parse_measurement_mode(["dictate.py"])
        self.assertFalse(mode.active)
        self.assertEqual(mode.arms, ())
        self.assertEqual(mode.refusals, ())
        self.assertEqual(mode.banner(), ())

    def test_each_arm_parses_and_reports_itself(self):
        mode = mm.parse_measurement_mode([
            "dictate.py",
            "--measure", GOOD_CALIBRATION,
            "--measure=keyword:Qwen",
            "--measure", "delayed-cleanup",
        ])
        self.assertTrue(mode.active)
        self.assertEqual(
            mode.arms, ("calibration", "keyword", "delayed-cleanup"))
        self.assertEqual(mode.calibration, mm.MeasuredCalibration(
            2.5, 0.008, 0.012, 280))
        self.assertEqual(mode.keyword, "Qwen")
        self.assertIs(mode.delayed_cleanup, True)
        self.assertEqual(mode.labels, (
            mm.CALIBRATION_LABEL, mm.KEYWORD_LABEL, mm.DELAYED_CLEANUP_LABEL))

    def test_one_malformed_argument_disables_every_arm(self):
        # Fail-closed, exactly like a malformed receipt: a half-configured
        # session would measure something other than what the operator thinks.
        mode = mm.parse_measurement_mode([
            "dictate.py",
            "--measure", "delayed-cleanup",
            "--measure", "calibration:gain=nonsense",
        ])
        self.assertFalse(mode.active)
        self.assertIs(mode.delayed_cleanup, False)
        self.assertTrue(mode.refusals)
        self.assertIn("[measurement] every arm is off", "\n".join(
            mode.banner()))

    def test_out_of_policy_settings_are_refused(self):
        for spec in (
            f"calibration:gain={GAIN_CEILING_BOUNDS[1] + 1},"
            "noise=0.008,vad=0.012,end-silence=280",
            "calibration:gain=2.5,"
            f"noise={NOISE_GATE_BOUNDS[0] / 2},vad=0.012,end-silence=280",
            "calibration:gain=2.5,noise=0.008,"
            f"vad={VAD_THRESHOLD_BOUNDS[1] * 2},end-silence=280",
            "calibration:gain=2.5,noise=0.008,vad=0.012,"
            f"end-silence={END_SILENCE_BOUNDS_MS[1] + 1}",
            # noise must stay below vad, the receipt validator's own rule
            "calibration:gain=2.5,noise=0.02,vad=0.012,end-silence=280",
            "calibration:gain=2.5,noise=0.008,vad=0.012,end-silence=280.5",
            "calibration:gain=2.5,noise=0.008,vad=0.012",
            "calibration:",
            "keyword:",
            "keyword:" + "x" * 200,
            "delayed-cleanup:replay-proposal-id",
            "everything",
            "",
        ):
            with self.subTest(spec=spec):
                mode = mm.parse_measurement_mode(["dictate.py", "--measure", spec])
                self.assertFalse(mode.active, spec)
                self.assertTrue(mode.refusals, spec)

    def test_a_valueless_flag_is_refused(self):
        mode = mm.parse_measurement_mode(["dictate.py", "--measure"])
        self.assertFalse(mode.active)
        self.assertTrue(mode.refusals)

    def test_a_repeated_arm_is_refused_rather_than_last_wins(self):
        mode = mm.parse_measurement_mode([
            "dictate.py", "--measure", "keyword:Qwen",
            "--measure", "keyword:Gwen"])
        self.assertFalse(mode.active)
        self.assertTrue(mode.refusals)

    def test_status_names_the_arms_but_never_the_keyword(self):
        mode = mm.parse_measurement_mode(
            ["dictate.py", "--measure", "keyword:SecretProjectName"])
        status = mode.status_snapshot()
        encoded = json.dumps(status) + "\n".join(mode.banner())
        self.assertNotIn("SecretProjectName", encoded)
        self.assertIs(status["active"], True)
        self.assertIs(status["grants_authority"], False)
        self.assertIs(status["persisted"], False)
        self.assertEqual(status["scope"], "process-session-only")
        self.assertIn("no runtime authority", status["summary"])


class EvidenceLabelTests(unittest.TestCase):
    def test_absent_means_the_ordinary_path(self):
        for arm in mm.ARM_LABELS:
            self.assertEqual(
                mm.evidence_label(None, arm=arm), mm.ORDINARY_PATH)

    def test_an_artifact_cannot_claim_another_arm(self):
        with self.assertRaises(mm.MeasurementModeError):
            mm.evidence_label(mm.KEYWORD_LABEL, arm=mm.CALIBRATION_LABEL)
        for bogus in ("yes", True, 1, "measured", ""):
            with self.subTest(value=bogus), self.assertRaises(
                    mm.MeasurementModeError):
                mm.evidence_label(bogus, arm=mm.CALIBRATION_LABEL)

    def test_only_arm_labels_count_as_measured(self):
        self.assertFalse(mm.used_measurement_mode(mm.ORDINARY_PATH))
        self.assertFalse(mm.used_measurement_mode(None))
        for label in mm.ARM_LABELS:
            self.assertTrue(mm.used_measurement_mode(label))


class NoAuthorityTests(unittest.TestCase):
    """There must be no code path from measurement mode to a receipt."""

    SOURCE = ROOT / "measurement_mode.py"
    FORBIDDEN_IMPORTS = {
        "acoustic_calibration_activation",
        "acoustic_keyword_activation",
        "delayed_cleanup_activation",
        "relisten_activation",
        "os",
        "pathlib",
        "tempfile",
        "shutil",
        "subprocess",
        "runpy",
        "json",
        "pickle",
    }
    FORBIDDEN_NAMES = {
        "open", "write_activation_receipt", "build_activation_receipt",
        "build_activation_entry", "upsert_activation", "evaluate_activation",
        "validate_activation_receipt", "validate_state", "eval", "exec",
    }

    def tree(self):
        return ast.parse(self.SOURCE.read_text(encoding="utf-8"))

    def test_it_imports_nothing_that_could_grant_authority(self):
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".")[0], self.FORBIDDEN_IMPORTS)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    (node.module or "").split(".")[0], self.FORBIDDEN_IMPORTS)

    def test_it_calls_nothing_that_could_write_or_install_a_receipt(self):
        for node in ast.walk(self.tree()):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (function.id if isinstance(function, ast.Name)
                    else function.attr if isinstance(function, ast.Attribute)
                    else None)
            self.assertNotIn(name, self.FORBIDDEN_NAMES)

    def test_it_cannot_construct_the_type_the_runtime_treats_as_proof(self):
        # The runtime gates on `CalibrationSettings`. This module carries its
        # own carrier type instead, so it cannot hand the runtime the thing
        # that reads as proof; the conversion is explicit at the point of use.
        referenced = {
            node.id for node in ast.walk(self.tree())
            if isinstance(node, ast.Name)}
        self.assertNotIn("CalibrationSettings", referenced)
        self.assertNotIn("activation.json", self.SOURCE.read_text(
            encoding="utf-8"))

    def test_a_measured_session_installs_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = sorted(root.iterdir())
            mode = mm.parse_measurement_mode([
                "dictate.py", "--measure", GOOD_CALIBRATION,
                "--measure", "keyword:Qwen",
                "--measure", "delayed-cleanup"])
            mode.status_snapshot()
            mode.banner()
            self.assertEqual(sorted(root.iterdir()), before)
            self.assertEqual(before, [])


class CalibrationGateTests(unittest.TestCase):
    """A measured calibration corpus is acceptable and visibly measured."""

    @staticmethod
    def manifest(measurement=None):
        cases = []
        for index in range(40):
            condition = ("clean", "quiet", "noisy", "long-pause")[index % 4]
            improved = index < 3
            cases.append({
                "case_token": f"case-{index:016x}",
                "evidence_source": "physical-caller-attested",
                "condition": condition,
                "baseline": {"recognition_correct": not improved,
                             "endpoint_correct": True},
                "candidate": {"recognition_correct": True,
                              "endpoint_correct": True},
            })
        telemetry = [{
            "adaptive_threshold": 0.014, "clipped_ratio": 0.0,
            "derived_gain_factor": 1.25 + item * 0.02, "duration_ms": 2000.0,
            "frame_rms_p20": 0.006, "frame_rms_p50": 0.022,
            "frame_rms_p95": 0.062, "nonfinite_ratio": 0.0,
            "peak_amplitude": 0.42, "peak_rms": 0.075, "rms": 0.034,
            "sample_count": 32000.0, "sample_rate_hz": 16000.0,
            "silence_ratio": 0.24, "trailing_silence_ms": 210 + item * 10,
            "voiced_fraction": 0.62,
        } for item in range(8)]
        manifest = {
            "schema_version": 1,
            "kind": "whisper-face/acoustic-calibration-activation-manifest",
            "telemetry": telemetry,
            "cases": cases,
        }
        if measurement is not None:
            manifest[mm.EVIDENCE_KEY] = measurement
        return manifest

    def load(self, manifest):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            return load_calibration_manifest(path)

    def test_a_measured_candidate_pass_still_earns_a_receipt(self):
        report = evaluate_calibration(
            self.load(self.manifest(mm.CALIBRATION_LABEL)))
        receipt = build_calibration_receipt(
            report, manual_review_approved=True)
        status = validate_calibration_receipt(receipt)
        self.assertTrue(status.ready)
        self.assertEqual(receipt["measurement_mode"], mm.CALIBRATION_LABEL)
        self.assertEqual(status.measurement_mode, mm.CALIBRATION_LABEL)

    def test_an_unlabelled_manifest_reads_as_the_ordinary_path(self):
        report = evaluate_calibration(self.load(self.manifest()))
        receipt = build_calibration_receipt(
            report, manual_review_approved=True)
        self.assertEqual(receipt["measurement_mode"], mm.ORDINARY_PATH)
        self.assertTrue(validate_calibration_receipt(receipt).ready)

    def test_a_receipt_without_the_disclosure_is_refused(self):
        report = evaluate_calibration(
            self.load(self.manifest(mm.CALIBRATION_LABEL)))
        receipt = build_calibration_receipt(
            report, manual_review_approved=True)
        stripped = {key: value for key, value in receipt.items()
                    if key != "measurement_mode"}
        self.assertFalse(validate_calibration_receipt(stripped).ready)
        self.assertFalse(validate_calibration_receipt(
            {**receipt, "measurement_mode": mm.KEYWORD_LABEL}).ready)

    def test_a_manifest_with_a_foreign_label_is_refused(self):
        with self.assertRaises(CalibrationBenchmarkError):
            self.load(self.manifest(mm.DELAYED_CLEANUP_LABEL))

    def test_measurement_mode_does_not_soften_any_other_threshold(self):
        manifest = self.manifest(mm.CALIBRATION_LABEL)
        for case in manifest["cases"]:
            case["candidate"]["recognition_correct"] = True
            case["baseline"]["recognition_correct"] = True
        report = evaluate_calibration(self.load(manifest))
        self.assertFalse(report["activation_candidate"])
        with self.assertRaises(CalibrationActivationError):
            build_calibration_receipt(report, manual_review_approved=True)

    def test_manual_review_is_still_required_for_measured_evidence(self):
        report = evaluate_calibration(
            self.load(self.manifest(mm.CALIBRATION_LABEL)))
        with self.assertRaisesRegex(
                CalibrationActivationError, "manual review"):
            build_calibration_receipt(report, manual_review_approved=False)


class KeywordGateTests(unittest.TestCase):
    def setUp(self):
        memory = AcousticKeywordMemory()
        for index in range(3):
            self.candidate = memory.accept_explicit_correction(
                "PrivateProjectName", evidence_id=f"correction-{index}")
        self.memory = memory
        self.records = next(
            rows for name, _expected, rows in synthetic_cases()
            if name == "constructed-physical-gain")

    def report(self, measurement):
        return {
            **evaluate_keyword_bias(self.candidate, self.records),
            mm.EVIDENCE_KEY: measurement,
        }

    def test_a_measured_biased_pass_still_activates_and_says_so(self):
        entry = build_activation_entry(
            self.candidate, self.report(mm.KEYWORD_LABEL),
            manual_review_approved=True)
        self.assertEqual(entry["measurement_mode"], mm.KEYWORD_LABEL)
        state = validate_keyword_state({
            "schema_version": 1,
            "kind": "whisper-face/acoustic-keyword-activation",
            "runtime_effect": "prompt-priority",
            "entries": [entry],
        })
        self.assertEqual(len(state["entries"]), 1)

    def test_an_entry_without_the_disclosure_is_refused(self):
        entry = build_activation_entry(
            self.candidate, self.report(mm.ORDINARY_PATH),
            manual_review_approved=True)
        for broken in (
            {key: value for key, value in entry.items()
             if key != "measurement_mode"},
            {**entry, "measurement_mode": mm.CALIBRATION_LABEL},
            {**entry, "measurement_mode": True},
        ):
            with self.subTest(), self.assertRaises(KeywordActivationError):
                validate_keyword_state({
                    "schema_version": 1,
                    "kind": "whisper-face/acoustic-keyword-activation",
                    "runtime_effect": "prompt-priority",
                    "entries": [broken],
                })

    def test_synthetic_evidence_is_still_refused_under_measurement_mode(self):
        synthetic = next(
            rows for name, _expected, rows in synthetic_cases()
            if name == "synthetic-gain")
        report = {
            **evaluate_keyword_bias(self.candidate, synthetic),
            mm.EVIDENCE_KEY: mm.KEYWORD_LABEL,
        }
        with self.assertRaises(KeywordActivationError):
            build_activation_entry(
                self.candidate, report, manual_review_approved=True)

    def test_a_foreign_label_cannot_enter_an_entry(self):
        with self.assertRaises(KeywordActivationError):
            build_activation_entry(
                self.candidate, self.report(mm.DELAYED_CLEANUP_LABEL),
                manual_review_approved=True)


class DelayedCleanupGateTests(unittest.TestCase):
    @staticmethod
    def records(measurement, count=52):
        surfaces = ("native-text", "web-text", "electron-editor",
                    "terminal-editor")
        scenarios = ("unchanged", "edit-elsewhere", "edit-overlap",
                     "focus-drift")
        rows = []
        for index in range(count):
            scenario = scenarios[index % len(scenarios)]
            expected = {
                "unchanged": "applied", "edit-elsewhere": "applied",
                "edit-overlap": "no_safe_changes",
                "focus-drift": "focus_drift"}[scenario]
            row = {
                "id": f"physical-{index:03d}",
                "source": "caller-attested-physical",
                "surface": surfaces[index % len(surfaces)],
                "scenario": scenario,
                "expected_outcome": expected,
                "actual_outcome": expected,
                "wrong_target_write": False,
                "user_edit_overwritten": False,
                "selection_disrupted": False,
                "duplicate_write": False,
                "apply_ms": 25.0 + index % 10,
            }
            if measurement is not None:
                row[mm.EVIDENCE_KEY] = measurement
            rows.append(row)
        return rows

    def test_a_fully_measured_corpus_activates_and_discloses_the_count(self):
        receipt = evaluate_activation(
            self.records(mm.DELAYED_CLEANUP_LABEL), manual_reviewed=True)
        self.assertIs(receipt["active"], True)
        self.assertEqual(receipt["measurement_mode_cases"], 52)

    def test_an_unlabelled_corpus_reads_as_ordinary(self):
        receipt = evaluate_activation(
            self.records(None), manual_reviewed=True)
        self.assertEqual(receipt["measurement_mode_cases"], 0)

    def test_a_foreign_label_is_a_schema_violation(self):
        with self.assertRaisesRegex(ValueError, "measurement"):
            evaluate_activation(
                self.records(mm.KEYWORD_LABEL), manual_reviewed=True)

    def test_measurement_mode_softens_no_other_check(self):
        rows = self.records(mm.DELAYED_CLEANUP_LABEL)
        rows[0]["wrong_target_write"] = True
        receipt = evaluate_activation(rows, manual_reviewed=True)
        self.assertIs(receipt["active"], False)
        self.assertEqual(receipt["reason"], "wrong-target-write")

        slow = self.records(mm.DELAYED_CLEANUP_LABEL)
        for row in slow:
            row["apply_ms"] = 400.0
        self.assertEqual(
            evaluate_activation(slow, manual_reviewed=True)["reason"],
            "apply-latency-budget-exceeded")

        short = self.records(mm.DELAYED_CLEANUP_LABEL, count=20)
        self.assertEqual(
            evaluate_activation(short, manual_reviewed=True)["reason"],
            "minimum-physical-evidence-not-met")

        self.assertEqual(
            evaluate_activation(
                self.records(mm.DELAYED_CLEANUP_LABEL),
                manual_reviewed=False)["reason"],
            "manual-review-required")

    def test_synthetic_evidence_is_still_refused(self):
        rows = self.records(mm.DELAYED_CLEANUP_LABEL)
        rows[0]["source"] = "synthetic"
        with self.assertRaisesRegex(ValueError, "physical"):
            evaluate_activation(rows, manual_reviewed=True)


class RuntimeApplicationTests(unittest.TestCase):
    """Measurement mode must drive the real runtime helpers, not copies."""

    def namespace(self, *names, **extra):
        source = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))
        selected = [node for node in source.body
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                    and node.name in names]
        self.assertEqual(
            len(selected), len(names),
            f"missing production definitions: "
            f"{set(names) - {node.name for node in selected}}")
        module = ast.fix_missing_locations(ast.Module(
            body=[ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")], level=0), *selected],
            type_ignores=[]))
        namespace = dict(extra)
        exec(compile(module, "dictate-measured", "exec"), namespace)
        return namespace

    def test_calibration_settings_come_from_the_arm_when_it_is_on(self):
        from acoustic_calibration_activation import CalibrationSettings
        mode = mm.parse_measurement_mode(
            ["dictate.py", "--measure", GOOD_CALIBRATION])
        ns = self.namespace(
            "active_calibration_settings", "acoustic_calibration_source",
            "acoustic_calibration_status_snapshot",
            "calibrated_vad_threshold", "calibrated_end_silence_seconds",
            MEASUREMENT_MODE=mode,
            CalibrationSettings=CalibrationSettings,
            SILENCE_RMS=0.008, TAIL_SKIP_SILENCE=0.12,
            ACOUSTIC_CALIBRATION_STATE={
                "settings": None, "status": "receipt-missing"})
        settings = ns["active_calibration_settings"]()

        self.assertEqual(settings, CalibrationSettings(2.5, 0.008, 0.012, 280))
        # The same helpers the capture path already calls, not a copy of them.
        self.assertEqual(ns["calibrated_vad_threshold"](), 0.012)
        self.assertEqual(ns["calibrated_end_silence_seconds"](), 0.28)
        status = ns["acoustic_calibration_status_snapshot"]()
        # `enabled` is receipt-only so nothing can read the override as proof.
        self.assertIs(status["enabled"], False)
        self.assertIs(status["applied"], True)
        self.assertEqual(status["source"], "measurement-mode")
        self.assertEqual(
            status["controls"], ("gain", "noise", "vad", "end-silence"))

    def test_an_inert_mode_leaves_the_defaults_exactly_as_they_were(self):
        from acoustic_calibration_activation import CalibrationSettings
        ns = self.namespace(
            "active_calibration_settings", "acoustic_calibration_source",
            "acoustic_calibration_status_snapshot",
            "calibrated_vad_threshold", "calibrated_end_silence_seconds",
            MEASUREMENT_MODE=mm.parse_measurement_mode(["dictate.py"]),
            CalibrationSettings=CalibrationSettings,
            SILENCE_RMS=0.008, TAIL_SKIP_SILENCE=0.12,
            ACOUSTIC_CALIBRATION_STATE={
                "settings": None, "status": "receipt-missing"})
        self.assertIsNone(ns["active_calibration_settings"]())
        self.assertEqual(ns["calibrated_vad_threshold"](), 0.008)
        self.assertEqual(ns["calibrated_end_silence_seconds"](), 0.12)
        status = ns["acoustic_calibration_status_snapshot"]()
        self.assertIs(status["applied"], False)
        self.assertEqual(status["source"], "defaults")
        self.assertEqual(status["controls"], ())

    def test_the_measured_keyword_reaches_the_prompt_hints(self):
        mode = mm.parse_measurement_mode(
            ["dictate.py", "--measure", "keyword:Qwen"])
        ns = self.namespace("measured_keyword_hints", MEASUREMENT_MODE=mode)
        self.assertEqual(ns["measured_keyword_hints"](()), ("Qwen",))
        # Never duplicated when a receipt already activated the same term.
        self.assertEqual(ns["measured_keyword_hints"](("qwen",)), ("qwen",))
        self.assertEqual(
            ns["measured_keyword_hints"](("Other",)), ("Qwen", "Other"))

    def test_no_keyword_arm_leaves_the_receipt_hints_untouched(self):
        ns = self.namespace(
            "measured_keyword_hints",
            MEASUREMENT_MODE=mm.parse_measurement_mode(["dictate.py"]))
        self.assertEqual(ns["measured_keyword_hints"](("Approved",)),
                         ("Approved",))
        self.assertEqual(ns["measured_keyword_hints"](()), ())

    def test_delayed_cleanup_schedules_without_ever_setting_active(self):
        import threading
        mode = mm.parse_measurement_mode(
            ["dictate.py", "--measure", "delayed-cleanup"])
        state = {"active": False, "status": "missing", "generation": 0,
                 "lock": threading.Lock()}
        starts = []
        ns = self.namespace(
            "delayed_cleanup_activation_status",
            "delayed_cleanup_scheduling_enabled", "schedule_delayed_cleanup",
            MEASUREMENT_MODE=mode, IS_MACOS=True,
            DELAYED_CLEANUP_STATE=state, PIPELINE_STATE={},
            _run_delayed_cleanup=object())

        status = ns["delayed_cleanup_activation_status"]()
        scheduled = ns["schedule_delayed_cleanup"](
            "proposal-1", "original", "compiled", "tone", SimpleNamespace(),
            continuing=False, context_tail="", context_text=None,
            tone_key="formal", snippet_restore={},
            starter=lambda target, args: starts.append(args))

        self.assertIs(scheduled, True)
        self.assertEqual(len(starts), 1)
        # The receipt-backed flag is never written by measurement mode.
        self.assertIs(status["active"], False)
        self.assertIs(status["measurement_mode"], True)
        self.assertIs(status["scheduling"], True)
        self.assertIs(state["active"], False)
        self.assertEqual(state["status"], "missing")

    def test_measurement_mode_is_off_for_delayed_cleanup_off_mac(self):
        import threading
        mode = mm.parse_measurement_mode(
            ["dictate.py", "--measure", "delayed-cleanup"])
        ns = self.namespace(
            "delayed_cleanup_activation_status",
            "delayed_cleanup_scheduling_enabled",
            MEASUREMENT_MODE=mode, IS_MACOS=False,
            DELAYED_CLEANUP_STATE={
                "active": False, "status": "unsupported_platform",
                "generation": 0, "lock": threading.Lock()})
        self.assertIs(ns["delayed_cleanup_scheduling_enabled"](), False)

    def test_the_runtime_reads_the_arguments_once_and_never_persists_them(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertEqual(
            source.count("MEASUREMENT_MODE = parse_measurement_mode"), 1)
        self.assertIn("parse_measurement_mode(sys.argv)", source)
        # Nothing writes the mode anywhere, so it cannot outlive the process:
        # no preference key, no state file, no receipt path.
        self.assertNotIn('PREFERENCES["measurement', source)
        self.assertNotIn("measurement_mode.json", source)
        self.assertNotIn("MEASUREMENT_MODE.calibration =", source)
        # And it never feeds an activation writer.
        for writer in ("write_activation_receipt", "build_activation_receipt",
                       "build_activation_entry", "upsert_activation"):
            self.assertNotIn(writer, source, writer)


class MenuSurfaceTests(unittest.TestCase):
    def title(self, mode):
        source = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))
        selected = [node for node in source.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "measurement_menu_title"]
        namespace = {"MEASUREMENT_MODE": mode}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=selected, type_ignores=[])), "menu", "exec"), namespace)
        return namespace["measurement_menu_title"]()

    def test_an_active_session_announces_itself_without_the_keyword(self):
        title = self.title(mm.parse_measurement_mode([
            "dictate.py", "--measure", "keyword:SecretProjectName",
            "--measure", "delayed-cleanup"]))
        self.assertIn("Measurement mode", title)
        self.assertIn("keyword", title)
        self.assertIn("delayed-cleanup", title)
        self.assertIn("evidence only", title)
        self.assertNotIn("SecretProjectName", title)

    def test_an_inert_session_says_it_is_off(self):
        self.assertEqual(
            self.title(mm.parse_measurement_mode(["dictate.py"])),
            "Measurement mode off")


if __name__ == "__main__":
    unittest.main()
