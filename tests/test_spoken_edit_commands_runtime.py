# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Runtime seam tests for opt-in, stateless spoken edit commands."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_dictate import load_definitions  # noqa: E402
from parrot_core import (  # noqa: E402
    EDIT_COMMAND_UNDO,
    EDIT_COMMAND_DELETE_WORD,
    EDIT_COMMAND_DELETE_SENTENCE,
    EDIT_COMMAND_NEWLINE,
    EDIT_COMMAND_NEWPARAGRAPH,
    EDIT_COMMAND_UPPERCASE_LAST,
    EDIT_COMMAND_CAPITALIZE_LAST,
    EDIT_COMMAND_LOWERCASE_LAST,
    classify_edit_command,
    transform_last_insertion,
)


class _FakeKey:
    """Stable stand-in for a pynput Key; identity is what the assertions use."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"Key.{self.name}"


# One shared keyboard object so injected keys keep object identity across a test.
_FAKE_KEYBOARD = SimpleNamespace(Key=SimpleNamespace(
    cmd=_FakeKey("cmd"),
    alt=_FakeKey("alt"),
    backspace=_FakeKey("backspace"),
    enter=_FakeKey("enter"),
    shift=_FakeKey("shift"),
    ctrl=_FakeKey("ctrl"),
    esc=_FakeKey("esc"),
    left=_FakeKey("left"),
))


class _RecordingController:
    """Fake keyboard controller that records every press/release in order."""

    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))


def _snapshot(element, text, selection):
    """A stand-in FocusSnapshot: identity (.element) plus field text/selection."""
    return SimpleNamespace(element=element, text=text, selection=selection)


def runtime_namespace(*, enabled, is_macos, last_insertion=None, snapshot=None):
    kb = _RecordingController()

    def fake_focused_snapshot():
        return snapshot

    def fake_focus_destination_matches(original, current, ob, cb):
        # Mirror production's contract without touching Accessibility: same
        # destination iff both snapshots exist, bundles agree, and the elements
        # are identical.
        return bool(
            original is not None and current is not None
            and ob == cb and original.element == current.element)

    def fake_paste(text):
        kb.events.append(("paste", text))

    return load_definitions(
        "apply_spoken_edit_command",
        "_press_edit_chord",
        extra={
            "IS_MACOS": is_macos,
            "PREFERENCES": {"spoken_edit_commands": enabled},
            "classify_edit_command": classify_edit_command,
            "transform_last_insertion": transform_last_insertion,
            "focused_snapshot": fake_focused_snapshot,
            "focus_destination_matches": fake_focus_destination_matches,
            "paste": fake_paste,
            "play": lambda *args, **kwargs: None,
            "LAST_INSERTION": last_insertion,
            "keyboard": _FAKE_KEYBOARD,
            "kb": kb,
            "EDIT_COMMAND_UNDO": EDIT_COMMAND_UNDO,
            "EDIT_COMMAND_DELETE_WORD": EDIT_COMMAND_DELETE_WORD,
            "EDIT_COMMAND_DELETE_SENTENCE": EDIT_COMMAND_DELETE_SENTENCE,
            "EDIT_COMMAND_NEWLINE": EDIT_COMMAND_NEWLINE,
            "EDIT_COMMAND_NEWPARAGRAPH": EDIT_COMMAND_NEWPARAGRAPH,
            "EDIT_COMMAND_UPPERCASE_LAST": EDIT_COMMAND_UPPERCASE_LAST,
            "EDIT_COMMAND_CAPITALIZE_LAST": EDIT_COMMAND_CAPITALIZE_LAST,
            "EDIT_COMMAND_LOWERCASE_LAST": EDIT_COMMAND_LOWERCASE_LAST,
        },
    )


def _rec(mode="capture"):
    return SimpleNamespace(mode=mode, utterance_id="utterance-1")


BUNDLE = "com.example.editor"


class SpokenEditCommandRuntimeTests(unittest.TestCase):
    def test_enabled_tier1_commands_emit_expected_key_events(self):
        key = _FAKE_KEYBOARD.Key
        cmd_z = [("press", key.cmd), ("press", "z"),
                 ("release", "z"), ("release", key.cmd)]
        cases = {
            "scratch that": cmd_z,
            "undo that": cmd_z,
            "undo": cmd_z,
            "delete last sentence": cmd_z,
            "delete that": cmd_z,
            "delete last word": [
                ("press", key.alt), ("press", key.backspace),
                ("release", key.backspace), ("release", key.alt)],
            "new line": [("press", key.enter), ("release", key.enter)],
            "new paragraph": [
                ("press", key.enter), ("release", key.enter),
                ("press", key.enter), ("release", key.enter)],
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                runtime = runtime_namespace(enabled=True, is_macos=True)
                handled = runtime["apply_spoken_edit_command"](
                    phrase, _rec(), BUNDLE)
                self.assertTrue(handled)
                self.assertEqual(runtime["kb"].events, expected)

    def test_pref_disabled_performs_no_action(self):
        runtime = runtime_namespace(enabled=False, is_macos=True)
        self.assertFalse(runtime["apply_spoken_edit_command"](
            "scratch that", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_non_macos_performs_no_action(self):
        runtime = runtime_namespace(enabled=True, is_macos=False)
        self.assertFalse(runtime["apply_spoken_edit_command"](
            "scratch that", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_near_miss_utterance_preserves_normal_dictation(self):
        runtime = runtime_namespace(enabled=True, is_macos=True)
        for utterance in (
            "lets scratch that plan",
            "please undo the change",
            "add a new line here",
            "delete last word from the note",
        ):
            with self.subTest(utterance=utterance):
                self.assertFalse(runtime["apply_spoken_edit_command"](
                    utterance, _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_unsupported_mode_is_ignored(self):
        runtime = runtime_namespace(enabled=True, is_macos=True)
        for mode in ("command", "edit", "reply", "compose"):
            with self.subTest(mode=mode):
                self.assertFalse(runtime["apply_spoken_edit_command"](
                    "scratch that", _rec(mode), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_verbs_fall_through_without_acting(self):
        # With no tracked insertion (LAST_INSERTION is None) the Tier 2 case
        # verbs have nothing to rewrite, so the dispatcher declines them and they
        # fall through to normal dictation without emitting any keystroke.
        runtime = runtime_namespace(enabled=True, is_macos=True)
        for phrase in (
            "all caps", "uppercase that", "capitalize that", "lowercase that",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(runtime["apply_spoken_edit_command"](
                    phrase, _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_happy_path_rewrites_verified_last_insertion(self):
        key = _FAKE_KEYBOARD.Key
        chord = [("press", key.shift), ("press", key.left),
                 ("release", key.left), ("release", key.shift)]
        cases = [
            ("all caps", "hello", "HELLO"),
            ("uppercase that", "hello", "HELLO"),
            ("capitalize that", "hello world", "Hello world"),
            ("lowercase that", "HELLO", "hello"),
        ]
        for phrase, inserted, transformed in cases:
            with self.subTest(phrase=phrase):
                prefix = "before "
                fresh = _snapshot(
                    "elem-A", prefix + inserted, (len(prefix + inserted), 0))
                stored = _snapshot("elem-A", "", (0, 0))
                last = {"text": inserted, "element": stored,
                        "bundle": BUNDLE, "utterance_id": "u1"}
                runtime = runtime_namespace(
                    enabled=True, is_macos=True,
                    last_insertion=last, snapshot=fresh)
                handled = runtime["apply_spoken_edit_command"](
                    phrase, _rec(), BUNDLE)
                self.assertTrue(handled)
                # Shift+Left once per inserted char selects exactly that text,
                # then a single paste of the transform replaces the selection.
                self.assertEqual(
                    runtime["kb"].events,
                    chord * len(inserted) + [("paste", transformed)])
                # LAST_INSERTION advances so a follow-up command sees new text.
                self.assertEqual(last["text"], transformed)
                self.assertEqual(
                    runtime["LAST_INSERTION"]["text"], transformed)

    def test_tier2_fail_closed_on_focus_drift(self):
        # Same text before the caret, but a different field element: never edit.
        fresh = _snapshot("elem-B", "before hello", (len("before hello"), 0))
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "hello", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=last, snapshot=fresh)
        self.assertTrue(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])
        self.assertEqual(last["text"], "hello")

    def test_tier2_fail_closed_on_bundle_change(self):
        # Same element, but the frontmost bundle changed under us: never edit.
        fresh = _snapshot("elem-A", "before hello", (len("before hello"), 0))
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "hello", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=last, snapshot=fresh)
        self.assertTrue(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), "com.example.other"))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_fail_closed_when_text_before_cursor_differs(self):
        # The user typed something else after the insertion, so the bytes before
        # the caret are no longer the tracked text: refuse to edit.
        fresh = _snapshot("elem-A", "before world", (len("before world"), 0))
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "hello", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=last, snapshot=fresh)
        self.assertTrue(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_fail_closed_on_active_selection(self):
        # A non-zero selection is live; rewriting would replace the user's
        # selection rather than the tracked insertion. Refuse to edit.
        fresh = _snapshot("elem-A", "before hello", (7, 5))
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "hello", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=last, snapshot=fresh)
        self.assertTrue(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_fail_closed_when_snapshot_unreadable(self):
        # A field exposing no text/selection (snapshot is None) is never
        # rewritten blindly.
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "hello", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=last, snapshot=None)
        self.assertTrue(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_none_last_insertion_falls_through(self):
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=None,
            snapshot=_snapshot(
                "elem-A", "before hello", (len("before hello"), 0)))
        self.assertFalse(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_noop_transform_falls_through(self):
        # Text is already uppercase: the transform is a no-op, so the command
        # falls through to normal dictation and emits nothing.
        fresh = _snapshot("elem-A", "before HELLO", (len("before HELLO"), 0))
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "HELLO", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        runtime = runtime_namespace(
            enabled=True, is_macos=True, last_insertion=last, snapshot=fresh)
        self.assertFalse(runtime["apply_spoken_edit_command"](
            "all caps", _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_tier2_disabled_or_non_macos_never_acts(self):
        fresh = _snapshot("elem-A", "before hello", (len("before hello"), 0))
        stored = _snapshot("elem-A", "", (0, 0))
        last = {"text": "hello", "element": stored,
                "bundle": BUNDLE, "utterance_id": "u1"}
        for enabled, is_macos in ((False, True), (True, False)):
            with self.subTest(enabled=enabled, is_macos=is_macos):
                runtime = runtime_namespace(
                    enabled=enabled, is_macos=is_macos,
                    last_insertion=last, snapshot=fresh)
                self.assertFalse(runtime["apply_spoken_edit_command"](
                    "all caps", _rec(), BUNDLE))
                self.assertEqual(runtime["kb"].events, [])
        # The pref-gated path must never mutate the tracked text.
        self.assertEqual(last["text"], "hello")

    def test_runtime_hook_uses_exact_recognition_pre_compiler(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn(
            "apply_spoken_edit_command(\n"
            "                recognized_raw, rec, bundle)",
            source,
        )


class CaseTransformTests(unittest.TestCase):
    """Pure, side-effect-free case transforms applied to the last insertion."""

    def test_uppercase(self):
        self.assertEqual(
            transform_last_insertion(EDIT_COMMAND_UPPERCASE_LAST, "hello"),
            "HELLO")

    def test_lowercase(self):
        self.assertEqual(
            transform_last_insertion(EDIT_COMMAND_LOWERCASE_LAST, "HeLLo"),
            "hello")

    def test_capitalize_is_sentence_style(self):
        self.assertEqual(
            transform_last_insertion(
                EDIT_COMMAND_CAPITALIZE_LAST, "hello world"),
            "Hello world")

    def test_capitalize_skips_leading_non_cased(self):
        self.assertEqual(
            transform_last_insertion(EDIT_COMMAND_CAPITALIZE_LAST, "  'hello'"),
            "  'Hello'")

    def test_capitalize_leaves_interior_casing_untouched(self):
        # Sentence-style, not title-case: only the first cased character moves.
        self.assertEqual(
            transform_last_insertion(
                EDIT_COMMAND_CAPITALIZE_LAST, "hELLO wORLD"),
            "HELLO wORLD")

    def test_no_op_when_already_in_target_case(self):
        self.assertIsNone(
            transform_last_insertion(EDIT_COMMAND_UPPERCASE_LAST, "HELLO"))
        self.assertIsNone(
            transform_last_insertion(EDIT_COMMAND_LOWERCASE_LAST, "hello"))
        self.assertIsNone(
            transform_last_insertion(EDIT_COMMAND_CAPITALIZE_LAST, "Hello"))

    def test_empty_text_is_no_op(self):
        self.assertIsNone(
            transform_last_insertion(EDIT_COMMAND_UPPERCASE_LAST, ""))

    def test_non_case_command_returns_none(self):
        self.assertIsNone(
            transform_last_insertion(EDIT_COMMAND_UNDO, "hello"))


if __name__ == "__main__":
    unittest.main()
