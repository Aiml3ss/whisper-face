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

The look is Sticker: flat saturated fills, one thick ink outline around the
whole character (never around an inner feature), and an optional hard shadow
of that same silhouette.  Both are ordinary ops at the front of the list, so
every renderer draws them without knowing what an outline is; see
``character_ops`` for how a surface asks for the shadow.

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

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

Color = tuple[float, float, float]
Point = tuple[float, float]

# Every character is drawn inside this square, y down, before the HUD scales
# and squashes it.
DESIGN_SIZE = 256.0

# The sticker palette.  Every hue is the old pastel turned up: saturation
# roughly half again as strong, value a touch lower, then tuned by eye so
# each animal stays as easy to tell apart as it was.  The chips in
# ``whisper_face_theme.FACE_CHIP_COLORS`` keep the pastels, because a chip is
# the ground and the character standing on it is the loud thing; a test
# holds each head to its chip's hue so the chip still names its animal.
DARK_EYE = (0.165, 0.129, 0.110)         # #2a211c, warm near-black
INK = DARK_EYE                           # the outline and its hard shadow
MOUTH = (0.200, 0.157, 0.122)            # #33281f
CATCH = (1.000, 0.992, 0.973)            # #fffdf8, warm porcelain catchlight
EMERALD = (0.286, 0.869, 0.626)          # #49dda0, parrot head
BEAK_UP = (0.950, 0.687, 0.106)          # #f2af1b
BEAK_LO = (0.922, 0.572, 0.000)          # #eb9200
MINT = (0.405, 0.919, 0.812)             # #67eacf, owl head and speech puffs
OWL_DEEP = (0.351, 0.269, 0.622)         # #59459f, horn tufts and chevrons
OWL_CREAM = (0.828, 0.778, 0.980)        # #d3c6fa, facial discs
TONGUE = (0.946, 0.341, 0.426)           # #f1576d
BLUSH = (0.956, 0.483, 0.504)            # #f47b80, rouge laid over a warm head
ROUGE = (0.974, 0.687, 0.692)            # #f8afb0, the same rouge as a flat
#                                          opaque color, for the green heads
EAR_TIP = (0.420, 0.180, 0.070)          # #6b2e12, fox ear caps
IRIS_GOLD = BEAK_UP                      # owl iris, same gold as the beaks

# Sticker proportions, in design units.  The outline is grown from the
# character's body shapes; the hard shadow is that same silhouette, offset.
OUTLINE = 6.5
SHADOW_OFFSET: Point = (7.0, 9.0)
EYE_SCALE = 1.14                         # eyes, about their old centers
HEAVY = 1.35                             # whiskers, brows, stripes, chevrons
SMILE_WIDTH = 5.6                        # closed smile
LID_WIDTH = 6.0                          # the happy lid of a blink


@dataclass(frozen=True)
class Ellipse:
    """Axis-aligned oval, given as its bounding box.

    ``grow``, here and on the other three filled ops, swells the shape by
    that many design units on every side with round corners: a renderer
    fills the shape and then strokes it in the same color at twice the
    width.  It is how the sticker outline and its hard shadow are drawn, and
    it is only ever set on opaque ops, where fill and stroke may overlap.
    """
    x: float
    y: float
    w: float
    h: float
    color: Color
    alpha: float = 1.0
    grow: float = 0.0


@dataclass(frozen=True)
class Polygon:
    points: tuple[Point, ...]
    color: Color
    alpha: float = 1.0
    grow: float = 0.0


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
    grow: float = 0.0


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
    grow: float = 0.0


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

    ``fit`` shrinks the three faces whose ears or quills reach the edge of
    the design box, so their outline and hard shadow still land inside it.
    See ``_fitted``.
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
    fit: float = 1.0


