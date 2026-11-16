#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Headless tests for the Whisper Face native-window state seam."""

import json
import math
import re
import sys
import ast
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisper_face_gui import (
    APPKIT_AVAILABLE,
    AcousticKeywordCandidate,
    DROP_TARGET_MAX_PHRASE_CHARS,
    FACES,
    GUIActions,
    POINT_AND_SPEAK_MAX_PHRASE_CHARS,
    RISKY_ACTION_CLASSES,
    SECTIONS,
    SETTINGS_PANES,
    SIDEBAR_ROWS,
    SIDEBAR_STATES,
    STRING_CATALOGS,
    SUPPORTED_LOCALES,
    WhisperFaceViewModel,
    select_sidebar_row,
    sidebar_row_destination,
    sidebar_row_index,
    sidebar_state,
    correction_review_text,
    correction_row_caption,
    create_gui,
    localized_string,
    native_appkit_smoke_contract,
    onboarding_presentation,
    home_bubble_actions,
    home_review_due,
    home_outbox_line,
    home_review_in_row,
    setup_rows,
    normalize_email_compose_receipt,
    normalize_voice_draft_clear_receipt,
    normalize_voice_draft_copy_receipt,
    normalize_point_and_speak_action,
    normalize_acoustic_keyword_inspection,
    normalize_drop_target_preview,
    normalize_point_and_speak_preview,
    normalize_result_evidence,
    normalize_snapshot,
    normalize_settings,
    hotkey_name_for_keycode,
    hotkey_row_detail,
    hotkey_shared_mode_note,
    recent_dictation_row,
    HOTKEY_KEYCODES,
    MODE_GUIDE,
    SOUND_THEMES,
    LANGUAGES,
    result_evidence_text,
    run_native_appkit_smoke,
    resolve_locale,
    set_accessible_text,
    support_snapshot_text,
    sync_accessibility,
    tone_for_app_index,
)
import whisper_face_gui as gui_module

# Model rows in the shape runtime_status_snapshot emits them.
READY_MODEL = {
    "name": "Parakeet Unified 0.6B", "role": "Primary recognition",
    "install_state": "installed", "required": True, "warm": True,
    "status": "RESOLVED",
    "detail": "Exact pinned files resolved · Warm path observed · Runtime "
              "readiness not attested · Capability bounds unavailable",
}
PREPARING_MODEL = {
    **READY_MODEL, "install_state": "checking", "warm": False,
    "status": "UNAVAILABLE",
    "detail": "Exact pinned files not resolved · Runtime readiness not "
              "attested · Capability bounds unavailable",
}


