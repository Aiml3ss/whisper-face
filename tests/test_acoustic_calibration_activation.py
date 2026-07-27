# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acoustic_calibration_activation import (  # noqa: E402
    CONDITIONS,
    ActivationError,
    build_activation_receipt,
    load_activation_receipt,
    validate_activation_receipt,
)
from benchmark_acoustic_calibration_activation import (  # noqa: E402
    MANIFEST_KIND,
    BenchmarkError,
    evaluate,
    load_manifest,
    main,
)


def telemetry_record(**changes):
    record = {
        "adaptive_threshold": 0.014,
        "clipped_ratio": 0.0,
        "derived_gain_factor": 1.35,
        "duration_ms": 2000.0,
        "frame_rms_p20": 0.006,
        "frame_rms_p50": 0.022,
        "frame_rms_p95": 0.062,
        "nonfinite_ratio": 0.0,
        "peak_amplitude": 0.42,
        "peak_rms": 0.075,
        "rms": 0.034,
        "sample_count": 32000.0,
        "sample_rate_hz": 16000.0,
        "silence_ratio": 0.24,
        "trailing_silence_ms": 240.0,
        "voiced_fraction": 0.62,
    }
    record.update(changes)
    return record


class AcousticCalibrationActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest_path = self.root / "private-manifest.json"
        self.activation_path = (
            self.root / "acoustic_calibration_activation.json")

    @staticmethod
    def manifest(*, regress=False, improvements=3):
        cases = []
        index = 0
        for condition in CONDITIONS:
            for _ in range(10):
                improved = index < improvements
                cases.append({
                    "case_token": f"case-{index:016x}",
                    "evidence_source": "physical-caller-attested",
                    "condition": condition,
                    "baseline": {
                        "recognition_correct": not improved,
                        "endpoint_correct": True,
                    },
                    "candidate": {
                        "recognition_correct": (
                            False if regress and index == 10 else True),
                        "endpoint_correct": True,
                    },
                })
                index += 1
        return {
            "schema_version": 1,
            "kind": MANIFEST_KIND,
            "telemetry": [
                telemetry_record(
                    derived_gain_factor=1.25 + item * 0.02,
                    trailing_silence_ms=210 + item * 10,
                )
                for item in range(8)
            ],
            "cases": cases,
        }

    def write_manifest(self, value):
        self.manifest_path.write_text(
            json.dumps(value), encoding="utf-8")

    def test_balanced_gain_without_regression_can_build_receipt(self):
        self.write_manifest(self.manifest())
        report = evaluate(load_manifest(self.manifest_path))
        receipt = build_activation_receipt(
            report, manual_review_approved=True)
        status = validate_activation_receipt(receipt)

        self.assertTrue(report["activation_candidate"])
        self.assertEqual(report["verdict"], "manual-review-required")
        self.assertTrue(status.ready)
        self.assertIsNotNone(status.settings)
        self.assertIsNone(receipt["settings"]["reverb"])
        encoded = json.dumps(receipt)
        self.assertNotIn("case-", encoded)
        self.assertNotIn("telemetry", encoded)

    def test_regression_and_no_gain_fail_closed(self):
        for manifest in (
                self.manifest(regress=True),
                self.manifest(improvements=0)):
            with self.subTest():
                self.write_manifest(manifest)
                report = evaluate(load_manifest(self.manifest_path))
                self.assertFalse(report["activation_candidate"])
                with self.assertRaises(ActivationError):
                    build_activation_receipt(
                        report, manual_review_approved=True)

    def test_manual_review_and_strict_receipt_are_required(self):
        self.write_manifest(self.manifest())
        report = evaluate(load_manifest(self.manifest_path))
        with self.assertRaisesRegex(ActivationError, "manual review"):
            build_activation_receipt(
                report, manual_review_approved=False)
        receipt = build_activation_receipt(
            report, manual_review_approved=True)
        receipt["settings"]["reverb"] = 0.5
        self.assertFalse(validate_activation_receipt(receipt).ready)

    def test_cli_writes_private_receipt_only_after_confirmation(self):
        self.write_manifest(self.manifest())
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            refused = main([
                str(self.manifest_path),
                "--approve-runtime", str(self.activation_path),
            ])
        self.assertEqual(refused, 2)
        self.assertFalse(self.activation_path.exists())

        with redirect_stdout(stdout), redirect_stderr(stderr):
            accepted = main([
                str(self.manifest_path),
                "--approve-runtime", str(self.activation_path),
                "--confirm-manual-review",
            ])
        self.assertEqual(accepted, 0)
        if os.name == "posix":
            self.assertEqual(
                os.stat(self.activation_path).st_mode & 0o777, 0o600)
        self.assertTrue(load_activation_receipt(
            self.activation_path).ready)
        self.assertNotIn("case-", stdout.getvalue())

    def test_private_or_malformed_case_fields_are_rejected_without_echo(self):
        manifest = self.manifest()
        manifest["cases"][0]["private_text"] = "do not print"
        self.write_manifest(manifest)

        with self.assertRaisesRegex(BenchmarkError, "invalid"):
            load_manifest(self.manifest_path)


if __name__ == "__main__":
    unittest.main()
