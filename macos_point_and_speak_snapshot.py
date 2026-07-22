"""Read-only macOS Accessibility snapshots for Point-and-Speak.

Only role, accessible title/description, geometry, visibility, enablement,
focus, and selected-state metadata are read. Element values, document text,
selected text, actions, callbacks, application identifiers, and bundle
identifiers are never requested or returned. This module has no write or AX
action surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import math
import sys
from typing import Any, Protocol


MAX_OBSERVED_ELEMENTS = 2_048
MAX_TARGETS = 256
MAX_DEPTH = 12
MAX_ACCESSIBLE_NAME_CHARS = 128

_ROLE_MAP = {
    "AXButton": "button",
    "AXMenuButton": "button",
    "AXPopUpButton": "button",
    "AXCheckBox": "checkbox",
    "AXLink": "link",
    "AXMenuItem": "menu_item",
    "AXRadioButton": "radio_button",
    "AXTab": "tab",
    "AXTabButton": "tab",
    "AXTextField": "text_field",
    "AXTextArea": "text_field",
}


class SnapshotState(str, Enum):
    CAPTURED = "captured"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SnapshotReceipt:
    """Content-free aggregate evidence for one read-only capture."""

    state: SnapshotState
    observed_elements: int
    emitted_targets: int
    skipped_elements: int
    truncated: bool


@dataclass(frozen=True, slots=True, repr=False)
class AccessibilityTargetCapture:
    """Private accessible names plus their content-free capture receipt."""

    targets: tuple[Mapping[str, Any], ...]
    receipt: SnapshotReceipt


class AccessibilityReader(Protocol):
    def trusted(self) -> bool: ...

    def root(self) -> object | None: ...

    def attribute(self, element: object, name: str) -> object | None: ...

    def geometry(self, element: object) -> tuple[float, float, float, float] \
            | None: ...


def _safe_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    if (len(value) > MAX_ACCESSIBLE_NAME_CHARS
            or any(ord(character) < 32 for character in value)):
        return ""
    return value


def _plain_geometry(
    value: object,
) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        x, y, width, height = (float(part) for part in value)
    except Exception:
        return None
    if (not all(math.isfinite(part) for part in (x, y, width, height))
            or not -1_000_000 <= x <= 1_000_000
            or not -1_000_000 <= y <= 1_000_000
            or not 0.0 < width <= 1_000_000
            or not 0.0 < height <= 1_000_000):
        return None
    return x, y, width, height


def _attribute(reader: AccessibilityReader, element: object,
               name: str) -> object | None:
    try:
        return reader.attribute(element, name)
    except Exception:
        return None


def _geometry(reader: AccessibilityReader, element: object) -> object | None:
    try:
        return reader.geometry(element)
    except Exception:
        return None


def _children(reader: AccessibilityReader, element: object) -> Sequence[object] \
        | None:
    value = _attribute(reader, element, "children")
    if (value is None or isinstance(value, (str, bytes, bytearray))
            or not isinstance(value, Sequence)):
        return None
    try:
        len(value)
    except Exception:
        return None
    return value


def capture_accessibility_targets(
    reader: AccessibilityReader,
) -> AccessibilityTargetCapture:
    """Traverse one frontmost AX tree without requesting values or actions."""
    try:
        trusted = reader.trusted()
    except Exception:
        trusted = None
    if trusted is not True:
        return AccessibilityTargetCapture((), SnapshotReceipt(
            (SnapshotState.PERMISSION_DENIED if trusted is False
             else SnapshotState.UNAVAILABLE), 0, 0, 0, False))
    try:
        root = reader.root()
    except Exception:
        root = None
    if root is None:
        return AccessibilityTargetCapture((), SnapshotReceipt(
            SnapshotState.UNAVAILABLE, 0, 0, 0, False))

    stack: list[tuple[object, int]] = [(root, 0)]
    targets: list[Mapping[str, Any]] = []
    observed = skipped = 0
    truncated = False
    seen: set[int] = set()
    while stack:
        if observed >= MAX_OBSERVED_ELEMENTS:
            truncated = True
            break
        element, depth = stack.pop()
        element_identity = id(element)
        if element_identity in seen:
            skipped += 1
            continue
        seen.add(element_identity)
        observed += 1

        role_value = _attribute(reader, element, "role")
        role = _ROLE_MAP.get(role_value) if isinstance(role_value, str) else None
        if role is not None:
            title = _safe_name(_attribute(reader, element, "title"))
            label = _safe_name(_attribute(reader, element, "description"))
            geometry = _plain_geometry(_geometry(reader, element))
            hidden = _attribute(reader, element, "hidden")
            enabled = _attribute(reader, element, "enabled")
            if ((title.strip() or label.strip()) and geometry is not None
                    and isinstance(hidden, bool) and isinstance(enabled, bool)):
                x, y, width, height = geometry
                focused = _attribute(reader, element, "focused") is True
                selected = _attribute(reader, element, "selected")
                selection = (
                    "selected" if selected is True
                    else "unselected" if selected is False
                    else "not_applicable"
                )
                targets.append({
                    "schema_version": 1,
                    "target_id": f"ax-{len(targets):04x}",
                    "role": role,
                    "title": title,
                    "label": label,
                    "geometry": {
                        "x": x, "y": y, "width": width, "height": height,
                    },
                    "visible": not hidden,
                    "enabled": enabled,
                    "focused": focused and not hidden,
                    "selection": selection,
                })
                if len(targets) >= MAX_TARGETS:
                    children = _children(reader, element)
                    truncated = bool(stack) or bool(children)
                    break
            else:
                skipped += 1

        children = _children(reader, element)
        if depth >= MAX_DEPTH:
            if children:
                truncated = True
            continue
        if children is not None:
            remaining = MAX_OBSERVED_ELEMENTS - observed - len(stack)
            if len(children) > remaining:
                truncated = True
            try:
                next_children = children[:max(0, remaining)]
                for child in reversed(next_children):
                    stack.append((child, depth + 1))
            except Exception:
                skipped += 1

    receipt = SnapshotReceipt(
        SnapshotState.CAPTURED,
        observed_elements=observed,
        emitted_targets=len(targets),
        skipped_elements=skipped,
        truncated=truncated,
    )
    return AccessibilityTargetCapture(tuple(targets), receipt)


class SystemMacAccessibilityReader:
    """Narrow read-only adapter over macOS ApplicationServices."""

    _ATTRIBUTE_NAMES = {
        "role": "AXRole",
        "title": "AXTitle",
        "description": "AXDescription",
        "hidden": "AXHidden",
        "enabled": "AXEnabled",
        "focused": "AXFocused",
        "selected": "AXSelected",
        "children": "AXChildren",
    }

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

    def root(self) -> object | None:
        try:
            system_wide = self.services.AXUIElementCreateSystemWide()
            error, application = self.services.AXUIElementCopyAttributeValue(
                system_wide, "AXFocusedApplication", None)
            return application if error == 0 else None
        except Exception:
            return None

    def attribute(self, element: object, name: str) -> object | None:
        attribute = self._ATTRIBUTE_NAMES.get(name)
        if attribute is None:
            raise ValueError("unsupported accessibility attribute")
        try:
            error, value = self.services.AXUIElementCopyAttributeValue(
                element, attribute, None)
        except Exception:
            return None
        return value if error == 0 else None

    def geometry(self, element: object) -> tuple[float, float, float, float] \
            | None:
        position = self._ax_value(element, "AXPosition", 1)
        size = self._ax_value(element, "AXSize", 2)
        if position is None or size is None:
            return None
        return (
            float(position.x), float(position.y),
            float(size.width), float(size.height),
        )

    def _ax_value(self, element: object, attribute: str, value_type: int):
        try:
            error, value = self.services.AXUIElementCopyAttributeValue(
                element, attribute, None)
            if error != 0 or value is None:
                return None
            success, result = self.services.AXValueGetValue(
                value, value_type, None)
            return result if success else None
        except Exception:
            return None


def capture_frontmost_accessibility_targets() -> AccessibilityTargetCapture:
    """Capture the frontmost Mac app without prompting or performing actions."""
    if sys.platform != "darwin":
        return AccessibilityTargetCapture((), SnapshotReceipt(
            SnapshotState.UNAVAILABLE, 0, 0, 0, False))
    return capture_accessibility_targets(SystemMacAccessibilityReader())
