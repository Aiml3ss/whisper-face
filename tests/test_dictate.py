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
from dataclasses import dataclass
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
from acoustic_keyword_memory import (  # noqa: E402
    AcousticKeywordMemory,
    hash_app_scope,
)
from acoustic_time_machine import AcousticTimeMachine  # noqa: E402
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
    # Production uses modern annotations throughout. Execute the selected AST
    # with postponed annotation evaluation so this harness behaves consistently
    # on every supported Python, including the Windows runner's Python 3.12.
    future_annotations = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations")],
        level=0,
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[future_annotations, *selected],
        type_ignores=[],
    ))
    exec(compile(module, "dictate-selected", "exec"), namespace)
    return namespace


class DictationSuccessSoundTests(unittest.TestCase):
    def test_mac_review_route_uses_advisory_ping(self):
        select = load_definitions(
            "dictation_success_sound")["dictation_success_sound"]

        self.assertEqual(select("review", is_macos=True), "Ping")

    def test_other_routes_and_windows_keep_standard_success_sound(self):
        select = load_definitions(
            "dictation_success_sound")["dictation_success_sound"]

        self.assertEqual(select("standard", is_macos=True), "Pop")
        self.assertEqual(select("review", is_macos=False), "Pop")


class RecognitionMenuTitleTests(unittest.TestCase):
    def test_review_route_marks_last_recognition_for_review(self):
        title = load_definitions(
            "recognition_root_title")["recognition_root_title"]

        self.assertEqual(title("review"), "Last Recognition — Review")

    def test_all_other_or_invalid_routes_keep_neutral_title(self):
        title = load_definitions(
            "recognition_root_title")["recognition_root_title"]

        self.assertEqual(title("Review"), "Last Recognition")
        self.assertEqual(title("review-needed"), "Last Recognition")
        self.assertEqual(title(None), "Last Recognition")


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

    def test_parakeet_processing_time_reaches_recognition_evidence(self):
        ns = load_definitions(
            "transcribe_detailed",
            extra={
                "np": np,
                "GLOSS": {"lock": threading.Lock(), "prompt": None},
                "IS_MACOS": True,
                "WHISPER_REPO": "large",
                "FAST_WHISPER_REPO": "tiny",
                "PARAKEET_ENABLED": True,
                "PARAKEET": SimpleNamespace(
                    transcribe=lambda _audio: ("hello", 0.125)),
                "PARAKEET_ROUTE_CONFIDENCE": 0.9,
                "SAMPLE_RATE": 16_000,
                "Recognition": Recognition,
            },
        )

        result = ns["transcribe_detailed"](
            np.ones(1_600, dtype=np.float32), model_repo="large")

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.engine, "parakeet-unified")
        self.assertEqual(result.native_processing_s, 0.125)

        ns["PARAKEET"] = SimpleNamespace(
            transcribe=lambda _audio: ("hello", float("nan")))
        invalid = ns["transcribe_detailed"](
            np.ones(1_600, dtype=np.float32), model_repo="large")
        self.assertIsNone(invalid.native_processing_s)


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
                    "IS_MACOS": True,
                    "ACOUSTIC_TIME_MACHINE": AcousticTimeMachine(),
                    "json": json,
                    "atomic_write_text": lambda path, value:
                        path.write_text(value, encoding="utf-8"),
                },
            )
            preferences.write_text(json.dumps({
                "flight_recorder": True,
                "acoustic_time_machine": True,
                "voice_object_commands": False,
                "face": "dragon",
            }), encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["current_face"](), "parrot")
            self.assertTrue(ns["PREFERENCES"]["flight_recorder"])
            self.assertTrue(ns["PREFERENCES"]["acoustic_time_machine"])
            self.assertFalse(ns["PREFERENCES"]["voice_object_commands"])

            ns["PREFERENCES"]["face"] = "FOX"
            ns["save_preferences"]()
            saved = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(saved, {
                "flight_recorder": True,
                "acoustic_time_machine": True,
                "voice_object_commands": False,
                "face": "fox",
            })

    def test_privacy_preferences_are_mac_only_and_default_off(self):
        for is_macos, acoustic, voice_objects, expected in (
                (True, True, True, True), (True, False, False, False),
                (False, True, True, False)):
            with self.subTest(is_macos=is_macos, acoustic=acoustic,
                              voice_objects=voice_objects):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "preferences.json"
                    path.write_text(json.dumps({
                        "acoustic_time_machine": acoustic,
                        "voice_object_commands": voice_objects,
                    }), encoding="utf-8")
                    buffer = AcousticTimeMachine()
                    ns = load_definitions(
                        "normalize_face", "load_preferences",
                        assignments={"FACE_CHOICES", "DEFAULT_FACE", "PREFERENCES"},
                        extra={
                            "PREFERENCES_FILE": path,
                            "IS_MACOS": is_macos,
                            "ACOUSTIC_TIME_MACHINE": buffer,
                            "json": json,
                        },
                    )
                    ns["load_preferences"]()
                    self.assertIs(
                        ns["PREFERENCES"]["acoustic_time_machine"], expected)
                    self.assertIs(
                        ns["PREFERENCES"]["voice_object_commands"], expected)
                    self.assertIs(buffer.enabled, expected)

    def test_all_default_faces_are_supported(self):
        ns = load_definitions(
            "normalize_face",
            assignments={"FACE_CHOICES", "DEFAULT_FACE"},
        )
        self.assertEqual(
            tuple(ns["normalize_face"](face) for face in ns["FACE_CHOICES"]),
            ("parrot", "fox", "owl", "cat", "bear"),
        )

    def test_reduce_motion_freezes_hud_audio_animation(self):
        ns = load_definitions(
            "hud_level_step", assignments={"LEVEL_SMOOTH"})

        self.assertEqual(ns["hud_level_step"](0.9, 0.7, "recording", True), 0.0)
        self.assertGreater(
            ns["hud_level_step"](0.9, 0.0, "recording", False), 0.0)


