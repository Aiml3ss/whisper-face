import json
import ast
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_inbox import (  # noqa: E402
    InboxConflictError,
    InboxState,
    MAX_PAYLOAD_CHARS,
    VoiceInbox,
)
from voice_object_inbox_bridge import (  # noqa: E402
    DraftPayloadFormatError,
    ProjectionNotQueueableError,
    VoiceObjectInboxBridge,
)
from voice_objects import (  # noqa: E402
    Destination,
    FactRole,
    PlainTextDraft,
    ProjectionReason,
    ProjectionState,
    TaskDraft,
    VoiceFact,
    VoiceObject,
    project,
)
from macos_email_compose import (  # noqa: E402
    ComposeState,
    MacEmailComposeAdapter,
)
from macos_voice_draft_clipboard import (  # noqa: E402
    ClearState,
    CopyState,
    MacVoiceDraftClipboardAdapter,
    _clear_clipboard_if_owned,
)


def projected_task(title: str = "Send release notes"):
    return project(VoiceObject("utterance-1", (
        VoiceFact(FactRole.SUMMARY, title),
        VoiceFact(FactRole.DETAILS, "Include installation changes."),
    )), Destination.TASK)


class VoiceObjectInboxBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.inbox = VoiceInbox(
            Path(self.temporary_directory.name) / "private" / "inbox.json")
        self.bridge = VoiceObjectInboxBridge(self.inbox)

    def test_native_compose_adapter_hands_private_fields_in_process_once(self):
        class Service:
            def __init__(self):
                self.calls = []

            def canPerformWithItems_(self, items):
                self.calls.append(("can", items))
                return True

            def setRecipients_(self, recipients):
                self.calls.append(("recipients", recipients))

            def setSubject_(self, subject):
                self.calls.append(("subject", subject))

            def performWithItems_(self, items):
                self.calls.append(("perform", items))

        service = Service()
        adapter = MacEmailComposeAdapter(
            service_factory=lambda: service,
            main_thread_check=lambda: True)
        nonce = adapter.issue_nonce()
        secret_subject = "Project Bluebird"
        secret_body = "Private launch plan 8492"

        receipt = adapter.compose(
            nonce,
            recipients=("ada@example.com",),
            subject=secret_subject,
            body=secret_body)
        replay = adapter.compose(
            nonce,
            recipients=("different@example.com",),
            subject="Different",
            body="Different")

        self.assertEqual(receipt.state, ComposeState.REQUESTED)
        self.assertIs(replay, receipt)
        self.assertEqual(service.calls, [
            ("can", [secret_body]),
            ("recipients", ["ada@example.com"]),
            ("subject", secret_subject),
            ("perform", [secret_body]),
        ])
        encoded = json.dumps(receipt.to_mapping(), sort_keys=True)
        self.assertNotIn("ada@example.com", encoded)
        self.assertNotIn(secret_subject, encoded)
        self.assertNotIn(secret_body, encoded)

    def test_compose_adapter_rejects_non_main_invalid_and_evicted_nonces(self):
        service_calls = []
        adapter = MacEmailComposeAdapter(
            service_factory=lambda: service_calls.append(True),
            main_thread_check=lambda: False,
            max_pending=1)
        evicted = adapter.issue_nonce()
        current = adapter.issue_nonce()

        arbitrary = adapter.compose(
            "x" * 32, recipients=("a@example.com",),
            subject=None, body="Body")
        old = adapter.compose(
            evicted, recipients=("a@example.com",),
            subject=None, body="Body")
        off_main = adapter.compose(
            current, recipients=("a@example.com",),
            subject=None, body="Body")
        invalid_nonce = adapter.issue_nonce()
        invalid = adapter.compose(
            invalid_nonce, recipients=(), subject=None, body="Body")

        self.assertEqual(arbitrary.state, ComposeState.UNAVAILABLE)
        self.assertEqual(old.state, ComposeState.UNAVAILABLE)
        self.assertEqual(off_main.state, ComposeState.UNAVAILABLE)
        self.assertEqual(invalid.state, ComposeState.INVALID)
        self.assertEqual(service_calls, [])
        self.assertFalse(any((arbitrary.attempted, old.attempted,
                              off_main.attempted, invalid.attempted)))

    def test_native_draft_clipboard_is_one_shot_and_receipt_is_content_free(self):
        writes = []
        adapter = MacVoiceDraftClipboardAdapter(
            writer=lambda content: writes.append(content) or 41,
            main_thread_check=lambda: True)
        nonce = adapter.issue_nonce()
        secret = "Title: Project Bluebird\nNotes: Private launch 8492"

        receipt = adapter.copy(nonce, content=secret)
        replay = adapter.copy(nonce, content="Different private draft")

        self.assertEqual(receipt.state, CopyState.COPIED)
        self.assertIs(replay, receipt)
        self.assertEqual(writes, [secret])
        encoded = json.dumps(receipt.to_mapping(), sort_keys=True)
        self.assertNotIn("Bluebird", encoded)
        self.assertNotIn("8492", encoded)
        self.assertNotIn(secret, repr(adapter.__dict__))

    def test_draft_clipboard_clear_is_owned_one_shot_and_content_free(self):
        writes = []
        clear_calls = []
        adapter = MacVoiceDraftClipboardAdapter(
            writer=lambda content: writes.append(content) or 41,
            clearer=lambda expected: clear_calls.append(expected)
            or ClearState.CLEARED,
            main_thread_check=lambda: True)
        secret = "Title: Project Bluebird\nNotes: Private launch 8492"

        copied = adapter.copy(adapter.issue_nonce(), content=secret)
        clear_nonce = adapter.issue_clear_nonce()
        cleared = adapter.clear(clear_nonce)
        replay = adapter.clear(clear_nonce)

        self.assertEqual(copied.state, CopyState.COPIED)
        self.assertEqual(cleared.state, ClearState.CLEARED)
        self.assertTrue(cleared.attempted)
        self.assertIs(replay, cleared)
        self.assertEqual(clear_calls, [41])
        self.assertEqual(adapter.issue_clear_nonce(), "")
        self.assertNotIn(secret, repr(adapter.__dict__))
        self.assertNotIn("Bluebird", json.dumps(cleared.to_mapping()))

    def test_draft_clipboard_clear_preserves_changed_clipboard_and_closes_failure(self):
        changed_calls = []
        changed = MacVoiceDraftClipboardAdapter(
            writer=lambda _content: 73,
            clearer=lambda expected: changed_calls.append(expected)
            or ClearState.CHANGED,
            main_thread_check=lambda: True)
        changed.copy(changed.issue_nonce(), content="Title: Still queued")
        changed_receipt = changed.clear(changed.issue_clear_nonce())

        failed = MacVoiceDraftClipboardAdapter(
            writer=lambda _content: 74,
            clearer=lambda _expected: (_ for _ in ()).throw(
                RuntimeError("clipboard unavailable")),
            main_thread_check=lambda: True)
        failed.copy(failed.issue_nonce(), content="Title: Still queued")
        failed_nonce = failed.issue_clear_nonce()
        failed_receipt = failed.clear(failed_nonce)

        self.assertEqual(changed_receipt.state, ClearState.CHANGED)
        self.assertFalse(changed_receipt.attempted)
        self.assertEqual(changed_calls, [73])
        self.assertEqual(failed_receipt.state, ClearState.FAILED)
        self.assertTrue(failed_receipt.attempted)
        self.assertIs(failed.clear(failed_nonce), failed_receipt)

        on_main = [True]
        off_main_calls = []
        off_main = MacVoiceDraftClipboardAdapter(
            writer=lambda _content: 75,
            clearer=lambda expected: off_main_calls.append(expected)
            or ClearState.CLEARED,
            main_thread_check=lambda: on_main[0])
        off_main.copy(off_main.issue_nonce(), content="Title: Still queued")
        off_main_nonce = off_main.issue_clear_nonce()
        on_main[0] = False
        off_main_receipt = off_main.clear(off_main_nonce)
        self.assertEqual(off_main_receipt.state, ClearState.UNAVAILABLE)
        self.assertFalse(off_main_receipt.attempted)
        self.assertEqual(off_main_calls, [])

    def test_native_clear_checks_change_count_without_reading_content(self):
        class FakePasteboard:
            def __init__(self):
                self.change_count = 19
                self.clear_calls = 0

            def changeCount(self):
                return self.change_count

            def clearContents(self):
                self.clear_calls += 1
                self.change_count += 1

        pasteboard = FakePasteboard()
        appkit = SimpleNamespace(NSPasteboard=SimpleNamespace(
            generalPasteboard=lambda: pasteboard))
        with patch.dict(sys.modules, {"AppKit": appkit}):
            changed = _clear_clipboard_if_owned(18)
            cleared = _clear_clipboard_if_owned(19)

        self.assertEqual(changed, ClearState.CHANGED)
        self.assertEqual(cleared, ClearState.CLEARED)
        self.assertEqual(pasteboard.clear_calls, 1)

    def test_draft_clipboard_rejects_invalid_non_main_and_writer_failure(self):
        calls = []
        off_main = MacVoiceDraftClipboardAdapter(
            writer=lambda content: calls.append(content) or 1,
            main_thread_check=lambda: False)
        off_main_receipt = off_main.copy(
            off_main.issue_nonce(), content="Title: Valid")
        invalid = MacVoiceDraftClipboardAdapter(
            writer=lambda content: calls.append(content) or 2,
            main_thread_check=lambda: True)
        invalid_receipt = invalid.copy(invalid.issue_nonce(), content="")
        failed = MacVoiceDraftClipboardAdapter(
            writer=lambda content: calls.append(content) or None,
            main_thread_check=lambda: True)
        failed_receipt = failed.copy(
            failed.issue_nonce(), content="Title: Still queued")

        self.assertEqual(off_main_receipt.state, CopyState.UNAVAILABLE)
        self.assertEqual(invalid_receipt.state, CopyState.INVALID)
        self.assertEqual(failed_receipt.state, CopyState.FAILED)
        self.assertEqual(calls, ["Title: Still queued"])
        self.assertTrue(failed_receipt.attempted)

    def test_native_adapter_has_no_send_url_process_or_persistence_surface(self):
        tree = ast.parse((ROOT / "macos_email_compose.py").read_text(
            encoding="utf-8"))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }

        self.assertFalse(names & {
            "subprocess", "Popen", "open", "NSURL", "NSWorkspace"})
        self.assertFalse(any("send" in attribute.casefold()
                             for attribute in attributes))
        self.assertEqual(
            {attribute for attribute in attributes
             if attribute.endswith("WithItems_")},
            set())

        clipboard_tree = ast.parse(
            (ROOT / "macos_voice_draft_clipboard.py").read_text(
                encoding="utf-8"))
        clipboard_names = {
            node.id for node in ast.walk(clipboard_tree)
            if isinstance(node, ast.Name)
        }
        clipboard_attributes = {
            node.attr for node in ast.walk(clipboard_tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertFalse(clipboard_names & {
            "subprocess", "Popen", "open", "NSURL", "NSWorkspace",
            "requests", "socket", "EventKit",
        })
        self.assertFalse(clipboard_attributes & {
            "performWithItems_", "launchApplication_", "openURL_",
            "postKeyboardEvent_keyCode_character_", "setValue_forAttribute_",
            "stringForType_",
        })

    def test_projection_is_canonical_and_decoded_only_by_explicit_read(self):
        projection = projected_task()
        receipt = self.bridge.enqueue(
            "draft:001", projection, source_id="voice-object:001")

        self.assertEqual(receipt.state, InboxState.QUEUED)
        self.assertEqual(
            set(receipt.to_dict()),
            {"schema_version", "kind", "sequence", "state"},
        )
        self.assertNotIn("Send release notes", json.dumps(receipt.to_dict()))
        stored = self.inbox.get("draft:001")
        self.assertEqual(stored.source_id, "voice-object:001")
        self.assertEqual(stored.payload, (
            '{"destination":"task","draft":{"due_at":null,'
            '"notes":"Include installation changes.",'
            '"title":"Send release notes"},"draft_type":"task_draft",'
            '"kind":"whisper-face/voice-object-draft","schema_version":1}'
        ))

        revealed = self.bridge.read("draft:001")
        self.assertEqual(revealed.item_id, "draft:001")
        self.assertEqual(revealed.source_id, "voice-object:001")
        self.assertEqual(revealed.destination, Destination.TASK)
        self.assertEqual(revealed.draft, projection.draft)

    def test_enqueue_is_idempotent_and_conflicting_ids_fail_closed(self):
        first = self.bridge.enqueue(
            "draft:same", projected_task(), source_id="voice-object:same")
        duplicate = self.bridge.enqueue(
            "draft:same", projected_task(), source_id="voice-object:same")

        self.assertEqual(duplicate, first)
        self.assertEqual(len(self.inbox.items()), 1)
        with self.assertRaises(InboxConflictError):
            self.bridge.enqueue(
                "draft:same", projected_task("Different title"),
                source_id="voice-object:same")
        with self.assertRaises(InboxConflictError):
            self.bridge.enqueue(
                "draft:same", projected_task(),
                source_id="voice-object:different")

    def test_each_closed_draft_type_round_trips(self):
        cases = (
            (Destination.PLAIN_TEXT, (
                VoiceFact(FactRole.DETAILS, "Exact dictated text"),
            )),
            (Destination.EMAIL_DRAFT, (
                VoiceFact(FactRole.SUMMARY, "Release"),
                VoiceFact(FactRole.DETAILS, "The release is ready."),
                VoiceFact(FactRole.CONTACT, "team@example.com"),
            )),
            (Destination.TASK, (
                VoiceFact(FactRole.SUMMARY, "Publish release"),
                VoiceFact(FactRole.WHEN, "2026-07-22T09:00:00-07:00"),
            )),
            (Destination.CALENDAR_DRAFT, (
                VoiceFact(FactRole.SUMMARY, "Release review"),
                VoiceFact(FactRole.WHEN, "2026-07-22T09:00:00-07:00"),
                VoiceFact(FactRole.END, "2026-07-22T09:30:00-07:00"),
                VoiceFact(FactRole.CONTACT, "team@example.com"),
            )),
        )

        for index, (destination, facts) in enumerate(cases):
            with self.subTest(destination=destination):
                projection = project(
                    VoiceObject(f"utterance-{index}", facts), destination)
                item_id = f"draft:{index}"
                self.bridge.enqueue(
                    item_id, projection, source_id=f"voice-object:{index}")
                revealed = self.bridge.read(item_id)
                self.assertEqual(revealed.destination, destination)
                self.assertEqual(revealed.draft, projection.draft)

    def test_rejected_or_mismatched_projections_are_not_queued(self):
        rejected = project(VoiceObject("utterance-1", (
            VoiceFact(FactRole.DETAILS, "Notes without a title"),
        )), Destination.TASK)
        mismatch = replace(
            projected_task(), draft=PlainTextDraft("wrong draft type"))
        invalid_fields = replace(
            projected_task(), draft=TaskDraft(123, None, None))

        for projection in (rejected, mismatch, invalid_fields):
            with self.subTest(projection=projection):
                with self.assertRaises(ProjectionNotQueueableError):
                    self.bridge.enqueue(
                        "draft:bad", projection, source_id="voice-object:bad")
        self.assertEqual(self.inbox.items(), ())

    def test_oversized_encoded_content_is_rejected_before_storage(self):
        oversized = projected_task("x" * MAX_PAYLOAD_CHARS)

        with self.assertRaisesRegex(
                ProjectionNotQueueableError, "exceeds"):
            self.bridge.enqueue(
                "draft:large", oversized, source_id="voice-object:large")
        self.assertEqual(self.inbox.items(), ())

    def test_explicit_read_rejects_unknown_fields_types_and_noncanonical_json(self):
        valid = {
            "schema_version": 1,
            "kind": "whisper-face/voice-object-draft",
            "destination": "task",
            "draft_type": "task_draft",
            "draft": {
                "title": "Private title",
                "notes": None,
                "due_at": None,
            },
        }
        malformed = (
            {**valid, "unknown": "field"},
            {**valid, "draft_type": "unknown_draft"},
            {**valid, "draft": {**valid["draft"], "unknown": "field"}},
            {**valid, "draft": {**valid["draft"], "title": 123}},
            {**valid, "draft": {**valid["draft"], "title": "\x00"}},
        )
        for index, body in enumerate(malformed):
            with self.subTest(index=index):
                item_id = f"raw:{index}"
                self.inbox.enqueue(
                    item_id,
                    json.dumps(body, sort_keys=True, separators=(",", ":")),
                    source_id="untrusted:source",
                )
                with self.assertRaises(DraftPayloadFormatError):
                    self.bridge.read(item_id)

        self.inbox.enqueue(
            "raw:whitespace", json.dumps(valid), source_id="untrusted:source")
        with self.assertRaisesRegex(DraftPayloadFormatError, "canonical"):
            self.bridge.read("raw:whitespace")

    def test_manually_forged_success_state_is_rejected(self):
        projection = projected_task()
        forged_receipt = replace(
            projection.receipt,
            state=ProjectionState.REJECTED,
            reason=ProjectionReason.CONTRADICTORY_FACTS,
        )

        with self.assertRaises(ProjectionNotQueueableError):
            self.bridge.enqueue(
                "draft:forged", replace(projection, receipt=forged_receipt),
                source_id="voice-object:forged",
            )


if __name__ == "__main__":
    unittest.main()
