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

Animation contract: ``mouth`` sweeps a closed smile through a rounded open
jaw, ``level`` drives the trailing speech puffs, and ``blink`` closes the
eyes into happy lids.  Static surfaces sample the three named frames in
``FRAMES``; the HUD animates all three inputs continuously.

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
BLUSH = (0.973, 0.549, 0.573)            # #f88c92, soft sticker rouge
IRIS_GOLD = (0.984, 0.749, 0.141)        # owl iris, same gold as the beaks


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
    feature that separates it from a pink bear.  The remaining flags are the
    species accents that keep ten similar toys from reading as one animal in
    ten colorways.
    """
    head: Color
    deep: Color
    muzzle: Color
    ears: str = "pointed"
    whiskers: bool = False
    patches: bool = False
    stripes: bool = False
    snout: bool = False
    fangs: bool = False
    cheek_fluff: bool = False
    ear_tips: bool = False
    brow_patch: bool = False
    stern_brows: bool = False
    big_tongue: bool = False
    inner_ears: bool = True
    blush_alpha: float = 0.4


COMPANIONS: Mapping[str, CharacterStyle] = {
    "fox": CharacterStyle(
        head=FACE_CHIP_COLORS["fox"],
        deep=(0.706, 0.231, 0.075),
        muzzle=(1.000, 0.878, 0.702),
        cheek_fluff=True,
        ear_tips=True,
    ),
    "cat": CharacterStyle(
        head=FACE_CHIP_COLORS["cat"],
        deep=(0.188, 0.349, 0.573),
        muzzle=(0.824, 0.914, 1.000),
        whiskers=True,
        fangs=True,
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
        brow_patch=True,
        big_tongue=True,
    ),
    "wolf": CharacterStyle(
        head=FACE_CHIP_COLORS["wolf"],
        deep=(0.310, 0.345, 0.400),
        muzzle=(0.831, 0.859, 0.902),
        cheek_fluff=True,
        stern_brows=True,
        blush_alpha=0.28,
    ),
    "pig": CharacterStyle(
        head=FACE_CHIP_COLORS["pig"],
        deep=(0.804, 0.435, 0.533),
        muzzle=(1.000, 0.859, 0.878),
        ears="round",
        snout=True,
        blush_alpha=0.6,
    ),
    "panda": CharacterStyle(
        head=FACE_CHIP_COLORS["panda"],
        deep=(0.129, 0.145, 0.161),
        muzzle=(1.000, 1.000, 1.000),
        ears="round",
        patches=True,
        inner_ears=False,
        blush_alpha=0.5,
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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _whisper_ops(level: float) -> list[Op]:
    """The mint speech puffs that trail off the character's shoulder."""
    glow = 0.35 + level * 0.6
    ops: list[Op] = [
        Stroke(((212, 62), (232, 52)), MINT, 12.0, glow),
        Stroke(((224, 88), (246, 84)), MINT, 12.0, glow * 0.6),
        Ellipse(232, 32, 16, 16, MINT, glow * 0.55),
    ]
    return ops


def _eye_ops(left: float, right: float, y: float, w: float, h: float,
             blink: float = 0.0, lid: Color = DARK_EYE) -> list[Op]:
    """A pair of dark sticker eyes with twin catchlights.

    ``blink`` squashes the eye toward its own vertical center; past 0.75 the
    eye becomes a happy closed lid drawn as an upward arc, which is what the
    HUD shows for the two or three frames a blink lasts.  ``lid`` recolors
    that arc for faces whose eyes sit on a dark patch, where the default ink
    would disappear.
    """
    ops: list[Op] = []
    centers = (left + w / 2.0, right + w / 2.0)
    if blink >= 0.75:
        for cx in centers:
            ops.append(Arc(cx, y + h * 0.62, w * 0.52, 200, 340,
                           lid, 5.5))
        return ops
    open_h = h * (1.0 - 0.88 * blink)
    top = y + (h - open_h) / 2.0
    for x in (left, right):
        ops.append(Ellipse(x, top, w, open_h, DARK_EYE))
    if blink < 0.45:
        # Twin catchlights: one bold, one whisper-small, offset diagonally.
        for x in (left, right):
            ops.append(Ellipse(x + w * 0.48, top + open_h * 0.14,
                               w * 0.32, open_h * 0.30, CATCH))
            ops.append(Ellipse(x + w * 0.16, top + open_h * 0.58,
                               w * 0.17, open_h * 0.17, CATCH, 0.85))
    return ops


