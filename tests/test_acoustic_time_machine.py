import inspect
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import acoustic_time_machine as module  # noqa: E402
from acoustic_time_machine import (  # noqa: E402
    MAX_SPANS,
    MAX_SPAN_SAMPLES,
    MAX_TOTAL_SAMPLES,
    SAMPLE_RATE_HZ,
    AcousticTimeMachine,
    BufferReceipt,
    Operation,
    Outcome,
)


class AcousticTimeMachineTests(unittest.TestCase):
    def test_disabled_by_default_retains_nothing_and_ignores_audio(self):
        buffer = AcousticTimeMachine()

        result = buffer.store(object(), sample_rate_hz=-1)

        self.assertIsNone(result.span_id)
        self.assertEqual(
            result.receipt,
            BufferReceipt(Operation.STORE, Outcome.DISABLED),
        )
        self.assertFalse(buffer.enabled)
        self.assertEqual(buffer.span_count, 0)
        self.assertEqual(buffer.retained_samples, 0)

    def test_enable_store_and_exact_sample_slice(self):
        buffer = AcousticTimeMachine(enabled=True)
        samples = (-1.0, -0.5, 0.0, 0.123456789, 0.5, 1.0)

        stored = buffer.store(samples, sample_rate_hz=SAMPLE_RATE_HZ)
        result = buffer.read(
            stored.span_id, start_sample=1, end_sample=5)

        self.assertRegex(stored.span_id, r"^atm-[0-9a-f]{32}$")
        self.assertEqual(
            stored.receipt,
            BufferReceipt(Operation.STORE, Outcome.STORED),
        )
        self.assertEqual(result.audio.samples, samples[1:5])
        self.assertEqual(result.audio.sample_rate_hz, SAMPLE_RATE_HZ)
        self.assertEqual(
            (result.audio.start_sample, result.audio.end_sample), (1, 5))
        self.assertEqual(buffer.span_count, 1)

    def test_read_is_nondestructive_consume_returns_slice_and_deletes_span(self):
        buffer = AcousticTimeMachine(enabled=True)
        stored = buffer.store(
            [0.0, 0.25, 0.5, 0.75], sample_rate_hz=SAMPLE_RATE_HZ)

        first = buffer.read(stored.span_id)
        second = buffer.read(stored.span_id)
        consumed = buffer.consume(
            stored.span_id, start_sample=1, end_sample=3)
        missing = buffer.read(stored.span_id)

        self.assertEqual(first.audio.samples, second.audio.samples)
        self.assertEqual(consumed.audio.samples, (0.25, 0.5))
        self.assertEqual(consumed.receipt.outcome, Outcome.CONSUMED)
        self.assertIsNone(missing.audio)
        self.assertEqual(missing.receipt.outcome, Outcome.NOT_FOUND)
        self.assertEqual((buffer.span_count, buffer.retained_samples), (0, 0))

    def test_delete_clear_and_disable_drop_all_internal_audio(self):
        buffer = AcousticTimeMachine(enabled=True)
        first = buffer.store([0.25], sample_rate_hz=SAMPLE_RATE_HZ)
        second = buffer.store([0.5], sample_rate_hz=SAMPLE_RATE_HZ)
        first_storage = buffer._spans[first.span_id].samples
        second_storage = buffer._spans[second.span_id].samples

        self.assertEqual(buffer.delete(first.span_id).outcome, Outcome.DELETED)
        self.assertEqual(first_storage, [0.0])
        self.assertEqual(buffer.span_count, 1)
        self.assertEqual(buffer.clear().outcome, Outcome.CLEARED)
        self.assertEqual(second_storage, [0.0])
        self.assertEqual((buffer.span_count, buffer.retained_samples), (0, 0))

        third = buffer.store([0.75], sample_rate_hz=SAMPLE_RATE_HZ)
        third_storage = buffer._spans[third.span_id].samples
        self.assertEqual(buffer.disable().outcome, Outcome.DISABLED)
        self.assertEqual(third_storage, [0.0])
        self.assertFalse(buffer.enabled)
        self.assertEqual((buffer.span_count, buffer.retained_samples), (0, 0))
        self.assertEqual(
            buffer.read(second.span_id).receipt.outcome, Outcome.DISABLED)

    def test_strict_audio_and_slice_limits_fail_without_mutation(self):
        buffer = AcousticTimeMachine(enabled=True)
        invalid_samples = (
            [],
            [0.0] * (MAX_SPAN_SAMPLES + 1),
            [float("nan")],
            [float("inf")],
            [-1.01],
            [1.01],
            [True],
            ["audio"],
        )
        for samples in invalid_samples:
            with self.subTest(samples_type=type(samples), length=len(samples)):
                with self.assertRaises(ValueError):
                    buffer.store(samples, sample_rate_hz=SAMPLE_RATE_HZ)
        for sample_rate in (0, 8_000, 44_100, 48_000, True, 16_000.0):
            with self.subTest(sample_rate=sample_rate):
                with self.assertRaises(ValueError):
                    buffer.store([0.0], sample_rate_hz=sample_rate)

        stored = buffer.store([0.0, 0.5], sample_rate_hz=SAMPLE_RATE_HZ)
        for start, end in ((-1, 1), (0, 0), (1, 1), (0, 3), (True, 1)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    buffer.read(
                        stored.span_id, start_sample=start, end_sample=end)
        self.assertEqual((buffer.span_count, buffer.retained_samples), (1, 2))

    def test_count_and_aggregate_duration_bounds_reject_without_eviction(self):
        count_bound = AcousticTimeMachine(enabled=True)
        handles = [
            count_bound.store(
                [index / MAX_SPANS], sample_rate_hz=SAMPLE_RATE_HZ)
            for index in range(MAX_SPANS)
        ]
        rejected = count_bound.store([0.0], sample_rate_hz=SAMPLE_RATE_HZ)

        self.assertIsNone(rejected.span_id)
        self.assertEqual(rejected.receipt.outcome, Outcome.CAPACITY_EXCEEDED)
        self.assertEqual(count_bound.span_count, MAX_SPANS)
        self.assertTrue(all(
            count_bound.read(handle.span_id).audio is not None
            for handle in handles
        ))

        duration_bound = AcousticTimeMachine(enabled=True)
        for _ in range(MAX_TOTAL_SAMPLES // MAX_SPAN_SAMPLES):
            stored = duration_bound.store(
                [0.0] * MAX_SPAN_SAMPLES, sample_rate_hz=SAMPLE_RATE_HZ)
            self.assertIsNotNone(stored.span_id)
        remainder = MAX_TOTAL_SAMPLES % MAX_SPAN_SAMPLES
        if remainder:
            duration_bound.store(
                [0.0] * remainder, sample_rate_hz=SAMPLE_RATE_HZ)
        rejected = duration_bound.store([0.0], sample_rate_hz=SAMPLE_RATE_HZ)

        self.assertEqual(duration_bound.retained_samples, MAX_TOTAL_SAMPLES)
        self.assertEqual(rejected.receipt.outcome, Outcome.CAPACITY_EXCEEDED)

    def test_receipts_are_deterministic_fixed_shape_and_content_free(self):
        secret_samples = [0.123456]
        first = AcousticTimeMachine(enabled=True).store(
            secret_samples, sample_rate_hz=SAMPLE_RATE_HZ)
        second = AcousticTimeMachine(enabled=True).store(
            secret_samples, sample_rate_hz=SAMPLE_RATE_HZ)

        self.assertNotEqual(first.span_id, second.span_id)
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(
            [field.name for field in fields(BufferReceipt)],
            ["operation", "outcome"],
        )
        receipt_text = repr(first.receipt).casefold()
        for forbidden in (
                first.span_id, "sample", "audio", "duration", "digest",
                "timestamp", "0.123456"):
            self.assertNotIn(forbidden, receipt_text)

    def test_parallel_stores_are_unique_and_never_cross_hard_bound(self):
        buffer = AcousticTimeMachine(enabled=True)

        with ThreadPoolExecutor(max_workers=MAX_SPANS) as pool:
            results = list(pool.map(
                lambda _: buffer.store([0.0], sample_rate_hz=SAMPLE_RATE_HZ),
                range(MAX_SPANS * 2),
            ))

        stored_ids = [result.span_id for result in results if result.span_id]
        self.assertEqual(len(stored_ids), MAX_SPANS)
        self.assertEqual(len(set(stored_ids)), MAX_SPANS)
        self.assertEqual(buffer.span_count, MAX_SPANS)
        self.assertEqual(buffer.retained_samples, MAX_SPANS)

    def test_module_exposes_no_persistence_network_logging_or_serialization_api(self):
        source = inspect.getsource(module)
        for forbidden_import in (
                "import json", "import logging", "import pickle",
                "import socket", "import tempfile", "from pathlib"):
            self.assertNotIn(forbidden_import, source)
        buffer = AcousticTimeMachine()
        for forbidden_method in (
                "dump", "dumps", "load", "loads", "save", "serialize",
                "write", "upload"):
            self.assertFalse(hasattr(buffer, forbidden_method))


if __name__ == "__main__":
    unittest.main()
