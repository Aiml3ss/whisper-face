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
    set_accessible_text,
    sync_accessibility,
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

    def test_first_run_checklist_uses_real_permission_model_and_result_state(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "hotkey_label": "Right Option",
            "models": [{"name": "Parakeet", "status": "Running"}],
            "last_word_count": 7,
        })
        self.assertTrue(state.onboarding_complete)
        self.assertEqual(
            [step.key for step in state.onboarding_steps],
            ["permissions", "hotkey", "models", "first_dictation"],
        )
        self.assertTrue(all(step.complete for step in state.onboarding_steps))

        first_run = normalize_snapshot({
            "service_status": "Starting",
            "microphone_status": "Needs attention",
            "accessibility_status": "Needs attention",
            "models": [{"name": "Parakeet", "status": "Preparing"}],
        })
        self.assertFalse(first_run.onboarding_complete)
        self.assertEqual(first_run.onboarding_steps[0].key, "permissions")
        self.assertEqual(first_run.onboarding_steps[0].status, "Needs attention")

    def test_status_presentation_covers_capture_processing_recovery_and_degraded(self):
        common = {
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "models": [{"name": "Parakeet", "status": "Running"}],
        }
        listening = normalize_snapshot({**common, "capture_state": "Listening"})
        self.assertEqual(listening.status_phase, "recording")
        self.assertIn("release", listening.status_detail.casefold())

        processing = normalize_snapshot({**common, "capture_state": "Processing"})
        self.assertEqual(processing.status_phase, "processing")
        self.assertIn("protecting names and numbers", processing.status_detail)

        recovery = normalize_snapshot({**common, "outbox_count": 2})
        self.assertEqual(recovery.status_phase, "recovery")
        self.assertIn("2 dictations", recovery.status_detail)

        degraded = normalize_snapshot({
            **common,
            "accessibility_status": "Needs attention",
        })
        self.assertEqual(degraded.status_phase, "degraded")
        self.assertEqual(degraded.degraded_issues[0].key, "accessibility")
        self.assertIn("Voice Outbox", degraded.degraded_issues[0].detail)

        microphone_failed = normalize_snapshot({
            **common,
            "microphone_status": "Unavailable",
        })
        self.assertEqual(microphone_failed.status_phase, "degraded")
        self.assertEqual(microphone_failed.degraded_issues[0].key, "microphone")
        self.assertNotIn("not blocked", microphone_failed.status_title.casefold())

    def test_ready_fallback_remains_usable_while_warning_is_explained(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "models": [
                {"name": "Parakeet", "status": "Running"},
                {"name": "Whisper fallback", "status": "Unavailable"},
            ],
        })
        self.assertEqual(state.status_phase, "ready")
        self.assertEqual(len(state.degraded_issues), 1)
        self.assertEqual(state.degraded_issues[0].severity, "warning")
        self.assertIn("continue", state.degraded_issues[0].detail)

    def test_result_inspector_is_evidence_only_and_honors_reduced_motion(self):
        self.assertIsNone(normalize_snapshot({
            "last_confidence": 1.0,
        }).last_result.confidence)
        state = normalize_snapshot({
            "service_status": "Running",
            "active_engine": "Parakeet Unified",
            "last_latency_ms": 842,
            "last_word_count": 12,
            "last_mode": "Capture",
            "last_stable_prefix_words": 9,
            "last_confidence": 0.91,
            "last_compiler_decisions": 4,
            "last_cleanup_edits": ["proof:filler", "punctuation"],
            "last_proof_edits_accepted": 2,
            "last_proof_edits_rejected": 1,
            "last_protected_anchors": 3,
            "last_alternatives_considered": 2,
            "last_alternatives": ["private alternative text"],
            "last_compiler_details": ["private before → private after"],
            "last_context_influence": "Context helped resolve: personal vocabulary",
            "prefers_reduced_motion": True,
            "transcript": "must not be projected into GUIState",
        })
        result = state.last_result
        self.assertTrue(result.available)
        self.assertEqual(result.summary, "12 words in 0.84s")
        self.assertEqual(result.stable_prefix_words, 9)
        self.assertEqual(result.confidence, 0.91)
        self.assertEqual(result.compiler_decisions, 4)
        self.assertEqual(result.proof_edits_accepted, 2)
        self.assertEqual(result.proof_edits_rejected, 1)
        self.assertEqual(result.cleanup_edits, ("proof:filler", "punctuation"))
        self.assertEqual(result.protected_anchor_count, 3)
        self.assertEqual(result.alternatives_considered, 2)
        self.assertEqual(
            result.context_influence,
            "Context helped resolve: personal vocabulary")
        self.assertFalse(hasattr(result, "transcript"))
        self.assertFalse(hasattr(result, "compiler_details"))
        self.assertTrue(state.prefers_reduced_motion)


class ViewModelTests(unittest.TestCase):
    def test_dynamic_accessibility_updates_label_and_value_together(self):
        class FakeControl:
            def setStringValue_(self, value):
                self.visual = value

            def setAccessibilityLabel_(self, value):
                self.label = value

            def setAccessibilityValue_(self, value):
                self.value = value

        control = FakeControl()

        sync_accessibility(
            control, "Dictation paused", label="Resume dictation")

        self.assertEqual(control.label, "Resume dictation")
        self.assertEqual(control.value, "Dictation paused")

        set_accessible_text(
            control, "Running…", label="Verification result")
        self.assertEqual(control.visual, "Running…")
        self.assertEqual(control.label, "Verification result")
        self.assertEqual(control.value, "Running…")

        set_accessible_text(
            control, "Waiting for model status", label="Model name")
        self.assertEqual(control.visual, "Waiting for model status")
        self.assertEqual(control.label, "Model name")
        self.assertEqual(control.value, "Waiting for model status")

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

    def test_onboarding_and_degraded_guidance_routes_without_blocking(self):
        runtime = {
            "service_status": "Running",
            "microphone_status": "Needs attention",
            "accessibility_status": "Needs attention",
            "models": [{"name": "Parakeet", "status": "Preparing"}],
        }
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: runtime))
        state = model.show_next_onboarding_step()
        self.assertEqual(state.section, "Diagnostics")
        self.assertEqual(state.notice_level, "info")
        self.assertIn("Microphone captures speech", state.notice)

        state = model.show_issue()
        self.assertEqual(state.section, "Diagnostics")
        self.assertEqual(state.notice_level, "error")
        self.assertIn("Microphone", state.notice)

    def test_completed_onboarding_acknowledgement_survives_refresh(self):
        self.assertFalse(self.model.state.onboarding_acknowledged)
        self.model.acknowledge_onboarding()
        self.assertTrue(self.model.refresh().onboarding_acknowledged)


if __name__ == "__main__":
    unittest.main()
