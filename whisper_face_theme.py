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
    error: Color


# Pastel clay: warm cream field, porcelain surfaces, warm green-gray ink.
# The brand emerald and butter amber stay in the family as soft accents;
# text-bearing uses swap in the AA inks the window derives from these.
LIGHT_PALETTE = ThemePalette(
    bg=_hex_color("#F5F1E8"),
    surface=_hex_color("#FFFDF8"),
    ink=_hex_color("#33403A"),
    ink_soft=_hex_color("#5E6C64"),
    line=_hex_color("#38332A"),
    brand=_hex_color("#4CC9A2"),
    accent=_hex_color("#F7C873"),
    teal=_hex_color("#A2EFDF"),
    error=_hex_color("#F0907E"),
)

DARK_PALETTE = ThemePalette(
    bg=_hex_color("#1E2420"),
    surface=_hex_color("#2A322C"),
    ink=_hex_color("#F2EEE4"),
    ink_soft=_hex_color("#A8B5AC"),
    line=_hex_color("#141814"),
    brand=_hex_color("#4CC9A2"),
    accent=_hex_color("#F7C873"),
    teal=_hex_color("#A2EFDF"),
    error=_hex_color("#F0907E"),
)

# Chibi-clay pastels: every chip color is the old hue relaxed toward cream so
# the characters read as soft vinyl toys instead of stickers.
#
# The four newest chips claim the hue families the first ten left empty --
# leaf green, rose, cocoa, and sky -- because a chip has to be nameable at a
# glance in a fourteen-wide picker. Every pair in the map is at least as far
# apart in CIELAB as the roster's own closest pair (wolf/panda, dE 12.5), and
# every chip keeps the dark menu-bar silhouette it carries in the site footer
# above 4.5:1.
FACE_CHIP_COLORS: Mapping[str, Color] = {
    "fox": _hex_color("#FFB899"),
    "bear": _hex_color("#FDD779"),
    "owl": _hex_color("#A2EFDF"),
    "parrot": _hex_color("#89E2BD"),
    "cat": _hex_color("#DDD1F7"),
    "dog": _hex_color("#EAC79C"),
    "wolf": _hex_color("#C4C9CF"),
    "pig": _hex_color("#FACAD2"),
    "panda": _hex_color("#EBEAEB"),
    "tiger": _hex_color("#FDC589"),
    "frog": _hex_color("#BCE283"),
    "rabbit": _hex_color("#F6A2BC"),
    "hedgehog": _hex_color("#BE9E7F"),
    "penguin": _hex_color("#86CDF0"),
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
class SurfaceSpec:
    radius: float
    border_width: float
    shadow_x: float
    shadow_y: float


# AppKit and the site translate these geometry tokens into native layers and
# CSS. Work surfaces remain quiet; only playful objects carry the full sticker
# offset. Radii sit a step larger than classic macOS so surfaces read as soft
# clay next to the chibi characters.
SURFACE_SPECS: Mapping[str, SurfaceSpec] = {
    "work": SurfaceSpec(16.0, 1.0, 0.0, 0.0),
    "card": SurfaceSpec(18.0, 1.5, 0.0, 0.0),
    "playful": SurfaceSpec(20.0, 2.0, 5.0, -5.0),
    "control": SurfaceSpec(14.0, 1.5, 2.0, -2.0),
}


@dataclass(frozen=True)
class HudPresentation:
    eyebrow: str
    confidence: str
    accessibility_value: str
    accent: str


# The palette's raw emerald and amber fail WCAG AA as text on light
# surfaces, so text-bearing uses swap in these darkened inks while fills
# and dark-appearance text keep the shared palette hues. Contrast is
# enforced by tests against LIGHT_PALETTE.bg and .surface.
BRAND_TEXT_ON_LIGHT: Color = (0.043, 0.478, 0.341)   # ≈ #0B7A57
AMBER_TEXT_ON_LIGHT: Color = (0.478, 0.310, 0.0)     # ≈ #7A4F00


def relative_luminance(color: Color) -> float:
    """WCAG 2.x relative luminance of a linear-intent sRGB color."""
    channels = []
    for value in color:
        if value <= 0.03928:
            channels.append(value / 12.92)
        else:
            channels.append(((value + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: Color, background: Color) -> float:
    """WCAG contrast ratio between two theme colors."""
    lighter = max(relative_luminance(foreground),
                  relative_luminance(background))
    darker = min(relative_luminance(foreground),
                 relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


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
