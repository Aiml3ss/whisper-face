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
import io
import json
import os
import re
import struct
import subprocess
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

from parrot_core import (  # noqa: E402
    Recognition,
    RecognitionWord,
    compile_cleanup,
    infer_revised_insertion,
)
from voice_compiler import (  # noqa: E402
    ContextCandidate,
    ContextPack,
    RecognitionHypothesis,
    VoiceCompiler,
    VoiceIR,
    WordEvidence,
)
from personal_regression import PersonalRegressionLab  # noqa: E402
from insertion_integrity import (  # noqa: E402
    DestinationObservation,
    InsertionCoordinator,
    InsertionLease,
    ReadbackResult,
    ReceiptState,
)

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


class ParakeetClientTests(unittest.TestCase):
    def test_native_helper_protocol_keeps_audio_in_memory(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(
                    b'{"ready":true,"load_s":0.1}\n'
                    b'{"ok":true,"text":"hello","processing_s":0.02}\n')
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

        process = FakeProcess()
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "parrot-asr-helper"
            helper.write_bytes(b"helper")
            ns = load_definitions(
                "ParakeetClient",
                extra={
                    "PARAKEET_HELPER": helper,
                    "PARAKEET_ENABLED": True,
                    "Path": Path,
                    "subprocess": subprocess,
                    "threading": threading,
                    "struct": struct,
                    "json": json,
                    "np": np,
                },
            )
            client = ns["ParakeetClient"](
                helper=helper, process_factory=lambda *_args, **_kwargs: process)
            result = client.transcribe(np.array([0.25, -0.5], dtype=np.float32))

        self.assertEqual(result, ("hello", 0.02))
        payload = process.stdin.getvalue()
        self.assertEqual(struct.unpack("<Q", payload[:8])[0], 2)
        self.assertEqual(len(payload), 8 + 2 * 4)


class FacePreferenceTests(unittest.TestCase):
    def test_face_choice_persists_and_unknown_values_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            ns = load_definitions(
                "normalize_face", "current_face", "load_preferences",
                "save_preferences",
                assignments={"FACE_CHOICES", "DEFAULT_FACE", "PREFERENCES"},
                extra={
                    "PREFERENCES_FILE": preferences,
                    "json": json,
                    "atomic_write_text": lambda path, value:
                        path.write_text(value, encoding="utf-8"),
                },
            )
            preferences.write_text(json.dumps({
                "flight_recorder": True,
                "face": "dragon",
            }), encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["current_face"](), "parrot")
            self.assertTrue(ns["PREFERENCES"]["flight_recorder"])

            ns["PREFERENCES"]["face"] = "FOX"
            ns["save_preferences"]()
            saved = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(saved, {
                "flight_recorder": True,
                "face": "fox",
            })

    def test_all_default_faces_are_supported(self):
        ns = load_definitions(
            "normalize_face",
            assignments={"FACE_CHOICES", "DEFAULT_FACE"},
        )
        self.assertEqual(
            tuple(ns["normalize_face"](face) for face in ns["FACE_CHOICES"]),
            ("parrot", "fox", "owl", "cat", "bear"),
        )


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
        self.assertEqual(ns["quick_clean"]("already punctuated,"),
                         "Already punctuated.")
        self.assertEqual(ns["quick_clean"]("Two things:"), "Two things:")
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
                "STRUCTURED_FEW_SHOT": [],
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

    def test_live_caption_publishes_only_the_compiler_stable_prefix(self):
        caption = {"text": "Listening"}
        ns = load_definitions(
            "_caption_add",
            extra={
                "Recognition": Recognition,
                "CAPTION": caption,
                "compile_voice_evidence": lambda *_args, **_kwargs: (
                    None, SimpleNamespace(stable_prefix="stable words")),
                "is_hallucination": lambda _text: False,
            },
        )
        future = SimpleNamespace(result=lambda: Recognition(
            "stable words provisional tail"))
        ns["_caption_add"](future)
        self.assertEqual(caption["text"], "stable words")

    def test_active_context_is_compiled_before_cleanup(self):
        ns = load_definitions(
            "compile_voice_evidence",
            extra={
                "Recognition": Recognition,
                "WordEvidence": WordEvidence,
                "RecognitionHypothesis": RecognitionHypothesis,
                "ContextCandidate": ContextCandidate,
                "ContextPack": ContextPack,
                "VoiceIR": VoiceIR,
                "VOICE_COMPILER": VoiceCompiler(),
                "learned_alternatives": lambda *_args: [],
                "compiler_personal_priors": lambda _bundle: (),
                "np": np,
                "analyze_prosody": lambda *_args: (),
                "SAMPLE_RATE": 16_000,
            },
        )
        _voice, result = ns["compile_voice_evidence"](
            Recognition("Use Gwen here", confidence=0.7, engine="tiny"),
            ["Qwen"],
            "com.openai.codex",
        )
        self.assertEqual(result.text, "Use Qwen here")


class ConfigurationTests(unittest.TestCase):
    def test_model_repository_is_resolved_only_once_per_process(self):
        downloads = []

        def download(repo_id, revision=None):
            downloads.append((repo_id, revision))
            return f"/models/{repo_id.replace('/', '--')}"

        ns = load_definitions(
            "resolve_asr_model",
            assignments={
                "ASR_MODEL_PATHS", "ASR_MODEL_PATHS_LOCK",
                "ASR_MODEL_REVISIONS",
            },
            extra={"IS_MACOS": True},
        )
        first = ns["resolve_asr_model"]("org/tiny", downloader=download)
        second = ns["resolve_asr_model"]("org/tiny", downloader=download)
        self.assertEqual(first, "/models/org--tiny")
        self.assertEqual(second, first)
        self.assertEqual(downloads, [("org/tiny", None)])

    def test_production_asr_repositories_have_immutable_revisions(self):
        ns = load_definitions(
            assignments={"ASR_MODEL_REVISIONS", "PARAKEET_MODEL_REVISION"})
        revisions = ns["ASR_MODEL_REVISIONS"]
        self.assertEqual(set(revisions), {
            "mlx-community/whisper-tiny",
            "mlx-community/whisper-large-v3-turbo",
            "tiny",
            "turbo",
        })
        for revision in (*revisions.values(), ns["PARAKEET_MODEL_REVISION"]):
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_ollama_manifest_verifier_rejects_model_tag_drift(self):
        import hashlib

        previous = os.environ.get("OLLAMA_MODELS")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["OLLAMA_MODELS"] = td
                manifest = (
                    Path(td) / "manifests" / "registry.ollama.ai" /
                    "library" / "qwen3.5" / "4b")
                manifest.parent.mkdir(parents=True)
                manifest.write_bytes(b"audited manifest")
                ns = load_definitions(
                    "verify_ollama_model_manifest",
                    assignments={
                        "OLLAMA_MODEL", "OLLAMA_MODEL_MANIFEST_SHA256"},
                    extra={"hashlib": hashlib, "os": os, "Path": Path},
                )
                ns["OLLAMA_MODEL_MANIFEST_SHA256"] = hashlib.sha256(
                    manifest.read_bytes()).hexdigest()
                ns["verify_ollama_model_manifest"]()
                manifest.write_bytes(b"different model")
                with self.assertRaisesRegex(RuntimeError, "manifest drift"):
                    ns["verify_ollama_model_manifest"]()
        finally:
            if previous is None:
                os.environ.pop("OLLAMA_MODELS", None)
            else:
                os.environ["OLLAMA_MODELS"] = previous

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
                "USAGE_CACHE": {
                    "at": 0.0, "value": (0, 0.0),
                    "lock": threading.Lock()},
            })
            ns["append_transcript"](
                "raw", "clean", "app", "fast",
                metrics={"release_s": 0.12, "asr_engine": "tiny"},
            )
            entry = json.loads(transcript.read_text())
            self.assertEqual(entry["metrics"]["release_s"], 0.12)
            self.assertEqual(entry["metrics"]["asr_engine"], "tiny")

    def test_paste_outcome_updates_only_its_receipted_record(self):
        ns = load_definitions(
            "append_transcript", "record_paste_outcome",
            extra={
                "os": os,
                "time": time,
                "PasteReceipt": object,
                "atomic_write_text": lambda path, value: path.write_text(value),
            },
        )
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "transcripts.jsonl"
            ns.update({
                "TRANSCRIPTS_FILE": transcript,
                "TRANSCRIPTS_LOCK": threading.Lock(),
                "USAGE_CACHE": {
                    "at": 0.0, "value": (0, 0.0),
                    "lock": threading.Lock()},
            })
            ns["append_transcript"](
                "raw", "clean", "app", "fast", event_id="receipt-1")
            receipt = SimpleNamespace(
                event_id="receipt-1", pasted="clean")
            self.assertTrue(ns["record_paste_outcome"](
                receipt, "corrected"))
            entry = json.loads(transcript.read_text())
            self.assertEqual(entry["observed_text"], "corrected")
            self.assertFalse(entry["metrics"]["zero_edit"])

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

    def test_network_source_offer_names_license_and_corresponding_source(self):
        ns = load_definitions(
            "source_metadata",
            assignments={"PROJECT_SOURCE_URL"},
            extra={
                "os": os,
                "source_revision": lambda: (
                    "0123456789abcdef0123456789abcdef01234567"),
            },
        )
        metadata = ns["source_metadata"]()
        self.assertEqual(metadata["license"], "AGPL-3.0-only")
        self.assertEqual(
            metadata["source"],
            "https://github.com/Aiml3ss/whispering-parrot/tree/"
            "0123456789abcdef0123456789abcdef01234567",
        )
        self.assertEqual(
            metadata["source_revision"],
            "0123456789abcdef0123456789abcdef01234567",
        )
        self.assertTrue(metadata["license_policy"].endswith("LICENSE_POLICY.md"))
        self.assertIn("without warranty", metadata["warranty"].lower())