def _smile_and_jaw(cx: float, top: float, mouth: float, *,
                   max_drop: float = 44.0, tongue_scale: float = 1.0,
                   fangs: bool = False) -> list[Op]:
    """Closed smile that opens into a rounded jaw with a rising tongue.

    ``mouth`` 0 is a stitched-on grin; as it grows the smile fades out and a
    dark cavity swings open beneath ``top``.  The cavity narrows a little as
    it opens, which reads as ah -> oh instead of a widening letterbox.
    """
    ops: list[Op] = []
    if mouth < 0.18:
        fade = 1.0 - mouth / 0.18
        ops.append(Arc(cx, top - 5.0, 16.0, 38, 142, MOUTH, 5.5, fade))
    if mouth <= 0.04:
        return ops

    half = (46.0 - 10.0 * mouth) / 2.0
    drop = 6.0 + max_drop * mouth
    lip = top - 3.0
    ops.append(Curve(
        (cx - half, lip),
        (((cx - half + 2, lip + drop * 0.82),
          (cx - 13, lip + drop), (cx, lip + drop)),
         ((cx + 13, lip + drop),
          (cx + half - 2, lip + drop * 0.82), (cx + half, lip)),
         ((cx + half * 0.5, lip - 4.5),
          (cx - half * 0.5, lip - 4.5), (cx - half, lip))),
        MOUTH,
    ))
    if fangs and mouth > 0.3:
        for dx in (-12.0, 12.0):
            ops.append(Polygon(((cx + dx - 4, lip + 1), (cx + dx + 4, lip + 1),
                                (cx + dx, lip + 10)), CATCH))
    if mouth > 0.24:
        rise = _clamp01((mouth - 0.24) / 0.6)
        tongue_h = (7.0 + 9.0 * rise) * tongue_scale
        tongue_w = 22.0 * (0.8 + 0.2 * rise) * max(1.0, tongue_scale * 0.9)
        ops.append(Ellipse(cx - tongue_w / 2.0,
                           lip + drop - tongue_h * 0.72,
                           tongue_w, tongue_h, TONGUE))
    return ops


def _blush_ops(alpha: float) -> list[Op]:
    return [Ellipse(44, 132, 32, 19, BLUSH, alpha),
            Ellipse(180, 132, 32, 19, BLUSH, alpha)]


def _nose_ops(color: Color) -> list[Op]:
    """Soft rounded-triangle nose with a tiny highlight."""
    return [
        Curve(
            (113, 137),
            (((118, 132), (138, 132), (143, 137)),
             ((141, 148), (133, 153), (128, 153)),
             ((123, 153), (115, 148), (113, 137))),
            color,
        ),
        Ellipse(119, 138, 7, 4.5, CATCH, 0.4),
    ]


