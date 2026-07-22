# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic fake-worker tests for the killable verifier boundary."""

import os
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from process_verifier import (  # noqa: E402
    ProcessIsolatedVerifier,
    RefusalReason,
)


def confirming_worker(request):
    if request.sample_rate <= 0 or not request.samples or not request.expected:
        raise AssertionError("fake worker received an invalid request")
    return {
        "outcome": "confirmed",
        "confidence": 0.875,
        "engine": "fake-asr",
    }


def isolation_worker(request):
    return {
        "outcome": (
            "confirmed" if os.getpid() != int(request.expected)
            else "contradicted"
        ),
        "confidence": 1.0,
        "engine": "fake-isolation-check",
    }


def hanging_worker(_request):
    while True:
        time.sleep(1.0)


def crashing_worker(_request):
    os._exit(23)


def malformed_worker(_request):
    return {
        "outcome": "confirmed",
        "confidence": 0.9,
        "engine": "fake-asr",
        "transcript": "must never cross the boundary",
    }


class UnreadableAudio:
    def __iter__(self):
        raise AssertionError("expired requests must not read audio")


class ProcessVerifierTests(unittest.TestCase):
    def verifier(self, worker):
        return ProcessIsolatedVerifier(worker)

    def test_accepts_only_closed_transcript_free_evidence(self):
        verifier = self.verifier(confirming_worker)

        receipt = verifier.verify(
            [0.0, 0.25, -0.25],
            16_000,
            "invoice 2042",
            deadline_at=time.monotonic() + 2.0,
        )

        self.assertTrue(receipt.accepted)
        self.assertIsNone(receipt.refusal)
        self.assertEqual(receipt.result.outcome, "confirmed")
        self.assertEqual(receipt.result.confidence, 0.875)
        self.assertEqual(receipt.result.engine, "fake-asr")
        encoded = repr(receipt)
        self.assertNotIn("invoice", encoded)
        self.assertNotIn("2042", encoded)
        self.assertFalse(hasattr(verifier, "requests"))
        self.assertFalse(hasattr(verifier, "results"))

    def test_worker_runs_outside_the_calling_process(self):
        receipt = self.verifier(isolation_worker).verify(
            [0.0], 16_000, str(os.getpid()),
            deadline_at=time.monotonic() + 2.0,
        )

        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.result.outcome, "confirmed")

    def test_hanging_worker_is_killed_at_hard_deadline(self):
        verifier = self.verifier(hanging_worker)
        started = time.monotonic()

        receipt = verifier.verify(
            [0.0], 16_000, "private words",
            deadline_at=started + 0.15,
        )

        elapsed = time.monotonic() - started
        self.assertEqual(receipt.refusal, RefusalReason.TIMEOUT)
        self.assertFalse(receipt.accepted)
        self.assertLess(elapsed, 1.0)

    def test_crashed_worker_has_a_fixed_transcript_free_refusal(self):
        receipt = self.verifier(crashing_worker).verify(
            [0.0], 16_000, "private words",
            deadline_at=time.monotonic() + 2.0,
        )

        self.assertEqual(receipt.refusal, RefusalReason.CRASH)
        self.assertEqual(repr(receipt), (
            "VerificationReceipt(result=None, "
            "refusal=<RefusalReason.CRASH: 'crash'>)"))

    def test_extra_transcript_field_is_a_malformed_result_refusal(self):
        receipt = self.verifier(malformed_worker).verify(
            [0.0], 16_000, "private words",
            deadline_at=time.monotonic() + 2.0,
        )

        self.assertEqual(
            receipt.refusal, RefusalReason.MALFORMED_RESULT)
        self.assertNotIn("private words", repr(receipt))

    def test_expired_deadline_refuses_without_reading_or_spawning(self):
        receipt = self.verifier(confirming_worker).verify(
            UnreadableAudio(), 16_000, "private words",
            deadline_at=time.monotonic() - 1.0,
        )

        self.assertEqual(receipt.refusal, RefusalReason.TIMEOUT)

    def test_malformed_result_scalars_are_refused(self):
        cases = (
            {"outcome": "invented", "confidence": 0.9, "engine": "fake"},
            {"outcome": "confirmed", "confidence": True, "engine": "fake"},
            {"outcome": "confirmed", "confidence": float("nan"),
             "engine": "fake"},
            {"outcome": "confirmed", "confidence": 0.9,
             "engine": "fake engine with transcript-shaped output"},
        )

        for payload in cases:
            with self.subTest(payload=payload):
                receipt = self.verifier(
                    _ConstantWorker(payload)).verify(
                        [0.0], 16_000, "private words",
                        deadline_at=time.monotonic() + 2.0,
                    )
                self.assertEqual(
                    receipt.refusal, RefusalReason.MALFORMED_RESULT)

    def test_received_result_allows_graceful_exit_before_terminate(self):
        events = []

        class FakeProcess:
            def __init__(self):
                self.alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                events.append(("join", timeout))
                self.alive = False

            def terminate(self):
                raise AssertionError("completed child must exit gracefully")

        ProcessIsolatedVerifier._stop(
            FakeProcess(), graceful_result=True)

        self.assertEqual(events, [("join", 0.25)])

    def test_disposable_process_handle_is_closed_after_verification(self):
        events = []

        class FakeConnection:
            def close(self):
                events.append("connection-close")

            def poll(self, _remaining):
                return True

            def recv(self):
                return {
                    "outcome": "confirmed",
                    "confidence": 1.0,
                    "engine": "fake",
                }

        class FakeSender:
            def close(self):
                events.append("sender-close")

        class FakeProcess:
            pid = 42

            def start(self):
                events.append("start")

            def is_alive(self):
                return False

            def join(self, _timeout=None):
                events.append("join")

            def close(self):
                events.append("process-close")

        class FakeContext:
            def Pipe(self, duplex=False):
                self.duplex = duplex
                return FakeConnection(), FakeSender()

            def Process(self, **_kwargs):
                return FakeProcess()

        verifier = ProcessIsolatedVerifier(
            confirming_worker, context=FakeContext())

        receipt = verifier.verify(
            [0.0], 16_000, "private",
            deadline_at=time.monotonic() + 1.0)

        self.assertTrue(receipt.accepted)
        self.assertEqual(events[-3:], [
            "connection-close", "join", "process-close",
        ])


class _ConstantWorker:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, _request):
        return self.payload


if __name__ == "__main__":
    unittest.main()
