# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///

import json
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_macos_asr_warm_path as warm_path  # noqa: E402
from benchmark_macos_asr_warm_path import (  # noqa: E402
    MIN_SAMPLES,
    _synthetic_audio,
    _writev_all,
    build_report,
)


def timings(wall, native=80.0, overhead=20.0, prep=0.1):
    return [{
        "wall_ms": float(wall),
        "native_ms": native,
        "client_overhead_ms": overhead,
        "preparation_ms": prep,
    } for _index in range(MIN_SAMPLES)]


class MacASRWarmPathBenchmarkTests(unittest.TestCase):
    def test_partial_writev_consumes_every_byte_without_copy_contract(self):
        received = bytearray()

        def writer(_descriptor, chunks):
            available = b"".join(bytes(chunk) for chunk in chunks)
            count = min(3, len(available))
            received.extend(available[:count])
            return count

        _writev_all(7, (b"header", b"payload"), writer=writer)
        self.assertEqual(received, b"headerpayload")

    def test_no_progress_write_fails_closed(self):
        with self.assertRaisesRegex(OSError, "no progress"):
            _writev_all(7, (b"payload",), writer=lambda *_args: 0)

    def test_writev_resolution_is_lazy_and_injected_writer_is_portable(self):
        received = bytearray()

        def writer(_descriptor, chunks):
            received.extend(bytes(chunks[0]))
            return len(chunks[0])

        parameter = inspect.signature(_writev_all).parameters["writer"]
        self.assertIsNone(parameter.default)
        with mock.patch.object(warm_path.os, "writev", None, create=True):
            _writev_all(7, (b"portable",), writer=writer)
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                _writev_all(7, (b"payload",))
        self.assertEqual(received, b"portable")

    def test_report_requires_output_parity_and_ten_percent_tail_win(self):
        eligible = build_report(
            timings(100.0, overhead=20.0),
            timings(85.0, overhead=15.0), output_mismatches=0,
            duration_seconds=1.0)
        self.assertTrue(
            eligible["comparison"]["runtime_change_eligible"])

        mismatch = build_report(
            timings(100.0, overhead=20.0),
            timings(80.0, overhead=15.0), output_mismatches=1,
            duration_seconds=1.0)
        self.assertFalse(
            mismatch["comparison"]["runtime_change_eligible"])

        too_small = build_report(
            timings(100.0, overhead=20.0),
            timings(95.0, overhead=19.0), output_mismatches=0,
            duration_seconds=1.0)
        self.assertFalse(
            too_small["comparison"]["runtime_change_eligible"])

    def test_native_decode_jitter_cannot_mask_overhead_regression(self):
        report = build_report(
            timings(100.0, native=80.0, overhead=20.0),
            timings(80.0, native=55.0, overhead=25.0),
            output_mismatches=0, duration_seconds=1.0)
        comparison = report["comparison"]
        self.assertGreaterEqual(
            comparison["p95_improvement_fraction"],
            warm_path.MEANINGFUL_IMPROVEMENT)
        self.assertLess(
            comparison["client_overhead_p95_improvement_fraction"], 0.0)
        self.assertFalse(comparison["runtime_change_eligible"])

    def test_zero_overhead_baseline_cannot_authorize_runtime_change(self):
        report = build_report(
            timings(100.0, overhead=0.0),
            timings(80.0, overhead=0.0),
            output_mismatches=0, duration_seconds=1.0)
        comparison = report["comparison"]
        self.assertIsNone(
            comparison["client_overhead_p95_improvement_fraction"])
        self.assertFalse(comparison["runtime_change_eligible"])
        json.dumps(report, allow_nan=False)

    def test_report_is_content_free_and_has_no_runtime_authority(self):
        report = build_report(
            timings(100.0), timings(90.0), output_mismatches=0,
            duration_seconds=1.0)
        encoded = json.dumps(report)
        self.assertEqual(report["runtime_authority"], "none")
        self.assertFalse(report["claim"]["runtime_change_recommended"])
        for forbidden in ("text", "transcript", "audio_path", "hypothesis"):
            self.assertNotIn(forbidden, encoded)

    def test_synthetic_audio_is_deterministic_bounded_float32(self):
        first = _synthetic_audio(0.25)
        second = _synthetic_audio(0.25)
        self.assertEqual(first.dtype.str, "<f4")
        self.assertEqual(len(first), 4_000)
        self.assertTrue(first.flags.c_contiguous)
        self.assertTrue((first == second).all())
        self.assertLessEqual(float(abs(first).max()), 0.06)

    def test_report_rejects_too_few_samples(self):
        with self.assertRaisesRegex(ValueError, "insufficient"):
            build_report(
                timings(100.0)[:-1], timings(90.0)[:-1],
                output_mismatches=0, duration_seconds=1.0)


if __name__ == "__main__":
    unittest.main()
