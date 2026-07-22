"""One-shot, content-free macOS clipboard boundary for Voice Inbox drafts.

Private draft text is accepted only for the duration of ``copy`` and is never
stored in adapter state or returned in its receipt.  The adapter exposes no
typing, paste, app automation, process, URL, persistence, or network surface.
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
MAX_CONTENT_CHARS = 300_000


class CopyState(str, Enum):
    COPIED = "copied"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    FAILED = "failed"


class ClearState(str, Enum):
    CLEARED = "cleared"
    CHANGED = "changed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CopyReceipt:
    """Content-free terminal evidence for one explicit copy attempt."""

    state: CopyState
    attempted: bool
    schema_version: int = SCHEMA_VERSION

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "attempted": self.attempted,
        }


@dataclass(frozen=True, slots=True)
class ClearReceipt:
    """Content-free terminal evidence for one explicit clear attempt."""

    state: ClearState
    attempted: bool
    schema_version: int = SCHEMA_VERSION

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "attempted": self.attempted,
        }


def _write_clipboard(content: str) -> int | None:
    from AppKit import NSPasteboard, NSPasteboardTypeString

    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    if pasteboard.setString_forType_(content, NSPasteboardTypeString) is not True:
        return None
    return int(pasteboard.changeCount())


def _clear_clipboard_if_owned(expected_change_count: int) -> ClearState:
    from AppKit import NSPasteboard

    pasteboard = NSPasteboard.generalPasteboard()
    if int(pasteboard.changeCount()) != expected_change_count:
        return ClearState.CHANGED
    pasteboard.clearContents()
    return ClearState.CLEARED


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


class MacVoiceDraftClipboardAdapter:
    """Allow one clipboard write per process-session capability nonce."""

    def __init__(
        self,
        *,
        writer: Callable[[str], int | None] = _write_clipboard,
        clearer: Callable[[int], ClearState] = _clear_clipboard_if_owned,
        main_thread_check: Callable[[], bool] = _on_main_thread,
        max_pending: int = MAX_PENDING,
    ) -> None:
        if (not callable(writer) or not callable(clearer)
                or not callable(main_thread_check)
                or not isinstance(max_pending, int)
                or isinstance(max_pending, bool) or max_pending <= 0):
            raise ValueError("invalid Voice Inbox clipboard configuration")
        self._writer = writer
        self._clearer = clearer
        self._main_thread_check = main_thread_check
        self._max_pending = max_pending
        self._session_prefix = secrets.token_urlsafe(16)
        self._sequence = 0
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._receipts: OrderedDict[str, CopyReceipt] = OrderedDict()
        self._clear_pending: OrderedDict[str, int] = OrderedDict()
        self._clear_receipts: OrderedDict[str, ClearReceipt] = OrderedDict()
        self._owned_change_count: int | None = None
        self._lock = threading.Lock()

    def issue_nonce(self) -> str:
        """Issue a bounded capability only after the GUI asks for one."""

        with self._lock:
            self._sequence += 1
            nonce = f"{self._session_prefix}_{self._sequence:x}"
            self._pending[nonce] = None
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
            return nonce

    def issue_clear_nonce(self) -> str:
        """Issue one capability for the latest adapter-owned clipboard write."""

        with self._lock:
            if self._owned_change_count is None:
                return ""
            self._sequence += 1
            nonce = f"{self._session_prefix}_{self._sequence:x}"
            self._clear_pending[nonce] = self._owned_change_count
            self._owned_change_count = None
            while len(self._clear_pending) > self._max_pending:
                self._clear_pending.popitem(last=False)
            return nonce

    def _remember(self, nonce: str, receipt: CopyReceipt) -> CopyReceipt:
        self._receipts[nonce] = receipt
        while len(self._receipts) > self._max_pending:
            self._receipts.popitem(last=False)
        return receipt

    def _remember_clear(
        self, nonce: str, receipt: ClearReceipt,
    ) -> ClearReceipt:
        self._clear_receipts[nonce] = receipt
        while len(self._clear_receipts) > self._max_pending:
            self._clear_receipts.popitem(last=False)
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
    def _valid_content(content: object) -> bool:
        return (
            isinstance(content, str)
            and bool(content)
            and len(content) <= MAX_CONTENT_CHARS
            and "\x00" not in content
        )

    def copy(self, nonce: str, *, content: str) -> CopyReceipt:
        """Copy private content once and retain only a content-free receipt."""

        if not self._valid_nonce(nonce):
            return CopyReceipt(CopyState.UNAVAILABLE, False)
        with self._lock:
            previous = self._receipts.get(nonce)
            if previous is not None:
                return previous
            if nonce not in self._pending:
                return CopyReceipt(CopyState.UNAVAILABLE, False)
            self._pending.pop(nonce, None)
            if not self._valid_content(content):
                return self._remember(
                    nonce, CopyReceipt(CopyState.INVALID, False))
            self._owned_change_count = None
            try:
                if self._main_thread_check() is not True:
                    return self._remember(
                        nonce, CopyReceipt(CopyState.UNAVAILABLE, False))
            except Exception:
                return self._remember(
                    nonce, CopyReceipt(CopyState.UNAVAILABLE, False))
            try:
                owned_change_count = self._writer(content)
            except Exception:
                return self._remember(
                    nonce, CopyReceipt(CopyState.FAILED, True))
            if (not isinstance(owned_change_count, int)
                    or isinstance(owned_change_count, bool)
                    or owned_change_count < 0):
                return self._remember(
                    nonce, CopyReceipt(CopyState.FAILED, True))
            self._owned_change_count = owned_change_count
            return self._remember(
                nonce, CopyReceipt(CopyState.COPIED, True))

    def clear(self, nonce: str) -> ClearReceipt:
        """Clear once only while the clipboard is still adapter-owned."""

        if not self._valid_nonce(nonce):
            return ClearReceipt(ClearState.UNAVAILABLE, False)
        with self._lock:
            previous = self._clear_receipts.get(nonce)
            if previous is not None:
                return previous
            expected_change_count = self._clear_pending.pop(nonce, None)
            if expected_change_count is None:
                return ClearReceipt(ClearState.UNAVAILABLE, False)
            try:
                if self._main_thread_check() is not True:
                    return self._remember_clear(
                        nonce, ClearReceipt(ClearState.UNAVAILABLE, False))
            except Exception:
                return self._remember_clear(
                    nonce, ClearReceipt(ClearState.UNAVAILABLE, False))
            try:
                state = self._clearer(expected_change_count)
            except Exception:
                return self._remember_clear(
                    nonce, ClearReceipt(ClearState.FAILED, True))
            if state is ClearState.CHANGED:
                return self._remember_clear(
                    nonce, ClearReceipt(ClearState.CHANGED, False))
            if state is ClearState.CLEARED:
                return self._remember_clear(
                    nonce, ClearReceipt(ClearState.CLEARED, True))
            return self._remember_clear(
                nonce, ClearReceipt(ClearState.FAILED, True))