class LearningTests(unittest.TestCase):
    def test_snippet_edit_is_persisted_and_exposed_as_learning(self):
        ns = load_definitions(
            "load_learned", "save_learned", "save_snippet_edit",
            extra={
                "Path": Path,
                "atomic_write_text": lambda path, value: path.write_text(value),
                "time": SimpleNamespace(time=lambda: 123.0),
            },
        )
        with tempfile.TemporaryDirectory() as td:
            snippets = Path(td) / "snippets.json"
            learned = Path(td) / "learned.json"
            snippets.write_text(json.dumps({
                "address": "EDIT ME: your street address",
            }))
            ns.update({
                "SNIPPETS_FILE": snippets,
                "LEARNED_FILE": learned,
                "SNIPPETS_LOCK": threading.Lock(),
                "LEARN_LOCK": threading.Lock(),
            })

            changed = ns["save_snippet_edit"](
                "address",
                "EDIT ME: your street address",
                "7623 Opal Ridge Lane, Bainbridge Island, 98110 WA",
                "com.openai.codex",
            )

            self.assertTrue(changed)
            self.assertEqual(
                json.loads(snippets.read_text())["address"],
                "7623 Opal Ridge Lane, Bainbridge Island, 98110 WA",
            )
            state = json.loads(learned.read_text())
        self.assertEqual(
            state["snippet_edits"]["address"]["to"],
            "7623 Opal Ridge Lane, Bainbridge Island, 98110 WA",
        )
        self.assertEqual(state["snippet_edits"]["address"]["n"], 1)
        self.assertEqual(state["history"][-1]["kind"], "snippet_edit")

    def test_snippet_snapshot_is_taken_before_paste_and_observed(self):
        events = []
        receipt = SimpleNamespace(pasted="EDIT ME: your street address")
        ns = load_definitions(
            "paste_snippet_and_watch",
            extra={
                "threading": threading,
                "focused_snapshot": lambda: events.append("snapshot") or
                    "focus",
                "make_paste_receipt": lambda snapshot, pasted, bundle, mode:
                    receipt if snapshot == "focus" else None,
                "paste": lambda text: events.append(("paste", text)),
                "learn_snippet_edit": lambda name, value:
                    events.append(("learn", name, value)),
            },
        )

        returned = ns["paste_snippet_and_watch"](
            "address", "EDIT ME: your street address",
            "com.openai.codex", "capture",
            starter=lambda target, args: events.append(("start", target, args)),
        )

        self.assertIs(returned, receipt)
        self.assertEqual(events[0], "snapshot")
        self.assertEqual(events[1], ("paste", "EDIT ME: your street address"))
        self.assertEqual(events[2][0], "start")
        self.assertEqual(events[2][2], ("address", receipt))

    def test_snippet_observer_saves_the_revised_insertion(self):
        saved = []
        ns = load_definitions(
            "learn_snippet_edit",
            extra={
                "observe_paste_outcome": lambda _receipt:
                    "7623 Opal Ridge Lane, Bainbridge Island, 98110 WA",
                "save_snippet_edit": lambda name, old, new, bundle:
                    saved.append((name, old, new, bundle)) or True,
            },
        )
        receipt = SimpleNamespace(
            pasted="EDIT ME: your street address",
            bundle="com.openai.codex",
        )

        ns["learn_snippet_edit"]("address", receipt)

        self.assertEqual(saved, [(
            "address",
            "EDIT ME: your street address",
            "7623 Opal Ridge Lane, Bainbridge Island, 98110 WA",
            "com.openai.codex",
        )])

    def test_unverified_snippet_paste_never_starts_learning(self):
        events = []
        ns = load_definitions(
            "paste_snippet_and_watch",
            extra={
                "PasteReceipt": object,
                "ReceiptState": ReceiptState,
                "focused_snapshot": lambda: "focus",
                "make_paste_receipt": lambda *_args: object(),
                "commit_insertion": lambda *_args: SimpleNamespace(
                    state=ReceiptState.UNVERIFIABLE,
                    paste_attempted=True,
                ),
                "learn_snippet_edit": lambda *_args: events.append("learn"),
            },
        )

        returned = ns["paste_snippet_and_watch"](
            "address", "private text", "com.openai.codex", "capture",
            starter=lambda *_args: events.append("start"),
            rec=SimpleNamespace(),
        )

        self.assertIsNone(returned)
        self.assertEqual(events, [])

    def test_early_correction_reaches_learned_state(self):
        elapsed = [0.0]
        observed = []
        fake_time = SimpleNamespace(
            monotonic=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(
                0, elapsed[0] + seconds),
            time=lambda: 1.0,
        )
        ns = load_definitions(
            "load_learned", "save_learned", "observe_paste_outcome",
            "personal_regression_lab", "learn_from_corrections",
            extra={
                "Path": Path,
                "PasteReceipt": object,
                "atomic_write_text": lambda path, value: path.write_text(value),
                "time": fake_time,
                "difflib": __import__("difflib"),
                "correction_similarity": lambda _old, _new: 0.8,
                "infer_revised_insertion": infer_revised_insertion,
                "parse_dictionary": lambda: ([], set()),
                "record_paste_outcome": lambda _receipt, value:
                    observed.append(value),
                "refresh_glossary": lambda: None,
                "PersonalRegressionLab": PersonalRegressionLab,
                "PERSONAL_APP_MIN_COUNT": 2,
                "PERSONAL_GLOBAL_MIN_COUNT": 3,
            },
        )
        values = iter(["Hello Gwen world", "Hello Qwen world", ""])
        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "learned.json"
            ns.update({
                "LEARNED_FILE": learned,
                "LEARN_LOCK": threading.Lock(),
                "CORRECTION_DELAY": 10.0,
                "CORRECTION_POLL_INTERVAL": 0.2,
                "CORRECTION_MAX_LEARN": 3,
                "PROMOTE_MIN_COUNT": 2,
                "_ax_text": lambda _element: next(values),
            })
            receipt = SimpleNamespace(
                element=object(),
                before="Hello  world",
                selection=(6, 0),
                pasted="Gwen",
                bundle="com.openai.codex",
                mode="capture",
            )

            ns["learn_from_corrections"](receipt)

            below_threshold = json.loads(learned.read_text())
            values = iter(["Hello Gwen world", "Hello Qwen world"])
            ns["learn_from_corrections"](receipt)

            state = json.loads(learned.read_text())
            values = iter(["Hello Gwen world", "Hello Qwen world"])
            ns["learn_from_corrections"](receipt)
            global_state = json.loads(learned.read_text())
        self.assertEqual(observed, ["Qwen", "Qwen", "Qwen"])
        self.assertEqual(
            below_threshold["regression_lab"]["promoted"], [])
        self.assertEqual(state["fixes"]["gwen"], {"to": "Qwen", "n": 2})
        self.assertEqual(state["confusions"]["gwen->qwen"]["n"], 2)
        self.assertEqual(len(state["regression_lab"]["cases"]), 1)
        self.assertEqual(state["regression_lab"]["promoted"], [{
            "heard": "Gwen",
            "preferred": "Qwen",
            "app": "com.openai.codex",
        }])
        self.assertEqual(
            [item["app"] for item in global_state["regression_lab"]["promoted"]],
            [None, "com.openai.codex"],
        )

    def test_correction_is_observed_before_chat_composer_clears(self):
        ns = load_definitions(
            "observe_paste_outcome",
            extra={
                "PasteReceipt": object,
                "infer_revised_insertion": infer_revised_insertion,
            },
        )
        receipt = SimpleNamespace(
            element=object(),
            before="Hello  world",
            selection=(6, 0),
            pasted="Gwen",
        )
        values = iter(["Hello Gwen world", "Hello Qwen world", ""])
        elapsed = [0.0]

        observed = ns["observe_paste_outcome"](
            receipt,
            timeout=10.0,
            poll_interval=0.2,
            reader=lambda _element: next(values),
            clock=lambda: elapsed[0],
            sleeper=lambda seconds: elapsed.__setitem__(
                0, elapsed[0] + seconds),
        )

        self.assertEqual(observed, "Qwen")
        self.assertLess(elapsed[0], 10.0)

    def test_cleared_chat_composer_is_not_reported_as_unchanged(self):
        ns = load_definitions(
            "observe_paste_outcome",
            extra={
                "PasteReceipt": object,
                "infer_revised_insertion": infer_revised_insertion,
            },
        )
        receipt = SimpleNamespace(
            element=object(), before="", selection=(0, 0), pasted="Gwen")
        values = ["Gwen", ""]
        elapsed = [0.0]

        observed = ns["observe_paste_outcome"](
            receipt,
            timeout=0.4,
            poll_interval=0.2,
            reader=lambda _element: values.pop(0) if values else "",
            clock=lambda: elapsed[0],
            sleeper=lambda seconds: elapsed.__setitem__(
                0, elapsed[0] + seconds),
        )

        self.assertIsNone(observed)

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
                 "confusions": {}, "snippet_edits": {},
                 "regression_lab": {}, "history": []},
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