COMPANIONS: Mapping[str, CharacterStyle] = {
    "fox": CharacterStyle(
        head=(0.980, 0.471, 0.282),         # #fa7848
        deep=(0.796, 0.226, 0.000),         # #cb3a00
        muzzle=(0.980, 0.861, 0.677),       # #fadcad
        cheek_fluff=True,
        ear_tips=True,
    ),
    "cat": CharacterStyle(
        head=(0.755, 0.666, 0.949),         # #c1aaf2
        deep=(0.253, 0.352, 0.688),         # #415aaf
        muzzle=(0.778, 0.862, 0.980),       # #c6dcfa
        whiskers=True,
        fangs=True,
    ),
    "bear": CharacterStyle(
        head=(0.972, 0.729, 0.128),         # #f8ba21
        deep=(0.564, 0.342, 0.189),         # #905730
        muzzle=(0.930, 0.725, 0.487),       # #edb97c
        ears="round",
    ),
    "dog": CharacterStyle(
        head=(0.899, 0.667, 0.381),         # #e5aa61
        deep=(0.704, 0.463, 0.197),         # #b37632
        muzzle=(0.976, 0.894, 0.763),       # #f9e4c2
        ears="floppy",
        brow_patch=True,
        big_tongue=True,
    ),
    "wolf": CharacterStyle(
        head=(0.714, 0.757, 0.820),         # #b6c1d1
        deep=(0.373, 0.455, 0.588),         # #5f7496
        muzzle=(0.910, 0.933, 0.965),       # #e8eef6
        cheek_fluff=True,
        stern_brows=True,
        blush_alpha=0.34,
    ),
    "pig": CharacterStyle(
        head=(0.961, 0.617, 0.674),         # #f59dac
        deep=(0.860, 0.399, 0.513),         # #db6683
        muzzle=(0.980, 0.784, 0.811),       # #fac8cf
        ears="round",
        snout=True,
        blush_alpha=0.65,
    ),
    "panda": CharacterStyle(
        head=(0.922, 0.918, 0.922),         # #ebeaeb
        deep=(0.288, 0.323, 0.358),         # #49525b
        muzzle=(1.000, 1.000, 1.000),       # #ffffff
        ears="round",
        patches=True,
        inner_ears=False,
        blush_alpha=0.55,
    ),
    "tiger": CharacterStyle(
        head=(0.972, 0.611, 0.223),         # #f89c39
        deep=(0.854, 0.369, 0.000),         # #da5e00
        muzzle=(0.980, 0.872, 0.719),       # #fadeb7
        stripes=True,
    ),
    "frog": CharacterStyle(
        head=(0.621, 0.869, 0.251),         # #9edd40
        deep=(0.295, 0.622, 0.000),         # #4b9f00
        muzzle=(0.939, 0.968, 0.714),       # #eff7b6
        ears="none",
        eye_domes=True,
        wide_mouth=True,
        blush_alpha=0.55,
    ),
    "rabbit": CharacterStyle(
        head=(0.945, 0.388, 0.561),         # #f1638f
        deep=(0.850, 0.232, 0.414),         # #d93b6a
        muzzle=(1.000, 0.949, 0.957),       # #fff2f4
        ears="long",
        buck_teeth=True,
        blush_alpha=0.55,
    ),
    "hedgehog": CharacterStyle(
        head=(0.730, 0.517, 0.311),         # #ba844f
        deep=(0.522, 0.310, 0.129),         # #854f21
        muzzle=(0.980, 0.883, 0.760),       # #fae1c2
        ears="none",
        quills=True,
        snout_point=True,
        blush_alpha=0.42,
        fit=0.945,
    ),
    "penguin": CharacterStyle(
        head=(0.236, 0.696, 0.922),         # #3cb1eb
        deep=(0.063, 0.230, 0.434),         # #103b6f
        muzzle=(1.000, 0.996, 0.965),       # #fffef6
        ears="none",
        hood=True,
        beak=True,
        blush_alpha=0.45,
    ),
    "pickles": CharacterStyle(
        head=(0.945, 0.844, 0.621),         # #f1d79e
        deep=(0.788, 0.531, 0.145),         # #c98725
        muzzle=(0.980, 0.925, 0.807),       # #faecce
        ears="retriever",
        big_tongue=True,
        blep=True,
        blush_alpha=0.45,
        fit=0.962,
    ),
    "olive": CharacterStyle(
        head=(0.930, 0.844, 0.696),         # #edd7b1
        deep=(0.792, 0.588, 0.280),         # #ca9647
        muzzle=(1.000, 0.984, 0.949),       # #fffbf2
        ears="retriever",
        bow=True,
        blush_alpha=0.45,
        fit=0.962,
    ),
}

