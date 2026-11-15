"""Shared Whisper Face visual language for native UI surfaces.

This module stays platform-independent so motion, color, copy, and
accessibility contracts can be tested without loading AppKit. Native views
translate ``MOTION_SPECS`` to Core Animation springs.
"""

from __future__ import annotations

import math
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

# Pastel grounds: every chip color is a hue relaxed toward cream. The sticker
# characters wear these same hues at full strength, from their own palette in
# whisper_face_characters; the chip stays soft so the face standing on it is
# the loud thing.
#
# Hue does the naming, because a chip has to be findable at a glance in a
# sixteen-chip picker: every animal through penguin claimed a hue family the
# roster had left empty -- leaf green, rose, cocoa, sky -- which kept each
# pair at least as far apart in CIELAB as wolf/panda, dE 12.5. The English
# cream goldens are the sanctioned exception and break that floor twice:
# pickles/olive sit dE 6.3 apart, and olive lands dE 11.0 from panda. Being
# litter-mates is the point, so the picker separates them by art and tooltip
# instead of by color. Every chip still carries the dark menu-bar silhouette
# it wears in the site footer above 4.5:1 (hedgehog is tightest, 5.7:1).
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
    # The two English cream goldens are deliberately near-twins: they are a
    # real pair of litter-mates, told apart by accessories, not by palette.
    "pickles": _hex_color("#F6E8C9"),
    "olive": _hex_color("#F2E7D4"),
}


@dataclass(frozen=True)
class TypeSpec:
    size: float
    weight: float
    # The HUD roles predate these two fields and are always drawn rounded by
    # their renderer, so the defaults describe them. Window roles say
    # explicitly which voice they speak in.
    rounded: bool = True
    tabular: bool = False


# AppKit's NSFontWeight values, named so a role reads as a weight rather
# than as a number. They are the same floats ``NSFontWeightBold`` and its
# siblings carry, so a renderer can hand them straight to
# ``systemFontOfSize:weight:``.
WEIGHT_REGULAR = 0.0
WEIGHT_MEDIUM = 0.23
WEIGHT_SEMIBOLD = 0.30
WEIGHT_BOLD = 0.40
WEIGHT_HEAVY = 0.56