class InsertionAdapterTests(unittest.TestCase):
    def test_fresh_ax_wrapper_for_same_field_keeps_the_destination_lease(self):
        original_element = object()
        current_element = object()
        original = SimpleNamespace(
            element=original_element, text="", selection=(0, 0),
            window_title="Composer")
        current = SimpleNamespace(
            element=current_element, text="", selection=(0, 0),
            window_title="Composer")
        ns = load_definitions(
            "bounded_focus_text", "focus_destination_id",
            "focus_destination_matches", "opaque_focus_context",
            "capture_insertion_lease", "destination_observation",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "_ax_elements_equal": lambda left, right:
                    (left is original_element and right is current_element),
            },
        )
        lease = ns["capture_insertion_lease"](
            original, "com.openai.codex", "utterance-1")

        observation = ns["destination_observation"](
            current,
            "com.openai.codex",
            lease,
            original,
            "com.openai.codex",
        )

        self.assertEqual(observation.destination_id, lease.destination_id)
        self.assertEqual(observation.selection, lease.selection)
        self.assertEqual(
            observation.surrounding_fingerprint,
            lease.surrounding_fingerprint,
        )

    def test_runtime_commit_accepts_equivalent_ax_wrappers_for_same_field(self):
        original_element = object()
        current_element = object()
        original = SimpleNamespace(
            element=original_element, text="", selection=(0, 0),
            window_title="Composer")
        current = SimpleNamespace(
            element=current_element, text="", selection=(0, 0),
            window_title="Composer")
        coordinator = InsertionCoordinator()
        lease = InsertionLease.capture(
            "utterance-1", "com.openai.codex:original", (0, 0), "")
        rec = SimpleNamespace(
            insertion_lease=lease,
            insertion_receipt=None,
            focus_at_press=original,
            bundle_at_press="com.openai.codex",
        )
        pasted = []
        pipeline = {}
        ns = load_definitions(
            "bounded_focus_text", "focus_destination_id",
            "focus_destination_matches", "opaque_focus_context",
            "destination_observation", "commit_insertion",
            extra={
                "FocusSnapshot": object,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "_ax_elements_equal": lambda left, right:
                    (left is original_element and right is current_element),
                "insertion_readback": lambda *_args:
                    ReadbackResult.verified(),
                "paste": pasted.append,
                "PIPELINE_STATE": pipeline,
                "frontmost_bundle": lambda: "com.openai.codex",
            },
        )

        receipt = ns["commit_insertion"](
            rec, "Ship it", "com.openai.codex", current)

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(pipeline["last_insertion_state"], "verified")

    def test_capture_lease_uses_a_bounded_cursor_neighborhood(self):
        ns = load_definitions(
            "bounded_focus_text", "focus_destination_id",
            "opaque_focus_context", "capture_insertion_lease",
            extra={"FocusSnapshot": object, "InsertionLease": InsertionLease},
        )
        snapshot = SimpleNamespace(
            element=object(),
            text="a" * 500 + "cursor" + "b" * 500,
            selection=(506, 0),
        )

        lease = ns["capture_insertion_lease"](
            snapshot, "com.openai.codex", "utterance-1")

        self.assertIsNotNone(lease)
        self.assertEqual(lease.selection, (506, 0))
        self.assertNotIn("a" * 200, repr(lease))
        self.assertNotIn("b" * 200, repr(lease))

    def test_readback_proves_the_exact_field_mutation(self):
        ns = load_definitions(
            "insertion_readback",
            extra={
                "FocusSnapshot": object,
                "ReadbackResult": ReadbackResult,
                "time": time,
            },
        )
        snapshot = SimpleNamespace(
            element=object(), text="Hello  world", selection=(6, 0))

        result = ns["insertion_readback"](
            snapshot,
            "Qwen",
            reader=lambda _element: "Hello Qwen world",
        )

        self.assertEqual(result.state, ReceiptState.VERIFIED)

    def test_hidden_field_lease_still_binds_to_original_application(self):
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "destination_observation",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
            },
        )
        lease = ns["capture_insertion_lease"](
            None, "com.apple.Terminal", "opaque-1")

        current = ns["destination_observation"](
            None, "com.openai.codex", lease)

        self.assertTrue(lease.opaque)
        self.assertNotEqual(current.destination_id, lease.destination_id)

    def test_runtime_commit_refuses_destination_drift_without_pasting(self):
        coordinator = InsertionCoordinator()
        lease = InsertionLease.capture(
            "utterance-1", "field-a", (0, 0), "nearby")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None)
        pasted = []
        pipeline = {}
        ns = load_definitions(
            "commit_insertion",
            extra={
                "INSERTION_COORDINATOR": coordinator,
                "destination_observation": lambda _snapshot, _bundle, _lease,
                *_args:
                    DestinationObservation.capture(
                        "field-b", (0, 0), "nearby"),
                "insertion_readback": lambda *_args: ReadbackResult.verified(),
                "paste": pasted.append,
                "PIPELINE_STATE": pipeline,
                "frontmost_bundle": lambda: "com.other.app",
            },
        )

        receipt = ns["commit_insertion"](
            rec, "do not paste", "com.openai.codex", object())

        self.assertEqual(pasted, [])
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(pipeline["last_insertion_state"], "conflict")

    def test_outbox_clipboard_failure_does_not_acknowledge_payload(self):
        acknowledgements = []
        item = SimpleNamespace(
            text="recover me",
            receipt=SimpleNamespace(utterance_id="utterance-1"),
        )

        class Pasteboard:
            def clearContents(self):
                return None

            def setString_forType_(self, _text, _kind):
                return False

        coordinator = SimpleNamespace(
            recoverable=lambda: (item,),
            acknowledge=acknowledgements.append,
        )
        ns = load_definitions(
            "copy_latest_outbox",
            extra={
                "INSERTION_COORDINATOR": coordinator,
                "NSPasteboard": SimpleNamespace(
                    generalPasteboard=lambda: Pasteboard()),
                "NSPasteboardTypeString": "public.utf8-plain-text",
            },
        )

        with self.assertRaises(RuntimeError):
            ns["copy_latest_outbox"]()
        self.assertEqual(acknowledgements, [])