# The head color of every face, for surfaces that pick a ground to set the
# character against.
FACE_HEAD_COLORS: Mapping[str, Color] = {
    "parrot": EMERALD,
    "owl": MINT,
    **{face: style.head for face, style in COMPANIONS.items()},
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
    # Sticker eyes are bigger than the box the caller names, about the same
    # centers, so every face grows its eyes by one rule.
    left -= w * (EYE_SCALE - 1.0) / 2.0
    right -= w * (EYE_SCALE - 1.0) / 2.0
    y -= h * (EYE_SCALE - 1.0) / 2.0
    w *= EYE_SCALE
    h *= EYE_SCALE
    centers = (left + w / 2.0, right + w / 2.0)
    if blink >= 0.75:
        for cx in centers:
            ops.append(Arc(cx, y + h * 0.62, w * 0.52, 200, 340,
                           lid, LID_WIDTH))
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
        ops.append(Arc(cx, top - 4.0, 10.5, 38, 142, MOUTH, SMILE_WIDTH,
                       fade))
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
               inset: float = 0.0, solid: bool = False) -> list[Op]:
    """Big chibi rouge, tucked right under the eyes.

    ``y`` follows the eyes on faces that moved them, and ``inset`` pulls the
    pads toward the center on faces whose cheeks are not the head color.
    ``solid`` is for the green heads: pink laid over green goes gray, so
    they wear the rouge as one flat opaque color instead of a tint.
    """
    color, alpha = (ROUGE, 1.0) if solid else (BLUSH, alpha)
    return [Ellipse(42 + inset, y, 42, 24, color, alpha),
            Ellipse(172 - inset, y, 42, 24, color, alpha)]


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


