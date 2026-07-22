"""Private, inert drafts for teach-by-demonstration experiments.

This is a storage boundary, not an app-control boundary.  Callers may record a
small closed vocabulary of *described* Finder, Mail, Notes, or menu steps,
preview them, cancel them (which rolls the draft back out of storage), or
explicitly approve them.  Approval only durably marks the draft approved; it
never interprets, replays, executes, or exports a step.

There are deliberately no callbacks, subprocesses, network calls, clipboard,
pointer, keyboard, accessibility, or application APIs in this module.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


SCHEMA_VERSION = 1
STATE_KIND = "whisper-face/demonstration-drafts"
RECEIPT_KIND = "whisper-face/demonstration-draft-receipt"
MAX_DRAFTS = 64
MAX_DRAFT_ID_CHARS = 128
MAX_STEPS = 12
MAX_STEP_TEXT_CHARS = 512

_OPAQUE_ID = re.compile(r"demo-[0-9a-f]{32}\Z")

_ROOT_KEYS = frozenset({"schema_version", "kind", "next_sequence", "drafts"})
_DRAFT_KEYS = frozenset({"draft_id", "domain", "state", "sequence", "steps"})
_STEP_KEYS = frozenset({"action", "text"})


class DemonstrationError(ValueError):
    """Base class for closed-boundary demonstration failures."""


class DemonstrationFormatError(DemonstrationError):
    """Persisted state did not match the closed schema."""


class DemonstrationConflictError(DemonstrationError):
    """A draft identifier was reused for a different domain."""


class DemonstrationNotFoundError(DemonstrationError):
    """The requested draft does not exist."""


class DemonstrationTransitionError(DemonstrationError):
    """A terminal draft was asked to record or approve again."""


class DemonstrationDomain(str, Enum):
    FINDER = "finder"
    MAIL = "mail"
    NOTES = "notes"
    MENU = "menu"


class DemonstrationAction(str, Enum):
    SELECT_ITEM = "select_item"
    CREATE_FOLDER = "create_folder"
    RENAME_ITEM = "rename_item"
    COMPOSE_MESSAGE = "compose_message"
    ADDRESS_MESSAGE = "address_message"
    SET_SUBJECT = "set_subject"
    SET_BODY = "set_body"
    CREATE_NOTE = "create_note"
    SET_NOTE_TITLE = "set_note_title"
    SET_NOTE_BODY = "set_note_body"
    OPEN_MENU = "open_menu"
    CHOOSE_MENU_ITEM = "choose_menu_item"


class DemonstrationState(str, Enum):
    RECORDING = "recording"
    APPROVED = "approved"
    CANCELLED = "cancelled"


_DOMAIN_ACTIONS: dict[DemonstrationDomain, frozenset[DemonstrationAction]] = {
    DemonstrationDomain.FINDER: frozenset({
        DemonstrationAction.SELECT_ITEM,
        DemonstrationAction.CREATE_FOLDER,
        DemonstrationAction.RENAME_ITEM,
    }),
    DemonstrationDomain.MAIL: frozenset({
        DemonstrationAction.COMPOSE_MESSAGE,
        DemonstrationAction.ADDRESS_MESSAGE,
        DemonstrationAction.SET_SUBJECT,
        DemonstrationAction.SET_BODY,
    }),
    DemonstrationDomain.NOTES: frozenset({
        DemonstrationAction.CREATE_NOTE,
        DemonstrationAction.SET_NOTE_TITLE,
        DemonstrationAction.SET_NOTE_BODY,
    }),
    DemonstrationDomain.MENU: frozenset({
        DemonstrationAction.OPEN_MENU,
        DemonstrationAction.CHOOSE_MENU_ITEM,
    }),
}


@dataclass(frozen=True, slots=True)
class DemonstrationStep:
    """One caller-described step; its text is private and omitted from repr."""

    action: DemonstrationAction
    text: str

    def __repr__(self) -> str:
        return f"DemonstrationStep(action={self.action!r}, text=<redacted>)"


@dataclass(frozen=True, slots=True)
class DemonstrationDraft:
    """An explicitly read local draft; step text is omitted from repr."""

    draft_id: str
    domain: DemonstrationDomain
    state: DemonstrationState
    sequence: int
    steps: tuple[DemonstrationStep, ...]

    def __repr__(self) -> str:
        return (
            "DemonstrationDraft("
            f"domain={self.domain!r}, state={self.state!r}, "
            f"sequence={self.sequence!r}, "
            f"step_count={len(self.steps)!r})"
        )


@dataclass(frozen=True, slots=True)
class DemonstrationReceipt:
    """Content-free evidence for a local draft transition."""

    domain: DemonstrationDomain
    state: DemonstrationState
    sequence: int
    step_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": RECEIPT_KIND,
            "domain": self.domain.value,
            "state": self.state.value,
            "sequence": self.sequence,
            "step_count": self.step_count,
        }


def _closed_mapping(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise DemonstrationFormatError(f"{label} has an unsupported shape")
    return dict(value)


def _plain_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _draft_id(value: Any) -> str:
    if (not isinstance(value, str)
            or _OPAQUE_ID.fullmatch(value) is None):
        raise DemonstrationError(
            "draft_id must be an opaque demo-<32 lowercase hex> token")
    return value


def _step_text(value: Any) -> str:
    if (not isinstance(value, str) or not value or "\x00" in value
            or len(value) > MAX_STEP_TEXT_CHARS):
        raise DemonstrationError("step text must be non-empty bounded text")
    return value


def _step(domain: DemonstrationDomain, action: Any, text: Any) -> DemonstrationStep:
    if not isinstance(action, DemonstrationAction):
        raise DemonstrationError("action must be a DemonstrationAction")
    if action not in _DOMAIN_ACTIONS[domain]:
        raise DemonstrationError("action is not allowed for this demonstration domain")
    return DemonstrationStep(action, _step_text(text))


def _encode(drafts: Mapping[str, DemonstrationDraft], next_sequence: int) -> str:
    body = {
        "schema_version": SCHEMA_VERSION,
        "kind": STATE_KIND,
        "next_sequence": next_sequence,
        "drafts": [{
            "draft_id": draft.draft_id,
            "domain": draft.domain.value,
            "state": draft.state.value,
            "sequence": draft.sequence,
            "steps": [
                {"action": step.action.value, "text": step.text}
                for step in draft.steps
            ],
        } for draft in sorted(drafts.values(), key=lambda item: item.sequence)],
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _decode(text: str) -> tuple[dict[str, DemonstrationDraft], int]:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DemonstrationFormatError("demonstration state must be valid JSON") from exc
    root = _closed_mapping(raw, _ROOT_KEYS, "demonstration state")
    if root["schema_version"] != SCHEMA_VERSION or isinstance(root["schema_version"], bool):
        raise DemonstrationFormatError("unsupported demonstration schema version")
    if root["kind"] != STATE_KIND or not _plain_positive_int(root["next_sequence"]):
        raise DemonstrationFormatError("unsupported demonstration state")
    if not isinstance(root["drafts"], list) or len(root["drafts"]) > MAX_DRAFTS:
        raise DemonstrationFormatError("demonstration draft limit exceeded")
    drafts: dict[str, DemonstrationDraft] = {}
    sequences: set[int] = set()
    for index, raw_draft in enumerate(root["drafts"]):
        data = _closed_mapping(raw_draft, _DRAFT_KEYS, f"drafts[{index}]")
        try:
            draft_id = _draft_id(data["draft_id"])
            domain = DemonstrationDomain(data["domain"])
            state = DemonstrationState(data["state"])
        except (DemonstrationError, TypeError, ValueError) as exc:
            raise DemonstrationFormatError("draft contains an unsupported value") from exc
        if state == DemonstrationState.CANCELLED:
            raise DemonstrationFormatError("cancelled drafts must not be retained")
        if not _plain_positive_int(data["sequence"]) or data["sequence"] >= root["next_sequence"]:
            raise DemonstrationFormatError("draft sequence is invalid")
        if not isinstance(data["steps"], list) or len(data["steps"]) > MAX_STEPS:
            raise DemonstrationFormatError("draft step limit exceeded")
        try:
            steps = tuple(_decode_step(domain, raw_step) for raw_step in data["steps"])
        except (DemonstrationError, TypeError, ValueError) as exc:
            raise DemonstrationFormatError("draft step is invalid") from exc
        if draft_id in drafts or data["sequence"] in sequences:
            raise DemonstrationFormatError("draft identifiers and sequences must be unique")
        drafts[draft_id] = DemonstrationDraft(draft_id, domain, state, data["sequence"], steps)
        sequences.add(data["sequence"])
    return drafts, root["next_sequence"]


def _decode_step(domain: DemonstrationDomain, value: Any) -> DemonstrationStep:
    data = _closed_mapping(value, _STEP_KEYS, "step")
    return _step(domain, DemonstrationAction(data["action"]), data["text"])


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


class DemonstrationDraftStore:
    """Single-owner, atomically persisted local drafts with no execution path."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = RLock()
        if self.path.exists():
            self._drafts, self._next_sequence = _decode(self.path.read_text(encoding="utf-8"))
        else:
            self._drafts, self._next_sequence = {}, 1

    @staticmethod
    def _receipt(draft: DemonstrationDraft) -> DemonstrationReceipt:
        return DemonstrationReceipt(
            draft.domain, draft.state, draft.sequence, len(draft.steps))

    def begin(self, draft_id: str, domain: DemonstrationDomain) -> DemonstrationReceipt:
        draft_id = _draft_id(draft_id)
        if not isinstance(domain, DemonstrationDomain):
            raise DemonstrationError("domain must be a DemonstrationDomain")
        with self._lock:
            existing = self._drafts.get(draft_id)
            if existing is not None:
                if existing.domain != domain:
                    raise DemonstrationConflictError("draft_id is already bound to a different domain")
                return self._receipt(existing)
            if len(self._drafts) >= MAX_DRAFTS:
                raise OverflowError("demonstration draft store is full")
            draft = DemonstrationDraft(draft_id, domain, DemonstrationState.RECORDING, self._next_sequence, ())
            proposed = dict(self._drafts)
            proposed[draft_id] = draft
            _atomic_write(self.path, _encode(proposed, self._next_sequence + 1))
            self._drafts, self._next_sequence = proposed, self._next_sequence + 1
            return self._receipt(draft)

    def record(self, draft_id: str, action: DemonstrationAction, text: str) -> DemonstrationReceipt:
        draft_id = _draft_id(draft_id)
        with self._lock:
            existing = self._require_recording(draft_id)
            if len(existing.steps) >= MAX_STEPS:
                raise OverflowError("demonstration step limit reached")
            step = _step(existing.domain, action, text)
            updated = DemonstrationDraft(existing.draft_id, existing.domain, existing.state, existing.sequence, existing.steps + (step,))
            proposed = dict(self._drafts)
            proposed[draft_id] = updated
            _atomic_write(self.path, _encode(proposed, self._next_sequence))
            self._drafts = proposed
            return self._receipt(updated)

    def preview(self, draft_id: str) -> DemonstrationDraft:
        """Explicitly read a local draft; this does not execute or mutate it."""
        return self.get(draft_id)

    def approve(self, draft_id: str) -> DemonstrationReceipt:
        draft_id = _draft_id(draft_id)
        with self._lock:
            existing = self._drafts.get(draft_id)
            if existing is None:
                raise DemonstrationNotFoundError("demonstration draft was not found")
            if existing.state == DemonstrationState.APPROVED:
                return self._receipt(existing)
            if not existing.steps:
                raise DemonstrationTransitionError("cannot approve an empty demonstration draft")
            updated = DemonstrationDraft(existing.draft_id, existing.domain, DemonstrationState.APPROVED, existing.sequence, existing.steps)
            proposed = dict(self._drafts)
            proposed[draft_id] = updated
            _atomic_write(self.path, _encode(proposed, self._next_sequence))
            self._drafts = proposed
            return self._receipt(updated)

    def cancel(self, draft_id: str) -> DemonstrationReceipt:
        """Atomically remove one unapproved draft, rolling back its private text."""
        draft_id = _draft_id(draft_id)
        with self._lock:
            existing = self._drafts.get(draft_id)
            if existing is None:
                raise DemonstrationNotFoundError("demonstration draft was not found")
            if existing.state == DemonstrationState.APPROVED:
                raise DemonstrationTransitionError("approved demonstration drafts cannot be cancelled")
            proposed = dict(self._drafts)
            del proposed[draft_id]
            _atomic_write(self.path, _encode(proposed, self._next_sequence))
            self._drafts = proposed
            return DemonstrationReceipt(
                existing.domain, DemonstrationState.CANCELLED,
                existing.sequence, len(existing.steps))

    def get(self, draft_id: str) -> DemonstrationDraft:
        draft_id = _draft_id(draft_id)
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise DemonstrationNotFoundError("demonstration draft was not found")
            return draft

    def drafts(self, *, state: DemonstrationState | None = None) -> tuple[DemonstrationDraft, ...]:
        if state is not None and not isinstance(state, DemonstrationState):
            raise TypeError("state must be a DemonstrationState or None")
        with self._lock:
            return tuple(draft for draft in sorted(self._drafts.values(), key=lambda item: item.sequence) if state is None or draft.state == state)

    def _require_recording(self, draft_id: str) -> DemonstrationDraft:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise DemonstrationNotFoundError("demonstration draft was not found")
        if draft.state != DemonstrationState.RECORDING:
            raise DemonstrationTransitionError("approved demonstration drafts cannot be changed")
        return draft
