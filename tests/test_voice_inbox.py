import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_inbox import (  # noqa: E402
    InboxConflictError,
    InboxFormatError,
    InboxNotFoundError,
    InboxState,
    InboxTransitionError,
    MAX_ITEMS,
    MAX_PAYLOAD_CHARS,
    VoiceInbox,
)


class VoiceInboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "private" / "inbox.json"

    def test_payload_round_trips_exactly_and_queue_order_is_stable(self):
        inbox = VoiceInbox(self.path)
        first_payload = "Draft launch note — café\nKeep this exact."
        second_payload = "Second deferred request"

        inbox.enqueue(
            "request:001", first_payload, source_id="utterance:001")
        inbox.enqueue(
            "request:002", second_payload, source_id="utterance:002")
        restored = VoiceInbox(self.path)

        self.assertEqual(restored.get("request:001").payload, first_payload)
        self.assertEqual(
            restored.get("request:001").source_id, "utterance:001")
        self.assertEqual(
            [item.item_id for item in restored.items()],
            ["request:001", "request:002"],
        )
        self.assertEqual(
            [item.item_id for item in restored.items(state=InboxState.QUEUED)],
            ["request:001", "request:002"],
        )
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_receipts_are_fixed_shape_and_content_free(self):
        secret = "Please send the unreleased launch plan to Casey"
        receipt = VoiceInbox(self.path).enqueue(
            "request-123", secret, source_id="utterance-456")
        encoded = json.dumps(receipt.to_dict(), sort_keys=True)

        self.assertEqual(
            set(receipt.to_dict()),
            {"schema_version", "kind", "sequence", "state"},
        )
        self.assertEqual(receipt.state, InboxState.QUEUED)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("utterance-456", encoded)
        self.assertNotIn("request-123", encoded)
        for forbidden in ("payload", "text", "preview", "length", "digest"):
            self.assertNotIn(forbidden, encoded.casefold())

    def test_enqueue_is_idempotent_and_id_conflicts_fail_closed(self):
        inbox = VoiceInbox(self.path)
        first = inbox.enqueue(
            "same-id", "same payload", source_id="source-1")
        saved = self.path.read_bytes()
        duplicate = inbox.enqueue(
            "same-id", "same payload", source_id="source-1")

        self.assertEqual(duplicate, first)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(len(inbox.items()), 1)
        with self.assertRaisesRegex(InboxConflictError, "different"):
            inbox.enqueue(
                "same-id", "changed payload", source_id="source-1")
        with self.assertRaisesRegex(InboxConflictError, "provenance"):
            inbox.enqueue(
                "same-id", "same payload", source_id="source-2")
        self.assertEqual(inbox.get("same-id").payload, "same payload")

    def test_ack_is_idempotent_and_durable(self):
        inbox = VoiceInbox(self.path)
        inbox.enqueue("ack-me", "private task", source_id="utterance-1")

        first = inbox.ack("ack-me")
        saved = self.path.read_bytes()
        duplicate = inbox.ack("ack-me")
        restored = VoiceInbox(self.path)

        self.assertEqual(first, duplicate)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(first.state, InboxState.ACKNOWLEDGED)
        self.assertEqual(restored.get("ack-me").state, InboxState.ACKNOWLEDGED)
        self.assertEqual(restored.get("ack-me").payload, "private task")

    def test_cancel_is_idempotent_and_terminal_states_cannot_cross(self):
        inbox = VoiceInbox(self.path)
        inbox.enqueue(
            "cancel-me", "private task", source_id="utterance-1")
        first = inbox.cancel("cancel-me")
        duplicate = inbox.cancel("cancel-me")

        self.assertEqual(first, duplicate)
        self.assertEqual(first.state, InboxState.CANCELLED)
        with self.assertRaisesRegex(InboxTransitionError, "cannot be changed"):
            inbox.ack("cancel-me")

        inbox.enqueue(
            "ack-me", "another private task", source_id="utterance-2")
        inbox.ack("ack-me")
        with self.assertRaisesRegex(InboxTransitionError, "cannot be changed"):
            inbox.cancel("ack-me")

    def test_missing_items_and_invalid_public_inputs_fail_closed(self):
        inbox = VoiceInbox(self.path)

        with self.assertRaises(InboxNotFoundError):
            inbox.ack("missing")
        with self.assertRaises(InboxNotFoundError):
            inbox.cancel("missing")
        with self.assertRaises(InboxNotFoundError):
            inbox.get("missing")
        for invalid_id in ("", "has spaces", "x" * 129, None, True):
            with self.subTest(item_id=invalid_id):
                with self.assertRaises(ValueError):
                    inbox.enqueue(
                        invalid_id, "payload", source_id="utterance-1")
        for invalid_payload in ("", None, True):
            with self.subTest(payload=invalid_payload):
                with self.assertRaises(ValueError):
                    inbox.enqueue(
                        "valid-id", invalid_payload,
                        source_id="utterance-1")
        with self.assertRaises(TypeError):
            inbox.items(state="queued")
        with self.assertRaisesRegex(ValueError, "up to"):
            inbox.enqueue(
                "too-large", "x" * (MAX_PAYLOAD_CHARS + 1),
                source_id="utterance-1")

    def test_item_limit_and_explicit_terminal_purge_bound_retention(self):
        inbox = VoiceInbox(self.path)
        for index in range(MAX_ITEMS):
            inbox.enqueue(
                f"request-{index}", "payload", source_id=f"source-{index}")

        with self.assertRaisesRegex(OverflowError, "purge terminal"):
            inbox.enqueue("overflow", "payload", source_id="source-overflow")

        inbox.ack("request-0")
        inbox.cancel("request-1")
        self.assertEqual(inbox.purge_terminal(), 2)
        self.assertEqual(inbox.purge_terminal(), 0)
        self.assertEqual(len(VoiceInbox(self.path).items()), MAX_ITEMS - 2)

    def test_schema_rejects_unknown_fields_versions_and_states(self):
        valid = {
            "schema_version": 1,
            "kind": "whisper-face/voice-inbox",
            "next_sequence": 2,
            "items": [{
                "item_id": "one",
                "source_id": "utterance-1",
                "payload": "private",
                "state": "queued",
                "sequence": 1,
            }],
        }

        malformed = [
            {**valid, "unexpected": "private"},
            {**valid, "schema_version": 999},
            {**valid, "schema_version": True},
            {**valid, "kind": "different"},
            {**valid, "next_sequence": 1},
            {**valid, "items": [{**valid["items"][0], "state": "running"}]},
            {**valid, "items": valid["items"] * 2, "next_sequence": 3},
        ]
        for index, body in enumerate(malformed):
            with self.subTest(index=index):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(body), encoding="utf-8")
                with self.assertRaises(InboxFormatError):
                    VoiceInbox(self.path)

    def test_atomic_replace_failure_preserves_disk_and_memory_state(self):
        inbox = VoiceInbox(self.path)
        inbox.enqueue("safe", "preserve me", source_id="utterance-1")
        before = self.path.read_bytes()

        with patch("voice_inbox.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                inbox.enqueue(
                    "not-stored", "do not retain",
                    source_id="utterance-2")

        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([item.item_id for item in inbox.items()], ["safe"])
        self.assertEqual(
            [item.item_id for item in VoiceInbox(self.path).items()], ["safe"])
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
