# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Runtime persistence tests for the native unified Settings adapters."""

import ast
import json
import math
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_dictate import load_definitions  # noqa: E402
from personal_regression import PersonalRegressionLab  # noqa: E402


class RiskyConfirmationRuntimeIntegrationTests(unittest.TestCase):
    class FakeStatus:
        def __init__(self, risk=None, state="idle", reason="idle"):
            self.risk = risk
            self.state = state
            self.reason = reason

    class FakeRuntime:
        def __init__(self):
            self.calls = []
            self.current = RiskyConfirmationRuntimeIntegrationTests.FakeStatus()

        def status(self):
            return self.current

        def start(self, risk):
            self.calls.append(("start", risk))
            self.current = RiskyConfirmationRuntimeIntegrationTests.FakeStatus(
                type("Risk", (), {"value": risk})(),
                "awaiting_voice", "proposed")

        def click_confirm(self):
            self.calls.append(("click",))

        def cancel(self):
            self.calls.append(("cancel",))

        def consume_voice(self, text):
            self.calls.append(("voice", text))
            return type("Receipt", (), {"consumed": True})()

    def namespace(self, runtime, *, is_macos=True):
        return load_definitions(
            "risky_action_confirmation_status_snapshot",
            "start_risky_action_confirmation",
            "click_risky_action_confirmation",
            "cancel_risky_action_confirmation",
            "consume_risky_action_confirmation_voice",
            extra={
                "IS_MACOS": is_macos,
                "RISKY_ACTION_CONFIRMATIONS": runtime,
            },
        )

    def test_runtime_callbacks_expose_only_closed_status_and_no_action(self):
        runtime = self.FakeRuntime()
        ns = self.namespace(runtime)

        self.assertTrue(ns["start_risky_action_confirmation"](
            "file_mutation"))
        self.assertEqual(
            ns["risky_action_confirmation_status_snapshot"](), {
                "risk": "file_mutation",
                "state": "awaiting_voice",
                "reason": "proposed",
            })
        self.assertTrue(ns["consume_risky_action_confirmation_voice"](
            "confirm risky action"))
        self.assertTrue(ns["click_risky_action_confirmation"]())
        self.assertTrue(ns["cancel_risky_action_confirmation"]())
        self.assertEqual(runtime.calls, [
            ("start", "file_mutation"),
            ("voice", "confirm risky action"),
            ("click",),
            ("cancel",),
        ])

    def test_non_mac_runtime_is_inert(self):
        runtime = self.FakeRuntime()
        ns = self.namespace(runtime, is_macos=False)

        self.assertFalse(ns["start_risky_action_confirmation"](
            "agent_execution"))
        self.assertFalse(ns["consume_risky_action_confirmation_voice"](
            "confirm risky action"))
        self.assertFalse(ns["click_risky_action_confirmation"]())
        self.assertFalse(ns["cancel_risky_action_confirmation"]())
        self.assertEqual(runtime.calls, [])

    def test_voice_intercept_precedes_all_content_sinks(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "finish_and_process")
        body = ast.get_source_segment(source, function)
        self.assertIsNotNone(body)
        intercept = body.index("consume_risky_action_confirmation_voice(raw)")
        for sink in (
            "compile_voice_evidence(",
            "CAPTION[\"text\"] = raw",
            "queue_voice_object_command(",
            "commit_insertion(",
            "append_transcript(",
        ):
            with self.subTest(sink=sink):
                self.assertLess(intercept, body.index(sink))

    def test_inline_snippet_masking_wraps_cleanup(self):
        # Inline expansion is a round-trip: the trigger is masked after the
        # whole-utterance command and before cleanup, and the sentinel is
        # restored to its exact expansion on the finalized text just before it
        # is committed to the paste. This pins that ordering in the pipeline.
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "finish_and_process")
        body = ast.get_source_segment(source, function)
        self.assertIsNotNone(body)
        whole_utterance = body.index("hit = match_snippet(raw)")
        mask = body.index("_mask_snippets_inline(")
        first_cleanup = body.index("extract_tone_override(raw)")
        restore = body.index("_restore_snippet_sentinels(")
        commit = body.index("commit_insertion(")
        self.assertLess(whole_utterance, mask)   # after the verbatim command
        self.assertLess(mask, first_cleanup)     # before tone/cleanup
        self.assertLess(mask, restore)
        self.assertLess(restore, commit)         # restored before the paste


