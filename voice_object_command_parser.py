"""Strict, inert parsing of a small spoken Voice Object command grammar.

Only the command forms documented in :func:`parse_command` are accepted.  The
parser neither infers intent nor performs any destination action; it produces
a :class:`voice_objects.VoiceObject` and its existing inert projection.  Parse
receipts contain classifications and counts only, never spoken content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from voice_objects import (
    MAX_FACT_CHARS,
    Destination,
    FactRole,
    ProjectionResult,
    ProjectionState,
    VoiceFact,
    VoiceObject,
    project,
)


# An email command can carry two fact values.  This protects parsing before
# constructing any values while still allowing every bounded VoiceFact value.
MAX_COMMAND_CHARS = (MAX_FACT_CHARS * 2) + 64


class CommandKind(str, Enum):
    CREATE_TASK = "create_task"
    DRAFT_EMAIL = "draft_email"
    CREATE_CALENDAR_EVENT = "create_calendar_event"


class ParseState(str, Enum):
    PARSED = "parsed"
    REJECTED = "rejected"


class ParseReason(str, Enum):
    READY = "ready"
    UNSUPPORTED_COMMAND = "unsupported_command"
    INVALID_CONTENT = "invalid_content"
    INVALID_TIME = "invalid_time"
    INVALID_OBJECT_ID = "invalid_object_id"
    PROJECTION_REJECTED = "projection_rejected"


@dataclass(frozen=True)
class ParseReceipt:
    """Content-free evidence for one command parsing decision."""

    command_kind: CommandKind | None
    destination: Destination | None
    state: ParseState
    reason: ParseReason
    input_character_count: int
    output_fact_count: int


@dataclass(frozen=True)
class CommandParseResult:
    """A fully accepted Voice Object command, or a content-free rejection."""

    voice_object: VoiceObject | None
    projection: ProjectionResult | None
    receipt: ParseReceipt


_ANY_TEXT = r"[\s\S]*"
_TASK_RE = re.compile(rf"\Acreate task:(?P<title>{_ANY_TEXT})\Z")
_EMAIL_RE = re.compile(
    rf"\Adraft email to (?P<contact>[^:\r\n]*):(?P<body>{_ANY_TEXT})\Z")

# This intentionally admits only a closed, extended ISO 8601 subset.  The
# value is then validated by VoiceFact using the repository's time contract.
_ISO_START = (
    r"\d{4}-\d{2}-\d{2}"
    r"(?:[Tt ]\d{2}:\d{2}"
    r"(?::\d{2}(?:[.,]\d{1,6})?)?"
    r"(?:Z|[+-]\d{2}:\d{2})?)?"
)
_CALENDAR_RE = re.compile(
    rf"\Acreate calendar event (?P<when>{_ISO_START}):"
    rf"(?P<title>{_ANY_TEXT})\Z")


def _receipt(
    *,
    command_kind: CommandKind | None,
    destination: Destination | None,
    state: ParseState,
    reason: ParseReason,
    input_character_count: int,
    output_fact_count: int = 0,
) -> ParseReceipt:
    return ParseReceipt(
        command_kind=command_kind,
        destination=destination,
        state=state,
        reason=reason,
        input_character_count=input_character_count,
        output_fact_count=output_fact_count,
    )


def _rejected(
    *,
    command_kind: CommandKind | None,
    destination: Destination | None,
    reason: ParseReason,
    input_character_count: int,
) -> CommandParseResult:
    return CommandParseResult(
        voice_object=None,
        projection=None,
        receipt=_receipt(
            command_kind=command_kind,
            destination=destination,
            state=ParseState.REJECTED,
            reason=reason,
            input_character_count=input_character_count,
        ),
    )


def parse_command(command: str, *, object_id: str) -> CommandParseResult:
    """Parse one exact command without making any destination-side effect.

    The closed, case-sensitive grammar is:

    * ``create task: <title>``
    * ``draft email to <contact>: <body>``
    * ``create calendar event <ISO start>: <title>``

    Text after a command delimiter is retained verbatim in the corresponding
    fact.  Missing, blank, oversized, malformed, or unsupported inputs return
    a rejected result rather than a best-effort interpretation.
    """
    if not isinstance(command, str):
        raise TypeError("command must be a string")

    input_character_count = len(command)
    if input_character_count > MAX_COMMAND_CHARS:
        return _rejected(
            command_kind=None,
            destination=None,
            reason=ParseReason.INVALID_CONTENT,
            input_character_count=input_character_count,
        )

    command_kind: CommandKind
    destination: Destination
    facts: tuple[VoiceFact, ...]
    match = _TASK_RE.fullmatch(command)
    if match is not None:
        command_kind = CommandKind.CREATE_TASK
        destination = Destination.TASK
        raw_facts = ((FactRole.SUMMARY, match["title"]),)
    else:
        match = _EMAIL_RE.fullmatch(command)
        if match is not None:
            command_kind = CommandKind.DRAFT_EMAIL
            destination = Destination.EMAIL_DRAFT
            raw_facts = (
                (FactRole.CONTACT, match["contact"]),
                (FactRole.DETAILS, match["body"]),
            )
        else:
            match = _CALENDAR_RE.fullmatch(command)
            if match is None:
                return _rejected(
                    command_kind=None,
                    destination=None,
                    reason=ParseReason.UNSUPPORTED_COMMAND,
                    input_character_count=input_character_count,
                )
            command_kind = CommandKind.CREATE_CALENDAR_EVENT
            destination = Destination.CALENDAR_DRAFT
            raw_facts = (
                (FactRole.WHEN, match["when"]),
                (FactRole.SUMMARY, match["title"]),
            )

    try:
        facts = tuple(VoiceFact(role, value) for role, value in raw_facts)
    except ValueError:
        return _rejected(
            command_kind=command_kind,
            destination=destination,
            reason=(ParseReason.INVALID_TIME
                    if command_kind == CommandKind.CREATE_CALENDAR_EVENT
                    else ParseReason.INVALID_CONTENT),
            input_character_count=input_character_count,
        )

    try:
        voice_object = VoiceObject(object_id, facts)
    except ValueError:
        return _rejected(
            command_kind=command_kind,
            destination=destination,
            reason=ParseReason.INVALID_OBJECT_ID,
            input_character_count=input_character_count,
        )

    projection = project(voice_object, destination)
    if projection.receipt.state != ProjectionState.PROJECTED:
        return _rejected(
            command_kind=command_kind,
            destination=destination,
            reason=ParseReason.PROJECTION_REJECTED,
            input_character_count=input_character_count,
        )

    return CommandParseResult(
        voice_object=voice_object,
        projection=projection,
        receipt=_receipt(
            command_kind=command_kind,
            destination=destination,
            state=ParseState.PARSED,
            reason=ParseReason.READY,
            input_character_count=input_character_count,
            output_fact_count=len(facts),
        ),
    )