def _companion_ops(face: str, mouth: float, level: float,
                   blink: float) -> list[Op]:
    style = COMPANIONS.get(face, COMPANIONS["fox"])
    ops: list[Op] = []

    # Ears sit behind the shared rounded head.
    if style.ears == "round":
        ops += [Ellipse(40, 42, 58, 58, style.deep),
                Ellipse(158, 42, 58, 58, style.deep)]
        if style.inner_ears:
            ops += [Ellipse(52, 55, 34, 33, style.muzzle),
                    Ellipse(170, 55, 34, 33, style.muzzle)]
    elif style.ears == "floppy":
        # Teardrop ears that hang past the jaw, drawn as closed curves so
        # they droop instead of standing up as stiff ovals.
        ops.append(Curve(
            (70, 66),
            (((26, 78), (14, 148), (40, 178)),
             ((58, 198), (76, 176), (74, 148)),
             ((88, 110), (86, 76), (70, 66))),
            style.deep,
        ))
        ops.append(Curve(
            (186, 66),
            (((230, 78), (242, 148), (216, 178)),
             ((198, 198), (180, 176), (182, 148)),
             ((168, 110), (170, 76), (186, 66))),
            style.deep,
        ))
    else:
        ops += [
            Polygon(((42, 96), (55, 28), (105, 78)), style.deep),
            Polygon(((151, 78), (201, 28), (214, 96)), style.deep),
            Polygon(((58, 78), (63, 48), (88, 75)), style.muzzle),
            Polygon(((168, 75), (193, 48), (198, 78)), style.muzzle),
        ]
        if style.ear_tips:
            # Tip triangles ride the outer ear's own edges so the dark fur
            # caps the apex instead of floating inside the ear.
            ops += [Polygon(((50.5, 51.8), (55, 28), (72.5, 45.5)), MOUTH),
                    Polygon(((183.5, 45.5), (201, 28), (205.5, 51.8)), MOUTH)]

    ops.append(Ellipse(34, 55, 188, 172, style.head))

    if style.cheek_fluff:
        # Two outward fur spikes per jaw side keep the vulpine faces from
        # being perfect circles.
        ops += [
            Polygon(((62, 148), (32, 158), (60, 168)), style.muzzle),
            Polygon(((64, 170), (38, 184), (66, 188)), style.muzzle),
            Polygon(((194, 148), (224, 158), (196, 168)), style.muzzle),
            Polygon(((192, 170), (218, 184), (190, 188)), style.muzzle),
        ]

    # Cheeks keep the soft, toy-like language and leave a clean lip-sync
    # cavity between them.
    ops += [Ellipse(61, 123, 78, 68, style.muzzle),
            Ellipse(117, 123, 78, 68, style.muzzle)]

    if style.patches:
        # Tilted teardrop patches, not straight ovals: the tilt is what makes
        # a panda look like a panda instead of a raccoon.
        ops.append(Curve(
            (68, 96),
            (((72, 80), (94, 78), (102, 88)),
             ((112, 100), (108, 124), (94, 128)),
             ((78, 130), (64, 112), (68, 96))),
            style.deep,
        ))
        ops.append(Curve(
            (188, 96),
            (((184, 80), (162, 78), (154, 88)),
             ((144, 100), (148, 124), (162, 128)),
             ((178, 130), (192, 112), (188, 96))),
            style.deep,
        ))

    ops += _blush_ops(style.blush_alpha)
    ops += _eye_ops(80, 154, 92, 22, 27, blink,
                    lid=style.muzzle if style.patches else DARK_EYE)

    if style.brow_patch:
        ops.append(Ellipse(146, 74, 34, 20, style.deep, 0.9))
    if style.stern_brows:
        ops += [Stroke(((76, 82), (102, 88)), style.deep, 5.0, 0.8),
                Stroke(((180, 82), (154, 88)), style.deep, 5.0, 0.8)]

    if style.stripes:
        # Tapered crown and cheek stripes, drawn as slim triangles so they
        # thin out the way brush strokes do.
        ops += [
            Polygon(((123, 58), (133, 58), (128, 92)), style.deep),
            Polygon(((100, 62), (110, 60), (107, 90)), style.deep),
            Polygon(((146, 60), (156, 62), (149, 90)), style.deep),
            Polygon(((40, 118), (66, 122), (42, 130)), style.deep),
            Polygon(((214, 122), (190, 126), (212, 134)), style.deep),
        ]

    if style.snout:
        # A flat disc with two nostrils, and the mouth pushed below it.
        # Without this the pig is a pink bear.
        ops.append(Ellipse(97, 132, 62, 48, style.deep))
        ops.append(Ellipse(103, 138, 50, 36, style.muzzle))
        ops += [Ellipse(x, 146, 11, 17, style.deep) for x in (111, 134)]
        ops += _smile_and_jaw(128, 189, mouth, max_drop=26.0)
    else:
        ops += _nose_ops(style.deep)
        ops += _smile_and_jaw(
            128, 158, mouth,
            tongue_scale=1.5 if style.big_tongue else 1.0,
            fangs=style.fangs,
        )

    if style.whiskers:
        for y, dy in ((149, -6), (159, 0), (169, 6)):
            ops.append(Stroke(((91, y), (64, y + dy * 0.4), (39, y + dy)),
                              style.deep, 2.6, 0.65))
            ops.append(Stroke(((165, y), (192, y + dy * 0.4), (217, y + dy)),
                              style.deep, 2.6, 0.65))

    return ops + _whisper_ops(level)


