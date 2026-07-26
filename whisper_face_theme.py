"""Shared Whisper Face visual language for native UI surfaces.

This module stays platform-independent so motion, color, copy, and
accessibility contracts can be tested without loading AppKit. Native views
translate ``MOTION_SPECS`` to Core Animation springs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


Color = tuple[float, float, float]


def _hex_color(value: str) -> Color:
    value = value.removeprefix("#")
    if len(value) != 6:
        raise ValueError("theme colors must use six-digit hex values")
    return tuple(int(value[index:index + 2], 16) / 255.0
                 for index in (0, 2, 4))


@dataclass(frozen=True)
class ThemePalette:
    bg: Color
    surface: Color
    ink: Color
    ink_soft: Color
    line: Color
    brand: Color
    accent: Color
    teal: Color


LIGHT_PALETTE = ThemePalette(
    bg=_hex_color("#E8FBF0"),
    surface=_hex_color("#FFFFFF"),
    ink=_hex_color("#0E2A24"),
    ink_soft=_hex_color("#3C5C51"),
    line=_hex_color("#0C221D"),
    brand=_hex_color("#10B981"),
    accent=_hex_color("#FBBF24"),
    teal=_hex_color("#5EEAD4"),
)

DARK_PALETTE = ThemePalette(
    bg=_hex_color("#0A231D"),
    surface=_hex_color("#123029"),
    ink=_hex_color("#E8FBF0"),
    ink_soft=_hex_color("#9FC9BB"),
    line=_hex_color("#05130F"),
    brand=_hex_color("#10B981"),
    accent=_hex_color("#FBBF24"),
    teal=_hex_color("#5EEAD4"),
)

FACE_CHIP_COLORS: Mapping[str, Color] = {
    "fox": _hex_color("#FF8A5B"),
    "bear": _hex_color("#FBBF24"),
    "owl": _hex_color("#5EEAD4"),
    "parrot": _hex_color("#34D399"),
    "cat": _hex_color("#C4B5FD"),
    "dog": _hex_color("#DAA560"),
    "wolf": _hex_color("#9AA7B8"),
    "pig": _hex_color("#F6A9BD"),
    "panda": _hex_color("#DCE0E8"),
    "tiger": _hex_color("#FBA13F"),
}


@dataclass(frozen=True)
class TypeSpec:
    size: float
    weight: float


# SF Rounded is applied by native renderers. Body/data views remain on normal
# system type; only face/status chrome uses these personality-bearing tokens.
TYPE_SPECS: Mapping[str, TypeSpec] = {
    "hud_eyebrow": TypeSpec(9.5, 0.65),
    "hud_confidence": TypeSpec(10.5, 0.35),
    "hud_caption": TypeSpec(11.5, 0.28),
}


@dataclass(frozen=True)
class MotionSpec:
    mass: float
    stiffness: float
    damping: float
    initial_velocity: float
    duration: float
    squash_x: float
    squash_y: float


# Named once, translated by AppKit surfaces to CASpringAnimation. Values keep
# movement quick enough for a frequently used tool while retaining one playful
# overshoot. Reduce Motion callers bypass every spec.
MOTION_SPECS: Mapping[str, MotionSpec] = {
    "press": MotionSpec(1.0, 420.0, 28.0, 0.0, 0.24, 1.05, 0.94),
    "release": MotionSpec(1.0, 360.0, 22.0, 0.5, 0.34, 0.96, 1.05),
    "wobble": MotionSpec(1.0, 330.0, 16.0, 0.8, 0.46, 1.07, 0.93),
    "pop": MotionSpec(1.0, 390.0, 20.0, 0.4, 0.38, 0.90, 0.90),
}


@dataclass(frozen=True)
class HudPresentation:
    eyebrow: str
    confidence: str
    accessibility_value: str
    accent: str


def palette_for_appearance(dark: bool) -> ThemePalette:
    return DARK_PALETTE if dark else LIGHT_PALETTE


def normalize_confidence(confidence: object) -> float | None:
    if isinstance(confidence, bool):
        return None
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return max(0.0, min(1.0, value))


def hud_presentation(
        mode: str,
        caption: str,
        confidence: object = None,
        *,
        stable_prefix: bool = False,
) -> HudPresentation:
    """Return honest compact HUD copy without exposing transcript elsewhere."""
    normalized = normalize_confidence(confidence)
    confidence_copy = (
        f"Recognition {normalized:.0%}" if normalized is not None else "")
    clean_caption = " ".join(str(caption or "").split())

    if mode == "error":
        eyebrow = "TRY AGAIN"
        confidence_copy = ""
        accent = "error"
        phase = "Dictation needs another try"
    elif mode == "processing":
        eyebrow = "TIDYING UP"
        accent = "accent"
        phase = "Processing dictation"
    elif stable_prefix and clean_caption:
        eyebrow = "HEARD YOU"
        accent = "brand"
        phase = "Listening; stable words available"
    else:
        eyebrow = "LISTENING"
        accent = "brand"
        phase = "Listening"

    pieces = [phase]
    if confidence_copy:
        pieces.append(confidence_copy)
    if clean_caption:
        pieces.append(clean_caption)
    return HudPresentation(
        eyebrow=eyebrow,
        confidence=confidence_copy,
        accessibility_value=". ".join(pieces),
        accent=accent,
    )


def jelly_face_scale(
        level: object,
        *,
        processing: bool = False,
        reduce_motion: bool = False,
) -> tuple[float, float]:
    """Small live squash/stretch; identity when motion must stop."""
    if reduce_motion or processing:
        return (1.0, 1.0)
    try:
        energy = float(level)
    except (TypeError, ValueError):
        energy = 0.0
    if energy != energy or energy in (float("inf"), float("-inf")):
        energy = 0.0
    energy = max(0.0, min(1.0, energy))
    return (1.0 + energy * 0.055, 1.0 - energy * 0.042)
