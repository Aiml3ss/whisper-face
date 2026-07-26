"""Shared Whisper Face character geometry for every colored surface.

The ten faces used to be drawn twice: once as AppKit Bezier calls inside the
recording HUD, and once as flat silhouettes hand-authored under ``icons/faces``.
Only the HUD ever showed the colored character, so the app window and the
onboarding hero rendered a menu-bar glyph blown up past the size it was drawn
for.

This module owns the character spec once, as an ordered list of primitive draw
ops in a 256x256 y-down design box.  ``dictate.py`` replays those ops through
Core Graphics; ``scripts/generate_face_art.py`` replays the same ops into SVG.
Both renderers therefore move together by construction.  It stays free of
AppKit so the geometry can be tested without displaying a window.

The flat template silhouettes under ``icons/faces`` are deliberately *not*
generated from here.  A menu-bar template image is tinted by the system and has
to survive 18 points, so it is drawn as its own artwork.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from whisper_face_theme import FACE_CHIP_COLORS

Color = tuple[float, float, float]
Point = tuple[float, float]

# Every character is drawn inside this square, y down, before the HUD scales
# and squashes it.
DESIGN_SIZE = 256.0

EMERALD = FACE_CHIP_COLORS["parrot"]     # #34d399
DEEP = (0.016, 0.471, 0.341)             # #047857
BEAK_UP = (0.984, 0.749, 0.141)          # #fbbf24
BEAK_LO = (0.941, 0.659, 0.118)          # #f0a81e
DARK_EYE = (0.043, 0.231, 0.196)         # #0b3b32
MOUTH = (0.024, 0.145, 0.122)            # #06251f
MINT = FACE_CHIP_COLORS["owl"]           # #5eead4
CATCH = (0.918, 1.000, 0.965)            # #eafff6
TONGUE = (0.941, 0.447, 0.525)           # #f07286


@dataclass(frozen=True)
class Ellipse:
    """Axis-aligned oval, given as its bounding box."""
    x: float
    y: float
    w: float
    h: float
    color: Color
    alpha: float = 1.0


@dataclass(frozen=True)
class Polygon:
    points: tuple[Point, ...]
    color: Color
    alpha: float = 1.0


@dataclass(frozen=True)
class Stroke:
    """Round-capped polyline."""
    points: tuple[Point, ...]
    color: Color
    width: float
    alpha: float = 1.0


@dataclass(frozen=True)
class RoundedRect:
    x: float
    y: float
    w: float
    h: float
    radius: float
    color: Color
    alpha: float = 1.0


@dataclass(frozen=True)
class Arc:
    cx: float
    cy: float
    radius: float
    start_degrees: float
    end_degrees: float
    color: Color
    width: float
    alpha: float = 1.0


@dataclass(frozen=True)
class Curve:
    """Closed path: a start point plus cubic segments as (cp1, cp2, end)."""
    start: Point
    segments: tuple[tuple[Point, Point, Point], ...]
    color: Color
    alpha: float = 1.0


Op = Ellipse | Polygon | Stroke | RoundedRect | Arc | Curve


@dataclass(frozen=True)
class CharacterStyle:
    """One companion character.

    ``ears`` picks the silhouette: ``pointed`` for the feline and vulpine
    faces, ``round`` for the bear family, ``floppy`` for the dog, whose flat
    silhouette has always had hanging ears.  ``snout`` gives the pig the
    feature that separates it from a pink bear.
    """
    head: Color
    deep: Color
    muzzle: Color
    ears: str = "pointed"
    whiskers: bool = False
    patches: bool = False
    stripes: bool = False
    snout: bool = False


COMPANIONS: Mapping[str, CharacterStyle] = {
    "fox": CharacterStyle(
        head=FACE_CHIP_COLORS["fox"],
        deep=(0.706, 0.231, 0.075),
        muzzle=(1.000, 0.878, 0.702),
    ),
    "cat": CharacterStyle(
        head=FACE_CHIP_COLORS["cat"],
        deep=(0.188, 0.349, 0.573),
        muzzle=(0.824, 0.914, 1.000),
        whiskers=True,
    ),
    "bear": CharacterStyle(
        head=FACE_CHIP_COLORS["bear"],
        deep=(0.373, 0.220, 0.133),
        muzzle=(0.890, 0.710, 0.514),
        ears="round",
    ),
    "dog": CharacterStyle(
        head=FACE_CHIP_COLORS["dog"],
        deep=(0.573, 0.396, 0.196),
        muzzle=(0.988, 0.925, 0.816),
        ears="floppy",
    ),
    "wolf": CharacterStyle(
        head=FACE_CHIP_COLORS["wolf"],
        deep=(0.310, 0.345, 0.400),
        muzzle=(0.831, 0.859, 0.902),
    ),
    "pig": CharacterStyle(
        head=FACE_CHIP_COLORS["pig"],
        deep=(0.804, 0.435, 0.533),
        muzzle=(1.000, 0.859, 0.878),
        ears="round",
        snout=True,
    ),
    "panda": CharacterStyle(
        head=FACE_CHIP_COLORS["panda"],
        deep=(0.129, 0.145, 0.161),
        muzzle=(1.000, 1.000, 1.000),
        ears="round",
        patches=True,
    ),
    "tiger": CharacterStyle(
        head=FACE_CHIP_COLORS["tiger"],
        deep=(0.816, 0.404, 0.098),
        muzzle=(1.000, 0.910, 0.784),
        stripes=True,
    ),
}

# Parrot and owl keep bespoke geometry; the other eight share one template.
FACE_ORDER: tuple[str, ...] = (
    "parrot", "fox", "bear", "owl", "cat", "dog", "wolf", "pig", "panda",
    "tiger",
)


def _whisper_ops(level: float) -> list[Op]:
    """The two mint speech puffs that trail off the character's shoulder."""
    glow = 0.35 + level * 0.6
    ops: list[Op] = [
        Stroke(((212, 62), (232, 52)), MINT, 12.0, glow),
        Stroke(((224, 88), (246, 84)), MINT, 12.0, glow * 0.6),
        Ellipse(232, 32, 16, 16, MINT, glow * 0.55),
    ]
    return ops