def _parrot_ops(mouth: float, level: float, blink: float) -> list[Op]:
    """Emerald head, feathered gold crest, hooked beak that swings open."""
    ops: list[Op] = [
        # Three gold crest feathers fanned above the crown, center tallest.
        # The darker side petals sit behind the bright center one so the
        # crest reads as plumage instead of a single sprout.
        Curve(
            (96, 78),
            (((80, 50), (76, 24), (92, 16)),
             ((106, 10), (116, 40), (116, 66)),
             ((114, 76), (100, 82), (96, 78))),
            BEAK_LO,
        ),
        Curve(
            (160, 78),
            (((176, 50), (180, 24), (164, 16)),
             ((150, 10), (140, 40), (140, 66)),
             ((142, 76), (156, 82), (160, 78))),
            BEAK_LO,
        ),
        Curve(
            (112, 68),
            (((106, 32), (112, 2), (128, 2)),
             ((144, 2), (150, 32), (144, 68)),
             ((140, 78), (116, 78), (112, 68))),
            BEAK_UP,
        ),
        Ellipse(34, 55, 188, 172, EMERALD),
    ]
    # Bare facial patches behind the eyes, the parrot's white "spectacles".
    ops += [Ellipse(74, 88, 34, 40, CATCH, 0.92),
            Ellipse(148, 88, 34, 40, CATCH, 0.92)]
    ops += _eye_ops(82, 156, 94, 20, 25, blink)

    # Beak stack, bottom-up: dark gape, then the gold hook whose tip ends
    # above the mandible, then the mandible that swings down.  The gape only
    # becomes visible in the band the mandible vacates, so a closed beak is
    # seamless and an open one shows a real dark mouth.
    drop = mouth * 14.0
    ops.append(Polygon(((112, 158), (144, 158), (128, 182 + drop)), MOUTH))
    # Upper beak: broad at the cere, curling to a blunt hook tip that hangs
    # slightly lower than its sides, the classic parrot profile.
    ops.append(Curve(
        (104, 142),
        (((102, 158), (110, 170), (122, 176)),
         ((127, 179), (131, 179), (134, 175)),
         ((146, 166), (152, 154), (152, 142)),
         ((146, 133), (110, 133), (104, 142))),
        BEAK_UP,
    ))
    ops.append(Curve(
        (114, 170 + drop),
        (((120, 175 + drop), (136, 175 + drop), (142, 170 + drop)),
         ((134, 190 + drop), (122, 190 + drop), (114, 170 + drop))),
        BEAK_LO,
    ))
    ops += [Ellipse(119, 141, 4, 4.5, MOUTH, 0.5),
            Ellipse(133, 141, 4, 4.5, MOUTH, 0.5)]
    return ops + _whisper_ops(level)


