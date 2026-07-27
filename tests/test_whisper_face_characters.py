#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Headless tests for the shared Whisper Face character spec.

The ten characters are drawn by two renderers: the HUD replays the op list
through Core Graphics, and ``scripts/generate_face_art.py`` replays it into
SVG. These tests pin the seam so the two cannot drift, and pin the character
traits that make each face recognizable as its own animal.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisper_face_characters import (
    COMPANIONS,
    DESIGN_SIZE,
    FACE_ORDER,
    Arc,
    Curve,
    Ellipse,
    Polygon,
    RoundedRect,
    Stroke,
    character_ops,
    character_svg,
)


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

    def test_ops_stay_inside_the_design_box(self):
        # A character that overflows the box gets clipped by the HUD stage and
        # letterboxed oddly in the app window chip.
        for face in FACE_ORDER:
            for mouth, blink in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0),
                                 (1.0, 0.5)):
                for op in character_ops(face, mouth, 1.0, blink):
                    if isinstance(op, (Ellipse, RoundedRect)):
                        points = [(op.x, op.y), (op.x + op.w, op.y + op.h)]
                    elif isinstance(op, (Polygon, Stroke)):
                        points = list(op.points)
                    elif isinstance(op, Arc):
                        points = [(op.cx - op.radius, op.cy - op.radius),
                                  (op.cx + op.radius, op.cy + op.radius)]
                    elif isinstance(op, Curve):
                        points = [op.start]
                        for segment in op.segments:
                            points.extend(segment)
                    else:
                        self.fail(f"unhandled op {type(op).__name__}")
                    for x, y in points:
                        with self.subTest(face=face, op=type(op).__name__):
                            self.assertGreaterEqual(x, -8)
                            self.assertGreaterEqual(y, -8)
                            self.assertLessEqual(x, DESIGN_SIZE + 8)
                            self.assertLessEqual(y, DESIGN_SIZE + 8)

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
