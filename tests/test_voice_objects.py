import json
import sys
import unittest
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_objects import (
    CalendarDraft,
    Destination,
    EmailDraft,
    FactRole,
    PlainTextDraft,
    ProjectionReason,
    ProjectionState,
    TaskDraft,
    VoiceFact,
    VoiceObject,
    project,
)


def voice_object(*facts: VoiceFact) -> VoiceObject:
    return VoiceObject("utterance-1", facts)


class VoiceObjectValidationTests(unittest.TestCase):
    def test_object_and_facts_require_closed_valid_types(self):
        with self.assertRaises(ValueError):
            VoiceObject("bad id", (VoiceFact(FactRole.SUMMARY, "Ship"),))
        with self.assertRaises(ValueError):
            VoiceObject("valid", ())
        with self.assertRaises(ValueError):
            VoiceFact(FactRole.DETAILS, "  ")
        with self.assertRaises(ValueError):
            VoiceFact(FactRole.WHEN, "tomorrow morning")
        with self.assertRaises(ValueError):
            VoiceFact("summary", "Ship")  # type: ignore[arg-type]

    def test_project_requires_closed_destination_type(self):
        value = voice_object(VoiceFact(FactRole.DETAILS, "Hello"))
        with self.assertRaises(TypeError):
            project(value, "plain_text")  # type: ignore[arg-type]