class PersonalPriorIntegrationTests(unittest.TestCase):
    def test_unpromoted_case_does_not_disable_established_legacy_fix(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")
        ns = load_definitions(
            "apply_learned_fixes",
            extra={
                "GLOSS": {
                    "lock": threading.Lock(),
                    "fixes": {"gwen": "Qwen"},
                    "confusions": {},
                    "regression": lab,
                },
            },
        )

        self.assertEqual(
            ns["apply_learned_fixes"](
                "Use Gwen", "com.openai.codex"),
            "Use Qwen",
        )
        self.assertEqual(
            ns["apply_learned_fixes"]("Use Gwen", "com.apple.Notes"),
            "Use Qwen",
        )

    def test_regression_lab_app_prior_does_not_reach_other_apps(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")
        lab.propose("Gwen", "Qwen", app="com.openai.codex")
        ns = load_definitions(
            "compiler_personal_priors",
            extra={
                "GLOSS": {
                    "lock": threading.Lock(),
                    "fixes": {},
                    "confusions": {},
                    "regression": lab,
                },
                "PersonalPrior": __import__(
                    "voice_compiler").PersonalPrior,
            },
        )

        matching = ns["compiler_personal_priors"]("com.openai.codex")
        other = ns["compiler_personal_priors"]("com.apple.Notes")

        self.assertEqual([(p.heard, p.preferred) for p in matching], [
            ("Gwen", "Qwen")])
        self.assertEqual(other, ())


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

    def test_assembly_offsets_word_evidence_across_chunks(self):
        class Future:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        ns = load_definitions(
            "assemble_raw",
            extra={
                "np": SimpleNamespace(ndarray=object),
                "SAMPLE_RATE": 16_000,
                "GATE_PEAK_RMS": 0.002,
                "ASR_POOL": SimpleNamespace(),
                "transcribe_detailed": None,
                "peak_rms": lambda _audio: 0.0,
                "is_hallucination": lambda _text: False,
                "Recognition": Recognition,
                "RecognitionWord": RecognitionWord,
            },
        )
        first = Recognition(
            "hello", engine="tiny", audio_duration=1.0,
            words=(RecognitionWord("hello", 0.2, 0.5, 0.9),),
        )
        second = Recognition(
            "world", engine="turbo", audio_duration=0.8,
            words=(RecognitionWord("world", 0.1, 0.4, 0.8),),
        )
        result = ns["assemble_raw"](
            [Future(first)], Future(second), [], None)
        self.assertEqual(result.text, "hello world")
        self.assertAlmostEqual(result.words[1].start, 1.1)
        self.assertAlmostEqual(result.audio_duration, 1.8)

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
                "IS_MACOS": False,
                "PARAKEET_ENABLED": False,
                "PARAKEET_HELPER": Path("missing"),
            },
        )
        result = ns["_speculative_frames"](
            [np.ones((100, 1), dtype=np.float32)], still_valid=lambda: True)
        self.assertEqual(result.text, "tiny")
        self.assertEqual(pool.calls, ["tiny"])

    def test_mac_parakeet_route_verifies_even_confident_tiny(self):
        class ImmediatePool:
            def __init__(self):
                self.calls = []

            def submit(self, function, *args):
                self.calls.append(args[-1])
                return SimpleNamespace(result=lambda: function(*args))

        pool = ImmediatePool()

        def detailed(audio, prompt, verify, model):
            confidence = 0.99 if model == "tiny" else 0.84
            return Recognition(model, confidence)

        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "parrot-asr-helper"
            helper.write_bytes(b"helper")
            ns = load_definitions(
                "_speculative_frames",
                extra={
                    "np": np,
                    "Recognition": Recognition,
                    "ASR_POOL": pool,
                    "transcribe_detailed": detailed,
                    "FAST_WHISPER_REPO": "tiny",
                    "WHISPER_REPO": "parakeet-or-whisper-fallback",
                    "FAST_ACCEPT_CONFIDENCE": 0.70,
                    "IS_MACOS": True,
                    "PARAKEET_ENABLED": True,
                    "PARAKEET_HELPER": helper,
                },
            )
            result = ns["_speculative_frames"](
                [np.ones((100, 1), dtype=np.float32)],
                still_valid=lambda: True,
            )

        self.assertEqual(result.text, "parakeet-or-whisper-fallback")
        self.assertEqual(result.alternative, "tiny")
        self.assertTrue(result.verified)
        self.assertEqual(pool.calls, ["tiny", "parakeet-or-whisper-fallback"])

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
                "IS_MACOS": False,
                "PARAKEET_ENABLED": False,
                "PARAKEET_HELPER": Path("missing"),
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
