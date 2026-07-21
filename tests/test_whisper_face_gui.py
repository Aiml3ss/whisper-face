#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Headless tests for the Whisper Face native-window state seam."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisper_face_gui import (
    FACES,
    GUIActions,
    SECTIONS,
    WhisperFaceViewModel,
    create_gui,
    normalize_snapshot,
)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_normalized_without_appkit_or_runtime(self):
        state = normalize_snapshot({
            "capture_state": "Listening",
            "paused": True,
            "face": "FOX",
            "flight_recorder": True,
            "last_latency_ms": "174.5",
            "last_word_count": "55",
            "words_today": 2042.76,
            "minutes_saved": "12.4",
            "outbox_count": 2,
            "regression_cases": 7,
            "regression_quarantined": 1,
            "models": [{
                "name": "Parakeet Unified",
                "role": "Primary recognition",
                "status": "Ready",
                "detail": "Apple Silicon",
            }],
        })
        self.assertEqual(state.face, "fox")
        self.assertTrue(state.paused)
        self.assertTrue(state.flight_recorder)
        self.assertEqual(state.last_latency_ms, 174.5)
        self.assertEqual(state.last_word_count, 55)
        self.assertEqual(state.words_today, 2042)
        self.assertEqual(state.outbox_count, 2)
        self.assertEqual(state.regression_cases, 7)
        self.assertEqual(state.regression_quarantined, 1)
        self.assertEqual(state.models[0].name, "Parakeet Unified")

    def test_malformed_snapshot_gets_safe_truthful_defaults(self):
        state = normalize_snapshot({
            "face": "dragon",
            "last_latency_ms": float("nan"),
            "minutes_saved": -100,
            "models": "not a model list",
        })
        self.assertEqual(state.face, "parrot")
        self.assertIsNone(state.last_latency_ms)
        self.assertEqual(state.minutes_saved, 0)
        self.assertEqual(state.models, ())
        self.assertEqual(state.active_engine, "Waiting for status")


class ViewModelTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {
            "face": "parrot",
            "paused": False,
            "flight_recorder": False,
            "active_engine": "Parakeet Unified",
        }
        self.calls = []

        def set_face(face):
            self.calls.append(("face", face))
            self.runtime["face"] = face

        def set_flight(enabled):
            self.calls.append(("flight", enabled))
            self.runtime["flight_recorder"] = enabled

        def pause():
            self.calls.append(("pause",))
            self.runtime["paused"] = True

        def resume():
            self.calls.append(("resume",))
            self.runtime["paused"] = False

        self.actions = GUIActions(
            status_snapshot=lambda: dict(self.runtime),
            set_face=set_face,
            set_flight_recorder=set_flight,
            pause=pause,
            resume=resume,
            open_log=lambda: self.calls.append(("log",)),
            open_source_and_license=lambda: self.calls.append(("source",)),
            open_local_license_notices=lambda: self.calls.append(("license",)),
            copy_latest_outbox=lambda: self.calls.append(("outbox",)),
            rerun_verification=lambda: {
                "passed": True, "message": "Mac installation verified"},
        )
        self.model = WhisperFaceViewModel(self.actions)

    def test_all_sections_are_navigable(self):
        for section in SECTIONS:
            self.assertEqual(self.model.select_section(section).section, section)
        with self.assertRaises(ValueError):
            self.model.select_section("Billing")

    def test_face_selection_validates_and_calls_runtime(self):
        for face in FACES:
            self.assertEqual(self.model.choose_face(face).face, face)
        self.assertEqual(self.calls[-1], ("face", "bear"))
        with self.assertRaises(ValueError):
            self.model.choose_face("dragon")

    def test_privacy_and_pause_controls_are_callbacks(self):
        self.assertTrue(self.model.set_flight_recorder(True).flight_recorder)
        self.assertTrue(self.model.set_paused(True).paused)
        self.assertFalse(self.model.set_paused(False).paused)
        self.assertIn(("flight", True), self.calls)
        self.assertIn(("pause",), self.calls)
        self.assertIn(("resume",), self.calls)

    def test_diagnostics_actions_report_results(self):
        self.model.open_log()
        self.model.open_source_and_license()
        self.model.open_local_license_notices()
        self.model.copy_latest_outbox()
        state = self.model.rerun_verification()
        self.assertIn(("log",), self.calls)
        self.assertIn(("source",), self.calls)
        self.assertIn(("license",), self.calls)
        self.assertIn(("outbox",), self.calls)
        self.assertEqual(state.verification, "Mac installation verified")

    def test_callback_failure_becomes_user_visible_notice(self):
        def fail(_face):
            raise RuntimeError("read only")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, set_face=fail))
        state = model.choose_face("owl")
        self.assertEqual(state.face, "parrot")
        self.assertIn("read only", state.notice)

    def test_status_failure_preserves_last_known_state(self):
        calls = [0]

        def status():
            calls[0] += 1
            if calls[0] > 1:
                raise RuntimeError("service restarting")
            return {"face": "cat", "active_engine": "Parakeet"}

        model = WhisperFaceViewModel(GUIActions(status_snapshot=status))
        state = model.refresh()
        self.assertEqual(state.face, "cat")
        self.assertEqual(state.active_engine, "Parakeet")
        self.assertIn("service restarting", state.notice)

    def test_facade_creation_does_not_show_a_window(self):
        gui = create_gui(self.actions)
        self.assertIs(gui.view_model.actions, self.actions)
        self.assertIsNone(gui._controller)


if __name__ == "__main__":
    unittest.main()
