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
                        self.assertEqual(op.grow, OUTLINE)
                        # Fill and border overlap, which only an opaque
                        # op can do without a visible seam.
                        self.assertEqual(op.alpha, 1.0)

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
                        self.assertEqual(cast.grow, OUTLINE)
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

    @unittest.skipUnless(whisper_face_render.RENDER_APPKIT_AVAILABLE,
                         "needs AppKit")
    def test_core_graphics_replay_draws_every_op(self):
        from AppKit import (
            NSBitmapImageRep,
            NSDeviceRGBColorSpace,
            NSGraphicsContext,
        )

        size = int(DESIGN_SIZE)
        for face in FACE_ORDER:
            rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
                None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace,
                0, 0)
            NSGraphicsContext.setCurrentContext_(
                NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
            for mouth, level, blink, gaze in POSES:
                whisper_face_render.replay_ops(character_ops(
                    face, mouth, level, blink, gaze, shadow=True))
            with self.subTest(face=face):
                # The head covers the middle of the box on every face.
                self.assertEqual(
                    rep.colorAtX_y_(size // 2, size // 2).alphaComponent(),
                    1.0)
        with self.assertRaises(TypeError):
            whisper_face_render.replay_ops([object()])


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

    def test_exported_art_wears_the_shadow_and_site_art_does_not(self):
        # The exported SVGs are large standalone art; the site sets each
        # face inside a chip of its own, which is a container already.
        exported = (ROOT / "icons" / "faces" / "color" / "fox-idle.svg"
                    ).read_text(encoding="utf-8")
        site = (ROOT / "site" / "src" / "data" / "face-art.ts"
                ).read_text(encoding="utf-8")
        self.assertIn(
            character_body("fox", whispers=False, shadow=True), exported)
        self.assertIn(character_body("fox", whispers=False), site)
        self.assertNotIn(
            character_body("fox", whispers=False, shadow=True), site)

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