def _companion_ops(face: str, mouth: float, blink: float,
                   gaze: Point = (0.0, 0.0)) -> tuple[list[Op], list[Op]]:
    """``(body, ops)`` for one template companion.

    ``ops`` is everything, in paint order.  ``body`` names the few of them
    that reach the character's outer edge -- ears, head, anything worn past
    the head -- which is what the sticker outline is grown from.  Features
    stay out of it so the outline never rings an eye or a nose.
    """
    style = COMPANIONS.get(face, COMPANIONS["fox"])
    ops: list[Op] = []
    body: list[Op] = []

    # Ears sit behind the shared rounded head, tucked low so the silhouette
    # stays one soft blob.
    if style.ears == "round":
        ops += [Ellipse(46, 40, 52, 52, style.deep),
                Ellipse(158, 40, 52, 52, style.deep)]
        body += ops     # nothing is drawn before the ears, so this is them
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
        body += ops
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
    elif style.ears == "retriever":
        # A golden's ears are the breed marker: set at eye level, broad,
        # and hanging past the cheek with a feathered edge. The shared
        # floppy teardrop tucks inside the head blob and disappears
        # entirely at chip size, which is why the litter-mates read as
        # round-eared rodents rather than as dogs. These deliberately
        # break the silhouette -- the outer edge swings well outside the
        # head ellipse (x 30..226) so the pair is visible before any
        # feature is, and the root stays hidden under the crown so the
        # head still reads as one soft blob.
        for flip in (False, True):
            def x(value: float) -> float:
                return 256.0 - value if flip else value

            ops.append(Curve(
                (x(96), 70),
                (((x(24), 64), (x(-8), 134), (x(12), 194)),
                 ((x(20), 226), (x(76), 234), (x(98), 204)),
                 ((x(112), 152), (x(118), 92), (x(96), 70))),
                style.deep,
            ))
            body.append(ops[-1])
            # Feathering: one paler wave riding the inner edge, the fringe
            # a golden carries down the length of the ear. Low alpha so it
            # reads as fur at 208 points and vanishes politely at 18.
            ops.append(Curve(
                (x(44), 128),
                (((x(28), 156), (x(26), 188), (x(42), 206)),
                 ((x(58), 222), (x(80), 214), (x(84), 190)),
                 ((x(78), 162), (x(64), 138), (x(44), 128))),
                style.muzzle, 0.30,
            ))
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
        body += ops
    else:
        # Short, wide triangles: pointed species keep their ears, chibi
        # keeps them stubby.
        ops += [
            Polygon(((50, 92), (66, 36), (112, 76)), style.deep),
            Polygon(((144, 76), (190, 36), (206, 92)), style.deep),
        ]
        body += ops
        ops += [
            Polygon(((64, 76), (70, 52), (94, 72)), style.muzzle),
            Polygon(((162, 72), (186, 52), (192, 76)), style.muzzle),
        ]
        if style.ear_tips:
            # Tip triangles ride the outer ear's own edges so the dark fur
            # caps the apex instead of floating inside the ear.  They are
            # brown rather than near-black, and reach well down the ear, so
            # the cap stays a marking instead of thickening the ink outline.
            ops += [Polygon(((59.3, 59.5), (66, 36), (85.3, 52.8)), EAR_TIP),
                    Polygon(((170.7, 52.8), (190, 36), (196.7, 59.5)),
                            EAR_TIP)]

    if style.eye_domes:
        # Head-colored bulges on the crown.  A frog's eyes are not on its
        # face, they are on top of its skull, and drawing them in the head
        # color keeps the silhouette one blob with two bumps.
        ops += [Ellipse(44, 32, 70, 70, style.head),
                Ellipse(142, 32, 70, 70, style.head)]
        body += ops[-2:]
    if style.quills:
        ops += _quill_ops(style.deep)
        body += _quill_ops(style.deep)

    ops.append(Ellipse(30, 52, 196, 184, style.head))
    body.append(ops[-1])

    if style.cheek_fluff:
        # Two soft fur bumps per jaw side, drawn before the cheeks so only
        # their outer tips peek past the blob silhouette.
        ops += [
            Polygon(((70, 148), (34, 158), (68, 172)), style.muzzle),
            Polygon(((72, 172), (40, 184), (74, 192)), style.muzzle),
            Polygon(((186, 148), (222, 158), (188, 172)), style.muzzle),
            Polygon(((184, 172), (216, 184), (182, 192)), style.muzzle),
        ]
        body += ops[-4:]

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

    if style.quills:
        # A dark spiny mantle over the crown.  The fan behind the head
        # breaks the outline; this is what it grows from.
        ops.append(Curve(
            (48, 100),
            (((48, 66), (84, 52), (128, 52)),
             ((172, 52), (208, 66), (208, 100)),
             ((172, 112), (84, 112), (48, 100))),
            style.deep,
        ))

    if style.eye_domes:
        # The eyeballs come back on top of the head so the domes keep a rim
        # of head color.
        ops += _blush_ops(style.blush_alpha, y=126.0, inset=4.0, solid=True)
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
        ops += [
            Stroke(((82, 98), (102, 102)), style.deep, 4.0 * HEAVY, 0.9),
            Stroke(((174, 98), (154, 102)), style.deep, 4.0 * HEAVY, 0.9),
        ]

    if style.stripes:
        # Three stubby crown stripes, round-capped so they read as plush
        # markings instead of brush strokes.
        ops += [
            Stroke(((128, 58), (128, 84)), style.deep, 6.0 * HEAVY, 0.9),
            Stroke(((106, 62), (110, 86)), style.deep, 5.0 * HEAVY, 0.9),
            Stroke(((150, 62), (146, 86)), style.deep, 5.0 * HEAVY, 0.9),
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
        body += ops[-2:]
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
        body.append(ops[-1])
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
        ops.append(Stroke(((128, 174), (128, 184)), BLUSH, 2.5 * HEAVY,
                          fade * 0.55))

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
                              style.deep, 2.2 * HEAVY, 0.7))
            ops.append(Stroke(((166, y), (188, y + dy * 0.4), (208, y + dy)),
                              style.deep, 2.2 * HEAVY, 0.7))

    return body, ops