def _eye_ops(left: float, right: float, y: float, w: float, h: float,
             catch_dx: float, catch_y: float, catch_w: float,
             catch_h: float) -> list[Op]:
    ops: list[Op] = [Ellipse(x, y, w, h, DARK_EYE) for x in (left, right)]
    ops += [Ellipse(x + catch_dx, catch_y, catch_w, catch_h, CATCH)
            for x in (left, right)]
    return ops


def _companion_ops(face: str, mouth: float, level: float) -> list[Op]:
    style = COMPANIONS.get(face, COMPANIONS["fox"])
    ops: list[Op] = []

    # Ears sit behind the shared rounded head.
    if style.ears == "round":
        ops += [Ellipse(40, 42, 58, 58, style.deep),
                Ellipse(158, 42, 58, 58, style.deep)]
    elif style.ears == "floppy":
        ops += [Ellipse(16, 80, 56, 104, style.deep),
                Ellipse(184, 80, 56, 104, style.deep)]
    else:
        ops += [
            Polygon(((42, 96), (55, 28), (105, 78)), style.deep),
            Polygon(((151, 78), (201, 28), (214, 96)), style.deep),
            Polygon(((58, 78), (63, 48), (88, 75)), style.muzzle),
            Polygon(((168, 75), (193, 48), (198, 78)), style.muzzle),
        ]

    ops.append(Ellipse(34, 55, 188, 172, style.head))

    # Cheeks keep the soft, toy-like language and leave a clean lip-sync
    # cavity between them.
    ops += [Ellipse(61, 123, 78, 68, style.muzzle),
            Ellipse(117, 123, 78, 68, style.muzzle)]

    if style.patches:
        ops += [Ellipse(x, 90, 31, 36, style.deep) for x in (76, 150)]

    ops += _eye_ops(82, 156, 96, 19, 23, 10, 99, 6, 7)

    if style.stripes:
        for x0, y0, x1, y1 in ((128, 60, 128, 90),
                               (104, 66, 112, 92),
                               (152, 66, 144, 92)):
            ops.append(Stroke(((x0, y0), (x1, y1)), style.deep, 6.0, 0.85))

    if style.snout:
        # A flat disc with two nostrils, and the mouth pushed below it. Without
        # this the pig is a pink bear.
        ops.append(Ellipse(97, 134, 62, 46, style.deep))
        ops.append(Ellipse(103, 139, 50, 34, style.muzzle))
        ops += [Ellipse(x, 148, 11, 15, style.deep) for x in (111, 134)]
        mouth_y = 184.0
    else:
        ops.append(Polygon(((116, 137), (140, 137), (128, 150)), style.deep))
        mouth_y = 153.0

    ops.append(RoundedRect(111, mouth_y, 34, 5.0 + mouth * 28.0, 15, MOUTH))
    if mouth > 0.32:
        ops.append(Ellipse(119, mouth_y + 7 + mouth * 10, 18, 9, TONGUE))

    if style.whiskers:
        for y, dy in ((149, -5), (158, 0), (167, 5)):
            ops.append(Stroke(((91, y), (39, y + dy)), style.deep, 3.0, 0.75))
            ops.append(Stroke(((165, y), (217, y + dy)), style.deep, 3.0, 0.75))

    return ops + _whisper_ops(level)