class SnapshotTests(unittest.TestCase):
    @staticmethod
    def email_compose_receipt(*, state="requested", attempted=True):
        return {
            "schema_version": 1,
            "state": state,
            "attempted": attempted,
        }

    @staticmethod
    def voice_draft_copy_receipt(*, state="copied", attempted=True):
        return {
            "schema_version": 1,
            "state": state,
            "attempted": attempted,
        }

    @staticmethod
    def voice_draft_clear_receipt(*, state="cleared", attempted=True):
        return {
            "schema_version": 1,
            "state": state,
            "attempted": attempted,
        }

    @staticmethod
    def drop_preview(
        *, state="resolved", name="Team Inbox", role="AXGroup",
        capture_state="captured",
    ):
        captured = capture_state == "captured"
        return {
            "schema_version": 1,
            "state": state,
            "accessibility_name": name,
            "role": role if state == "resolved" else "",
            "declared_role": role,
            "source_kind": "file_reference",
            "effect": "copy",
            "receipt": {
                "schema_version": 1,
                "capture_state": capture_state,
                "observed_elements": 8 if captured else 0,
                "emitted_targets": 2 if captured else 0,
                "skipped_elements": 1 if captured else 0,
                "truncated": False,
                "observed_targets": 2 if captured else 0,
                "eligible_targets": 1 if captured else 0,
                "contradiction_count": 0,
                "evidence": ["exact_name", "source_compatible",
                             "effect_compatible"] if captured else [],
                "confidence_bucket": "very_high" if captured else "none",
                "margin_bucket": "wide" if captured else "none",
                "capability_basis": "caller_declared_role_policy",
                "execution": "none",
            },
        }

    @staticmethod
    def point_preview(
        *, state="resolved", name="Save Changes", role="button",
        capture_state="captured",
    ):
        captured = capture_state == "captured"
        return {
            "schema_version": 1,
            "state": state,
            "accessibility_name": name,
            "role": role,
            "receipt": {
                "schema_version": 1,
                "capture_state": capture_state,
                "observed_elements": 8 if captured else 0,
                "emitted_targets": 2 if captured else 0,
                "skipped_elements": 1 if captured else 0,
                "truncated": False,
                "observed_targets": 2 if captured else 0,
                "eligible_targets": 2 if captured else 0,
                "contradiction_count": 0,
                "evidence": ["exact", "role"] if captured else [],
                "confidence_bucket": "very_high" if captured else "none",
                "margin_bucket": "wide" if captured else "none",
            },
        }

    @staticmethod
    def point_action(*, state="executed", attempted=True, recheck="matched"):
        return {
            "schema_version": 1,
            "state": state,
            "receipt": {
                "schema_version": 1,
                "capture_state": "captured",
                "observed_elements": 8,
                "emitted_targets": 2,
                "truncated": False,
                "eligible_targets": 2,
                "contradiction_count": 0,
                "evidence": ["normalized", "role"],
                "confidence_bucket": "very_high",
                "margin_bucket": "wide",
                "transaction": {
                    "schema_version": 1,
                    "state": state,
                    "attempted": attempted,
                    "recheck": recheck,
                },
            },
        }

    def test_point_and_speak_preview_projection_is_strict_and_content_scoped(self):
        preview = normalize_point_and_speak_preview(self.point_preview())

        self.assertEqual(preview.state, "resolved")
        self.assertEqual(preview.accessibility_name, "Save Changes")
        self.assertEqual(preview.role, "button")
        self.assertNotIn("Save Changes", repr(preview))
        self.assertEqual(preview.receipt.evidence, ("exact", "role"))

        malformed = self.point_preview()
        malformed["document_text"] = "private document"
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_point_and_speak_preview(malformed)

        malformed = self.point_preview(
            state="ambiguous", name="Secret target", role="button")
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_point_and_speak_preview(malformed)

    def test_point_and_speak_action_receipt_is_strict_and_content_free(self):
        result = normalize_point_and_speak_action(self.point_action())

        self.assertEqual(result.state, "executed")
        self.assertTrue(result.receipt.attempted)
        self.assertEqual(result.receipt.recheck, "matched")
        self.assertNotIn("name", repr(result).casefold())

        for poison in ("phrase", "accessibility_name", "target_id"):
            malformed = self.point_action()
            malformed[poison] = "Project Bluebird"
            with self.subTest(poison=poison), self.assertRaisesRegex(
                    ValueError, "malformed"):
                normalize_point_and_speak_action(malformed)

        malformed = self.point_action(
            state="recheck_failed", attempted=False, recheck="mismatched")
        malformed["receipt"]["transaction"]["attempted"] = True
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_point_and_speak_action(malformed)

    def test_email_compose_receipt_rejects_any_payload_or_identity(self):
        receipt = normalize_email_compose_receipt(
            self.email_compose_receipt())
        self.assertEqual(receipt.state, "requested")
        self.assertTrue(receipt.attempted)

        for key in ("recipients", "subject", "body", "item_id"):
            raw = self.email_compose_receipt()
            raw[key] = "Project Bluebird"
            with self.subTest(key=key), self.assertRaisesRegex(
                    ValueError, "malformed"):
                normalize_email_compose_receipt(raw)

        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_email_compose_receipt(
                self.email_compose_receipt(
                    state="requested", attempted=False))

    def test_voice_draft_copy_receipt_rejects_payload_and_identity(self):
        receipt = normalize_voice_draft_copy_receipt(
            self.voice_draft_copy_receipt())
        self.assertEqual(receipt.state, "copied")
        self.assertTrue(receipt.attempted)

        for key in ("content", "item_id", "destination", "sequence"):
            raw = self.voice_draft_copy_receipt()
            raw[key] = "Project Bluebird"
            with self.subTest(key=key), self.assertRaisesRegex(
                    ValueError, "malformed"):
                normalize_voice_draft_copy_receipt(raw)

        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_voice_draft_copy_receipt(
                self.voice_draft_copy_receipt(
                    state="copied", attempted=False))

    def test_voice_draft_clear_receipt_rejects_payload_and_identity(self):
        receipt = normalize_voice_draft_clear_receipt(
            self.voice_draft_clear_receipt())
        self.assertEqual(receipt.state, "cleared")
        self.assertTrue(receipt.attempted)

        for key in ("content", "content_hash", "item_id", "change_count"):
            raw = self.voice_draft_clear_receipt()
            raw[key] = "Project Bluebird"
            with self.subTest(key=key), self.assertRaisesRegex(
                    ValueError, "malformed"):
                normalize_voice_draft_clear_receipt(raw)

        for state, attempted in (
                ("cleared", False), ("changed", True),
                ("unavailable", True), ("failed", False)):
            with self.subTest(state=state, attempted=attempted), \
                    self.assertRaisesRegex(ValueError, "malformed"):
                normalize_voice_draft_clear_receipt(
                    self.voice_draft_clear_receipt(
                        state=state, attempted=attempted))

    def test_drop_target_preview_projection_is_strict_transient_and_inert(self):
        preview = normalize_drop_target_preview(self.drop_preview())

        self.assertEqual(preview.state, "resolved")
        self.assertEqual(preview.accessibility_name, "Team Inbox")
        self.assertEqual(preview.declared_role, "AXGroup")
        self.assertEqual(preview.receipt.execution, "none")
        self.assertEqual(
            preview.receipt.capability_basis,
            "caller_declared_role_policy")
        self.assertNotIn("Team Inbox", repr(preview))

        malformed = self.drop_preview()
        malformed["target_id"] = "private-identifier"
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_drop_target_preview(malformed)

        malformed = self.drop_preview()
        malformed["receipt"]["execution"] = "drop"
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_drop_target_preview(malformed)

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
                 "count": "3", "kind": "correction",
                 "global_decision": "active",
                 "app_scopes": [{
                     "bundle": "com.example.mail",
                     "name": "Mail",
                     "count": 2,
                     "decision": "active",
                 }]},
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
        self.assertEqual(settings.corrections[0].global_decision, "active")
        self.assertEqual(settings.corrections[0].app_scopes[0].name, "Mail")

    def test_correction_review_explains_scope_reason_and_local_storage(self):
        active = normalize_settings({"corrections": [{
            "key": "gwen",
            "source": "Gwen",
            "target": "Qwen",
            "count": 2,
            "kind": "correction",
            "global_decision": "learning",
            "app_scopes": [{
                "bundle": "com.example.mail",
                "name": "Mail",
                "count": 2,
                "decision": "active",
            }],
        }]}).corrections[0]

        review = correction_review_text(active)

        self.assertIn("Applies: Whole-word matches in Mail.", review)
        self.assertIn("passed the local safety checks in Mail", review)
        self.assertIn("Observed in: Mail 2×.", review)
        self.assertIn("never audio or surrounding transcript", review)

        held = normalize_settings({"corrections": [{
            "key": "gwen",
            "source": "Gwen",
            "target": "Qwen",
            "count": 3,
            "kind": "correction",
            "global_decision": "held_back",
        }]}).corrections[0]
        self.assertIn(
            "Held back because the local correction cases disagree",
            correction_review_text(held),
        )

    def test_localization_catalog_formats_and_falls_back_to_english(self):
        self.assertEqual(SUPPORTED_LOCALES, ("en",))
        self.assertEqual(tuple(STRING_CATALOGS), ("en",))
        self.assertEqual(localized_string("nav.settings"), "Settings")
        self.assertEqual(resolve_locale("en-US"), "en")
        self.assertEqual(resolve_locale("fr-CA"), "en")
        self.assertEqual(
            localized_string("settings.personalize.snippets.detail.many",
                             locale="fr", count=2),
            "2 saved phrases")
        self.assertEqual(
            localized_string("settings.personalize.snippets.detail.one",
                             count=1),
            "1 saved phrase")
        with self.assertRaises(KeyError):
            localized_string("missing.key")
        with self.assertRaises(ValueError):
            localized_string("settings.personalize.snippets.detail.many")
        for key in (
            "nav.home",
            "nav.settings",
            "nav.advanced",
            "advanced.accessibility.shortcut",
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
            "onboarding.step.permissions",
            "onboarding.step.summary",
            "onboarding.action.open_system_settings",
            "onboarding.action.open_system_settings.help",
            "onboarding.action.finish",
            "onboarding.complete.title",
            "onboarding.complete.detail",
            "onboarding.privacy",
            "overview.accessibility.onboarding.face",
            "overview.accessibility.onboarding.steps",
            "overview.accessibility.onboarding.step",
            "settings.personalize.modes",
            "settings.personalize.modes.detail",
            "settings.dialog.modes.title",
            "settings.dialog.modes.message",
            "settings.dialog.modes.row",
            "settings.accessibility.modes.label",
            "settings.accessibility.modes_summary.label",
            "settings.action.view",
            "results.empty.title",
            "results.empty.detail",
            "results.firewall.quarantine.one",
            "results.consequence.review.advisory",
            "results.inspect.summary",
            "models.accessibility.guidance",
            "diagnostics.accessibility.verification",
            "diagnostics.accessibility.open_system_settings",
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
                "validation.language.unsupported",
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
                "operation.self_correction.update_failed",
                "operation.remove_repeats.update_failed",
                "operation.speech_runtime.update_failed",
                "operation.acoustic.update_failed",
                "operation.relisten.update_failed",
                "operation.acoustic.play_failed",
                "operation.acoustic.clear_failed",
                "operation.voice_objects.update_failed",
                "operation.voice_objects.inspect_failed",
                "operation.voice_objects.reveal_failed",
                "operation.voice_objects.transition_failed",
                "operation.voice_objects.purge_failed",
                "operation.voice_objects.compose_failed",
                "operation.voice_objects.copy_failed",
                "operation.voice_objects.clear_failed",
                "operation.demonstrations.inspect_failed",
                "operation.demonstrations.create_failed",
                "operation.demonstrations.reveal_failed",
                "operation.demonstrations.record_failed",
                "operation.demonstrations.approve_failed",
                "operation.demonstrations.cancel_failed",
                "operation.demonstrations.delete_failed",
                "operation.risky_confirmation.start_failed",
                "operation.risky_confirmation.click_failed",
                "operation.risky_confirmation.cancel_failed",
                "operation.log.open_failed",
                "operation.system_settings.open_failed",
                "operation.support_snapshot.copy_failed",
                "operation.support_bundle.export_failed",
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
            "input_monitoring_status": "Granted",
            "hotkey_label": "Right Option",
            "models": [READY_MODEL],
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
        self.assertIn("command-d:advanced", contract.key_equivalents)
        self.assertIn("command-r:verification", contract.key_equivalents)
        self.assertIn("return:continue-setup", contract.key_equivalents)
        self.assertIn("select_section", contract.model_actions)
        self.assertIn("open_system_settings", contract.model_actions)
        self.assertIn("acknowledge_onboarding", contract.model_actions)
        self.assertIn("forget_snippet", contract.model_actions)
        self.assertIn("preview_point_and_speak", contract.model_actions)
        self.assertIn("issue_point_and_speak_nonce", contract.model_actions)
        self.assertIn("press_point_and_speak", contract.model_actions)
        self.assertIn("preview_drop_to_target", contract.model_actions)
        self.assertIn("issue_voice_object_copy_nonce", contract.model_actions)
        self.assertIn("copy_voice_object_draft", contract.model_actions)
        self.assertIn(
            "issue_voice_object_clear_clipboard_nonce", contract.model_actions)
        self.assertIn(
            "clear_voice_object_draft_clipboard", contract.model_actions)
        self.assertIn(
            "click_risky_action_confirmation", contract.model_actions)
        self.assertIn("set_selective_relisten", contract.model_actions)
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

    def test_model_wallet_shadow_is_fixed_advisory_copy_and_never_execution(self):
        provider_ids = (
            "local.parakeet-coreml",
            "local.whisper-tiny-mlx",
            "local.whisper-large-v3-turbo-mlx",
            "local.qwen3.5-4b-ollama",
        )
        supported = {
            "fast_asr": {"local.whisper-tiny-mlx"},
            "final_asr": {
                "local.parakeet-coreml",
                "local.whisper-large-v3-turbo-mlx",
            },
            "cleanup": {"local.qwen3.5-4b-ollama"},
        }
        capabilities = []
        for capability, supported_ids in supported.items():
            capabilities.append({
                "capability": capability,
                "providers": [{
                    "provider_id": provider_id,
                    "eligibility": (
                        "missing_capability_evidence"
                        if provider_id in supported_ids
                        else "unsupported_capability"
                    ),
                } for provider_id in provider_ids],
                "advisory_order": [],
                "selected_provider_id": None,
                "fail_closed": True,
                "attempted": False,
            })
        receipt = {
            "schema_version": 1,
            "mode": "shadow-only",
            "pins": [{
                "provider_id": provider_id,
                "resolution_state": "resolved",
                "warm_path_observed": index < 2,
                "revision_verified": True,
                "capability_bounds_attested": False,
            } for index, provider_id in enumerate(provider_ids)],
            "capabilities": capabilities,
            "attempted": False,
        }

        state = normalize_snapshot({"model_wallet_shadow": receipt})

        self.assertEqual(
            state.model_wallet_advisory,
            localized_string(
                "models.wallet.evidence", resolved=4, warm=2),
        )
        self.assertIn("shadow advisory only",
                      state.model_wallet_advisory.casefold())
        self.assertIn("No model execution or routing",
                      state.model_wallet_advisory)
        self.assertIn("Exact files resolved 4/4", state.model_wallet_advisory)
        self.assertIn("Runtime readiness attested 0/4",
                      state.model_wallet_advisory)

        poisoned = dict(receipt, transcript="Private Project Bluebird")
        poisoned_state = normalize_snapshot({
            "model_wallet_shadow": poisoned,
        })
        self.assertEqual(
            poisoned_state.model_wallet_advisory,
            localized_string("models.wallet.unavailable"),
        )
        self.assertNotIn("Bluebird", poisoned_state.model_wallet_advisory)

    def test_support_snapshot_is_deterministic_and_strictly_transcript_free(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "version": "Local checkout",
            "models": [
                {"name": "Whisper", "status": "Installed"},
                READY_MODEL,
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
            "last_result_evidence": {
                "alternatives": ["private latest evidence"],
            },
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
            {"family": "parakeet", "status": "ready"},
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
                "private rewrite", "another edit", "private latest evidence"):
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
            "input_monitoring_status": "Granted",
            "hotkey_label": "Right Option",
            "models": [READY_MODEL],
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
            "microphone_status": "Unavailable",
            "accessibility_status": "Needs attention",
            "models": [PREPARING_MODEL],
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
            "models": [PREPARING_MODEL],
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
            microphone_status="Ready", accessibility_status="Granted",
            input_monitoring_status="Granted")
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
        runtime["models"] = [READY_MODEL]
        self.assertEqual(
            next(step for step in model.refresh().onboarding_steps
                 if not step.complete).key,
            "first_dictation",
        )
        runtime["last_word_count"] = 5
        self.assertTrue(model.refresh().onboarding_complete)

    def test_first_run_presentation_keeps_completion_visible_until_acknowledged(self):
        first_run = normalize_snapshot({
            "microphone_status": "Unavailable",
            "accessibility_status": "Needs attention",
        })
        active = onboarding_presentation(
            first_run.onboarding_steps, acknowledged=False)
        self.assertTrue(active.visible)
        self.assertFalse(active.complete)
        self.assertEqual(active.current_key, "permissions")
        self.assertEqual(active.title, "First, let me hear you")
        self.assertEqual(active.action_title, "Open System Settings")

        complete = normalize_snapshot({
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "hotkey_practiced": True,
            "models": [READY_MODEL],
            "last_word_count": 5,
        })
        celebration = onboarding_presentation(
            complete.onboarding_steps, acknowledged=False)
        self.assertTrue(celebration.visible)
        self.assertTrue(celebration.complete)
        self.assertIsNone(celebration.current_key)
        self.assertEqual(celebration.title, "Nice. You’re ready.")
        self.assertEqual(celebration.action_title, "Start Dictating")
        self.assertFalse(onboarding_presentation(
            complete.onboarding_steps, acknowledged=True).visible)

    def test_a_permission_still_being_checked_offers_no_system_settings(self):
        starting = normalize_snapshot({
            "microphone_status": "Starting",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
        })
        self.assertEqual(starting.missing_permissions, ())
        presentation = onboarding_presentation(
            starting.onboarding_steps, acknowledged=False,
            permission_missing=bool(starting.missing_permissions))
        self.assertEqual(presentation.current_key, "permissions")
        self.assertEqual(presentation.action_title, "Keep Going")

    def test_onboarding_copy_explains_observed_practice_and_recovery(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "hotkey_label": "Right Option",
            "models": [READY_MODEL],
        })
        hotkey = next(
            step for step in state.onboarding_steps if step.key == "hotkey")
        self.assertFalse(hotkey.complete)
        self.assertIn("observes capture", hotkey.detail)

        first_dictation = next(
            step for step in state.onboarding_steps
            if step.key == "first_dictation")
        self.assertIn("Voice Outbox", first_dictation.detail)
        # The button's name, held on one line by no-break spaces.
        self.assertIn("Copy\u00a0&\u00a0Dismiss", first_dictation.detail)

    def test_status_presentation_covers_capture_processing_recovery_and_degraded(self):
        common = {
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "models": [READY_MODEL],
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
        self.assertEqual(recovery.outbox_unchecked, 0)

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

    def test_copies_of_unchecked_pastes_do_not_ask_for_recovery(self):
        common = {
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "models": [READY_MODEL],
        }
        ready = normalize_snapshot(common)
        # Five kept copies, none of which needs a home: the phase, the
        # headline and the instruction are those of an empty outbox.
        calm = normalize_snapshot({
            **common, "outbox_count": 5, "outbox_needs_home": 0,
            "outbox_summary": "Pasted, not double-checked"})
        self.assertEqual(calm.status_phase, "ready")
        self.assertEqual(calm.status_title, ready.status_title)
        self.assertEqual(calm.status_detail, ready.status_detail)
        self.assertEqual(calm.outbox_count, 5)
        self.assertEqual(calm.outbox_unchecked, 5)
        self.assertEqual(
            calm.outbox_summary,
            localized_string("overview.outbox.summary.pasted_unchecked"))
        self.assertEqual(
            sidebar_state(calm.status_phase, setting_up=False),
            ("shell.state.ready", "good"))
        # They do not hide a real problem either: a missing permission is
        # still the degraded phase.
        degraded = normalize_snapshot({
            **common, "accessibility_status": "Needs attention",
            "outbox_count": 5, "outbox_needs_home": 0})
        self.assertEqual(degraded.status_phase, "degraded")

        # Two of five still need a home: recovery, for those two.
        mixed = normalize_snapshot({
            **common, "outbox_count": 5, "outbox_needs_home": 2})
        self.assertEqual(mixed.status_phase, "recovery")
        self.assertIn("2 dictations", mixed.status_detail)
        self.assertEqual(mixed.outbox_unchecked, 3)

        # A count that is missing, is not a count, or does not fit the
        # outbox is never read as "nothing needs a home".
        for reported in (None, True, False, "0", 0.0, [], {}):
            with self.subTest(reported=reported):
                careful = normalize_snapshot({
                    **common, "outbox_count": 5,
                    "outbox_needs_home": reported})
                self.assertEqual(careful.status_phase, "recovery")
                self.assertEqual(careful.outbox_unchecked, 0)
        negative = normalize_snapshot({
            **common, "outbox_count": 5, "outbox_needs_home": -3})
        self.assertEqual(negative.status_phase, "ready")
        self.assertEqual(negative.outbox_unchecked, 5)
        beyond = normalize_snapshot({
            **common, "outbox_count": 5, "outbox_needs_home": 99})
        self.assertEqual(beyond.status_phase, "recovery")
        self.assertEqual(beyond.outbox_unchecked, 0)
        self.assertIn("5 dictations", beyond.status_detail)
        # A state built with a count and nothing else needs a home too.
        self.assertEqual(
            gui_module.GUIState(outbox_count=3).outbox_unchecked, 0)

    def test_homes_outbox_row_says_what_the_outbox_holds(self):
        def line(**changes):
            key, values = home_outbox_line(
                gui_module.GUIState(**changes))
            return localized_string(key, **values)

        self.assertEqual(line(), localized_string("overview.outbox.empty"))
        # Only copies of pastes that could not be read back.
        self.assertEqual(
            line(outbox_count=5, outbox_unchecked=5,
                 outbox_summary="Pasted, not double-checked"),
            "Voice Outbox: 5 pasted, not double-checked")
        self.assertEqual(
            line(outbox_count=1, outbox_unchecked=1),
            "Voice Outbox: 1 pasted, not double-checked")
        # Anything that still needs a home keeps the recoverable line and
        # the summary of what needs it. The copies beside it are counted
        # as copies, so the row and the headline ("1 dictation needs…")
        # never disagree about how many need a review.
        self.assertEqual(
            line(outbox_count=5, outbox_unchecked=4,
                 outbox_summary="Not pasted — destination changed"),
            "Voice Outbox: 1 recoverable, 4 pasted · "
            "Not pasted — destination changed")
        self.assertEqual(
            line(outbox_count=20, outbox_unchecked=1,
                 outbox_summary="Paste may have landed — verify before "
                 "reusing"),
            "Voice Outbox: 19 recoverable, 1 pasted · "
            "Paste may have landed — verify before reusing")
        # A count of copies that does not fit the outbox is never taken
        # further than the outbox holds, in either direction.
        self.assertEqual(
            line(outbox_count=2, outbox_unchecked=-3,
                 outbox_summary="Not pasted — destination changed"),
            "Voice Outbox: 2 recoverable · Not pasted — destination changed")
        self.assertEqual(
            line(outbox_count=2, outbox_unchecked=9),
            "Voice Outbox: 2 pasted, not double-checked")
        self.assertEqual(
            line(outbox_count=2,
                 outbox_summary="Paste may have landed — verify before "
                 "reusing"),
            "Voice Outbox: 2 recoverable · "
            "Paste may have landed — verify before reusing")
        # The window draws exactly this.
        source = (ROOT / "whisper_face_gui.py").read_text(encoding="utf-8")
        self.assertIn(
            "            outbox_key, outbox_values = home_outbox_line(state)\n"
            "            outbox = self._l(outbox_key, **outbox_values)\n"
            "            self.dynamic[\"overview_outbox\"]"
            ".setStringValue_(outbox)\n", source)

    def test_the_calm_outbox_copy_counts_without_calling_it_a_problem(self):
        catalog = STRING_CATALOGS["en"]
        row = localized_string("overview.outbox.unchecked", count=5)
        self.assertEqual(row, "Voice Outbox: 5 pasted, not double-checked")
        for text in (row, catalog["overview.outbox.summary.pasted_unchecked"]):
            for nag in ("recover", "review", "attention", "verify", "needs",
                        "fail", "error", "unverified", "problem", "!"):
                self.assertNotIn(nag, text.casefold())
        # The two strings that do describe a problem are as they were.
        self.assertEqual(
            catalog["overview.outbox.summary.paste_attempted"],
            "Paste may have landed — verify before reusing")
        self.assertEqual(
            catalog["overview.outbox.summary.not_pasted"],
            "Not pasted — destination changed")
        self.assertEqual(
            catalog["overview.outbox.pending"],
            "Voice Outbox: {count} recoverable · {summary}")
        self.assertEqual(
            catalog["overview.outbox.mixed"],
            "Voice Outbox: {count} recoverable, {unchecked} pasted · "
            "{summary}")
        # Copy & Dismiss acts on whatever the outbox holds, so neither its
        # help nor its notice calls an unchecked copy "recoverable".
        for key in ("overview.action.copy_outbox.help",
                    "overview.notice.outbox.copied"):
            self.assertNotIn("recoverable", catalog[key])
            self.assertIn("Voice Outbox", catalog[key])

    def test_error_text_meets_aa_on_light_surfaces(self):
        from whisper_face_theme import LIGHT_PALETTE, contrast_ratio

        for surface in (LIGHT_PALETTE.bg, LIGHT_PALETTE.surface):
            self.assertGreaterEqual(
                contrast_ratio(gui_module.ERROR_TEXT_ON_LIGHT, surface), 4.5)

    def test_ready_fallback_remains_usable_while_warning_is_explained(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "models": [
                READY_MODEL,
                {**READY_MODEL, "name": "Whisper large-v3-turbo",
                 "required": False, "install_state": "mismatch",
                 "warm": False, "status": "UNAVAILABLE"},
            ],
        })
        self.assertEqual(state.status_phase, "ready")
        self.assertEqual(len(state.degraded_issues), 1)
        self.assertEqual(state.degraded_issues[0].severity, "warning")
        self.assertIn("still works", state.degraded_issues[0].detail)
        self.assertIn(
            "Whisper large-v3-turbo", state.degraded_issues[0].detail)

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

    def test_explicit_result_evidence_reveal_is_strict_and_private(self):
        payload = {
            "schema_version": 1,
            "kind": "whisper-face/result-evidence",
            "alternatives": ["private alternative text"],
            "protected_anchors": ["Qwen", "Project Bluebird"],
            "proof_edits": [{
                "kind": "filler",
                "before": "um",
                "after": "",
                "accepted": True,
                "reason": "allowlisted filler",
            }, {
                "kind": "rewrite",
                "before": "private before",
                "after": "private after",
                "accepted": False,
                "reason": "changed protected meaning",
            }],
            "timings_ms": {
                "release": 842.2,
                "asr": 410.0,
                "cleanup": 87.5,
            },
        }

        evidence = normalize_result_evidence(payload)
        rendered = result_evidence_text(evidence)

        self.assertIn("private alternative text", rendered)
        self.assertIn("• Project Bluebird", rendered)
        self.assertIn("ACCEPTED · filler", rendered)
        self.assertIn("REJECTED · rewrite", rendered)
        self.assertIn("Total release: 842.2 ms", rendered)
        self.assertNotIn("private alternative text", repr(evidence))
        self.assertNotIn("private before", repr(evidence.proof_edits[1]))

        poisoned = dict(payload, transcript="must not enter the reveal")
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_result_evidence(poisoned)
        poisoned = dict(payload, timings_ms={"unknown": 1})
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_result_evidence(poisoned)

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
    def test_result_evidence_is_fetched_only_on_explicit_inspection(self):
        reads = []
        private = {
            "schema_version": 1,
            "kind": "whisper-face/result-evidence",
            "alternatives": ["private latest alternative"],
            "protected_anchors": [],
            "proof_edits": [],
            "timings_ms": {},
        }
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {"last_word_count": 4},
            inspect_result_evidence=lambda: reads.append(True) or private,
        ))

        self.assertEqual(reads, [])
        evidence = model.inspect_result_evidence()

        self.assertEqual(reads, [True])
        self.assertEqual(
            evidence.alternatives, ("private latest alternative",))
        self.assertNotIn("private latest alternative", repr(model.state))
        self.assertNotIn(
            "private latest alternative", support_snapshot_text(model.state))

    def test_point_and_speak_preview_is_explicit_bounded_and_never_enters_state(self):
        phrases = []
        private_name = "Project Bluebird Submit"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            preview_point_and_speak=lambda phrase: (
                phrases.append(phrase)
                or SnapshotTests.point_preview(name=private_name)),
        ))

        preview = model.preview_point_and_speak("submit button")

        self.assertEqual(phrases, ["submit button"])
        self.assertEqual(preview.accessibility_name, private_name)
        self.assertNotIn(private_name, repr(model.state))
        self.assertNotIn(private_name, support_snapshot_text(model.state))
        self.assertNotIn(private_name, repr(preview))
        for invalid in ("", "line\nbreak", "x" * (
                POINT_AND_SPEAK_MAX_PHRASE_CHARS + 1)):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaisesRegex(ValueError, "between 1"):
                    model.preview_point_and_speak(invalid)
        self.assertEqual(phrases, ["submit button"])

    def test_point_and_speak_permission_ambiguity_and_malformed_data_fail_closed(self):
        cases = [
            SnapshotTests.point_preview(
                state="permission_denied", name="", role="",
                capture_state="permission_denied"),
            SnapshotTests.point_preview(
                state="ambiguous", name="", role=""),
            {"unexpected": "private target text"},
        ]
        expected = ("permission_denied", "ambiguous", "unavailable")
        for raw, state in zip(cases, expected):
            with self.subTest(state=state):
                model = WhisperFaceViewModel(GUIActions(
                    status_snapshot=lambda: {},
                    preview_point_and_speak=lambda _phrase, raw=raw: raw,
                ))
                preview = model.preview_point_and_speak("save button")
                self.assertEqual(preview.state, state)
                self.assertEqual(preview.accessibility_name, "")
                self.assertEqual(preview.role, "")

    def test_point_and_speak_press_uses_session_nonce_and_never_enters_state(self):
        calls = []
        nonce = "session_nonce_1234567890"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            issue_point_and_speak_nonce=lambda: nonce,
            press_point_and_speak=lambda supplied_nonce, phrase, role: (
                calls.append((supplied_nonce, phrase, role))
                or SnapshotTests.point_action()),
        ))

        issued = model.issue_point_and_speak_nonce()
        result = model.press_point_and_speak(
            issued, "save button", "button")

        self.assertEqual(calls, [(nonce, "save button", "button")])
        self.assertEqual(result.state, "executed")
        self.assertNotIn("save button", repr(model.state).casefold())
        self.assertNotIn(nonce, repr(model.state))

        malformed = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            issue_point_and_speak_nonce=lambda: nonce,
            press_point_and_speak=lambda _nonce, _phrase, _role: {
                "target_id": "private-native-identity"},
        ))
        fallback = malformed.press_point_and_speak(
            nonce, "save button", "button")
        self.assertEqual(fallback.state, "unavailable")
        self.assertFalse(fallback.receipt.attempted)
        for unsupported in ("text_field", ["button"]):
            with self.subTest(unsupported=unsupported), self.assertRaises(
                    ValueError):
                model.press_point_and_speak(
                    nonce, "search field", unsupported)

    def test_drop_target_preview_is_explicit_inert_and_never_enters_state(self):
        calls = []
        private_name = "Project Bluebird Team Inbox"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            preview_drop_to_target=lambda phrase, role, source, effect: (
                calls.append((phrase, role, source, effect))
                or SnapshotTests.drop_preview(name=private_name)),
        ))

        preview = model.preview_drop_to_target(
            "team inbox", "AXGroup", "file_reference", "copy")

        self.assertEqual(calls, [(
            "team inbox", "AXGroup", "file_reference", "copy")])
        self.assertEqual(preview.accessibility_name, private_name)
        self.assertEqual(preview.receipt.execution, "none")
        self.assertNotIn(private_name, repr(model.state))
        self.assertNotIn(private_name, support_snapshot_text(model.state))
        self.assertNotIn(private_name, repr(preview))
        for invalid in ("", "line\nbreak", "x" * (
                DROP_TARGET_MAX_PHRASE_CHARS + 1)):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaisesRegex(ValueError, "between 1"):
                    model.preview_drop_to_target(
                        invalid, "AXGroup", "file_reference", "copy")
        with self.assertRaisesRegex(ValueError, "capability"):
            model.preview_drop_to_target(
                "inbox", "AXButton", "file_reference", "copy")
        self.assertEqual(len(calls), 1)

    def test_drop_target_permission_and_malformed_execution_fail_closed(self):
        denied = SnapshotTests.drop_preview(
            state="permission_denied", name="", role="AXGroup",
            capture_state="permission_denied")
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            preview_drop_to_target=lambda *_args: denied,
        ))
        preview = model.preview_drop_to_target(
            "inbox", "AXGroup", "file_reference", "copy")
        self.assertEqual(preview.state, "permission_denied")
        self.assertEqual(preview.accessibility_name, "")

        poisoned = SnapshotTests.drop_preview()
        poisoned["receipt"]["execution"] = "drop"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            preview_drop_to_target=lambda *_args: poisoned,
        ))
        preview = model.preview_drop_to_target(
            "inbox", "AXGroup", "file_reference", "copy")
        self.assertEqual(preview.state, "unavailable")
        self.assertEqual(preview.accessibility_name, "")

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
        self.voice_draft_inspections = 0
        self.voice_draft_reveals = 0
        self.demonstration_inspections = 0
        self.demonstration_reveals = 0
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

        def set_acoustic(enabled):
            self.calls.append(("acoustic", enabled))
            self.runtime["acoustic_time_machine"] = enabled
            if not enabled:
                self.runtime["retained_consequence_spans"] = 0

        def set_selective_relisten(enabled):
            self.calls.append(("selective_relisten", enabled))
            self.runtime["selective_relisten"] = {
                "requested": enabled,
                "evidence_ready": True,
                "enabled": enabled,
                "verifier_ready": enabled,
                "warming": False,
                "status": "ready" if enabled else "off",
            }

        def set_voice_objects(enabled):
            self.calls.append(("voice_objects", enabled))
            self.runtime["voice_object_commands"] = enabled
            self.runtime["voice_object_inbox_status"] = (
                "Ready" if enabled else "Off")

        def clear_acoustic():
            self.calls.append(("clear_retained",))
            self.runtime["retained_consequence_spans"] = 0

        def pause():
            self.calls.append(("pause",))
            self.runtime["paused"] = True

        def resume():
            self.calls.append(("resume",))
            self.runtime["paused"] = False

        def inspect_keywords():
            self.keyword_reads += 1
            return SnapshotTests.keyword_export()

        def inspect_voice_drafts():
            self.voice_draft_inspections += 1
            return ({
                "item_id": "voice-object:utterance-1",
                "sequence": 1,
                "destination": "task",
                "state": "queued",
            },)

        def reveal_voice_draft(item_id):
            self.voice_draft_reveals += 1
            self.calls.append(("reveal_voice_draft", item_id))
            return {
                "sequence": 1,
                "destination": "task",
                "state": "queued",
                "content": "Title: private launch plan",
            }

        def inspect_demonstrations():
            self.demonstration_inspections += 1
            return ({
                "draft_id": "demo-" + "1" * 32,
                "sequence": 3,
                "domain": "mail",
                "state": "recording",
                "step_count": 1,
            }, {
                "draft_id": "demo-" + "4" * 32,
                "sequence": 5,
                "domain": "notes",
                "state": "approved",
                "step_count": 1,
            },)

        def reveal_demonstration(draft_id):
            self.demonstration_reveals += 1
            self.calls.append(("reveal_demonstration", draft_id))
            return {
                "sequence": 3,
                "domain": "mail",
                "state": "recording",
                "steps": ({
                    "action": "set_subject",
                    "text": "Private demonstration subject",
                },),
            }

        def start_risky_confirmation(risk):
            self.calls.append(("risk_start", risk))
            self.runtime["risky_action_confirmation"] = {
                "risk": risk,
                "state": "awaiting_voice",
                "reason": "proposed",
            }
            return True

        def click_risky_confirmation():
            self.calls.append(("risk_click",))
            self.runtime["risky_action_confirmation"]["state"] = "confirmed"
            self.runtime["risky_action_confirmation"][
                "reason"] = "two_factor_confirmed"
            return True

        def cancel_risky_confirmation():
            self.calls.append(("risk_cancel",))
            self.runtime["risky_action_confirmation"]["state"] = "cancelled"
            self.runtime["risky_action_confirmation"][
                "reason"] = "explicitly_cancelled"
            return True

        self.actions = GUIActions(
            status_snapshot=lambda: dict(self.runtime),
            settings_snapshot=lambda: dict(self.private_settings),
            set_face=set_face,
            set_flight_recorder=set_flight,
            set_acoustic_time_machine=set_acoustic,
            set_selective_relisten=set_selective_relisten,
            set_voice_object_commands=set_voice_objects,
            inspect_voice_object_drafts=inspect_voice_drafts,
            reveal_voice_object_draft=reveal_voice_draft,
            acknowledge_voice_object_draft=lambda item_id:
                self.calls.append(("ack_voice_draft", item_id)) or True,
            cancel_voice_object_draft=lambda item_id:
                self.calls.append(("cancel_voice_draft", item_id)) or True,
            purge_terminal_voice_object_drafts=lambda:
                self.calls.append(("purge_voice_drafts",)) or 2,
            inspect_demonstration_drafts=inspect_demonstrations,
            create_demonstration_draft=lambda domain: {
                "draft_id": "demo-" + "2" * 32,
                "sequence": 4,
                "domain": domain,
                "state": "recording",
                "step_count": 0,
            },
            reveal_demonstration_draft=reveal_demonstration,
            record_demonstration_step=lambda draft_id, action, text:
                self.calls.append(
                    ("record_demonstration", draft_id, action, text)) or True,
            approve_demonstration_draft=lambda draft_id:
                self.calls.append(("approve_demonstration", draft_id)) or True,
            cancel_demonstration_draft=lambda draft_id:
                self.calls.append(("cancel_demonstration", draft_id)) or True,
            delete_approved_demonstration_draft=lambda draft_id:
                self.calls.append(
                    ("delete_approved_demonstration", draft_id)) or True,
            start_risky_action_confirmation=start_risky_confirmation,
            click_risky_action_confirmation=click_risky_confirmation,
            cancel_risky_action_confirmation=cancel_risky_confirmation,
            play_retained_span=lambda:
                self.calls.append(("play_retained",)) or True,
            clear_retained_spans=clear_acoustic,
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

    def test_acoustic_replay_preference_and_result_actions_use_callbacks(self):
        self.runtime.update(
            acoustic_time_machine=True,
            retained_consequence_spans=1,
            last_word_count=4,
        )
        state = self.model.refresh()
        self.assertTrue(state.acoustic_time_machine)
        self.assertTrue(state.last_result.acoustic_replay_enabled)
        self.assertEqual(state.last_result.retained_span_count, 1)

        self.model.play_retained_span()
        self.model.clear_retained_spans()
        self.assertEqual(self.model.state.last_result.retained_span_count, 0)
        self.model.set_acoustic_time_machine(False)

        self.assertIn(("play_retained",), self.calls)
        self.assertIn(("clear_retained",), self.calls)
        self.assertIn(("acoustic", False), self.calls)
        self.assertFalse(self.model.state.acoustic_time_machine)

    def test_selective_relisten_projection_and_toggle_use_content_free_state(self):
        self.runtime["selective_relisten"] = {
            "requested": False,
            "evidence_ready": True,
            "enabled": False,
            "verifier_ready": False,
            "warming": False,
            "status": "off",
            "private_manifest": "must-not-project",
        }
        state = self.model.refresh()
        self.assertFalse(state.selective_relisten_requested)
        self.assertTrue(state.selective_relisten_evidence_ready)
        self.assertEqual(state.selective_relisten_status, "off")
        self.assertNotIn("private_manifest", repr(state))

        state = self.model.set_selective_relisten(True)

        self.assertIn(("selective_relisten", True), self.calls)
        self.assertTrue(state.selective_relisten_requested)
        self.assertEqual(state.selective_relisten_status, "ready")

    def test_selective_relisten_fails_closed_on_unknown_or_failed_state(self):
        state = normalize_snapshot({
            "selective_relisten": {
                "requested": True,
                "evidence_ready": False,
                "status": "private-unknown-state",
            },
        })
        self.assertTrue(state.selective_relisten_requested)
        self.assertFalse(state.selective_relisten_evidence_ready)
        self.assertEqual(state.selective_relisten_status, "receipt-invalid")

        model = WhisperFaceViewModel(GUIActions(
            set_selective_relisten=lambda _enabled:
                (_ for _ in ()).throw(RuntimeError("evidence unavailable")),
        ))
        failed = model.set_selective_relisten(True)
        self.assertFalse(failed.selective_relisten_requested)
        self.assertEqual(failed.notice_level, "error")
        self.assertIn("evidence unavailable", failed.notice)

    def test_acoustic_privacy_copy_and_projection_expose_no_audio_metadata(self):
        state = normalize_snapshot({
            "acoustic_time_machine": True,
            "retained_consequence_spans": 1,
            "retained_audio_expires_at": "private-monotonic-deadline",
            "retained_audio_duration": "private-duration",
            "last_word_count": 4,
        })

        self.assertTrue(state.last_result.acoustic_replay_enabled)
        self.assertEqual(state.last_result.retained_span_count, 1)
        self.assertNotIn("private-monotonic-deadline", repr(state))
        self.assertNotIn("private-duration", repr(state))
        privacy = localized_string("results.privacy")
        self.assertIn("wiped after one minute", privacy)
        self.assertIn("never written, logged, or sent", privacy)

    def test_voice_object_setting_exposes_only_opt_in_and_queue_count(self):
        self.runtime.update(
            voice_object_commands=True,
            voice_object_inbox_count=2,
            voice_object_inbox_status="Ready",
        )

        state = self.model.refresh()
        self.assertTrue(state.voice_object_commands)
        self.assertEqual(state.voice_object_inbox_count, 2)
        self.assertEqual(state.voice_object_inbox_status, "Ready")
        state = self.model.set_voice_object_commands(False)
        self.assertIn(("voice_objects", False), self.calls)
        self.assertFalse(state.voice_object_commands)
        self.assertEqual(state.voice_object_inbox_count, 2)
        self.assertEqual(state.voice_object_inbox_status, "Off")
        self.assertEqual(
            localized_string("settings.privacy.voice_objects"),
            "Voice Object Commands",
        )
        self.assertIn(
            "Nothing is sent or scheduled",
            localized_string("settings.accessibility.voice_objects.help"),
        )

    def test_risky_confirmation_renders_only_closed_content_free_state(self):
        self.assertEqual(len(RISKY_ACTION_CLASSES), 4)

        state = self.model.start_risky_action_confirmation(
            "external_communication")
        self.assertEqual(state.risky_action_risk, "external_communication")
        self.assertEqual(
            state.risky_action_confirmation_state, "awaiting_voice")
        self.assertIn(("risk_start", "external_communication"), self.calls)

        self.runtime["risky_action_confirmation"][
            "state"] = "awaiting_click"
        state = self.model.refresh()
        self.assertEqual(state.risky_action_confirmation_state, "awaiting_click")
        state = self.model.click_risky_action_confirmation()
        self.assertEqual(state.risky_action_confirmation_state, "confirmed")
        self.assertIn(("risk_click",), self.calls)

        with self.assertRaises(ValueError):
            self.model.start_risky_action_confirmation("arbitrary_payload")

    def test_risky_confirmation_snapshot_rejects_content_and_unknown_values(self):
        secret = "send the Project Bluebird budget to Ada"
        state = normalize_snapshot({
            "risky_action_confirmation": {
                "risk": secret,
                "state": secret,
                "reason": secret,
                "payload": secret,
            },
        })

        self.assertEqual(state.risky_action_risk, "none")
        self.assertEqual(state.risky_action_confirmation_state, "idle")
        self.assertNotIn(secret, repr(state))

    def test_voice_inbox_content_is_lazy_transient_and_actions_are_explicit(self):
        self.model.refresh()
        self.assertEqual(self.voice_draft_inspections, 0)
        self.assertEqual(self.voice_draft_reveals, 0)
        self.assertNotIn("private launch plan", repr(self.model.state))

        drafts = self.model.inspect_voice_object_drafts()
        self.assertEqual(self.voice_draft_inspections, 1)
        self.assertEqual(self.voice_draft_reveals, 0)
        self.assertEqual(drafts[0].destination, "task")
        self.assertNotIn("voice-object:utterance-1", repr(drafts[0]))

        revealed = self.model.reveal_voice_object_draft(drafts[0])
        self.assertEqual(self.voice_draft_reveals, 1)
        self.assertIn("private launch plan", revealed.content)
        self.assertNotIn("private launch plan", repr(revealed))
        self.assertNotIn("private launch plan", repr(self.model.state))

        self.model.transition_voice_object_draft(
            drafts[0], target="acknowledged")
        self.assertIn(
            ("ack_voice_draft", "voice-object:utterance-1"), self.calls)
        self.model.inspect_voice_object_drafts()
        self.model.transition_voice_object_draft(
            drafts[0], target="cancelled")
        self.assertIn(
            ("cancel_voice_draft", "voice-object:utterance-1"), self.calls)
        self.model.purge_terminal_voice_object_drafts()
        self.assertIn(("purge_voice_drafts",), self.calls)

    def test_voice_inbox_reveal_requires_metadata_from_explicit_inspection(self):
        from whisper_face_gui import VoiceDraftMetadata

        forged = VoiceDraftMetadata(
            "voice-object:utterance-1", 1, "task", "queued")
        with self.assertRaisesRegex(ValueError, "Could not reveal"):
            self.model.reveal_voice_object_draft(forged)

        self.assertEqual(self.voice_draft_inspections, 0)
        self.assertEqual(self.voice_draft_reveals, 0)

    def test_email_compose_is_explicit_email_only_and_receipt_stays_content_free(self):
        calls = []
        nonce = "email_session_nonce_123456"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            inspect_voice_object_drafts=lambda: ({
                "item_id": "voice-object:email-1",
                "sequence": 7,
                "destination": "email_draft",
                "state": "queued",
            },),
            reveal_voice_object_draft=lambda _item_id: {
                "sequence": 7,
                "destination": "email_draft",
                "state": "queued",
                "content": "To: ada@example.com\nSubject: Project Bluebird\n\n8492",
            },
            issue_voice_object_email_compose_nonce=lambda: nonce,
            compose_voice_object_email=lambda supplied_nonce, item_id: (
                calls.append((supplied_nonce, item_id))
                or SnapshotTests.email_compose_receipt()),
        ))

        draft = model.inspect_voice_object_drafts()[0]
        revealed = model.reveal_voice_object_draft(draft)
        receipt = model.compose_voice_object_email(draft)

        self.assertIn("Project Bluebird", revealed.content)
        self.assertEqual(receipt.state, "requested")
        self.assertEqual(calls, [(nonce, "voice-object:email-1")])
        self.assertNotIn("ada@example.com", repr(receipt))
        self.assertNotIn("Project Bluebird", repr(receipt))
        self.assertNotIn("8492", repr(model.state))
        self.assertEqual(
            model.compose_voice_object_email(draft).state, "unavailable")
        self.assertEqual(len(calls), 1)

        non_email_calls = []
        non_email = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            inspect_voice_object_drafts=lambda: ({
                "item_id": "voice-object:task-1",
                "sequence": 8,
                "destination": "task",
                "state": "queued",
            },),
            issue_voice_object_email_compose_nonce=lambda:
                non_email_calls.append("nonce") or nonce,
            compose_voice_object_email=lambda *_args:
                non_email_calls.append("compose") or
                SnapshotTests.email_compose_receipt(),
        ))
        task = non_email.inspect_voice_object_drafts()[0]
        self.assertEqual(
            non_email.compose_voice_object_email(task).state, "unavailable")
        self.assertEqual(non_email_calls, [])

    def test_email_compose_copy_defines_confirmation_and_requested_boundary(self):
        inbox_copy = localized_string("settings.dialog.voice_objects.message")
        confirm_copy = localized_string(
            "settings.dialog.voice_objects.compose.message", sequence=7)
        receipt_copy = localized_string(
            "settings.dialog.voice_objects.compose.receipt",
            state="requested", attempted="yes")

        self.assertIn("separate confirmation", inbox_copy)
        self.assertIn("cannot send or auto-dispatch", confirm_copy)
        self.assertIn("never enter a URL, process argument, log, status, or receipt",
                      confirm_copy)
        self.assertIn("only handed to the compose UI", receipt_copy)
        self.assertIn("does not confirm a saved draft or send", receipt_copy)

    def test_task_copy_requires_reveal_and_passes_exact_fresh_identity_once(self):
        calls = []
        secret = "Title: Project Bluebird\nNotes: Private launch 8492"
        nonce = "copy_session_nonce_123456"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            inspect_voice_object_drafts=lambda: ({
                "item_id": "voice-object:task-1",
                "sequence": 9,
                "destination": "task",
                "state": "queued",
            },),
            reveal_voice_object_draft=lambda _item_id: {
                "sequence": 9,
                "destination": "task",
                "state": "queued",
                "content": secret,
            },
            issue_voice_object_copy_nonce=lambda:
                calls.append(("nonce",)) or nonce,
            copy_voice_object_draft=lambda supplied, item_id, destination: (
                calls.append(("copy", supplied, item_id, destination))
                or SnapshotTests.voice_draft_copy_receipt()),
        ))

        task = model.inspect_voice_object_drafts()[0]
        self.assertEqual(
            model.copy_voice_object_draft(task).state, "unavailable")
        self.assertEqual(calls, [])
        revealed = model.reveal_voice_object_draft(task)
        receipt = model.copy_voice_object_draft(task)

        self.assertIn("Bluebird", revealed.content)
        self.assertEqual(receipt.state, "copied")
        self.assertEqual(calls, [
            ("nonce",),
            ("copy", nonce, "voice-object:task-1", "task"),
        ])
        self.assertNotIn(secret, repr(receipt))
        self.assertNotIn(secret, repr(model.state))
        self.assertEqual(
            model.copy_voice_object_draft(task).state, "unavailable")
        self.assertEqual(len(calls), 2)

    def test_task_copy_offers_one_explicit_content_free_clear(self):
        calls = []
        secret = "Title: Project Bluebird\nNotes: Private launch 8492"
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            inspect_voice_object_drafts=lambda: ({
                "item_id": "voice-object:task-1",
                "sequence": 9,
                "destination": "task",
                "state": "queued",
            },),
            reveal_voice_object_draft=lambda _item_id: {
                "sequence": 9,
                "destination": "task",
                "state": "queued",
                "content": secret,
            },
            issue_voice_object_copy_nonce=lambda: "copy_nonce_1234567890",
            copy_voice_object_draft=lambda *_args:
                SnapshotTests.voice_draft_copy_receipt(),
            issue_voice_object_clear_clipboard_nonce=lambda:
                calls.append(("issue_clear",)) or "clear_nonce_123456789",
            clear_voice_object_draft_clipboard=lambda nonce:
                calls.append(("clear", nonce)) or
                SnapshotTests.voice_draft_clear_receipt(),
            acknowledge_voice_object_draft=lambda _item_id:
                (_ for _ in ()).throw(AssertionError("queue must stay queued")),
            cancel_voice_object_draft=lambda _item_id:
                (_ for _ in ()).throw(AssertionError("queue must stay queued")),
        ))

        task = model.inspect_voice_object_drafts()[0]
        model.reveal_voice_object_draft(task)
        copied = model.copy_voice_object_draft(task)
        cleared = model.clear_voice_object_draft_clipboard()
        replay = model.clear_voice_object_draft_clipboard()

        self.assertEqual(copied.state, "copied")
        self.assertEqual(cleared.state, "cleared")
        self.assertTrue(cleared.attempted)
        self.assertEqual(replay.state, "unavailable")
        self.assertEqual(calls, [
            ("issue_clear",), ("clear", "clear_nonce_123456789")])
        self.assertEqual(task.state, "queued")
        self.assertNotIn(secret, repr(cleared))
        self.assertNotIn(secret, repr(model.state))

    def test_task_copy_clear_changed_and_failure_are_closed(self):
        def make_model(clear_action):
            return WhisperFaceViewModel(GUIActions(
                status_snapshot=lambda: {},
                inspect_voice_object_drafts=lambda: ({
                    "item_id": "voice-object:task-1",
                    "sequence": 9,
                    "destination": "task",
                    "state": "queued",
                },),
                reveal_voice_object_draft=lambda _item_id: {
                    "sequence": 9,
                    "destination": "task",
                    "state": "queued",
                    "content": "Private draft",
                },
                issue_voice_object_copy_nonce=lambda: "copy_nonce_1234567890",
                copy_voice_object_draft=lambda *_args:
                    SnapshotTests.voice_draft_copy_receipt(),
                issue_voice_object_clear_clipboard_nonce=lambda:
                    "clear_nonce_123456789",
                clear_voice_object_draft_clipboard=clear_action,
            ))

        changed = make_model(lambda _nonce:
                             SnapshotTests.voice_draft_clear_receipt(
                                 state="changed", attempted=False))
        changed_draft = changed.inspect_voice_object_drafts()[0]
        changed.reveal_voice_object_draft(changed_draft)
        changed.copy_voice_object_draft(changed_draft)
        changed_receipt = changed.clear_voice_object_draft_clipboard()

        failed = make_model(lambda _nonce: (_ for _ in ()).throw(
            RuntimeError("clipboard unavailable")))
        failed_draft = failed.inspect_voice_object_drafts()[0]
        failed.reveal_voice_object_draft(failed_draft)
        failed.copy_voice_object_draft(failed_draft)
        failed_receipt = failed.clear_voice_object_draft_clipboard()

        self.assertEqual(changed_receipt.state, "changed")
        self.assertFalse(changed_receipt.attempted)
        self.assertEqual(changed_draft.state, "queued")
        self.assertEqual(failed_receipt.state, "unavailable")
        self.assertEqual(failed.state.notice_level, "error")
        self.assertNotIn("clipboard unavailable", failed.state.notice)

    def test_voice_draft_copy_is_task_calendar_queued_only(self):
        calls = []
        for destination, state, expected_calls in (
                ("calendar_draft", "queued", 2),
                ("email_draft", "queued", 0),
                ("task", "acknowledged", 0)):
            with self.subTest(destination=destination, state=state):
                calls.clear()
                model = WhisperFaceViewModel(GUIActions(
                    status_snapshot=lambda: {},
                    inspect_voice_object_drafts=lambda: ({
                        "item_id": "voice-object:draft-1",
                        "sequence": 10,
                        "destination": destination,
                        "state": state,
                    },),
                    reveal_voice_object_draft=lambda _item_id: {
                        "sequence": 10,
                        "destination": destination,
                        "state": state,
                        "content": "Private draft",
                    },
                    issue_voice_object_copy_nonce=lambda:
                        calls.append("nonce") or "n" * 32,
                    copy_voice_object_draft=lambda *_args:
                        calls.append("copy") or
                        SnapshotTests.voice_draft_copy_receipt(),
                ))
                draft = model.inspect_voice_object_drafts()[0]
                model.reveal_voice_object_draft(draft)
                receipt = model.copy_voice_object_draft(draft)
                self.assertEqual(len(calls), expected_calls)
                self.assertEqual(
                    receipt.state,
                    "copied" if expected_calls else "unavailable")

    def test_voice_draft_copy_copy_and_receipt_are_localized_and_explicit(self):
        inbox_copy = localized_string("settings.dialog.voice_objects.message")
        confirm_copy = localized_string(
            "settings.dialog.voice_objects.copy.message", sequence=9)
        receipt_copy = localized_string(
            "settings.dialog.voice_objects.copy.receipt",
            state="copied", attempted="yes")

        self.assertIn("separate confirmation", inbox_copy)
        self.assertIn("freshly rechecks", confirm_copy)
        for excluded in (
                "paste", "type", "schedule", "launch", "send",
                "acknowledge", "delete", "network"):
            self.assertIn(excluded, confirm_copy.casefold())
        self.assertIn("remains in Voice Inbox", receipt_copy)
        self.assertIn(
            "after confirmation",
            localized_string("settings.accessibility.voice_objects.copy"))
        clear_receipt = localized_string(
            "settings.dialog.voice_objects.clear.receipt",
            state="changed", attempted="no")
        self.assertIn("never read or retained clipboard content", clear_receipt)
        self.assertIn("remains in Voice Inbox", clear_receipt)
        self.assertIn(
            "only if", localized_string(
                "settings.accessibility.voice_objects.clear_clipboard"))
        self.assertFalse(any(
            "clear" in equivalent
            for equivalent in native_appkit_smoke_contract().key_equivalents))

    def test_demonstration_content_is_lazy_redacted_and_actions_stay_inert(self):
        self.model.refresh()
        self.assertEqual(self.demonstration_inspections, 0)
        self.assertEqual(self.demonstration_reveals, 0)
        self.assertNotIn("Private demonstration subject", repr(self.model.state))

        drafts = self.model.inspect_demonstration_drafts()
        self.assertEqual(self.demonstration_inspections, 1)
        self.assertEqual(self.demonstration_reveals, 0)
        self.assertEqual(drafts[0].domain, "mail")
        self.assertNotIn("demo-" + "1" * 32, repr(drafts[0]))
        with self.assertRaisesRegex(ValueError, "record"):
            self.model.record_demonstration_step(
                drafts[0], action="set_subject", text="Not revealed")
        self.assertEqual(self.model.state.notice_level, "error")
        self.assertIn("Could not record", self.model.state.notice)

        revealed = self.model.reveal_demonstration_draft(drafts[0])
        self.assertEqual(self.demonstration_reveals, 1)
        self.assertEqual(
            revealed.steps[0].text, "Private demonstration subject")
        self.assertNotIn("Private demonstration subject", repr(revealed))
        self.assertNotIn("Private demonstration subject", repr(revealed.steps[0]))
        self.assertNotIn("Private demonstration subject", repr(self.model.state))

        with self.assertRaisesRegex(ValueError, "record"):
            self.model.record_demonstration_step(
                drafts[0], action="set_subject", text="")
        self.assertEqual(self.model.state.notice_level, "error")
        self.assertIn("Could not record", self.model.state.notice)

        self.model.record_demonstration_step(
            drafts[0], action="set_body", text="Manually described only")
        self.assertIn((
            "record_demonstration", "demo-" + "1" * 32,
            "set_body", "Manually described only"), self.calls)
        self.model.approve_demonstration_draft(drafts[0])
        self.assertIn(
            ("approve_demonstration", "demo-" + "1" * 32), self.calls)

        drafts = self.model.inspect_demonstration_drafts()
        self.model.cancel_demonstration_draft(drafts[0])
        self.assertIn(
            ("cancel_demonstration", "demo-" + "1" * 32), self.calls)
        drafts = self.model.inspect_demonstration_drafts()
        with self.assertRaisesRegex(ValueError, "delete"):
            self.model.delete_approved_demonstration_draft(drafts[0])
        self.model.delete_approved_demonstration_draft(drafts[1])
        self.assertIn(
            ("delete_approved_demonstration", "demo-" + "4" * 32),
            self.calls)

    def test_demonstration_runtime_allocates_id_and_view_model_rejects_forgery(self):
        from whisper_face_gui import DemonstrationDraftMetadata

        created = self.model.create_demonstration_draft("notes")
        self.assertEqual(created.domain, "notes")
        self.assertEqual(created.draft_id, "demo-" + "2" * 32)
        forged = DemonstrationDraftMetadata(
            "demo-" + "3" * 32, 9, "finder", "recording", 0)
        with self.assertRaisesRegex(ValueError, "reveal"):
            self.model.reveal_demonstration_draft(forged)
        forged_approved = DemonstrationDraftMetadata(
            "demo-" + "5" * 32, 10, "finder", "approved", 1)
        with self.assertRaisesRegex(ValueError, "delete"):
            self.model.delete_approved_demonstration_draft(forged_approved)
        with self.assertRaisesRegex(ValueError, "create"):
            self.model.create_demonstration_draft("calendar")

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
        with self.assertRaisesRegex(
                ValueError, "no evidence for that pronunciation keyword"):
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
        self.assertEqual(self.calls[-1], ("face", FACES[-1]))
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

    def test_pausing_and_resuming_leave_unchecked_copies_calm(self):
        # The pause toggle works out the phase without waiting for the
        # runtime. An outbox of unchecked copies must not flash the
        # recovery phase on resume until the next refresh corrects it.
        self.model.state = replace(
            self.model.state, service_status="Running",
            outbox_count=5, outbox_unchecked=5)
        paused = self.model.set_paused(True)
        self.assertEqual(paused.status_phase, "paused")
        resumed = self.model.set_paused(False)
        self.assertEqual(resumed.status_phase, "ready")
        self.assertEqual(
            resumed.status_title,
            localized_string("overview.status.ready.title"))
        self.assertEqual(
            (resumed.outbox_count, resumed.outbox_unchecked), (5, 5))
        # One that still needs a home is the recovery phase at once, and
        # for that one dictation, not for all six.
        self.model.state = replace(
            self.model.state, outbox_count=6, outbox_unchecked=5)
        self.model.set_paused(True)
        mixed = self.model.set_paused(False)
        self.assertEqual(mixed.status_phase, "recovery")
        self.assertEqual(
            mixed.status_detail,
            localized_string("overview.status.recovery.detail.one"))

    def test_copy_and_dismiss_takes_what_needs_a_home_first(self):
        # The runtime copies an entry that needs a home before any copy of
        # an unchecked paste, and the window's own count follows it until
        # the next refresh.
        self.model.state = replace(
            self.model.state, outbox_count=4, outbox_unchecked=3)
        state = self.model.copy_latest_outbox()
        self.assertEqual((state.outbox_count, state.outbox_unchecked), (3, 3))
        state = self.model.copy_latest_outbox()
        self.assertEqual((state.outbox_count, state.outbox_unchecked), (2, 2))
        self.model.copy_latest_outbox()
        state = self.model.copy_latest_outbox()
        self.assertEqual((state.outbox_count, state.outbox_unchecked), (0, 0))
        state = self.model.copy_latest_outbox()
        self.assertEqual((state.outbox_count, state.outbox_unchecked), (0, 0))

    def test_support_snapshot_failure_becomes_user_visible_notice(self):
        def fail(_payload):
            raise RuntimeError("clipboard unavailable")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, copy_support_snapshot=fail))

        state = model.copy_support_snapshot()

        self.assertEqual(state.notice_level, "error")
        self.assertIn("clipboard unavailable", state.notice)

    def test_support_bundle_export_is_local_save_panel_only(self):
        source = (ROOT / "whisper_face_gui.py").read_text(encoding="utf-8")
        start = source.index("def exportSupportBundle_")
        end = source.index("def openSource_", start)
        action = source[start:end]
        self.assertIn("NSSavePanel.savePanel()", action)
        self.assertIn("write_support_bundle(", action)
        self.assertIn("support_snapshot_text(self.view_model.state)", action)
        for forbidden in (
                "self.actions", "copy_support_snapshot", "open_log",
                "copy_latest_outbox", "subprocess", "NSPasteboard"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, action)
        for key in (
                "diagnostics.action.export_support",
                "diagnostics.action.export_support.help",
                "diagnostics.notice.support_bundle.saved",
                "operation.support_bundle.export_failed"):
            self.assertIn(key, STRING_CATALOGS["en"])

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
        with self.assertRaisesRegex(
                ValueError, "no section called Billing"):
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

    def test_a_rejected_save_stays_on_screen_through_status_refreshes(self):
        now = [100.0]
        model = WhisperFaceViewModel(
            GUIActions(status_snapshot=lambda: {"capture_state": "Ready"}),
            clock=lambda: now[0])
        rejected = model.save_snippet("x" * 81, "Kind regards")
        self.assertEqual(rejected.notice_level, "error")
        self.assertNotIn("..", rejected.notice)

        for _tick in range(5):
            now[0] += 2.0
            state = model.refresh()

        self.assertEqual(state.notice, rejected.notice)
        self.assertEqual(state.notice_level, "error")
        self.assertEqual(model.select_section("Home").notice, "")

    def test_a_reviewed_issue_notice_clears_once_the_issue_recovers(self):
        now = [100.0]
        runtime = {"service_status": "Running",
                   "microphone_status": "Unavailable",
                   "accessibility_status": "Granted",
                   "input_monitoring_status": "Granted",
                   "capture_state": "Ready"}
        model = WhisperFaceViewModel(
            GUIActions(status_snapshot=lambda: dict(runtime)),
            clock=lambda: now[0])
        reviewed = model.show_issue()
        self.assertEqual(reviewed.notice_level, "error")
        self.assertTrue(reviewed.notice)

        now[0] += 2.0
        self.assertEqual(model.refresh().notice, reviewed.notice)

        runtime["microphone_status"] = "Ready"
        now[0] += 2.0
        state = model.refresh()

        self.assertEqual(state.degraded_issues, ())
        self.assertEqual(state.notice, "")

    def test_a_success_notice_outlives_a_refresh_then_expires(self):
        now = [100.0]
        model = WhisperFaceViewModel(
            GUIActions(status_snapshot=lambda: {},
                       save_snippet=lambda *_args: None),
            clock=lambda: now[0])
        saved = model.save_snippet("signature", "Kind regards").notice
        self.assertTrue(saved)

        now[0] += 2.0
        self.assertEqual(model.refresh().notice, saved)
        now[0] += 8.0
        self.assertEqual(model.refresh().notice, "")

    def test_the_same_success_posted_again_gets_its_own_lifetime(self):
        now = [100.0]
        model = WhisperFaceViewModel(
            GUIActions(status_snapshot=lambda: {},
                       save_snippet=lambda *_args: None),
            clock=lambda: now[0])
        first = model.save_snippet("signature", "Kind regards").notice
        now[0] += 6.0
        second = model.save_snippet("signature", "Kind regards").notice
        self.assertEqual(second, first)

        now[0] += 6.0
        self.assertEqual(model.refresh().notice, second)
        now[0] += 2.0
        self.assertEqual(model.refresh().notice, "")

    def test_refreshes_and_side_updates_never_extend_a_notice(self):
        now = [100.0]
        model = WhisperFaceViewModel(
            GUIActions(status_snapshot=lambda: {},
                       save_snippet=lambda *_args: None),
            clock=lambda: now[0])
        saved = model.save_snippet("signature", "Kind regards").notice
        for _tick in range(3):
            now[0] += 2.0
            self.assertEqual(model.refresh().notice, saved)
        model.set_verification("Checks passed")
        model.acknowledge_onboarding()
        now[0] += 2.0
        self.assertEqual(model.refresh().notice, "")

    def test_a_status_error_clears_once_status_reads_again(self):
        failing = [True]

        def status():
            if failing[0]:
                raise RuntimeError("service restarting")
            return {}

        model = WhisperFaceViewModel(GUIActions(status_snapshot=status))
        self.assertIn("service restarting", model.state.notice)
        failing[0] = False
        self.assertEqual(model.refresh().notice, "")

    def test_facade_creation_does_not_show_a_window(self):
        gui = create_gui(self.actions)
        self.assertIs(gui.view_model.actions, self.actions)
        self.assertIsNone(gui._controller)

    def test_feed_level_is_a_noop_before_any_window_exists(self):
        # The HUD tick calls this at 30fps; with no window it must stay a
        # cheap guard, never allocate a controller, and never raise.
        gui = create_gui(self.actions)
        gui.feed_level(0.5, "recording")
        self.assertIsNone(gui._controller)

    def test_feed_level_swallows_controller_errors(self):
        gui = create_gui(self.actions)

        class ExplodingController:
            def feed_level(self, level, mode):
                raise RuntimeError("boom")

        gui._controller = ExplodingController()
        gui.feed_level(0.2, "processing")  # must not raise

    def test_voice_inbox_facade_delegates_to_the_native_metadata_entry(self):
        calls = []

        class FakeController:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithViewModel_(self, view_model):
                calls.append(("init", view_model))
                return self

            def show_voice_inbox(self):
                calls.append(("show_voice_inbox",))

        gui = create_gui(self.actions)
        with patch.object(gui_module, "APPKIT_AVAILABLE", True), patch.object(
                gui_module, "WhisperFaceWindowController", FakeController,
                create=True):
            gui.show_voice_inbox()

        self.assertIs(calls[0][1], gui.view_model)
        self.assertEqual(calls[1:], [("show_voice_inbox",)])
        self.assertIsInstance(gui._controller, FakeController)

    def test_results_facade_delegates_to_the_native_inspector(self):
        calls = []

        class FakeController:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithViewModel_(self, view_model):
                calls.append(("init", view_model))
                return self

            def show_results(self):
                calls.append(("show_results",))

        gui = create_gui(self.actions)
        with patch.object(gui_module, "APPKIT_AVAILABLE", True), patch.object(
                gui_module, "WhisperFaceWindowController", FakeController,
                create=True):
            gui.show_results()

        self.assertIs(calls[0][1], gui.view_model)
        self.assertEqual(calls[1:], [("show_results",)])
        self.assertIsInstance(gui._controller, FakeController)

    def test_outbox_facade_delegates_without_recovery_action(self):
        calls = []

        class FakeController:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithViewModel_(self, view_model):
                calls.append(("init", view_model))
                return self

            def show_outbox(self):
                calls.append(("show_outbox",))

        gui = create_gui(self.actions)
        with patch.object(gui_module, "APPKIT_AVAILABLE", True), patch.object(
                gui_module, "WhisperFaceWindowController", FakeController,
                create=True):
            gui.show_outbox()

        self.assertIs(calls[0][1], gui.view_model)
        self.assertEqual(calls[1:], [("show_outbox",)])
        self.assertIsInstance(gui._controller, FakeController)

    def test_onboarding_and_degraded_guidance_routes_without_blocking(self):
        runtime = {
            "service_status": "Running",
            "microphone_status": "Unavailable",
            "accessibility_status": "Needs attention",
            "models": [PREPARING_MODEL],
        }
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: runtime))
        state = model.show_next_onboarding_step()
        self.assertEqual(state.section, "Advanced")
        self.assertEqual(state.notice_level, "info")
        self.assertIn("Microphone so I can hear you", state.notice)

        state = model.show_issue()
        self.assertEqual(state.section, "Advanced")
        self.assertEqual(state.notice_level, "error")
        self.assertIn("Microphone", state.notice)

    def test_permission_recovery_opens_only_while_permission_evidence_is_incomplete(self):
        runtime = {
            "service_status": "Running",
            "microphone_status": "Unavailable",
            "accessibility_status": "Needs attention",
        }
        opened: list[str] = []
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: dict(runtime),
            open_system_settings=opened.append))

        self.assertTrue(model.permission_recovery_needed())
        state = model.open_system_settings()
        self.assertEqual(opened, ["microphone"])
        self.assertEqual(state.notice_level, "info")
        self.assertIn("Return here", state.notice)

        runtime.update(
            microphone_status="Ready", accessibility_status="Granted",
            input_monitoring_status="Granted")
        model.refresh()
        self.assertFalse(model.permission_recovery_needed())
        model.open_system_settings()
        self.assertEqual(opened, ["microphone"])

    def test_missing_input_monitoring_is_named_and_opens_its_own_pane(self):
        opened: list[str] = []
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {
                "service_status": "Running",
                "microphone_status": "Ready",
                "accessibility_status": "Granted",
                "input_monitoring_status": "Needs attention",
            },
            open_system_settings=opened.append))

        self.assertEqual(
            [issue.key for issue in model.state.degraded_issues],
            ["input_monitoring"])
        self.assertIn("Input Monitoring", model.state.degraded_issues[0].title)
        self.assertNotIn(
            "Accessibility", model.state.degraded_issues[0].detail)
        model.open_system_settings()
        self.assertEqual(opened, ["input_monitoring"])

    def test_microphone_permission_reads_exact_runtime_states(self):
        granted = {
            "service_status": "Running",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
        }
        failed = normalize_snapshot({
            **granted, "microphone_status": "Unavailable"})
        self.assertFalse(failed.onboarding_steps[0].complete)
        self.assertEqual(failed.onboarding_steps[0].status, "Needs attention")
        self.assertEqual(failed.missing_permissions, ("microphone",))

        starting = normalize_snapshot({
            **granted, "microphone_status": "Starting"})
        self.assertFalse(starting.onboarding_steps[0].complete)
        self.assertEqual(starting.onboarding_steps[0].status, "Checking")
        self.assertEqual(starting.missing_permissions, ())
        self.assertEqual(starting.degraded_issues, ())

        ready = normalize_snapshot({**granted, "microphone_status": "Ready"})
        self.assertTrue(ready.onboarding_steps[0].complete)

    def test_permission_recovery_keeps_failure_local_and_content_free(self):
        def fail_to_open(_pane: str) -> None:
            raise RuntimeError("settings unavailable")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {
                "microphone_status": "Unavailable",
                "accessibility_status": "Needs attention",
            },
            open_system_settings=fail_to_open))
        state = model.open_system_settings()
        self.assertEqual(state.notice_level, "error")
        self.assertEqual(
            state.notice,
            localized_string("operation.system_settings.open_failed",
                             error="settings unavailable"))

    def test_completed_onboarding_acknowledgement_survives_refresh(self):
        self.assertFalse(self.model.state.onboarding_acknowledged)
        self.model.acknowledge_onboarding()
        self.assertTrue(self.model.refresh().onboarding_acknowledged)