def _parrot_ops(mouth: float, blink: float,
                gaze: Point = (0.0, 0.0)) -> tuple[list[Op], list[Op]]:
    """Emerald head, stubby gold crest, small hooked beak."""
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
    body = list(ops)
    # Bare facial patches behind the eyes, the parrot's white "spectacles".
    ops += [Ellipse(70, 102, 32, 36, CATCH, 0.92),
            Ellipse(154, 102, 32, 36, CATCH, 0.92)]
    ops += _blush_ops(1.0, solid=True)
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
    return body, ops


def _owl_ops(mouth: float, blink: float,
             gaze: Point = (0.0, 0.0)) -> tuple[list[Op], list[Op]]:
    mint = MINT
    deep = OWL_DEEP
    cream = OWL_CREAM
    gx, gy = _clamp_gaze(gaze)
    ops: list[Op] = [
        # Horn tufts big enough to survive the chibi head swallowing them.
        Polygon(((38, 108), (60, 34), (106, 78)), deep),
        Polygon(((150, 78), (196, 34), (218, 108)), deep),
        Ellipse(28, 50, 200, 188, mint),
    ]
    body = list(ops)
    ops += [Ellipse(x, 100, 64, 64, cream) for x in (60, 132)]
    ops += [Ellipse(42, 150, 38, 21, ROUGE),
            Ellipse(176, 150, 38, 21, ROUGE)]

    # Golden iris rings around wide pupils; the iris, pupil, and catchlights
    # drift with ``gaze`` while the outer eye stays put.
    # Like every sticker eye, the whole stack is EYE_SCALE larger about the
    # same center.
    big = EYE_SCALE
    if blink >= 0.75:
        ops += [Arc(92, 136, 13 * big, 200, 340, DARK_EYE, LID_WIDTH),
                Arc(164, 136, 13 * big, 200, 340, DARK_EYE, LID_WIDTH)]
    else:
        squash = 1.0 - 0.88 * blink
        for cx in (92.0, 164.0):
            outer_h = 30.0 * big * squash
            ops.append(Ellipse(cx - 13 * big, 132 - outer_h / 2.0,
                               26 * big, outer_h, DARK_EYE))
            iris_h = 20.0 * big * squash
            ops.append(Ellipse(cx - 8.5 * big + gx * 0.8,
                               132 + gy * 0.8 - iris_h / 2.0,
                               17 * big, iris_h, IRIS_GOLD))
            pupil_h = 11.0 * big * squash
            ops.append(Ellipse(cx - 4.5 * big + gx * 0.8,
                               132 + gy * 0.8 - pupil_h / 2.0,
                               9 * big, pupil_h, DARK_EYE))
            if blink < 0.45:
                ops.append(Ellipse(cx - 1 * big + gx * 0.8,
                                   132 + gy * 0.8 - outer_h * 0.30,
                                   6 * big, 7 * big * squash, CATCH))
                ops.append(Ellipse(cx - 6 * big + gx * 0.8,
                                   132 + gy * 0.8 + outer_h * 0.12,
                                   3 * big, 3.4 * big * squash, CATCH, 0.85))

    ops += _beak_ops(152, mouth)
    # Two rows of chest chevrons read as feathers where the parrot wears its
    # single scallop, so the two birds stay distinct below the beak.
    for row_y, spread in ((202.0, 14.0), (216.0, 10.0)):
        ops.append(Stroke(((128 - spread, row_y), (128, row_y + 6),
                           (128 + spread, row_y)),
                          deep, 4.0 * HEAVY, 0.35))
    return body, ops


