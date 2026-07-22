"""Pure Voice Object projections for inert destination drafts.

Voice Objects preserve a small, closed set of semantic facts independently of
where the user may eventually use them.  ``project`` converts those facts into
one of four typed drafts.  It never sends, schedules, saves, copies, types, or
otherwise executes a draft.

The projection receipt contains only schema metadata and counts.  User content
stays in the returned draft and is absent from the receipt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


MAX_FACT_CHARS = 100_000
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class FactRole(str, Enum):
    """Closed semantic vocabulary accepted by a Voice Object."""

    SUMMARY = "summary"
    DETAILS = "details"
    CONTACT = "contact"
    WHEN = "when"
    END = "end"


class Destination(str, Enum):
    PLAIN_TEXT = "plain_text"
    EMAIL_DRAFT = "email_draft"
    TASK = "task"
    CALENDAR_DRAFT = "calendar_draft"


class ProjectionState(str, Enum):
    PROJECTED = "projected"
    REJECTED = "rejected"


class ProjectionReason(str, Enum):
    READY = "ready"
    CONTRADICTORY_FACTS = "contradictory_facts"
    MISSING_REQUIRED_FACT = "missing_required_fact"
    INVALID_TIME_RANGE = "invalid_time_range"


@dataclass(frozen=True)
class VoiceFact:
    role: FactRole
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, FactRole):
            raise ValueError("fact role must be a FactRole")
        if (not isinstance(self.value, str)
                or not self.value.strip()
                or "\x00" in self.value
                or len(self.value) > MAX_FACT_CHARS):
            raise ValueError("fact value must be non-empty bounded text")
        if self.role in {FactRole.WHEN, FactRole.END}:
            try:
                datetime.fromisoformat(self.value)
            except ValueError as error:
                raise ValueError("time facts must use ISO 8601") from error


@dataclass(frozen=True)
class VoiceObject:
    object_id: str
    facts: tuple[VoiceFact, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.object_id, str)
                or not _IDENTIFIER_RE.fullmatch(self.object_id)):
            raise ValueError("invalid Voice Object identifier")
        facts = tuple(self.facts)
        if not facts or not all(isinstance(fact, VoiceFact) for fact in facts):
            raise ValueError("Voice Object requires typed facts")
        object.__setattr__(self, "facts", facts)


@dataclass(frozen=True)
class PlainTextDraft:
    text: str


@dataclass(frozen=True)
class EmailDraft:
    recipients: tuple[str, ...]
    subject: str | None
    body: str


@dataclass(frozen=True)
class TaskDraft:
    title: str
    notes: str | None
    due_at: str | None


@dataclass(frozen=True)
class CalendarDraft:
    title: str
    notes: str | None
    start_at: str
    end_at: str | None
    attendees: tuple[str, ...]


Draft = PlainTextDraft | EmailDraft | TaskDraft | CalendarDraft


@dataclass(frozen=True)
class ProjectionReceipt:
    """Content-free evidence for one pure projection decision."""

    destination: Destination
    state: ProjectionState
    reason: ProjectionReason
    input_fact_count: int
    output_field_count: int
    conflict_count: int


@dataclass(frozen=True)
class ProjectionResult:
    draft: Draft | None
    receipt: ProjectionReceipt


_SINGLE_VALUE_ROLES = (
    FactRole.SUMMARY,
    FactRole.DETAILS,
    FactRole.WHEN,
    FactRole.END,
)


def _normalized_fact(value: str) -> str:
    """Collapse layout-only differences without changing semantic case."""
    return " ".join(value.split())


def _normalized_contact(value: str) -> str:
    return _normalized_fact(value).casefold()


def _group_facts(
    voice_object: VoiceObject,
) -> tuple[dict[FactRole, str], tuple[str, ...], int]:
    grouped: dict[FactRole, list[str]] = {role: [] for role in FactRole}
    for fact in voice_object.facts:
        grouped[fact.role].append(fact.value)

    single_values: dict[FactRole, str] = {}
    conflicts = 0
    for role in _SINGLE_VALUE_ROLES:
        by_normalized: dict[str, list[str]] = {}
        for value in grouped[role]:
            by_normalized.setdefault(_normalized_fact(value), []).append(value)
        if len(by_normalized) > 1:
            conflicts += 1
        elif by_normalized:
            # Equivalent values collapse without depending on input order.
            single_values[role] = min(next(iter(by_normalized.values())))

    contacts_by_normalized: dict[str, list[str]] = {}
    for value in grouped[FactRole.CONTACT]:
        contacts_by_normalized.setdefault(_normalized_contact(value), []).append(value)
    contacts = tuple(sorted(
        (min(values) for values in contacts_by_normalized.values()),
        key=lambda value: (_normalized_contact(value), value),
    ))
    return single_values, contacts, conflicts


def _receipt(
    destination: Destination,
    voice_object: VoiceObject,
    state: ProjectionState,
    reason: ProjectionReason,
    *,
    output_field_count: int = 0,
    conflict_count: int = 0,
) -> ProjectionReceipt:
    return ProjectionReceipt(
        destination=destination,
        state=state,
        reason=reason,
        input_fact_count=len(voice_object.facts),
        output_field_count=output_field_count,
        conflict_count=conflict_count,
    )


def _time_range_is_valid(start: str, end: str | None) -> bool:
    if end is None:
        return True
    start_value = datetime.fromisoformat(start)
    end_value = datetime.fromisoformat(end)
    if (start_value.tzinfo is None) != (end_value.tzinfo is None):
        return False
    return end_value > start_value


def project(voice_object: VoiceObject, destination: Destination) -> ProjectionResult:
    """Deterministically project facts into an inert, typed destination draft."""
    if not isinstance(voice_object, VoiceObject):
        raise TypeError("voice_object must be a VoiceObject")
    if not isinstance(destination, Destination):
        raise TypeError("destination must be a Destination")

    values, contacts, conflicts = _group_facts(voice_object)
    if conflicts:
        return ProjectionResult(
            draft=None,
            receipt=_receipt(
                destination,
                voice_object,
                ProjectionState.REJECTED,
                ProjectionReason.CONTRADICTORY_FACTS,
                conflict_count=conflicts,
            ),
        )

    summary = values.get(FactRole.SUMMARY)
    details = values.get(FactRole.DETAILS)
    when = values.get(FactRole.WHEN)
    end = values.get(FactRole.END)

    draft: Draft
    if destination == Destination.PLAIN_TEXT:
        text = details or summary
        if text is None:
            return ProjectionResult(
                None,
                _receipt(
                    destination,
                    voice_object,
                    ProjectionState.REJECTED,
                    ProjectionReason.MISSING_REQUIRED_FACT,
                ),
            )
        draft = PlainTextDraft(text=text)
    elif destination == Destination.EMAIL_DRAFT:
        if details is None:
            return ProjectionResult(
                None,
                _receipt(
                    destination,
                    voice_object,
                    ProjectionState.REJECTED,
                    ProjectionReason.MISSING_REQUIRED_FACT,
                ),
            )
        draft = EmailDraft(recipients=contacts, subject=summary, body=details)
    elif destination == Destination.TASK:
        if summary is None:
            return ProjectionResult(
                None,
                _receipt(
                    destination,
                    voice_object,
                    ProjectionState.REJECTED,
                    ProjectionReason.MISSING_REQUIRED_FACT,
                ),
            )
        draft = TaskDraft(title=summary, notes=details, due_at=when)
    else:
        if summary is None or when is None:
            return ProjectionResult(
                None,
                _receipt(
                    destination,
                    voice_object,
                    ProjectionState.REJECTED,
                    ProjectionReason.MISSING_REQUIRED_FACT,
                ),
            )
        if not _time_range_is_valid(when, end):
            return ProjectionResult(
                None,
                _receipt(
                    destination,
                    voice_object,
                    ProjectionState.REJECTED,
                    ProjectionReason.INVALID_TIME_RANGE,
                ),
            )
        draft = CalendarDraft(
            title=summary,
            notes=details,
            start_at=when,
            end_at=end,
            attendees=contacts,
        )

    return ProjectionResult(
        draft=draft,
        receipt=_receipt(
            destination,
            voice_object,
            ProjectionState.PROJECTED,
            ProjectionReason.READY,
            output_field_count=len(draft.__dataclass_fields__),
        ),
    )
