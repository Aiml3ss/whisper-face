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
import builtins
import contextlib
import copy
import ctypes
import enum
import functools
import hashlib
import io
import json
import os
import queue
import re
import select
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parrot_core import (  # noqa: E402
    NUMBER_WORDS,
    Recognition,
    RecognitionWord,
    can_reuse_speculation,
    compile_cleanup,
    infer_revised_insertion,
    mode_from_modifiers,
    should_start_speculation,
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
from acoustic_keyword_activation import (  # noqa: E402
    ActivationError as AcousticKeywordActivationError,
    active_keywords as active_acoustic_keywords,
    clear_activations as clear_acoustic_keyword_activations,
    remove_activation as remove_acoustic_keyword_activation,
)
from acoustic_calibration_activation import (  # noqa: E402
    ActivationStatus as AcousticCalibrationActivationStatus,
    CalibrationSettings,
)
from acoustic_time_machine import AcousticTimeMachine  # noqa: E402
from cleanup_circuit_breaker import CleanupCircuitBreaker  # noqa: E402
from compatibility_fingerprint import (  # noqa: E402
    AVAILABILITY_BUCKETS,
    TARGET_BUCKETS,
)
from measurement_mode import MeasurementMode  # noqa: E402
from insertion_integrity import (  # noqa: E402
    DestinationObservation,
    InsertionCoordinator,
    InsertionLease,
    ReadbackResult,
    ReceiptState,
)
from model_wallet import (  # noqa: E402
    MAX_LATENCY_BOUND_MS,
    Capability,
    CURRENT_PROVIDER_PROFILES,
    ModelRequest,
    PARAKEET_PROFILE,
    QWEN_CLEANUP_PROFILE,
    ReadinessState,
    WHISPER_LARGE_TURBO_PROFILE,
    WHISPER_TINY_PROFILE,
)
from model_wallet_shadow import (  # noqa: E402
    RuntimeModelEvidence,
    assess_model_wallet,
)
from whisper_face_theme import (  # noqa: E402
    DARK_PALETTE,
    FACE_CHIP_COLORS,
    LIGHT_PALETTE,
    MOTION_SPECS,
    SURFACE_SPECS,
    TYPE_SPECS,
    hud_presentation,
    jelly_face_scale,
)

TREE = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))

# Cross-cutting primitives nearly every selection needs. They are pulled from
# the real source like everything else — never stubbed — so a test still fails
# if production changes them, but no test has to name them to get a language
# code or an autorelease pool.
BASELINE_DEFINITIONS = (
    "normalize_language",
    "join_recognized_parts",
    "current_language",
    "language_uses_spaces",
    "glossary_char_budget",
    "cocoa_pool",
    "with_cocoa_pool",
)
BASELINE_ASSIGNMENTS = (
    "IS_MACOS",
    "IS_WINDOWS",
    "PREFERENCES",
    "DEFAULT_FACE",
    "HOTKEY_DEFAULT",
    "SOUND_THEME_DEFAULT",
    "LANGUAGE_DEFAULT",
    "LANGUAGES",
    "LANGUAGE_CODES",
    "SPACELESS_LANGUAGES",
    "PARAKEET_LANGUAGES",
    "SPACELESS_GLOSSARY_CHAR_DIVISOR",
    "GLOSSARY_MAX_CHARS",
    "GLOSSARY_MAX_TERMS",
)

try:  # pragma: no cover - depends on the runner
    import objc as _objc
except ImportError:  # pragma: no cover - depends on the runner
    # This harness does not depend on pyobjc. An autorelease pool bounds
    # object lifetime and has no behavior to assert, so a null pool stands in
    # rather than skipping every macOS worker under test.
    _objc = types.SimpleNamespace(
        autorelease_pool=contextlib.nullcontext)


def _evaluate(nodes, namespace):
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    ))
    exec(compile(module, "dictate-selected", "exec"), namespace)
    return namespace


def _baseline_values():
    """Evaluate the shared constants once, from the real source."""
    nodes = [
        node for node in TREE.body
        if isinstance(node, ast.Assign)
        and {t.id for t in node.targets if isinstance(t, ast.Name)}
        & set(BASELINE_ASSIGNMENTS)
    ]
    namespace = _evaluate(nodes, {"sys": sys, "__builtins__": __builtins__})
    return {name: namespace[name] for name in BASELINE_ASSIGNMENTS
            if name in namespace}


BASELINE_VALUES = _baseline_values()


def load_definitions(*names, assignments=(), extra=None):
    selected = []
    found = set()
    wanted = set(names) | set(BASELINE_DEFINITIONS)
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) \
                and node.name in wanted:
            selected.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & set(assignments):
                selected.append(node)
    missing = wanted - found
    if missing:
        raise AssertionError(f"production definitions missing: {sorted(missing)}")
    namespace = {
        "json": json,
        "re": re,
        "threading": threading,
        "contextlib": contextlib,
        "functools": functools,
        "objc": _objc,
        "sys": sys,
    }
    # Shared constants come first so an explicit `extra` still overrides them
    # (tests pin IS_MACOS both ways), and a per-call copy keeps one test's
    # preference mutation out of the next test.
    namespace.update(copy.deepcopy(BASELINE_VALUES))
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


class RoutineLogPrivacyTests(unittest.TestCase):
    def test_routine_logs_do_not_interpolate_private_user_values(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        for forbidden in (
            "[tones] {bundle}",
            "forgot correction: {removed.get",
            "candidates: {pretty}",
            "saving {name!r}",
            "snippet {name!r}",
            "snippet updated: {name!r}",
            "forgot snippet edit: {name!r}",
            "edits to {name!r}",
            "correction observed: {term!r}",
            "prior quarantined: {old!r}",
            "fix rule active: {old!r}",
            "{text[:70]}",
            "snippet:{name}",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        for expected in (
            "[tones] app preference updated",
            "[learn] correction forgotten",
            "[learn] correction observed for dictionary",
            "[learn] app prior quarantined",
            "[learn] global prior quarantined",
            "[learn] fix rule active",
            'f"{len(text.split())} words]"',
            "[release {release_total:.2f}s | snippet |",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)


class PhoneEndpointBindingTests(unittest.TestCase):
    def test_only_explicit_server_mode_binds_beyond_loopback(self):
        bind_host = load_definitions("phone_bind_host")["phone_bind_host"]

        self.assertEqual(bind_host(False), "127.0.0.1")
        self.assertEqual(bind_host(True), "0.0.0.0")
        for invalid in (None, 0, 1, "server-only", object()):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    bind_host(invalid)

    def test_server_uses_localhost_for_desktop_and_lan_for_headless(self):
        for server_only, expected_bind, expected_display in (
                (False, "127.0.0.1", "127.0.0.1"),
                (True, "0.0.0.0", "192.0.2.10")):
            with self.subTest(server_only=server_only):
                calls = []

                class FakeServer:
                    def __init__(self, address, handler):
                        calls.append(("bind", address, handler))

                    def serve_forever(self):
                        calls.append(("serve",))

                namespace = load_definitions(
                    "phone_bind_host", "phone_server",
                    extra={
                        "SERVER_ONLY": server_only,
                        "PHONE_PORT": 8787,
                        "PHONE_TOKEN": {"value": ""},
                        "os": os,
                        "PhoneHandler": object(),
                        "ThreadingHTTPServer": FakeServer,
                        "lan_ip": lambda: "192.0.2.10",
                    },
                )
                output = io.StringIO()
                original_stdout = sys.stdout
                try:
                    sys.stdout = output
                    namespace["phone_server"]()
                finally:
                    sys.stdout = original_stdout

                self.assertEqual(
                    calls[0][1], (expected_bind, 8787))
                self.assertEqual(calls[-1], ("serve",))
                self.assertIn(
                    f"http://{expected_display}:8787/", output.getvalue())
                token = namespace["PHONE_TOKEN"]["value"]
                if server_only:
                    # Created at start and shown once, so the operator can
                    # give it to the phone.
                    self.assertRegex(token, r"^[0-9a-f]{64}$")
                    self.assertEqual(output.getvalue().count(token), 1)
                else:
                    self.assertEqual(token, "")


class PhoneEndpointRequestTests(unittest.TestCase):
    """What a caller that reaches port 8787 can and cannot do."""

    TOKEN = "a" * 64

    def handler(self, server_only, transcribed="Insert my address."):
        calls = []

        def decode_audio(path):
            calls.append(("decode", Path(path).read_bytes()))
            return np.zeros(16000, dtype=np.float32)

        ns = load_definitions(
            "PhoneHandler", "parse_multipart", "phone_host_allowed",
            "phone_token_matches",
            assignments=("PHONE_MAX_BODY",),
            extra={
                "BaseHTTPRequestHandler": __import__(
                    "http.server").server.BaseHTTPRequestHandler,
                "email": __import__("email.policy"),
                "hashlib": hashlib,
                "tempfile": tempfile,
                "time": time,
                "SERVER_ONLY": server_only,
                "PHONE_TOKEN": {"value": self.TOKEN if server_only else ""},
                "LAST_USE": {"t": 0.0},
                "SAMPLE_RATE": 16000,
                "decode_audio": decode_audio,
                "transcribe": lambda audio: transcribed,
                "ASR_POOL": SimpleNamespace(
                    submit=lambda fn, arg: SimpleNamespace(
                        result=lambda: fn(arg))),
                "phone_clean": lambda raw: calls.append(("clean", raw))
                or "Insert my address.",
                "source_metadata": lambda: {"source_revision": "abc"},
                "local_license_notice": lambda: "notices",
            },
        )
        return ns["PhoneHandler"], calls

    @staticmethod
    def drive(handler_class, request: bytes):
        handler = handler_class.__new__(handler_class)
        handler.rfile = io.BytesIO(request)
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 50000)
        handler.server = None
        handler.request_version = "HTTP/1.1"
        handler.requestline = ""
        handler.command = None
        handler.close_connection = True
        handler.raw_requestline = handler.rfile.readline()
        handler.parse_request()
        with contextlib.redirect_stdout(io.StringIO()):
            getattr(handler, "do_" + handler.command)()
        head, _, body = handler.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), body

    @staticmethod
    def request(method, path, *, host="127.0.0.1:8787", body=b"",
                headers=()):
        lines = [f"{method} {path} HTTP/1.1", f"Host: {host}"]
        lines += list(headers)
        if method == "POST" and not any(
                h.lower().startswith("content-length") for h in headers):
            lines.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + body

    def test_desktop_serves_only_the_health_check(self):
        handler, calls = self.handler(server_only=False)

        self.assertEqual(
            self.drive(handler, self.request("GET", "/health")),
            (200, b"ok"))
        for path in ("/", "/source", "/license"):
            with self.subTest(path=path):
                status, _ = self.drive(handler, self.request("GET", path))
                self.assertEqual(status, 404)
        status, _ = self.drive(handler, self.request(
            "POST", "/v1/audio/transcriptions", body=b"RIFF-audio",
            headers=("Content-Type: text/plain",
                     f"Authorization: Bearer {self.TOKEN}")))
        self.assertEqual(status, 404)
        self.assertEqual(calls, [])

    def test_a_web_page_is_refused_even_on_the_health_check(self):
        handler, _ = self.handler(server_only=False)
        for request in (
                self.request("GET", "/health", headers=(
                    "Origin: https://evil.example",)),
                self.request("GET", "/health", host="evil.example:8787")):
            with self.subTest(request=request):
                status, _ = self.drive(handler, request)
                self.assertEqual(status, 403)

    def test_server_mode_transcribes_only_for_the_token_holder(self):
        handler, calls = self.handler(server_only=True)
        audio = b"RIFF-short-audio"

        refused = {
            "no token": self.request(
                "POST", "/v1/audio/transcriptions", body=audio),
            "wrong token": self.request(
                "POST", "/v1/audio/transcriptions", body=audio,
                headers=("Authorization: Bearer " + "b" * 64,)),
            "browser origin": self.request(
                "POST", "/v1/audio/transcriptions", body=audio,
                headers=(f"Authorization: Bearer {self.TOKEN}",
                         "Origin: https://evil.example")),
            "rebound host": self.request(
                "POST", "/v1/audio/transcriptions", body=audio,
                host="evil.example:8787",
                headers=(f"Authorization: Bearer {self.TOKEN}",)),
            "oversized body": self.request(
                "POST", "/v1/audio/transcriptions", body=audio,
                headers=(f"Authorization: Bearer {self.TOKEN}",
                         f"Content-Length: {26 * 1024 * 1024}")),
            "no length": self.request(
                "POST", "/v1/audio/transcriptions",
                headers=(f"Authorization: Bearer {self.TOKEN}",
                         "Content-Length: -1")),
        }
        for reason, request in refused.items():
            with self.subTest(reason=reason):
                status, _ = self.drive(handler, request)
                self.assertIn(status, {401, 403, 411, 413})
        self.assertEqual(calls, [])

        status, body = self.drive(handler, self.request(
            "POST", "/v1/audio/transcriptions", host="192.0.2.10:8787",
            body=audio, headers=(f"Authorization: Bearer {self.TOKEN}",)))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"text": "Insert my address."})
        self.assertEqual(calls, [
            ("decode", audio), ("clean", "Insert my address.")])

    def test_a_failed_request_reveals_no_local_detail(self):
        handler, _ = self.handler(server_only=True)
        handler_ns = handler.do_POST.__globals__
        handler_ns["decode_audio"] = lambda path: (_ for _ in ()).throw(
            RuntimeError(f"ffmpeg failed: {path}: /Users/someone/secret"))

        status, body = self.drive(handler, self.request(
            "POST", "/v1/audio/transcriptions", body=b"bad",
            headers=(f"Authorization: Bearer {self.TOKEN}",)))

        self.assertEqual(status, 500)
        self.assertNotIn(b"/Users", body)

    def test_phone_text_never_expands_snippets_or_feeds_the_history(self):
        logged = []
        ns = load_definitions(
            "phone_clean",
            extra={
                "collapse_repeats": lambda raw: (raw, False),
                "is_hallucination": lambda raw, language: False,
                "looks_like_prompt_echo": lambda raw: False,
                "apply_learned_fixes": lambda raw: raw,
                "match_snippet": lambda raw: (
                    "address", "742 Evergreen Terrace, Springfield"),
                "_load_snippet_map": lambda: {
                    "address": "742 Evergreen Terrace, Springfield"},
                "_mask_snippets_inline": lambda raw, snippets: (
                    raw.replace("address", "\ue000"),
                    {"\ue000": snippets["address"]}),
                "_restore_snippet_sentinels": lambda text, restore: (
                    text.replace("\ue000", restore["\ue000"])),
                "extract_tone_override": lambda raw: (raw, None),
                "needs_llm_cleanup": lambda *args, **kwargs: False,
                "quick_clean": lambda raw, verbatim=False, language="en": raw,
                "llm_clean": lambda raw, tone: raw,
                "apply_vocabulary_casing": lambda text: text,
                "TONE": {"default": ""},
                "append_transcript": lambda *args, **kwargs:
                    logged.append(args),
            },
        )

        text = ns["phone_clean"]("Insert my address.")

        self.assertEqual(text, "Insert my address.")
        self.assertNotIn("Evergreen", text)
        self.assertEqual(logged, [])

    def test_decoding_is_bounded_in_length_and_time(self):
        seen = {}

        def run(command, **kwargs):
            seen["command"] = command
            if kwargs.get("timeout") is None:
                raise AssertionError("ffmpeg would run without a time limit")
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        ns = load_definitions(
            "decode_audio",
            assignments=("PHONE_MAX_AUDIO_SECONDS", "PHONE_DECODE_TIMEOUT"),
            extra={"subprocess": SimpleNamespace(
                run=run, TimeoutExpired=subprocess.TimeoutExpired),
                   "SAMPLE_RATE": 16000, "np": np},
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            ns["decode_audio"]("/tmp/upload.audio")
        command = seen["command"]
        self.assertEqual(command[command.index("-t") + 1], "600")


class _FakePasteboardItem:
    def __init__(self):
        self.data = {}

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def types(self):
        return list(self.data)

    def dataForType_(self, kind):
        return self.data.get(kind)

    def setData_forType_(self, data, kind):
        self.data[kind] = data
        return True

    def setString_forType_(self, text, kind):
        self.data[kind] = text
        return True


class _FakePasteboard:
    """The parts of NSPasteboard the clipboard round-trip uses."""

    def __init__(self, text=None):
        self.items = []
        self.change = 0
        self.options = None
        if text is not None:
            self.user_copies(text)

    def user_copies(self, text):
        item = _FakePasteboardItem()
        item.setString_forType_(text, "public.utf8-plain-text")
        self.change += 1
        self.items = [item]

    def pasteboardItems(self):
        return list(self.items)

    def changeCount(self):
        return self.change

    def clearContents(self):
        self.change += 1
        self.items = []
        self.options = None

    def prepareForNewContentsWithOptions_(self, options):
        self.clearContents()
        self.options = options

    def writeObjects_(self, objects):
        self.items.extend(objects)
        return True

    def setString_forType_(self, text, kind):
        item = _FakePasteboardItem()
        item.setString_forType_(text, kind)
        self.items = [item]
        return True

    def text(self):
        return [item.data.get("public.utf8-plain-text")
                for item in self.items]


class _FakeTimer:
    def __init__(self, delay, function):
        self.function = function
        self.cancelled = False
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function()


class _FakeKeyboard:
    def __init__(self, pasted):
        self.pasted = pasted

    @contextlib.contextmanager
    def pressed(self, _key):
        yield

    def press(self, key):
        self.pasted.append(key)

    def release(self, _key):
        pass


class ClipboardRoundTripTests(unittest.TestCase):
    """Dictated text borrows the clipboard briefly and gives it back."""

    NAMES = ("paste", "snapshot_pasteboard", "restore_pasteboard",
             "write_private_pasteboard", "_begin_paste_burst",
             "_finish_paste_burst", "snapshot_windows_clipboard",
             "restore_windows_clipboard", "write_private_windows_clipboard")
    ASSIGNMENTS = ("PASTEBOARD_MARKER_TYPES", "PASTEBOARD_CURRENT_HOST_ONLY",
                   "WINDOWS_CLIPBOARD_EXCLUSIONS", "PASTE_RESTORE")

    def mac(self, pasteboard):
        timers = []
        presses = []

        def timer(delay, function):
            timers.append(_FakeTimer(delay, function))
            return timers[-1]

        ns = load_definitions(
            *self.NAMES, assignments=self.ASSIGNMENTS,
            extra={
                "IS_WINDOWS": False,
                "NSPasteboard": SimpleNamespace(
                    generalPasteboard=lambda: pasteboard),
                "NSPasteboardItem": _FakePasteboardItem,
                "NSPasteboardTypeString": "public.utf8-plain-text",
                "NSData": SimpleNamespace(data=lambda: b""),
                "kb": _FakeKeyboard(presses),
                "keyboard": SimpleNamespace(
                    Key=SimpleNamespace(cmd="cmd", ctrl="ctrl")),
                "time": SimpleNamespace(sleep=lambda _s: None),
                "threading": SimpleNamespace(
                    Timer=timer, Lock=threading.Lock),
            },
        )
        return ns, timers, presses

    def test_dictated_text_is_marked_transient_concealed_and_local(self):
        pasteboard = _FakePasteboard("tracking 1Z999AA10123456784")
        ns, _, presses = self.mac(pasteboard)

        ns["paste"]("Meet at the north entrance.")

        self.assertEqual(presses, ["v"])
        self.assertEqual(pasteboard.text(), ["Meet at the north entrance."])
        kinds = set(pasteboard.items[0].types())
        self.assertIn("org.nspasteboard.TransientType", kinds)
        self.assertIn("org.nspasteboard.ConcealedType", kinds)
        # NSPasteboardContentsCurrentHostOnly: not offered to other devices.
        self.assertEqual(pasteboard.options, 1)

    def test_back_to_back_pastes_restore_the_users_clipboard(self):
        pasteboard = _FakePasteboard("tracking 1Z999AA10123456784")
        ns, timers, _ = self.mac(pasteboard)

        ns["paste"]("First dictation.")
        ns["paste"]("Second dictation.")
        for timer in timers:
            timer.fire()

        self.assertEqual(pasteboard.text(), ["tracking 1Z999AA10123456784"])

    def test_an_empty_clipboard_is_left_empty_after_the_paste(self):
        pasteboard = _FakePasteboard()
        ns, timers, _ = self.mac(pasteboard)

        ns["paste"]("My account number is in the notes.")
        for timer in timers:
            timer.fire()

        self.assertEqual(pasteboard.items, [])

    def test_a_clipboard_the_user_changed_since_is_left_alone(self):
        pasteboard = _FakePasteboard("older")
        ns, timers, _ = self.mac(pasteboard)

        ns["paste"]("Dictated.")
        pasteboard.user_copies("copied just now")
        ns["paste"]("Dictated again.")
        for timer in timers:
            timer.fire()

        self.assertEqual(pasteboard.text(), ["copied just now"])

    def test_windows_text_is_excluded_from_history_and_cloud_and_restored(self):
        CF_UNICODETEXT = 13
        state = {"formats": {CF_UNICODETEXT: "user's own text"}}
        registered = {}

        class Clipboard:
            CF_UNICODETEXT = 13

            @staticmethod
            def OpenClipboard():
                pass

            @staticmethod
            def CloseClipboard():
                pass

            @staticmethod
            def EmptyClipboard():
                state["formats"] = {}

            @staticmethod
            def EnumClipboardFormats(previous):
                keys = sorted(state["formats"])
                later = [key for key in keys if key > previous]
                return later[0] if later else 0

            @staticmethod
            def GetClipboardData(kind):
                return state["formats"][kind]

            @staticmethod
            def SetClipboardData(kind, data):
                state["formats"][kind] = data

            @staticmethod
            def SetClipboardText(text, kind):
                state["formats"][kind] = text

            @staticmethod
            def RegisterClipboardFormat(name):
                return registered.setdefault(name, 0xC000 + len(registered))

        timers = []
        ns = load_definitions(
            *self.NAMES, assignments=self.ASSIGNMENTS,
            extra={
                "IS_WINDOWS": True,
                "win32clipboard": Clipboard,
                "pyperclip": SimpleNamespace(
                    paste=lambda: state["formats"].get(CF_UNICODETEXT, ""),
                    copy=lambda text: self.fail("pyperclip.copy was used")),
                "kb": _FakeKeyboard([]),
                "keyboard": SimpleNamespace(
                    Key=SimpleNamespace(cmd="cmd", ctrl="ctrl")),
                "time": SimpleNamespace(sleep=lambda _s: None),
                "threading": SimpleNamespace(
                    Timer=lambda delay, function: timers.append(
                        _FakeTimer(delay, function)) or timers[-1],
                    Lock=threading.Lock),
            },
        )

        ns["paste"]("First dictation.")
        during = dict(state["formats"])
        ns["paste"]("Second dictation.")
        for timer in timers:
            timer.fire()

        self.assertEqual(during[CF_UNICODETEXT], "First dictation.")
        for name in ("ExcludeClipboardContentFromMonitorProcessing",
                     "CanIncludeInClipboardHistory",
                     "CanUploadToCloudClipboard"):
            with self.subTest(name=name):
                self.assertIn(registered[name], during)
        self.assertEqual(
            during[registered["CanIncludeInClipboardHistory"]],
            b"\0\0\0\0")
        self.assertEqual(state["formats"], {CF_UNICODETEXT: "user's own text"})


class GuiLauncherActivationTests(unittest.TestCase):
    def namespace(self, cleanups):
        return load_definitions(
            "gui_activation_socket_path",
            "cleanup_stale_gui_activation_sockets",
            "current_launchd_service_pid",
            "_parent_pid",
            "_process_has_ancestor",
            "start_gui_activation_server",
            extra={
                "AppHelper": SimpleNamespace(callAfter=lambda callback: callback()),
                "IS_MACOS": True,
                "SERVER_ONLY": False,
                "Path": Path,
                "atexit": SimpleNamespace(register=cleanups.append),
                "os": os,
                "socket": socket,
                "stat": stat,
                "subprocess": subprocess,
                "source_revision": lambda: "f" * 40,
            },
        )

    @unittest.skipUnless(
        hasattr(os, "getuid") and hasattr(socket, "AF_UNIX")
        and Path("/usr/bin/nc").is_file(),
        "same-user launcher activation requires macOS nc and Unix sockets",
    )
    def test_fixed_same_user_socket_opens_only_existing_gui(self):
        cleanups = []
        shown = threading.Event()
        gui = SimpleNamespace(show=shown.set)
        revision = "a" * 40
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            namespace = self.namespace(cleanups)
            endpoint = namespace["start_gui_activation_server"](
                gui,
                revision=revision,
                pid=4242,
                uid=os.getuid(),
                root=directory,
                call_after=lambda callback: callback(),
            )
            self.assertIsNotNone(endpoint)
            listener, path = endpoint
            self.assertEqual(
                path.name,
                f"whisper-face-gui-{os.getuid()}-4242-{revision}.sock",
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            blocker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            blocker.connect(str(path))
            time.sleep(0.3)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.connect(str(path))
                peer.sendall(b"\x00")
            self.assertFalse(shown.wait(0.05))

            nc = subprocess.run(
                ["/usr/bin/nc", "-U", str(path)],
                input=b"\x01", capture_output=True, timeout=1, check=False,
            )
            self.assertEqual(nc.returncode, 0, nc.stderr)
            self.assertTrue(shown.wait(1.0))
            blocker.close()
            listener.close()
            cleanups[0]()

    @unittest.skipUnless(
        hasattr(os, "getuid") and hasattr(socket, "AF_UNIX"),
        "stale Unix activation socket cleanup requires POSIX",
    )
    def test_cleanup_removes_only_owned_pattern_socket_for_dead_pid(self):
        cleanups = []
        namespace = self.namespace(cleanups)
        revision = "b" * 40
        dead_pid = 99999999
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            stale_path = Path(namespace["gui_activation_socket_path"](
                dead_pid, revision, uid=os.getuid(), root=directory))
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(stale_path))
            stale.close()
            regular = Path(directory) / (
                f"whisper-face-gui-{os.getuid()}-{dead_pid}-{'c' * 40}.sock")
            regular.write_text("not a socket", encoding="utf-8")

            removed = namespace["cleanup_stale_gui_activation_sockets"](
                uid=os.getuid(), root=directory)
            self.assertEqual(removed, 1)
            self.assertFalse(stale_path.exists())
            self.assertTrue(regular.exists())

    def test_invalid_binding_and_non_gui_modes_fail_closed(self):
        cleanups = []
        namespace = self.namespace(cleanups)
        path_for = namespace["gui_activation_socket_path"]
        with self.assertRaises(ValueError):
            path_for(0, "a" * 40)
        with self.assertRaises(ValueError):
            path_for(1, "moving-main")
        namespace["IS_MACOS"] = False
        self.assertIsNone(namespace["start_gui_activation_server"](object()))
        self.assertEqual(cleanups, [])

    @unittest.skipUnless(hasattr(os, "getuid"), "launchd PID binding requires POSIX")
    def test_launchd_pid_binding_accepts_only_runtime_or_uv_parent(self):
        cleanups = []
        namespace = self.namespace(cleanups)
        parent_pid = os.getppid()
        namespace["subprocess"] = SimpleNamespace(
            SubprocessError=subprocess.SubprocessError,
            run=lambda *_args, **_kwargs: SimpleNamespace(
                stdout=f"state = running\n\tpid = {parent_pid}\n"),
        )
        self.assertEqual(
            namespace["current_launchd_service_pid"](uid=os.getuid()),
            parent_pid,
        )
        namespace["subprocess"].run = lambda *_args, **_kwargs: SimpleNamespace(
            stdout="state = running\n\tpid = 99999999\n")
        self.assertIsNone(
            namespace["current_launchd_service_pid"](uid=os.getuid()))

    @unittest.skipUnless(hasattr(os, "getuid"), "launchd PID binding requires POSIX")
    def test_launchd_pid_binding_trusts_launcher_service_pid_via_env(self):
        # Under the signed launcher chain (launchd -> Whisper Face.app -> uv ->
        # python) the job PID is the launcher, an ancestor several hops up, so
        # it is neither this process nor its parent. It is trusted only when the
        # exported PID is launchctl-confirmed AND a real ancestor.
        cleanups = []
        namespace = self.namespace(cleanups)
        service_pid = 4242
        namespace["subprocess"] = SimpleNamespace(
            SubprocessError=subprocess.SubprocessError,
            run=lambda *_args, **_kwargs: SimpleNamespace(
                stdout=f"state = running\n\tpid = {service_pid}\n"),
        )
        namespace["_parent_pid"] = lambda pid: (
            service_pid if pid == os.getpid() else None)
        resolve = namespace["current_launchd_service_pid"]
        with mock.patch.dict(
                os.environ, {"WHISPER_FACE_SERVICE_PID": str(service_pid)}):
            self.assertEqual(resolve(uid=os.getuid()), service_pid)
        # A forged PID launchctl does not report is rejected.
        with mock.patch.dict(
                os.environ, {"WHISPER_FACE_SERVICE_PID": str(service_pid + 1)}):
            self.assertIsNone(resolve(uid=os.getuid()))
        # The correct PID that is not an ancestor of this process is rejected.
        namespace["_parent_pid"] = lambda pid: None
        with mock.patch.dict(
                os.environ, {"WHISPER_FACE_SERVICE_PID": str(service_pid)}):
            self.assertIsNone(resolve(uid=os.getuid()))
        # With no exported PID, the non-ancestor job PID stays rejected too.
        os.environ.pop("WHISPER_FACE_SERVICE_PID", None)
        self.assertIsNone(resolve(uid=os.getuid()))


class DictationSuccessSoundTests(unittest.TestCase):
    def test_unavailable_system_sound_never_breaks_dictation(self):
        logs = []

        def fail_to_play(*_args, **_kwargs):
            raise OSError("missing afplay")

        namespace = load_definitions(
            "play", "sound_cue_path", "normalize_sound_theme",
            assignments={"SOUND_THEMES", "SOUND_THEME_DEFAULT", "SOUND_CUES"},
            extra={
                "IS_WINDOWS": False,
                "PREFERENCES": {"sounds": "system"},
                "SOUNDS_DIR": Path("/nonexistent-sounds"),
                "print": logs.append,
                "subprocess": SimpleNamespace(
                    DEVNULL=None,
                    Popen=fail_to_play,
                ),
            },
        )

        namespace["play"]("Pop")

        self.assertEqual(logs, ["! feedback sound unavailable"])

    def test_mac_review_route_uses_advisory_ping(self):
        select = load_definitions(
            "dictation_success_sound")["dictation_success_sound"]

        self.assertEqual(select("review", is_macos=True), "Ping")

    def test_other_routes_and_windows_keep_standard_success_sound(self):
        select = load_definitions(
            "dictation_success_sound")["dictation_success_sound"]

        self.assertEqual(select("standard", is_macos=True), "Pop")
        self.assertEqual(select("review", is_macos=False), "Pop")


class SoundThemeTests(unittest.TestCase):
    """D1: the app can sound like itself, or say nothing at all."""

    def namespace(self, theme, directory, *, played=None, windows=False):
        played = [] if played is None else played
        return load_definitions(
            "play", "sound_cue_path", "normalize_sound_theme",
            assignments={"SOUND_THEMES", "SOUND_THEME_DEFAULT", "SOUND_CUES"},
            extra={
                "IS_WINDOWS": windows,
                "PREFERENCES": {"sounds": theme},
                "SOUNDS_DIR": Path(directory),
                "print": lambda *_a, **_k: None,
                "subprocess": SimpleNamespace(
                    DEVNULL=None,
                    Popen=lambda argv, **_k: played.append(argv),
                ),
            },
        )

    def test_silent_theme_plays_nothing_at_all(self):
        played = []
        ns = self.namespace("silent", ROOT / "sounds", played=played)
        for cue in ("Tink", "Pop", "Ping", "Funk"):
            ns["play"](cue)
        self.assertEqual(played, [])

    def test_system_theme_keeps_the_existing_macos_sounds(self):
        played = []
        ns = self.namespace("system", ROOT / "sounds", played=played)
        ns["play"]("Pop")
        self.assertEqual(
            played, [["afplay", "/System/Library/Sounds/Pop.aiff"]])

    def test_first_party_theme_plays_the_committed_assets(self):
        played = []
        ns = self.namespace("whisper", ROOT / "sounds", played=played)
        for cue, asset in (
                ("Tink", "start"), ("Pop", "finish"),
                ("Ping", "review"), ("Funk", "error")):
            ns["play"](cue)
            self.assertEqual(
                played[-1], ["afplay", str(ROOT / "sounds" / f"{asset}.wav")])

    def test_missing_first_party_asset_falls_back_to_system_sound(self):
        played = []
        ns = self.namespace("whisper", "/nonexistent-sounds", played=played)
        ns["play"]("Pop")
        self.assertEqual(
            played, [["afplay", "/System/Library/Sounds/Pop.aiff"]])

    def test_unknown_theme_names_keep_system_sounds(self):
        ns = self.namespace("system", ROOT / "sounds")
        for candidate in ("", "  ", "loud", None, 3, True):
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    ns["normalize_sound_theme"](candidate), "system")
        self.assertEqual(ns["normalize_sound_theme"]("SILENT"), "silent")

    def test_every_committed_cue_is_short_quiet_mono_audio(self):
        import wave

        for cue in ("start", "finish", "review", "error"):
            with self.subTest(cue=cue):
                with wave.open(str(ROOT / "sounds" / f"{cue}.wav")) as handle:
                    self.assertEqual(handle.getnchannels(), 1)
                    self.assertEqual(handle.getsampwidth(), 2)
                    seconds = handle.getnframes() / handle.getframerate()
                    self.assertLess(seconds, 0.150)
                    self.assertGreater(seconds, 0.050)


class ModelWalletShadowRuntimeStatusTests(unittest.TestCase):
    def snapshot(self, *, resolved=(), warm=()):
        evidence = SimpleNamespace(providers=tuple(
            SimpleNamespace(
                provider_id=profile.provider_id,
                state=(ReadinessState.RESOLVED
                       if profile.provider_id in resolved
                       else ReadinessState.NOT_INSTALLED),
                revision_verified=profile.provider_id in resolved,
            )
            for profile in CURRENT_PROVIDER_PROFILES
        ))
        function = load_definitions(
            "model_wallet_shadow_status_snapshot",
            extra={
                "MODEL_READINESS_CACHE": {
                    "receipt": evidence, "lock": threading.Lock()},
                "MODEL_WARM_PATHS": {
                    "providers": set(warm), "lock": threading.Lock()},
                "CURRENT_PROVIDER_PROFILES": CURRENT_PROVIDER_PROFILES,
                "WHISPER_TINY_PROFILE": WHISPER_TINY_PROFILE,
                "WHISPER_LARGE_TURBO_PROFILE":
                    WHISPER_LARGE_TURBO_PROFILE,
                "RuntimeModelEvidence": RuntimeModelEvidence,
                "ReadinessState": ReadinessState,
                "Capability": Capability,
                "ModelRequest": ModelRequest,
                "MAX_LATENCY_BOUND_MS": MAX_LATENCY_BOUND_MS,
                "assess_model_wallet": assess_model_wallet,
            },
        )["model_wallet_shadow_status_snapshot"]
        return function()

    def test_all_exact_pins_and_warm_paths_do_not_overclaim_readiness(self):
        resolved = tuple(
            profile.provider_id for profile in CURRENT_PROVIDER_PROFILES)
        snapshot = self.snapshot(resolved=resolved, warm=resolved)
        encoded = json.dumps(snapshot, sort_keys=True)

        self.assertEqual(snapshot["mode"], "shadow-only")
        self.assertEqual(len(snapshot["pins"]), 4)
        self.assertTrue(all(
            item["resolution_state"] == "resolved"
            and item["warm_path_observed"]
            and item["revision_verified"]
            and not item["capability_bounds_attested"]
            for item in snapshot["pins"]))
        self.assertFalse(snapshot["attempted"])
        self.assertEqual(
            {item["capability"] for item in snapshot["capabilities"]},
            {"fast_asr", "final_asr", "cleanup"},
        )
        self.assertTrue(all(
            item["fail_closed"] and not item["attempted"]
            and item["selected_provider_id"] is None
            and item["advisory_order"] == []
            for item in snapshot["capabilities"]
        ))
        states = {
            (item["capability"], provider["provider_id"]):
                provider["eligibility"]
            for item in snapshot["capabilities"]
            for provider in item["providers"]
        }
        self.assertEqual(
            states[("fast_asr", WHISPER_TINY_PROFILE.provider_id)],
            "not_ready",
        )
        self.assertEqual(
            states[("final_asr", WHISPER_LARGE_TURBO_PROFILE.provider_id)],
            "not_ready",
        )
        self.assertNotIn("transcript", encoded.casefold())
        self.assertNotIn("/resolved/", encoded)

    def test_unresolved_pins_fail_closed_as_not_ready_or_missing(self):
        snapshot = self.snapshot(resolved=(
            WHISPER_TINY_PROFILE.provider_id,
        ))
        supported = [
            provider["eligibility"]
            for item in snapshot["capabilities"]
            for provider in item["providers"]
            if provider["eligibility"] != "unsupported_capability"
        ]

        self.assertTrue(supported)
        self.assertEqual(
            set(supported), {"not_ready"})

    def test_routine_status_wires_only_the_non_executing_projection(self):
        status = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot"
        )
        status_names = {
            node.id for node in ast.walk(status) if isinstance(node, ast.Name)
        }
        self.assertIn("model_wallet_shadow_status_snapshot", status_names)

        projection = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "model_wallet_shadow_status_snapshot"
        )
        projection_names = {
            node.id for node in ast.walk(projection)
            if isinstance(node, ast.Name)
        }
        self.assertFalse(projection_names & {
            "transcribe", "transcribe_detailed", "ollama_chat",
            "resolve_asr_model", "windows_whisper_model", "PARAKEET",
            "collect_model_readiness",
        })


class PointAndSpeakPreviewRuntimeTests(unittest.TestCase):
    @staticmethod
    def namespace(capture, resolution, *, is_macos=True):
        captured = (
            capture.receipt.state
            if capture is not None
            and getattr(capture.receipt.state, "value", None) == "captured"
            else object()
        )
        resolved = SimpleNamespace(value="resolved")
        ns = load_definitions(
            "preview_point_and_speak",
            extra={
                "IS_MACOS": is_macos,
                "PointAndSpeakSnapshotState": SimpleNamespace(
                    CAPTURED=captured),
                "PointAndSpeakResolutionState": SimpleNamespace(
                    RESOLVED=resolved),
                "capture_frontmost_accessibility_targets": lambda: capture,
                "resolve_point_and_speak": lambda phrase, targets: resolution(
                    phrase, targets, resolved),
            },
        )
        return ns["preview_point_and_speak"]

    def test_resolved_preview_returns_only_name_role_and_content_free_receipt(self):
        captured = SimpleNamespace(value="captured")
        capture = SimpleNamespace(
            targets=({
                "target_id": "ax-private-id",
                "title": "Save Project Bluebird",
                "label": "",
                "role": "button",
            },),
            receipt=SimpleNamespace(
                state=captured, observed_elements=4, emitted_targets=1,
                skipped_elements=2, truncated=False),
        )

        def resolution(phrase, targets, resolved):
            self.assertEqual(phrase, "save project button")
            self.assertIs(targets, capture.targets)
            return SimpleNamespace(
                state=resolved, target_id="ax-private-id",
                receipt=SimpleNamespace(
                    observed_targets=1, eligible_targets=1,
                    contradiction_count=0, evidence=("token", "role"),
                    confidence_bucket="high", margin_bucket="wide"),
            )

        preview = self.namespace(capture, resolution)("save project button")
        serialized_receipt = json.dumps(preview["receipt"], sort_keys=True)

        self.assertEqual(preview["state"], "resolved")
        self.assertEqual(preview["accessibility_name"],
                         "Save Project Bluebird")
        self.assertEqual(preview["role"], "button")
        self.assertNotIn("save project button", serialized_receipt)
        self.assertNotIn("Bluebird", serialized_receipt)
        self.assertNotIn("ax-private-id", serialized_receipt)

    def test_permission_no_focus_ambiguity_and_invalid_phrase_fail_closed(self):
        calls = []
        denied = SimpleNamespace(
            targets=(),
            receipt=SimpleNamespace(
                state=SimpleNamespace(value="permission_denied"),
                observed_elements=0, emitted_targets=0,
                skipped_elements=0, truncated=False),
        )
        preview = self.namespace(
            denied, lambda *_args: calls.append("resolve"))("save")
        self.assertEqual(preview["state"], "permission_denied")
        self.assertEqual(calls, [])

        unavailable = SimpleNamespace(
            targets=(),
            receipt=SimpleNamespace(
                state=SimpleNamespace(value="unavailable"),
                observed_elements=0, emitted_targets=0,
                skipped_elements=0, truncated=False),
        )
        preview = self.namespace(
            unavailable, lambda *_args: calls.append("resolve"))("save")
        self.assertEqual(preview["state"], "unavailable")
        self.assertEqual(calls, [])

        captured = SimpleNamespace(value="captured")
        capture = SimpleNamespace(
            targets=(), receipt=SimpleNamespace(
                state=captured, observed_elements=1, emitted_targets=0,
                skipped_elements=0, truncated=False))

        def ambiguous(_phrase, _targets, _resolved):
            return SimpleNamespace(
                state=SimpleNamespace(value="ambiguous"), target_id=None,
                receipt=SimpleNamespace(
                    observed_targets=0, eligible_targets=0,
                    contradiction_count=0, evidence=(),
                    confidence_bucket="none", margin_bucket="none"))

        preview = self.namespace(capture, ambiguous)("save")
        self.assertEqual(preview["state"], "ambiguous")
        self.assertEqual(preview["accessibility_name"], "")
        self.assertEqual(preview["role"], "")

        capture_calls = []
        invalid_preview = load_definitions(
            "preview_point_and_speak",
            extra={
                "IS_MACOS": True,
                "PointAndSpeakSnapshotState": SimpleNamespace(
                    CAPTURED=object()),
                "PointAndSpeakResolutionState": SimpleNamespace(
                    RESOLVED=object()),
                "capture_frontmost_accessibility_targets": lambda:
                    capture_calls.append(True),
                "resolve_point_and_speak": lambda *_args: None,
            },
        )["preview_point_and_speak"]
        for invalid in ("", "line\nbreak", "x" * 97):
            with self.subTest(invalid=invalid[:8]):
                preview = invalid_preview(invalid)
                self.assertEqual(preview["state"], "unavailable")
        self.assertEqual(capture_calls, [])

        non_mac = self.namespace(None, lambda *_args: None, is_macos=False)
        self.assertEqual(non_mac("save")["state"], "unavailable")

    def test_preview_has_no_write_logging_or_routine_status_surface(self):
        preview = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "preview_point_and_speak")
        called = {
            node.func.id for node in ast.walk(preview)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(called & {
            "print", "open", "paste_text", "type_text", "click", "focus",
        })
        status = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot")
        status_names = {
            node.id for node in ast.walk(status) if isinstance(node, ast.Name)
        }
        self.assertNotIn("preview_point_and_speak", status_names)


class PointAndSpeakActionRuntimeTests(unittest.TestCase):
    def test_explicit_press_returns_only_content_free_terminal_evidence(self):
        captured_state = SimpleNamespace(value="captured")
        resolved_state = object()
        captured = SimpleNamespace(
            targets=({
                "target_id": "ax-secret", "title": "Project Bluebird Save",
                "label": "", "role": "button",
            },),
            receipt=SimpleNamespace(
                state=captured_state, observed_elements=5, emitted_targets=1,
                truncated=False),
        )
        decision = SimpleNamespace(
            state=resolved_state,
            target_id="ax-secret",
            receipt=SimpleNamespace(
                eligible_targets=1, contradiction_count=0,
                evidence=("normalized", "role"),
                confidence_bucket="very_high", margin_bucket="wide"),
        )
        calls = []

        class Transactions:
            def execute(self, nonce, lease):
                calls.append((nonce, lease))
                return SimpleNamespace(
                    state=SimpleNamespace(value="executed"),
                    to_mapping=lambda: {
                        "schema_version": 1, "state": "executed",
                        "attempted": True, "recheck": "matched",
                    })

        lease = object()
        function = load_definitions(
            "press_point_and_speak",
            extra={
                "IS_MACOS": True,
                "time": SimpleNamespace(monotonic=lambda: 42.0),
                "PointAndSpeakSnapshotState": SimpleNamespace(
                    CAPTURED=captured_state),
                "PointAndSpeakResolutionState": SimpleNamespace(
                    RESOLVED=resolved_state),
                "capture_frontmost_accessibility_targets": lambda: captured,
                "resolve_point_and_speak": lambda phrase, targets: decision,
                "prepare_point_and_speak_press_lease": (
                    lambda capture, target_id, expected_role, created_at:
                    lease if expected_role == "button" else None),
                "POINT_AND_SPEAK_TRANSACTIONS": Transactions(),
            },
        )["press_point_and_speak"]

        result = function(
            "n" * 32, "save project bluebird button", "button")
        encoded = json.dumps(result, sort_keys=True)

        self.assertEqual(result["state"], "executed")
        self.assertTrue(result["receipt"]["transaction"]["attempted"])
        self.assertEqual(calls, [("n" * 32, lease)])
        self.assertNotIn("Bluebird", encoded)
        self.assertNotIn("save project", encoded.casefold())
        self.assertNotIn("ax-secret", encoded)

    def test_action_is_not_reachable_from_preview_or_status_polling(self):
        action = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "press_point_and_speak")
        action_calls = {
            node.func.id for node in ast.walk(action)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("capture_frontmost_accessibility_targets", action_calls)
        self.assertIn("resolve_point_and_speak", action_calls)
        self.assertIn("prepare_point_and_speak_press_lease", action_calls)

        for function_name in ("runtime_status_snapshot",
                              "preview_point_and_speak"):
            function = next(
                node for node in TREE.body
                if isinstance(node, ast.FunctionDef)
                and node.name == function_name)
            names = {
                node.id for node in ast.walk(function)
                if isinstance(node, ast.Name)
            }
            self.assertNotIn("press_point_and_speak", names)
            self.assertNotIn("POINT_AND_SPEAK_TRANSACTIONS", names)
            self.assertNotIn("prepare_point_and_speak_press_lease", names)

    def test_malformed_expected_role_fails_before_capture(self):
        captures = []
        function = load_definitions(
            "press_point_and_speak",
            extra={
                "IS_MACOS": True,
                "time": SimpleNamespace(monotonic=lambda: 1.0),
                "capture_frontmost_accessibility_targets": lambda:
                    captures.append(True),
                "POINT_AND_SPEAK_TRANSACTIONS": SimpleNamespace(),
            },
        )["press_point_and_speak"]

        result = function("n" * 32, "search field", ["text_field"])

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(captures, [])


class VoiceObjectEmailComposeRuntimeTests(unittest.TestCase):
    @staticmethod
    def function(revealed, adapter, *, is_macos=True, enabled=True, reads=None):
        queued = object()
        email_destination = object()

        class EmailDraft:
            def __init__(self, recipients, subject, body):
                self.recipients = recipients
                self.subject = subject
                self.body = body

        if callable(revealed):
            revealed_value = revealed(EmailDraft, queued, email_destination)
        else:
            revealed_value = revealed

        def bridge():
            return SimpleNamespace(read=lambda item_id: (
                reads.append(item_id) if reads is not None else None)
                or revealed_value)

        namespace = load_definitions(
            "compose_voice_object_email",
            extra={
                "IS_MACOS": is_macos,
                "PREFERENCES": {"voice_object_commands": enabled},
                "EMAIL_COMPOSE_ADAPTER": adapter,
                "_voice_object_inbox_bridge": bridge,
                "InboxState": SimpleNamespace(QUEUED=queued),
                "Destination": SimpleNamespace(
                    EMAIL_DRAFT=email_destination),
                "EmailDraft": EmailDraft,
            },
        )
        return namespace["compose_voice_object_email"], EmailDraft, queued, \
            email_destination

    def test_email_only_runtime_reads_private_draft_and_returns_closed_receipt(self):
        calls = []

        class Adapter:
            def compose(self, nonce, **draft):
                calls.append((nonce, draft))
                return SimpleNamespace(to_mapping=lambda: {
                    "schema_version": 1,
                    "state": "requested",
                    "attempted": True,
                })

        reads = []

        def revealed(EmailDraft, queued, destination):
            return SimpleNamespace(
                state=queued,
                destination=destination,
                draft=EmailDraft(
                    ("ada@example.com",), "Project Bluebird",
                    "Private launch plan 8492"),
            )

        function, _draft, _queued, _destination = self.function(
            revealed, Adapter(), reads=reads)
        result = function("n" * 32, "voice-object:email-1")
        encoded = json.dumps(result, sort_keys=True)

        self.assertEqual(result, {
            "schema_version": 1, "state": "requested", "attempted": True})
        self.assertEqual(reads, ["voice-object:email-1"])
        self.assertEqual(calls[0][0], "n" * 32)
        self.assertEqual(calls[0][1]["recipients"], ("ada@example.com",))
        self.assertNotIn("ada@example.com", encoded)
        self.assertNotIn("Bluebird", encoded)
        self.assertNotIn("8492", encoded)

    def test_disabled_and_non_email_paths_never_receive_payload(self):
        calls = []

        class Adapter:
            def compose(self, nonce, **draft):
                calls.append((nonce, draft))
                state = "invalid" if not draft["recipients"] else "requested"
                return SimpleNamespace(to_mapping=lambda: {
                    "schema_version": 1, "state": state,
                    "attempted": False,
                })

        reads = []
        disabled, *_ = self.function(
            None, Adapter(), enabled=False, reads=reads)
        self.assertEqual(disabled("n" * 32, "voice-object:email-1")["state"],
                         "unavailable")
        self.assertEqual(reads, [])
        self.assertEqual(calls, [])

        def non_email(_EmailDraft, queued, _destination):
            return SimpleNamespace(
                state=queued, destination=object(),
                draft=SimpleNamespace(
                    title="Private task", notes="Do not compose"))

        function, *_ = self.function(non_email, Adapter(), reads=reads)
        result = function("n" * 32, "voice-object:task-1")
        self.assertEqual(result["state"], "invalid")
        self.assertEqual(calls, [(
            "n" * 32,
            {"recipients": (), "subject": None, "body": ""},
        )])

    def test_compose_is_absent_from_status_and_has_no_process_url_or_log_calls(self):
        status = next(
            node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot")
        action = next(
            node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "compose_voice_object_email")
        status_names = {
            node.id for node in ast.walk(status) if isinstance(node, ast.Name)
        }
        action_names = {
            node.id for node in ast.walk(action) if isinstance(node, ast.Name)
        }
        self.assertNotIn("compose_voice_object_email", status_names)
        self.assertFalse(action_names & {
            "print", "open", "subprocess", "Popen", "NSURL", "NSWorkspace"})


class VoiceObjectDraftCopyRuntimeTests(unittest.TestCase):
    @staticmethod
    def function(revealed, adapter, *, is_macos=True, enabled=True, reads=None):
        queued = object()
        task_destination = SimpleNamespace(value="task")
        calendar_destination = SimpleNamespace(value="calendar_draft")

        class TaskDraft:
            pass

        class CalendarDraft:
            pass

        def bridge():
            return SimpleNamespace(read=lambda item_id: (
                reads.append(item_id) if reads is not None else None)
                or revealed)

        namespace = load_definitions(
            "copy_voice_object_draft",
            extra={
                "IS_MACOS": is_macos,
                "PREFERENCES": {"voice_object_commands": enabled},
                "VOICE_DRAFT_CLIPBOARD_ADAPTER": adapter,
                "_voice_object_inbox_bridge": bridge,
                "_voice_object_draft_content": lambda _draft:
                    "Title: Project Bluebird\nNotes: Private launch 8492",
                "InboxState": SimpleNamespace(QUEUED=queued),
                "Destination": SimpleNamespace(
                    TASK=task_destination,
                    CALENDAR_DRAFT=calendar_destination),
                "TaskDraft": TaskDraft,
                "CalendarDraft": CalendarDraft,
            },
        )
        return (namespace["copy_voice_object_draft"], TaskDraft,
                CalendarDraft, queued, task_destination,
                calendar_destination)

    def test_fresh_task_reread_copies_only_to_adapter_and_keeps_receipt_closed(self):
        calls = []

        class Adapter:
            def copy(self, nonce, *, content):
                calls.append((nonce, content))
                return SimpleNamespace(to_mapping=lambda: {
                    "schema_version": 1,
                    "state": "copied",
                    "attempted": True,
                })

        reads = []
        placeholder = SimpleNamespace()
        function, TaskDraft, _CalendarDraft, queued, task, _calendar = \
            self.function(placeholder, Adapter(), reads=reads)
        placeholder.state = queued
        placeholder.destination = task
        placeholder.draft = TaskDraft()

        result = function("n" * 32, "voice-object:task-1", "task")

        self.assertEqual(reads, ["voice-object:task-1"])
        self.assertEqual(calls, [(
            "n" * 32,
            "Title: Project Bluebird\nNotes: Private launch 8492",
        )])
        self.assertEqual(result, {
            "schema_version": 1, "state": "copied", "attempted": True})
        self.assertNotIn("Bluebird", json.dumps(result))

    def test_destination_or_state_drift_consumes_nonce_without_private_content(self):
        calls = []

        class Adapter:
            def copy(self, nonce, *, content):
                calls.append((nonce, content))
                return SimpleNamespace(to_mapping=lambda: {
                    "schema_version": 1,
                    "state": "invalid",
                    "attempted": False,
                })

        placeholder = SimpleNamespace()
        function, TaskDraft, _CalendarDraft, queued, task, calendar = \
            self.function(placeholder, Adapter())
        placeholder.state = queued
        placeholder.destination = calendar
        placeholder.draft = TaskDraft()

        result = function("n" * 32, "voice-object:task-1", "task")

        self.assertEqual(result["state"], "invalid")
        self.assertEqual(calls, [("n" * 32, "")])
        disabled, *_ = self.function(
            placeholder, Adapter(), enabled=False)
        self.assertEqual(
            disabled("n" * 32, "voice-object:task-1", "task")["state"],
            "unavailable")
        self.assertEqual(len(calls), 1)

    def test_copy_is_absent_from_status_and_has_no_external_action_calls(self):
        status = next(
            node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot")
        action = next(
            node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "copy_voice_object_draft")
        status_names = {
            node.id for node in ast.walk(status) if isinstance(node, ast.Name)
        }
        action_names = {
            node.id for node in ast.walk(action) if isinstance(node, ast.Name)
        }
        self.assertNotIn("copy_voice_object_draft", status_names)
        self.assertFalse(action_names & {
            "print", "open", "subprocess", "Popen", "NSURL", "NSWorkspace",
            "EventKit", "requests", "socket",
        })


class VoiceObjectDraftClearRuntimeTests(unittest.TestCase):
    @staticmethod
    def functions(adapter, *, is_macos=True, enabled=True):
        namespace = load_definitions(
            "issue_voice_object_clear_clipboard_nonce",
            "clear_voice_object_draft_clipboard",
            extra={
                "IS_MACOS": is_macos,
                "PREFERENCES": {"voice_object_commands": enabled},
                "VOICE_DRAFT_CLIPBOARD_ADAPTER": adapter,
            },
        )
        return (
            namespace["issue_voice_object_clear_clipboard_nonce"],
            namespace["clear_voice_object_draft_clipboard"],
        )

    def test_clear_uses_only_adapter_capability_and_closed_receipt(self):
        calls = []

        class Adapter:
            def issue_clear_nonce(self):
                calls.append(("issue",))
                return "clear_session_nonce_123456"

            def clear(self, nonce):
                calls.append(("clear", nonce))
                return SimpleNamespace(to_mapping=lambda: {
                    "schema_version": 1,
                    "state": "cleared",
                    "attempted": True,
                })

        issue, clear = self.functions(Adapter())
        nonce = issue()
        result = clear(nonce)

        self.assertEqual(calls, [
            ("issue",), ("clear", "clear_session_nonce_123456")])
        self.assertEqual(result, {
            "schema_version": 1, "state": "cleared", "attempted": True})
        self.assertEqual(set(result), {"schema_version", "state", "attempted"})

    def test_clear_is_unavailable_off_mac_or_when_disabled(self):
        class Adapter:
            def issue_clear_nonce(self):
                raise AssertionError("adapter must remain untouched")

            def clear(self, _nonce):
                raise AssertionError("adapter must remain untouched")

        for is_macos, enabled in ((False, True), (True, False)):
            with self.subTest(is_macos=is_macos, enabled=enabled):
                issue, clear = self.functions(
                    Adapter(), is_macos=is_macos, enabled=enabled)
                self.assertEqual(issue(), "")
                self.assertEqual(clear("n" * 32), {
                    "schema_version": 1,
                    "state": "unavailable",
                    "attempted": False,
                })

    def test_clear_is_absent_from_status_and_has_no_queue_or_external_calls(self):
        status = next(
            node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot")
        action = next(
            node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "clear_voice_object_draft_clipboard")
        status_names = {
            node.id for node in ast.walk(status) if isinstance(node, ast.Name)
        }
        action_names = {
            node.id for node in ast.walk(action) if isinstance(node, ast.Name)
        }
        self.assertNotIn("clear_voice_object_draft_clipboard", status_names)
        self.assertFalse(action_names & {
            "print", "open", "subprocess", "Popen", "NSURL", "NSWorkspace",
            "EventKit", "requests", "socket", "_voice_object_inbox_bridge",
        })


class DropTargetPreviewRuntimeTests(unittest.TestCase):
    @staticmethod
    def namespace(
        capture, decision, *, is_macos=True, captured_state=None,
    ):
        captured = captured_state if captured_state is not None else object()
        resolved = SimpleNamespace(value="resolved")

        class EnumFactory:
            def __init__(self, allowed):
                self.allowed = allowed

            def __call__(self, value):
                if value not in self.allowed:
                    raise ValueError(value)
                return SimpleNamespace(value=value)

        class Capability:
            def __init__(self, kinds, effects):
                self.accepted_kinds = kinds
                self.accepted_effects = effects

        ns = load_definitions(
            "preview_drop_to_target",
            assignments=("DROP_TARGET_PREVIEW_ROLES",),
            extra={
                "IS_MACOS": is_macos,
                "DropTargetSnapshotState": SimpleNamespace(CAPTURED=captured),
                "DropTargetDecisionState": SimpleNamespace(RESOLVED=resolved),
                "SourceKind": EnumFactory({
                    "file_reference", "image_reference", "text_selection",
                    "url_reference"}),
                "DropEffect": EnumFactory({"copy", "link", "move"}),
                "DropCapability": Capability,
                "capture_frontmost_drop_target_evidence":
                    lambda policy: capture(policy),
                "decide_drop_to_target":
                    lambda proposal, targets: decision(
                        proposal, targets, resolved),
            },
        )
        return ns["preview_drop_to_target"]

    def test_resolved_preview_is_transient_content_scoped_and_no_execution(self):
        captured = SimpleNamespace(value="captured")
        capture_value = SimpleNamespace(
            targets=({
                "target_id": "ax-drop-private",
                "title": "Project Bluebird Team Inbox",
                "label": "",
            },),
            receipt=SimpleNamespace(
                state=captured, observed_elements=5, emitted_targets=1,
                skipped_elements=2, truncated=False),
        )

        def capture(policy):
            self.assertEqual(set(policy), {"AXGroup"})
            capability = policy["AXGroup"]
            self.assertEqual(capability.accepted_kinds[0].value,
                             "file_reference")
            self.assertEqual(capability.accepted_effects[0].value, "copy")
            return capture_value

        def decide(proposal, targets, resolved):
            self.assertEqual(proposal, {
                "schema_version": 1,
                "target_hint": "team inbox",
                "source_kind": "file_reference",
                "effect": "copy",
            })
            self.assertIs(targets, capture_value.targets)
            return SimpleNamespace(
                state=resolved, target_id="ax-drop-private",
                receipt=SimpleNamespace(
                    observed_targets=1, eligible_targets=1,
                    contradiction_count=0,
                    evidence=("exact_name", "source_compatible",
                              "effect_compatible"),
                    confidence_bucket="very_high", margin_bucket="wide"),
            )

        preview = self.namespace(
            capture, decide, captured_state=captured)(
            "team inbox", "AXGroup", "file_reference", "copy")
        receipt = json.dumps(preview["receipt"], sort_keys=True)

        self.assertEqual(preview["state"], "resolved")
        self.assertEqual(preview["accessibility_name"],
                         "Project Bluebird Team Inbox")
        self.assertEqual(preview["role"], "AXGroup")
        self.assertEqual(preview["receipt"]["execution"], "none")
        self.assertEqual(preview["receipt"]["capability_basis"],
                         "caller_declared_role_policy")
        self.assertNotIn("team inbox", receipt)
        self.assertNotIn("Bluebird", receipt)
        self.assertNotIn("ax-drop-private", receipt)

    def test_permission_invalid_and_non_mac_paths_fail_closed(self):
        calls = []
        denied_value = SimpleNamespace(
            targets=(), receipt=SimpleNamespace(
                state=SimpleNamespace(value="permission_denied"),
                observed_elements=0, emitted_targets=0,
                skipped_elements=0, truncated=False))

        denied = self.namespace(
            lambda _policy: denied_value,
            lambda *_args: calls.append("decide"))
        preview = denied("inbox", "AXGroup", "file_reference", "copy")
        self.assertEqual(preview["state"], "permission_denied")
        self.assertEqual(calls, [])
        self.assertEqual(preview["receipt"]["execution"], "none")

        capture_calls = []
        worker = self.namespace(
            lambda _policy: capture_calls.append(True),
            lambda *_args: None)
        for arguments in (
            ("", "AXGroup", "file_reference", "copy"),
            ("inbox\nname", "AXGroup", "file_reference", "copy"),
            ("inbox", "AXButton", "file_reference", "copy"),
            ("inbox", "AXGroup", "payload", "copy"),
            ("inbox", "AXGroup", "file_reference", "execute"),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(worker(*arguments)["state"], "unavailable")
        self.assertEqual(capture_calls, [])

        non_mac = self.namespace(
            lambda _policy: capture_calls.append(True),
            lambda *_args: None, is_macos=False)
        self.assertEqual(non_mac(
            "inbox", "AXGroup", "file_reference", "copy")["state"],
            "unavailable")
        self.assertEqual(capture_calls, [])

    def test_preview_has_no_write_log_persistence_or_routine_status_surface(self):
        preview = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "preview_drop_to_target")
        called = {
            node.func.id for node in ast.walk(preview)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(called & {
            "print", "open", "paste_text", "type_text", "click", "focus",
            "drag", "drop", "save", "write_transcript_log",
        })
        status = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot")
        status_names = {
            node.id for node in ast.walk(status)
            if isinstance(node, ast.Name)
        }
        self.assertNotIn("preview_drop_to_target", status_names)


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

    def test_last_result_shortcut_only_delegates_to_existing_gui(self):
        status_bar = next(
            node for node in TREE.body
            if isinstance(node, ast.ClassDef) and node.name == "StatusBar")
        methods = {
            node.name: node for node in status_bar.body
            if isinstance(node, ast.FunctionDef)
        }
        # The quick-glance menu keeps one Results shortcut whose title
        # carries the review flag and which hides until a result exists;
        # the dense evidence itself lives in the window's Results view.
        refresh_source = ast.unparse(methods["refresh_recognition_item"])
        self.assertIn("recognition_root_title", refresh_source)
        self.assertIn("last_result_evidence", refresh_source)
        self.assertIn("setHidden_", refresh_source)
        init_source = ast.unparse(methods["initWithGUI_"]) \
            if "initWithGUI_" in methods else ast.unparse(status_bar)
        self.assertIn("openResults:", init_source)

        opener = methods["openResults_"]
        attributes = {
            node.attr for node in ast.walk(opener)
            if isinstance(node, ast.Attribute)
        }
        names = {
            node.id for node in ast.walk(opener) if isinstance(node, ast.Name)
        }
        self.assertIn("show_results", attributes)
        self.assertFalse(names & {
            "paste_text", "copy_voice_object_draft", "PhoneHandler",
            "recognize", "transcribe", "compile_cleanup",
        })


class VoiceInboxMenuTests(unittest.TestCase):
    def test_menu_title_is_count_only_and_bounded_by_inbox_capacity(self):
        title = load_definitions(
            "voice_inbox_menu_title",
            extra={"MAX_ITEMS": 256},
        )["voice_inbox_menu_title"]

        self.assertEqual(title({"queued_count": 0}), "Voice Inbox")
        self.assertEqual(title({"queued_count": 3}),
                         "Voice Inbox — 3 queued")
        self.assertEqual(title({"queued_count": 1000}),
                         "Voice Inbox — 256 queued")
        self.assertEqual(title({"queued_count": -2}), "Voice Inbox")
        self.assertEqual(title({"queued_count": True}), "Voice Inbox")
        self.assertEqual(title({"queued_count": "private draft"}),
                         "Voice Inbox")

    def test_menu_delegates_to_gui_without_reading_or_acting_on_drafts(self):
        status_bar = next(
            node for node in TREE.body
            if isinstance(node, ast.ClassDef) and node.name == "StatusBar")
        methods = {
            node.name: node for node in status_bar.body
            if isinstance(node, ast.FunctionDef)
        }
        opener = methods["openVoiceInbox_"]
        opener_names = {
            node.id for node in ast.walk(opener) if isinstance(node, ast.Name)
        }
        opener_attributes = {
            node.attr for node in ast.walk(opener)
            if isinstance(node, ast.Attribute)
        }
        self.assertEqual(opener_names & {
            "inspect_voice_object_drafts", "_voice_object_inbox_bridge",
            "VoiceInbox", "copy_voice_object_draft",
            "compose_voice_object_email",
        }, set())
        self.assertIn("show_voice_inbox", opener_attributes)

        menu_refresh = methods["menuWillOpen_"]
        refresh_names = {
            node.id for node in ast.walk(menu_refresh)
            if isinstance(node, ast.Name)
        }
        self.assertIn("voice_object_inbox_status", refresh_names)
        self.assertIn("voice_inbox_menu_title", refresh_names)

        init_source = ast.unparse(methods["init"])
        self.assertIn(
            "self.voice_inbox_item.setEnabled_(False)", init_source)
        refresh_tries = [
            node for node in menu_refresh.body if isinstance(node, ast.Try)
        ]
        self.assertGreaterEqual(len(refresh_tries), 2)
        inbox_refresh = ast.unparse(refresh_tries[0])
        other_refresh = next(
            ast.unparse(node) for node in refresh_tries
            if "rebuild_faces" in ast.unparse(node))
        self.assertIn("voice_object_inbox_status", inbox_refresh)
        self.assertNotIn("rebuild_faces", inbox_refresh)
        self.assertIn("rebuild_faces", other_refresh)


class VoiceOutboxMenuTests(unittest.TestCase):
    def test_menu_title_is_count_only_and_bounded(self):
        title = load_definitions(
            "voice_outbox_menu_title",
            extra={"VOICE_OUTBOX_MAX_ITEMS": 20},
        )["voice_outbox_menu_title"]

        self.assertEqual(title(0), "Voice Outbox")
        self.assertEqual(title(3), "Voice Outbox — 3 recoverable")
        self.assertEqual(title(1000), "Voice Outbox — 20 recoverable")
        for invalid in (-2, True, "private dictation", None):
            with self.subTest(invalid=invalid):
                self.assertEqual(title(invalid), "Voice Outbox")

    def test_menu_only_routes_to_existing_recovery_surface(self):
        status_bar = next(
            node for node in TREE.body
            if isinstance(node, ast.ClassDef) and node.name == "StatusBar")
        methods = {
            node.name: node for node in status_bar.body
            if isinstance(node, ast.FunctionDef)
        }
        opener = methods["openVoiceOutbox_"]
        attributes = {
            node.attr for node in ast.walk(opener)
            if isinstance(node, ast.Attribute)
        }
        names = {
            node.id for node in ast.walk(opener) if isinstance(node, ast.Name)
        }
        self.assertIn("show_outbox", attributes)
        self.assertFalse(names & {
            "copy_latest_outbox", "acknowledge_recoverable", "paste_text",
            "type_text", "INSERTION_COORDINATOR",
        })

        init_source = ast.unparse(methods["init"])
        self.assertIn("self.voice_outbox_item.setEnabled_(False)", init_source)
        refresh_source = ast.unparse(methods["menuWillOpen_"])
        self.assertIn("INSERTION_COORDINATOR.recoverable_count()",
                      refresh_source)
        self.assertNotIn("INSERTION_COORDINATOR.recoverable()",
                         refresh_source)


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


class FakePortAudio:
    """sounddevice over PortAudio on macOS, reduced to the device table.

    Pa_Initialize snapshots the default input; Pa_GetDefaultInputDevice only
    reads that snapshot. Terminating with a stream open would free it under
    its Python owner, so the fake refuses.
    """

    def __init__(self, default):
        self.system_default = default
        self.cached_default = default
        self.streams = []
        self.reinitializations = 0

    def InputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        stream.device = self.cached_default
        self.streams.append(stream)
        return stream

    def open_devices(self):
        return [stream.device for stream in self.streams if not stream.closed]

    def _terminate(self):
        if self.open_devices():
            raise AssertionError("PortAudio terminated with a stream open")
        self.cached_default = None

    def _initialize(self):
        self.cached_default = self.system_default
        self.reinitializations += 1


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

            def wait(self, timeout=None):
                return 0

        process = FakeProcess()
        requests = []

        def exchange(_process, chunks, *, timeout):
            requests.append((tuple(bytes(chunk) for chunk in chunks), timeout))
            return ({"ready": True, "load_s": 0.1} if not chunks else
                    {"ok": True, "text": "hello", "processing_s": 0.02})

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
                    "time": time,
                    "json": json,
                    "np": np,
                    "PARAKEET_STARTUP_TIMEOUT": 10.0,
                    "PARAKEET_MIN_REQUEST_TIMEOUT": 3.0,
                    "PARAKEET_MAX_REQUEST_TIMEOUT": 10.0,
                    "PARAKEET_PROFILE": PARAKEET_PROFILE,
                    "mark_model_warm_path_observed": lambda _provider: True,
                },
            )
            client = ns["ParakeetClient"](
                helper=helper, process_factory=lambda *_args, **_kwargs: process,
                exchange=exchange)
            result = client.transcribe(np.array([0.25, -0.5], dtype=np.float32))

        self.assertEqual(result, ("hello", 0.02))
        payload = b"".join(requests[1][0])
        self.assertEqual(struct.unpack("<Q", payload[:8])[0], 2)
        self.assertEqual(len(payload), 8 + 2 * 4)
        self.assertAlmostEqual(requests[1][1], 3.0, places=4)

    @unittest.skipIf(os.name == "nt", "select() cannot wait on Windows pipes")
    def test_bounded_helper_exchange_times_out_without_a_response(self):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        process = SimpleNamespace(
            stdin=os.fdopen(input_write, "wb", buffering=0),
            stdout=os.fdopen(output_read, "rb", buffering=0),
        )
        try:
            exchange = load_definitions(
                "bounded_helper_exchange",
                extra={
                    "PARAKEET_MAX_RESPONSE_BYTES": 64 * 1024,
                    "PARAKEET_MAX_REQUEST_TIMEOUT": 60.0,
                    "json": json,
                    "os": os,
                    "select": select,
                    "time": time,
                },
            )["bounded_helper_exchange"]
            with self.assertRaisesRegex(TimeoutError, "response timed out"):
                exchange(process, (), timeout=0.01)
        finally:
            process.stdin.close()
            process.stdout.close()
            os.close(input_read)
            os.close(output_write)

    @unittest.skipIf(os.name == "nt", "select() cannot wait on Windows pipes")
    def test_bounded_helper_exchange_handles_framed_pipe_io(self):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        process = SimpleNamespace(
            stdin=os.fdopen(input_write, "wb", buffering=0),
            stdout=os.fdopen(output_read, "rb", buffering=0),
        )
        received = bytearray()

        def helper():
            while len(received) < 12:
                received.extend(os.read(input_read, 3))
            os.write(output_write, b'{"ok":true,')
            os.write(output_write, b'"text":"yes"}\n')

        worker = threading.Thread(target=helper)
        worker.start()
        try:
            exchange = load_definitions(
                "bounded_helper_exchange",
                extra={
                    "PARAKEET_MAX_RESPONSE_BYTES": 64 * 1024,
                    "PARAKEET_MAX_REQUEST_TIMEOUT": 60.0,
                    "json": json,
                    "os": os,
                    "select": select,
                    "time": time,
                },
            )["bounded_helper_exchange"]
            response = exchange(
                process, (struct.pack("<Q", 1), b"data"), timeout=1.0)
        finally:
            worker.join(timeout=1.0)
            process.stdin.close()
            process.stdout.close()
            os.close(input_read)
            os.close(output_write)

        self.assertEqual(response, {"ok": True, "text": "yes"})
        self.assertEqual(bytes(received), struct.pack("<Q", 1) + b"data")

    @unittest.skipIf(os.name == "nt", "select() cannot wait on Windows pipes")
    def test_bounded_helper_exchange_times_out_on_blocked_write(self):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        process = SimpleNamespace(
            stdin=os.fdopen(input_write, "wb", buffering=0),
            stdout=os.fdopen(output_read, "rb", buffering=0),
        )
        try:
            exchange = load_definitions(
                "bounded_helper_exchange",
                extra={
                    "PARAKEET_MAX_RESPONSE_BYTES": 64 * 1024,
                    "PARAKEET_MAX_REQUEST_TIMEOUT": 60.0,
                    "json": json,
                    "os": os,
                    "select": select,
                    "time": time,
                },
            )["bounded_helper_exchange"]
            with self.assertRaisesRegex(TimeoutError, "write timed out"):
                exchange(process, (b"x" * 1_000_000,), timeout=0.01)
        finally:
            process.stdin.close()
            process.stdout.close()
            os.close(input_read)
            os.close(output_write)

    def test_timeout_closes_child_and_next_call_restarts_lazily(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO()
                self.terminated = False
                self.killed = False
                self.wait_calls = 0

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise TimeoutError("helper did not exit")
                return 0

            def kill(self):
                self.killed = True

        first_process = FakeProcess()
        processes = [first_process, FakeProcess()]
        calls = []

        def exchange(process, chunks, *, timeout):
            calls.append((process, bool(chunks), timeout))
            if not chunks:
                return {"ready": True, "load_s": 0.1}
            if process is first_process:
                raise TimeoutError("helper response timed out")
            return {"ok": True, "text": "recovered", "processing_s": 0.1}

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
                    "time": time,
                    "np": np,
                    "PARAKEET_STARTUP_TIMEOUT": 10.0,
                    "PARAKEET_MIN_REQUEST_TIMEOUT": 3.0,
                    "PARAKEET_MAX_REQUEST_TIMEOUT": 10.0,
                    "PARAKEET_PROFILE": PARAKEET_PROFILE,
                    "mark_model_warm_path_observed": lambda _provider: True,
                },
            )
            client = ns["ParakeetClient"](
                helper=helper,
                process_factory=lambda *_args, **_kwargs: processes.pop(0),
                exchange=exchange)
            first = client.transcribe(np.ones(160, dtype=np.float32))
            second = client.transcribe(np.ones(160, dtype=np.float32))

        self.assertIsNone(first)
        self.assertEqual(second, ("recovered", 0.1))
        self.assertTrue(first_process.terminated)
        self.assertTrue(first_process.killed)
        self.assertEqual(first_process.wait_calls, 2)

    @staticmethod
    def client(directory, *, process_factory, exchange, clock=None,
               printed=None):
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
                "time": time,
                "np": np,
                "PARAKEET_STARTUP_TIMEOUT": 10.0,
                "PARAKEET_MIN_REQUEST_TIMEOUT": 3.0,
                "PARAKEET_MAX_REQUEST_TIMEOUT": 10.0,
                "PARAKEET_PROFILE": PARAKEET_PROFILE,
                "mark_model_warm_path_observed": lambda _provider: True,
                "print": lambda *args, **_kwargs: (
                    printed.append(" ".join(str(arg) for arg in args))
                    if printed is not None else None),
            },
        )
        return ns["ParakeetClient"](
            helper=helper, process_factory=process_factory,
            exchange=exchange, clock=clock)

    def test_helper_that_cannot_be_spawned_falls_back_to_whisper(self):
        # A zero-byte or non-executable helper file exists, so the route is
        # taken, and the spawn itself is what fails.
        for error in (PermissionError(13, "Permission denied"),
                      OSError(8, "Exec format error")):
            with self.subTest(error=type(error).__name__), \
                    tempfile.TemporaryDirectory() as directory:
                printed = []

                def refuse(*_args, _error=error, **_kwargs):
                    raise _error

                client = self.client(
                    directory, process_factory=refuse,
                    exchange=lambda *_args, **_kwargs: self.fail(
                        "no process to talk to"),
                    printed=printed)
                audio = np.ones(160, dtype=np.float32)
                self.assertIsNone(client.transcribe(audio))

                recognition, decodes = ParakeetCrosscheckTests.cascade(
                    parakeet=client,
                    scripted={"tiny-repo": ("Ship the installer today.", 0.9)},
                    cached={"tiny-repo"})
                self.assertEqual(recognition.text, "Ship the installer today.")
                self.assertEqual(recognition.engine, "tiny")
                self.assertEqual(len(printed), 1)

    def test_failed_start_is_remembered_and_retried_after_a_backoff(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO()
                self.alive = True

            def poll(self):
                return None if self.alive else 1

            def terminate(self):
                self.alive = False

            def wait(self, timeout=None):
                return 0

        now = [1_000.0]
        spawned = []
        ready = [False]
        printed = []

        def factory(*_args, **_kwargs):
            spawned.append(FakeProcess())
            return spawned[-1]

        def exchange(_process, chunks, *, timeout):
            if not chunks:
                if not ready[0]:
                    raise TimeoutError("helper response timed out")
                return {"ready": True, "load_s": 12.25}
            return {"ok": True, "text": "hello", "processing_s": 0.02}

        audio = np.ones(160, dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(
                directory, process_factory=factory, exchange=exchange,
                clock=lambda: now[0], printed=printed)

            # One slow start costs one start-up timeout, not one per decode.
            for _decode in range(4):
                self.assertIsNone(client.transcribe(audio))
            self.assertEqual(len(spawned), 1)
            self.assertFalse(spawned[0].alive)

            now[0] += 29.0
            self.assertIsNone(client.transcribe(audio))
            self.assertEqual(len(spawned), 1)
            now[0] += 2.0
            self.assertIsNone(client.transcribe(audio))
            self.assertEqual(len(spawned), 2)

            # A second failure in a row waits twice as long.
            now[0] += 59.0
            self.assertIsNone(client.transcribe(audio))
            self.assertEqual(len(spawned), 2)
            now[0] += 2.0
            ready[0] = True
            self.assertEqual(client.transcribe(audio), ("hello", 0.02))
            self.assertEqual(len(spawned), 3)

            # A helper that started and later died is restarted at once: the
            # backoff is for starts that fail, and a success clears it.
            spawned[-1].alive = False
            self.assertEqual(client.transcribe(audio), ("hello", 0.02))
            self.assertEqual(len(spawned), 4)

        failures = [line for line in printed if "did not start" in line]
        self.assertEqual(len(failures), 2)
        for line in failures:
            self.assertNotIn(directory, line)
            self.assertNotIn("timed out", line)

    def test_parakeet_processing_time_reaches_recognition_evidence(self):
        class ImmediatePool:
            def submit(self, function, *args, **kwargs):
                return SimpleNamespace(
                    result=lambda: function(*args, **kwargs))

        ns = load_definitions(
            "transcribe_detailed",
            "_parakeet_crosschecked",
            "_clean_native_processing_s",
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
                "PARAKEET_IO_POOL": ImmediatePool(),
                "PARAKEET_CROSSCHECK": False,
                "PARAKEET_CROSSCHECK_MAX_SECONDS": 90.0,
                "SAMPLE_RATE": 16_000,
                "Recognition": Recognition,
                "prepare_asr_audio": lambda audio: audio,
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
                "save_preferences", "normalize_hotkey",
                "normalize_sound_theme",
                assignments={
                    "FACE_CHOICES", "DEFAULT_FACE", "PREFERENCES",
                    "HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                    "SOUND_THEMES", "SOUND_THEME_DEFAULT",
                },
                extra={
                    "PREFERENCES_FILE": preferences,
                    "IS_MACOS": True,
                    "ACOUSTIC_TIME_MACHINE": AcousticTimeMachine(),
                    "json": json,
                    "apply_hotkey_bindings": lambda: None,
                    "atomic_write_text": lambda path, value:
                        path.write_text(value, encoding="utf-8"),
                },
            )
            preferences.write_text(json.dumps({
                "flight_recorder": True,
                "acoustic_time_machine": True,
                "voice_object_commands": False,
                "spoken_edit_commands": False,
                "face": "dragon",
            }), encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["current_face"](), "parrot")
            self.assertTrue(ns["PREFERENCES"]["flight_recorder"])
            self.assertTrue(ns["PREFERENCES"]["acoustic_time_machine"])
            self.assertFalse(ns["PREFERENCES"]["voice_object_commands"])
            self.assertFalse(ns["PREFERENCES"]["spoken_edit_commands"])
            # A1 migration: this file predates the hotkey setting entirely and
            # keeps Right Option without saying anything about it.
            self.assertEqual(ns["PREFERENCES"]["hotkey"], "alt_r")
            self.assertEqual(ns["PREFERENCES"]["undo_hotkey"], "")
            self.assertEqual(ns["PREFERENCES"]["sounds"], "system")
            self.assertFalse(ns["PREFERENCES"]["recent_dictations"])

            ns["PREFERENCES"]["face"] = "FOX"
            ns["save_preferences"]()
            saved = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(saved, {
                "flight_recorder": True,
                "acoustic_time_machine": True,
                "voice_object_commands": False,
                "spoken_edit_commands": False,
                "selective_relisten": False,
                "face": "fox",
                "hotkey": "alt_r",
                "undo_hotkey": "",
                "sounds": "system",
                "recent_dictations": False,
                "language": "en",
            })

    def test_control_preferences_round_trip_and_reject_bad_values(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            ns = load_definitions(
                "normalize_face", "current_face", "load_preferences",
                "save_preferences", "normalize_hotkey",
                "normalize_sound_theme",
                assignments={
                    "FACE_CHOICES", "DEFAULT_FACE", "PREFERENCES",
                    "HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                    "SOUND_THEMES", "SOUND_THEME_DEFAULT",
                },
                extra={
                    "PREFERENCES_FILE": preferences,
                    "IS_MACOS": True,
                    "ACOUSTIC_TIME_MACHINE": AcousticTimeMachine(),
                    "json": json,
                    "apply_hotkey_bindings": lambda: None,
                    "atomic_write_text": lambda path, value:
                        path.write_text(value, encoding="utf-8"),
                },
            )
            preferences.write_text(json.dumps({
                "hotkey": "f13",
                "undo_hotkey": "f14",
                "sounds": "whisper",
                "recent_dictations": True,
            }), encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["PREFERENCES"]["hotkey"], "f13")
            self.assertEqual(ns["PREFERENCES"]["undo_hotkey"], "f14")
            self.assertEqual(ns["PREFERENCES"]["sounds"], "whisper")
            self.assertTrue(ns["PREFERENCES"]["recent_dictations"])

            # A stored value that is not bindable never reaches the listener:
            # an ordinary letter would swallow typing everywhere.
            preferences.write_text(json.dumps({
                "hotkey": "a",
                "undo_hotkey": "space",
                "sounds": "airhorn",
                "recent_dictations": "yes",
            }), encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["PREFERENCES"]["hotkey"], "alt_r")
            self.assertEqual(ns["PREFERENCES"]["undo_hotkey"], "")
            self.assertEqual(ns["PREFERENCES"]["sounds"], "system")
            self.assertFalse(ns["PREFERENCES"]["recent_dictations"])

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
                        "spoken_edit_commands": voice_objects,
                        "selective_relisten": voice_objects,
                    }), encoding="utf-8")
                    buffer = AcousticTimeMachine()
                    ns = load_definitions(
                        "normalize_face", "load_preferences",
                        "normalize_hotkey", "normalize_sound_theme",
                        assignments={
                            "FACE_CHOICES", "DEFAULT_FACE", "PREFERENCES",
                            "HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                            "SOUND_THEMES", "SOUND_THEME_DEFAULT",
                        },
                        extra={
                            "PREFERENCES_FILE": path,
                            "IS_MACOS": is_macos,
                            "ACOUSTIC_TIME_MACHINE": buffer,
                            "json": json,
                            "apply_hotkey_bindings": lambda: None,
                        },
                    )
                    ns["load_preferences"]()
                    self.assertIs(
                        ns["PREFERENCES"]["acoustic_time_machine"], expected)
                    self.assertIs(
                        ns["PREFERENCES"]["voice_object_commands"], expected)
                    self.assertIs(
                        ns["PREFERENCES"]["spoken_edit_commands"], expected)
                    self.assertIs(
                        ns["PREFERENCES"]["selective_relisten"], expected)
                    self.assertIs(buffer.enabled, expected)

    def test_all_default_faces_are_supported(self):
        ns = load_definitions(
            "normalize_face",
            assignments={"FACE_CHOICES", "DEFAULT_FACE"},
        )
        # The property, not the roster: every supported face normalizes to
        # itself, so adding a companion does not fail this test.
        self.assertEqual(
            tuple(ns["normalize_face"](face) for face in ns["FACE_CHOICES"]),
            tuple(ns["FACE_CHOICES"]),
        )
        self.assertIn(ns["DEFAULT_FACE"], ns["FACE_CHOICES"])
        self.assertEqual(
            ns["normalize_face"]("not-an-animal"), ns["DEFAULT_FACE"])

    def test_reduce_motion_freezes_hud_audio_animation(self):
        ns = load_definitions(
            "hud_level_step", assignments={"LEVEL_SMOOTH"})

        self.assertEqual(ns["hud_level_step"](0.9, 0.7, "recording", True), 0.0)
        self.assertGreater(
            ns["hud_level_step"](0.9, 0.0, "recording", False), 0.0)
        self.assertLess(
            ns["hud_level_step"](0.9, 0.7, "error", False), 0.7)


class SelectiveRelistenPreferenceTests(unittest.TestCase):
    def _namespace(self, *, is_macos=True, evidence_ready=True):
        calls = []
        status = SimpleNamespace(ready=evidence_ready)
        ns = load_definitions(
            "set_selective_relisten_enabled",
            assignments={"PREFERENCES"},
            extra={
                "DEFAULT_FACE": "parrot",
                "HOTKEY_DEFAULT": "alt_r",
                "SOUND_THEME_DEFAULT": "system",
                "IS_MACOS": is_macos,
                "RELISTEN_ACTIVATION_FILE": Path("private-receipt.json"),
                "load_activation_receipt": lambda _path: status,
                "refresh_selective_relisten_verifier":
                    lambda: calls.append("refresh"),
                "save_preferences": lambda: calls.append("save"),
            },
        )
        return ns, calls

    def test_enable_requires_current_local_activation_evidence(self):
        ns, calls = self._namespace(evidence_ready=False)

        with self.assertRaisesRegex(RuntimeError, "evidence"):
            ns["set_selective_relisten_enabled"](True)

        self.assertFalse(ns["PREFERENCES"]["selective_relisten"])
        self.assertEqual(calls, [])

    def test_enable_and_disable_refresh_lifetime_then_persist(self):
        ns, calls = self._namespace()

        ns["set_selective_relisten_enabled"](True)
        self.assertTrue(ns["PREFERENCES"]["selective_relisten"])
        self.assertEqual(calls, ["refresh", "save"])

        ns["set_selective_relisten_enabled"](False)
        self.assertFalse(ns["PREFERENCES"]["selective_relisten"])
        self.assertEqual(calls, ["refresh", "save", "refresh", "save"])

    def test_non_mac_cannot_activate_even_with_receipt(self):
        ns, calls = self._namespace(is_macos=False)

        ns["set_selective_relisten_enabled"](True)

        self.assertFalse(ns["PREFERENCES"]["selective_relisten"])
        self.assertEqual(calls, ["refresh", "save"])


class WhisperFaceThemeTests(unittest.TestCase):
    def test_light_and_dark_palettes_share_brand_but_not_work_surfaces(self):
        self.assertEqual(LIGHT_PALETTE.brand, DARK_PALETTE.brand)
        self.assertEqual(LIGHT_PALETTE.error, DARK_PALETTE.error)
        self.assertNotEqual(LIGHT_PALETTE.bg, DARK_PALETTE.bg)
        self.assertNotEqual(LIGHT_PALETTE.surface, DARK_PALETTE.surface)
        self.assertNotEqual(LIGHT_PALETTE.ink, DARK_PALETTE.ink)
        # The chip map, the character roster, and the runtime picker roster
        # are one roster: every companion has a chip, no chip is orphaned,
        # and everything drawn is actually selectable in the macOS picker
        # and the Windows tray, which both iterate FACE_CHOICES. Asserted
        # as a property so the roster can grow without this test restating
        # it, the same lesson #129 applied to the face normalization test.
        from whisper_face_characters import FACE_ORDER
        face_choices = load_definitions(
            "normalize_face", assignments={"FACE_CHOICES"})["FACE_CHOICES"]
        self.assertEqual(set(FACE_CHIP_COLORS), set(FACE_ORDER))
        self.assertEqual(set(FACE_CHIP_COLORS), set(face_choices))

    # The chip map argues for itself in CIELAB, and that argument went false
    # in silence when the goldens landed: the comment still claimed a dE 12.5
    # floor while olive sat 11.0 from panda. A comment cannot check its own
    # arithmetic, so the two claims it rests on are pinned here instead.
    CHIP_TWINS = frozenset({"pickles", "olive"})
    CHIP_HUE_FLOOR = 12.5
    # Named pairs may sit below the floor, each pinned at the distance it may
    # not close further. Exempting a *face* rather than a pair would let a new
    # look-alike in behind the golden that is already exempt, so the key is
    # the pair. pickles/olive is the design: litter-mates, told apart by
    # accessory. panda/olive is not designed -- olive's cream simply landed
    # near panda's -- and is pinned at its measured distance so the accident
    # cannot deepen while the palette waits on a decision.
    CHIP_EXEMPT_PAIRS = {
        frozenset({"pickles", "olive"}): 6.2,
        frozenset({"panda", "olive"}): 10.9,
    }

    @staticmethod
    def _cielab(color):
        """CIE L*a*b* under D65 for a linear-decoded sRGB triple."""
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                  for c in color]
        r, g, b = linear
        xyz = (
            (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047,
            (r * 0.2126729 + g * 0.7151522 + b * 0.0721750),
            (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883,
        )
        fx, fy, fz = (
            t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29
            for t in xyz
        )
        return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

    def test_chip_colors_stay_nameable_apart_except_for_the_twins(self):
        """Hue names the chip, and only named pairs may opt out.

        Every pair stays at least as far apart as wolf/panda, the pair that
        set the floor, unless it appears in CHIP_EXEMPT_PAIRS -- and an
        exempt pair still may not drift closer than the distance recorded
        for it. Being one of the goldens buys a face nothing on its own, so
        a new chip that lands near pickles is caught like any other. On top
        of that the litter-mates must stay the closest pair in the map: an
        accidental look-alike tighter than the designed one is a bug even if
        it clears every other rule here.
        """
        labs = {face: self._cielab(color)
                for face, color in FACE_CHIP_COLORS.items()}
        self.assertEqual(
            self.CHIP_TWINS & set(labs), self.CHIP_TWINS,
            "the sanctioned near-twins are no longer in the chip map")

        def distance(one, two):
            return sum((a - b) ** 2
                       for a, b in zip(labs[one], labs[two])) ** 0.5

        pairs = [(distance(a, b), a, b)
                 for index, a in enumerate(sorted(labs))
                 for b in sorted(labs)[index + 1:]]
        self.assertTrue(pairs, "expected a chip roster to compare")

        for gap, one, two in pairs:
            pinned = self.CHIP_EXEMPT_PAIRS.get(frozenset({one, two}))
            if pinned is None:
                self.assertGreaterEqual(
                    gap, self.CHIP_HUE_FLOOR,
                    f"{one} and {two} are only dE {gap:.1f} apart; hue-named "
                    f"chips owe each other dE {self.CHIP_HUE_FLOOR}. Add the "
                    "pair to CHIP_EXEMPT_PAIRS only if the collision is a "
                    "deliberate design decision")
            else:
                self.assertGreaterEqual(
                    gap, pinned,
                    f"{one} and {two} are a sanctioned near-pair, but they "
                    f"have closed from dE {pinned} to dE {gap:.1f}")

        unused = set(self.CHIP_EXEMPT_PAIRS) - {frozenset(p[1:]) for p in pairs}
        self.assertFalse(
            unused,
            f"CHIP_EXEMPT_PAIRS still excuses pairs that left the map: "
            f"{sorted(sorted(pair) for pair in unused)}")

        closest = min(pairs)
        self.assertEqual(
            frozenset(closest[1:]), self.CHIP_TWINS,
            f"{closest[1]}/{closest[2]} (dE {closest[0]:.1f}) are now the "
            "closest chips in the map, closer than the goldens meant to be "
            "the only near-twins")

    def test_all_named_jelly_motions_have_bounded_fast_springs(self):
        self.assertEqual(
            set(MOTION_SPECS), {"press", "release", "wobble", "pop"})
        for motion in MOTION_SPECS.values():
            self.assertGreater(motion.stiffness, 0.0)
            self.assertGreater(motion.damping, 0.0)
            self.assertLessEqual(motion.duration, 0.5)
            self.assertGreater(motion.squash_x, 0.0)
            self.assertGreater(motion.squash_y, 0.0)

    def test_surface_tokens_keep_work_quiet_and_playful_objects_offset(self):
        self.assertEqual(
            set(SURFACE_SPECS), {"work", "card", "playful", "control"})
        self.assertEqual(SURFACE_SPECS["work"].shadow_x, 0.0)
        self.assertEqual(SURFACE_SPECS["work"].shadow_y, 0.0)
        self.assertGreater(SURFACE_SPECS["playful"].shadow_x, 0.0)
        self.assertLess(SURFACE_SPECS["playful"].shadow_y, 0.0)
        self.assertGreater(
            SURFACE_SPECS["playful"].border_width,
            SURFACE_SPECS["work"].border_width,
        )

    def test_hud_type_tokens_use_compact_rounded_chrome_sizes(self):
        self.assertEqual(
            set(TYPE_SPECS),
            {"hud_eyebrow", "hud_confidence", "hud_caption"},
        )
        self.assertLess(TYPE_SPECS["hud_eyebrow"].size, 10.0)
        self.assertLessEqual(TYPE_SPECS["hud_caption"].size, 12.0)

    def test_hud_copy_distinguishes_stable_listening_and_processing(self):
        listening = hud_presentation(
            "recording", "hello world", 0.91, stable_prefix=True)
        processing = hud_presentation(
            "processing", "hello world", 0.61, stable_prefix=True)
        error = hud_presentation(
            "error", "I couldn't understand that — try again", 0.61,
            stable_prefix=True)

        self.assertEqual(listening.eyebrow, "HEARD YOU")
        self.assertEqual(listening.confidence, "Recognition 91%")
        self.assertIn("hello world", listening.accessibility_value)
        self.assertEqual(processing.eyebrow, "TIDYING UP")
        self.assertEqual(processing.accent, "accent")
        self.assertEqual(error.eyebrow, "TRY AGAIN")
        self.assertEqual(error.confidence, "")
        self.assertEqual(error.accent, "error")
        self.assertIn(
            "Dictation needs another try", error.accessibility_value)

    def test_reduce_motion_disables_whole_head_squash(self):
        self.assertEqual(
            jelly_face_scale(0.9, reduce_motion=True), (1.0, 1.0))
        self.assertEqual(
            jelly_face_scale(0.9, processing=True), (1.0, 1.0))
        active = jelly_face_scale(0.9)
        self.assertGreater(active[0], 1.0)
        self.assertLess(active[1], 1.0)

    def test_every_text_ink_meets_aa_on_its_surfaces(self):
        from whisper_face_theme import (
            AMBER_TEXT_ON_LIGHT,
            BRAND_TEXT_ON_LIGHT,
            contrast_ratio,
        )

        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            for surface in (palette.bg, palette.surface):
                for ink in (palette.ink, palette.ink_soft):
                    self.assertGreaterEqual(
                        contrast_ratio(ink, surface), 4.5,
                        f"{ink} on {surface}")
        # The darkened accent inks carry colored text on light surfaces.
        for ink in (BRAND_TEXT_ON_LIGHT, AMBER_TEXT_ON_LIGHT):
            for surface in (LIGHT_PALETTE.bg, LIGHT_PALETTE.surface):
                self.assertGreaterEqual(
                    contrast_ratio(ink, surface), 4.5, f"{ink} on {surface}")


class IdleLifeDriverTests(unittest.TestCase):
    """The shared face schedules must stay deterministic and gated."""

    @staticmethod
    def _run(frames, **calls):
        from whisper_face_render import IdleLifeDriver

        driver = IdleLifeDriver()
        trace = []
        for _ in range(frames):
            driver.advance()
            trace.append(tuple(
                getattr(driver, name)(*args) for name, args in calls.items()))
        return trace

    def test_schedules_are_deterministic(self):
        first = self._run(400, blink=(True,), mouth=(0.6, True),
                          breath=(True,), gaze=(True,))
        second = self._run(400, blink=(True,), mouth=(0.6, True),
                           breath=(True,), gaze=(True,))
        self.assertEqual(first, second)

    def test_inactive_schedules_rest(self):
        for blink, mouth, breath, gaze in self._run(
                240, blink=(False,), mouth=(0.9, False),
                breath=(False,), gaze=(False,)):
            self.assertEqual(blink, 0.0)
            self.assertEqual(breath, 0.0)
            self.assertLess(mouth, 0.05)
            self.assertLess(abs(gaze[0]) + abs(gaze[1]), 0.05)

    def test_blinks_recur_and_first_lands_early(self):
        blinks = [frame for frame, (value,) in enumerate(
            self._run(600, blink=(True,))) if value >= 1.0]
        self.assertGreaterEqual(len(blinks), 2)
        self.assertLess(blinks[0], 120)

    def test_speaking_opens_the_mouth_and_silence_closes_it(self):
        from whisper_face_render import IdleLifeDriver

        driver = IdleLifeDriver()
        for _ in range(30):
            driver.advance()
            speaking = driver.mouth(0.8, True)
        self.assertGreater(speaking, 0.3)
        for _ in range(60):
            driver.advance()
            resting = driver.mouth(0.0, False)
        self.assertLess(resting, 0.02)

    def test_gaze_wanders_within_the_clamp(self):
        drifted = False
        for (gaze,) in self._run(900, gaze=(True,)):
            self.assertLessEqual(abs(gaze[0]), 3.0)
            self.assertLessEqual(abs(gaze[1]), 3.0)
            if abs(gaze[0]) > 0.5 or abs(gaze[1]) > 0.5:
                drifted = True
        self.assertTrue(drifted)

    def test_micro_wobble_is_rare_but_present(self):
        wobbles = sum(
            1 for (value,) in self._run(3600, should_wobble=(True,)) if value)
        self.assertGreaterEqual(wobbles, 2)
        self.assertLessEqual(wobbles, 12)

    def test_moods_map_processing_and_error(self):
        from whisper_face_render import mood_overrides

        self.assertGreaterEqual(mood_overrides("processing")["blink"], 0.75)
        self.assertGreater(mood_overrides("error")["gaze"][1], 0.0)
        self.assertEqual(mood_overrides("idle"), {})
        self.assertEqual(mood_overrides("recording"), {})


class AcousticKeywordMemoryRuntimeTests(unittest.TestCase):
    @staticmethod
    def runtime_namespace(path: Path):
        namespace = load_definitions(
            "atomic_write_text",
            "_load_acoustic_keyword_memory",
            "acoustic_keyword_memory_status_snapshot",
            "measured_keyword_hints",
            "export_acoustic_keyword_memory",
            "remember_explicit_acoustic_keyword_correction",
            "forget_acoustic_keyword",
            "forget_all_acoustic_keywords",
            assignments={"ACOUSTIC_KEYWORD_MEMORY_LOCK"},
            extra={
                "AcousticKeywordMemory": AcousticKeywordMemory,
                "AcousticKeywordActivationError":
                    AcousticKeywordActivationError,
                "active_acoustic_keywords": active_acoustic_keywords,
                "clear_acoustic_keyword_activations":
                    clear_acoustic_keyword_activations,
                "remove_acoustic_keyword_activation":
                    remove_acoustic_keyword_activation,
                "refresh_glossary": lambda: None,
                "MEASUREMENT_MODE": MeasurementMode.inert(),
                "os": os,
                "tempfile": tempfile,
                "Path": Path,
            },
        )
        namespace["ACOUSTIC_KEYWORD_MEMORY_FILE"] = path
        namespace["ACOUSTIC_KEYWORD_ACTIVATION_FILE"] = (
            path.with_name("acoustic_keyword_activation.json"))
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
    @staticmethod
    def namespace():
        return load_definitions(
            "AudioSlot", "AudioPool",
            assignments={"SAMPLE_RATE"},
            extra={"sd": SimpleNamespace(InputStream=FakeStream)},
        )

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

        with self.assertRaisesRegex(
                RuntimeError, "microphone stream unavailable") as raised:
            pool.warm()

        self.assertNotIn("private device detail", str(raised.exception))
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

        with self.assertRaisesRegex(
                RuntimeError, "microphone stream unavailable"):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))

        self.assertEqual(pool.readiness(), "Unavailable")

    def test_idle_device_switch_closes_stale_slots_and_lazily_reopens(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        pool = self.namespace()["AudioPool"](
            size=2, stream_factory=factory)
        pool.warm()
        original = tuple(streams)

        pool.invalidate()
        pool.invalidate()

        self.assertEqual(pool.readiness(), "Starting")
        self.assertEqual(pool.slots, [])
        self.assertTrue(all(stream.closed for stream in original))
        self.assertEqual(len(streams), 2)

        slot = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(len(streams), 4)
        self.assertNotIn(slot.stream, original)
        pool.release(slot)

    def test_idle_recovery_prewarms_before_the_next_keypress(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        pool = self.namespace()["AudioPool"](
            size=2, stream_factory=factory)
        pool.warm()
        original = tuple(streams)

        self.assertTrue(pool.recover_default_device())

        self.assertEqual(pool.readiness(), "Ready")
        self.assertEqual(len(pool.slots), 2)
        self.assertEqual(len(streams), 4)
        self.assertTrue(all(stream.closed for stream in original))

    def test_close_prevents_a_background_reopen(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        pool = self.namespace()["AudioPool"](
            size=1, stream_factory=factory)
        pool.warm()
        pool.close()

        self.assertFalse(pool.warm_async())
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].closed)
        with self.assertRaisesRegex(RuntimeError, "stream unavailable"):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))

    def test_active_capture_finishes_before_stale_pool_is_replaced(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        pool = self.namespace()["AudioPool"](
            size=2, stream_factory=factory)
        received = []
        recorder = SimpleNamespace(
            _callback=lambda indata, *_args: received.append(indata.copy()))
        slot = pool.acquire(recorder)
        original = tuple(streams)

        pool.invalidate()
        slot.stream.callback(
            np.ones((4, 1), dtype=np.float32), 4, None, None)

        self.assertEqual(len(received), 1)
        self.assertTrue(pool.recovery_pending)
        self.assertFalse(any(stream.closed for stream in original))
        with self.assertRaisesRegex(RuntimeError, "recovery pending"):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(len(streams), 2)

        pool.release(slot)
        self.assertTrue(pool.wait_for_recovery())
        self.assertTrue(all(stream.closed for stream in original))
        self.assertEqual(len(pool.slots), 2)
        replacement = pool.acquire(
            SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(len(streams), 4)
        pool.release(replacement)

    def test_reopen_failure_stays_closed_and_next_keypress_can_retry(self):
        streams = []
        fail = {"on": False}

        def factory(**kwargs):
            if fail["on"]:
                raise RuntimeError("private device detail")
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        pool = self.namespace()["AudioPool"](
            size=1, stream_factory=factory)
        pool.warm()
        fail["on"] = True

        self.assertFalse(pool.recover_default_device())

        with self.assertRaises(RuntimeError):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))

        self.assertEqual(pool.slots, [])
        self.assertEqual(pool.readiness(), "Unavailable")
        fail["on"] = False
        slot = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(pool.readiness(), "Ready")
        self.assertEqual(len(streams), 2)
        pool.release(slot)

    def recorder_namespace(self, factory, clock):
        ns = load_definitions(
            "AudioSlot", "AudioPool", "CapturedAudio", "Recorder",
            "stalled_take_action",
            assignments={"SAMPLE_RATE", "CAPTURE_BLOCK_SECONDS",
                         "NO_AUDIO_SECONDS", "CHUNK_MIN_SECONDS",
                         "CHUNK_CUT_SILENCE", "SPECULATIVE_MIN_SECONDS",
                         "SPECULATIVE_SILENCE"},
            extra={
                "np": np,
                "LEVELS": [],
                "calibrated_vad_threshold": lambda: 0.008,
                "should_start_speculation": should_start_speculation,
                "can_reuse_speculation": can_reuse_speculation,
                "time": SimpleNamespace(
                    perf_counter=lambda: clock["now"],
                    time_ns=time.time_ns),
                "ContextPack": SimpleNamespace,
                "FLIGHT": SimpleNamespace(attach=lambda _recorder: False),
                "print": lambda *_args: None,
            },
        )
        ns["AUDIO_POOL"] = ns["AudioPool"](size=1, stream_factory=factory)
        ns["AUDIO_POOL"].warm()
        return ns

    def test_a_stream_that_delivers_nothing_is_detected_and_replaced(self):
        streams = []
        clock = {"now": 100.0}

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        ns = self.recorder_namespace(factory, clock)
        pool = ns["AUDIO_POOL"]
        recorder = ns["Recorder"]()
        recorder.start(100.0)
        dead = streams[0]

        clock["now"] = 100.0 + ns["NO_AUDIO_SECONDS"] - 0.01
        self.assertIsNone(ns["stalled_take_action"](recorder, clock["now"]))
        clock["now"] = 100.0 + ns["NO_AUDIO_SECONDS"]
        self.assertEqual(
            ns["stalled_take_action"](recorder, clock["now"]), "abandon")

        self.assertEqual(len(recorder.stop()), 0)

        # The silent stream is never handed to the next take.
        self.assertTrue(dead.closed)
        self.assertTrue(pool.wait_for_recovery())
        replacement = ns["Recorder"]()
        replacement.start(clock["now"])
        self.assertIsNot(replacement.slot.stream, dead)
        replacement.stop()

    def test_a_stream_that_stops_mid_take_keeps_its_audio_and_is_replaced(self):
        # The microphone is unplugged, or its Bluetooth link drops, after
        # the speaker has started: callbacks simply stop.
        streams = []
        clock = {"now": 100.0}

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        ns = self.recorder_namespace(factory, clock)
        pool = ns["AUDIO_POOL"]
        recorder = ns["Recorder"]()
        recorder.start(100.0)
        dead = streams[0]
        for _ in range(50):
            clock["now"] += 0.01
            dead.callback(
                np.full((160, 1), 0.02, dtype=np.float32), 160, None, None)
        stalled_at = clock["now"]

        clock["now"] = stalled_at + ns["NO_AUDIO_SECONDS"] - 0.01
        self.assertIsNone(ns["stalled_take_action"](recorder, clock["now"]))
        clock["now"] = stalled_at + ns["NO_AUDIO_SECONDS"]
        # Speech was heard, so the take finishes rather than being dropped.
        self.assertEqual(
            ns["stalled_take_action"](recorder, clock["now"]), "finish")

        self.assertEqual(len(recorder.stop()), 8_000)
        self.assertTrue(dead.closed)
        self.assertTrue(pool.wait_for_recovery())
        replacement = ns["Recorder"]()
        replacement.start(clock["now"])
        self.assertIsNot(replacement.slot.stream, dead)
        replacement.stop()

    def test_a_stream_that_delivers_audio_is_kept(self):
        streams = []
        clock = {"now": 100.0}

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        ns = self.recorder_namespace(factory, clock)
        pool = ns["AUDIO_POOL"]
        recorder = ns["Recorder"]()
        recorder.start(100.0)
        # Ten seconds of 0.1 s blocks, as a live stream delivers them.
        for _ in range(100):
            clock["now"] += 0.1
            streams[0].callback(
                np.full((1600, 1), 0.01, dtype=np.float32), 1600, None, None)
            self.assertIsNone(
                ns["stalled_take_action"](recorder, clock["now"]))

        self.assertEqual(len(recorder.stop()), 160_000)
        self.assertEqual(pool.readiness(), "Ready")
        self.assertFalse(streams[0].closed)
        # Too short to judge: a quick tap that heard nothing is not a fault.
        tap = ns["Recorder"]()
        tap.start(110.0)
        clock["now"] = 110.3
        tap.stop()
        self.assertEqual(pool.readiness(), "Ready")
        self.assertEqual(len(streams), 1)

    def test_stop_failure_never_reuses_slot_and_preserves_other_active_take(self):
        class FailingStopStream(FakeStream):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.fail_stop = False

            def stop(self):
                super().stop()
                if self.fail_stop:
                    raise RuntimeError("private device detail")

        streams = []

        def factory(**kwargs):
            stream = FailingStopStream(**kwargs)
            streams.append(stream)
            return stream

        pool = self.namespace()["AudioPool"](
            size=2, stream_factory=factory)
        first = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        second = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        original = tuple(streams)
        first.stream.fail_stop = True

        with self.assertRaisesRegex(
                RuntimeError, "microphone stream stop failed") as raised:
            pool.release(first)

        self.assertNotIn("private device detail", str(raised.exception))
        self.assertTrue(pool.recovery_pending)
        self.assertFalse(any(stream.closed for stream in original))
        with self.assertRaisesRegex(RuntimeError, "recovery pending"):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))

        pool.release(second)
        self.assertTrue(pool.wait_for_recovery())
        self.assertTrue(all(stream.closed for stream in original))
        replacement = pool.acquire(
            SimpleNamespace(_callback=lambda *_args: None))
        self.assertNotIn(replacement.stream, original)
        pool.release(replacement)


class MacAudioRecoveryNotificationTests(unittest.TestCase):
    class NativeCall:
        def __init__(self, status=0):
            self.status = status
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            return self.status

    def test_failed_native_removal_retains_callback_until_retry_succeeds(self):
        library = SimpleNamespace(
            AudioObjectAddPropertyListener=self.NativeCall(),
            AudioObjectRemovePropertyListener=self.NativeCall(status=1),
        )
        ns = load_definitions(
            "_AudioObjectPropertyAddress", "_CoreAudioDefaultInputListener",
            extra={"ctypes": ctypes},
        )
        listener = ns["_CoreAudioDefaultInputListener"](library=library)
        listener.start(lambda: None)
        callback = listener.callback

        with self.assertRaisesRegex(
                RuntimeError, "CoreAudio notification removal failed"):
            listener.close()

        self.assertTrue(listener.started)
        self.assertIs(listener.callback, callback)
        library.AudioObjectRemovePropertyListener.status = 0
        listener.close()
        self.assertFalse(listener.started)
        self.assertIsNone(listener.callback)

    class CoreAudio:
        def __init__(self):
            self.callback = None
            self.closed = 0

        def start(self, callback):
            self.callback = callback

        def emit_default_input_change(self):
            self.callback()

        def close(self):
            self.closed += 1

    class WorkspaceCenter:
        def __init__(self):
            self.block = None
            self.removed = []

        def addObserverForName_object_queue_usingBlock_(
                self, _name, _object, _queue, block):
            self.block = block
            return "wake-token"

        def emit_wake(self):
            self.block(None)

        def removeObserver_(self, token):
            self.removed.append(token)

    @staticmethod
    def device_namespace(portaudio, requests=None, *, flight=True):
        """The production pool, Flight Recorder and recovery entry point."""
        ns = load_definitions(
            "AudioSlot", "AudioPool", "FlightRecorder",
            "_invalidate_default_audio_inputs",
            "refresh_default_audio_inputs", "_reinitialize_portaudio",
            assignments={"SAMPLE_RATE", "FLIGHT_BUFFER_SECONDS"},
            extra={
                "sd": portaudio,
                "deque": __import__("collections").deque,
                "np": np,
                "time": time,
            },
        )
        hook = None if requests is None else (
            lambda: requests.append("refresh") or True)
        ns["AUDIO_POOL"] = ns["AudioPool"](size=2, request_refresh=hook)
        # restore_allowed stands in for the stored Flight Recorder opt-in.
        ns["FLIGHT"] = ns["FlightRecorder"](
            restore_allowed=lambda: flight, request_refresh=hook)
        return ns

    def test_a_default_input_change_rebinds_every_stream(self):
        portaudio = FakePortAudio("built-in")
        ns = self.device_namespace(portaudio)
        ns["AUDIO_POOL"].warm()
        ns["FLIGHT"].enable()
        self.assertEqual(portaudio.open_devices(), ["built-in"] * 3)

        portaudio.system_default = "usb"
        ns["_invalidate_default_audio_inputs"]()

        self.assertEqual(portaudio.open_devices(), ["usb"] * 3)
        self.assertEqual(ns["AUDIO_POOL"].readiness(), "Ready")
        self.assertTrue(ns["FLIGHT"].is_enabled())
        self.assertEqual(portaudio.reinitializations, 1)

    def test_a_removed_launch_device_does_not_leave_dictation_dead(self):
        # Launched on AirPods; they go back in the case and the built-in
        # microphone becomes the default. Opening the vanished device fails.
        portaudio = FakePortAudio("airpods")
        opened = portaudio.InputStream

        def open_existing(**kwargs):
            if portaudio.cached_default == "airpods" and (
                    portaudio.system_default != "airpods"):
                raise RuntimeError("private device detail")
            return opened(**kwargs)

        portaudio.InputStream = open_existing
        ns = self.device_namespace(portaudio, flight=False)
        ns["AUDIO_POOL"].warm()

        portaudio.system_default = "built-in"
        ns["_invalidate_default_audio_inputs"]()

        self.assertEqual(ns["AUDIO_POOL"].readiness(), "Ready")
        slot = ns["AUDIO_POOL"].acquire(
            SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(slot.stream.device, "built-in")
        ns["AUDIO_POOL"].release(slot)

    def test_a_live_pool_take_defers_the_refresh_to_its_release(self):
        portaudio = FakePortAudio("built-in")
        requests = []
        ns = self.device_namespace(portaudio, requests, flight=False)
        pool = ns["AUDIO_POOL"]
        slot = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))

        portaudio.system_default = "usb"
        ns["_invalidate_default_audio_inputs"]()

        # The take keeps recording on its stream; nothing was terminated.
        self.assertFalse(slot.stream.closed)
        self.assertEqual(portaudio.reinitializations, 0)
        self.assertTrue(pool.recovery_pending)

        pool.release(slot)
        self.assertEqual(requests, ["refresh"])
        self.assertEqual(portaudio.open_devices(), [])
        ns["_invalidate_default_audio_inputs"]()

        self.assertEqual(portaudio.open_devices(), ["usb", "usb"])
        self.assertEqual(portaudio.reinitializations, 1)

    def test_a_refresh_never_terminates_while_a_release_is_closing(self):
        # PortAudio is not thread-safe and Pa_Terminate closes every open
        # stream itself, so a terminate that overlaps a close on another
        # thread disposes the same stream twice.
        closing = threading.Event()
        finish_close = threading.Event()
        overlaps = []

        class SlowClosePortAudio(FakePortAudio):
            def InputStream(self, **kwargs):
                stream = super().InputStream(**kwargs)
                close = stream.close

                def slow_close():
                    closing.set()
                    finish_close.wait(2.0)
                    close()
                stream.close = slow_close
                return stream

            def _terminate(self):
                if closing.is_set() and not finish_close.is_set():
                    overlaps.append("terminate during close")
                super()._terminate()

        portaudio = SlowClosePortAudio("built-in")
        requests = []
        ns = self.device_namespace(portaudio, requests, flight=False)
        pool = ns["AUDIO_POOL"]
        slot = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        portaudio.system_default = "usb"
        ns["_invalidate_default_audio_inputs"]()      # deferred: take is live

        releaser = threading.Thread(target=pool.release, args=(slot,))
        releaser.start()
        self.assertTrue(closing.wait(2.0))
        # A second default-input notification, or a wake, lands mid-close.
        refresher = threading.Thread(
            target=ns["_invalidate_default_audio_inputs"])
        refresher.start()
        refresher.join(0.2)
        finish_close.set()
        releaser.join(2.0)
        refresher.join(2.0)

        self.assertEqual(overlaps, [])
        self.assertEqual(portaudio.open_devices(), ["usb", "usb"])
        self.assertEqual(portaudio.reinitializations, 1)

    def test_a_failed_reinitialization_is_retried_after_a_keypress(self):
        class FlakyPortAudio(FakePortAudio):
            initialized = True
            failures = 1

            def InputStream(self, **kwargs):
                if not self.initialized:
                    raise RuntimeError("PortAudio not initialized")
                return super().InputStream(**kwargs)

            def _terminate(self):
                super()._terminate()
                self.initialized = False

            def _initialize(self):
                if self.failures:
                    self.failures -= 1
                    raise RuntimeError("Error initializing PortAudio")
                super()._initialize()
                self.initialized = True

        portaudio = FlakyPortAudio("built-in")
        requests = []
        ns = self.device_namespace(portaudio, requests, flight=False)
        pool = ns["AUDIO_POOL"]
        pool.warm()
        portaudio.system_default = "usb"
        ns["_invalidate_default_audio_inputs"]()
        self.assertEqual(pool.readiness(), "Unavailable")

        # The keypress cannot open anything, so it asks the recovery worker
        # to initialize PortAudio again.
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(requests, ["refresh"])
        ns["_invalidate_default_audio_inputs"]()

        self.assertEqual(pool.readiness(), "Ready")
        slot = pool.acquire(SimpleNamespace(_callback=lambda *_args: None))
        self.assertEqual(slot.stream.device, "usb")
        pool.release(slot)

    def test_a_live_flight_take_defers_the_refresh_to_its_detach(self):
        portaudio = FakePortAudio("built-in")
        requests = []
        ns = self.device_namespace(portaudio, requests)
        flight = ns["FLIGHT"]
        flight.enable()
        recorder = SimpleNamespace(_callback=lambda *_args: None)
        self.assertTrue(flight.attach(recorder))

        portaudio.system_default = "usb"
        ns["_invalidate_default_audio_inputs"]()
        self.assertEqual(portaudio.reinitializations, 0)
        # The idle pool reopens at once, still on the launch-time table.
        self.assertEqual(portaudio.open_devices(), ["built-in"] * 3)

        flight.detach(recorder)
        # Detach hands the refresh to the recovery worker instead of
        # reopening on PortAudio's stale table.
        self.assertEqual(requests, ["refresh"])
        ns["_invalidate_default_audio_inputs"]()

        self.assertEqual(portaudio.open_devices(), ["usb"] * 3)
        self.assertEqual(portaudio.reinitializations, 1)
        self.assertTrue(flight.is_enabled())

    def test_refresh_requests_reach_only_a_running_recovery_worker(self):
        ns = load_definitions(
            "MacAudioRecoveryNotifications",
            extra={"IS_MACOS": True, "threading": threading, "time": time},
        )
        refreshes = []
        recovery = ns["MacAudioRecoveryNotifications"](
            lambda: refreshes.append(True),
            core_audio=self.CoreAudio(),
            workspace_center=self.WorkspaceCenter(),
            wake_name="wake",
        )
        # Not started: the caller reopens locally rather than waiting on a
        # worker that does not exist.
        self.assertFalse(recovery.request())

        recovery.start()
        self.assertTrue(recovery.request())
        self.assertTrue(recovery.wait_for_idle())
        self.assertEqual(refreshes, [True])
        recovery.close()
        self.assertFalse(recovery.request())

    def test_default_device_and_wake_events_are_content_free_and_coalesced(self):
        core = self.CoreAudio()
        center = self.WorkspaceCenter()
        invalidations = []
        ns = load_definitions(
            "MacAudioRecoveryNotifications",
            extra={
                "IS_MACOS": True,
                "NSWorkspace": SimpleNamespace(),
                "NSWorkspaceDidWakeNotification": "wake",
                "_CoreAudioDefaultInputListener": SimpleNamespace,
                "threading": threading,
                "time": time,
            },
        )
        recovery = ns["MacAudioRecoveryNotifications"](
            lambda: invalidations.append("invalidated"),
            core_audio=core,
            workspace_center=center,
            wake_name="wake",
        )
        recovery.start()

        core.emit_default_input_change()
        self.assertTrue(recovery.wait_for_idle())
        center.emit_wake()
        self.assertTrue(recovery.wait_for_idle())

        self.assertEqual(invalidations, ["invalidated", "invalidated"])
        recovery.close()
        core.emit_default_input_change()
        self.assertEqual(invalidations, ["invalidated", "invalidated"])
        self.assertEqual(core.closed, 1)
        self.assertEqual(center.removed, ["wake-token"])


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
                "QWEN_CLEANUP_PROFILE": QWEN_CLEANUP_PROFILE,
                "mark_model_warm_path_observed": lambda _provider: True,
                "refresh_model_readiness_evidence": lambda: True,
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


class AcousticCalibrationRuntimeTests(unittest.TestCase):
    @staticmethod
    def namespace(loader):
        return load_definitions(
            "refresh_acoustic_calibration",
            "active_calibration_settings",
            "acoustic_calibration_source",
            "acoustic_calibration_status_snapshot",
            "calibrated_vad_threshold",
            "calibrated_end_silence_seconds",
            "prepare_asr_audio",
            assignments={"SILENCE_RMS", "TAIL_SKIP_SILENCE"},
            extra={
                "np": np,
                "MEASUREMENT_MODE": MeasurementMode.inert(),
                "CalibrationSettings": CalibrationSettings,
                "load_acoustic_calibration_activation": loader,
                "ACOUSTIC_CALIBRATION_ACTIVATION_FILE":
                    Path("private-receipt.json"),
                "ACOUSTIC_CALIBRATION_STATE": {
                    "settings": None, "status": "not-loaded"},
            },
        )

    def test_missing_receipt_preserves_existing_audio_defaults(self):
        ns = self.namespace(lambda _path:
            AcousticCalibrationActivationStatus(
                None, "receipt-missing"))
        self.assertFalse(ns["refresh_acoustic_calibration"]())
        audio = np.array([0.001, 0.1], dtype=np.float32)

        prepared = ns["prepare_asr_audio"](audio)

        np.testing.assert_allclose(prepared, audio * 2.5)
        self.assertEqual(ns["calibrated_vad_threshold"](), 0.008)
        self.assertEqual(
            ns["calibrated_end_silence_seconds"](), 0.12)
        self.assertEqual(
            ns["acoustic_calibration_status_snapshot"]()["controls"], ())

    def test_approved_receipt_applies_bounded_front_end_controls(self):
        settings = CalibrationSettings(1.5, 0.01, 0.02, 300)
        ns = self.namespace(lambda _path:
            AcousticCalibrationActivationStatus(settings, "ready"))
        self.assertTrue(ns["refresh_acoustic_calibration"]())
        audio = np.array([0.005, 0.1], dtype=np.float32)

        prepared = ns["prepare_asr_audio"](audio)
        status = ns["acoustic_calibration_status_snapshot"]()

        np.testing.assert_allclose(prepared, [0.0, 0.15])
        self.assertEqual(ns["calibrated_vad_threshold"](), 0.02)
        self.assertEqual(
            ns["calibrated_end_silence_seconds"](), 0.3)
        self.assertTrue(status["enabled"])
        self.assertEqual(
            status["controls"], ("gain", "noise", "vad", "end-silence"))
        self.assertNotIn("0.01", json.dumps(status))


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

    @staticmethod
    def recorder_namespace():
        return load_definitions(
            "FlightRecorder",
            assignments={"SAMPLE_RATE", "FLIGHT_BUFFER_SECONDS"},
            extra={
                "deque": __import__("collections").deque,
                "np": np,
                "sd": SimpleNamespace(InputStream=FakeStream),
                "threading": threading,
                "time": time,
            },
        )

    def test_idle_default_input_change_replaces_the_continuous_stream(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        flight = self.recorder_namespace()["FlightRecorder"](
            stream_factory=factory)
        flight.enable()
        original = streams[0]

        flight.invalidate()

        self.assertTrue(original.closed)
        self.assertEqual(original.stops, 1)
        self.assertEqual(len(streams), 2)
        self.assertIs(flight.stream, streams[1])
        self.assertEqual(streams[1].starts, 1)

    def test_active_take_defers_recovery_and_coalesces_repeated_events(self):
        streams = []

        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        flight = self.recorder_namespace()["FlightRecorder"](
            stream_factory=factory)
        flight.enable()
        received = []
        recorder = SimpleNamespace(
            _callback=lambda indata, *_args: received.append(indata.copy()))
        self.assertTrue(flight.attach(recorder))
        original = streams[0]

        flight.invalidate()
        flight.invalidate()
        original.callback(
            np.ones((4, 1), dtype=np.float32), 4, None, None)

        self.assertTrue(flight.recovery_pending)
        self.assertFalse(original.closed)
        self.assertEqual(len(streams), 1)
        self.assertEqual(len(received), 1)

        flight.detach(recorder)

        self.assertFalse(flight.recovery_pending)
        self.assertTrue(original.closed)
        self.assertEqual(len(streams), 2)
        self.assertIs(flight.stream, streams[1])

    def test_recovery_failure_retries_and_feature_off_stays_closed(self):
        streams = []
        state = {"allowed": True, "fail": False}

        def factory(**kwargs):
            if state["fail"]:
                raise RuntimeError("private device detail")
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        flight = self.recorder_namespace()["FlightRecorder"](
            stream_factory=factory,
            restore_allowed=lambda: state["allowed"],
        )
        flight.enable()
        original = streams[0]
        state["fail"] = True

        flight.invalidate()

        self.assertTrue(original.closed)
        self.assertIsNone(flight.stream)
        state["fail"] = False
        flight.invalidate()
        self.assertEqual(len(streams), 2)
        self.assertIs(flight.stream, streams[1])

        state["allowed"] = False
        flight.invalidate()

        self.assertTrue(streams[1].closed)
        self.assertIsNone(flight.stream)
        self.assertEqual(len(streams), 2)

    def test_attach_waits_for_reopen_and_falls_back_when_start_fails(self):
        reopen_started = threading.Event()
        allow_failure = threading.Event()
        streams = []

        class ReopenStream(FakeStream):
            def start(self):
                super().start()
                if len(streams) > 1:
                    reopen_started.set()
                    allow_failure.wait(1.0)
                    raise RuntimeError("private device detail")

        def factory(**kwargs):
            stream = ReopenStream(**kwargs)
            streams.append(stream)
            return stream

        flight = self.recorder_namespace()["FlightRecorder"](
            stream_factory=factory)
        flight.enable()
        recovery = threading.Thread(target=flight.invalidate)
        recovery.start()
        self.assertTrue(reopen_started.wait(1.0))

        result = []
        attach_done = threading.Event()

        def attach():
            result.append(flight.attach(SimpleNamespace()))
            attach_done.set()

        attempt = threading.Thread(target=attach)
        attempt.start()
        self.assertFalse(attach_done.wait(0.05))
        allow_failure.set()
        recovery.join(1.0)
        attempt.join(1.0)

        self.assertFalse(recovery.is_alive())
        self.assertFalse(attempt.is_alive())
        self.assertEqual(result, [False])
        self.assertIsNone(flight.stream)

    def test_pending_recovery_cannot_clear_retrospective_tap_snapshot(self):
        buffered = np.ones(int(0.8 * 16_000), dtype=np.float32)
        calls = []

        class PendingRecoveryFlight:
            def __init__(self):
                self.frames = buffered

            def extract_before(self, before_at):
                calls.append(("extract", before_at))
                return self.frames.copy()

            def clear(self):
                calls.append(("clear",))
                self.frames = np.zeros(0, dtype=np.float32)

        flight = PendingRecoveryFlight()

        class Recorder:
            press_at = 42.0
            source = "hold"

            def stop(self):
                calls.append(("stop",))
                # Detach would run pending recovery and clear the real Flight
                # Recorder's frames at this point.
                flight.clear()

        ns = load_definitions(
            "_capture_retrospective_flight_tap",
            extra={"FLIGHT": flight, "np": np},
        )
        recorder = Recorder()

        result = ns["_capture_retrospective_flight_tap"](recorder)

        self.assertEqual(calls[0], ("extract", 42.0))
        self.assertEqual(calls[1:], [("stop",), ("clear",), ("clear",)])
        self.assertEqual(recorder.source, "flight")
        np.testing.assert_array_equal(result, buffered)


class CleanupGuardTests(unittest.TestCase):
    HALLUCINATION_GUARD = {
        "HALLUCINATIONS", "WHISPER_ENGINES", "LOW_CONFIDENCE",
        "HALLUCINATION_SECONDS_PER_WORD",
    }

    def test_hallucinations_ignore_terminal_punctuation(self):
        ns = load_definitions(
            "is_hallucination", "weak_whisper_evidence",
            assignments=self.HALLUCINATION_GUARD)
        guard = ns["is_hallucination"]
        weak = Recognition(
            "", confidence=0.31, engine="tiny", audio_duration=1.4)
        for text in ("Thank you!", "THANK YOU...", "Bye?", "you"):
            with self.subTest(text=text):
                self.assertTrue(guard(text, "en", weak))
        # Output with no words in it is nothing, whoever produced it.
        for text in ("!", ".", "  ...  "):
            with self.subTest(text=text):
                self.assertTrue(guard(text))
                self.assertTrue(guard(text, "en", Recognition(
                    text, confidence=0.93, engine="parakeet-unified")))
        self.assertFalse(guard("This is real speech!", "en", weak))

    def test_silent_audio_phrases_need_a_weak_whisper_decode(self):
        ns = load_definitions(
            "is_hallucination", "weak_whisper_evidence",
            assignments=self.HALLUCINATION_GUARD)
        guard = ns["is_hallucination"]
        kept = (
            # Parakeet never produces Whisper's silent-audio artifacts, at
            # any confidence its cross-check can assign.
            Recognition("Thank you.", confidence=0.93,
                        engine="parakeet-unified", audio_duration=1.2),
            Recognition("Thank you.", confidence=0.45,
                        engine="parakeet-unified", audio_duration=9.0),
            Recognition("Bye!", confidence=0.69,
                        engine="parakeet-unified", audio_duration=0.8),
            # A confident Whisper decode of a clip the phrase fills.
            Recognition("Thank you.", confidence=0.84, engine="tiny",
                        audio_duration=1.3),
            Recognition("Bye.", confidence=0.77, engine="turbo",
                        audio_duration=0.9),
            # No engine named: nothing says this was Whisper.
            Recognition("Thank you.", confidence=0.2, audio_duration=8.0),
        )
        for recognition in kept:
            with self.subTest(recognition=recognition):
                self.assertFalse(guard(recognition.text, "en", recognition))
        # With no evidence at all the phrase is the speaker's.
        self.assertFalse(guard("Thank you."))
        dropped = (
            # Low confidence.
            Recognition("Thank you.", confidence=0.38, engine="tiny",
                        audio_duration=1.3),
            Recognition("You", confidence=0.5, engine="turbo",
                        audio_duration=0.7),
            # Two words decoded out of seven seconds of audio.
            Recognition("Thank you.", confidence=0.84, engine="tiny",
                        audio_duration=7.0),
            Recognition("Thanks for watching!", confidence=0.9,
                        engine="tiny+turbo", audio_duration=12.0),
        )
        for recognition in dropped:
            with self.subTest(recognition=recognition):
                self.assertTrue(guard(recognition.text, "en", recognition))

    def test_repeat_collapse_keeps_digits_number_words_and_letters(self):
        ns = load_definitions(
            "collapse_repeats", extra={"NUMBER_WORDS": NUMBER_WORDS})
        for spoken in (
            "the zip is 1 0 0 0 1",
            "The zip is 1, 0, 0, 0, 1.",
            "my number is five five five one two one two",
            "Dial 555 555 555 and wait.",
            "w w w dot example dot com",
            "The grade was A A A plus.",
        ):
            with self.subTest(spoken=spoken):
                self.assertEqual(
                    ns["collapse_repeats"](spoken), (spoken, False))

    def test_repeat_collapse_still_removes_a_decode_loop(self):
        ns = load_definitions(
            "collapse_repeats", extra={"NUMBER_WORDS": NUMBER_WORDS})
        collapse = ns["collapse_repeats"]
        self.assertEqual(
            collapse("Restart " + "Unraid " * 40 + "tonight."),
            ("Restart Unraid Unraid tonight.", True))
        self.assertEqual(
            collapse("No no, that is wrong."),
            ("No no, that is wrong.", False))

    def test_spoken_tone_override_needs_the_tone_keyword(self):
        ns = load_definitions(
            "extract_tone_override",
            assignments={"TONE_OVERRIDE_RE", "TONE_ALIASES"})
        extract = ns["extract_tone_override"]
        for spoken, expected in (
            ("Formal tone, please confirm the meeting.",
             ("please confirm the meeting.", "formal")),
            ("Casual style: see you at six.", ("see you at six.", "casual")),
            ("Verbatim mode. Keep every word.",
             ("Keep every word.", "verbatim")),
            ("technical tone, restart the worker",
             ("restart the worker", "code")),
            ("Neutral tone, send the summary.",
             ("send the summary.", "default")),
        ):
            with self.subTest(spoken=spoken):
                self.assertEqual(extract(spoken), expected)
        # A sentence that merely opens with one of the tone words keeps it.
        for spoken in (
            "Code, tests, and docs are all updated.",
            "Code: 4471 opens the side gate.",
            "Casual, but not sloppy.",
            "Technical, legal, and financial review is done.",
            "Neutral. That is my position.",
            "Verbatim: keep every word.",
            "Formal education is expensive.",
            "Formal tone.",
        ):
            with self.subTest(spoken=spoken):
                self.assertEqual(extract(spoken), (spoken, None))

    def test_context_echo_is_stripped_only_as_whole_words(self):
        strip = load_definitions("strip_context_echo")["strip_context_echo"]
        # The field's short text is also the start of the next word.
        for field, text in (
            ("to", "Tomorrow works for me"),
            ("ok", "Okay, see you then"),
            ("no", "Nobody told me"),
            ("I", "It works now"),
            ("a", "Another thing"),
            ("I", "I'm on my way"),
            ("re", "re-send the invoice"),
        ):
            with self.subTest(field=field, text=text):
                self.assertEqual(strip(text, field), text)
        # Repeating the field is not an echo when nothing follows it.
        self.assertEqual(strip("Ok.", "ok"), "Ok.")
        # A real echo of the field's tail, followed by the new words.
        self.assertEqual(
            strip("see you at six thirty", "see you at"), "six thirty")
        self.assertEqual(
            strip("See you at, six thirty", "see you at"), ", six thirty")
        long_field = (
            "Thanks for the update on the quarterly migration plan and")
        self.assertEqual(
            long_field[-40:], "date on the quarterly migration plan and")
        self.assertEqual(
            strip("on the quarterly migration plan and the budget",
                  long_field),
            "the budget")
        # The 40-character window opens inside "update". "date" is a word,
        # but the field never said it.
        dated = "date on the quarterly migration plan and the budget"
        self.assertEqual(strip(dated, long_field), dated)
        self.assertEqual(strip("six thirty", "see you at"), "six thirty")
        self.assertEqual(strip("six thirty", ""), "six thirty")

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
        # A filler on its own leaves nothing to paste, not its period.
        self.assertEqual(ns["quick_clean"]("Um."), "")
        self.assertEqual(
            ns["quick_clean"]("I studied at UM for four years"),
            "I studied at UM for four years.")
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
                "LLM_CLEANUP_TOTAL_DEADLINES", "CLEANUP_RESPONSE_SCHEMA",
                "MODE_INSTRUCTIONS", "REFUSAL_RE", "STRUCTURED_OUTPUT",
            },
            extra={
                "CleanupEdit": object,
                "LLM_CLEANUP_BREAKER": CleanupCircuitBreaker(),
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
        # Capture stays on the tight whole-reply ceiling.
        self.assertEqual(seen["total_deadline"], 6.0)

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
                "LLM_CLEANUP_TOTAL_DEADLINES", "CLEANUP_RESPONSE_SCHEMA",
                "LLM_EDIT_KINDS", "MODE_INSTRUCTIONS", "REFUSAL_RE",
                "STRUCTURED_OUTPUT",
            },
            extra={
                "CleanupEdit": lambda kind, before, after: SimpleNamespace(
                    kind=kind, before=before, after=after),
                "LLM_CLEANUP_BREAKER": CleanupCircuitBreaker(),
                "ollama_chat": fake_ollama_chat,
                "quick_clean": lambda text: text,
                "STRUCTURED_FEW_SHOT": [],
            },
        )

        _cleaned, edits = ns["llm_clean_with_edits"](
            "Ready", "Keep the tone neutral.")

        self.assertEqual(edits[0].kind, "semantic_cleanup")
        self.assertNotIn("secret", edits[0].kind)

    def test_llm_cleanup_timeout_opens_circuit_for_consecutive_dictations(self):
        calls = []
        lines = []

        def unavailable(*_args, **_kwargs):
            calls.append(True)
            raise TimeoutError("local cleanup deadline")

        ns = load_definitions(
            "_guard_cleaned_output", "llm_clean_with_edits",
            assignments={
                "BASE_PROMPT", "FEW_SHOT", "LLM_CLEANUP_TIMEOUT",
                "LLM_CLEANUP_TOTAL_DEADLINES", "CLEANUP_RESPONSE_SCHEMA",
                "MODE_INSTRUCTIONS", "REFUSAL_RE", "STRUCTURED_OUTPUT",
            },
            extra={
                "CleanupEdit": object,
                "LLM_CLEANUP_BREAKER": CleanupCircuitBreaker(
                    cooldown_seconds=60),
                "ollama_chat": unavailable,
                "quick_clean": lambda text: f"fallback:{text}",
                "STRUCTURED_FEW_SHOT": [],
                "print": lines.append,
            },
        )

        first = ns["llm_clean_with_edits"](
            "first private dictation", "neutral")
        second = ns["llm_clean_with_edits"](
            "second private dictation", "neutral")

        self.assertEqual(calls, [True])
        self.assertEqual(first, ("fallback:first private dictation", []))
        self.assertEqual(second, ("fallback:second private dictation", []))
        self.assertTrue(any("bypassed (cooldown)" in line for line in lines))
        self.assertFalse(any("second private dictation" in line
                             for line in lines))

    def test_guard_rejection_releases_without_resetting_the_cooldown(self):
        breaker = CleanupCircuitBreaker(cooldown_seconds=60)

        def refusing(*_args, **_kwargs):
            return json.dumps({
                "text": "I cannot help with that.", "edits": [],
            }), "stop"

        ns = load_definitions(
            "_guard_cleaned_output", "llm_clean_with_edits",
            assignments={
                "BASE_PROMPT", "FEW_SHOT", "LLM_CLEANUP_TIMEOUT",
                "LLM_CLEANUP_TOTAL_DEADLINES", "CLEANUP_RESPONSE_SCHEMA",
                "MODE_INSTRUCTIONS", "REFUSAL_RE", "STRUCTURED_OUTPUT",
            },
            extra={
                "CleanupEdit": object,
                "LLM_CLEANUP_BREAKER": breaker,
                "ollama_chat": refusing,
                "quick_clean": lambda text: f"fallback:{text}",
                "STRUCTURED_FEW_SHOT": [],
                "print": lambda *_: None,
            },
        )
        cleaned, _ = ns["llm_clean_with_edits"](
            "please clean this dictation", "neutral")
        self.assertEqual(cleaned, "fallback:please clean this dictation")
        # Released, not stuck in flight, and not opened either.
        self.assertTrue(breaker.acquire().allowed)

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
                "is_hallucination": lambda *_args: False,
            },
        )
        future = SimpleNamespace(result=lambda: Recognition(
            "stable words provisional tail"))
        ns["_caption_add"](future)
        self.assertEqual(caption["text"], "stable words")

    def test_live_caption_follows_the_silent_audio_rule_of_final_text(self):
        def caption_after(recognition):
            caption = {"text": "Listening"}
            ns = load_definitions(
                "_caption_add", "is_hallucination", "weak_whisper_evidence",
                assignments=CleanupGuardTests.HALLUCINATION_GUARD,
                extra={
                    "Recognition": Recognition,
                    "CAPTION": caption,
                    "compile_voice_evidence": lambda result, *_args, **_kw: (
                        None, SimpleNamespace(stable_prefix=result.text)),
                    "join_recognized_parts":
                        lambda parts, _language: " ".join(filter(None, parts)),
                },
            )
            ns["_caption_add"](SimpleNamespace(result=lambda: recognition))
            return caption["text"]

        # Whisper filling a pause must not flash in the caption either.
        self.assertEqual(caption_after(Recognition(
            "Thank you.", confidence=0.31, engine="tiny",
            audio_duration=3.0)), "Listening")
        self.assertEqual(caption_after(Recognition(
            "Thank you.", confidence=0.93, engine="parakeet-unified",
            audio_duration=1.2)), "Thank you.")

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
                "GLOSS": {
                    "lock": threading.Lock(),
                    "anchor_pack": ContextPack(),
                },
            },
        )
        _voice, result = ns["compile_voice_evidence"](
            Recognition("Use Gwen here", confidence=0.7, engine="tiny"),
            ["Qwen"],
            "com.openai.codex",
        )
        self.assertEqual(result.text, "Use Qwen here")

    def test_dictionary_and_screen_names_anchor_without_replacing_words(self):
        from voice_compiler import ContextObservation, ContextRouter

        dictionary = ContextPack(tuple(
            ContextCandidate(term, 3.5, "dictionary")
            for term in ("AI", "CI", "PR")))
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
                "GLOSS": {
                    "lock": threading.Lock(),
                    "anchor_pack": dictionary,
                },
            },
        )
        # Parakeet's transcript at the confidence one disagreeing word in two
        # leaves it with, Tiny's as the alternative, and the Context Pack the
        # router builds for the frontmost app and window.
        for app, title, parakeet, tiny in (
                ("Slack", "", "Who is on call today?", "Hu is on call to day?"),
                ("Code", "dictate.py", "Looks good.", "Look good."),
                ("Messages", "Nate Smith", "Not yet.", "Nah yet."),
                ("Zed", "", "She said the site is down.",
                 "Shi set the side is down."),
        ):
            with self.subTest(app=app):
                voice, result = ns["compile_voice_evidence"](
                    Recognition(parakeet, confidence=0.69,
                                engine="parakeet-unified", alternative=tiny),
                    bundle="com.example.app",
                    context_pack=ContextRouter().collect(ContextObservation(
                        app=app, bundle="com.example.app",
                        window_title=title)),
                )
                self.assertEqual(result.text, parakeet)
                self.assertEqual(result.decisions, ())
                self.assertEqual(
                    {candidate.text for candidate in voice.context.candidates
                     if candidate.source == "dictionary"},
                    {"AI", "CI", "PR"})


class ConfigurationTests(unittest.TestCase):
    def test_mac_permission_recovery_opens_generic_system_settings(self):
        calls = []
        fake_subprocess = SimpleNamespace(
            DEVNULL=object(),
            run=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        ns = load_definitions(
            "open_mac_system_settings",
            extra={"IS_MACOS": True, "subprocess": fake_subprocess},
        )

        ns["open_mac_system_settings"]()

        self.assertEqual(calls[0][0], (["open", "-a", "System Settings"],))
        self.assertTrue(calls[0][1]["check"])
        self.assertEqual(calls[0][1]["timeout"], 5)
        self.assertIs(calls[0][1]["stdout"], fake_subprocess.DEVNULL)
        self.assertIs(calls[0][1]["stderr"], fake_subprocess.DEVNULL)

    def test_permission_recovery_fails_closed_off_mac(self):
        fake_subprocess = SimpleNamespace(DEVNULL=object(), run=lambda: None)
        ns = load_definitions(
            "open_mac_system_settings",
            extra={"IS_MACOS": False, "subprocess": fake_subprocess},
        )

        with self.assertRaisesRegex(RuntimeError, "only on macOS"):
            ns["open_mac_system_settings"]()

    def load_permission_recheck(self):
        return load_definitions(
            "permission_recheck_attempt",
            "permission_recheck_delay",
            assignments=(
                "PERMISSION_ATTEMPT_ENV",
                "PERMISSION_RECHECK_ENV",
                "PERMISSION_RECHECK_SECONDS",
                "PERMISSION_RECHECK_BACKOFF_SECONDS",
                "PERMISSION_RECHECK_FAST_ATTEMPTS",
                "PERMISSION_RECHECK_MAX_SECONDS",
            ),
            extra={"os": SimpleNamespace(environ={})},
        )

    def test_permission_recheck_is_fast_then_backs_off(self):
        ns = self.load_permission_recheck()
        delay = ns["permission_recheck_delay"]
        fast = ns["PERMISSION_RECHECK_SECONDS"]
        attempts = ns["PERMISSION_RECHECK_FAST_ATTEMPTS"]
        backoff = ns["PERMISSION_RECHECK_BACKOFF_SECONDS"]

        # A grant has to start the app within seconds, not a minute.
        self.assertLessEqual(fast, 5)
        self.assertEqual(delay(0, {}), fast)
        self.assertEqual(delay(attempts - 1, {}), fast)
        # The eager phase covers roughly the first minute of attempts.
        self.assertGreaterEqual(fast * attempts, 45)
        self.assertLessEqual(fast * attempts, 120)
        # Then it backs off so an unattended Mac never spins at that rate.
        self.assertGreater(backoff, fast)
        self.assertEqual(delay(attempts, {}), backoff)
        self.assertEqual(delay(1_000_000, {}), backoff)

    def test_permission_recheck_interval_is_overridable_and_bounded(self):
        ns = self.load_permission_recheck()
        delay = ns["permission_recheck_delay"]
        fast = ns["PERMISSION_RECHECK_SECONDS"]
        name = ns["PERMISSION_RECHECK_ENV"]

        self.assertEqual(delay(0, {name: "0.25"}), 0.25)
        self.assertEqual(delay(0, {name: " 1 "}), 1.0)
        # Anything unusable keeps the built-in interval: the wait is never
        # zero, negative, unbounded, or an exception.
        for hostile in ("", "0", "-5", "abc", "nan", None, "1e9", "600"):
            with self.subTest(hostile=hostile):
                self.assertEqual(delay(0, {name: hostile}), fast)
        for hostile in (None, "", object()):
            with self.subTest(attempt=hostile):
                self.assertEqual(delay(hostile, {}), fast)

    def test_permission_attempt_counter_survives_reexec_and_bad_values(self):
        ns = self.load_permission_recheck()
        attempt = ns["permission_recheck_attempt"]
        name = ns["PERMISSION_ATTEMPT_ENV"]

        self.assertEqual(attempt({}), 0)
        self.assertEqual(attempt({name: " 7 "}), 7)
        for hostile in ("", "-1", "abc", None, "9999999999", "1.5"):
            with self.subTest(hostile=hostile):
                self.assertEqual(attempt({name: hostile}), 0)

    def test_permission_wait_reexecs_without_blocking_appkit_reachability(self):
        script = (ROOT / "dictate.py").read_text(encoding="utf-8")
        body = script.split(
            "def ensure_event_permissions", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("time.sleep(60)", body)
        self.assertIn("Re-checking every few seconds", body)
        self.assertIn("attempt = permission_recheck_attempt()", body)
        self.assertIn("os.environ[PERMISSION_ATTEMPT_ENV] = str(attempt + 1)", body)
        self.assertIn('name="whisper-face-permission-recheck"', body)
        self.assertIn("return False", body)
        self.assertNotIn("time.sleep(", body)
        # Main must publish the exact-process activation socket before entering
        # permission recovery, then keep AppKit alive while the worker refreshes
        # macOS's process-frozen TCC verdict.
        main = script.split("def main():", 1)[1].split(
            '\n\nif __name__ == "__main__":', 1)[0]
        self.assertLess(
            main.index('start_gui_activation_server(STATUS["bar"].gui)'),
            main.index("if not ensure_event_permissions():"),
        )
        self.assertLess(
            main.index("if not ensure_event_permissions():"),
            main.index('trace_operation("warmup_audio_pool", AUDIO_POOL.warm)'),
        )
        recovery = main.split("if not ensure_event_permissions():", 1)[1]
        self.assertIn("AppHelper.runEventLoop", recovery)

    def test_permission_recheck_worker_reexecs_once_after_fresh_grant(self):
        calls = []
        verdicts = iter((False, False, True))
        fake_os = SimpleNamespace(
            environ={},
            execv=lambda executable, arguments:
                calls.append(("execv", executable, arguments)))
        fake_sys = SimpleNamespace(
            executable="/runtime/python", argv=["dictate.py", "--test"])
        namespace = load_definitions(
            "_wait_for_permission_grant_and_reexec",
            assignments={"PERMISSION_ATTEMPT_ENV"},
            extra={
                "os": fake_os,
                "sys": fake_sys,
                "time": SimpleNamespace(
                    sleep=lambda delay: calls.append(("sleep", delay))),
                "permission_recheck_delay": lambda attempt: attempt + 0.5,
                "_fresh_event_permissions_granted": lambda: False,
            },
        )

        namespace["_wait_for_permission_grant_and_reexec"](
            3,
            sleeper=lambda delay: calls.append(("sleep", delay)),
            preflight=lambda: next(verdicts),
            execv=fake_os.execv,
        )

        self.assertEqual(calls, [
            ("sleep", 3.5),
            ("sleep", 4.5),
            ("sleep", 5.5),
            ("execv", "/runtime/python", [
                "/runtime/python", "dictate.py", "--test"]),
        ])
        self.assertEqual(
            fake_os.environ[namespace["PERMISSION_ATTEMPT_ENV"]], "6")

    def test_permission_probe_is_content_free_and_non_prompting(self):
        received = []
        fake_subprocess = SimpleNamespace(
            DEVNULL=-3,
            SubprocessError=subprocess.SubprocessError,
            run=None,
        )
        namespace = load_definitions(
            "_fresh_event_permissions_granted",
            extra={
                "subprocess": fake_subprocess,
                "sys": SimpleNamespace(executable="/runtime/python"),
            },
        )
        probe = namespace["_fresh_event_permissions_granted"]

        def runner(arguments, **options):
            received.append((arguments, options))
            return SimpleNamespace(returncode=0)

        self.assertTrue(probe(runner=runner))
        arguments, options = received[0]
        self.assertEqual(arguments[:2], ["/runtime/python", "-c"])
        self.assertIn("CGPreflightListenEventAccess", arguments[2])
        self.assertIn("CGPreflightPostEventAccess", arguments[2])
        self.assertNotIn("CGRequest", arguments[2])
        self.assertEqual(options["timeout"], 5)
        self.assertFalse(options["check"])
        self.assertFalse(probe(
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=1)))
        self.assertFalse(probe(
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("unavailable"))))

    def test_missing_permissions_schedule_recovery_without_blocking(self):
        calls = []

        class FakeThread:
            def __init__(self, *, target, args, name, daemon):
                calls.append(("thread", target, args, name, daemon))

            def start(self):
                calls.append(("start",))

        fake_os = SimpleNamespace(environ={})
        worker = object()
        namespace = load_definitions(
            "ensure_event_permissions",
            assignments={"PERMISSION_ATTEMPT_ENV"},
            extra={
                "IS_WINDOWS": False,
                "os": fake_os,
                "threading": SimpleNamespace(Thread=FakeThread),
                "permission_recheck_attempt": lambda: 0,
                "_wait_for_permission_grant_and_reexec": worker,
            },
        )
        quartz = SimpleNamespace(
            CGPreflightListenEventAccess=lambda: False,
            CGPreflightPostEventAccess=lambda: False,
            CGRequestListenEventAccess=lambda: calls.append(("listen",)),
            CGRequestPostEventAccess=lambda: calls.append(("post",)),
        )
        application_services = SimpleNamespace(
            AXIsProcessTrustedWithOptions=lambda options:
                calls.append(("accessibility", options)),
            kAXTrustedCheckOptionPrompt="prompt",
        )
        with mock.patch.dict(sys.modules, {
            "Quartz": quartz,
            "ApplicationServices": application_services,
        }):
            self.assertFalse(namespace["ensure_event_permissions"]())

        self.assertIn(("listen",), calls)
        self.assertIn(("post",), calls)
        thread = next(call for call in calls if call[0] == "thread")
        self.assertIs(thread[1], worker)
        self.assertEqual(thread[2], (0,))
        self.assertEqual(thread[3:], (
            "whisper-face-permission-recheck", True))
        self.assertIn(("start",), calls)

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

    @staticmethod
    def probe_namespace(cached, downloads=None, *, is_macos=True):
        """Load the cache probe over a fake, download-free Hugging Face API."""
        def download(repo_id, revision=None, local_files_only=None):
            if downloads is not None:
                downloads.append((repo_id, revision, local_files_only))
            if repo_id not in cached:
                raise FileNotFoundError(repo_id)
            return f"/models/{repo_id.replace('/', '--')}"

        ns = load_definitions(
            "resolve_asr_model", "asr_model_is_cached",
            assignments={
                "ASR_MODEL_PATHS", "ASR_MODEL_PATHS_LOCK",
                "ASR_MODEL_REVISIONS", "ASR_MODELS_NOT_CACHED",
            },
            extra={"IS_MACOS": is_macos},
        )
        ns["download"] = download
        return ns

    def test_model_presence_probe_never_downloads_and_memoizes_its_answer(self):
        downloads = []
        ns = self.probe_namespace({"org/tiny"}, downloads)

        probe = ns["asr_model_is_cached"]
        self.assertTrue(probe("org/tiny", ns["download"]))
        self.assertFalse(probe("org/turbo", ns["download"]))
        # Repeat probes must reuse both the hit and the miss, so an installer
        # or a dictation never pays for the same cache walk twice.
        self.assertTrue(probe("org/tiny", ns["download"]))
        self.assertFalse(probe("org/turbo", ns["download"]))

        self.assertEqual(downloads, [
            ("org/tiny", None, True),
            ("org/turbo", None, True),
        ])
        self.assertTrue(all(
            local_files_only is True for _, _, local_files_only in downloads))
        self.assertEqual(ns["ASR_MODELS_NOT_CACHED"], {"org/turbo"})

        # Windows resolves through faster-whisper's own cache, so this probe
        # claims nothing about it rather than guessing.
        windows = self.probe_namespace({"org/tiny"}, is_macos=False)
        self.assertFalse(
            windows["asr_model_is_cached"]("org/tiny", windows["download"]))

    def test_missing_optional_whisper_model_degrades_to_the_installed_one(self):
        present = set()

        def available(repo):
            return repo in present

        ns = load_definitions(
            "asr_decode_target",
            assignments={"ASR_DEGRADED_NOTICES"},
            extra={
                "IS_MACOS": True,
                "WHISPER_REPO": "org/turbo",
                "FAST_WHISPER_REPO": "org/tiny",
            },
        )
        target = ns["asr_decode_target"]

        # A full install keeps the accurate fallback.
        present = {"org/turbo", "org/tiny"}
        self.assertEqual(target("org/turbo", available=available), "org/turbo")

        # A minimal install decodes with the model it actually has.
        ns["ASR_DEGRADED_NOTICES"].clear()
        present = {"org/tiny"}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(
                target("org/turbo", available=available), "org/tiny")
            self.assertEqual(
                target("org/turbo", available=available), "org/tiny")
        self.assertEqual(out.getvalue().count("./setup.sh --models"), 1)

        # Nothing cached: surface the real resolution error, do not substitute.
        ns["ASR_DEGRADED_NOTICES"].clear()
        present = set()
        self.assertEqual(target("org/turbo", available=available), "org/turbo")

        # The fast model and non-macOS routing are never rewritten.
        present = {"org/tiny"}
        self.assertEqual(target("org/tiny", available=available), "org/tiny")
        self.assertEqual(
            target("org/turbo", available=available, is_macos=False),
            "org/turbo")

    def test_transcription_decodes_through_the_available_model_target(self):
        transcribe = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "transcribe_detailed"
        )
        calls = {
            node.func.id for node in ast.walk(transcribe)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("asr_decode_target", calls)
        self.assertIn("resolve_asr_model", calls)

    def test_preload_downloads_only_the_requested_missing_models(self):
        resolved = []
        present = {"org/tiny"}
        ns = load_definitions(
            "preload_model_files",
            extra={
                "IS_WINDOWS": False,
                "FAST_WHISPER_REPO": "org/tiny",
                "WHISPER_REPO": "org/turbo",
                "asr_model_is_cached": lambda repo: repo in present,
                "resolve_asr_model": lambda repo, local_files_only=None: (
                    resolved.append((repo, local_files_only)) or repo),
                "windows_whisper_model": lambda repo: repo,
            },
        )

        # Default minimal install: only the small model, and it is cached.
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ns["preload_model_files"](("org/tiny",))
        self.assertEqual(resolved, [])
        self.assertIn("already cached", out.getvalue())
        self.assertNotIn("Downloading", out.getvalue())

        # Opt-in run: fetch only the model that is genuinely absent.
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ns["preload_model_files"]()
        self.assertEqual(resolved, [("org/turbo", False)])
        self.assertIn("Downloading org/turbo", out.getvalue())
        self.assertNotIn("Downloading org/tiny", out.getvalue())

    def test_windows_runtime_loads_models_without_contacting_the_hub(self):
        # faster-whisper's download_model asks the Hub for repo metadata
        # before looking in its cache unless local_files_only is set. Only
        # the installer's preload may go online.
        fetched = []

        def download_model(repo, **kwargs):
            fetched.append((repo, kwargs.get("local_files_only", False)))
            return f"C:/models/{repo}"

        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = download_model
        package = types.ModuleType("faster_whisper")
        package.utils = utils
        ns = load_definitions(
            "windows_whisper_model", "preload_model_files",
            extra={
                "IS_WINDOWS": True,
                "HERE": Path("C:/Whisper Face"),
                "ASR_MODEL_REVISIONS": {"tiny": "d90ca5fe", "turbo": "0a363e91"},
                "WINDOWS_ASR_MODELS": {},
                "FAST_WHISPER_REPO": "tiny",
                "WHISPER_REPO": "turbo",
                "ctranslate2": SimpleNamespace(
                    get_cuda_device_count=lambda: 0),
                "WhisperModel": lambda path, device, compute_type: path,
            },
        )
        with mock.patch.dict(sys.modules, {
                "faster_whisper": package, "faster_whisper.utils": utils}), \
                contextlib.redirect_stdout(io.StringIO()):
            ns["windows_whisper_model"]("tiny")
            ns["WINDOWS_ASR_MODELS"].clear()
            ns["preload_model_files"](("turbo",))

        self.assertEqual(fetched, [("tiny", True), ("turbo", False)])

    def test_model_inventory_reports_every_pinned_model_without_downloading(
            self):
        ns = load_definitions(
            "model_inventory", "print_model_inventory",
            extra={
                "FAST_WHISPER_REPO": "org/tiny",
                "WHISPER_REPO": "org/turbo",
                "parakeet_model_is_cached": lambda: True,
                "asr_model_is_cached": lambda repo: repo == "org/tiny",
            },
        )

        self.assertEqual(ns["model_inventory"](), {
            "parakeet": True,
            "whisper-fast": True,
            "whisper-large": False,
        })
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ns["print_model_inventory"]()
        self.assertEqual(sorted(out.getvalue().split()), [
            "parakeet=present",
            "whisper-fast=present",
            "whisper-large=missing",
        ])

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
            *TranscriptRetentionTests.NAMES,
            assignments=TranscriptRetentionTests.ASSIGNMENTS,
            extra={"hashlib": hashlib, "os": os, "time": time},
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

    def test_a_windows_window_title_never_reaches_transcript_history(self):
        ns = load_definitions(
            *TranscriptRetentionTests.NAMES,
            assignments=TranscriptRetentionTests.ASSIGNMENTS,
            extra={"hashlib": hashlib, "os": os, "time": time},
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
                "raw", "clean", "windows:Q3 layoffs plan.docx - Word", "fast")
            entry = json.loads(transcript.read_text())

        self.assertNotIn("layoffs", json.dumps(entry))
        self.assertRegex(entry["app"], r"^windows:[0-9a-f]{16}$")
        # Stable per window title, so per-app grouping survives redaction,
        # and a macOS bundle identifier is still stored verbatim.
        self.assertEqual(
            ns["transcript_app_identity"](
                "windows:Q3 layoffs plan.docx - Word"),
            entry["app"])
        self.assertNotEqual(
            ns["transcript_app_identity"]("windows:Other - Word"),
            entry["app"])
        self.assertEqual(
            ns["transcript_app_identity"]("com.apple.TextEdit"),
            "com.apple.TextEdit")

    def test_transcript_metrics_name_asr_and_insertion_verification_apart(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        # `verified` alone read as delivery truth and was aggregated as one.
        self.assertNotIn('"verified": recognition.verified', source)
        self.assertIn('"asr_verified": recognition.verified', source)
        self.assertIn('"insertion_verified": verified', source)

    def test_transcript_metrics_export_the_capability_triple(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        for key in ("insertion_target", "insertion_paste",
                    "insertion_readback"):
            self.assertIn(f'"{key}": insertion_capabilities.get(', source)

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
            *TranscriptRetentionTests.NAMES,
            assignments=TranscriptRetentionTests.ASSIGNMENTS,
            extra={"hashlib": hashlib, "os": portable_os, "time": time},
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
            *TranscriptRetentionTests.NAMES, "record_paste_outcome",
            assignments=TranscriptRetentionTests.ASSIGNMENTS,
            extra={
                "hashlib": hashlib,
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
            "match_snippet", "snippet_name_key", "_snippet_is_placeholder",
            assignments={"SNIPPET_RE"},
        )
        with tempfile.TemporaryDirectory() as td:
            snippets = Path(td) / "snippets.json"
            snippets.write_text("[]")
            ns["SNIPPETS_FILE"] = snippets
            self.assertIsNone(ns["match_snippet"]("insert email"))

    def test_snippet_names_work_in_any_script(self):
        # The old key stripped [^a-z0-9 ], so every non-Latin name
        # collapsed to "" and matched whichever snippet came first.
        ns = load_definitions(
            "match_snippet", "snippet_name_key", "_snippet_is_placeholder",
            assignments={"SNIPPET_RE"},
        )
        with tempfile.TemporaryDirectory() as td:
            snippets = Path(td) / "snippets.json"
            snippets.write_text(json.dumps({
                "住所": "1-2-3 Chiyoda",
                "адрес": "Tverskaya 7",
                "café": "Rue de Rivoli 12",
            }), encoding="utf-8")
            ns["SNIPPETS_FILE"] = snippets
            self.assertEqual(
                ns["match_snippet"]("insert 住所"),
                ("住所", "1-2-3 Chiyoda"))
            self.assertEqual(
                ns["match_snippet"]("insert адрес"),
                ("адрес", "Tverskaya 7"))
            # Accented Latin no longer loses its accents to the key.
            self.assertEqual(
                ns["match_snippet"]("insert café"),
                ("café", "Rue de Rivoli 12"))
            # A different non-Latin name is not a match for any of them.
            self.assertIsNone(ns["match_snippet"]("insert 이메일"))

    def test_a_name_that_normalizes_to_nothing_matches_nothing(self):
        ns = load_definitions(
            "match_snippet", "snippet_name_key", "_snippet_is_placeholder",
            assignments={"SNIPPET_RE"},
        )
        with tempfile.TemporaryDirectory() as td:
            snippets = Path(td) / "snippets.json"
            snippets.write_text(json.dumps({"!!!": "boom"}), encoding="utf-8")
            ns["SNIPPETS_FILE"] = snippets
            # Empty key on both sides must never count as equality.
            self.assertIsNone(ns["match_snippet"]("insert ..."))

    def test_an_unfilled_placeholder_is_not_a_snippet(self):
        ns = load_definitions(
            "match_snippet", "snippet_name_key", "_snippet_is_placeholder",
            assignments={"SNIPPET_RE"},
        )
        with tempfile.TemporaryDirectory() as td:
            snippets = Path(td) / "snippets.json"
            # What both installers copy into place on a fresh install.
            template = json.loads(
                (ROOT / "snippets.template.json").read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(template), ["address", "email", "signature"])
            snippets.write_text(json.dumps(template), encoding="utf-8")
            ns["SNIPPETS_FILE"] = snippets
            for name in template:
                with self.subTest(name=name):
                    self.assertIsNone(
                        ns["match_snippet"](f"Insert my {name}."))
            # The same name is a snippet once the user has filled it in.
            template["email"] = "andrew@example.com"
            snippets.write_text(json.dumps(template), encoding="utf-8")
            self.assertEqual(
                ns["match_snippet"]("Insert my email."),
                ("email", "andrew@example.com"))
            self.assertIsNone(ns["match_snippet"]("Insert my address."))

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
            "https://github.com/Aiml3ss/whisper-face/tree/"
            "0123456789abcdef0123456789abcdef01234567",
        )
        self.assertEqual(
            metadata["source_revision"],
            "0123456789abcdef0123456789abcdef01234567",
        )
        self.assertTrue(metadata["license_policy"].endswith("LICENSE_POLICY.md"))
        self.assertIn("without warranty", metadata["warranty"].lower())


class TranscriptRetentionTests(unittest.TestCase):
    """The history log stays bounded whether or not the miner ever runs."""

    NOW = 1_800_000_000.0
    DAY = 24 * 3600.0
    NAMES = (
        "append_transcript", "transcript_app_identity",
        "_transcript_entry_time", "retained_transcript_lines",
        "_trim_transcripts_locked", "_rebase_learned_processed",
        "prune_transcripts", "clear_transcript_history",
        "load_learned", "save_learned",
    )
    ASSIGNMENTS = (
        "TRANSCRIPT_KEEP", "TRANSCRIPT_MAX_AGE", "TRANSCRIPT_TRIM_SLACK",
        "TRANSCRIPT_AGE_SLACK",
    )

    def namespace(self, folder, *names, assignments=(), extra=None):
        writes = []

        def atomic_write_text(path, value, mode=0o600):
            writes.append(path.name)
            path.write_text(value)

        ns = load_definitions(
            *self.NAMES, *names,
            assignments=self.ASSIGNMENTS + tuple(assignments),
            extra={
                "hashlib": hashlib,
                "os": os,
                "Path": Path,
                "time": SimpleNamespace(time=lambda: self.NOW),
                "atomic_write_text": atomic_write_text,
                **(extra or {}),
            },
        )
        ns.update({
            "TRANSCRIPTS_FILE": Path(folder) / "transcripts.jsonl",
            "LEARNED_FILE": Path(folder) / "learned.json",
            "TRANSCRIPTS_LOCK": threading.Lock(),
            "LEARN_LOCK": threading.Lock(),
            "USAGE_CACHE": {
                "at": 5.0, "value": (0, 0.0), "lock": threading.Lock()},
        })
        return ns, writes

    @staticmethod
    def write_log(path, timestamps):
        path.write_text("".join(
            json.dumps({
                "ts": ts, "app": "com.apple.TextEdit",
                "raw": f"note number {index}",
                "clean": f"Note number {index}.",
                "path": "fast", "language": "en",
            }) + "\n"
            for index, ts in enumerate(timestamps)))

    @staticmethod
    def entries(path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_a_failing_miner_still_leaves_the_log_bounded(self):
        # The default install has no cleanup model, so every mining pass
        # fails. Retention must not depend on it succeeding.
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(
                td, "learn_pass", "parse_texts", "parse_dictionary",
                "merge_learned_state",
                assignments=(
                    "MINER_PROMPT", "LEARN_MIN_NEW", "PROMOTE_MIN_COUNT",
                    "AUTO_MARKER"),
                extra={
                    "ollama_chat": mock.Mock(side_effect=ConnectionError(
                        "connection refused")),
                    "refresh_glossary": lambda: [],
                    "DICTIONARY_FILE": Path(td) / "dictionary.txt",
                    "DICTIONARY_LOCK": threading.Lock(),
                },
            )
            log = ns["TRANSCRIPTS_FILE"]
            self.write_log(log, [
                self.NOW - 3600 + index for index in range(2000)])
            with contextlib.redirect_stdout(io.StringIO()):
                ns["learn_pass"]()
            kept = self.entries(log)

        self.assertLessEqual(len(kept), ns["TRANSCRIPT_KEEP"])
        self.assertEqual(kept[-1]["clean"], "Note number 1999.")

    def test_appending_alone_never_lets_the_log_exceed_its_cap(self):
        with tempfile.TemporaryDirectory() as td:
            ns, writes = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            keep = ns["TRANSCRIPT_KEEP"]
            self.write_log(log, [self.NOW - 60] * keep)
            sizes = []
            for index in range(250):
                ns["append_transcript"](
                    f"extra {index}", f"Extra {index}.",
                    "com.apple.TextEdit", "fast")
                sizes.append(len(log.read_text().splitlines()))
            kept = self.entries(log)

        self.assertLessEqual(max(sizes), keep)
        self.assertEqual(kept[-1]["clean"], "Extra 249.")
        # Headroom per trim: the file is rewritten a few times, not per paste.
        self.assertLessEqual(len(writes), 3)

    def test_appending_drops_dictations_older_than_thirty_days(self):
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            self.write_log(log, [
                self.NOW - 40 * self.DAY,
                self.NOW - 31 * self.DAY,
                self.NOW - 10 * self.DAY,
            ])
            ns["append_transcript"](
                "today", "Today.", "com.apple.TextEdit", "fast")
            kept = [entry["clean"] for entry in self.entries(log)]

        self.assertEqual(kept, ["Note number 2.", "Today."])

    def test_a_log_within_its_bounds_is_appended_without_a_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            ns, writes = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            self.write_log(log, [self.NOW - 29 * self.DAY] + [self.NOW] * 99)
            ns["append_transcript"](
                "fine", "Fine.", "com.apple.TextEdit", "fast")
            count = len(log.read_text().splitlines())

        self.assertEqual(count, 101)
        self.assertEqual(writes, [])

    def test_a_damaged_log_never_stops_a_dictation_from_being_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            self.write_log(log, [self.NOW - 60] * 3)
            log.write_bytes(b'{"ts": \xff\xfe\n' + log.read_bytes())
            ns["append_transcript"](
                "after", "After.", "com.apple.TextEdit", "fast")
            kept = self.entries(log)

        # The unreadable line is dropped; every readable one is kept.
        self.assertEqual([entry["clean"] for entry in kept], [
            "Note number 0.", "Note number 1.", "Note number 2.", "After."])

    def test_the_mining_pass_ages_out_history_without_new_dictations(self):
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            self.write_log(log, [self.NOW - 45 * self.DAY] * 3)
            ns["prune_transcripts"]()
            remaining = log.read_text()

        self.assertEqual(remaining, "")

    def test_trimming_keeps_the_miner_on_the_same_dictation(self):
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            keep = ns["TRANSCRIPT_KEEP"]
            self.write_log(log, [self.NOW - 60] * keep)
            ns["LEARNED_FILE"].write_text(json.dumps({"processed": keep}))
            ns["append_transcript"](
                "new", "New.", "com.apple.TextEdit", "fast")
            lines = log.read_text().splitlines()
            processed = json.loads(ns["LEARNED_FILE"].read_text())["processed"]

        # Every older dictation was mined; only the one just added is new.
        self.assertEqual(processed, len(lines) - 1)
        self.assertEqual(json.loads(lines[processed])["clean"], "New.")

    def test_clearing_history_erases_the_log_and_resets_the_miner(self):
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(td)
            log = ns["TRANSCRIPTS_FILE"]
            self.write_log(log, [self.NOW] * 12)
            ns["LEARNED_FILE"].write_text(json.dumps({
                "processed": 12, "counts": {"Qwen": 2}}))
            ns["clear_transcript_history"]()
            existed = log.exists()
            learned = json.loads(ns["LEARNED_FILE"].read_text())
            cache_at = ns["USAGE_CACHE"]["at"]
            ns["append_transcript"](
                "after", "After.", "com.apple.TextEdit", "fast")
            after = [entry["clean"] for entry in self.entries(log)]
            mode = stat.S_IMODE(log.stat().st_mode)

        self.assertFalse(existed)
        self.assertEqual(learned["processed"], 0)
        self.assertEqual(learned["counts"], {"Qwen": 2})
        self.assertEqual(cache_at, 0.0)
        self.assertEqual(after, ["After."])
        if os.name == "posix":
            self.assertEqual(mode, 0o600)


class VocabularyMinerTests(unittest.TestCase):
    """What the miner may count toward the dictionary."""

    NOW = TranscriptRetentionTests.NOW
    NAMES = TranscriptRetentionTests.NAMES
    ASSIGNMENTS = TranscriptRetentionTests.ASSIGNMENTS
    namespace = TranscriptRetentionTests.namespace

    def mine(self, texts, reply, learned):
        with tempfile.TemporaryDirectory() as td:
            ns, _ = self.namespace(
                td, "learn_pass", "parse_texts", "parse_dictionary",
                "merge_learned_state", "term_occurs",
                assignments=(
                    "MINER_PROMPT", "LEARN_MIN_NEW", "PROMOTE_MIN_COUNT",
                    "AUTO_MARKER"),
                extra={
                    "ollama_chat": lambda *args, **kwargs: (
                        json.dumps(reply), "stop"),
                    "refresh_glossary": lambda: [],
                    "DICTIONARY_FILE": Path(td) / "dictionary.txt",
                    "DICTIONARY_LOCK": threading.Lock(),
                },
            )
            ns["TRANSCRIPTS_FILE"].write_text("".join(
                json.dumps({"ts": self.NOW - 60 + index, "app": "com.apple.Notes",
                            "raw": text, "clean": text, "path": "fast",
                            "language": "en"}) + "\n"
                for index, text in enumerate(texts)))
            ns["LEARNED_FILE"].write_text(json.dumps(learned))
            with contextlib.redirect_stdout(io.StringIO()):
                ns["learn_pass"]()
            return json.loads(ns["LEARNED_FILE"].read_text())["counts"]

    def test_terms_are_counted_as_whole_words_not_substrings(self):
        texts = [
            "We are going to ship it today.",
            "That sounds good to me.",
            "I sent it a week ago.",
            "Zorblax signed the contract.",
            "Ask Zorblax about the invoice.",
        ] * 2
        counts = self.mine(texts, ["Go", "Zorblax"], {"counts": {"Ago": 1}})

        self.assertNotIn("Go", counts)
        self.assertEqual(counts["Zorblax"], 4)
        # "ago" is a word on its own, so a held term may still grow.
        self.assertEqual(counts["Ago"], 3)

    def test_a_term_the_user_corrected_away_is_never_counted(self):
        texts = ["Gwen runs locally on the laptop.",
                 "Ask Gwen to summarise the notes."] * 5
        learned = {
            "counts": {"Qwen": 2, "Gwenda": 1},
            "fixes": {"gwen": {"to": "Qwen", "n": 1},
                      "qwen": {"to": "Qwen", "n": 1}},
            "confusions": {"gwenda->brenda": {
                "from": "Gwenda", "to": "Brenda", "n": 1, "apps": {}}},
        }
        counts = self.mine(
            texts + ["Gwenda called."] * 2, ["Gwen", "Qwen"], learned)

        self.assertNotIn("Gwen", counts)
        self.assertEqual(counts["Gwenda"], 1)
        # A casing-only fix does not ban the word it fixes toward.
        self.assertEqual(counts["Qwen"], 2)

    def test_a_term_built_on_a_corrected_away_word_is_never_counted(self):
        learned = {
            "counts": {"Gwen Notes": 1},
            "fixes": {"gwen": {"to": "Qwen", "n": 1}},
            "confusions": {},
        }
        texts = ["Gwen 3.5 runs locally.", "Ask Gwen 3.5 for a summary.",
                 "Gwen's notes are ready.", "Send Gwen's summary.",
                 "Open Gwen Notes now.", "Gwendolyn Park signed."] * 2

        counts = self.mine(
            texts, ["Gwen 3.5", "Gwen's", "Gwendolyn Park"], learned)

        self.assertNotIn("Gwen 3.5", counts)
        self.assertNotIn("Gwen's", counts)
        # A held term containing the word stops growing toward promotion.
        self.assertEqual(counts["Gwen Notes"], 1)
        # Only the whole word is banned, not names that begin with it.
        self.assertEqual(counts["Gwendolyn Park"], 2)


class InlineSnippetExpansionTests(unittest.TestCase):
    """Inline snippet expansion inside a longer dictation.

    A snippet expands only on the spoken cue "insert my <name>", the cue the
    whole-utterance command uses. A bare name is an ordinary word, and an
    unfilled installer placeholder is never text to insert.
    """

    INLINE = (
        "expand_snippets_inline",
        "_load_snippet_map",
        "_compile_snippet_pattern",
        "_snippet_is_placeholder",
        "snippet_name_key",
    )
    PIPELINE = (
        "_load_snippet_map",
        "_compile_snippet_pattern",
        "_snippet_is_placeholder",
        "snippet_name_key",
        "_snippet_sentinel",
        "_mask_snippets_inline",
        "_restore_snippet_sentinels",
        "quick_clean",
    )
    PERSONAL = {
        "email": "andrew@example.com",
        "address": "7623 Opal Ridge Lane\nBainbridge Island, WA 98110",
        "signature": "Best,\nAndrew",
    }

    def _expander(self, mapping=None):
        ns = load_definitions(*self.INLINE)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "snippets.json"
        if mapping is not None:
            path.write_text(json.dumps(mapping))
        ns["SNIPPETS_FILE"] = path
        return ns["expand_snippets_inline"]

    def _dictate(self, spoken, snippets_text):
        """The capture pipeline's order: mask, clean up, restore."""
        ns = load_definitions(
            *self.PIPELINE,
            assignments={"_SNIPPET_SENTINEL_MARK"},
            extra={"compile_cleanup": compile_cleanup},
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "snippets.json"
        path.write_text(snippets_text, encoding="utf-8")
        ns["SNIPPETS_FILE"] = path
        masked, restore = ns["_mask_snippets_inline"](
            spoken, ns["_load_snippet_map"]())
        cleaned = ns["quick_clean"](masked if restore else spoken)
        return ns["_restore_snippet_sentinels"](cleaned, restore)

    ORDINARY = (
        "Send me your email.",
        "Email me when you land.",
        "Can you send me an email about the address change?",
        "What's your address?",
        "Let me address that in the meeting tomorrow.",
        "We need it before the signature deadline.",
        "Text him my email, please.",
        "My signature is on the last page.",
    )

    def test_shipped_template_names_are_ordinary_words(self):
        template = (ROOT / "snippets.template.json").read_text(
            encoding="utf-8")
        for name in json.loads(template):
            self.assertTrue(any(
                name in sentence.casefold() for sentence in self.ORDINARY))
        for spoken in self.ORDINARY:
            with self.subTest(spoken=spoken):
                self.assertEqual(self._dictate(spoken, template), spoken)

    def test_filled_in_snippets_never_expand_on_a_bare_name(self):
        for spoken in self.ORDINARY:
            with self.subTest(spoken=spoken):
                self.assertEqual(
                    self._dictate(spoken, json.dumps(self.PERSONAL)), spoken)

    def test_an_unfilled_placeholder_never_expands_even_on_the_cue(self):
        template = (ROOT / "snippets.template.json").read_text(
            encoding="utf-8")
        for name in json.loads(template):
            with self.subTest(name=name):
                spoken = f"Please use insert my {name} for now."
                self.assertEqual(self._dictate(spoken, template), spoken)

    def test_the_spoken_cue_expands_inside_a_sentence(self):
        self.assertEqual(
            self._dictate(
                "You can reach me at insert my email, or call.",
                json.dumps(self.PERSONAL)),
            "You can reach me at andrew@example.com, or call.",
        )

    def test_the_cue_is_case_insensitive(self):
        expand = self._expander({"email": "andrew@example.com"})
        self.assertEqual(
            expand("send it to Insert My EMAIL now"),
            "send it to andrew@example.com now")

    def test_partial_word_never_fires(self):
        # The name must end at a word boundary, so "address" does not fire
        # inside "addresses".
        expand = self._expander({"address": "1 Main St"})
        spoken = "please insert my addresses into the form"
        self.assertEqual(expand(spoken), spoken)

    def test_longest_name_wins(self):
        expand = self._expander({
            "email": "andrew@example.com",
            "email signature": "Best,\nAndrew",
        })
        self.assertEqual(
            expand("then insert my email signature here"),
            "then Best,\nAndrew here",
        )

    def test_multiline_expansion_is_preserved(self):
        expand = self._expander(self.PERSONAL)
        self.assertEqual(
            expand("mail it to insert my address today"),
            "mail it to 7623 Opal Ridge Lane\nBainbridge Island, WA 98110 "
            "today",
        )

    def test_a_name_with_no_word_characters_is_never_a_trigger(self):
        expand = self._expander({"": "BOOM", "!!!": "BOOM"})
        spoken = "hello, insert my world. fine"
        self.assertEqual(expand(spoken), spoken)

    def test_no_cue_returns_raw_unchanged(self):
        expand = self._expander({"email": "andrew@example.com"})
        self.assertEqual(expand("just talking normally"), "just talking normally")

    def test_missing_file_returns_raw_unchanged(self):
        expand = self._expander(mapping=None)  # file never written
        self.assertEqual(
            expand("send insert my email now"), "send insert my email now")

    def test_wrong_shaped_snippets_file_returns_raw_unchanged(self):
        # Clone of ConfigurationTests.test_wrong_shaped_snippets_file_is_ignored,
        # asserted against the inline path: a JSON array (not an object) and
        # syntactically broken JSON both degrade to leaving the dictation alone.
        for payload in ("[]", "{not valid json"):
            with self.subTest(payload=payload):
                ns = load_definitions(*self.INLINE)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "snippets.json"
                    path.write_text(payload)
                    ns["SNIPPETS_FILE"] = path
                    self.assertEqual(
                        ns["expand_snippets_inline"](
                            "send insert my email now"),
                        "send insert my email now",
                    )

    def test_masked_multiline_survives_deterministic_cleanup(self):
        # The pipeline round-trip: masking shields a multiline (indented)
        # expansion from cleanup, the surrounding words are still cleaned, and
        # restoration leaves no sentinel behind.
        ns = load_definitions(
            "_compile_snippet_pattern",
            "_snippet_is_placeholder",
            "snippet_name_key",
            "_snippet_sentinel",
            "_mask_snippets_inline",
            "_restore_snippet_sentinels",
            assignments={"_SNIPPET_SENTINEL_MARK"},
        )
        expansion = "Regards,\n    Andrew\n    Engineer"
        masked, restore = ns["_mask_snippets_inline"](
            "here is insert my sig okay", {"sig": expansion})
        self.assertTrue(restore)
        self.assertNotIn("sig", masked)         # cue and name were masked away
        cleaned = compile_cleanup(masked).text  # deterministic cleanup only
        restored = ns["_restore_snippet_sentinels"](cleaned, restore)
        self.assertEqual(restored, f"here is {expansion} okay")
        self.assertEqual(restored.count("\ue000"), 0)  # no sentinel leaked

    def test_restoration_of_absent_sentinel_is_a_no_op(self):
        ns = load_definitions(
            "_compile_snippet_pattern",
            "_snippet_is_placeholder",
            "snippet_name_key",
            "_snippet_sentinel",
            "_mask_snippets_inline",
            "_restore_snippet_sentinels",
            assignments={"_SNIPPET_SENTINEL_MARK"},
        )
        _, restore = ns["_mask_snippets_inline"](
            "insert my email", {"email": "X"})
        self.assertTrue(restore)
        # A token the cleaned text no longer contains must not corrupt output.
        self.assertEqual(
            ns["_restore_snippet_sentinels"]("unrelated text", restore),
            "unrelated text",
        )


class LearningTests(unittest.TestCase):
    @staticmethod
    def learning_runtime(directory, *, manual=(), learned=None):
        """The real learning, glossary and compile functions over scratch
        state, with a scripted Accessibility field and a fake clock."""
        root = Path(directory)
        clock = [0.0]
        field = {"timeline": []}
        observed = []

        def read(_element):
            current = None
            for at, text in field["timeline"]:
                if at <= clock[0] + 1e-9:
                    current = text
            return current

        (root / "dictionary.txt").write_text(
            "".join(f"{term}\n" for term in manual), encoding="utf-8")
        (root / "snippets.json").write_text("{}", encoding="utf-8")
        if learned is not None:
            (root / "learned.json").write_text(
                json.dumps(learned), encoding="utf-8")
        ns = load_definitions(
            "parse_dictionary", "load_learned", "save_learned",
            "personal_regression_lab", "write_auto_section",
            "refresh_glossary", "apply_vocabulary_casing",
            "apply_learned_fixes", "learned_alternatives",
            "compiler_personal_priors", "compile_voice_evidence",
            "FocusSnapshot", "PasteReceipt", "make_paste_receipt",
            "committed_paste_receipt", "_learned_count",
            "insertion_join_prefix", "commit_insertion",
            "observe_paste_outcome", "learn_from_corrections",
            "learn_snippet_edit", "paste_snippet_and_watch",
            "save_snippet_edit", "suggest_snippet_edit",
            "apply_pending_snippet_edit", "forget_snippet_edit",
            "forget_gui_correction", "SentUnchanged", "finish_and_process",
            assignments={
                "AUTO_MARKER", "PROMOTE_MIN_COUNT", "PERSONAL_APP_MIN_COUNT",
                "PERSONAL_GLOBAL_MIN_COUNT", "ANCHOR_MAX_TERMS",
                "CORRECTION_DELAY", "CORRECTION_POLL_INTERVAL",
                "CORRECTION_MAX_LEARN", "CORRECTION_STABLE_POLLS",
                "VOCAB_WORD_RE", "MIN_SECONDS", "GATE_PEAK_RMS", "CONT_END",
            },
            extra={
                "Path": Path,
                "dataclass": dataclass,
                "difflib": __import__("difflib"),
                "np": np,
                "print": lambda *_args, **_kwargs: None,
                "atomic_write_text":
                    lambda path, value: path.write_text(value),
                "time": SimpleNamespace(
                    monotonic=lambda: clock[0],
                    perf_counter=lambda: clock[0],
                    sleep=lambda seconds: clock.__setitem__(
                        0, clock[0] + seconds),
                    time=lambda: 1000.0 + clock[0],
                ),
                "DICTIONARY_FILE": root / "dictionary.txt",
                "LEARNED_FILE": root / "learned.json",
                "SNIPPETS_FILE": root / "snippets.json",
                "DICTIONARY_LOCK": threading.RLock(),
                "LEARN_LOCK": threading.Lock(),
                "SNIPPETS_LOCK": threading.Lock(),
                "GLOSS": {
                    "terms": [], "prompt": None, "confusions": {},
                    "regression": PersonalRegressionLab(),
                    "anchor_pack": ContextPack(), "vocabulary": {},
                    "lock": threading.Lock(),
                },
                "PIPELINE_STATE": {},
                "ACOUSTIC_KEYWORD_MEMORY_LOCK": threading.Lock(),
                "ACOUSTIC_KEYWORD_ACTIVATION_FILE": root / "activation.json",
                "_load_acoustic_keyword_memory":
                    lambda: (AcousticKeywordMemory(), "missing"),
                "active_acoustic_keywords":
                    lambda _path, _memory: ((), "missing"),
                "measured_keyword_hints": tuple,
                "remember_explicit_acoustic_keyword_correction":
                    lambda *_args, **_kwargs: True,
                "utterance_was_undone": lambda _event_id: False,
                "record_paste_outcome": lambda receipt, value:
                    observed.append((receipt.pasted, value)),
                "focused_snapshot": lambda: None,
                "paste": lambda _text: None,
                "ReceiptState": ReceiptState,
                "_ax_text": read,
                "infer_revised_insertion": infer_revised_insertion,
                "correction_similarity": __import__(
                    "parrot_core").correction_similarity,
                "PersonalRegressionLab": PersonalRegressionLab,
                "PersonalPrior": __import__("voice_compiler").PersonalPrior,
                "ContextPack": ContextPack,
                "ContextCandidate": ContextCandidate,
                "Recognition": Recognition,
                "WordEvidence": WordEvidence,
                "RecognitionHypothesis": RecognitionHypothesis,
                "VoiceIR": VoiceIR,
                "VOICE_COMPILER": VoiceCompiler(),
                "analyze_prosody": lambda *_args: (),
                "SAMPLE_RATE": 16_000,
            },
        )
        ns["refresh_glossary"]()

        def dictate(text, edits=(), *, before="", bundle="com.slack",
                    learn=True):
            """Commit ``text`` after ``before`` the way the pipeline does,
            script the user's later edits as (seconds, old, new), and run
            the watcher. Returns the receipt the watcher was given."""
            snapshot = ns["FocusSnapshot"](
                element=object(), text=before, selection=(len(before), 0))
            rec = SimpleNamespace(
                insertion_lease=None, insertion_receipt=None, language="en")
            ns["commit_insertion"](rec, text, bundle, snapshot)
            receipt = ns["committed_paste_receipt"](
                rec, snapshot, text, bundle, "capture",
                f"event-{clock[0]}")
            current = before + rec.committed_text
            field["timeline"] = [(clock[0], current)]
            for delay, old, new in edits:
                current = current.replace(old, new, 1)
                field["timeline"].append((clock[0] + delay, current))
            if learn:
                ns["learn_from_corrections"](receipt)
            return receipt

        def compiled(text, bundle="com.slack", engine="parakeet-unified"):
            return ns["compile_voice_evidence"](
                Recognition(text, confidence=0.9, engine=engine),
                bundle=bundle)[1].text

        class Delivered(BaseException):
            """Ends a pipeline run where the learner is handed its receipt."""

        def pipeline(text, edits=(), *, before="", bundle="com.slack"):
            """Run ``finish_and_process`` itself on recognized ``text`` with
            the focused field holding ``before``, then the learner it
            starts, over the scripted (seconds, old, new) edits. Returns the
            receipt the pipeline handed to the learner."""
            element = object()
            handed = []

            class Thread:
                def __init__(self, target=None, args=(), **_kwargs):
                    handed.append((target, args))

                def start(self):
                    raise Delivered()

            class Threading:
                def __getattr__(self, name):
                    return getattr(threading, name)

            fake_threading = Threading()
            fake_threading.Thread = Thread

            def stopped(_rec, _hud, _caption, log, **_kwargs):
                raise AssertionError(f"pipeline stopped early: {log}")

            rec = SimpleNamespace(
                released_at=clock[0], stop=lambda: np.zeros(
                    16_000, dtype=np.float32),
                cut_samples=0, chunks=[], speculative_future=None,
                speculative_invalid=False, speculative_start=0,
                speculative_end=0, prompt=None, language="en",
                process_ticket=None, mode="capture", bundle_at_press=bundle,
                context_terms=(), context_pack=None, focus_at_press=None,
                utterance_id=f"utterance-{clock[0]}", insertion_lease=None,
                insertion_receipt=None, recording=False, uncertain=False)
            fakes = {
                "threading": fake_threading,
                "report_dictation_problem": stopped,
                "peak_rms": lambda _audio: 0.0,
                "audio_gate_measurements": lambda _audio: (1.0, 0.1),
                "assemble_raw": lambda *_args: Recognition(
                    text, confidence=0.9, engine="parakeet-unified"),
                "DICTATION_PROCESS_ORDER": SimpleNamespace(
                    wait=lambda _ticket: None),
                "collapse_repeats": lambda raw: (raw, False),
                "runtime_consequence_evidence": lambda *_a, **_k: 0.0,
                "runtime_context_firewall_evidence": lambda *_a: 0.0,
                "CAPTION": {},
                "PIPELINE_STATE": {"last_consequence_route": "standard"},
                "_mask_snippets_inline": lambda raw, _snippets: (raw, {}),
                "extract_tone_override": lambda raw: (raw, None),
                "compile_cleanup": lambda raw, _language: SimpleNamespace(
                    text=raw, edits=[]),
                "cursor_context": lambda: before,
                "TONE": {"default": ""},
                "tone_for": lambda _bundle: "default",
                "quick_clean": lambda value, **_kwargs: value,
                "focused_snapshot": lambda: ns["FocusSnapshot"](
                    element=element, text=before,
                    selection=(len(before), 0)),
                "LAST_USE": {},
            }
            # Every other collaborator of the pipeline is inert here: it is
            # called, returns nothing, and so takes no branch of its own.
            pipeline_node = next(
                node for node in TREE.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "finish_and_process")
            for node in ast.walk(pipeline_node):
                if isinstance(node, ast.Name) and node.id not in ns \
                        and node.id not in fakes \
                        and not hasattr(builtins, node.id):
                    fakes[node.id] = lambda *_args, **_kwargs: None
            saved = {name: ns[name] for name in fakes if name in ns}
            ns.update(fakes)
            try:
                ns["finish_and_process"](rec, SimpleNamespace(), {})
            except Delivered:
                pass
            finally:
                for name in fakes:
                    ns.pop(name)
                ns.update(saved)
            target, (receipt,) = handed[0]
            current = before + rec.committed_text
            field["timeline"] = [(clock[0], current)]
            for delay, old, new in edits:
                current = current.replace(old, new, 1)
                field["timeline"].append((clock[0] + delay, current))
            target(receipt)
            return receipt

        return SimpleNamespace(
            ns=ns, clock=clock, field=field, observed=observed,
            dictate=dictate, compiled=compiled, pipeline=pipeline,
            state=lambda: ns["load_learned"](),
            snippets=lambda: json.loads(
                (root / "snippets.json").read_text(encoding="utf-8")),
        )

    def test_correction_after_a_finished_sentence_is_learned(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)

            receipt = runtime.dictate(
                "Ask Gwen about it.", [(3.0, "Gwen", "Qwen")],
                before="First sentence.")
            untouched = runtime.dictate(
                "Then ship it.", before="First sentence. Ask Qwen about it.")

            state = runtime.state()
        # The paste joined itself to the sentence before it with a space, and
        # the watcher has to expect that space instead of taking it for the
        # user's first edit.
        self.assertEqual(receipt.pasted, " Ask Gwen about it.")
        self.assertEqual(list(state["confusions"]), ["gwen->qwen"])
        self.assertEqual(runtime.observed, [
            (" Ask Gwen about it.", " Ask Qwen about it."),
            (untouched.pasted, untouched.pasted),
        ])

    def test_the_pipeline_hands_the_learner_the_text_it_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)

            receipt = runtime.pipeline(
                "Ask Gwen about it.", [(3.0, "Gwen", "Qwen")],
                before="First sentence.")
            untouched = runtime.pipeline(
                "Then ship it.", before="First sentence. Ask Qwen about it.")

            state = runtime.state()
        # finish_and_process builds the learner's receipt only once the
        # commit has decided what is inserted, joining space included.
        self.assertEqual(receipt.pasted, " Ask Gwen about it.")
        self.assertEqual(untouched.pasted, " Then ship it.")
        self.assertEqual(list(state["confusions"]), ["gwen->qwen"])
        self.assertEqual(runtime.observed, [
            (" Ask Gwen about it.", " Ask Qwen about it."),
            (" Then ship it.", " Then ship it."),
        ])

    def test_a_correction_typed_key_by_key_is_learned_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            # 150 ms per keystroke against the 200 ms poll: every poll during
            # the edit sees a different half-typed word.
            steps = ["kuberneti", "kubernet", "kuberne", "kubernet",
                     "kubernete", "kubernetes"]
            edits, last = [], "kubernetis"
            for index, step in enumerate(steps):
                edits.append((2.0 + 0.15 * index, last, step))
                last = step
            runtime.dictate("Ask Jon about the kubernetis rollout.", edits)
            # Select the word and type over it.
            runtime.dictate("Run Gwen locally.", [
                (2.0, "Gwen", "Q"), (2.15, "Q", "Qw"),
                (2.3, "Qw", "Qwe"), (2.45, "Qwe", "Qwen")])

            state = runtime.state()
        self.assertEqual(
            sorted(state["confusions"]),
            ["gwen->qwen", "kubernetis->kubernetes"])
        self.assertEqual(sorted(state["counts"]), ["Qwen", "kubernetes"])
        self.assertEqual(runtime.observed[0][1],
                         "Ask Jon about the kubernetes rollout.")
        self.assertEqual(runtime.observed[1][1], "Run Qwen locally.")

    def test_a_pause_in_the_middle_of_retyping_is_not_a_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)

            # The user stops for a second on half a word, late in the
            # window, and is typing again when the window closes.
            runtime.dictate("Restart kubernetis now.", [
                (8.4, "kubernetis", "kubernet"),
                (9.5, "kubernet", "kubernetes")])
            paused = runtime.state()
            for _ in range(2):
                runtime.dictate("Restart kubernetis now.",
                                [(3.0, "kubernetis", "kubernetes")])
            learned = runtime.compiled("Restart kubernetis now.")

        self.assertEqual(paused["confusions"], {})
        self.assertEqual(paused["counts"], {})
        self.assertEqual(runtime.observed[:1], [(
            "Restart kubernetis now.", "Restart kubernetes now.")])
        # Nothing stale stands in the way of the real correction.
        self.assertEqual(learned, "Restart kubernetes now.")

    def test_a_message_typed_after_sending_is_not_an_edit(self):
        message = "Thanks Gwen, see you at noon."
        following = "Thanks Owen, see you at one."
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)

            # Sent after two seconds; the next message is typed straight
            # into the emptied composer and then sits there.
            for _ in range(2):
                runtime.dictate(message, [
                    (2.0, message, ""), (2.3, "", "Thanks"),
                    (2.5, "Thanks", following)])

            state = runtime.state()
            lab = runtime.ns["GLOSS"]["regression"]
            compiled = runtime.compiled("Ask Gwen about it at noon.")
        self.assertEqual(runtime.observed, [])
        self.assertEqual(state["confusions"], {})
        self.assertEqual(state["counts"], {})
        self.assertEqual(lab.promoted, ())
        self.assertEqual(compiled, "Ask Gwen about it at noon.")

    def test_a_confirmed_correction_rewrites_on_the_parakeet_engine(self):
        codex, mail = "com.openai.codex", "com.apple.mail"
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            fix = [(3.0, "Gwen", "Qwen")]

            runtime.dictate("Run Gwen locally.", fix, bundle=codex)
            once = runtime.compiled("Run Gwen locally.", codex)
            runtime.dictate("Ask Gwen to rebase.", fix, bundle=codex)
            # Every dictation reloads the lab from disk; so does this.
            runtime.ns["refresh_glossary"]()
            twice = {
                (app, engine): runtime.compiled(
                    "Run Gwen locally.", app, engine)
                for app in (codex, mail)
                for engine in ("parakeet-unified", "turbo", "tiny")}
            runtime.dictate("Email Gwen today.", fix, bundle=mail)
            runtime.ns["refresh_glossary"]()
            thrice = {
                app: runtime.compiled("Gwen is ready. Ask Gwen.", app)
                for app in (codex, mail, "com.apple.Notes")}
            for _ in range(2):
                runtime.dictate(
                    "Restart kubernetis now.",
                    [(3.0, "kubernetis", "kubernetes")], bundle=codex)
            ordinary = runtime.compiled(
                "Kubernetis is down. Restart kubernetis.", codex)

        self.assertEqual(once, "Run Gwen locally.")
        for (app, engine), text in twice.items():
            with self.subTest(app=app, engine=engine):
                self.assertEqual(
                    text,
                    "Run Qwen locally." if app == codex
                    else "Run Gwen locally.")
        # The third correction promotes the mapping everywhere, and the app
        # that taught it keeps it.
        self.assertEqual(set(thrice.values()), {"Qwen is ready. Ask Qwen."})
        self.assertEqual(ordinary, "Kubernetes is down. Restart kubernetes.")

    def test_homophone_corrections_never_become_a_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)

            runtime.dictate("I think there car is outside.",
                            [(3.0, "there", "their")])
            runtime.dictate("Is there manager joining?",
                            [(3.0, "there", "their")])
            runtime.dictate("We can ship it today.",
                            [(3.0, "can", "can't")])
            runtime.dictate("We can make the release.",
                            [(3.0, "can", "can't")])
            # The same choices spelled without the apostrophe, a negation
            # flip and a change of tense are no more a mishearing.
            for heard, typed in (
                    ("can", "cant"), ("want", "won't"), ("got", "get"),
                    ("site", "sight")):
                runtime.dictate(f"I think {heard} it works.",
                                [(3.0, f" {heard} ", f" {typed} ")])
                runtime.dictate(f"Maybe {heard} we go.",
                                [(3.0, f" {heard} ", f" {typed} ")])

            state = runtime.state()
            gloss = runtime.ns["GLOSS"]
            for engine in ("parakeet-unified", "tiny"):
                for sentence in (
                        "Is there a problem over there?",
                        "Yes, we can ship it today.",
                        "I want to ship it today.",
                        "We got the build.",
                        "The site is down."):
                    with self.subTest(engine=engine, sentence=sentence):
                        self.assertEqual(
                            runtime.compiled(sentence, engine=engine),
                            sentence)
            self.assertEqual(
                runtime.ns["apply_vocabulary_casing"](
                    "Their plan is fine. Their team agreed."),
                "Their plan is fine. Their team agreed.")
        self.assertEqual(state["confusions"], {})
        self.assertEqual(state["counts"], {})
        self.assertEqual(gloss["regression"].promoted, ())
        self.assertIsNone(gloss["prompt"])
        self.assertEqual(gloss["anchor_pack"].candidates, ())

    def test_one_correction_does_not_promote_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            gloss = runtime.ns["GLOSS"]

            runtime.dictate("Run Gwen locally.", [(3.0, "Gwen", "Qwen")])
            after_one = (gloss["prompt"], gloss["anchor_pack"].candidates,
                         dict(gloss["vocabulary"]))
            runtime.dictate("Ask Gwen to rebase.", [(3.0, "Gwen", "Qwen")])

            self.assertEqual(after_one, (None, (), {}))
            self.assertEqual(gloss["prompt"], "Common terms: Qwen.")
            self.assertEqual(
                [item.text for item in gloss["anchor_pack"].candidates],
                ["Qwen"])

    def test_a_promotion_count_left_by_an_old_correction_is_inert(self):
        # Earlier versions set the target's count straight to the promotion
        # threshold, so a learned.json in the field still holds these.
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(
                directory, learned={"counts": {"their": 2, "The": 6}})
            gloss = runtime.ns["GLOSS"]

            self.assertIsNone(gloss["prompt"])
            self.assertEqual(gloss["anchor_pack"].candidates, ())
            self.assertEqual(
                runtime.ns["apply_vocabulary_casing"](
                    "Their plan is fine. the end."),
                "Their plan is fine. the end.")

    def test_reverting_a_learned_correction_demotes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            fix = [(3.0, "Mark", "Mary")]
            runtime.dictate("Call Mark tomorrow.", fix)
            runtime.dictate("Ask Mark about it.", fix)
            learned = runtime.compiled("Call Mark tomorrow.")

            # The mapping now writes "Mary"; the user changes it back.
            runtime.dictate(learned, [(3.0, "Mary", "Mark")])
            reverted = runtime.compiled("Call Mark tomorrow.")
            runtime.dictate("Ping Mark later.", fix)
            runtime.ns["refresh_glossary"]()
            still = runtime.compiled("Call Mark tomorrow.")
            state = runtime.state()

        self.assertEqual(learned, "Call Mary tomorrow.")
        self.assertEqual(reverted, "Call Mark tomorrow.")
        self.assertEqual(still, "Call Mark tomorrow.")
        # The revert is not learned as the opposite rule.
        self.assertEqual(list(state["confusions"]), ["mark->mary"])
        self.assertEqual(
            runtime.compiled("Call Mary tomorrow."), "Call Mary tomorrow.")

    def test_unedited_pastes_count_against_a_candidate_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            fix = [(3.0, "Mark", "Mary")]
            runtime.dictate("Call Mark tomorrow.", fix)
            # Twice the heard word is pasted and left alone for the whole
            # correction window.
            runtime.dictate("Mark is on call.")
            runtime.dictate("Thank Mark for me.")
            runtime.dictate("Ask Mark about it.", fix)
            held = runtime.compiled("Call Mark tomorrow.")
            kept = runtime.state()["confusions"]["mark->mary"]["kept"]
            runtime.dictate("Ping Mark later.", fix)
            active = runtime.compiled("Call Mark tomorrow.")

        self.assertEqual(kept, {"com.slack": 2})
        self.assertEqual(held, "Call Mark tomorrow.")
        self.assertEqual(active, "Call Mary tomorrow.")

    def test_messages_sent_unedited_count_against_a_candidate_correction(
            self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)

            def sent(text, fix=()):
                """Dictate into a chat composer that is emptied by the send,
                five seconds later."""
                final = text
                for _delay, old, new in fix:
                    final = final.replace(old, new, 1)
                return runtime.dictate(text, [*fix, (5.0, final, "")])

            fix = [(3.0, "Mark", "Mary")]
            sent("Call Mark tomorrow.", fix)
            # The heard word is dictated, read and sent as it is, twice.
            sent("Mark is on call.")
            sent("Thank Mark for me.")
            recorded = list(runtime.observed)
            sent("Ask Mark about it.", fix)
            held = runtime.compiled("Call Mark tomorrow.")
            kept = runtime.state()["confusions"]["mark->mary"]["kept"]
            sent("Ping Mark later.", fix)
            active = runtime.compiled("Call Mark tomorrow.")

        self.assertEqual(kept, {"com.slack": 2})
        self.assertEqual(held, "Call Mark tomorrow.")
        self.assertEqual(active, "Call Mary tomorrow.")
        # An emptied field is not an observed zero-edit outcome: it reads
        # the same when the user deletes the dictation.
        self.assertEqual(recorded, [
            ("Call Mark tomorrow.", "Call Mary tomorrow.")])

    def test_destination_edit_of_a_snippet_is_only_a_suggestion(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            ns, clock, field = runtime.ns, runtime.clock, runtime.field
            (Path(directory) / "snippets.json").write_text(json.dumps({
                "email": "john@example.com",
                "bank details": "GB29 NWBK 6016 1331 9268 19",
            }))

            def insert(name, before, edits=()):
                """Paste a saved snippet after ``before`` and run the
                observer over the scripted (seconds, old, new) edits."""
                snippet = runtime.snippets()[name]
                ns["focused_snapshot"] = lambda: ns["FocusSnapshot"](
                    element=object(), text=before,
                    selection=(len(before), 0))
                rec = SimpleNamespace(
                    insertion_lease=None, insertion_receipt=None,
                    language="en")
                started = []
                receipt = ns["paste_snippet_and_watch"](
                    name, snippet, "com.google.Chrome", "capture",
                    starter=lambda target, args:
                        started.append((target, args)),
                    rec=rec)
                current = before + rec.committed_text
                field["timeline"] = [(clock[0], current)]
                for delay, old, new in edits:
                    current = current.replace(old, new, 1)
                    field["timeline"].append((clock[0] + delay, current))
                target, args = started[0]
                target(*args)
                return receipt

            # Pasted after "Email:" the snippet gains a joining space. Left
            # alone, that space must not come back as an edit of the snippet.
            receipt = insert("email", "Email:")
            self.assertEqual(receipt.pasted, " john@example.com")
            self.assertEqual(runtime.state()["snippet_edits"], {})

            # A field that reformats its own content, or a user fixing the
            # address: either way the saved snippet is not rewritten.
            insert("email", "Email:", [(0.5, "john@", "jon@")])
            insert("bank details", "",
                   [(0.5, "GB29 NWBK 6016 1331 9268 19",
                     "GB33 EVIL 0000 0000 0000 00")])
            self.assertEqual(runtime.snippets(), {
                "email": "john@example.com",
                "bank details": "GB29 NWBK 6016 1331 9268 19",
            })
            self.assertEqual(runtime.state()["snippet_edits"]["email"], {
                "suggested": {
                    "from": "john@example.com", "to": "jon@example.com",
                    "app": "com.google.Chrome"},
            })

            # The user accepts one suggestion by name and dismisses the other.
            self.assertTrue(ns["apply_pending_snippet_edit"]("email"))
            self.assertFalse(ns["apply_pending_snippet_edit"]("email"))
            self.assertTrue(ns["forget_snippet_edit"]("bank details"))
            self.assertFalse(ns["apply_pending_snippet_edit"]("bank details"))
            self.assertEqual(runtime.snippets(), {
                "email": "jon@example.com",
                "bank details": "GB29 NWBK 6016 1331 9268 19",
            })
            applied = runtime.state()
            self.assertEqual(applied["snippet_edits"], {"email": {
                "from": "john@example.com", "to": "jon@example.com",
                "n": 1, "app": "com.google.Chrome",
            }})
            # Nothing in history repeats a snippet's text.
            self.assertNotIn("example.com", json.dumps(applied["history"]))
            self.assertNotIn("GB", json.dumps(applied["history"]))

    def test_a_stale_snippet_suggestion_is_dropped_not_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            ns = runtime.ns
            snippets = Path(directory) / "snippets.json"
            snippets.write_text(json.dumps({"email": "john@example.com"}))
            self.assertTrue(ns["suggest_snippet_edit"](
                "email", "john@example.com", "jon@example.com", "com.slack"))
            # The user edits the snippet in Settings before deciding.
            snippets.write_text(json.dumps({"email": "j.doe@example.com"}))

            self.assertFalse(ns["apply_pending_snippet_edit"]("email"))

            self.assertEqual(
                runtime.snippets(), {"email": "j.doe@example.com"})
            self.assertEqual(runtime.state()["snippet_edits"], {})

    def test_a_snippet_suggestion_keeps_the_record_of_an_applied_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.learning_runtime(directory)
            ns = runtime.ns
            snippets = Path(directory) / "snippets.json"
            snippets.write_text(json.dumps({"email": "john@example.com"}))
            ns["suggest_snippet_edit"](
                "email", "john@example.com", "jon@example.com", "com.slack")
            self.assertTrue(ns["apply_pending_snippet_edit"]("email"))
            applied = {"from": "john@example.com", "to": "jon@example.com",
                       "n": 1, "app": "com.slack"}

            # A later observation waits beside the applied edit's record.
            ns["suggest_snippet_edit"](
                "email", "jon@example.com", "evil@example.net", "com.slack")
            self.assertEqual(runtime.state()["snippet_edits"]["email"], {
                **applied, "suggested": {
                    "from": "jon@example.com", "to": "evil@example.net",
                    "app": "com.slack"}})
            # While snippets.json cannot be read the suggestion cannot be
            # judged stale, so it is neither applied nor dropped.
            snippets.write_text("{", encoding="utf-8")
            self.assertFalse(ns["apply_pending_snippet_edit"]("email"))
            self.assertIn(
                "suggested", runtime.state()["snippet_edits"]["email"])
            snippets.write_text(json.dumps({"email": "jon@example.com"}))

            # Forget still undoes the applied edit, and the suggestion goes
            # with it.
            self.assertTrue(ns["forget_snippet_edit"]("email"))
            self.assertEqual(runtime.snippets(), {"email": "john@example.com"})
            self.assertEqual(runtime.state()["snippet_edits"], {})
            # Forgetting a suggestion for a snippet that is gone creates
            # nothing.
            ns["suggest_snippet_edit"](
                "email", "john@example.com", "jon@example.com", "com.slack")
            snippets.write_text("{}", encoding="utf-8")
            self.assertTrue(ns["forget_snippet_edit"]("email"))
            self.assertEqual(runtime.snippets(), {})

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
            "paste_snippet_and_watch", "committed_paste_receipt",
            extra={
                "threading": threading,
                "focused_snapshot": lambda: events.append("snapshot") or
                    "focus",
                "make_paste_receipt":
                    lambda snapshot, pasted, bundle, mode, event_id="":
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
        self.assertEqual(events[2][2], (
            "address", receipt, "EDIT ME: your street address"))

    def test_snippet_observer_suggests_the_revised_insertion(self):
        saved = []
        ns = load_definitions(
            "learn_snippet_edit",
            extra={
                "observe_paste_outcome": lambda _receipt:
                    "7623 Opal Ridge Lane, Bainbridge Island, 98110 WA",
                "suggest_snippet_edit": lambda name, old, new, bundle:
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
            "_learned_count",
            assignments={"CORRECTION_STABLE_POLLS"},
            extra={
                "Path": Path,
                "PasteReceipt": object,
                "atomic_write_text": lambda path, value: path.write_text(value),
                "time": fake_time,
                "difflib": __import__("difflib"),
                "correction_similarity": lambda _old, _new: 0.8,
                "infer_revised_insertion": infer_revised_insertion,
                "parse_dictionary": lambda: ([], set()),
                "utterance_was_undone": lambda _event_id: False,
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
        # The correction is made at once, holds for a second, and the chat
        # composer is then cleared by the user submitting the message.
        def corrected_then_submitted():
            started = elapsed[0]
            return lambda _element: (
                "Hello Gwen world" if elapsed[0] == started
                else "Hello Qwen world" if elapsed[0] - started < 1.5
                else "")

        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "learned.json"
            ns.update({
                "LEARNED_FILE": learned,
                "LEARN_LOCK": threading.Lock(),
                "CORRECTION_DELAY": 10.0,
                "CORRECTION_POLL_INTERVAL": 0.2,
                "CORRECTION_MAX_LEARN": 3,
                "PROMOTE_MIN_COUNT": 2,
                "_ax_text": corrected_then_submitted(),
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
            ns["_ax_text"] = corrected_then_submitted()
            ns["learn_from_corrections"](receipt)

            state = json.loads(learned.read_text())
            ns["_ax_text"] = corrected_then_submitted()
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
            assignments={"CORRECTION_STABLE_POLLS"},
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
        elapsed = [0.0]

        def observe(field_at):
            elapsed[0] = 0.0
            return ns["observe_paste_outcome"](
                receipt,
                timeout=10.0,
                poll_interval=0.2,
                reader=lambda _element: field_at(elapsed[0]),
                clock=lambda: elapsed[0],
                sleeper=lambda seconds: elapsed.__setitem__(
                    0, elapsed[0] + seconds),
            )

        # Corrected, left alone for a second, then submitted: the composer
        # clears, and the settled correction is still reported, early.
        observed = observe(lambda at: (
            "Hello Gwen world" if at < 0.2
            else "Hello Qwen world" if at < 1.5 else ""))
        self.assertEqual(observed, "Qwen")
        self.assertLess(elapsed[0], 10.0)
        # Submitted in the middle of retyping the word: the last read before
        # the composer cleared was half a word, and it is not reported.
        self.assertIsNone(observe(lambda at: (
            "Hello Gwen world" if at < 0.2
            else "Hello Gw world" if at < 0.4
            else "Hello Qw world" if at < 0.6 else "")))

    def test_only_a_settled_edit_of_the_pasted_range_is_reported(self):
        ns = load_definitions(
            "observe_paste_outcome",
            assignments={"CORRECTION_STABLE_POLLS"},
            extra={"PasteReceipt": object},
        )
        document = "lorem ipsum dolor sit amet " * 800
        pasted = " Ask Gwen about it."
        receipt = SimpleNamespace(
            element=object(), before=document,
            selection=(len(document), 0), pasted=pasted)
        elapsed = [0.0]
        compared = []

        def infer(before, selection, text, current):
            compared.append(len(before) + len(current))
            return infer_revised_insertion(before, selection, text, current)

        ns["infer_revised_insertion"] = infer

        def observe(field_at):
            elapsed[0] = 0.0
            return ns["observe_paste_outcome"](
                receipt,
                reader=lambda _element: field_at(elapsed[0]),
                timeout=10.0, poll_interval=0.2,
                clock=lambda: elapsed[0],
                sleeper=lambda seconds: elapsed.__setitem__(
                    0, elapsed[0] + seconds),
            )

        # The user keeps typing after the paste for the whole window. The
        # pasted range itself never changes, so that is a zero-edit paste,
        # and no poll compares more than the range and a bounded tail.
        typed = " and then some more typing that goes on for a while"
        self.assertEqual(
            observe(lambda at: document + pasted
                    + typed[:int(at * 5) % len(typed)]),
            pasted)
        self.assertTrue(compared)
        self.assertLess(max(compared), 600)
        # An edit still in progress when the window closes is not reported
        # as anything, least of all as an unchanged paste.
        self.assertIsNone(observe(lambda at: (
            document + pasted if at < 9.7
            else document + " Ask Gw about it.")))
        # Nor is the half word the user paused on before going on typing.
        self.assertIsNone(observe(lambda at: (
            document + pasted if at < 8.4
            else document + " Ask Gw about it." if at < 9.5
            else document + " Ask Qwen about it.")))
        # An edit that settled and was then taken back is a zero-edit paste.
        self.assertEqual(
            observe(lambda at: (
                document + " Ask Qwen about it." if 2.0 <= at < 5.0
                else document + pasted)),
            pasted)

    def test_cleared_chat_composer_is_not_reported_as_unchanged(self):
        ns = load_definitions(
            "observe_paste_outcome",
            assignments={"CORRECTION_STABLE_POLLS"},
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

    def test_an_emptied_composer_ends_the_observation(self):
        ns = load_definitions(
            "observe_paste_outcome", "SentUnchanged",
            assignments={"CORRECTION_STABLE_POLLS"},
            extra={
                "PasteReceipt": object,
                "infer_revised_insertion": infer_revised_insertion,
            },
        )
        message = "Thanks Gwen, see you at noon."
        receipt = SimpleNamespace(
            element=object(), before="", selection=(0, 0), pasted=message)
        elapsed = [0.0]

        def observe(field_at):
            elapsed[0] = 0.0
            return ns["observe_paste_outcome"](
                receipt, timeout=10.0, poll_interval=0.2,
                reader=lambda _element: field_at(elapsed[0]),
                clock=lambda: elapsed[0],
                sleeper=lambda seconds: elapsed.__setitem__(
                    0, elapsed[0] + seconds),
            )

        # Sent unedited, and the next message typed at once: the watcher
        # stops at the send and never reads the next message.
        observed = observe(lambda at: (
            message if at < 2.0 else "" if at < 2.3
            else "Thanks Owen, see you at one."))
        self.assertEqual(observed, message)
        self.assertTrue(observed.sent)
        self.assertLess(elapsed[0], 2.3)
        # A composer that reads as a lone newline when empty is as empty.
        self.assertTrue(observe(
            lambda at: message if at < 2.0 else "\n").sent)
        # Sent while a word was being retyped, after a pause on half of it.
        self.assertIsNone(observe(lambda at: (
            message if at < 1.0
            else "Thanks Gw, see you at noon." if at < 2.4
            else "Thanks Qwe, see you at noon." if at < 2.6 else "")))
        # A read that fails for a moment is not an emptied field.
        self.assertEqual(observe(lambda at: (
            None if 2.0 <= at < 2.5
            else message if at < 3.0
            else "Thanks Qwen, see you at noon.")),
            "Thanks Qwen, see you at noon.")

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
            "capture_insertion_lease", "unresolved_destination_id",
            "destination_observation",
            "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
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
            "destination_observation", "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
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
            "destination_observation", "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
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
            "destination_observation", "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
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
            "destination_observation", "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
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
            "destination_observation", "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
            extra={
                "FocusSnapshot": object,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "_ax_elements_equal": lambda left, right:
                    (left is original_element and right is current_element),
                "insertion_readback": lambda *_args, **_kwargs:
                    ReadbackResult.verified(),
                "READBACK_TIMEOUT": 0.02,
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
            assignments={"READBACK_TIMEOUT"},
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

    def commit_with_lagging_value(self, lag):
        """Commit into a field whose Accessibility value trails the paste.

        Chromium browsers and Electron apps both publish late, and nothing
        about the app is consulted: every destination gets the same window.
        """
        import unicodedata
        now = [0.0]
        field = {"text": "Hello", "pasted_at": None}

        def paste(text):
            field["pasted_at"] = now[0]
            field["text"] = "Hello" + text

        def ax_text(_element):
            if (field["pasted_at"] is None or lag is None
                    or now[0] < field["pasted_at"] + lag):
                return "Hello"
            return field["text"]

        lease = InsertionLease.capture(
            "utterance-1", "field-a", (5, 0), "Hello")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None,
            bundle_at_press="com.google.Chrome", language="en")
        ns = load_definitions(
            "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix", "unresolved_destination_id",
            "insertion_readback", "classify_readback_conflict",
            assignments={"READBACK_TIMEOUT"},
            extra={
                "INSERTION_COORDINATOR": InsertionCoordinator(),
                "destination_observation": lambda *_args:
                    DestinationObservation.capture(
                        "field-a", (5, 0), "Hello"),
                "ReadbackResult": ReadbackResult,
                "unicodedata": unicodedata,
                "_ax_text": ax_text,
                "paste": paste,
                "PIPELINE_STATE": {},
                "frontmost_bundle": lambda: "com.google.Chrome",
                "time": SimpleNamespace(
                    monotonic=lambda: now[0],
                    sleep=lambda delay: now.__setitem__(0, now[0] + delay)),
            },
        )
        receipt = ns["commit_insertion"](
            rec, "World.", "com.google.Chrome", SimpleNamespace(
                element=object(), text="Hello", selection=(5, 0)))
        return receipt, now[0]

    def test_a_value_published_late_still_verifies_in_any_app(self):
        receipt, waited = self.commit_with_lagging_value(0.15)

        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        # Returned as soon as the value matched, not at the end of the window.
        self.assertLess(waited, 0.2)

    def test_a_value_published_at_once_is_read_once(self):
        receipt, waited = self.commit_with_lagging_value(0.0)

        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(waited, 0.0)

    def test_a_value_that_never_matches_fails_within_the_window(self):
        receipt, waited = self.commit_with_lagging_value(None)

        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertGreaterEqual(waited, 0.35)
        self.assertLessEqual(waited, 0.45)

    def test_hidden_field_lease_still_binds_to_original_application(self):
        ns = load_definitions(
            "focus_destination_id", "opaque_focus_context",
            "capture_insertion_lease", "unresolved_destination_id",
            "destination_observation",
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
            "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
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

    def capability_namespace(self, coordinator, **extra):
        return load_definitions(
            "commit_insertion", "insertion_capability_buckets",
            "insertion_join_prefix",
            "unresolved_destination_id",
            extra={
                "INSERTION_COORDINATOR": coordinator,
                "destination_observation": lambda *_args:
                    DestinationObservation.capture(
                        "field-a", (0, 0), "nearby"),
                "insertion_readback": lambda *_args, **_kwargs:
                    ReadbackResult.verified(),
                "READBACK_TIMEOUT": 0.02,
                "paste": lambda _text: None,
                "PIPELINE_STATE": {},
                "frontmost_bundle": lambda: "com.openai.codex",
                **extra,
            },
        )

    def test_a_readable_field_reports_the_full_capability_triple(self):
        lease = InsertionLease.capture(
            "utterance-1", "field-a", (0, 0), "nearby")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None,
            bundle_at_press="com.openai.codex")
        ns = self.capability_namespace(InsertionCoordinator())

        ns["commit_insertion"](rec, "Ship it", "com.openai.codex", object())

        self.assertEqual(rec.insertion_capabilities, {
            "target": "readable", "paste": "available",
            "readback": "available"})

    def test_an_opaque_lease_reports_opaque_target_and_no_readback(self):
        lease = InsertionLease.capture_opaque(
            "utterance-2", "com.apple.Terminal:window-7", "context")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None,
            bundle_at_press="com.apple.Terminal")
        ns = self.capability_namespace(InsertionCoordinator())

        ns["commit_insertion"](rec, "Ship it", "com.apple.Terminal", None)

        self.assertEqual(rec.insertion_capabilities, {
            "target": "opaque", "paste": "available",
            "readback": "unavailable"})

    def test_an_unresolved_destination_reports_an_unavailable_target(self):
        ns = self.capability_namespace(InsertionCoordinator())
        lease = InsertionLease.capture_opaque(
            "utterance-3",
            ns["unresolved_destination_id"]("com.unknown.app", "utterance-3"),
            "unavailable")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None,
            bundle_at_press="com.unknown.app")

        ns["commit_insertion"](rec, "Ship it", "com.unknown.app", None)

        self.assertEqual(rec.insertion_capabilities["target"], "unavailable")

    def test_a_second_stage_reports_that_no_paste_path_existed(self):
        coordinator = InsertionCoordinator()
        lease = InsertionLease.capture(
            "utterance-4", "field-a", (0, 0), "nearby")
        coordinator.stage(lease, "already staged")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None,
            bundle_at_press="com.openai.codex")
        ns = self.capability_namespace(coordinator)

        ns["commit_insertion"](rec, "Ship it", "com.openai.codex", object())

        self.assertEqual(rec.insertion_capabilities["paste"], "unavailable")

    def test_a_readback_shape_never_outlives_its_utterance(self):
        ns = self.capability_namespace(
            InsertionCoordinator(),
            insertion_readback=lambda *_args, **_kwargs:
                ReadbackResult.conflict("divergent"),
        )
        readable = SimpleNamespace(
            insertion_lease=InsertionLease.capture(
                "utterance-1", "field-a", (0, 0), "nearby"),
            insertion_receipt=None, bundle_at_press="com.openai.codex")
        first = ns["commit_insertion"](
            readable, "Ship it", "com.openai.codex", object())
        self.assertEqual(first.readback_shape, "divergent")

        # Refused before the paste: no readback ran, so there is no shape.
        drifted = SimpleNamespace(
            insertion_lease=InsertionLease.capture_opaque(
                "utterance-2", "com.apple.Terminal:window-7", "context"),
            insertion_receipt=None, bundle_at_press="com.apple.Terminal")
        receipt = ns["commit_insertion"](
            drifted, "Ship it", "com.apple.Terminal", None)
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(receipt.readback_shape, "")

        # A re-insert from the menu commits outside the release order, so it
        # can land between a dictation's readback and its transcript record.
        repaste = SimpleNamespace(
            insertion_lease=InsertionLease.capture_opaque(
                "repaste:1", "field-a", "nearby"),
            insertion_receipt=None, bundle_at_press="com.openai.codex")
        ns["commit_insertion"](repaste, "Ship it", "com.openai.codex", None)
        self.assertEqual(readable.insertion_receipt.readback_shape,
                         "divergent")

        legacy = SimpleNamespace(insertion_lease=None, insertion_receipt=None)
        self.assertIsNone(ns["commit_insertion"](
            legacy, "Ship it", "com.openai.codex", None))

    def test_the_legacy_path_reports_no_capabilities_at_all(self):
        rec = SimpleNamespace(insertion_lease=None, insertion_receipt=None)
        ns = self.capability_namespace(InsertionCoordinator())

        self.assertIsNone(
            ns["commit_insertion"](rec, "Ship it", "com.openai.codex", None))
        self.assertIsNone(rec.insertion_capabilities)

    def test_every_reported_bucket_is_in_the_aggregator_vocabulary(self):
        ns = self.capability_namespace(InsertionCoordinator())
        for lease, snapshot, expected_target in (
            (InsertionLease.capture("u1", "field", (0, 0), "near"),
             object(), "readable"),
            (InsertionLease.capture_opaque("u2", "bundle:window", "ctx"),
             None, "opaque"),
        ):
            rec = SimpleNamespace(bundle_at_press="com.openai.codex")
            buckets = ns["insertion_capability_buckets"](
                rec, lease, snapshot, paste_available=True)
            self.assertIn(buckets["target"], TARGET_BUCKETS)
            self.assertIn(buckets["paste"], AVAILABILITY_BUCKETS)
            self.assertIn(buckets["readback"], AVAILABILITY_BUCKETS)
            self.assertEqual(buckets["target"], expected_target)

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


class AccessibilityRangeTests(unittest.TestCase):
    """Accessibility counts UTF-16 units; the field text counts code points."""

    BUNDLE = "com.apple.Notes"
    APPLICATION_SERVICES = SimpleNamespace(
        AXValueGetType=lambda _value: None,
        AXValueGetValue=lambda *_args: None,
        kAXSelectedTextRangeAttribute="AXSelectedTextRange",
        kAXSelectedTextAttribute="AXSelectedText",
        kAXValueCFRangeType=4,
    )

    @staticmethod
    def utf16_units(text):
        return len(text.encode("utf-16-le")) // 2

    def field(self, text, caret):
        """A field that reports its caret the way macOS does, in UTF-16."""
        field = {"text": text, "caret": caret, "element": object()}

        def ax_attribute(_element, attribute):
            if attribute == "AXSelectedTextRange":
                return (self.utf16_units(field["text"][:field["caret"]]), 0)
            return None

        def paste(inserted):
            # The application inserts at its real caret, which is where the
            # user sees it, whatever unit Accessibility reports it in.
            field["text"] = (field["text"][:field["caret"]] + inserted
                             + field["text"][field["caret"]:])
            field["caret"] += len(inserted)

        field["ax_attribute"] = ax_attribute
        field["paste"] = paste
        return field

    def namespace(self, field, coordinator=None):
        import unicodedata
        return load_definitions(
            "_ax_selection", "_ax_reported_range", "_coerce_range",
            "utf16_range_to_code_points", "FocusSnapshot",
            "bounded_focus_text", "focus_destination_id",
            "focus_destination_matches", "opaque_focus_context",
            "capture_insertion_lease", "unresolved_destination_id",
            "destination_observation", "commit_insertion",
            "insertion_capability_buckets", "insertion_join_prefix",
            "insertion_readback", "classify_readback_conflict",
            extra={
                "dataclass": dataclass,
                "time": time,
                "unicodedata": unicodedata,
                "InsertionLease": InsertionLease,
                "DestinationObservation": DestinationObservation,
                "INSERTION_COORDINATOR": coordinator or InsertionCoordinator(),
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "_ax_attribute": field["ax_attribute"],
                "_ax_text": lambda _element: field["text"],
                "_ax_elements_equal": lambda left, right: left is right,
                "frontmost_window_destination": lambda _bundle: None,
                "frontmost_bundle": lambda: self.BUNDLE,
                "READBACK_TIMEOUT": 0.0,
                "paste": field["paste"],
                "PIPELINE_STATE": {},
            },
        )

    def snapshot(self, ns, field):
        with mock.patch.dict(sys.modules, {
                "ApplicationServices": self.APPLICATION_SERVICES}):
            selection = ns["_ax_selection"](field["element"], field["text"])
        return ns["FocusSnapshot"](
            element=field["element"], text=field["text"], selection=selection)

    def dictate(self, text, caret, words):
        """Lease at press, then commit at release, as the runtime does."""
        field = self.field(text, caret)
        ns = self.namespace(field)
        at_press = self.snapshot(ns, field)
        lease = ns["capture_insertion_lease"](
            at_press, self.BUNDLE, "utterance-1")
        rec = SimpleNamespace(
            insertion_lease=lease, insertion_receipt=None,
            focus_at_press=at_press, bundle_at_press=self.BUNDLE,
            language="en")
        receipt = ns["commit_insertion"](
            rec, words, self.BUNDLE, self.snapshot(ns, field))
        return lease, receipt, field["text"]

    def test_an_emoji_before_a_trailing_caret_still_verifies(self):
        lease, receipt, result = self.dictate(
            "Sounds good \U0001F44D", len("Sounds good \U0001F44D"),
            "See you soon.")

        self.assertFalse(lease.opaque)
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(result, "Sounds good \U0001F44D See you soon.")

    def test_a_caret_mid_text_after_an_emoji_joins_and_verifies(self):
        text = "\U0001F389 Great news, the build is green."
        caret = text.index(" the build")
        lease, receipt, result = self.dictate(text, caret, "Ship it.")

        self.assertFalse(lease.opaque)
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        # The character left of the caret is the comma, so one space joins.
        self.assertEqual(
            result,
            "\U0001F389 Great news, Ship it. the build is green.")

    def test_a_joined_emoji_sequence_before_the_caret_verifies(self):
        family = "\U0001F468‍\U0001F469‍\U0001F467"
        text = f"Family photo {family} and \U0001F44D\U0001F3FD"
        lease, receipt, result = self.dictate(text, len(text), "Lovely.")

        self.assertFalse(lease.opaque)
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(result, text + " Lovely.")

    def test_a_field_without_emoji_still_verifies(self):
        lease, receipt, result = self.dictate(
            "Sounds good.", len("Sounds good."), "See you soon.")

        self.assertFalse(lease.opaque)
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(result, "Sounds good. See you soon.")

    def test_a_selection_over_an_emoji_covers_one_code_point(self):
        field = self.field("Ok \U0001F44D!", 3)
        ns = self.namespace(field)
        convert = ns["utf16_range_to_code_points"]

        self.assertEqual(convert("Ok \U0001F44D!", (3, 2)), (3, 1))
        self.assertEqual(convert("Ok \U0001F44D!", (5, 1)), (4, 1))

    def test_a_range_that_does_not_fit_the_text_is_withheld(self):
        field = self.field("Ok \U0001F44D!", 3)
        ns = self.namespace(field)
        convert = ns["utf16_range_to_code_points"]

        # Inside the surrogate pair, and past the end of the text: neither
        # describes this text, so nothing downstream may slice with it.
        self.assertIsNone(convert("Ok \U0001F44D!", (4, 0)))
        self.assertIsNone(convert("Ok \U0001F44D!", (7, 0)))
        field["ax_attribute"] = lambda _element, attribute: (
            (9, 0) if attribute == "AXSelectedTextRange" else None)
        ns["_ax_attribute"] = field["ax_attribute"]
        snapshot = self.snapshot(ns, field)
        self.assertIsNone(snapshot.selection)
        lease = ns["capture_insertion_lease"](
            snapshot, self.BUNDLE, "utterance-1")
        self.assertTrue(lease.opaque)


class ElectronAccessibilityTests(unittest.TestCase):
    def _classifier(self):
        return load_definitions(
            "is_electron_app", "_bundle_is_electron",
            assignments={"_ELECTRON_BUNDLE_IDS", "_ELECTRON_BUNDLE_CACHE"},
            extra={"IS_MACOS": True, "Path": Path},
        )

    def _wake_retry(self):
        # electron_wake_retry binds its detector/waker/text_reader defaults at
        # def-execution time, so those module globals must exist in the
        # namespace even though every test overrides them per call.
        return load_definitions(
            "electron_wake_retry", "_focus_read_is_empty",
            extra={
                "is_electron_app": lambda app: False,
                "wake_electron_accessibility": lambda pid: True,
                "_ax_text": lambda element: None,
            },
        )

    @staticmethod
    def _reader(*reads):
        calls = []

        def reader():
            calls.append(True)
            return reads[min(len(calls) - 1, len(reads) - 1)]

        return reader, calls

    def test_allowlisted_bundle_is_electron_without_touching_the_filesystem(
            self):
        ns = self._classifier()
        app = SimpleNamespace(
            bundleIdentifier=lambda: "com.anthropic.claudefordesktop",
            bundleURL=lambda: None,
            processIdentifier=lambda: 501,
        )
        self.assertTrue(ns["is_electron_app"](app))
        # The allowlist short-circuits before any bundle path is inspected, so
        # even a path that does not exist stays classified as Electron.
        self.assertTrue(
            ns["_bundle_is_electron"](
                "com.anthropic.claudefordesktop", "/nonexistent/Claude.app"))

    def test_framework_probe_follows_the_electron_framework_directory(self):
        ns = load_definitions(
            "_bundle_is_electron",
            assignments={"_ELECTRON_BUNDLE_IDS"},
            extra={"Path": Path},
        )
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "Third Party.app"
            bundle.mkdir()
            # No framework present: a non-allowlisted bundle is not Electron.
            self.assertFalse(
                ns["_bundle_is_electron"]("com.example.other", str(bundle)))
            framework = bundle / "Contents" / "Frameworks" \
                / "Electron Framework.framework"
            framework.mkdir(parents=True)
            self.assertTrue(
                ns["_bundle_is_electron"]("com.example.other", str(bundle)))

    def test_native_bundle_id_is_not_electron(self):
        ns = self._classifier()
        app = SimpleNamespace(
            bundleIdentifier=lambda: "com.apple.Notes",
            bundleURL=lambda: None,
            processIdentifier=lambda: 777,
        )
        self.assertFalse(ns["is_electron_app"](app))
        self.assertFalse(ns["_bundle_is_electron"]("com.apple.Notes", None))

    def test_verdict_is_memoized_by_bundle_id(self):
        ns = self._classifier()
        app = SimpleNamespace(
            bundleIdentifier=lambda: "com.anthropic.claudefordesktop",
            bundleURL=lambda: None,
            processIdentifier=lambda: 501,
        )
        self.assertTrue(ns["is_electron_app"](app))
        self.assertEqual(
            ns["_ELECTRON_BUNDLE_CACHE"],
            {"com.anthropic.claudefordesktop": True})

        # A later lookup returns the cached verdict without re-probing: the
        # bundle URL, which would raise, is never consulted a second time.
        def exploding_url():
            raise RuntimeError("bundleURL should not be consulted again")

        cached_app = SimpleNamespace(
            bundleIdentifier=lambda: "com.anthropic.claudefordesktop",
            bundleURL=exploding_url,
            processIdentifier=lambda: 501,
        )
        self.assertTrue(ns["is_electron_app"](cached_app))

    def test_empty_electron_read_wakes_once_and_reads_again(self):
        ns = self._wake_retry()
        reader, calls = self._reader((0, "first-el"), (0, "second-el"))
        waker_pids = []

        def waker(pid):
            waker_pids.append(pid)
            return True

        app = SimpleNamespace(processIdentifier=lambda: 4242)
        err, focused = ns["electron_wake_retry"](
            reader, app,
            detector=lambda a: True,
            waker=waker,
            text_reader=lambda element: None,
        )

        self.assertEqual(waker_pids, [4242])
        self.assertEqual(len(calls), 2)
        self.assertEqual((err, focused), (0, "second-el"))

    def test_empty_non_electron_read_does_not_wake(self):
        ns = self._wake_retry()
        reader, calls = self._reader((0, "only-el"))
        waker_pids = []
        app = SimpleNamespace(processIdentifier=lambda: 9)
        err, focused = ns["electron_wake_retry"](
            reader, app,
            detector=lambda a: False,
            waker=lambda pid: waker_pids.append(pid),
            text_reader=lambda element: None,
        )

        self.assertEqual(waker_pids, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual((err, focused), (0, "only-el"))

    def test_non_empty_read_returns_without_waking(self):
        ns = self._wake_retry()
        reader, calls = self._reader((0, "editor"))
        waker_pids = []
        app = SimpleNamespace(processIdentifier=lambda: 9)
        err, focused = ns["electron_wake_retry"](
            reader, app,
            detector=lambda a: True,
            waker=lambda pid: waker_pids.append(pid),
            text_reader=lambda element: "typed text",
        )

        self.assertEqual(waker_pids, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual((err, focused), (0, "editor"))

    def test_focus_read_emptiness_covers_error_missing_and_unreadable(self):
        ns = self._wake_retry()
        probed = []

        def text_reader(element):
            probed.append(element)
            return None if element == "blank" else "readable"

        is_empty = ns["_focus_read_is_empty"]
        # An errored read is empty and never consults the text reader.
        self.assertTrue(is_empty(1, "ignored", text_reader))
        # A missing element is empty without consulting the text reader.
        self.assertTrue(is_empty(0, None, text_reader))
        self.assertEqual(probed, [])
        # Present but unreadable is empty; a readable element is not.
        self.assertTrue(is_empty(0, "blank", text_reader))
        self.assertFalse(is_empty(0, "field", text_reader))
        self.assertEqual(probed, ["blank", "field"])


class PersonalPriorIntegrationTests(unittest.TestCase):
    def test_only_a_mapping_the_lab_promoted_is_applied(self):
        lab = PersonalRegressionLab()
        lab.record_correction("gwen", "qwen", app="com.openai.codex")
        ns = load_definitions(
            "apply_learned_fixes", "compiler_personal_priors",
            extra={
                "GLOSS": {
                    "lock": threading.Lock(),
                    # Counts the lab never promoted are not a second way in.
                    "fixes": {"gwen": "qwen"},
                    "confusions": {"gwen->qwen": {
                        "from": "gwen", "to": "qwen", "n": 9,
                        "apps": {"com.openai.codex": 9}}},
                    "regression": lab,
                },
                "PersonalPrior": __import__(
                    "voice_compiler").PersonalPrior,
            },
        )

        self.assertEqual(
            ns["apply_learned_fixes"]("Use gwen", "com.openai.codex"),
            "Use gwen")
        self.assertEqual(
            ns["compiler_personal_priors"]("com.openai.codex"), ())
        lab.propose("gwen", "qwen", app="com.openai.codex")
        self.assertEqual(
            ns["apply_learned_fixes"](
                "Gwen is up. Use gwen.", "com.openai.codex"),
            "Qwen is up. Use qwen.")
        self.assertEqual(
            ns["apply_learned_fixes"]("Use gwen", "com.apple.Notes"),
            "Use gwen")

    def test_a_common_word_pair_is_never_offered_as_an_alternative(self):
        ns = load_definitions(
            "learned_alternatives",
            extra={
                "GLOSS": {
                    "lock": threading.Lock(),
                    "confusions": {
                        "there->their": {
                            "from": "there", "to": "their", "n": 2},
                        "gwen->qwen": {"from": "Gwen", "to": "Qwen", "n": 1},
                    },
                },
                "PersonalRegressionLab": PersonalRegressionLab,
            },
        )

        self.assertEqual(
            ns["learned_alternatives"](
                "Is there a Gwen over there?", "com.slack"),
            ["Is there a Qwen over there?"])

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


class CapturedAudioTests(unittest.TestCase):
    def namespace(self):
        return load_definitions(
            "CapturedAudio",
            extra={
                "np": np,
                "CAPTURE_BLOCK_SECONDS": 8,
                "SAMPLE_RATE": 16_000,
            },
        )

    def test_blocks_preserve_exact_audio_and_capture_offsets(self):
        captured = self.namespace()["CapturedAudio"](block_samples=4)
        captured.append(np.array([[0.0], [1.0], [2.0]], dtype=np.float32))
        captured.append(np.array([3.0, 4.0, 5.0], dtype=np.float32))
        captured.append(np.array([6.0], dtype=np.float32))

        np.testing.assert_array_equal(
            captured.array(), np.arange(7, dtype=np.float32))
        np.testing.assert_array_equal(
            np.concatenate(captured.frames_from(3)),
            np.arange(3, 7, dtype=np.float32),
        )
        self.assertEqual(captured.frames_from(99), ())
        self.assertEqual(captured.total_samples, 7)

    def test_small_callbacks_use_fixed_size_storage_blocks(self):
        captured = self.namespace()["CapturedAudio"](block_samples=8)
        for value in range(1_000):
            captured.append(np.array([value], dtype=np.float32))

        self.assertEqual(len(captured.blocks), 125)
        self.assertIsNone(captured.tail)
        self.assertEqual(captured.total_samples, 1_000)
        np.testing.assert_array_equal(
            captured.array(), np.arange(1_000, dtype=np.float32))

    def test_snapshot_views_do_not_expand_with_later_callbacks(self):
        captured = self.namespace()["CapturedAudio"](block_samples=8)
        captured.append(np.arange(4, dtype=np.float32))
        snapshot = captured.frames_from()

        captured.append(np.arange(4, 8, dtype=np.float32))

        np.testing.assert_array_equal(
            np.concatenate(snapshot), np.arange(4, dtype=np.float32))
        np.testing.assert_array_equal(
            captured.array(), np.arange(8, dtype=np.float32))

    def test_invalid_block_size_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "block size"):
            self.namespace()["CapturedAudio"](block_samples=0)


class HotkeyListenerRecoveryTests(unittest.TestCase):
    def test_healthy_listener_is_left_alone(self):
        ns = load_definitions("restart_dead_hotkey_listener")
        state = {
            "l": SimpleNamespace(running=True),
            "make": lambda: self.fail("healthy listener was replaced"),
            "recovering": False,
        }
        cleanups = []

        self.assertFalse(ns["restart_dead_hotkey_listener"](
            lambda: cleanups.append(True), state))
        self.assertEqual(cleanups, [])
        self.assertFalse(state["recovering"])

    class ExitedListener(threading.Thread):
        """pynput's AbstractListener shape: a thread whose ``running`` flag
        is set in run() and cleared only by stop(). When the platform loop
        returns by itself the flag stays set and the thread is gone."""

        def __init__(self):
            super().__init__(daemon=True)
            self._running = False

        @property
        def running(self):
            return self._running

        def run(self):
            self._running = True

    def test_a_listener_thread_that_exited_is_replaced(self):
        ns = load_definitions(
            "restart_dead_hotkey_listener",
            extra={"print": lambda *_args: None},
        )
        exited = self.ExitedListener()
        exited.start()
        exited.join(timeout=1.0)
        self.assertTrue(exited.running)
        self.assertFalse(exited.is_alive())
        replacement = SimpleNamespace(running=True)
        cleanups = []
        state = {"l": exited, "make": lambda: replacement,
                 "recovering": False}

        self.assertTrue(ns["restart_dead_hotkey_listener"](
            lambda: cleanups.append(True), state))

        self.assertIs(state["l"], replacement)
        self.assertEqual(cleanups, [True])

    def test_a_listener_that_keeps_exiting_is_logged_once(self):
        logged = []
        ns = load_definitions(
            "restart_dead_hotkey_listener",
            extra={"print": logged.append},
        )

        def exited_listener():
            listener = self.ExitedListener()
            listener.start()
            listener.join(timeout=1.0)
            return listener

        state = {"l": exited_listener(), "make": exited_listener,
                 "recovering": False}
        for _ in range(5):
            self.assertTrue(ns["restart_dead_hotkey_listener"](
                lambda: None, state))
        self.assertEqual(logged, [
            "! hotkey listener died — restarting it",
            "! hotkey listener keeps exiting — still restarting it",
        ])

        # Once a replacement stays up, a later death is reported again.
        state["l"] = SimpleNamespace(running=True)
        self.assertFalse(ns["restart_dead_hotkey_listener"](
            lambda: None, state))
        state["l"] = exited_listener()
        self.assertTrue(ns["restart_dead_hotkey_listener"](
            lambda: None, state))
        self.assertEqual(
            logged[-1], "! hotkey listener died — restarting it")
        self.assertEqual(len(logged), 3)

    def test_a_running_listener_thread_is_left_alone(self):
        ns = load_definitions("restart_dead_hotkey_listener")
        release = threading.Event()

        class Live(self.ExitedListener):
            def run(self):
                self._running = True
                release.wait(timeout=5.0)

        live = Live()
        live.start()
        try:
            state = {"l": live,
                     "make": lambda: self.fail("live listener replaced"),
                     "recovering": False}
            self.assertFalse(ns["restart_dead_hotkey_listener"](
                lambda: self.fail("cleanup ran"), state))
            self.assertIs(state["l"], live)
        finally:
            release.set()
            live.join(timeout=1.0)

    def test_dead_listener_cleanup_runs_once_across_restart_retries(self):
        ns = load_definitions(
            "restart_dead_hotkey_listener",
            extra={"print": lambda *_args: None},
        )
        replacement = SimpleNamespace(running=True)
        attempts = []
        cleanups = []

        def make_listener():
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("event tap unavailable")
            return replacement

        state = {
            "l": SimpleNamespace(running=False),
            "make": make_listener,
            "recovering": False,
        }

        self.assertFalse(ns["restart_dead_hotkey_listener"](
            lambda: cleanups.append(True), state))
        self.assertTrue(state["recovering"])
        self.assertTrue(ns["restart_dead_hotkey_listener"](
            lambda: cleanups.append(True), state))

        self.assertEqual(cleanups, [True])
        self.assertEqual(len(attempts), 2)
        self.assertIs(state["l"], replacement)
        self.assertFalse(state["recovering"])

    def test_cleanup_failure_does_not_prevent_listener_replacement(self):
        ns = load_definitions(
            "restart_dead_hotkey_listener",
            extra={"print": lambda *_args: None},
        )
        replacement = SimpleNamespace(running=True)
        state = {
            "l": SimpleNamespace(running=False),
            "make": lambda: replacement,
            "recovering": False,
        }

        def fail_cleanup():
            raise RuntimeError("cleanup failed")

        self.assertTrue(ns["restart_dead_hotkey_listener"](
            fail_cleanup, state))
        self.assertIs(state["l"], replacement)
        self.assertFalse(state["recovering"])

    def test_recovery_unlatches_key_and_queues_capture_cleanup(self):
        ns = load_definitions("queue_hotkey_listener_recovery")
        key_down = {"on": True}
        modifiers = {"command", "shift"}
        events = queue.Queue()

        ns["queue_hotkey_listener_recovery"](
            key_down, modifiers, events, event_at=12.5)

        self.assertFalse(key_down["on"])
        self.assertEqual(modifiers, set())
        self.assertEqual(events.get_nowait(), (
            "listener_recovery", 12.5, frozenset()))

    @staticmethod
    def hold_namespace():
        return load_definitions(
            "latch_hotkey_press", "supervise_hotkey_latch",
            assignments={"MAX_TAKE_SECONDS", "HOLD_KEY_UP_POLLS"},
        )

    @staticmethod
    def drain(events):
        drained = []
        while not events.empty():
            drained.append(events.get_nowait())
        return drained

    def test_a_lost_key_up_ends_the_take_through_a_release(self):
        # Right Shift released while Left Shift is still held: pynput reads
        # the family flag, so no release event ever arrives.
        ns = self.hold_namespace()
        latch, lock, events = {"on": False}, threading.Lock(), queue.Queue()
        modifiers = {"shift"}
        ns["latch_hotkey_press"](latch, 100.0)
        readings = iter([True, True, False, False])
        step = functools.partial(
            ns["supervise_hotkey_latch"], latch, lock, events, modifiers,
            lambda: next(readings))

        outcomes = [step(now) for now in (100.25, 100.5, 100.75, 101.0)]

        self.assertEqual(outcomes, [None, None, None, "key_up_lost"])
        self.assertEqual(self.drain(events), [
            ("release", 101.0, frozenset({"shift"}))])
        self.assertFalse(latch["on"])
        # The next real press is accepted again.
        self.assertIsNone(step(101.25))

    def test_an_unreadable_key_state_never_ends_a_hold_early(self):
        # A remapped key or a missing permission reads "up" while held. With
        # no "down" reading during the hold, only the cap applies.
        ns = self.hold_namespace()
        latch, lock, events = {"on": False}, threading.Lock(), queue.Queue()
        ns["latch_hotkey_press"](latch, 0.0)
        cap = ns["MAX_TAKE_SECONDS"]
        for reading in (False, None):
            with self.subTest(reading=reading):
                for now in (0.25, 0.5, 60.0, cap - 0.01):
                    self.assertIsNone(ns["supervise_hotkey_latch"](
                        latch, lock, events, set(), lambda: reading, now))
                self.assertTrue(latch["on"])
                self.assertEqual(self.drain(events), [])

    def test_the_take_cap_finishes_once_and_keeps_the_key_latched(self):
        ns = self.hold_namespace()
        latch, lock, events = {"on": False}, threading.Lock(), queue.Queue()
        ns["latch_hotkey_press"](latch, 0.0)
        cap = ns["MAX_TAKE_SECONDS"]
        self.assertEqual(cap, 600.0)
        held = functools.partial(
            ns["supervise_hotkey_latch"], latch, lock, events, set(),
            lambda: True)

        self.assertEqual(held(cap), "take_limit")
        self.assertIsNone(held(cap + 0.25))
        self.assertIsNone(held(cap + 30.0))

        self.assertEqual(self.drain(events), [("release", cap, frozenset())])
        # Still latched: a held function key's auto-repeat cannot start a
        # new take, and a later lost key-up only clears the latch.
        self.assertTrue(latch["on"])
        for now in (cap + 31.0, cap + 31.25):
            ns["supervise_hotkey_latch"](
                latch, lock, events, set(), lambda: False, now)
        self.assertFalse(latch["on"])
        self.assertEqual(self.drain(events), [])

    def test_a_real_key_event_during_a_poll_wins(self):
        ns = self.hold_namespace()
        latch, lock, events = {"on": False}, threading.Lock(), queue.Queue()
        ns["latch_hotkey_press"](latch, 0.0)
        latch.update(confirmed=True, up_polls=1)

        def released_then_pressed_again():
            # The listener releases and re-presses while the poll reads.
            with lock:
                latch["on"] = False
                ns["latch_hotkey_press"](latch, 0.2)
            return False

        self.assertIsNone(ns["supervise_hotkey_latch"](
            latch, lock, events, set(), released_then_pressed_again, 0.25))
        self.assertTrue(latch["on"])
        self.assertEqual(latch["at"], 0.2)
        self.assertEqual(self.drain(events), [])

    def test_physical_key_state_reads_the_bound_key_code(self):
        reads = []
        quartz = types.ModuleType("Quartz")
        quartz.kCGEventSourceStateHIDSystemState = 1
        quartz.CGEventSourceKeyState = (
            lambda source, vk: reads.append((source, vk)) or vk == 61)
        ns = load_definitions(
            "physical_key_is_down",
            extra={"IS_MACOS": True, "IS_WINDOWS": False, "ctypes": ctypes})
        right_option = SimpleNamespace(value=SimpleNamespace(vk=61))
        with mock.patch.dict(sys.modules, {"Quartz": quartz}):
            self.assertTrue(ns["physical_key_is_down"](right_option))
            self.assertFalse(ns["physical_key_is_down"](
                SimpleNamespace(value=SimpleNamespace(vk=60))))
            quartz.CGEventSourceKeyState = mock.Mock(
                side_effect=RuntimeError("no access"))
            self.assertIsNone(ns["physical_key_is_down"](right_option))
        self.assertEqual(reads, [(1, 61), (1, 60)])
        self.assertIsNone(ns["physical_key_is_down"](None))

    def test_active_hotkey_recovery_surfaces_retry_feedback(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        main = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        segment = ast.get_source_segment(source, main)

        self.assertIn('"Hotkey reset — try again"', segment)
        self.assertIn('"Listening failed — try again"', segment)
        self.assertIn(
            "schedule_dictation_feedback_dismissal(rec, hud, active)",
            segment,
        )


class ReleasePlanTests(unittest.TestCase):
    def test_dictation_problem_shows_bounded_retry_guidance(self):
        captions = {"text": ""}
        statuses = []
        modes = []
        sounds = []
        logs = []
        ns = load_definitions(
            "report_dictation_problem",
            "dictation_feedback_delay",
            extra={
                "AppHelper": SimpleNamespace(
                    callAfter=lambda function, value: function(value)),
                "CAPTION": captions,
                "DICTATION_ERROR_SECONDS": 2.5,
                "math": __import__("math"),
                "play": sounds.append,
                "print": logs.append,
                "set_status": statuses.append,
            },
        )
        recorder = SimpleNamespace(uncertain=False, feedback_seconds=0.0)
        hud = SimpleNamespace(showMode_=modes.append)

        ns["report_dictation_problem"](
            recorder,
            hud,
            "I couldn't understand that — try again",
            "[dropped] ASR gave nothing",
        )

        self.assertEqual(
            captions["text"], "I couldn't understand that — try again")
        self.assertIsNone(captions["confidence"])
        self.assertFalse(captions["stable_prefix"])
        self.assertEqual(statuses, ["err"])
        self.assertEqual(modes, ["error"])
        self.assertEqual(sounds, ["Funk"])
        self.assertEqual(logs, ["[dropped] ASR gave nothing"])
        self.assertEqual(ns["dictation_feedback_delay"](recorder), 2.5)

    def test_feedback_delay_preserves_uncertain_and_bounds_bad_values(self):
        ns = load_definitions(
            "dictation_feedback_delay",
            extra={"math": __import__("math")},
        )

        self.assertEqual(ns["dictation_feedback_delay"](
            SimpleNamespace(uncertain=True, feedback_seconds=0.0)), 3.0)
        self.assertEqual(ns["dictation_feedback_delay"](
            SimpleNamespace(uncertain=False, feedback_seconds=99.0)), 10.0)
        self.assertEqual(ns["dictation_feedback_delay"](
            SimpleNamespace(uncertain=False, feedback_seconds=float("nan"))), 0.0)
        self.assertEqual(ns["dictation_feedback_delay"](
            SimpleNamespace(uncertain=False, feedback_seconds="bad")), 0.0)

    def test_feedback_dismissal_does_not_hide_newer_recording(self):
        timers = []
        dismissals = []
        status = []

        class Timer:
            def __init__(self, seconds, callback):
                self.seconds = seconds
                self.callback = callback

            def start(self):
                timers.append(self)

        ns = load_definitions(
            "dictation_feedback_delay",
            "schedule_dictation_feedback_dismissal",
            extra={
                "AppHelper": SimpleNamespace(
                    callAfter=lambda function: function()),
                "math": __import__("math"),
                "PAUSED": {"on": False},
                "STATUS": {
                    "bar": SimpleNamespace(setState_=status.append),
                },
                "threading": SimpleNamespace(Timer=Timer),
            },
        )
        recorder = SimpleNamespace(uncertain=False, feedback_seconds=2.5)
        hud = SimpleNamespace(dismiss=lambda: dismissals.append(True))
        active = {"rec": None}

        ns["schedule_dictation_feedback_dismissal"](
            recorder, hud, active)

        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0].seconds, 2.5)
        active["rec"] = object()
        timers[0].callback()
        self.assertEqual(dismissals, [])
        self.assertEqual(status, [])

    def test_unexpected_release_failure_becomes_visible_retry(self):
        problems = []
        dismissals = []
        ns = load_definitions(
            "finish_and_process",
            extra={
                "AppHelper": SimpleNamespace(
                    callAfter=lambda function: function()),
                "LAST_USE": {"t": 0.0},
                "PAUSED": {"on": False},
                "STATUS": {"bar": None},
                "dictation_feedback_delay": lambda _rec: 0.0,
                "release_should_wait_for_tail": lambda _rec: False,
                "report_dictation_problem": (
                    lambda _rec, _hud, caption, log, **_kwargs:
                    problems.append((caption, log))),
                "schedule_dictation_feedback_dismissal": (
                    lambda _rec, hud, _active: hud.dismiss()),
                "time": SimpleNamespace(
                    perf_counter=lambda: 10.0,
                    time=lambda: 10.0,
                ),
            },
        )

        class Recorder:
            released_at = 1.0
            recording = False

            def stop(self):
                raise RuntimeError("private device detail")

        ns["finish_and_process"](
            Recorder(),
            SimpleNamespace(dismiss=lambda: dismissals.append(True)),
            {"rec": None},
        )

        self.assertEqual(problems, [(
            "Dictation failed — try again",
            "! dictation failed during capture-finalize: RuntimeError",
        )])
        self.assertEqual(dismissals, [True])

    def test_outbox_failures_use_bounded_error_feedback(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        finish = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "finish_and_process"
        )
        segment = ast.get_source_segment(source, finish)

        self.assertGreaterEqual(segment.count(
            '"Destination changed — saved in Voice Outbox"'), 2)
        self.assertGreaterEqual(segment.count(
            '"Paste unverified — check target; saved in'), 2)
        self.assertNotIn(
            'CAPTION["text"] = "Destination changed — saved in Voice Outbox"',
            segment,
        )

    def test_back_to_back_releases_finish_in_order(self):
        ns = load_definitions("ReleaseOrder")
        release_order = ns["ReleaseOrder"]()
        first = release_order.issue()
        second = release_order.issue()
        order = []

        second_entered = threading.Event()

        def await_second():
            release_order.wait(second)
            order.append("second")
            second_entered.set()

        waiter = threading.Thread(target=await_second)
        waiter.start()
        self.assertFalse(second_entered.wait(0.05))
        release_order.wait(first)
        order.append("first")
        release_order.complete(first)
        self.assertTrue(second_entered.wait(1.0))
        release_order.complete(second)
        waiter.join(1.0)

        self.assertFalse(waiter.is_alive())
        self.assertEqual(order, ["first", "second"])

    def test_failed_release_does_not_strand_later_tickets(self):
        ns = load_definitions("ReleaseOrder")
        release_order = ns["ReleaseOrder"]()
        first = release_order.issue()
        failed_second = release_order.issue()
        third = release_order.issue()
        third_entered = threading.Event()

        release_order.complete(failed_second)
        waiter = threading.Thread(
            target=lambda: (
                release_order.wait(third),
                third_entered.set(),
            ),
        )
        waiter.start()
        self.assertFalse(third_entered.wait(0.05))
        release_order.complete(first)
        self.assertTrue(third_entered.wait(1.0))
        release_order.complete(third)
        waiter.join(1.0)

        self.assertFalse(waiter.is_alive())

    def test_finish_failure_completes_release_ticket(self):
        def fail(*_args):
            raise RuntimeError("failed")

        ns = load_definitions(
            "ReleaseOrder",
            "finish_in_release_order",
            extra={"finish_and_process": fail},
        )
        release_order = ns["ReleaseOrder"]()
        first = release_order.issue()
        rec = SimpleNamespace(process_ticket=first)
        ns["finish_in_release_order"].__globals__[
            "DICTATION_PROCESS_ORDER"] = release_order

        with self.assertRaisesRegex(RuntimeError, "failed"):
            ns["finish_in_release_order"](rec, None, {})

        second = release_order.issue()
        release_order.wait(second)
        release_order.complete(second)

    def test_active_speech_waits_before_submitting_the_remainder(self):
        ns = load_definitions(
            "release_should_wait_for_tail",
            assignments={"SAMPLE_RATE", "TAIL_SKIP_SILENCE"},
            extra={
                "calibrated_end_silence_seconds":
                    lambda: 0.12,
            },
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

            def submit(self, function, audio, *args, **kwargs):
                self.submissions += 1
                return Future(function(audio, *args, **kwargs))

        pool = Pool()
        ns = load_definitions(
            "assemble_raw",
            extra={
                "np": SimpleNamespace(ndarray=object),
                "SAMPLE_RATE": 16_000,
                "GATE_PEAK_RMS": 0.002,
                "ASR_POOL": pool,
                "transcribe_detailed":
                    lambda audio, prompt=None, language="en":
                        Recognition(
                            "remainder", verified=True, engine="tiny"),
                "peak_rms": lambda audio: 0.1,
                "is_hallucination": lambda *_args: False,
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

    def _assemble_with_the_real_guard(self, lines):
        return load_definitions(
            "BoundedRecognitionFuture",
            "assemble_raw",
            "is_hallucination",
            "weak_whisper_evidence",
            assignments=CleanupGuardTests.HALLUCINATION_GUARD,
            extra={
                "dataclass": dataclass,
                "np": SimpleNamespace(ndarray=object),
                "SAMPLE_RATE": 16_000,
                "GATE_PEAK_RMS": 0.002,
                "ASR_POOL": SimpleNamespace(),
                "transcribe_detailed": None,
                "peak_rms": lambda _audio: 0.0,
                "Recognition": Recognition,
                "RecognitionWord": RecognitionWord,
                "print": lines.append,
            },
        )

    def test_a_sign_off_after_a_pause_is_part_of_the_dictation(self):
        class Future:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        lines = []
        ns = self._assemble_with_the_real_guard(lines)
        bounded = ns["BoundedRecognitionFuture"]
        # A rolling chunk cut at a pause, then the sign-off decoded on its
        # own as the remainder: the shape of any longer dictated message.
        body = Recognition(
            "Let me know if you have any questions.", confidence=0.93,
            engine="parakeet-unified", audio_duration=4.6)
        sign_off = Recognition(
            "Thank you.", confidence=0.93, engine="parakeet-unified",
            audio_duration=1.4)

        result = ns["assemble_raw"](
            [bounded(Future(body), 0, 73_600)],
            bounded(Future(sign_off), 73_600, 96_000), [], None, "en")

        self.assertEqual(
            result.text, "Let me know if you have any questions. Thank you.")
        self.assertEqual(result.engine, "parakeet-unified")
        self.assertEqual(lines, [])
        # Spoken alone it is still the whole dictation.
        alone = ns["assemble_raw"](
            [], bounded(Future(sign_off), 0, 22_400), [], None, "en")
        self.assertEqual(alone.text, "Thank you.")
        self.assertFalse(ns["is_hallucination"](alone.text, "en", alone))

    def test_a_rejected_part_is_logged_without_its_words(self):
        class Future:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        lines = []
        ns = self._assemble_with_the_real_guard(lines)
        bounded = ns["BoundedRecognitionFuture"]
        body = Recognition(
            "Let me know if you have any questions.", confidence=0.81,
            engine="turbo", audio_duration=4.6)
        silence = Recognition(
            "Thanks for watching!", confidence=0.34, engine="turbo",
            audio_duration=3.0)

        result = ns["assemble_raw"](
            [bounded(Future(body), 0, 73_600)],
            bounded(Future(silence), 73_600, 121_600), [], None, "en")

        self.assertEqual(result.text, "Let me know if you have any questions.")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("[dropped] "))
        self.assertIn("turbo", lines[0])
        for word in ("thanks", "watching"):
            self.assertNotIn(word, lines[0].casefold())

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
                "is_hallucination": lambda *_args: False,
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
                "is_hallucination": lambda *_args: False,
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
                "is_hallucination": lambda *_args: False,
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
            "CapturedAudio",
            "BoundedRecognitionFuture",
            "Recorder",
            extra={
                "dataclass": dataclass,
                "np": np,
                "ContextPack": SimpleNamespace,
                "LEVELS": [],
                "SILENCE_RMS": 0.01,
                "calibrated_vad_threshold": lambda: 0.01,
                "SAMPLE_RATE": 16_000,
                "CAPTURE_BLOCK_SECONDS": 8,
                "should_start_speculation": lambda *_args: False,
                "SPECULATIVE_MIN_SECONDS": 0.5,
                "SPECULATIVE_SILENCE": 0.1,
                "CHUNK_MIN_SECONDS": 0.5,
                "CHUNK_CUT_SILENCE": 0.1,
                "can_reuse_speculation": lambda *_args: False,
                "CHUNK_PREP_POOL": prep_pool,
                "_transcribe_frames": object(),
                "_caption_add": lambda *_args: None,
                "time": time,
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

    def soft_speech_take(self, segments, block=1024):
        """Run the production rolling plan over synthetic audio, then the
        release choice finish_and_process makes, and return what nothing
        decodes. Futures never finish, as on a busy decode pool."""
        class Pending:
            def cancel(self):
                return False

            def done(self):
                return False

            def add_done_callback(self, _callback):
                pass

        ns = load_definitions(
            "CapturedAudio", "BoundedRecognitionFuture", "Recorder",
            "peak_rms",
            assignments={
                "SAMPLE_RATE", "CAPTURE_BLOCK_SECONDS", "SILENCE_RMS",
                "GATE_PEAK_RMS", "CHUNK_MIN_SECONDS", "CHUNK_CUT_SILENCE",
                "SPECULATIVE_MIN_SECONDS", "SPECULATIVE_SILENCE",
                "NO_AUDIO_SECONDS",
            },
            extra={
                "dataclass": dataclass,
                "np": np,
                "time": time,
                "ContextPack": SimpleNamespace,
                "LEVELS": [],
                "should_start_speculation": should_start_speculation,
                "can_reuse_speculation": can_reuse_speculation,
                "CHUNK_PREP_POOL": SimpleNamespace(
                    submit=lambda *_args, **_kwargs: Pending()),
                "_speculative_frames": object(),
                "_transcribe_frames": object(),
                "_caption_add": lambda *_args: None,
            },
        )
        ns["calibrated_vad_threshold"] = lambda: ns["SILENCE_RMS"]
        recorder = ns["Recorder"]()
        recorder.recording = True
        rng = np.random.default_rng(0)
        for seconds, rms in segments:
            for _ in range(int(seconds * 16_000 / block)):
                noise = rng.standard_normal((block, 1)).astype(np.float32)
                recorder._callback(
                    noise * (rms / float(np.sqrt(np.mean(noise ** 2)))),
                    block, None, None)

        audio = recorder.stop()
        ranges = [(c.start_sample, c.end_sample) for c in recorder.chunks]
        reused = can_reuse_speculation(
            recorder.speculative_future is not None,
            recorder.speculative_invalid,
            recorder.speculative_start,
            recorder.cut_samples)
        if reused:
            ranges.append(
                (recorder.speculative_start, recorder.speculative_end))
        else:
            ranges.append((recorder.cut_samples, len(audio)))
        covered = np.zeros(len(audio), dtype=bool)
        for start, end in ranges:
            covered[start:end] = True
        return SimpleNamespace(
            skipped_peak=ns["peak_rms"](audio[~covered]),
            gate=ns["GATE_PEAK_RMS"], reused=reused)

    def test_quiet_speech_after_a_pause_is_decoded_at_release(self):
        # Loud opening, a short pause, then speech under the rolling silence
        # threshold (0.008) but well above the speech gate (0.002).
        take = self.soft_speech_take(
            [(1.5, 0.05), (0.3, 0.0005), (1.5, 0.0042)])
        self.assertLess(take.skipped_peak, take.gate)

    def test_quiet_speech_across_a_chunk_cut_is_decoded(self):
        take = self.soft_speech_take(
            [(1.0, 0.05), (3.6, 0.0045), (1.5, 0.05), (0.7, 0.0005)])
        self.assertLess(take.skipped_peak, take.gate)

    def test_a_silent_tail_still_reuses_the_speculation(self):
        # Room noise under the gate after the pause: the speculative decode
        # is still the whole remainder, so release pays for nothing new.
        take = self.soft_speech_take([(1.5, 0.05), (0.8, 0.0005)])
        self.assertTrue(take.reused)
        self.assertLess(take.skipped_peak, take.gate)

    def test_release_attaches_capture_bounds_to_remainder_decode(self):
        class Pool:
            def __init__(self):
                self.future = SimpleNamespace()

            def submit(self, *_args, **_kwargs):
                return self.future

        pool = Pool()
        assembled = []
        problems = []

        def assemble(chunk_futures, pre_future, remainder, prompt,
                     language="en"):
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
                "is_hallucination": lambda *_args: False,
                "report_dictation_problem": (
                    lambda _rec, _hud, caption, log, **_kwargs:
                    problems.append((caption, log))),
                "dictation_feedback_delay": lambda _rec: 0.0,
                "schedule_dictation_feedback_dismissal":
                    lambda _rec, _hud, _active: None,
                "DICTATION_PROCESS_ORDER": SimpleNamespace(
                    wait=lambda _ticket: None),
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
            language = "en"
            process_ticket = None
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
        self.assertEqual(problems, [(
            "I couldn't understand that — try again",
            "[dropped] ASR gave nothing",
        )])

    def _release(self, mode, language, spoken, selected="Hallo zusammen",
                 field=None, recognition=None):
        """Drive finish_and_process from a decoded take to its insertion.

        ``field`` is the text already before the cursor. ``recognition``
        replaces the default confident decode of ``spoken``.
        """
        pasted = []
        problems = []

        def commit(_rec, text, _bundle, _target):
            pasted.append(text)
            raise RuntimeError("stop after the insertion call")

        compiled = SimpleNamespace(
            text=spoken, confidence=0.93, decisions=(), anchors=(),
            stable_prefix="")
        ns = load_definitions(
            "finish_and_process",
            "collapse_repeats",
            "extract_tone_override",
            "needs_llm_cleanup",
            "quick_clean",
            "is_hallucination",
            "weak_whisper_evidence",
            "strip_context_echo",
            assignments={
                "TONE", "TONE_OVERRIDE_RE", "TONE_ALIASES", "CONT_END",
            } | CleanupGuardTests.HALLUCINATION_GUARD,
            extra={
                "np": np,
                "time": SimpleNamespace(
                    perf_counter=lambda: 10.0, time=lambda: 10.0,
                    time_ns=lambda: 10),
                "NUMBER_WORDS": NUMBER_WORDS,
                "compile_cleanup": compile_cleanup,
                "release_should_wait_for_tail": lambda _rec: False,
                "can_reuse_speculation": lambda *_args: False,
                "ASR_POOL": SimpleNamespace(
                    submit=lambda *_args, **_kwargs: SimpleNamespace()),
                "BoundedRecognitionFuture": lambda *_args: SimpleNamespace(),
                "transcribe_detailed": object(),
                "SAMPLE_RATE": 16_000,
                "MIN_SECONDS": 0.25,
                "GATE_PEAK_RMS": 0.002,
                "peak_rms": lambda _audio: 0.1,
                "audio_gate_measurements": lambda _audio: (1.0, 0.1),
                "assemble_raw": lambda *_args: recognition or Recognition(
                    spoken, confidence=0.86, engine="turbo",
                    audio_duration=1.0),
                "Recognition": Recognition,
                "looks_like_prompt_echo": lambda _text: False,
                "consume_risky_action_confirmation_voice":
                    lambda _text: False,
                "clear_retained_consequence_spans": lambda: None,
                "compile_voice_evidence": lambda *_args, **_kwargs: (
                    SimpleNamespace(
                        context=SimpleNamespace(candidates=())),
                    compiled),
                "runtime_consequence_evidence":
                    lambda *_args, **_kwargs: 0.0,
                "active_consequence_verifier": lambda: None,
                "runtime_context_firewall_evidence": lambda *_args: 0.0,
                "learned_alternatives": lambda _text, _bundle: [],
                "PIPELINE_STATE": {},
                "CAPTION": {},
                "queue_voice_object_command": lambda *_args: False,
                "apply_spoken_edit_command": lambda *_args: False,
                "match_snippet": lambda _text: None,
                "_load_snippet_map": lambda: {},
                "_mask_snippets_inline": lambda text, _snippets: (text, {}),
                "is_verbatim_app": lambda _bundle: False,
                "cursor_context": lambda: field,
                "tone_for": lambda _bundle: "default",
                "apply_vocabulary_casing": lambda text: text,
                "focused_snapshot": lambda: SimpleNamespace(),
                "make_paste_receipt": lambda *_args: SimpleNamespace(),
                "commit_insertion": commit,
                "play": lambda _sound: None,
                "report_dictation_problem": (
                    lambda _rec, _hud, caption, log, **_kwargs:
                    problems.append((caption, log))),
                "dictation_feedback_delay": lambda _rec: 0.0,
                "schedule_dictation_feedback_dismissal":
                    lambda _rec, _hud, _active: None,
                "DICTATION_PROCESS_ORDER": SimpleNamespace(
                    wait=lambda _ticket: None),
                "print": lambda *_args: None,
                "LAST_USE": {},
                "AppHelper": SimpleNamespace(callAfter=lambda *_args: None),
            },
        )
        recorder = SimpleNamespace(
            released_at=1.0,
            speculative_future=None,
            speculative_invalid=False,
            speculative_start=0,
            speculative_end=0,
            prompt=None,
            language=language,
            process_ticket=None,
            recording=False,
            uncertain=False,
            mode=mode,
            bundle_at_press="com.apple.TextEdit",
            context_terms=(),
            context_pack=None,
            utterance_id="take-1",
            insertion_lease=None,
            insertion_receipt=None,
            focus_at_press=SimpleNamespace(
                selected_text=selected, text=selected,
                selection=(0, len(selected))),
            stop=lambda: np.ones(16_000, dtype=np.float32),
            cut_samples=8_000,
            chunks=[],
        )
        ns["finish_and_process"](recorder, SimpleNamespace(), {})
        return pasted, problems

    def test_rewrite_modes_never_paste_the_instruction_without_the_model(self):
        # The cleanup model is English-only. In another language Edit used
        # to fall through to plain cleanup and paste the spoken instruction
        # over the text it was meant to change.
        for mode, spoken in (
            ("edit", "mach das formeller"),
            ("reply", "sag ihr dass ich zustimme"),
            ("compose", "kurze absage an das team"),
        ):
            with self.subTest(mode=mode):
                pasted, problems = self._release(mode, "de", spoken)
                self.assertEqual(pasted, [])
                self.assertEqual(len(problems), 1)
                caption, log = problems[0]
                self.assertIn("nothing changed", caption)
                self.assertIn(mode.capitalize(), caption)
                self.assertNotIn(spoken, log)

    def test_plain_dictation_in_another_language_is_still_inserted(self):
        pasted, _problems = self._release(
            "capture", "de", "wir treffen uns morgen")
        self.assertEqual(pasted, ["wir treffen uns morgen"])

    def test_a_field_tail_is_stripped_from_the_paste_only_as_whole_words(self):
        # The field ends in "to", which is also how "Tomorrow" starts. The
        # old prefix strip pasted "morrow works for me."
        pasted, _problems = self._release(
            "capture", "en", "Tomorrow works for me.", field="to")
        self.assertEqual(len(pasted), 1)
        self.assertEqual(pasted[0].casefold(), " tomorrow works for me.")
        # A real repeat of the field's own tail is still removed.
        pasted, _problems = self._release(
            "capture", "en", "See you at six thirty.", field="see you at")
        self.assertEqual(pasted, [" six thirty."])

    def test_a_short_reply_is_dropped_only_on_a_weak_whisper_decode(self):
        pasted, _problems = self._release(
            "capture", "en", "Thank you.", recognition=Recognition(
                "Thank you.", confidence=0.93, engine="parakeet-unified",
                audio_duration=1.2))
        self.assertEqual(pasted, ["Thank you."])
        # The same words from Whisper, unsure of itself, are its filler for
        # silence. The take is refused with the cue and nothing is pasted.
        pasted, problems = self._release(
            "capture", "en", "Thank you.", recognition=Recognition(
                "Thank you.", confidence=0.34, engine="turbo",
                audio_duration=3.0))
        self.assertEqual(pasted, [])
        self.assertEqual(problems, [(
            "I couldn't understand that — try again",
            "[dropped] ASR hallucination detected",
        )])

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
                "is_hallucination": lambda *_args: False,
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

            def submit(self, function, *args, **kwargs):
                # The cascade now passes the decode language after the model
                # repository, so the recorded call is the model, not the last
                # positional argument.
                self.calls.append(args[3] if len(args) > 3 else None)
                return SimpleNamespace(
                    result=lambda: function(*args, **kwargs))

        pool = ImmediatePool()

        def detailed(audio, prompt, verify, model, language="en"):
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

            def submit(self, function, *args, **kwargs):
                # The model repository is the fourth positional argument;
                # the cross-check hint and the decode language ride as
                # keywords, so index rather than take the last one.
                self.calls.append(args[3] if len(args) > 3 else None)
                return SimpleNamespace(
                    result=lambda: function(*args, **kwargs))

        pool = ImmediatePool()
        crosscheck_hints = []

        def detailed(audio, prompt, verify, model,
                     crosscheck_text=None, language="en"):
            crosscheck_hints.append(crosscheck_text)
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
        # The speculative Tiny hypothesis rides along so the Parakeet route
        # can derive agreement confidence without a second Tiny decode.
        self.assertEqual(crosscheck_hints, [None, "tiny"])

    def test_tiny_first_cascade_escalates_low_confidence(self):
        class ImmediatePool:
            def __init__(self):
                self.calls = []

            def submit(self, function, *args, **kwargs):
                # The model repository is the fourth positional argument;
                # the cross-check hint and the decode language ride as
                # keywords, so index rather than take the last one.
                self.calls.append(args[3] if len(args) > 3 else None)
                return SimpleNamespace(
                    result=lambda: function(*args, **kwargs))

        pool = ImmediatePool()

        def detailed(audio, prompt, verify, model,
                     crosscheck_text=None, language="en"):
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


class RecognitionContextPrivacyTests(unittest.TestCase):
    """Context capture leaves secrets alone."""

    @staticmethod
    def mac(*, clipboard_types=(), role="AXTextArea", subrole=None):
        from voice_compiler import ContextObservation, ContextRouter

        clipboard = "Quixotrel"
        snapshot = SimpleNamespace(
            element="focused-field", text="Draft for Zorblax Dynamics",
            selection=(0, 0), selected_text="", window_title="Notes",
            document=None)
        pasteboard = SimpleNamespace(
            types=lambda: ["public.utf8-plain-text", *clipboard_types],
            stringForType_=lambda kind: clipboard)
        return load_definitions(
            "capture_recognition_context", "pasteboard_is_private",
            assignments=("PASTEBOARD_MARKER_TYPES",),
            extra={
                "IS_WINDOWS": False,
                "focused_snapshot": lambda: snapshot,
                "NSWorkspace": SimpleNamespace(sharedWorkspace=lambda:
                    SimpleNamespace(frontmostApplication=lambda:
                        SimpleNamespace(localizedName=lambda: "Notes"))),
                "NSPasteboard": SimpleNamespace(
                    generalPasteboard=lambda: pasteboard),
                "NSPasteboardTypeString": "public.utf8-plain-text",
                "_ax_attribute": lambda element, attribute: {
                    "AXRole": role, "AXSubrole": subrole}.get(attribute),
                "ContextObservation": ContextObservation,
                "CONTEXT_ROUTER": ContextRouter(),
                "urllib": __import__("urllib.parse"),
                "Path": Path,
            },
        )["capture_recognition_context"]

    def test_an_ordinary_clipboard_and_field_are_used(self):
        _, words, _ = self.mac()()
        self.assertIn("Quixotrel", words)
        self.assertIn("Zorblax", words)

    def test_a_concealed_or_transient_clipboard_is_never_read(self):
        for marker in ("org.nspasteboard.ConcealedType",
                       "org.nspasteboard.TransientType"):
            with self.subTest(marker=marker):
                _, words, pack = self.mac(clipboard_types=(marker,))()
                self.assertNotIn("Quixotrel", words)
                self.assertNotIn(
                    "Quixotrel", [c.text for c in pack.candidates])
                self.assertIn("Zorblax", words)

    def test_a_secure_text_field_yields_no_context_at_all(self):
        snapshot, words, pack = self.mac(
            role="AXTextField", subrole="AXSecureTextField")()
        self.assertEqual(words, [])
        # Only the application's own name, which is not the user's secret.
        self.assertEqual({c.source for c in pack.candidates}, {"app"})
        # The insertion lease still needs to know where focus is.
        self.assertEqual(snapshot.element, "focused-field")

    def test_windows_skips_a_clipboard_marked_private(self):
        from voice_compiler import ContextObservation, ContextRouter

        for available, expected in ((set(), True), ({"private"}, False)):
            with self.subTest(marked=bool(available)):
                ns = load_definitions(
                    "capture_recognition_context",
                    "windows_clipboard_is_private",
                    assignments=("WINDOWS_PRIVATE_CLIPBOARD_FORMATS",),
                    extra={
                        "IS_WINDOWS": True,
                        "windows_foreground_title": lambda: "Vault",
                        "pyperclip": SimpleNamespace(
                            paste=lambda: "Quixotrel"),
                        "win32clipboard": SimpleNamespace(
                            RegisterClipboardFormat=lambda name: (
                                "private" if name ==
                                "ExcludeClipboardContentFromMonitorProcessing"
                                else name),
                            IsClipboardFormatAvailable=lambda kind:
                                kind in available),
                        "ContextObservation": ContextObservation,
                        "CONTEXT_ROUTER": ContextRouter(),
                    },
                )
                _, words, _ = ns["capture_recognition_context"]()
                self.assertEqual("Quixotrel" in words, expected)


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


class CustomVocabularyTests(unittest.TestCase):
    def test_refresh_glossary_protects_and_normalizes_every_manual_term(self):
        # 70 manual terms exceed the 60-term Whisper prompt cap. Every one must
        # still land in the anchor pack and the casing map even though only the
        # first 60 fit the prompt.
        manual = [f"Term{i}" for i in range(70)]
        gloss = {"lock": threading.Lock()}
        ns = load_definitions(
            "refresh_glossary",
            extra={
                "parse_dictionary": lambda: (list(manual), set()),
                "PersonalRegressionLab": PersonalRegressionLab,
                "load_learned": lambda: {
                    "counts": {"swift": 3}, "fixes": {}, "confusions": {}},
                "write_auto_section": lambda _promoted: None,
                "personal_regression_lab": lambda _state: object(),
                "ContextPack": ContextPack,
                "ContextCandidate": ContextCandidate,
                "GLOSS": gloss,
                "PROMOTE_MIN_COUNT": 2,
                "PERSONAL_GLOBAL_MIN_COUNT": 3,
                "GLOSSARY_MAX_TERMS": 60,
                "GLOSSARY_MAX_CHARS": 700,
                "ANCHOR_MAX_TERMS": 256,
                "ACOUSTIC_KEYWORD_MEMORY_LOCK": threading.Lock(),
                "_load_acoustic_keyword_memory":
                    lambda: (AcousticKeywordMemory(), "missing"),
                "active_acoustic_keywords":
                    lambda _path, _memory: ((), "missing"),
                "measured_keyword_hints": tuple,
                "ACOUSTIC_KEYWORD_ACTIVATION_FILE":
                    Path("acoustic_keyword_activation.json"),
            },
        )

        ns["refresh_glossary"]()

        self.assertEqual(len(gloss["terms"]), 60)   # prompt cap unchanged
        anchor_texts = {c.text for c in gloss["anchor_pack"].candidates}
        self.assertEqual(len(anchor_texts), 71)
        for term in manual:
            self.assertIn(term, anchor_texts)
            self.assertEqual(gloss["vocabulary"][term.casefold()], term)
        # A learned term is protected, but only a term the user wrote sets
        # casing: nobody chose the case a learned term was first seen in.
        self.assertIn("swift", anchor_texts)
        self.assertEqual(len(gloss["vocabulary"]), 70)
        # A term beyond the 60-term prompt cap is still protected and normalized.
        self.assertNotIn("Term65", gloss["terms"])
        self.assertIn("Term65", anchor_texts)
        for candidate in gloss["anchor_pack"].candidates:
            self.assertEqual(candidate.weight, 3.5)
            self.assertEqual(candidate.source, "dictionary")

    def test_apply_vocabulary_casing_is_whole_word_and_additive(self):
        gloss = {"lock": threading.Lock(),
                 "vocabulary": {"github": "GitHub"}}
        ns = load_definitions(
            "apply_vocabulary_casing",
            assignments={"VOCAB_WORD_RE"},
            extra={"GLOSS": gloss},
        )
        casing = ns["apply_vocabulary_casing"]

        self.assertEqual(casing("i pushed to github"), "i pushed to GitHub")
        # A larger word that merely contains the term is left alone.
        self.assertEqual(
            casing("i really githubbed it"), "i really githubbed it")
        # An already-canonical token is left untouched.
        self.assertEqual(casing("i pushed to GitHub"), "i pushed to GitHub")
        # An unlisted (or banned, hence absent) term is untouched.
        self.assertEqual(casing("switch to qwen now"), "switch to qwen now")
        # Casing only raises letters. A lowercase term never takes the
        # capital off a sentence start, and capitals are not lowered to fit.
        gloss["vocabulary"].update({"kubectl": "kubectl", "iphone": "iPhone"})
        self.assertEqual(
            casing("Kubectl is installed. Github and GITHUB. Iphone, iphone."),
            "Kubectl is installed. GitHub and GITHUB. Iphone, iPhone.")


class DelayedCleanupRuntimeTests(unittest.TestCase):
    @staticmethod
    def activation_namespace(*, is_macos=True, validator=None):
        state = {
            "active": False,
            "status": "not_loaded",
            "generation": 0,
            "lock": threading.Lock(),
        }
        namespace = load_definitions(
            "load_delayed_cleanup_activation",
            "delayed_cleanup_activation_status",
            "delayed_cleanup_scheduling_enabled",
            extra={
                "MEASUREMENT_MODE": MeasurementMode.inert(),
                "Path": Path,
                "os": os,
                "stat": stat,
                "IS_MACOS": is_macos,
                "DELAYED_CLEANUP_ACTIVATION_FILE":
                    Path("delayed_cleanup_activation.json"),
                "validate_delayed_cleanup_activation":
                    validator or (lambda value: value == {"valid": True}),
                "DELAYED_CLEANUP_STATE": state,
            },
        )
        return namespace, state

    @unittest.skipUnless(
        hasattr(os, "getuid"),
        "receipt ownership and mode checks need POSIX uids")
    def test_activation_receipt_is_mac_only_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activation.json"
            path.write_text('{"valid": true}', encoding="utf-8")
            path.chmod(0o600)
            namespace, state = self.activation_namespace()

            self.assertTrue(
                namespace["load_delayed_cleanup_activation"](path))
            self.assertEqual(
                namespace["delayed_cleanup_activation_status"](),
                {"active": True, "status": "active",
                 "measurement_mode": False, "scheduling": True},
            )

            path.write_text('{"valid": false}', encoding="utf-8")
            path.chmod(0o600)
            self.assertFalse(
                namespace["load_delayed_cleanup_activation"](path))
            self.assertEqual(state["status"], "invalid")
            path.write_text('{"valid": true}', encoding="utf-8")
            path.chmod(0o644)
            self.assertFalse(
                namespace["load_delayed_cleanup_activation"](path))
            self.assertEqual(state["status"], "unsafe_permissions")
            path.unlink()
            self.assertFalse(
                namespace["load_delayed_cleanup_activation"](path))
            self.assertEqual(state["status"], "missing")

    def test_activation_receipt_is_refused_off_mac(self):
        # Split from the POSIX test above so the fails-closed non-mac
        # behavior stays covered on Windows CI, where os.getuid does not
        # exist and the ownership assertions cannot run.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activation.json"
            other, other_state = self.activation_namespace(is_macos=False)
            path.write_text('{"valid": true}', encoding="utf-8")
            self.assertFalse(other["load_delayed_cleanup_activation"](path))
            self.assertEqual(other_state["status"], "unsupported_platform")

    def test_scheduler_is_inert_until_active_and_copies_private_inputs(self):
        starts = []
        state = {
            "active": False,
            "status": "missing",
            "generation": 0,
            "lock": threading.Lock(),
        }
        pipeline = {}
        worker = object()
        namespace = load_definitions(
            "schedule_delayed_cleanup",
            "delayed_cleanup_activation_status",
            "delayed_cleanup_scheduling_enabled",
            extra={
                "MEASUREMENT_MODE": MeasurementMode.inert(),
                "IS_MACOS": True,
                "DELAYED_CLEANUP_STATE": state,
                "PIPELINE_STATE": pipeline,
                "_run_delayed_cleanup": worker,
            },
        )
        restore = {"sentinel": "private expansion"}
        arguments = (
            "proposal-1", "original", "compiled", "tone",
            SimpleNamespace(),
        )
        keywords = {
            "continuing": False,
            "context_tail": "private tail",
            "context_text": "private document",
            "tone_key": "formal",
            "snippet_restore": restore,
            "starter": lambda target, args: starts.append((target, args)),
        }

        self.assertFalse(
            namespace["schedule_delayed_cleanup"](*arguments, **keywords))
        self.assertEqual(starts, [])
        state["active"] = True
        self.assertTrue(
            namespace["schedule_delayed_cleanup"](*arguments, **keywords))
        restore["sentinel"] = "changed after scheduling"

        self.assertEqual(len(starts), 1)
        self.assertIs(starts[0][0], worker)
        self.assertEqual(starts[0][1][0:4], (
            1, "proposal-1", "original", "compiled"))
        self.assertEqual(
            starts[0][1][-1], {"sentinel": "private expansion"})
        self.assertEqual(
            pipeline["last_delayed_cleanup_outcome"], "scheduled")

    @staticmethod
    def runner_namespace(*, proposal, outcome="applied", measured=False,
                         raises=False, printed=None):
        state = {"active": True, "status": "active", "generation": 1,
                 "lock": threading.Lock()}
        pipeline = {}

        class Transactions:
            def apply(self, *_args):
                if raises:
                    raise RuntimeError("adapter blew up")
                return SimpleNamespace(
                    outcome=SimpleNamespace(value=outcome),
                    merge_applied_count=2,
                    merge_rejected_count=1)

        namespace = load_definitions(
            "_run_delayed_cleanup",
            extra={
                "build_delayed_cleanup_proposal":
                    lambda *_args, **_kwargs: proposal,
                "MacDestinationStateAdapter": lambda reader: SimpleNamespace(
                    capture=lambda: SimpleNamespace(snapshot=object()),
                    apply_if_unchanged=lambda *_args: True),
                "SystemMacDestinationStateReader": object,
                "DELAYED_CLEANUP_TRANSACTIONS": Transactions(),
                "DELAYED_CLEANUP_STATE": state,
                "PIPELINE_STATE": pipeline,
                "MEASUREMENT_MODE": MeasurementMode(
                    delayed_cleanup=measured),
                "time": time,
                "print": (printed.append if printed is not None else print),
            },
        )
        return namespace, pipeline

    def test_a_delayed_apply_is_timed_and_printed_in_the_parsed_shape(self):
        printed = []
        namespace, pipeline = self.runner_namespace(
            proposal=object(), printed=printed)

        namespace["_run_delayed_cleanup"](
            1, "proposal-1", "original", "compiled", "tone",
            SimpleNamespace(), False, "", None, "formal", {})

        self.assertEqual(len(printed), 1)
        self.assertRegex(
            printed[0],
            r"^\[delayed-cleanup\] applied; 2 applied, 1 held; "
            r"\d+\.\d{3} ms$")
        self.assertIsNotNone(pipeline["last_delayed_cleanup_apply_ms"])
        self.assertGreaterEqual(
            pipeline["last_delayed_cleanup_apply_ms"], 0.0)

    def test_a_measured_pass_marks_itself_after_the_duration(self):
        printed = []
        namespace, _ = self.runner_namespace(
            proposal=object(), measured=True, printed=printed)

        namespace["_run_delayed_cleanup"](
            1, "proposal-1", "original", "compiled", "tone",
            SimpleNamespace(), False, "", None, "formal", {})

        self.assertRegex(
            printed[0],
            r"^\[delayed-cleanup\] applied; 2 applied, 1 held; "
            r"\d+\.\d{3} ms; measurement-mode$")

    def test_a_pass_that_never_applied_reports_no_duration_at_all(self):
        for kwargs in ({"proposal": None}, {"proposal": object(),
                                            "raises": True}):
            with self.subTest(**kwargs):
                printed = []
                namespace, pipeline = self.runner_namespace(
                    printed=printed, **kwargs)

                namespace["_run_delayed_cleanup"](
                    1, "proposal-1", "original", "compiled", "tone",
                    SimpleNamespace(), False, "", None, "formal", {})

                self.assertNotIn(" ms", printed[0])
                self.assertIsNone(
                    pipeline["last_delayed_cleanup_apply_ms"])

    def test_proposal_requires_exact_voice_compiler_reconstruction(self):
        verified = []

        class Compiler:
            proof_text = "clean github"

            def verify_edits(
                    self, original, proposals, candidates, *, mode):
                verified.append(
                    (original, tuple(proposals), candidates, mode))
                return SimpleNamespace(text=self.proof_text)

        compiler = Compiler()
        namespace = load_definitions(
            "build_delayed_cleanup_proposal",
            extra={
                "VOICE_COMPILER": compiler,
                "EditProposal": lambda kind, before, after:
                    SimpleNamespace(
                        kind=kind, before=before, after=after),
                "llm_clean_with_edits": lambda *_args: (
                    "clean github",
                    (SimpleNamespace(
                        kind="replace", before="raw", after="clean"),),
                ),
                "quick_clean": lambda text, **_kwargs: text,
                "strip_casual_period": lambda text: text,
                "apply_vocabulary_casing":
                    lambda text: text.replace("github", "GitHub"),
                "_restore_snippet_sentinels": lambda text, _restore: text,
            },
        )
        voice_ir = SimpleNamespace(
            context=SimpleNamespace(candidates=("anchor",)))
        kwargs = {
            "continuing": False,
            "context_tail": "",
            "context_text": None,
            "tone_key": "formal",
            "snippet_restore": {},
        }

        self.assertEqual(
            namespace["build_delayed_cleanup_proposal"](
                "raw github", "tone", voice_ir, **kwargs),
            "clean GitHub",
        )
        self.assertEqual(
            (verified[0][0], verified[0][2], verified[0][3]),
            ("raw github", ("anchor",), "capture"),
        )
        compiler.proof_text = "not the claimed candidate"
        self.assertIsNone(
            namespace["build_delayed_cleanup_proposal"](
                "raw github", "tone", voice_ir, **kwargs))

    def test_delayed_proposal_keeps_a_word_that_starts_with_the_field(self):
        class Compiler:
            def verify_edits(self, original, proposals, candidates, *, mode):
                return SimpleNamespace(text="tomorrow works for me")

        namespace = load_definitions(
            "build_delayed_cleanup_proposal",
            "strip_context_echo",
            extra={
                "VOICE_COMPILER": Compiler(),
                "EditProposal": lambda kind, before, after:
                    SimpleNamespace(
                        kind=kind, before=before, after=after),
                "llm_clean_with_edits": lambda *_args: (
                    "tomorrow works for me", ()),
                "quick_clean": lambda text, **_kwargs: text,
                "strip_casual_period": lambda text: text,
                "apply_vocabulary_casing": lambda text: text,
                "_restore_snippet_sentinels": lambda text, _restore: text,
            },
        )
        voice_ir = SimpleNamespace(
            context=SimpleNamespace(candidates=()))

        def propose(field):
            return namespace["build_delayed_cleanup_proposal"](
                "tomorrow works for me", "tone", voice_ir,
                continuing=True, context_tail=field, context_text=field,
                tone_key="default", snippet_restore={})

        # The field holds "to": a prefix of the first word, not an echo.
        self.assertEqual(propose("to"), " tomorrow works for me")
        # The model repeated the field before continuing it.
        self.assertEqual(propose("tomorrow"), " works for me")

    def test_finish_schedules_only_after_verified_commit(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        finish = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "finish_and_process"
        )
        segment = ast.get_source_segment(source, finish)
        self.assertIn(
            "if needs_llm and not delayed_cleanup_requested:", segment)
        self.assertIn(
            "if verified and delayed_cleanup_requested:", segment)
        self.assertIn(
            "if learn_correction and verified and "
            "not delayed_cleanup_scheduled:",
            segment,
        )
        self.assertGreater(
            segment.index("schedule_delayed_cleanup("),
            segment.index("integrity_receipt = commit_insertion("),
        )
        main = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        self.assertIn(
            "load_delayed_cleanup_activation()",
            ast.get_source_segment(source, main),
        )


class ReadbackEdgeWhitespaceTests(unittest.TestCase):
    """A trimmed edge newline is proven delivery, not a conflict."""

    def _namespace(self):
        import re
        import unicodedata
        from insertion_integrity import ReadbackResult
        return load_definitions(
            "insertion_readback", "classify_readback_conflict",
            assignments={"READBACK_TIMEOUT"},
            extra={"ReadbackResult": ReadbackResult, "re": re,
                   "unicodedata": unicodedata},
        )

    @staticmethod
    def _snapshot():
        return types.SimpleNamespace(
            text="", selection=(0, 0), element=object())

    def _run(self, inserted, observed):
        ns = self._namespace()
        return ns["insertion_readback"](
            self._snapshot(), inserted, timeout=0.0,
            reader=lambda _element: observed,
            clock=lambda: 0.0, sleeper=lambda _s: None)

    def test_trailing_newline_difference_is_verified(self):
        result = self._run("hello there", "hello there\n")
        self.assertEqual(result.state.value, "verified")
        self.assertEqual(result.reason.value,
                         "commit_verified_edge_whitespace")

    def test_byte_exact_match_keeps_the_plain_verified_reason(self):
        result = self._run("hello there", "hello there")
        self.assertEqual(result.reason.value, "commit_verified")

    def test_reordered_content_is_still_a_conflict(self):
        result = self._run("alpha beta", "beta alpha")
        self.assertEqual(result.state.value, "conflict")

    def test_partial_delivery_is_still_a_conflict(self):
        result = self._run("alpha beta", "alpha")
        self.assertEqual(result.state.value, "conflict")
        self.assertEqual(result.detail, "observed-is-prefix")

    def test_whitespace_only_insert_cannot_verify_trivially(self):
        result = self._run("   ", "\n\n")
        self.assertEqual(result.state.value, "conflict")

    def test_classifier_separates_the_shapes_that_need_opposite_fixes(self):
        classify = self._namespace()["classify_readback_conflict"]
        self.assertEqual(classify("abc\n", "abc"), "trailing-whitespace")
        self.assertEqual(classify("ab", "abcd"), "observed-is-prefix")
        self.assertEqual(classify("xx abc xx", "abc"),
                         "expected-is-substring")
        self.assertEqual(classify("totally other", "abc"), "divergent")
        self.assertEqual(classify(None, "abc"), "observed-empty")
        self.assertEqual(classify("", "abc"), "observed-empty")


class InsertionJoinPrefixTests(unittest.TestCase):
    """Dictating twice in a row must not weld the sentences together."""

    def _join(self):
        return load_definitions(
            "insertion_join_prefix",
            assignments={"NO_JOIN_AFTER", "NO_JOIN_BEFORE", "NO_JOIN_AFTER", "NO_JOIN_BEFORE"},
        )["insertion_join_prefix"]

    @staticmethod
    def _snapshot(text, start=None):
        start = len(text) if start is None else start
        return types.SimpleNamespace(
            text=text, selection=(start, 0), element=object())

    def test_appending_after_a_finished_sentence_adds_one_space(self):
        join = self._join()
        self.assertEqual(
            join(self._snapshot("Hello there."), "Second sentence"), " ")

    def test_continuing_mid_sentence_also_needs_the_space(self):
        join = self._join()
        self.assertEqual(join(self._snapshot("I went to the"), "store"), " ")

    def test_empty_field_gets_no_leading_space(self):
        join = self._join()
        self.assertEqual(join(self._snapshot(""), "First words"), "")

    def test_existing_trailing_space_is_not_doubled(self):
        join = self._join()
        self.assertEqual(join(self._snapshot("Hello there. "), "Next"), "")
        self.assertEqual(join(self._snapshot("Hello\n"), "Next"), "")

    def test_opening_bracket_or_quote_stays_tight(self):
        join = self._join()
        for opener in ("(", "[", "{", "“", "'"):
            with self.subTest(opener=opener):
                self.assertEqual(
                    join(self._snapshot(f"see {opener}"), "inside"), "")

    def test_text_starting_with_attached_punctuation_stays_tight(self):
        join = self._join()
        for lead in (",", ".", "!", "?", ")", ":"):
            with self.subTest(lead=lead):
                self.assertEqual(
                    join(self._snapshot("word"), f"{lead} more"), "")

    def test_cursor_in_the_middle_uses_the_character_to_its_left(self):
        join = self._join()
        # Cursor after "the ", not at the end of the field.
        self.assertEqual(join(self._snapshot("the  end", start=4), "quick"), "")
        self.assertEqual(join(self._snapshot("the end", start=3), "quick"), " ")

    def test_unreadable_or_missing_destination_changes_nothing(self):
        join = self._join()
        self.assertEqual(join(None, "words"), "")
        blind = types.SimpleNamespace(
            text=None, selection=None, element=object())
        self.assertEqual(join(blind, "words"), "")

    def test_selection_start_beyond_the_text_is_ignored(self):
        join = self._join()
        self.assertEqual(join(self._snapshot("short", start=99), "words"), "")

    def test_replacing_a_selection_looks_before_the_selection(self):
        join = self._join()
        replacing = types.SimpleNamespace(
            text="the cat sat", selection=(4, 3), element=object())
        self.assertEqual(join(replacing, "dog"), "")


class OllamaChatTransportTests(unittest.TestCase):
    """Structured-output schema and streamed generation deadlines."""

    @staticmethod
    def namespace(post, installed=True):
        return load_definitions(
            "ollama_chat", "_ollama_stream_reply",
            extra={
                "OLLAMA_MODEL": "test-model",
                "OLLAMA_URL": "http://127.0.0.1:11434/api/chat",
                "LOCAL_MODEL_SESSION": SimpleNamespace(post=post),
                "cleanup_model_installed": lambda: installed,
                "time": time,
            },
        )

    @staticmethod
    def response(status=200, *, body=None, lines=None, text=""):
        return SimpleNamespace(
            status_code=status,
            text=text,
            json=lambda: body,
            iter_lines=lambda: iter(lines or []),
            close=lambda: None,
            raise_for_status=lambda: None,
        )

    def test_nothing_is_sent_when_the_cleanup_model_is_not_installed(self):
        # The default install has no cleanup model. Cleanup, warm-up,
        # keep-warm and the learning pass all reach the model through
        # ollama_chat, so none of them may put text on the wire.
        posted = []
        ns = load_definitions(
            "ollama_chat", "_ollama_stream_reply", "cleanup_model_installed",
            assignments=("OLLAMA_MODEL",),
            extra={
                "os": os,
                "Path": Path,
                "time": time,
                "OLLAMA_URL": "http://127.0.0.1:11434/api/chat",
                "LOCAL_MODEL_SESSION": SimpleNamespace(
                    post=lambda *a, **k: posted.append(k) or self.response(
                        body={"message": {"content": "ok"}})),
            },
        )
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"OLLAMA_MODELS": td}):
            with self.assertRaises(RuntimeError):
                ns["ollama_chat"](None, "Send the contract to Priya.")
            self.assertEqual(posted, [])

            manifest = (Path(td) / "manifests" / "registry.ollama.ai" /
                        "library" / "qwen3.5" / "4b")
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}")
            out, _ = ns["ollama_chat"](None, "Send the contract to Priya.")
        self.assertEqual(out, "ok")
        self.assertEqual(len(posted), 1)

    def test_schema_is_sent_and_non_streaming_path_is_unchanged(self):
        posted = []

        def post(url, json=None, timeout=None, stream=False):
            posted.append((json, stream))
            return self.response(body={
                "message": {"content": "{\"text\":\"ok\"}"},
                "done_reason": "stop",
            })

        ns = self.namespace(post)
        out, done = ns["ollama_chat"](
            "system", "user", json_mode=True,
            json_schema={"type": "object"})
        self.assertEqual(out, "{\"text\":\"ok\"}")
        self.assertEqual(done, "stop")
        payload, stream = posted[0]
        self.assertEqual(payload["format"], {"type": "object"})
        self.assertFalse(payload["stream"])
        self.assertFalse(stream)

    def test_schema_rejection_falls_back_to_plain_json_mode(self):
        posted = []

        def post(url, json=None, timeout=None, stream=False):
            posted.append(dict(json))
            if isinstance(json.get("format"), dict):
                return self.response(400, text="invalid format")
            return self.response(body={
                "message": {"content": "{}"}, "done_reason": "stop"})

        ns = self.namespace(post)
        out, _ = ns["ollama_chat"](
            "system", "user", json_mode=True,
            json_schema={"type": "object"})
        self.assertEqual(out, "{}")
        self.assertEqual(posted[1]["format"], "json")

    def test_streaming_assembles_chunks_and_reports_done_reason(self):
        lines = [
            json.dumps({"message": {"content": "Hello "}}).encode(),
            b"",
            json.dumps({"message": {"content": "world."},
                        "done": True, "done_reason": "stop"}).encode(),
        ]

        def post(url, json=None, timeout=None, stream=False):
            self.assertTrue(json["stream"])
            self.assertTrue(stream)
            return self.response(lines=lines)

        ns = self.namespace(post)
        out, done = ns["ollama_chat"](
            "system", "user", total_deadline=5.0)
        self.assertEqual(out, "Hello world.")
        self.assertEqual(done, "stop")

    def test_deadline_starts_before_the_first_post(self):
        # Connect time and compatibility retries spend the same whole-reply
        # budget as generation; a post that eats the entire budget leaves the
        # stream reader an already-expired deadline.
        clock = {"t": 100.0}
        lines = [json.dumps({"message": {"content": "x"}}).encode()] * 3

        def slow_post(url, json=None, timeout=None, stream=False):
            clock["t"] += 10.0     # the request itself burns the whole budget
            return self.response(lines=lines)

        ns = self.namespace(slow_post)
        ns["time"] = SimpleNamespace(monotonic=lambda: clock["t"])
        with self.assertRaises(TimeoutError):
            ns["ollama_chat"]("system", "user", total_deadline=5.0)

    def test_streaming_total_deadline_fails_rather_than_hanging(self):
        def endless():
            while True:
                yield json.dumps(
                    {"message": {"content": "x"}}).encode()

        ns = self.namespace(lambda *args, **kwargs: None)
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += 0.5
            return clock["t"]

        closed = []
        with self.assertRaises(TimeoutError):
            ns["_ollama_stream_reply"](
                SimpleNamespace(
                    iter_lines=endless,
                    close=lambda: closed.append(True)),
                deadline=1.0, clock=fake_monotonic)
        self.assertEqual(closed, [True])

    def test_think_flag_rejection_retries_without_think(self):
        posted = []

        def post(url, json=None, timeout=None, stream=False):
            posted.append(dict(json))
            if "think" in json:
                return self.response(400, text="think is not supported")
            return self.response(body={
                "message": {"content": "ok"}, "done_reason": "stop"})

        ns = self.namespace(post)
        out, _ = ns["ollama_chat"]("system", "user")
        self.assertEqual(out, "ok")
        self.assertNotIn("think", posted[-1])


class TailSilencePollTests(unittest.TestCase):
    """The post-release tail ends as soon as the silence run exists."""

    @staticmethod
    def waited(silent_samples_curve, *, end_silence=0.12, cap=0.30):
        clock = {"t": 0.0}
        curve = list(silent_samples_curve)
        rec = SimpleNamespace(silent_samples=curve.pop(0))

        def now():
            return clock["t"]

        def sleep(seconds):
            clock["t"] += seconds
            if curve:
                rec.silent_samples = curve.pop(0)

        ns = load_definitions(
            "wait_for_tail_silence",
            extra={
                "TAIL_SECONDS": cap,
                "SAMPLE_RATE": 16_000,
                "calibrated_end_silence_seconds": lambda: end_silence,
                "time": SimpleNamespace(
                    sleep=sleep, perf_counter=now),
            },
        )
        return ns["wait_for_tail_silence"](
            rec, sleep=sleep, now=now)

    def test_quiet_tail_exits_at_the_first_silence_run(self):
        # Silence run completes after two polls: 40 ms, not 300 ms.
        waited = self.waited([0, 500, 16_000 * 0.13])
        self.assertLess(waited, 0.05)

    def test_continuing_speech_still_caps_at_the_full_tail(self):
        waited = self.waited([0] * 100)
        self.assertAlmostEqual(waited, 0.30, places=2)

    def test_calibrated_end_silence_shortens_the_wait(self):
        # A 60 ms calibrated run is satisfied by the second sample.
        waited = self.waited(
            [0, 16_000 * 0.07, 16_000 * 0.07], end_silence=0.06)
        self.assertLess(waited, 0.05)


class ParakeetCrosscheckTests(unittest.TestCase):
    """Agreement-derived route confidence on the Parakeet primary path."""

    @staticmethod
    def crosschecked(*, parakeet_result, tiny_text=None, fallback=None,
                     crosscheck_enabled=True, tiny_cached=True,
                     calls=None):
        from parrot_core import (
            hypothesis_agreement,
            parakeet_confidence_from_agreement,
            should_escalate_uncertain,
        )
        calls = calls if calls is not None else []

        class ImmediatePool:
            def submit(self, fn, *args, **kwargs):
                return SimpleNamespace(result=lambda: fn(*args, **kwargs))

        def fake_transcribe_detailed(audio, prompt=None, verify=True,
                                     model_repo="turbo",
                                     crosscheck_text=None,
                                     _skip_parakeet=False,
                                     language="en", _prepared=False):
            # Record the language rather than merely tolerating it. The
            # cross-check is only meaningful against Parakeet's English, and
            # the caller's `except Exception` would otherwise swallow a
            # signature mismatch into a silent drop back to the fixed prior.
            calls.append((model_repo, _skip_parakeet, language))
            if model_repo == "tiny-repo":
                return Recognition(text=tiny_text or "", engine="tiny")
            return fallback or Recognition(text="", confidence=0.0)

        ns = load_definitions(
            "_parakeet_crosschecked",
            "_clean_native_processing_s",
            "crosscheck_agreement",
            assignments={"CROSSCHECK_MAJORITY_AGREEMENT"},
            extra={
                "PARAKEET_IO_POOL": ImmediatePool(),
                "PARAKEET": SimpleNamespace(
                    transcribe=lambda audio: parakeet_result),
                "PARAKEET_CROSSCHECK": crosscheck_enabled,
                "PARAKEET_CROSSCHECK_MAX_SECONDS": 90.0,
                "PARAKEET_ROUTE_CONFIDENCE": 0.84,
                "SAMPLE_RATE": 16_000,
                "WHISPER_REPO": "turbo-repo",
                "FAST_WHISPER_REPO": "tiny-repo",
                "asr_model_is_cached": lambda repo: tiny_cached,
                "transcribe_detailed": fake_transcribe_detailed,
                "hypothesis_agreement": hypothesis_agreement,
                "parakeet_confidence_from_agreement":
                    parakeet_confidence_from_agreement,
                "should_escalate_uncertain": should_escalate_uncertain,
                "Recognition": Recognition,
                "print": lambda *args, **kwargs: None,
            },
        )
        audio = np.zeros(16_000 * 4, dtype=np.float32)
        return ns["_parakeet_crosschecked"](audio, None, verify=True), calls

    def test_full_agreement_yields_high_confidence_and_no_alternative(self):
        recognition, _ = self.crosschecked(
            parakeet_result=("ship the installer today", 0.05),
            tiny_text="ship the installer today")
        self.assertEqual(recognition.engine, "parakeet-unified")
        self.assertAlmostEqual(recognition.confidence, 0.93)
        self.assertIsNone(recognition.alternative)
        self.assertEqual(recognition.native_processing_s, 0.05)

    def test_disagreement_drops_confidence_below_the_context_gate(self):
        recognition, _ = self.crosschecked(
            parakeet_result=("ship the installer to Berg today ok", 0.05),
            tiny_text="skip the finish line by the bay no")
        self.assertLess(recognition.confidence, 0.70)
        self.assertEqual(
            recognition.alternative, "skip the finish line by the bay no")

    def test_no_crosscheck_keeps_the_fixed_routing_prior(self):
        recognition, calls = self.crosschecked(
            parakeet_result=("hello there", 0.05),
            crosscheck_enabled=False)
        self.assertAlmostEqual(recognition.confidence, 0.84)
        self.assertEqual(calls, [])

    def test_missing_tiny_snapshot_keeps_the_fixed_routing_prior(self):
        recognition, calls = self.crosschecked(
            parakeet_result=("hello there", 0.05), tiny_cached=False)
        self.assertAlmostEqual(recognition.confidence, 0.84)
        self.assertEqual(calls, [])

    def test_helper_failure_returns_none_for_the_whisper_fallback(self):
        recognition, _ = self.crosschecked(
            parakeet_result=None, tiny_text="anything")
        self.assertIsNone(recognition)

    def test_severe_disagreement_escalates_once_and_keeps_the_majority(self):
        # Turbo's confidence is deliberately the lowest number in play: the
        # vote is on what was heard, never on scores from different scales.
        fallback = Recognition(
            text="One two three four.", confidence=0.2, engine="turbo")
        recognition, calls = self.crosschecked(
            parakeet_result=("Alpha beta gamma delta.", 0.05),
            tiny_text="one two three four",
            fallback=fallback)
        self.assertEqual(
            [repo for repo, _skip, _lang in calls],
            ["tiny-repo", "turbo-repo"])
        self.assertIn(("turbo-repo", True, "en"), calls)
        self.assertEqual(recognition.text, "One two three four.")
        self.assertEqual(recognition.engine, "turbo")
        self.assertTrue(recognition.verified)
        self.assertEqual(recognition.alternative, "Alpha beta gamma delta.")

    def test_turbo_agreeing_with_parakeet_keeps_parakeet_verified(self):
        # Turbo out-scores Parakeet's agreement-derived number here. It still
        # only gets a vote, and it votes for the words Parakeet heard.
        fallback = Recognition(
            text="ping Priya about Qwen", confidence=0.97, engine="turbo")
        recognition, _ = self.crosschecked(
            parakeet_result=("Ping Priya about Qwen.", 0.05),
            tiny_text="Being pre-about Gwen.",
            fallback=fallback)
        self.assertEqual(recognition.text, "Ping Priya about Qwen.")
        self.assertEqual(recognition.engine, "parakeet-unified")
        self.assertTrue(recognition.verified)
        # Two engines agree, so the route confidence is no longer the one
        # Tiny's dissent produced.
        self.assertAlmostEqual(recognition.confidence, 0.93)
        self.assertEqual(recognition.alternative, "ping Priya about Qwen")

    def test_three_way_disagreement_keeps_parakeet_unverified(self):
        fallback = Recognition(
            text="Red green blue pink.", confidence=0.97, engine="turbo")
        recognition, _ = self.crosschecked(
            parakeet_result=("Alpha beta gamma delta.", 0.05),
            tiny_text="One two three four.",
            fallback=fallback)
        self.assertEqual(recognition.text, "Alpha beta gamma delta.")
        self.assertEqual(recognition.engine, "parakeet-unified")
        self.assertFalse(recognition.verified)
        self.assertLess(recognition.confidence, 0.52)
        self.assertEqual(recognition.alternative, "Red green blue pink.")

    def test_agreement_ignores_case_punctuation_and_digit_grouping(self):
        # The same words written two ways are not two hearings: no lowered
        # confidence, no escalation, no Turbo decode.
        for parakeet_text, tiny_text in (
                ("Don\u2019t ship 1,500 units.", "don't ship 1500 units"),
                ("Okay, let's go.", "okay lets go"),
                ("Looks good.", "LOOKS GOOD!"),
        ):
            with self.subTest(parakeet=parakeet_text):
                recognition, calls = self.crosschecked(
                    parakeet_result=(parakeet_text, 0.05),
                    tiny_text=tiny_text,
                    fallback=Recognition(
                        text="something else", confidence=0.99,
                        engine="turbo"))
                self.assertEqual(recognition.text, parakeet_text)
                self.assertAlmostEqual(recognition.confidence, 0.93)
                self.assertEqual(
                    [repo for repo, _skip, _lang in calls], ["tiny-repo"])

    @staticmethod
    def cascade(*, parakeet_text=None, scripted, cached, parakeet=None,
                audio=None, levels=None, definitions=(), overrides=None):
        """Run the production cascade against scripted model outputs.

        ``cached`` is the set of Whisper snapshots on disk and ``scripted``
        maps a snapshot to ``(text, confidence)``. Model selection, the
        cross-check, the escalation and the arbitration are all production
        code; only the three recognizers are scripted. ``levels`` collects
        the peak level each recognizer was handed.
        """
        import math
        from parrot_core import (
            confidence_from_segments,
            hypothesis_agreement,
            parakeet_confidence_from_agreement,
            recognition_words_from_segments,
            should_escalate_uncertain,
        )
        decodes = []

        class ImmediatePool:
            def submit(self, fn, *args, **kwargs):
                return SimpleNamespace(result=lambda: fn(*args, **kwargs))

        def heard(name, samples):
            if levels is not None:
                levels[name] = round(float(np.max(np.abs(samples))), 4)

        def parakeet_transcribe(samples):
            heard("parakeet", samples)
            return (parakeet_text, 0.05)

        def mlx_transcribe(_audio, *, path_or_hf_repo, **_kwargs):
            decodes.append(path_or_hf_repo)
            heard(path_or_hf_repo, _audio)
            text, confidence = scripted[path_or_hf_repo]
            return {"text": f" {text}", "segments": [{
                "text": text, "start": 0.0, "end": 2.0,
                "avg_logprob": math.log(confidence)}]}

        def resolve(repo):
            if repo not in cached:
                raise FileNotFoundError("snapshot is not cached")
            return repo

        ns = load_definitions(
            "transcribe_detailed", "_parakeet_crosschecked",
            "_clean_native_processing_s", "crosscheck_agreement",
            "asr_decode_target", *definitions,
            assignments={"ASR_DEGRADED_NOTICES",
                         "CROSSCHECK_MAJORITY_AGREEMENT"},
            extra={
                "np": np,
                "Recognition": Recognition,
                "GLOSS": {"lock": threading.Lock(), "prompt": None},
                "IS_MACOS": True,
                "PARAKEET_ENABLED": True,
                "PARAKEET": parakeet or SimpleNamespace(
                    transcribe=parakeet_transcribe),
                "PARAKEET_IO_POOL": ImmediatePool(),
                "PARAKEET_CROSSCHECK": True,
                "PARAKEET_CROSSCHECK_MAX_SECONDS": 90.0,
                "PARAKEET_ROUTE_CONFIDENCE": 0.84,
                "SAMPLE_RATE": 16_000,
                "MIN_SECONDS": 0.4,
                "LOW_CONFIDENCE": 0.52,
                "WHISPER_REPO": "turbo-repo",
                "FAST_WHISPER_REPO": "tiny-repo",
                "asr_model_is_cached": lambda repo: repo in cached,
                "resolve_asr_model": resolve,
                "prepare_asr_audio": lambda audio: audio,
                "mlx_whisper": SimpleNamespace(transcribe=mlx_transcribe),
                "hypothesis_agreement": hypothesis_agreement,
                "parakeet_confidence_from_agreement":
                    parakeet_confidence_from_agreement,
                "should_escalate_uncertain": should_escalate_uncertain,
                "confidence_from_segments": confidence_from_segments,
                "recognition_words_from_segments":
                    recognition_words_from_segments,
                "mark_model_warm_path_observed": lambda _provider: None,
                "WHISPER_TINY_PROFILE": SimpleNamespace(provider_id="tiny"),
                "WHISPER_LARGE_TURBO_PROFILE": SimpleNamespace(
                    provider_id="turbo"),
                "print": lambda *args, **kwargs: None,
                **(overrides or {}),
            },
        )
        if audio is None:
            audio = np.full(16_000 * 2, 0.3, dtype=np.float32)
        recognition = ns["transcribe_detailed"](audio, language="en")
        return recognition, decodes

    def test_default_install_never_lets_tiny_overrule_parakeet(self):
        # A default install has Parakeet and Tiny only. Tiny's own confidence
        # beats the number its disagreement leaves Parakeet with in every row,
        # and it must not matter.
        for parakeet_text, tiny_text, tiny_confidence in (
                ("Qwen.", "Gwen.", 0.70),
                ("Thanks.", "Thank you.", 0.70),
                ("Ship the Kubernetes patch.",
                 "Shipped a cube and his patch.", 0.55),
                ("Call Dr. Nguyen.", "Call Doctor Win.", 0.72),
        ):
            with self.subTest(parakeet=parakeet_text):
                recognition, decodes = self.cascade(
                    parakeet_text=parakeet_text,
                    scripted={"tiny-repo": (tiny_text, tiny_confidence)},
                    cached={"tiny-repo"})
                self.assertEqual(recognition.text, parakeet_text)
                self.assertEqual(recognition.engine, "parakeet-unified")
                self.assertFalse(recognition.verified)
                self.assertEqual(recognition.alternative, tiny_text)
                # One cross-check decode. "Escalating" to a Turbo that is
                # not installed used to decode the same audio with Tiny again.
                self.assertEqual(decodes, ["tiny-repo"])

    def test_default_install_compiles_parakeet_as_the_primary_hypothesis(self):
        # The compiler ranks the delivered transcript first and demotes the
        # alternative. Parakeet must be the one it ranks first.
        recognition, _ = self.cascade(
            parakeet_text="Ping Priya about Qwen.",
            scripted={"tiny-repo": ("Being pre-about Gwen.", 0.71)},
            cached={"tiny-repo"})
        ns = load_definitions(
            "compile_voice_evidence",
            extra={
                "np": np,
                "WordEvidence": WordEvidence,
                "RecognitionHypothesis": RecognitionHypothesis,
                "ContextCandidate": ContextCandidate,
                "ContextPack": ContextPack,
                "VoiceIR": VoiceIR,
                "VOICE_COMPILER": VoiceCompiler(),
                "GLOSS": {"lock": threading.Lock(), "anchor_pack": None},
                "learned_alternatives": lambda _text, _bundle: [],
                "compiler_personal_priors": lambda _bundle: (),
                "analyze_prosody": lambda *_args: (),
                "SAMPLE_RATE": 16_000,
            },
        )
        voice, compiled = ns["compile_voice_evidence"](recognition)
        self.assertEqual(compiled.text, "Ping Priya about Qwen.")
        primary, alternative = voice.hypotheses
        self.assertEqual(
            (primary.text, primary.engine),
            ("Ping Priya about Qwen.", "parakeet-unified"))
        self.assertEqual(alternative.text, "Being pre-about Gwen.")
        self.assertGreater(primary.confidence, alternative.confidence)

    def test_full_install_escalates_to_the_real_turbo_and_takes_the_vote(self):
        recognition, decodes = self.cascade(
            parakeet_text="Ping Priya about Qwen.",
            scripted={
                "tiny-repo": ("Being pre-about Gwen.", 0.71),
                "turbo-repo": ("Ping Priya about Qwen.", 0.40),
            },
            cached={"tiny-repo", "turbo-repo"})
        self.assertEqual(decodes, ["tiny-repo", "turbo-repo"])
        self.assertEqual(recognition.text, "Ping Priya about Qwen.")
        self.assertEqual(recognition.engine, "parakeet-unified")
        self.assertTrue(recognition.verified)

        recognition, decodes = self.cascade(
            parakeet_text="Being pre-about Gwen.",
            scripted={
                "tiny-repo": ("Ping Priya about Qwen.", 0.71),
                "turbo-repo": ("Ping Priya about Qwen.", 0.40),
            },
            cached={"tiny-repo", "turbo-repo"})
        self.assertEqual(decodes, ["tiny-repo", "turbo-repo"])
        self.assertEqual(recognition.text, "Ping Priya about Qwen.")
        self.assertEqual(recognition.engine, "turbo")
        self.assertTrue(recognition.verified)
        self.assertEqual(recognition.alternative, "Being pre-about Gwen.")

    def test_escalation_failure_keeps_the_primary_transcript(self):
        # A raising Turbo fallback must not turn a working dictation into a
        # dropped one: the primary transcript survives escalation failure.
        from parrot_core import (
            hypothesis_agreement,
            parakeet_confidence_from_agreement,
            should_escalate_uncertain,
        )

        class ImmediatePool:
            def submit(self, fn, *args, **kwargs):
                return SimpleNamespace(result=lambda: fn(*args, **kwargs))

        ns = load_definitions(
            "_parakeet_crosschecked",
            "_clean_native_processing_s",
            "crosscheck_agreement",
            assignments={"CROSSCHECK_MAJORITY_AGREEMENT"},
            extra={
                "PARAKEET_IO_POOL": ImmediatePool(),
                "PARAKEET": SimpleNamespace(
                    transcribe=lambda audio: ("alpha beta gamma delta", 0.05)),
                "PARAKEET_CROSSCHECK": True,
                "PARAKEET_CROSSCHECK_MAX_SECONDS": 90.0,
                "PARAKEET_ROUTE_CONFIDENCE": 0.84,
                "SAMPLE_RATE": 16_000,
                "WHISPER_REPO": "turbo-repo",
                "FAST_WHISPER_REPO": "tiny-repo",
                "asr_model_is_cached": lambda repo: True,
                "transcribe_detailed": lambda *args, **kwargs: (
                    Recognition(text="one two three four", engine="tiny")
                    if kwargs.get("model_repo") == "tiny-repo"
                    or (len(args) >= 4 and args[3] == "tiny-repo")
                    else (_ for _ in ()).throw(
                        RuntimeError("turbo snapshot missing"))),
                "hypothesis_agreement": hypothesis_agreement,
                "parakeet_confidence_from_agreement":
                    parakeet_confidence_from_agreement,
                "should_escalate_uncertain": should_escalate_uncertain,
                "Recognition": Recognition,
                "print": lambda *args, **kwargs: None,
            },
        )
        audio = np.zeros(16_000 * 4, dtype=np.float32)
        recognition = ns["_parakeet_crosschecked"](audio, None, verify=True)
        self.assertEqual(recognition.text, "alpha beta gamma delta")
        # No third decode came back, so nothing verified this transcript.
        self.assertFalse(recognition.verified)
        self.assertEqual(recognition.alternative, "one two three four")

    def test_quiet_speech_gain_reaches_every_recognizer_once(self):
        # A whisper-level take is lifted by the capped gain before decoding.
        # The cross-check and the escalation decode the same prepared audio;
        # preparing it again lifted it past the cap for Whisper alone.
        levels = {}
        self.cascade(
            parakeet_text="Ping Priya about Qwen.",
            scripted={
                "tiny-repo": ("Being pre-about Gwen.", 0.71),
                "turbo-repo": ("Ping Priya about Qwen.", 0.40),
            },
            cached={"tiny-repo", "turbo-repo"},
            audio=np.full(16_000 * 2, 0.004, dtype=np.float32),
            levels=levels,
            definitions=("prepare_asr_audio",),
            overrides={
                "active_calibration_settings": lambda: None,
                "CalibrationSettings": type("CalibrationSettings", (), {}),
            })
        self.assertEqual(
            levels,
            {"parakeet": 0.1, "tiny-repo": 0.1, "turbo-repo": 0.1})

    def test_low_confidence_escalation_loser_stays_inspectable(self):
        fallback = Recognition(
            text="different words", confidence=0.1, engine="turbo")
        recognition, calls = self.crosschecked(
            parakeet_result=("alpha beta gamma delta", 0.05),
            tiny_text="one two three four",
            fallback=fallback)
        self.assertIn(("turbo-repo", True, "en"), calls)
        self.assertEqual(recognition.text, "alpha beta gamma delta")
        self.assertFalse(recognition.verified)
        self.assertEqual(recognition.alternative, "different words")

    def test_speculative_crosscheck_text_skips_the_extra_tiny_decode(self):
        recognition, calls = self.crosschecked(
            parakeet_result=("use the same words", 0.05),
            tiny_text="never decoded")
        self.assertTrue(
            any(repo == "tiny-repo" for repo, _skip, _lang in calls))

        calls_with_hint = []
        from parrot_core import (
            hypothesis_agreement,
            parakeet_confidence_from_agreement,
            should_escalate_uncertain,
        )

        class ImmediatePool:
            def submit(self, fn, *args, **kwargs):
                return SimpleNamespace(result=lambda: fn(*args, **kwargs))

        ns = load_definitions(
            "_parakeet_crosschecked",
            "_clean_native_processing_s",
            "crosscheck_agreement",
            assignments={"CROSSCHECK_MAJORITY_AGREEMENT"},
            extra={
                "PARAKEET_IO_POOL": ImmediatePool(),
                "PARAKEET": SimpleNamespace(
                    transcribe=lambda audio: ("use the same words", 0.05)),
                "PARAKEET_CROSSCHECK": True,
                "PARAKEET_CROSSCHECK_MAX_SECONDS": 90.0,
                "PARAKEET_ROUTE_CONFIDENCE": 0.84,
                "SAMPLE_RATE": 16_000,
                "WHISPER_REPO": "turbo-repo",
                "FAST_WHISPER_REPO": "tiny-repo",
                "asr_model_is_cached": lambda repo: True,
                "transcribe_detailed":
                    lambda *args, **kwargs: calls_with_hint.append(args)
                    or Recognition(text=""),
                "hypothesis_agreement": hypothesis_agreement,
                "parakeet_confidence_from_agreement":
                    parakeet_confidence_from_agreement,
                "should_escalate_uncertain": should_escalate_uncertain,
                "Recognition": Recognition,
                "print": lambda *args, **kwargs: None,
            },
        )
        audio = np.zeros(16_000 * 4, dtype=np.float32)
        recognition = ns["_parakeet_crosschecked"](
            audio, None, verify=True, crosscheck_text="use the same words")
        self.assertEqual(calls_with_hint, [])
        self.assertAlmostEqual(recognition.confidence, 0.93)


class BuiltInVerbatimToneTests(unittest.TestCase):
    """E1: terminals must resolve to verbatim through the built-in path."""

    def _tone_for(self, override=None):
        return load_definitions(
            "tone_for",
            assignments={"TONE", "CASUAL_APPS", "FORMAL_APPS",
                         "VERBATIM_APPS", "CODE_APPS"},
            extra={"app_tone_override": lambda _bundle: override},
        )["tone_for"]

    def test_verbatim_apps_resolve_to_verbatim_without_a_manual_override(self):
        tone_for = self._tone_for()
        for bundle in ("com.apple.Terminal", "com.googlecode.iterm2",
                       "net.kovidgoyal.kitty", "com.github.wez.wezterm"):
            with self.subTest(bundle=bundle):
                self.assertEqual(tone_for(bundle), "verbatim")

    def test_other_built_in_sets_are_unchanged(self):
        tone_for = self._tone_for()
        self.assertEqual(tone_for("com.tinyspeck.slackmacgap"), "casual")
        self.assertEqual(tone_for("com.apple.mail"), "formal")
        self.assertEqual(tone_for("com.microsoft.VSCode"), "code")
        self.assertEqual(tone_for("com.example.unknown"), "default")

    def test_a_manual_tone_override_still_wins_over_the_built_in_set(self):
        self.assertEqual(
            self._tone_for(override="casual")("com.apple.Terminal"), "casual")

    def test_verbatim_is_a_contract_and_carries_no_prompt_text(self):
        # The call site reads TONE with a fallback precisely because of this.
        namespace = load_definitions(
            "tone_for", assignments={"TONE"},
            extra={"app_tone_override": lambda _bundle: None})
        self.assertNotIn("verbatim", namespace["TONE"])


class HotkeyBindingTests(unittest.TestCase):
    """A1: the capture key is chosen, and the guard refuses typing keys."""

    def namespace(self):
        return load_definitions(
            "normalize_hotkey", "hotkey_label_for", "hotkey_shared_modes",
            "hotkey_binding_decision", "modifier_pressed_by",
            "hotkey_matches",
            assignments={"HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                         "HOTKEY_MODIFIER_FAMILY",
                         "HOTKEY_MODES_BY_MODIFIER"},
            extra={"IS_MACOS": True},
        )

    def test_ordinary_typing_keys_are_refused(self):
        decide = self.namespace()["hotkey_binding_decision"]
        for candidate in ("a", "Z", "7", "space", "enter", "tab", ".",
                          "backspace", "", "   ", None, 5, True):
            with self.subTest(candidate=candidate):
                verdict = decide(candidate)
                self.assertFalse(verdict["accepted"])
                self.assertEqual(verdict["reason"], "unsupported_key")

    def test_modifier_and_function_keys_are_accepted(self):
        decide = self.namespace()["hotkey_binding_decision"]
        for candidate in ("alt_r", "alt_l", "cmd_l", "ctrl_r", "shift_l",
                          "f1", "f13", "f20"):
            with self.subTest(candidate=candidate):
                verdict = decide(candidate)
                self.assertTrue(verdict["accepted"])
                self.assertEqual(verdict["name"], candidate)
                self.assertTrue(verdict["label"])

    def test_an_unbound_undo_key_is_allowed_only_where_it_is_meaningful(self):
        decide = self.namespace()["hotkey_binding_decision"]
        self.assertFalse(decide("")["accepted"])
        unbound = decide("", allow_unbound=True)
        self.assertTrue(unbound["accepted"])
        self.assertEqual(unbound["reason"], "unbound")
        self.assertEqual(unbound["name"], "")

    def test_labels_follow_the_platform(self):
        label = self.namespace()["hotkey_label_for"]
        self.assertEqual(label("alt_r", is_macos=True), "Right Option")
        self.assertEqual(label("alt_r", is_macos=False), "Right Alt")
        self.assertEqual(label("cmd_l", is_macos=True), "Left Command")
        self.assertEqual(label("cmd_l", is_macos=False), "Left Windows")
        self.assertEqual(label("f13", is_macos=True), "F13")
        self.assertEqual(label("", is_macos=True), "")

    def test_migration_keeps_right_option_when_no_preference_is_stored(self):
        normalize = self.namespace()["normalize_hotkey"]
        for stored in (None, "", "nonsense", 12, {}):
            with self.subTest(stored=stored):
                self.assertEqual(normalize(stored), "alt_r")
        self.assertEqual(normalize(None, default=""), "")

    def test_option_and_function_keys_leave_every_mode_reachable(self):
        shared = self.namespace()["hotkey_shared_modes"]
        for candidate in ("alt_r", "alt_l", "f1", "f13", "f20"):
            with self.subTest(candidate=candidate):
                self.assertEqual(shared(candidate), ())

    def test_mode_modifier_keys_declare_exactly_which_modes_they_share(self):
        shared = self.namespace()["hotkey_shared_modes"]
        # mode_from_modifiers reads shift/command/control, so a bound key from
        # one of those families is named here rather than silently colliding.
        self.assertEqual(shared("shift_r"), ("compose", "code"))
        self.assertEqual(shared("shift_l"), ("compose", "code"))
        self.assertEqual(shared("cmd_r"), ("edit", "command"))
        self.assertEqual(shared("ctrl_l"), ("reply", "command", "code"))

    def test_shared_modes_match_the_real_mode_table(self):
        """Derive the answer from parrot_core rather than restating it here.

        A mode "needs" a modifier when every combination that selects it holds
        that modifier down. Those are exactly the modes that, once the same
        key is the capture trigger, require the opposite-side key instead.
        """
        shared = self.namespace()["hotkey_shared_modes"]
        combinations = [
            frozenset(names) for names in (
                (), ("shift",), ("command",), ("control",),
                ("shift", "command"), ("shift", "control"),
                ("command", "control"),
                ("shift", "command", "control"))
        ]
        for candidate, modifier in (
                ("shift_r", "shift"), ("shift_l", "shift"),
                ("cmd_r", "command"), ("cmd_l", "command"),
                ("ctrl_r", "control"), ("ctrl_l", "control")):
            selections: dict[str, list] = {}
            for combination in combinations:
                mode = mode_from_modifiers(
                    "shift" in combination, "command" in combination,
                    "control" in combination)
                selections.setdefault(mode, []).append(combination)
            expected = {
                mode for mode, holding in selections.items()
                if all(modifier in combination for combination in holding)
            }
            with self.subTest(candidate=candidate):
                self.assertEqual(set(shared(candidate)), expected)
                # Capture is never listed: the trigger is not a modifier.
                self.assertNotIn("capture", shared(candidate))

    def test_the_bound_key_never_counts_as_its_own_modifier(self):
        contributes = self.namespace()["modifier_pressed_by"]
        families = (
            ("shift", {"shift_l", "shift_r"}),
            ("command", {"cmd_l", "cmd_r"}),
            ("control", {"ctrl_l", "ctrl_r"}),
        )
        # Binding Right Shift must leave plain Capture reachable: the trigger
        # is not also a modifier.
        self.assertIsNone(contributes("shift_r", "shift_r", families))
        self.assertEqual(contributes("shift_l", "shift_r", families), "shift")
        self.assertEqual(contributes("cmd_r", "shift_r", families), "command")
        self.assertIsNone(contributes("alt_r", "alt_r", families))
        self.assertIsNone(contributes("f13", "alt_r", families))

    class _Win32KeyCode:
        """pynput 1.8.2 ``keyboard._base.KeyCode`` equality, with the
        ``_flags``/``_scan`` platform extensions ``keyboard._win32`` adds.

        pynput itself is not a test dependency; this keeps exactly the
        comparison its enum members use.
        """

        _PLATFORM_EXTENSIONS = ("_flags", "_scan")

        def __init__(self, vk=None, char=None, _flags=None, _scan=None):
            self.vk, self.char, self.is_dead = vk, char, False
            self._flags, self._scan = _flags, _scan

        def __repr__(self):
            return repr(self.char) if self.char is not None else (
                "<%d>" % self.vk)

        def __eq__(self, other):
            if not isinstance(other, self.__class__):
                return False
            if self.char is not None and other.char is not None:
                return (self.char == other.char
                        and self.is_dead == other.is_dead)
            return self.vk == other.vk and all(
                getattr(self, name) == getattr(other, name)
                for name in self._PLATFORM_EXTENSIONS)

        def __hash__(self):
            return hash(repr(self))

    def win32_keyboard(self):
        """The Windows Key table as pynput 1.8.2 defines and delivers it."""
        code = self._Win32KeyCode
        extended = 1   # KEYBDINPUT.EXTENDEDKEY, as KeyCode._from_ext sets
        key = enum.Enum("Key", [
            ("alt", code(0x12)),
            ("alt_l", code(0xA4)),
            ("alt_r", code(0xA5, _flags=extended)),
            ("alt_gr", code(0xA5)),
            ("cmd", code(0x5B)),
            ("cmd_l", code(0x5B)),
            ("cmd_r", code(0x5C)),
            ("ctrl", code(0x11)),
            ("ctrl_l", code(0xA2)),
            ("ctrl_r", code(0xA3, _flags=extended)),
            ("shift", code(0xA0)),
            ("shift_l", code(0xA0)),
            ("shift_r", code(0xA1)),
            ("f13", code(0x7C)),
        ])
        # keyboard._win32.Listener._SPECIAL_KEYS: the later member wins.
        delivered = {member.value.vk: member for member in key}
        return SimpleNamespace(Key=key), delivered

    def win32_namespace(self, keyboard):
        return load_definitions(
            "normalize_hotkey", "hotkey_key_object", "hotkey_matches",
            "modifier_pressed_by", "windows_hotkey_event_filter",
            assignments={"HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                         "WINDOWS_VK_LCONTROL",
                         "WINDOWS_ALTGR_SYNTHETIC_CTRL_SCAN"},
            extra={"IS_MACOS": False, "IS_WINDOWS": True,
                   "keyboard": keyboard},
        )

    def test_windows_right_alt_starts_the_default_capture_key(self):
        keyboard, delivered = self.win32_keyboard()
        ns = self.win32_namespace(keyboard)
        bound = ns["hotkey_key_object"]("alt_r")
        right_alt = delivered[0xA5]

        # The library shape behind the defect: Right Alt is delivered as a
        # member that is not equal to the one the default binding names.
        self.assertIs(right_alt, keyboard.Key.alt_gr)
        self.assertNotEqual(right_alt, bound)

        self.assertTrue(ns["hotkey_matches"](right_alt, bound))
        for other in (0xA4, 0xA2, 0xA3, 0xA0, 0xA1, 0x5B, 0x7C):
            with self.subTest(vk=hex(other)):
                self.assertFalse(ns["hotkey_matches"](delivered[other], bound))
        self.assertFalse(ns["hotkey_matches"](right_alt, None))
        # Every other bindable Windows key is still matched by itself only.
        for name in ("alt_l", "ctrl_r", "ctrl_l", "shift_r", "cmd_r", "f13"):
            with self.subTest(name=name):
                key = ns["hotkey_key_object"](name)
                self.assertTrue(ns["hotkey_matches"](
                    delivered[key.value.vk], key))
                self.assertFalse(ns["hotkey_matches"](right_alt, key))

    def test_altgr_layouts_start_capture_not_reply(self):
        keyboard, delivered = self.win32_keyboard()
        ns = self.win32_namespace(keyboard)
        Key = keyboard.Key

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_uint32),
                ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        families = (
            ("shift", {Key.shift, Key.shift_l, Key.shift_r}),
            ("command", {Key.cmd, Key.cmd_l, Key.cmd_r}),
            ("control", {Key.ctrl, Key.ctrl_l, Key.ctrl_r}),
        )
        bound = ns["hotkey_key_object"]("alt_r")
        # German, French and Spanish layouts: Right Alt is AltGr, and Windows
        # sends a synthetic Left Ctrl (scan 0x21D) first.
        events = (KBDLLHOOKSTRUCT(0xA2, 0x21D), KBDLLHOOKSTRUCT(0xA5, 0x38))
        modifiers = set()
        started = []
        for data in events:
            if ns["windows_hotkey_event_filter"](0x0104, data) is False:
                continue
            key = delivered[data.vkCode]
            modifier = ns["modifier_pressed_by"](key, bound, families)
            if modifier is not None:
                modifiers.add(modifier)
            if ns["hotkey_matches"](key, bound):
                started.append(mode_from_modifiers(
                    "shift" in modifiers, "command" in modifiers,
                    "control" in modifiers))

        self.assertEqual(started, ["capture"])
        # A real Left Ctrl (scan 0x1D) still reaches the listener, so Reply
        # stays reachable by holding it on purpose.
        self.assertTrue(ns["windows_hotkey_event_filter"](
            0x0100, KBDLLHOOKSTRUCT(0xA2, 0x1D)))

    def settings_namespace(self, path):
        return load_definitions(
            "set_hotkey", "set_undo_hotkey", "apply_hotkey_bindings",
            "normalize_hotkey", "hotkey_key_object", "save_preferences",
            "normalize_sound_theme", "hotkey_binding_decision",
            "hotkey_label_for", "hotkey_shared_modes",
            assignments={
                "PREFERENCES", "HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                "HOTKEY_MODIFIER_FAMILY", "HOTKEY_MODES_BY_MODIFIER",
                "SOUND_THEMES", "SOUND_THEME_DEFAULT",
            },
            extra={
                "IS_MACOS": True,
                "PREFERENCES_FILE": path,
                "keyboard": SimpleNamespace(Key=SimpleNamespace(
                    alt_r="alt_r", f13="f13", f14="f14")),
                "current_face": lambda: "parrot",
                "print": lambda *_a, **_k: None,
                "atomic_write_text": lambda target, value:
                    target.write_text(value, encoding="utf-8"),
            },
        )

    def test_the_capture_key_cannot_take_the_undo_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.settings_namespace(path)
            self.assertTrue(ns["set_undo_hotkey"]("f14")["accepted"])

            verdict = ns["set_hotkey"]("f14")

            self.assertFalse(verdict["accepted"])
            self.assertEqual(verdict["reason"], "conflicts_with_undo")
            stored = json.loads(path.read_text())
            self.assertEqual(
                (stored["hotkey"], stored["undo_hotkey"]), ("alt_r", "f14"))
            self.assertEqual(ns["HOTKEY"], "alt_r")
            self.assertEqual(ns["UNDO_HOTKEY"], "f14")
            # Any other key is still accepted.
            self.assertTrue(ns["set_hotkey"]("f13")["accepted"])

    def test_a_stored_undo_key_equal_to_the_capture_key_is_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.settings_namespace(path)
            ns["PREFERENCES"].update(hotkey="f14", undo_hotkey="f14")

            self.assertEqual(ns["apply_hotkey_bindings"](), ("f14", ""))

            self.assertEqual(ns["HOTKEY"], "f14")
            self.assertIsNone(ns["UNDO_HOTKEY"])
            self.assertEqual(ns["PREFERENCES"]["undo_hotkey"], "")


class RecentDictationTests(unittest.TestCase):
    """A3: metadata in the menu, text only after an explicit reveal."""

    LOG = [
        json.dumps({"ts": 1000.0, "app": "com.apple.mail",
                    "clean": "alpha bravo charlie", "id": "a"}),
        json.dumps({"ts": 2000.0, "app": "com.apple.Terminal",
                    "clean": "delta", "id": "b"}),
        "{ not json at all",
        json.dumps({"ts": 3000.0, "app": "", "clean": "   "}),
        json.dumps({"ts": 3600.0, "app": "com.apple.mail",
                    "clean": "echo foxtrot golf hotel", "id": "c"}),
    ]

    def namespace(self):
        return load_definitions(
            "recent_dictation_metadata", "recent_dictation_menu_title",
            "describe_dictation_age", "undo_menu_title",
            assignments={"RECENT_DICTATION_LIMIT"},
            extra={"time": SimpleNamespace(time=lambda: 3700.0),
                   "math": __import__("math"),
                   "TRANSCRIPTS_LOCK": threading.Lock(),
                   "app_display_name":
                       lambda bundle: bundle.rsplit(".", 1)[-1]},
        )

    def test_metadata_never_contains_the_dictated_text(self):
        ns = self.namespace()
        entries = ns["recent_dictation_metadata"](lines=self.LOG, now=3700.0)
        self.assertEqual([entry["id"] for entry in entries], ["c", "b", "a"])
        serialized = json.dumps(entries)
        for text in ("alpha", "bravo", "charlie", "delta", "echo",
                     "foxtrot", "golf", "hotel"):
            self.assertNotIn(text, serialized)
        self.assertEqual(entries[0]["words"], 4)
        self.assertEqual(entries[0]["app"], "mail")

    def test_the_list_is_bounded_and_skips_unusable_entries(self):
        ns = self.namespace()
        entries = ns["recent_dictation_metadata"](
            limit=2, lines=self.LOG, now=3700.0)
        self.assertEqual(len(entries), 2)
        # A blank transcript and a corrupt line are simply not offered.
        self.assertEqual(len(ns["recent_dictation_metadata"](
            lines=self.LOG, now=3700.0)), 3)

    def test_older_entries_without_an_id_stay_addressable_by_position(self):
        ns = self.namespace()
        log = [json.dumps({"ts": 1.0, "app": "x", "clean": "no id here"})]
        entries = ns["recent_dictation_metadata"](lines=log, now=2.0)
        self.assertEqual(entries[0]["id"], "line:1")

    def test_menu_titles_carry_no_text(self):
        ns = self.namespace()
        entries = ns["recent_dictation_metadata"](lines=self.LOG, now=3700.0)
        titles = [ns["recent_dictation_menu_title"](e) for e in entries]
        self.assertEqual(titles[0], "1 min ago · 4 words · mail")
        self.assertEqual(titles[1], "28 min ago · 1 word · Terminal")
        for title in titles:
            for text in ("alpha", "bravo", "charlie", "delta", "echo",
                         "foxtrot", "golf", "hotel"):
                self.assertNotIn(text, title)

    def test_age_is_coarse_and_survives_nonsense(self):
        describe = self.namespace()["describe_dictation_age"]
        self.assertEqual(describe(0), "just now")
        self.assertEqual(describe(59), "just now")
        self.assertEqual(describe(120), "2 min ago")
        self.assertEqual(describe(7200), "2 hr ago")
        self.assertEqual(describe(172800), "2d ago")
        self.assertEqual(describe(float("nan")), "just now")
        self.assertEqual(describe("later"), "just now")

    def test_undo_menu_title_names_state_never_content(self):
        title = self.namespace()["undo_menu_title"]
        self.assertEqual(title({"available": False}), "Undo Last Dictation")
        self.assertEqual(
            title({"available": True, "app": "Mail"}, "F14"),
            "Undo Last Dictation in Mail (F14)")
        self.assertEqual(
            title({"available": True, "app": ""}, ""),
            "Undo Last Dictation")


class UndoLastDictationTests(unittest.TestCase):
    """A2: undo is an insertion in reverse, held to the same standard."""

    BEFORE = "Dear Ada, "
    INSERTED = "thanks for the note."

    def namespace(self, *, field=None, destination="mail:field-1"):
        from insertion_integrity import (
            DestinationObservation,
            InsertionCoordinator,
            InsertionLease,
            ReadbackResult,
            ReceiptState,
        )

        element = object()
        state = {
            "text": (field if field is not None
                     else self.BEFORE + self.INSERTED),
            "keys": [],
            "pasted": [],
        }

        def press(*keys):
            state["keys"].append(tuple(getattr(k, "name", k) for k in keys))
            # Selecting then deleting is what the real chord does; the fake
            # field applies the same substitution so readback is meaningful.
            if len(keys) == 1 and getattr(keys[0], "name", "") == "backspace":
                state["text"] = self.BEFORE

        def paste(text):
            state["pasted"].append(text)
            state["text"] = self.BEFORE + text

        namespace = load_definitions(
            "undo_last_dictation", "undo_utterance_id", "undo_refusal",
            "UndoableInsertion", "record_undoable_insertion",
            "undoable_insertion_status", "note_undone_utterance",
            "utterance_was_undone", "build_undoable_insertion",
            "bounded_focus_text", "destination_observation",
            "insertion_readback", "classify_readback_conflict",
            "FocusSnapshot", "grapheme_count", "_grapheme_attaches",
            extra={
                "dataclass": dataclass,
                "time": time,
                "math": __import__("math"),
                "unicodedata": __import__("unicodedata"),
                "DestinationObservation": DestinationObservation,
                "InsertionLease": InsertionLease,
                "ReadbackResult": ReadbackResult,
                "ReceiptState": ReceiptState,
                "INSERTION_COORDINATOR": InsertionCoordinator(),
                "UNDO_COORDINATOR": InsertionCoordinator(),
                # A field that does not take a selection through
                # Accessibility, so undo selects with the keyboard.
                "_ax_select_text_range": lambda *_args: None,
                "UNDOABLE_INSERTION": {"record": None,
                                       "lock": threading.RLock()},
                "UNDONE_UTTERANCES": __import__("collections").deque(
                    maxlen=64),
                "UNDONE_UTTERANCES_LOCK": threading.Lock(),
                "UNDO_MAX_CHARACTERS": 2000,
                "LAST_INSERTION": None,
                "app_display_name": lambda bundle: bundle,
                "keyboard": SimpleNamespace(Key=SimpleNamespace(
                    shift=SimpleNamespace(name="shift"),
                    left=SimpleNamespace(name="left"),
                    backspace=SimpleNamespace(name="backspace"))),
                # The real helper folds the frontmost bundle into the id, so
                # the fake must too: that is what makes focus drift visible.
                "focus_destination_id": lambda snapshot, bundle: (
                    f"{bundle}:{destination}" if snapshot is not None
                    else None),
                "focus_destination_matches": lambda *_a: True,
                "opaque_focus_context": lambda _snapshot: "",
                "READBACK_TIMEOUT": 0.0,
                "_ax_text": lambda _element: state["text"],
                "print": lambda *_a, **_k: None,
                "paste": paste,
                "_press_edit_chord": press,
                "focused_snapshot": lambda: None,
                "frontmost_bundle": lambda: "com.apple.mail",
            },
        )
        namespace["_state"] = state
        namespace["_element"] = element
        return namespace

    def _record(self, ns):
        return ns["UndoableInsertion"](
            element=ns["_element"],
            bundle="com.apple.mail",
            destination_id="com.apple.mail:mail:field-1",
            utterance_id="utterance-1",
            inserted=self.INSERTED,
            before=self.BEFORE,
            selection=(len(self.BEFORE), 0),
        )

    def _snapshot(self, ns, *, text=None, caret=None):
        return ns["FocusSnapshot"](
            element=ns["_element"],
            text=ns["_state"]["text"] if text is None else text,
            selection=(len(self.BEFORE + self.INSERTED), 0)
            if caret is None else caret,
        )

    def test_undo_restores_the_exact_prior_text(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns))

        self.assertTrue(result["undone"])
        self.assertEqual(ns["_state"]["text"], self.BEFORE)
        # Exactly one shift+left per inserted character, then one delete.
        self.assertEqual(
            ns["_state"]["keys"].count(("shift", "left")),
            len(self.INSERTED))
        self.assertEqual(ns["_state"]["keys"][-1], ("backspace",))

    def test_undo_restores_replaced_text_rather_than_deleting_it(self):
        ns = self.namespace()
        record = ns["UndoableInsertion"](
            element=ns["_element"],
            bundle="com.apple.mail",
            destination_id="com.apple.mail:mail:field-1",
            utterance_id="utterance-2",
            inserted=self.INSERTED,
            before="Dear Ada, hello",
            selection=(10, 5),
        )
        ns["record_undoable_insertion"](record)
        ns["_state"]["text"] = record.expected
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: ns["FocusSnapshot"](
                element=ns["_element"], text=record.expected,
                selection=(10 + len(self.INSERTED), 0)))

        self.assertTrue(result["undone"])
        self.assertEqual(ns["_state"]["pasted"], ["hello"])

    def test_undo_refuses_when_focus_moved(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns),
            bundle_reader=lambda: "com.tinyspeck.slackmacgap")

        self.assertFalse(result["undone"])
        self.assertEqual(result["reason"], "focus_drift")
        self.assertEqual(ns["_state"]["text"], self.BEFORE + self.INSERTED)
        self.assertEqual(ns["_state"]["keys"], [])

    def test_undo_refuses_when_the_caret_moved(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns, caret=(3, 0)))

        self.assertFalse(result["undone"])
        self.assertEqual(result["reason"], "selection_drift")
        self.assertEqual(ns["_state"]["keys"], [])

    def test_undo_refuses_when_the_surrounding_text_changed(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        # Same length, so the caret still matches: this must be caught by
        # the surrounding-text fingerprint and nothing else.
        edited = "Dear Zed, " + self.INSERTED
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(
                ns, text=edited, caret=(len(edited), 0)))

        self.assertFalse(result["undone"])
        self.assertEqual(result["reason"], "surrounding_text_drift")
        self.assertEqual(ns["_state"]["keys"], [])

    def test_undo_refuses_when_the_destination_cannot_be_read(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        result = ns["undo_last_dictation"](snapshot_reader=lambda: None)

        self.assertFalse(result["undone"])
        self.assertEqual(result["reason"], "target_unreadable")
        self.assertEqual(ns["_state"]["keys"], [])

    def test_undo_is_single_use(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        first = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns))
        self.assertTrue(first["undone"])

        # Re-arming the same utterance cannot produce a second undo, even
        # against a field that looks exactly as it did after the insertion:
        # the coordinator already holds a terminal entry for that id.
        ns["_state"]["text"] = self.BEFORE + self.INSERTED
        keys_before = list(ns["_state"]["keys"])
        ns["record_undoable_insertion"](self._record(ns))
        second = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns))
        self.assertFalse(second["undone"])
        self.assertEqual(second["reason"], "already_undone")
        self.assertEqual(ns["_state"]["keys"], keys_before)

    def test_a_refusal_before_any_keystroke_keeps_the_undo(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        refused = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns, caret=(3, 0)))

        self.assertEqual(refused["reason"], "selection_drift")
        self.assertTrue(ns["undoable_insertion_status"]()["available"])
        # The caret is back where the dictation left it: the same undo runs.
        retried = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns))
        self.assertTrue(retried["undone"])
        self.assertEqual(ns["_state"]["text"], self.BEFORE)

    def test_a_refused_undo_never_reaches_the_voice_outbox(self):
        for name, kwargs in (
                ("focus", {"bundle_reader": lambda: "com.other.app"}),
                ("caret", {"caret": (3, 0)}),
                ("unreadable", {"unreadable": True})):
            with self.subTest(name):
                ns = self.namespace()
                ns["record_undoable_insertion"](self._record(ns))
                caret = kwargs.get("caret")
                reader = (lambda: None) if kwargs.get("unreadable") \
                    else (lambda: self._snapshot(ns, caret=caret))
                result = ns["undo_last_dictation"](
                    snapshot_reader=reader,
                    bundle_reader=kwargs.get("bundle_reader"))

                self.assertFalse(result["undone"])
                self.assertEqual(ns["INSERTION_COORDINATOR"].recoverable(), ())
                self.assertEqual(ns["UNDO_COORDINATOR"].recoverable(), ())
                self.assertEqual(ns["_state"]["keys"], [])

    def test_an_unproven_undo_leaves_the_voice_outbox_as_it_was(self):
        from insertion_integrity import (
            DestinationObservation,
            InsertionCoordinator,
            InsertionLease,
            ReadbackResult,
        )

        capacity = load_definitions(
            assignments={"VOICE_OUTBOX_MAX_ITEMS"})["VOICE_OUTBOX_MAX_ITEMS"]
        # The dictation replaced words the user had selected, so what undo
        # stages is the user's own text and never a blank.
        replaced = "it's \"great\" news."
        for name in ("the app rewrote what undo pasted",
                     "the selection was never confirmed"):
            with self.subTest(name):
                ns = self.namespace()
                # A full Voice Outbox: dictations that never reached a field.
                outbox = InsertionCoordinator(max_recoverable=capacity)
                ns["INSERTION_COORDINATOR"] = outbox
                elsewhere = DestinationObservation.capture(
                    "com.apple.Notes:field-9", (0, 0), "")
                for number in range(capacity):
                    lease = InsertionLease.capture(
                        f"waiting-{number}", "com.apple.mail:mail:field-1",
                        (0, 0), "")
                    outbox.stage(lease, f"See you at {number + 1}.")
                    outbox.commit(lease.utterance_id, elsewhere,
                                  lambda _text: None, ReadbackResult.verified)
                waiting = outbox.recoverable()
                self.assertEqual(len(waiting), capacity)

                record = ns["UndoableInsertion"](
                    element=ns["_element"], bundle="com.apple.mail",
                    destination_id="com.apple.mail:mail:field-1",
                    utterance_id="utterance-3", inserted=self.INSERTED,
                    before=self.BEFORE + replaced,
                    selection=(len(self.BEFORE), len(replaced)))
                ns["record_undoable_insertion"](record)
                ns["_state"]["text"] = record.expected

                def smart_quotes(text):
                    ns["_state"]["text"] = self.BEFORE + text.replace(
                        "'", "’").replace("\"", "”")

                attempt = ({"paster": smart_quotes}
                           if name.startswith("the app")
                           else {"selector": lambda *_args: False})
                result = ns["undo_last_dictation"](
                    snapshot_reader=lambda: ns["FocusSnapshot"](
                        element=ns["_element"], text=ns["_state"]["text"],
                        selection=record.caret),
                    **attempt)

                self.assertFalse(result["undone"])
                self.assertEqual(outbox.recoverable(), waiting)
                self.assertEqual(ns["UNDO_COORDINATOR"].recoverable(), ())

    def test_undo_selects_through_accessibility_when_the_field_allows_it(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        requests = []

        def select(element, text, selection, expected):
            requests.append((element, text, selection, expected))
            return True

        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns), selector=select)

        self.assertTrue(result["undone"])
        self.assertEqual(requests, [(
            ns["_element"], self.BEFORE + self.INSERTED,
            (len(self.BEFORE), len(self.INSERTED)), self.INSERTED)])
        self.assertEqual(ns["_state"]["keys"], [("backspace",)])
        self.assertEqual(ns["_state"]["text"], self.BEFORE)

    def test_an_unconfirmed_selection_deletes_nothing(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns),
            selector=lambda *_args: False)

        self.assertFalse(result["undone"])
        self.assertEqual(ns["_state"]["keys"], [])
        self.assertEqual(ns["_state"]["text"], self.BEFORE + self.INSERTED)
        self.assertEqual(ns["INSERTION_COORDINATOR"].recoverable(), ())
        self.assertEqual(ns["UNDO_COORDINATOR"].recoverable(), ())

    def test_keyboard_selection_never_reaches_the_users_own_text(self):
        # What the user sees as one character each: a heart with its
        # presentation selector, a flag, a skin-toned ZWJ profession, an
        # accented letter written with a combining mark.
        user = "Dear Sam, "
        inserted = ["t", "h", "a", "n", "k", "s", " ", "❤️", " ",
                    "\U0001F1FA\U0001F1F8", " ",
                    "\U0001F469\U0001F3FD‍\U0001F4BB", " ",
                    "é", "!"]
        field = {"characters": list(user) + inserted, "anchor": None}
        ns = self.namespace()
        ns["_state"]["text"] = "".join(field["characters"])

        def press(*keys):
            names = tuple(getattr(key, "name", key) for key in keys)
            ns["_state"]["keys"].append(names)
            end = len(field["characters"])
            if names == ("shift", "left"):
                # The system extends a selection by one whole character.
                field["anchor"] = max(0, (end if field["anchor"] is None
                                          else field["anchor"]) - 1)
            elif names == ("backspace",):
                del field["characters"][field["anchor"]:]
                field["anchor"] = None
            ns["_state"]["text"] = "".join(field["characters"])

        record = ns["UndoableInsertion"](
            element=ns["_element"], bundle="com.apple.mail",
            destination_id="com.apple.mail:mail:field-1",
            utterance_id="utterance-emoji", inserted="".join(inserted),
            before=user, selection=(len(user), 0))
        ns["record_undoable_insertion"](record)
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: ns["FocusSnapshot"](
                element=ns["_element"], text=ns["_state"]["text"],
                selection=record.caret),
            presser=press)

        self.assertTrue(result["undone"])
        self.assertEqual(ns["_state"]["text"], user)
        self.assertEqual(ns["_state"]["keys"].count(("shift", "left")),
                         len(inserted))

    def _accessibility_selection(self, text, caret, *, settable, applies):
        state = {"range": (len(text[:caret].encode("utf-16-le")) // 2, 0),
                 "selected": "", "writes": []}

        def set_value(_element, _attribute, value):
            state["writes"].append(value)
            if applies:
                start, length = value
                encoded = text.encode("utf-16-le")
                state["range"] = value
                state["selected"] = encoded[
                    2 * start:2 * (start + length)].decode("utf-16-le")
            return 0

        services = SimpleNamespace(
            AXUIElementIsAttributeSettable=lambda *_args: (0, settable),
            AXUIElementSetAttributeValue=set_value,
            AXValueCreate=lambda _kind, value: value,
            AXValueGetType=lambda _value: None,
            AXValueGetValue=lambda *_args: None,
            kAXSelectedTextAttribute="AXSelectedText",
            kAXSelectedTextRangeAttribute="AXSelectedTextRange",
            kAXValueCFRangeType=4,
        )
        now = [0.0]
        ns = load_definitions(
            "_ax_select_text_range", "code_points_to_utf16_range",
            "_ax_reported_range", "_coerce_range",
            assignments={"READBACK_TIMEOUT"},
            extra={
                "time": SimpleNamespace(
                    monotonic=lambda: now[0],
                    sleep=lambda delay: now.__setitem__(0, now[0] + delay)),
                "_ax_attribute": lambda _element, attribute: (
                    state["range"] if attribute == "AXSelectedTextRange"
                    else state["selected"]),
            },
        )
        with mock.patch.dict(sys.modules, {"ApplicationServices": services}):
            result = ns["_ax_select_text_range"](
                object(), text, (5, 7), "thanks!")
        return result, state["writes"]

    def test_accessibility_selection_is_written_in_utf16_and_confirmed(self):
        text = "Ok \U0001F44D thanks!"
        result, writes = self._accessibility_selection(
            text, len(text), settable=True, applies=True)

        self.assertIs(result, True)
        self.assertEqual(writes, [(6, 7)])

    def test_a_field_without_settable_selection_leaves_the_keyboard(self):
        text = "Ok \U0001F44D thanks!"
        result, writes = self._accessibility_selection(
            text, len(text), settable=False, applies=False)

        self.assertIsNone(result)
        self.assertEqual(writes, [])

    def test_a_selection_request_the_field_ignores_is_not_confirmed(self):
        text = "Ok \U0001F44D thanks!"
        result, writes = self._accessibility_selection(
            text, len(text), settable=True, applies=False)

        self.assertIs(result, False)
        self.assertEqual(writes, [(6, 7)])

    def test_whole_characters_are_counted_as_the_caret_moves(self):
        count = self.namespace()["grapheme_count"]
        for text, expected in (
                ("thanks", 6),
                ("\U0001F44D\U0001F3FD", 1),
                ("\U0001F468‍\U0001F469‍\U0001F467", 1),
                ("\U0001F1FA\U0001F1F8\U0001F1EC\U0001F1E7", 2),
                ("1️⃣", 1),
                ("é", 1),
                ("\r\n", 1),
                ("각", 1),
                ("क्ष", 1),
                ("", 0)):
            with self.subTest(text=text):
                self.assertEqual(count(text), expected)

    def test_nothing_to_undo_is_a_refusal_not_a_crash(self):
        ns = self.namespace()
        self.assertEqual(
            ns["undo_last_dictation"](),
            {"undone": False, "reason": "nothing_to_undo"})

    def test_a_new_dictation_replaces_what_is_undoable(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        self.assertTrue(ns["undoable_insertion_status"]()["available"])
        ns["record_undoable_insertion"](None)
        self.assertFalse(ns["undoable_insertion_status"]()["available"])
        self.assertEqual(
            ns["undo_last_dictation"]()["reason"], "nothing_to_undo")

    def test_an_over_long_insertion_is_declined_rather_than_typed_back(self):
        ns = self.namespace()
        ns["UNDO_MAX_CHARACTERS"] = 4
        record = ns["UndoableInsertion"](
            element=ns["_element"], bundle="com.apple.mail",
            destination_id="com.apple.mail:mail:field-1",
            utterance_id="utterance-long",
            inserted="x" * 5, before="", selection=(0, 0))
        ns["record_undoable_insertion"](record)
        result = ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns))
        self.assertEqual(result["reason"], "too_long_to_undo")
        self.assertEqual(ns["_state"]["keys"], [])

    def test_a_verified_undo_is_suppressed_from_correction_learning(self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        self.assertFalse(ns["utterance_was_undone"]("utterance-1"))
        ns["undo_last_dictation"](snapshot_reader=lambda: self._snapshot(ns))
        self.assertTrue(ns["utterance_was_undone"]("utterance-1"))

    def test_a_refusal_that_never_touched_the_field_still_allows_learning(
            self):
        ns = self.namespace()
        ns["record_undoable_insertion"](self._record(ns))
        ns["undo_last_dictation"](
            snapshot_reader=lambda: self._snapshot(ns, caret=(3, 0)))
        # The destination is untouched, so a real correction made there is
        # still the user's and still deserves to be learned.
        self.assertFalse(ns["utterance_was_undone"]("utterance-1"))

    def test_an_unreadable_destination_is_never_recorded_as_undoable(self):
        ns = self.namespace()
        request = SimpleNamespace(committed_text=self.INSERTED)
        opaque = ns["FocusSnapshot"](
            element=ns["_element"], text=None, selection=None)
        self.assertIsNone(ns["build_undoable_insertion"](
            request, opaque, "com.apple.mail", "u"))
        self.assertIsNone(ns["build_undoable_insertion"](
            request, None, "com.apple.mail", "u"))
        self.assertIsNone(ns["build_undoable_insertion"](
            SimpleNamespace(committed_text=""), self._snapshot(ns),
            "com.apple.mail", "u"))

    def test_the_recorded_insertion_is_the_string_that_reached_the_field(self):
        ns = self.namespace()
        request = SimpleNamespace(committed_text=" " + self.INSERTED)
        snapshot = ns["FocusSnapshot"](
            element=ns["_element"], text=self.BEFORE.rstrip(),
            selection=(len(self.BEFORE.rstrip()), 0))
        record = ns["build_undoable_insertion"](
            request, snapshot, "com.apple.mail", "u")
        # The joining space is part of what was inserted, so undo removes it.
        self.assertEqual(record.inserted, " " + self.INSERTED)
        self.assertEqual(record.restored, self.BEFORE.rstrip())


class CorrectionLearningUndoSuppressionTests(unittest.TestCase):
    """A2: an undo must never be mistaken for the user correcting text."""

    def namespace(self, undone, *, revised="Hello Qwen world"):
        recorded = []
        return load_definitions(
            "learn_from_corrections", "_learned_count",
            extra={
                "PersonalRegressionLab": PersonalRegressionLab,
                "observe_paste_outcome": lambda _receipt: revised,
                "record_paste_outcome":
                    lambda *args: recorded.append(args),
                "utterance_was_undone": lambda event_id: event_id in undone,
                "print": lambda *_a, **_k: None,
                "difflib": __import__("difflib"),
                "time": SimpleNamespace(time=lambda: 1.0),
                "correction_similarity": lambda _old, _new: 0.8,
                "parse_dictionary": lambda: ([], set()),
                "CORRECTION_MAX_LEARN": 3,
                "PROMOTE_MIN_COUNT": 2,
                "LEARN_LOCK": threading.Lock(),
                "load_learned": lambda: {
                    "counts": {}, "fixes": {}, "confusions": {},
                    "history": [], "regression_lab": {}},
                "save_learned": lambda _state: None,
                "personal_regression_lab": lambda _state: SimpleNamespace(
                    promoted=(),
                    record_revert=lambda *_a, **_k: False,
                    record_correction=lambda *_a, **_k: None,
                    propose=lambda *_a, **_k: SimpleNamespace(passed=True),
                    to_dict=lambda: {}),
                "refresh_glossary": lambda: None,
                "remember_explicit_acoustic_keyword_correction":
                    lambda *_a, **_k: True,
                "PERSONAL_APP_MIN_COUNT": 2,
                "PERSONAL_GLOBAL_MIN_COUNT": 3,
            },
        ), recorded

    def test_an_undone_utterance_never_reaches_the_personal_prior(self):
        ns, recorded = self.namespace({"undone-event"})
        receipt = SimpleNamespace(
            pasted="Hello Gwen world", bundle="com.apple.mail",
            mode="capture", event_id="undone-event")

        ns["learn_from_corrections"](receipt)

        self.assertEqual(recorded, [])

    def test_an_undo_during_the_watch_window_is_still_suppressed(self):
        # The watcher is already sleeping when the undo arrives, so the check
        # after observe_paste_outcome is the one that has to catch it.
        undone = set()
        ns, recorded = self.namespace(undone)

        def observe(_receipt):
            undone.add("late-event")
            return "Hello Qwen world"

        ns["observe_paste_outcome"] = observe
        receipt = SimpleNamespace(
            pasted="Hello Gwen world", bundle="com.apple.mail",
            mode="capture", event_id="late-event")

        ns["learn_from_corrections"](receipt)

        self.assertEqual(recorded, [])

    def test_an_ordinary_correction_is_unaffected(self):
        ns, recorded = self.namespace(set())
        receipt = SimpleNamespace(
            pasted="Hello Gwen world", bundle="com.apple.mail",
            mode="capture", event_id="normal-event")

        ns["learn_from_corrections"](receipt)

        self.assertEqual(len(recorded), 1)


class DictationLanguageTests(unittest.TestCase):
    """B1: a non-English language must route around the English-only parts."""

    def test_language_preference_round_trips_and_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            ns = load_definitions(
                "normalize_face", "current_face", "load_preferences",
                "save_preferences", "normalize_hotkey",
                "normalize_sound_theme", "set_dictation_language",
                assignments={"FACE_CHOICES", "HOTKEY_CHOICES",
                             "SOUND_THEMES"},
                extra={
                    "PREFERENCES_FILE": preferences,
                    "IS_MACOS": True,
                    "ACOUSTIC_TIME_MACHINE": AcousticTimeMachine(),
                    "json": json,
                    "print": lambda *_args: None,
                    "refresh_glossary": lambda: None,
                    "apply_hotkey_bindings": lambda: None,
                    "atomic_write_text": lambda path, value:
                        path.write_text(value, encoding="utf-8"),
                },
            )
            # A preferences file written before this setting existed keeps
            # the English behavior it already had.
            preferences.write_text(json.dumps({"face": "fox"}),
                                   encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["current_language"](), "en")

            preferences.write_text(json.dumps({"language": "pt-BR"}),
                                   encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["current_language"](), "pt")

            for unsupported in ("klingon", "", None, 7):
                preferences.write_text(json.dumps({"language": unsupported}),
                                       encoding="utf-8")
                ns["load_preferences"]()
                self.assertEqual(ns["current_language"](), "en")

            self.assertEqual(ns["set_dictation_language"]("ja"), "ja")
            saved = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(saved["language"], "ja")
            self.assertEqual(ns["set_dictation_language"]("nonsense"), "en")

    def _decode_namespace(self, **overrides):
        calls = []

        def parakeet_transcribe(_audio):
            calls.append("parakeet")
            return ("native text", 0.05)

        def mlx_transcribe(_audio, **kwargs):
            calls.append(kwargs.get("language"))
            return {"text": " decoded ", "segments": []}

        extra = {
            "np": np,
            "Recognition": Recognition,
            "GLOSS": {"lock": threading.Lock(), "prompt": None},
            "IS_MACOS": True,
            "PARAKEET_ENABLED": True,
            "PARAKEET": SimpleNamespace(transcribe=parakeet_transcribe),
            "PARAKEET_ROUTE_CONFIDENCE": 0.84,
            # The English route runs the helper and the Tiny cross-check
            # concurrently; resolve both inline so ordering is deterministic.
            "PARAKEET_IO_POOL": SimpleNamespace(
                submit=lambda fn, *a, **k: SimpleNamespace(
                    result=lambda: fn(*a, **k))),
            "PARAKEET_CROSSCHECK": False,
            "PARAKEET_CROSSCHECK_MAX_SECONDS": 90.0,
            "asr_model_is_cached": lambda _repo: False,
            "hypothesis_agreement": lambda _a, _b: 1.0,
            "parakeet_confidence_from_agreement": lambda _a: 0.93,
            "should_escalate_uncertain": lambda *a, **k: False,
            "WHISPER_REPO": "large",
            "FAST_WHISPER_REPO": "tiny",
            "SAMPLE_RATE": 16_000,
            "MIN_SECONDS": 0.25,
            "LOW_CONFIDENCE": 0.5,
            "prepare_asr_audio": lambda audio: audio,
            "asr_decode_target": lambda repo: repo,
            "resolve_asr_model": lambda repo: repo,
            "mlx_whisper": SimpleNamespace(transcribe=mlx_transcribe),
            "confidence_from_segments": lambda _segments: 0.9,
            "recognition_words_from_segments": lambda _segments: [],
            "mark_model_warm_path_observed": lambda _provider: None,
            "WHISPER_TINY_PROFILE": SimpleNamespace(provider_id="tiny"),
            "WHISPER_LARGE_TURBO_PROFILE": SimpleNamespace(
                provider_id="turbo"),
            "PREFERENCES": {"language": "en"},
        }
        extra.update(overrides)
        return load_definitions(
            "transcribe_detailed", "_parakeet_crosschecked",
            "_clean_native_processing_s", extra=extra), calls

    def test_english_still_takes_the_native_parakeet_route(self):
        ns, calls = self._decode_namespace()
        result = ns["transcribe_detailed"](
            np.ones(16_000, dtype=np.float32), language="en")
        self.assertEqual(result.engine, "parakeet-unified")
        self.assertEqual(calls, ["parakeet"])

    def test_a_non_english_language_never_reaches_parakeet(self):
        # Parakeet Unified is the English-only checkpoint. Fed other
        # languages it answers ok=true with an English-alphabet
        # transliteration, and the fixed route confidence would let that
        # stand as the final transcript, so the routing has to be explicit.
        for language in ("es", "ja", "ru"):
            with self.subTest(language=language):
                ns, calls = self._decode_namespace()
                result = ns["transcribe_detailed"](
                    np.ones(16_000, dtype=np.float32), language=language)
                self.assertNotIn("parakeet", calls)
                self.assertEqual(calls, [language])
                self.assertEqual(result.engine, "turbo")
                self.assertEqual(result.text, "decoded")

    def test_the_decode_language_defaults_to_the_stored_preference(self):
        ns, calls = self._decode_namespace(PREFERENCES={"language": "de"})
        ns["transcribe_detailed"](np.ones(16_000, dtype=np.float32))
        self.assertEqual(calls, ["de"])

    def test_the_cascade_drops_the_parakeet_route_off_english(self):
        class ImmediatePool:
            def __init__(self):
                self.models = []

            def submit(self, function, *args, **kwargs):
                self.models.append(args[3])
                return SimpleNamespace(
                    result=lambda: function(*args, **kwargs))

        def detailed(_audio, _prompt, _verify, model,
                     crosscheck_text=None, language="en"):
            return Recognition(model, 0.99 if model == "tiny" else 0.84)

        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "parrot-asr-helper"
            helper.write_bytes(b"helper")
            for language, expected in (("en", ["tiny", "large"]),
                                       ("ja", ["tiny"])):
                with self.subTest(language=language):
                    pool = ImmediatePool()
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
                            "IS_MACOS": True,
                            "PARAKEET_ENABLED": True,
                            "PARAKEET_HELPER": helper,
                            "PREFERENCES": {"language": language},
                        },
                    )
                    ns["_speculative_frames"](
                        [np.ones((100, 1), dtype=np.float32)],
                        language=language)
                    self.assertEqual(pool.models, expected)

    def test_non_latin_transcripts_survive_the_hallucination_guard(self):
        ns = load_definitions(
            "is_hallucination", "weak_whisper_evidence",
            assignments=CleanupGuardTests.HALLUCINATION_GUARD)
        guard = ns["is_hallucination"]
        # The old ASCII-only token class matched nothing here, so every
        # correct transcript in these scripts was dropped as a hallucination.
        for text, language in (
            ("\u5b63\u5ea6\u62a5\u544a", "zh"),
            ("\u56db\u534a\u671f\u5831\u544a\u66f8", "ja"),
            ("\u041a\u0432\u0430\u0440\u0442\u0430\u043b\u044c\u043d"
             "\u044b\u0439 \u043e\u0442\u0447\u0451\u0442", "ru"),
            ("\ubd84\uae30 \ubcf4\uace0\uc11c", "ko"),
        ):
            with self.subTest(language=language):
                self.assertFalse(guard(text, language))
        # Punctuation-only output is still nothing, in any language.
        self.assertTrue(guard("\u3002\u3002\u3002", "ja"))
        self.assertTrue(guard("  ...  ", "en"))
        # The phrase list is English, so it only fires for English.
        weak = Recognition(
            "Thank you for watching.", confidence=0.3, engine="turbo",
            audio_duration=2.0)
        self.assertTrue(guard("Thank you for watching.", "en", weak))
        self.assertFalse(guard("Thank you for watching.", "de", weak))

    def test_english_only_text_rules_leave_other_languages_alone(self):
        ns = load_definitions(
            "quick_clean", "strip_casual_period", "needs_llm_cleanup",
            extra={"compile_cleanup": compile_cleanup})
        # English: sentence casing and a closing period.
        self.assertEqual(ns["quick_clean"]("hello there", language="en"),
                         "Hello there.")
        # Japanese already ends its sentence; nothing is appended, nothing
        # is capitalized, and no ASCII period is invented.
        japanese = "\u56db\u534a\u671f\u5831\u544a\u66f8\u3002"
        self.assertEqual(ns["quick_clean"](japanese, language="ja"), japanese)
        spanish = "el informe estara listo manana"
        self.assertEqual(ns["quick_clean"](spanish, language="es"), spanish)
        # The casual trailing-period convention is anglophone.
        self.assertEqual(ns["strip_casual_period"]("See you.", "en"),
                         "See you")
        self.assertEqual(ns["strip_casual_period"]("Hasta luego.", "es"),
                         "Hasta luego.")
        # The cleanup model's prompts, examples and refusal guard are all
        # English, so non-English dictation stays deterministic.
        self.assertTrue(ns["needs_llm_cleanup"](
            "make this sound right", "formal", False, language="en"))
        self.assertFalse(ns["needs_llm_cleanup"](
            "make this sound right", "formal", False, language="ja"))

    def test_spaceless_scripts_are_never_joined_with_a_space(self):
        ns = load_definitions(
            "join_recognized_parts", "insertion_join_prefix",
            extra={"FocusSnapshot": SimpleNamespace})
        self.assertEqual(
            ns["join_recognized_parts"](("hello", "there"), "en"),
            "hello there")
        self.assertEqual(
            ns["join_recognized_parts"](
                ("\u56db\u534a\u671f", "\u5831\u544a\u66f8"), "ja"),
            "\u56db\u534a\u671f\u5831\u544a\u66f8")
        field = SimpleNamespace(text="\u65e2\u5b58\u306e", selection=(3, 0))
        self.assertEqual(
            ns["insertion_join_prefix"](field, "\u6587\u7ae0", "ja"), "")
        latin = SimpleNamespace(text="Hello", selection=(5, 0))
        self.assertEqual(ns["insertion_join_prefix"](latin, "world", "en"),
                         " ")

    def test_dense_scripts_get_a_smaller_glossary_character_budget(self):
        ns = load_definitions("glossary_char_budget")
        english = ns["glossary_char_budget"]("en")
        # Whisper honors ~224 prompt tokens. Latin BPE averages about three
        # characters per token; CJK runs closer to one, so the same character
        # budget would be a threefold token overrun.
        self.assertEqual(english, 700)
        for dense in ("ja", "zh"):
            with self.subTest(language=dense):
                self.assertLess(ns["glossary_char_budget"](dense), english)
                self.assertGreater(ns["glossary_char_budget"](dense), 0)

    def test_the_vocabulary_miner_only_reads_english_dictations(self):
        # MINER_PROMPT instructs the model to exclude ordinary *English*
        # words, so another language would promote its everyday vocabulary
        # into the glossary and bias every later decode.
        ns = load_definitions("parse_texts")
        lines = [
            json.dumps({"clean": "english phrase", "language": "en"}),
            json.dumps({"clean": "legacy phrase without a language"}),
            json.dumps({"clean": "frase espanola", "language": "es"}),
            json.dumps({"clean": "japanese", "language": "ja"}),
        ]
        self.assertEqual(ns["parse_texts"](lines), [
            "english phrase", "legacy phrase without a language"])


class HotkeyWorkerDiagnosticsTests(unittest.TestCase):
    """E2: the unexplained `hotkey worker failed: AttributeError`."""

    @staticmethod
    def _namespace(bundle_identifier, *, application=True):
        front = SimpleNamespace(
            bundleIdentifier=lambda: bundle_identifier) if application else None
        return load_definitions(
            "frontmost_bundle",
            extra={
                "IS_WINDOWS": False,
                "NSWorkspace": SimpleNamespace(
                    sharedWorkspace=lambda: SimpleNamespace(
                        frontmostApplication=lambda: front)),
            },
        )

    def test_a_bundleless_frontmost_app_reports_an_empty_bundle(self):
        # Root cause: bundleIdentifier() is nil for any process outside an
        # app bundle, including Whisper Face itself, which runs as a bare
        # script. That nil reached the context adapters as None, where
        # `observation.bundle.casefold()` raised, so pressing the hotkey
        # while the app's own window was frontmost lost the whole take.
        bundle = self._namespace(None)["frontmost_bundle"]()
        self.assertEqual(bundle, "")
        self.assertIsInstance(bundle, str)
        self.assertEqual(bundle.casefold(), "")   # the call that used to raise

    def test_a_named_bundle_is_still_returned_verbatim(self):
        self.assertEqual(
            self._namespace("com.apple.mail")["frontmost_bundle"](),
            "com.apple.mail")

    def test_no_frontmost_application_is_also_an_empty_bundle(self):
        self.assertEqual(
            self._namespace(None, application=False)["frontmost_bundle"](),
            "")

    def test_a_swallowed_failure_names_its_origin_without_any_content(self):
        ns = load_definitions("exception_origin", extra={"os": os})

        def inner():
            raise AttributeError(
                "'NoneType' object has no attribute 'casefold'")

        try:
            inner()
        except AttributeError as error:
            origin = ns["exception_origin"](error)

        self.assertIn("AttributeError", origin)
        self.assertIn("inner", origin)
        self.assertIn("test_dictate.py:", origin)
        # The message can quote a transcript or a destination field, so it is
        # never logged.
        self.assertNotIn("casefold", origin)
        self.assertNotIn("NoneType", origin)

    def test_an_exception_with_no_traceback_still_names_its_type(self):
        ns = load_definitions("exception_origin", extra={"os": os})
        self.assertEqual(
            ns["exception_origin"](RuntimeError("boom")), "RuntimeError")


class AutoreleasePoolTests(unittest.TestCase):
    """E4: workers that create Objective-C objects own a pool."""

    def test_cocoa_pool_is_inert_off_mac(self):
        ns = load_definitions("cocoa_pool", extra={"IS_MACOS": False})
        with ns["cocoa_pool"]():
            pass

    def test_every_objective_c_worker_is_wrapped(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        # These run on their own threads and create Objective-C objects:
        # Accessibility attribute values, pasteboard strings, running
        # applications. Without a pool every one of them lives until exit.
        for worker in (
            "def insert_recent_dictation",
            "def undo_last_dictation",
            "def learn_from_corrections",
            "def learn_snippet_edit",
            "def _run_delayed_cleanup",
        ):
            with self.subTest(worker=worker):
                self.assertIn("@with_cocoa_pool\n" + worker, source)
        # The two that scope a block rather than a whole function.
        self.assertIn(
            "with cocoa_pool():\n        try:\n"
            "            finish_and_process", source)
        self.assertIn("with cocoa_pool():\n                try:", source)


if __name__ == "__main__":
    unittest.main()