# SF Rounded is applied by native renderers. Body/data views remain on normal
# system type; only face/status chrome uses these personality-bearing tokens.
#
# The window roles below the HUD ones belong to the new look. Rounded is the
# brand's voice and is used only where the character or the brand speaks:
# page titles, section headers, the bubble headline, stat numbers, chunky
# buttons and key caps. Rows stay on SF Pro. Row titles are 13pt, the size
# macOS uses for body and row text, rather than the mock-up's 13.5.
TYPE_SPECS: Mapping[str, TypeSpec] = {
    "hud_eyebrow": TypeSpec(9.5, 0.65),
    "hud_confidence": TypeSpec(10.5, 0.35),
    "hud_caption": TypeSpec(11.5, 0.28),
    "page_title": TypeSpec(17.0, WEIGHT_BOLD),
    "section_header": TypeSpec(13.0, WEIGHT_BOLD),
    "bubble_headline": TypeSpec(27.0, WEIGHT_HEAVY),
    "stat_number": TypeSpec(25.0, WEIGHT_HEAVY, tabular=True),
    "stat_label": TypeSpec(12.5, WEIGHT_SEMIBOLD, rounded=False),
    "chunky_button": TypeSpec(14.0, WEIGHT_HEAVY),
    "chunky_button_small": TypeSpec(13.0, WEIGHT_HEAVY),
    "key_cap": TypeSpec(12.5, WEIGHT_BOLD),
    "row_title": TypeSpec(13.0, WEIGHT_SEMIBOLD, rounded=False),
    "row_caption": TypeSpec(12.0, WEIGHT_REGULAR, rounded=False),
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


# ---------------------------------------------------------------------------
# The new look: native structure, loud brand moments.
#
# Everything a Mac user operates stays system-standard; everything that
# carries the brand (the stage, the bubble, the primary action, the stat
# stickers, key caps, the selected face chip) is ink-outlined with a hard
# shadow. The tokens below are that vocabulary. They live here, away from
# AppKit, so their contrast and geometry are tested headlessly and no view
# code has to carry a hex value.
# ---------------------------------------------------------------------------

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Shade:
    """One colour at one opacity."""

    color: Color
    alpha: float = 1.0

    def over(self, background: Color) -> Color:
        """The opaque colour this shade makes when laid on ``background``."""
        return tuple(
            self.alpha * top + (1.0 - self.alpha) * under
            for top, under in zip(self.color, background))


@dataclass(frozen=True)
class ColorToken:
    """A named colour with a value for every way the system can ask for it.

    ``light_contrast`` and ``dark_contrast`` answer Increase Contrast. A
    token that is already strong enough leaves them unset and answers with
    its ordinary value, so callers never have to ask whether one exists.
    """

    light: Shade
    dark: Shade
    light_contrast: Shade | None = None
    dark_contrast: Shade | None = None

    def shade(self, dark: bool, increased_contrast: bool = False) -> Shade:
        if increased_contrast:
            stronger = self.dark_contrast if dark else self.light_contrast
            if stronger is not None:
                return stronger
        return self.dark if dark else self.light


def _token(light: str, dark: str, *, light_contrast: str = "",
           dark_contrast: str = "") -> ColorToken:
    return ColorToken(
        Shade(_hex_color(light)), Shade(_hex_color(dark)),
        Shade(_hex_color(light_contrast)) if light_contrast else None,
        Shade(_hex_color(dark_contrast)) if dark_contrast else None)


# The characters' eye colour. Text on amber and on the light sticker tints
# is drawn in it, in both appearances, because those fills do not change
# enough between light and dark to need a different ink.
STICKER_INK: Color = _hex_color("#2A211C")

# Light and dark values come from the design contract. Increase Contrast
# values are added wherever the colour is an edge or carries text:
#
# - ``ink_outline`` in the dark appearance is darker than the window behind
#   it (1.1:1), which is the sticker look but not an edge. Increase Contrast
#   swaps in the light ink so every outlined object gets a 13:1 edge.
# - ``hairline`` is a whisper by design; Increase Contrast lifts it to 3:1.
# - ``switch_on`` is 2.7:1 on the light surface, like the system's own
#   green; Increase Contrast darkens it past 4:1.
COLOR_TOKENS: Mapping[str, ColorToken] = {
    "ink_outline": ColorToken(
        Shade(LIGHT_PALETTE.line), Shade(DARK_PALETTE.line),
        Shade(_hex_color("#14110D")), Shade(DARK_PALETTE.ink)),
    "hairline": ColorToken(
        Shade(LIGHT_PALETTE.line, 0.15), Shade(DARK_PALETTE.ink, 0.13),
        Shade(LIGHT_PALETTE.line, 0.60), Shade(DARK_PALETTE.ink, 0.45)),
    "stage_amber": _token("#F6BE55", "#D9A23F"),
    "stage_emerald": _token("#43C79C", "#2FA37E"),
    "switch_on": _token("#22B07F", "#22B07F", light_contrast="#14805B"),
    "brand_ink": _token("#0B7A57", "#7FE3C2",
                        light_contrast="#075C41", dark_contrast="#B8F5E1"),
    "sticker_teal": _token("#BFF3E7", "#2F5E55"),
    "sticker_butter": _token("#FBE3B0", "#5E5132"),
    "sticker_rose": _token("#F8C4B8", "#62423B"),
    "sticker_ink": ColorToken(Shade(STICKER_INK), Shade(DARK_PALETTE.ink)),
    "on_amber_ink": ColorToken(Shade(STICKER_INK), Shade(STICKER_INK)),
    "chip_rest": ColorToken(
        Shade(LIGHT_PALETTE.ink, 0.05), Shade(DARK_PALETTE.ink, 0.05),
        Shade(LIGHT_PALETTE.ink, 0.12), Shade(DARK_PALETTE.ink, 0.14)),
    "sidebar_selection": ColorToken(
        Shade(LIGHT_PALETTE.brand, 0.38), Shade(DARK_PALETTE.brand, 0.38),
        Shade(LIGHT_PALETTE.brand, 0.50), None),
}


def color_token(name: str, dark: bool,
                increased_contrast: bool = False) -> Shade:
    """The shade a named token takes in one appearance."""
    return COLOR_TOKENS[name].shade(dark, increased_contrast)


# The three stat stickers, in reading order: tint, then tilt in degrees.
# Tilts are in the screen's sense, positive clockwise, as the mock-ups were
# drawn; AppKit's ``frameCenterRotation`` turns the other way.
STICKER_TINTS: tuple[str, str, str] = (
    "sticker_teal", "sticker_butter", "sticker_rose")
STICKER_TILTS: tuple[float, float, float] = (-1.4, 1.0, -0.6)
# The sticker's label is the sticker ink at this opacity, which keeps it
# above 4.5:1 on all three tints in both appearances.
STICKER_LABEL_ALPHA = 0.78

# The stage sits behind the selected face and is chosen to contrast with it:
# emerald behind the warm and pink animals, amber behind the cool and
# neutral ones.
STAGE_FOR_FACE: Mapping[str, str] = {
    "fox": "stage_emerald",
    "bear": "stage_emerald",
    "tiger": "stage_emerald",
    "dog": "stage_emerald",
    "hedgehog": "stage_emerald",
    "pickles": "stage_emerald",
    "olive": "stage_emerald",
    "pig": "stage_emerald",
    "rabbit": "stage_emerald",
    "parrot": "stage_amber",
    "frog": "stage_amber",
    "owl": "stage_amber",
    "cat": "stage_amber",
    "wolf": "stage_amber",
    "panda": "stage_amber",
    "penguin": "stage_amber",
}


def stage_token_for_face(face: str) -> str:
    """Name of the stage colour token that sets off ``face``."""
    return STAGE_FOR_FACE.get(face, "stage_amber")


@dataclass(frozen=True)
class StageCircle:
    """One soft circle on the stage, placed from the edges it hugs.

    ``from_right`` and ``from_bottom`` choose which edges ``inset_x`` and
    ``inset_y`` are measured from; a negative inset hangs the circle off
    that edge. ``lighten`` picks a white wash, otherwise a black one.
    """

    diameter: float
    inset_x: float
    inset_y: float
    from_right: bool
    from_bottom: bool
    lighten: bool
    alpha: float


STAGE_CIRCLES: tuple[StageCircle, ...] = (
    StageCircle(300.0, -90.0, -110.0, False, False, True, 0.20),
    StageCircle(220.0, -60.0, -110.0, True, True, False, 0.09),
    StageCircle(90.0, 120.0, -46.0, True, False, True, 0.16),
)
STAGE_HEIGHT = 246.0
STAGE_FACE_SIZE = 200.0
# Screen sense, positive clockwise: the face leans a little to its right.
STAGE_FACE_TILT = -4.0
STAGE_PADDING_LEFT = 12.0
STAGE_PADDING_RIGHT = 26.0
STAGE_PADDING_Y = 18.0
STAGE_GAP = 8.0
STAGE_CONTENT_MIN_WIDTH = 160.0
STAGE_FACE_MIN_SIZE = 96.0


@dataclass(frozen=True)
class BrandShape:
    """Geometry of one ink-outlined object.

    A hard shadow has no blur, is drawn in the ink outline colour, and sits
    ``shadow_right`` and ``shadow_down`` points from the object it belongs
    to. ``height`` and ``padding_x`` are set for the objects that size
    themselves around a line of text.
    """

    radius: float
    outline: float
    shadow_right: float = 0.0
    shadow_down: float = 0.0
    height: float = 0.0
    padding_x: float = 0.0


BRAND_SHAPES: Mapping[str, BrandShape] = {
    "group": BrandShape(13.0, 1.0),
    "stage": BrandShape(24.0, 2.5, 5.0, 6.0, STAGE_HEIGHT),
    "bubble": BrandShape(22.0, 2.5, 4.0, 5.0),
    "chunky_button": BrandShape(12.0, 2.0, 3.0, 4.0, 36.0, 16.0),
    "chunky_button_small": BrandShape(10.0, 2.0, 2.0, 3.0, 30.0, 12.0),
    "sticker": BrandShape(16.0, 2.0, 3.0, 4.0, 0.0, 14.0),
    "key_cap": BrandShape(7.0, 1.5, 0.0, 2.0, 21.0, 7.0),
    "face_chip": BrandShape(16.0, 2.0, 2.0, 3.0),
}
# A disabled brand control keeps its shape, loses its shadow and fades.
DISABLED_ALPHA = 0.45


def hard_shadow_frames(width: float, height: float, shape: BrandShape, *,
                       pressed: bool = False) -> tuple[Rect, Rect]:
    """Where an object and its hard shadow sit inside a ``width`` by
    ``height`` box, as ``(body, shadow)`` rectangles measured from the
    top-left corner.

    The box is the whole footprint: the body is smaller than it by the
    shadow offset, so nothing is ever drawn outside the view that owns it.
    Pressed, the body travels onto its shadow and leaves a one-point
    sliver, which is how a sticker looks when a finger pushes it down.
    """
    right, down = shape.shadow_right, shape.shadow_down
    body_width = max(0.0, width - right)
    body_height = max(0.0, height - down)
    travel_x = max(0.0, right - 1.0) if pressed else 0.0
    travel_y = max(0.0, down - 1.0) if pressed else 0.0
    return ((travel_x, travel_y, body_width, body_height),
            (right, down, body_width, body_height))


def hard_shadow_footprint(body_width: float, body_height: float,
                          shape: BrandShape) -> tuple[float, float]:
    """Size of the box an object of this body size needs, shadow included."""
    return (body_width + shape.shadow_right, body_height + shape.shadow_down)


def tilted_footprint(width: float, height: float,
                     degrees: float) -> tuple[float, float]:
    """Size of the upright box that holds a rectangle turned ``degrees``."""
    angle = math.radians(abs(degrees))
    return (width * math.cos(angle) + height * math.sin(angle),
            width * math.sin(angle) + height * math.cos(angle))


def tilted_inner_size(width: float, height: float,
                      degrees: float) -> tuple[float, float]:
    """Size of the largest rectangle that, turned ``degrees``, still fits an
    upright ``width`` by ``height`` box: the inverse of ``tilted_footprint``.
    """
    angle = math.radians(abs(degrees))
    cos, sin = math.cos(angle), math.sin(angle)
    spread = cos * cos - sin * sin
    if spread <= 0.0:
        return (0.0, 0.0)
    return (max(0.0, (width * cos - height * sin) / spread),
            max(0.0, (height * cos - width * sin) / spread))


def stage_layout(width: float, height: float, *,
                 face_size: float = STAGE_FACE_SIZE) -> tuple[Rect, Rect]:
    """Where the face and the content sit on a stage of this footprint.

    Returns ``(face, content)`` measured from the top-left corner of the
    stage's footprint, both inside its body and clear of its hard shadow.
    The face keeps a square on the left, centred on the body; the content
    takes the rest. On a stage too narrow for both, the face shrinks before
    the content loses its last ``STAGE_CONTENT_MIN_WIDTH`` points, but never
    below ``STAGE_FACE_MIN_SIZE``: the face is the point of the stage.
    """
    body, _shadow = hard_shadow_frames(width, height, BRAND_SHAPES["stage"])
    _x, _y, body_width, body_height = body
    inner_height = max(0.0, body_height - 2.0 * STAGE_PADDING_Y)
    room = (body_width - STAGE_PADDING_LEFT - STAGE_PADDING_RIGHT
            - STAGE_GAP - STAGE_CONTENT_MIN_WIDTH)
    side = max(0.0, min(
        face_size, body_height,
        max(room, min(face_size, STAGE_FACE_MIN_SIZE))))
    face = (STAGE_PADDING_LEFT, (body_height - side) / 2.0, side, side)
    content_x = STAGE_PADDING_LEFT + side + STAGE_GAP
    content = (content_x, STAGE_PADDING_Y,
               max(0.0, body_width - content_x - STAGE_PADDING_RIGHT),
               inner_height)
    return face, content
