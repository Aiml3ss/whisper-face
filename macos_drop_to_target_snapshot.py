"""Read-only macOS Accessibility evidence for inert Drop-to-Target decisions.

The adapter requests only a frontmost target's role, bounded accessible title
or description, geometry, visibility, enablement, and ``AXDropEnabled``
capability flag. Accessible titles and descriptions can still contain private
user-provided labels, so they are returned only to the explicit caller and are
never logged, persisted, or placed in a receipt. A caller must supply the
source-kind/effect policy: macOS Accessibility has no generic way to prove
those semantics, so this module never guesses them.

It cannot start a drag, drop, click, focus an element, paste, invoke an AX
action, or request AX values, selected text, document contents, paths, or
payloads.
The native Mac Diagnostics preview may invoke this boundary after an explicit
user action. It remains transient and inert: no routine capture, action,
logging, or persistence surface exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import math
import sys
from typing import Any, Protocol

from drop_to_target import DropEffect, SourceKind


MAX_OBSERVED_ELEMENTS = 1_024
MAX_TARGETS = 128
MAX_DEPTH = 10
MAX_ACCESSIBLE_NAME_CHARS = 128


class SnapshotState(str, Enum):
    CAPTURED = "captured"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DropCapability:
    """Explicit non-AX policy required to form a resolver target fact."""

    accepted_kinds: tuple[SourceKind, ...]
    accepted_effects: tuple[DropEffect, ...]

    def __post_init__(self) -> None:
        for values, expected, label in (
            (self.accepted_kinds, SourceKind, "source kinds"),
            (self.accepted_effects, DropEffect, "effects"),
        ):
            if (not values or len(values) > len(expected)
                    or any(not isinstance(value, expected) for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"invalid drop capability {label}")


@dataclass(frozen=True, slots=True)
class SnapshotReceipt:
    """Content-free aggregate evidence from one read-only capture."""

    state: SnapshotState
    observed_elements: int
    emitted_targets: int
    skipped_elements: int
    truncated: bool


@dataclass(frozen=True, slots=True, repr=False)
class DropTargetCapture:
    """Resolver-compatible facts plus a content-free capture receipt."""

    targets: tuple[Mapping[str, Any], ...]
    receipt: SnapshotReceipt


class AccessibilityReader(Protocol):
    def trusted(self) -> bool: ...

    def root(self) -> object | None: ...

    def attribute(self, element: object, name: str) -> object | None: ...

    def geometry(self, element: object) -> tuple[float, float, float, float] \
            | None: ...


def _safe_name(value: object) -> str:
    if (not isinstance(value, str) or len(value) > MAX_ACCESSIBLE_NAME_CHARS
            or any(ord(character) < 32 for character in value)):
        return ""
    return value


def _geometry(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        x, y, width, height = (float(part) for part in value)
    except Exception:
        return None
    if (not all(math.isfinite(part) for part in (x, y, width, height))
            or not -1_000_000 <= x <= 1_000_000
            or not -1_000_000 <= y <= 1_000_000
            or not 0 < width <= 1_000_000 or not 0 < height <= 1_000_000):
        return None
    return x, y, width, height


def _attribute(reader: AccessibilityReader, element: object,
               name: str) -> object | None:
    try:
        return reader.attribute(element, name)
    except Exception:
        return None


def _read_geometry(reader: AccessibilityReader, element: object) -> object | None:
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
    return value


def _valid_policy(policy: Mapping[str, DropCapability]) -> bool:
    return (isinstance(policy, Mapping) and len(policy) <= 32
            and all(isinstance(role, str) and 1 <= len(role) <= 64
                    and isinstance(capability, DropCapability)
                    for role, capability in policy.items()))


def capture_drop_target_evidence(
    reader: AccessibilityReader,
    capability_policy: Mapping[str, DropCapability],
) -> DropTargetCapture:
    """Capture bounded, read-only facts usable by ``decide_drop_to_target``.

    ``capability_policy`` is deliberately separate from Accessibility data.
    It declares which known role types a future caller considers compatible;
    no emitted record claims that macOS proved source-kind or effect support.
    """
    if not _valid_policy(capability_policy):
        raise ValueError("invalid drop capability policy")
    try:
        trusted = reader.trusted()
    except Exception:
        trusted = None
    if trusted is not True:
        state = (SnapshotState.PERMISSION_DENIED if trusted is False
                 else SnapshotState.UNAVAILABLE)
        return DropTargetCapture((), SnapshotReceipt(state, 0, 0, 0, False))
    try:
        root = reader.root()
    except Exception:
        root = None
    if root is None:
        return DropTargetCapture((), SnapshotReceipt(
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
        if id(element) in seen:
            skipped += 1
            continue
        seen.add(id(element))
        observed += 1

        role = _attribute(reader, element, "role")
        capability = capability_policy.get(role) if isinstance(role, str) else None
        if capability is not None:
            title = _safe_name(_attribute(reader, element, "title"))
            label = _safe_name(_attribute(reader, element, "description"))
            geometry = _geometry(_read_geometry(reader, element))
            hidden = _attribute(reader, element, "hidden")
            enabled = _attribute(reader, element, "enabled")
            drop_enabled = _attribute(reader, element, "drop_enabled")
            if ((title.strip() or label.strip()) and geometry is not None
                    and isinstance(hidden, bool) and isinstance(enabled, bool)
                    and isinstance(drop_enabled, bool)):
                targets.append({
                    "schema_version": 1,
                    "target_id": f"ax-drop-{len(targets):04x}",
                    "title": title,
                    "label": label,
                    "accepted_kinds": [item.value for item in capability.accepted_kinds],
                    "accepted_effects": [item.value for item in capability.accepted_effects],
                    "visible": not hidden,
                    "enabled": enabled,
                    "drop_enabled": drop_enabled,
                })
                if len(targets) >= MAX_TARGETS:
                    truncated = bool(stack) or bool(_children(reader, element))
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
                stack.extend((child, depth + 1) for child in reversed(
                    children[:max(0, remaining)]))
            except Exception:
                skipped += 1

    return DropTargetCapture(tuple(targets), SnapshotReceipt(
        SnapshotState.CAPTURED, observed, len(targets), skipped, truncated))


class SystemMacAccessibilityReader:
    """Narrow read-only ApplicationServices adapter; it has no AX action API."""

    _ATTRIBUTE_NAMES = {
        "role": "AXRole", "title": "AXTitle", "description": "AXDescription",
        "hidden": "AXHidden", "enabled": "AXEnabled",
        "drop_enabled": "AXDropEnabled", "children": "AXChildren",
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
            system = self.services.AXUIElementCreateSystemWide()
            error, app = self.services.AXUIElementCopyAttributeValue(
                system, "AXFocusedApplication", None)
            return app if error == 0 else None
        except Exception:
            return None

    def attribute(self, element: object, name: str) -> object | None:
        attribute = self._ATTRIBUTE_NAMES.get(name)
        if attribute is None:
            raise ValueError("unsupported accessibility attribute")
        try:
            error, value = self.services.AXUIElementCopyAttributeValue(
                element, attribute, None)
            return value if error == 0 else None
        except Exception:
            return None

    def geometry(self, element: object) -> tuple[float, float, float, float] \
            | None:
        try:
            error, position = self.services.AXUIElementCopyAttributeValue(
                element, "AXPosition", None)
            if error != 0 or position is None:
                return None
            error, size = self.services.AXUIElementCopyAttributeValue(
                element, "AXSize", None)
            if error != 0 or size is None:
                return None
            ok, point = self.services.AXValueGetValue(position, 1, None)
            if not ok:
                return None
            ok, dimensions = self.services.AXValueGetValue(size, 2, None)
            if not ok:
                return None
            return float(point.x), float(point.y), float(dimensions.width), float(dimensions.height)
        except Exception:
            return None


def capture_frontmost_drop_target_evidence(
    capability_policy: Mapping[str, DropCapability],
) -> DropTargetCapture:
    """Capture the frontmost Mac app without prompting or performing actions."""
    if not _valid_policy(capability_policy):
        raise ValueError("invalid drop capability policy")
    if sys.platform != "darwin":
        return DropTargetCapture((), SnapshotReceipt(
            SnapshotState.UNAVAILABLE, 0, 0, 0, False))
    return capture_drop_target_evidence(
        SystemMacAccessibilityReader(), capability_policy)