class AccessoryActivationOrderTests(unittest.TestCase):
    """An accessory app must activate before it orders a window front.

    Ordering front while the process is still inactive puts the window in a
    background list, so the user has to click "Open Whisper Face" twice. The
    order is invisible to a headless render, so it is pinned as source.
    """

    def _show_source(self):
        source = Path(__file__).resolve().parents[1] / "whisper_face_gui.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "show":
                text = ast.unparse(node)
                if "makeKeyAndOrderFront_" in text:
                    return text
        self.fail("window show() not found")

    def test_activation_precedes_ordering_the_window_front(self):
        text = self._show_source()
        activate = text.index("activateIgnoringOtherApps_")
        order = text.index("makeKeyAndOrderFront_")
        self.assertLess(
            activate, order,
            "activateIgnoringOtherApps_ must run before "
            "makeKeyAndOrderFront_ or the first click is swallowed")

    def test_show_orders_front_regardless_as_a_fallback(self):
        self.assertIn("orderFrontRegardless", self._show_source())


class ConfirmationKeyTests(unittest.TestCase):
    """Escape cancels a confirmation; Return never reaches a destructive one.

    The objects stand in for NSAlert, its NSButtons and a key-down NSEvent,
    answering the same selectors the window calls on the real ones.
    """

    ESCAPE, RETURN, PERIOD, COMMAND = 53, 36, 47, 1 << 20

    class Button:
        def __init__(self, title, key, destructive=False):
            self.title, self.key, self.destructive = title, key, destructive
            self.clicks = 0

        def keyEquivalent(self):
            return self.key

        def hasDestructiveAction(self):
            return self.destructive

        def performClick_(self, _sender):
            self.clicks += 1

    class Alert:
        def __init__(self, *buttons):
            self._buttons = buttons
            self._window = object()

        def buttons(self):
            return self._buttons

        def window(self):
            return self._window

    class Event:
        def __init__(self, window, key_code, characters, flags=0):
            self._window, self._key_code = window, key_code
            self._characters, self._flags = characters, flags

        def window(self):
            return self._window

        def keyCode(self):
            return self._key_code

        def charactersIgnoringModifiers(self):
            return self._characters

        def modifierFlags(self):
            return self._flags

    def destructive_confirmation(self):
        delete = self.Button("Delete", "", destructive=True)
        cancel = self.Button("Cancel", "\r")
        return self.Alert(delete, cancel), delete, cancel

    def test_return_and_escape_both_route_to_cancel_when_it_destroys(self):
        alert, _delete, _cancel = self.destructive_confirmation()
        self.assertEqual(gui_module.alert_key_routes(alert), (1, 1))

    def test_escape_clicks_cancel_and_never_the_destructive_button(self):
        alert, delete, cancel = self.destructive_confirmation()
        handler = gui_module.alert_escape_handler(alert)
        self.assertIsNotNone(handler)
        escape = self.Event(alert.window(), self.ESCAPE, "\x1b")
        self.assertIsNone(handler(escape))
        command_period = self.Event(
            alert.window(), self.PERIOD, ".", self.COMMAND)
        self.assertIsNone(handler(command_period))
        self.assertEqual((delete.clicks, cancel.clicks), (0, 2))

    def test_other_keys_and_other_windows_pass_through_untouched(self):
        alert, delete, cancel = self.destructive_confirmation()
        handler = gui_module.alert_escape_handler(alert)
        for event in (
                self.Event(alert.window(), self.RETURN, "\r"),
                self.Event(alert.window(), self.PERIOD, "."),
                self.Event(object(), self.ESCAPE, "\x1b")):
            with self.subTest(key=event.keyCode()):
                self.assertIs(handler(event), event)
        self.assertEqual((delete.clicks, cancel.clicks), (0, 0))

    def test_a_confirmation_that_destroys_nothing_keeps_appkit_keys(self):
        # Return confirms, and Escape is Cancel's own key equivalent, so
        # AppKit answers both and no monitor is needed.
        alert = self.Alert(
            self.Button("Compose", "\r"), self.Button("Cancel", "\x1b"))
        self.assertEqual(gui_module.alert_key_routes(alert), (0, 1))
        self.assertIsNone(gui_module.alert_escape_handler(alert))

    def test_escape_is_not_invented_for_a_dialog_without_a_safe_button(self):
        alert = self.Alert(
            self.Button("Review", "\r"), self.Button("Done", ""))
        self.assertEqual(gui_module.alert_key_routes(alert), (0, None))
        self.assertIsNone(gui_module.alert_escape_handler(alert))


class BoundKeyCopyTests(unittest.TestCase):
    """A1: no surface may claim a key that is not the one bound."""

    def test_no_catalog_string_hardcodes_a_key_name(self):
        for locale, catalog in STRING_CATALOGS.items():
            for key, value in catalog.items():
                if key == "settings.hotkey.default":
                    continue        # the one honest default, used as a value
                with self.subTest(locale=locale, key=key):
                    self.assertNotIn("Right Option", value)
                    self.assertNotIn("Right Alt", value)

    def test_every_mode_shortcut_is_written_from_the_bound_key(self):
        for mode in MODE_GUIDE:
            with self.subTest(mode=mode):
                rendered = localized_string(
                    f"settings.mode.{mode}.shortcut", hotkey="F13")
                self.assertIn("F13", rendered)
                self.assertNotIn("{hotkey}", rendered)

    def test_snapshot_carries_the_runtime_key_into_every_surface(self):
        state = normalize_snapshot({
            "hotkey": "f13",
            "hotkey_label": "F13",
            "hotkey_shared_modes": [],
            "capture_state": "Ready",
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted",
            "models": [READY_MODEL],
        })
        self.assertEqual(state.hotkey_label, "F13")
        self.assertIn("F13", state.status_detail)
        self.assertIn(
            "F13",
            localized_string("settings.personalize.modes.detail",
                             hotkey=state.hotkey_label))
        practice = next(
            step for step in state.onboarding_steps if step.key == "hotkey")
        self.assertIn("F13", practice.detail)

    def test_a_missing_runtime_label_falls_back_to_the_shipped_default(self):
        state = normalize_snapshot({})
        self.assertEqual(state.hotkey_label, "Right Option")


class HotkeyPickerCopyTests(unittest.TestCase):
    """A1: the picker states its guard and its mode consequences."""

    def test_only_modifier_and_function_keys_are_capturable(self):
        self.assertEqual(hotkey_name_for_keycode(61), "alt_r")
        self.assertEqual(hotkey_name_for_keycode(105), "f13")
        # A, 1, space, return: ordinary typing keys are never a shortcut.
        for keycode in (0, 18, 49, 36, -1, None, "alt_r", 4.5):
            with self.subTest(keycode=keycode):
                self.assertEqual(hotkey_name_for_keycode(keycode), "")

    def test_every_capturable_keycode_is_a_modifier_or_function_key(self):
        for name in HOTKEY_KEYCODES.values():
            with self.subTest(name=name):
                self.assertTrue(
                    name.startswith(("alt_", "cmd_", "ctrl_", "shift_"))
                    or (name.startswith("f") and name[1:].isdigit()))

    def test_row_detail_names_the_bound_key(self):
        state = normalize_snapshot(
            {"hotkey": "f13", "hotkey_label": "F13"})
        self.assertEqual(hotkey_row_detail(state), "Hold F13 to dictate")

    def test_row_detail_says_which_modes_now_need_the_other_key(self):
        state = normalize_snapshot({
            "hotkey": "ctrl_r",
            "hotkey_label": "Right Control",
            "hotkey_shared_modes": ["reply", "command", "code"],
        })
        detail = hotkey_row_detail(state)
        self.assertIn("Right Control", detail)
        self.assertIn("Reply, Command, Code", detail)
        self.assertIn("Control key", detail)

    def test_unknown_modes_from_the_runtime_are_dropped(self):
        state = normalize_snapshot({
            "hotkey": "ctrl_r",
            "hotkey_label": "Right Control",
            "hotkey_shared_modes": ["reply", "nonsense", 7],
        })
        self.assertEqual(state.hotkey_shared_modes, ("reply",))

    def test_dialog_note_is_empty_for_a_key_that_shares_nothing(self):
        self.assertEqual(hotkey_shared_mode_note(("compose",), "alt_r"), "")
        self.assertEqual(hotkey_shared_mode_note((), "shift_r"), "")
        self.assertIn(
            "Shift", hotkey_shared_mode_note(("compose", "code"), "shift_r"))


class RecentDictationRowTests(unittest.TestCase):
    """A3: a browser row is metadata, and survives a malformed entry."""

    def test_rows_render_from_metadata_only(self):
        self.assertEqual(
            recent_dictation_row({
                "age_seconds": 90.0, "words": 4, "app": "Mail"}),
            "1 min ago · 4 words · Mail")
        self.assertEqual(
            recent_dictation_row({
                "age_seconds": 5.0, "words": 1, "app": "Terminal"}),
            "just now · 1 word · Terminal")
        self.assertEqual(
            recent_dictation_row({
                "age_seconds": 7200.0, "words": 12, "app": ""}),
            "2 hr ago · 12 words · unknown app")
        self.assertEqual(
            recent_dictation_row({
                "age_seconds": 172800.0, "words": 3, "app": "Notes"}),
            "2d ago · 3 words · Notes")

    def test_a_malformed_entry_never_raises(self):
        for entry in ({}, {"words": "many", "age_seconds": "ages"},
                      {"words": -4, "age_seconds": float("nan")},
                      {"words": True, "app": 7}):
            with self.subTest(entry=entry):
                self.assertTrue(recent_dictation_row(entry))


