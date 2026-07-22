# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Focused fake-provider tests for the prewarmed verifier supervisor."""

import os
from pathlib import Path
import sys
from threading import Thread
import time
import unittest
import weakref


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prewarmed_verifier import (  # noqa: E402
    MAX_REQUEST_SAMPLES,
    SAMPLE_RATE_HZ,
    PrewarmedVerifierSupervisor,
)
from process_verifier import RefusalReason  # noqa: E402


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.pid = os.getpid()

    def __call__(self, request):
        self.calls += 1
        if request.expected == "hang":
            while True:
                time.sleep(1.0)
        if request.expected == "crash":
            os._exit(23)
        if request.expected == "malformed":
            return {
                "outcome": "confirmed",
                "confidence": 0.9,
                "engine": "fake-asr",
                "transcript": "must not cross",
            }
        if request.expected == "slow":
            time.sleep(0.05)
        if request.expected == "noisy":
            print("private child output", flush=True)
            os.write(2, b"private child error\n")
        return {
            "outcome": "confirmed",
            "confidence": min(1.0, self.calls / 10),
            "engine": f"fake-{self.pid}",
        }


def fake_provider_factory():
    return FakeProvider()


def crashing_factory():
    raise RuntimeError("private provider setup detail")


def slow_factory():
    time.sleep(1.0)
    return FakeProvider()


class RetentionProbeProvider:
    def __init__(self):
        self.collected = False

    def __call__(self, request):
        if request.expected == "arm":
            request_ref = weakref.ref(request)

            def probe():
                time.sleep(0.05)
                self.collected = request_ref() is None

            Thread(target=probe, daemon=True).start()
        return {
            "outcome": "confirmed" if self.collected else "inconclusive",
            "confidence": 1.0,
            "engine": "fake-retention-probe",
        }


def retention_probe_factory():
    return RetentionProbeProvider()


class UnreadableAudio:
    def __len__(self):
        raise AssertionError("expired requests must not inspect audio")


class OversizedAudio:
    def __len__(self):
        return MAX_REQUEST_SAMPLES + 1

    def __iter__(self):
        raise AssertionError("oversized audio must not be iterated")


