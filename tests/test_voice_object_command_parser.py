import json
import sys
import unittest
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_object_command_parser import (  # noqa: E402
    CommandKind,
    MAX_COMMAND_CHARS,
    ParseReason,
    ParseState,
    parse_command,
)
from voice_objects import (  # noqa: E402
    CalendarDraft,
    Destination,
    EmailDraft,
    FactRole,
    TaskDraft,
    VoiceFact,
)


class VoiceObjectCommandParserTests(unittest.TestCase):
    def test_create_task_preserves_every_character_after_its_delimiter(self):
        result = parse_command(
            "create task:  Ship: the release  ", object_id="utterance-001")

        self.assertEqual(result.receipt.state, ParseState.PARSED)
        self.assertEqual(result.receipt.command_kind, CommandKind.CREATE_TASK)
        self.assertEqual(result.receipt.destination, Destination.TASK)
        self.assertEqual(result.voice_object.facts, (
            VoiceFact(FactRole.SUMMARY, "  Ship: the release  "),
        ))
        self.assertEqual(
            result.projection.draft,
            TaskDraft(title="  Ship: the release  ", notes=None, due_at=None),
        )

    def test_draft_email_preserves_body_after_delimiter(self):
        body = "  Status: green\nNo action needed.  "
        result = parse_command(
            f"draft email to Ada Lovelace:{body}", object_id="utterance-002")

        self.assertEqual(result.receipt.command_kind, CommandKind.DRAFT_EMAIL)
        self.assertEqual(result.voice_object.facts, (
            VoiceFact(FactRole.CONTACT, "Ada Lovelace"),
            VoiceFact(FactRole.DETAILS, body),
        ))
        self.assertEqual(
            result.projection.draft,
            EmailDraft(
                recipients=("Ada Lovelace",),
                subject=None,
                body=body,
            ),
        )

    def test_create_calendar_event_projects_an_iso_start_and_exact_title(self):
        title = "  Review: API contracts  "
        result = parse_command(
            "create calendar event 2026-07-22T10:30:00-07:00:" + title,
            object_id="utterance-003",
        )

        self.assertEqual(
            result.receipt.command_kind,
            CommandKind.CREATE_CALENDAR_EVENT,
        )
        self.assertEqual(result.voice_object.facts, (
            VoiceFact(FactRole.WHEN, "2026-07-22T10:30:00-07:00"),
            VoiceFact(FactRole.SUMMARY, title),
        ))
        self.assertEqual(
            result.projection.draft,
            CalendarDraft(
                title=title,
                notes=None,
                start_at="2026-07-22T10:30:00-07:00",
                end_at=None,
                attendees=(),
            ),
        )

    def test_rejects_missing_ambiguous_or_nonexact_commands_without_a_draft(self):
        cases = (
            "create task title",
            "create task:",
            "draft email to :Body",
            "draft email to Ada:",
            "create calendar event 2026-07-22T10:30:",
            "Create task: case changes are not inferred",
        )

        for command in cases:
            with self.subTest(command=command):
                result = parse_command(command, object_id="utterance-004")
                self.assertIsNone(result.voice_object)
                self.assertIsNone(result.projection)
                self.assertEqual(result.receipt.state, ParseState.REJECTED)

    def test_rejects_invalid_calendar_times_and_object_identifiers(self):
        invalid_time = parse_command(
            "create calendar event 2026-19-22T10:30:00:Review",
            object_id="utterance-005",
        )
        invalid_id = parse_command(
            "create task: Review", object_id="bad object id")

        self.assertEqual(invalid_time.receipt.reason, ParseReason.INVALID_TIME)
        self.assertEqual(invalid_id.receipt.reason, ParseReason.INVALID_OBJECT_ID)
        self.assertIsNone(invalid_time.projection)
        self.assertIsNone(invalid_id.projection)

    def test_receipt_is_content_free_while_projection_holds_private_content(self):
        secrets = ("Ada Lovelace", "Project Bluebird: budget is 8492")
        result = parse_command(
            f"draft email to {secrets[0]}: {secrets[1]}",
            object_id="utterance-006",
        )
        serialized = json.dumps(asdict(result.receipt), default=str)

        for secret in secrets:
            self.assertNotIn(secret, serialized)
        self.assertEqual(result.receipt.output_fact_count, 2)

    def test_non_string_commands_fail_at_the_api_boundary(self):
        with self.assertRaises(TypeError):
            parse_command(None, object_id="utterance-007")  # type: ignore[arg-type]

    def test_rejects_input_beyond_its_fixed_command_bound(self):
        result = parse_command(
            "x" * (MAX_COMMAND_CHARS + 1), object_id="utterance-008")

        self.assertEqual(result.receipt.state, ParseState.REJECTED)
        self.assertEqual(result.receipt.reason, ParseReason.INVALID_CONTENT)
        self.assertIsNone(result.voice_object)
        self.assertIsNone(result.projection)


if __name__ == "__main__":
    unittest.main()