class ControlSettingsViewModelTests(unittest.TestCase):
    """A1/A2/A3/D1: the window drives the runtime and reports its verdicts."""

    def setUp(self):
        self.calls = []
        self.runtime = {
            "hotkey": "alt_r", "hotkey_label": "Right Option",
            "hotkey_shared_modes": [], "undo_hotkey": "",
            "undo_hotkey_label": "", "sounds": "system",
            "recent_dictations": False, "undo_available": False,
        }

    def model(self, **overrides):
        actions = GUIActions(
            status_snapshot=lambda: dict(self.runtime),
            **overrides)
        return WhisperFaceViewModel(actions)

    def test_a_refused_key_changes_nothing_and_says_so(self):
        model = self.model(set_hotkey=lambda name: {
            "accepted": False, "reason": "unsupported_key",
            "name": "", "label": "", "shared_modes": ()})
        model.choose_hotkey("a")
        self.assertEqual(model.state.hotkey, "alt_r")
        self.assertEqual(model.state.hotkey_label, "Right Option")
        self.assertEqual(model.state.notice_level, "error")

    def test_an_accepted_key_is_applied_with_its_shared_modes(self):
        def set_hotkey(name):
            self.calls.append(name)
            self.runtime.update(
                hotkey=name, hotkey_label="Right Shift",
                hotkey_shared_modes=["compose", "code"])
            return {"accepted": True, "reason": "ok", "name": name,
                    "label": "Right Shift",
                    "shared_modes": ("compose", "code")}

        model = self.model(set_hotkey=set_hotkey)
        model.choose_hotkey("shift_r")
        self.assertEqual(self.calls, ["shift_r"])
        self.assertEqual(model.state.hotkey_label, "Right Shift")
        self.assertEqual(
            model.state.hotkey_shared_modes, ("compose", "code"))

    def test_clearing_the_undo_key_is_reported_as_cleared(self):
        model = self.model(set_undo_hotkey=lambda name: {
            "accepted": True, "reason": "unbound", "name": "",
            "label": "", "shared_modes": ()})
        model.choose_undo_hotkey("")
        self.assertEqual(model.state.undo_hotkey_label, "")
        self.assertIn("cleared", model.state.notice)

    def test_sound_themes_are_validated_before_they_reach_the_runtime(self):
        model = self.model(
            set_sound_theme=lambda name: self.calls.append(name) or name)
        for theme in SOUND_THEMES:
            model.choose_sound_theme(theme)
        self.assertEqual(self.calls, list(SOUND_THEMES))
        with self.assertRaises(ValueError):
            model.choose_sound_theme("airhorn")

    def test_the_language_list_matches_the_runtime_exactly(self):
        # Two tables, one contract: the picker offers what dictate.py can
        # actually decode, in the same order, so a code can never reach the
        # runtime that the runtime would silently fold back to English.
        tree = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))
        runtime = None
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if {t.id for t in node.targets
                    if isinstance(t, ast.Name)} == {"LANGUAGES"}:
                runtime = tuple(
                    row.elts[0].value for row in node.value.elts)
        self.assertIsNotNone(runtime, "dictate.py LANGUAGES not found")
        self.assertEqual(tuple(LANGUAGES), runtime)
        self.assertEqual(LANGUAGES[0], "en")
        for code in LANGUAGES:
            self.assertIn(f"settings.language.{code}", STRING_CATALOGS["en"])

    def test_languages_are_validated_before_they_reach_the_runtime(self):
        def set_language(code):
            self.calls.append(code)
            self.runtime["language"] = code
            return code

        model = self.model(set_language=set_language)
        for code in LANGUAGES:
            model.choose_language(code)
        self.assertEqual(self.calls, list(LANGUAGES))
        self.assertEqual(model.state.language, LANGUAGES[-1])
        with self.assertRaises(ValueError):
            model.choose_language("klingon")

    def test_the_runtimes_answer_wins_over_the_requested_language(self):
        # The runtime normalizes and is authoritative; a rejected code must
        # not leave the picker showing something that is not in force.
        model = self.model(set_language=lambda _code: "en")
        model.choose_language("ja")
        self.assertEqual(model.state.language, "en")

    def test_an_unsupported_stored_language_falls_back_to_english(self):
        self.runtime["language"] = "klingon"
        self.assertEqual(normalize_snapshot(self.runtime).language, "en")
        self.runtime["language"] = "ja"
        self.assertEqual(normalize_snapshot(self.runtime).language, "ja")

    def test_recent_dictations_stay_empty_while_the_setting_is_off(self):
        model = self.model(
            recent_dictations=lambda: ({"id": "a", "words": 3},))
        self.assertEqual(model.recent_dictations(), ())
        self.runtime["recent_dictations"] = True
        model.refresh()
        self.assertEqual(len(model.recent_dictations()), 1)

    def test_the_actually_correction_is_off_unless_settings_say_on(self):
        self.assertFalse(GUIActions().set_self_correction(True))
        self.assertFalse(normalize_settings({}).self_correction)
        self.assertFalse(
            normalize_settings({"self_correction": "yes"}).self_correction)
        self.assertTrue(
            normalize_settings({"self_correction": True}).self_correction)

    def test_the_actually_correction_switch_shows_what_the_runtime_stored(
            self):
        stored = {"self_correction": False}

        def set_self_correction(enabled):
            self.calls.append(enabled)
            stored["self_correction"] = enabled
            return enabled

        model = self.model(
            settings_snapshot=lambda: dict(stored),
            set_self_correction=set_self_correction)
        self.assertFalse(model.select_section("Settings").settings
                         .self_correction)
        state = model.set_self_correction(True)
        self.assertEqual(self.calls, [True])
        self.assertTrue(state.settings.self_correction)
        self.assertTrue(model.refresh().settings.self_correction)
        self.assertFalse(model.set_self_correction(False)
                         .settings.self_correction)

    def test_a_refused_actually_correction_leaves_the_switch_off(self):
        model = self.model(set_self_correction=lambda _enabled: False)
        self.assertFalse(
            model.set_self_correction(True).settings.self_correction)

    def test_the_repeat_repair_is_on_unless_settings_say_off(self):
        # A runtime from before the switch, and every value that is not the
        # JSON literal false, reads as on.
        self.assertTrue(GUIActions().set_remove_repeats(True))
        self.assertTrue(normalize_settings({}).remove_repeats)
        self.assertTrue(
            normalize_settings({"remove_repeats": "no"}).remove_repeats)
        self.assertFalse(
            normalize_settings({"remove_repeats": False}).remove_repeats)

    def test_the_repeat_repair_switch_shows_what_the_runtime_stored(self):
        stored = {"remove_repeats": True}

        def set_remove_repeats(enabled):
            self.calls.append(enabled)
            stored["remove_repeats"] = enabled
            return enabled

        model = self.model(
            settings_snapshot=lambda: dict(stored),
            set_remove_repeats=set_remove_repeats)
        self.assertTrue(model.select_section("Settings").settings
                        .remove_repeats)
        state = model.set_remove_repeats(False)
        self.assertEqual(self.calls, [False])
        self.assertFalse(state.settings.remove_repeats)
        self.assertFalse(model.refresh().settings.remove_repeats)
        self.assertTrue(model.set_remove_repeats(True)
                        .settings.remove_repeats)

    def test_a_refused_repeat_repair_change_shows_what_was_stored(self):
        model = self.model(set_remove_repeats=lambda _enabled: True)
        self.assertTrue(
            model.set_remove_repeats(False).settings.remove_repeats)

    def test_a_raising_repeat_repair_switch_reports_and_stays_on(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError("private detail")

        model = self.model(set_remove_repeats=explode)
        state = model.set_remove_repeats(False)
        self.assertTrue(state.settings.remove_repeats)
        self.assertEqual(state.notice_level, "error")
        self.assertIn("Repeated words", state.notice)

    def test_the_new_speech_runtime_is_off_unless_the_runtime_says_on(self):
        self.assertFalse(GUIActions().set_new_speech_runtime(True))
        self.assertFalse(normalize_snapshot({}).new_speech_runtime)
        self.assertFalse(normalize_snapshot(
            {"new_speech_runtime": "yes"}).new_speech_runtime)
        self.assertTrue(normalize_snapshot(
            {"new_speech_runtime": True}).new_speech_runtime)
        # Only the two words the window has copy for survive.
        for last, expected in (("new", "new"), ("previous", "previous"),
                               ("", ""), ("fluidaudio", ""), (None, ""),
                               ("parrot-speech-runtime-1", "")):
            with self.subTest(last=last):
                self.assertEqual(normalize_snapshot(
                    {"new_speech_runtime_last": last})
                    .new_speech_runtime_last, expected)
        # Still getting ready only when the runtime says True.
        for preparing, expected in ((True, True), (False, False),
                                    ("true", False), (1, False), (None, False)):
            with self.subTest(preparing=preparing):
                self.assertIs(normalize_snapshot(
                    {"new_speech_runtime_preparing": preparing})
                    .new_speech_runtime_preparing, expected)
        self.assertFalse(normalize_snapshot({}).new_speech_runtime_preparing)

    def test_the_new_speech_runtime_switch_shows_what_the_runtime_stored(
            self):
        self.runtime["new_speech_runtime"] = False

        def set_new_speech_runtime(enabled):
            self.calls.append(enabled)
            self.runtime["new_speech_runtime"] = enabled
            return enabled

        model = self.model(set_new_speech_runtime=set_new_speech_runtime)
        self.assertFalse(model.refresh().new_speech_runtime)
        state = model.set_new_speech_runtime(True)
        self.assertEqual(self.calls, [True])
        self.assertTrue(state.new_speech_runtime)
        self.assertTrue(model.refresh().new_speech_runtime)
        self.assertFalse(model.set_new_speech_runtime(False)
                         .new_speech_runtime)

    def test_a_pinned_new_speech_runtime_shows_what_actually_runs(self):
        # PARROT_ASR_RUNTIME set: the runtime answers with what runs, and
        # the switch shows that rather than the click.
        self.runtime["new_speech_runtime"] = False
        model = self.model(set_new_speech_runtime=lambda _enabled: False)
        self.assertFalse(
            model.set_new_speech_runtime(True).new_speech_runtime)

    def test_a_raising_new_speech_runtime_switch_reports_and_stays_off(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError("private detail")

        model = self.model(set_new_speech_runtime=explode)
        state = model.set_new_speech_runtime(True)
        self.assertFalse(state.new_speech_runtime)
        self.assertEqual(state.notice_level, "error")
        self.assertIn("New speech runtime", state.notice)

    def test_the_new_speech_runtime_caption_is_plain_words(self):
        from whisper_face_gui import speech_runtime_caption
        on = localized_string("models.speech_runtime.on")
        off = localized_string("models.speech_runtime.off")
        self.assertEqual(speech_runtime_caption(True, ""), on)
        self.assertEqual(speech_runtime_caption(False, ""), off)
        self.assertEqual(speech_runtime_caption(True, "unknown"), on)
        self.assertTrue(speech_runtime_caption(True, "new").startswith(on))
        self.assertIn("new\u00a0one", speech_runtime_caption(True, "new"))
        self.assertIn("previous\u00a0one",
                      speech_runtime_caption(False, "previous"))
        # Switched on and still loading, it says so, after what the switch
        # does, in place of which program answered last; switched off the
        # loading state is never shown.
        preparing = localized_string(
            "models.speech_runtime.preparing", caption=on)
        for last in ("", "new", "previous"):
            with self.subTest(preparing_last=last):
                self.assertEqual(
                    speech_runtime_caption(True, last, preparing=True),
                    preparing)
                self.assertEqual(
                    speech_runtime_caption(False, last, preparing=True),
                    speech_runtime_caption(False, last))
        self.assertTrue(preparing.startswith(on + " "))
        self.assertIn("getting ready", preparing)
        self.assertIn("previous one", preparing)
        # What it does, in plain words: its own code, and it goes back by
        # itself. No program, package or switch name is ever shown.
        for text in (on, off, localized_string("models.speech_runtime.help")):
            with self.subTest(text=text):
                self.assertIn("its own code", text)
                self.assertIn("goes back to the previous one by itself",
                              text)
        visible = [
            localized_string(key) for key in (
                "models.speech_runtime.label", "models.speech_runtime.on",
                "models.speech_runtime.off", "models.speech_runtime.help",
                "models.accessibility.speech_runtime")]
        visible.append(localized_string(
            "operation.speech_runtime.update_failed", error="busy"))
        visible += [speech_runtime_caption(enabled, last,
                                           preparing=preparing)
                    for enabled in (True, False)
                    for last in ("new", "previous")
                    for preparing in (False, True)]
        for text in visible:
            for word in ("FluidAudio", "fluidaudio", "parrot", "Parakeet",
                         "PARROT_ASR_RUNTIME", "protocol", "helper",
                         "restart", "own runtime"):
                with self.subTest(text=text, word=word):
                    self.assertNotIn(word, text)

    def test_a_runtime_that_raises_never_takes_the_window_down(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError("private detail")

        model = self.model(
            set_hotkey=explode, set_undo_hotkey=explode,
            set_sound_theme=explode, set_recent_dictations=explode,
            recent_dictations=explode, reveal_recent_dictation=explode,
            insert_recent_dictation=explode, undo_last_dictation=explode,
            preview_sound=explode, describe_hotkey=explode,
            set_self_correction=explode)
        model.choose_hotkey("f13")
        model.choose_undo_hotkey("f14")
        model.choose_sound_theme("silent")
        model.set_recent_dictations(True)
        self.assertEqual(model.recent_dictations(), ())
        state = model.set_self_correction(True)
        self.assertFalse(state.settings.self_correction)
        self.assertEqual(state.notice_level, "error")
        self.assertNotIn("Flight Recorder", state.notice)
        self.assertEqual(model.reveal_recent_dictation("a"), "")
        model.insert_recent_dictation("a")
        model.undo_last_dictation()
        self.assertFalse(model.preview_sound())
        self.assertFalse(model.describe_hotkey("f13")["accepted"])

    def test_undo_reports_state_and_reason_and_never_any_text(self):
        model = self.model(undo_last_dictation=lambda: {
            "undone": False, "reason": "focus_drift"})
        model.undo_last_dictation()
        self.assertEqual(model.state.notice_level, "error")
        self.assertIn("focus_drift", model.state.notice)

        model = self.model(undo_last_dictation=lambda: {
            "undone": True, "reason": "commit_verified"})
        model.undo_last_dictation()
        self.assertEqual(model.state.notice_level, "success")
        self.assertEqual(model.state.notice, "Last dictation undone.")

    def test_revealing_a_recent_dictation_requires_the_runtime_to_supply_it(
            self):
        model = self.model(reveal_recent_dictation=lambda _entry: None)
        self.assertEqual(model.reveal_recent_dictation("a"), "")


GUI_SOURCE = (ROOT / "whisper_face_gui.py").read_text(encoding="utf-8")
GUI_TREE = ast.parse(GUI_SOURCE)


def _gui_class(name):
    for node in ast.walk(GUI_TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"whisper_face_gui.py no longer defines {name}")


def _gui_function(name, inside=None):
    for node in ast.walk(inside or GUI_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"whisper_face_gui.py no longer defines {name}")


def _method_names(node):
    return {item.name for item in node.body
            if isinstance(item, ast.FunctionDef)}


def _called_names(node):
    """Bare names and ``self.`` methods a piece of code calls, in order."""
    calls = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        target = item.func
        if isinstance(target, ast.Name):
            calls.append((item.lineno, item.col_offset, target.id))
        elif (isinstance(target, ast.Attribute)
              and isinstance(target.value, ast.Name)
              and target.value.id == "self"):
            calls.append((item.lineno, item.col_offset, target.attr))
    return [name for _line, _column, name in sorted(calls)]


def _gui_constants(*names):
    """Evaluate simple module-level constants that sit behind the AppKit
    guard, so the layout contract is checked where AppKit is absent."""
    values = {}
    for node in ast.walk(GUI_TREE):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in names:
            values[target.id] = eval(  # noqa: S307 - our own constants
                compile(ast.Expression(node.value), "<gui>", "eval"),
                {}, dict(values))
    missing = set(names) - set(values)
    if missing:
        raise AssertionError(f"missing layout constants: {sorted(missing)}")
    return values


class BrandGeometryTests(unittest.TestCase):
    """The pure geometry every brand component draws from."""

    def setUp(self):
        import whisper_face_theme as theme
        self.theme = theme

    def test_hard_shadow_keeps_body_and_shadow_inside_the_footprint(self):
        theme = self.theme
        for name, shape in theme.BRAND_SHAPES.items():
            with self.subTest(shape=name):
                width, height = theme.hard_shadow_footprint(120.0, 40.0, shape)
                self.assertEqual(
                    (width, height),
                    (120.0 + shape.shadow_right, 40.0 + shape.shadow_down))
                body, shadow = theme.hard_shadow_frames(width, height, shape)
                self.assertEqual(body, (0.0, 0.0, 120.0, 40.0))
                self.assertEqual(
                    shadow,
                    (shape.shadow_right, shape.shadow_down, 120.0, 40.0))
                for x, y, w, h in (body, shadow):
                    self.assertGreaterEqual(x, 0.0)
                    self.assertGreaterEqual(y, 0.0)
                    self.assertLessEqual(x + w, width)
                    self.assertLessEqual(y + h, height)

    def test_pressing_moves_the_body_onto_its_shadow_and_leaves_a_sliver(
            self):
        theme = self.theme
        for name in ("chunky_button", "chunky_button_small", "face_chip"):
            shape = theme.BRAND_SHAPES[name]
            width, height = theme.hard_shadow_footprint(90.0, 36.0, shape)
            rest, shadow = theme.hard_shadow_frames(width, height, shape)
            pressed, pressed_shadow = theme.hard_shadow_frames(
                width, height, shape, pressed=True)
            with self.subTest(shape=name):
                self.assertEqual(shadow, pressed_shadow)
                self.assertEqual(rest[2:], pressed[2:])
                self.assertEqual(
                    (pressed[0], pressed[1]),
                    (shape.shadow_right - 1.0, shape.shadow_down - 1.0))
                # Still inside the footprint: pressing never draws outside.
                self.assertLessEqual(pressed[0] + pressed[2], width)
                self.assertLessEqual(pressed[1] + pressed[3], height)
        # An object without a shadow has nowhere to travel.
        group = theme.BRAND_SHAPES["group"]
        self.assertEqual(
            theme.hard_shadow_frames(50.0, 20.0, group, pressed=True)[0],
            (0.0, 0.0, 50.0, 20.0))

    def test_a_tilted_tile_fits_the_upright_box_it_is_given(self):
        theme = self.theme
        for tilt in theme.STICKER_TILTS:
            with self.subTest(tilt=tilt):
                width, height = theme.tilted_inner_size(200.0, 76.0, tilt)
                self.assertLess(width, 200.0)
                self.assertLess(height, 76.0)
                outer = theme.tilted_footprint(width, height, tilt)
                self.assertAlmostEqual(outer[0], 200.0, places=6)
                self.assertAlmostEqual(outer[1], 76.0, places=6)
        self.assertEqual(theme.tilted_inner_size(200.0, 76.0, 0.0),
                         (200.0, 76.0))
        # A box too flat to hold the turned tile yields nothing, not a
        # negative size.
        self.assertEqual(theme.tilted_inner_size(200.0, 1.0, 20.0)[1], 0.0)

    def test_stage_puts_the_face_left_and_the_content_beside_it(self):
        theme = self.theme
        shape = theme.BRAND_SHAPES["stage"]
        # The widest and the narrowest the page column can be.
        for column in (720.0, 632.0):
            with self.subTest(column=column):
                height = theme.STAGE_HEIGHT + shape.shadow_down
                face, content = theme.stage_layout(column, height)
                body_width = column - shape.shadow_right
                self.assertEqual(face[2:], (200.0, 200.0))
                self.assertEqual(face[0], theme.STAGE_PADDING_LEFT)
                # Centred on the body, not on the footprint.
                self.assertAlmostEqual(
                    face[1] + face[3] / 2.0, theme.STAGE_HEIGHT / 2.0)
                self.assertEqual(
                    content[0], face[0] + face[2] + theme.STAGE_GAP)
                self.assertAlmostEqual(
                    content[0] + content[2],
                    body_width - theme.STAGE_PADDING_RIGHT)
                self.assertGreaterEqual(
                    content[2], theme.STAGE_CONTENT_MIN_WIDTH)
                self.assertGreaterEqual(content[1], theme.STAGE_PADDING_Y)
                self.assertLessEqual(
                    content[1] + content[3],
                    theme.STAGE_HEIGHT - theme.STAGE_PADDING_Y)

    def test_a_cramped_stage_shrinks_the_face_but_never_loses_it(self):
        theme = self.theme
        face, content = theme.stage_layout(330.0, 252.0)
        self.assertLess(face[2], 200.0)
        self.assertGreaterEqual(face[2], theme.STAGE_FACE_MIN_SIZE)
        self.assertGreaterEqual(content[2], theme.STAGE_CONTENT_MIN_WIDTH)
        face, content = theme.stage_layout(150.0, 252.0)
        self.assertEqual(face[2], theme.STAGE_FACE_MIN_SIZE)
        self.assertGreaterEqual(content[2], 0.0)
        # A face smaller than the floor is honoured as asked.
        self.assertEqual(
            theme.stage_layout(150.0, 252.0, face_size=44.0)[0][2], 44.0)


class NewLookFoundationContractTests(unittest.TestCase):
    """What the next lanes rely on, checked without AppKit."""

    COMPONENTS = {
        # Controls: drawn, pressable, focusable.
        "ChunkyButton": {
            "configure", "drawRect_", "intrinsicContentSize", "is_pressed",
            "drawFocusRingMask", "focusRingMaskBounds",
            "viewDidChangeEffectiveAppearance"},
        "FaceChipButton": {
            "configure", "set_selected", "drawRect_", "is_pressed",
            "drawFocusRingMask", "focusRingMaskBounds",
            "viewDidChangeEffectiveAppearance"},
        # Not controls: drawn and described, never focused.
        "KeyCapView": {
            "set_key", "drawRect_", "intrinsicContentSize",
            "viewDidChangeEffectiveAppearance"},
        "StickerTileView": {
            "configure", "drawRect_", "intrinsicContentSize",
            "viewDidChangeEffectiveAppearance"},
        "StagePanelView": {
            "set_face", "set_face_view", "set_content_view", "arrange",
            "drawRect_", "viewDidChangeEffectiveAppearance"},
    }
    ROLES = {
        "ChunkyButton": None,  # NSButton's own button role
        "FaceChipButton": "NSAccessibilityRadioButtonRole",
        "KeyCapView": "NSAccessibilityStaticTextRole",
        "StickerTileView": "NSAccessibilityStaticTextRole",
        "StagePanelView": "NSAccessibilityGroupRole",
    }

    def test_every_brand_component_is_defined_with_its_states(self):
        shared = _gui_class("BrandButton")
        for name, methods in self.COMPONENTS.items():
            with self.subTest(component=name):
                node = _gui_class(name)
                defined = _method_names(node)
                if [base.id for base in node.bases] == ["BrandButton"]:
                    defined |= _method_names(shared)
                self.assertFalse(
                    methods - defined,
                    f"{name} lost {sorted(methods - defined)}")
                self.assertTrue(ast.get_docstring(node))
        for name in ("ChunkyButton", "FaceChipButton"):
            self.assertEqual(
                [base.id for base in _gui_class(name).bases],
                ["BrandButton"])
        # Both ride the shared press spring, so Reduce Motion gates them
        # exactly like every other jelly control.
        self.assertEqual(
            [base.id for base in shared.bases], ["JellyButton"])
        # VoiceOver reads a button's cell, so what is set on the button
        # must reach the cell.
        self.assertLessEqual(
            {"setAccessibilityLabel_", "setAccessibilityHelp_",
             "setAccessibilityRole_", "setAccessibilityValue_"},
            _method_names(shared))

    def test_every_brand_component_draws_through_the_one_shadow_helper(self):
        for name in (*self.COMPONENTS, "SpeechBubbleView"):
            with self.subTest(component=name):
                draw = _gui_function("drawRect_", _gui_class(name))
                called = _called_names(draw)
                if name == "KeyCapView":
                    # A cap is drawn by the one function the cap inside a
                    # line of copy is drawn by too.
                    self.assertIn("draw_key_cap", called)
                    called = _called_names(_gui_function("draw_key_cap"))
                self.assertIn("draw_hard_shadow_shape", called)
        helper = _gui_function("draw_hard_shadow_shape")
        self.assertIn("hard_shadow_frames", _called_names(helper))
        # The stage hands its circles to the helper and never strokes an
        # outline of its own: one object, one outline.
        stage = ast.unparse(
            _gui_function("drawRect_", _gui_class("StagePanelView")))
        self.assertIn("overlay=circles", stage)
        self.assertNotIn("stroke", stage)

    def test_every_brand_component_has_a_voiceover_role_and_label(self):
        for name, role in self.ROLES.items():
            with self.subTest(component=name):
                text = ast.unparse(_gui_class(name))
                if role is not None:
                    self.assertIn(f"setAccessibilityRole_({role})", text)
                self.assertTrue(
                    "_accessible(" in text
                    or "setAccessibilityLabel_(" in text
                    or name == "StagePanelView",
                    f"{name} sets no VoiceOver label")
        # The chip says whether it is the chosen one.
        self.assertIn(
            "setAccessibilityValue_",
            ast.unparse(_gui_function("set_selected",
                                      _gui_class("FaceChipButton"))))

    def test_brand_components_carry_no_colour_of_their_own(self):
        start = GUI_SOURCE.index("# Brand components of the new look.")
        end = GUI_SOURCE.index("class WhisperFaceWindowController(NSObject)")
        block = GUI_SOURCE[start:end]
        import re
        self.assertEqual(re.findall(r"#[0-9A-Fa-f]{6}\b", block), [])
        # Tokens are sRGB; the calibrated helper would lighten them.
        self.assertNotIn("_theme_color(", block)
        self.assertNotIn("colorWithCalibrated", block)

    def test_layout_contract_adds_up_at_the_window_minimum(self):
        values = _gui_constants(
            "SIDEBAR_WIDTH", "CONTENT_WIDTH", "WINDOW_MIN_SIZE",
            "TITLE_STRIP_HEIGHT", "CONTENT_INSET", "PAGE_COLUMN_MAX_WIDTH",
            "PAGE_COLUMN_MIN_WIDTH", "ROW_MIN_HEIGHT",
            "ROW_CAPTION_MIN_HEIGHT", "ROW_INSET", "SIDEBAR_ROW_HEIGHT",
            "SIDEBAR_FACE_SIZE", "GROUP_HEADER_GAP", "GROUP_GAP")
        # Sidebar, two insets and the narrowest column fill the minimum
        # window exactly, so no page is ever asked to be narrower.
        self.assertEqual(
            values["SIDEBAR_WIDTH"] + 2 * values["CONTENT_INSET"]
            + values["PAGE_COLUMN_MIN_WIDTH"],
            values["WINDOW_MIN_SIZE"][0])
        self.assertEqual(values["SIDEBAR_WIDTH"], 200.0)
        self.assertEqual(
            values["PAGE_COLUMN_MAX_WIDTH"], values["CONTENT_WIDTH"])
        self.assertLess(
            values["PAGE_COLUMN_MIN_WIDTH"], values["PAGE_COLUMN_MAX_WIDTH"])
        self.assertEqual(values["ROW_MIN_HEIGHT"], 44.0)
        self.assertEqual(values["ROW_CAPTION_MIN_HEIGHT"], 50.0)
        self.assertEqual(values["SIDEBAR_ROW_HEIGHT"], 32.0)
        self.assertEqual(values["SIDEBAR_FACE_SIZE"], 44.0)
        self.assertEqual(values["TITLE_STRIP_HEIGHT"], 52.0)

    def test_string_catalog_keeps_each_lanes_strings_under_its_marker(self):
        markers = (
            "# --- new look: shell",
            "# --- new look: pages",
            "# --- new look: home and setup",
            "# --- new look: editors",
        )
        lines = GUI_SOURCE.splitlines()
        positions = []
        for marker in markers:
            found = [index for index, line in enumerate(lines)
                     if line.strip() == marker]
            self.assertEqual(len(found), 1, marker)
            self.assertEqual(lines[found[0] + 1], "", marker)
            positions.append(found[0])
        self.assertEqual(positions, sorted(positions))
        # They close the ``en`` catalog: nothing but markers, blank lines
        # and whatever later lanes add sits between the first marker and
        # the end of the dictionary.
        closing = next(
            index for index in range(positions[0], len(lines))
            if lines[index] == "}")
        self.assertEqual(lines[closing + 1],
                         "SUPPORTED_LOCALES = tuple(STRING_CATALOGS)")
        self.assertEqual(SUPPORTED_LOCALES, ("en",))
        # Every lane has filled its place: each marker is followed by that
        # lane's own strings.
        self.assertTrue(any(
            line.strip().startswith('"shell.')
            for line in lines[positions[0]:positions[1]]))
        self.assertTrue(any(
            line.strip().startswith('"pages.')
            for line in lines[positions[1]:positions[2]]))
        self.assertTrue(any(
            line.strip().startswith(('"home.', '"onboarding.'))
            for line in lines[positions[2]:positions[3]]))
        # The editors' strings are all under theirs, and nowhere else.
        editors = [line.strip() for line in lines[positions[3]:closing]
                   if line.strip().startswith('"')]
        self.assertTrue(editors)
        self.assertTrue(all(
            line.startswith('"sheets.') for line in editors))
        self.assertEqual(
            len(editors),
            sum(key.startswith("sheets.") for key in STRING_CATALOGS["en"]))

    RENDER_ORDER = [
        "_render_shell", "_render_home", "_render_onboarding",
        "_render_settings_shell", "_render_settings_personalize",
        "_render_settings_controls", "_render_settings_privacy",
        "_render_advanced", "_render_notices", "_render_finish",
    ]

    def test_render_calls_one_method_per_surface_in_a_fixed_order(self):
        controller = _gui_class("WhisperFaceWindowController")
        render = _gui_function("render", controller)
        self.assertEqual(
            [name for name in _called_names(render)
             if name.startswith("_render")], self.RENDER_ORDER)
        self.assertLess(
            render.end_lineno - render.lineno, 30,
            "render() is growing back into one method for every surface")
        for name in self.RENDER_ORDER:
            _gui_function(name, controller)
        # The finish depends on what every surface enabled and hid.
        finish = _called_names(_gui_function("_render_finish", controller))
        self.assertEqual(
            [name for name in finish if name.startswith("_")],
            ["_sync_face_clock", "_configure_key_view_loop"])

    def test_each_settings_pane_has_its_own_builder(self):
        controller = _gui_class("WhisperFaceWindowController")
        build = _gui_function("_build_settings", controller)
        self.assertEqual(
            [name for name in _called_names(build)
             if name.startswith("_build_settings_")],
            ["_build_settings_personalize", "_build_settings_controls",
             "_build_settings_privacy"])
        self.assertEqual(SETTINGS_PANES, ("Personalize", "Controls",
                                          "Privacy"))


class SettingsPagesContractTests(unittest.TestCase):
    """The four settings pages, checked without AppKit."""

    PAGE_BUILDERS = (
        "_build_settings_personalize", "_build_settings_controls",
        "_build_settings_privacy", "_build_advanced")

    def test_each_page_is_a_grouped_scrolling_page_without_a_title(self):
        controller = _gui_class("WhisperFaceWindowController")
        for name in self.PAGE_BUILDERS + ("_build_settings",):
            with self.subTest(builder=name):
                called = _called_names(_gui_function(name, controller))
                if name != "_build_settings":
                    self.assertIn("_grouped_page", called)
                    self.assertIn("_section_header", called)
                    self.assertIn("_settings_group", called)
                    # Groups are drawn by the group view, never an NSBox
                    # card with its ambient shadow.
                    self.assertNotIn("_card", called)
                    self.assertNotIn("_pill", called)

    def test_what_the_new_look_replaced_is_gone(self):
        # The title strip names the page, so no page has a header of its
        # own; buttons are the system's, so nothing repaints them; groups
        # draw themselves, so nothing themes a card or a separator.
        names = {node.id for node in ast.walk(GUI_TREE)
                 if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(GUI_TREE)
            if isinstance(node, ast.Attribute)} | {
            node.name for node in ast.walk(GUI_TREE)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))} | {
            alias.asname or alias.name for node in ast.walk(GUI_TREE)
            if isinstance(node, ast.ImportFrom) for alias in node.names}
        for gone in ("_page_header", "_stack", "_restyle_secondary_buttons",
                     "_SECONDARY_BUTTONS", "_card", "_hairline", "_pill",
                     "_fit_pill", "_set_pill_tone", "_amber_text_color",
                     "_mirror_accessibility_to_cell", "_appkit_name",
                     "NSSegmentStyleRounded", "NSBox", "SURFACE_SPECS",
                     "CARD_RADIUS"):
            with self.subTest(name=gone):
                self.assertNotIn(gone, names)
        # Every name the module imports from a framework or the theme is
        # one it uses.
        imported = [
            (alias.asname or alias.name, node.lineno)
            for node in ast.walk(GUI_TREE)
            if isinstance(node, ast.ImportFrom)
            and node.module in ("AppKit", "Foundation", "Quartz",
                                "whisper_face_theme")
            for alias in node.names]
        used = {node.id for node in ast.walk(GUI_TREE)
                if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)}
        self.assertEqual(
            [name for name, _line in imported if name not in used], [])
        # No catalog string is left behind by a page that no longer
        # shows it.
        for key in ("settings.title", "settings.subtitle",
                    "models.relisten.status.ready",
                    "settings.personalize.keywords.detail",
                    "settings.accessibility.edit.help"):
            self.assertNotIn(key, STRING_CATALOGS["en"])

    def test_a_render_that_changes_nothing_does_no_work(self):
        # A page is laid out only when what a layout is worked out from
        # (its width, which views show, what they say) has changed.
        page = _gui_class("GroupedPageView")
        arrange = ast.unparse(_gui_function("arrange", page))
        self.assertIn(
            "if not force and self.layout_inputs() == self.arranged_for:",
            arrange)
        self.assertIn("self.arranged_for = self.layout_inputs()", arrange)
        inputs = ast.unparse(_gui_function("layout_inputs", page))
        for read in ("self.content_width()", "view.stringValue()",
                     "view.title()", "view.key", "view.isHidden()"):
            self.assertIn(read, inputs)
        # A group is marked for drawing only when its size or its
        # separators changed: the one call is inside that test.
        group = _gui_function("arrange", _gui_class("GroupBoxView"))
        marks = [
            node for node in ast.walk(group)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "setNeedsDisplay_"]
        guarded = [
            node for branch in ast.walk(group)
            if isinstance(branch, ast.If)
            and "self.drawn_shape" in ast.unparse(branch.test)
            for node in ast.walk(branch)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "setNeedsDisplay_"]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks, guarded)
        self.assertIn(
            "shape = (float(width), top, tuple(separators))",
            ast.unparse(group))
        # The 2-second timer renders only when the state, the appearance
        # or Increase Contrast changed what a render would write.
        controller = _gui_class("WhisperFaceWindowController")
        tick = ast.unparse(_gui_function("refreshTimer_", controller))
        self.assertIn(
            "    if self._what_a_render_reads() != self._rendered_from:\n"
            "        self.render()", tick)
        reads = ast.unparse(
            _gui_function("_what_a_render_reads", controller))
        for read in ("self.view_model.state", "_uses_dark_appearance",
                     "_increase_contrast()"):
            self.assertIn(read, reads)
        render = ast.unparse(_gui_function("render", controller))
        self.assertIn(
            "self._rendered_from = self._what_a_render_reads()", render)
        # Every other render (an action, a window being shown) still
        # renders: only the timer asks first.
        callers = [
            node.name for node in ast.walk(controller)
            if isinstance(node, ast.FunctionDef)
            and "_what_a_render_reads" in _called_names(node)]
        self.assertEqual(sorted(callers), ["refreshTimer_", "render"])

    def test_buttons_are_system_push_buttons_read_through_their_cells(self):
        button = _gui_function("_button")
        text = ast.unparse(button)
        self.assertIn("PushButton.alloc()", text)
        self.assertIn("setBezelStyle_(NSBezelStyleRounded)", text)
        # The system draws the bezel: no layer paint, no fixed corners.
        for banned in ("setCornerRadius_", "setBordered_(False)", "layer()"):
            self.assertNotIn(banned, text)
        forwarded = _method_names(_gui_class("PushButton"))
        self.assertLessEqual(
            {"setAccessibilityLabel_", "setAccessibilityHelp_",
             "setAccessibilityValue_"}, forwarded)

    def test_the_face_picker_is_a_grid_of_face_chips(self):
        picker = _gui_class("ArtFacePicker")
        populate = ast.unparse(_gui_function("populate", picker))
        self.assertIn("FaceChipButton.alloc()", populate)
        self.assertLessEqual(
            {"setSelectedSegment_", "selectedSegment", "chipPicked_",
             "height_for_width", "arrange"}, _method_names(picker))
        self.assertIn(
            "chip.set_selected(",
            ast.unparse(_gui_function("_paint_selection", picker)))

    ROW_BUTTON_LABELS = {
        "pages.personalize.tones.action": "Edit app tones",
        "pages.personalize.snippets.action": "Edit snippets",
        "pages.personalize.vocabulary.action": "Edit vocabulary",
        "pages.personalize.corrections.action": "Review learned corrections",
        "pages.personalize.keywords.action":
            "Inspect pronunciation keywords",
        "pages.controls.hotkey.action": "Change capture key",
        "pages.controls.undo.action": "Change undo key",
        "pages.controls.modes.action": "View voice modes",
        "pages.controls.sound.action": "Preview feedback sounds",
        "pages.privacy.recent.action": "Browse recent dictations",
        "pages.privacy.voice_objects.action": "Inspect Voice Inbox",
        "pages.support.licenses.action": "Open license notices",
        "pages.support.source.action": "Open the exact source",
        "home.replay.clear.action": "Clear retained spans",
    }

    def test_voiceover_copy_reaches_the_cell_a_control_is_read_through(self):
        from whisper_face_gui import voiceover_elements

        class Element:
            label = help = value = None

            def setAccessibilityLabel_(self, value):
                self.label = value

            def setAccessibilityHelp_(self, value):
                self.help = value

            def setAccessibilityValue_(self, value):
                self.value = value

        class Field(Element):
            """A control drawn by a cell: VoiceOver reads the cell."""

            def __init__(self):
                self.its_cell = Element()

            def cell(self):
                return self.its_cell

            def setStringValue_(self, value):
                self.visual = value

        class Switch(Element):
            """A control with no cell is its own element."""

            def cell(self):
                return None

        field = Field()
        self.assertEqual(voiceover_elements(field), (field, field.cell()))
        self.assertEqual(voiceover_elements(Element())[1:], ())
        switch = Switch()
        self.assertEqual(voiceover_elements(switch), (switch,))

        gui_module._accessible(field, "Whisper Tiny status", "How it stands")
        sync_accessibility(field, "Ready")
        for element in (field, field.cell()):
            self.assertEqual(
                (element.label, element.help, element.value),
                ("Whisper Tiny status", "How it stands", "Ready"))
        # No help given: none set, and the label still lands on both.
        other = Field()
        gui_module._accessible(other, "Model name")
        self.assertEqual(
            (other.cell().label, other.cell().help), ("Model name", None))
        sync_accessibility(field, "Checking", label="Parakeet status")
        set_accessible_text(other, "Parakeet", label="Model name")
        self.assertEqual(
            (field.cell().label, field.cell().value),
            ("Parakeet status", "Checking"))
        self.assertEqual(
            (other.visual, other.cell().label, other.cell().value),
            ("Parakeet", "Model name", "Parakeet"))
        # It is done once, in the helpers: nothing mirrors page by page.
        names = {node.name for node in ast.walk(GUI_TREE)
                 if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("_mirror_accessibility_to_cell", names)
        for helper in ("_accessible", "sync_accessibility"):
            self.assertIn(
                "voiceover_elements", _called_names(_gui_function(helper)))
        self.assertIn("sync_accessibility", _called_names(
            _gui_function("set_accessible_text")))

    def test_a_text_is_labelled_only_by_what_it_does_not_say(self):
        # A label that only repeated the text would be spoken as well as
        # the text, now that labels reach the cell.
        label = ast.unparse(_gui_function("_label"))
        self.assertNotIn("accessibility_label or text", label)
        self.assertIn(
            "_accessible(label, accessibility_label) "
            "if accessibility_label else label", label)

    def test_a_row_button_says_what_it_does_to_what(self):
        catalog = STRING_CATALOGS["en"]
        contract = native_appkit_smoke_contract().accessibility_catalog_keys
        controller = ast.unparse(_gui_class("WhisperFaceWindowController"))
        for key, words in self.ROW_BUTTON_LABELS.items():
            with self.subTest(key=key):
                self.assertEqual(catalog[key], words)
                self.assertIn(key, contract)
                self.assertGreaterEqual(len(words.split()), 2)
                if key.startswith("pages.personalize."):
                    # Built in a loop over the rows.
                    self.assertIn(
                        "label=self._l(f'pages.personalize.{key}.action')",
                        controller)
                else:
                    self.assertIn(f"'{key}'", controller)
        self.assertEqual(
            len(set(self.ROW_BUTTON_LABELS.values())),
            len(self.ROW_BUTTON_LABELS))
        # The visible titles are unchanged, three of them still Edit.
        self.assertEqual(
            [catalog[key] for key in (
                "settings.action.edit", "settings.action.review",
                "settings.action.inspect", "settings.action.view",
                "settings.controls.action.change",
                "settings.controls.action.preview",
                "settings.privacy.recent.browse", "results.audio.clear")],
            ["Edit", "Review", "Inspect", "View", "Change…", "Preview",
             "Browse…", "Clear"])
        # A button reads its label, or its title when it has none, and a
        # symbol beside the title never names it.
        button = ast.unparse(_gui_function("_button"))
        self.assertIn("_accessible(button, label or title, help_text)",
                      button)
        self.assertIn("image.setAccessibilityDescription_('')", button)
        # A state's symbol is silent on the view and on its cell.
        self.assertIn("_silence_image(self.icon)", ast.unparse(
            _gui_function("initWithFrame_", _gui_class("StatusLabelView"))))
        silence = ast.unparse(_gui_function("_silence_image"))
        self.assertIn("view.cell()", silence)

    def test_the_face_grid_fills_its_group_eight_to_a_row(self):
        from whisper_face_gui import (
            FACE_CHIP_GAP, FACE_CHIP_MAX, FACE_CHIP_MIN, FACE_GRID_COLUMNS,
            face_grid)
        self.assertEqual(
            (FACE_GRID_COLUMNS, FACE_CHIP_GAP, FACE_CHIP_MAX), (8, 10.0, 78.0))
        values = _gui_constants(
            "CONTENT_WIDTH", "PAGE_COLUMN_MIN_WIDTH",
            "PAGE_COLUMN_MAX_WIDTH", "ROW_INSET")
        # The room the chips' bodies get: the group, less its two insets.
        for column in (values["PAGE_COLUMN_MIN_WIDTH"],
                       values["PAGE_COLUMN_MAX_WIDTH"]):
            width = column - 2 * values["ROW_INSET"]
            with self.subTest(column=column):
                columns, chip, margin, rows = face_grid(width, len(FACES))
                self.assertEqual((columns, rows), (8, 2))
                # As large as the width allows with the spec's gap, in
                # whole points; what is left over is under a point a chip
                # and is split evenly either side.
                self.assertEqual(chip, math.floor(
                    (width - 7 * FACE_CHIP_GAP) / 8))
                self.assertEqual(chip, int(chip))
                left_over = width - (8 * chip + 7 * FACE_CHIP_GAP)
                self.assertTrue(0.0 <= left_over < 8.0)
                self.assertEqual(margin, math.floor(left_over / 2.0))
                self.assertLessEqual(margin, 3.5)
        self.assertEqual(face_grid(720.0 - 28.0, 16)[1], 77.0)
        self.assertEqual(face_grid(632.0 - 28.0, 16)[1], 66.0)
        # Never larger than the cap; then the grid is centred.
        columns, chip, margin, rows = face_grid(1000.0, 16)
        self.assertEqual((columns, chip, rows), (8, FACE_CHIP_MAX, 2))
        self.assertEqual(margin, (1000.0 - (8 * 78.0 + 70.0)) / 2.0)
        # Too narrow for eight chips of the smallest size: fewer to a row,
        # not smaller chips.
        columns, chip, _margin, rows = face_grid(300.0, 16)
        self.assertLess(columns, 8)
        self.assertGreaterEqual(chip, FACE_CHIP_MIN)
        self.assertEqual(rows, -(-16 // columns))
        self.assertEqual(face_grid(500.0, 3)[0], 3)
        # The picker asks this function, and the line naming the chosen
        # face rides the section header's line, outside the group.
        picker = _gui_class("ArtFacePicker")
        self.assertIn("face_grid", _called_names(
            _gui_function("grid", picker)))
        build = ast.unparse(_gui_function(
            "_build_settings_personalize",
            _gui_class("WhisperFaceWindowController")))
        self.assertIn("hint = self._header_note()", build)
        self.assertIn("trailing=hint", build)
        self.assertNotIn("face_row.addSubview_(hint)", build)

    def test_sentences_that_wrap_do_not_end_on_one_word(self):
        # Each of these wrapped with its last word alone on a line, or with
        # a button's name split across two, at a width the window takes.
        catalog = STRING_CATALOGS["en"]
        # Every sentence the bubble speaks, on Home and in setup, and the
        # captions and notes that were seen to wrap badly.
        spoken = [
            key for key in catalog
            if (key.startswith("overview.status.") and ".detail" in key)
            or (key.startswith("onboarding.") and key.endswith(".detail")
                and key.count(".") == 2)]
        self.assertGreaterEqual(len(spoken), 12)
        for key in (
                *spoken,
                "settings.controls.self_correction.off",
                "settings.controls.self_correction.on",
                "settings.controls.remove_repeats.off",
                "settings.controls.remove_repeats.on",
                "pages.models.advisory",
                "pages.models.advisory.evidence",
                "pages.models.advisory.unavailable"):
            with self.subTest(key=key):
                last = catalog[key].rsplit(" ", 1)[-1]
                self.assertGreaterEqual(
                    len(last.split("\u00a0")), 2,
                    "the last two words stay on one line")
        name = catalog["overview.action.copy_outbox"]
        self.assertEqual(name, "Copy & Dismiss")
        held = name.replace(" ", "\u00a0")
        for key, text in catalog.items():
            if key != "overview.action.copy_outbox":
                with self.subTest(key=key):
                    self.assertNotIn(
                        name, text,
                        "a button's name inside a sentence stays on one line")
        for key in ("overview.status.recovery.detail.one",
                    "overview.status.recovery.detail.many",
                    "onboarding.first_dictation.detail"):
            self.assertIn(held, catalog[key])

    def test_the_model_checks_note_says_what_it_found(self):
        import re
        from whisper_face_gui import (
            INTERNAL_STATUS_WORDS, internal_words_in, model_checks_note)
        # The last words are held together by no-break spaces, so the
        # note never ends with one word alone on a line.
        information_only = (
            "for information only; they never change "
            "which\u00a0model\u00a0runs.")
        # The runtime's own sentence carries counts; the page keeps them.
        evidence = localized_string(
            "models.wallet.evidence", resolved=3, warm=2)
        self.assertEqual(
            model_checks_note(evidence),
            "Model files found on this Mac: 3 of 4 · warmed up: 2 of 4. "
            "These checks are " + information_only)
        for resolved in range(5):
            for warm in range(5):
                with self.subTest(resolved=resolved, warm=warm):
                    note = model_checks_note(localized_string(
                        "models.wallet.evidence", resolved=resolved,
                        warm=warm))
                    self.assertIn(
                        f"found on this Mac: {resolved} of 4 · "
                        f"warmed up: {warm} of 4.", note)
        # No receipt, or one that failed validation.
        unavailable = (
            "Model file checks are not available on this Mac yet. They are "
            + information_only)
        self.assertEqual(
            model_checks_note(localized_string("models.wallet.unavailable")),
            unavailable)
        self.assertEqual(
            model_checks_note(
                gui_module.GUIState().model_wallet_advisory), unavailable)
        self.assertEqual(
            model_checks_note(normalize_snapshot(
                {"model_wallet_shadow": {"mode": "anything"}}
            ).model_wallet_advisory), unavailable)
        # Anything else claims nothing.
        informational = "Model checks are " + information_only
        for other in (localized_string("models.wallet.informational"),
                      "", "Exact files resolved 9/4"):
            self.assertEqual(model_checks_note(other), informational)
        # Whatever it says, it says in plain words.
        for note in (model_checks_note(evidence), unavailable, informational):
            with self.subTest(note=note):
                self.assertTrue(note.endswith(information_only))
                self.assertFalse(internal_words_in(note))
                self.assertIsNone(re.search(
                    r"shadow|advisory|wallet|attested|\bpin", note,
                    re.IGNORECASE))
        self.assertIn("wallet", INTERNAL_STATUS_WORDS)
        # The runtime's value and its wording are untouched, and the page
        # goes through this function.
        self.assertIn("Exact files resolved 3/4", evidence)
        controller = _gui_class("WhisperFaceWindowController")
        self.assertIn("model_checks_note", _called_names(
            _gui_function("_render_advanced", controller)))
        self.assertNotIn(
            "models.accessibility.wallet", ast.unparse(controller))
        self.assertIn(
            "pages.models.advisory.label",
            native_appkit_smoke_contract().accessibility_catalog_keys)

    def test_model_states_are_shown_in_words_with_a_tone(self):
        from whisper_face_gui import ModelStatus, model_status_display
        models = normalize_snapshot({"models": [
            READY_MODEL, PREPARING_MODEL,
            {**READY_MODEL, "install_state": "not_installed",
             "required": False, "warm": False},
            {**READY_MODEL, "install_state": "failed", "warm": False},
        ]}).models
        self.assertEqual(
            [model_status_display(model) for model in models],
            [("Ready", "good"), ("Checking", "neutral"),
             ("Optional", "neutral"), ("Needs repair", "warn")])
        # A row the runtime did not classify keeps none of its words.
        for word, expected in (("RESOLVED", ("Installed", "good")),
                               ("UNAVAILABLE", ("Not available", "warn")),
                               ("shadow-only", ("Unknown", "neutral"))):
            with self.subTest(word=word):
                model = ModelStatus("Whisper Tiny", role="Preview",
                                    status=word, detail="Exact pinned")
                self.assertEqual(model_status_display(model), expected)
                self.assertEqual(
                    gui_module.model_detail_display(model), "Preview")

    def test_no_state_phrase_uses_an_internal_word(self):
        from whisper_face_gui import (
            INTERNAL_STATUS_TOKENS, INTERNAL_STATUS_WORDS, internal_words_in,
            relisten_status_display, system_status_tone,
            voice_inbox_status_display)
        phrases = [
            relisten_status_display(status) for status in (
                "off", "ready", "warming", "enabled-not-ready",
                "evidence-required", "receipt-invalid", "anything")]
        phrases += [voice_inbox_status_display(status, count)
                    for status in ("Off", "Ready", "Unavailable", "odd")
                    for count in (0, 1, 3)]
        phrases += [value for key, value in STRING_CATALOGS["en"].items()
                    if key.startswith("pages.")]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(internal_words_in(phrase))
        # The receipt's status token counts as the runtime writes it; the
        # microphone's own plain word for the same thing does not, and a
        # Mac with no microphone shows it.
        self.assertEqual(INTERNAL_STATUS_TOKENS, ("UNAVAILABLE",))
        self.assertLessEqual(
            set(INTERNAL_STATUS_TOKENS), set(INTERNAL_STATUS_WORDS))
        self.assertEqual(internal_words_in("Unavailable"), [])
        self.assertEqual(internal_words_in("UNAVAILABLE"), ["UNAVAILABLE"])
        self.assertEqual(
            internal_words_in("Exact pinned files resolved"),
            ["RESOLVED", "pinned"])
        self.assertEqual(
            internal_words_in("Runtime readiness not attested"), ["attested"])
        self.assertEqual(internal_words_in("Ready"), [])
        # The reasons it cannot turn on all read the same way.
        self.assertEqual(relisten_status_display("evidence-required"),
                         relisten_status_display("receipt-invalid"))
        self.assertIn("1 draft waiting",
                      voice_inbox_status_display("Ready", 1))
        self.assertEqual(
            [system_status_tone(word) for word in (
                "Running", "Granted", "Unavailable", "Needs attention",
                "Starting")],
            ["good", "good", "warn", "warn", "neutral"])


class EditorSheetContractTests(unittest.TestCase):
    """The Personalize editors are sheets on the window, not alerts.

    The native smoke test builds each one and measures it. These hold the
    shape of the code where AppKit is absent: which actions are sheets,
    what a sheet is made of, and what its words are.
    """

    EDITORS = {
        "editTone_": "_tones_page",
        "editSnippets_": "_snippets_page",
        "editVocabulary_": "_vocabulary_page",
        "reviewCorrections_": "_corrections_page",
        "inspectKeywords_": "_keywords_page",
    }

    @staticmethod
    def _names(node):
        return {item.id for item in ast.walk(node)
                if isinstance(item, ast.Name)} | {
            item.attr for item in ast.walk(node)
            if isinstance(item, ast.Attribute)}

    def test_the_five_personalize_actions_no_longer_build_an_alert(self):
        controller = _gui_class("WhisperFaceWindowController")
        pieces = [*self.EDITORS, *self.EDITORS.values(), "_edit_snippet",
                  "_snippet_form", "_new_sheet", "_sheet_fill",
                  "_sheet_layout", "_run_sheet"]
        for name in pieces:
            with self.subTest(function=name):
                names = self._names(_gui_function(name, controller))
                self.assertFalse(
                    names & {"NSAlert", "runModal", "setAccessoryView_",
                             "addButtonWithTitle_", "setMessageText_"},
                    f"{name} builds a system alert")
        for action, page in self.EDITORS.items():
            with self.subTest(action=action):
                node = _gui_function(action, controller)
                called = _called_names(node)
                # Open one sheet, fill it with the editor's page, run it,
                # and take it down however the action ends.
                self.assertEqual(called.count("_new_sheet"), 1)
                self.assertIn("_sheet_fill", called)
                self.assertIn(page, called)
                self.assertIn("_run_sheet", called)
                closing = [
                    item for item in ast.walk(node)
                    if isinstance(item, ast.Try) and item.finalbody
                    and "_end_sheet" in _called_names(
                        ast.Module(body=item.finalbody, type_ignores=[]))]
                self.assertEqual(len(closing), 1)
        # One editor at a time: a second is refused before it is built.
        opening = ast.unparse(_gui_function("_new_sheet", controller))
        self.assertIn(
            "if self._sheet is not None:\n        raise RuntimeError(",
            opening)
        self.assertLess(
            opening.index("raise RuntimeError("),
            opening.index("EditorSheet()"))
        # The form is the open sheet filled again, not a second sheet.
        form = _called_names(_gui_function("_edit_snippet", controller))
        self.assertNotIn("_new_sheet", form)
        self.assertIn("_sheet_fill", form)
        # What used to build the old dialogs is gone.
        defined = {node.name for node in ast.walk(controller)
                   if isinstance(node, ast.FunctionDef)}
        self.assertFalse(
            defined & {"_tone_dialog_form", "toneDialogAppChanged_"})

    def test_every_dialog_runs_through_the_one_place_that_shows_the_face(
            self):
        controller = _gui_class("WhisperFaceWindowController")
        run = _gui_function("_run_modal", controller)
        called = _called_names(run)
        # A sheet runs itself; an alert is given the face, then run.
        self.assertIn("isinstance(dialog, EditorSheet)", ast.unparse(run))
        self.assertIn("_show_the_face", called)
        self.assertLess(
            ast.unparse(run).index("_show_the_face"),
            ast.unparse(run).index("runModal"))
        face = ast.unparse(_gui_function("_show_the_face", controller))
        self.assertIn("face_chip_icon", face)
        self.assertIn("setIcon_", face)
        for node in controller.body:
            if not isinstance(node, ast.FunctionDef) \
                    or node.name == "_run_modal":
                continue
            names = self._names(node)
            allowed = set()
            if node.name == "_show_the_face":
                allowed.add("setIcon_")
            if "NSSavePanel" in names:
                # The system's own save panel, not a dialog of ours.
                allowed.add("runModal")
            with self.subTest(function=node.name):
                # Nothing else runs a dialog, or picks its icon.
                self.assertFalse(
                    names & {"runModal", "runModalForWindow_", "setIcon_"}
                    - allowed)
                if "NSAlert" in names:
                    self.assertTrue(
                        {"_run_modal", "_confirm"}
                        & set(_called_names(node)),
                        f"{node.name} builds an alert it does not run "
                        "through _run_modal")
        # The dialogs that are still alerts, to be turned into sheets
        # next: this list is what the hand-off names.
        alerts = sorted(
            node.name for node in controller.body
            if isinstance(node, ast.FunctionDef)
            and "NSAlert" in self._names(node))
        self.assertEqual(alerts, [
            "_capture_hotkey", "_confirm", "browseRecentDictations_",
            "inspectResultEvidence_", "inspectVoiceObjects_", "viewModes_"])

    def test_a_sheet_is_made_of_the_pages_own_parts(self):
        controller = _gui_class("WhisperFaceWindowController")
        layout = _gui_function("_sheet_layout", controller)
        text = ast.unparse(layout)
        called = _called_names(layout)
        # The body is the pages' document in the pages' scroll view, its
        # sections their headers, groups and notes.
        self.assertIn("GroupedPageView.alloc()", text)
        self.assertIn("_section_header", called)
        self.assertIn("_settings_group", called)
        self.assertIn("add_section", text)
        self.assertIn(
            "PageScrollView.alloc()",
            ast.unparse(_gui_function("_new_sheet", controller)))
        # A row is the pages' row.
        self.assertEqual(
            _called_names(_gui_function("_sheet_row", controller)),
            ["_settings_row", "list"])
        for builder in ("_sheet_empty_row", "_sheet_editor_row"):
            self.assertIn(
                "SettingsRowView.alloc()",
                ast.unparse(_gui_function(builder, controller)))
        # The page lays itself out through arrange_section, and a sheet
        # changes only the room under its last group.
        page = _gui_class("GroupedPageView")
        self.assertIn(
            "arrange_section", _called_names(_gui_function("arrange", page)))
        self.assertIn("y += self.foot", ast.unparse(page))
        self.assertIn("document.foot = SHEET_BODY_FOOT", text)
        # No second group, row or header is defined for sheets.
        classes = {node.name for node in ast.walk(GUI_TREE)
                   if isinstance(node, ast.ClassDef)}
        self.assertEqual(
            {name for name in classes if name.startswith(("Sheet", "Editor"))
             or name == "FaceChipView"},
            {"EditorSheet", "FaceChipView", "SheetContentView",
             "SheetTextView", "SheetWindow"})
        # A sheet's labels are themed with the sheet, not the window.
        fill = ast.unparse(_gui_function("_sheet_fill", controller))
        self.assertIn("self._theme_sink = sheet.themed", fill)
        self.assertIn("finally:\n        self._theme_sink = None", fill)

    def test_a_sheet_fits_the_window_at_its_minimum(self):
        values = _gui_constants(
            "WINDOW_MIN_SIZE", "PAGE_TOP", "SIDEBAR_FACE_SIZE",
            "SHEET_WIDTH", "SHEET_INSET", "SHEET_FACE_SIZE",
            "SHEET_WINDOW_MARGIN", "SHEET_MAX_HEIGHT", "SHEET_BODY_FOOT",
            "SHEET_BUTTON_MIN_WIDTH", "FACE_CHIP_ART_INSET",
            "SHEET_FIRST_TAG")
        width, height = values["WINDOW_MIN_SIZE"]
        self.assertEqual(values["SHEET_WIDTH"], 540.0)
        self.assertEqual(values["SHEET_FACE_SIZE"], 40.0)
        self.assertGreaterEqual(values["SHEET_WINDOW_MARGIN"], 40.0)
        self.assertEqual(
            values["SHEET_MAX_HEIGHT"],
            height - 2 * values["SHEET_WINDOW_MARGIN"])
        self.assertLessEqual(
            values["SHEET_WIDTH"], width - 2 * values["SHEET_WINDOW_MARGIN"])
        # The page's own margin, not a number of the sheet's.
        self.assertEqual(values["SHEET_BODY_FOOT"], values["PAGE_TOP"])
        # The sidebar's chip, in proportion.
        self.assertEqual(
            values["FACE_CHIP_ART_INSET"] * values["SIDEBAR_FACE_SIZE"], 4.0)
        # A sheet's answers never look like an alert's.
        self.assertGreater(values["SHEET_FIRST_TAG"], 1003)

    def test_the_footer_has_a_default_and_an_escape(self):
        controller = _gui_class("WhisperFaceWindowController")
        footer = ast.unparse(_gui_function("_sheet_footer", controller))
        # Cancel then Save for a form, one Done for a list.
        self.assertLess(
            footer.index("'settings.action.cancel'"),
            footer.index("'settings.action.save'"))
        self.assertIn("'cancel', self._l('settings.action.cancel'), "
                      "key='\\x1b'", footer)
        self.assertIn("'save', self._l('settings.action.save'), key='\\r'",
                      footer)
        self.assertIn("'done', self._l('settings.action.done'), key='\\r'",
                      footer)
        # Escape reaches a sheet with no Cancel through its window, and a
        # multi-line field hands Escape and Tab to the sheet.
        sheet = _gui_class("EditorSheet")
        leave = ast.unparse(_gui_function("leave", sheet))
        self.assertIn("for name in ('cancel', 'done')", leave)
        window = ast.unparse(_gui_class("SheetWindow"))
        self.assertIn("def cancelOperation_", window)
        # Return starts a new line in a multi-line field, so
        # Command-Return presses the default button from anywhere.
        self.assertIn("def performKeyEquivalent_", window)
        self.assertIn("self.defaultButtonCell()", window)
        field = _gui_class("SheetTextView")
        self.assertEqual(
            _method_names(field),
            {"insertTab_", "insertBacktab_", "cancelOperation_"})
        text = ast.unparse(field)
        self.assertIn("selectNextKeyView_", text)
        self.assertIn("selectPreviousKeyView_", text)
        # Every button answers the sheet through the one action.
        button = ast.unparse(_gui_function("_sheet_button", controller))
        self.assertIn("'sheetChose:'", button)
        self.assertIn("sheet.register(name, button, item)", button)

    def test_a_sheet_run_is_modal_on_the_window_and_headless_by_a_responder(
            self):
        sheet = _gui_class("EditorSheet")
        run = ast.unparse(_gui_function("run", sheet))
        # With a responder nothing is shown and no loop is run.
        self.assertLess(
            run.index("if responder is not None"),
            run.index("beginSheet_completionHandler_"))
        self.assertLess(
            run.index("return self.tag"),
            run.index("beginSheet_completionHandler_"))
        self.assertIn(
            "NSApplication.sharedApplication().runModalForWindow_("
            "self.window)", run)
        self.assertIn("finally:\n        self.running = False", run)
        self.assertLess(
            run.index("self.running = True"),
            run.index("runModalForWindow_"))
        self.assertIn(
            "if self.parent is None and parent is not None and "
            "bool(parent.isVisible())", run)
        # A button stops the session the run is in, with its own answer.
        choose = ast.unparse(_gui_function("choose", sheet))
        self.assertIn(
            "if self.running:\n        "
            "NSApplication.sharedApplication().stopModalWithCode_(int(tag))",
            choose)
        # An open sheet is resized about its middle: where AppKit holds a
        # sheet, which since macOS 11 is the middle of its window.
        layout = ast.unparse(_gui_function(
            "_sheet_layout", _gui_class("WhisperFaceWindowController")))
        self.assertIn(
            "old.origin.y + (old.size.height - new.size.height) / 2.0",
            layout)
        self.assertIn("window.setTitle_(title)", layout)
        end = ast.unparse(_gui_function("end", sheet))
        self.assertIn("self.parent.endSheet_(self.window)", end)
        self.assertIn("orderOut_", end)
        # A press goes through the button's own target and action, and a
        # button that cannot be clicked cannot be pressed.
        press = ast.unparse(_gui_function("press", sheet))
        self.assertIn("sendAction_to_(button.action(), button.target())",
                      press)
        self.assertIn("raise ValueError", press)

    def test_the_editors_strings_are_plain_and_say_what_they_act_on(self):
        from whisper_face_gui import internal_words_in
        catalog = STRING_CATALOGS["en"]
        sheets = {key: value for key, value in catalog.items()
                  if key.startswith("sheets.")}
        self.assertGreaterEqual(len(sheets), 30)
        for key, value in sheets.items():
            with self.subTest(key=key):
                self.assertEqual(internal_words_in(value), [])
                self.assertEqual(value, value.strip())
        # A row's button says what it does to what.
        for key, names in {
                "sheets.tones.choice.label": {"app": "Mail"},
                "sheets.snippets.edit.label": {"name": "signature"},
                "sheets.snippets.delete.label": {"name": "signature"},
                "sheets.corrections.forget.label": {
                    "source": "Gwen", "target": "Qwen"},
                "sheets.keywords.forget.label": {"keyword": "Qwen"}}.items():
            with self.subTest(key=key):
                said = localized_string(key, **names)
                self.assertTrue(all(
                    value in said for value in names.values()))
                self.assertGreaterEqual(len(said.split()), 3)
                self.assertIn(
                    key,
                    native_appkit_smoke_contract().accessibility_catalog_keys)
        self.assertEqual(
            localized_string(
                "sheets.snippets.delete.label", name="signature"),
            "Delete snippet signature")
        # What only the old alerts showed is gone from the catalog.
        for key in (
                "settings.action.add", "settings.action.forget_mapping",
                "settings.dialog.tone.title", "settings.dialog.tone.message",
                "settings.dialog.tone.app.label",
                "settings.dialog.tone.app.help",
                "settings.dialog.tone.choice.label",
                "settings.dialog.snippet.chooser.label",
                "settings.dialog.snippet.chooser.help",
                "settings.dialog.vocabulary.message",
                "settings.dialog.correction.chooser.label",
                "settings.dialog.correction.chooser.help",
                "settings.dialog.correction.chooser.message",
                "settings.dialog.keywords.message",
                "settings.dialog.keywords.empty",
                "settings.dialog.keywords.chooser.label",
                "settings.dialog.keywords.chooser.help",
                "settings.dialog.keywords.row",
                "settings.dialog.keywords.status.eligible",
                "settings.dialog.keywords.status.gathering"):
            self.assertNotIn(key, catalog)
        # Every string the editors ask for is in the catalog.
        controller = _gui_class("WhisperFaceWindowController")
        asked = set()
        for name in (*self.EDITORS, *self.EDITORS.values(), "_edit_snippet",
                     "_snippet_form", "_sheet_footer", "_sheet_notice"):
            for node in ast.walk(_gui_function(name, controller)):
                if isinstance(node, ast.Constant) \
                        and isinstance(node.value, str) \
                        and node.value.startswith(
                            ("sheets.", "settings.")):
                    asked.add(node.value)
        self.assertGreaterEqual(len(asked), 40)
        for key in asked:
            with self.subTest(key=key):
                self.assertTrue(
                    key in catalog or any(
                        other.startswith(key) for other in catalog),
                    f"{key} is not in the catalog")
        used = ast.unparse(controller) + ast.unparse(
            _gui_function("correction_row_caption"))
        for key in sheets:
            with self.subTest(used=key):
                stem = key.rsplit(".", 1)[0]
                self.assertTrue(
                    f"'{key}'" in used or f"'{stem}." in used
                    or f"{stem}." in used,
                    f"{key} is in the catalog and nothing shows it")

    @staticmethod
    def _runtime_snippets():
        """The runtime's own snippet matching, read out of dictate.py:
        ``whole(said, name)`` for a dictation that is only the phrase,
        ``inside(said, name)`` for the phrase in a longer sentence."""
        tree = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))
        wanted = {"snippet_name_key", "_compile_snippet_pattern",
                  "_snippet_is_placeholder", "expand_snippets_inline"}
        body = [
            node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in wanted)
            or (isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == "SNIPPET_RE"
                for target in node.targets))]
        found = {getattr(node, "name", "SNIPPET_RE") for node in body}
        if found != wanted | {"SNIPPET_RE"}:
            raise AssertionError(
                f"dictate.py no longer defines {wanted - found}")
        runtime = {"re": re}
        exec(compile(ast.Module(body=body, type_ignores=[]),
                     "dictate.py", "exec"), runtime)

        def whole(said, name):
            match = runtime["SNIPPET_RE"].match(said)
            return bool(match) and runtime["snippet_name_key"](
                match.group(1)) == runtime["snippet_name_key"](name)

        def inside(said, name):
            sentence = f"please {said} thanks"
            return runtime["expand_snippets_inline"](
                sentence, {name: "TYPED"}) == "please TYPED thanks"

        return whole, inside

    def test_what_a_sheet_says_to_say_is_what_the_runtime_answers(self):
        whole, inside = self._runtime_snippets()
        # The runtime's own example, to hold this test to the runtime.
        self.assertTrue(whole("insert my email", "email"))
        self.assertTrue(inside("insert my email", "email"))
        self.assertFalse(whole("insert my email", "my email"))
        self.assertFalse(inside("insert my email", "my email"))
        # The form gives a name and the phrase that goes with it.
        speak = localized_string("sheets.snippet.speak")
        example = re.search(r"named “([^”]+)” is “([^”]+)”", speak)
        self.assertIsNotNone(example, speak)
        name, said = example.groups()
        self.assertTrue(
            whole(said, name),
            f"said alone, {said!r} does not type the snippet {name!r}")
        self.assertTrue(
            inside(said, name),
            f"inside a sentence, {said!r} does not type the snippet "
            f"{name!r}")
        # Both sheets name the cue that works both ways, in full.
        cue = "“insert my”"
        self.assertIn(cue, speak)
        self.assertIn(cue, localized_string("sheets.snippets.message"))
        self.assertEqual(said, f"insert my {name}")
        # The pictures the owner is shown follow the same rule: every
        # snippet in the probe's fixture is typed by "insert my" and its
        # name, so none is named with a "my" the runtime would swallow.
        tree = ast.parse((ROOT / "scripts" / "window_render_probe.py")
                         .read_text(encoding="utf-8"))
        fixture = next(
            ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "SHEET_SETTINGS")
        names = [item["name"] for item in fixture["snippets"]]
        self.assertIn(name, names)
        for snippet in names:
            with self.subTest(snippet=snippet):
                self.assertTrue(whole(f"insert my {snippet}", snippet))
                self.assertTrue(inside(f"insert my {snippet}", snippet))

    def test_the_keyword_sheet_claims_no_more_than_the_runtime_does(self):
        # A keyword that was tested and approved separately is given
        # priority in the recognizer's prompt; one that was not, is not.
        activation = (ROOT / "acoustic_keyword_activation.py").read_text(
            encoding="utf-8")
        runtime = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn('RUNTIME_EFFECT = "prompt-priority"', activation)
        self.assertIn('"prompt-priority" if measured else "none"', runtime)
        message = localized_string("sheets.keywords.message")
        # So the sheet may say a keyword changes nothing on its own, and
        # must not say that keywords never change what is recognized.
        self.assertRegex(
            message,
            r"On their own they do not change what is recognized; "
            r"only a name you test and approve separately can\.$")
        self.assertIn("kept on this Mac", message)

    def test_a_refused_save_says_what_cancel_cannot_take_back(self):
        controller = _gui_class("WhisperFaceWindowController")
        save = ast.unparse(_gui_function("editTone_", controller))
        # The app that was refused is named, and so is every app saved
        # before it: those stay saved whatever is pressed next.
        self.assertIn("'sheets.tones.refused', app=item.name", save)
        self.assertIn(
            "'sheets.tones.saved_before', apps=', '.join(saved)", save)
        self.assertLess(save.index("break"), save.index("saved.append"))
        self.assertEqual(
            localized_string(
                "sheets.tones.refused", app="Code", reason="No."),
            "Code: No.")
        self.assertEqual(
            localized_string("sheets.tones.saved_before", apps="Mail, Notes"),
            "Already saved: Mail, Notes.")
        # A name that was not typed is asked for in the form's own
        # words, before anything is sent to be saved.
        form = ast.unparse(_gui_function("_edit_snippet", controller))
        self.assertLess(
            form.index("if not typed_name.strip():"),
            form.index("self.view_model.save_snippet("))
        self.assertIn("self._l('validation.snippet.required')", form)
        # No sentence says "A excluded".
        for key in ("validation.vocabulary.preferred",
                    "validation.vocabulary.excluded"):
            said = localized_string(
                "validation.vocabulary.reserved",
                label=localized_string(key))
            self.assertNotRegex(said, r"\bA [aeiou]")
            self.assertIn(localized_string(key), said)

    def test_a_learned_correction_says_where_it_stands_in_a_line(self):
        def one(**fields):
            return normalize_settings({"corrections": [{
                "key": "gwen", "source": "Gwen", "target": "Qwen",
                "count": 3, "kind": "correction", **fields}]}).corrections[0]

        mail = {"bundle": "com.example.mail", "name": "Mail", "count": 2}
        cases = {
            "On in every app\nSeen in Mail 2×": one(
                global_decision="active",
                app_scopes=[{**mail, "decision": "active"}]),
            "On in Mail\nSeen in Mail 2×": one(
                app_scopes=[{**mail, "decision": "active"}]),
            "Held back, because your corrections for it disagree": one(
                global_decision="held_back"),
            "Held back, because your corrections for it disagree\n"
            "Seen in Mail 2×": one(
                app_scopes=[{**mail, "decision": "held_back"}]),
            "Still learning, so not on yet": one(),
            "From an edit you made to this snippet": one(kind="snippet"),
        }
        for caption, correction in cases.items():
            with self.subTest(caption=caption):
                self.assertEqual(correction_row_caption(correction), caption)
                self.assertEqual(
                    correction_row_caption(correction, locale="fr-CA"),
                    caption)
                # The dialog's explanation still says the same of it: the
                # two are worked out from one place.
                review = correction_review_text(correction)
                self.assertEqual(
                    "Whole-word matches in every app" in review,
                    caption.startswith("On in every app"))
                self.assertEqual(
                    "Whole-word matches in Mail" in review,
                    caption.startswith("On in Mail"))
                self.assertEqual(
                    "Held back" in review, caption.startswith("Held back"))
                self.assertEqual(
                    "Not active yet" in review,
                    caption.startswith(("Held back", "Still learning")))
        self.assertIn(
            "_correction_standing",
            _called_names(_gui_function("correction_review_text")))
        self.assertIn(
            "_correction_standing",
            _called_names(_gui_function("correction_row_caption")))

    def test_an_app_icon_is_drawn_when_its_row_is_made(self):
        # The system renders an app's icon the first time something asks
        # for it, and shows an image view a dashed placeholder until then.
        icon = _gui_function("app_icon_image")
        self.assertIn("URLForApplicationWithBundleIdentifier_",
                      ast.unparse(icon))
        self.assertIn("_drawn_image", _called_names(icon))
        self.assertIn("'app.dashed'", ast.unparse(icon))
        drawn = ast.unparse(_gui_function("_drawn_image"))
        self.assertIn("graphicsContextWithBitmapImageRep_", drawn)
        self.assertIn("restoreGraphicsState", drawn)
        # The bundle identifier is help on the row, never its text.
        page = ast.unparse(_gui_function(
            "_tones_page", _gui_class("WhisperFaceWindowController")))
        self.assertIn("title.setToolTip_(item.bundle)", page)
        self.assertNotIn("f'{item.name} — {item.bundle}'", page)
        self.assertIn("_silence_image(icon)", page)

    def test_the_render_probe_pictures_the_alerts(self):
        # ``--alerts`` shows the app icon where the menu bar shows it, and
        # the chosen face where the window's own confirmations show it.
        source = (ROOT / "scripts" / "window_render_probe.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn('ALERTS = "--alerts" in ARGS', source)
        self.assertIn("``--alerts``", ast.get_docstring(tree))
        functions = {node.name: node for node in tree.body
                     if isinstance(node, ast.FunctionDef)}
        render = ast.unparse(functions["render_alerts"])
        self.assertIn("'WhisperFace.icns'", render)
        self.assertIn("setIcon_", render)
        self.assertIn("_show_the_face", render)
        dictate = (ROOT / "dictate.py").read_text(encoding="utf-8")
        self.assertIn('str(HERE / "icons" / "WhisperFace.icns"))', dictate)

    def test_the_render_probe_pictures_every_editor(self):
        source = (ROOT / "scripts" / "window_render_probe.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn('SHEETS = "--sheets" in ARGS', source)
        self.assertIn("``--sheets``", ast.get_docstring(tree))
        functions = {node.name: node for node in tree.body
                     if isinstance(node, ast.FunctionDef)}
        render = ast.unparse(functions["render_sheets"])
        # Every editor is opened by its own action and answered through
        # the window's seam; nothing is built for the probe.
        for action in self.EDITORS:
            self.assertIn(f"controller.{action}", render)
        self.assertIn("controller.modal_responder = respond", render)
        self.assertIn("finally:\n            controller.modal_responder = "
                      "None", render)
        # A step is (picture, answer); a step with no picture has None.
        pictures = {
            node.elts[0].value
            for node in ast.walk(functions["render_sheets"])
            if isinstance(node, ast.Tuple) and len(node.elts) == 2
            and isinstance(node.elts[0], ast.Constant)
            and isinstance(node.elts[0].value, str)}
        self.assertEqual(pictures, {
            "tones", "tones-many", "tones-contrast", "snippets",
            "snippets-edit", "snippets-add", "snippets-add-refused",
            "snippets-empty", "vocabulary", "vocabulary-refused",
            "vocabulary-empty", "corrections", "corrections-contrast",
            "corrections-empty", "keywords", "keywords-copied",
            "keywords-empty"})
        self.assertIn("f'sheet-{picture}-{suffix}'", render)
        self.assertIn("f'sheet-tones-empty-{suffix}'", render)
        self.assertIn("f'sheet-in-window-{picture}-{suffix}'", render)
        # A sheet is drawn as it is when it has the keyboard (the default
        # button as the default), over the window at its minimum size.
        self.assertIn("gui.SheetWindow = KeySheetWindow", render)
        self.assertIn("window.setContentSize_(gui.WINDOW_MIN_SIZE)", render)
        key_window = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "KeySheetWindow")
        self.assertEqual(ast.unparse(key_window.bases[0]), "gui.SheetWindow")
        self.assertEqual(
            _method_names(key_window),
            {"isKeyWindow", "hasKeyAppearance", "_hasKeyAppearance"})
        # In the middle of the window, where AppKit puts a sheet.
        placed = ast.unparse(functions["sheet_in_window"])
        self.assertIn("(height - size.height) / 2.0", placed)
        # Increase Contrast is put back however the render ends.
        self.assertIn("finally:\n        gui._increase_contrast = ", render)
        # Both appearances, and without the option the probe writes what
        # it always wrote.
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.If)
                 and ast.unparse(node.test) == "SHEETS"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [ast.unparse(line) for line in calls[1].body],
            ["render_sheets(suffix)", "continue"])
        # The fixture: several apps with different tones, several
        # snippets with a long one, both lists, both kinds of correction.
        fixture = next(
            ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "SHEET_SETTINGS")
        self.assertGreaterEqual(
            len({item["tone"] for item in fixture["app_tones"]}), 5)
        self.assertGreaterEqual(len(fixture["snippets"]), 3)
        self.assertTrue(any(
            len(item["text"]) > 120 for item in fixture["snippets"]))
        self.assertTrue(fixture["manual_vocabulary"])
        self.assertTrue(fixture["banned_vocabulary"])
        self.assertEqual(
            {item["kind"] for item in fixture["corrections"]},
            {"correction", "snippet"})
        settings = normalize_settings(fixture)
        self.assertEqual(
            len(settings.corrections), len(fixture["corrections"]))
        self.assertEqual(len(settings.snippets), len(fixture["snippets"]))


