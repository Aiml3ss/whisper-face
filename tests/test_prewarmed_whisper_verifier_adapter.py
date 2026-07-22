# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Model-free reuse tests for the prewarmed Whisper Tiny adapter."""

import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from process_verifier import RefusalReason  # noqa: E402
from whisper_verifier_adapter import (  # noqa: E402
    MAX_AUDIO_SAMPLES,
    WHISPER_SAMPLE_RATE,
    WHISPER_TINY_ENGINE,
    LoadedWhisperTiny,
    PrewarmedWhisperTinyProviderFactory,
    PrewarmedWhisperTinyVerifier,
)


_FAKE_LOADS = 0


def fake_resolver():
    return tempfile.gettempdir()


def fake_loader(model_path):
    global _FAKE_LOADS
    _FAKE_LOADS += 1
    return {
        "load_number": _FAKE_LOADS,
        "calls": 0,
        "model_path": model_path,
        "pid": os.getpid(),
    }


def fake_transcriber(samples, loaded):
    state = loaded.model
    state["calls"] += 1
    if samples[0] == 0.75:
        raise RuntimeError("private decoder failure")
    if samples[0] == 0.9:
        time.sleep(1.0)
    confidence = state["load_number"] / 10 + state["calls"] / 100
    text = "invoice 2042"
    return {
        "text": text,
        "segments": [{
            "text": text,
            "start": 0.0,
            "end": 0.5,
            "avg_logprob": math.log(confidence),
        }],
    }


def fake_factory():
    return PrewarmedWhisperTinyProviderFactory(
        resolver=fake_resolver,
        loader=fake_loader,
        transcriber=fake_transcriber,
    )


class PrewarmedWhisperTinyAdapterTests(unittest.TestCase):
    def setUp(self):
        global _FAKE_LOADS
        _FAKE_LOADS = 0

    def verify(self, verifier, samples=(0.1, -0.1), deadline=2.0):
        return verifier.verify(
            samples,
            WHISPER_SAMPLE_RATE,
            "Invoice 2042",
            deadline_at=time.monotonic() + deadline,
        )

    def test_factory_resolves_and_loads_one_child_only_state(self):
        provider = fake_factory()()

        self.assertEqual(_FAKE_LOADS, 1)
        self.assertIsInstance(provider.loaded, LoadedWhisperTiny)
        self.assertEqual(provider.loaded.model_path, tempfile.gettempdir())
        self.assertNotIn("model", repr(provider.loaded).casefold())

    def test_one_load_is_reused_for_sequential_bounded_requests(self):
        verifier = PrewarmedWhisperTinyVerifier(provider_factory=fake_factory())

        first = self.verify(verifier)
        second = self.verify(verifier)

        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(first.result.engine, WHISPER_TINY_ENGINE)
        self.assertEqual(first.result.confidence, 0.11)
        self.assertEqual(second.result.confidence, 0.12)
        self.assertNotIn("invoice", repr(first).casefold())
        self.assertNotIn("2042", repr(first))
        verifier.close()

    def test_decoder_failure_discards_loaded_child_and_lazily_reloads(self):
        verifier = PrewarmedWhisperTinyVerifier(provider_factory=fake_factory())
        self.assertEqual(self.verify(verifier).result.confidence, 0.11)

        failed = self.verify(verifier, samples=(0.75,))
        recovered = self.verify(verifier)

        self.assertEqual(failed.refusal, RefusalReason.CRASH)
        self.assertEqual(recovered.result.confidence, 0.11)
        verifier.close()

    def test_timeout_kills_loaded_child_and_next_request_reloads(self):
        verifier = PrewarmedWhisperTinyVerifier(provider_factory=fake_factory())
        started = time.monotonic()

        timeout = self.verify(verifier, samples=(0.9,), deadline=0.12)
        recovered = self.verify(verifier)

        self.assertEqual(timeout.refusal, RefusalReason.TIMEOUT)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(recovered.result.confidence, 0.11)
        verifier.close()

    def test_parent_rejects_non_16khz_and_oversized_audio_without_loading(self):
        class RecordingSupervisor:
            def __init__(self):
                self.calls = []

            def verify(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("invalid request must not cross boundary")

            def close(self):
                pass

        supervisor = RecordingSupervisor()
        verifier = PrewarmedWhisperTinyVerifier(supervisor=supervisor)

        wrong_rate = verifier.verify(
            [0.1], 8_000, "Invoice 2042",
            deadline_at=time.monotonic() + 2.0)
        oversized = self.verify(
            verifier, samples=[0.1] * (MAX_AUDIO_SAMPLES + 1))

        self.assertEqual(wrong_rate.result.outcome, "inconclusive")
        self.assertEqual(oversized.result.outcome, "inconclusive")
        self.assertEqual(supervisor.calls, [])

    def test_expiry_during_invalid_input_validation_returns_timeout(self):
        class ExpiringInvalidAudio:
            def __len__(self):
                return 1

            def __iter__(self):
                time.sleep(0.03)
                yield float("nan")

        verifier = PrewarmedWhisperTinyVerifier(provider_factory=fake_factory())

        receipt = verifier.verify(
            ExpiringInvalidAudio(),
            WHISPER_SAMPLE_RATE,
            "Invoice 2042",
            deadline_at=time.monotonic() + 0.01,
        )

        self.assertEqual(receipt.refusal, RefusalReason.TIMEOUT)
        self.assertIsNone(verifier._supervisor._process)
        verifier.close()

    def test_context_manager_closes_without_activating_runtime(self):
        verifier = PrewarmedWhisperTinyVerifier(provider_factory=fake_factory())

        with verifier as active:
            self.assertTrue(self.verify(active).accepted)

        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.verify(verifier)


if __name__ == "__main__":
    unittest.main()