def _parrot_ops(mouth: float, level: float) -> list[Op]:
    """Emerald head, gold crest, hooked beak whose lower mandible drops."""
    ops: list[Op] = [
        Polygon(((120, 60), (136, 60), (128, 22)), BEAK_UP),
        Polygon(((104, 68), (120, 60), (112, 30)), DEEP),
        Polygon(((136, 60), (152, 68), (144, 30)), DEEP),
        Ellipse(34, 55, 188, 172, EMERALD),
    ]
    ops += _eye_ops(82, 156, 96, 19, 23, 10, 99, 6, 7)
    ops.append(Polygon(((114, 150), (142, 150), (128, 170 + mouth * 16)),
                       MOUTH))
    ops.append(Curve(
        (106, 148),
        (((110, 178), (120, 188), (128, 188)),
         ((136, 188), (146, 178), (150, 148))),
        BEAK_UP,
    ))
    drop = mouth * 12.0
    ops.append(Curve(
        (116, 168 + drop),
        (((122, 172 + drop), (134, 172 + drop), (140, 168 + drop)),
         ((132, 184 + drop), (120, 184 + drop), (116, 168 + drop))),
        BEAK_LO,
    ))
    return ops + _whisper_ops(level)


def _owl_ops(mouth: float, level: float) -> list[Op]:
    purple = FACE_CHIP_COLORS["owl"]
    deep = (0.255, 0.200, 0.506)
    cream = (0.890, 0.855, 1.000)
    ops: list[Op] = [
        Polygon(((39, 104), (62, 31), (104, 79)), deep),
        Polygon(((152, 79), (194, 31), (217, 104)), deep),
        Ellipse(32, 52, 192, 180, purple),
    ]
    ops += [Ellipse(x, 88, 70, 70, cream) for x in (57, 129)]
    ops += _eye_ops(82, 154, 108, 24, 28, 12, 111, 7, 8)
    ops.append(Polygon(((111, 151), (145, 151), (128, 167 + mouth * 16)),
                       MOUTH))
    ops.append(Polygon(((105, 145), (151, 145), (128, 163)), BEAK_UP))
    drop = mouth * 13.0
    ops.append(Polygon(((111, 164 + drop), (145, 164 + drop),
                        (128, 178 + drop)), BEAK_LO))
    # Chest crescent gives the owl the parrot's single-swoosh signature
    # without making the two characters read as the same bird.
    ops.append(Arc(128, 165, 45, 20, 160, deep, 13.0, 0.8))
    return ops + _whisper_ops(level)


def character_ops(face: str, mouth: float, level: float) -> tuple[Op, ...]:
    """Ordered draw ops for ``face`` at a given mouth opening and mic level.

    ``mouth`` and ``level`` are both 0..1.  Animation offsets are baked into
    the returned coordinates so a renderer never has to manage a transform
    stack.
    """
    mouth = max(0.0, min(1.0, float(mouth)))
    level = max(0.0, min(1.0, float(level)))
    if face == "parrot":
        ops = _parrot_ops(mouth, level)
    elif face == "owl":
        ops = _owl_ops(mouth, level)
    else:
        ops = _companion_ops(face, mouth, level)
    return tuple(ops)


def _hex(color: Color) -> str:
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(channel * 255))) for channel in color)