class AcousticKeywordMemoryRuntimeTests(unittest.TestCase):
    @staticmethod
    def runtime_namespace(path: Path):
        namespace = load_definitions(
            "atomic_write_text",
            "_load_acoustic_keyword_memory",
            "acoustic_keyword_memory_status_snapshot",
            "export_acoustic_keyword_memory",
            "remember_explicit_acoustic_keyword_correction",
            "forget_acoustic_keyword",
            "forget_all_acoustic_keywords",
            assignments={"ACOUSTIC_KEYWORD_MEMORY_LOCK"},
            extra={
                "AcousticKeywordMemory": AcousticKeywordMemory,
                "os": os,
                "tempfile": tempfile,
                "Path": Path,
            },
        )
        namespace["ACOUSTIC_KEYWORD_MEMORY_FILE"] = path
        return namespace

    def test_exact_correction_signal_populates_global_memory_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acoustic_keyword_memory.json"
            namespace = self.runtime_namespace(path)

            self.assertFalse(
                namespace["remember_explicit_acoustic_keyword_correction"](
                    "Qwen", evidence_id=""))
            self.assertTrue(
                namespace["remember_explicit_acoustic_keyword_correction"](
                    "Qwen", evidence_id="opaque-1"))
            self.assertTrue(
                namespace["remember_explicit_acoustic_keyword_correction"](
                    "Qwen", evidence_id="opaque-1"))
            self.assertTrue(
                namespace["remember_explicit_acoustic_keyword_correction"](
                    "Qwen", evidence_id="opaque-2"))

            candidate = AcousticKeywordMemory.loads(
                path.read_text(encoding="utf-8")).candidates[0]
            self.assertEqual(candidate.keyword, "Qwen")
            self.assertIsNone(candidate.app_scope)
            self.assertEqual(
                (candidate.observations, candidate.confirmations), (2, 2))
            encoded = path.read_text(encoding="utf-8")
            self.assertNotIn("opaque-1", encoded)
            self.assertNotIn("opaque-2", encoded)

    def test_status_is_bounded_and_omits_keywords_evidence_and_app_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acoustic_keyword_memory.json"
            raw_app = "com.example.private-notes"
            scope = hash_app_scope(raw_app, salt=b"private-test-salt")
            memory = AcousticKeywordMemory()
            memory.observe(
                "SecretProjectName", evidence_id="private-utterance-1",
                app_scope=scope)
            path.write_text(memory.dumps(), encoding="utf-8")
            namespace = self.runtime_namespace(path)

            status = namespace["acoustic_keyword_memory_status_snapshot"]()
            encoded = json.dumps(status)

            self.assertEqual(status["storage_status"], "ready")
            self.assertEqual(status["candidate_count"], 1)
            self.assertEqual(status["eligible_count"], 0)
            self.assertEqual(status["recognition_effect"], "none")
            self.assertEqual(
                status["candidate_summaries"][0]["scope_hash"], scope)
            for private_value in (
                    "SecretProjectName", "private-utterance-1", raw_app,
                    "observation_tokens", "confirmation_tokens"):
                self.assertNotIn(private_value, encoded)

    def test_explicit_export_includes_keyword_but_not_evidence_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acoustic_keyword_memory.json"
            memory = AcousticKeywordMemory()
            memory.observe("Qwen", evidence_id="private-utterance-1")
            path.write_text(memory.dumps(), encoding="utf-8")
            namespace = self.runtime_namespace(path)

            exported = namespace["export_acoustic_keyword_memory"]()
            encoded = json.dumps(exported)

            self.assertEqual(exported["candidates"][0]["keyword"], "Qwen")
            self.assertNotIn("private-utterance-1", encoded)
            self.assertNotIn("observation_tokens", encoded)

    def test_explicit_export_callback_copies_only_the_token_free_projection(self):
        copied = []

        class Pasteboard:
            def clearContents(self):
                return None

            def setString_forType_(self, value, kind):
                copied.append((value, kind))
                return True

        namespace = load_definitions(
            "copy_acoustic_keyword_memory_export",
            extra={
                "json": json,
                "export_acoustic_keyword_memory": lambda: {
                    "candidates": [{"keyword": "Qwen", "observations": 1}],
                },
                "NSPasteboard": SimpleNamespace(
                    generalPasteboard=lambda: Pasteboard()),
                "NSPasteboardTypeString": "public.utf8-plain-text",
            },
        )

        namespace["copy_acoustic_keyword_memory_export"]()

        self.assertEqual(copied[0][1], "public.utf8-plain-text")
        self.assertIn('"keyword": "Qwen"', copied[0][0])
        self.assertNotIn("observation_tokens", copied[0][0])

    def test_exact_and_all_forget_actions_atomically_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acoustic_keyword_memory.json"
            memory = AcousticKeywordMemory()
            memory.observe("Qwen", evidence_id="one")
            memory.observe("Codex", evidence_id="two")
            path.write_text(memory.dumps(), encoding="utf-8")
            namespace = self.runtime_namespace(path)

            self.assertTrue(namespace["forget_acoustic_keyword"]("Qwen"))
            restored = AcousticKeywordMemory.loads(
                path.read_text(encoding="utf-8"))
            self.assertEqual(
                [candidate.keyword for candidate in restored.candidates],
                ["Codex"],
            )
            self.assertEqual(namespace["forget_all_acoustic_keywords"](), 1)
            self.assertEqual(
                AcousticKeywordMemory.loads(
                    path.read_text(encoding="utf-8")).candidates,
                (),
            )
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_malformed_state_fails_closed_until_explicit_forget_all(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acoustic_keyword_memory.json"
            path.write_text(
                '{"keyword":"must-not-activate","transcript":"private"}',
                encoding="utf-8",
            )
            namespace = self.runtime_namespace(path)

            status = namespace["acoustic_keyword_memory_status_snapshot"]()
            self.assertEqual(status["storage_status"], "invalid")
            self.assertEqual(status["candidate_count"], 0)
            self.assertEqual(status["candidate_summaries"], [])
            with self.assertRaisesRegex(ValueError, "malformed"):
                namespace["export_acoustic_keyword_memory"]()
            with self.assertRaisesRegex(ValueError, "forget all"):
                namespace["forget_acoustic_keyword"]("must-not-activate")
            self.assertFalse(
                namespace["remember_explicit_acoustic_keyword_correction"](
                    "Qwen", evidence_id="opaque-correction"))

            self.assertEqual(namespace["forget_all_acoustic_keywords"](), 0)
            self.assertEqual(
                AcousticKeywordMemory.loads(
                    path.read_text(encoding="utf-8")).candidates,
                (),
            )

    def test_final_processing_path_does_not_read_keyword_memory(self):
        finish = next(
            node for node in TREE.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "finish_and_process"
        )
        names = {
            node.id for node in ast.walk(finish) if isinstance(node, ast.Name)
        }
        self.assertFalse(any("acoustic_keyword" in name for name in names))

        status = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot"
        )
        status_names = {
            node.id for node in ast.walk(status) if isinstance(node, ast.Name)
        }
        self.assertIn("acoustic_keyword_memory_status_snapshot", status_names)


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
        self.assertEqual(pool.readiness(), "Starting")
        pool.warm()
        self.assertEqual(pool.readiness(), "Ready")
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

    def test_failed_microphone_warmup_is_reported_as_unavailable(self):
        def denied(**_kwargs):
            raise RuntimeError("private device detail")

        ns = load_definitions(
            "AudioSlot", "AudioPool",
            assignments={"SAMPLE_RATE"},
            extra={"sd": SimpleNamespace(InputStream=denied)},
        )
        pool = ns["AudioPool"](size=2, stream_factory=denied)

        with self.assertRaises(RuntimeError):
            pool.warm()

        self.assertEqual(pool.readiness(), "Unavailable")
        self.assertEqual(pool.slots, [])
        self.assertEqual(pool.warm_error, "RuntimeError")

    def test_runtime_microphone_start_failure_changes_readiness(self):
        class FailsAfterWarmup(FakeStream):
            def start(self):
                super().start()
                if self.starts > 1:
                    raise RuntimeError("device disappeared")

        def factory(**kwargs):
            return FailsAfterWarmup(**kwargs)

        ns = load_definitions(
            "AudioSlot", "AudioPool",
            assignments={"SAMPLE_RATE"},
            extra={"sd": SimpleNamespace(InputStream=factory)},
        )
        pool = ns["AudioPool"](size=1, stream_factory=factory)
        pool.warm()

        with self.assertRaises(RuntimeError):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))

        self.assertEqual(pool.readiness(), "Unavailable")


