# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Fast regression tests for platform-independent dictate.py seams.

The application imports macOS UI, audio, and MLX frameworks at module import
time.  These tests compile only the selected production definitions so the
logic can run in under a second without loading either model.
"""

import ast
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parrot_core import Recognition, compile_cleanup  # noqa: E402

TREE = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))


def load_definitions(*names, assignments=(), extra=None):
    selected = []
    found = set()
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) \
                and node.name in names:
            selected.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & set(assignments):
                selected.append(node)
    missing = set(names) - found
    if missing:
        raise AssertionError(f"production definitions missing: {sorted(missing)}")
    namespace = {
        "json": json,
        "re": re,
        "threading": threading,
    }
    namespace.update(extra or {})
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, "dictate-selected", "exec"), namespace)
    return namespace


class FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.starts = 0
        self.stops = 0
        self.closed = False

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed = True


class AudioPoolTests(unittest.TestCase):
    def test_streams_are_preopened_and_reused(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        ns = load_definitions(
            "AudioSlot", "AudioPool",
            assignments={"SAMPLE_RATE"},
            extra={"sd": SimpleNamespace(InputStream=factory)},
        )
        pool = ns["AudioPool"](size=2, stream_factory=factory)
        pool.warm()
        self.assertEqual(len(streams), 2)
        self.assertEqual([s.starts for s in streams], [1, 1])

        first = SimpleNamespace(_callback=lambda *args: None)
        slot = pool.acquire(first)
        pool.release(slot)
        second = SimpleNamespace(_callback=lambda *args: None)
        reused = pool.acquire(second)
        pool.release(reused)

        self.assertIs(reused, slot)
        self.assertEqual(len(streams), 2)
        self.assertEqual(streams[0].starts, 3)


class FlightRecorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_definitions(
            "extract_recent_utterance", "peak_rms",
            assignments={
                "SAMPLE_RATE", "MIN_SECONDS", "GATE_PEAK_RMS",
                "FLIGHT_BUFFER_SECONDS", "FLIGHT_MAX_LAG",
                "FLIGHT_START_SILENCE", "FLIGHT_PAD_SECONDS",
            },
            extra={"np": np},
        )

    @staticmethod
    def silence(seconds):
        return np.zeros(int(16_000 * seconds), dtype=np.float32)

    @staticmethod
    def speech(seconds, frequency=220):
        t = np.arange(int(16_000 * seconds), dtype=np.float32) / 16_000
        return (0.04 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)

    def test_extracts_the_recent_utterance_with_padding(self):
        audio = np.concatenate([
            self.silence(2.0), self.speech(1.0), self.silence(0.5),
        ])
        selected = self.ns["extract_recent_utterance"](audio)
        self.assertGreater(len(selected), int(16_000 * 1.15))
        self.assertLess(len(selected), int(16_000 * 1.4))

    def test_rejects_stale_speech(self):
        audio = np.concatenate([self.speech(1.0), self.silence(3.0)])
        selected = self.ns["extract_recent_utterance"](audio)
        self.assertEqual(len(selected), 0)

    def test_a_long_pause_separates_utterances(self):
        audio = np.concatenate([
            self.speech(1.0, 180), self.silence(1.2),
            self.speech(0.8, 320), self.silence(0.4),
        ])
        selected = self.ns["extract_recent_utterance"](audio)
        self.assertGreater(len(selected), int(16_000 * 0.95))
        self.assertLess(len(selected), int(16_000 * 1.3))

    def test_disable_detaches_and_erases_the_ram_buffer(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        ns = load_definitions(
            "FlightRecorder",
            assignments={"SAMPLE_RATE", "FLIGHT_BUFFER_SECONDS"},
            extra={
                "deque": __import__("collections").deque,
                "np": np,
                "threading": threading,
                "time": __import__("time"),
            },
        )
        flight = ns["FlightRecorder"](seconds=1, stream_factory=factory)
        flight.enable()
        calls = []
        target = SimpleNamespace(_callback=lambda *args: calls.append(args))
        self.assertTrue(flight.attach(target))
        self.assertFalse(flight.attach(SimpleNamespace(_callback=lambda: None)))
        streams[0].callback(
            np.ones((160, 1), dtype=np.float32), 160, None, None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(flight.total_samples, 160)

        flight.disable()
        self.assertFalse(flight.is_enabled())
        self.assertEqual(flight.total_samples, 0)
        self.assertEqual(len(flight.frames), 0)
        self.assertTrue(streams[0].closed)


class CleanupGuardTests(unittest.TestCase):
    def test_hallucinations_ignore_terminal_punctuation(self):
        ns = load_definitions("is_hallucination", assignments={"HALLUCINATIONS"})
        guard = ns["is_hallucination"]
        for text in ("!", ".", "Thank you!", "THANK YOU...", "Bye?"):
            with self.subTest(text=text):
                self.assertTrue(guard(text))
        self.assertFalse(guard("This is real speech!"))

    def test_simple_fillers_stay_on_the_fast_path(self):
        ns = load_definitions(
            "quick_clean", "needs_llm_cleanup",
            assignments={
                "SIMPLE_FILLER_RE", "AMBIGUOUS_FILLER_RE", "COMMAND_RE",
                "ENUM_RE",
            },
            extra={"compile_cleanup": compile_cleanup},
        )
        self.assertEqual(ns["quick_clean"]("um this is ready"), "This is ready.")
        self.assertEqual(
            ns["quick_clean"]("I, um, think this is ready"),
            "I think this is ready.",
        )
        self.assertFalse(ns["needs_llm_cleanup"](
            "um this is ready", None, False))
        self.assertTrue(ns["needs_llm_cleanup"](
            "you know this is ready", None, False))
        self.assertFalse(ns["needs_llm_cleanup"](
            "Tuesday actually Wednesday", None, False))

    def test_clean_long_dictation_stays_on_the_fast_path(self):
        ns = load_definitions(
            "needs_llm_cleanup",
            extra={"compile_cleanup": compile_cleanup},
        )
        raw = (
            "Regarding our one script setup, make sure it is smart enough "
            "to detect whether it is running on Windows or Mac, and install "
            "the appropriate resources for that platform. It should install "
            "everything needed so it works just as well as it does on this "
            "MacBook without requiring any manual follow-up steps."
        )
        self.assertGreater(len(raw.split()), 40)
        self.assertFalse(ns["needs_llm_cleanup"](raw, None, False))

    def test_llm_cleanup_has_a_short_read_deadline(self):
        seen = {}

        def fake_ollama_chat(*_args, **kwargs):
            seen.update(kwargs)
            return json.dumps({"text": "Ready.", "edits": []}), "stop"

        ns = load_definitions(
            "_guard_cleaned_output", "llm_clean_with_edits",
            assignments={
                "BASE_PROMPT", "FEW_SHOT", "LLM_CLEANUP_TIMEOUT",
                "MODE_INSTRUCTIONS", "REFUSAL_RE", "STRUCTURED_OUTPUT",
            },
            extra={
                "CleanupEdit": object,
                "ollama_chat": fake_ollama_chat,
                "quick_clean": lambda text: text,
            },
        )
        cleaned, edits = ns["llm_clean_with_edits"](
            "Ready.", "Keep the tone neutral.")
        self.assertEqual(cleaned, "Ready.")
        self.assertEqual(edits, [])
        self.assertEqual(seen["timeout"], (1, 4))

    def test_structured_output_guard_rejects_destructive_results(self):
        ns = load_definitions(
            "_guard_cleaned_output",
            assignments={"REFUSAL_RE"},
        )
        guard = ns["_guard_cleaned_output"]
        self.assertEqual(
            guard("Keep every important word in this sentence",
                  "Keep words", "stop", "capture"),
            "over-deletion",
        )
        self.assertEqual(
            guard("Ship API v2 at 15:30", "Ship it later.", "stop", "compose"),
            "missing factual anchors",
        )
        self.assertEqual(
            guard("A complete source sentence", "", "stop", "edit"),
            "empty or truncated",
        )


class ConfigurationTests(unittest.TestCase):
    def test_model_repository_is_resolved_only_once_per_process(self):
        downloads = []

        def download(repo_id):
            downloads.append(repo_id)
            return f"/models/{repo_id.replace('/', '--')}"

        ns = load_definitions(
            "resolve_asr_model",
            assignments={"ASR_MODEL_PATHS", "ASR_MODEL_PATHS_LOCK"},
            extra={"IS_MACOS": True},
        )
        first = ns["resolve_asr_model"]("org/tiny", downloader=download)
        second = ns["resolve_asr_model"]("org/tiny", downloader=download)
        self.assertEqual(first, "/models/org--tiny")
        self.assertEqual(second, first)
        self.assertEqual(downloads, ["org/tiny"])

    def test_transcript_log_keeps_performance_metrics(self):
        ns = load_definitions(
            "append_transcript",
            extra={"os": os, "time": time},
        )
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "transcripts.jsonl"
            ns.update({
                "TRANSCRIPTS_FILE": transcript,
                "TRANSCRIPTS_LOCK": threading.Lock(),
            })
            ns["append_transcript"](
                "raw", "clean", "app", "fast",
                metrics={"release_s": 0.12, "asr_engine": "tiny"},
            )
            entry = json.loads(transcript.read_text())
            self.assertEqual(entry["metrics"]["release_s"], 0.12)
            self.assertEqual(entry["metrics"]["asr_engine"], "tiny")

    def test_wrong_shaped_tones_file_degrades_to_empty_map(self):
        ns = load_definitions("load_app_tones")
        with tempfile.TemporaryDirectory() as td:
            tones = Path(td) / "tones.json"
            tones.write_text("[]")
            ns.update({
                "TONES_FILE": tones,
                "APP_TONES": {"map": {}, "lock": threading.Lock()},
            })
            ns["load_app_tones"]()
            self.assertEqual(ns["APP_TONES"]["map"], {})

    def test_wrong_shaped_snippets_file_is_ignored(self):
        ns = load_definitions(
            "match_snippet",
            assignments={"SNIPPET_RE"},
        )
        with tempfile.TemporaryDirectory() as td:
            snippets = Path(td) / "snippets.json"
            snippets.write_text("[]")
            ns["SNIPPETS_FILE"] = snippets
            self.assertIsNone(ns["match_snippet"]("insert email"))


class LearningTests(unittest.TestCase):
    def test_wrong_shaped_learned_state_uses_safe_defaults(self):
        ns = load_definitions("load_learned")
        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "learned.json"
            learned.write_text(
                json.dumps({"counts": [], "processed": "all", "fixes": 1})
            )
            ns["LEARNED_FILE"] = learned
            self.assertEqual(
                ns["load_learned"](),
                {"counts": {}, "processed": 0, "fixes": {},
                 "confusions": {}, "history": []},
            )

    def test_merge_preserves_corrections_written_during_mining(self):
        ns = load_definitions("merge_learned_state")
        base = {"counts": {"Qwen": 1}, "processed": 0, "fixes": {}}
        mined = {
            "counts": {"Qwen": 2, "MLX": 1},
            "processed": 10,
            "fixes": {},
        }
        latest = {
            "counts": {"Qwen": 2},
            "processed": 0,
            "fixes": {"gwen": {"to": "Qwen", "n": 1}},
        }
        merged = ns["merge_learned_state"](base, mined, latest)
        self.assertEqual(merged["counts"], {"Qwen": 3, "MLX": 1})
        self.assertEqual(merged["fixes"], latest["fixes"])
        self.assertEqual(merged["processed"], 10)


class ReleasePlanTests(unittest.TestCase):
    def test_active_speech_waits_before_submitting_the_remainder(self):
        ns = load_definitions(
            "release_should_wait_for_tail",
            assignments={"SAMPLE_RATE", "TAIL_SKIP_SILENCE"},
        )
        active = SimpleNamespace(voiced_since_cut=True, silent_samples=0)
        silent = SimpleNamespace(
            voiced_since_cut=True,
            silent_samples=int(ns["SAMPLE_RATE"] * 0.2),
        )
        self.assertTrue(ns["release_should_wait_for_tail"](active))
        self.assertFalse(ns["release_should_wait_for_tail"](silent))

    def test_remainder_is_decoded_exactly_once(self):
        class Future:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        class Pool:
            def __init__(self):
                self.submissions = 0

            def submit(self, function, audio, *args):
                self.submissions += 1
                return Future(function(audio, *args))

        pool = Pool()
        ns = load_definitions(
            "assemble_raw",
            extra={
                "np": SimpleNamespace(ndarray=object),
                "SAMPLE_RATE": 16_000,
                "GATE_PEAK_RMS": 0.002,
                "ASR_POOL": pool,
                "transcribe_detailed": lambda audio, prompt=None:
                    Recognition("remainder", verified=True, engine="tiny"),
                "peak_rms": lambda audio: 0.1,
                "is_hallucination": lambda text: False,
                "Recognition": Recognition,
            },
        )
        audio = [0.1] * 4_000

        self.assertEqual(
            ns["assemble_raw"]([], Future("already queued"), audio).text,
            "already queued",
        )
        self.assertEqual(pool.submissions, 0)

        result = ns["assemble_raw"]([], None, audio)
        self.assertEqual(result.text, "remainder")
        self.assertTrue(result.verified)
        self.assertEqual(result.engine, "tiny")
        self.assertEqual(pool.submissions, 1)

    def test_tiny_first_cascade_skips_large_when_confident(self):
        class ImmediatePool:
            def __init__(self):
                self.calls = []

            def submit(self, function, *args):
                self.calls.append(args[-1])
                return SimpleNamespace(result=lambda: function(*args))

        pool = ImmediatePool()

        def detailed(audio, prompt, verify, model):
            confidence = 0.9 if model == "tiny" else 1.0
            return Recognition(model, confidence)

        ns = load_definitions(
            "_speculative_frames",
            extra={
                "np": np,
                "Recognition": Recognition,
                "ASR_POOL": pool,
                "transcribe_detailed": detailed,
                "FAST_WHISPER_REPO": "tiny",
                "WHISPER_REPO": "large",
                "FAST_ACCEPT_CONFIDENCE": 0.70,
            },
        )
        result = ns["_speculative_frames"](
            [np.ones((100, 1), dtype=np.float32)], still_valid=lambda: True)
        self.assertEqual(result.text, "tiny")
        self.assertEqual(pool.calls, ["tiny"])

    def test_tiny_first_cascade_escalates_low_confidence(self):
        class ImmediatePool:
            def __init__(self):
                self.calls = []

            def submit(self, function, *args):
                self.calls.append(args[-1])
                return SimpleNamespace(result=lambda: function(*args))

        pool = ImmediatePool()

        def detailed(audio, prompt, verify, model):
            return Recognition(model, 0.5 if model == "tiny" else 0.9)

        ns = load_definitions(
            "_speculative_frames",
            extra={
                "np": np,
                "Recognition": Recognition,
                "ASR_POOL": pool,
                "transcribe_detailed": detailed,
                "FAST_WHISPER_REPO": "tiny",
                "WHISPER_REPO": "large",
                "FAST_ACCEPT_CONFIDENCE": 0.70,
            },
        )
        result = ns["_speculative_frames"](
            [np.ones((100, 1), dtype=np.float32)], still_valid=lambda: True)
        self.assertEqual(result.text, "large")
        self.assertEqual(result.alternative, "tiny")
        self.assertTrue(result.verified)
        self.assertEqual(pool.calls, ["tiny", "large"])


if __name__ == "__main__":
    unittest.main()