class ShellNavigationTests(unittest.TestCase):
    """The sidebar as a projection of the view model, checked headlessly."""

    def test_five_rows_in_sidebar_order(self):
        self.assertEqual(
            [(row.key, row.section, row.pane) for row in SIDEBAR_ROWS],
            [("home", "Home", None),
             ("personalize", "Settings", "Personalize"),
             ("controls", "Settings", "Controls"),
             ("privacy", "Settings", "Privacy"),
             ("advanced", "Advanced", None)])
        for row in SIDEBAR_ROWS:
            with self.subTest(row=row.key):
                self.assertIn(row.section, SECTIONS)
                self.assertTrue(row.pane is None or row.pane in SETTINGS_PANES)
                self.assertIn(row.label_key, STRING_CATALOGS["en"])
                self.assertTrue(row.symbol)
        # One row per settings pane, and one for each section without panes.
        self.assertEqual(
            [row.pane for row in SIDEBAR_ROWS if row.section == "Settings"],
            list(SETTINGS_PANES))
        self.assertEqual(
            len({row.symbol for row in SIDEBAR_ROWS}), len(SIDEBAR_ROWS))

    def test_row_to_destination_and_back(self):
        for index, row in enumerate(SIDEBAR_ROWS):
            with self.subTest(row=row.key):
                section, pane = sidebar_row_destination(index)
                self.assertEqual((section, pane), (row.section, row.pane))
                self.assertEqual(
                    sidebar_row_index(section, pane or SETTINGS_PANES[0]),
                    index)

    def test_every_state_the_view_model_can_hold_has_exactly_one_row(self):
        seen = set()
        for section in SECTIONS:
            for pane in SETTINGS_PANES:
                with self.subTest(section=section, pane=pane):
                    index = sidebar_row_index(section, pane)
                    self.assertEqual(
                        sidebar_row_destination(index),
                        (section, pane if section == "Settings" else None))
                    seen.add(index)
        self.assertEqual(seen, set(range(len(SIDEBAR_ROWS))))

    def test_the_remembered_pane_does_not_move_the_selection_elsewhere(self):
        # The view model keeps the last settings pane while Home or
        # Advanced is showing.
        for pane in SETTINGS_PANES:
            self.assertEqual(sidebar_row_index("Home", pane), 0)
            self.assertEqual(sidebar_row_index("Advanced", pane), 4)

    def test_unknown_rows_and_destinations_are_errors(self):
        for index in (-1, len(SIDEBAR_ROWS), 99):
            with self.assertRaises(ValueError):
                sidebar_row_destination(index)
        with self.assertRaises(ValueError):
            sidebar_row_index("Nowhere", "Personalize")
        with self.assertRaises(ValueError):
            sidebar_row_index("Settings", "Nowhere")

    def model(self, reads, *, fail=False):
        def settings_snapshot():
            reads.append(True)
            if fail:
                raise OSError("disk says no")
            return {}

        return WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {},
            settings_snapshot=settings_snapshot))

    def test_selecting_a_row_goes_through_the_view_model(self):
        reads = []
        model = self.model(reads)
        for index, row in enumerate(SIDEBAR_ROWS):
            with self.subTest(row=row.key):
                state = select_sidebar_row(model, index)
                self.assertIs(state, model.state)
                self.assertEqual(state.section, row.section)
                if row.pane is not None:
                    self.assertEqual(state.settings_pane, row.pane)
                self.assertEqual(
                    sidebar_row_index(state.section, state.settings_pane),
                    index)
        # Walking back up shows the same pages.
        for index in reversed(range(len(SIDEBAR_ROWS))):
            select_sidebar_row(model, index)
            self.assertEqual(
                sidebar_row_index(
                    model.state.section, model.state.settings_pane), index)

    def test_settings_are_read_on_arrival_not_between_panes(self):
        reads = []
        model = self.model(reads)
        select_sidebar_row(model, 1)       # Home -> Personalize: arrival
        self.assertEqual(len(reads), 1)
        select_sidebar_row(model, 2)       # Personalize -> Controls
        select_sidebar_row(model, 3)       # Controls -> Privacy
        self.assertEqual(len(reads), 1)
        select_sidebar_row(model, 3)       # the showing row again: refresh
        self.assertEqual(len(reads), 2)
        select_sidebar_row(model, 4)       # Advanced reads nothing
        select_sidebar_row(model, 0)
        self.assertEqual(len(reads), 2)
        select_sidebar_row(model, 2)       # arrival again, straight to a pane
        self.assertEqual(len(reads), 3)
        self.assertEqual(model.state.settings_pane, "Controls")

    def test_selecting_a_row_clears_the_last_notice(self):
        model = self.model([])
        for index in range(len(SIDEBAR_ROWS)):
            with self.subTest(row=SIDEBAR_ROWS[index].key):
                model.state = replace(
                    model.state, notice="stale", notice_level="success")
                select_sidebar_row(model, index)
                self.assertEqual(model.state.notice, "")
                self.assertEqual(model.state.notice_level, "info")

    def test_a_failed_settings_read_stays_on_screen_in_the_chosen_pane(self):
        model = self.model([], fail=True)
        select_sidebar_row(model, 2)
        self.assertEqual(model.state.section, "Settings")
        self.assertEqual(model.state.settings_pane, "Controls")
        self.assertEqual(model.state.notice_level, "error")
        self.assertIn("disk says no", model.state.notice)

    def test_every_runtime_phase_has_a_one_or_two_word_state(self):
        phases = {
            "ready", "recording", "processing", "recovery", "degraded",
            "paused", "starting"}
        self.assertEqual(set(SIDEBAR_STATES), phases)
        for phase in phases:
            with self.subTest(phase=phase):
                key, tone = sidebar_state(phase, setting_up=False)
                self.assertLessEqual(
                    len(localized_string(key).split()), 2)
                self.assertIn(tone, ("good", "attention", "quiet"))
                # First-run setup outranks whatever the service reports.
                self.assertEqual(
                    sidebar_state(phase, setting_up=True),
                    ("shell.state.setup", "quiet"))
        self.assertEqual(localized_string("shell.state.ready"), "Ready")
        self.assertEqual(localized_string("shell.state.paused"), "Paused")
        self.assertEqual(localized_string("shell.state.setup"), "Setting up")
        self.assertEqual(sidebar_state("ready", setting_up=False)[1], "good")
        self.assertEqual(
            sidebar_state("degraded", setting_up=False)[1], "attention")
        # A phase the window has never heard of is not reported as ready.
        self.assertEqual(
            sidebar_state("mystery", setting_up=False),
            SIDEBAR_STATES["starting"])
        # Model snapshots really produce these phases.
        self.assertIn(normalize_snapshot({}).status_phase, phases)

    def test_page_shortcuts_are_in_the_smoke_contract(self):
        equivalents = native_appkit_smoke_contract().key_equivalents
        for index, row in enumerate(SIDEBAR_ROWS):
            self.assertIn(f"command-{index + 1}:{row.key}", equivalents)
        self.assertIn("command-d:advanced", equivalents)

    def test_shell_accessibility_labels_are_in_the_smoke_contract(self):
        keys = native_appkit_smoke_contract().accessibility_catalog_keys
        self.assertIn("shell.sidebar.accessibility.label", keys)
        self.assertIn("shell.state.accessibility.label", keys)
        # The pane switch is gone, and so is the label only it carried.
        self.assertNotIn("settings.accessibility.category.label", keys)

    def test_shell_structure_is_the_systems(self):
        controller = _gui_class("WhisperFaceWindowController")
        build = ast.unparse(_gui_function("_build_window", controller))
        # A split view controller with a sidebar item, in a full-size
        # content view: the material, divider and full-height layout are
        # the system's. No toolbar, no hand-made effect view.
        self.assertIn("NSSplitViewItem.sidebarWithViewController_(", build)
        self.assertIn("setContentViewController_(", build)
        self.assertIn("NSWindowStyleMaskFullSizeContentView", build)
        self.assertIn("setTitlebarAppearsTransparent_(True)", build)
        self.assertIn("setTitleVisibility_(NSWindowTitleHidden)", build)
        # Read from the code the shell is built by, not from the module's
        # text, so a comment or another surface elsewhere cannot trip it.
        shell = build + ast.unparse(
            _gui_function("_build_sidebar", controller))
        for hand_made in ("NSVisualEffectView", "NSToolbar"):
            self.assertNotIn(hand_made, shell)
        self.assertEqual(
            [base.id for base in _gui_class(
                "ShellSplitViewController").bases],
            ["NSSplitViewController"])
        # The old in-window header and pane switch are gone for good: no
        # attribute, function or dynamic key still goes by their names.
        names = set()
        for node in ast.walk(GUI_TREE):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(
                    node.value, str):
                names.add(node.value)
        for gone in ("topbar", "settings_pane_control",
                     "settingsPaneChanged_", "settingsPaneChanged:",
                     "sectionChanged_", "sectionChanged:"):
            self.assertNotIn(gone, names)

    def test_shell_names_are_imported_inside_the_appkit_guard(self):
        # One missing AppKit name outside the guard would stop the module
        # importing at all, instead of leaving the window unavailable.
        guard = next(
            node for node in GUI_TREE.body
            if isinstance(node, ast.Try) and any(
                isinstance(item, ast.Assign)
                and getattr(item.targets[0], "id", "") == "APPKIT_AVAILABLE"
                for item in node.body))
        guarded = {
            alias.name
            for item in guard.body if isinstance(item, ast.ImportFrom)
            for alias in item.names}
        self.assertLessEqual(
            {"NSSplitViewController", "NSSplitViewItem", "NSStackView",
             "NSViewController", "NSWindowStyleMaskFullSizeContentView",
             "NSWindowTitleHidden", "NSAccessibilityListRole"}, guarded)
        # Newer than macOS 14, so looked up rather than imported.
        self.assertNotIn("NSAccessibilityHeadingRole", guarded)
        # So are the names the brand components, Home and setup draw with.
        self.assertLessEqual(
            {"NSAccessibilityGroupRole", "NSAccessibilityImageRole",
             "NSAffineTransform", "NSGraphicsContext", "NSTextAttachment",
             "NSMutableAttributedString", "NSTextAlignmentRight",
             "NSTextAlignmentCenter", "CGContextBeginTransparencyLayer"},
            guarded)
        # Nothing from a system framework is imported anywhere else at
        # import time: outside the guard, only the project's own modules.
        guarded_lines = {item.lineno for item in guard.body}
        frameworks = {"AppKit", "Foundation", "Quartz", "objc", "CoreText"}

        def top_level_imports(body):
            for node in body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    yield node
                elif isinstance(node, (ast.If, ast.Try)):
                    yield from top_level_imports(node.body)
                    yield from top_level_imports(node.orelse)

        for node in top_level_imports(GUI_TREE.body):
            module = (node.module if isinstance(node, ast.ImportFrom)
                      else node.names[0].name)
            if module.split(".")[0] in frameworks:
                self.assertIn(
                    node.lineno, guarded_lines,
                    f"line {node.lineno} imports {module} outside the guard")
        # No name is fetched from AppKit by string where it is used.
        self.assertNotIn("_appkit_name", {
            node.name for node in ast.walk(GUI_TREE)
            if isinstance(node, ast.FunctionDef)})
        self.assertNotIn("_appkit_name(", GUI_SOURCE)

    def test_one_missing_appkit_name_leaves_the_module_importable(self):
        # Import the module in a fresh interpreter against stand-in
        # frameworks. With every name there, the import goes all the way
        # through, so a name that is missing is the only thing in the
        # way: the window is then unavailable, and the module, with the
        # view model in it, still loads.
        import subprocess
        import textwrap
        script = textwrap.dedent("""
            import sys, types

            class Anything:
                def __init__(self, *args, **kwargs):
                    pass

                def __call__(self, *args, **kwargs):
                    return Anything()

                def __getattr__(self, name):
                    if name.startswith("__"):
                        raise AttributeError(name)
                    return Anything()

                def __mro_entries__(self, bases):
                    return (object,)

                def __or__(self, other):
                    return self

                __ror__ = __and__ = __rand__ = __or__

                def __int__(self):
                    return 0

                def __float__(self):
                    return 0.0

                def __index__(self):
                    return 0

            class Framework(types.ModuleType):
                missing = ()

                def __getattr__(self, name):
                    if name.startswith("__") or name in self.missing:
                        raise AttributeError(name)
                    return Anything()

            for module in ("objc", "AppKit", "Foundation", "Quartz"):
                sys.modules[module] = Framework(module)
            if sys.argv[3]:
                sys.modules[sys.argv[2]].missing = (sys.argv[3],)
            sys.path.insert(0, sys.argv[1])
            import whisper_face_gui
            print("available" if whisper_face_gui.APPKIT_AVAILABLE
                  else "unavailable")
            whisper_face_gui.WhisperFaceViewModel(
                whisper_face_gui.GUIActions())
        """)
        for module, name, expected in (
                ("AppKit", "", "available"),
                # Only the brand components draw with these.
                ("AppKit", "NSAccessibilityRadioButtonRole", "unavailable"),
                ("AppKit", "NSFontFeatureSettingsAttribute", "unavailable"),
                ("AppKit", "NSMutableParagraphStyle", "unavailable"),
                ("AppKit", "NSViewNoIntrinsicMetric", "unavailable"),
                ("Quartz", "CGContextSetAlpha", "unavailable"),
                # Home and setup used to fetch these by name at use.
                ("AppKit", "NSTextAttachment", "unavailable"),
                ("Foundation", "NSMutableAttributedString", "unavailable"),
                # The window's own, as before.
                ("AppKit", "NSSplitViewController", "unavailable"),
                ("AppKit", "NSWindow", "unavailable")):
            with self.subTest(missing=f"{module}.{name}"):
                result = subprocess.run(
                    [sys.executable, "-c", script, str(ROOT), module, name],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(
                    result.returncode, 0,
                    f"import failed without {name}:\n{result.stderr[-600:]}")
                self.assertEqual(result.stdout.strip(), expected)

    def test_sidebar_row_has_every_state_a_custom_control_needs(self):
        row = _gui_class("SidebarRowButton")
        self.assertEqual([base.id for base in row.bases], ["BrandButton"])
        self.assertTrue(ast.get_docstring(row))
        self.assertLessEqual(
            {"configure", "set_selected", "drawRect_", "keyDown_",
             "mouseDown_", "hitTest_", "canBecomeKeyView",
             "takes_tab_stop", "drawFocusRingMask",
             "focusRingMaskBounds", "viewDidChangeEffectiveAppearance"},
            _method_names(row))
        text = ast.unparse(row)
        # One Tab stop for the whole sidebar: only the selected row, and
        # only where the system lets a button take one. The rule is its
        # own method so it can be checked where that setting is off.
        self.assertIn(
            "takes_tab_stop",
            _called_names(_gui_function("canBecomeKeyView", row)))
        self.assertEqual(
            ast.unparse(_gui_function("takes_tab_stop", row).body[-1]),
            "return bool(self.selected) and bool(buttons_take_tab)")
        # Selected, for VoiceOver as well as for the eye.
        self.assertIn("setAccessibilitySelected_", text)
        self.assertIn("'sidebar_selection'", text)
        # No press spring on navigation: the click skips JellyButton.
        self.assertIn(
            "objc.super(JellyButton, self).mouseDown_(event)", text)
        self.assertNotIn("add_jelly_motion", text)

    def test_shell_render_and_actions_are_wired(self):
        controller = _gui_class("WhisperFaceWindowController")
        shell = _called_names(_gui_function("_render_shell", controller))
        for name in ("_apply_window_theme", "sidebar_row_index",
                     "_sync_sidebar", "_sync_title_strip",
                     "_animate_section_change"):
            self.assertIn(name, shell)
        self.assertLess(
            shell.index("_apply_window_theme"), shell.index("_sync_sidebar"),
            "the sidebar reads the appearance the theme resolves")
        for name in ("set_setup_mode", "add_title_item", "selectSidebarRow_",
                     "move_sidebar_focus", "windowDidBecomeKey_",
                     "windowDidResignKey_", "_build_sidebar"):
            _gui_function(name, controller)
        self.assertIn(
            "select_sidebar_row",
            _called_names(_gui_function("_select_sidebar_row", controller)))
        menu = ast.unparse(_gui_function("_install_main_menu", controller))
        self.assertIn("'menu.view'", menu)
        self.assertIn("'selectSidebarRow:'", menu)
        self.assertIn("enumerate(SIDEBAR_ROWS)", menu)
        self.assertEqual(localized_string("menu.view"), "View")

    def test_shell_geometry_adds_up(self):
        values = _gui_constants(
            "SIDEBAR_WIDTH", "CONTENT_WIDTH", "CONTENT_MIN_WIDTH",
            "CONTENT_EDGE_MIN", "CONTENT_INSET", "WINDOW_MIN_SIZE",
            "WINDOW_DEFAULT_SIZE", "TITLE_STRIP_HEIGHT", "TITLE_BAR_HEIGHT",
            "SIDEBAR_BRAND_TOP", "SIDEBAR_FACE_SIZE", "SIDEBAR_BRAND_GAP",
            "SIDEBAR_ROW_HEIGHT", "SIDEBAR_ROW_GAP",
            "SIDEBAR_GROUP_LABEL_HEIGHT", "SIDEBAR_FOOT_INSET",
            "SIDEBAR_PADDING", "SIDEBAR_ROW_RADIUS", "PAGE_COLUMN_MIN_WIDTH")
        # At the window minimum the column is at its narrowest, and the
        # margin beside it is never less than CONTENT_EDGE_MIN. Today the
        # pages need CONTENT_MIN_WIDTH, which leaves exactly that margin;
        # with PAGE_COLUMN_MIN_WIDTH it would be the full CONTENT_INSET.
        margin = (values["WINDOW_MIN_SIZE"][0] - values["SIDEBAR_WIDTH"]
                  - values["CONTENT_MIN_WIDTH"]) / 2.0
        self.assertGreaterEqual(margin, values["CONTENT_EDGE_MIN"])
        self.assertLessEqual(margin, values["CONTENT_INSET"])
        self.assertLessEqual(
            values["PAGE_COLUMN_MIN_WIDTH"], values["CONTENT_MIN_WIDTH"])
        self.assertLess(values["CONTENT_MIN_WIDTH"], values["CONTENT_WIDTH"])
        self.assertLess(values["CONTENT_EDGE_MIN"], values["CONTENT_INSET"])
        # The default window has room for the full column and full insets.
        self.assertGreaterEqual(
            values["WINDOW_DEFAULT_SIZE"][0],
            values["SIDEBAR_WIDTH"] + 2 * values["CONTENT_INSET"]
            + values["CONTENT_WIDTH"])
        # The brand block starts below the title bar, and the title bar
        # fits inside the strip that replaces it on the content side.
        self.assertGreater(
            values["SIDEBAR_BRAND_TOP"], values["TITLE_BAR_HEIGHT"])
        self.assertGreater(
            values["TITLE_STRIP_HEIGHT"], values["TITLE_BAR_HEIGHT"])
        # Everything in the sidebar fits the minimum height with the build
        # label clear of the last row.
        rows = len(SIDEBAR_ROWS)
        rows_bottom = (
            values["SIDEBAR_BRAND_TOP"] + values["SIDEBAR_FACE_SIZE"]
            + values["SIDEBAR_BRAND_GAP"]
            + rows * values["SIDEBAR_ROW_HEIGHT"]
            + (rows - 1) * values["SIDEBAR_ROW_GAP"]
            + values["SIDEBAR_GROUP_LABEL_HEIGHT"])
        self.assertLess(
            rows_bottom + 24.0,
            values["WINDOW_MIN_SIZE"][1] - values["SIDEBAR_FOOT_INSET"] - 14.0)
        self.assertLess(
            values["SIDEBAR_ROW_RADIUS"], values["SIDEBAR_ROW_HEIGHT"] / 2.0)

    def test_sidebar_text_keeps_aa_on_the_selection_and_the_material(self):
        import whisper_face_theme as theme

        # The opaque colours the sidebar material falls back to, measured
        # in the probe: what an inactive window and a headless render show.
        materials = {False: (0.941, 0.941, 0.941), True: (0.157, 0.157, 0.157)}
        for dark, material in materials.items():
            palette = theme.palette_for_appearance(dark)
            with self.subTest(dark=dark):
                for contrast in (False, True):
                    selection = theme.color_token(
                        "sidebar_selection", dark, contrast).over(material)
                    self.assertGreaterEqual(
                        theme.contrast_ratio(palette.ink, selection), 4.5)
                dimmed = theme.Shade(palette.ink, 0.10).over(material)
                self.assertGreaterEqual(
                    theme.contrast_ratio(palette.ink, dimmed), 4.5)
                for ink in (palette.ink, palette.ink_soft):
                    self.assertGreaterEqual(
                        theme.contrast_ratio(ink, material), 4.5)
                attention = (
                    palette.error if dark else gui_module.ERROR_TEXT_ON_LIGHT)
                self.assertGreaterEqual(
                    theme.contrast_ratio(attention, material), 3.0)



class HomeAndSetupTests(unittest.TestCase):
    """Home and first-run setup, as far as they can be checked without
    AppKit: the rules that decide what shows, and the edge the dark
    appearance gives a control."""

    ISSUE = gui_module.DegradedIssue(
        "microphone", "Microphone permission is needed", "Enable it")

    def test_a_dark_filled_control_gets_the_lowest_edge_that_measures_3(
            self):
        import whisper_face_theme as theme

        dark = theme.DARK_PALETTE
        edge = theme.color_token("control_edge", True)
        # The light ink, at the lowest opacity (in hundredths) that is 3:1
        # against the surface a plain button or a key cap is filled with
        # and usually sits on.
        self.assertEqual(edge.color, dark.ink)
        self.assertEqual(edge.alpha, theme.CONTROL_EDGE_DARK_ALPHA)
        self.assertEqual(edge.alpha, 0.39)
        measured = theme.contrast_ratio(
            edge.over(dark.surface), dark.surface)
        self.assertGreaterEqual(measured, 3.0)
        self.assertLess(measured, 3.1)
        self.assertLess(
            theme.contrast_ratio(
                theme.Shade(dark.ink, edge.alpha - 0.01).over(dark.surface),
                dark.surface), 3.0)
        # On the darker window field the same edge is stronger.
        self.assertGreater(
            theme.contrast_ratio(edge.over(dark.surface), dark.bg), measured)
        # Increase Contrast keeps the whole light ink; the light
        # appearance keeps the ordinary outline in both.
        self.assertEqual(
            theme.color_token("control_edge", True, True),
            theme.Shade(dark.ink))
        for contrast in (False, True):
            self.assertEqual(
                theme.color_token("control_edge", False, contrast),
                theme.color_token("ink_outline", False, contrast))
        # The ink of bright fills and of things that are not controls is
        # the dark ink, as before: the stage, amber, the bubble, stickers.
        self.assertEqual(
            theme.color_token("ink_outline", True).color, dark.line)

    def test_the_edge_rule_is_applied_where_the_components_draw(self):
        ink = ast.unparse(_gui_function("_outline_ink"))
        self.assertIn("color_token('control_edge', dark", ink)
        chunky = ast.unparse(
            _gui_function("drawRect_", _gui_class("ChunkyButton")))
        # Plain is the dark-filled control; amber keeps the dark ink.
        self.assertIn(
            "ink=_outline_ink(dark, on_bright=amber, control=not amber)",
            chunky)
        cap = ast.unparse(_gui_function("draw_key_cap"))
        self.assertIn("ink=_outline_ink(dark, control=True)", cap)
        # Not controls: the bubble and the stickers keep the ordinary ink.
        for name in ("SpeechBubbleView", "StickerTileView"):
            draw = ast.unparse(_gui_function("drawRect_", _gui_class(name)))
            self.assertIn("_outline_ink(dark)", draw)
            self.assertNotIn("control=True", draw)

    def test_a_hard_shadow_is_dark_whatever_its_outline_is(self):
        # Increase Contrast turns the bubble's and the stickers' outline
        # light in the dark appearance. Their shadow used to follow it
        # and read as a glow. Every shadow is drawn in one place, in the
        # ink a bright fill keeps: dark in both appearances.
        helper = _gui_function("draw_hard_shadow_shape")
        text = ast.unparse(helper)
        self.assertIn("_outline_ink(dark, on_bright=True).setFill()", text)
        self.assertNotIn("shadow_ink", GUI_SOURCE)
        arguments = [argument.arg for argument in helper.args.kwonlyargs]
        self.assertIn("dark", arguments)
        # No default: a caller cannot forget which appearance it draws in.
        position = arguments.index("dark")
        self.assertIsNone(helper.args.kw_defaults[position])
        # The ink it uses never takes the light Increase Contrast value
        # in the dark appearance, and is the palette's dark line there.
        theme = gui_module.__dict__.get("color_token") or __import__(
            "whisper_face_theme").color_token
        from whisper_face_theme import DARK_PALETTE, LIGHT_PALETTE
        ink = ast.unparse(_gui_function("_outline_ink"))
        self.assertIn(
            "_increase_contrast() and (not (dark and on_bright))", ink)
        self.assertEqual(
            theme("ink_outline", True, False).color, DARK_PALETTE.line)
        self.assertEqual(
            theme("ink_outline", True, True).color, DARK_PALETTE.ink)
        for increase in (False, True):
            self.assertLess(
                sum(theme("ink_outline", False, increase).color),
                sum(LIGHT_PALETTE.surface) / 2.0)
        # Every component hands the helper its appearance.
        for name in (*NewLookFoundationContractTests.COMPONENTS,
                     "SpeechBubbleView"):
            draw = _gui_function("drawRect_", _gui_class(name))
            if name == "KeyCapView":
                draw = _gui_function("draw_key_cap")
            with self.subTest(component=name):
                self.assertIn("dark=dark", ast.unparse(draw))

    def test_key_cap_is_read_once_in_the_readers_language(self):
        cap = ast.unparse(_gui_class("KeyCapView"))
        self.assertIn(
            "localized_string('key_cap.accessibility.role'", cap)
        self.assertNotIn("setAccessibilityRoleDescription_('key')", cap)
        self.assertNotIn("setAccessibilityValue_", cap)
        self.assertEqual(
            localized_string("key_cap.accessibility.role"), "key")

    def state(self, **changes):
        return replace(normalize_snapshot({}), **{
            "onboarding_acknowledged": True, **changes})

    def test_the_bubble_carries_the_primary_action_and_at_most_one_more(
            self):
        cases = (
            ({}, ("pause",), False),
            ({"paused": True}, ("pause",), False),
            ({"outbox_count": 2}, ("pause", "copy_outbox"), False),
            ({"degraded_issues": (self.ISSUE,)}, ("pause", "review"), False),
            ({"outbox_count": 1, "degraded_issues": (self.ISSUE,)},
             ("pause", "copy_outbox"), True),
            # Setup that was never finished or acknowledged asks for a
            # review exactly as a degraded item does.
            ({"onboarding_acknowledged": False, "onboarding_complete": False},
             ("pause", "review"), False),
        )
        for changes, actions, in_row in cases:
            with self.subTest(changes=changes):
                state = self.state(**changes)
                self.assertEqual(home_bubble_actions(state), actions)
                self.assertEqual(home_review_in_row(state), in_row)
                self.assertLessEqual(len(home_bubble_actions(state)), 2)
                self.assertEqual(home_bubble_actions(state)[0], "pause")
                # Whatever is due is always offered somewhere.
                self.assertEqual(
                    home_review_due(state),
                    "review" in actions or in_row)
                self.assertEqual(
                    bool(state.outbox_count), "copy_outbox" in actions)

    RUNTIME = {
        "service_status": "Running", "microphone_status": "Starting",
        "accessibility_status": "Needs attention",
        "input_monitoring_status": "Granted", "capture_state": "Ready",
        "hotkey_label": "F13",
        "models": [{**PREPARING_MODEL}],
    }

    def test_setup_shows_one_row_for_each_permission_with_its_state(self):
        rows = setup_rows(normalize_snapshot(self.RUNTIME))
        self.assertEqual(
            [row.key for row in rows],
            ["microphone", "accessibility", "input_monitoring"])
        self.assertEqual(
            [row.title for row in rows],
            ["Microphone", "Accessibility", "Input Monitoring"])
        self.assertTrue(all(row.caption and row.symbol for row in rows))
        checking, missing, granted = rows
        # Still being checked: nothing to grant yet, so no button.
        self.assertEqual(
            (checking.status, checking.tone, checking.permission),
            ("Checking", "quiet", ""))
        # One state, one phrase: a model being checked says it the same way.
        self.assertEqual(
            checking.status, STRING_CATALOGS["en"]["models.state.checking"])
        # Missing: the row's own button to that pane, and no status words.
        self.assertEqual(
            (missing.status, missing.tone, missing.permission),
            ("", "attention", "accessibility"))
        # Granted: the check mark and the word.
        self.assertEqual(
            (granted.status, granted.tone, granted.permission),
            ("Allowed", "good", ""))
        self.assertNotIn(
            "allow ", " ".join(
                localized_string(key).casefold() for key in (
                    "onboarding.action.open_system_settings",
                    "onboarding.action.continue")))

    def test_the_first_missing_permission_carries_the_steps_primary_action(
            self):
        def primaries(**statuses):
            rows = setup_rows(normalize_snapshot(
                {**self.RUNTIME, **statuses}))
            return rows, [row.key for row in rows if row.primary]

        # One missing: its row is the primary, and Continue waits.
        rows, primary = primaries()
        self.assertEqual(primary, ["accessibility"])
        self.assertTrue(gui_module.setup_waits_on_a_row(rows))
        # Several missing: only the first, the pane the step's single
        # button has always opened.
        rows, primary = primaries(
            microphone_status="Unavailable",
            input_monitoring_status="Needs attention")
        self.assertEqual(primary, ["microphone"])
        self.assertEqual(
            primary[0], normalize_snapshot({
                **self.RUNTIME, "microphone_status": "Unavailable",
                "input_monitoring_status": "Needs attention",
            }).missing_permissions[0])
        self.assertEqual(
            [row.permission for row in rows],
            ["microphone", "accessibility", "input_monitoring"])
        # Nothing missing, only still being checked: no row carries it,
        # and the foot's Continue is the way forward.
        rows, primary = primaries(accessibility_status="Unknown")
        self.assertEqual(primary, [])
        self.assertFalse(gui_module.setup_waits_on_a_row(rows))
        # No other step ever puts its primary action in a row.
        granted = {**self.RUNTIME, "microphone_status": "Ready",
                   "accessibility_status": "Granted"}
        for runtime in (
                granted, {**granted, "hotkey_practiced": True},
                {**granted, "hotkey_practiced": True,
                 "models": [READY_MODEL]},
                {**granted, "hotkey_practiced": True,
                 "models": [READY_MODEL], "last_word_count": 3}):
            self.assertFalse(gui_module.setup_waits_on_a_row(
                setup_rows(normalize_snapshot(runtime))))

    def test_the_window_gives_the_primary_row_the_amber_button_and_return(
            self):
        controller = _gui_class("WhisperFaceWindowController")
        rows = ast.unparse(_gui_function("_render_setup_rows", controller))
        self.assertIn("'amber' if spec.primary else 'plain'", rows)
        self.assertIn(
            "button.setKeyEquivalent_('\\r' if spec.primary else '')", rows)
        onboarding = ast.unparse(
            _gui_function("_render_onboarding", controller))
        self.assertIn("waiting = setup_waits_on_a_row(rows)", onboarding)
        self.assertIn("action.setEnabled_(not waiting)", onboarding)
        self.assertIn(
            "action.setKeyEquivalent_('' if waiting else '\\r')", onboarding)
        # The action itself is unchanged: sent while a permission is
        # missing, it still opens the first missing permission's pane.
        proceed = ast.unparse(_gui_function("continueSetup_", controller))
        self.assertIn(
            "    elif self.view_model.permission_recovery_needed():\n"
            "        self.view_model.open_system_settings()\n", proceed)

    def test_only_what_just_turned_good_pops(self):
        turned = gui_module.setup_rows_turned_good
        none = frozenset()
        # The first render pops nothing.
        self.assertEqual(
            turned(None, "permissions", frozenset({"microphone"}), none),
            none)
        # A permission granted while its step is showing.
        self.assertEqual(
            turned(("permissions", frozenset({"microphone"})), "permissions",
                   frozenset({"microphone", "accessibility"}), none),
            frozenset({"accessibility"}))
        # A new step: every row is new, nothing pops.
        self.assertEqual(
            turned(("hotkey", none), "models",
                   frozenset({"model:Parakeet"}), frozenset({"hotkey"})),
            none)
        # Completion: only the step that was just confirmed.
        steps = frozenset(native_appkit_smoke_contract().onboarding_steps)
        self.assertEqual(
            turned(("first_dictation", none), "complete", steps,
                   frozenset({"first_dictation"})),
            frozenset({"first_dictation"}))
        # A refresh that changed nothing.
        self.assertEqual(
            turned(("complete", steps), "complete", steps, none), none)

    def test_model_rows_are_keyed_by_the_model_not_its_place(self):
        granted = {
            **self.RUNTIME, "microphone_status": "Ready",
            "accessibility_status": "Granted", "hotkey_practiced": True}
        models = [PREPARING_MODEL, {
            "name": "Optional", "install_state": "installed"}]
        first = setup_rows(normalize_snapshot({**granted, "models": models}))
        second = setup_rows(normalize_snapshot(
            {**granted, "models": models[::-1]}))
        self.assertEqual(
            {row.key: row.tone for row in first},
            {row.key: row.tone for row in second})
        self.assertEqual(
            {row.key for row in first},
            {f"model:{model['name']}" for model in models})

    def test_the_face_pops_only_when_a_dictation_lands(self):
        landed = gui_module.home_dictation_landed
        before, after = (930.0, 9, 714, "9 words"), (810.0, 4, 718, "4 words")
        self.assertTrue(
            landed(before, after, available=True, section="Home"))
        # Not on the first render, and not on a refresh that changed
        # nothing (the window renders every two seconds).
        self.assertFalse(landed(None, after, available=True, section="Home"))
        self.assertFalse(
            landed(after, after, available=True, section="Home"))
        # Not without a result to show, and not behind another page.
        self.assertFalse(
            landed(before, after, available=False, section="Home"))
        self.assertFalse(
            landed(before, after, available=True, section="Advanced"))
        # Every spring Home and setup add is gated by Reduce Motion.
        controller = _gui_class("WhisperFaceWindowController")
        for name in ("_animate_home_landing", "_animate_onboarding_face",
                     "_animate_onboarding_steps", "_entrance_stagger"):
            function = _gui_function(name, controller)
            calls = [
                node for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "add_jelly_motion"]
            self.assertTrue(calls, name)
            for call in calls:
                gates = [ast.unparse(keyword.value)
                         for keyword in call.keywords
                         if keyword.arg == "reduced_motion"]
                self.assertEqual(len(gates), 1, name)
                self.assertIn("reduced", gates[0], name)
                self.assertNotIn(gates[0], ("False", "True"), name)
        self.assertIn(
            "home_dictation_landed",
            _called_names(_gui_function("_animate_home_landing", controller)))

    def test_setup_rows_follow_the_step_and_the_key_that_is_bound(self):
        granted = {
            **self.RUNTIME, "microphone_status": "Ready",
            "accessibility_status": "Granted"}
        hotkey = setup_rows(normalize_snapshot(granted))
        self.assertEqual(
            [(row.key, row.key_cap, row.status) for row in hotkey],
            [("hotkey", "F13", "")])
        models = setup_rows(normalize_snapshot(
            {**granted, "hotkey_practiced": True}))
        self.assertEqual(
            [(row.title, row.status) for row in models],
            [(PREPARING_MODEL["name"], normalize_snapshot(
                granted).models[0].status)])
        # No model listed yet: the step itself, waiting.
        waiting = setup_rows(normalize_snapshot(
            {**granted, "hotkey_practiced": True, "models": []}))
        self.assertEqual([row.key for row in waiting], ["models"])
        first = setup_rows(normalize_snapshot(
            {**granted, "hotkey_practiced": True, "models": [READY_MODEL]}))
        self.assertEqual(
            [(row.key, row.key_cap) for row in first],
            [("first_dictation", "F13")])
        done = setup_rows(normalize_snapshot(
            {**granted, "hotkey_practiced": True, "models": [READY_MODEL],
             "last_word_count": 3}))
        self.assertEqual(
            [(row.key, row.tone, row.status) for row in done],
            [(key, "good", "Done") for key in native_appkit_smoke_contract()
             .onboarding_steps])
        self.assertEqual(done[1].key_cap, "F13")
        # Never more rows than the window builds, required models first.
        many = setup_rows(normalize_snapshot({
            **granted, "hotkey_practiced": True, "models": [
                {"name": f"Optional {index}", "install_state": "checking"}
                for index in range(5)] + [PREPARING_MODEL]}))
        self.assertEqual(len(many), gui_module.SETUP_ROW_LIMIT)
        self.assertEqual(many[0].title, PREPARING_MODEL["name"])

    def test_a_row_opens_its_own_permissions_pane_and_only_a_missing_one(
            self):
        opened = []
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {
                **self.RUNTIME, "microphone_status": "Unavailable"},
            open_system_settings=opened.append))
        self.assertEqual(
            model.state.missing_permissions, ("microphone", "accessibility"))
        model.open_system_settings("accessibility")
        model.open_system_settings("input_monitoring")   # granted: ignored
        model.open_system_settings("nonsense")
        model.open_system_settings()                     # the first missing
        self.assertEqual(opened, ["accessibility", "microphone"])

    def test_home_and_setup_are_built_from_the_brand_components(self):
        controller = _gui_class("WhisperFaceWindowController")
        home = ast.unparse(_gui_function("_build_home", controller))
        setup = ast.unparse(_gui_function("_build_setup", controller))
        for text in (home, setup):
            self.assertIn("StagePanelView.alloc()", text)
            self.assertIn("LiveFaceView.alloc()", text)
            self.assertIn("SpeechBubbleView.alloc()", text)
            # On a stage the face has its hard shadow and no speech puffs.
            self.assertIn("setShadow_(True)", text)
            self.assertIn("setWhispers_(False)", text)
            # One look for a group of rows: the header, the rows and the
            # group that holds them are the settings pages' own, and
            # nothing is a boxed card with separator views of its own.
            for helper in ("self._section_header(", "self._settings_row(",
                           "self._settings_group("):
                self.assertIn(helper, text)
            for old in ("_card(", "_hairline(", "NSBox"):
                self.assertNotIn(old, text)
            self.assertNotIn("_face_image(", text)
        self.assertIn("StickerTileView.alloc()", home)
        # Both place that header and group through the one section
        # layout every settings page uses.
        page = _called_names(
            _gui_function("arrange", _gui_class("GroupedPageView")))
        self.assertIn("arrange_section", page)
        for name in ("_layout_home", "_layout_onboarding"):
            self.assertIn("arrange_section", _called_names(
                _gui_function(name, controller)))
        # A setup row's state is the status view every page uses.
        self.assertIn("self._status_label()", setup)
        self.assertIn("_show_status", _called_names(
            _gui_function("_render_setup_rows", controller)))
        # The old blob backdrop and the layer-styled amber button are gone.
        names = {node.attr for node in ast.walk(GUI_TREE)
                 if isinstance(node, ast.Attribute)} | {
            node.name for node in ast.walk(GUI_TREE)
            if isinstance(node, ast.FunctionDef)} | {
            node.value for node in ast.walk(GUI_TREE)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        for gone in ("home_blobs", "home_stat_badges", "_primary_button",
                     "_set_cta_title", "onboarding_face_chip",
                     # ... and so are the card, its separator views and
                     # the row layouts Home and setup used to keep.
                     "_home_row", "_group_row", "_rows_host",
                     "_layout_home_rows", "_layout_setup_row",
                     "home_row_lines", "onboarding_row_lines"):
            self.assertNotIn(gone, names)
        # One Continue button; per-permission buttons are plain.
        self.assertEqual(setup.count("'continueSetup:'"), 1)
        self.assertIn("'openPermissionSettings:', variant='plain'", setup)

    def test_the_render_probe_can_write_every_home_state_and_setup_variant(
            self):
        # ``--states`` renders the complete look from the repository: the
        # states and variants below, by these names, in both appearances.
        source = (ROOT / "scripts" / "window_render_probe.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body
                     if isinstance(node, ast.FunctionDef)}
        self.assertIn('STATES = "--states" in ARGS', source)
        self.assertIn("``--states``", ast.get_docstring(tree))
        returned = next(
            node.value for node in ast.walk(functions["home_states"])
            if isinstance(node, ast.Return))
        self.assertEqual(
            [key.value for key in returned.keys],
            ["ready", "ready-empty", "ready-long-key", "ready-fox",
             "ready-replay", "ready-replay-held", "paused", "listening",
             "working", "starting", "outbox", "outbox-unchecked",
             "outbox-mixed", "needs-attention-permission", "needs-attention-model",
             "outbox-and-attention", "paused-outbox-attention"])
        variants = next(
            node.value for node in tree.body
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "SETUP_VARIANTS")
        self.assertEqual(
            [key.value for key in variants.keys],
            ["permissions", "permissions-all-missing", "permissions-mixed",
             "hotkey", "hotkey-long", "models", "models-many",
             "first-dictation", "complete"])
        render = ast.unparse(functions["render_states"])
        for name in ("f'home-{name}-{suffix}'", "f'setup-{name}-{suffix}'",
                     "'home-outbox-contrast'", "'setup-permissions-contrast'",
                     "f'setup-left-for-advanced-{suffix}'"):
            self.assertIn(name, render)
        # Increase Contrast is put back however the render ends.
        self.assertIn("finally:\n        gui._increase_contrast = ", render)
        # Without the option the probe writes what it always wrote.
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.If)
                 and ast.unparse(node.test) == "STATES"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            ast.unparse(calls[1].body[0]), "render_states(suffix)")
        # Every phase it renders has status copy in the catalog.
        phases = {
            node.args[0].value for node in ast.walk(functions["home_states"])
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "home_phase"}
        self.assertEqual(phases, {
            "ready", "paused", "recording", "processing", "starting",
            "recovery", "degraded"})
        for phase in phases:
            self.assertIn(
                f"overview.status.{phase}.title", STRING_CATALOGS["en"])

    def test_the_render_drives_setup_mode(self):
        controller = _gui_class("WhisperFaceWindowController")
        onboarding = _gui_function("_render_onboarding", controller)
        self.assertIn("set_setup_mode", _called_names(onboarding))
        self.assertIn(
            "self.set_setup_mode(presentation.visible and "
            "state.section == 'Home')", ast.unparse(onboarding))
        # Nothing else in the window turns it on or off.
        callers = [
            node.name for node in controller.body
            if isinstance(node, ast.FunctionDef)
            and "set_setup_mode" in _called_names(node)]
        self.assertEqual(callers, ["_render_onboarding"])

    def test_the_live_face_can_be_asked_for_a_shadow_a_lean_and_no_puffs(
            self):
        source = (ROOT / "whisper_face_render.py").read_text(encoding="utf-8")
        view = next(
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ClassDef) and node.name == "LiveFaceView")
        self.assertLessEqual(
            {"setTilt_", "setBleed_", "setShadow_", "setWhispers_",
             "setPose_", "art_side"}, _method_names(view))
        draw = ast.unparse(next(
            node for node in view.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "drawRect_"))
        self.assertIn("shadow=self.shadow, whispers=self.whispers", draw)
        self.assertIn("rotateByDegrees_(self.tilt)", draw)
        self.assertIn("self.art_side()", draw)

    def test_home_and_setup_voiceover_copy_is_in_the_smoke_contract(self):
        keys = native_appkit_smoke_contract().accessibility_catalog_keys
        for key in ("key_cap.accessibility.role",
                    "home.stage.accessibility.label",
                    "home.attention.accessibility.label",
                    "onboarding.stage.accessibility.label",
                    "onboarding.rows.accessibility.label",
                    "onboarding.permission.open.label",
                    "onboarding.row.accessibility.status",
                    "onboarding.action.waiting.help"):
            self.assertIn(key, keys)
            self.assertIn(key, STRING_CATALOGS["en"])
        # What the phase label carried still has its VoiceOver label.
        self.assertIn("overview.accessibility.phase", keys)