# Every head rests its chin on this point, and a face that has to shrink
# shrinks toward it, so a row of faces keeps one baseline.
CHIN: Point = (128.0, 236.0)


def _moved(op: Op, scale: float, dx: float, dy: float) -> Op:
    """``op`` scaled about ``CHIN`` and then shifted; line widths follow."""
    def at(point: Point) -> Point:
        return (CHIN[0] + (point[0] - CHIN[0]) * scale + dx,
                CHIN[1] + (point[1] - CHIN[1]) * scale + dy)

    if isinstance(op, (Ellipse, RoundedRect)):
        x, y = at((op.x, op.y))
        moved = replace(op, x=x, y=y, w=op.w * scale, h=op.h * scale)
        if isinstance(op, RoundedRect):
            moved = replace(moved, radius=op.radius * scale)
        return moved
    if isinstance(op, Polygon):
        return replace(op, points=tuple(at(point) for point in op.points))
    if isinstance(op, Stroke):
        return replace(op, points=tuple(at(point) for point in op.points),
                       width=op.width * scale)
    if isinstance(op, Arc):
        cx, cy = at((op.cx, op.cy))
        return replace(op, cx=cx, cy=cy, radius=op.radius * scale,
                       width=op.width * scale)
    if isinstance(op, Curve):
        return replace(op, start=at(op.start), segments=tuple(
            (at(c1), at(c2), at(end)) for c1, c2, end in op.segments))
    raise TypeError(f"unsupported draw op: {type(op).__name__}")


def _fitted(ops: Sequence[Op], fit: float) -> list[Op]:
    """Keep a wide character, its outline, and its shadow inside the box.

    Thirteen faces already leave room and pass through untouched.  The
    hedgehog and the two goldens reach the box edge, so they shrink by
    ``fit`` toward the chin and slide left by half the shadow's reach, which
    centers sticker-plus-shadow as a pair.  The placement is the same
    whether or not the shadow is drawn: asking for it never moves the face.
    """
    if fit >= 1.0:
        return list(ops)
    return [_moved(op, fit, -SHADOW_OFFSET[0] / 2.0, 0.0) for op in ops]


def _inked(op: Op) -> Op:
    """``op`` as a piece of the ink silhouette, ``OUTLINE`` larger all round.

    Body shapes are filled ops today; a stroked one would simply widen.
    """
    if isinstance(op, (Stroke, Arc)):
        return replace(op, color=INK, alpha=1.0,
                       width=op.width + 2.0 * OUTLINE)
    return replace(op, color=INK, alpha=1.0, grow=OUTLINE)


def character_ops(face: str, mouth: float, level: float,
                  blink: float = 0.0,
                  gaze: Point = (0.0, 0.0), *,
                  shadow: bool = False,
                  whispers: bool = True) -> tuple[Op, ...]:
    """Ordered draw ops for ``face`` at one animation instant.

    ``mouth``, ``level``, and ``blink`` are all 0..1; ``gaze`` is a small
    pupil-drift offset clamped to a few points.  Animation offsets are baked
    into the returned coordinates so a renderer never has to manage a
    transform stack.  ``blink`` and ``gaze`` only ever move in live views;
    the exported frames keep both at rest.

    The list opens with the sticker outline: the character's body shapes in
    ink, each grown by ``OUTLINE``, so their union reads as one line around
    the whole character.  ``shadow=True`` puts a second copy of that
    silhouette under it, offset by ``SHADOW_OFFSET``.  The shadow belongs to
    the surface rather than the art: ask for it where the face is large and
    stands on its own, leave it off where the face is small or already sits
    in an outlined container.  Either way everything stays inside the design
    box and the face does not move.

    ``whispers=False`` drops the speech puffs for surfaces that show the
    character inside a tight chip.
    """
    mouth = _clamp01(mouth)
    level = _clamp01(level)
    blink = _clamp01(blink)
    fit = 1.0
    if face == "parrot":
        body, ops = _parrot_ops(mouth, blink, gaze)
    elif face == "owl":
        body, ops = _owl_ops(mouth, blink, gaze)
    else:
        body, ops = _companion_ops(face, mouth, blink, gaze)
        fit = COMPANIONS.get(face, COMPANIONS["fox"]).fit
    if whispers:
        ops = ops + _whisper_ops(level)
    silhouette = [_inked(op) for op in _fitted(body, fit)]
    drawn: list[Op] = []
    if shadow:
        drawn += [_moved(op, 1.0, *SHADOW_OFFSET) for op in silhouette]
    return tuple(drawn + silhouette + _fitted(ops, fit))