class PerformanceTraceTests(unittest.TestCase):
    def test_trace_emitter_accepts_only_fixed_finite_numeric_schemas(self):
        lines = []
        ns = load_definitions(
            "emit_performance_trace",
            assignments={
                "PERFORMANCE_TRACE_PREFIX",
                "PERFORMANCE_TRACE_SCHEMA_VERSION",
                "PERFORMANCE_TRACE_SCHEMAS",
            },
            extra={"math": __import__("math"), "print": lines.append},
        )
        emit = ns["emit_performance_trace"]

        self.assertTrue(emit(
            "warmup_asr_tiny", {"duration_ms": 12.34567, "success": 1}))
        payload = json.loads(lines.pop().removeprefix("[trace] "))
        self.assertEqual(payload, {
            "duration_ms": 12.3457,
            "event": "warmup_asr_tiny",
            "schema_version": 1,
            "success": 1.0,
        })

        self.assertFalse(emit(
            "warmup_asr_tiny",
            {"duration_ms": 1, "success": 1, "transcript": "private"},
        ))
        self.assertFalse(emit(
            "warmup_asr_tiny", {"duration_ms": float("nan"), "success": 1}))
        self.assertFalse(emit(
            "arbitrary-event", {"duration_ms": 1, "success": 1}))
        self.assertFalse(emit(
            ["warmup_asr_tiny"], {"duration_ms": 1, "success": 1}))
        self.assertEqual(lines, [])

    def test_trace_output_failure_never_masks_the_wrapped_operation(self):
        def output_failed(_line):
            raise OSError("stdout closed")

        clock_values = iter((1.0, 1.01))
        ns = load_definitions(
            "emit_performance_trace", "trace_operation",
            assignments={
                "PERFORMANCE_TRACE_PREFIX",
                "PERFORMANCE_TRACE_SCHEMA_VERSION",
                "PERFORMANCE_TRACE_SCHEMAS",
            },
            extra={
                "math": __import__("math"),
                "print": output_failed,
                "time": SimpleNamespace(
                    perf_counter=lambda: next(clock_values)),
            },
        )

        self.assertFalse(ns["emit_performance_trace"](
            "warmup_asr_tiny", {"duration_ms": 1, "success": 1}))
        self.assertEqual(
            ns["trace_operation"]("warmup_asr_tiny", lambda: "ready"),
            "ready",
        )

    def test_acoustic_trace_failure_preserves_original_gate_measurements(self):
        ns = load_definitions(
            "peak_rms",
            "emit_performance_trace",
            "emit_acoustic_trace",
            "audio_gate_measurements",
            assignments={
                "MIN_SECONDS",
                "PERFORMANCE_TRACE_PREFIX",
                "PERFORMANCE_TRACE_SCHEMA_VERSION",
                "PERFORMANCE_TRACE_SCHEMAS",
                "SAMPLE_RATE",
            },
            extra={
                "math": __import__("math"),
                "np": np,
                "print": lambda _line: None,
            },
        )
        audio = np.full(16_000, 0.04, dtype=np.float32)

        def statistics_failed(*_args, **_kwargs):
            raise RuntimeError("diagnostics failed")

        ns["acoustic_statistics"] = statistics_failed
        duration, peak = ns["audio_gate_measurements"](audio)

        self.assertEqual(duration, len(audio) / 16_000)
        self.assertAlmostEqual(peak, ns["peak_rms"](audio))

    def test_acoustic_statistics_are_finite_and_content_free(self):
        ns = load_definitions(
            "peak_rms", "acoustic_statistics",
            assignments={"SAMPLE_RATE", "SILENCE_RMS"},
            extra={"math": __import__("math"), "np": np},
        )
        stats = ns["acoustic_statistics"](np.array([
            0.0, np.nan, np.inf, -np.inf, 1.2, -0.5,
        ], dtype=np.float32), sample_rate=6)

        self.assertEqual(set(stats), {
            "adaptive_threshold",
            "clipped_ratio",
            "derived_gain_factor",
            "duration_ms",
            "frame_rms_p20",
            "frame_rms_p50",
            "frame_rms_p95",
            "nonfinite_ratio",
            "peak_amplitude",
            "peak_rms",
            "rms",
            "sample_count",
            "sample_rate_hz",
            "silence_ratio",
            "trailing_silence_ms",
            "voiced_fraction",
        })
        self.assertEqual(stats["sample_count"], 6.0)
        self.assertEqual(stats["duration_ms"], 1000.0)
        self.assertAlmostEqual(stats["nonfinite_ratio"], 0.5)
        self.assertTrue(all(
            isinstance(value, float) and np.isfinite(value)
            for value in stats.values()))
        self.assertEqual(
            ns["acoustic_statistics"](np.array([], dtype=np.float32), 16_000),
            {
                "adaptive_threshold": 0.0,
                "clipped_ratio": 0.0,
                "derived_gain_factor": 1.0,
                "duration_ms": 0.0,
                "frame_rms_p20": 0.0,
                "frame_rms_p50": 0.0,
                "frame_rms_p95": 0.0,
                "nonfinite_ratio": 0.0,
                "peak_amplitude": 0.0,
                "peak_rms": 0.0,
                "rms": 0.0,
                "sample_count": 0.0,
                "sample_rate_hz": 16000.0,
                "silence_ratio": 0.0,
                "trailing_silence_ms": 0.0,
                "voiced_fraction": 0.0,
            },
        )

    def test_acoustic_statistics_distinguish_quiet_noisy_and_clipped_audio(self):
        ns = load_definitions(
            "peak_rms", "acoustic_statistics",
            assignments={"SAMPLE_RATE", "SILENCE_RMS"},
            extra={"math": __import__("math"), "np": np},
        )
        measure = ns["acoustic_statistics"]
        sample_rate = 1_000
        phase = np.arange(sample_rate, dtype=np.float64)
        quiet = measure(0.004 * np.sin(2 * np.pi * phase / 20), sample_rate)
        noisy = measure(np.tile(
            np.concatenate((np.full(20, 0.01), np.full(20, 0.04))),
            25,
        ), sample_rate)
        clipped = measure(np.ones(sample_rate, dtype=np.float64), sample_rate)

        self.assertEqual(quiet["derived_gain_factor"], 8.0)
        self.assertEqual(quiet["voiced_fraction"], 0.0)
        self.assertGreater(noisy["frame_rms_p95"], noisy["frame_rms_p20"])
        self.assertGreater(noisy["adaptive_threshold"], 0.008)
        self.assertAlmostEqual(noisy["voiced_fraction"], 0.5)
        self.assertEqual(clipped["clipped_ratio"], 1.0)
        self.assertEqual(clipped["derived_gain_factor"], 1.0)
        self.assertEqual(clipped["voiced_fraction"], 1.0)

    def test_acoustic_statistics_measure_trailing_silence(self):
        ns = load_definitions(
            "peak_rms", "acoustic_statistics",
            assignments={"SAMPLE_RATE", "SILENCE_RMS"},
            extra={"math": __import__("math"), "np": np},
        )
        audio = np.concatenate((
            np.full(100, 0.1, dtype=np.float64),
            np.zeros(200, dtype=np.float64),
        ))
        stats = ns["acoustic_statistics"](audio, sample_rate=1_000)

        self.assertEqual(stats["trailing_silence_ms"], 200.0)
        self.assertAlmostEqual(stats["voiced_fraction"], 1.0 / 3.0)
        self.assertEqual(stats["frame_rms_p20"], 0.0)
        self.assertEqual(stats["frame_rms_p50"], 0.0)
        self.assertAlmostEqual(stats["frame_rms_p95"], 0.1)

    def test_warmup_emits_deterministic_stage_and_total_traces(self):
        lines = []
        clock_values = iter((
            0.0,
            0.01, 0.03,
            0.03, 0.08,
            0.08, 0.10,
            0.11,
        ))

        class ImmediatePool:
            @staticmethod
            def submit(function, *args):
                return SimpleNamespace(result=lambda: function(*args))

        ns = load_definitions(
            "emit_performance_trace", "trace_operation", "warmup",
            assignments={
                "FAST_WHISPER_REPO",
                "PERFORMANCE_TRACE_PREFIX",
                "PERFORMANCE_TRACE_SCHEMA_VERSION",
                "PERFORMANCE_TRACE_SCHEMAS",
                "SAMPLE_RATE",
            },
            extra={
                "ASR_POOL": ImmediatePool(),
                "PARAKEET_ENABLED": False,
                "PARAKEET_HELPER": SimpleNamespace(is_file=lambda: False),
                "PIPELINE_STATE": {"cleanup_status": "Checking"},
                "SERVER_ONLY": False,
                "IS_MACOS": True,
                "np": SimpleNamespace(
                    float32="float32", zeros=lambda _size, dtype: []),
                "ollama_chat": lambda *_args, **_kwargs: ("ok", "stop"),
                "transcribe": lambda *_args: "",
                "transcribe_detailed": lambda *_args: SimpleNamespace(text=""),
                "time": SimpleNamespace(
                    perf_counter=lambda: next(clock_values)),
                "math": __import__("math"),
                "print": lines.append,
            },
        )

        ns["warmup"]()

        traces = [
            json.loads(line.removeprefix("[trace] "))
            for line in lines if line.startswith("[trace] ")
        ]
        self.assertEqual(
            [trace["event"] for trace in traces],
            [
                "warmup_asr_tiny",
                "warmup_asr_final",
                "warmup_ollama",
                "warmup_total",
            ],
        )
        self.assertEqual(
            [trace["duration_ms"] for trace in traces],
            [20.0, 50.0, 20.0, 110.0],
        )
        self.assertTrue(all(trace["success"] == 1.0 for trace in traces))


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

    def test_single_ordinal_prose_stays_fast_in_capture_and_code_modes(self):
        ns = load_definitions(
            "needs_llm_cleanup",
            extra={"compile_cleanup": compile_cleanup},
        )
        raw = (
            "The second thing regarding the audio is that the microphone "
            "should remain warm between dictations."
        )
        plan = compile_cleanup(raw)

        for mode in ("capture", "code"):
            with self.subTest(mode=mode):
                self.assertFalse(ns["needs_llm_cleanup"](
                    raw, None, False, mode, plan))

    def test_numbered_marker_list_stays_on_the_fast_path(self):
        ns = load_definitions(
            "quick_clean", "needs_llm_cleanup",
            extra={"compile_cleanup": compile_cleanup},
        )
        raw = (
            "The two things and feedback still does not list out items here, "
            "right? So listing, here's one as a test, and here's two as a "
            "test."
        )

        self.assertFalse(ns["needs_llm_cleanup"](raw, None, False))
        self.assertEqual(
            ns["quick_clean"](raw),
            "The two things and feedback still does not list out items here, "
            "right? So listing:\n"
            "- Here's one as a test.\n"
            "- Here's two as a test.",
        )
        feedback = (
            "Here's some feedback items. One, this is great. Two, this is "
            "not so great."
        )
        self.assertFalse(ns["needs_llm_cleanup"](feedback, None, False))
        self.assertEqual(
            ns["quick_clean"](feedback),
            "Here's some feedback items:\n"
            "- This is great.\n"
            "- This is not so great.",
        )
        mixed = "Two things. One test. Second, test."
        self.assertFalse(ns["needs_llm_cleanup"](mixed, None, False))
        self.assertEqual(
            ns["quick_clean"](mixed),
            "Two things:\n- Test.\n- Test.",
        )

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

    def test_llm_edit_kind_is_canonicalized_before_status_projection(self):
        private_kind = "customer said secret launch phrase"

        def fake_ollama_chat(*_args, **_kwargs):
            return json.dumps({
                "text": "Ready.",
                "edits": [{
                    "kind": private_kind,
                    "before": "Ready",
                    "after": "Ready.",
                }],
            }), "stop"

        ns = load_definitions(
            "canonical_llm_edit_kind", "_guard_cleaned_output",
            "llm_clean_with_edits",
            assignments={
                "BASE_PROMPT", "FEW_SHOT", "LLM_CLEANUP_TIMEOUT",
                "LLM_EDIT_KINDS", "MODE_INSTRUCTIONS", "REFUSAL_RE",
                "STRUCTURED_OUTPUT",
            },
            extra={
                "CleanupEdit": lambda kind, before, after: SimpleNamespace(
                    kind=kind, before=before, after=after),
                "ollama_chat": fake_ollama_chat,
                "quick_clean": lambda text: text,
                "STRUCTURED_FEW_SHOT": [],
            },
        )

        _cleaned, edits = ns["llm_clean_with_edits"](
            "Ready", "Keep the tone neutral.")

        self.assertEqual(edits[0].kind, "semantic_cleanup")
        self.assertNotIn("secret", edits[0].kind)

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
    def test_mlx_progress_uses_thread_lock_not_multiprocessing_semaphore(self):
        received = []

        class FakeProgress:
            @classmethod
            def set_lock(cls, lock):
                received.append(lock)

        ns = load_definitions(
            "configure_mlx_progress_lock",
            extra={"threading": threading},
        )
        ns["configure_mlx_progress_lock"](
            SimpleNamespace(tqdm=FakeProgress))

        self.assertEqual(len(received), 1)
        self.assertEqual(type(received[0]), type(threading.RLock()))

    def test_runtime_model_resolution_is_local_only_and_memoized(self):
        downloads = []

        def download(repo_id, revision=None, local_files_only=None):
            downloads.append((repo_id, revision, local_files_only))
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
        self.assertEqual(downloads, [("org/tiny", None, True)])

    def test_installer_model_resolution_explicitly_allows_downloads(self):
        downloads = []

        def download(repo_id, revision=None, local_files_only=None):
            downloads.append((repo_id, revision, local_files_only))
            return f"/models/{repo_id.replace('/', '--')}"

        ns = load_definitions(
            "resolve_asr_model",
            assignments={
                "ASR_MODEL_PATHS", "ASR_MODEL_PATHS_LOCK",
                "ASR_MODEL_REVISIONS",
            },
            extra={"IS_MACOS": True},
        )

        resolved = ns["resolve_asr_model"](
            "org/turbo", downloader=download, local_files_only=False)

        self.assertEqual(resolved, "/models/org--turbo")
        self.assertEqual(downloads, [("org/turbo", None, False)])

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

    def test_transcript_log_closes_descriptor_without_fchmod(self):
        class OsWithoutFchmod:
            O_WRONLY = os.O_WRONLY
            O_CREAT = os.O_CREAT
            O_APPEND = os.O_APPEND

            def __init__(self):
                self.fd = None

            def open(self, *args, **kwargs):
                self.fd = os.open(*args, **kwargs)
                return self.fd

            @staticmethod
            def fdopen(*args, **kwargs):
                return os.fdopen(*args, **kwargs)

            @staticmethod
            def close(fd):
                os.close(fd)

        portable_os = OsWithoutFchmod()
        ns = load_definitions(
            "append_transcript",
            extra={"os": portable_os, "time": time},
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
            ns["append_transcript"]("raw", "clean", "app", "fast")
            self.assertEqual(json.loads(transcript.read_text())["clean"], "clean")
            with self.assertRaises(OSError):
                os.fstat(portable_os.fd)

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
        remembered = []
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
                "remember_explicit_acoustic_keyword_correction":
                    lambda keyword, evidence_id:
                    remembered.append((keyword, evidence_id)) or True,
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
                event_id="opaque-correction-event",
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
        self.assertEqual(remembered, [
            ("Qwen", "opaque-correction-event:0"),
            ("Qwen", "opaque-correction-event:0"),
            ("Qwen", "opaque-correction-event:0"),
        ])
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

    def test_vocabulary_mining_excludes_unverified_and_outbox_text(self):
        ns = load_definitions("parse_texts", extra={"json": json})
        lines = [
            json.dumps({
                "clean": "verified phrase",
                "path": "fast",
                "metrics": {"insertion_verified": True},
            }),
            json.dumps({
                "clean": "private unverified phrase",
                "path": "fast",
                "metrics": {"insertion_verified": False},
            }),
            json.dumps({
                "clean": "legacy outbox phrase",
                "path": "outbox/fast",
            }),
            json.dumps({"clean": "legacy verified phrase", "path": "fast"}),
        ]

        self.assertEqual(ns["parse_texts"](lines), [
            "verified phrase",
            "legacy verified phrase",
        ])


class InsertionAdapterTests(unittest.TestCase):
    def test_no_ax_element_fails_closed_instead_of_pasting_by_window(self):
        coordinator = InsertionCoordinator()
        pasted = []
        pipeline = {}
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "destination_observation",
            "commit_insertion",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "frontmost_window_destination": lambda _bundle: None,
                "user_input_signature": lambda: None,
                "frontmost_bundle": lambda: "com.openai.codex",
                "paste": pasted.append,
                "PIPELINE_STATE": pipeline,
            },
        )
        lease = ns["capture_insertion_lease"](
            None, "com.openai.codex", "utterance-1")
        rec = SimpleNamespace(
            insertion_lease=lease,
            insertion_receipt=None,
            focus_at_press=None,
            bundle_at_press="com.openai.codex",
            input_signature_at_press="10:20:30:40:50",
        )

        receipt = ns["commit_insertion"](
            rec, "Keep this recoverable", "com.openai.codex", None)

        self.assertEqual(pasted, [])
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)
        self.assertEqual(pipeline["last_insertion_state"], "unverifiable")
        self.assertEqual(len(coordinator.recoverable()), 1)

    def test_reviewed_opaque_editor_pastes_when_window_and_input_are_stable(self):
        coordinator = InsertionCoordinator()
        pasted = []
        pipeline = {}
        destination = "com.openai.codex:42:7"
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "seal_opaque_window_lease",
            "destination_observation", "commit_insertion",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "frontmost_window_destination": lambda _bundle: destination,
                "user_input_signature": lambda: "10:20:30:40:50",
                "frontmost_bundle": lambda: "com.openai.codex",
                "paste": pasted.append,
                "PIPELINE_STATE": pipeline,
            },
        )
        rec = SimpleNamespace(
            insertion_lease=ns["capture_insertion_lease"](
                None, "com.openai.codex", "opaque-1"),
            insertion_receipt=None,
            focus_at_press=None,
            bundle_at_press="com.openai.codex",
            input_signature_at_press="10:20:30:40:50",
        )
        ns["seal_opaque_window_lease"](rec)

        receipt = ns["commit_insertion"](
            rec, "Paste once", "com.openai.codex", None)

        self.assertEqual(pasted, ["Paste once"])
        self.assertTrue(receipt.paste_attempted)
        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)

    def test_reviewed_opaque_editor_rejects_input_during_processing(self):
        coordinator = InsertionCoordinator()
        pasted = []
        destination = "com.openai.codex:42:7"
        signatures = iter(("10:20:30:40:50", "11:20:30:40:50"))
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "seal_opaque_window_lease",
            "destination_observation", "commit_insertion",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "frontmost_window_destination": lambda _bundle: destination,
                "user_input_signature": lambda: next(signatures),
                "frontmost_bundle": lambda: "com.openai.codex",
                "paste": pasted.append,
                "PIPELINE_STATE": {},
            },
        )
        rec = SimpleNamespace(
            insertion_lease=ns["capture_insertion_lease"](
                None, "com.openai.codex", "opaque-1"),
            insertion_receipt=None,
            focus_at_press=None,
            bundle_at_press="com.openai.codex",
            input_signature_at_press="10:20:30:40:50",
        )
        ns["seal_opaque_window_lease"](rec)

        receipt = ns["commit_insertion"](
            rec, "Do not misdirect", "com.openai.codex", None)

        self.assertEqual(pasted, [])
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertEqual(len(coordinator.recoverable()), 1)

    def test_reviewed_opaque_editor_rejects_window_drift(self):
        coordinator = InsertionCoordinator()
        pasted = []
        destinations = iter((
            "com.openai.codex:42:7",
            "com.openai.codex:42:7",
            "com.openai.codex:42:99",
        ))
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "seal_opaque_window_lease",
            "destination_observation", "commit_insertion",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "frontmost_window_destination": lambda _bundle:
                    next(destinations),
                "user_input_signature": lambda: "10:20:30:40:50",
                "frontmost_bundle": lambda: "com.openai.codex",
                "paste": pasted.append,
                "PIPELINE_STATE": {},
            },
        )
        rec = SimpleNamespace(
            insertion_lease=ns["capture_insertion_lease"](
                None, "com.openai.codex", "opaque-1"),
            insertion_receipt=None,
            focus_at_press=None,
            bundle_at_press="com.openai.codex",
            input_signature_at_press="10:20:30:40:50",
        )
        ns["seal_opaque_window_lease"](rec)

        receipt = ns["commit_insertion"](
            rec, "Do not misdirect", "com.openai.codex", None)

        self.assertEqual(pasted, [])
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)

    def test_reviewed_opaque_editor_rejects_input_during_the_hold(self):
        coordinator = InsertionCoordinator()
        pasted = []
        destination = "com.openai.codex:42:7"
        signatures = iter(("11:20:30:40:50", "11:20:30:40:50"))
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "seal_opaque_window_lease",
            "destination_observation", "commit_insertion",
            extra={
                "FocusSnapshot": object,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "frontmost_window_destination": lambda _bundle: destination,
                "user_input_signature": lambda: next(signatures),
                "frontmost_bundle": lambda: "com.openai.codex",
                "paste": pasted.append,
                "PIPELINE_STATE": {},
            },
        )
        rec = SimpleNamespace(
            insertion_lease=ns["capture_insertion_lease"](
                None, "com.openai.codex", "opaque-1"),
            insertion_receipt=None,
            focus_at_press=None,
            bundle_at_press="com.openai.codex",
            input_signature_at_press="10:20:30:40:50",
        )
        ns["seal_opaque_window_lease"](rec)

        receipt = ns["commit_insertion"](
            rec, "Do not misdirect", "com.openai.codex", None)

        self.assertEqual(pasted, [])
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)

    def test_opaque_compatibility_allowlist_rejects_unknown_apps(self):
        ns = load_definitions(
            "frontmost_window_destination",
            assignments={"OPAQUE_WINDOW_COMPAT_BUNDLES"},
            extra={"IS_MACOS": True},
        )

        self.assertIsNone(ns["frontmost_window_destination"](
            "com.example.unknown-editor"))

    def test_opaque_resolution_does_not_wait_for_missing_ax(self):
        calls = []
        rec = SimpleNamespace(
            insertion_lease=InsertionLease.capture_opaque(
                "opaque-1", "com.openai.codex:42:7", "sealed"),
            focus_at_press=None,
            bundle_at_press="com.openai.codex",
        )
        ns = load_definitions(
            "resolve_insertion_target",
            extra={
                "FocusSnapshot": object,
                "focused_snapshot": lambda: calls.append("read") or None,
                "frontmost_bundle": lambda: "com.openai.codex",
                "time": SimpleNamespace(
                    monotonic=lambda: 0.0,
                    sleep=lambda _delay: calls.append("sleep"),
                ),
            },
        )

        self.assertIsNone(ns["resolve_insertion_target"](rec))
        self.assertEqual(calls, ["read"])

    def test_release_retries_a_transient_unreadable_ax_target(self):
        original_element = object()
        current_element = object()
        original = SimpleNamespace(
            element=original_element, text="Draft", selection=(5, 0),
            window_title="Composer")
        recovered = SimpleNamespace(
            element=current_element, text="Draft", selection=(5, 0),
            window_title="Composer")
        lease = InsertionLease.capture(
            "utterance-1", "com.openai.codex:original", (5, 0), "Draft")
        rec = SimpleNamespace(
            insertion_lease=lease,
            focus_at_press=original,
            bundle_at_press="com.openai.codex",
        )
        snapshots = iter((None, recovered))
        now = [0.0]

        def sleep(delay):
            now[0] += delay

        ns = load_definitions(
            "bounded_focus_text", "focus_destination_matches",
            "resolve_insertion_target",
            extra={
                "FocusSnapshot": object,
                "_ax_elements_equal": lambda left, right:
                    (left is original_element and right is current_element),
                "focused_snapshot": lambda: next(snapshots),
                "frontmost_bundle": lambda: "com.openai.codex",
                "time": SimpleNamespace(
                    monotonic=lambda: now[0], sleep=sleep),
            },
        )

        result = ns["resolve_insertion_target"](rec)

        self.assertIs(result, recovered)
        self.assertGreater(now[0], 0.0)

    def test_release_does_not_retry_past_a_real_focus_change(self):
        original_element = object()
        changed_element = object()
        original = SimpleNamespace(
            element=original_element, text="Draft", selection=(5, 0),
            window_title="Composer")
        changed = SimpleNamespace(
            element=changed_element, text="Other", selection=(5, 0),
            window_title="Other field")
        lease = InsertionLease.capture(
            "utterance-1", "com.openai.codex:original", (5, 0), "Draft")
        rec = SimpleNamespace(
            insertion_lease=lease,
            focus_at_press=original,
            bundle_at_press="com.openai.codex",
        )
        calls = []
        ns = load_definitions(
            "bounded_focus_text", "focus_destination_matches",
            "resolve_insertion_target",
            extra={
                "FocusSnapshot": object,
                "_ax_elements_equal": lambda _left, _right: False,
                "focused_snapshot": lambda: calls.append(True) or changed,
                "frontmost_bundle": lambda: "com.openai.codex",
                "time": time,
            },
        )

        result = ns["resolve_insertion_target"](rec)

        self.assertIs(result, changed)
        self.assertEqual(len(calls), 1)

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
                "frontmost_window_destination": lambda _bundle: None,
                "user_input_signature": lambda: None,
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

    def test_support_snapshot_uses_standard_clipboard_and_rejects_empty_payload(self):
        copied = []

        class Pasteboard:
            def clearContents(self):
                copied.append(("cleared",))

            def setString_forType_(self, value, kind):
                copied.append((value, kind))
                return True

        ns = load_definitions(
            "copy_support_snapshot",
            extra={
                "NSPasteboard": SimpleNamespace(
                    generalPasteboard=lambda: Pasteboard()),
                "NSPasteboardTypeString": "public.utf8-plain-text",
            },
        )

        ns["copy_support_snapshot"]('{"schema_version": 1}')

        self.assertEqual(copied, [
            ("cleared",),
            ('{"schema_version": 1}', "public.utf8-plain-text"),
        ])
        with self.assertRaisesRegex(ValueError, "empty"):
            ns["copy_support_snapshot"]("  ")


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
        self.assertEqual({word.timing for word in result.words}, {"segment"})

    def test_bounded_chunks_preserve_absolute_word_timing_across_silence(self):
        class Future:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        ns = load_definitions(
            "BoundedRecognitionFuture",
            "assemble_raw",
            extra={
                "dataclass": dataclass,
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
        bounded = ns["BoundedRecognitionFuture"]
        first = Recognition(
            "hello", engine="tiny", audio_duration=1.0,
            native_processing_s=0.1,
            words=(RecognitionWord("hello", 0.2, 0.5, 0.9),),
        )
        second = Recognition(
            "world", engine="turbo", audio_duration=1.0,
            native_processing_s=0.2,
            words=(RecognitionWord("world", 0.1, 0.4, 0.8),),
        )
        empty = Recognition(
            "", audio_duration=0.5, native_processing_s=0.05)

        result = ns["assemble_raw"]([
            bounded(Future(first), 0, 16_000),
            bounded(Future(second), 32_000, 48_000),
            bounded(Future(empty), 48_000, 56_000),
        ], None, [], None)

        self.assertEqual(result.text, "hello world")
        self.assertEqual([word.timing for word in result.words], [
            "native", "native"])
        self.assertAlmostEqual(result.words[0].start, 0.2)
        self.assertAlmostEqual(result.words[1].start, 2.1)
        self.assertAlmostEqual(result.audio_duration, 3.5)
        self.assertAlmostEqual(result.native_processing_s, 0.35)

    def test_invalid_capture_bounds_fail_closed_without_losing_text(self):
        class Future:
            def result(self):
                return Recognition(
                    "invoice 2042", engine="tiny", audio_duration=0.8,
                    words=(RecognitionWord(
                        "2042", "invalid", 0.7, 0.8, "native"),),
                )

        ns = load_definitions(
            "BoundedRecognitionFuture",
            "assemble_raw",
            extra={
                "dataclass": dataclass,
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
        scheduled = ns["BoundedRecognitionFuture"](
            Future(), 16_000, 8_000)

        result = ns["assemble_raw"]([scheduled], None, [], None)

        self.assertEqual(result.text, "invoice 2042")
        self.assertEqual(result.engine, "tiny")
        self.assertEqual(result.words[0].timing, "segment")

    def test_recorder_attaches_capture_bounds_to_rolling_decode(self):
        class Future:
            def add_done_callback(self, callback):
                self.callback = callback

        future = Future()
        prep_pool = SimpleNamespace(
            submit=lambda *_args, **_kwargs: future)
        ns = load_definitions(
            "BoundedRecognitionFuture",
            "Recorder",
            extra={
                "dataclass": dataclass,
                "np": np,
                "ContextPack": SimpleNamespace,
                "LEVELS": [],
                "SILENCE_RMS": 0.01,
                "SAMPLE_RATE": 16_000,
                "should_start_speculation": lambda *_args: False,
                "SPECULATIVE_MIN_SECONDS": 0.5,
                "SPECULATIVE_SILENCE": 0.1,
                "CHUNK_MIN_SECONDS": 0.5,
                "CHUNK_CUT_SILENCE": 0.1,
                "can_reuse_speculation": lambda *_args: False,
                "CHUNK_PREP_POOL": prep_pool,
                "_transcribe_frames": object(),
                "_caption_add": lambda *_args: None,
            },
        )
        recorder = ns["Recorder"]()
        recorder.recording = True
        recorder.voiced_since_cut = True

        recorder._callback(
            np.zeros((16_000, 1), dtype=np.float32), 16_000, None, None)

        self.assertEqual(len(recorder.chunks), 1)
        scheduled = recorder.chunks[0]
        self.assertIsInstance(scheduled, ns["BoundedRecognitionFuture"])
        self.assertIs(scheduled.future, future)
        self.assertEqual((scheduled.start_sample, scheduled.end_sample), (
            0, 16_000))

    def test_release_attaches_capture_bounds_to_remainder_decode(self):
        class Pool:
            def __init__(self):
                self.future = SimpleNamespace()

            def submit(self, *_args):
                return self.future

        pool = Pool()
        assembled = []

        def assemble(chunk_futures, pre_future, remainder, prompt):
            assembled.append((chunk_futures, pre_future, remainder, prompt))
            return Recognition("")

        ns = load_definitions(
            "BoundedRecognitionFuture",
            "finish_and_process",
            extra={
                "dataclass": dataclass,
                "np": np,
                "time": SimpleNamespace(
                    perf_counter=lambda: 10.0, time=lambda: 10.0),
                "release_should_wait_for_tail": lambda _rec: False,
                "can_reuse_speculation": lambda *_args: False,
                "ASR_POOL": pool,
                "transcribe_detailed": object(),
                "SAMPLE_RATE": 16_000,
                "MIN_SECONDS": 0.25,
                "GATE_PEAK_RMS": 0.002,
                "peak_rms": lambda _audio: 0.1,
                "audio_gate_measurements": lambda _audio: (1.0, 0.1),
                "assemble_raw": assemble,
                "Recognition": Recognition,
                "is_hallucination": lambda _text: False,
                "print": lambda *_args: None,
                "LAST_USE": {},
                "AppHelper": SimpleNamespace(callAfter=lambda *_args: None),
            },
        )
        audio = np.ones(16_000, dtype=np.float32)
        class Recorder:
            released_at = 1.0
            speculative_future = None
            speculative_invalid = False
            speculative_start = 0
            speculative_end = 0
            prompt = None
            recording = False
            uncertain = False

            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True
                return audio

            def snapshot(self):
                raise AssertionError("release plan read before capture stopped")

            @property
            def cut_samples(self):
                if not self.stopped:
                    raise AssertionError("cut read before capture stopped")
                return 8_000

            @property
            def chunks(self):
                if not self.stopped:
                    raise AssertionError("chunks read before capture stopped")
                return []

        recorder = Recorder()

        ns["finish_and_process"](recorder, SimpleNamespace(), {})

        scheduled = assembled[0][1]
        self.assertIsInstance(scheduled, ns["BoundedRecognitionFuture"])
        self.assertIs(scheduled.future, pool.future)
        self.assertEqual((scheduled.start_sample, scheduled.end_sample), (
            8_000, 16_000))

    def test_failed_chunk_downgrades_surviving_word_timing(self):
        class Future:
            def __init__(self, value=None, error=None):
                self.value = value
                self.error = error

            def result(self):
                if self.error is not None:
                    raise self.error
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
        later = Recognition(
            "invoice 2042", engine="tiny", audio_duration=1.0,
            words=(
                RecognitionWord("invoice", 0.1, 0.4, 0.8),
                RecognitionWord("2042", 0.5, 0.8, 0.7),
            ),
        )
        result = ns["assemble_raw"](
            [Future(error=RuntimeError("decode failed"))],
            Future(value=later), [], None)
        self.assertEqual(result.text, "invoice 2042")
        self.assertTrue(result.words)
        self.assertEqual({word.timing for word in result.words}, {"segment"})

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


class ConsequenceRuntimeProjectionTests(unittest.TestCase):
    def _namespace(self):
        pipeline = {}
        ns = load_definitions(
            "_consequence_count",
            "store_consequence_receipt",
            "runtime_consequence_evidence",
            "consequence_state_snapshot",
            extra={
                "time": time,
                "PIPELINE_STATE": pipeline,
                "CONSEQUENCE_RISK_IDS": frozenset({
                    "name", "number", "currency", "date", "time",
                    "recipient", "contact", "url", "path", "command",
                    "action",
                }),
                "CONSEQUENCE_SKIP_IDS": frozenset({
                    "timing-unavailable", "span-not-micro",
                    "selection-limit", "overlapping-span",
                    "verifier-unavailable", "unsafe-verifier-contract",
                    "audio-unavailable", "deadline-expired",
                    "verifier-error", "invalid-verifier-result",
                    "verifier-not-independent", "receipt-error",
                }),
                "CONSEQUENCE_ROUTE_IDS": frozenset({
                    "standard", "protected", "review", "verified",
                    "unavailable",
                }),
                "CONSEQUENCE_RELISTEN_IDS": frozenset({
                    "not-needed", "skipped", "confirmed", "contradicted",
                    "timed-out", "inconclusive", "mixed", "unavailable",
                }),
                "consequence_receipt": lambda *_args, **_kwargs: None,
            },
        )
        return ns, pipeline

    @staticmethod
    def _receipt(**overrides):
        values = {
            "route": "review",
            "risk_counts": (("currency", 1),),
            "high_risks": 1,
            "uncertain_risks": 1,
            "relisten_status": "skipped",
            "relisten_selected": 1,
            "relisten_attempted": 0,
            "relisten_confirmed": 0,
            "relisten_contradicted": 0,
            "relisten_inconclusive": 0,
            "relisten_skipped": (("verifier-unavailable", 1),),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_projection_drops_private_or_unknown_ids(self):
        ns, pipeline = self._namespace()
        private = "Alice private@example.com /secret/path"
        ns["store_consequence_receipt"](self._receipt(
            route=private,
            risk_counts=(("currency", 2), (private, 99)),
            relisten_status=private,
            relisten_skipped=(("timing-unavailable", 1), (private, 99)),
        ))
        snapshot = ns["consequence_state_snapshot"]()
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(private, encoded)
        self.assertEqual(snapshot["route"], "unavailable")
        self.assertEqual(snapshot["risk_counts"], {"currency": 2})
        self.assertEqual(
            snapshot["relisten_skipped"], {"timing-unavailable": 1})
        self.assertEqual(pipeline["last_relisten_status"], "unavailable")

    def test_evaluator_failure_is_fail_open_and_never_records_error_text(self):
        ns, _pipeline = self._namespace()
        audio = np.arange(64, dtype=np.float32)
        private = "private transcript and /Users/private/path"

        def fail(*_args, **_kwargs):
            raise RuntimeError(private)

        elapsed = ns["runtime_consequence_evidence"](
            object(), audio, sample_rate=16_000, audio_duration=0.004,
            evaluator=fail)
        snapshot = ns["consequence_state_snapshot"]()
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(snapshot["route"], "unavailable")
        self.assertEqual(snapshot["relisten_skipped"], {"receipt-error": 1})
        self.assertNotIn(private, json.dumps(snapshot))
        np.testing.assert_array_equal(audio, np.arange(64, dtype=np.float32))

    def test_evidence_only_runtime_seam_stays_below_warm_path_budget(self):
        ns, _pipeline = self._namespace()
        audio = np.zeros(320, dtype=np.float32)
        voice = object()
        seen = []

        def evaluate(received_voice, **kwargs):
            seen.append((received_voice is voice, kwargs["audio"] is audio))
            return self._receipt()

        samples = [ns["runtime_consequence_evidence"](
            voice, audio, sample_rate=16_000, audio_duration=0.02,
            evaluator=evaluate) for _ in range(200)]
        self.assertTrue(all(left and right for left, right in seen))
        self.assertLess(sorted(samples)[189], 0.005)
        self.assertEqual(
            ns["consequence_state_snapshot"]()["route"], "review")


class AcousticTimeMachineRuntimeTests(unittest.TestCase):
    class ManualClock:
        def __init__(self):
            self.value = 100.0

        def __call__(self):
            return self.value

    def _namespace(self, *, enabled):
        buffer = AcousticTimeMachine(enabled=enabled)
        state = {
            "span_ids": [], "play_index": 0, "expires_at": None,
            "expiry_timer": None, "lock": threading.RLock(), "sound": None,
        }
        clock = self.ManualClock()
        ns = load_definitions(
            "_clear_retained_consequence_spans_locked",
            "clear_retained_consequence_spans",
            "expire_retained_consequence_spans",
            "retain_consequence_microspans",
            "acoustic_time_machine_status_snapshot",
            "_retained_span_wav_bytes",
            "play_retained_consequence_span",
            assignments={"ACOUSTIC_TIME_MACHINE_TTL_SECONDS"},
            extra={
                "ACOUSTIC_TIME_MACHINE": buffer,
                "ACOUSTIC_TIME_MACHINE_STATE": state,
                "IS_MACOS": True,
                "PREFERENCES": {"acoustic_time_machine": enabled},
                "SAMPLE_RATE": 16_000,
                "math": __import__("math"),
                "np": np,
                "struct": struct,
                "time": time,
            },
        )
        return ns, buffer, state, clock

    def test_disabled_retention_does_not_read_audio(self):
        class ForbiddenAudio:
            def __len__(self):
                raise AssertionError("disabled retention inspected audio")

            def __getitem__(self, _key):
                raise AssertionError("disabled retention sliced audio")

        ns, buffer, _state, clock = self._namespace(enabled=False)
        plan = SimpleNamespace(relisten_requests=(
            SimpleNamespace(start=0.001, end=0.002),
        ))

        self.assertEqual(ns["retain_consequence_microspans"](
            ForbiddenAudio(), plan, sample_rate=16_000, clock=clock,
            timer_factory=None), 0)
        self.assertEqual(buffer.span_count, 0)

    def test_exact_selected_span_is_retained_and_clear_drops_it(self):
        ns, buffer, state, clock = self._namespace(enabled=True)
        audio = np.linspace(-1.0, 1.0, 64, dtype=np.float32)
        plan = SimpleNamespace(relisten_requests=(
            SimpleNamespace(start=16 / 16_000, end=24 / 16_000),
        ))

        self.assertEqual(ns["retain_consequence_microspans"](
            audio, plan, sample_rate=16_000, clock=clock,
            timer_factory=None), 1)
        retained = buffer.read(state["span_ids"][0]).audio
        self.assertEqual(
            retained.samples, tuple(float(value) for value in audio[16:24]))
        self.assertEqual(ns["acoustic_time_machine_status_snapshot"](
            clock=clock), {
            "enabled": True,
            "retained_spans": 1,
        })

        ns["clear_retained_consequence_spans"]()
        self.assertEqual(buffer.span_count, 0)
        self.assertEqual(state["span_ids"], [])

    def test_ttl_callback_wipes_samples_stops_sound_and_releases_references(self):
        class FakeSound:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

        class FakeTimer:
            def __init__(self, interval, action):
                self.interval = interval
                self.action = action
                self.daemon = False
                self.started = False
                self.cancelled = False

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True

        ns, buffer, state, clock = self._namespace(enabled=True)
        timers = []

        def timer_factory(interval, action):
            timer = FakeTimer(interval, action)
            timers.append(timer)
            return timer

        audio = np.linspace(-1.0, 1.0, 64, dtype=np.float32)
        plan = SimpleNamespace(relisten_requests=(
            SimpleNamespace(start=16 / 16_000, end=24 / 16_000),
        ))
        self.assertEqual(ns["retain_consequence_microspans"](
            audio, plan, sample_rate=16_000, clock=clock,
            timer_factory=timer_factory), 1)
        stored = buffer._spans[state["span_ids"][0]].samples
        sound = FakeSound()
        state["sound"] = sound
        self.assertEqual(len(timers), 1)
        self.assertEqual(ns["ACOUSTIC_TIME_MACHINE_TTL_SECONDS"], 60.0)
        self.assertEqual(timers[0].interval, 60.0)
        self.assertTrue(timers[0].started)

        clock.value += 60.0
        timers[0].action(clock=clock)

        self.assertEqual(stored, [0.0] * len(stored))
        self.assertEqual(buffer.span_count, 0)
        self.assertTrue(sound.stopped)
        self.assertIsNone(state["sound"])
        self.assertIsNone(state["expiry_timer"])
        self.assertIsNone(state["expires_at"])

    def test_status_and_play_fail_closed_at_monotonic_expiry(self):
        ns, buffer, state, clock = self._namespace(enabled=True)
        audio = np.linspace(-1.0, 1.0, 64, dtype=np.float32)
        plan = SimpleNamespace(relisten_requests=(
            SimpleNamespace(start=16 / 16_000, end=24 / 16_000),
        ))
        self.assertEqual(ns["retain_consequence_microspans"](
            audio, plan, sample_rate=16_000, clock=clock,
            timer_factory=None), 1)

        clock.value += 59.999
        self.assertEqual(ns["acoustic_time_machine_status_snapshot"](
            clock=clock)["retained_spans"], 1)
        clock.value += 0.001
        self.assertFalse(ns["play_retained_consequence_span"](clock=clock))
        self.assertEqual(ns["acoustic_time_machine_status_snapshot"](
            clock=clock)["retained_spans"], 0)
        self.assertEqual(buffer.span_count, 0)
        self.assertEqual(state["span_ids"], [])

    def test_expiry_during_sound_construction_cannot_start_playback(self):
        ns, buffer, state, clock = self._namespace(enabled=True)
        audio = np.linspace(-1.0, 1.0, 64, dtype=np.float32)
        plan = SimpleNamespace(relisten_requests=(
            SimpleNamespace(start=16 / 16_000, end=24 / 16_000),
        ))
        self.assertEqual(ns["retain_consequence_microspans"](
            audio, plan, sample_rate=16_000, clock=clock,
            timer_factory=None), 1)

        class FakeData:
            @staticmethod
            def dataWithBytes_length_(payload, length):
                self.assertEqual(length, len(payload))
                clock.value += 60.0
                return payload

        class FakeSound:
            played = False

            @classmethod
            def alloc(cls):
                return cls()

            def initWithData_(self, _data):
                return self

            def play(self):
                type(self).played = True
                return True

        ns.update(NSData=FakeData, NSSound=FakeSound)

        self.assertFalse(ns["play_retained_consequence_span"](clock=clock))
        self.assertFalse(FakeSound.played)
        self.assertEqual(buffer.span_count, 0)
        self.assertEqual(state["span_ids"], [])


class ContextFirewallRuntimeProjectionTests(unittest.TestCase):
    def _namespace(self):
        pipeline = {}
        ns = load_definitions(
            "_consequence_count",
            "store_context_firewall_receipt",
            "runtime_context_firewall_evidence",
            "context_firewall_state_snapshot",
            extra={
                "time": time,
                "PIPELINE_STATE": pipeline,
                "CONTEXT_FIREWALL_MODE_IDS": frozenset({
                    "shadow-only", "unavailable",
                }),
                "CONTEXT_FIREWALL_DISPOSITION_IDS": frozenset({
                    "no-effect", "promotion-candidate", "quarantine",
                    "unavailable",
                }),
                "CONTEXT_FIREWALL_REASON_IDS": frozenset({
                    "context-protected", "context-unprotected",
                    "personal-prior-protected",
                    "personal-prior-unprotected", "no-influence",
                    "receipt-error",
                }),
                "context_firewall_receipt":
                    lambda *_args, **_kwargs: None,
            },
        )
        return ns, pipeline

    @staticmethod
    def _receipt(**overrides):
        values = {
            "mode": "shadow-only",
            "disposition": "quarantine",
            "counterfactual_changed": True,
            "risky_spans": 2,
            "influence_count": 1,
            "context_influences": 1,
            "personal_prior_influences": 0,
            "protected_influences": 1,
            "promotion_candidates": 0,
            "quarantined": 1,
            "reason_counts": (("context-protected", 1),),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_projection_drops_private_and_unknown_fields(self):
        ns, _pipeline = self._namespace()
        private = "Alice private@example.com /Users/alice/secret"
        ns["store_context_firewall_receipt"](self._receipt(
            mode=private,
            disposition=private,
            reason_counts=(("context-protected", 1), (private, 99)),
            secret_transcript=private,
        ))
        snapshot = ns["context_firewall_state_snapshot"]()
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(private, encoded)
        self.assertEqual(snapshot["mode"], "unavailable")
        self.assertEqual(snapshot["disposition"], "unavailable")
        self.assertEqual(
            snapshot["reason_counts"], {"context-protected": 1})

    def test_failure_is_fail_open_and_does_not_mutate_active_objects(self):
        ns, _pipeline = self._namespace()
        voice = object()
        active_compiled = object()
        private = "private transcript and /Users/private/path"
        seen = []

        def fail(received_voice, *, compiled):
            seen.append((received_voice is voice,
                         compiled is active_compiled))
            raise RuntimeError(private)

        elapsed = ns["runtime_context_firewall_evidence"](
            voice, active_compiled, evaluator=fail)
        snapshot = ns["context_firewall_state_snapshot"]()
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(seen, [(True, True)])
        self.assertEqual(snapshot["mode"], "unavailable")
        self.assertEqual(snapshot["disposition"], "unavailable")
        self.assertEqual(snapshot["reason_counts"], {"receipt-error": 1})
        self.assertNotIn(private, json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
