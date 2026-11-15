#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Headless tests for the shared Whisper Face character spec.

The sixteen characters are drawn by two renderers: the HUD and the window
replay the op list through Core Graphics, and ``scripts/generate_face_art.py``
replays it into SVG. These tests pin the seam so the two cannot drift, pin
the character traits that make each face recognizable as its own animal, and
pin the Sticker look: one ink outline around the whole character, an optional
hard shadow, and all of it inside the design box.
"""

import ast
import colorsys
import math
import subprocess
import sys
import typing
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import whisper_face_render
from whisper_face_characters import (
    COMPANIONS,
    DESIGN_SIZE,
    EYE_SCALE,
    FACE_HEAD_COLORS,
    FACE_ORDER,
    FRAMES,
    INK,
    OUTLINE,
    QUILL_GROW,
    SHADOW_OFFSET,
    Arc,
    Curve,
    Ellipse,
    Op,
    Polygon,
    RoundedRect,
    Stroke,
    _svg_op,
    character_body,
    character_ops,
    character_svg,
)
from whisper_face_theme import FACE_CHIP_COLORS

# Every animation corner a live view can reach: mouth, level, blink, gaze.
POSES = (
    (0.0, 0.0, 0.0, (0.0, 0.0)),
    (0.45, 0.5, 0.0, (0.0, 0.0)),
    (1.0, 1.0, 0.0, (3.0, 3.0)),
    (0.0, 1.0, 1.0, (-3.0, -3.0)),
    (1.0, 1.0, 0.5, (3.0, -3.0)),
)


def op_bounds(op):
    """The painted extent of one op: (left, top, right, bottom).

    Counts everything a renderer adds to the bare geometry: half the width
    of a stroke, and the ``grow`` border of a filled op. Curves are walked
    along the path, because a control point can sit outside the box while
    the curve it steers stays inside.
    """
    if isinstance(op, (Ellipse, RoundedRect)):
        points, pad = [(op.x, op.y), (op.x + op.w, op.y + op.h)], op.grow
    elif isinstance(op, Polygon):
        points, pad = list(op.points), op.grow
    elif isinstance(op, Stroke):
        points, pad = list(op.points), op.width / 2.0
    elif isinstance(op, Arc):
        sweep = op.end_degrees - op.start_degrees
        steps = max(2, int(abs(sweep) // 2) + 1)
        points = [
            (op.cx + op.radius * math.cos(math.radians(
                op.start_degrees + sweep * step / (steps - 1))),
             op.cy + op.radius * math.sin(math.radians(
                 op.start_degrees + sweep * step / (steps - 1))))
            for step in range(steps)]
        pad = op.width / 2.0
    elif isinstance(op, Curve):
        points, pad, here = [], op.grow, op.start
        for c1, c2, end in op.segments:
            for step in range(41):
                t = step / 40.0
                u = 1.0 - t
                points.append(tuple(
                    u ** 3 * here[axis] + 3 * u * u * t * c1[axis]
                    + 3 * u * t * t * c2[axis] + t ** 3 * end[axis]
                    for axis in (0, 1)))
            here = end
    else:
        raise AssertionError(f"unhandled op {type(op).__name__}")
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def union_bounds(ops):
    boxes = [op_bounds(op) for op in ops]
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


def outline_of(ops):
    """The grown ink ops, which are the sticker outline (and its shadow)."""
    return [op for op in ops if getattr(op, "grow", 0.0) > 0.0]


def head_of(ops):
    """The head: the largest oval in the outline."""
    return max((op for op in outline_of(ops) if isinstance(op, Ellipse)),
               key=lambda op: op.w * op.h)


def inside_triangle(point, triangle, slack=0.0):
    """Whether ``point`` lies in ``triangle``, within ``slack`` units."""
    (ax, ay), (bx, by), (cx, cy) = triangle
    turn = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    for (x0, y0), (x1, y1) in (((ax, ay), (bx, by)), ((bx, by), (cx, cy)),
                               ((cx, cy), (ax, ay))):
        side = (x1 - x0) * (point[1] - y0) - (y1 - y0) * (point[0] - x0)
        length = math.hypot(x1 - x0, y1 - y0)
        if side * (1 if turn > 0 else -1) < -slack * length:
            return False
    return True


class CharacterSpecTests(unittest.TestCase):
    def test_every_menu_bar_face_has_a_character(self):
        # The flat silhouettes drive the face picker, so the colored spec has
        # to cover exactly the same ten names.
        silhouettes = {
            path.name.rsplit("-", 1)[0]
            for path in (ROOT / "icons" / "faces").glob("*.svg")
        }
        self.assertEqual(set(FACE_ORDER), silhouettes)

    def test_companions_cover_every_face_except_parrot_and_owl(self):
        self.assertEqual(
            set(COMPANIONS) | {"parrot", "owl"}, set(FACE_ORDER))

    def test_each_character_draws_something(self):
        for face in FACE_ORDER:
            with self.subTest(face=face):
                self.assertGreater(len(character_ops(face, 0.0, 0.0)), 4)

    def test_everything_painted_stays_inside_the_design_box(self):
        # A character that overflows the box gets clipped by the HUD stage and
        # letterboxed oddly in the app window chip. The outline and the hard
        # shadow are part of what is painted, so they are measured too, with
        # the width a renderer adds to them; callers never change geometry
        # to make room for either.
        for face in FACE_ORDER:
            for mouth, level, blink, gaze in POSES:
                for shadow in (False, True):
                    ops = character_ops(
                        face, mouth, level, blink, gaze, shadow=shadow)
                    for op in ops:
                        left, top, right, bottom = op_bounds(op)
                        with self.subTest(face=face, shadow=shadow,
                                          op=type(op).__name__):
                            self.assertGreaterEqual(left, 0.0)
                            self.assertGreaterEqual(top, 0.0)
                            self.assertLessEqual(right, DESIGN_SIZE)
                            self.assertLessEqual(bottom, DESIGN_SIZE)

    def test_talking_opens_the_mouth(self):
        for face in FACE_ORDER:
            with self.subTest(face=face):
                closed = character_ops(face, 0.0, 0.0)
                half = character_ops(face, 0.45, 0.5)
                open_ = character_ops(face, 1.0, 1.0)
                # Three distinct mouth positions, or the site's half frame
                # would silently collapse the flap back to two states.
                self.assertNotEqual(closed, half)
                self.assertNotEqual(half, open_)
                self.assertNotEqual(closed, open_)

    def test_blinking_changes_the_eyes_only_in_the_live_path(self):
        for face in FACE_ORDER:
            with self.subTest(face=face):
                awake = character_ops(face, 0.0, 0.0)
                lidded = character_ops(face, 0.0, 0.0, 1.0)
                self.assertNotEqual(awake, lidded)
                # The default keeps exported frames blink-free.
                self.assertEqual(awake, character_ops(face, 0.0, 0.0, 0.0))

    def test_gaze_drifts_eyes_only_in_the_live_path(self):
        for face in FACE_ORDER:
            with self.subTest(face=face):
                ahead = character_ops(face, 0.0, 0.0)
                glance = character_ops(face, 0.0, 0.0, 0.0, gaze=(3.0, -2.0))
                self.assertNotEqual(ahead, glance)
                # The default keeps exported frames looking straight ahead.
                self.assertEqual(
                    ahead, character_ops(face, 0.0, 0.0, 0.0, gaze=(0.0, 0.0)))
                # A closed lid ignores the glance, so blinks cannot jitter.
                self.assertEqual(
                    character_ops(face, 0.0, 0.0, 1.0),
                    character_ops(face, 0.0, 0.0, 1.0, gaze=(3.0, -2.0)))

    def test_existing_positional_calls_keep_working(self):
        # The HUD and the window call with positional mouth, level, blink and
        # a gaze; the sticker arguments are keyword-only with defaults.
        self.assertEqual(
            character_ops("fox", 0.5, 0.5, 0.2, (1.0, 1.0)),
            character_ops("fox", 0.5, 0.5, blink=0.2, gaze=(1.0, 1.0),
                          shadow=False, whispers=True))
        with self.assertRaises(TypeError):
            character_ops("fox", 0.5, 0.5, 0.2, (1.0, 1.0), True)

    def test_eyes_are_larger_about_the_same_centers(self):
        eyes = [op for op in character_ops("fox", 0.0, 0.0)
                if isinstance(op, Ellipse) and op.color == INK
                and op.grow == 0.0]
        self.assertEqual(len(eyes), 2)
        for eye, center in zip(eyes, (85.0, 171.0)):
            self.assertAlmostEqual(eye.x + eye.w / 2.0, center)
            self.assertAlmostEqual(eye.y + eye.h / 2.0, 119.0)
            self.assertAlmostEqual(eye.w, 18.0 * EYE_SCALE)
            self.assertAlmostEqual(eye.h, 22.0 * EYE_SCALE)
        self.assertGreater(EYE_SCALE, 1.1)

    def test_pig_has_a_snout_and_dog_has_floppy_ears(self):
        # Without these the pig renders as a pink bear and the dog disagrees
        # with its own menu-bar silhouette, which has always had hanging ears.
        self.assertTrue(COMPANIONS["pig"].snout)
        self.assertEqual(COMPANIONS["dog"].ears, "floppy")
        self.assertFalse(COMPANIONS["bear"].snout)

    def test_the_dogs_ears_hang_clear_of_its_head(self):
        # Ears tucked behind the head showed only as two bumps in the
        # outline, and the dog read as a plain tan ball. Like the ovals on
        # its menu-bar silhouette, they must hang visibly outside the head
        # on both sides, from above the eyes down to the cheek.
        ops = character_ops("dog", 0.0, 0.0)
        head = head_of(ops)
        ears = [op for op in outline_of(ops) if isinstance(op, Curve)]
        self.assertEqual(len(ears), 2)
        left, right = sorted(ears, key=lambda op: op_bounds(op)[0])
        clear = 12.0
        self.assertLessEqual(op_bounds(left)[0] + clear, op_bounds(head)[0])
        self.assertGreaterEqual(op_bounds(right)[2] - clear,
                                op_bounds(head)[2])
        eyes = [op for op in ops if isinstance(op, Ellipse)
                and op.color == INK and op.grow == 0.0]
        for ear in ears:
            _, top, _, bottom = op_bounds(ear)
            self.assertLess(top, min(eye.y for eye in eyes))
            self.assertGreater(bottom, max(eye.y + eye.h for eye in eyes)
                               + 40.0)

    def test_every_face_sits_on_the_same_center_line(self):
        # A face that had to shrink to fit shrinks toward its chin and
        # nowhere else. One shifted sideways sits off-center in every chip
        # and row that shows it without the shadow.
        for face in FACE_ORDER:
            for shadow in (False, True):
                ops = character_ops(face, 0.0, 0.0, shadow=shadow)
                head = head_of(ops[len(ops) - len(
                    character_ops(face, 0.0, 0.0)):])
                with self.subTest(face=face, shadow=shadow):
                    self.assertAlmostEqual(
                        head.x + head.w / 2.0, DESIGN_SIZE / 2.0)
        chins = {face: head_of(character_ops(face, 0.0, 0.0))
                 for face in COMPANIONS}
        for face, head in chins.items():
            with self.subTest(face=face):
                self.assertAlmostEqual(head.y + head.h, 236.0)

    def test_the_hedgehogs_quills_stay_spiky(self):
        # The full outline with round joins ends every spine in a blunt
        # knob. Each spine carries its own ink instead: a sharper triangle
        # that wraps the brown one and runs on past its tip to a point.
        ops = character_ops("hedgehog", 0.0, 0.0)
        ink = [op for op in outline_of(ops) if isinstance(op, Polygon)]
        quills = [op for op in ops if isinstance(op, Polygon)
                  and op.color == COMPANIONS["hedgehog"].deep]
        self.assertEqual(len(ink), 9)
        self.assertEqual(len(quills), 9)
        self.assertLess(QUILL_GROW, OUTLINE / 3.0)
        for sharp, quill in zip(ink, quills):
            self.assertEqual(sharp.grow, QUILL_GROW)
            for point in quill.points:
                self.assertTrue(
                    inside_triangle(point, sharp.points, slack=0.01))
            # The ink point runs well past the brown tip...
            beyond = math.dist(sharp.points[1], quill.points[1])
            self.assertGreater(beyond, OUTLINE)
            # ...while the line it leaves beside the tip is thinner than
            # the outline, which is what keeps the point a point.
            tip_x, tip_y = quill.points[1]
            (ax, ay), (bx, by) = sharp.points[0], sharp.points[1]
            beside = abs((bx - ax) * (tip_y - ay) - (by - ay) * (tip_x - ax)
                         ) / math.hypot(bx - ax, by - ay) + sharp.grow
            self.assertLess(beside, OUTLINE)
            self.assertGreater(beside, OUTLINE / 2.0)

    def test_each_companion_is_visually_distinct(self):
        # Same silhouette plus same accents means two faces a user cannot tell
        # apart in the picker.
        traits = {
            face: (style.head, style.ears, style.whiskers, style.patches,
                   style.stripes, style.snout)
            for face, style in COMPANIONS.items()
        }
        self.assertEqual(len(set(traits.values())), len(traits))


    def test_each_head_keeps_the_hue_of_its_chip(self):
        # The chips stay pastel and the characters carry their own saturated
        # palette, but a chip still has to name the animal standing on it.
        self.assertEqual(set(FACE_HEAD_COLORS), set(FACE_ORDER))
        for face in FACE_ORDER:
            chip = colorsys.rgb_to_hsv(*FACE_CHIP_COLORS[face])
            head = colorsys.rgb_to_hsv(*FACE_HEAD_COLORS[face])
            with self.subTest(face=face):
                # Never paler than its own chip.
                self.assertGreaterEqual(head[1], chip[1])
                if chip[1] < 0.1:
                    continue        # a gray chip has no hue to keep
                drift = abs(head[0] - chip[0])
                self.assertLess(min(drift, 1.0 - drift) * 360.0, 10.0)


class StickerOutlineTests(unittest.TestCase):
    def test_every_face_and_frame_opens_with_an_ink_outline(self):
        for face in FACE_ORDER:
            for mouth, level, blink, gaze in POSES:
                ops = character_ops(face, mouth, level, blink, gaze)
                outline = outline_of(ops)
                with self.subTest(face=face, mouth=mouth, blink=blink):
                    self.assertGreaterEqual(len(outline), 1)
                    # Drawn first, so it sits behind the whole character
                    # and never rings an inner feature.
                    self.assertEqual(list(ops[:len(outline)]), outline)
                    for op in outline:
                        self.assertEqual(op.color, INK)
                        # The full weight, except a piece that shaped its
                        # own outline (the hedgehog's spines).
                        self.assertIn(op.grow, (OUTLINE, QUILL_GROW))
                        # Fill and border overlap, which only an opaque
                        # op can do without a visible seam.
                        self.assertEqual(op.alpha, 1.0)
                    # The head always carries the full outline.
                    self.assertEqual(head_of(ops).grow, OUTLINE)

    def test_the_outline_does_not_move_with_the_animation(self):
        # It is grown from the body, and no animation input reshapes the
        # body: an outline that twitched with the mouth would read as a bug.
        for face in FACE_ORDER:
            at_rest = outline_of(character_ops(face, 0.0, 0.0))
            for mouth, level, blink, gaze in POSES:
                with self.subTest(face=face, mouth=mouth, blink=blink):
                    self.assertEqual(at_rest, outline_of(character_ops(
                        face, mouth, level, blink, gaze)))

    def test_the_outline_surrounds_every_feature(self):
        # The outline is grown from the shapes each builder names as its
        # body. A feature added later that pokes past them would be painted
        # with no ink around it, so nothing but the speech puffs may reach
        # outside the outline's own extent.
        for face in FACE_ORDER:
            for mouth, level, blink, gaze in POSES:
                ops = character_ops(face, mouth, level, blink, gaze,
                                    whispers=False)
                outline = outline_of(ops)
                edge = union_bounds(outline)
                for op in ops[len(outline):]:
                    left, top, right, bottom = op_bounds(op)
                    with self.subTest(face=face, op=type(op).__name__):
                        self.assertGreaterEqual(left, edge[0])
                        self.assertGreaterEqual(top, edge[1])
                        self.assertLessEqual(right, edge[2])
                        self.assertLessEqual(bottom, edge[3])

    def test_the_hard_shadow_is_off_unless_a_surface_asks(self):
        for face in FACE_ORDER:
            for mouth, level, blink, gaze in POSES:
                plain = character_ops(face, mouth, level, blink, gaze)
                shadowed = character_ops(
                    face, mouth, level, blink, gaze, shadow=True)
                outline = outline_of(plain)
                extra = len(shadowed) - len(plain)
                with self.subTest(face=face, mouth=mouth, blink=blink):
                    # One shadow piece per outline piece, under everything.
                    self.assertEqual(extra, len(outline))
                    # Asking for the shadow never moves or resizes the face.
                    self.assertEqual(shadowed[extra:], plain)
                    for cast, source in zip(shadowed[:extra], outline):
                        self.assertEqual(type(cast), type(source))
                        self.assertEqual(cast.color, INK)
                        self.assertEqual(cast.grow, source.grow)
                        ahead = op_bounds(source)
                        behind = op_bounds(cast)
                        for index, offset in enumerate(SHADOW_OFFSET * 2):
                            self.assertAlmostEqual(
                                behind[index] - ahead[index], offset)

    def test_whispers_can_be_dropped_without_touching_the_character(self):
        for face in FACE_ORDER:
            with self.subTest(face=face):
                loud = character_ops(face, 0.85, 0.8)
                quiet = character_ops(face, 0.85, 0.8, whispers=False)
                self.assertEqual(len(loud) - len(quiet), 3)
                self.assertEqual(loud[:len(quiet)], quiet)


class RendererCoverageTests(unittest.TestCase):
    """Both renderers must draw every op the spec can emit."""

    OP_TYPES = set(typing.get_args(Op))

    @staticmethod
    def _every_op():
        for face in FACE_ORDER:
            for mouth, level, blink, gaze in POSES:
                yield from character_ops(
                    face, mouth, level, blink, gaze, shadow=True)

    def test_the_spec_emits_every_op_type_it_declares(self):
        # Otherwise the two tests below would pass without exercising a
        # branch a renderer is supposed to have.
        self.assertEqual({type(op) for op in self._every_op()}, self.OP_TYPES)
        grown = {type(op) for op in self._every_op()
                 if getattr(op, "grow", 0.0) > 0.0}
        self.assertTrue({Ellipse, Polygon, Curve} <= grown)

    def test_svg_emitter_accepts_every_op(self):
        from xml.etree import ElementTree

        for op in self._every_op():
            element = ElementTree.fromstring(_svg_op(op))
            grow = getattr(op, "grow", 0.0)
            with self.subTest(op=type(op).__name__, grow=grow):
                if grow > 0.0:
                    self.assertEqual(element.get("stroke"),
                                     element.get("fill"))
                    self.assertEqual(float(element.get("stroke-width")),
                                     2.0 * grow)
                    self.assertEqual(element.get("stroke-linejoin"), "round")
                elif not isinstance(op, (Stroke, Arc)):
                    self.assertIsNone(element.get("stroke"))
        with self.assertRaises(TypeError):
            _svg_op(object())

    def test_svg_emitter_draws_a_grown_rounded_rect(self):
        # No face grows a rounded rect today, but the op carries the field,
        # so the emitter must not drop it the day one does.
        element_text = _svg_op(RoundedRect(10, 10, 40, 30, 8, INK, grow=4.0))
        self.assertIn('stroke-width="8"', element_text)
        self.assertIn('stroke-linejoin="round"', element_text)

    def test_core_graphics_replay_branches_on_every_op_type(self):
        # The gate runs these tests without AppKit, so the replay cannot be
        # executed here. Its dispatch is read from source instead: every op
        # type has an isinstance branch, the filled ones go through the
        # helper that draws ``grow``, and an unknown op is an error rather
        # than a silently missing shape.
        source = Path(whisper_face_render.__file__).read_text(
            encoding="utf-8")
        functions = {
            node.name: node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)}
        replay = functions["replay_ops"]
        branches = {
            call.args[1].id
            for call in ast.walk(replay)
            if isinstance(call, ast.Call)
            and getattr(call.func, "id", "") == "isinstance"
            and isinstance(call.args[1], ast.Name)}
        self.assertEqual(branches, {kind.__name__ for kind in self.OP_TYPES})
        self.assertTrue(any(isinstance(node, ast.Raise)
                            for node in ast.walk(replay)))
        fills = [call for call in ast.walk(replay)
                 if isinstance(call, ast.Call)
                 and getattr(call.func, "id", "") == "_render_fill"]
        self.assertEqual(len(fills), 4)      # Ellipse, Polygon, rect, Curve
        self.assertIn("op.grow", ast.unparse(functions["_render_fill"]))

    @staticmethod
    def _replayed(ops):
        """``ops`` drawn offscreen by the real replay; returns a pixel reader.

        The bitmap is tagged sRGB and flipped, so the reader takes design
        coordinates and returns the stored (r, g, b, a).
        """
        from AppKit import (
            NSAffineTransform,
            NSBitmapImageRep,
            NSColorSpace,
            NSDeviceRGBColorSpace,
            NSGraphicsContext,
        )

        size = int(DESIGN_SIZE)
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
            None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
        rep = rep.bitmapImageRepByRetaggingWithColorSpace_(
            NSColorSpace.sRGBColorSpace())
        NSGraphicsContext.setCurrentContext_(
            NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
        flip = NSAffineTransform.transform()
        flip.translateXBy_yBy_(0, size)
        flip.scaleXBy_yBy_(1, -1)
        flip.concat()
        whisper_face_render.replay_ops(ops)

        def pixel(x, y):
            color = rep.colorAtX_y_(int(x), int(y))
            return (color.redComponent(), color.greenComponent(),
                    color.blueComponent(), color.alphaComponent())
        return pixel

    def assertInk(self, pixel):
        self.assertEqual(pixel[3], 1.0)
        for got, want in zip(pixel, INK):
            self.assertAlmostEqual(got, want, delta=0.02)

    @unittest.skipUnless(whisper_face_render.RENDER_APPKIT_AVAILABLE,
                         "needs AppKit")
    def test_core_graphics_replay_draws_every_op(self):
        for face in FACE_ORDER:
            for mouth, level, blink, gaze in POSES:
                pixel = self._replayed(character_ops(
                    face, mouth, level, blink, gaze, shadow=True))
                with self.subTest(face=face, mouth=mouth, blink=blink):
                    # The head covers the middle of the box on every face.
                    self.assertEqual(pixel(128, 128)[3], 1.0)
        with self.assertRaises(TypeError):
            whisper_face_render.replay_ops([object()])

    @unittest.skipUnless(whisper_face_render.RENDER_APPKIT_AVAILABLE,
                         "needs AppKit")
    def test_core_graphics_replay_draws_the_outline_and_the_shadow(self):
        # The outline exists only as the border ``grow`` adds to the ink
        # shapes. A replay that filled them and never stroked them would
        # hide the whole ink layer behind the character and still paint a
        # plausible face, so the ink is looked for where nothing else is:
        # in the band just outside the head, and under the chin where only
        # the shadow reaches.
        for face in ("parrot", "fox", "bear", "panda", "frog", "penguin"):
            head = head_of(character_ops(face, 0.0, 0.0))
            left, middle = head.x, head.y + head.h / 2.0
            center, chin = head.x + head.w / 2.0, head.y + head.h
            plain = self._replayed(character_ops(face, 0.0, 0.0))
            cast = self._replayed(character_ops(face, 0.0, 0.0, shadow=True))
            with self.subTest(face=face):
                # Inside the head it is the head, not ink.
                inside = plain(left + 5.0, middle)
                self.assertEqual(inside[3], 1.0)
                self.assertGreater(
                    max(abs(got - want) for got, want in zip(inside, INK)),
                    0.2)
                # The band the outline adds, on both sides and below.
                self.assertInk(plain(left - OUTLINE / 2.0, middle))
                self.assertInk(plain(head.x + head.w + OUTLINE / 2.0,
                                     middle))
                self.assertInk(plain(center, chin + OUTLINE / 2.0))
                # It is a band, not a flood.
                self.assertEqual(plain(left - OUTLINE - 4.0, middle)[3], 0.0)
                # Past the outline's lower edge there is nothing, until a
                # surface asks for the shadow.
                under = chin + OUTLINE + SHADOW_OFFSET[1] / 2.0
                self.assertEqual(plain(center, under)[3], 0.0)
                self.assertInk(cast(center, under))
                self.assertInk(cast(left - OUTLINE / 2.0, middle))

    @unittest.skipUnless(whisper_face_render.RENDER_APPKIT_AVAILABLE,
                         "needs AppKit")
    def test_core_graphics_replay_draws_the_hedgehogs_sharp_quill_ink(self):
        # The quill ink is a polygon with a small ``grow``; just past a
        # brown tip there must be ink, and it must come from that polygon.
        ops = character_ops("hedgehog", 0.0, 0.0)
        pixel = self._replayed(ops)
        ink = [op for op in outline_of(ops) if isinstance(op, Polygon)]
        quills = [op for op in ops if isinstance(op, Polygon)
                  and op.color == COMPANIONS["hedgehog"].deep]
        (tip_x, tip_y), (ink_x, ink_y) = quills[4].points[1], ink[4].points[1]
        self.assertInk(pixel((tip_x + ink_x) / 2.0, (tip_y + ink_y) / 2.0))
        # Beyond the ink point, nothing.
        self.assertEqual(pixel(ink_x, ink_y - QUILL_GROW - 4.0)[3], 0.0)


class CharacterSVGTests(unittest.TestCase):
    def test_svg_is_well_formed_for_every_face_and_frame(self):
        from xml.etree import ElementTree

        for face in FACE_ORDER:
            for frame in ("idle", "half", "talk"):
                with self.subTest(face=face, frame=frame):
                    markup = character_svg(face, frame=frame)
                    ElementTree.fromstring(markup)
                    self.assertIn(f'viewBox="0 0 {DESIGN_SIZE:g}', markup)

    def test_markup_carries_no_element_ids(self):
        # The site inlines every face and frame into shared documents, so
        # the markup must stay id-free (which also rules out gradient defs).
        for face in FACE_ORDER:
            for frame in ("idle", "half", "talk"):
                with self.subTest(face=face, frame=frame):
                    self.assertNotIn("id=", character_svg(face, frame=frame))

    def test_chip_variant_drops_the_speech_puffs(self):
        # The puffs trail off the shoulder and would clip inside the app
        # window's 44-point chip.
        with_puffs = character_svg("fox", whispers=True)
        without = character_svg("fox", whispers=False)
        self.assertLess(len(without), len(with_puffs))

    def test_svg_helpers_draw_the_shadow_only_when_asked(self):
        for face in FACE_ORDER:
            for frame in FRAMES:
                plain = character_body(face, frame=frame)
                shadowed = character_body(face, frame=frame, shadow=True)
                with self.subTest(face=face, frame=frame):
                    self.assertTrue(shadowed.endswith(plain))
                    self.assertGreater(len(shadowed), len(plain))
                    self.assertIn(
                        shadowed, character_svg(face, frame=frame,
                                                shadow=True))
                    self.assertIn(plain, character_svg(face, frame=frame))
                    self.assertNotIn(
                        shadowed, character_svg(face, frame=frame))

    def test_generated_art_is_the_plain_sticker_without_the_shadow(self):
        # The window loads the exported SVGs for small contained uses (a
        # chip, the picker) and the site sets each face inside a chip of
        # its own, so neither carries the hard shadow. A large surface asks
        # character_ops for it.
        site = (ROOT / "site" / "src" / "data" / "face-art.ts"
                ).read_text(encoding="utf-8")
        for face in FACE_ORDER:
            for frame in FRAMES:
                exported = (ROOT / "icons" / "faces" / "color"
                            / f"{face}-{frame}.svg").read_text(
                                encoding="utf-8")
                plain = character_body(face, frame=frame, whispers=False)
                shadowed = character_body(
                    face, frame=frame, whispers=False, shadow=True)
                with self.subTest(face=face, frame=frame):
                    self.assertIn(plain, exported)
                    self.assertNotIn(shadowed, exported)
                    self.assertIn(plain, site)
                    self.assertNotIn(shadowed, site)

    def test_committed_art_matches_the_spec(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_face_art.py"),
             "--check"],
            capture_output=True, text=True)
        self.assertEqual(
            result.returncode, 0,
            f"generated face art is stale:\n{result.stdout}{result.stderr}")


if __name__ == "__main__":
    unittest.main()
