# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Model-free tests for the bounded macOS Whisper verifier adapter."""

import math
from pathlib import Path
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from process_verifier import VerificationReceipt, VerificationRequest  # noqa: E402
from whisper_verifier_adapter import (  # noqa: E402
    MAX_AUDIO_SAMPLES,
    MAX_EXPECTED_CHARACTERS,
    WHISPER_SAMPLE_RATE,
    WHISPER_TINY_ENGINE,
    WHISPER_TINY_REPO,
    WHISPER_TINY_REVISION,
    WhisperTinyVerifier,
    WhisperTinyWorker,
    _resolve_local_snapshot,
    normalize_for_verification,
)


def request(samples=(0.1, -0.1), expected="Invoice 2042"):
    return VerificationRequest(
        tuple(samples), WHISPER_SAMPLE_RATE, expected,
        time.monotonic() + 2.0)


def result(text, log_probability=math.log(0.9)):
    return {
        "text": text,
        "segments": [{
            "text": text,
            "start": 0.0,
            "end": 0.5,
            "avg_logprob": log_probability,
        }],
    }


def local_test_resolver():
    return tempfile.gettempdir()


def model_free_transcriber(_samples, _path):
    return result("invoice 2042")


class WhisperTinyWorkerTests(unittest.TestCase):
    def worker(self, text, confidence=0.9):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return WhisperTinyWorker(
            resolver=lambda: temporary.name,
            transcriber=lambda _samples, _path: result(
                text, math.log(confidence) if confidence else -1000.0),
        )

    def test_normalization_is_nfkc_casefolded_and_punctuation_insensitive(self):
        self.assertEqual(
            normalize_for_verification("  ＩＮＶＯＩＣＥ—2042! "),
            "invoice 2042",
        )

    def test_snapshot_resolution_is_exact_revision_and_local_only(self):
        calls = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        resolved = _resolve_local_snapshot(
            lambda **kwargs: calls.append(kwargs) or temporary.name)

        self.assertEqual(resolved, temporary.name)
        self.assertEqual(calls, [{
            "repo_id": WHISPER_TINY_REPO,
            "revision": WHISPER_TINY_REVISION,
            "local_files_only": True,
        }])

    def test_exact_normalized_match_is_confirmed_without_returning_transcript(self):
        payload = self.worker("invoice, 2042.")(request())

        self.assertEqual(set(payload), {"outcome", "confidence", "engine"})
        self.assertEqual(payload["outcome"], "confirmed")
        self.assertEqual(payload["confidence"], 0.9)
        self.assertEqual(payload["engine"], WHISPER_TINY_ENGINE)
        self.assertNotIn("invoice", repr(payload).casefold())
        self.assertNotIn("2042", repr(payload))

    def test_confident_token_mismatch_is_contradicted(self):
        payload = self.worker("invoice 2043")(request())

        self.assertEqual(payload["outcome"], "contradicted")
        self.assertEqual(payload["confidence"], 0.9)

    def test_partial_or_low_confidence_evidence_is_inconclusive(self):
        partial = self.worker("please invoice 2042")(request())
        low = self.worker("invoice 2042", confidence=0.4)(request())

        self.assertEqual(partial["outcome"], "inconclusive")
        self.assertEqual(low["outcome"], "inconclusive")

    def test_invalid_or_silent_inputs_fail_closed_before_model_access(self):
        calls = []
        worker = WhisperTinyWorker(
            resolver=lambda: calls.append("resolve") or "/not-used",
            transcriber=lambda _samples, _path: calls.append("decode") or {},
        )
        invalid = (
            request(samples=()),
            request(samples=(0.0,)),
            request(samples=(float("nan"),)),
            request(samples=(1.01,)),
            request(samples=(0.1,), expected=""),
            request(samples=(0.1,), expected="x\nprivate"),
            request(samples=(0.1,), expected="x" * (MAX_EXPECTED_CHARACTERS + 1)),
            VerificationRequest((0.1,), 8_000, "invoice", time.monotonic() + 2),
            VerificationRequest(
                _BrokenAudio(), WHISPER_SAMPLE_RATE, "invoice",
                time.monotonic() + 2),
        )

        for candidate in invalid:
            with self.subTest(candidate=candidate):
                self.assertEqual(worker(candidate), {
                    "outcome": "inconclusive",
                    "confidence": 0.0,
                    "engine": WHISPER_TINY_ENGINE,
                })
        self.assertEqual(calls, [])

    def test_oversized_audio_is_rejected_without_iterating_it(self):
        worker = WhisperTinyWorker(
            resolver=lambda: self.fail("resolver must not run"))

        payload = worker(VerificationRequest(
            _OversizedAudio(), WHISPER_SAMPLE_RATE, "invoice",
            time.monotonic() + 2.0))

        self.assertEqual(payload["outcome"], "inconclusive")

    def test_resolution_and_decode_failures_return_only_closed_evidence(self):
        worker = WhisperTinyWorker(
            resolver=lambda: (_ for _ in ()).throw(RuntimeError("private")))

        payload = worker(request(expected="private expected text"))

        self.assertEqual(payload, {
            "outcome": "inconclusive",
            "confidence": 0.0,
            "engine": WHISPER_TINY_ENGINE,
        })
        self.assertNotIn("private", repr(payload))


