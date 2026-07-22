# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acoustic_calibration import (  # noqa: E402
    ACOUSTIC_TELEMETRY_FIELDS,
    END_SILENCE_BOUNDS_MS,
    GAIN_CEILING_BOUNDS,
    MAX_RECORDS,
    NOISE_GATE_BOUNDS,
    VAD_THRESHOLD_BOUNDS,
    recommend_calibration,
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


class AcousticCalibrationPolicyTests(unittest.TestCase):
    def test_policy_schema_matches_both_existing_closed_trace_schemas(self):
        self.assertEqual(
            tuple(ACOUSTIC_TELEMETRY_FIELDS),
            self._literal_schema(ROOT / "dictate.py", "PERFORMANCE_TRACE_SCHEMAS"),
        )
        self.assertEqual(
            tuple(ACOUSTIC_TELEMETRY_FIELDS),
            self._literal_schema(ROOT / "performance_lab.py", "RUNTIME_TRACE_SCHEMAS"),
        )

    @staticmethod
    def _literal_schema(path, variable):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mapping = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == variable
                    for target in node.targets)
        )
        return mapping["utterance_acoustic"]

    def test_clean_separated_numeric_evidence_emits_bounded_candidates_only(self):
        records = [telemetry_record(
            derived_gain_factor=1.25 + index * 0.05,
            trailing_silence_ms=200 + index * 20,
        ) for index in range(8)]

        report = recommend_calibration(records)

        self.assertEqual(report["verdict"], "keep")
        self.assertEqual(report["runtime_effect"], "none")
        self.assertFalse(report["activation_claim"])
        self.assertFalse(report["quality_claim"])
        self.assertFalse(report["evidence"]["physical_conditions_verified"])
        decisions = report["decisions"]
        self.assertLessEqual(
            decisions["gain_ceiling"]["value"] * 0.42, 0.85)
        for control, bounds in (
                ("gain_ceiling", GAIN_CEILING_BOUNDS),
                ("noise_gate", NOISE_GATE_BOUNDS),
                ("vad_threshold", VAD_THRESHOLD_BOUNDS),
                ("end_silence", END_SILENCE_BOUNDS_MS)):
            self.assertEqual(decisions[control]["state"], "recommend")
            self.assertGreaterEqual(decisions[control]["value"], bounds[0])
            self.assertLessEqual(decisions[control]["value"], bounds[1])
        self.assertEqual(decisions["reverb"], {
            "state": "unavailable",
            "value": None,
            "reason": "reverb-metric-unavailable",
        })

    def test_malformed_nonfinite_and_clipping_evidence_are_killed(self):
        cases = (
            ([telemetry_record(secret="must not escape")] * 8,
             "invalid-telemetry"),
            ([telemetry_record(frame_rms_p20=float("nan"))] * 8,
             "invalid-telemetry"),
            ([telemetry_record(nonfinite_ratio=0.0001)] * 8,
             "nonfinite-samples-observed"),
            ([telemetry_record(clipped_ratio=0.000001)] * 8,
             "clipping-observed"),
            ([telemetry_record(peak_amplitude=0.99)] * 8,
             "clipping-observed"),
            ([telemetry_record()] * (MAX_RECORDS + 1),
             "telemetry-batch-out-of-bounds"),
        )
        for records, reason in cases:
            with self.subTest(reason=reason):
                report = recommend_calibration(records)
                self.assertEqual(report["verdict"], "kill")
                self.assertEqual(report["reason"], reason)
                self.assertTrue(all(
                    decision["state"] in {"refused", "unavailable"}
                    for decision in report["decisions"].values()
                ))
                self.assertNotIn("secret", json.dumps(report))

    def test_silence_noise_quiet_speech_and_short_evidence_fail_closed(self):
        cases = (
            ([telemetry_record()] * 2, "minimum-evidence-not-met"),
            ([telemetry_record(
                silence_ratio=1.0, voiced_fraction=0.0,
                frame_rms_p20=0.0, frame_rms_p50=0.0,
                frame_rms_p95=0.0, peak_rms=0.0,
            )] * 8, "silence-ambiguous"),
            ([telemetry_record(
                frame_rms_p20=0.026, frame_rms_p50=0.035,
                frame_rms_p95=0.045,
            )] * 8, "noise-separation-ambiguous"),
            ([telemetry_record(
                frame_rms_p20=0.002, frame_rms_p50=0.006,
                frame_rms_p95=0.012, peak_rms=0.011,
            )] * 8, "quiet-speech-ambiguous"),
            ([telemetry_record(trailing_silence_ms=0.0)] * 8,
             "end-silence-ambiguous"),
        )
        for records, reason in cases:
            with self.subTest(reason=reason):
                report = recommend_calibration(records)
                self.assertEqual(report["verdict"], "insufficient-evidence")
                self.assertEqual(report["reason"], reason)
                self.assertFalse(report["activation_claim"])
                self.assertFalse(report["quality_claim"])

    def test_peak_above_supported_gain_headroom_fails_closed(self):
        report = recommend_calibration([
            telemetry_record(peak_amplitude=0.95) for _ in range(8)
        ])

        self.assertEqual(report["verdict"], "insufficient-evidence")
        self.assertEqual(report["reason"], "gain-headroom-ambiguous")
        self.assertTrue(all(
            decision["state"] in {"refused", "unavailable"}
            for decision in report["decisions"].values()
        ))

    def test_inconsistent_duration_and_boolean_numbers_are_invalid(self):
        for record in (
                telemetry_record(duration_ms=1999.0),
                telemetry_record(sample_count=True),
                telemetry_record(
                    duration_ms=1_000_000.0,
                    sample_count=16_000_000.0),
                telemetry_record(sample_rate_hz=1_000_000.0)):
            with self.subTest(record=record):
                report = recommend_calibration([record] * 8)
                self.assertEqual(report["verdict"], "kill")
                self.assertEqual(report["reason"], "invalid-telemetry")


if __name__ == "__main__":
    unittest.main()
