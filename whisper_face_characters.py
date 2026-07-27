"""Shared Whisper Face character geometry for every colored surface.

The faces used to be drawn twice: once as AppKit Bezier calls inside the
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
jaw, ``level`` drives the trailing speech puffs, ``blink`` closes the eyes
into happy lids, and ``gaze`` drifts the pupils a few points so an idle
face can glance around.  Static surfaces sample the three named frames in
``FRAMES``; live views animate every input continuously.

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

EMERALD = FACE_CHIP_COLORS["parrot"]     # pastel emerald, #89e2bd
DEEP = (0.239, 0.588, 0.463)             # #3d9676, relaxed parrot accent
BEAK_UP = (0.969, 0.808, 0.451)          # #f7ce73
BEAK_LO = (0.941, 0.718, 0.353)          # #f0b75a
DARK_EYE = (0.165, 0.129, 0.110)         # #2a211c, warm clay near-black
MOUTH = (0.200, 0.157, 0.122)            # #33281f
MINT = FACE_CHIP_COLORS["owl"]           # #a2efdf
CATCH = (1.000, 0.992, 0.973)            # #fffdf8, warm porcelain catchlight
TONGUE = (0.965, 0.604, 0.655)           # #f69aa7
BLUSH = (0.976, 0.702, 0.714)            # #f9b3b6, chibi rouge
IRIS_GOLD = (0.969, 0.808, 0.451)        # owl iris, same gold as the beaks


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
    silhouette has always had hanging ears, ``long`` for the rabbit, and
    ``none`` for the two faces whose crown carries something else entirely.
    ``snout`` gives the pig the feature that separates it from a pink bear.
    The remaining flags are the species accents that keep a dozen similar
    toys from reading as one animal in a dozen colorways.

    The last four flags each carry one species on their own: ``eye_domes``
    puts the frog's eyes on top of its head where no mammal's are,
    ``buck_teeth`` pairs with the rabbit's long ears, ``quills`` fans the
    hedgehog's spines past the head outline, and ``hood`` swaps the paired
    cheeks for the penguin's single face bib under a dark cap.

    ``bow`` and ``blep`` carry the two English cream goldens, who are
    litter-mates and share one coat: Olive wears a white bow at her ear,
    and Pickles rests the tip of his tongue past the closed smile the way
    the real dog does.  Accessories, not palette, tell the pair apart.
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
    eye_domes: bool = False
    wide_mouth: bool = False
    buck_teeth: bool = False
    quills: bool = False
    snout_point: bool = False
    hood: bool = False
    beak: bool = False
    bow: bool = False
    blep: bool = False


COMPANIONS: Mapping[str, CharacterStyle] = {
    "fox": CharacterStyle(
        head=FACE_CHIP_COLORS["fox"],
        deep=(0.812, 0.447, 0.302),
        muzzle=(1.000, 0.937, 0.839),
        cheek_fluff=True,
        ear_tips=True,
    ),
    "cat": CharacterStyle(
        head=FACE_CHIP_COLORS["cat"],
        deep=(0.443, 0.502, 0.702),
        muzzle=(0.906, 0.945, 1.000),
        whiskers=True,
        fangs=True,
    ),
    "bear": CharacterStyle(
        head=FACE_CHIP_COLORS["bear"],
        deep=(0.576, 0.443, 0.351),
        muzzle=(0.949, 0.831, 0.694),
        ears="round",
    ),
    "dog": CharacterStyle(
        head=FACE_CHIP_COLORS["dog"],
        deep=(0.718, 0.573, 0.412),
        muzzle=(0.996, 0.957, 0.894),
        ears="floppy",
        brow_patch=True,
        big_tongue=True,
    ),
    "wolf": CharacterStyle(
        head=FACE_CHIP_COLORS["wolf"],
        deep=(0.506, 0.541, 0.596),
        muzzle=(0.902, 0.922, 0.949),
        cheek_fluff=True,
        stern_brows=True,
        blush_alpha=0.34,
    ),
    "pig": CharacterStyle(
        head=FACE_CHIP_COLORS["pig"],
        deep=(0.878, 0.608, 0.675),
        muzzle=(1.000, 0.910, 0.922),
        ears="round",
        snout=True,
        blush_alpha=0.65,
    ),
    "panda": CharacterStyle(
        head=FACE_CHIP_COLORS["panda"],
        deep=(0.333, 0.349, 0.365),
        muzzle=(1.000, 1.000, 1.000),
        ears="round",
        patches=True,
        inner_ears=False,
        blush_alpha=0.55,
    ),
    "tiger": CharacterStyle(
        head=FACE_CHIP_COLORS["tiger"],
        deep=(0.871, 0.557, 0.318),
        muzzle=(1.000, 0.945, 0.867),
        stripes=True,
    ),
    "frog": CharacterStyle(
        head=FACE_CHIP_COLORS["frog"],
        deep=(0.427, 0.635, 0.239),
        muzzle=(0.973, 0.988, 0.859),
        ears="none",
        eye_domes=True,
        wide_mouth=True,
        blush_alpha=0.55,
    ),
    "rabbit": CharacterStyle(
        head=FACE_CHIP_COLORS["rabbit"],
        deep=(0.867, 0.494, 0.604),
        muzzle=(1.000, 0.949, 0.957),
        ears="long",
        buck_teeth=True,
        blush_alpha=0.55,
    ),
    "hedgehog": CharacterStyle(
        head=FACE_CHIP_COLORS["hedgehog"],
        deep=(0.404, 0.302, 0.216),
        muzzle=(1.000, 0.953, 0.894),
        ears="none",
        quills=True,
        snout_point=True,
        blush_alpha=0.42,
    ),
    "penguin": CharacterStyle(
        head=FACE_CHIP_COLORS["penguin"],
        deep=(0.216, 0.318, 0.443),
        muzzle=(1.000, 0.996, 0.965),
        ears="none",
        hood=True,
        beak=True,
        blush_alpha=0.45,
    ),
    "pickles": CharacterStyle(
        head=FACE_CHIP_COLORS["pickles"],
        deep=(0.804, 0.647, 0.412),
        muzzle=(1.000, 0.976, 0.925),
        ears="floppy",
        big_tongue=True,
        blep=True,
        blush_alpha=0.45,
    ),
    "olive": CharacterStyle(
        head=FACE_CHIP_COLORS["olive"],
        deep=(0.808, 0.686, 0.502),
        muzzle=(1.000, 0.984, 0.949),
        ears="floppy",
        bow=True,
        blush_alpha=0.45,
    ),
}

# Parrot and owl keep bespoke geometry; the other fourteen share one template.
FACE_ORDER: tuple[str, ...] = (
    "parrot", "fox", "bear", "owl", "cat", "dog", "wolf", "pig", "panda",
    "tiger", "frog", "rabbit", "hedgehog", "penguin", "pickles", "olive",
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


def _clamp_gaze(gaze: Point) -> Point:
    """Clamp a pupil-drift offset to a subtle chibi glance."""
    gx, gy = gaze
    return (max(-3.0, min(3.0, float(gx))),
            max(-3.0, min(3.0, float(gy))))


def _eye_ops(left: float, right: float, y: float, w: float, h: float,
             blink: float = 0.0, lid: Color = DARK_EYE,
             gaze: Point = (0.0, 0.0)) -> list[Op]:
    """A pair of round chibi eyes with twin catchlights.

    ``blink`` squashes the eye toward its own vertical center; past 0.75 the
    eye becomes a happy closed lid drawn as an upward arc, which is what the
    HUD shows for the two or three frames a blink lasts.  ``lid`` recolors
    that arc for faces whose eyes sit on a dark patch, where the default ink
    would disappear.  ``gaze`` drifts the whole eye a few points so an idle
    face can glance around; lids ignore it.
    """
    ops: list[Op] = []
    gx, gy = _clamp_gaze(gaze)
    centers = (left + w / 2.0, right + w / 2.0)
    if blink >= 0.75:
        for cx in centers:
            ops.append(Arc(cx, y + h * 0.62, w * 0.52, 200, 340,
                           lid, 5.5))
        return ops
    open_h = h * (1.0 - 0.88 * blink)
    top = y + (h - open_h) / 2.0 + gy
    for x in (left, right):
        ops.append(Ellipse(x + gx, top, w, open_h, DARK_EYE))
    if blink < 0.45:
        # Twin catchlights: one bold, one whisper-small, offset diagonally.
        for x in (left, right):
            ops.append(Ellipse(x + gx + w * 0.44, top + open_h * 0.16,
                               w * 0.36, open_h * 0.34, CATCH))
            ops.append(Ellipse(x + gx + w * 0.14, top + open_h * 0.60,
                               w * 0.18, open_h * 0.18, CATCH, 0.85))
    return ops


def _smile_and_jaw(cx: float, top: float, mouth: float, *,
                   max_drop: float = 30.0, tongue_scale: float = 1.0,
                   fangs: bool = False) -> list[Op]:
    """Closed smile that opens into a rounded jaw with a rising tongue.

    ``mouth`` 0 is a stitched-on grin; as it grows the smile fades out and a
    dark cavity swings open beneath ``top``.  The cavity narrows a little as
    it opens, which reads as ah -> oh instead of a widening letterbox.
    """
    ops: list[Op] = []
    if mouth < 0.18:
        fade = 1.0 - mouth / 0.18
        ops.append(Arc(cx, top - 4.0, 10.5, 38, 142, MOUTH, 5.0, fade))
    if mouth <= 0.04:
        return ops

    half = (34.0 - 8.0 * mouth) / 2.0
    drop = 5.0 + max_drop * mouth
    lip = top - 3.0
    ops.append(Curve(
        (cx - half, lip),
        (((cx - half + 2, lip + drop * 0.82),
          (cx - 9, lip + drop), (cx, lip + drop)),
         ((cx + 9, lip + drop),
          (cx + half - 2, lip + drop * 0.82), (cx + half, lip)),
         ((cx + half * 0.5, lip - 3.5),
          (cx - half * 0.5, lip - 3.5), (cx - half, lip))),
        MOUTH,
    ))
    if fangs and mouth > 0.3:
        for dx in (-9.0, 9.0):
            ops.append(Polygon(((cx + dx - 3, lip + 1), (cx + dx + 3, lip + 1),
                                (cx + dx, lip + 8)), CATCH))
    if mouth > 0.24:
        rise = _clamp01((mouth - 0.24) / 0.6)
        tongue_h = (6.0 + 7.0 * rise) * tongue_scale
        tongue_w = 17.0 * (0.8 + 0.2 * rise) * max(1.0, tongue_scale * 0.9)
        ops.append(Ellipse(cx - tongue_w / 2.0,
                           lip + drop - tongue_h * 0.72,
                           tongue_w, tongue_h, TONGUE))
    return ops


def _wide_grin(cx: float, top: float, mouth: float) -> list[Op]:
    """The frog's ear-to-ear mouth, on the same three-frame contract.

    A frog's grin is nearly as wide as its head, which no template mouth can
    stretch to without the closed smile and the open jaw disagreeing about
    where the corners are.  Closed it is a shallow round-capped line; open it
    is a broad, shallow cavity rather than the deep chibi jaw.
    """
    ops: list[Op] = []
    if mouth < 0.18:
        fade = 1.0 - mouth / 0.18
        ops.append(Stroke(((cx - 56, top - 8), (cx - 30, top + 8),
                           (cx, top + 12), (cx + 30, top + 8),
                           (cx + 56, top - 8)), MOUTH, 6.5, fade))
    if mouth <= 0.04:
        return ops

    half = (108.0 - 20.0 * mouth) / 2.0
    drop = 6.0 + 30.0 * mouth
    lip = top - 6.0
    ops.append(Curve(
        (cx - half, lip),
        (((cx - half + 6, lip + drop * 0.9),
          (cx - 20, lip + drop), (cx, lip + drop)),
         ((cx + 20, lip + drop),
          (cx + half - 6, lip + drop * 0.9), (cx + half, lip)),
         ((cx + half * 0.5, lip - 6.0),
          (cx - half * 0.5, lip - 6.0), (cx - half, lip))),
        MOUTH,
    ))
    if mouth > 0.24:
        rise = _clamp01((mouth - 0.24) / 0.6)
        tongue_h = 8.0 + 9.0 * rise
        tongue_w = 40.0 * (0.8 + 0.2 * rise)
        ops.append(Ellipse(cx - tongue_w / 2.0,
                           lip + drop - tongue_h * 0.72,
                           tongue_w, tongue_h, TONGUE))
    return ops


def _beak_ops(top: float, mouth: float) -> list[Op]:
    """Bottom-up beak stack: dark gape, fixed upper, swinging mandible.

    Shared by the owl and the penguin so the two beaked faces cannot drift
    apart in how they open.
    """
    drop = mouth * 11.0
    return [
        Polygon(((115, top + 6), (141, top + 6),
                 (128, top + 20 + mouth * 12)), MOUTH),
        Polygon(((110, top), (146, top), (128, top + 16)), BEAK_UP),
        Polygon(((115, top + 17 + drop), (141, top + 17 + drop),
                 (128, top + 29 + drop)), BEAK_LO),
    ]


def _quill_ops(color: Color) -> list[Op]:
    """The hedgehog's spines, fanned around the crown behind the head.

    Each spine roots just inside the head ellipse and points straight out of
    it, so the head swallows the bases and only the tips break the outline.
    """
    ops: list[Op] = []
    count = 9
    for index in range(count):
        angle = math.radians(200.0 + index * (140.0 / (count - 1)))
        edge_x = 128.0 + 98.0 * math.cos(angle)
        edge_y = 144.0 + 92.0 * math.sin(angle)
        out_x, out_y = edge_x - 128.0, edge_y - 144.0
        span = math.hypot(out_x, out_y)
        out_x, out_y = out_x / span, out_y / span
        root_x, root_y = edge_x - out_x * 14.0, edge_y - out_y * 14.0
        ops.append(Polygon((
            (root_x - out_y * 16.0, root_y + out_x * 16.0),
            (edge_x + out_x * 34.0, edge_y + out_y * 34.0),
            (root_x + out_y * 16.0, root_y - out_x * 16.0),
        ), color))
    return ops


def _blush_ops(alpha: float, *, y: float = 146.0,
               inset: float = 0.0) -> list[Op]:
    """Big soft chibi rouge, tucked right under the eyes.

    ``y`` follows the eyes on faces that moved them, and ``inset`` pulls the
    pads toward the center on faces whose cheeks are not the head color.
    """
    return [Ellipse(42 + inset, y, 42, 24, BLUSH, alpha),
            Ellipse(172 - inset, y, 42, 24, BLUSH, alpha)]


def _nose_ops(color: Color, y: float = 142.0) -> list[Op]:
    """Small rounded-triangle nose with a tiny highlight."""
    drop = y - 142.0
    return [
        Curve(
            (117, 142 + drop),
            (((121, 138 + drop), (135, 138 + drop), (139, 142 + drop)),
             ((137, 150 + drop), (131, 154 + drop), (128, 154 + drop)),
             ((125, 154 + drop), (119, 150 + drop), (117, 142 + drop))),
            color,
        ),
        Ellipse(121, 142 + drop, 6, 4, CATCH, 0.4),
    ]


def _clay_ops(style_deep: Color) -> list[Op]:
    """The soft-vinyl read: a crown sheen and a jaw shadow, both flat.

    Two low-alpha overlays stand in for the radial gradients the op DSL
    deliberately does not have (SVG gradients would force element ids into
    ``character_body``, which the site's shared documents forbid).
    """
    return [
        Ellipse(62, 62, 108, 58, CATCH, 0.17),
        Curve(
            (52, 192),
            (((78, 232), (178, 232), (204, 192)),
             ((182, 216), (74, 216), (52, 192))),
            style_deep, 0.10,
        ),
    ]


def _companion_ops(face: str, mouth: float, level: float, blink: float,
                   gaze: Point = (0.0, 0.0)) -> list[Op]:
    style = COMPANIONS.get(face, COMPANIONS["fox"])
    ops: list[Op] = []

    # Ears sit behind the shared rounded head, tucked low so the silhouette
    # stays one soft blob.
    if style.ears == "round":
        ops += [Ellipse(46, 40, 52, 52, style.deep),
                Ellipse(158, 40, 52, 52, style.deep)]
        if style.inner_ears:
            ops += [Ellipse(58, 52, 28, 28, style.muzzle),
                    Ellipse(170, 52, 28, 28, style.muzzle)]
    elif style.ears == "long":
        # Leaning teardrops with pink liners.  Two upright capsules read as
        # antennae; the outward lean and the taper at the root are what make
        # the pair read as a rabbit.  The head swallows the roots.
        ops.append(Curve(
            (96, 104),
            (((74, 96), (62, 46), (76, 22)),
             ((86, 6), (108, 14), (110, 44)),
             ((112, 70), (108, 100), (96, 104))),
            style.deep,
        ))
        ops.append(Curve(
            (160, 104),
            (((182, 96), (194, 46), (180, 22)),
             ((170, 6), (148, 14), (146, 44)),
             ((144, 70), (148, 100), (160, 104))),
            style.deep,
        ))
        ops.append(Curve(
            (96, 98),
            (((84, 92), (76, 52), (86, 34)),
             ((94, 22), (102, 30), (103, 52)),
             ((104, 74), (103, 94), (96, 98))),
            style.muzzle,
        ))
        ops.append(Curve(
            (160, 98),
            (((172, 92), (180, 52), (170, 34)),
             ((162, 22), (154, 30), (153, 52)),
             ((152, 74), (153, 94), (160, 98))),
            style.muzzle,
        ))
    elif style.ears == "none":
        pass
    elif style.ears == "floppy":
        # Teardrop ears that hug the head instead of hanging past the jaw;
        # closed curves so they still droop rather than stand up stiff.
        ops.append(Curve(
            (74, 62),
            (((38, 74), (28, 132), (48, 158)),
             ((62, 174), (78, 156), (78, 132)),
             ((90, 102), (88, 72), (74, 62))),
            style.deep,
        ))
        ops.append(Curve(
            (182, 62),
            (((218, 74), (228, 132), (208, 158)),
             ((194, 174), (178, 156), (178, 132)),
             ((166, 102), (168, 72), (182, 62))),
            style.deep,
        ))
    else:
        # Short, wide triangles: pointed species keep their ears, chibi
        # keeps them stubby.
        ops += [
            Polygon(((50, 92), (66, 36), (112, 76)), style.deep),
            Polygon(((144, 76), (190, 36), (206, 92)), style.deep),
            Polygon(((64, 76), (70, 52), (94, 72)), style.muzzle),
            Polygon(((162, 72), (186, 52), (192, 76)), style.muzzle),
        ]
        if style.ear_tips:
            # Tip triangles ride the outer ear's own edges so the dark fur
            # caps the apex instead of floating inside the ear.
            ops += [Polygon(((61.5, 51.5), (66, 36), (79.5, 47.5)), MOUTH),
                    Polygon(((176.5, 47.5), (190, 36), (194.5, 51.5)), MOUTH)]

    if style.eye_domes:
        # Head-colored bulges on the crown.  A frog's eyes are not on its
        # face, they are on top of its skull, and drawing them in the head
        # color keeps the silhouette one blob with two bumps.
        ops += [Ellipse(44, 32, 70, 70, style.head),
                Ellipse(142, 32, 70, 70, style.head)]
    if style.quills:
        ops += _quill_ops(style.deep)

    ops.append(Ellipse(30, 52, 196, 184, style.head))

    if style.cheek_fluff:
        # Two soft fur bumps per jaw side, drawn before the cheeks so only
        # their outer tips peek past the blob silhouette.
        ops += [
            Polygon(((70, 148), (34, 158), (68, 172)), style.muzzle),
            Polygon(((72, 172), (40, 184), (74, 192)), style.muzzle),
            Polygon(((186, 148), (222, 158), (188, 172)), style.muzzle),
            Polygon(((184, 172), (216, 184), (182, 192)), style.muzzle),
        ]

    if style.hood:
        # A dark cap over the crown and one cream bib instead of the paired
        # cheeks.  That pairing is what reads as a seabird rather than as a
        # round-eared mammal, and it survives being 40 points wide.
        ops += [Ellipse(44, 62, 168, 142, style.deep),
                Ellipse(64, 104, 128, 122, style.muzzle)]
    elif style.wide_mouth:
        # A frog has no cheeks worth drawing: one pale throat spanning the
        # chin, with the grin riding its top edge the way a real one does.
        ops.append(Ellipse(66, 184, 124, 48, style.muzzle))
    elif style.snout_point:
        # One tapered muzzle in place of the paired cheeks, because a face
        # that does not actually come to a point reads as a spiky bear.
        ops.append(Curve(
            (91, 146),
            (((89, 178), (102, 200), (128, 200)),
             ((154, 200), (167, 178), (165, 146)),
             ((165, 128), (91, 128), (91, 146))),
            style.muzzle,
        ))
    else:
        # Cheeks keep the soft, toy-like language and leave a clean lip-sync
        # cavity between them.
        ops += [Ellipse(64, 140, 72, 58, style.muzzle),
                Ellipse(120, 140, 72, 58, style.muzzle)]

    if style.patches:
        # Tilted teardrop patches, not straight ovals: the tilt is what makes
        # a panda look like a panda instead of a raccoon.
        ops.append(Curve(
            (72, 112),
            (((76, 96), (96, 94), (104, 104)),
             ((114, 116), (110, 138), (96, 142)),
             ((80, 144), (68, 128), (72, 112))),
            style.deep,
        ))
        ops.append(Curve(
            (184, 112),
            (((180, 96), (160, 94), (152, 104)),
             ((142, 116), (146, 138), (160, 142)),
             ((176, 144), (188, 128), (184, 112))),
            style.deep,
        ))

    ops += _clay_ops(style.deep)

    if style.quills:
        # A dark spiny mantle over the crown, laid on after the clay sheen so
        # the sheen does not leave a bald highlight in the middle of it.  The
        # fan behind the head breaks the outline; this is what it grows from.
        ops.append(Curve(
            (48, 100),
            (((48, 66), (84, 52), (128, 52)),
             ((172, 52), (208, 66), (208, 100)),
             ((172, 112), (84, 112), (48, 100))),
            style.deep,
        ))

    if style.eye_domes:
        # Pink over green goes gray, so the rouge sits on small cream pads,
        # the same trick the parrot and the owl use.  The eyeballs come back
        # on top of the head so the domes keep a rim of head color.
        ops += [Ellipse(46, 125, 38, 21, CATCH, 0.5),
                Ellipse(172, 125, 38, 21, CATCH, 0.5)]
        ops += _blush_ops(style.blush_alpha, y=126.0, inset=4.0)
        ops += [Ellipse(55, 43, 48, 48, CATCH),
                Ellipse(153, 43, 48, 48, CATCH)]
        ops += _eye_ops(68, 166, 54, 22, 26, blink, gaze=gaze)
    elif style.hood:
        # The bib sits low, so the eyes and the rouge follow it down and in.
        ops += _blush_ops(style.blush_alpha, y=152.0, inset=30.0)
        ops += _eye_ops(78, 160, 124, 18, 22, blink, gaze=gaze)
    else:
        ops += _blush_ops(style.blush_alpha)
        ops += _eye_ops(76, 162, 108, 18, 22, blink,
                        lid=style.muzzle if style.patches else DARK_EYE,
                        gaze=gaze)

    if style.brow_patch:
        ops.append(Ellipse(146, 88, 30, 18, style.deep, 0.9))
    if style.stern_brows:
        ops += [Stroke(((82, 98), (102, 102)), style.deep, 4.0, 0.45),
                Stroke(((174, 98), (154, 102)), style.deep, 4.0, 0.45)]

    if style.stripes:
        # Three stubby crown stripes, round-capped so they read as plush
        # markings instead of brush strokes.
        ops += [
            Stroke(((128, 58), (128, 84)), style.deep, 6.0, 0.9),
            Stroke(((106, 62), (110, 86)), style.deep, 5.0, 0.9),
            Stroke(((150, 62), (146, 86)), style.deep, 5.0, 0.9),
        ]

    if style.bow:
        # Olive's white grosgrain bow, clipped sideways over the right ear
        # the way the real dog wears hers: inner loop lying on the crown,
        # outer loop breaking the head outline over the ear.  Every white
        # shape sits on a deep-gold rim copy of itself, so the porcelain
        # keeps a crisp edge on head, ear, and background alike.
        ops.append(Curve(
            (190, 102),
            (((170, 109), (144, 102), (142, 88)),
             ((140, 69), (170, 71), (190, 80)),
             ((196, 86), (196, 96), (190, 102))),
            style.deep,
        ))
        ops.append(Curve(
            (202, 100),
            (((222, 105), (248, 96), (250, 78)),
             ((252, 61), (220, 67), (200, 78)),
             ((196, 86), (197, 96), (202, 100))),
            style.deep,
        ))
        ops.append(Curve(
            (188, 98),
            (((172, 104), (150, 98), (148, 88)),
             ((146, 74), (170, 76), (188, 84)),
             ((192, 88), (192, 94), (188, 98))),
            CATCH,
        ))
        ops.append(Curve(
            (204, 96),
            (((220, 100), (242, 92), (244, 80)),
             ((246, 66), (220, 72), (204, 82)),
             ((200, 87), (200, 92), (204, 96))),
            CATCH,
        ))
        ops.append(Ellipse(185, 79, 26, 26, style.deep))
        ops.append(Ellipse(188, 82, 20, 20, CATCH))
        ops.append(Ellipse(193, 90, 8, 6, style.deep, 0.20))

    if style.snout:
        # A flat disc with two nostrils, and the mouth pushed below it.
        # Without this the pig is a pink bear.
        ops.append(Ellipse(100, 140, 56, 40, style.deep))
        ops.append(Ellipse(105, 145, 46, 30, style.muzzle))
        ops += [Ellipse(x, 151, 8, 13, style.deep) for x in (113, 135)]
        ops += _smile_and_jaw(128, 192, mouth, max_drop=18.0)
    elif style.beak:
        ops += _beak_ops(158, mouth)
    elif style.wide_mouth:
        # Two nostril pricks where a mammal wears a nose, then the grin.
        ops += [Ellipse(115, 138, 9, 7, style.deep, 0.9),
                Ellipse(132, 138, 9, 7, style.deep, 0.9)]
        ops += _wide_grin(128, 180, mouth)
    elif style.snout_point:
        # A round nose right on the tip of the snout, not the small triangle
        # a flat-faced companion wears halfway up its muzzle.
        ops += [Ellipse(114, 170, 28, 21, style.deep),
                Ellipse(120, 174, 8, 5, CATCH, 0.4)]
        ops += _smile_and_jaw(128, 195, mouth, max_drop=18.0)
    else:
        # A face wearing incisors needs its nose out of their way.
        ops += _nose_ops(style.deep,
                         y=136.0 if style.buck_teeth else 142.0)
        ops += _smile_and_jaw(
            128, 166, mouth,
            tongue_scale=1.4 if style.big_tongue else 1.0,
            fangs=style.fangs,
        )

    if style.blep and mouth < 0.18:
        # Pickles' resting blep: a tongue tip parked past the closed smile.
        # It fades on the same ramp the smile does, so the open jaw's rising
        # tongue takes over without two tongues ever showing.
        fade = 1.0 - mouth / 0.18
        ops.append(Ellipse(117, 167, 22, 21, TONGUE, fade))
        ops.append(Stroke(((128, 174), (128, 184)), BLUSH, 2.5, fade * 0.55))

    if style.buck_teeth:
        # Two incisors on their own dark pad: cream teeth on a cream cheek
        # would disappear the moment the mouth closed.  They stay put as the
        # jaw opens, so the pair hangs into the cavity the way real ones do.
        ops += [RoundedRect(110, 150, 36, 25, 8, MOUTH),
                RoundedRect(111.5, 151.5, 16, 22, 5, CATCH),
                RoundedRect(128.5, 151.5, 16, 22, 5, CATCH)]

    if style.whiskers:
        for y, dy in ((152, -5), (160, 0), (168, 5)):
            ops.append(Stroke(((90, y), (68, y + dy * 0.4), (48, y + dy)),
                              style.deep, 2.2, 0.5))
            ops.append(Stroke(((166, y), (188, y + dy * 0.4), (208, y + dy)),
                              style.deep, 2.2, 0.5))

    return ops + _whisper_ops(level)


def _parrot_ops(mouth: float, level: float, blink: float,
                gaze: Point = (0.0, 0.0)) -> list[Op]:
    """Pastel emerald head, stubby gold crest, small hooked beak."""
    ops: list[Op] = [
        # Three gold crest feathers fanned above the crown, center tallest.
        # Chibi keeps them short and round so the crest reads as a tuft.
        Curve(
            (100, 72),
            (((88, 50), (85, 28), (98, 22)),
             ((110, 17), (117, 42), (116, 62)),
             ((114, 70), (104, 76), (100, 72))),
            BEAK_LO,
        ),
        Curve(
            (156, 72),
            (((168, 50), (171, 28), (158, 22)),
             ((146, 17), (139, 42), (140, 62)),
             ((142, 70), (152, 76), (156, 72))),
            BEAK_LO,
        ),
        Curve(
            (114, 64),
            (((109, 36), (114, 10), (128, 10)),
             ((142, 10), (147, 36), (142, 64)),
             ((138, 72), (118, 72), (114, 64))),
            BEAK_UP,
        ),
        Ellipse(30, 52, 196, 184, EMERALD),
    ]
    ops += _clay_ops(DEEP)
    # Bare facial patches behind the eyes, the parrot's white "spectacles".
    ops += [Ellipse(70, 102, 32, 36, CATCH, 0.92),
            Ellipse(154, 102, 32, 36, CATCH, 0.92)]
    # Pink over emerald goes gray, so the rouge sits on small cream pads.
    ops += [Ellipse(44, 148, 38, 21, CATCH, 0.55),
            Ellipse(174, 148, 38, 21, CATCH, 0.55)]
    ops += _blush_ops(0.6)
    ops += _eye_ops(80, 158, 108, 17, 21, blink, gaze=gaze)

    # Beak stack, bottom-up: dark gape, then the gold hook whose tip ends
    # above the mandible, then the mandible that swings down.  The gape only
    # becomes visible in the band the mandible vacates, so a closed beak is
    # seamless and an open one shows a real dark mouth.
    drop = mouth * 12.0
    ops.append(Polygon(((114, 160), (142, 160), (128, 180 + drop)), MOUTH))
    # Upper beak: broad at the cere, curling to a blunt hook tip that hangs
    # slightly lower than its sides, the classic parrot profile.
    ops.append(Curve(
        (108, 146),
        (((106, 158), (112, 168), (122, 173)),
         ((127, 176), (131, 176), (134, 172)),
         ((144, 164), (149, 154), (149, 146)),
         ((144, 138), (112, 138), (108, 146))),
        BEAK_UP,
    ))
    ops.append(Curve(
        (116, 168 + drop),
        (((121, 172 + drop), (135, 172 + drop), (140, 168 + drop)),
         ((133, 185 + drop), (123, 185 + drop), (116, 168 + drop))),
        BEAK_LO,
    ))
    ops += [Ellipse(120, 143, 3.5, 4, MOUTH, 0.5),
            Ellipse(132, 143, 3.5, 4, MOUTH, 0.5)]
    return ops + _whisper_ops(level)


def _owl_ops(mouth: float, level: float, blink: float,
             gaze: Point = (0.0, 0.0)) -> list[Op]:
    mint = FACE_CHIP_COLORS["owl"]
    deep = (0.475, 0.427, 0.635)
    cream = (0.929, 0.906, 1.000)
    gx, gy = _clamp_gaze(gaze)
    ops: list[Op] = [
        # Horn tufts big enough to survive the chibi head swallowing them.
        Polygon(((38, 108), (60, 34), (106, 78)), deep),
        Polygon(((150, 78), (196, 34), (218, 108)), deep),
        Ellipse(28, 50, 200, 188, mint),
    ]
    ops += _clay_ops(deep)
    ops += [Ellipse(x, 100, 64, 64, cream) for x in (60, 132)]
    # Pink over mint goes gray, so the rouge sits on small cream pads.
    ops += [Ellipse(42, 150, 38, 21, cream, 0.6),
            Ellipse(176, 150, 38, 21, cream, 0.6)]
    ops += [Ellipse(44, 151, 36, 20, BLUSH, 0.55),
            Ellipse(178, 151, 36, 20, BLUSH, 0.55)]

    # Golden iris rings around wide pupils; the iris, pupil, and catchlights
    # drift with ``gaze`` while the outer eye stays put.
    if blink >= 0.75:
        ops += [Arc(92, 136, 13, 200, 340, DARK_EYE, 5.5),
                Arc(164, 136, 13, 200, 340, DARK_EYE, 5.5)]
    else:
        squash = 1.0 - 0.88 * blink
        for cx in (92.0, 164.0):
            outer_h = 30.0 * squash
            ops.append(Ellipse(cx - 13, 132 - outer_h / 2.0, 26, outer_h,
                               DARK_EYE))
            iris_h = 20.0 * squash
            ops.append(Ellipse(cx - 8.5 + gx * 0.8,
                               132 + gy * 0.8 - iris_h / 2.0, 17, iris_h,
                               IRIS_GOLD))
            pupil_h = 11.0 * squash
            ops.append(Ellipse(cx - 4.5 + gx * 0.8,
                               132 + gy * 0.8 - pupil_h / 2.0, 9, pupil_h,
                               DARK_EYE))
            if blink < 0.45:
                ops.append(Ellipse(cx - 1 + gx * 0.8,
                                   132 + gy * 0.8 - outer_h * 0.30,
                                   6, 7 * squash, CATCH))
                ops.append(Ellipse(cx - 6 + gx * 0.8,
                                   132 + gy * 0.8 + outer_h * 0.12,
                                   3, 3.4 * squash, CATCH, 0.85))

    ops += _beak_ops(152, mouth)
    # Two rows of chest chevrons read as feathers where the parrot wears its
    # single scallop, so the two birds stay distinct below the beak.
    for row_y, spread in ((202.0, 14.0), (216.0, 10.0)):
        ops.append(Stroke(((128 - spread, row_y), (128, row_y + 6),
                           (128 + spread, row_y)), deep, 4.0, 0.35))
    return ops + _whisper_ops(level)


def character_ops(face: str, mouth: float, level: float,
                  blink: float = 0.0,
                  gaze: Point = (0.0, 0.0)) -> tuple[Op, ...]:
    """Ordered draw ops for ``face`` at one animation instant.

    ``mouth``, ``level``, and ``blink`` are all 0..1; ``gaze`` is a small
    pupil-drift offset clamped to a few points.  Animation offsets are baked
    into the returned coordinates so a renderer never has to manage a
    transform stack.  ``blink`` and ``gaze`` only ever move in live views;
    the exported frames keep both at rest.
    """
    mouth = _clamp01(mouth)
    level = _clamp01(level)
    blink = _clamp01(blink)
    if face == "parrot":
        ops = _parrot_ops(mouth, level, blink, gaze)
    elif face == "owl":
        ops = _owl_ops(mouth, level, blink, gaze)
    else:
        ops = _companion_ops(face, mouth, level, blink, gaze)
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
