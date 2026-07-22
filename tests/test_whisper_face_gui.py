#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Headless tests for the Whisper Face native-window state seam."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisper_face_gui import (
    APPKIT_AVAILABLE,
    AcousticKeywordCandidate,
    FACES,
    GUIActions,
    SECTIONS,
    SETTINGS_PANES,
    STRING_CATALOGS,
    SUPPORTED_LOCALES,
    WhisperFaceViewModel,
    create_gui,
    localized_string,
    native_appkit_smoke_contract,
    normalize_acoustic_keyword_inspection,
    normalize_snapshot,
    normalize_settings,
    run_native_appkit_smoke,
    resolve_locale,
    set_accessible_text,
    support_snapshot_text,
    sync_accessibility,
    tone_for_app_index,
)


class SnapshotTests(unittest.TestCase):
    @staticmethod
    def keyword_export(keyword="Qwen"):
        return {
            "schema_version": 1,
            "kind": "whisper-face/acoustic-keyword-memory-export",
            "policy": {
                "minimum_observations": 3,
                "minimum_confirmations": 2,
                "max_entries": 256,
                "recognition_effect": "none",
            },
            "candidates": [{
                "keyword": keyword,
                "app_scope": None,
                "observations": 1,
                "confirmations": 1,
                "eligible": False,
                "status": "needs-2-observations-and-1-confirmations",
            }],
        }

    def test_keyword_inspection_is_strict_token_free_and_derives_scope(self):
        inspection = normalize_acoustic_keyword_inspection(
            self.keyword_export())

        self.assertEqual(inspection.candidates[0].keyword, "Qwen")
        self.assertIsNone(inspection.candidates[0].app_scope)
        self.assertFalse(inspection.candidates[0].eligible)
        malformed = self.keyword_export()
        malformed["candidates"][0]["transcript"] = "private words"
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_acoustic_keyword_inspection(malformed)

        malformed = self.keyword_export()
        malformed["candidates"][0]["eligible"] = True
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_acoustic_keyword_inspection(malformed)

    def test_private_settings_snapshot_normalizes_malformed_rows(self):
        settings = normalize_settings({
            "app_tones": [
                {"bundle": "com.example.mail", "name": "Mail", "tone": "formal"},
                {"bundle": "com.example.bad", "tone": "invented"},
                "not a row",
            ],
            "snippets": [
                {"name": "signature", "text": "Cheers"},
                {"name": "", "text": "ignored"},
                {"name": "oversize", "text": "x" * 4001},
            ],
            "manual_vocabulary": ["Qwen", " qwen ", "Whisper Face"],
            "banned_vocabulary": "not a list",
            "corrections": [
                {"key": "gwen", "source": "Gwen", "target": "Qwen",
                 "count": "3", "kind": "correction"},
                {"key": "bad", "source": "", "target": "ignored"},
            ],
        })
        self.assertEqual(len(settings.app_tones), 2)
        self.assertEqual(settings.app_tones[1].tone, "auto")
        self.assertEqual(settings.snippets[0].name, "signature")
        self.assertEqual(len(settings.snippets), 1)
        self.assertEqual(settings.manual_vocabulary, ("Qwen", "Whisper Face"))
        self.assertEqual(settings.banned_vocabulary, ())
        self.assertEqual(settings.corrections[0].count, 3)

    def test_localization_catalog_formats_and_falls_back_to_english(self):
        self.assertEqual(SUPPORTED_LOCALES, ("en",))
        self.assertEqual(tuple(STRING_CATALOGS), ("en",))
        self.assertEqual(localized_string("nav.settings"), "Settings")
        self.assertEqual(resolve_locale("en-US"), "en")
        self.assertEqual(resolve_locale("fr-CA"), "en")
        self.assertEqual(
            localized_string("settings.personalize.snippets.detail",
                             locale="fr", count=2),
            "2 saved phrases")
        with self.assertRaises(KeyError):
            localized_string("missing.key")
        with self.assertRaises(ValueError):
            localized_string("settings.personalize.snippets.detail")
        for key in (
            "overview.phase.ready",
            "overview.status.recording.detail",
            "overview.status.recovery.detail.one",
            "overview.engine.active",
            "overview.outbox.pending",
            "overview.outbox.summary.paste_attempted",
            "overview.action.copy_outbox.help",
            "overview.metric.last.words.many",
            "overview.accessibility.outbox",
            "overview.notice.outbox.copied",
            "overview.notice.status.error",
            "onboarding.permissions.title",
            "onboarding.hotkey.title",
            "onboarding.models.title",
            "onboarding.first_dictation.title",
            "onboarding.progress",
            "settings.action.diagnostics",
            "results.title",
            "results.firewall.quarantine.one",
            "results.accessibility.firewall",
            "results.consequence.review.advisory",
            "results.accessibility.consequence_advisory",
            "models.title",
            "models.accessibility.guidance",
            "diagnostics.title",
            "diagnostics.accessibility.verification",
        ):
            with self.subTest(key=key):
                self.assertIn(key, STRING_CATALOGS["en"])

    def test_gui_owned_copy_inventory_has_localized_defaults_and_fallback(self):
        expected_by_prefix = {
            "default.": {
                "default.model.name",
                "default.status.unknown",
                "default.capture.ready",
                "default.capture.paused",
                "default.flight.off",
                "default.privacy.local",
                "default.build.development",
            },
            "validation.": {
                "validation.tone.selection",
                "validation.section.unknown",
                "validation.settings_pane.unknown",
                "validation.app.bundle",
                "validation.tone.unsupported",
                "validation.snippet.name",
                "validation.snippet.text",
                "validation.snippet.required",
                "validation.snippet.expected",
                "validation.vocabulary.preferred",
                "validation.vocabulary.excluded",
                "validation.vocabulary.list",
                "validation.vocabulary.term_length",
                "validation.vocabulary.reserved",
                "validation.vocabulary.maximum",
                "validation.vocabulary.overlap",
                "validation.correction.kind",
                "validation.correction.unknown",
                "validation.correction.stale_snippet",
                "validation.keyword.unknown",
                "validation.face.unsupported",
            },
            "operation.": {
                "operation.settings.load_failed",
                "operation.tone.save_failed",
                "operation.snippet.save_failed",
                "operation.snippet.delete_failed",
                "operation.vocabulary.save_failed",
                "operation.correction.forget_failed",
                "operation.keyword.inspect_failed",
                "operation.keyword.export_failed",
                "operation.keyword.forget_failed",
                "operation.face.change_failed",
                "operation.flight.update_failed",
                "operation.log.open_failed",
                "operation.support_snapshot.copy_failed",
                "operation.source.open_failed",
                "operation.licenses.open_failed",
            },
            "diagnostics.verification.": {
                "diagnostics.verification.not_run",
                "diagnostics.verification.running",
                "diagnostics.verification.passed",
                "diagnostics.verification.attention",
                "diagnostics.verification.failed",
            },
        }
        for prefix, expected in expected_by_prefix.items():
            with self.subTest(prefix=prefix):
                actual = {
                    key for key in STRING_CATALOGS["en"]
                    if key.startswith(prefix)
                }
                self.assertEqual(actual, expected)

        state = normalize_snapshot({}, locale="fr-CA")
        self.assertEqual(
            state.capture_state,
            localized_string("default.capture.ready", locale="fr-CA"))
        self.assertEqual(
            state.service_status,
            localized_string("default.status.unknown", locale="fr-CA"))
        self.assertEqual(
            state.privacy_summary,
            localized_string("default.privacy.local", locale="fr-CA"))
        self.assertEqual(
            state.verification,
            localized_string(
                "diagnostics.verification.not_run", locale="fr-CA"))

    def test_overview_status_copy_uses_catalog_and_locale_fallback(self):
        common = {
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "hotkey_label": "Right Option",
            "models": [{"name": "Parakeet", "status": "Running"}],
        }
        listening = normalize_snapshot({
            **common, "capture_state": "Listening",
        }, locale="fr-CA")
        self.assertEqual(
            listening.status_title,
            localized_string(
                "overview.status.recording.title", locale="fr-CA"))
        self.assertEqual(
            listening.status_detail,
            localized_string(
                "overview.status.recording.detail", locale="fr-CA",
                hotkey="Right Option"))

        one = normalize_snapshot({**common, "outbox_count": 1})
        many = normalize_snapshot({**common, "outbox_count": 2})
        self.assertEqual(
            one.status_detail,
            localized_string("overview.status.recovery.detail.one", count=1))
        self.assertEqual(
            many.status_detail,
            localized_string(
                "overview.status.recovery.detail.many", count=2))

        runtime_copy = normalize_snapshot({
            **common,
            "active_engine": "Warming up",
            "outbox_count": 1,
            "outbox_summary": "Paste may have landed — verify before reusing",
        }, locale="fr-CA")
        self.assertEqual(
            runtime_copy.active_engine,
            localized_string("overview.engine.warming", locale="fr-CA"))
        self.assertEqual(
            runtime_copy.outbox_summary,
            localized_string(
                "overview.outbox.summary.paste_attempted", locale="fr-CA"))

    def test_native_appkit_smoke_contract_is_headless_and_catalog_complete(self):
        contract = native_appkit_smoke_contract()
        self.assertEqual(contract.sections, SECTIONS)
        self.assertEqual(contract.settings_panes, SETTINGS_PANES)
        self.assertEqual(contract.allowed_side_effects, ())
        self.assertEqual(
            contract.onboarding_steps,
            ("permissions", "hotkey", "models", "first_dictation"),
        )
        self.assertEqual(contract.locale_fallback, "en")
        self.assertIn("command-d:diagnostics", contract.key_equivalents)
        self.assertIn("select_section", contract.model_actions)
        self.assertIn("forget_snippet", contract.model_actions)
        for key in contract.accessibility_catalog_keys:
            with self.subTest(key=key):
                self.assertIn(key, STRING_CATALOGS["en"])
        if not APPKIT_AVAILABLE:
            with self.assertRaisesRegex(RuntimeError, "requires macOS"):
                run_native_appkit_smoke()

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

    def test_support_snapshot_is_deterministic_and_strictly_transcript_free(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "version": "Local checkout",
            "models": [
                {"name": "Whisper", "status": "Installed"},
                {"name": "Parakeet", "status": "Running"},
            ],
            "active_engine": "Parakeet",
            "last_latency_ms": 123.4,
            "last_word_count": 7,
            "last_confidence": 0.75,
            "last_mode": "compose",
            "last_stable_prefix_words": 3,
            "last_compiler_decisions": 4,
            "last_protected_anchors": 2,
            "last_alternatives_considered": 1,
            "last_cleanup_edits": ["private rewrite", "another edit"],
            "last_proof_edits_accepted": 1,
            "last_proof_edits_rejected": 2,
            "transcript": "private dictation",
            "selection": "private selection",
            "context": "private context",
            "log_path": "/Users/example/private.log",
            "dictionary": ["private dictionary"],
            "snippets": ["private snippet"],
            "corrections": ["private correction"],
            "machine_identifier": "private machine",
        })

        first = support_snapshot_text(state)
        second = support_snapshot_text(state)
        payload = json.loads(first)

        self.assertEqual(first, second)
        self.assertEqual(payload["kind"], "whisper-face/support-snapshot")
        self.assertEqual(payload["models"], [
            {"family": "parakeet", "status": "running"},
            {"family": "whisper", "status": "installed"},
        ])
        self.assertEqual(payload["last_result"]["cleanup_edits_count"], 2)
        self.assertNotIn("cleanup_edits", payload["last_result"])
        self.assertEqual(set(payload), {
            "kind", "schema_version", "health", "permissions", "build",
            "models", "last_result",
        })
        for private_value in (
                "private dictation", "private selection", "private context",
                "/Users/example/private.log", "private dictionary",
                "private snippet", "private correction", "private machine",
                "private rewrite", "another edit"):
            self.assertNotIn(private_value, first)

        poisoned = support_snapshot_text(normalize_snapshot({
            "service_status": "private service transcript",
            "microphone_status": "private microphone path",
            "accessibility_status": "private permission detail",
            "version": "private machine identifier",
            "models": [{
                "name": "private model label",
                "status": "private model status",
            }],
            "active_engine": "private engine",
            "last_word_count": 1,
            "last_mode": "private mode",
        }))
        self.assertNotIn("private", poisoned)
        self.assertEqual(json.loads(poisoned)["models"], [
            {"family": "unknown", "status": "unknown"},
        ])

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

    def test_first_run_progresses_only_from_runtime_evidence(self):
        runtime = {
            "service_status": "Running",
            "microphone_status": "Not requested",
            "accessibility_status": "Not requested",
            "capture_state": "Ready",
            "models": [{"name": "Parakeet", "status": "Preparing"}],
        }
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: dict(runtime)), locale="en-US")
        self.assertEqual(model.locale, "en")
        self.assertEqual(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key,
            "permissions",
        )

        runtime.update(
            microphone_status="Ready", accessibility_status="Granted")
        self.assertEqual(
            next(step for step in model.refresh().onboarding_steps
                 if not step.complete).key,
            "hotkey",
        )
        runtime["capture_state"] = "Listening"
        self.assertEqual(
            next(step for step in model.refresh().onboarding_steps
                 if not step.complete).key,
            "models",
        )
        runtime["capture_state"] = "Ready"
        runtime["models"] = [{"name": "Parakeet", "status": "Running"}]
        self.assertEqual(
            next(step for step in model.refresh().onboarding_steps
                 if not step.complete).key,
            "first_dictation",
        )
        runtime["last_word_count"] = 5
        self.assertTrue(model.refresh().onboarding_complete)

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
            "last_context_firewall": {
                "mode": "shadow-only",
                "disposition": "quarantine",
                "protected_influences": 3,
                "quarantined": 2,
                "reason_counts": {"private@example.com": 999},
                "context": "Alice /Users/alice/secret",
                "personal_prior": "private before → private after",
            },
            "last_consequence": {
                "route": "review",
                "risk_counts": {"currency": 1, "private transcript": 999},
                "high_risks": 1,
                "uncertain_risks": 1,
                "relisten_status": "skipped",
            },
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
        self.assertEqual(
            result.context_firewall_summary,
            "Context safety: the shadow check flagged 3 protected influences "
            "for quarantine review.")
        self.assertEqual(
            result.consequence_summary,
            "Consequence: Review · 1 high-risk · 1 uncertain · currency 1 · "
            "Re-listen: skipped")
        self.assertEqual(
            result.consequence_advisory,
            "Check names, numbers, dates, and recipients before relying on "
            "this result.")
        self.assertNotIn("private transcript", result.consequence_summary)
        self.assertNotIn("private transcript", result.consequence_advisory)
        self.assertNotIn("private@example.com", repr(result))
        self.assertNotIn("/Users/alice/secret", repr(result))
        self.assertNotIn("private before", repr(result))
        self.assertFalse(hasattr(result, "transcript"))
        self.assertFalse(hasattr(result, "compiler_details"))
        self.assertTrue(state.prefers_reduced_motion)

    def test_review_advisory_is_scoped_to_the_review_route(self):
        for route in ("standard", "protected", "verified", "unavailable"):
            with self.subTest(route=route):
                result = normalize_snapshot({
                    "last_word_count": 4,
                    "last_consequence": {"route": route},
                }).last_result
                self.assertEqual(result.consequence_advisory, "")

        review = normalize_snapshot({
            "last_word_count": 4,
            "last_consequence": {"route": "review"},
        }).last_result
        self.assertEqual(
            review.consequence_advisory,
            localized_string("results.consequence.review.advisory"))

    def test_context_firewall_receipt_copy_is_bounded_and_truthful(self):
        common = {"last_word_count": 4, "active_engine": "Parakeet"}
        cases = (
            ({"mode": "shadow-only", "disposition": "no-effect"},
             "found no context-driven change"),
            ({"mode": "shadow-only", "disposition": "promotion-candidate",
              "promotion_candidates": 2},
             "2 non-protected influences for later evaluation"),
            ({"mode": "shadow-only", "disposition": "quarantine",
              "quarantined": 999999},
             "1000 protected influences for quarantine review"),
            ({"mode": "unsupported", "disposition": "quarantine"},
             "no finalized shadow check is available yet"),
        )
        for receipt, expected in cases:
            with self.subTest(receipt=receipt):
                state = normalize_snapshot({
                    **common, "last_context_firewall": receipt,
                }, locale="fr-CA")
                self.assertIn(expected, state.last_result.context_firewall_summary)


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
        self.keyword_reads = 0
        self.private_settings = {
            "app_tones": [{
                "bundle": "com.example.mail", "name": "Mail", "tone": "auto"}],
            "snippets": [{"name": "signature", "text": "Cheers"}],
            "manual_vocabulary": ["Qwen"],
            "banned_vocabulary": ["gwen"],
            "corrections": [{
                "key": "gwen", "source": "Gwen", "target": "Qwen",
                "count": 3, "kind": "correction"}, {
                "key": "gwen", "source": "Snippet: gwen",
                "target": "Qwen snippet", "count": 2, "kind": "snippet"}, {
                "key": "signature", "source": "Snippet: signature",
                "target": "Cheers", "count": 1, "kind": "snippet"}],
        }

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

        def inspect_keywords():
            self.keyword_reads += 1
            return SnapshotTests.keyword_export()

        self.actions = GUIActions(
            status_snapshot=lambda: dict(self.runtime),
            settings_snapshot=lambda: dict(self.private_settings),
            set_face=set_face,
            set_flight_recorder=set_flight,
            set_app_tone=lambda bundle, tone:
                self.calls.append(("tone", bundle, tone)),
            save_snippet=lambda name, expected, text:
                self.calls.append(("save_snippet", name, expected, text)),
            delete_snippet=lambda name, expected:
                self.calls.append(("delete_snippet", name, expected)),
            save_vocabulary=lambda terms, bans:
                self.calls.append(("vocabulary", tuple(terms), tuple(bans))),
            forget_correction=lambda key:
                self.calls.append(("forget_correction", key)),
            forget_snippet_edit=lambda key:
                self.calls.append(("forget_snippet", key)),
            inspect_acoustic_keywords=inspect_keywords,
            export_acoustic_keywords=lambda:
                self.calls.append(("export_acoustic_keywords",)),
            forget_acoustic_keyword=lambda keyword, scope:
                self.calls.append(
                    ("forget_acoustic_keyword", keyword, scope)) or True,
            forget_all_acoustic_keywords=lambda:
                self.calls.append(("forget_all_acoustic_keywords",)),
            pause=pause,
            resume=resume,
            open_log=lambda: self.calls.append(("log",)),
            copy_support_snapshot=lambda payload:
                self.calls.append(("support_snapshot", payload)),
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

    def test_unified_settings_load_only_on_navigation_and_all_panes_work(self):
        self.assertEqual(self.model.state.settings.snippets, ())
        state = self.model.select_section("Settings")
        self.assertEqual(state.settings.snippets[0].name, "signature")
        for pane in SETTINGS_PANES:
            self.assertEqual(
                self.model.select_settings_pane(pane).settings_pane, pane)
        with self.assertRaises(ValueError):
            self.model.select_settings_pane("Cloud")

    def test_keyword_text_loads_only_on_explicit_inspection_and_actions_are_callbacks(self):
        self.model.select_section("Settings")
        self.model.refresh()
        self.assertEqual(self.keyword_reads, 0)
        self.assertFalse(hasattr(self.model.state, "acoustic_keywords"))

        inspection = self.model.inspect_acoustic_keywords()

        self.assertEqual(self.keyword_reads, 1)
        candidate = inspection.candidates[0]
        self.assertEqual(candidate.keyword, "Qwen")
        self.assertFalse(hasattr(self.model.state, "acoustic_keywords"))
        self.model.export_acoustic_keywords()
        self.model.forget_acoustic_keyword(candidate)
        self.model.forget_all_acoustic_keywords()
        self.assertIn(("export_acoustic_keywords",), self.calls)
        self.assertIn(
            ("forget_acoustic_keyword", "Qwen", None), self.calls)
        self.assertIn(("forget_all_acoustic_keywords",), self.calls)

    def test_malformed_keyword_inspection_stays_fail_closed(self):
        malformed = SnapshotTests.keyword_export()
        malformed["candidates"][0]["raw_transcript"] = "private"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            inspect_acoustic_keywords=lambda: malformed,
        ))

        with self.assertRaisesRegex(ValueError, "Could not inspect"):
            model.inspect_acoustic_keywords()
        self.assertEqual(model.state.notice_level, "error")
        self.assertNotIn("private", model.state.notice)

    def test_keyword_forget_rejects_uninspected_identity(self):
        with self.assertRaisesRegex(ValueError, "unknown pronunciation"):
            self.model.forget_acoustic_keyword("Qwen")

    def test_personalization_actions_validate_and_call_runtime(self):
        self.model.select_section("Settings")
        self.model.set_app_tone("com.example.mail", "formal")
        self.model.save_snippet("address", "123 Main Street")
        self.model.save_snippet(
            "signature", "Kind regards", expected_original="Cheers")
        self.model.delete_snippet("signature", "Cheers")
        self.model.save_vocabulary(["Qwen", "Qwen"], ["Gwen"])
        self.model.forget_learned("correction", "gwen")
        self.model.forget_learned("snippet", "gwen")
        self.model.forget_learned("snippet", "signature")
        self.assertIn(("tone", "com.example.mail", "formal"), self.calls)
        self.assertIn(
            ("save_snippet", "address", None, "123 Main Street"), self.calls)
        self.assertIn(
            ("save_snippet", "signature", "Cheers", "Kind regards"),
            self.calls)
        self.assertIn(("delete_snippet", "signature", "Cheers"), self.calls)
        self.assertIn(("vocabulary", ("Qwen",), ("Gwen",)), self.calls)
        self.assertIn(("forget_correction", "gwen"), self.calls)
        self.assertIn(("forget_snippet", "gwen"), self.calls)
        self.assertIn(("forget_snippet", "signature"), self.calls)

    def test_personalization_rejects_invalid_or_ambiguous_input(self):
        self.model.select_section("Settings")
        with self.assertRaises(ValueError):
            self.model.set_app_tone("not a bundle id", "formal")
        with self.assertRaises(ValueError):
            self.model.set_app_tone("com.example.mail", "sparkly")
        self.assertEqual(
            self.model.save_snippet("bad\nname", "text").notice_level,
            "error")
        self.assertIn(
            "1–4000 characters",
            self.model.save_snippet("empty", "   ").notice)
        self.assertIn(
            "cannot also be excluded",
            self.model.save_vocabulary(["Qwen"], ["qwen"]).notice)
        self.assertIn(
            "reserved",
            self.model.save_vocabulary(["# managed-looking"], []).notice)
        self.assertIn(
            "reserved",
            self.model.save_vocabulary([], ["-already-prefixed"]).notice)
        with self.assertRaises(ValueError):
            self.model.forget_learned("correction", "missing")

    def test_false_snippet_forget_result_is_visible_failure(self):
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            settings_snapshot=lambda: {
                "corrections": [{
                    "key": "signature", "source": "Snippet: signature",
                    "target": "Cheers", "kind": "snippet"}]},
            forget_snippet_edit=lambda _key: False,
        ))
        model.select_section("Settings")
        state = model.forget_learned("snippet", "signature")
        self.assertEqual(state.notice_level, "error")
        self.assertIn("no longer exists", state.notice)

    def test_app_tone_selection_resolves_each_selected_apps_saved_tone(self):
        settings = normalize_settings({"app_tones": [{
            "bundle": "com.example.mail", "name": "Mail", "tone": "formal",
        }, {
            "bundle": "com.example.code", "name": "Code", "tone": "code",
        }]})
        self.assertEqual(tone_for_app_index(settings.app_tones, 0), "formal")
        self.assertEqual(tone_for_app_index(settings.app_tones, 1), "code")
        with self.assertRaises(IndexError):
            tone_for_app_index(settings.app_tones, 2)

    def test_face_selection_validates_and_calls_runtime(self):
        for face in FACES:
            self.assertEqual(self.model.choose_face(face).face, face)
        self.assertEqual(self.calls[-1], ("face", "bear"))
        with self.assertRaises(ValueError):
            self.model.choose_face("dragon")

    def test_privacy_and_pause_controls_are_callbacks(self):
        self.assertTrue(self.model.set_flight_recorder(True).flight_recorder)
        paused = self.model.set_paused(True)
        self.assertTrue(paused.paused)
        self.assertEqual(
            paused.status_title,
            localized_string("overview.status.paused.title"))
        self.assertFalse(self.model.set_paused(False).paused)
        self.assertIn(("flight", True), self.calls)
        self.assertIn(("pause",), self.calls)
        self.assertIn(("resume",), self.calls)

    def test_diagnostics_actions_report_results(self):
        self.model.open_log()
        snapshot_state = self.model.copy_support_snapshot()
        self.model.open_source_and_license()
        self.model.open_local_license_notices()
        self.model.copy_latest_outbox()
        self.assertEqual(
            self.model.state.notice,
            localized_string("overview.notice.outbox.copied"))
        state = self.model.rerun_verification()
        self.assertIn(("log",), self.calls)
        support_payload = next(
            call[1] for call in self.calls if call[0] == "support_snapshot")
        self.assertEqual(
            json.loads(support_payload)["kind"],
            "whisper-face/support-snapshot")
        self.assertEqual(
            snapshot_state.notice,
            localized_string("diagnostics.notice.support_snapshot.copied"))
        self.assertIn(("source",), self.calls)
        self.assertIn(("license",), self.calls)
        self.assertIn(("outbox",), self.calls)
        self.assertEqual(state.verification, "Mac installation verified")

    def test_support_snapshot_failure_becomes_user_visible_notice(self):
        def fail(_payload):
            raise RuntimeError("clipboard unavailable")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, copy_support_snapshot=fail))

        state = model.copy_support_snapshot()

        self.assertEqual(state.notice_level, "error")
        self.assertIn("clipboard unavailable", state.notice)

    def test_callback_failure_becomes_user_visible_notice(self):
        def fail(_face):
            raise RuntimeError("read only")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, set_face=fail))
        state = model.choose_face("owl")
        self.assertEqual(state.face, "parrot")
        self.assertIn("read only", state.notice)

    def test_validation_operations_and_verification_use_locale_fallback(self):
        def fail_face(_face):
            raise RuntimeError("read only")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, set_face=fail_face,
            rerun_verification=lambda: False), locale="fr-CA")
        with self.assertRaisesRegex(ValueError, "unknown section: Billing"):
            model.select_section("Billing")
        self.assertEqual(
            model.choose_face("owl").notice,
            localized_string(
                "operation.face.change_failed", locale="fr-CA",
                error="read only"))
        self.assertEqual(
            model.rerun_verification().verification,
            localized_string(
                "diagnostics.verification.attention", locale="fr-CA"))

        passing = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, rerun_verification=lambda: None),
            locale="fr-CA")
        self.assertEqual(
            passing.verification_result(),
            localized_string(
                "diagnostics.verification.passed", locale="fr-CA"))

        def fail_verification():
            raise RuntimeError("install unavailable")

        failed = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            rerun_verification=fail_verification), locale="fr-CA")
        self.assertEqual(
            failed.verification_result(),
            localized_string(
                "diagnostics.verification.failed", locale="fr-CA",
                error="install unavailable"))

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
        self.assertEqual(
            state.notice,
            localized_string(
                "overview.notice.status.error", error="service restarting"))

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