def _owl_ops(mouth: float, level: float, blink: float) -> list[Op]:
    purple = FACE_CHIP_COLORS["owl"]
    deep = (0.255, 0.200, 0.506)
    cream = (0.890, 0.855, 1.000)
    ops: list[Op] = [
        Polygon(((39, 104), (62, 31), (104, 79)), deep),
        Polygon(((152, 79), (194, 31), (217, 104)), deep),
        Ellipse(32, 52, 192, 180, purple),
    ]
    ops += [Ellipse(x, 88, 70, 70, cream) for x in (57, 129)]
    ops += [Ellipse(48, 146, 26, 15, BLUSH, 0.3),
            Ellipse(182, 146, 26, 15, BLUSH, 0.3)]

    # Golden iris rings around wide pupils: the owl finally gets owl eyes.
    if blink >= 0.75:
        ops += [Arc(94, 122, 14, 200, 340, DARK_EYE, 5.5),
                Arc(162, 122, 14, 200, 340, DARK_EYE, 5.5)]
    else:
        squash = 1.0 - 0.88 * blink
        for cx in (94.0, 162.0):
            outer_h = 33.0 * squash
            ops.append(Ellipse(cx - 14, 119 - outer_h / 2.0, 28, outer_h,
                               DARK_EYE))
            iris_h = 22.0 * squash
            ops.append(Ellipse(cx - 9.5, 119 - iris_h / 2.0, 19, iris_h,
                               IRIS_GOLD))
            pupil_h = 12.0 * squash
            ops.append(Ellipse(cx - 5, 119 - pupil_h / 2.0, 10, pupil_h,
                               DARK_EYE))
            if blink < 0.45:
                ops.append(Ellipse(cx - 1, 119 - outer_h * 0.32,
                                   6.5, 7.5 * squash, CATCH))
                ops.append(Ellipse(cx - 6, 119 + outer_h * 0.14,
                                   3.2, 3.6 * squash, CATCH, 0.85))

    ops.append(Polygon(((111, 151), (145, 151), (128, 167 + mouth * 16)),
                       MOUTH))
    ops.append(Polygon(((105, 145), (151, 145), (128, 163)), BEAK_UP))
    drop = mouth * 13.0
    ops.append(Polygon(((111, 164 + drop), (145, 164 + drop),
                        (128, 178 + drop)), BEAK_LO))
    # Two rows of chest chevrons read as feathers where the parrot wears its
    # single scallop, so the two birds stay distinct below the beak.
    for row_y, spread in ((196.0, 16.0), (212.0, 12.0)):
        ops.append(Stroke(((128 - spread, row_y), (128, row_y + 7),
                           (128 + spread, row_y)), deep, 4.5, 0.4))
    return ops + _whisper_ops(level)


def character_ops(face: str, mouth: float, level: float,
                  blink: float = 0.0) -> tuple[Op, ...]:
    """Ordered draw ops for ``face`` at one animation instant.

    ``mouth``, ``level``, and ``blink`` are all 0..1.  Animation offsets are
    baked into the returned coordinates so a renderer never has to manage a
    transform stack.  ``blink`` only ever moves in the live HUD; the exported
    frames keep it at zero.
    """
    mouth = _clamp01(mouth)
    level = _clamp01(level)
    blink = _clamp01(blink)
    if face == "parrot":
        ops = _parrot_ops(mouth, level, blink)
    elif face == "owl":
        ops = _owl_ops(mouth, level, blink)
    else:
        ops = _companion_ops(face, mouth, level, blink)
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


# The frames every static surface renders.  ``half`` is the mid-syllable
# in-between the site cycles through so the flap reads as speech instead of a
# two-state puppet.
FRAMES: Mapping[str, tuple[float, float]] = {
    "idle": (0.0, 0.0),
    "half": (0.45, 0.5),
    "talk": (0.85, 0.8),
}
IDLE_FRAME = FRAMES["idle"]
TALK_FRAME = FRAMES["talk"]


def character_body(face: str, *, frame: str = "idle",
                   whispers: bool = True) -> str:
    """Character markup without the ``<svg>`` wrapper, for inlining.

    ``frame`` names an entry in ``FRAMES``.  ``whispers`` can drop the
    trailing speech puffs for surfaces that show the character inside a tight
    chip, where the puffs would collide with the chip edge.

    The colored characters use no ``<mask>`` elements, so the returned markup
    carries no element ids and any number of them can share one document.
    """
    mouth, level = FRAMES[frame]
    ops: Sequence[Op] = character_ops(face, mouth, level)
    if not whispers:
        skip = set(_whisper_ops(level))
        ops = [op for op in ops if op not in skip]
    return "".join(_svg_op(op) for op in ops)


def character_svg(face: str, *, frame: str = "idle",
                  whispers: bool = True) -> str:
    """Standalone colored SVG for ``face``."""
    size = f"{DESIGN_SIZE:g}"
    body = character_body(face, frame=frame, whispers=whispers)
    return (f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' viewBox="0 0 {size} {size}" width="{size}" height="{size}"'
            f' role="img" aria-hidden="true">{body}</svg>')