@unittest.skipUnless(
    APPKIT_AVAILABLE,
    "needs AppKit: run with "
    "uv run --with pyobjc-framework-Cocoa --with pyobjc-framework-Quartz "
    "tests/test_whisper_face_gui.py BrandComponentAppKitTests")
class BrandComponentAppKitTests(unittest.TestCase):
    """The components themselves, drawn off screen. Never shows a window."""

    @classmethod
    def setUpClass(cls):
        import AppKit
        cls.AppKit = AppKit
        cls.app = AppKit.NSApplication.sharedApplication()
        cls.window = AppKit.NSWindow.alloc() \
            .initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(0, 0, 400, 300), 0,
                AppKit.NSBackingStoreBuffered, False)

    def setUp(self):
        self.set_appearance(dark=False)
        contrast = patch.object(
            gui_module, "_increase_contrast", lambda: False)
        contrast.start()
        self.addCleanup(contrast.stop)

    def set_appearance(self, *, dark):
        AppKit = self.AppKit
        appearance = AppKit.NSAppearance.appearanceNamed_(
            AppKit.NSAppearanceNameDarkAqua if dark
            else AppKit.NSAppearanceNameAqua)
        self.app.setAppearance_(appearance)
        self.window.setAppearance_(appearance)

    def show(self, view):
        """Put a view in the off-screen window and return its bitmap."""
        AppKit = self.AppKit
        root = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 400, 300))
        self.window.setContentView_(root)
        root.addSubview_(view)
        view.setNeedsDisplay_(True)
        bounds = view.bounds()
        rep = view.bitmapImageRepForCachingDisplayInRect_(bounds)
        view.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        srgb = rep.bitmapImageRepByConvertingToColorSpace_renderingIntent_(
            AppKit.NSColorSpace.sRGBColorSpace(), 0)
        # Converting drops the point size; ``pixel`` needs it back.
        srgb.setSize_(bounds.size)
        return srgb

    def pixel(self, rep, x, y):
        """sRGB colour at a point, measured in points from the top left."""
        scale = rep.pixelsWide() / rep.size().width
        color = rep.colorAtX_y_(int(x * scale), int(y * scale))
        return (color.redComponent(), color.greenComponent(),
                color.blueComponent())

    def assertColor(self, actual, expected, message=""):
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want, delta=0.02, msg=message)

    def frame(self, width=10, height=10):
        return self.AppKit.NSMakeRect(0, 0, width, height)

    def chunky(self, title, **kwargs):
        button = gui_module.ChunkyButton.alloc().initWithFrame_(
            self.frame()).configure(title, **kwargs)
        size = button.intrinsicContentSize()
        button.setFrame_(self.frame(size.width, size.height))
        return button

    def test_chunky_button_sizes_itself_around_its_title_and_shadow(self):
        from whisper_face_theme import BRAND_SHAPES

        regular = self.chunky("Pause")
        small = self.chunky("Pause", small=True)
        self.assertEqual(
            regular.frame().size.height,
            BRAND_SHAPES["chunky_button"].height
            + BRAND_SHAPES["chunky_button"].shadow_down)
        self.assertEqual(
            small.frame().size.height,
            BRAND_SHAPES["chunky_button_small"].height
            + BRAND_SHAPES["chunky_button_small"].shadow_down)
        self.assertLess(small.frame().size.width,
                        regular.frame().size.width)
        self.assertGreater(
            self.chunky("Copy & Dismiss").frame().size.width,
            regular.frame().size.width)
        # The hit target clears the 28pt a Mac control should offer.
        self.assertGreaterEqual(small.body_rect().size.height, 28.0)
        with self.assertRaises(ValueError):
            self.chunky("Pause", variant="rainbow")

    def test_chunky_button_is_a_labelled_button_with_a_focus_ring(self):
        button = self.chunky("Pause")
        self.assertEqual(str(button.accessibilityLabel()), "Pause")
        # VoiceOver reads the cell: it is the element, it is a button, and
        # it carries whatever label and help the button was given.
        element = self.AppKit.NSAccessibilityUnignoredDescendant(button)
        self.assertIs(element, button.cell())
        self.assertTrue(element.isAccessibilityElement())
        self.assertEqual(
            str(element.accessibilityRole()),
            str(self.AppKit.NSAccessibilityButtonRole))
        self.assertEqual(str(element.accessibilityLabel()), "Pause")
        gui_module._accessible(button, "Pause dictation", "Stops listening")
        self.assertEqual(
            str(element.accessibilityLabel()), "Pause dictation")
        self.assertEqual(str(element.accessibilityHelp()), "Stops listening")
        body = button.body_rect()
        ring = button.focusRingMaskBounds()
        self.assertEqual(
            (ring.origin.x, ring.origin.y, ring.size.width,
             ring.size.height),
            (body.origin.x, body.origin.y, body.size.width,
             body.size.height))
        # The ring hugs the body, not the shadow under it.
        self.assertLess(ring.size.height, button.bounds().size.height)
        self.assertNotEqual(
            int(button.focusRingType()),
            int(self.AppKit.NSFocusRingTypeNone))
        fired = []
        target = _ClickTarget.alloc().init()
        target.fired = fired
        button.setTarget_(target)
        button.setAction_("clicked:")
        button.performClick_(None)
        self.assertEqual(fired, ["clicked"])

    def test_chunky_button_draws_amber_and_plain_in_both_appearances(self):
        from whisper_face_theme import (
            DARK_PALETTE, LIGHT_PALETTE, STICKER_INK)

        from whisper_face_theme import color_token, contrast_ratio

        for dark, palette in ((False, LIGHT_PALETTE), (True, DARK_PALETTE)):
            self.set_appearance(dark=dark)
            for variant, fill in (("amber", palette.accent),
                                  ("plain", palette.surface)):
                with self.subTest(dark=dark, variant=variant):
                    button = self.chunky("M", variant=variant)
                    rep = self.show(button)
                    body = button.body_rect()
                    # Just inside the outline, clear of the title.
                    self.assertColor(
                        self.pixel(rep, body.origin.x + 6.0,
                                   body.size.height / 2.0),
                        fill, "fill")
                    # The outline, on the body's left edge. A fill that is
                    # dark in the dark appearance gets the light control
                    # edge, laid over that fill; everything else the ink.
                    outline = palette.line
                    if dark and variant == "plain":
                        outline = color_token("control_edge", True).over(
                            palette.surface)
                        self.assertGreaterEqual(
                            contrast_ratio(
                                self.pixel(rep, 1.0, body.size.height / 2.0),
                                palette.surface), 3.0)
                    self.assertColor(
                        self.pixel(rep, 1.0, body.size.height / 2.0),
                        outline, "outline")
                    # The hard shadow, under the body's bottom edge.
                    self.assertColor(
                        self.pixel(rep, body.size.width / 2.0,
                                   button.bounds().size.height - 1.0),
                        palette.line, "shadow")
        self.assertEqual(STICKER_INK, (42 / 255, 33 / 255, 28 / 255))

    def test_chunky_button_pressed_and_disabled_states_are_visible(self):
        from whisper_face_theme import BRAND_SHAPES, LIGHT_PALETTE

        shape = BRAND_SHAPES["chunky_button"]
        button = self.chunky("Pause")
        rest = button.body_rect()
        self.assertEqual((rest.origin.x, rest.origin.y), (0.0, 0.0))
        self.assertFalse(button.is_pressed())
        button.highlight_(True)
        self.assertTrue(button.is_pressed())
        pressed = button.body_rect()
        self.assertEqual(
            (pressed.origin.x, pressed.origin.y),
            (shape.shadow_right - 1.0, shape.shadow_down - 1.0))
        rep = self.show(button)
        # Where the body's top-left corner used to be is now the window.
        self.assertEqual(rep.colorAtX_y_(0, 0).alphaComponent(), 0.0)
        button.highlight_(False)

        button.setEnabled_(False)
        self.assertFalse(button.is_pressed())
        rep = self.show(button)
        # No shadow under a disabled button, and a faded body.
        height = button.bounds().size.height
        scale = rep.pixelsWide() / rep.size().width
        shadow = rep.colorAtX_y_(
            int(rest.size.width / 2.0 * scale), int((height - 1.0) * scale))
        self.assertEqual(shadow.alphaComponent(), 0.0)
        fill = rep.colorAtX_y_(
            int(6.0 * scale), int(rest.size.height / 2.0 * scale))
        self.assertLess(fill.alphaComponent(), 0.6)
        self.assertGreater(fill.alphaComponent(), 0.3)
        self.assertIsNotNone(LIGHT_PALETTE)

    def test_increase_contrast_gives_dark_fills_a_light_edge(self):
        from whisper_face_theme import DARK_PALETTE

        self.set_appearance(dark=True)
        with patch.object(gui_module, "_increase_contrast", lambda: True):
            plain = self.chunky("M", variant="plain")
            rep = self.show(plain)
            self.assertColor(
                self.pixel(rep, 1.0, plain.body_rect().size.height / 2.0),
                DARK_PALETTE.ink, "dark fill gets the light outline")
            amber = self.chunky("M")
            rep = self.show(amber)
            self.assertColor(
                self.pixel(rep, 1.0, amber.body_rect().size.height / 2.0),
                DARK_PALETTE.line, "bright fill keeps the dark outline")

    def test_key_cap_reads_as_a_named_key_and_fits_its_legend(self):
        from whisper_face_theme import BRAND_SHAPES, LIGHT_PALETTE

        cap = gui_module.KeyCapView.alloc().initWithFrame_(
            self.frame()).set_key("Right Option")
        self.assertEqual(str(cap.accessibilityLabel()), "Right Option")
        self.assertEqual(
            str(cap.accessibilityRole()),
            str(self.AppKit.NSAccessibilityStaticTextRole))
        # Read once: the name is the label, there is no value to repeat
        # it, and the role's words come from the string catalog.
        self.assertIsNone(cap.accessibilityValue())
        self.assertEqual(
            str(cap.accessibilityRoleDescription()),
            localized_string("key_cap.accessibility.role"))
        self.assertTrue(cap.isAccessibilityElement())
        self.assertFalse(cap.acceptsFirstResponder())
        shape = BRAND_SHAPES["key_cap"]
        size = cap.intrinsicContentSize()
        self.assertEqual(size.height, shape.height + shape.shadow_down)
        short = gui_module.KeyCapView.alloc().initWithFrame_(
            self.frame()).set_key("⌘")
        # A one-glyph key is at least as wide as it is tall.
        self.assertGreaterEqual(
            short.intrinsicContentSize().width, shape.height)
        self.assertLess(short.intrinsicContentSize().width, size.width)
        cap.setFrame_(self.frame(size.width, size.height))
        rep = self.show(cap)
        self.assertColor(
            self.pixel(rep, 3.0, shape.height / 2.0),
            LIGHT_PALETTE.surface, "cap fill")
        self.assertColor(
            self.pixel(rep, size.width / 2.0, size.height - 0.5),
            LIGHT_PALETTE.line, "cap shadow")

    def test_key_cap_has_a_light_edge_and_a_dark_shadow_in_the_dark(self):
        from whisper_face_theme import (
            BRAND_SHAPES, DARK_PALETTE, color_token, contrast_ratio)

        self.set_appearance(dark=True)
        cap = gui_module.KeyCapView.alloc().initWithFrame_(
            self.frame()).set_key("F13")
        size = cap.intrinsicContentSize()
        cap.setFrame_(self.frame(size.width, size.height))
        rep = self.show(cap)
        edge = self.pixel(rep, 0.5, BRAND_SHAPES["key_cap"].height / 2.0)
        self.assertColor(
            edge, color_token("control_edge", True).over(
                DARK_PALETTE.surface), "cap edge")
        self.assertGreaterEqual(
            contrast_ratio(edge, DARK_PALETTE.surface), 3.0)
        self.assertColor(
            self.pixel(rep, size.width / 2.0, size.height - 0.5),
            DARK_PALETTE.line, "cap shadow stays dark")
        with patch.object(gui_module, "_increase_contrast", lambda: True):
            rep = self.show(cap)
            self.assertColor(
                self.pixel(rep, 0.5, BRAND_SHAPES["key_cap"].height / 2.0),
                DARK_PALETTE.ink, "Increase Contrast keeps the whole ink")

    def test_sticker_tile_takes_its_tint_and_tilt_from_its_place(self):
        from whisper_face_theme import (
            STICKER_TILTS, STICKER_TINTS, color_token)

        for dark in (False, True):
            self.set_appearance(dark=dark)
            for index in range(4):
                with self.subTest(dark=dark, index=index):
                    tile = gui_module.StickerTileView.alloc() \
                        .initWithFrame_(self.frame(200, 10)).configure(
                            "714", "Words today", index)
                    place = index % 3
                    self.assertEqual(tile.tilt(), STICKER_TILTS[place])
                    height = tile.intrinsicContentSize().height
                    self.assertGreater(height, 60.0)
                    self.assertLess(height, 90.0)
                    tile.setFrame_(self.frame(200, height))
                    width, tall = tile.tile_size()
                    self.assertLess(width, 200.0)
                    self.assertLess(tall, height)
                    rep = self.show(tile)
                    # Right of the text, in the middle of the tile.
                    self.assertColor(
                        self.pixel(rep, 150.0, height / 2.0),
                        color_token(STICKER_TINTS[place], dark).color)
                    # Drawn tilted, not only sized for it: on a row level
                    # with the middle of the tile's top edge, the end the
                    # tilt lifts is inside the tile and the other is not.
                    lean = math.radians(tile.tilt())
                    reach = width / 2.0 - 20.0
                    row = height / 2.0 - tall / 2.0 * math.cos(lean)
                    scale = rep.pixelsWide() / rep.size().width
                    left, right = (
                        rep.colorAtX_y_(
                            int((100.0 + side * reach) * scale),
                            int(row * scale)).alphaComponent()
                        for side in (-1.0, 1.0))
                    lifted, dropped = (
                        (left, right) if tile.tilt() > 0.0
                        else (right, left))
                    self.assertGreater(lifted, 0.6)
                    self.assertLess(dropped, 0.4)
        self.assertEqual(str(tile.accessibilityLabel()), "Words today")
        self.assertEqual(str(tile.accessibilityValue()), "714")
        self.assertEqual(
            str(tile.accessibilityRole()),
            str(self.AppKit.NSAccessibilityStaticTextRole))

    def test_sticker_number_gets_smaller_before_it_gets_cut(self):
        from whisper_face_theme import TYPE_SPECS

        tile = gui_module.StickerTileView.alloc().initWithFrame_(
            self.frame(200, 76)).configure("714", "Words today", 1)
        full = TYPE_SPECS["stat_number"].size
        self.assertEqual(tile.number_font(160.0).pointSize(), full)
        tile.configure("0.93s · 9 words", "Last dictation", 0)
        fitted = tile.number_font(160.0)
        self.assertLess(fitted.pointSize(), full)
        self.assertGreaterEqual(
            fitted.pointSize(), tile.NUMBER_MIN_SIZE)
        self.assertLessEqual(
            gui_module._text_size(tile.number, fitted)[0], 160.0)
        tile.configure("an impossibly long value that cannot fit", "x", 0)
        self.assertEqual(
            tile.number_font(160.0).pointSize(), tile.NUMBER_MIN_SIZE)

    def test_stage_lays_out_a_leaning_face_and_its_content(self):
        from whisper_face_theme import (
            STAGE_FACE_TILT, STAGE_FOR_FACE, color_token, stage_layout)

        AppKit = self.AppKit
        stage = gui_module.StagePanelView.alloc().initWithFrame_(
            self.frame(380, 252))
        face = AppKit.NSView.alloc().initWithFrame_(self.frame())
        content = AppKit.NSView.alloc().initWithFrame_(self.frame())
        stage.set_face_view(face).set_content_view(content)
        expected_face, expected_content = stage_layout(380.0, 252.0)
        self.assertEqual(face.superview(), stage)
        self.assertEqual(
            (face.frame().size.width, face.frame().size.height),
            expected_face[2:])
        self.assertEqual(face.frameCenterRotation(), STAGE_FACE_TILT)
        got = content.frame()
        self.assertEqual(
            (got.origin.x, got.origin.y, got.size.width, got.size.height),
            expected_content)
        # Resizing the stage re-lays both.
        stage.setFrameSize_((330.0, 252.0))
        self.assertEqual(
            content.frame().size.width, stage_layout(330.0, 252.0)[1][2])
        # Replacing the face removes the old one.
        other = AppKit.NSView.alloc().initWithFrame_(self.frame())
        stage.set_face_view(other, 120.0)
        self.assertIsNone(face.superview())
        self.assertLessEqual(other.frame().size.width, 120.0)

        self.assertEqual(
            str(stage.accessibilityRole()),
            str(AppKit.NSAccessibilityGroupRole))
        gui_module._accessible(stage, "Fox, ready")
        self.assertEqual(str(stage.accessibilityLabel()), "Fox, ready")

        for dark in (False, True):
            self.set_appearance(dark=dark)
            for name in ("fox", "parrot"):
                with self.subTest(dark=dark, face=name):
                    plain = gui_module.StagePanelView.alloc() \
                        .initWithFrame_(self.frame(380, 252)).set_face(name)
                    rep = self.show(plain)
                    # Clear of all three circles and of the outline.
                    self.assertColor(
                        self.pixel(rep, 230.0, 60.0),
                        color_token(STAGE_FOR_FACE[name], dark).color)

    def test_stage_leaves_a_face_that_leans_itself_unrotated(self):
        from whisper_face_render import LiveFaceView
        from whisper_face_theme import (
            STAGE_FACE_BLEED, STAGE_FACE_SIZE, STAGE_FACE_TILT, stage_layout)

        stage = gui_module.StagePanelView.alloc().initWithFrame_(
            self.frame(500, 252))
        face = LiveFaceView.alloc().initWithFrame_(self.frame())
        stage.set_face_view(face)
        box = stage_layout(500.0, 252.0)[0]
        frame = face.frame()
        # The view is the face's box and clear room round it; the lean is
        # drawn inside, so the frame itself is never turned.
        self.assertEqual(face.frameCenterRotation(), 0.0)
        self.assertEqual(face.tilt, STAGE_FACE_TILT)
        self.assertEqual(face.bleed, STAGE_FACE_BLEED)
        self.assertEqual(face.art_side(), STAGE_FACE_SIZE)
        self.assertEqual(
            (frame.origin.x, frame.origin.y, frame.size.width),
            (box[0] - STAGE_FACE_BLEED, box[1] - STAGE_FACE_BLEED,
             STAGE_FACE_SIZE + 2.0 * STAGE_FACE_BLEED))
        # A spring re-anchors the layer at its middle. Laying the stage
        # out again, as every layout pass does, must leave it there: a
        # frame rotation put the anchor back in the corner mid-spring.
        self.show(stage)
        self.assertTrue(gui_module.add_jelly_motion(face, "wobble"))
        anchor = face.layer().anchorPoint()
        position = face.layer().position()
        self.assertEqual((anchor.x, anchor.y), (0.5, 0.5))
        stage.arrange()
        stage.layout()
        self.assertEqual(
            (face.layer().anchorPoint().x, face.layer().anchorPoint().y),
            (0.5, 0.5))
        self.assertEqual(
            (face.layer().position().x, face.layer().position().y),
            (position.x, position.y))
        self.assertIsNotNone(
            face.layer().animationForKey_("whisper-face-wobble-x"))
        # The lean and the hard shadow are in the drawing.
        face.layer().removeAllAnimations()
        leaning = self.show(face)
        face.setTilt_(0.0)
        upright = self.show(face)
        face.setTilt_(STAGE_FACE_TILT)
        face.setShadow_(True)
        shadowed = self.show(face)
        points = [(x, y) for x in range(12, 220, 16)
                  for y in range(12, 220, 16)]

        def rgba(rep, x, y):
            scale = rep.pixelsWide() / rep.size().width
            color = rep.colorAtX_y_(int(x * scale), int(y * scale))
            return (color.redComponent(), color.greenComponent(),
                    color.blueComponent(), color.alphaComponent())

        def differ(first, second):
            return sum(
                any(abs(a - b) > 0.05 for a, b in zip(
                    rgba(first, x, y), rgba(second, x, y)))
                for x, y in points)

        self.assertGreaterEqual(differ(leaning, upright), 6)
        self.assertGreaterEqual(differ(leaning, shadowed), 4)
        # Nothing reaches the edge of the view, shadow included.
        scale = shadowed.pixelsWide() / shadowed.size().width
        last = shadowed.pixelsWide() - 1
        for along in range(0, shadowed.pixelsWide(), 5):
            for across in (0, last):
                self.assertEqual(
                    shadowed.colorAtX_y_(along, across).alphaComponent(),
                    0.0)
                self.assertEqual(
                    shadowed.colorAtX_y_(across, along).alphaComponent(),
                    0.0)
        self.assertGreater(scale, 0.0)

    def test_face_chip_is_a_radio_button_that_shows_its_selection(self):
        from whisper_face_theme import LIGHT_PALETTE

        AppKit = self.AppKit
        chip = gui_module.FaceChipButton.alloc().initWithFrame_(
            self.frame(74, 75)).configure(None, "Fox")
        self.assertEqual(str(chip.accessibilityLabel()), "Fox")
        self.assertEqual(str(chip.toolTip()), "Fox")
        self.assertEqual(
            str(chip.accessibilityRole()),
            str(AppKit.NSAccessibilityRadioButtonRole))
        self.assertEqual(int(chip.accessibilityValue()), 0)
        # The cell is what VoiceOver reads, so it says the same.
        element = AppKit.NSAccessibilityUnignoredDescendant(chip)
        self.assertIs(element, chip.cell())
        self.assertEqual(
            str(element.accessibilityRole()),
            str(AppKit.NSAccessibilityRadioButtonRole))
        self.assertEqual(str(element.accessibilityLabel()), "Fox")
        self.assertEqual(int(element.accessibilityValue()), 0)
        rest = chip.body_rect()
        rep = self.show(chip)
        # Unselected: no outline, no shadow, a faint tile.
        self.assertEqual(
            rep.colorAtX_y_(
                rep.pixelsWide() - 1, rep.pixelsHigh() - 1).alphaComponent(),
            0.0)
        self.assertLess(
            rep.colorAtX_y_(rep.pixelsWide() // 2,
                            rep.pixelsHigh() // 2).alphaComponent(), 0.2)

        chip.set_selected(True)
        self.assertEqual(int(chip.accessibilityValue()), 1)
        self.assertEqual(int(element.accessibilityValue()), 1)
        selected = chip.body_rect()
        # Choosing a face never nudges the art.
        self.assertEqual(
            (rest.origin.x, rest.origin.y, rest.size.width,
             rest.size.height),
            (selected.origin.x, selected.origin.y, selected.size.width,
             selected.size.height))
        rep = self.show(chip)
        self.assertColor(
            self.pixel(rep, 36.0, 36.0), LIGHT_PALETTE.accent, "amber")
        self.assertColor(
            self.pixel(rep, 1.0, 36.0), LIGHT_PALETTE.line, "outline")
        self.assertColor(
            self.pixel(rep, 36.0, 74.0), LIGHT_PALETTE.line, "shadow")
        ring = chip.focusRingMaskBounds()
        self.assertEqual(
            (ring.size.width, ring.size.height),
            (selected.size.width, selected.size.height))
        # A small chip keeps a tile's corners instead of becoming a circle.
        small = gui_module.FaceChipButton.alloc().initWithFrame_(
            self.frame(46, 47))
        self.assertLess(small.corner_radius(), 44.0 / 2.0)
        self.assertLess(small.corner_radius(), chip.corner_radius())

    def test_role_fonts_follow_the_type_tokens(self):
        from whisper_face_theme import TYPE_SPECS

        for role, spec in TYPE_SPECS.items():
            with self.subTest(role=role):
                font = gui_module._role_font(role)
                self.assertEqual(font.pointSize(), spec.size)
        rounded = gui_module._role_font("page_title")
        plain = gui_module._role_font("row_title")
        self.assertNotEqual(
            str(rounded.fontDescriptor().postscriptName()),
            str(plain.fontDescriptor().postscriptName()))
        # Tabular figures: every digit takes the same room.
        stat = gui_module._role_font("stat_number")
        widths = {gui_module._text_size(digit * 4, stat)[0]
                  for digit in "0123456789"}
        self.assertEqual(len(widths), 1)


@unittest.skipUnless(
    APPKIT_AVAILABLE,
    "needs AppKit: run with "
    "uv run --with pyobjc-framework-Cocoa --with pyobjc-framework-Quartz "
    "tests/test_whisper_face_gui.py ShellAppKitTests")
class ShellAppKitTests(unittest.TestCase):
    """The shell in a real window that is never shown.

    The native smoke test asserts the structure in the release gate; these
    look closer at what it leaves out: pixels, key events and resizing.
    """

    @classmethod
    def setUpClass(cls):
        import AppKit
        cls.AppKit = AppKit
        cls.app = AppKit.NSApplication.sharedApplication()

    def setUp(self):
        contrast = patch.object(
            gui_module, "_increase_contrast", lambda: False)
        contrast.start()
        self.addCleanup(contrast.stop)
        self.model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {}, settings_snapshot=lambda: {}))
        self.model.state = replace(
            self.model.state, onboarding_acknowledged=True)
        self.controller = gui_module.WhisperFaceWindowController.alloc() \
            .initForSmokeWithViewModel_(self.model)
        self.window = self.controller.window
        self.addCleanup(self.close)
        self.set_appearance(dark=False)

    def close(self):
        self.window.setDelegate_(None)
        self.window.close()
        self.controller.pages.clear()
        self.controller.dynamic.clear()

    def set_appearance(self, *, dark):
        AppKit = self.AppKit
        appearance = AppKit.NSAppearance.appearanceNamed_(
            AppKit.NSAppearanceNameDarkAqua if dark
            else AppKit.NSAppearanceNameAqua)
        self.app.setAppearance_(appearance)
        self.window.setAppearance_(appearance)
        self.controller.render()

    def layout(self, width=None, height=None):
        if width is not None:
            self.window.setContentSize_((width, height))
        self.window.contentView().layoutSubtreeIfNeeded()

    def in_window(self, view):
        return view.convertRect_toView_(view.bounds(), None)

    def row_pixel(self, row, x, y):
        """Unpremultiplied RGBA at a point in the row, from its top left."""
        bounds = row.bounds()
        rep = row.bitmapImageRepForCachingDisplayInRect_(bounds)
        row.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        srgb = rep.bitmapImageRepByConvertingToColorSpace_renderingIntent_(
            self.AppKit.NSColorSpace.sRGBColorSpace(), 0)
        scale = rep.pixelsWide() / bounds.size.width
        color = srgb.colorAtX_y_(int(x * scale), int(y * scale))
        return (color.redComponent(), color.greenComponent(),
                color.blueComponent(), color.alphaComponent())

    def key_event(self, key_code):
        AppKit = self.AppKit
        return AppKit.NSEvent \
            .keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                AppKit.NSEventTypeKeyDown, (0.0, 0.0), 0, 0.0,
                self.window.windowNumber(), None, "", "", False, key_code)

    def test_selected_row_is_drawn_in_the_selection_colour(self):
        from whisper_face_theme import LIGHT_PALETTE, color_token

        rows = self.controller.sidebar_rows
        for dark in (False, True):
            self.set_appearance(dark=dark)
            shade = color_token("sidebar_selection", dark)
            red, green, blue, alpha = self.row_pixel(rows[0], 4, 16)
            with self.subTest(dark=dark):
                self.assertAlmostEqual(alpha, shade.alpha, delta=0.03)
                for got, want in zip((red, green, blue), shade.color):
                    self.assertAlmostEqual(got, want, delta=0.04)
                self.assertLess(self.row_pixel(rows[1], 4, 16)[3], 0.02)
                # The corner is rounded away.
                self.assertLess(self.row_pixel(rows[0], 0.5, 0.5)[3], 0.1)
        self.set_appearance(dark=False)
        self.controller.windowDidResignKey_(None)
        red, green, blue, alpha = self.row_pixel(rows[0], 4, 16)
        self.assertAlmostEqual(alpha, 0.10, delta=0.03)
        for got, want in zip((red, green, blue), LIGHT_PALETTE.ink):
            self.assertAlmostEqual(got, want, delta=0.12)
        self.controller.windowDidBecomeKey_(None)
        self.assertAlmostEqual(
            self.row_pixel(rows[0], 4, 16)[3], 0.38, delta=0.03)

    def test_increase_contrast_outlines_the_selected_row(self):
        from whisper_face_theme import LIGHT_PALETTE

        row = self.controller.sidebar_rows[0]
        self.assertLess(self.row_pixel(row, 60, 0.5)[3], 0.6)
        with patch.object(gui_module, "_increase_contrast", lambda: True):
            self.controller.render()
            red, green, blue, alpha = self.row_pixel(row, 60, 0.5)
        self.assertGreater(alpha, 0.9)
        for got, want in zip((red, green, blue), LIGHT_PALETTE.ink):
            self.assertAlmostEqual(got, want, delta=0.06)

    def test_arrow_keys_move_through_the_rows_and_stop_at_the_ends(self):
        rows = self.controller.sidebar_rows
        down, up = self.key_event(125), self.key_event(126)
        rows[0].keyDown_(up)
        self.assertEqual(self.model.state.section, "Home")
        expected = [("Settings", "Personalize"), ("Settings", "Controls"),
                    ("Settings", "Privacy"), ("Advanced", "Privacy")]
        for index, (section, pane) in enumerate(expected):
            rows[index].keyDown_(down)
            self.assertEqual(
                (self.model.state.section, self.model.state.settings_pane),
                (section, pane))
            self.assertTrue(rows[index + 1].selected)
            self.assertEqual(self.window.firstResponder(), rows[index + 1])
            # At most one Tab stop: the row the arrows landed on, when the
            # system lets buttons take Tab stops at all.
            buttons_take_tab = bool(self.controller.dynamic[
                "window_face_button"].canBecomeKeyView())
            self.assertEqual(
                [bool(row.canBecomeKeyView()) for row in rows],
                [buttons_take_tab and position == index + 1
                 for position in range(len(rows))])
            # The same rule with the system setting taken out of it.
            self.assertEqual(
                [row.takes_tab_stop(True) for row in rows],
                [position == index + 1 for position in range(len(rows))])
            self.assertFalse(any(row.takes_tab_stop(False) for row in rows))
            # Keyboard entry starts on that row too.
            self.assertEqual(
                self.window.initialFirstResponder(), rows[index + 1])
        rows[4].keyDown_(down)
        self.assertEqual(self.model.state.section, "Advanced")
        rows[4].keyDown_(up)
        self.assertEqual(
            (self.model.state.section, self.model.state.settings_pane),
            ("Settings", "Privacy"))

    def test_a_click_anywhere_in_a_row_reaches_the_row(self):
        row = self.controller.sidebar_rows[2]
        frame = row.frame()
        for offset in (4.0, 17.0, 60.0, frame.size.width - 4.0):
            hit = row.hitTest_(
                (frame.origin.x + offset, frame.origin.y + 16.0))
            self.assertEqual(hit, row)
        self.assertIsNone(row.hitTest_(
            (frame.origin.x + 20.0, frame.origin.y - 1.0)))
        row.performClick_(None)
        self.assertEqual(self.model.state.settings_pane, "Controls")
        self.assertEqual(self.model.state.section, "Settings")

    def test_focus_ring_is_the_rows_own_shape(self):
        row = self.controller.sidebar_rows[1]
        ring, bounds = row.focusRingMaskBounds(), row.bounds()
        self.assertEqual(
            (ring.origin.x, ring.origin.y, ring.size.width, ring.size.height),
            (bounds.origin.x, bounds.origin.y, bounds.size.width,
             bounds.size.height))
        self.assertTrue(row.acceptsFirstResponder())
        self.assertTrue(row.isFlipped())

    def test_the_divider_is_a_line_not_a_handle(self):
        split = self.controller.split_controller
        view = split.splitView()
        rect = self.AppKit.NSMakeRect(199, 0, 1, 680)
        grab = split.splitView_effectiveRect_forDrawnRect_ofDividerAtIndex_(
            view, rect, rect, 0)
        self.assertEqual((grab.size.width, grab.size.height), (0.0, 0.0))
        self.assertTrue(view.isVertical())
        for width in (880.0, 1000.0, 1400.0):
            self.layout(width, 700.0)
            sidebar = self.in_window(self.controller.sidebar)
            self.assertAlmostEqual(
                sidebar.size.width + view.dividerThickness(),
                gui_module.SIDEBAR_WIDTH, delta=0.5)
            self.assertAlmostEqual(sidebar.size.height, 700.0, delta=0.5)

    def test_the_column_keeps_its_range_and_its_insets_at_every_width(self):
        narrowest = gui_module.CONTENT_MIN_WIDTH
        widest = gui_module.CONTENT_WIDTH
        for window_width in (880.0, 904.0, 940.0, 968.0, 1000.0, 1400.0):
            # The column takes what the comfortable inset leaves, within
            # its range, and sits in the middle of the content side.
            side = window_width - gui_module.SIDEBAR_WIDTH
            column_width = min(widest, max(
                narrowest, side - 2.0 * gui_module.CONTENT_INSET))
            inset = (side - column_width) / 2.0
            with self.subTest(window=window_width):
                self.assertGreaterEqual(inset, gui_module.CONTENT_EDGE_MIN)
                self.layout(window_width, 680.0)
                column = self.in_window(self.controller.dynamic["page_column"])
                self.assertAlmostEqual(
                    column.size.width, column_width, delta=0.5)
                self.assertAlmostEqual(
                    column.origin.x - gui_module.SIDEBAR_WIDTH, inset,
                    delta=0.5)
                title = self.controller.dynamic["page_title"]
                self.assertAlmostEqual(
                    self.in_window(title).origin.x
                    + title.alignmentRectInsets().left,
                    column.origin.x, delta=0.5)

    def test_homes_actions_keep_their_places_at_every_width(self):
        # With the Voice Outbox holding text and a setup item needing
        # attention, Home shows every action it has: Pause and Copy &
        # Dismiss in the bubble, a button apart, and Review Setup in its
        # own row. All of it stays inside the column down to the minimum.
        self.model.state = replace(
            self.model.state, section="Home", outbox_count=1,
            outbox_summary="Paste may have landed",
            degraded_issues=(gui_module.DegradedIssue(
                "microphone", "Microphone unavailable",
                "Check the microphone"),))
        self.controller.render()
        dynamic = self.controller.dynamic
        bubble = dynamic["home_bubble"]
        for window_width in (880.0, 900.0, 920.0, 944.0, 1000.0, 1400.0):
            with self.subTest(window=window_width):
                self.layout(window_width, 648.0)
                column = self.in_window(dynamic["page_column"])
                self.assertFalse(dynamic["pause_button"].isHidden())
                self.assertFalse(dynamic["copy_outbox_button"].isHidden())
                self.assertTrue(dynamic["review_issue_button"].isHidden())
                self.assertFalse(dynamic["home_attention_row"].isHidden())
                pause = self.in_window(dynamic["pause_button"])
                copy = self.in_window(dynamic["copy_outbox_button"])
                review_button = dynamic["review_issue_row_button"]
                review = review_button.superview().convertRect_toView_(
                    review_button.alignmentRectForFrame_(
                        review_button.frame()), None)
                room = bubble.convertRect_toView_(bubble.content_rect(), None)
                self.assertAlmostEqual(
                    pause.origin.x, room.origin.x, delta=0.5)
                self.assertAlmostEqual(
                    copy.origin.x - pause.origin.x - pause.size.width,
                    gui_module.BUBBLE_BUTTON_GAP, delta=0.5)
                self.assertLessEqual(
                    copy.origin.x + copy.size.width,
                    room.origin.x + room.size.width + 0.5)
                self.assertAlmostEqual(
                    review.origin.x + review.size.width,
                    column.origin.x + column.size.width
                    - gui_module.ROW_INSET, delta=0.5)
                self.assertGreaterEqual(review.origin.x, column.origin.x)

    def test_the_sidebar_keeps_its_foot_and_its_top_as_the_window_grows(self):
        for height in (648.0, 680.0, 1000.0):
            with self.subTest(height=height):
                self.layout(1000.0, height)
                header = self.in_window(
                    self.controller.dynamic["window_header"])
                version = self.in_window(
                    self.controller.dynamic["window_version"])
                self.assertAlmostEqual(
                    height - header.origin.y - header.size.height,
                    gui_module.SIDEBAR_BRAND_TOP, delta=0.5)
                self.assertAlmostEqual(
                    version.origin.y, gui_module.SIDEBAR_FOOT_INSET,
                    delta=0.5)
                last_row = self.in_window(self.controller.sidebar_rows[-1])
                self.assertGreater(
                    last_row.origin.y,
                    version.origin.y + version.size.height + 24.0)

    def test_the_state_line_follows_the_runtime_and_setup(self):
        label = self.controller.dynamic["window_state"]
        for phase, (key, _tone) in SIDEBAR_STATES.items():
            with self.subTest(phase=phase):
                self.model.state = replace(
                    self.model.state, status_phase=phase)
                self.controller.render()
                self.assertEqual(
                    str(label.stringValue()), localized_string(key))
                self.assertEqual(
                    str(label.accessibilityLabel()),
                    localized_string("shell.state.accessibility.label"))
        self.model.state = replace(
            self.model.state, onboarding_acknowledged=False,
            onboarding_complete=False)
        self.controller.render()
        self.assertEqual(str(label.stringValue()), "Setting up")

    def test_setup_mode_round_trip_changes_nothing_else(self):
        self.layout(1000.0, 680.0)
        self.controller.sidebar_rows[3].performClick_(None)
        self.layout()
        tracked = ("page_column", "title_strip", "page_title", "notice",
                   "content_side")

        def frames():
            return {
                key: tuple(round(value, 1) for value in (
                    rect.origin.x, rect.origin.y,
                    rect.size.width, rect.size.height))
                for key in tracked
                for rect in [self.in_window(self.controller.dynamic[key])]}

        before = frames()
        self.controller.set_setup_mode(True)
        self.controller.set_setup_mode(True)       # idempotent
        self.layout()
        during = frames()
        self.assertEqual(during["content_side"], (0.0, 0.0, 1000.0, 680.0))
        self.assertEqual(during["page_column"][2], before["page_column"][2])
        self.assertEqual(
            during["page_column"][1] + during["page_column"][3], 680.0 - 28.0)
        self.assertTrue(self.controller.title_strip.isHidden())
        # The page shortcuts still work, and still move the selection.
        self.controller.sidebar_rows[4].performClick_(None)
        self.assertEqual(self.model.state.section, "Advanced")
        self.controller.sidebar_rows[3].performClick_(None)
        self.controller.set_setup_mode(False)
        self.layout()
        self.assertEqual(frames(), before)
        self.assertTrue(self.controller.sidebar_rows[3].selected)
        self.assertEqual(
            str(self.controller.dynamic["page_title"].stringValue()),
            "Privacy")

    def test_title_items_keep_their_order_and_follow_their_page(self):
        AppKit = self.AppKit

        def button(title):
            control = AppKit.NSButton.alloc().initWithFrame_(
                AppKit.NSMakeRect(0, 0, 10, 10))
            control.setTitle_(title)
            control.setBezelStyle_(AppKit.NSBezelStyleRounded)
            return control

        first, second, elsewhere = (
            button("First"), button("Second"), button("Elsewhere"))
        self.controller.add_title_item("home", first)
        self.controller.add_title_item(
            "home", second, visible=lambda state: state.outbox_count > 0)
        self.controller.add_title_item("advanced", elsewhere)
        self.controller.render()
        self.layout()
        self.assertFalse(first.isHidden())
        self.assertTrue(second.isHidden())
        self.assertTrue(elsewhere.isHidden())
        self.model.state = replace(self.model.state, outbox_count=2)
        self.controller.render()
        self.layout()
        self.assertFalse(second.isHidden())
        self.assertLess(
            self.in_window(first).origin.x, self.in_window(second).origin.x)
        column = self.in_window(self.controller.dynamic["page_column"])
        stack = self.in_window(self.controller.dynamic["title_strip_items"])
        self.assertAlmostEqual(
            stack.origin.x + stack.size.width,
            column.origin.x + column.size.width, delta=0.5)
        rows = self.controller.sidebar_rows
        self.assertEqual(rows[-1].nextKeyView(), first)
        self.assertEqual(first.nextKeyView(), second)
        rows[4].performClick_(None)
        self.assertTrue(first.isHidden())
        self.assertFalse(elsewhere.isHidden())
        self.assertEqual(rows[-1].nextKeyView(), elsewhere)


