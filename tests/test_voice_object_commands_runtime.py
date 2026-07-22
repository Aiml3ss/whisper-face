# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Runtime seam tests for opt-in, inert Voice Object command diversion."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_dictate import load_definitions  # noqa: E402
from voice_inbox import InboxState, VoiceInbox  # noqa: E402
from voice_object_command_parser import parse_command  # noqa: E402
from voice_object_inbox_bridge import VoiceObjectInboxBridge  # noqa: E402
from voice_objects import TaskDraft  # noqa: E402


def runtime_namespace(path: Path, *, enabled: bool, is_macos: bool):
    namespace = load_definitions(
        "_voice_object_inbox_bridge",
        "_existing_voice_object_inbox_queued_count",
        "set_voice_object_commands_enabled",
        "voice_object_inbox_status",
        "queue_voice_object_command",
        extra={
            "VOICE_OBJECT_INBOX_STATE": {
                "lock": threading.RLock(), "inbox": None, "bridge": None,
            },
            "VOICE_INBOX_FILE": path,
            "VoiceInbox": VoiceInbox,
            "VoiceObjectInboxBridge": VoiceObjectInboxBridge,
            "InboxState": InboxState,
            "parse_command": parse_command,
            "PREFERENCES": {"voice_object_commands": enabled},
            "IS_MACOS": is_macos,
            "save_preferences": lambda: None,
        },
    )
    return namespace


class VoiceObjectCommandRuntimeTests(unittest.TestCase):
    def test_disabled_or_unsupported_speech_preserves_the_normal_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice_inbox.json"
            disabled = runtime_namespace(path, enabled=False, is_macos=True)

            self.assertFalse(disabled["queue_voice_object_command"](
                "create task: Private launch", "utterance-1"))
            self.assertEqual(disabled["voice_object_inbox_status"](), {
                "enabled": False, "queued_count": 0, "status": "Off",
            })
            self.assertFalse(path.exists())

            enabled = runtime_namespace(path, enabled=True, is_macos=True)
            self.assertFalse(enabled["queue_voice_object_command"](
                "create a task: Private launch", "utterance-2"))
            self.assertFalse(enabled["queue_voice_object_command"](
                None, "utterance-2"))  # type: ignore[arg-type]
            self.assertFalse(path.exists())

    def test_runtime_intercept_uses_exact_recognition_not_compiler_output(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn(
            "queue_voice_object_command(\n"
            "                recognized_raw, rec.utterance_id)",
            source,
        )

    def test_mac_opt_in_queues_only_exact_commands_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice_inbox.json"
            runtime = runtime_namespace(path, enabled=True, is_macos=True)
            command = "create task: Private launch: phase one"

            self.assertTrue(runtime["queue_voice_object_command"](
                command, "utterance-1"))
            self.assertTrue(runtime["queue_voice_object_command"](
                command, "utterance-1"))
            restarted = runtime_namespace(
                path, enabled=True, is_macos=True)
            self.assertTrue(restarted["queue_voice_object_command"](
                command, "utterance-1"))
            self.assertEqual(runtime["voice_object_inbox_status"](), {
                "enabled": True, "queued_count": 1, "status": "Ready",
            })

            revealed = VoiceObjectInboxBridge(VoiceInbox(path)).read(
                "voice-object:utterance-1")
            self.assertEqual(
                revealed.draft, TaskDraft(" Private launch: phase one", None, None))
            self.assertEqual(revealed.state, InboxState.QUEUED)
            self.assertEqual(len(VoiceInbox(path).items()), 1)

    def test_windows_cannot_activate_command_diversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice_inbox.json"
            runtime = runtime_namespace(path, enabled=True, is_macos=False)

            self.assertFalse(runtime["queue_voice_object_command"](
                "create task: Private launch", "utterance-3"))
            self.assertEqual(runtime["voice_object_inbox_status"](), {
                "enabled": False, "queued_count": 0, "status": "Unavailable",
            })
            self.assertFalse(path.exists())

    def test_disabling_stops_diversion_without_hiding_existing_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice_inbox.json"
            runtime = runtime_namespace(path, enabled=True, is_macos=True)
            self.assertTrue(runtime["queue_voice_object_command"](
                "create task: Existing local draft", "utterance-5"))

            self.assertIsNotNone(
                runtime["VOICE_OBJECT_INBOX_STATE"]["bridge"])
            runtime["set_voice_object_commands_enabled"](False)
            self.assertIsNone(runtime["VOICE_OBJECT_INBOX_STATE"]["inbox"])
            self.assertIsNone(runtime["VOICE_OBJECT_INBOX_STATE"]["bridge"])
            self.assertFalse(runtime["queue_voice_object_command"](
                "create task: New local draft", "utterance-6"))
            self.assertEqual(runtime["voice_object_inbox_status"](), {
                "enabled": False, "queued_count": 1, "status": "Off",
            })
            self.assertIsNone(runtime["VOICE_OBJECT_INBOX_STATE"]["inbox"])
            self.assertIsNone(runtime["VOICE_OBJECT_INBOX_STATE"]["bridge"])
            self.assertEqual(len(VoiceInbox(path).items()), 1)

    def test_status_is_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice_inbox.json"
            runtime = runtime_namespace(path, enabled=True, is_macos=True)
            secret = "Project Bluebird budget 8492"
            self.assertTrue(runtime["queue_voice_object_command"](
                f"create task: {secret}", "utterance-4"))

            self.assertNotIn(
                secret, json.dumps(runtime["voice_object_inbox_status"]()))


if __name__ == "__main__":
    unittest.main()
