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
        self.learned = Path(self.directory.name) / "learned.json"
        self.ns = settings_namespace(
            "save_gui_snippet", "delete_gui_snippet", "snippet_name_key",
            "load_learned", "save_learned")
        self.ns.update(
            SNIPPETS_FILE=self.path,
            SNIPPETS_LOCK=threading.Lock(),
            LEARNED_FILE=self.learned,
            LEARN_LOCK=threading.Lock(),
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

    def test_delete_also_removes_what_was_learned_about_the_snippet(self):
        self.path.write_text(json.dumps({
            "address": "7623 Opal Ridge Lane", "signature": "Cheers"}))
        self.learned.write_text(json.dumps({
            "snippet_edits": {
                "address": {"from": "123 Main", "to": "7623 Opal Ridge Lane",
                            "n": 1, "app": "com.apple.mail"},
                "signature": {"from": "Bye", "to": "Cheers", "n": 1,
                              "app": "com.apple.mail"},
            },
            "history": [
                {"ts": 1.0, "kind": "snippet_edit", "name": "address",
                 "from": "123 Main", "to": "7623 Opal Ridge Lane",
                 "app": "com.apple.mail"},
                {"ts": 2.0, "kind": "snippet_edit", "name": "signature",
                 "from": "Bye", "to": "Cheers", "app": "com.apple.mail"},
                {"ts": 3.0, "kind": "correction", "from": "Gwen",
                 "to": "Qwen", "app": "com.apple.mail", "mode": "capture"},
            ],
        }))

        self.ns["delete_gui_snippet"]("address", "7623 Opal Ridge Lane")

        remaining = self.learned.read_text()
        self.assertNotIn("Opal Ridge", remaining)
        self.assertNotIn("123 Main", remaining)
        state = json.loads(remaining)
        self.assertEqual(list(state["snippet_edits"]), ["signature"])
        self.assertEqual(
            [entry["kind"] for entry in state["history"]],
            ["snippet_edit", "correction"])

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
                        "signature": {"to": "Cheers", "n": 2},
                        "address": {"suggested": {
                            "from": "7623 Opal Ridge Lane",
                            "to": "1 Attacker Way", "app": "com.example"}}},
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
            # An observed snippet edit the user has not applied is not in
            # use, so nothing reports it as the snippet's saved text.
            self.assertEqual(
                [(row["key"], row["global_decision"])
                 for row in snapshot["corrections"] if row["kind"] == "snippet"],
                [("signature", "saved")])
            self.assertNotIn("Attacker", json.dumps(snapshot))

    def test_an_observed_snippet_edit_is_not_shown_as_the_saved_snippet(self):
        from whisper_face_gui import normalize_settings

        saved = "GB29 NWBK 6016 1331 9268 19"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "snippets.json").write_text(
                json.dumps({"bank details": saved}))
            ns = settings_namespace(
                "gui_settings_snapshot", "load_learned", "save_learned",
                "suggest_snippet_edit")
            ns.update(
                GUI_TONES={"auto", "formal", "default"},
                APP_TONES={"map": {}, "lock": threading.Lock()},
                recent_dictation_apps=lambda: [],
                app_display_name=lambda bundle: bundle,
                SNIPPETS_FILE=root / "snippets.json",
                SNIPPETS_LOCK=threading.Lock(),
                DICTIONARY_LOCK=threading.RLock(),
                parse_dictionary=lambda: ([], set()),
                LEARNED_FILE=root / "learned.json",
                LEARN_LOCK=threading.Lock(),
                personal_regression_lab=lambda _state: PersonalRegressionLab(),
                PERSONAL_APP_MIN_COUNT=2,
                PERSONAL_GLOBAL_MIN_COUNT=3,
                print=lambda *_args, **_kwargs: None,
            )
            # A page rewrote the pasted bank details in its own field.
            self.assertTrue(ns["suggest_snippet_edit"](
                "bank details", saved, "GB33 EVIL 0000 0000 0000 00",
                "com.google.Chrome"))
            snapshot = ns["gui_settings_snapshot"]()

        self.assertEqual(
            snapshot["snippets"], [{"name": "bank details", "text": saved}])
        self.assertEqual(snapshot["corrections"], [])
        # The window is given nothing it could present as the snippet's
        # replacement.
        self.assertEqual(list(normalize_settings(snapshot).corrections), [])

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

    def test_forget_correction_clears_its_count_history_and_evidence(self):
        lab = PersonalRegressionLab()
        for app in ("com.example.mail", "com.example.mail", "com.example.chat"):
            lab.record_correction("Sersha", "Saoirse", app=app)
        lab.propose("Sersha", "Saoirse", app="com.example.mail")
        lab.propose("Sersha", "Saoirse")
        # The mapping was later undone in an app that never corrected it.
        lab.record_revert("Sersha", "Saoirse", app="com.example.notes")
        state = {
            "counts": {"Saoirse": 3, "Private Project": 4},
            "processed": 8,
            "fixes": {"sersha": {"to": "Saoirse", "n": 3}},
            "confusions": {
                "sersha->saoirse": {
                    "from": "Sersha", "to": "Saoirse", "n": 3,
                    "apps": {"com.example.mail": 2, "com.example.chat": 1}},
            },
            "snippet_edits": {},
            "regression_lab": lab.to_dict(),
            "history": [
                {"ts": 1.0, "kind": "correction", "from": "Sersha",
                 "to": "Saoirse", "app": "com.example.mail",
                 "mode": "capture"},
                {"ts": 2.0, "kind": "correction", "from": "Gwen",
                 "to": "Qwen", "app": "com.example.mail", "mode": "capture"},
                {"ts": 3.0, "kind": "forgotten", "from": "Sersha",
                 "to": "Saoirse"},
            ],
        }
        saved = []
        ns = load_definitions(
            "forget_gui_correction", "personal_regression_lab",
            extra={"time": time, "PersonalRegressionLab": PersonalRegressionLab},
        )
        ns.update(
            LEARN_LOCK=threading.Lock(),
            load_learned=lambda: state,
            save_learned=lambda value: saved.append(
                json.loads(json.dumps(value))),
            refresh_glossary=lambda: None,
        )

        ns["forget_gui_correction"]("sersha->saoirse")

        result = saved[0]
        # Nothing left to promote the term again, and nothing that still
        # spells the mapping out.
        self.assertEqual(result["counts"], {"Private Project": 4})
        encoded = json.dumps(result)
        self.assertNotIn("Saoirse", encoded)
        self.assertNotIn("Sersha", encoded)
        self.assertEqual(
            [entry["kind"] for entry in result["history"]],
            ["correction", "forgotten"])
        self.assertEqual(set(result["history"][-1]), {"ts", "kind"})
        restored = PersonalRegressionLab.from_dict(result["regression_lab"])
        self.assertEqual(
            (restored.cases, restored.promoted, restored.quarantined),
            ((), (), ()))


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
                 microphone="Ready", outbox=None, parakeet=None,
                 last_engine="parakeet-unified", speech_runtime=False,
                 speech_program=""):
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
                "PARAKEET": parakeet or SimpleNamespace(process=None),
                "speech_runtime_selected": lambda: speech_runtime,
                "PIPELINE_STATE": {
                    "last_asr_engine": last_engine,
                    "last_speech_program": speech_program,
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
                "INSERTION_COORDINATOR": outbox or SimpleNamespace(
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

    def test_a_running_speech_runtime_counts_as_parakeet_running(self):
        # With PARROT_ASR_RUNTIME=own the runtime may be the only program
        # loaded (the FluidAudio helper is closed while it answers). Before
        # the first dictation names an engine, either one running is
        # Parakeet, not "Warming up".
        from types import SimpleNamespace
        live = SimpleNamespace(poll=lambda: None)
        ended = SimpleNamespace(poll=lambda: 1)
        cases = (
            (SimpleNamespace(process=None), "Warming up"),
            (SimpleNamespace(process=live), "Parakeet Unified"),
            (SimpleNamespace(process=None, preferred=SimpleNamespace(
                process=live)), "Parakeet Unified"),
            (SimpleNamespace(process=None, preferred=SimpleNamespace(
                process=ended)), "Warming up"),
            (SimpleNamespace(process=None, preferred=None), "Warming up"),
        )
        for parakeet, expected in cases:
            with self.subTest(parakeet=parakeet):
                self.assertEqual(self.snapshot(
                    parakeet=parakeet, last_engine=None)["active_engine"],
                    expected)

    def test_the_window_shows_the_speech_runtime_switch_and_last_program(
            self):
        # The status snapshot carries the switch as the runtime resolved it
        # (environment over preference) and which program ran the model for
        # the last dictation; the window keeps only the two words it shows.
        cases = (
            ((False, ""), (False, "")),
            ((True, ""), (True, "")),
            ((True, "new"), (True, "new")),
            ((False, "previous"), (False, "previous")),
            ((True, "fluidaudio"), (True, "")),
        )
        for (selected, program), expected in cases:
            with self.subTest(selected=selected, program=program):
                snapshot = self.snapshot(
                    speech_runtime=selected, speech_program=program)
                self.assertIs(snapshot["new_speech_runtime"], selected)
                self.assertEqual(
                    snapshot["new_speech_runtime_last"], program)
                state = self.window(snapshot)
                self.assertEqual(
                    (state.new_speech_runtime,
                     state.new_speech_runtime_last), expected)

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

    def test_missing_input_monitoring_is_not_reported_as_accessibility(self):
        state = self.window(self.snapshot(input_monitoring=False))

        self.assertEqual(
            [issue.key for issue in state.degraded_issues],
            ["input_monitoring"])
        self.assertEqual(state.missing_permissions, ("input_monitoring",))
        self.assertEqual(state.accessibility_status, "Granted")

    def test_a_failed_microphone_is_missing_and_a_starting_one_is_not(self):
        failed = self.window(self.snapshot(microphone="Unavailable"))
        starting = self.window(self.snapshot(microphone="Starting"))

        self.assertEqual(failed.missing_permissions, ("microphone",))
        self.assertFalse(failed.onboarding_steps[0].complete)
        self.assertEqual(starting.missing_permissions, ())
        self.assertEqual(starting.degraded_issues, ())

    def test_each_permission_opens_its_own_settings_pane(self):
        from types import SimpleNamespace

        commands = []
        ns = load_definitions(
            "open_mac_system_settings",
            extra={"IS_MACOS": True, "subprocess": SimpleNamespace(
                DEVNULL=None,
                run=lambda command, **_kwargs: commands.append(command))},
        )
        for pane in ("microphone", "accessibility", "input_monitoring",
                     "something else"):
            ns["open_mac_system_settings"](pane)

        self.assertEqual(commands, [
            ["open", "x-apple.systempreferences:"
             "com.apple.preference.security?Privacy_Microphone"],
            ["open", "x-apple.systempreferences:"
             "com.apple.preference.security?Privacy_Accessibility"],
            ["open", "x-apple.systempreferences:"
             "com.apple.preference.security?Privacy_ListenEvent"],
            ["open", "-a", "System Settings"],
        ])

    def outbox(self, *outcomes):
        """A real coordinator after one dictation per named outcome."""
        from insertion_integrity import (
            DestinationObservation, InsertionCoordinator, InsertionLease,
            ReadbackResult)

        coordinator = InsertionCoordinator()
        for index, outcome in enumerate(outcomes):
            utterance = f"take-{index}"
            if outcome == "went-quiet":
                # A field the runtime read, which answered nothing once
                # the paste was sent: not an app that hides its text.
                coordinator.stage(InsertionLease.capture(
                    utterance, "com.apple.Notes:4", (3, 0), "Hi."), "Ship it")
                coordinator.commit(
                    utterance,
                    DestinationObservation.capture(
                        "com.apple.Notes:4", (3, 0), "Hi."),
                    lambda _text: None, ReadbackResult.unverifiable)
                continue
            coordinator.stage(InsertionLease.capture_opaque(
                utterance, "com.apple.MobileSMS:7", "Chat"), "Ship it")
            coordinator.commit(
                utterance,
                DestinationObservation.capture(
                    "com.apple.Notes:9" if outcome == "not-pasted"
                    else "com.apple.MobileSMS:7", (0, 0), "Chat"),
                lambda _text: None,
                (lambda: ReadbackResult.conflict("divergent"))
                if outcome == "conflict" else ReadbackResult.unverifiable)
        return coordinator

    # Words that would tell the person something needs them.
    NAGS = ("recover", "review", "attention", "verify", "needs", "fail",
            "error", "unverified", "problem", "action needed", "!")

    def test_five_unchecked_pastes_in_a_row_leave_the_window_calm(self):
        from whisper_face_gui import (
            home_bubble_actions, home_review_due, home_review_in_row,
            localized_string, sidebar_state)

        outbox = self.outbox(*["unchecked"] * 5)
        snapshot = self.snapshot(outbox=outbox)

        # The copies are all there, and none of them needs a home.
        self.assertEqual(snapshot["outbox_count"], 5)
        self.assertEqual(snapshot["outbox_needs_home"], 0)
        self.assertEqual(
            snapshot["outbox_summary"], "Pasted, not double-checked")

        state = self.window(snapshot)
        # The phase a Mac with an empty outbox would be in, word for word.
        idle = self.window(self.snapshot())
        self.assertEqual(state.status_phase, "ready")
        self.assertEqual(state.status_phase, idle.status_phase)
        self.assertEqual(state.status_title, idle.status_title)
        self.assertEqual(state.status_detail, idle.status_detail)
        self.assertEqual(state.degraded_issues, ())
        # The sidebar's state word and its dot: Ready, and green.
        self.assertEqual(
            sidebar_state(state.status_phase, setting_up=False),
            ("shell.state.ready", "good"))
        # Home still counts them and still offers the copy; they add no
        # review of their own to what an empty outbox would show.
        self.assertEqual(state.outbox_count, 5)
        self.assertEqual(state.outbox_unchecked, 5)
        self.assertEqual(
            home_bubble_actions(state), ("pause", "copy_outbox"))
        self.assertEqual(home_review_due(state), home_review_due(idle))
        self.assertEqual(
            home_review_in_row(state), home_review_due(idle))
        row = localized_string("overview.outbox.unchecked", count=5)
        self.assertEqual(
            row, "Voice Outbox: 5 pasted, not double-checked")
        shown = (state.status_title, state.status_detail,
                 state.outbox_summary, row,
                 localized_string(sidebar_state(
                     state.status_phase, setting_up=False)[0]))
        for text in shown:
            for nag in self.NAGS:
                self.assertNotIn(nag, text.casefold(), text)

        # The menu bar says the same thing the same way.
        menu = load_definitions(
            "voice_outbox_menu_title",
            extra={"VOICE_OUTBOX_MAX_ITEMS": 20})["voice_outbox_menu_title"]
        title = menu(outbox.recoverable_count(), outbox.needs_home_count())
        self.assertEqual(
            title, "Voice Outbox — 5 pasted, not double-checked")
        for nag in self.NAGS:
            self.assertNotIn(nag, title.casefold())

    def test_one_entry_that_needs_a_home_keeps_todays_treatment(self):
        from whisper_face_gui import (
            home_outbox_line, localized_string, sidebar_state)

        menu = load_definitions(
            "voice_outbox_menu_title",
            extra={"VOICE_OUTBOX_MAX_ITEMS": 20})["voice_outbox_menu_title"]

        for outcome, summary in (
            ("not-pasted", "Not pasted — destination changed"),
            ("conflict", "Paste may have landed — verify before reusing"),
            ("went-quiet", "Paste may have landed — verify before reusing"),
        ):
            with self.subTest(outcome=outcome):
                # Its place in the queue does not matter: buried under
                # calm copies or the newest entry, it is what is shown.
                for order in (
                    [outcome] + ["unchecked"] * 5,
                    ["unchecked"] * 5 + [outcome],
                    ["unchecked"] * 2 + [outcome] + ["unchecked"] * 3,
                ):
                    outbox = self.outbox(*order)
                    snapshot = self.snapshot(outbox=outbox)
                    self.assertEqual(snapshot["outbox_count"], 6)
                    self.assertEqual(snapshot["outbox_needs_home"], 1)
                    self.assertEqual(snapshot["outbox_summary"], summary)
                    state = self.window(snapshot)
                    self.assertEqual(state.status_phase, "recovery")
                    self.assertEqual(
                        state.status_title, localized_string(
                            "overview.status.recovery.title"))
                    # One dictation needs the review, not six.
                    self.assertEqual(
                        state.status_detail, localized_string(
                            "overview.status.recovery.detail.one"))
                    self.assertEqual(
                        sidebar_state(state.status_phase, setting_up=False),
                        ("shell.state.recovery", "attention"))
                    self.assertEqual(state.outbox_unchecked, 5)
                    self.assertEqual(state.outbox_summary, summary)
                    # Home's row and the menu count the one that needs a
                    # home apart from the five copies, as the headline
                    # does.
                    key, values = home_outbox_line(state)
                    self.assertEqual(
                        localized_string(key, **values),
                        f"Voice Outbox: 1 recoverable, 5 pasted · {summary}")
                    self.assertEqual(
                        menu(outbox.recoverable_count(),
                             outbox.needs_home_count()),
                        "Voice Outbox — 1 recoverable, 5 pasted")
                    self.assertEqual(
                        outbox.recoverable_counts(), (6, 1))

    def test_an_outbox_of_real_failures_is_exactly_as_before(self):
        from whisper_face_gui import localized_string

        outbox = self.outbox("not-pasted", "conflict", "not-pasted")
        snapshot = self.snapshot(outbox=outbox)
        self.assertEqual(snapshot["outbox_count"], 3)
        self.assertEqual(snapshot["outbox_needs_home"], 3)
        self.assertEqual(
            snapshot["outbox_summary"], "Not pasted — destination changed")
        state = self.window(snapshot)
        self.assertEqual(state.status_phase, "recovery")
        self.assertEqual(state.outbox_unchecked, 0)
        self.assertEqual(state.status_detail, localized_string(
            "overview.status.recovery.detail.many", count=3))

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