def settings_namespace(*names):
    return load_definitions(
        "atomic_write_text", "_json_object", *names,
        extra={
            "Path": Path,
            "os": os,
            "tempfile": tempfile,
        },
    )


class SnippetPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "snippets.json"
        self.ns = settings_namespace(
            "save_gui_snippet", "delete_gui_snippet", "snippet_name_key")
        self.ns.update(
            SNIPPETS_FILE=self.path,
            SNIPPETS_LOCK=threading.Lock(),
        )

    def test_atomic_round_trip_preserves_unrelated_snippets_and_private_mode(self):
        self.path.write_text(json.dumps({"signature": "Cheers"}))
        self.ns["save_gui_snippet"]("address", None, "123 Main Street")
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved, {
            "signature": "Cheers", "address": "123 Main Street"})
        if os.name != "nt":
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertFalse(any(path.name.startswith(".snippets.json.")
                             for path in self.path.parent.iterdir()))

    def test_malformed_input_fails_closed_without_overwriting_file(self):
        self.path.write_text("[]")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "JSON object"):
            self.ns["save_gui_snippet"]("address", None, "private text")
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.ns["save_gui_snippet"]("bad\nname", None, "text")
        with self.assertRaises(ValueError):
            self.ns["save_gui_snippet"]("empty", None, " ")

    def test_distinct_non_latin_names_are_not_a_collision(self):
        # The old normalized_key stripped [^a-z0-9 ], so "住所" and "адрес"
        # both collapsed to "" and the editor refused the second one as a
        # duplicate of the first.
        self.ns["save_gui_snippet"]("住所", None, "1-2-3 Chiyoda")
        self.ns["save_gui_snippet"]("адрес", None, "Tverskaya 7")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["住所"], "1-2-3 Chiyoda")
        self.assertEqual(saved["адрес"], "Tverskaya 7")
        # Same-script near-duplicates still collide through the key.
        with self.assertRaises(ValueError):
            self.ns["save_gui_snippet"]("Адрес", None, "different")

    def test_compare_and_swap_rejects_stale_edits_and_create_collisions(self):
        self.path.write_text(json.dumps({"signature": "Cheers"}))
        self.ns["save_gui_snippet"]("signature", "Cheers", "Kind regards")
        self.assertEqual(
            json.loads(self.path.read_text())["signature"], "Kind regards")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "editor was opened"):
            self.ns["save_gui_snippet"](
                "signature", "Cheers", "Stale overwrite")
        with self.assertRaisesRegex(RuntimeError, "editor was opened"):
            self.ns["save_gui_snippet"](
                "signature", None, "Create collision")
        self.assertEqual(self.path.read_bytes(), before)

    def test_delete_removes_only_the_selected_snippet(self):
        self.path.write_text(json.dumps({
            "address": "123 Main", "signature": "Cheers"}))
        self.ns["delete_gui_snippet"]("address", "123 Main")
        self.assertEqual(json.loads(self.path.read_text()), {
            "signature": "Cheers"})
        with self.assertRaises(RuntimeError):
            self.ns["delete_gui_snippet"]("missing", "old text")

    def test_stale_delete_fails_without_rewriting_newer_content(self):
        self.path.write_text(json.dumps({
            "signature": "Newer runtime edit", "address": "123 Main"}))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "editor was opened"):
            self.ns["delete_gui_snippet"]("signature", "Stale snapshot")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(self.path.read_text()), {
            "signature": "Newer runtime edit", "address": "123 Main"})


