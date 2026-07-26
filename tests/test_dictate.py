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
import ctypes
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
import unittest
from unittest import mock
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
from cleanup_circuit_breaker import CleanupCircuitBreaker  # noqa: E402
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
    def test_mac_review_route_uses_advisory_ping(self):
        select = load_definitions(
            "dictation_success_sound")["dictation_success_sound"]

        self.assertEqual(select("review", is_macos=True), "Ping")

    def test_other_routes_and_windows_keep_standard_success_sound(self):
        select = load_definitions(
            "dictation_success_sound")["dictation_success_sound"]

        self.assertEqual(select("standard", is_macos=True), "Pop")
        self.assertEqual(select("review", is_macos=False), "Pop")


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
        rebuild_source = ast.unparse(methods["rebuild_recognition"])
        self.assertIn("Open Last Result…", rebuild_source)
        self.assertIn("openResults:", rebuild_source)

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
                "spoken_edit_commands": False,
                "face": "dragon",
            }), encoding="utf-8")
            ns["load_preferences"]()
            self.assertEqual(ns["current_face"](), "parrot")
            self.assertTrue(ns["PREFERENCES"]["flight_recorder"])
            self.assertTrue(ns["PREFERENCES"]["acoustic_time_machine"])
            self.assertFalse(ns["PREFERENCES"]["voice_object_commands"])
            self.assertFalse(ns["PREFERENCES"]["spoken_edit_commands"])

            ns["PREFERENCES"]["face"] = "FOX"
            ns["save_preferences"]()
            saved = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(saved, {
                "flight_recorder": True,
                "acoustic_time_machine": True,
                "voice_object_commands": False,
                "spoken_edit_commands": False,
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
                        "spoken_edit_commands": voice_objects,
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
                    self.assertIs(
                        ns["PREFERENCES"]["spoken_edit_commands"], expected)
                    self.assertIs(buffer.enabled, expected)

    def test_all_default_faces_are_supported(self):
        ns = load_definitions(
            "normalize_face",
            assignments={"FACE_CHOICES", "DEFAULT_FACE"},
        )
        self.assertEqual(
            tuple(ns["normalize_face"](face) for face in ns["FACE_CHOICES"]),
            ("parrot", "fox", "owl", "cat", "bear",
             "dog", "wolf", "pig", "panda", "tiger"),
        )

    def test_reduce_motion_freezes_hud_audio_animation(self):
        ns = load_definitions(
            "hud_level_step", assignments={"LEVEL_SMOOTH"})

        self.assertEqual(ns["hud_level_step"](0.9, 0.7, "recording", True), 0.0)
        self.assertGreater(
            ns["hud_level_step"](0.9, 0.0, "recording", False), 0.0)
        self.assertLess(
            ns["hud_level_step"](0.9, 0.7, "error", False), 0.7)


class WhisperFaceThemeTests(unittest.TestCase):
    def test_light_and_dark_palettes_share_brand_but_not_work_surfaces(self):
        self.assertEqual(LIGHT_PALETTE.brand, DARK_PALETTE.brand)
        self.assertEqual(LIGHT_PALETTE.error, DARK_PALETTE.error)
        self.assertNotEqual(LIGHT_PALETTE.bg, DARK_PALETTE.bg)
        self.assertNotEqual(LIGHT_PALETTE.surface, DARK_PALETTE.surface)
        self.assertNotEqual(LIGHT_PALETTE.ink, DARK_PALETTE.ink)
        self.assertEqual(
            set(FACE_CHIP_COLORS),
            {"parrot", "fox", "owl", "cat", "bear",
             "dog", "wolf", "pig", "panda", "tiger"},
        )

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

    def test_runtime_invalidation_covers_pool_and_flight_recorder(self):
        calls = []
        ns = load_definitions(
            "_invalidate_default_audio_inputs",
            extra={
                "AUDIO_POOL": SimpleNamespace(
                    recover_default_device=lambda: calls.append("pool")),
                "FLIGHT": SimpleNamespace(
                    invalidate=lambda: calls.append("flight")),
            },
        )

        ns["_invalidate_default_audio_inputs"]()

        self.assertEqual(calls, ["pool", "flight"])

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


class InlineSnippetExpansionTests(unittest.TestCase):
    """Inline (embedded-trigger) snippet expansion. Additive to the
    whole-utterance command covered by ConfigurationTests; must never alter a
    non-matching dictation and must survive a missing or damaged file."""

    def _expander(self, mapping=None):
        ns = load_definitions(
            "expand_snippets_inline",
            "_load_snippet_map",
            "_compile_snippet_pattern",
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "snippets.json"
        if mapping is not None:
            path.write_text(json.dumps(mapping))
        ns["SNIPPETS_FILE"] = path
        return ns["expand_snippets_inline"]

    def test_inline_hit_inside_a_sentence(self):
        expand = self._expander({"email": "andrew@example.com"})
        self.assertEqual(
            expand("text him my email please"),
            "text him my andrew@example.com please",
        )

    def test_expansion_is_case_insensitive(self):
        expand = self._expander({"email": "andrew@example.com"})
        self.assertEqual(expand("send EMAIL now"), "send andrew@example.com now")

    def test_partial_word_never_fires(self):
        # "address" must not expand inside "addressed"; the boundary is
        # stricter than \b so a suffix cannot trigger it.
        expand = self._expander({"address": "1 Main St"})
        self.assertEqual(expand("he addressed the room"), "he addressed the room")

    def test_longest_trigger_wins(self):
        expand = self._expander({
            "email": "andrew@example.com",
            "email signature": "Best,\nAndrew",
        })
        self.assertEqual(
            expand("use my email signature here"),
            "use my Best,\nAndrew here",
        )

    def test_multiline_expansion_is_preserved(self):
        expand = self._expander({"address": "7623 Opal Ridge Lane\nBainbridge Island, WA 98110"})
        self.assertEqual(
            expand("mail it to my address today"),
            "mail it to my 7623 Opal Ridge Lane\nBainbridge Island, WA 98110 today",
        )

    def test_no_matching_trigger_returns_raw_unchanged(self):
        expand = self._expander({"email": "andrew@example.com"})
        self.assertEqual(expand("just talking normally"), "just talking normally")

    def test_missing_file_returns_raw_unchanged(self):
        expand = self._expander(mapping=None)  # file never written
        self.assertEqual(expand("send my email now"), "send my email now")

    def test_wrong_shaped_snippets_file_returns_raw_unchanged(self):
        # Clone of ConfigurationTests.test_wrong_shaped_snippets_file_is_ignored,
        # asserted against the inline path: a JSON array (not an object) and
        # syntactically broken JSON both degrade to leaving the dictation alone.
        for payload in ("[]", "{not valid json"):
            with self.subTest(payload=payload):
                ns = load_definitions(
                    "expand_snippets_inline",
                    "_load_snippet_map",
                    "_compile_snippet_pattern",
                )
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "snippets.json"
                    path.write_text(payload)
                    ns["SNIPPETS_FILE"] = path
                    self.assertEqual(
                        ns["expand_snippets_inline"]("send my email now"),
                        "send my email now",
                    )

    def test_masked_multiline_survives_deterministic_cleanup(self):
        # The pipeline round-trip: masking shields a multiline (indented)
        # expansion from cleanup, the surrounding words are still cleaned, and
        # restoration leaves no sentinel behind.
        ns = load_definitions(
            "_compile_snippet_pattern",
            "_snippet_sentinel",
            "_mask_snippets_inline",
            "_restore_snippet_sentinels",
            assignments={"_SNIPPET_SENTINEL_MARK"},
        )
        expansion = "Regards,\n    Andrew\n    Engineer"
        masked, restore = ns["_mask_snippets_inline"](
            "here is my sig okay", {"my sig": expansion})
        self.assertTrue(restore)
        self.assertNotIn("my sig", masked)      # trigger was masked away
        cleaned = compile_cleanup(masked).text  # deterministic cleanup only
        restored = ns["_restore_snippet_sentinels"](cleaned, restore)
        self.assertIn(expansion, restored)      # indentation intact
        self.assertEqual(restored.count("\ue000"), 0)  # no sentinel leaked

    def test_restoration_of_absent_sentinel_is_a_no_op(self):
        ns = load_definitions(
            "_compile_snippet_pattern",
            "_snippet_sentinel",
            "_mask_snippets_inline",
            "_restore_snippet_sentinels",
            assignments={"_SNIPPET_SENTINEL_MARK"},
        )
        _, restore = ns["_mask_snippets_inline"]("my email", {"email": "X"})
        # A token the cleaned text no longer contains must not corrupt output.
        self.assertEqual(
            ns["_restore_snippet_sentinels"]("unrelated text", restore),
            "unrelated text",
        )


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
                "insertion_readback": lambda *_args, **_kwargs:
                    ReadbackResult.verified(),
                "readback_timeout_for_frontmost": lambda: 0.02,
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
            "CapturedAudio",
            "BoundedRecognitionFuture",
            "Recorder",
            extra={
                "dataclass": dataclass,
                "np": np,
                "ContextPack": SimpleNamespace,
                "LEVELS": [],
                "SILENCE_RMS": 0.01,
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
        problems = []

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
                "report_dictation_problem": (
                    lambda _rec, _hud, caption, log, **_kwargs:
                    problems.append((caption, log))),
                "dictation_feedback_delay": lambda _rec: 0.0,
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
                "load_learned": lambda: {
                    "counts": {}, "fixes": {}, "confusions": {}},
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
            },
        )

        ns["refresh_glossary"]()

        self.assertEqual(len(gloss["terms"]), 60)   # prompt cap unchanged
        anchor_texts = {c.text for c in gloss["anchor_pack"].candidates}
        self.assertEqual(len(anchor_texts), 70)
        for term in manual:
            self.assertIn(term, anchor_texts)
            self.assertEqual(gloss["vocabulary"][term.casefold()], term)
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


if __name__ == "__main__":
    unittest.main()