def _hex(color: Color) -> str:
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(channel * 255))) for channel in color)


def _svg_fill(op: Ellipse | Polygon | RoundedRect | Curve) -> str:
    """The paint attributes of a filled op, including its ``grow`` border."""
    paint = f' fill="{_hex(op.color)}"'
    if op.alpha < 1.0:
        paint += f' fill-opacity="{op.alpha:.3f}"'
    if op.grow > 0.0:
        paint += (f' stroke="{_hex(op.color)}"'
                  f' stroke-width="{2.0 * op.grow:g}"'
                  f' stroke-linejoin="round"')
    return paint


def _svg_op(op: Op) -> str:
    if isinstance(op, Ellipse):
        return (f'<ellipse cx="{op.x + op.w / 2:g}" cy="{op.y + op.h / 2:g}"'
                f' rx="{op.w / 2:g}" ry="{op.h / 2:g}"{_svg_fill(op)}/>')
    if isinstance(op, Polygon):
        points = " ".join(f"{x:g},{y:g}" for x, y in op.points)
        return f'<polygon points="{points}"{_svg_fill(op)}/>'
    if isinstance(op, Stroke):
        opacity = ("" if op.alpha >= 1.0
                   else f' stroke-opacity="{op.alpha:.3f}"')
        points = " ".join(f"{x:g},{y:g}" for x, y in op.points)
        return (f'<polyline points="{points}" fill="none"'
                f' stroke="{_hex(op.color)}"{opacity}'
                f' stroke-width="{op.width:g}" stroke-linecap="round"'
                f' stroke-linejoin="round"/>')
    if isinstance(op, RoundedRect):
        radius = min(op.radius, op.w / 2.0), min(op.radius, op.h / 2.0)
        return (f'<rect x="{op.x:g}" y="{op.y:g}" width="{op.w:g}"'
                f' height="{op.h:g}" rx="{radius[0]:g}" ry="{radius[1]:g}"'
                f'{_svg_fill(op)}/>')
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
        data = f"M{op.start[0]:g} {op.start[1]:g}"
        for (c1, c2, end) in op.segments:
            data += (f" C{c1[0]:g} {c1[1]:g} {c2[0]:g} {c2[1]:g}"
                     f" {end[0]:g} {end[1]:g}")
        data += " Z"
        return f'<path d="{data}"{_svg_fill(op)}/>'
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
                   whispers: bool = True, shadow: bool = False) -> str:
    """Character markup without the ``<svg>`` wrapper, for inlining.

    ``frame`` names an entry in ``FRAMES``.  ``whispers`` and ``shadow`` mean
    what they mean to ``character_ops``.

    The colored characters use no ``<mask>`` elements, so the returned markup
    carries no element ids and any number of them can share one document.
    """
    mouth, level = FRAMES[frame]
    return "".join(_svg_op(op) for op in character_ops(
        face, mouth, level, shadow=shadow, whispers=whispers))


def character_svg(face: str, *, frame: str = "idle",
                  whispers: bool = True, shadow: bool = False) -> str:
    """Standalone colored SVG for ``face``."""
    size = f"{DESIGN_SIZE:g}"
    body = character_body(face, frame=frame, whispers=whispers,
                          shadow=shadow)
    return (f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' viewBox="0 0 {size} {size}" width="{size}" height="{size}"'
            f' role="img" aria-hidden="true">{body}</svg>')