def _svg_op(op: Op) -> str:
    if isinstance(op, Ellipse):
        fill = "" if op.alpha >= 1.0 else f' fill-opacity="{op.alpha:.3f}"'
        return (f'<ellipse cx="{op.x + op.w / 2:g}" cy="{op.y + op.h / 2:g}"'
                f' rx="{op.w / 2:g}" ry="{op.h / 2:g}"'
                f' fill="{_hex(op.color)}"{fill}/>')
    if isinstance(op, Polygon):
        fill = "" if op.alpha >= 1.0 else f' fill-opacity="{op.alpha:.3f}"'
        points = " ".join(f"{x:g},{y:g}" for x, y in op.points)
        return f'<polygon points="{points}" fill="{_hex(op.color)}"{fill}/>'
    if isinstance(op, Stroke):
        opacity = ("" if op.alpha >= 1.0
                   else f' stroke-opacity="{op.alpha:.3f}"')
        points = " ".join(f"{x:g},{y:g}" for x, y in op.points)
        return (f'<polyline points="{points}" fill="none"'
                f' stroke="{_hex(op.color)}"{opacity}'
                f' stroke-width="{op.width:g}" stroke-linecap="round"'
                f' stroke-linejoin="round"/>')
    if isinstance(op, RoundedRect):
        fill = "" if op.alpha >= 1.0 else f' fill-opacity="{op.alpha:.3f}"'
        radius = min(op.radius, op.w / 2.0), min(op.radius, op.h / 2.0)
        return (f'<rect x="{op.x:g}" y="{op.y:g}" width="{op.w:g}"'
                f' height="{op.h:g}" rx="{radius[0]:g}" ry="{radius[1]:g}"'
                f' fill="{_hex(op.color)}"{fill}/>')
    if isinstance(op, Arc):
        start = math.radians(op.start_degrees)
        end = math.radians(op.end_degrees)
        x0 = op.cx + op.radius * math.cos(start)
        y0 = op.cy + op.radius * math.sin(start)
        x1 = op.cx + op.radius * math.cos(end)
        y1 = op.cy + op.radius * math.sin(end)
        large = 1 if abs(op.end_degrees - op.start_degrees) > 180 else 0
        opacity = ("" if op.alpha >= 1.0
                   else f' stroke-opacity="{op.alpha:.3f}"')
        return (f'<path d="M{x0:g} {y0:g} A{op.radius:g} {op.radius:g} 0'
                f' {large} 1 {x1:g} {y1:g}" fill="none"'
                f' stroke="{_hex(op.color)}"{opacity}'
                f' stroke-width="{op.width:g}" stroke-linecap="round"/>')
    if isinstance(op, Curve):
        fill = "" if op.alpha >= 1.0 else f' fill-opacity="{op.alpha:.3f}"'
        data = f"M{op.start[0]:g} {op.start[1]:g}"
        for (c1, c2, end) in op.segments:
            data += (f" C{c1[0]:g} {c1[1]:g} {c2[0]:g} {c2[1]:g}"
                     f" {end[0]:g} {end[1]:g}")
        data += " Z"
        return f'<path d="{data}" fill="{_hex(op.color)}"{fill}/>'
    raise TypeError(f"unsupported draw op: {type(op).__name__}")


# The frames every static surface renders: mouth shut, and mid-sentence.
IDLE_FRAME = (0.0, 0.0)
TALK_FRAME = (0.85, 0.8)


def character_body(face: str, *, talk: bool = False,
                   whispers: bool = True) -> str:
    """Character markup without the ``<svg>`` wrapper, for inlining.

    ``talk`` picks the open-mouth frame.  ``whispers`` can drop the trailing
    speech puffs for surfaces that show the character inside a tight chip,
    where the puffs would collide with the chip edge.

    The colored characters use no ``<mask>`` elements, so the returned markup
    carries no element ids and any number of them can share one document.
    """
    mouth, level = TALK_FRAME if talk else IDLE_FRAME
    ops: Sequence[Op] = character_ops(face, mouth, level)
    if not whispers:
        skip = set(_whisper_ops(level))
        ops = [op for op in ops if op not in skip]
    return "".join(_svg_op(op) for op in ops)


def character_svg(face: str, *, talk: bool = False,
                  whispers: bool = True) -> str:
    """Standalone colored SVG for ``face``."""
    size = f"{DESIGN_SIZE:g}"
    body = character_body(face, talk=talk, whispers=whispers)
    return (f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' viewBox="0 0 {size} {size}" width="{size}" height="{size}"'
            f' role="img" aria-hidden="true">{body}</svg>')
