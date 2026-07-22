"""Closed bridge from inert Voice Object drafts to the local voice inbox.

The bridge serializes one successful projection into canonical JSON and stores
it without interpreting or executing the draft.  Enqueue receipts remain the
content-free receipts supplied by :mod:`voice_inbox`; draft content is decoded
only by an explicit ``read`` call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from voice_inbox import (
    MAX_PAYLOAD_CHARS,
    InboxItem,
    InboxReceipt,
    InboxState,
    VoiceInbox,
)
from voice_objects import (
    CalendarDraft,
    Destination,
    Draft,
    EmailDraft,
    FactRole,
    PlainTextDraft,
    ProjectionReason,
    ProjectionReceipt,
    ProjectionResult,
    ProjectionState,
    TaskDraft,
    VoiceFact,
    VoiceObject,
    project,
)


SCHEMA_VERSION = 1
PAYLOAD_KIND = "whisper-face/voice-object-draft"

_ROOT_KEYS = frozenset({
    "schema_version", "kind", "destination", "draft_type", "draft",
})
_DRAFT_FIELDS: dict[Destination, frozenset[str]] = {
    Destination.PLAIN_TEXT: frozenset({"text"}),
    Destination.EMAIL_DRAFT: frozenset({"recipients", "subject", "body"}),
    Destination.TASK: frozenset({"title", "notes", "due_at"}),
    Destination.CALENDAR_DRAFT: frozenset({
        "title", "notes", "start_at", "end_at", "attendees",
    }),
}
_DRAFT_TYPES: dict[Destination, type[Draft]] = {
    Destination.PLAIN_TEXT: PlainTextDraft,
    Destination.EMAIL_DRAFT: EmailDraft,
    Destination.TASK: TaskDraft,
    Destination.CALENDAR_DRAFT: CalendarDraft,
}
_DRAFT_TYPE_NAMES: dict[Destination, str] = {
    Destination.PLAIN_TEXT: "plain_text_draft",
    Destination.EMAIL_DRAFT: "email_draft",
    Destination.TASK: "task_draft",
    Destination.CALENDAR_DRAFT: "calendar_draft",
}


class VoiceObjectInboxBridgeError(ValueError):
    """A projection or stored payload failed the bridge's closed contract."""


class ProjectionNotQueueableError(VoiceObjectInboxBridgeError):
    """A value was not one successful, internally consistent projection."""


class DraftPayloadFormatError(VoiceObjectInboxBridgeError):
    """A stored draft payload was malformed, non-canonical, or unsupported."""


@dataclass(frozen=True, slots=True)
class QueuedDraft:
    """One draft revealed by an explicit bridge read."""

    item_id: str
    source_id: str
    destination: Destination
    draft: Draft
    state: InboxState
    sequence: int


