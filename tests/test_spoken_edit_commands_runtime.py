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
))


class _RecordingController:
    """Fake keyboard controller that records every press/release in order."""

    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))


def runtime_namespace(*, enabled, is_macos):
    return load_definitions(
        "apply_spoken_edit_command",
        "_press_edit_chord",
        extra={
            "IS_MACOS": is_macos,
            "PREFERENCES": {"spoken_edit_commands": enabled},
            "classify_edit_command": classify_edit_command,
            "keyboard": _FAKE_KEYBOARD,
            "kb": _RecordingController(),
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
        # Tier 2 (uppercase/capitalize/lowercase the last insertion) is deferred:
        # the dispatcher declines it so it falls through to normal dictation.
        runtime = runtime_namespace(enabled=True, is_macos=True)
        for phrase in (
            "all caps", "uppercase that", "capitalize that", "lowercase that",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(runtime["apply_spoken_edit_command"](
                    phrase, _rec(), BUNDLE))
        self.assertEqual(runtime["kb"].events, [])

    def test_runtime_hook_uses_exact_recognition_pre_compiler(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn(
            "apply_spoken_edit_command(\n"
            "                recognized_raw, rec, bundle)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
