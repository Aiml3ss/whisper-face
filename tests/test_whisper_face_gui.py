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
    STRING_CATALOGS,
    SUPPORTED_LOCALES,
    WhisperFaceViewModel,
    correction_review_text,
    create_gui,
    localized_string,
    native_appkit_smoke_contract,
    onboarding_presentation,
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
    result_evidence_text,
    run_native_appkit_smoke,
    resolve_locale,
    set_accessible_text,
    support_snapshot_text,
    sync_accessibility,
    tone_for_app_index,
)
import whisper_face_gui as gui_module


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
            localized_string("settings.personalize.snippets.detail",
                             locale="fr", count=2),
            "2 saved phrases")
        with self.assertRaises(KeyError):
            localized_string("missing.key")
        with self.assertRaises(ValueError):
            localized_string("settings.personalize.snippets.detail")
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

    def test_first_run_presentation_keeps_completion_visible_until_acknowledged(self):
        first_run = normalize_snapshot({
            "microphone_status": "Needs attention",
            "accessibility_status": "Needs attention",
        })
        active = onboarding_presentation(
            first_run.onboarding_steps, acknowledged=False)
        self.assertTrue(active.visible)
        self.assertFalse(active.complete)
        self.assertEqual(active.current_key, "permissions")
        self.assertEqual(active.title, "First, let your face listen.")
        self.assertEqual(active.action_title, "Open System Settings")

        complete = normalize_snapshot({
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "hotkey_practiced": True,
            "models": [{"name": "Parakeet", "status": "Running"}],
            "last_word_count": 5,
        })
        celebration = onboarding_presentation(
            complete.onboarding_steps, acknowledged=False)
        self.assertTrue(celebration.visible)
        self.assertTrue(celebration.complete)
        self.assertIsNone(celebration.current_key)
        self.assertEqual(celebration.title, "Your face works.")
        self.assertEqual(celebration.action_title, "Start Dictating")
        self.assertFalse(onboarding_presentation(
            complete.onboarding_steps, acknowledged=True).visible)

    def test_onboarding_copy_explains_observed_practice_and_recovery(self):
        state = normalize_snapshot({
            "service_status": "Running",
            "microphone_status": "Ready",
            "accessibility_status": "Granted",
            "hotkey_label": "Right Option",
            "models": [{"name": "Parakeet", "status": "Running"}],
        })
        hotkey = next(
            step for step in state.onboarding_steps if step.key == "hotkey")
        self.assertFalse(hotkey.complete)
        self.assertIn("observes capture", hotkey.detail)

        first_dictation = next(
            step for step in state.onboarding_steps
            if step.key == "first_dictation")
        self.assertIn("Voice Outbox", first_dictation.detail)
        self.assertIn("Copy & Dismiss", first_dictation.detail)

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
            "microphone_status": "Needs attention",
            "accessibility_status": "Needs attention",
            "models": [{"name": "Parakeet", "status": "Preparing"}],
        }
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: runtime))
        state = model.show_next_onboarding_step()
        self.assertEqual(state.section, "Advanced")
        self.assertEqual(state.notice_level, "info")
        self.assertIn("Microphone captures speech", state.notice)

        state = model.show_issue()
        self.assertEqual(state.section, "Advanced")
        self.assertEqual(state.notice_level, "error")
        self.assertIn("Microphone", state.notice)

    def test_permission_recovery_opens_only_while_permission_evidence_is_incomplete(self):
        runtime = {
            "service_status": "Running",
            "microphone_status": "Needs attention",
            "accessibility_status": "Needs attention",
        }
        opened: list[str] = []
        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: dict(runtime),
            open_system_settings=lambda: opened.append("opened")))

        self.assertTrue(model.permission_recovery_needed())
        state = model.open_system_settings()
        self.assertEqual(opened, ["opened"])
        self.assertEqual(state.notice_level, "info")
        self.assertIn("Return here", state.notice)

        runtime.update(
            microphone_status="Ready", accessibility_status="Granted")
        model.refresh()
        self.assertFalse(model.permission_recovery_needed())
        model.open_system_settings()
        self.assertEqual(opened, ["opened"])

    def test_permission_recovery_keeps_failure_local_and_content_free(self):
        def fail_to_open() -> None:
            raise RuntimeError("settings unavailable")

        model = WhisperFaceViewModel(GUIActions(
            status_snapshot=lambda: {
                "microphone_status": "Needs attention",
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


if __name__ == "__main__":
    unittest.main()