@unittest.skipUnless(
    APPKIT_AVAILABLE,
    "needs AppKit: run with "
    "uv run --with pyobjc-framework-Cocoa --with pyobjc-framework-Quartz "
    "tests/test_whisper_face_gui.py SettingsPagesAppKitTests")
class SettingsPagesAppKitTests(unittest.TestCase):
    """The pages' pieces in an off-screen window that is never shown."""

    @classmethod
    def setUpClass(cls):
        import AppKit
        cls.AppKit = AppKit
        cls.app = AppKit.NSApplication.sharedApplication()
        cls.window = AppKit.NSWindow.alloc() \
            .initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(0, 0, 800, 400), 0,
                AppKit.NSBackingStoreBuffered, False)

    def test_a_button_label_reaches_the_element_voiceover_reads(self):
        AppKit = self.AppKit
        button = gui_module._button(
            "Edit", AppKit.NSMakeRect(0, 0, 40, 28), None, "edit:",
            help_text="Edit the list")
        gui_module._accessible(button, "Edit vocabulary", "Opens it")
        self.window.contentView().addSubview_(button)
        element = AppKit.NSAccessibilityUnignoredDescendant(button)
        self.assertIs(element, button.cell())
        self.assertEqual(str(element.accessibilityLabel()), "Edit vocabulary")
        self.assertEqual(str(element.accessibilityHelp()), "Opens it")
        # The system's bezel and size: never narrower than its title.
        self.assertEqual(int(button.bezelStyle()),
                         int(AppKit.NSBezelStyleRounded))
        self.assertGreaterEqual(
            button.frame().size.width, button.fittingSize().width)

    def test_the_face_grid_fills_its_width_in_rows_of_eight(self):
        AppKit = self.AppKit
        picker = gui_module.ArtFacePicker.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 604, 140))
        picker.populate(FACES, [None] * len(FACES),
                        [face.title() for face in FACES], None, None)
        shape = gui_module.BRAND_SHAPES["face_chip"]
        # The grid's width is the group between its insets plus the room
        # the last chip's hard shadow takes.
        for width, side in ((604.0 + shape.shadow_right, 66.0),
                            (692.0 + shape.shadow_right, 77.0)):
            with self.subTest(width=width):
                columns, chip, margin, rows = picker.grid(width)
                self.assertEqual((columns, rows, chip), (8, 2, side))
                picker.setFrameSize_((width, picker.height_for_width(width)))
                picker.arrange()
                frames = [view.frame() for view in picker.chips]
                first, second, last = frames[0], frames[1], frames[-1]
                self.assertEqual(
                    (first.size.width, first.size.height),
                    (side + shape.shadow_right, side + shape.shadow_down))
                # Bodies are exactly the spec's gap apart, across and down.
                self.assertEqual(
                    second.origin.x - first.origin.x - side,
                    gui_module.FACE_CHIP_GAP)
                self.assertEqual(
                    frames[8].origin.y - first.origin.y - side,
                    gui_module.FACE_CHIP_GAP)
                # The bodies fill the width, the same margin either side,
                # and the last chip's shadow stays inside the grid.
                self.assertEqual(first.origin.x, margin)
                self.assertLessEqual(margin, 3.5)
                self.assertAlmostEqual(
                    width - shape.shadow_right
                    - (last.origin.x + side), margin, delta=1.0)
                self.assertLessEqual(
                    last.origin.x + last.size.width, width + 0.01)
                self.assertAlmostEqual(
                    last.origin.y + last.size.height,
                    picker.height_for_width(width), delta=0.5)
        picker.setSelectedSegment_(3)
        self.assertEqual(
            [chip.selected for chip in picker.chips].index(True), 3)
        self.assertEqual(picker.selectedSegment(), 3)

    def test_a_row_wraps_its_caption_beside_its_controls(self):
        AppKit = self.AppKit
        title = gui_module._label("Capture key", AppKit.NSMakeRect(
            0, 0, 100, 16), wrap=True)
        caption = gui_module._label(
            "Hold Right Control to dictate · Reply, Command, Code need "
            "the other Control key, which is a long line on purpose",
            AppKit.NSMakeRect(0, 0, 100, 15), size=12, wrap=True)
        button = gui_module._button(
            "Change…", AppKit.NSMakeRect(0, 0, 40, 28), None, "change:")
        row = gui_module.SettingsRowView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 632, 50)).configure(
                title, caption, [button])
        narrow = row.arrange(420.0)
        wide = row.arrange(720.0)
        self.assertGreaterEqual(wide, gui_module.ROW_CAPTION_MIN_HEIGHT)
        self.assertGreater(narrow, wide)
        row.arrange(632.0)
        text_right = caption.frame().origin.x + caption.frame().size.width
        self.assertLessEqual(
            text_right + gui_module.ROW_CONTROL_GAP,
            button.alignmentRectForFrame_(button.frame()).origin.x + 0.5)
        needed = caption.cell().cellSizeForBounds_(AppKit.NSMakeRect(
            0, 0, caption.frame().size.width, 10_000.0))
        self.assertLessEqual(needed.height, caption.frame().size.height)
        # A hidden control gives its room back to the text.
        button.setHidden_(True)
        self.assertGreater(row.text_width(632.0), text_right)

    def test_a_status_shows_its_whole_phrase_and_tone(self):
        AppKit = self.AppKit
        view = gui_module.StatusLabelView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 10, 10))
        ink = gui_module._srgb_color((0.2, 0.2, 0.2))
        view.set_status("Needs repair", "warn", symbol_color=ink,
                        text_color=ink)
        size = view.intrinsicContentSize()
        view.setFrame_(AppKit.NSMakeRect(0, 0, size.width, size.height))
        text = view.text
        needed = text.cell().cellSizeForBounds_(
            AppKit.NSMakeRect(0, 0, 10_000.0, 10_000.0))
        self.assertGreaterEqual(text.frame().size.width, needed.width)
        self.assertIsNotNone(view.icon.image())
        self.assertEqual(view.tone, "warn")
        view.set_status("Ready", "nonsense", symbol_color=ink,
                        text_color=ink)
        self.assertEqual(view.tone, "neutral")


@unittest.skipUnless(
    APPKIT_AVAILABLE,
    "needs AppKit: run with "
    "uv run --with pyobjc-framework-Cocoa --with pyobjc-framework-Quartz "
    "tests/test_whisper_face_gui.py HomeAppKitTests")
