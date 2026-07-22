"""Read-only macOS destination state for delayed-cleanup planning.

This standalone adapter may read one focused editable destination's bounded
value and selection so the pure delayed-cleanup transaction can plan against
an exact ``DestinationSnapshot``. Raw text and raw Accessibility identifiers
remain transient and are excluded from repr and receipts. Stable identity and
revision tokens are keyed, process-memory-only projections.

There is no write callback, AX action, pasteboard, keyboard, persistence,
logging, runtime, or GUI surface in this module.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from delayed_cleanup_merge import DestinationSnapshot


MAX_TEXT_CHARS = 32_768
MAX_ID_CHARS = 128
_OBSERVATION_KEYS = frozenset({
    "schema_version", "pid", "window_id", "element_id", "role", "text",
    "selection", "focused", "enabled",
})
_EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea"})


class DestinationCaptureState(str, Enum):
    CAPTURED = "captured"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    PRIVATE_DATA_REJECTED = "private_data_rejected"


@dataclass(frozen=True, slots=True)
class DestinationCaptureReceipt:
    """Content-free evidence for one read-only observation."""

    state: DestinationCaptureState
    focused: bool
    enabled: bool
    selection_present: bool
    identity_complete: bool


@dataclass(frozen=True, slots=True, repr=False)
class CapturedDestinationState:
    """Transient exact snapshot plus content-free capture evidence."""

    snapshot: DestinationSnapshot | None = field(repr=False)
    selection: tuple[int, int] | None = field(repr=False)
    receipt: DestinationCaptureReceipt


class DestinationStateReader(Protocol):
    def trusted(self) -> bool: ...

    def read_focused_destination(self) -> Mapping[str, Any] | None: ...


def _bounded_element_identifier(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= MAX_ID_CHARS
            and not any(ord(character) < 32 for character in value))


def _selection(value: object, text_length: int) -> tuple[int, int] | None:
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(not isinstance(item, int) or isinstance(item, bool)
                   for item in value)):
        return None
    start, length = value
    if start < 0 or length < 0 or start > text_length \
            or length > text_length - start:
        return None
    return start, length


def _coerce_ax_range(value: object) -> tuple[int, int] | None:
    """Accept bounded CFRange shapes emitted by supported PyObjC versions."""
    if value is None:
        return None
    if (isinstance(value, (tuple, list)) and len(value) == 2
            and isinstance(value[0], bool)):
        return _coerce_ax_range(value[1]) if value[0] else None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError, OverflowError):
            return None
    for location_name, length_name in (
            ("location", "length"), ("loc", "len")):
        try:
            location = getattr(value, location_name)
            length = getattr(value, length_name)
        except Exception:
            continue
        try:
            return int(location), int(length)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _token(key: bytes, purpose: bytes, parts: tuple[object, ...]) -> str:
    digest = hmac.new(key, purpose, hashlib.sha256)
    for part in parts:
        encoded = str(part).encode("utf-8", "strict")
        digest.update(struct.pack(">I", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


class MacDestinationStateAdapter:
    """Project read-only AX observations into the transaction contract."""

    def __init__(
        self, reader: DestinationStateReader, *, token_key: bytes | None = None,
    ) -> None:
        if not hasattr(reader, "trusted") \
                or not hasattr(reader, "read_focused_destination"):
            raise TypeError("invalid destination state reader")
        key = token_key if token_key is not None else secrets.token_bytes(32)
        if not isinstance(key, bytes) or len(key) < 16:
            raise ValueError("destination token key is too short")
        self._reader = reader
        self._key = key

    @staticmethod
    def _empty(
        state: DestinationCaptureState, *, focused: bool = False,
        enabled: bool = False, selection_present: bool = False,
        identity_complete: bool = False,
    ) -> CapturedDestinationState:
        return CapturedDestinationState(
            None, None, DestinationCaptureReceipt(
                state, focused, enabled, selection_present,
                identity_complete))

    def capture(self) -> CapturedDestinationState:
        """Read once without side effects and fail closed on schema expansion."""
        if sys.platform != "darwin":
            return self._empty(DestinationCaptureState.UNAVAILABLE)
        try:
            trusted = self._reader.trusted()
        except Exception:
            trusted = None
        if trusted is not True:
            return self._empty(
                DestinationCaptureState.PERMISSION_DENIED
                if trusted is False else DestinationCaptureState.UNAVAILABLE)
        try:
            raw = self._reader.read_focused_destination()
        except Exception:
            return self._empty(DestinationCaptureState.UNAVAILABLE)
        if raw is None:
            return self._empty(DestinationCaptureState.UNAVAILABLE)
        if not isinstance(raw, Mapping):
            return self._empty(DestinationCaptureState.MALFORMED)
        try:
            keys = set(raw)
        except Exception:
            return self._empty(DestinationCaptureState.MALFORMED)
        if keys - _OBSERVATION_KEYS:
            return self._empty(DestinationCaptureState.PRIVATE_DATA_REJECTED)
        if keys != _OBSERVATION_KEYS:
            return self._empty(DestinationCaptureState.MALFORMED)
        try:
            data = {key: raw[key] for key in _OBSERVATION_KEYS}
        except Exception:
            return self._empty(DestinationCaptureState.MALFORMED)
        if data["schema_version"] != 1:
            return self._empty(DestinationCaptureState.MALFORMED)

        focused = data["focused"] is True
        enabled = data["enabled"] is True
        identity_complete = (
            isinstance(data["pid"], int) and not isinstance(data["pid"], bool)
            and data["pid"] > 0
            and isinstance(data["window_id"], int)
            and not isinstance(data["window_id"], bool)
            and data["window_id"] >= 0
            and _bounded_element_identifier(data["element_id"])
        )
        text = data["text"]
        selection = _selection(
            data["selection"], len(text) if isinstance(text, str) else -1)
        selection_present = selection is not None
        if (data["role"] not in _EDITABLE_ROLES
                or not isinstance(data["focused"], bool)
                or not isinstance(data["enabled"], bool)
                or not isinstance(text, str) or len(text) > MAX_TEXT_CHARS
                or "\x00" in text
                or any(0xD800 <= ord(character) <= 0xDFFF
                       for character in text)
                or not identity_complete or not selection_present):
            return self._empty(
                DestinationCaptureState.MALFORMED,
                focused=focused, enabled=enabled,
                selection_present=selection_present,
                identity_complete=identity_complete)
        if not focused or not enabled:
            return self._empty(
                DestinationCaptureState.UNAVAILABLE,
                focused=focused, enabled=enabled,
                selection_present=True, identity_complete=True)
        assert selection is not None
        identity_parts = (
            data["pid"], data["window_id"], data["element_id"], data["role"])
        destination_id = "mac-destination-" + _token(
            self._key, b"destination-id-v1", identity_parts)
        revision = "mac-revision-" + _token(
            self._key, b"destination-revision-v1",
            (*identity_parts, selection[0], selection[1], text))
        snapshot = DestinationSnapshot(
            destination_id, revision, text, focused=True)
        return CapturedDestinationState(
            snapshot, selection, DestinationCaptureReceipt(
                DestinationCaptureState.CAPTURED, True, True, True, True))


class SystemMacDestinationStateReader:
    """Narrow AX copy-only reader; no action or mutation API is exposed."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("macOS Accessibility is unavailable")
        self._services = None

    @property
    def services(self):
        if self._services is None:
            import ApplicationServices
            self._services = ApplicationServices
        return self._services

    def trusted(self) -> bool:
        return bool(self.services.AXIsProcessTrusted())

    def _copy(self, element: object, attribute: str) -> object | None:
        try:
            error, value = self.services.AXUIElementCopyAttributeValue(
                element, attribute, None)
            return value if error == 0 else None
        except Exception:
            return None

    def read_focused_destination(self) -> Mapping[str, Any] | None:
        try:
            system = self.services.AXUIElementCreateSystemWide()
            app = self._copy(system, "AXFocusedApplication")
            if app is None:
                return None
            window = self._copy(app, "AXFocusedWindow")
            element = self._copy(app, "AXFocusedUIElement")
            if window is None or element is None:
                return None
            error, pid = self.services.AXUIElementGetPid(app, None)
            if error != 0:
                return None
            selected = self._copy(element, "AXSelectedTextRange")
            if selected is None:
                return None
            selected_range = _coerce_ax_range(selected)
            if selected_range is None:
                extracted = self.services.AXValueGetValue(
                    selected, 4, None)
                selected_range = _coerce_ax_range(extracted)
            if selected_range is None:
                return None
            return {
                "schema_version": 1,
                "pid": pid,
                "window_id": self._copy(window, "AXWindowNumber"),
                "element_id": self._copy(element, "AXIdentifier"),
                "role": self._copy(element, "AXRole"),
                "text": self._copy(element, "AXValue"),
                "selection": selected_range,
                "focused": self._copy(element, "AXFocused"),
                "enabled": self._copy(element, "AXEnabled"),
            }
        except Exception:
            return None


def capture_frontmost_destination_state() -> CapturedDestinationState:
    """One-shot convenience capture; transaction rechecks need one adapter."""
    if sys.platform != "darwin":
        return CapturedDestinationState(
            None, None, DestinationCaptureReceipt(
                DestinationCaptureState.UNAVAILABLE,
                False, False, False, False))
    return MacDestinationStateAdapter(SystemMacDestinationStateReader()).capture()