class VocabularyPersistenceTests(unittest.TestCase):
    def test_save_preserves_comments_and_managed_auto_section(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dictionary.txt"
            marker = "# --- auto-learned (managed by dictate.py) ---"
            path.write_text(
                "# Personal words\nOld term\n-never\n\n" + marker +
                "\nAuto Learned\n")
            ns = settings_namespace(
                "_validated_gui_terms", "save_gui_vocabulary")
            refreshes = []
            ns.update(
                DICTIONARY_FILE=path,
                DICTIONARY_LOCK=threading.RLock(),
                AUTO_MARKER=marker,
                refresh_glossary=lambda: refreshes.append(True),
            )
            ns["save_gui_vocabulary"](
                ["Qwen", "Whisper Face", "Qwen"], ["Gwen"])
            saved = path.read_text()
            self.assertIn("# Personal words", saved)
            self.assertIn("Qwen\nWhisper Face\n-Gwen", saved)
            self.assertIn(marker + "\nAuto Learned\n", saved)
            self.assertNotIn("Old term", saved)
            self.assertEqual(refreshes, [True])
            if os.name != "nt":
                if os.name == "posix":
                    self.assertEqual(
                        stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_ambiguous_or_oversized_vocabulary_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dictionary.txt"
            path.write_text("Qwen\n")
            ns = settings_namespace(
                "_validated_gui_terms", "save_gui_vocabulary")
            ns.update(
                DICTIONARY_FILE=path,
                DICTIONARY_LOCK=threading.RLock(),
                AUTO_MARKER="# auto",
                refresh_glossary=lambda: None,
            )
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "also be excluded"):
                ns["save_gui_vocabulary"](["Qwen"], ["qwen"])
            with self.assertRaisesRegex(ValueError, "80 characters"):
                ns["save_gui_vocabulary"](["x" * 81], [])
            with self.assertRaisesRegex(ValueError, "reserved"):
                ns["save_gui_vocabulary"](["# comment-shaped"], [])
            with self.assertRaisesRegex(ValueError, "reserved"):
                ns["save_gui_vocabulary"]([], ["-already-prefixed"])
            with self.assertRaisesRegex(ValueError, "reserved"):
                ns["save_gui_vocabulary"]([
                    "# --- auto-learned (managed by dictate.py) ---"], [])
            self.assertEqual(path.read_bytes(), before)


class AtomicWritePortabilityTests(unittest.TestCase):
    def test_missing_fchmod_keeps_atomic_write_available(self):
        class WindowsPre313OS:
            def __getattr__(self, name):
                if name == "fchmod":
                    raise AttributeError(name)
                return getattr(os, name)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = load_definitions(
                "atomic_write_text",
                extra={
                    "Path": Path,
                    "os": WindowsPre313OS(),
                    "tempfile": tempfile,
                },
            )
            ns["atomic_write_text"](path, '{"private": true}\n')
            self.assertEqual(path.read_text(), '{"private": true}\n')
            self.assertFalse(any(item.name.startswith(".preferences.json.")
                                 for item in path.parent.iterdir()))


class SettingsSnapshotTests(unittest.TestCase):
    def test_round_trip_snapshot_is_sorted_and_tolerates_malformed_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            snippets = Path(directory) / "snippets.json"
            snippets.write_text(json.dumps({"signature": "Cheers"}))
            ns = settings_namespace("gui_settings_snapshot")
            ns.update(
                GUI_TONES={"auto", "formal", "default"},
                APP_TONES={
                    "map": {"com.example.mail": "formal"},
                    "lock": threading.Lock(),
                },
                recent_dictation_apps=lambda: ["com.example.notes"],
                app_display_name=lambda bundle: bundle.rsplit(".", 1)[-1].title(),
                SNIPPETS_FILE=snippets,
                SNIPPETS_LOCK=threading.Lock(),
                DICTIONARY_LOCK=threading.RLock(),
                parse_dictionary=lambda: (["Qwen"], {"gwen"}),
                LEARN_LOCK=threading.Lock(),
                personal_regression_lab=lambda _state: PersonalRegressionLab(),
                PERSONAL_APP_MIN_COUNT=2,
                PERSONAL_GLOBAL_MIN_COUNT=3,
                load_learned=lambda: {
                    "confusions": {
                        "gwen": {"from": "Gwen", "to": "Qwen", "n": "bad"}},
                    "snippet_edits": {
                        "signature": {"to": "Cheers", "n": 2}},
                },
            )
            snapshot = ns["gui_settings_snapshot"]()
            self.assertEqual(
                [row["bundle"] for row in snapshot["app_tones"]],
                ["com.example.notes", "com.example.mail"])
            self.assertEqual(snapshot["app_tones"][0]["tone"], "auto")
            self.assertEqual(snapshot["snippets"], [{
                "name": "signature", "text": "Cheers"}])
            self.assertEqual(snapshot["manual_vocabulary"], ["Qwen"])
            self.assertEqual(snapshot["banned_vocabulary"], ["gwen"])
            self.assertEqual(snapshot["corrections"][0]["count"], 2)
            self.assertEqual(snapshot["corrections"][1]["count"], 0)
            self.assertEqual(
                snapshot["corrections"][1]["global_decision"], "learning")
            self.assertEqual(snapshot["corrections"][1]["app_scopes"], [])

    def test_snapshot_projects_scope_decisions_without_private_cases(self):
        lab = PersonalRegressionLab()
        lab.record_correction(
            "Gwen", "Qwen", app="com.example.mail")
        lab.propose("Gwen", "Qwen", app="com.example.mail")
        with tempfile.TemporaryDirectory() as directory:
            snippets = Path(directory) / "snippets.json"
            snippets.write_text("{}")
            ns = settings_namespace("gui_settings_snapshot")
            ns.update(
                GUI_TONES={"auto"},
                APP_TONES={"map": {}, "lock": threading.Lock()},
                recent_dictation_apps=lambda: [],
                app_display_name=lambda bundle: bundle.rsplit(".", 1)[-1].title(),
                SNIPPETS_FILE=snippets,
                SNIPPETS_LOCK=threading.Lock(),
                DICTIONARY_LOCK=threading.RLock(),
                parse_dictionary=lambda: ([], set()),
                LEARN_LOCK=threading.Lock(),
                personal_regression_lab=lambda _state: lab,
                PERSONAL_APP_MIN_COUNT=2,
                PERSONAL_GLOBAL_MIN_COUNT=3,
                load_learned=lambda: {
                    "confusions": {
                        "gwen->qwen": {
                            "from": "Gwen",
                            "to": "Qwen",
                            "n": 2,
                            "apps": {"com.example.mail": 2},
                        },
                    },
                    "snippet_edits": {},
                },
            )

            row = ns["gui_settings_snapshot"]()["corrections"][0]

            self.assertEqual(row["global_decision"], "learning")
            self.assertEqual(row["app_scopes"], [{
                "bundle": "com.example.mail",
                "name": "Mail",
                "count": 2,
                "decision": "active",
            }])
            self.assertNotIn("regression_lab", row)
            self.assertNotIn("cases", row)

    def test_forget_correction_preserves_explicit_vocabulary_and_unrelated_state(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "dictionary.txt"
            dictionary.write_text(
                "Qwen\n# --- auto-learned (managed by dictate.py) ---\nGwen\n")
            state = {
                "counts": {"Private Project": 4},
                "processed": 8,
                "fixes": {
                    "gwen": {"to": "Qwen"},
                    "other": {"to": "Unrelated"},
                },
                "confusions": {
                    "gwen": {
                        "from": "Gwen", "to": "Qwen", "n": 3,
                        "apps": {"com.example.private": 3}},
                    "other": {"from": "Other", "to": "Unrelated", "n": 4},
                },
                "snippet_edits": {"signature": {"to": "Private signature"}},
                "regression_lab": {},
                "history": [],
            }
            saved = []
            refreshed = []
            ns = load_definitions(
                "forget_gui_correction",
                extra={"time": time},
            )
            ns.update(
                LEARN_LOCK=threading.Lock(),
                load_learned=lambda: state,
                save_learned=lambda value: saved.append(value),
                personal_regression_lab=lambda _state:
                    PersonalRegressionLab(),
                refresh_glossary=lambda: refreshed.append(True),
            )
            before_dictionary = dictionary.read_bytes()
            ns["forget_gui_correction"]("gwen")
            result = saved[0]
            self.assertNotIn("gwen", result["confusions"])
            self.assertNotIn("gwen", result["fixes"])
            self.assertIn("other", result["confusions"])
            self.assertIn("other", result["fixes"])
            self.assertEqual(
                result["snippet_edits"]["signature"]["to"],
                "Private signature")
            self.assertEqual(result["counts"], {"Private Project": 4})
            self.assertEqual(dictionary.read_bytes(), before_dictionary)
            self.assertEqual(refreshed, [True])


class ResultEvidenceRuntimeTests(unittest.TestCase):
    def test_explicit_snapshot_is_bounded_and_closed(self):
        ns = load_definitions(
            "inspect_last_result_evidence",
            extra={"math": math},
        )
        ns["PIPELINE_STATE"] = {
            "last_result_evidence": {
                "alternatives": ["private alternative", "", 7],
                "protected_anchors": ["Qwen", "Whisper Face"],
                "proof_edits": [{
                    "kind": "filler",
                    "before": "um",
                    "after": "",
                    "accepted": True,
                    "reason": "allowlisted filler",
                }, {
                    "kind": "malformed",
                    "before": "ignored",
                    "after": "ignored",
                    "accepted": "yes",
                    "reason": "",
                }],
                "timings_ms": {
                    "release": 842.25,
                    "asr": 400,
                    "private_stage": 999,
                    "cleanup": float("nan"),
                },
            },
            "transcript": "must not escape",
        }

        snapshot = ns["inspect_last_result_evidence"]()

        self.assertEqual(set(snapshot), {
            "schema_version", "kind", "alternatives",
            "protected_anchors", "proof_edits", "timings_ms",
        })
        self.assertEqual(snapshot["alternatives"], ["private alternative"])
        self.assertEqual(
            snapshot["protected_anchors"], ["Qwen", "Whisper Face"])
        self.assertEqual(len(snapshot["proof_edits"]), 1)
        self.assertEqual(snapshot["timings_ms"], {
            "release": 842.2,
            "asr": 400.0,
        })
        self.assertNotIn("transcript", snapshot)
        self.assertNotIn("private_stage", snapshot["timings_ms"])


class RuntimeStatusWindowContractTests(unittest.TestCase):
    """The real status producer, fed straight into the window's normalizer."""

    def snapshot(self, *, installed=None, warm=None, receipt=True,
                 input_monitoring=True, post_events=True,
                 microphone="Ready"):
        from model_wallet import (
            CURRENT_PROVIDER_PROFILES, Capability, MAX_LATENCY_BOUND_MS,
            ModelRequest, PARAKEET_PROFILE, QWEN_CLEANUP_PROFILE,
            ReadinessState, WHISPER_LARGE_TURBO_PROFILE,
            WHISPER_TINY_PROFILE,
        )
        from model_wallet_shadow import RuntimeModelEvidence, \
            assess_model_wallet
        from types import ModuleType, SimpleNamespace

        default = (PARAKEET_PROFILE.provider_id,
                   WHISPER_TINY_PROFILE.provider_id)
        installed = default if installed is None else installed
        warm = installed if warm is None else warm
        evidence = SimpleNamespace(providers=tuple(
            SimpleNamespace(
                provider_id=profile.provider_id,
                state=(ReadinessState.RESOLVED
                       if profile.provider_id in installed
                       else ReadinessState.NOT_INSTALLED),
                revision_verified=profile.provider_id in installed,
            )
            for profile in CURRENT_PROVIDER_PROFILES
        )) if receipt else None
        quartz = ModuleType("Quartz")
        quartz.CGPreflightListenEventAccess = lambda: input_monitoring
        quartz.CGPreflightPostEventAccess = lambda: post_events
        lab = SimpleNamespace(cases=(), quarantined=())
        ns = load_definitions(
            "runtime_status_snapshot",
            "model_wallet_shadow_status_snapshot",
            "model_status_rows_from_shadow",
            extra={
                "STATUS": {"bar": SimpleNamespace(state="idle")},
                "usage_metrics": lambda: (120, 2.5),
                "load_learned": lambda: {},
                "personal_regression_lab": lambda _learned: lab,
                "PARAKEET_ENABLED": True,
                "PARAKEET_HELPER": Path(__file__),
                "PARAKEET": SimpleNamespace(process=None),
                "PIPELINE_STATE": {
                    "last_asr_engine": "parakeet-unified",
                    "last_release_s": 0.4,
                    "last_word_count": None,
                    "last_confidence": 0.93,
                    "last_mode": "capture",
                    "last_compiler_decisions": 0,
                    "last_protected_anchors": 0,
                    "last_stable_prefix_words": 0,
                    "last_alternatives": [],
                    "last_cleanup_edits": [],
                    "last_proof_edits_accepted": 0,
                    "last_proof_edits_rejected": 0,
                    "last_context_influence": "",
                    "last_delayed_cleanup_outcome": None,
                    "last_delayed_cleanup_applied": 0,
                    "last_delayed_cleanup_rejected": 0,
                    "last_delayed_cleanup_apply_ms": None,
                },
                "FLIGHT": SimpleNamespace(is_enabled=lambda: False),
                "PAUSED": {"on": False},
                "PREFERENCES": {
                    "flight_recorder": False, "sounds": "system",
                    "recent_dictations": False},
                "INSERTION_COORDINATOR": SimpleNamespace(
                    recoverable=lambda: []),
                "acoustic_keyword_memory_status_snapshot": lambda: {},
                "acoustic_calibration_status_snapshot": lambda: {},
                "acoustic_time_machine_status_snapshot": lambda: {
                    "enabled": False, "retained_spans": 0},
                "selective_relisten_status_snapshot": lambda: {
                    "requested": False, "evidence_ready": False,
                    "status": "receipt-missing"},
                "voice_object_inbox_status": lambda: {
                    "enabled": False, "queued_count": 0, "status": "Off"},
                "risky_action_confirmation_status_snapshot": lambda: {
                    "risk": "none", "state": "idle", "reason": "idle"},
                "current_face": lambda: "parrot",
                "HOTKEY_NAME": "alt_r",
                "UNDO_HOTKEY_NAME": "",
                "hotkey_label_for": lambda name: (
                    "Right Option" if name else ""),
                "hotkey_shared_modes": lambda _name: (),
                "normalize_sound_theme": lambda value: value,
                "undoable_insertion_status": lambda: {"available": False},
                "consequence_state_snapshot": lambda: {},
                "context_firewall_state_snapshot": lambda: {},
                "delayed_cleanup_activation_status": lambda: {},
                "MEASUREMENT_MODE": SimpleNamespace(
                    status_snapshot=lambda: {"active": False}),
                "mac_prefers_reduced_motion": lambda: False,
                "AUDIO_POOL": SimpleNamespace(readiness=lambda: microphone),
                "MODEL_READINESS_CACHE": {
                    "receipt": evidence, "lock": threading.Lock()},
                "MODEL_WARM_PATHS": {
                    "providers": set(warm), "lock": threading.Lock()},
                "CURRENT_PROVIDER_PROFILES": CURRENT_PROVIDER_PROFILES,
                "PARAKEET_PROFILE": PARAKEET_PROFILE,
                "WHISPER_TINY_PROFILE": WHISPER_TINY_PROFILE,
                "WHISPER_LARGE_TURBO_PROFILE": WHISPER_LARGE_TURBO_PROFILE,
                "QWEN_CLEANUP_PROFILE": QWEN_CLEANUP_PROFILE,
                "RuntimeModelEvidence": RuntimeModelEvidence,
                "ReadinessState": ReadinessState,
                "Capability": Capability,
                "ModelRequest": ModelRequest,
                "MAX_LATENCY_BOUND_MS": MAX_LATENCY_BOUND_MS,
                "assess_model_wallet": assess_model_wallet,
            },
        )
        previous = sys.modules.get("Quartz")
        sys.modules["Quartz"] = quartz
        try:
            return ns["runtime_status_snapshot"]()
        finally:
            if previous is None:
                sys.modules.pop("Quartz", None)
            else:
                sys.modules["Quartz"] = previous

    def window(self, snapshot):
        from whisper_face_gui import normalize_snapshot
        return normalize_snapshot(snapshot)

    def test_default_install_shows_no_warning_for_optional_models(self):
        state = self.window(self.snapshot())

        self.assertEqual(state.degraded_issues, ())
        self.assertEqual(state.status_phase, "ready")
        self.assertTrue(next(
            step for step in state.onboarding_steps
            if step.key == "models").complete)
        from whisper_face_gui import model_pill_tone
        self.assertEqual(
            [model_pill_tone(model) for model in state.models],
            ["good", "good", "neutral", "neutral"])
        self.assertEqual(
            [model.status for model in state.models],
            ["Ready", "Ready", "Optional", "Optional"])

    def test_warm_up_reads_as_starting_not_as_a_fault(self):
        state = self.window(self.snapshot(receipt=False, warm=()))

        self.assertEqual(state.degraded_issues, ())
        self.assertEqual(state.status_phase, "starting")
        models = next(
            step for step in state.onboarding_steps if step.key == "models")
        self.assertFalse(models.complete)
        self.assertEqual(models.status, "Warming up")

    def test_full_install_is_all_green(self):
        from model_wallet import CURRENT_PROVIDER_PROFILES
        from whisper_face_gui import model_pill_tone

        everything = tuple(
            profile.provider_id for profile in CURRENT_PROVIDER_PROFILES)
        state = self.window(self.snapshot(installed=everything))

        self.assertEqual(state.degraded_issues, ())
        self.assertEqual(
            {model_pill_tone(model) for model in state.models}, {"good"})

    def test_a_missing_required_model_is_an_error_naming_it(self):
        from model_wallet import WHISPER_TINY_PROFILE

        state = self.window(self.snapshot(
            installed=(WHISPER_TINY_PROFILE.provider_id,)))

        self.assertEqual(
            [(issue.key, issue.severity) for issue in state.degraded_issues],
            [("models", "error")])
        self.assertIn("Parakeet Unified 0.6B", state.degraded_issues[0].detail)
        self.assertEqual(state.status_phase, "degraded")

    def test_support_snapshot_names_healthy_models(self):
        from whisper_face_gui import support_snapshot_text

        payload = json.loads(support_snapshot_text(
            self.window(self.snapshot())))

        self.assertEqual(
            sorted(model["status"] for model in payload["models"]),
            ["ready", "ready", "unavailable", "unavailable"])


class ControlPreferenceRuntimeTests(unittest.TestCase):
    """A1/A3/D1: the window's control setters persist through preferences."""

    def namespace(self, path, *, applied=None, saved=None):
        applied = [] if applied is None else applied
        saved = [] if saved is None else saved
        return load_definitions(
            "set_hotkey", "set_undo_hotkey", "set_sound_theme",
            "set_recent_dictations_enabled", "preview_sound_cue",
            "normalize_hotkey", "normalize_sound_theme", "save_preferences",
            "hotkey_binding_decision", "hotkey_label_for",
            "hotkey_shared_modes",
            assignments={
                "PREFERENCES", "HOTKEY_CHOICES", "HOTKEY_DEFAULT",
                "HOTKEY_MODIFIER_FAMILY", "HOTKEY_MODES_BY_MODIFIER",
                "SOUND_THEMES", "SOUND_THEME_DEFAULT", "SOUND_CUES",
            },
            extra={
                "IS_MACOS": True,
                "DEFAULT_FACE": "parrot",
                "PREFERENCES_FILE": path,
                "json": json,
                "print": lambda *_a, **_k: None,
                "apply_hotkey_bindings": lambda: applied.append(True),
                "play": saved.append,
                "current_face": lambda: "parrot",
                "IS_WINDOWS": False,
                "atomic_write_text": lambda target, value:
                    target.write_text(value, encoding="utf-8"),
            },
        )

    def test_a_bindable_key_is_applied_and_written_to_disk(self):
        applied: list = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path, applied=applied)

            verdict = ns["set_hotkey"]("f13")

            self.assertTrue(verdict["accepted"])
            self.assertEqual(verdict["label"], "F13")
            self.assertEqual(applied, [True])
            self.assertEqual(
                json.loads(path.read_text())["hotkey"], "f13")

    def test_an_unbindable_key_is_refused_without_touching_the_file(self):
        applied: list = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path, applied=applied)

            for candidate in ("a", "space", "", "  ", None):
                with self.subTest(candidate=candidate):
                    verdict = ns["set_hotkey"](candidate)
                    self.assertFalse(verdict["accepted"])
                    self.assertEqual(verdict["reason"], "unsupported_key")
            self.assertEqual(applied, [])
            self.assertFalse(path.exists())

    def test_a_mode_sharing_key_is_accepted_and_declares_its_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path)

            verdict = ns["set_hotkey"]("shift_r")

            self.assertTrue(verdict["accepted"])
            self.assertEqual(verdict["shared_modes"], ("compose", "code"))

    def test_the_undo_key_cannot_be_the_capture_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path)
            ns["set_hotkey"]("f13")

            verdict = ns["set_undo_hotkey"]("f13")

            self.assertFalse(verdict["accepted"])
            self.assertEqual(verdict["reason"], "conflicts_with_capture")
            self.assertEqual(
                json.loads(path.read_text())["undo_hotkey"], "")

    def test_the_undo_key_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path)
            ns["set_undo_hotkey"]("f14")
            self.assertEqual(
                json.loads(path.read_text())["undo_hotkey"], "f14")

            verdict = ns["set_undo_hotkey"]("")

            self.assertTrue(verdict["accepted"])
            self.assertEqual(verdict["reason"], "unbound")
            self.assertEqual(
                json.loads(path.read_text())["undo_hotkey"], "")

    def test_sound_and_recent_settings_persist_and_reject_nonsense(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path)

            self.assertEqual(ns["set_sound_theme"]("silent"), "silent")
            self.assertEqual(json.loads(path.read_text())["sounds"], "silent")
            self.assertEqual(ns["set_sound_theme"]("airhorn"), "system")
            self.assertTrue(ns["set_recent_dictations_enabled"](True))
            self.assertTrue(
                json.loads(path.read_text())["recent_dictations"])
            self.assertFalse(ns["set_recent_dictations_enabled"](False))

    def test_only_known_cue_names_reach_the_sound_player(self):
        played: list = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            ns = self.namespace(path, saved=played)

            self.assertTrue(ns["preview_sound_cue"]("Pop"))
            # play() interpolates its argument into a filesystem path.
            for candidate in ("../../etc/passwd", "", "Boom"):
                with self.subTest(candidate=candidate):
                    self.assertFalse(ns["preview_sound_cue"](candidate))
            self.assertEqual(played, ["Pop"])


if __name__ == "__main__":
    unittest.main()
