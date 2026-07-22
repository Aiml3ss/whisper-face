"""Narrow, idempotent macOS compose-draft adapter.

Private recipients, subject, and body cross only the injected in-process
``NSSharingServiceNameComposeEmail`` seam. They never enter receipts, process
arguments, URLs, logs, or adapter state. The adapter can request a compose UI;
it has no send, dispatch, clipboard, subprocess, or persistence capability.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import secrets
import threading
from typing import Callable


SCHEMA_VERSION = 1
MAX_PENDING = 128
MAX_RECIPIENTS = 64
MAX_RECIPIENT_CHARS = 512
MAX_SUBJECT_CHARS = 2_000
MAX_BODY_CHARS = 100_000


class ComposeState(str, Enum):
    REQUESTED = "requested"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ComposeReceipt:
    """Content-free terminal evidence for one compose request."""

    state: ComposeState
    attempted: bool
    schema_version: int = SCHEMA_VERSION

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "attempted": self.attempted,
        }


def _default_service_factory():
    from AppKit import NSSharingService, NSSharingServiceNameComposeEmail
    return NSSharingService.sharingServiceNamed_(
        NSSharingServiceNameComposeEmail)


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def _valid_text(value: object, maximum: int, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value.strip()))
        and len(value) <= maximum
        and "\x00" not in value
    )


class MacEmailComposeAdapter:
    """Issue one in-process compose request per process-session nonce."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], object | None] = _default_service_factory,
        main_thread_check: Callable[[], bool] = _on_main_thread,
        max_pending: int = MAX_PENDING,
    ) -> None:
        if (not callable(service_factory) or not callable(main_thread_check)
                or not isinstance(max_pending, int)
                or isinstance(max_pending, bool) or max_pending <= 0):
            raise ValueError("invalid compose adapter configuration")
        self._service_factory = service_factory
        self._main_thread_check = main_thread_check
        self._max_pending = max_pending
        self._session_prefix = secrets.token_urlsafe(16)
        self._sequence = 0
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._receipts: OrderedDict[str, ComposeReceipt] = OrderedDict()
        self._lock = threading.Lock()

    def issue_nonce(self) -> str:
        """Create a bounded capability after explicit user confirmation."""

        with self._lock:
            self._sequence += 1
            nonce = f"{self._session_prefix}_{self._sequence:x}"
            self._pending[nonce] = None
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
            return nonce

    def _remember(self, nonce: str, receipt: ComposeReceipt) -> ComposeReceipt:
        self._receipts[nonce] = receipt
        while len(self._receipts) > self._max_pending:
            self._receipts.popitem(last=False)
        return receipt

    @staticmethod
    def _valid_nonce(nonce: object) -> bool:
        return (
            isinstance(nonce, str)
            and 16 <= len(nonce) <= 96
            and all(character.isalnum() or character in "-_"
                    for character in nonce)
        )

    @staticmethod
    def _valid_draft(
        recipients: object, subject: object, body: object,
    ) -> bool:
        return (
            isinstance(recipients, tuple)
            and 1 <= len(recipients) <= MAX_RECIPIENTS
            and all(_valid_text(value, MAX_RECIPIENT_CHARS, allow_empty=False)
                    for value in recipients)
            and (subject is None or _valid_text(
                subject, MAX_SUBJECT_CHARS, allow_empty=True))
            and _valid_text(body, MAX_BODY_CHARS, allow_empty=True)
        )

    def compose(
        self,
        nonce: str,
        *,
        recipients: tuple[str, ...],
        subject: str | None,
        body: str,
    ) -> ComposeReceipt:
        """Request a compose window once; never send or retry the request."""

        if not self._valid_nonce(nonce):
            return ComposeReceipt(ComposeState.UNAVAILABLE, False)
        with self._lock:
            previous = self._receipts.get(nonce)
            if previous is not None:
                return previous
            if nonce not in self._pending:
                return ComposeReceipt(ComposeState.UNAVAILABLE, False)
            self._pending.pop(nonce, None)
            if not self._valid_draft(recipients, subject, body):
                return self._remember(
                    nonce, ComposeReceipt(ComposeState.INVALID, False))
            try:
                if self._main_thread_check() is not True:
                    return self._remember(
                        nonce, ComposeReceipt(
                            ComposeState.UNAVAILABLE, False))
            except Exception:
                return self._remember(
                    nonce, ComposeReceipt(ComposeState.UNAVAILABLE, False))
            attempted = False
            try:
                service = self._service_factory()
                can_perform = getattr(service, "canPerformWithItems_", None)
                set_recipients = getattr(service, "setRecipients_", None)
                set_subject = getattr(service, "setSubject_", None)
                perform = getattr(service, "performWithItems_", None)
                items = [body]
                if (service is None or not callable(can_perform)
                        or not callable(set_recipients)
                        or not callable(set_subject) or not callable(perform)
                        or can_perform(items) is not True):
                    return self._remember(
                        nonce, ComposeReceipt(ComposeState.UNAVAILABLE, False))
                set_recipients(list(recipients))
                set_subject(subject or "")
                attempted = True
                perform(items)
                return self._remember(
                    nonce, ComposeReceipt(ComposeState.REQUESTED, True))
            except Exception:
                return self._remember(
                    nonce, ComposeReceipt(
                        ComposeState.FAILED if attempted else
                        ComposeState.UNAVAILABLE,
                        attempted,
                    ))