class PrewarmedVerifierTests(unittest.TestCase):
    def verify(self, verifier, expected="ok", deadline=2.0):
        return verifier.verify(
            [0.0, 0.25, -0.25],
            SAMPLE_RATE_HZ,
            expected,
            deadline_at=time.monotonic() + deadline,
        )

    def test_one_lazy_child_initializes_once_and_serves_multiple_requests(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)
        self.assertIsNone(verifier._process)

        first = self.verify(verifier)
        process = verifier._process
        second = self.verify(verifier)

        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertIs(verifier._process, process)
        self.assertEqual(first.result.engine, second.result.engine)
        self.assertEqual(first.result.confidence, 0.1)
        self.assertEqual(second.result.confidence, 0.2)
        self.assertFalse(hasattr(verifier, "requests"))
        self.assertFalse(hasattr(verifier, "results"))
        verifier.close()

    def test_idle_child_does_not_retain_the_previous_request(self):
        verifier = PrewarmedVerifierSupervisor(retention_probe_factory)

        verifier.verify(
            [0.25], 16_000, "arm",
            deadline_at=time.monotonic() + 2.0)
        time.sleep(0.1)
        receipt = verifier.verify(
            [0.5], 16_000, "check",
            deadline_at=time.monotonic() + 2.0)

        self.assertEqual(receipt.result.outcome, "confirmed")
        verifier.close()

    def test_absolute_timeout_discards_child_and_next_call_restarts_lazily(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)
        self.verify(verifier)
        original = verifier._process
        started = time.monotonic()

        timeout = self.verify(verifier, "hang", deadline=0.12)

        self.assertEqual(timeout.refusal, RefusalReason.TIMEOUT)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNone(verifier._process)
        recovered = self.verify(verifier)
        self.assertTrue(recovered.accepted)
        self.assertIsNot(verifier._process, original)
        self.assertEqual(recovered.result.confidence, 0.1)
        verifier.close()

    def test_crash_discards_child_and_returns_only_closed_refusal(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)

        receipt = self.verify(verifier, "crash")

        self.assertEqual(receipt.refusal, RefusalReason.CRASH)
        self.assertIsNone(verifier._process)
        self.assertTrue(self.verify(verifier).accepted)
        verifier.close()

    def test_malformed_response_discards_child_before_lazy_restart(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)

        receipt = self.verify(verifier, "malformed")

        self.assertEqual(receipt.refusal, RefusalReason.MALFORMED_RESULT)
        self.assertIsNone(verifier._process)
        recovered = self.verify(verifier)
        self.assertEqual(recovered.result.confidence, 0.1)
        self.assertNotIn("transcript", repr(receipt).casefold())
        verifier.close()

    def test_provider_initialization_failure_is_closed_and_restartable(self):
        verifier = PrewarmedVerifierSupervisor(crashing_factory)

        receipt = self.verify(verifier)

        self.assertEqual(receipt.refusal, RefusalReason.CRASH)
        self.assertIsNone(verifier._process)
        self.assertNotIn("private", repr(receipt).casefold())
        verifier.close()

    def test_provider_initialization_is_inside_the_request_deadline(self):
        verifier = PrewarmedVerifierSupervisor(slow_factory)
        started = time.monotonic()

        receipt = self.verify(verifier, deadline=0.12)

        self.assertEqual(receipt.refusal, RefusalReason.TIMEOUT)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNone(verifier._process)
        verifier.close()

    def test_expired_and_oversized_requests_never_spawn_or_read_audio(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)

        expired = verifier.verify(
            UnreadableAudio(), 16_000, "private",
            deadline_at=time.monotonic() - 1.0,
        )

        self.assertEqual(expired.refusal, RefusalReason.TIMEOUT)
        self.assertIsNone(verifier._process)
        with self.assertRaisesRegex(ValueError, "bounded"):
            verifier.verify(
                OversizedAudio(), SAMPLE_RATE_HZ, "private",
                deadline_at=time.monotonic() + 2.0,
            )
        self.assertIsNone(verifier._process)
        verifier.close()

    def test_invalid_audio_text_rate_and_deadline_fail_before_spawn(self):
        cases = (
            ([float("nan")], 16_000, "private", time.monotonic() + 2.0),
            ([0.0], True, "private", time.monotonic() + 2.0),
            ([0.0], 48_000, "private", time.monotonic() + 2.0),
            ([0.0], 16_000, "", time.monotonic() + 2.0),
            ([0.0], 16_000, "private", float("nan")),
        )
        for samples, rate, expected, deadline in cases:
            with self.subTest(rate=rate, expected=expected):
                verifier = PrewarmedVerifierSupervisor(fake_provider_factory)
                with self.assertRaises((TypeError, ValueError)):
                    verifier.verify(
                        samples, rate, expected, deadline_at=deadline)
                self.assertIsNone(verifier._process)
                verifier.close()

    def test_sequential_calls_reuse_state_and_child_output_is_suppressed(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)

        noisy = self.verify(verifier, "noisy")
        next_receipt = self.verify(verifier, "slow")

        self.assertTrue(noisy.accepted)
        self.assertTrue(next_receipt.accepted)
        self.assertEqual(noisy.result.engine, next_receipt.result.engine)
        self.assertEqual(next_receipt.result.confidence, 0.2)
        verifier.close()

    def test_close_and_context_manager_destroy_child_and_are_idempotent(self):
        verifier = PrewarmedVerifierSupervisor(fake_provider_factory)
        with verifier as active:
            self.assertTrue(self.verify(active).accepted)
            self.assertIsNotNone(active._process)

        self.assertIsNone(verifier._process)
        verifier.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.verify(verifier)


if __name__ == "__main__":
    unittest.main()