class WhisperTinyVerifierTests(unittest.TestCase):
    def test_composed_adapter_crosses_real_process_boundary(self):
        verifier = WhisperTinyVerifier(worker=WhisperTinyWorker(
            resolver=local_test_resolver,
            transcriber=model_free_transcriber,
        ))

        receipt = verifier.verify(
            [0.1, -0.1], WHISPER_SAMPLE_RATE, "Invoice 2042",
            deadline_at=time.monotonic() + 2.0)

        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.result.outcome, "confirmed")
        self.assertEqual(receipt.result.engine, WHISPER_TINY_ENGINE)

    def test_parent_bounds_before_crossing_process_boundary(self):
        delegate = _RecordingVerifier()
        verifier = WhisperTinyVerifier(process_verifier=delegate)

        receipt = verifier.verify(
            [0.1] * (MAX_AUDIO_SAMPLES + 1),
            WHISPER_SAMPLE_RATE,
            "private expected text",
            deadline_at=time.monotonic() + 2.0,
        )

        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.result.outcome, "inconclusive")
        self.assertEqual(receipt.result.confidence, 0.0)
        self.assertEqual(receipt.result.engine, WHISPER_TINY_ENGINE)
        self.assertEqual(delegate.calls, [])
        self.assertNotIn("private", repr(receipt))

    def test_valid_bounded_request_is_delegated_as_an_immutable_copy(self):
        delegate = _RecordingVerifier()
        verifier = WhisperTinyVerifier(process_verifier=delegate)
        samples = [0.1, -0.2]
        deadline = time.monotonic() + 2.0

        receipt = verifier.verify(
            samples, WHISPER_SAMPLE_RATE, "Invoice 2042",
            deadline_at=deadline)

        self.assertIs(receipt, delegate.receipt)
        self.assertEqual(len(delegate.calls), 1)
        copied, rate, expected, actual_deadline = delegate.calls[0]
        self.assertEqual(copied, (0.1, -0.2))
        self.assertIsInstance(copied, tuple)
        self.assertEqual(rate, WHISPER_SAMPLE_RATE)
        self.assertEqual(expected, "Invoice 2042")
        self.assertEqual(actual_deadline, deadline)


class _OversizedAudio:
    def __len__(self):
        return MAX_AUDIO_SAMPLES + 1

    def __iter__(self):
        raise AssertionError("oversized audio must not be read")


class _BrokenAudio:
    def __len__(self):
        return 1

    def __iter__(self):
        raise RuntimeError("private iterator failure")


class _RecordingVerifier:
    def __init__(self):
        self.calls = []
        self.receipt = VerificationReceipt(
            result=__import__("process_verifier").VerificationResult(
                "confirmed", 0.8, "fake"))

    def verify(self, samples, sample_rate, expected, *, deadline_at):
        self.calls.append((samples, sample_rate, expected, deadline_at))
        return self.receipt


if __name__ == "__main__":
    unittest.main()