class HomeAppKitTests(unittest.TestCase):
    """Home and first-run setup in a real window that is never shown.

    The native smoke test asserts them in the release gate; these go
    through more states, more widths and both appearances.
    """

    ISSUE = gui_module.DegradedIssue(
        "microphone", localized_string("issue.microphone.title"),
        localized_string("issue.microphone.detail"))
    SETUP = {
        "service_status": "Running", "microphone_status": "Starting",
        "accessibility_status": "Needs attention",
        "input_monitoring_status": "Granted", "capture_state": "Ready",
        "hotkey_label": "Right Command",
        "models": [{**PREPARING_MODEL}],
    }

    @classmethod
    def setUpClass(cls):
        import AppKit
        cls.AppKit = AppKit
        cls.app = AppKit.NSApplication.sharedApplication()

    def setUp(self):
        contrast = patch.object(
            gui_module, "_increase_contrast", lambda: False)
        contrast.start()
        self.addCleanup(contrast.stop)
        self.opened = []
        self.runtime = {
            "service_status": "Running", "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "input_monitoring_status": "Granted", "capture_state": "Ready",
            "hotkey_label": "Right Option", "models": [READY_MODEL],
            "hotkey_practiced": True, "last_word_count": 9,
            "last_latency_ms": 930.0,
        }
        self.model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: dict(self.runtime),
            settings_snapshot=lambda: {},
            open_system_settings=self.opened.append))
        self.model.acknowledge_onboarding()
        self.controller = gui_module.WhisperFaceWindowController.alloc() \
            .initForSmokeWithViewModel_(self.model)
        self.window = self.controller.window
        self.addCleanup(self.close)
        self.set_appearance(dark=False)

    def close(self):
        self.window.setDelegate_(None)
        self.window.close()
        self.controller.pages.clear()
        self.controller.dynamic.clear()

    def set_appearance(self, *, dark):
        AppKit = self.AppKit
        appearance = AppKit.NSAppearance.appearanceNamed_(
            AppKit.NSAppearanceNameDarkAqua if dark
            else AppKit.NSAppearanceNameAqua)
        self.app.setAppearance_(appearance)
        self.window.setAppearance_(appearance)
        self.controller.render()

    def show(self, **changes):
        self.model.state = replace(self.model.state, **changes)
        self.controller.render()
        self.window.contentView().layoutSubtreeIfNeeded()

    def resize(self, width, height=648.0):
        self.window.setContentSize_((width, height))
        self.window.contentView().layoutSubtreeIfNeeded()

    def in_window(self, view):
        return view.convertRect_toView_(view.bounds(), None)

    def leaves(self, view):
        AppKit = self.AppKit
        inside = view
        if (view.isKindOfClass_(AppKit.NSButton)
                or view.isKindOfClass_(AppKit.NSTextField)):
            inside = None
        children = [] if inside is None else [
            child for child in inside.subviews() if not child.isHidden()]
        if not children:
            return [view]
        return [leaf for child in children for leaf in self.leaves(child)]

    def assert_laid_out(self, container, over=()):
        """Nothing cut short, outside its page, or on top of another."""
        AppKit = self.AppKit
        self.window.contentView().layoutSubtreeIfNeeded()
        bounds = self.in_window(container)
        placed = []
        for leaf in self.leaves(container):
            # Where a control is, is its alignment rectangle: a system
            # button's frame is wider than the bezel it draws.
            rect = leaf.superview().convertRect_toView_(
                leaf.alignmentRectForFrame_(leaf.frame()), None)
            if leaf.isKindOfClass_(AppKit.NSTextField):
                size = leaf.frame().size
                if leaf.cell().wraps():
                    needed = leaf.cell().cellSizeForBounds_(
                        AppKit.NSMakeRect(0, 0, size.width, 10_000.0)).height
                    self.assertLessEqual(
                        needed, size.height + 1.0, str(leaf.stringValue()))
                else:
                    self.assertLessEqual(
                        leaf.cell().cellSize().width, size.width + 1.0,
                        str(leaf.stringValue()))
            self.assertGreaterEqual(rect.origin.x, bounds.origin.x - 0.5)
            self.assertGreaterEqual(rect.origin.y, bounds.origin.y - 0.5)
            self.assertLessEqual(
                rect.origin.x + rect.size.width,
                bounds.origin.x + bounds.size.width + 0.5)
            self.assertLessEqual(
                rect.origin.y + rect.size.height,
                bounds.origin.y + bounds.size.height + 0.5)
            if rect.size.width and rect.size.height and leaf not in over:
                placed.append((leaf, rect))
        for index, (first_view, first) in enumerate(placed):
            for second_view, second in placed[index + 1:]:
                self.assertFalse(
                    first.origin.x + 0.5 < second.origin.x + second.size.width
                    and second.origin.x + 0.5
                    < first.origin.x + first.size.width
                    and first.origin.y + 0.5
                    < second.origin.y + second.size.height
                    and second.origin.y + 0.5
                    < first.origin.y + first.size.height,
                    f"{first_view} overlaps {second_view}")

    def phase(self, name, **changes):
        detail = changes.pop("status_detail", None)
        if detail is None:
            detail = localized_string(
                f"overview.status.{name}.detail",
                hotkey=changes.get("hotkey_label", "Right Option"))
        return {"section": "Home", "status_phase": name,
                "status_title": localized_string(
                    f"overview.status.{name}.title"),
                "status_detail": detail, **changes}

    def home_states(self):
        outbox = {"outbox_count": 3, "outbox_summary": localized_string(
            "overview.outbox.summary.paste_attempted")}
        recovery = localized_string(
            "overview.status.recovery.detail.many", count=3)
        return {
            "ready": self.phase("ready"),
            "ready, longest key": self.phase(
                "ready", hotkey_label="Right Command"),
            "paused": self.phase("paused", paused=True),
            "listening": self.phase("recording"),
            "listening, longest key": self.phase(
                "recording", hotkey_label="Right Command"),
            "working": self.phase("processing"),
            "starting": self.phase("starting"),
            "outbox": self.phase(
                "recovery", status_detail=recovery, **outbox),
            "outbox, unchecked copies only": self.phase(
                "ready", outbox_count=20, outbox_unchecked=20,
                outbox_summary=localized_string(
                    "overview.outbox.summary.pasted_unchecked")),
            # The longest row there is: both kinds, two-digit counts and
            # the longer of the two summaries.
            "outbox, one of twenty needs a home": self.phase(
                "recovery", status_detail=localized_string(
                    "overview.status.recovery.detail.one"),
                outbox_count=20, outbox_unchecked=19,
                outbox_summary=localized_string(
                    "overview.outbox.summary.paste_attempted")),
            "needs attention": self.phase(
                "degraded", status_detail=self.ISSUE.detail,
                degraded_issues=(self.ISSUE,)),
            "outbox and attention": self.phase(
                "recovery", status_detail=recovery,
                degraded_issues=(self.ISSUE,), **outbox),
            "replay on, nothing held": {**self.phase("ready"), "last_result":
                replace(self.model.state.last_result,
                        acoustic_replay_enabled=True)},
            "no dictation yet": {
                **self.phase("ready"), "last_latency_ms": None,
                "last_word_count": None, "words_today": 0,
                "last_result": gui_module.ResultInspection()},
            "large numbers": {
                **self.phase("ready"), "words_today": 1234567,
                "minutes_saved": 98765.0, "last_latency_ms": 12345.0,
                "last_word_count": 1234},
        }

    def test_every_home_state_fits_the_columns_narrowest_and_widest(self):
        dynamic = self.controller.dynamic
        base = self.model.state
        for window_width in (880.0, 1000.0):
            self.resize(window_width)
            column = self.in_window(dynamic["page_column"]).size.width
            self.assertEqual(
                column, 632.0 if window_width == 880.0 else 720.0)
            for name, changes in self.home_states().items():
                with self.subTest(column=column, state=name):
                    self.model.state = base
                    self.show(**changes)
                    self.assert_laid_out(dynamic["home_cluster"])
                    # The whole instruction is showing, not a cut of it.
                    self.assertEqual(
                        dynamic["overview_detail"].maximumNumberOfLines(), 0)
                    self.assertIsNone(dynamic["overview_detail"].toolTip())
                    # The bubble stays on the stage, clear of its edge.
                    stage = self.in_window(dynamic["home_stage"])
                    bubble = self.in_window(dynamic["home_bubble"])
                    self.assertGreaterEqual(
                        bubble.origin.y, stage.origin.y + 6.0 + 16.0)
                    self.assertLessEqual(
                        bubble.origin.y + bubble.size.height,
                        stage.origin.y + stage.size.height - 16.0)
                    # At most the primary action and one secondary.
                    shown = [key for key in (
                        "pause_button", "review_issue_button",
                        "copy_outbox_button")
                        if not dynamic[key].isHidden()]
                    self.assertLessEqual(len(shown), 2)
                    self.assertEqual(shown[0], "pause_button")

    def test_unchecked_copies_are_counted_on_a_calm_home(self):
        dynamic = self.controller.dynamic
        # Five dictations into an app that hides its text, as the runtime
        # reports them.
        self.runtime.update({
            "outbox_count": 5, "outbox_needs_home": 0,
            "outbox_summary": "Pasted, not double-checked"})
        self.model.refresh()
        self.controller.render()
        ready = localized_string("shell.state.ready")
        row = "Voice Outbox: 5 pasted, not double-checked"

        self.assertEqual(self.model.state.status_phase, "ready")
        self.assertEqual(str(dynamic["window_state"].stringValue()), ready)
        self.assertEqual(
            str(dynamic["overview_status"].stringValue()),
            localized_string("overview.status.ready.title"))
        self.assertEqual(str(dynamic["overview_outbox"].stringValue()), row)
        self.assertEqual(
            str(dynamic["overview_outbox"].accessibilityValue()), row)
        # The copy is still one click away, and nothing asks for a review.
        self.assertFalse(dynamic["copy_outbox_button"].isHidden())
        self.assertTrue(dynamic["review_issue_button"].isHidden())
        self.assertTrue(dynamic["home_attention_row"].isHidden())
        # The dot is the one a Mac with an empty outbox shows.
        calm_dot = dynamic["window_state_dot"].layer().backgroundColor()
        self.runtime.update({"outbox_count": 0, "outbox_needs_home": 0,
                             "outbox_summary": ""})
        self.model.refresh()
        self.controller.render()
        empty_dot = dynamic["window_state_dot"].layer().backgroundColor()
        self.assertEqual(str(calm_dot), str(empty_dot))

        # One of them needs a home: today's treatment, in full.
        self.runtime.update({
            "outbox_count": 5, "outbox_needs_home": 1,
            "outbox_summary": "Not pasted — destination changed"})
        self.model.refresh()
        self.controller.render()
        self.assertEqual(self.model.state.status_phase, "recovery")
        self.assertEqual(
            str(dynamic["window_state"].stringValue()),
            localized_string("shell.state.recovery"))
        self.assertEqual(
            str(dynamic["overview_outbox"].stringValue()),
            "Voice Outbox: 1 recoverable, 4 pasted · "
            "Not pasted — destination changed")
        # The headline asks for the same one dictation the row counts.
        self.assertEqual(
            str(dynamic["overview_detail"].stringValue()),
            localized_string("overview.status.recovery.detail.one"))
        self.assertNotEqual(
            str(dynamic["window_state_dot"].layer().backgroundColor()),
            str(empty_dot))

    def test_the_headline_steps_down_before_it_wraps(self):
        from whisper_face_theme import TYPE_SPECS

        status = self.controller.dynamic["overview_status"]
        full = TYPE_SPECS["bubble_headline"].size
        self.resize(1000.0)
        self.show(**self.phase("ready"))
        self.assertEqual(status.font().pointSize(), full)
        self.resize(880.0)
        self.show(**self.phase("processing"))
        self.assertEqual(
            status.font().pointSize(), gui_module.BUBBLE_HEADLINE_SMALL)
        self.show(**self.phase("ready"))
        self.assertEqual(status.font().pointSize(), full)

    def test_the_instruction_shows_whichever_key_is_bound(self):
        detail = self.controller.dynamic["overview_detail"]
        for key in ("Right Option", "Right Command", "F13", "Fn"):
            for name in ("ready", "recording"):
                with self.subTest(key=key, phase=name):
                    self.show(**self.phase(name, hotkey_label=key))
                    plain = str(detail.attributedStringValue().string())
                    self.assertEqual(plain.count("\ufffc"), 1)
                    self.assertNotIn(key, plain)
                    self.assertEqual(
                        plain.replace("\ufffc", key),
                        self.model.state.status_detail)
                    attachment, _range = detail.attributedStringValue() \
                        .attribute_atIndex_effectiveRange_(
                            "NSAttachment", plain.index("\ufffc"), None)
                    self.assertEqual(
                        (attachment.bounds().size.width,
                         attachment.bounds().size.height),
                        gui_module.key_cap_size(key))
                    # VoiceOver reads the cell, and hears the key's name.
                    self.assertEqual(
                        str(detail.cell().accessibilityValue()),
                        self.model.state.status_detail)
        # Copy that names no key has no cap.
        self.show(**self.phase("paused", paused=True))
        self.assertNotIn(
            "\ufffc", str(detail.attributedStringValue().string()))

    def test_setup_rows_show_each_permissions_state_and_open_its_pane(self):
        self.runtime.clear()
        self.runtime.update(self.SETUP)
        self.model.acknowledge_onboarding(False)
        self.model.refresh()
        self.show(section="Home")
        dynamic = self.controller.dynamic
        self.assertTrue(self.controller.setup_mode)
        checking, missing, granted, unused = dynamic["onboarding_rows"]
        self.assertTrue(unused["view"].isHidden())
        # Still being checked: its state in words, in the neutral tone.
        self.assertTrue(checking["button"].isHidden())
        self.assertFalse(checking["state"].isHidden())
        self.assertEqual(checking["state"].tone, "neutral")
        self.assertEqual(str(checking["status"].stringValue()), "Checking")
        # Missing: its own button, and it opens that pane. It is the
        # first missing permission, so it is the step's primary action:
        # the one amber button, and the one that answers Return.
        self.assertFalse(missing["button"].isHidden())
        self.assertTrue(missing["state"].isHidden())
        self.assertEqual(missing["button"].variant, "amber")
        self.assertEqual(str(missing["button"].keyEquivalent()), "\r")
        self.assertEqual(
            str(missing["button"].title()), "Open System Settings")
        self.assertEqual(
            self.window.initialFirstResponder(), missing["button"])
        missing["button"].performClick_(None)
        self.assertEqual(self.opened, ["accessibility"])
        # The foot's Continue waits: plain, disabled, not on Return.
        action = dynamic["onboarding_action"]
        self.assertEqual(str(action.title()), "Keep Going")
        self.assertEqual(action.variant, "plain")
        self.assertFalse(action.isEnabled())
        self.assertEqual(str(action.keyEquivalent()), "")
        self.assertEqual(
            str(action.cell().accessibilityHelp()),
            localized_string("onboarding.action.waiting.help"))
        # Granted: the check mark and the word.
        self.assertTrue(granted["button"].isHidden())
        self.assertFalse(granted["state"].isHidden())
        self.assertEqual(granted["state"].tone, "good")
        self.assertEqual(str(granted["status"].stringValue()), "Allowed")
        self.assertIsNotNone(granted["check"].image())
        for width in (880.0, 1000.0):
            self.resize(width)
            self.assert_laid_out(
                dynamic["onboarding_card"],
                over=(dynamic["onboarding_progress_fill"],))
        # The check mark appears on the next refresh after the grant.
        self.runtime["accessibility_status"] = "Granted"
        self.model.refresh()
        self.show()
        self.assertFalse(missing["state"].isHidden())
        self.assertEqual(missing["state"].tone, "good")
        self.assertTrue(missing["button"].isHidden())
        self.assertEqual(str(missing["button"].keyEquivalent()), "")
        # Nothing is missing any more, so the step has moved on and the
        # foot's button is the way forward again.
        self.assertTrue(action.isEnabled())
        self.assertEqual(action.variant, "amber")
        self.assertEqual(str(action.keyEquivalent()), "\r")

    def test_several_missing_permissions_share_one_primary_action(self):
        self.runtime.clear()
        self.runtime.update({
            **self.SETUP, "microphone_status": "Unavailable",
            "input_monitoring_status": "Needs attention"})
        self.model.acknowledge_onboarding(False)
        self.model.refresh()
        self.show(section="Home")
        rows = self.controller.dynamic["onboarding_rows"][:3]
        self.assertEqual(
            [row["button"].variant for row in rows],
            ["amber", "plain", "plain"])
        self.assertEqual(
            [str(row["button"].keyEquivalent()) for row in rows],
            ["\r", "", ""])
        for row, permission in zip(rows, gui_module.PERMISSIONS):
            row["button"].performClick_(None)
            self.assertEqual(self.opened[-1], permission)
        # Granting the first hands the primary action to the next.
        self.runtime["microphone_status"] = "Ready"
        self.model.refresh()
        self.show()
        self.assertEqual(
            [row["button"].variant for row in rows[1:]], ["amber", "plain"])
        self.assertTrue(rows[0]["button"].isHidden())
        self.assertFalse(rows[0]["check"].isHidden())
        self.assertEqual(
            self.window.initialFirstResponder(), rows[1]["button"])

    def test_the_setup_bubble_shows_the_bound_key_as_a_key_cap(self):
        granted = {
            **self.SETUP, "microphone_status": "Ready",
            "accessibility_status": "Granted", "hotkey_label": "F13"}
        self.runtime.clear()
        self.runtime.update(granted)
        self.model.acknowledge_onboarding(False)
        self.model._hotkey_practiced = False
        self.model.refresh()
        self.show(section="Home")
        detail = self.controller.dynamic["onboarding_detail"]
        plain = str(detail.attributedStringValue().string())
        self.assertEqual(plain.count("\ufffc"), 1)
        self.assertNotIn("F13", plain)
        whole = localized_string("onboarding.hotkey.detail", hotkey="F13")
        self.assertEqual(plain.replace("\ufffc", "F13"), whole)
        # VoiceOver reads the cell, and hears the key's name.
        self.assertEqual(str(detail.cell().accessibilityValue()), whole)

    def test_every_setup_step_fits_and_shows_the_key_that_is_bound(self):
        dynamic = self.controller.dynamic
        granted = {
            **self.SETUP, "microphone_status": "Ready",
            "accessibility_status": "Granted"}
        steps = (
            ("hotkey", granted, 1),
            ("models", {**granted, "hotkey_practiced": True}, 1),
            ("first_dictation", {
                **granted, "hotkey_practiced": True,
                "models": [READY_MODEL]}, 1),
            (None, {**granted, "hotkey_practiced": True,
                    "models": [READY_MODEL], "last_word_count": 3}, 4),
        )
        self.model.acknowledge_onboarding(False)
        # The model remembers a key it has seen held; this walk starts
        # from a Mac where it has not been yet.
        self.model._hotkey_practiced = False
        for current, runtime, rows in steps:
            self.runtime.clear()
            self.runtime.update(runtime)
            self.model.refresh()
            for width in (880.0, 1000.0):
                with self.subTest(step=current, window=width):
                    self.resize(width)
                    self.show(section="Home")
                    self.assertTrue(self.controller.setup_mode)
                    shown = [row for row in dynamic["onboarding_rows"]
                             if not row["view"].isHidden()]
                    self.assertEqual(len(shown), rows)
                    self.assert_laid_out(
                        dynamic["onboarding_card"],
                        over=(dynamic["onboarding_progress_fill"],))
                    caps = [row["cap"].key for row in shown
                            if not row["cap"].isHidden()]
                    if current in ("hotkey", "first_dictation", None):
                        self.assertEqual(caps, ["Right Command"])
                    # The face opens its mouth where the copy asks the
                    # person to speak, and for the celebration.
                    self.assertEqual(
                        dynamic["onboarding_face"].pose,
                        "talk" if current in ("first_dictation", None)
                        else "idle")
                    # The route is there until the rows say it all.
                    self.assertEqual(
                        [marker.isHidden() for marker in
                         dynamic["onboarding_step_cards"]],
                        [current is None] * 4)
        # Finishing leaves setup mode and shows Home.
        self.controller.continueSetup_(None)
        self.assertFalse(self.controller.setup_mode)
        self.assertTrue(dynamic["onboarding_card"].isHidden())
        self.assertFalse(dynamic["home_cluster"].isHidden())

    def test_setup_mode_follows_what_home_is_showing(self):
        self.runtime.clear()
        self.runtime.update(self.SETUP)
        self.model.acknowledge_onboarding(False)
        self.model.refresh()
        self.show(section="Home")
        self.assertTrue(self.controller.setup_mode)
        self.assertTrue(self.controller.sidebar_item.isCollapsed())
        # A page shortcut shows that page with the shell back around it.
        self.controller._select_sidebar_row(2)
        self.assertFalse(self.controller.setup_mode)
        self.assertFalse(self.controller.sidebar_item.isCollapsed())
        self.assertFalse(self.controller.title_strip.isHidden())
        # Coming Home returns to setup.
        self.controller._select_sidebar_row(0)
        self.assertTrue(self.controller.setup_mode)

    def test_the_bubble_and_the_stage_follow_the_appearance(self):
        from whisper_face_theme import (
            DARK_PALETTE, LIGHT_PALETTE, color_token, stage_token_for_face)

        dynamic = self.controller.dynamic
        self.resize(1000.0, 680.0)
        for face in ("parrot", "fox"):
            for dark, palette in (
                    (False, LIGHT_PALETTE), (True, DARK_PALETTE)):
                with self.subTest(face=face, dark=dark):
                    self.show(face=face, **self.phase("ready"))
                    self.set_appearance(dark=dark)
                    self.window.contentView().layoutSubtreeIfNeeded()
                    bubble = dynamic["home_bubble"]
                    bounds = bubble.bounds()
                    rep = bubble.bitmapImageRepForCachingDisplayInRect_(
                        bounds)
                    bubble.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
                    rep = rep \
                        .bitmapImageRepByConvertingToColorSpace_renderingIntent_(
                            self.AppKit.NSColorSpace.sRGBColorSpace(), 0)
                    scale = rep.pixelsWide() / bounds.size.width
                    body = bubble.body_rect()

                    def at(x, y):
                        color = rep.colorAtX_y_(
                            int(x * scale), int(y * scale))
                        return (color.redComponent(), color.greenComponent(),
                                color.blueComponent())

                    low = body.origin.y + body.size.height
                    for got, want in zip(
                            at(body.origin.x + 8.0, low - 34.0),
                            palette.surface):
                        self.assertAlmostEqual(got, want, delta=0.03)
                    # Not a control: the bubble keeps the dark ink.
                    for got, want in zip(
                            at(body.origin.x + 0.75, low - 34.0),
                            palette.line):
                        self.assertAlmostEqual(got, want, delta=0.03)
                    self.assertEqual(
                        dynamic["home_stage"].stage_token,
                        stage_token_for_face(face))
                    self.assertIsNotNone(color_token(
                        dynamic["home_stage"].stage_token, dark))
        self.set_appearance(dark=False)


@unittest.skipUnless(
    APPKIT_AVAILABLE,
    "needs AppKit: run with "
    "uv run --with pyobjc-framework-Cocoa --with pyobjc-framework-Quartz "
    "tests/test_whisper_face_gui.py EditorSheetAppKitTests")
class EditorSheetAppKitTests(unittest.TestCase):
    """The Personalize editors as real sheets that are never shown.

    The native smoke test measures their look in the release gate; these
    go through more of what each one does, one behaviour to a test.
    """

    @classmethod
    def setUpClass(cls):
        import AppKit
        cls.AppKit = AppKit
        cls.app = AppKit.NSApplication.sharedApplication()

    def setUp(self):
        contrast = patch.object(
            gui_module, "_increase_contrast", lambda: False)
        contrast.start()
        self.addCleanup(contrast.stop)
        self.calls = []
        self.fail_tone_for = None
        self.settings = {
            "app_tones": [
                {"bundle": "com.apple.finder", "name": "Finder",
                 "tone": "formal"},
                {"bundle": "com.example.code", "name": "Code",
                 "tone": "code"},
                {"bundle": "com.example.mail", "name": "Mail",
                 "tone": "auto"},
            ],
            "snippets": [{"name": "signature", "text": "Cheers"}],
            "manual_vocabulary": ["Qwen"],
            "banned_vocabulary": ["Gwen"],
            "corrections": [{
                "key": "teh", "source": "teh", "target": "the",
                "count": 3, "kind": "correction"}],
        }
        self.keywords = {
            "schema_version": 1,
            "kind": "whisper-face/acoustic-keyword-memory-export",
            "policy": {"minimum_observations": 3,
                       "minimum_confirmations": 2, "max_entries": 256,
                       "recognition_effect": "none"},
            "candidates": [{
                "keyword": "Qwen", "app_scope": None, "observations": 1,
                "confirmations": 1, "eligible": False,
                "status": "needs-2-observations-and-1-confirmations"}],
        }

        def set_tone(bundle, tone):
            if bundle == self.fail_tone_for:
                raise OSError("the tones file is read-only")
            self.calls.append(("tone", bundle, tone))
            for item in self.settings["app_tones"]:
                if item["bundle"] == bundle:
                    item["tone"] = tone

        def save_snippet(name, expected, text):
            self.calls.append(("save_snippet", name, expected, text))
            self.settings["snippets"] = [
                item for item in self.settings["snippets"]
                if item["name"] != name] + [{"name": name, "text": text}]

        def forget(key):
            self.calls.append(("forget", key))
            self.settings["corrections"] = []

        self.model = WhisperFaceViewModel(GUIActions(
            settings_snapshot=lambda: self.settings,
            set_app_tone=set_tone,
            save_snippet=save_snippet,
            delete_snippet=lambda name, expected: self.calls.append(
                ("delete_snippet", name, expected)),
            save_vocabulary=lambda terms, bans: self.calls.append(
                ("vocabulary", tuple(terms), tuple(bans))),
            forget_correction=forget,
            inspect_acoustic_keywords=lambda: self.keywords,
            export_acoustic_keywords=lambda: self.calls.append(("export",)),
            forget_acoustic_keyword=lambda keyword, scope:
                self.calls.append(("forget_keyword", keyword, scope)),
            forget_all_acoustic_keywords=lambda:
                self.calls.append(("forget_all",))))
        self.model.acknowledge_onboarding()
        self.model.load_settings()
        self.controller = gui_module.WhisperFaceWindowController.alloc() \
            .initForSmokeWithViewModel_(self.model)
        self.addCleanup(self.close)

    def close(self):
        self.controller.modal_responder = None
        self.controller.window.setDelegate_(None)
        self.controller.window.close()
        self.controller.pages.clear()
        self.controller.dynamic.clear()

    def drive(self, action, *steps):
        """Open an editor and answer it; return the dialogs it raised."""
        remaining, seen = list(steps), []

        def respond(dialog):
            seen.append(dialog)
            step = remaining.pop(0)
            return step(dialog) if callable(step) else step

        self.controller.modal_responder = respond
        try:
            getattr(self.controller, action)(None)
        finally:
            self.controller.modal_responder = None
        self.assertEqual(remaining, [])
        return seen

    def test_every_editor_is_a_sheet_and_leaving_it_changes_nothing(self):
        for action, answer in (
                ("editTone_", "cancel"), ("editSnippets_", "done"),
                ("editVocabulary_", "cancel"),
                ("reviewCorrections_", "done"),
                ("inspectKeywords_", "done")):
            with self.subTest(action=action):
                (sheet,) = self.drive(action, answer)
                self.assertIsInstance(sheet, gui_module.EditorSheet)
                self.assertIsInstance(
                    sheet.window, gui_module.SheetWindow)
                self.assertFalse(sheet.window.isVisible())
                self.assertEqual(
                    sheet.window.contentView().frame().size.width,
                    gui_module.SHEET_WIDTH)
                self.assertIsNone(self.controller._sheet)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.controller.window.isVisible())

    def test_save_applies_each_tone_that_changed_and_no_other(self):
        def choose(sheet):
            for bundle, tone in (("com.apple.finder", "casual"),
                                 ("com.example.mail", "verbatim")):
                sheet.controls[f"tone:{bundle}"].selectItemAtIndex_(
                    gui_module.TONE_CHOICES.index(tone))
            return "save"

        self.drive("editTone_", choose)
        self.assertEqual(self.calls, [
            ("tone", "com.apple.finder", "casual"),
            ("tone", "com.example.mail", "verbatim")])
        # Saving again with nothing changed applies nothing.
        self.drive("editTone_", "save")
        self.assertEqual(len(self.calls), 2)

    def test_a_refused_tone_keeps_the_sheet_and_every_choice(self):
        self.fail_tone_for = "com.example.code"

        def choose(sheet):
            sheet.controls["tone:com.example.code"].selectItemAtIndex_(
                gui_module.TONE_CHOICES.index("casual"))
            sheet.controls["tone:com.example.mail"].selectItemAtIndex_(
                gui_module.TONE_CHOICES.index("formal"))
            return "save"

        def refused(sheet):
            note = sheet.controls["notice"]
            self.assertFalse(note.isHidden())
            self.assertIn("read-only", str(note.stringValue()))
            self.assertEqual(
                str(note.cell().accessibilityLabel()),
                localized_string("sheets.notice.error.label"))
            self.assertEqual(
                [gui_module.TONE_CHOICES[popup.indexOfSelectedItem()]
                 for _item, popup in sheet.listed],
                ["formal", "casual", "formal"])
            return "cancel"

        first, second = self.drive("editTone_", choose, refused)
        self.assertIs(first, second)
        # It stopped at the refusal: Mail, after Code, was never tried.
        self.assertEqual(self.calls, [])

    def test_a_refused_tone_says_which_app_and_what_was_already_saved(self):
        self.fail_tone_for = "com.example.code"
        notices = []

        def choose(sheet):
            for bundle in ("com.apple.finder", "com.example.code",
                           "com.example.mail"):
                sheet.controls[f"tone:{bundle}"].selectItemAtIndex_(
                    gui_module.TONE_CHOICES.index("casual"))
            return "save"

        def refused(sheet):
            notices.append(str(sheet.controls["notice"].stringValue()))
            return "cancel"

        self.drive("editTone_", choose, refused)
        # Finder was saved before Code was refused, and stays saved
        # after Cancel: the sheet says so. Mail was never tried.
        self.assertEqual(
            self.calls, [("tone", "com.apple.finder", "casual")])
        self.assertTrue(notices[0].startswith("Code: "), notices[0])
        self.assertIn("read-only", notices[0])
        self.assertTrue(
            notices[0].endswith(" Already saved: Finder."), notices[0])
        # With nothing saved before it, nothing is said to be.
        self.calls.clear()
        self.fail_tone_for = "com.apple.finder"

        def choose_first(sheet):
            sheet.controls["tone:com.apple.finder"].selectItemAtIndex_(
                gui_module.TONE_CHOICES.index("formal"))
            return "save"

        self.drive("editTone_", choose_first, refused)
        self.assertEqual(self.calls, [])
        self.assertTrue(notices[1].startswith("Finder: "), notices[1])
        self.assertNotIn("Already saved", notices[1])

    def test_adding_a_snippet_is_a_form_in_the_same_sheet(self):
        def fill(sheet):
            self.assertEqual(
                str(sheet.title.stringValue()),
                localized_string("settings.dialog.snippet.add"))
            self.assertEqual(sorted(sheet.buttons), ["cancel", "save"])
            # The name field is the first group, and has the keyboard.
            name = sheet.controls["name"]
            self.assertIs(
                name.superview(), sheet.document.sections[0][1].rows[0])
            self.assertIn(sheet.window.firstResponder(),
                          (name, name.currentEditor()))
            name.setStringValue_("email")
            sheet.controls["text"].setString_("hello@example.com")
            return "save"

        def listed(sheet):
            self.assertEqual(
                [str(row.title.stringValue())
                 for row in sheet.document.sections[0][1].rows],
                ["signature", "email"])
            return "done"

        dialogs = self.drive("editSnippets_", "add", fill, listed)
        self.assertEqual(len({id(dialog) for dialog in dialogs}), 1)
        self.assertEqual(self.calls, [
            ("save_snippet", "email", None, "hello@example.com")])

    def test_a_refused_snippet_keeps_the_form_and_what_was_typed(self):
        def blank(sheet):
            sheet.controls["text"].setString_("   ")
            return "save"

        def refused(sheet):
            self.assertEqual(str(sheet.controls["text"].string()), "   ")
            self.assertEqual(
                str(sheet.controls["name"].stringValue()), "signature")
            note = sheet.controls["notice"]
            self.assertIn("1–4000", str(note.stringValue()))
            # Under the text it is about, with the keyboard in the text;
            # how to say the snippet stays under its name.
            named, texted = sheet.document.sections
            self.assertEqual(list(texted[2]), [note])
            self.assertEqual(
                [str(line.stringValue()) for line in named[2]],
                [localized_string("sheets.snippet.speak")])
            self.assertIs(
                sheet.window.firstResponder(), sheet.controls["text"])
            return "cancel"

        self.drive(
            "editSnippets_", "edit:signature", blank, refused, "done")
        self.assertEqual(self.calls, [])

    def test_a_snippet_with_no_name_is_asked_for_one(self):
        def unnamed(sheet):
            sheet.controls["text"].setString_("hello@example.com")
            return "save"

        def asked(sheet):
            note = sheet.controls["notice"]
            self.assertEqual(
                str(note.stringValue()),
                localized_string("validation.snippet.required"))
            named = sheet.document.sections[0]
            self.assertIs(named[2][0], note)
            self.assertEqual(
                str(sheet.controls["text"].string()), "hello@example.com")
            name = sheet.controls["name"]
            self.assertIn(sheet.window.firstResponder(),
                          (name, name.currentEditor()))
            return "cancel"

        self.drive("editSnippets_", "add", unnamed, asked, "done")
        self.assertEqual(self.calls, [])

    def test_a_field_fills_its_group_with_no_border_of_its_own(self):
        AppKit = self.AppKit
        for dark in (False, True):
            self.controller.window.setAppearance_(
                AppKit.NSAppearance.appearanceNamed_(
                    AppKit.NSAppearanceNameDarkAqua if dark
                    else AppKit.NSAppearanceNameAqua))
            self.controller.render()

            def look(sheet, dark=dark):
                palette = gui_module.palette_for_appearance(dark)
                for name in ("terms", "bans"):
                    editor = sheet.controls[name]
                    scroll = editor.enclosingScrollView()
                    self.assertEqual(scroll.borderType(), AppKit.NSNoBorder)
                    self.assertFalse(scroll.drawsBackground())
                    self.assertFalse(editor.drawsBackground())
                    self.assertAlmostEqual(
                        editor.textColor().redComponent(), palette.ink[0],
                        places=2)
                    row = scroll.superview()
                    self.assertEqual(
                        scroll.frame().size.width,
                        row.frame().size.width - 2.0)
                return "cancel"

            with self.subTest(dark=dark):
                self.drive("editVocabulary_", look)

    def test_a_refusal_puts_the_keyboard_in_the_list_it_is_about(self):
        def exclude(sheet):
            sheet.controls["bans"].setString_("#Gwen")
            return "save"

        def refused(sheet):
            note = sheet.controls["notice"]
            self.assertIn(note, sheet.document.sections[1][2])
            self.assertIn(
                "A term in your excluded vocabulary",
                str(note.stringValue()))
            self.assertIs(
                sheet.window.firstResponder(), sheet.controls["bans"])
            return "cancel"

        self.drive("editVocabulary_", exclude, refused)
        self.assertEqual(self.calls, [])

    def test_deleting_and_forgetting_ask_first_with_a_safe_default(self):
        seen = self.drive(
            "editSnippets_", "delete:signature", 1001, "done")
        seen += self.drive(
            "reviewCorrections_", "forget:correction:teh", 1001, "done")
        self.assertEqual(self.calls, [])
        for alert in (seen[1], seen[4]):
            self.assertIsInstance(alert, self.AppKit.NSAlert)
            self.assertEqual(gui_module.alert_key_routes(alert), (1, 1))
            self.assertTrue(alert.buttons()[0].hasDestructiveAction())
        seen = self.drive(
            "reviewCorrections_", "forget:correction:teh", 1000, "done")
        self.assertEqual(self.calls, [("forget", "teh")])
        # The list is read again: the sheet now says there is nothing.
        rows = seen[2].document.sections[0][1].rows
        self.assertEqual(
            [str(view.stringValue()) for view in rows[0].subviews()],
            [localized_string("settings.empty.corrections")])

    def test_the_keyword_sheet_says_what_each_action_came_to(self):
        def copied(sheet):
            note = sheet.controls["notice"]
            self.assertEqual(
                str(note.stringValue()),
                localized_string("settings.notice.keyword_exported"))
            self.assertEqual(
                str(note.cell().accessibilityLabel()),
                localized_string("sheets.notice.result.label"))
            return "forget_all"

        def forgotten(sheet):
            self.assertEqual(
                str(sheet.controls["notice"].stringValue()),
                localized_string("settings.notice.keywords_forgotten"))
            return "done"

        self.drive("inspectKeywords_", "export", copied, 1000, forgotten)
        self.assertEqual(self.calls, [("export",), ("forget_all",)])

    def test_with_no_keyword_there_is_nothing_to_copy_or_forget(self):
        def listed(sheet):
            self.assertTrue(sheet.buttons["export"].isEnabled())
            self.assertTrue(sheet.buttons["forget_all"].isEnabled())
            return "done"

        self.drive("inspectKeywords_", listed)
        self.keywords["candidates"] = []

        def empty(sheet):
            self.assertFalse(sheet.buttons["export"].isEnabled())
            self.assertFalse(sheet.buttons["forget_all"].isEnabled())
            with self.assertRaises(ValueError):
                sheet.press("forget_all")
            self.assertEqual(
                str(sheet.window.title()),
                localized_string("settings.dialog.keywords.title"))
            return "done"

        self.drive("inspectKeywords_", empty)
        self.assertEqual(self.calls, [])

    def test_what_a_sheet_comes_to_is_said_aloud_once(self):
        said = []
        with patch.object(
                self.AppKit, "NSAccessibilityPostNotificationWithUserInfo",
                lambda element, name, info: said.append(
                    (element, str(name), dict(info)))):
            (sheet, _again) = self.drive("inspectKeywords_", "export", "done")
        said = [entry for entry in said if entry[1] == str(
            self.AppKit.NSAccessibilityAnnouncementRequestedNotification)]
        self.assertEqual(len(said), 1)
        element, _name, info = said[0]
        self.assertIs(element, sheet.window)
        self.assertEqual(
            info[self.AppKit.NSAccessibilityAnnouncementKey],
            localized_string("settings.notice.keyword_exported"))
        self.assertEqual(
            info[self.AppKit.NSAccessibilityPriorityKey],
            self.AppKit.NSAccessibilityPriorityHigh)

    def test_a_click_ends_a_real_run_and_the_sheet_leaves_its_window(self):
        """The path with no responder: on the window as a sheet, a modal
        session, a button that stops it, and the sheet taken down. The
        window is a stand-in, and so is the application the window
        module asks for as long as the test lasts, so nothing is shown
        and no real modal session can start, whatever is broken."""
        log = []

        class HostWindow:
            def isVisible(self):
                return True

            def beginSheet_completionHandler_(self, window, handler):
                log.append(("begin", window, handler))

            def endSheet_(self, window):
                log.append(("end", window))

        class Session:
            sheet, answers = None, []

            @classmethod
            def sharedApplication(cls):
                return cls

            @classmethod
            def runModalForWindow_(cls, window):
                log.append(("run", window, cls.sheet.running))
                answer = cls.answers.pop(0) if cls.answers else None
                if answer:
                    cls.sheet.press(answer)
                return cls.sheet.tag

            @classmethod
            def stopModalWithCode_(cls, code):
                log.append(("stop", code, cls.sheet.running))

        application = patch.object(gui_module, "NSApplication", Session)
        application.start()
        self.addCleanup(application.stop)
        controller, host = self.controller, HostWindow()
        sheet = controller._new_sheet()
        try:
            Session.sheet = sheet
            controller._sheet_fill(sheet, lambda: controller._vocabulary_page(
                sheet, "Qwen", "Gwen", ""))
            Session.answers = ["save"]
            tag = sheet.run(host, None)
            self.assertEqual(log, [
                ("begin", sheet.window, None),
                ("run", sheet.window, True),
                ("stop", sheet.buttons["save"].tag(), True)])
            self.assertEqual(tag, sheet.buttons["save"].tag())
            self.assertEqual(sheet.choice, ("save", None))
            self.assertIs(sheet.parent, host)
            self.assertFalse(sheet.running)
            # Taller while it is up: it stays in the middle, where AppKit
            # holds a sheet.
            before = sheet.window.frame()
            controller._sheet_fill(sheet, lambda: controller._vocabulary_page(
                sheet, "Qwen", "Gwen", "A line that makes it taller."))
            after = sheet.window.frame()
            self.assertGreater(
                after.size.height, before.size.height + 4.0)
            # (To the half point a window's frame is rounded to.)
            self.assertAlmostEqual(
                after.origin.y + after.size.height / 2.0,
                before.origin.y + before.size.height / 2.0, delta=0.5)
            # Run again by the window, with no responder: it is not begun
            # twice, and a session that ends with no button is Cancel.
            Session.answers = [None]
            self.assertEqual(
                controller._run_sheet(sheet), ("cancel", None))
            self.assertEqual(log[3:], [("run", sheet.window, True)])
            # One editor at a time. (Were a second opened, the responder
            # would answer it.)
            opened = []
            controller.modal_responder = lambda dialog: (
                opened.append(dialog), "cancel")[1]
            with self.assertRaises(RuntimeError):
                controller.editTone_(None)
            self.assertEqual(opened, [])
            self.assertIs(controller._sheet, sheet)
        finally:
            controller.modal_responder = None
            controller._end_sheet(sheet)
        self.assertEqual(log[4:], [("end", sheet.window)])
        self.assertIsNone(sheet.parent)
        self.assertIsNone(controller._sheet)
        self.assertEqual(self.calls, [])

    def test_a_list_filled_again_keeps_the_keyboard_in_its_place(self):
        self.settings["corrections"] = [
            {"key": key, "source": key, "target": target, "count": 3,
             "kind": "correction"}
            for key, target in (("teh", "the"), ("adn", "and"),
                                ("wich", "which"))]
        self.model.load_settings()

        def forget(key):
            self.calls.append(("forget", key))
            self.settings["corrections"] = [
                item for item in self.settings["corrections"]
                if item["key"] != key]

        self.model.actions = replace(
            self.model.actions, forget_correction=forget)

        def on_the_second_row(sheet):
            self.assertTrue(sheet.window.makeFirstResponder_(
                sheet.buttons["forget:correction:adn"]))
            return "forget:correction:adn"

        def next_row_has_it(sheet):
            self.assertIs(
                sheet.window.firstResponder(),
                sheet.buttons["forget:correction:wich"])
            return "done"

        self.drive("reviewCorrections_", on_the_second_row, 1000,
                   next_row_has_it)
        self.assertEqual(self.calls, [("forget", "adn")])
        # With the keyboard on nothing, it is put on nothing.
        def nowhere(sheet):
            self.assertIs(sheet.window.firstResponder(), sheet.window)
            return "done"

        self.drive("reviewCorrections_", "forget:correction:teh", 1001,
                   nowhere)

    def test_a_long_list_stops_at_the_cap_and_scrolls(self):
        self.settings["app_tones"] = [
            {"bundle": f"com.example.app-{index}", "name": f"App {index}",
             "tone": "auto"} for index in range(100)]
        self.model.load_settings()

        def measure(sheet):
            height = sheet.window.contentView().frame().size.height
            self.assertEqual(height, gui_module.SHEET_MAX_HEIGHT)
            self.assertLessEqual(
                height, gui_module.WINDOW_MIN_SIZE[1]
                - 2 * gui_module.SHEET_WINDOW_MARGIN)
            self.assertEqual(len(sheet.listed), 100)
            self.assertGreater(
                sheet.document.frame().size.height,
                sheet.scroll.frame().size.height)
            self.assertFalse(sheet.rule.isHidden())
            # The footer is under the body, not in it.
            foot = sheet.scroll.frame().origin.y \
                + sheet.scroll.frame().size.height
            for button in sheet.footer:
                self.assertIs(button.superview(), sheet.content)
                self.assertGreaterEqual(
                    gui_module.sheet_alignment(button)[1], foot)
            return "cancel"

        self.drive("editTone_", measure)

    def test_escape_cancels_a_form_and_is_done_in_a_list(self):
        def escape(sheet):
            sheet.window.cancelOperation_(None)

        (form,) = self.drive("editVocabulary_", escape)
        self.assertEqual(form.choice, ("cancel", None))
        (listing,) = self.drive("inspectKeywords_", escape)
        self.assertEqual(listing.choice, ("done", None))
        self.assertEqual(self.calls, [])
        # Return is the primary button's, drawn as the default.
        def default(sheet):
            save = sheet.buttons["save"]
            self.assertEqual(str(save.keyEquivalent()), "\r")
            self.assertEqual(
                sheet.window.defaultButtonCell(), save.cell())
            self.assertEqual(
                str(sheet.buttons["cancel"].keyEquivalent()), "\x1b")
            return "cancel"

        self.drive("editVocabulary_", default)

    def test_a_sheet_takes_the_appearance_the_window_is_in(self):
        AppKit = self.AppKit
        for dark in (False, True):
            self.controller.window.setAppearance_(
                AppKit.NSAppearance.appearanceNamed_(
                    AppKit.NSAppearanceNameDarkAqua if dark
                    else AppKit.NSAppearanceNameAqua))
            self.controller.render()

            def look(sheet, dark=dark):
                palette = gui_module.palette_for_appearance(dark)
                self.assertEqual(
                    gui_module._uses_dark_appearance(sheet.window), dark)
                ink = sheet.title.textColor()
                self.assertAlmostEqual(
                    ink.redComponent(), palette.ink[0], places=2)
                soft = sheet.message.textColor()
                self.assertAlmostEqual(
                    soft.greenComponent(), palette.ink_soft[1], places=2)
                return "cancel"

            with self.subTest(dark=dark):
                self.drive("editTone_", look)
        # Nothing a sheet made is left for the window to theme.
        before = {role: len(views)
                  for role, views in self.controller.themed.items()}
        self.drive("editSnippets_", "add", "cancel", "done")
        self.assertEqual(
            {role: len(views)
             for role, views in self.controller.themed.items()}, before)

    def test_an_alert_shows_the_face_that_is_chosen(self):
        def icon_of(face):
            self.model.state = replace(self.model.state, face=face)
            (alert,) = self.drive("viewModes_", 1000)
            self.assertIsInstance(alert, self.AppKit.NSAlert)
            return alert.icon()

        parrot, fox = icon_of("parrot"), icon_of("fox")
        self.assertIsNotNone(parrot)
        self.assertIsNot(parrot, fox)
        self.assertIs(icon_of("parrot"), parrot)
        self.assertEqual(
            (parrot.size().width, parrot.size().height), (64.0, 64.0))

    def test_a_button_that_is_not_on_the_sheet_cannot_be_pressed(self):
        def press(sheet):
            with self.assertRaises(KeyError):
                sheet.press("save")
            sheet.buttons["export"].setEnabled_(False)
            with self.assertRaises(ValueError):
                sheet.press("export")
            return "done"

        self.drive("inspectKeywords_", press)
        self.assertEqual(self.calls, [])
        # With no sheet open, a stray press of a sheet's button is ignored.
        button = self.AppKit.NSButton.alloc().initWithFrame_(
            self.AppKit.NSMakeRect(0, 0, 10, 10))
        button.setTag_(gui_module.SHEET_FIRST_TAG)
        self.controller.sheetChose_(button)


if APPKIT_AVAILABLE:
    from Foundation import NSObject as _NSObject

    class _ClickTarget(_NSObject):
        def clicked_(self, _sender):
            self.fired.append("clicked")


if __name__ == "__main__":
    unittest.main()