class ProjectionTests(unittest.TestCase):
    def test_plain_text_prefers_details_and_falls_back_to_summary(self):
        detailed = project(voice_object(
            VoiceFact(FactRole.SUMMARY, "Short version"),
            VoiceFact(FactRole.DETAILS, "Full version"),
        ), Destination.PLAIN_TEXT)
        summary_only = project(voice_object(
            VoiceFact(FactRole.SUMMARY, "Short version"),
        ), Destination.PLAIN_TEXT)

        self.assertEqual(detailed.draft, PlainTextDraft("Full version"))
        self.assertEqual(summary_only.draft, PlainTextDraft("Short version"))

    def test_email_draft_deduplicates_contacts_deterministically(self):
        facts = (
            VoiceFact(FactRole.CONTACT, "zoe@example.com"),
            VoiceFact(FactRole.DETAILS, "The launch is approved."),
            VoiceFact(FactRole.CONTACT, "Alex@example.com"),
            VoiceFact(FactRole.SUMMARY, "Launch approval"),
            VoiceFact(FactRole.CONTACT, "alex@example.com"),
        )
        forward = project(voice_object(*facts), Destination.EMAIL_DRAFT)
        reverse = project(voice_object(*reversed(facts)), Destination.EMAIL_DRAFT)

        expected = EmailDraft(
            recipients=("Alex@example.com", "zoe@example.com"),
            subject="Launch approval",
            body="The launch is approved.",
        )
        self.assertEqual(forward.draft, expected)
        self.assertEqual(reverse.draft, expected)

    def test_task_projection_preserves_only_supplied_facts(self):
        result = project(voice_object(
            VoiceFact(FactRole.SUMMARY, "Send release notes"),
            VoiceFact(FactRole.DETAILS, "Include installation changes."),
            VoiceFact(FactRole.WHEN, "2026-07-22T09:30:00-07:00"),
        ), Destination.TASK)

        self.assertEqual(result.draft, TaskDraft(
            title="Send release notes",
            notes="Include installation changes.",
            due_at="2026-07-22T09:30:00-07:00",
        ))
        self.assertEqual(result.receipt.state, ProjectionState.PROJECTED)
        self.assertEqual(result.receipt.reason, ProjectionReason.READY)

    def test_calendar_draft_preserves_time_range_and_attendees(self):
        result = project(voice_object(
            VoiceFact(FactRole.SUMMARY, "Design review"),
            VoiceFact(FactRole.WHEN, "2026-07-22T10:00:00-07:00"),
            VoiceFact(FactRole.END, "2026-07-22T10:30:00-07:00"),
            VoiceFact(FactRole.CONTACT, "team@example.com"),
        ), Destination.CALENDAR_DRAFT)

        self.assertEqual(result.draft, CalendarDraft(
            title="Design review",
            notes=None,
            start_at="2026-07-22T10:00:00-07:00",
            end_at="2026-07-22T10:30:00-07:00",
            attendees=("team@example.com",),
        ))

    def test_calendar_rejects_backward_or_mixed_timezone_ranges(self):
        for end in (
            "2026-07-22T09:59:00-07:00",
            "2026-07-22T10:30:00",
        ):
            with self.subTest(end=end):
                result = project(voice_object(
                    VoiceFact(FactRole.SUMMARY, "Design review"),
                    VoiceFact(FactRole.WHEN, "2026-07-22T10:00:00-07:00"),
                    VoiceFact(FactRole.END, end),
                ), Destination.CALENDAR_DRAFT)
                self.assertIsNone(result.draft)
                self.assertEqual(
                    result.receipt.reason,
                    ProjectionReason.INVALID_TIME_RANGE,
                )

    def test_conflicting_single_value_facts_fail_closed(self):
        result = project(voice_object(
            VoiceFact(FactRole.SUMMARY, "Email the report"),
            VoiceFact(FactRole.SUMMARY, "Delete the report"),
            VoiceFact(FactRole.DETAILS, "Attached."),
        ), Destination.EMAIL_DRAFT)

        self.assertIsNone(result.draft)
        self.assertEqual(result.receipt.state, ProjectionState.REJECTED)
        self.assertEqual(
            result.receipt.reason,
            ProjectionReason.CONTRADICTORY_FACTS,
        )
        self.assertEqual(result.receipt.conflict_count, 1)

    def test_equivalent_single_value_facts_collapse_without_input_order_bias(self):
        facts = (
            VoiceFact(FactRole.SUMMARY, "Ship   today"),
            VoiceFact(FactRole.SUMMARY, "Ship today"),
        )
        forward = project(voice_object(*facts), Destination.TASK)
        reverse = project(voice_object(*reversed(facts)), Destination.TASK)

        self.assertEqual(forward, reverse)
        self.assertEqual(forward.draft, TaskDraft("Ship   today", None, None))

    def test_case_distinct_single_value_facts_are_not_silently_collapsed(self):
        result = project(voice_object(
            VoiceFact(FactRole.SUMMARY, "ProjectID"),
            VoiceFact(FactRole.SUMMARY, "projectid"),
        ), Destination.TASK)

        self.assertIsNone(result.draft)
        self.assertEqual(
            result.receipt.reason,
            ProjectionReason.CONTRADICTORY_FACTS,
        )

    def test_destination_required_fields_fail_closed_without_invention(self):
        cases = (
            (voice_object(VoiceFact(FactRole.CONTACT, "a@example.com")),
             Destination.PLAIN_TEXT),
            (voice_object(VoiceFact(FactRole.SUMMARY, "Subject only")),
             Destination.EMAIL_DRAFT),
            (voice_object(VoiceFact(FactRole.DETAILS, "Notes only")),
             Destination.TASK),
            (voice_object(VoiceFact(FactRole.SUMMARY, "No start")),
             Destination.CALENDAR_DRAFT),
        )
        for value, destination in cases:
            with self.subTest(destination=destination):
                result = project(value, destination)
                self.assertIsNone(result.draft)
                self.assertEqual(
                    result.receipt.reason,
                    ProjectionReason.MISSING_REQUIRED_FACT,
                )

    def test_receipts_are_content_free(self):
        secret_values = (
            "Project Bluebird",
            "The launch code is 8492.",
            "private@example.com",
        )
        result = project(voice_object(
            VoiceFact(FactRole.SUMMARY, secret_values[0]),
            VoiceFact(FactRole.DETAILS, secret_values[1]),
            VoiceFact(FactRole.CONTACT, secret_values[2]),
        ), Destination.EMAIL_DRAFT)
        serialized = json.dumps(asdict(result.receipt), default=str)

        for value in secret_values:
            self.assertNotIn(value, serialized)
        self.assertEqual(result.receipt.input_fact_count, 3)
        self.assertEqual(result.receipt.output_field_count, 3)


if __name__ == "__main__":
    unittest.main()