def _closed_mapping(value: Any, expected: frozenset[str],
                    label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise DraftPayloadFormatError(
            f"{label} must contain exactly {sorted(expected)!r}")
    return dict(value)


def _optional_text(value: Any, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise DraftPayloadFormatError(f"{label} must be text or null")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DraftPayloadFormatError(f"{label} must be text")
    return value


def _text_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
            isinstance(entry, str) for entry in value):
        raise DraftPayloadFormatError(f"{label} must be a list of text")
    return tuple(value)


def _draft_body(draft: Draft) -> dict[str, Any]:
    if type(draft) is PlainTextDraft:
        if not isinstance(draft.text, str):
            raise ProjectionNotQueueableError("draft fields have invalid types")
        return {"text": draft.text}
    if type(draft) is EmailDraft:
        if (not isinstance(draft.recipients, tuple)
                or not all(isinstance(value, str)
                           for value in draft.recipients)
                or (draft.subject is not None
                    and not isinstance(draft.subject, str))
                or not isinstance(draft.body, str)):
            raise ProjectionNotQueueableError("draft fields have invalid types")
        return {
            "recipients": list(draft.recipients),
            "subject": draft.subject,
            "body": draft.body,
        }
    if type(draft) is TaskDraft:
        if (not isinstance(draft.title, str)
                or (draft.notes is not None
                    and not isinstance(draft.notes, str))
                or (draft.due_at is not None
                    and not isinstance(draft.due_at, str))):
            raise ProjectionNotQueueableError("draft fields have invalid types")
        return {
            "title": draft.title,
            "notes": draft.notes,
            "due_at": draft.due_at,
        }
    if type(draft) is CalendarDraft:
        if (not isinstance(draft.title, str)
                or (draft.notes is not None
                    and not isinstance(draft.notes, str))
                or not isinstance(draft.start_at, str)
                or (draft.end_at is not None
                    and not isinstance(draft.end_at, str))
                or not isinstance(draft.attendees, tuple)
                or not all(isinstance(value, str)
                           for value in draft.attendees)):
            raise ProjectionNotQueueableError("draft fields have invalid types")
        return {
            "title": draft.title,
            "notes": draft.notes,
            "start_at": draft.start_at,
            "end_at": draft.end_at,
            "attendees": list(draft.attendees),
        }
    raise ProjectionNotQueueableError("projection draft type is unsupported")


def _encode(destination: Destination, draft: Draft) -> str:
    body = {
        "schema_version": SCHEMA_VERSION,
        "kind": PAYLOAD_KIND,
        "destination": destination.value,
        "draft_type": _DRAFT_TYPE_NAMES[destination],
        "draft": _draft_body(draft),
    }
    return json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _decode(payload: str) -> tuple[Destination, Draft]:
    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DraftPayloadFormatError(
            "voice object draft payload must be valid JSON") from exc

    root = _closed_mapping(raw, _ROOT_KEYS, "voice object draft payload")
    if (root["schema_version"] != SCHEMA_VERSION
            or isinstance(root["schema_version"], bool)):
        raise DraftPayloadFormatError("unsupported draft payload schema version")
    if root["kind"] != PAYLOAD_KIND:
        raise DraftPayloadFormatError("unsupported draft payload kind")
    try:
        destination = Destination(root["destination"])
    except (TypeError, ValueError) as exc:
        raise DraftPayloadFormatError("unsupported draft destination") from exc
    if root["draft_type"] != _DRAFT_TYPE_NAMES[destination]:
        raise DraftPayloadFormatError(
            "draft type does not match the destination")

    data = _closed_mapping(
        root["draft"], _DRAFT_FIELDS[destination], "draft")
    if destination == Destination.PLAIN_TEXT:
        draft: Draft = PlainTextDraft(_text(data["text"], "draft.text"))
    elif destination == Destination.EMAIL_DRAFT:
        draft = EmailDraft(
            recipients=_text_tuple(
                data["recipients"], "draft.recipients"),
            subject=_optional_text(data["subject"], "draft.subject"),
            body=_text(data["body"], "draft.body"),
        )
    elif destination == Destination.TASK:
        draft = TaskDraft(
            title=_text(data["title"], "draft.title"),
            notes=_optional_text(data["notes"], "draft.notes"),
            due_at=_optional_text(data["due_at"], "draft.due_at"),
        )
    else:
        draft = CalendarDraft(
            title=_text(data["title"], "draft.title"),
            notes=_optional_text(data["notes"], "draft.notes"),
            start_at=_text(data["start_at"], "draft.start_at"),
            end_at=_optional_text(data["end_at"], "draft.end_at"),
            attendees=_text_tuple(
                data["attendees"], "draft.attendees"),
        )

    if _encode(destination, draft) != payload:
        raise DraftPayloadFormatError("draft payload must use canonical JSON")
    try:
        facts: list[VoiceFact] = []
        if type(draft) is PlainTextDraft:
            facts.append(VoiceFact(FactRole.DETAILS, draft.text))
        elif type(draft) is EmailDraft:
            if draft.subject is not None:
                facts.append(VoiceFact(FactRole.SUMMARY, draft.subject))
            facts.append(VoiceFact(FactRole.DETAILS, draft.body))
            facts.extend(VoiceFact(FactRole.CONTACT, value)
                         for value in draft.recipients)
        elif type(draft) is TaskDraft:
            facts.append(VoiceFact(FactRole.SUMMARY, draft.title))
            if draft.notes is not None:
                facts.append(VoiceFact(FactRole.DETAILS, draft.notes))
            if draft.due_at is not None:
                facts.append(VoiceFact(FactRole.WHEN, draft.due_at))
        else:
            facts.append(VoiceFact(FactRole.SUMMARY, draft.title))
            if draft.notes is not None:
                facts.append(VoiceFact(FactRole.DETAILS, draft.notes))
            facts.append(VoiceFact(FactRole.WHEN, draft.start_at))
            if draft.end_at is not None:
                facts.append(VoiceFact(FactRole.END, draft.end_at))
            facts.extend(VoiceFact(FactRole.CONTACT, value)
                         for value in draft.attendees)
        round_trip = project(
            VoiceObject("bridge-validation", tuple(facts)), destination)
    except (TypeError, ValueError) as exc:
        raise DraftPayloadFormatError(
            "draft content violates Voice Object constraints") from exc
    if (round_trip.receipt.state is not ProjectionState.PROJECTED
            or round_trip.draft != draft):
        raise DraftPayloadFormatError(
            "draft content is inconsistent with its destination")
    return destination, draft


class VoiceObjectInboxBridge:
    """Persist and explicitly read inert typed drafts through ``VoiceInbox``."""

    def __init__(self, inbox: VoiceInbox) -> None:
        if not isinstance(inbox, VoiceInbox):
            raise TypeError("inbox must be a VoiceInbox")
        self._inbox = inbox

    def enqueue(self, item_id: str, projection: ProjectionResult, *,
                source_id: str) -> InboxReceipt:
        """Queue one successful projection and return a content-free receipt."""
        if not isinstance(projection, ProjectionResult):
            raise TypeError("projection must be a ProjectionResult")
        receipt = projection.receipt
        if type(receipt) is not ProjectionReceipt:
            raise ProjectionNotQueueableError(
                "projection receipt type is unsupported")
        if (receipt.state is not ProjectionState.PROJECTED
                or receipt.reason is not ProjectionReason.READY
                or projection.draft is None):
            raise ProjectionNotQueueableError(
                "only successful projections can be queued")
        expected_type = _DRAFT_TYPES.get(receipt.destination)
        if expected_type is None or type(projection.draft) is not expected_type:
            raise ProjectionNotQueueableError(
                "projection draft type does not match its destination")

        payload = _encode(receipt.destination, projection.draft)
        if len(payload) > MAX_PAYLOAD_CHARS:
            raise ProjectionNotQueueableError(
                f"encoded draft exceeds {MAX_PAYLOAD_CHARS} characters")
        return self._inbox.enqueue(
            item_id, payload, source_id=source_id)

    def read(self, item_id: str) -> QueuedDraft:
        """Explicitly read and decode one queued draft, including its content."""
        item: InboxItem = self._inbox.get(item_id)
        destination, draft = _decode(item.payload)
        return QueuedDraft(
            item_id=item.item_id,
            source_id=item.source_id,
            destination=destination,
            draft=draft,
            state=item.state,
            sequence=item.sequence,
        )
