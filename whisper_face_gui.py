"""Native macOS settings window for Whisper Face.

The runtime owns capture, models, preferences, and service lifecycle.  This
module owns presentation only: callers inject a small callback interface and
can therefore open the window from the menu bar without importing
``dictate.py`` here.  ``WhisperFaceViewModel`` is deliberately AppKit-free so
its behavior can be tested without displaying a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from support_bundle import SupportBundleError, write_support_bundle
from whisper_face_theme import (
    FACE_CHIP_COLORS,
    MOTION_SPECS,
    SURFACE_SPECS,
    palette_for_appearance,
)


APP_NAME = "Whisper Face"
DEFAULTS_SUITE = "com.whisperface.app"
SECTIONS = ("Home", "Settings", "Advanced")
SETTINGS_PANES = ("Personalize", "Privacy")
MODE_GUIDE = ("capture", "compose", "edit", "reply", "command", "code")
TONE_CHOICES = ("auto", "casual", "formal", "code", "verbatim", "default")
CONSEQUENCE_CATEGORIES = frozenset({
    "name", "number", "currency", "date", "time", "recipient", "contact",
    "url", "path", "command", "action",
})
CONSEQUENCE_ROUTES = frozenset({
    "standard", "protected", "review", "verified", "unavailable",
})
CONSEQUENCE_RELISTEN_STATUSES = frozenset({
    "not-needed", "skipped", "confirmed", "contradicted", "timed-out",
    "inconclusive", "mixed", "unavailable",
})
RESULT_EVIDENCE_STAGES = (
    "release", "asr", "compiler", "consequence",
    "context", "cleanup", "insertion",
)
VOICE_DRAFT_DESTINATIONS = frozenset({
    "plain_text", "email_draft", "task", "calendar_draft", "unavailable",
})
VOICE_DRAFT_STATES = frozenset({"queued", "acknowledged", "cancelled"})
EMAIL_COMPOSE_STATES = frozenset({
    "requested", "unavailable", "invalid", "failed",
})
VOICE_DRAFT_COPY_STATES = frozenset({
    "copied", "unavailable", "invalid", "failed",
})
VOICE_DRAFT_CLEAR_STATES = frozenset({
    "cleared", "changed", "unavailable", "failed",
})
VOICE_DRAFT_INSPECT_LIMIT = 256
VOICE_DRAFT_CONTENT_LIMIT = 300_000
DEMONSTRATION_DOMAINS = ("finder", "mail", "notes", "menu")
DEMONSTRATION_STATES = frozenset({"recording", "approved"})
DEMONSTRATION_ACTIONS = {
    "finder": ("select_item", "create_folder", "rename_item"),
    "mail": ("compose_message", "address_message", "set_subject", "set_body"),
    "notes": ("create_note", "set_note_title", "set_note_body"),
    "menu": ("open_menu", "choose_menu_item"),
}
DEMONSTRATION_INSPECT_LIMIT = 64
DEMONSTRATION_STEP_LIMIT = 12
DEMONSTRATION_TEXT_LIMIT = 512
RISKY_ACTION_CLASSES = (
    "external_communication",
    "calendar_commit",
    "file_mutation",
    "agent_execution",
)
RISKY_ACTION_STATES = frozenset({
    "idle", "awaiting_voice", "awaiting_click", "confirmed", "cancelled",
    "expired",
})


def application_build_version() -> str:
    """Return an honest checkout build identifier for the native header."""
    try:
        result = subprocess.run(
            [
                "git", "-C", str(Path(__file__).resolve().parent),
                "rev-parse", "--short=7", "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().upper()
    except (OSError, subprocess.SubprocessError):
        pass
    return "LOCAL"
SELECTIVE_RELISTEN_STATUSES = frozenset({
    "off", "ready", "warming", "enabled-not-ready", "receipt-missing",
    "receipt-invalid", "receipt-policy-mismatch",
    "receipt-evidence-insufficient",
})
POINT_AND_SPEAK_MAX_PHRASE_CHARS = 96
POINT_AND_SPEAK_ROLES = frozenset({
    "button", "checkbox", "link", "menu_item", "radio_button", "tab",
    "text_field",
})
POINT_AND_SPEAK_ACTION_ROLES = frozenset({
    "button", "checkbox", "radio_button", "tab", "menu_item", "link",
})
POINT_AND_SPEAK_STATES = frozenset({
    "resolved", "ambiguous", "unavailable", "permission_denied",
})
POINT_AND_SPEAK_CAPTURE_STATES = frozenset({
    "captured", "unavailable", "permission_denied",
})
POINT_AND_SPEAK_EVIDENCE = frozenset({
    "exact", "normalized", "token", "role", "selection", "focus",
    "ordinal", "spatial",
})
POINT_AND_SPEAK_ACTION_STATES = frozenset({
    "executed", "recheck_failed", "expired", "unsupported",
    "execution_failed", "unavailable", "ambiguous", "permission_denied",
})
DROP_TARGET_MAX_PHRASE_CHARS = 96
DROP_TARGET_ROLES = (
    "AXGroup", "AXImage", "AXList", "AXScrollArea",
)
DROP_TARGET_SOURCE_KINDS = (
    "file_reference", "image_reference", "text_selection", "url_reference",
)
DROP_TARGET_EFFECTS = ("copy", "link", "move")
DROP_TARGET_STATES = frozenset({
    "resolved", "ambiguous", "unavailable", "permission_denied",
})
DROP_TARGET_CAPTURE_STATES = frozenset({
    "captured", "unavailable", "permission_denied",
})
DROP_TARGET_EVIDENCE = frozenset({
    "exact_name", "normalized_name", "token_name", "source_compatible",
    "effect_compatible", "constraint_conflict",
})
MODEL_WALLET_CAPABILITIES = frozenset({
    "fast_asr", "final_asr", "cleanup",
})
MODEL_WALLET_PROVIDER_IDS = frozenset({
    "local.parakeet-coreml",
    "local.whisper-tiny-mlx",
    "local.whisper-large-v3-turbo-mlx",
    "local.qwen3.5-4b-ollama",
})
MODEL_WALLET_SUPPORTED_PROVIDERS = {
    "fast_asr": frozenset({"local.whisper-tiny-mlx"}),
    "final_asr": frozenset({
        "local.parakeet-coreml",
        "local.whisper-large-v3-turbo-mlx",
    }),
    "cleanup": frozenset({"local.qwen3.5-4b-ollama"}),
}
MODEL_WALLET_ELIGIBILITIES = frozenset({
    "eligible", "unsupported_capability", "missing_runtime_evidence",
    "not_ready", "missing_capability_evidence", "outside_request_bounds",
})

# Stable semantic keys are intentionally separate from AppKit. Additional
# catalogs can be added without rewriting view logic or persistence schemas.
STRING_CATALOGS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "nav.home": "Home",
        "nav.settings": "Settings",
        "nav.advanced": "Advanced",
        "advanced.accessibility.shortcut": "Advanced section shortcut",
        "advanced.accessibility.shortcut.help": "Show the Advanced section (Command-D).",
        "app.subtitle": "Private, fast voice input on your Mac",
        "app.local_badge": "LOCAL FIRST",
        "app.version": "BUILD {version}",
        "overview.phase.ready": "READY",
        "overview.phase.recording": "RECORDING",
        "overview.phase.processing": "PROCESSING",
        "overview.phase.recovery": "RECOVERY AVAILABLE",
        "overview.phase.degraded": "ACTION NEEDED",
        "overview.phase.paused": "PAUSED",
        "overview.phase.starting": "STARTING LOCALLY",
        "overview.status.ready.title": "Ready when you are",
        "overview.status.ready.detail": "Hold {hotkey}, speak, then release to insert.",
        "overview.status.recording.title": "Listening…",
        "overview.status.recording.detail": "Keep holding {hotkey}; release when you finish speaking.",
        "overview.status.processing.title": "Making your words useful…",
        "overview.status.processing.detail": "Recognizing locally, protecting names and numbers, then checking the destination.",
        "overview.status.recovery.title": "Your words are safe",
        "overview.status.recovery.detail.one": "1 dictation needs an explicit Copy & Dismiss review.",
        "overview.status.recovery.detail.many": "{count} dictations need an explicit Copy & Dismiss review.",
        "overview.status.degraded.title": "One setup item needs attention",
        "overview.status.paused.title": "Dictation is paused",
        "overview.status.paused.detail": "Resume whenever you are ready. Settings and recovery still work.",
        "overview.status.starting.title": "Finishing local startup…",
        "overview.status.starting.detail": "You can leave this window open; readiness updates automatically.",
        "overview.engine.waiting": "Waiting for status",
        "overview.engine.warming": "Warming up",
        "overview.engine.active": "Active engine: {engine}",
        "overview.outbox.empty": "Voice Outbox: all clear",
        "overview.outbox.pending": "Voice Outbox: {count} recoverable · {summary}",
        "overview.outbox.summary.paste_attempted": "Paste may have landed — verify before reusing",
        "overview.outbox.summary.not_pasted": "Not pasted — destination changed",
        "overview.action.pause": "Pause",
        "overview.action.resume": "Resume",
        "overview.action.pause.help": "Pause or resume the global dictation hotkey.",
        "overview.action.review": "Review Setup",
        "overview.action.review.help": "Show the most useful recovery guidance.",
        "overview.action.copy_outbox": "Copy & Dismiss",
        "overview.action.copy_outbox.help": "Copy the latest recoverable dictation, then remove it from the Voice Outbox.",
        "overview.action.pause.state.paused": "Dictation paused",
        "overview.action.pause.state.active": "Dictation active",
        "overview.action.pause.label": "{action} dictation",
        "overview.metric.last.heading": "Last dictation",
        "overview.metric.words.heading": "Words today",
        "overview.metric.saved.heading": "Time saved",
        "overview.metric.last.empty": "—",
        "overview.metric.last.value": "{seconds}s{words}",
        "overview.metric.last.words.one": " · 1 word",
        "overview.metric.last.words.many": " · {count} words",
        "overview.metric.words.value": "{count}",
        "overview.metric.saved.value": "{minutes} min",
        "overview.onboarding.initial_progress": "SETUP",
        "overview.accessibility.phase": "Dictation phase",
        "overview.accessibility.status": "Dictation status",
        "overview.accessibility.detail": "Dictation status detail",
        "overview.accessibility.engine": "Active recognition engine",
        "overview.accessibility.outbox": "Voice Outbox status",
        "overview.accessibility.last": "Last dictation duration and word count",
        "overview.accessibility.words": "Words dictated today",
        "overview.accessibility.saved": "Estimated time saved today",
        "overview.accessibility.onboarding.progress": "First run setup progress",
        "overview.accessibility.onboarding.title": "Next setup step",
        "overview.accessibility.onboarding.detail": "Setup step detail",
        "overview.accessibility.onboarding.face": "{face} face, first-run guide",
        "overview.accessibility.onboarding.steps": "First run setup walkthrough",
        "overview.accessibility.onboarding.step": "Setup step {step}: {status}",
        "overview.notice.outbox.copied": "Latest recoverable dictation copied and dismissed",
        "overview.notice.outbox.error": "Could not copy Voice Outbox: {error}",
        "overview.notice.capture.error": "Could not change capture state: {error}",
        "overview.notice.status.error": "Status unavailable: {error}",
        "onboarding.permissions.title": "First, let your face listen.",
        "onboarding.permissions.detail": "Microphone captures speech; Accessibility safely inserts it into the field you chose. Input Monitoring lets the hotkey listen.",
        "onboarding.hotkey.title": "Now make it blink with {hotkey}.",
        "onboarding.hotkey.detail": "Hold {hotkey}, speak, then release. This step completes only after Whisper Face observes capture.",
        "onboarding.models.title": "Meet the local brain.",
        "onboarding.models.detail": "At least one local recognition engine must be ready; fallbacks can finish warming in the background.",
        "onboarding.first_dictation.title": "Say something. Watch it land.",
        "onboarding.first_dictation.detail": "Speak one sentence in a text field. If focus changes, recover it in Voice Outbox with Copy & Dismiss.",
        "onboarding.status.done": "Done",
        "onboarding.status.attention": "Needs attention",
        "onboarding.status.try": "Try it now",
        "onboarding.status.warming": "Warming up",
        "onboarding.status.turn": "Your turn",
        "onboarding.progress": "FIRST-RUN SETUP · {completed} OF {total} COMPLETE",
        "onboarding.step.permissions": "1 Permissions",
        "onboarding.step.hotkey": "2 Hotkey",
        "onboarding.step.models": "3 Models",
        "onboarding.step.first_dictation": "4 Dictate",
        "onboarding.step.summary": "{step} · {status}",
        "onboarding.action.permissions": "Review Permissions",
        "onboarding.action.open_system_settings": "Open System Settings",
        "onboarding.action.open_system_settings.help": "Open macOS System Settings so you can review Microphone, Accessibility, and Input Monitoring. Whisper Face does not change permissions.",
        "onboarding.action.hotkey": "Show Practice",
        "onboarding.action.models": "View Models",
        "onboarding.action.first_dictation": "Show How",
        "onboarding.action.continue": "Continue Setup",
        "onboarding.action.help": "Open the next incomplete first-run setup step.",
        "onboarding.action.finish": "Start Dictating",
        "onboarding.action.finish.help": "Finish first-run setup and show the live dictation overview.",
        "onboarding.complete.progress": "READY TO DICTATE · {total} OF {total} CONFIRMED",
        "onboarding.complete.title": "Your face works.",
        "onboarding.complete.detail": "Permissions, hotkey practice, local model readiness, and your first successful dictation are all confirmed on this Mac.",
        "onboarding.complete.status": "All set",
        "onboarding.privacy": "Speech and setup stay on this Mac.",
        "onboarding.complete": "Setup is complete — Whisper Face is ready.",
        "results.summary.empty": "No dictation yet",
        "results.summary.words": "{words} words",
        "results.summary.timed": "{words} words in {seconds}s",
        "results.engine.waiting": "Waiting for a result",
        "results.engine.session": "{engine} · session-only evidence",
        "results.mode.capture": "Capture",
        "results.evidence.stable": "Stable prefix",
        "results.evidence.anchors": "Protected anchors",
        "results.evidence.decisions": "Compiler decisions",
        "results.evidence.alternatives": "Alternatives",
        "results.evidence.cleanup": "Cleanup edits",
        "results.evidence.proof": "Proof review",
        "results.context.unreported": "Context influence not reported by runtime",
        "results.context.summary": "Context: {influence}",
        "results.consequence.summary": "Consequence: {route} · {high} high-risk · {uncertain} uncertain{risks} · Re-listen: {relisten}",
        "results.consequence.review.advisory": "Check names, numbers, dates, and recipients before relying on this result.",
        "results.consequence.risk": "{category} {count}",
        "results.consequence.risks": " · {risks}",
        "results.route.standard": "Standard",
        "results.route.protected": "Protected",
        "results.route.review": "Review",
        "results.route.verified": "Verified",
        "results.route.unavailable": "Unavailable",
        "results.risk.name": "name",
        "results.risk.number": "number",
        "results.risk.currency": "currency",
        "results.risk.date": "date",
        "results.risk.time": "time",
        "results.risk.recipient": "recipient",
        "results.risk.contact": "contact",
        "results.risk.url": "URL",
        "results.risk.path": "path",
        "results.risk.command": "command",
        "results.risk.action": "action",
        "results.relisten.not-needed": "not needed",
        "results.relisten.skipped": "skipped",
        "results.relisten.confirmed": "confirmed",
        "results.relisten.contradicted": "contradicted",
        "results.relisten.timed-out": "timed out",
        "results.relisten.inconclusive": "inconclusive",
        "results.relisten.mixed": "mixed",
        "results.relisten.unavailable": "unavailable",
        "results.empty.title": "Nothing to inspect yet",
        "results.empty.detail": "Dictate something and Whisper Face will explain what it heard, protected, changed, and delivered.",
        "results.audio.off": "Acoustic replay is off",
        "results.audio.empty": "No consequential span retained; expired audio is cleared automatically",
        "results.audio.available.one": "1 consequential span retained in RAM for at most one minute",
        "results.audio.available.many": "{count} consequential spans retained in RAM for at most one minute",
        "results.audio.play": "Play Span",
        "results.audio.clear": "Clear",
        "results.audio.play.help": "Play one selected consequential span directly from memory before its one-minute expiry; repeated presses move through retained spans. No temporary file is created.",
        "results.audio.clear.help": "Immediately forget every audio span retained for the latest result.",
        "results.audio.notice.played": "Playing the retained consequential span from memory",
        "results.audio.notice.cleared": "Retained consequential audio cleared",
        "results.audio.notice.unavailable": "No retained consequential span is available",
        "results.privacy": "Audio replay is off by default. Selected latest-result spans stay only in RAM and are wiped after one minute, on a new result, or when cleared; they are never written, logged, or sent.",
        "results.inspect.action": "Inspect Evidence",
        "results.inspect.action.help": "Explicitly reveal private alternatives, protected anchors, proof-edit decisions, and timing for only the latest result.",
        "results.inspect.title": "Latest-result evidence",
        "results.inspect.message": "Revealed only for this session. This detail is not added to transcript history or support exports.",
        "results.inspect.empty": "No detailed evidence was reported for this result.",
        "results.inspect.summary": "RESULT SUMMARY",
        "results.inspect.alternatives": "ALTERNATIVES",
        "results.inspect.anchors": "PROTECTED ANCHORS",
        "results.inspect.proof": "PROOF EDITS",
        "results.inspect.proof.accepted": "ACCEPTED",
        "results.inspect.proof.rejected": "REJECTED",
        "results.inspect.timing": "TIMING",
        "results.inspect.stage.release": "Total release",
        "results.inspect.stage.asr": "Recognition",
        "results.inspect.stage.compiler": "Voice Compiler",
        "results.inspect.stage.consequence": "Consequence check",
        "results.inspect.stage.context": "Context firewall",
        "results.inspect.stage.cleanup": "Cleanup",
        "results.inspect.stage.insertion": "Insertion",
        "results.inspect.none": "None reported",
        "results.value.words": "{count} words",
        "results.value.confidence": " · {confidence} confidence",
        "results.value.none_reported": "None reported",
        "results.value.not_reported": "not reported",
        "results.value.proof": "{accepted} accepted · {rejected} rejected",
        "results.firewall.unavailable": "Context safety: no finalized shadow check is available yet.",
        "results.firewall.no_effect": "Context safety: the shadow check found no context-driven change.",
        "results.firewall.quarantine.one": "Context safety: the shadow check flagged 1 protected influence for quarantine review.",
        "results.firewall.quarantine.many": "Context safety: the shadow check flagged {count} protected influences for quarantine review.",
        "results.firewall.promotion.one": "Context safety: the shadow check found 1 non-protected influence for later evaluation.",
        "results.firewall.promotion.many": "Context safety: the shadow check found {count} non-protected influences for later evaluation.",
        "results.accessibility.summary": "Last result summary",
        "results.accessibility.engine": "Last result engine",
        "results.accessibility.mode": "Last result mode",
        "results.accessibility.audio": "Acoustic replay privacy status",
        "results.accessibility.inspect": "Inspect private latest-result evidence",
        "results.accessibility.inspect.content": "Private latest-result evidence",
        "models.waiting": "Waiting for model status",
        "models.unknown": "Unknown",
        "models.waiting.detail": "Open this window after startup completes",
        "models.guidance": "Models prepare locally and can finish in the background.",
        "models.relisten.label": "Selective Re-listen",
        "models.relisten.status.off": "Off",
        "models.relisten.status.ready": "On · verifier ready",
        "models.relisten.status.warming": "On · warming locally",
        "models.relisten.status.enabled-not-ready": "On · starting locally",
        "models.relisten.status.evidence-required": "Evidence required",
        "models.relisten.help": "Recheck only uncertain names and numbers with the local Whisper Tiny verifier. Activation requires approved evidence from this Mac.",
        "models.wallet.unavailable": "Model wallet shadow advisory only · No model execution or routing · Exact pin evidence unavailable · Runtime readiness and capability evidence remain separate.",
        "models.wallet.evidence": "Model wallet shadow advisory only · No model execution or routing · Exact files resolved {resolved}/4 · Warm path observed {warm}/4 · Runtime readiness attested 0/4 · Capability bounds available 0/4.",
        "models.wallet.informational": "Model wallet shadow advisory only · No model execution or routing · Eligible ordering is informational.",
        "models.accessibility.name": "Model name",
        "models.accessibility.detail": "{name} role and detail",
        "models.accessibility.status": "{name} status",
        "models.accessibility.guidance": "Model guidance",
        "models.accessibility.wallet": "Model wallet shadow advisory; no execution or routing",
        "models.accessibility.relisten": "Selective Re-listen activation",
        "diagnostics.service": "Service",
        "diagnostics.microphone": "Microphone",
        "diagnostics.accessibility": "Accessibility",
        "diagnostics.regression": "Personal Regression Lab",
        "diagnostics.motion": "Motion",
        "diagnostics.build": "Build",
        "diagnostics.unknown": "Unknown",
        "diagnostics.action.log": "Open Log",
        "diagnostics.action.copy_support": "Copy Support Snapshot",
        "diagnostics.action.copy_support.help": "Copy a transcript-free support summary with health, permissions, build, model status, and aggregate last-result counts. It never includes dictation text, selections, context, paths, logs, or personal language data.",
        "diagnostics.action.export_support": "Export Support Bundle…",
        "diagnostics.action.export_support.help": "Save a local, transcript-free support bundle. You choose the destination; Whisper Face never uploads it. The bundle contains only health, permission, build, model, and aggregate result metadata.",
        "diagnostics.action.verify": "Run Verification",
        "diagnostics.action.open_system_settings": "Open System Settings",
        "diagnostics.action.open_system_settings.help": "Open macOS System Settings so you can review Microphone, Accessibility, and Input Monitoring. Whisper Face does not change permissions.",
        "diagnostics.action.licenses": "License Notices",
        "diagnostics.action.source": "Exact Source",
        "diagnostics.verification.not_run": "Not run",
        "diagnostics.verification.running": "Running…",
        "diagnostics.verification.passed": "All checks passed",
        "diagnostics.verification.attention": "Checks need attention",
        "diagnostics.verification.failed": "Verification failed: {error}",
        "diagnostics.notice.system_settings.opened": "System Settings opened. Return here when you finish; Whisper Face will refresh its status.",
        "diagnostics.notice.support_bundle.saved": "Transcript-free support bundle saved locally",
        "diagnostics.ready": "Everything looks ready.",
        "diagnostics.license": "AGPL-3.0-only · no warranty · corresponding source available",
        "diagnostics.regression.cases": "{count} cases",
        "diagnostics.regression.quarantined": " · {count} quarantined",
        "diagnostics.motion.reduced": "Reduced motion",
        "diagnostics.motion.standard": "Standard motion",
        "diagnostics.issue": "{title}: {detail}",
        "diagnostics.accessibility.service": "Service status",
        "diagnostics.accessibility.microphone": "Microphone status",
        "diagnostics.accessibility.permission": "Accessibility permission status",
        "diagnostics.accessibility.regression": "Personal Regression Lab status",
        "diagnostics.accessibility.motion": "Motion setting",
        "diagnostics.accessibility.build": "Build version",
        "diagnostics.accessibility.verification": "Verification result",
        "diagnostics.accessibility.open_system_settings": "Open macOS System Settings",
        "diagnostics.accessibility.guidance": "Diagnostic guidance",
        "diagnostics.accessibility.notice": "Whisper Face notice",
        "point_and_speak.result.yes": "yes",
        "point_and_speak.result.no": "no",
        "point_and_speak.validation.phrase": "Enter one target phrase between 1 and {limit} characters.",
        "drop_target.validation.phrase": "Enter one target phrase between 1 and {limit} characters.",
        "issue.service.title": "The local service is not ready",
        "issue.service.detail": "Run Verification for a repair path. Your settings and personal data stay on this Mac.",
        "issue.microphone.title": "Microphone permission is needed",
        "issue.microphone.detail": "Open System Settings › Privacy & Security › Microphone and enable Whisper Face. Other settings remain available.",
        "issue.accessibility.title": "Safe insertion needs Accessibility permission",
        "issue.accessibility.detail": "Open System Settings › Privacy & Security › Accessibility. Until then, recoverable text stays in the Voice Outbox.",
        "issue.models.title": "Local recognition models are still unavailable",
        "issue.models.detail": "Keep Whisper Face open while models finish preparing, then run Verification if their status does not change.",
        "issue.fallback.title": "A fallback model needs attention",
        "issue.fallback.detail": "Dictation can continue with a ready engine. Check: {models}.",
        "settings.title": "Settings",
        "settings.subtitle": "Personal language, appearance, and privacy in one place.",
        "settings.pane.personalize": "Personalize",
        "settings.pane.privacy": "Privacy",
        "settings.mode.capture.name": "Capture",
        "settings.mode.capture.shortcut": "Right Option",
        "settings.mode.capture.detail": "Faithful dictation",
        "settings.mode.compose.name": "Compose",
        "settings.mode.compose.shortcut": "Shift + Right Option",
        "settings.mode.compose.detail": "Compose and tighten",
        "settings.mode.edit.name": "Edit",
        "settings.mode.edit.shortcut": "Command + Right Option",
        "settings.mode.edit.detail": "Edit selected text",
        "settings.mode.reply.name": "Reply",
        "settings.mode.reply.shortcut": "Control + Right Option",
        "settings.mode.reply.detail": "Draft a direct reply",
        "settings.mode.command.name": "Command",
        "settings.mode.command.shortcut": "Command + Control + Right Option",
        "settings.mode.command.detail": "Editing commands",
        "settings.mode.code.name": "Code",
        "settings.mode.code.shortcut": "Shift + Control + Right Option",
        "settings.mode.code.detail": "Technical dictation",
        "settings.personalize.tones": "App tones",
        "settings.personalize.tones.detail": "{count} recent or configured apps",
        "settings.personalize.snippets": "Snippets",
        "settings.personalize.snippets.detail": "{count} saved phrases",
        "settings.personalize.vocabulary": "Vocabulary",
        "settings.personalize.vocabulary.detail": "{terms} terms · {bans} exclusions",
        "settings.personalize.corrections": "Learned corrections",
        "settings.personalize.corrections.detail": "{count} inspectable mappings",
        "settings.personalize.keywords": "Pronunciation keywords",
        "settings.personalize.keywords.detail": "Open to inspect correction-backed evidence",
        "settings.personalize.modes": "Voice modes",
        "settings.personalize.modes.detail": "Six fixed Right Option shortcuts choose capture behavior",
        "settings.dialog.modes.title": "Voice modes",
        "settings.dialog.modes.message": "Hold Right Option with a modifier to choose a mode. Shortcuts are fixed so capture behavior stays predictable and safe.",
        "settings.dialog.modes.row": "{name} · {shortcut}\n    {detail}",
        "settings.accessibility.modes.label": "Voice mode shortcuts",
        "settings.accessibility.modes.help": "View the six fixed voice mode shortcuts.",
        "settings.accessibility.modes_summary.label": "Voice modes summary",
        "settings.action.view": "View",
        "settings.action.edit": "Edit",
        "settings.action.inspect": "Inspect",
        "settings.action.export": "Copy Export",
        "settings.action.done": "Done",
        "settings.action.forget_all": "Forget All",
        "settings.action.add": "Add",
        "settings.action.delete": "Delete",
        "settings.action.forget": "Forget",
        "settings.action.forget_mapping": "Forget This Mapping",
        "settings.action.review": "Review",
        "settings.action.save": "Save",
        "settings.action.cancel": "Cancel",
        "settings.empty.tones": "No recent apps yet",
        "settings.empty.snippets": "No snippets",
        "settings.empty.corrections": "No learned corrections",
        "settings.dialog.tone.title": "App tone",
        "settings.dialog.tone.message": "Choose how cleanup should sound in this app.",
        "settings.dialog.tone.app.label": "Application",
        "settings.dialog.tone.app.help": "Choose the application whose cleanup tone you want to change.",
        "settings.dialog.tone.choice.label": "Cleanup tone",
        "settings.dialog.tone.choice.help": "Choose the writing tone used when cleaning up dictation in this application.",
        "settings.tone.auto": "Auto",
        "settings.tone.casual": "Casual",
        "settings.tone.formal": "Formal",
        "settings.tone.code": "Technical",
        "settings.tone.verbatim": "Verbatim",
        "settings.tone.default": "Neutral",
        "settings.dialog.snippet.add": "Add snippet",
        "settings.dialog.snippet.edit": "Edit snippet",
        "settings.dialog.snippet.name": "Snippet name",
        "settings.dialog.snippet.value": "Text inserted by this snippet",
        "settings.dialog.snippet.chooser.label": "Saved snippet",
        "settings.dialog.snippet.chooser.help": "Choose a saved snippet to edit or delete.",
        "settings.dialog.snippet.name.help": "Enter a short name for this snippet.",
        "settings.dialog.snippet.value.help": "Enter the text inserted when this snippet is used.",
        "settings.dialog.vocabulary.title": "Personal vocabulary",
        "settings.dialog.vocabulary.message": "One term per line. Exclusions prevent automatic learning.",
        "settings.dialog.vocabulary.terms": "Preferred terms",
        "settings.dialog.vocabulary.bans": "Excluded terms",
        "settings.dialog.vocabulary.terms.help": "Enter preferred words or names, one per line.",
        "settings.dialog.vocabulary.bans.help": "Enter words that Whisper Face must not learn, one per line.",
        "settings.dialog.delete.title": "Delete snippet?",
        "settings.dialog.delete.message": "This removes “{name}” from this Mac.",
        "settings.dialog.correction.chooser.label": "Learned correction",
        "settings.dialog.correction.chooser.help": "Choose a learned correction to review.",
        "settings.dialog.correction.chooser.message": "Choose a mapping to see what was learned, where it applies, and why.",
        "settings.dialog.correction.scope.global": "Applies: Whole-word matches in every app.",
        "settings.dialog.correction.scope.apps": "Applies: Whole-word matches in {apps}.",
        "settings.dialog.correction.scope.inactive": "Applies: Not active yet.",
        "settings.dialog.correction.scope.snippet": "Applies: This saved snippet now uses the replacement shown above.",
        "settings.dialog.correction.why.global": "Why: {count} exact corrections passed the local safety checks.",
        "settings.dialog.correction.why.apps": "Why: Repeated corrections passed the local safety checks in {apps}.",
        "settings.dialog.correction.why.held": "Why: Held back because the local correction cases disagree.",
        "settings.dialog.correction.why.learning": "Why: Still learning. Three matching corrections activate everywhere; two in one app activate there.",
        "settings.dialog.correction.why.snippet": "Why: You explicitly edited this snippet {count} time(s).",
        "settings.dialog.correction.observed": "Observed in: {apps}.",
        "settings.dialog.correction.observed.none": "Observed in: App information unavailable.",
        "settings.dialog.correction.privacy": "Stored locally: the corrected words, app scope, and counts only—never audio or surrounding transcript.",
        "settings.dialog.correction.privacy.snippet": "Stored locally: the snippet name, replacement, and edit count.",
        "settings.dialog.keywords.title": "Pronunciation keywords",
        "settings.dialog.keywords.message": "These candidates come only from exact corrections you made. Only separately reviewed physical evidence can grant bounded local-ASR prompt priority.",
        "settings.dialog.keywords.empty": "No correction-backed keyword candidates yet.",
        "settings.dialog.keywords.chooser.label": "Pronunciation keyword candidate",
        "settings.dialog.keywords.chooser.help": "Choose a pronunciation keyword to inspect, export, or forget.",
        "settings.dialog.keywords.row": "{keyword} · {observations} observations · {confirmations} confirmations · {status} · {scope}",
        "settings.dialog.keywords.status.eligible": "eligible for evaluation",
        "settings.dialog.keywords.status.gathering": "gathering evidence",
        "settings.dialog.keywords.scope.global": "Global",
        "settings.dialog.keywords.scope.private_app": "Private app scope",
        "settings.dialog.keywords.forget.title": "Forget pronunciation keyword?",
        "settings.dialog.keywords.forget.message": "This removes all aggregate evidence for “{keyword}” in {scope} scope.",
        "settings.dialog.keywords.forget_all.title": "Forget all pronunciation keywords?",
        "settings.dialog.keywords.forget_all.message": "This removes every correction-backed pronunciation candidate from this Mac.",
        "settings.dialog.keywords.invalid.title": "Pronunciation keyword memory needs attention",
        "settings.dialog.keywords.invalid.message": "The private state is malformed and remains inactive. You can explicitly forget all to reset it.",
        "settings.privacy.title": "Privacy controls",
        "settings.privacy.flight": "Flight Recorder",
        "settings.privacy.flight.detail": "Keeps a rolling 20-second audio buffer in RAM only.",
        "settings.privacy.acoustic": "Acoustic Time Machine",
        "settings.privacy.acoustic.detail": "Opt in to keep selected latest-result spans in RAM for at most one minute.",
        "settings.privacy.voice_objects": "Voice Object Commands",
        "settings.privacy.voice_objects.detail": "Exact task, email, and calendar commands queue inert local drafts instead of pasting.",
        "settings.privacy.voice_objects.status": "{status} · {count} local drafts queued",
        "settings.privacy.voice_objects.inspect": "Inspect",
        "settings.privacy.voice_objects.inspect.help": "Open the local Voice Inbox. Draft content stays hidden until you explicitly reveal a selected draft.",
        "settings.privacy.face": "Companion",
        "settings.face.parrot": "Parrot",
        "settings.face.fox": "Fox",
        "settings.face.owl": "Owl",
        "settings.face.cat": "Cat",
        "settings.face.bear": "Bear",
        "settings.face.dog": "Dog",
        "settings.face.wolf": "Wolf",
        "settings.face.pig": "Pig",
        "settings.face.panda": "Panda",
        "settings.face.tiger": "Tiger",
        "settings.state.enabled": "Enabled",
        "settings.state.local_processing": "Local processing",
        "settings.accessibility.sections.label": "Settings sections",
        "settings.accessibility.sections.help": "Use arrow keys to move between Whisper Face settings sections.",
        "settings.accessibility.category.label": "Settings category",
        "settings.accessibility.category.help": "Choose personalization or privacy settings.",
        "settings.accessibility.edit.help": "Edit {setting}.",
        "settings.accessibility.forget.help": "Inspect or forget {setting}.",
        "settings.accessibility.face.label": "Whisper Face companion",
        "settings.accessibility.face.help": "Choose the animal shown in the menu bar and listening HUD.",
        "settings.accessibility.flight.label": "Flight Recorder",
        "settings.accessibility.flight.help": "Toggle the rolling twenty second audio buffer held only in memory.",
        "settings.accessibility.acoustic.label": "Acoustic Time Machine",
        "settings.accessibility.acoustic.help": "Opt in to selected consequential audio replay. Retained audio is wiped after one minute, and disabling clears it immediately.",
        "settings.accessibility.voice_objects.label": "Voice Object Commands",
        "settings.accessibility.voice_objects.help": "Opt in to exact spoken commands becoming inert local drafts. Nothing is sent or scheduled.",
        "settings.accessibility.voice_objects.inspector": "Voice Inbox inspector",
        "settings.accessibility.voice_objects.chooser": "Queued draft metadata",
        "settings.accessibility.voice_objects.content": "Selected inert draft content",
        "settings.accessibility.voice_objects.copy": "Copy the revealed task or calendar draft after confirmation",
        "settings.accessibility.voice_objects.clear_clipboard": "Clear the copied draft only if the Mac clipboard is still unchanged",
        "settings.accessibility.tones_summary.label": "App tones summary",
        "settings.accessibility.snippets_summary.label": "Snippets summary",
        "settings.accessibility.vocabulary_summary.label": "Vocabulary summary",
        "settings.accessibility.corrections_summary.label": "Learned corrections summary",
        "settings.accessibility.keywords_summary.label": "Pronunciation keyword evidence",
        "settings.notice.loaded": "Settings loaded",
        "settings.notice.tone_saved": "App tone saved",
        "settings.notice.snippet_saved": "Snippet saved",
        "settings.notice.snippet_deleted": "Snippet deleted",
        "settings.notice.vocabulary_saved": "Vocabulary saved",
        "settings.notice.correction_forgotten": "Learned correction forgotten",
        "settings.notice.keyword_exported": "Pronunciation keyword export copied",
        "settings.notice.keyword_forgotten": "Pronunciation keyword forgotten",
        "settings.notice.keywords_forgotten": "All pronunciation keywords forgotten",
        "default.model.name": "Model",
        "default.status.unknown": "Unknown",
        "default.capture.ready": "Ready",
        "default.capture.paused": "Paused",
        "default.flight.off": "Off",
        "default.privacy.local": "Local processing",
        "default.build.development": "Development build",
        "results.consequence.empty": "Consequence: Standard · no protected spans",
        "validation.tone.selection": "app tone selection is out of range",
        "validation.section.unknown": "unknown section: {section}",
        "validation.settings_pane.unknown": "unknown settings pane: {pane}",
        "validation.app.bundle": "app identifier must be a non-empty bundle ID",
        "validation.tone.unsupported": "unsupported tone: {tone}",
        "validation.snippet.name": "snippet name must be 1–80 characters on one line",
        "validation.snippet.text": "snippet text must be 1–4000 characters",
        "validation.snippet.required": "snippet name is required",
        "validation.snippet.expected": "expected snippet text must be a string",
        "validation.vocabulary.preferred": "preferred vocabulary",
        "validation.vocabulary.excluded": "excluded vocabulary",
        "validation.vocabulary.list": "{label} must be a list of terms",
        "validation.vocabulary.term_length": "{label} terms must be at most 80 characters",
        "validation.vocabulary.reserved": "{label} terms cannot start with reserved '-' or '#'",
        "validation.vocabulary.maximum": "{label} supports at most 500 terms",
        "validation.vocabulary.overlap": "a term cannot also be excluded",
        "validation.correction.kind": "unknown learned correction kind",
        "validation.correction.unknown": "unknown learned correction",
        "validation.correction.stale_snippet": "the learned snippet edit no longer exists",
        "validation.keyword.unknown": "unknown pronunciation keyword",
        "validation.face.unsupported": "unsupported face: {face}",
        "operation.settings.load_failed": "Could not load settings: {error}",
        "operation.tone.save_failed": "Could not save app tone: {error}",
        "operation.snippet.save_failed": "Could not save snippet: {error}",
        "operation.snippet.delete_failed": "Could not delete snippet: {error}",
        "operation.vocabulary.save_failed": "Could not save vocabulary: {error}",
        "operation.correction.forget_failed": "Could not forget correction: {error}",
        "operation.keyword.inspect_failed": "Could not inspect pronunciation keywords: {error}",
        "operation.keyword.export_failed": "Could not export pronunciation keywords: {error}",
        "operation.keyword.forget_failed": "Could not forget pronunciation keyword: {error}",
        "operation.face.change_failed": "Could not change face: {error}",
        "operation.flight.update_failed": "Could not update Flight Recorder: {error}",
        "operation.acoustic.update_failed": "Could not update Acoustic Time Machine: {error}",
        "operation.relisten.update_failed": "Could not update Selective Re-listen: {error}",
        "operation.voice_objects.update_failed": "Could not update Voice Object Commands: {error}",
        "operation.voice_objects.inspect_failed": "Could not inspect local Voice Object drafts.",
        "operation.voice_objects.reveal_failed": "Could not reveal the selected local draft.",
        "operation.voice_objects.transition_failed": "Could not update the selected local draft.",
        "operation.voice_objects.purge_failed": "Could not purge finished local drafts.",
        "operation.voice_objects.compose_failed": "Could not request the email compose draft.",
        "operation.voice_objects.copy_failed": "Could not copy the selected task or calendar draft.",
        "operation.voice_objects.clear_failed": "Could not clear the copied draft from the Mac clipboard.",
        "operation.demonstrations.inspect_failed": "Could not inspect local demonstration drafts.",
        "operation.demonstrations.create_failed": "Could not create the local demonstration draft.",
        "operation.demonstrations.reveal_failed": "Could not reveal the selected demonstration draft.",
        "operation.demonstrations.record_failed": "Could not record that demonstration step.",
        "operation.demonstrations.approve_failed": "Could not approve the selected demonstration draft.",
        "operation.demonstrations.cancel_failed": "Could not cancel the selected demonstration draft.",
        "operation.demonstrations.delete_failed": "Could not delete the selected approved demonstration recipe.",
        "operation.risky_confirmation.start_failed": "Could not start the inert confirmation ceremony.",
        "operation.risky_confirmation.click_failed": "Confirmation stayed blocked; no valid voice receipt was followed by this click.",
        "operation.risky_confirmation.cancel_failed": "Could not cancel the inert confirmation ceremony.",
        "operation.acoustic.play_failed": "Could not play retained audio: {error}",
        "operation.acoustic.clear_failed": "Could not clear retained audio: {error}",
        "operation.log.open_failed": "Could not open log: {error}",
        "operation.system_settings.open_failed": "Could not open System Settings: {error}",
        "operation.support_snapshot.copy_failed": "Could not copy support snapshot: {error}",
        "operation.support_bundle.export_failed": "Could not save support bundle: {error}",
        "operation.source.open_failed": "Could not open source and license: {error}",
        "operation.licenses.open_failed": "Could not open local license notices: {error}",
        "settings.dialog.voice_objects.title": "Voice Inbox",
        "settings.dialog.voice_objects.message": "Only bounded draft metadata is listed. Select Reveal to read one draft. A revealed queued email can request a native compose window; a revealed queued task or calendar draft can be copied. Each action requires a separate confirmation.",
        "settings.dialog.voice_objects.empty": "No local Voice Object drafts are stored.",
        "settings.dialog.voice_objects.row": "Draft {sequence} · {destination} · {state}",
        "settings.dialog.voice_objects.reveal.title": "Draft {sequence} · {destination}",
        "settings.dialog.voice_objects.reveal.message": "Private local content only. Nothing is sent. A queued email can offer native compose; a queued task or calendar draft can offer clipboard copy. Either requires a separate confirmation.",
        "settings.dialog.voice_objects.compose.title": "Open this email compose draft?",
        "settings.dialog.voice_objects.compose.message": "This requests one macOS compose window for Draft {sequence}. It cannot send or auto-dispatch the email. Recipients, subject, and body stay in-process and never enter a URL, process argument, log, status, or receipt.",
        "settings.dialog.voice_objects.compose.result.requested": "Compose request handed to macOS",
        "settings.dialog.voice_objects.compose.result.unavailable": "Compose service unavailable",
        "settings.dialog.voice_objects.compose.result.invalid": "Email draft was rejected",
        "settings.dialog.voice_objects.compose.result.failed": "Compose request failed",
        "settings.dialog.voice_objects.compose.receipt": "Content-free receipt: state {state}; native compose request attempted {attempted}. Requested means only handed to the compose UI; it does not confirm a saved draft or send.",
        "settings.dialog.voice_objects.copy.title": "Copy this {destination} draft?",
        "settings.dialog.voice_objects.copy.message": "This freshly rechecks Draft {sequence}, then writes only its task or calendar text to the Mac clipboard. It does not paste, type, schedule, launch, send, acknowledge, delete, or access a network.",
        "settings.dialog.voice_objects.copy.result.copied": "Draft copied to the Mac clipboard",
        "settings.dialog.voice_objects.copy.result.unavailable": "Clipboard copy unavailable",
        "settings.dialog.voice_objects.copy.result.invalid": "Draft changed or was rejected",
        "settings.dialog.voice_objects.copy.result.failed": "Clipboard copy failed",
        "settings.dialog.voice_objects.copy.receipt": "Content-free receipt: state {state}; clipboard write attempted {attempted}. The queued draft remains in Voice Inbox. After a successful copy, Clear Clipboard is available while this app still owns the unchanged clipboard write.",
        "settings.dialog.voice_objects.clear.result.cleared": "Copied draft cleared from the Mac clipboard",
        "settings.dialog.voice_objects.clear.result.changed": "Clipboard changed — nothing was cleared",
        "settings.dialog.voice_objects.clear.result.unavailable": "Clipboard clear unavailable",
        "settings.dialog.voice_objects.clear.result.failed": "Clipboard clear failed",
        "settings.dialog.voice_objects.clear.receipt": "Content-free receipt: state {state}; clipboard clear attempted {attempted}. Whisper Face never read or retained clipboard content, and the queued draft remains in Voice Inbox.",
        "settings.dialog.voice_objects.ack.title": "Acknowledge this draft?",
        "settings.dialog.voice_objects.ack.message": "This marks Draft {sequence} finished without sending or executing it.",
        "settings.dialog.voice_objects.cancel.title": "Cancel this draft?",
        "settings.dialog.voice_objects.cancel.message": "This marks Draft {sequence} cancelled without sending or executing it.",
        "settings.dialog.voice_objects.purge.title": "Purge finished drafts?",
        "settings.dialog.voice_objects.purge.message": "Permanently remove acknowledged and cancelled local drafts. Queued drafts remain.",
        "settings.action.reveal": "Reveal",
        "settings.action.compose_email": "Open Compose Draft…",
        "settings.action.copy_draft": "Copy Draft…",
        "settings.action.clear_clipboard": "Clear Clipboard",
        "settings.action.acknowledge": "Acknowledge",
        "settings.action.cancel_draft": "Cancel Draft",
        "settings.action.purge_finished": "Purge Finished",
        "settings.notice.voice_object_acknowledged": "Local draft acknowledged",
        "settings.notice.voice_object_cancelled": "Local draft cancelled",
        "settings.notice.voice_objects_purged": "Finished local drafts purged: {count}",
        "settings.notice.demonstration_created": "Inert demonstration draft created",
        "settings.notice.demonstration_step_recorded": "Described step recorded; nothing was performed",
        "settings.notice.demonstration_approved": "Recipe approved and remains inert",
        "settings.notice.demonstration_cancelled": "Demonstration draft rolled back",
        "settings.notice.demonstration_deleted": "Approved demonstration recipe deleted",
        "diagnostics.notice.support_snapshot.copied": "Transcript-free support snapshot copied",
    },
}
SUPPORTED_LOCALES = tuple(STRING_CATALOGS)


def resolve_locale(locale: str | None) -> str:
    """Resolve an Apple-style language tag to an available catalog."""

    tag = str(locale or "").strip().replace("_", "-").casefold()
    for candidate in (tag, tag.split("-", 1)[0]):
        if candidate in STRING_CATALOGS:
            return candidate
    return "en"


def localized_string(key: str, *, locale: str = "en", **values: Any) -> str:
    """Return catalog copy with deterministic English fallback.

    Missing keys are programming errors and fail loudly in tests. An unknown
    locale falls back to English so a partially translated build remains usable.
    """

    catalog = STRING_CATALOGS[resolve_locale(locale)]
    template = catalog.get(key, STRING_CATALOGS["en"].get(key))
    if template is None:
        raise KeyError(f"unknown localized string: {key}")
    try:
        return template.format(**values)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid values for localized string {key!r}") from error
FACES = (
    "parrot", "fox", "owl", "cat", "bear",
    "dog", "wolf", "pig", "panda", "tiger",
)
FACE_EMOJI = {
    "parrot": "🦜",
    "fox": "🦊",
    "owl": "🦉",
    "cat": "🐱",
    "bear": "🐻",
    "dog": "🐶",
    "wolf": "🐺",
    "pig": "🐷",
    "panda": "🐼",
    "tiger": "🐯",
}


@dataclass(frozen=True)
class NativeAppKitSmokeContract:
    """Static contract Windows can validate without importing AppKit."""

    sections: tuple[str, ...]
    settings_panes: tuple[str, ...]
    model_actions: tuple[str, ...]
    accessibility_catalog_keys: tuple[str, ...]
    onboarding_steps: tuple[str, ...]
    key_equivalents: tuple[str, ...]
    locale_fallback: str
    allowed_side_effects: tuple[str, ...] = ()


def native_appkit_smoke_contract() -> NativeAppKitSmokeContract:
    """Describe the deterministic native gate without executing native code."""

    return NativeAppKitSmokeContract(
        sections=SECTIONS,
        settings_panes=SETTINGS_PANES,
        model_actions=(
            "select_section",
            "select_settings_pane",
            "set_app_tone",
            "save_snippet",
            "delete_snippet",
            "save_vocabulary",
            "forget_correction",
            "forget_snippet",
            "inspect_acoustic_keywords",
            "export_acoustic_keywords",
            "forget_acoustic_keyword",
            "forget_all_acoustic_keywords",
            "choose_face",
            "set_flight_recorder",
            "set_acoustic_time_machine",
            "set_selective_relisten",
            "set_voice_object_commands",
            "inspect_voice_object_drafts",
            "reveal_voice_object_draft",
            "issue_voice_object_email_compose_nonce",
            "compose_voice_object_email",
            "issue_voice_object_copy_nonce",
            "copy_voice_object_draft",
            "issue_voice_object_clear_clipboard_nonce",
            "clear_voice_object_draft_clipboard",
            "acknowledge_voice_object_draft",
            "cancel_voice_object_draft",
            "purge_terminal_voice_object_drafts",
            "inspect_demonstration_drafts",
            "create_demonstration_draft",
            "reveal_demonstration_draft",
            "record_demonstration_step",
            "approve_demonstration_draft",
            "cancel_demonstration_draft",
            "delete_approved_demonstration_draft",
            "start_risky_action_confirmation",
            "click_risky_action_confirmation",
            "cancel_risky_action_confirmation",
            "play_retained_span",
            "clear_retained_spans",
            "preview_point_and_speak",
            "issue_point_and_speak_nonce",
            "press_point_and_speak",
            "preview_drop_to_target",
            "open_system_settings",
            "acknowledge_onboarding",
        ),
        accessibility_catalog_keys=(
            "overview.accessibility.phase",
            "overview.accessibility.status",
            "overview.accessibility.detail",
            "overview.accessibility.engine",
            "overview.accessibility.outbox",
            "overview.accessibility.last",
            "overview.accessibility.words",
            "overview.accessibility.saved",
            "overview.accessibility.onboarding.progress",
            "overview.accessibility.onboarding.title",
            "overview.accessibility.onboarding.detail",
            "overview.accessibility.onboarding.face",
            "overview.accessibility.onboarding.steps",
            "overview.accessibility.onboarding.step",
            "settings.accessibility.sections.label",
            "settings.accessibility.category.label",
            "settings.accessibility.face.label",
            "settings.accessibility.flight.label",
            "settings.accessibility.acoustic.label",
            "settings.accessibility.voice_objects.label",
            "settings.accessibility.voice_objects.inspector",
            "settings.accessibility.voice_objects.chooser",
            "settings.accessibility.voice_objects.content",
            "settings.accessibility.voice_objects.copy",
            "settings.accessibility.voice_objects.clear_clipboard",
            "settings.accessibility.modes.label",
            "settings.dialog.tone.app.label",
            "settings.dialog.tone.choice.label",
            "settings.dialog.snippet.chooser.label",
            "settings.dialog.snippet.name",
            "settings.dialog.snippet.value",
            "settings.dialog.vocabulary.terms",
            "settings.dialog.vocabulary.bans",
            "settings.dialog.correction.chooser.label",
            "settings.dialog.keywords.chooser.label",
            "results.accessibility.audio",
            "models.accessibility.guidance",
            "models.accessibility.wallet",
            "models.accessibility.relisten",
            "diagnostics.accessibility.verification",
            "diagnostics.accessibility.open_system_settings",
        ),
        onboarding_steps=(
            "permissions", "hotkey", "models", "first_dictation"),
        key_equivalents=(
            "return:continue-setup",
            "command-d:advanced",
            "command-r:verification",
        ),
        locale_fallback="en",
    )


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


@dataclass(frozen=True)
class GUIActions:
    """Integration API supplied by the running Whisper Face application."""

    status_snapshot: Callable[[], Mapping[str, Any]] = lambda: {}
    inspect_result_evidence: Callable[[], Mapping[str, Any]] = lambda: {}
    settings_snapshot: Callable[[], Mapping[str, Any]] = lambda: {}
    set_face: Callable[[str], None] = _noop
    set_flight_recorder: Callable[[bool], None] = _noop
    set_acoustic_time_machine: Callable[[bool], None] = _noop
    set_selective_relisten: Callable[[bool], None] = _noop
    set_voice_object_commands: Callable[[bool], None] = _noop
    inspect_voice_object_drafts: Callable[[], Sequence[Mapping[str, Any]]] = (
        lambda: ())
    reveal_voice_object_draft: Callable[[str], Mapping[str, Any] | None] = (
        lambda _item_id: None)
    issue_voice_object_email_compose_nonce: Callable[[], str] = lambda: ""
    compose_voice_object_email: Callable[[str, str], Mapping[str, Any]] = (
        lambda _nonce, _item_id: {})
    issue_voice_object_copy_nonce: Callable[[], str] = lambda: ""
    copy_voice_object_draft: Callable[
        [str, str, str], Mapping[str, Any]
    ] = lambda _nonce, _item_id, _destination: {}
    issue_voice_object_clear_clipboard_nonce: Callable[[], str] = lambda: ""
    clear_voice_object_draft_clipboard: Callable[
        [str], Mapping[str, Any]
    ] = lambda _nonce: {}
    acknowledge_voice_object_draft: Callable[[str], bool] = (
        lambda _item_id: False)
    cancel_voice_object_draft: Callable[[str], bool] = lambda _item_id: False
    purge_terminal_voice_object_drafts: Callable[[], int | None] = lambda: None
    inspect_demonstration_drafts: Callable[[], Sequence[Mapping[str, Any]]] = (
        lambda: ())
    create_demonstration_draft: Callable[[str], Mapping[str, Any] | None] = (
        lambda _domain: None)
    reveal_demonstration_draft: Callable[[str], Mapping[str, Any] | None] = (
        lambda _draft_id: None)
    record_demonstration_step: Callable[[str, str, str], bool] = (
        lambda _draft_id, _action, _text: False)
    approve_demonstration_draft: Callable[[str], bool] = (
        lambda _draft_id: False)
    cancel_demonstration_draft: Callable[[str], bool] = (
        lambda _draft_id: False)
    delete_approved_demonstration_draft: Callable[[str], bool] = (
        lambda _draft_id: False)
    start_risky_action_confirmation: Callable[[str], bool] = (
        lambda _risk: False)
    click_risky_action_confirmation: Callable[[], bool] = lambda: False
    cancel_risky_action_confirmation: Callable[[], bool] = lambda: False
    play_retained_span: Callable[[], bool] = lambda: False
    clear_retained_spans: Callable[[], None] = _noop
    set_app_tone: Callable[[str, str], None] = _noop
    save_snippet: Callable[[str, str | None, str], None] = _noop
    delete_snippet: Callable[[str, str], None] = _noop
    save_vocabulary: Callable[[Sequence[str], Sequence[str]], None] = _noop
    forget_correction: Callable[[str], None] = _noop
    forget_snippet_edit: Callable[[str], Any] = _noop
    inspect_acoustic_keywords: Callable[[], Mapping[str, Any]] = lambda: {}
    export_acoustic_keywords: Callable[[], None] = _noop
    forget_acoustic_keyword: Callable[[str, str | None], Any] = _noop
    forget_all_acoustic_keywords: Callable[[], Any] = _noop
    pause: Callable[[], None] = _noop
    resume: Callable[[], None] = _noop
    open_log: Callable[[], None] = _noop
    open_system_settings: Callable[[], None] = _noop
    copy_support_snapshot: Callable[[str], None] = _noop
    open_source_and_license: Callable[[], None] = _noop
    open_local_license_notices: Callable[[], None] = _noop
    copy_latest_outbox: Callable[[], None] = _noop
    preview_point_and_speak: Callable[[str], Mapping[str, Any]] = (
        lambda _phrase: {})
    issue_point_and_speak_nonce: Callable[[], str] = lambda: ""
    press_point_and_speak: Callable[[str, str, str], Mapping[str, Any]] = (
        lambda _nonce, _phrase, _role: {})
    preview_drop_to_target: Callable[
        [str, str, str, str], Mapping[str, Any]
    ] = lambda _phrase, _role, _source_kind, _effect: {}
    rerun_verification: Callable[[], Any] = _noop


@dataclass(frozen=True)
class ModelStatus:
    name: str
    role: str = ""
    status: str = localized_string("default.status.unknown")
    detail: str = ""


@dataclass(frozen=True)
class VoiceDraftMetadata:
    """Content-free identity used only during an explicit inspector session."""

    item_id: str = field(repr=False)
    sequence: int
    destination: str
    state: str


@dataclass(frozen=True)
class RevealedVoiceDraft:
    """Private content returned transiently after an explicit reveal action."""

    sequence: int
    destination: str
    state: str
    content: str = field(repr=False)


@dataclass(frozen=True)
class EmailComposeReceipt:
    """Content-free terminal evidence for one explicit native request."""

    state: str
    attempted: bool


@dataclass(frozen=True)
class VoiceDraftCopyReceipt:
    """Content-free terminal evidence for one explicit clipboard write."""

    state: str
    attempted: bool


@dataclass(frozen=True)
class VoiceDraftClearReceipt:
    """Content-free terminal evidence for one explicit clipboard clear."""

    state: str
    attempted: bool


def normalize_email_compose_receipt(
    snapshot: Mapping[str, Any] | None,
) -> EmailComposeReceipt:
    """Validate the closed receipt; no payload or item identity is accepted."""

    if (not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "state", "attempted"}
            or snapshot.get("schema_version") != 1
            or snapshot.get("state") not in EMAIL_COMPOSE_STATES
            or not isinstance(snapshot.get("attempted"), bool)
            or (snapshot["state"] in {"requested", "failed"}) !=
            snapshot["attempted"]):
        raise ValueError("Email compose receipt is malformed")
    return EmailComposeReceipt(
        state=snapshot["state"], attempted=snapshot["attempted"])


def normalize_voice_draft_copy_receipt(
    snapshot: Mapping[str, Any] | None,
) -> VoiceDraftCopyReceipt:
    """Validate the closed receipt; no payload or item identity is accepted."""

    if (not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "state", "attempted"}
            or snapshot.get("schema_version") != 1
            or snapshot.get("state") not in VOICE_DRAFT_COPY_STATES
            or not isinstance(snapshot.get("attempted"), bool)
            or (snapshot["state"] in {"copied", "failed"}) !=
            snapshot["attempted"]):
        raise ValueError("Voice draft copy receipt is malformed")
    return VoiceDraftCopyReceipt(
        state=snapshot["state"], attempted=snapshot["attempted"])


def normalize_voice_draft_clear_receipt(
    snapshot: Mapping[str, Any] | None,
) -> VoiceDraftClearReceipt:
    """Validate closed clear evidence without accepting clipboard content."""

    if (not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "state", "attempted"}
            or snapshot.get("schema_version") != 1
            or snapshot.get("state") not in VOICE_DRAFT_CLEAR_STATES
            or not isinstance(snapshot.get("attempted"), bool)
            or (snapshot["state"] in {"cleared", "failed"}) !=
            snapshot["attempted"]):
        raise ValueError("Voice draft clear receipt is malformed")
    return VoiceDraftClearReceipt(
        state=snapshot["state"], attempted=snapshot["attempted"])


@dataclass(frozen=True)
class DemonstrationDraftMetadata:
    """Content-free identity from an explicit demonstration inspection."""

    draft_id: str = field(repr=False)
    sequence: int
    domain: str
    state: str
    step_count: int


@dataclass(frozen=True)
class DemonstrationStepPreview:
    """One explicitly revealed private description, redacted from repr."""

    action: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class RevealedDemonstrationDraft:
    """Private recipe returned transiently only after Reveal/Edit."""

    sequence: int
    domain: str
    state: str
    steps: tuple[DemonstrationStepPreview, ...] = field(repr=False)


@dataclass(frozen=True)
class AppToneSetting:
    bundle: str
    name: str
    tone: str = "auto"


@dataclass(frozen=True)
class SnippetSetting:
    name: str
    text: str


@dataclass(frozen=True)
class CorrectionScopeSetting:
    bundle: str
    name: str
    count: int = 0
    decision: str = "learning"


@dataclass(frozen=True)
class CorrectionSetting:
    key: str
    source: str
    target: str
    count: int = 0
    kind: str = "correction"
    global_decision: str = "learning"
    app_scopes: tuple[CorrectionScopeSetting, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AcousticKeywordCandidate:
    """One private candidate returned only after explicit inspection."""

    keyword: str
    app_scope: str | None
    observations: int
    confirmations: int
    eligible: bool


@dataclass(frozen=True)
class AcousticKeywordInspection:
    """Strict, token-free projection for the on-demand Settings dialog."""

    candidates: tuple[AcousticKeywordCandidate, ...] = field(
        default_factory=tuple)
    recognition_effect: str = "none"


@dataclass(frozen=True)
class PointAndSpeakReceipt:
    """Content-free aggregate evidence for one explicit preview."""

    capture_state: str
    observed_elements: int
    emitted_targets: int
    skipped_elements: int
    truncated: bool
    observed_targets: int
    eligible_targets: int
    contradiction_count: int
    evidence: tuple[str, ...] = field(default_factory=tuple)
    confidence_bucket: str = "none"
    margin_bucket: str = "none"


@dataclass(frozen=True, repr=False)
class PointAndSpeakPreview:
    """Transient preview result; the selected accessible name stays private."""

    state: str
    accessibility_name: str = field(default="", repr=False)
    role: str = ""
    receipt: PointAndSpeakReceipt = field(default_factory=lambda:
        PointAndSpeakReceipt(
            "unavailable", 0, 0, 0, False, 0, 0, 0))


@dataclass(frozen=True)
class PointAndSpeakActionReceipt:
    """Content-free evidence for one explicit, exactly-once press request."""

    capture_state: str
    observed_elements: int
    emitted_targets: int
    truncated: bool
    eligible_targets: int
    contradiction_count: int
    evidence: tuple[str, ...]
    confidence_bucket: str
    margin_bucket: str
    transaction_state: str
    attempted: bool
    recheck: str


@dataclass(frozen=True)
class PointAndSpeakActionResult:
    """No phrase, accessible name, target id, or native identity is retained."""

    state: str
    receipt: PointAndSpeakActionReceipt


def unavailable_point_and_speak_action() -> PointAndSpeakActionResult:
    """Build the fixed fallback that lets the GUI always unhide safely."""

    return PointAndSpeakActionResult(
        state="unavailable",
        receipt=PointAndSpeakActionReceipt(
            capture_state="unavailable",
            observed_elements=0,
            emitted_targets=0,
            truncated=False,
            eligible_targets=0,
            contradiction_count=0,
            evidence=(),
            confidence_bucket="none",
            margin_bucket="none",
            transaction_state="unavailable",
            attempted=False,
            recheck="not_run",
        ),
    )


@dataclass(frozen=True)
class DropTargetReceipt:
    """Content-free capability and decision evidence for one preview."""

    capture_state: str
    observed_elements: int
    emitted_targets: int
    skipped_elements: int
    truncated: bool
    observed_targets: int
    eligible_targets: int
    contradiction_count: int
    evidence: tuple[str, ...] = field(default_factory=tuple)
    confidence_bucket: str = "none"
    margin_bucket: str = "none"
    capability_basis: str = "caller_declared_role_policy"
    execution: str = "none"


@dataclass(frozen=True, repr=False)
class DropTargetPreview:
    """Transient inert result; accessible target text is never GUI state."""

    state: str
    accessibility_name: str = field(default="", repr=False)
    role: str = ""
    declared_role: str = ""
    source_kind: str = ""
    effect: str = ""
    receipt: DropTargetReceipt = field(default_factory=lambda:
        DropTargetReceipt(
            "unavailable", 0, 0, 0, False, 0, 0, 0))


@dataclass(frozen=True)
class UnifiedSettings:
    app_tones: tuple[AppToneSetting, ...] = field(default_factory=tuple)
    snippets: tuple[SnippetSetting, ...] = field(default_factory=tuple)
    manual_vocabulary: tuple[str, ...] = field(default_factory=tuple)
    banned_vocabulary: tuple[str, ...] = field(default_factory=tuple)
    corrections: tuple[CorrectionSetting, ...] = field(default_factory=tuple)


def tone_for_app_index(
    apps: Sequence[AppToneSetting], index: int, *, locale: str = "en",
) -> str:
    """Resolve the persisted tone for one AppKit popup selection."""
    if not isinstance(index, int) or isinstance(index, bool) \
            or not 0 <= index < len(apps):
        raise IndexError(localized_string(
            "validation.tone.selection", locale=locale))
    tone = apps[index].tone
    return tone if tone in TONE_CHOICES else "auto"


@dataclass(frozen=True)
class OnboardingStep:
    """One truthful, non-blocking first-run readiness checkpoint."""

    key: str
    title: str
    detail: str
    status: str
    complete: bool = False


@dataclass(frozen=True)
class OnboardingPresentation:
    """Localized first-run card state derived only from readiness evidence."""

    visible: bool
    complete: bool
    current_key: str | None
    progress: str
    title: str
    detail: str
    status: str
    action_title: str
    action_help: str


@dataclass(frozen=True)
class DegradedIssue:
    """A local recovery hint; ``error`` affects the main readiness state."""

    key: str
    title: str
    detail: str
    route: str = "Advanced"
    severity: str = "error"


@dataclass(frozen=True, repr=False)
class ProofEditInspection:
    kind: str
    before: str = field(repr=False)
    after: str = field(repr=False)
    accepted: bool = False
    reason: str = ""


@dataclass(frozen=True)
class StageTiming:
    stage: str
    milliseconds: float


@dataclass(frozen=True, repr=False)
class ResultEvidenceInspection:
    """Private details returned transiently after an explicit reveal."""

    alternatives: tuple[str, ...] = field(default_factory=tuple, repr=False)
    protected_anchors: tuple[str, ...] = field(
        default_factory=tuple, repr=False)
    proof_edits: tuple[ProofEditInspection, ...] = field(
        default_factory=tuple, repr=False)
    timings: tuple[StageTiming, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResultInspection:
    """Privacy-safe evidence for the latest result, never transcript history."""

    available: bool = False
    summary: str = localized_string("results.summary.empty")
    engine: str = localized_string("results.engine.waiting")
    mode: str = localized_string("results.mode.capture")
    stable_prefix_words: int = 0
    compiler_decisions: int = 0
    confidence: float | None = None
    cleanup_edits: tuple[str, ...] = field(default_factory=tuple)
    proof_edits_accepted: int = 0
    proof_edits_rejected: int | None = None
    protected_anchor_count: int = 0
    alternatives_considered: int = 0
    context_influence: str = localized_string("results.context.unreported")
    context_firewall_summary: str = localized_string(
        "results.firewall.unavailable")
    consequence_summary: str = localized_string("results.consequence.empty")
    consequence_advisory: str = ""
    retained_span_count: int = 0
    acoustic_replay_enabled: bool = False


@dataclass(frozen=True)
class GUIState:
    section: str = "Home"
    capture_state: str = localized_string("default.capture.ready")
    paused: bool = False
    face: str = "parrot"
    flight_recorder: bool = False
    flight_state: str = localized_string("default.flight.off")
    acoustic_time_machine: bool = False
    selective_relisten_requested: bool = False
    selective_relisten_evidence_ready: bool = False
    selective_relisten_status: str = "receipt-missing"
    voice_object_commands: bool = False
    voice_object_inbox_count: int = 0
    voice_object_inbox_status: str = "Off"
    risky_action_risk: str = "none"
    risky_action_confirmation_state: str = "idle"
    active_engine: str = localized_string("overview.engine.waiting")
    last_latency_ms: float | None = None
    last_word_count: int | None = None
    words_today: int = 0
    minutes_saved: float = 0.0
    outbox_count: int = 0
    outbox_summary: str = ""
    regression_cases: int = 0
    regression_quarantined: int = 0
    privacy_summary: str = localized_string("default.privacy.local")
    service_status: str = localized_string("default.status.unknown")
    microphone_status: str = localized_string("default.status.unknown")
    accessibility_status: str = localized_string("default.status.unknown")
    version: str = localized_string("default.build.development")
    models: tuple[ModelStatus, ...] = field(default_factory=tuple)
    model_wallet_advisory: str = localized_string(
        "models.wallet.unavailable")
    hotkey_label: str = localized_string("settings.mode.capture.shortcut")
    prefers_reduced_motion: bool = False
    onboarding_steps: tuple[OnboardingStep, ...] = field(default_factory=tuple)
    onboarding_complete: bool = False
    onboarding_acknowledged: bool = False
    status_phase: str = "ready"
    status_title: str = localized_string("overview.status.ready.title")
    status_detail: str = localized_string(
        "overview.status.ready.detail", hotkey=localized_string(
            "settings.mode.capture.shortcut"))
    degraded_issues: tuple[DegradedIssue, ...] = field(default_factory=tuple)
    last_result: ResultInspection = field(default_factory=ResultInspection)
    verification: str = localized_string("diagnostics.verification.not_run")
    notice: str = ""
    notice_level: str = "info"
    settings_pane: str = "Personalize"
    settings: UnifiedSettings = field(default_factory=UnifiedSettings)


def _support_status(value: object) -> str:
    """Collapse runtime display copy into a fixed non-content status."""
    normalized = str(value).strip().casefold()
    return {
        "running": "running",
        "ready": "ready",
        "granted": "granted",
        "installed": "installed",
        "starting": "starting",
        "checking": "checking",
        "unavailable": "unavailable",
        "unknown": "unknown",
    }.get(normalized, "unknown")


def _support_model_family(value: object) -> str:
    """Classify a known local model without copying its display label."""
    normalized = str(value).strip().casefold()
    for marker, family in (
        ("parakeet", "parakeet"),
        ("whisper", "whisper"),
        ("qwen", "qwen"),
    ):
        if marker in normalized:
            return family
    return "unknown"


def _support_mode(value: object) -> str:
    normalized = str(value).strip().casefold()
    return normalized if normalized in {
        "capture", "compose", "edit", "reply", "command", "code",
    } else "unknown"


def support_snapshot_text(state: GUIState) -> str:
    """Serialize only the fixed, transcript-free support allowlist.

    The native clipboard callback receives this completed payload, rather than
    a runtime snapshot, so private dictionaries, logs, text, and contextual
    capture data never cross the GUI integration boundary.
    """
    result = state.last_result
    payload = {
        "kind": "whisper-face/support-snapshot",
        "schema_version": 1,
        "health": {
            "service_status": _support_status(state.service_status),
            "microphone_status": _support_status(state.microphone_status),
        },
        "permissions": {
            "accessibility_status": _support_status(
                state.accessibility_status),
        },
        "build": (
            "local-checkout"
            if state.version.strip().casefold() == "local checkout"
            else "unknown"),
        "models": [
            {
                "family": _support_model_family(model.name),
                "status": _support_status(model.status),
            }
            for model in sorted(
                state.models,
                key=lambda model: (
                    _support_model_family(model.name),
                    _support_status(model.status),
                ))
        ],
        "last_result": {
            "available": result.available,
            "engine": _support_model_family(result.engine),
            "mode": _support_mode(result.mode),
            "latency_ms": state.last_latency_ms,
            "word_count": state.last_word_count,
            "confidence": result.confidence,
            "stable_prefix_words": result.stable_prefix_words,
            "compiler_decisions": result.compiler_decisions,
            "protected_anchor_count": result.protected_anchor_count,
            "alternatives_considered": result.alternatives_considered,
            "cleanup_edits_count": len(result.cleanup_edits),
            "proof_edits_accepted": result.proof_edits_accepted,
            "proof_edits_rejected": result.proof_edits_rejected,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _clean_text(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative_int(value: Any, default: int = 0) -> int:
    number = _finite_number(value)
    return max(0, int(number)) if number is not None else default


def _normalize_models(
    value: Any, *, locale: str = "en",
) -> tuple[ModelStatus, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    models: list[ModelStatus] = []
    for item in value:
        if isinstance(item, Mapping):
            name = _clean_text(item.get("name"), localized_string(
                "default.model.name", locale=locale))
            models.append(ModelStatus(
                name=name,
                role=_clean_text(item.get("role"), ""),
                status=_clean_text(item.get("status"), localized_string(
                    "default.status.unknown", locale=locale)),
                detail=_clean_text(item.get("detail"), ""),
            ))
        elif isinstance(item, str) and item.strip():
            models.append(ModelStatus(
                item.strip(), status=localized_string(
                    "default.status.unknown", locale=locale)))
    return tuple(models)


def _model_wallet_advisory_copy(value: Any, *, locale: str = "en") -> str:
    """Validate the closed receipt and return fixed, content-free UI copy."""
    unavailable = localized_string("models.wallet.unavailable", locale=locale)
    if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "mode", "pins", "capabilities", "attempted"}:
        return unavailable
    if (value.get("schema_version") != 1
            or value.get("mode") != "shadow-only"
            or value.get("attempted") is not False):
        return unavailable
    capabilities = value.get("capabilities")
    pins = value.get("pins")
    if (isinstance(pins, (str, bytes)) or not isinstance(pins, Sequence)
            or len(pins) != len(MODEL_WALLET_PROVIDER_IDS)):
        return unavailable
    pin_ids = set()
    resolved_count = warm_count = 0
    for pin in pins:
        if not isinstance(pin, Mapping) or set(pin) != {
                "provider_id", "resolution_state", "warm_path_observed",
                "revision_verified", "capability_bounds_attested"}:
            return unavailable
        provider_id = pin.get("provider_id")
        resolution = pin.get("resolution_state")
        warm = pin.get("warm_path_observed")
        verified = pin.get("revision_verified")
        if (provider_id not in MODEL_WALLET_PROVIDER_IDS
                or provider_id in pin_ids
                or resolution not in {
                    "resolved", "not_installed", "load_failed",
                    "revision_mismatch", "unavailable"}
                or not isinstance(warm, bool)
                or not isinstance(verified, bool)
                or pin.get("capability_bounds_attested") is not False
                or (resolution == "resolved") != verified
                or (warm and not verified)):
            return unavailable
        pin_ids.add(provider_id)
        resolved_count += int(verified)
        warm_count += int(warm)
    if pin_ids != MODEL_WALLET_PROVIDER_IDS:
        return unavailable
    if (isinstance(capabilities, (str, bytes))
            or not isinstance(capabilities, Sequence)
            or len(capabilities) != len(MODEL_WALLET_CAPABILITIES)):
        return unavailable
    seen = set()
    any_fail_closed = False
    for receipt in capabilities:
        if not isinstance(receipt, Mapping) or set(receipt) != {
                "capability", "providers", "advisory_order",
                "selected_provider_id", "fail_closed", "attempted"}:
            return unavailable
        capability = receipt.get("capability")
        providers = receipt.get("providers")
        order = receipt.get("advisory_order")
        selected = receipt.get("selected_provider_id")
        fail_closed = receipt.get("fail_closed")
        if (not isinstance(capability, str)
                or capability not in MODEL_WALLET_CAPABILITIES
                or capability in seen
                or receipt.get("attempted") is not False
                or not isinstance(fail_closed, bool)
                or isinstance(providers, (str, bytes))
                or not isinstance(providers, Sequence)
                or isinstance(order, (str, bytes))
                or not isinstance(order, Sequence)):
            return unavailable
        seen.add(capability)
        provider_ids = []
        eligible_ids = []
        for provider in providers:
            if not isinstance(provider, Mapping) or set(provider) != {
                    "provider_id", "eligibility"}:
                return unavailable
            provider_id = provider.get("provider_id")
            eligibility = provider.get("eligibility")
            if (not isinstance(provider_id, str)
                    or provider_id not in MODEL_WALLET_PROVIDER_IDS
                    or provider_id in provider_ids
                    or not isinstance(eligibility, str)
                    or eligibility not in MODEL_WALLET_ELIGIBILITIES):
                return unavailable
            supported = (
                provider_id in MODEL_WALLET_SUPPORTED_PROVIDERS[capability])
            if ((eligibility == "unsupported_capability")
                    != (not supported)):
                return unavailable
            provider_ids.append(provider_id)
            if eligibility == "eligible":
                eligible_ids.append(provider_id)
        if (set(provider_ids) != MODEL_WALLET_PROVIDER_IDS
                or any(not isinstance(item, str) for item in order)
                or any(item not in eligible_ids for item in order)
                or len(set(order)) != len(order)
                or set(order) != set(eligible_ids)
                or selected != (order[0] if order else None)
                or fail_closed != (not order)):
            return unavailable
        any_fail_closed = any_fail_closed or fail_closed
    if seen != MODEL_WALLET_CAPABILITIES:
        return unavailable
    if not pins:
        return unavailable
    return localized_string(
        "models.wallet.evidence", locale=locale,
        resolved=resolved_count, warm=warm_count)


def _text_items(value: Any, *, maximum: int = 500) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    items: list[str] = []
    seen: set[str] = set()
    for raw in value[:maximum]:
        item = str(raw).strip()
        folded = item.casefold()
        if not item or folded in seen:
            continue
        items.append(item)
        seen.add(folded)
    return tuple(items)


def normalize_point_and_speak_preview(
    snapshot: Mapping[str, Any] | None,
) -> PointAndSpeakPreview:
    """Validate the closed, transient Point-and-Speak preview projection."""

    if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "state", "accessibility_name", "role",
            "receipt"} or snapshot.get("schema_version") != 1:
        raise ValueError("Point-and-Speak preview is malformed")
    state = snapshot.get("state")
    name = snapshot.get("accessibility_name")
    role = snapshot.get("role")
    raw_receipt = snapshot.get("receipt")
    if (state not in POINT_AND_SPEAK_STATES
            or not isinstance(name, str) or len(name) > 128
            or any(ord(character) < 32 for character in name)
            or not isinstance(role, str)
            or not isinstance(raw_receipt, Mapping)
            or set(raw_receipt) != {
                "schema_version", "capture_state", "observed_elements",
                "emitted_targets", "skipped_elements", "truncated",
                "observed_targets", "eligible_targets",
                "contradiction_count", "evidence", "confidence_bucket",
                "margin_bucket",
            }
            or raw_receipt.get("schema_version") != 1):
        raise ValueError("Point-and-Speak preview is malformed")

    integer_keys = (
        "observed_elements", "emitted_targets", "skipped_elements",
        "observed_targets", "eligible_targets", "contradiction_count",
    )
    if any(
        not isinstance(raw_receipt.get(key), int)
        or isinstance(raw_receipt.get(key), bool)
        or not 0 <= raw_receipt[key] <= 2_048
        for key in integer_keys
    ):
        raise ValueError("Point-and-Speak preview is malformed")
    evidence = raw_receipt.get("evidence")
    if (not isinstance(raw_receipt.get("truncated"), bool)
            or raw_receipt.get("capture_state") not in
            POINT_AND_SPEAK_CAPTURE_STATES
            or not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes))
            or len(evidence) > len(POINT_AND_SPEAK_EVIDENCE)
            or len(set(evidence)) != len(evidence)
            or any(item not in POINT_AND_SPEAK_EVIDENCE for item in evidence)
            or raw_receipt.get("confidence_bucket") not in {
                "none", "below_threshold", "high", "very_high"}
            or raw_receipt.get("margin_bucket") not in {
                "none", "narrow", "sufficient", "wide"}
            or raw_receipt["emitted_targets"] > 256
            or raw_receipt["observed_targets"] !=
            raw_receipt["emitted_targets"]
            or raw_receipt["eligible_targets"] >
            raw_receipt["observed_targets"]):
        raise ValueError("Point-and-Speak preview is malformed")

    capture_state = raw_receipt["capture_state"]
    if capture_state != "captured" and (
            state != capture_state
            or any(raw_receipt[key] != 0 for key in integer_keys)
            or raw_receipt["truncated"] or evidence
            or raw_receipt["confidence_bucket"] != "none"
            or raw_receipt["margin_bucket"] != "none"):
        raise ValueError("Point-and-Speak preview is malformed")
    if state == "resolved":
        if (capture_state != "captured" or not name.strip()
                or role not in POINT_AND_SPEAK_ROLES):
            raise ValueError("Point-and-Speak preview is malformed")
    elif name or role:
        raise ValueError("Point-and-Speak preview is malformed")
    if state == "permission_denied" and capture_state != "permission_denied":
        raise ValueError("Point-and-Speak preview is malformed")

    return PointAndSpeakPreview(
        state=state,
        accessibility_name=name.strip(),
        role=role,
        receipt=PointAndSpeakReceipt(
            capture_state=capture_state,
            observed_elements=raw_receipt["observed_elements"],
            emitted_targets=raw_receipt["emitted_targets"],
            skipped_elements=raw_receipt["skipped_elements"],
            truncated=raw_receipt["truncated"],
            observed_targets=raw_receipt["observed_targets"],
            eligible_targets=raw_receipt["eligible_targets"],
            contradiction_count=raw_receipt["contradiction_count"],
            evidence=tuple(evidence),
            confidence_bucket=raw_receipt["confidence_bucket"],
            margin_bucket=raw_receipt["margin_bucket"],
        ),
    )


def normalize_point_and_speak_action(
    snapshot: Mapping[str, Any] | None,
) -> PointAndSpeakActionResult:
    """Validate the closed, content-free Point-and-Speak action receipt."""

    if (not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "state", "receipt"}
            or snapshot.get("schema_version") != 1
            or snapshot.get("state") not in POINT_AND_SPEAK_ACTION_STATES):
        raise ValueError("Point-and-Speak action is malformed")
    raw = snapshot.get("receipt")
    if not isinstance(raw, Mapping) or set(raw) != {
            "schema_version", "capture_state", "observed_elements",
            "emitted_targets", "truncated", "eligible_targets",
            "contradiction_count", "evidence", "confidence_bucket",
            "margin_bucket", "transaction"} or raw.get("schema_version") != 1:
        raise ValueError("Point-and-Speak action is malformed")
    integer_keys = (
        "observed_elements", "emitted_targets", "eligible_targets",
        "contradiction_count",
    )
    evidence = raw.get("evidence")
    transaction = raw.get("transaction")
    if (any(not isinstance(raw.get(key), int)
            or isinstance(raw.get(key), bool)
            or not 0 <= raw[key] <= 2_048 for key in integer_keys)
            or raw["emitted_targets"] > 256
            or raw["eligible_targets"] > raw["emitted_targets"]
            or not isinstance(raw.get("truncated"), bool)
            or raw.get("capture_state") not in POINT_AND_SPEAK_CAPTURE_STATES
            or isinstance(evidence, (str, bytes))
            or not isinstance(evidence, Sequence)
            or len(evidence) > len(POINT_AND_SPEAK_EVIDENCE)
            or len(set(evidence)) != len(evidence)
            or any(item not in POINT_AND_SPEAK_EVIDENCE for item in evidence)
            or raw.get("confidence_bucket") not in {
                "none", "below_threshold", "high", "very_high"}
            or raw.get("margin_bucket") not in {
                "none", "narrow", "sufficient", "wide"}
            or not isinstance(transaction, Mapping)
            or set(transaction) != {
                "schema_version", "state", "attempted", "recheck"}
            or transaction.get("schema_version") != 1
            or transaction.get("state") not in {
                "executed", "recheck_failed", "expired", "unsupported",
                "execution_failed", "unavailable"}
            or not isinstance(transaction.get("attempted"), bool)
            or transaction.get("recheck") not in {
                "not_run", "matched", "mismatched"}):
        raise ValueError("Point-and-Speak action is malformed")
    attempted = transaction["attempted"]
    transaction_state = transaction["state"]
    recheck = transaction["recheck"]
    if ((attempted != (transaction_state in {
                "executed", "execution_failed"}))
            or ((recheck == "matched") != attempted)
            or ((recheck == "mismatched") !=
                (transaction_state == "recheck_failed"))
            or (snapshot["state"] not in {"ambiguous", "permission_denied"}
                and snapshot["state"] != transaction_state)):
        raise ValueError("Point-and-Speak action is malformed")
    return PointAndSpeakActionResult(
        state=snapshot["state"],
        receipt=PointAndSpeakActionReceipt(
            capture_state=raw["capture_state"],
            observed_elements=raw["observed_elements"],
            emitted_targets=raw["emitted_targets"],
            truncated=raw["truncated"],
            eligible_targets=raw["eligible_targets"],
            contradiction_count=raw["contradiction_count"],
            evidence=tuple(evidence),
            confidence_bucket=raw["confidence_bucket"],
            margin_bucket=raw["margin_bucket"],
            transaction_state=transaction_state,
            attempted=attempted,
            recheck=recheck,
        ),
    )


def normalize_drop_target_preview(
    snapshot: Mapping[str, Any] | None,
) -> DropTargetPreview:
    """Validate the closed, transient, no-execution target projection."""

    if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "state", "accessibility_name", "role",
            "declared_role", "source_kind", "effect", "receipt",
            } or snapshot.get("schema_version") != 1:
        raise ValueError("Drop-to-Target preview is malformed")
    state = snapshot.get("state")
    name = snapshot.get("accessibility_name")
    role = snapshot.get("role")
    declared_role = snapshot.get("declared_role")
    source_kind = snapshot.get("source_kind")
    effect = snapshot.get("effect")
    raw = snapshot.get("receipt")
    if (state not in DROP_TARGET_STATES
            or not isinstance(name, str) or len(name) > 128
            or any(ord(character) < 32 for character in name)
            or not isinstance(role, str)
            or declared_role not in DROP_TARGET_ROLES
            or source_kind not in DROP_TARGET_SOURCE_KINDS
            or effect not in DROP_TARGET_EFFECTS
            or not isinstance(raw, Mapping)
            or set(raw) != {
                "schema_version", "capture_state", "observed_elements",
                "emitted_targets", "skipped_elements", "truncated",
                "observed_targets", "eligible_targets",
                "contradiction_count", "evidence", "confidence_bucket",
                "margin_bucket", "capability_basis", "execution",
            }
            or raw.get("schema_version") != 1):
        raise ValueError("Drop-to-Target preview is malformed")
    integer_keys = (
        "observed_elements", "emitted_targets", "skipped_elements",
        "observed_targets", "eligible_targets", "contradiction_count",
    )
    if any(not isinstance(raw.get(key), int)
           or isinstance(raw.get(key), bool)
           or not 0 <= raw[key] <= 1_024 for key in integer_keys):
        raise ValueError("Drop-to-Target preview is malformed")
    evidence = raw.get("evidence")
    if (not isinstance(raw.get("truncated"), bool)
            or raw.get("capture_state") not in DROP_TARGET_CAPTURE_STATES
            or not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes))
            or len(evidence) > len(DROP_TARGET_EVIDENCE)
            or len(set(evidence)) != len(evidence)
            or any(item not in DROP_TARGET_EVIDENCE for item in evidence)
            or raw.get("confidence_bucket") not in {
                "none", "below_threshold", "high", "very_high"}
            or raw.get("margin_bucket") not in {
                "none", "narrow", "sufficient", "wide"}
            or raw.get("capability_basis") !=
            "caller_declared_role_policy"
            or raw.get("execution") != "none"
            or raw["emitted_targets"] > 128
            or raw["observed_targets"] != raw["emitted_targets"]
            or raw["eligible_targets"] > raw["observed_targets"]):
        raise ValueError("Drop-to-Target preview is malformed")
    capture_state = raw["capture_state"]
    if capture_state != "captured" and (
            state != capture_state
            or any(raw[key] != 0 for key in integer_keys)
            or raw["truncated"] or evidence
            or raw["confidence_bucket"] != "none"
            or raw["margin_bucket"] != "none"):
        raise ValueError("Drop-to-Target preview is malformed")
    if state == "resolved":
        if (capture_state != "captured" or not name.strip()
                or role != declared_role):
            raise ValueError("Drop-to-Target preview is malformed")
    elif name or role:
        raise ValueError("Drop-to-Target preview is malformed")
    if state == "permission_denied" and capture_state != "permission_denied":
        raise ValueError("Drop-to-Target preview is malformed")
    return DropTargetPreview(
        state=state,
        accessibility_name=name.strip(),
        role=role,
        declared_role=declared_role,
        source_kind=source_kind,
        effect=effect,
        receipt=DropTargetReceipt(
            capture_state=capture_state,
            observed_elements=raw["observed_elements"],
            emitted_targets=raw["emitted_targets"],
            skipped_elements=raw["skipped_elements"],
            truncated=raw["truncated"],
            observed_targets=raw["observed_targets"],
            eligible_targets=raw["eligible_targets"],
            contradiction_count=raw["contradiction_count"],
            evidence=tuple(evidence),
            confidence_bucket=raw["confidence_bucket"],
            margin_bucket=raw["margin_bucket"],
            capability_basis=raw["capability_basis"],
            execution=raw["execution"],
        ),
    )


def normalize_settings(snapshot: Mapping[str, Any] | None) -> UnifiedSettings:
    """Normalize the private, explicitly requested settings projection."""

    source = snapshot if isinstance(snapshot, Mapping) else {}
    tones: list[AppToneSetting] = []
    raw_tones = source.get("app_tones")
    if isinstance(raw_tones, Sequence) and not isinstance(raw_tones, (str, bytes)):
        for item in raw_tones[:100]:
            if not isinstance(item, Mapping):
                continue
            bundle = _clean_text(item.get("bundle"), "")
            if not bundle or len(bundle) > 255:
                continue
            tone = str(item.get("tone", "auto")).strip().casefold()
            if tone not in TONE_CHOICES:
                tone = "auto"
            tones.append(AppToneSetting(
                bundle=bundle,
                name=_clean_text(item.get("name"), bundle),
                tone=tone,
            ))

    snippets: list[SnippetSetting] = []
    raw_snippets = source.get("snippets")
    if isinstance(raw_snippets, Sequence) and not isinstance(
            raw_snippets, (str, bytes)):
        for item in raw_snippets[:500]:
            if not isinstance(item, Mapping):
                continue
            name = _clean_text(item.get("name"), "")
            text = item.get("text")
            if (not name or len(name) > 80 or not isinstance(text, str)
                    or not text or len(text) > 4000):
                continue
            snippets.append(SnippetSetting(name=name, text=text))

    corrections: list[CorrectionSetting] = []
    raw_corrections = source.get("corrections")
    if isinstance(raw_corrections, Sequence) and not isinstance(
            raw_corrections, (str, bytes)):
        for item in raw_corrections[:500]:
            if not isinstance(item, Mapping):
                continue
            key = _clean_text(item.get("key"), "")
            original = _clean_text(item.get("source"), "")
            replacement = _clean_text(item.get("target"), "")
            kind = str(item.get("kind", "correction")).strip().casefold()
            if (not key or not original or not replacement
                    or kind not in {"correction", "snippet"}):
                continue
            default_decision = "saved" if kind == "snippet" else "learning"
            global_decision = str(
                item.get("global_decision", default_decision)
            ).strip().casefold()
            if global_decision not in {
                    "active", "held_back", "learning", "saved"}:
                global_decision = default_decision
            app_scopes: list[CorrectionScopeSetting] = []
            raw_scopes = item.get("app_scopes")
            if isinstance(raw_scopes, Sequence) and not isinstance(
                    raw_scopes, (str, bytes)):
                for scope in raw_scopes[:100]:
                    if not isinstance(scope, Mapping):
                        continue
                    bundle = _clean_text(scope.get("bundle"), "")
                    name = _clean_text(scope.get("name"), bundle)
                    decision = str(
                        scope.get("decision", "learning")
                    ).strip().casefold()
                    if (not bundle or len(bundle) > 255
                            or decision not in {
                                "active", "held_back", "learning"}):
                        continue
                    app_scopes.append(CorrectionScopeSetting(
                        bundle=bundle,
                        name=name,
                        count=_nonnegative_int(scope.get("count")),
                        decision=decision,
                    ))
            corrections.append(CorrectionSetting(
                key=key,
                source=original,
                target=replacement,
                count=_nonnegative_int(item.get("count")),
                kind=kind,
                global_decision=global_decision,
                app_scopes=tuple(app_scopes),
            ))
    return UnifiedSettings(
        app_tones=tuple(tones),
        snippets=tuple(snippets),
        manual_vocabulary=_text_items(source.get("manual_vocabulary")),
        banned_vocabulary=_text_items(source.get("banned_vocabulary")),
        corrections=tuple(corrections),
    )


def correction_review_text(
    correction: CorrectionSetting,
    *,
    locale: str = "en",
) -> str:
    """Explain one local learned mapping without exposing correction cases."""

    if correction.kind == "snippet":
        return "\n".join((
            localized_string(
                "settings.dialog.correction.scope.snippet", locale=locale),
            localized_string(
                "settings.dialog.correction.why.snippet",
                locale=locale,
                count=correction.count,
            ),
            localized_string(
                "settings.dialog.correction.privacy.snippet", locale=locale),
        ))

    active_apps = [
        scope.name for scope in correction.app_scopes
        if scope.decision == "active"
    ]
    held_back = (
        correction.global_decision == "held_back"
        or any(scope.decision == "held_back"
               for scope in correction.app_scopes)
    )
    if correction.global_decision == "active":
        scope_line = localized_string(
            "settings.dialog.correction.scope.global", locale=locale)
        why_line = localized_string(
            "settings.dialog.correction.why.global",
            locale=locale,
            count=correction.count,
        )
    elif active_apps:
        app_names = ", ".join(active_apps)
        scope_line = localized_string(
            "settings.dialog.correction.scope.apps",
            locale=locale,
            apps=app_names,
        )
        why_line = localized_string(
            "settings.dialog.correction.why.apps",
            locale=locale,
            apps=app_names,
        )
    else:
        scope_line = localized_string(
            "settings.dialog.correction.scope.inactive", locale=locale)
        why_line = localized_string(
            "settings.dialog.correction.why.held"
            if held_back else "settings.dialog.correction.why.learning",
            locale=locale,
        )

    observations = ", ".join(
        f"{scope.name} {scope.count}×" for scope in correction.app_scopes)
    observed_line = localized_string(
        "settings.dialog.correction.observed"
        if observations else "settings.dialog.correction.observed.none",
        locale=locale,
        **({"apps": observations} if observations else {}),
    )
    return "\n".join((
        scope_line,
        why_line,
        observed_line,
        localized_string(
            "settings.dialog.correction.privacy", locale=locale),
    ))


def normalize_acoustic_keyword_inspection(
    snapshot: Mapping[str, Any] | None,
) -> AcousticKeywordInspection:
    """Fail closed on any unexpected private export field or invariant."""

    if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema_version", "kind", "policy", "candidates"}:
        raise ValueError("pronunciation keyword export is malformed")
    if (snapshot.get("schema_version") != 1
            or snapshot.get("kind") !=
            "whisper-face/acoustic-keyword-memory-export"):
        raise ValueError("pronunciation keyword export is malformed")
    policy = snapshot.get("policy")
    if not isinstance(policy, Mapping) or set(policy) != {
            "minimum_observations", "minimum_confirmations", "max_entries",
            "recognition_effect"}:
        raise ValueError("pronunciation keyword export is malformed")
    if (policy.get("minimum_observations") != 3
            or policy.get("minimum_confirmations") != 2
            or policy.get("recognition_effect") != "none"
            or not isinstance(policy.get("max_entries"), int)
            or isinstance(policy.get("max_entries"), bool)
            or not 1 <= policy["max_entries"] <= 256):
        raise ValueError("pronunciation keyword export is malformed")
    raw_candidates = snapshot.get("candidates")
    if (not isinstance(raw_candidates, Sequence)
            or isinstance(raw_candidates, (str, bytes))
            or len(raw_candidates) > policy["max_entries"]):
        raise ValueError("pronunciation keyword export is malformed")
    candidates: list[AcousticKeywordCandidate] = []
    seen: set[tuple[str, str | None]] = set()
    for raw in raw_candidates:
        if not isinstance(raw, Mapping) or set(raw) != {
                "keyword", "app_scope", "observations", "confirmations",
                "eligible", "status"}:
            raise ValueError("pronunciation keyword export is malformed")
        keyword = raw.get("keyword")
        scope = raw.get("app_scope")
        observations = raw.get("observations")
        confirmations = raw.get("confirmations")
        eligible = raw.get("eligible")
        if (not isinstance(keyword, str) or not keyword
                or keyword != " ".join(keyword.split())
                or len(keyword) > 80
                or any(ord(character) < 32 or ord(character) == 127
                       for character in keyword)):
            raise ValueError("pronunciation keyword export is malformed")
        if scope is not None and (
                not isinstance(scope, str) or len(scope) != 20
                or not scope.startswith("app-")
                or any(character not in "0123456789abcdef"
                       for character in scope[4:])):
            raise ValueError("pronunciation keyword export is malformed")
        if (not isinstance(observations, int)
                or isinstance(observations, bool)
                or not 0 <= observations <= 3
                or not isinstance(confirmations, int)
                or isinstance(confirmations, bool)
                or not 0 <= confirmations <= 2
                or not isinstance(eligible, bool)):
            raise ValueError("pronunciation keyword export is malformed")
        expected_eligible = observations >= 3 and confirmations >= 2
        expected_status = (
            "eligible-not-connected-to-recognition"
            if expected_eligible else
            f"needs-{3 - observations}-observations-and-"
            f"{2 - confirmations}-confirmations"
        )
        if eligible != expected_eligible or raw.get("status") != expected_status:
            raise ValueError("pronunciation keyword export is malformed")
        key = (keyword.casefold(), scope)
        if key in seen:
            raise ValueError("pronunciation keyword export is malformed")
        seen.add(key)
        candidates.append(AcousticKeywordCandidate(
            keyword=keyword,
            app_scope=scope,
            observations=observations,
            confirmations=confirmations,
            eligible=eligible,
        ))
    return AcousticKeywordInspection(
        candidates=tuple(candidates), recognition_effect="none")


def _status_contains(value: str, words: Sequence[str]) -> bool:
    normalized = value.strip().casefold()
    return any(word in normalized for word in words)


def _models_ready(models: Sequence[ModelStatus]) -> bool:
    return any(_status_contains(model.status, ("ready", "running", "installed"))
               or "warm path observed" in model.detail.casefold()
               for model in models)


def _build_onboarding_steps(
    *,
    microphone_status: str,
    accessibility_status: str,
    hotkey_label: str,
    hotkey_practiced: bool,
    models: Sequence[ModelStatus],
    first_dictation_complete: bool,
    locale: str = "en",
) -> tuple[OnboardingStep, ...]:
    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    microphone_ready = _status_contains(
        microphone_status, ("ready", "granted", "available"))
    accessibility_ready = _status_contains(
        accessibility_status, ("ready", "granted", "trusted"))
    permissions_ready = microphone_ready and accessibility_ready
    model_ready = _models_ready(models)
    return (
        OnboardingStep(
            "permissions", copy("onboarding.permissions.title"),
            copy("onboarding.permissions.detail"),
            copy("onboarding.status.done") if permissions_ready else copy(
                "onboarding.status.attention"),
            permissions_ready,
        ),
        OnboardingStep(
            "hotkey", copy("onboarding.hotkey.title", hotkey=hotkey_label),
            copy("onboarding.hotkey.detail", hotkey=hotkey_label),
            copy("onboarding.status.done") if hotkey_practiced else copy(
                "onboarding.status.try"),
            hotkey_practiced,
        ),
        OnboardingStep(
            "models", copy("onboarding.models.title"),
            copy("onboarding.models.detail"),
            copy("onboarding.status.done") if model_ready else copy(
                "onboarding.status.warming"),
            model_ready,
        ),
        OnboardingStep(
            "first_dictation", copy("onboarding.first_dictation.title"),
            copy("onboarding.first_dictation.detail"),
            copy("onboarding.status.done") if first_dictation_complete else copy(
                "onboarding.status.turn"),
            first_dictation_complete,
        ),
    )


def onboarding_presentation(
    steps: Sequence[OnboardingStep],
    *,
    acknowledged: bool,
    locale: str = "en",
) -> OnboardingPresentation:
    """Build the active-step or explicit completion presentation."""

    total = len(steps)
    completed = sum(step.complete for step in steps)
    next_step = next((step for step in steps if not step.complete), None)
    if next_step is None:
        return OnboardingPresentation(
            visible=not acknowledged,
            complete=True,
            current_key=None,
            progress=localized_string(
                "onboarding.complete.progress", locale=locale, total=total),
            title=localized_string(
                "onboarding.complete.title", locale=locale),
            detail=localized_string(
                "onboarding.complete.detail", locale=locale),
            status=localized_string(
                "onboarding.complete.status", locale=locale),
            action_title=localized_string(
                "onboarding.action.finish", locale=locale),
            action_help=localized_string(
                "onboarding.action.finish.help", locale=locale),
        )
    action_key = {
        "permissions": "onboarding.action.open_system_settings",
        "hotkey": "onboarding.action.hotkey",
        "models": "onboarding.action.models",
        "first_dictation": "onboarding.action.first_dictation",
    }.get(next_step.key, "onboarding.action.continue")
    help_key = (
        "onboarding.action.open_system_settings.help"
        if next_step.key == "permissions"
        else "onboarding.action.help"
    )
    return OnboardingPresentation(
        visible=not acknowledged,
        complete=False,
        current_key=next_step.key,
        progress=localized_string(
            "onboarding.progress", locale=locale,
            completed=completed, total=total),
        title=next_step.title,
        detail=next_step.detail,
        status=next_step.status,
        action_title=localized_string(action_key, locale=locale),
        action_help=localized_string(help_key, locale=locale),
    )


def _build_degraded_issues(
    *,
    service_status: str,
    microphone_status: str,
    accessibility_status: str,
    models: Sequence[ModelStatus],
    locale: str = "en",
) -> tuple[DegradedIssue, ...]:
    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    issues: list[DegradedIssue] = []
    if _status_contains(service_status, (
            "failed", "stopped", "offline", "unavailable")):
        issues.append(DegradedIssue(
            "service", copy("issue.service.title"),
            copy("issue.service.detail")))
    if _status_contains(microphone_status, (
            "needs attention", "denied", "missing", "failed", "unavailable")):
        issues.append(DegradedIssue(
            "microphone", copy("issue.microphone.title"),
            copy("issue.microphone.detail")))
    if _status_contains(accessibility_status, (
            "needs attention", "denied", "not granted", "failed", "unavailable")):
        issues.append(DegradedIssue(
            "accessibility", copy("issue.accessibility.title"),
            copy("issue.accessibility.detail")))
    if models and not _models_ready(models):
        issues.append(DegradedIssue(
            "models", copy("issue.models.title"),
            copy("issue.models.detail"),
        ))
    elif models:
        unavailable = [model.name for model in models if _status_contains(
            model.status, ("failed", "missing", "unavailable"))]
        if unavailable:
            issues.append(DegradedIssue(
                "fallback", copy("issue.fallback.title"),
                copy("issue.fallback.detail", models=", ".join(unavailable)),
                severity="warning",
            ))
    return tuple(issues)


def _context_firewall_summary(
    source: Mapping[str, Any], *, available: bool, locale: str,
) -> str:
    """Translate only allowlisted aggregate receipt fields into plain copy."""

    receipt = source.get("last_context_firewall")
    if not available or not isinstance(receipt, Mapping) \
            or receipt.get("mode") != "shadow-only":
        return localized_string(
            "results.firewall.unavailable", locale=locale)
    disposition = str(receipt.get("disposition", "")).strip().casefold()
    if disposition == "no-effect":
        return localized_string("results.firewall.no_effect", locale=locale)
    if disposition == "quarantine":
        count = min(1000, max(
            _nonnegative_int(receipt.get("quarantined")),
            _nonnegative_int(receipt.get("protected_influences")),
            1,
        ))
        key = ("results.firewall.quarantine.one" if count == 1
               else "results.firewall.quarantine.many")
        return localized_string(key, locale=locale, count=count)
    if disposition == "promotion-candidate":
        count = min(1000, max(
            _nonnegative_int(receipt.get("promotion_candidates")), 1))
        key = ("results.firewall.promotion.one" if count == 1
               else "results.firewall.promotion.many")
        return localized_string(key, locale=locale, count=count)
    return localized_string("results.firewall.unavailable", locale=locale)


def normalize_result_evidence(
    snapshot: Mapping[str, Any] | None,
) -> ResultEvidenceInspection:
    """Validate the private, on-demand latest-result reveal."""

    expected = {
        "schema_version", "kind", "alternatives",
        "protected_anchors", "proof_edits", "timings_ms",
    }
    if (not isinstance(snapshot, Mapping) or set(snapshot) != expected
            or snapshot.get("schema_version") != 1
            or snapshot.get("kind") != "whisper-face/result-evidence"):
        raise ValueError("latest-result evidence is malformed")

    def private_items(key: str, *, limit: int, chars: int) -> tuple[str, ...]:
        raw = snapshot.get(key)
        if (not isinstance(raw, Sequence)
                or isinstance(raw, (str, bytes)) or len(raw) > limit):
            raise ValueError("latest-result evidence is malformed")
        items = []
        for value in raw:
            if (not isinstance(value, str) or not value
                    or "\x00" in value or len(value) > chars):
                raise ValueError("latest-result evidence is malformed")
            items.append(value)
        return tuple(items)

    alternatives = private_items("alternatives", limit=3, chars=2000)
    anchors = private_items("protected_anchors", limit=64, chars=160)
    raw_proof = snapshot.get("proof_edits")
    if (not isinstance(raw_proof, Sequence)
            or isinstance(raw_proof, (str, bytes)) or len(raw_proof) > 64):
        raise ValueError("latest-result evidence is malformed")
    proof_edits = []
    proof_keys = {"kind", "before", "after", "accepted", "reason"}
    for raw in raw_proof:
        if not isinstance(raw, Mapping) or set(raw) != proof_keys:
            raise ValueError("latest-result evidence is malformed")
        kind = raw.get("kind")
        before = raw.get("before")
        after = raw.get("after")
        accepted = raw.get("accepted")
        reason = raw.get("reason")
        if (not isinstance(kind, str) or not kind or len(kind) > 80
                or "\x00" in kind
                or not isinstance(before, str) or len(before) > 1000
                or "\x00" in before
                or not isinstance(after, str) or len(after) > 1000
                or "\x00" in after or (not before and not after)
                or not isinstance(accepted, bool)
                or not isinstance(reason, str) or len(reason) > 240
                or "\x00" in reason):
            raise ValueError("latest-result evidence is malformed")
        proof_edits.append(ProofEditInspection(
            kind=kind,
            before=before,
            after=after,
            accepted=accepted,
            reason=reason,
        ))

    raw_timings = snapshot.get("timings_ms")
    if not isinstance(raw_timings, Mapping) or any(
            key not in RESULT_EVIDENCE_STAGES for key in raw_timings):
        raise ValueError("latest-result evidence is malformed")
    timings = []
    for stage in RESULT_EVIDENCE_STAGES:
        if stage not in raw_timings:
            continue
        value = _finite_number(raw_timings[stage])
        if value is None or value < 0 or value > 3_600_000:
            raise ValueError("latest-result evidence is malformed")
        timings.append(StageTiming(stage, value))
    return ResultEvidenceInspection(
        alternatives=alternatives,
        protected_anchors=anchors,
        proof_edits=tuple(proof_edits),
        timings=tuple(timings),
    )


def result_evidence_text(
    evidence: ResultEvidenceInspection,
    *,
    result: ResultInspection | None = None,
    locale: str = "en",
) -> str:
    """Format a transient, selectable latest-result evidence view.

    When ``result`` is supplied the header repeats the aggregate trust
    surface (stable prefix, anchors, decisions, cleanup, proof review,
    context influence, firewall, and consequence receipts) so nothing the
    old persistent evidence cards showed is lost from this reveal.
    """

    lines: list[str] = []

    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    def section(key: str, rows: Sequence[str]) -> None:
        lines.append(localized_string(key, locale=locale))
        lines.extend(rows or (
            localized_string("results.inspect.none", locale=locale),))
        lines.append("")

    if result is not None:
        confidence = (
            copy("results.value.confidence",
                 confidence=f"{result.confidence:.0%}")
            if result.confidence is not None else "")
        rejected = (
            str(result.proof_edits_rejected)
            if result.proof_edits_rejected is not None
            else copy("results.value.not_reported"))
        cleanup_kinds = ", ".join(dict.fromkeys(result.cleanup_edits)) \
            or copy("results.value.none_reported")
        summary_rows = [
            "{}: {}".format(
                copy("results.evidence.stable"),
                copy("results.value.words",
                     count=result.stable_prefix_words)),
            "{}: {}".format(
                copy("results.evidence.anchors"),
                result.protected_anchor_count),
            "{}: {}{}".format(
                copy("results.evidence.decisions"),
                result.compiler_decisions, confidence),
            "{}: {}".format(
                copy("results.evidence.alternatives"),
                result.alternatives_considered),
            "{}: {}".format(
                copy("results.evidence.cleanup"), cleanup_kinds),
            "{}: {}".format(
                copy("results.evidence.proof"),
                copy("results.value.proof",
                     accepted=result.proof_edits_accepted,
                     rejected=rejected)),
            copy("results.context.summary",
                 influence=result.context_influence),
            result.context_firewall_summary,
            result.consequence_summary,
        ]
        if result.consequence_advisory:
            summary_rows.append(result.consequence_advisory)
        section("results.inspect.summary", tuple(summary_rows))

    section(
        "results.inspect.alternatives",
        tuple(f"{index}. {value}" for index, value in enumerate(
            evidence.alternatives, 1)),
    )
    section(
        "results.inspect.anchors",
        tuple(f"• {value}" for value in evidence.protected_anchors),
    )
    proof_rows = []
    for edit in evidence.proof_edits:
        status = localized_string(
            "results.inspect.proof.accepted"
            if edit.accepted else "results.inspect.proof.rejected",
            locale=locale,
        )
        row = f"{status} · {edit.kind}: {edit.before!r} → {edit.after!r}"
        if edit.reason:
            row += f"\n  {edit.reason}"
        proof_rows.append(row)
    section("results.inspect.proof", tuple(proof_rows))
    timing_rows = []
    for timing in evidence.timings:
        label = localized_string(
            f"results.inspect.stage.{timing.stage}", locale=locale)
        value = (
            f"{timing.milliseconds / 1000:.2f} s"
            if timing.milliseconds >= 1000
            else f"{timing.milliseconds:.1f} ms"
        )
        timing_rows.append(f"{label}: {value}")
    section("results.inspect.timing", tuple(timing_rows))
    rendered = "\n".join(lines).rstrip()
    if result is None and not any((
            evidence.alternatives,
            evidence.protected_anchors,
            evidence.proof_edits,
            evidence.timings)):
        return localized_string("results.inspect.empty", locale=locale)
    return rendered


def _build_result_inspection(
    source: Mapping[str, Any],
    *,
    active_engine: str,
    latency_ms: float | None,
    word_count: int | None,
    locale: str = "en",
) -> ResultInspection:
    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    def sequence_items(key: str) -> tuple[str, ...]:
        value = source.get(key)
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return ()
        return tuple(str(item).strip() for item in value
                     if str(item).strip())

    available = word_count is not None and word_count > 0
    if latency_ms is not None:
        summary = copy(
            "results.summary.timed", words=word_count or 0,
            seconds=f"{latency_ms / 1000:.2f}")
        available = True
    elif available:
        summary = copy("results.summary.words", words=word_count)
    else:
        summary = copy("results.summary.empty")
    cleanup_edits = sequence_items("last_cleanup_edits")
    explicit_accepted = source.get("last_proof_edits_accepted")
    proof_edits_accepted = (
        _nonnegative_int(explicit_accepted)
        if explicit_accepted is not None
        else sum(item.casefold().startswith("proof:")
                 for item in cleanup_edits))
    explicit_rejected = source.get("last_proof_edits_rejected")
    confidence = _finite_number(source.get("last_confidence"))
    consequence = source.get("last_consequence")
    consequence_source = consequence if isinstance(consequence, Mapping) else {}
    route = str(consequence_source.get("route", "standard")).strip().casefold()
    if route not in CONSEQUENCE_ROUTES:
        route = "unavailable"
    relisten = str(consequence_source.get(
        "relisten_status", "not-needed")).strip().casefold()
    if relisten not in CONSEQUENCE_RELISTEN_STATUSES:
        relisten = "unavailable"
    risk_counts_value = consequence_source.get("risk_counts")
    risk_counts: list[tuple[str, int]] = []
    if isinstance(risk_counts_value, Mapping):
        for category in sorted(CONSEQUENCE_CATEGORIES):
            count = _nonnegative_int(risk_counts_value.get(category))
            if count:
                risk_counts.append((category, min(1000, count)))
    high_risks = min(1000, _nonnegative_int(
        consequence_source.get("high_risks")))
    uncertain_risks = min(1000, _nonnegative_int(
        consequence_source.get("uncertain_risks")))
    risk_summary = ""
    if risk_counts:
        risk_summary = copy(
            "results.consequence.risks",
            risks=", ".join(copy(
                "results.consequence.risk",
                category=copy(f"results.risk.{category}"), count=count)
                for category, count in risk_counts),
        )
    raw_mode = _clean_text(source.get("last_mode"), "capture")
    mode_key = raw_mode.strip().casefold()
    mode = (copy(f"settings.mode.{mode_key}.name")
            if mode_key in MODE_GUIDE else raw_mode)
    return ResultInspection(
        available=available,
        summary=summary,
        engine=active_engine if available else copy("results.engine.waiting"),
        mode=mode,
        stable_prefix_words=_nonnegative_int(
            source.get("last_stable_prefix_words")),
        compiler_decisions=_nonnegative_int(
            source.get("last_compiler_decisions")),
        confidence=(min(1.0, max(0.0, confidence))
                    if confidence is not None and available else None),
        cleanup_edits=cleanup_edits,
        proof_edits_accepted=proof_edits_accepted,
        proof_edits_rejected=(
            _nonnegative_int(explicit_rejected)
            if explicit_rejected is not None else None),
        protected_anchor_count=_nonnegative_int(
            source.get("last_protected_anchors",
                       source.get("last_protected_anchor_count"))),
        alternatives_considered=_nonnegative_int(
            source.get("last_alternatives_considered")),
        context_influence=_clean_text(
            source.get("last_context_influence"),
            copy("results.context.unreported")),
        context_firewall_summary=_context_firewall_summary(
            source, available=available, locale=locale),
        consequence_summary=copy(
            "results.consequence.summary",
            route=copy(f"results.route.{route}"),
            high=high_risks,
            uncertain=uncertain_risks,
            risks=risk_summary,
            relisten=copy(f"results.relisten.{relisten}"),
        ),
        consequence_advisory=(
            copy("results.consequence.review.advisory")
            if route == "review" else ""),
        retained_span_count=min(2, _nonnegative_int(
            source.get("retained_consequence_spans"))),
        acoustic_replay_enabled=source.get("acoustic_time_machine") is True,
    )


def _status_presentation(
    *,
    capture_state: str,
    paused: bool,
    hotkey_label: str,
    outbox_count: int,
    service_status: str,
    degraded_issues: Sequence[DegradedIssue],
    locale: str = "en",
) -> tuple[str, str, str]:
    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    capture = capture_state.strip().casefold()
    if paused:
        return (
            "paused", copy("overview.status.paused.title"),
            copy("overview.status.paused.detail"))
    if _status_contains(capture, ("listen", "record", "captur")):
        return (
            "recording", copy("overview.status.recording.title"),
            copy("overview.status.recording.detail", hotkey=hotkey_label))
    if _status_contains(capture, ("process", "clean", "insert", "compil")):
        return (
            "processing", copy("overview.status.processing.title"),
            copy("overview.status.processing.detail"))
    if outbox_count:
        detail_key = ("overview.status.recovery.detail.one"
                      if outbox_count == 1
                      else "overview.status.recovery.detail.many")
        return (
            "recovery", copy("overview.status.recovery.title"),
            copy(detail_key, count=outbox_count))
    errors = [issue for issue in degraded_issues if issue.severity == "error"]
    if errors:
        return (
            "degraded", copy("overview.status.degraded.title"),
            errors[0].detail)
    if _status_contains(service_status, ("starting", "warming", "unknown")):
        return (
            "starting", copy("overview.status.starting.title"),
            copy("overview.status.starting.detail"))
    return (
        "ready", copy("overview.status.ready.title"),
        copy("overview.status.ready.detail", hotkey=hotkey_label))


def _localized_runtime_overview_copy(
    value: Any, *, kind: str, locale: str,
) -> str:
    """Translate the small allowlist of fixed runtime copy shown in Overview."""

    text = _clean_text(value, "")
    candidates = {
        "engine": ("overview.engine.warming",),
        "outbox": (
            "overview.outbox.summary.paste_attempted",
            "overview.outbox.summary.not_pasted",
        ),
    }.get(kind, ())
    key = next((
        candidate for candidate in candidates
        if text.casefold() == STRING_CATALOGS["en"][candidate].casefold()
    ), None)
    return localized_string(key, locale=locale) if key else text


def _localized_verification_copy(value: Any, *, locale: str) -> str:
    """Translate only GUI-owned verification states; preserve runtime copy."""

    if value is None:
        return localized_string(
            "diagnostics.verification.not_run", locale=locale)
    text = _clean_text(value, "")
    keys = (
        "diagnostics.verification.not_run",
        "diagnostics.verification.running",
        "diagnostics.verification.passed",
        "diagnostics.verification.attention",
    )
    key = next((candidate for candidate in keys
                if text.casefold() ==
                STRING_CATALOGS["en"][candidate].casefold()), None)
    if key:
        return localized_string(key, locale=locale)
    return text


def normalize_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    section: str = "Home",
    verification: str | None = None,
    notice: str = "",
    notice_level: str = "info",
    onboarding_acknowledged: bool = False,
    settings_pane: str = "Personalize",
    settings: UnifiedSettings | None = None,
    locale: str = "en",
) -> GUIState:
    """Convert an intentionally loose runtime snapshot to stable UI state."""

    source = snapshot if isinstance(snapshot, Mapping) else {}
    face = str(source.get("face", "parrot")).strip().casefold()
    if face not in FACES:
        face = "parrot"
    latency = _finite_number(source.get("last_latency_ms"))
    words = source.get("last_word_count")
    last_word_count = None if words is None else _nonnegative_int(words)
    minutes_saved = _finite_number(source.get("minutes_saved"))
    capture_state = _clean_text(
        source.get("capture_state"), localized_string(
            "default.capture.ready", locale=locale))
    paused = source.get("paused") is True
    active_engine = _localized_runtime_overview_copy(
        source.get("active_engine"), kind="engine", locale=locale)
    if not active_engine:
        active_engine = localized_string(
            "overview.engine.waiting", locale=locale)
    outbox_count = _nonnegative_int(source.get("outbox_count"))
    relisten = source.get("selective_relisten")
    relisten = relisten if isinstance(relisten, Mapping) else {}
    relisten_status = str(
        relisten.get("status", "receipt-missing")).strip().casefold()
    if relisten_status not in SELECTIVE_RELISTEN_STATUSES:
        relisten_status = "receipt-invalid"
    voice_object_inbox_status = str(
        source.get("voice_object_inbox_status", "Off")).strip().casefold()
    if voice_object_inbox_status not in {"off", "ready", "unavailable"}:
        voice_object_inbox_status = "unavailable"
    confirmation = source.get("risky_action_confirmation")
    confirmation = confirmation if isinstance(confirmation, Mapping) else {}
    risky_action_risk = str(confirmation.get("risk", "none")).casefold()
    confirmation_state = str(confirmation.get("state", "idle")).casefold()
    if risky_action_risk not in RISKY_ACTION_CLASSES:
        risky_action_risk = "none"
    if confirmation_state not in RISKY_ACTION_STATES:
        confirmation_state = "idle"
    if risky_action_risk == "none":
        confirmation_state = "idle"
    unknown_status = localized_string("default.status.unknown", locale=locale)
    service_status = _clean_text(source.get("service_status"), unknown_status)
    microphone_status = _clean_text(
        source.get("microphone_status"), unknown_status)
    accessibility_status = _clean_text(
        source.get("accessibility_status"), unknown_status)
    models = _normalize_models(source.get("models"), locale=locale)
    model_wallet_advisory = _model_wallet_advisory_copy(
        source.get("model_wallet_shadow"), locale=locale)
    hotkey_label = _clean_text(
        source.get("hotkey_label"), localized_string(
            "settings.mode.capture.shortcut", locale=locale))
    successful_dictation = (
        source.get("first_dictation_complete") is True
        or (last_word_count is not None and last_word_count > 0))
    hotkey_practiced = (
        source.get("hotkey_practiced") is True
        or successful_dictation
        or _status_contains(capture_state, ("listen", "record", "captur")))
    onboarding_steps = _build_onboarding_steps(
        microphone_status=microphone_status,
        accessibility_status=accessibility_status,
        hotkey_label=hotkey_label,
        hotkey_practiced=hotkey_practiced,
        models=models,
        first_dictation_complete=successful_dictation,
        locale=locale,
    )
    degraded_issues = _build_degraded_issues(
        service_status=service_status,
        microphone_status=microphone_status,
        accessibility_status=accessibility_status,
        models=models,
        locale=locale,
    )
    phase, status_title, status_detail = _status_presentation(
        capture_state=capture_state,
        paused=paused,
        hotkey_label=hotkey_label,
        outbox_count=outbox_count,
        service_status=service_status,
        degraded_issues=degraded_issues,
        locale=locale,
    )
    normalized_latency = max(0.0, latency) if latency is not None else None
    return GUIState(
        section=section if section in SECTIONS else "Home",
        capture_state=capture_state,
        paused=paused,
        face=face,
        flight_recorder=source.get("flight_recorder") is True,
        flight_state=_clean_text(
            source.get("flight_state"), localized_string(
                "default.flight.off", locale=locale)),
        acoustic_time_machine=source.get("acoustic_time_machine") is True,
        selective_relisten_requested=relisten.get("requested") is True,
        selective_relisten_evidence_ready=(
            relisten.get("evidence_ready") is True),
        selective_relisten_status=relisten_status,
        voice_object_commands=source.get("voice_object_commands") is True,
        voice_object_inbox_count=_nonnegative_int(
            source.get("voice_object_inbox_count")),
        voice_object_inbox_status=voice_object_inbox_status.title(),
        risky_action_risk=risky_action_risk,
        risky_action_confirmation_state=confirmation_state,
        active_engine=active_engine,
        last_latency_ms=normalized_latency,
        last_word_count=last_word_count,
        words_today=_nonnegative_int(source.get("words_today")),
        minutes_saved=max(0.0, minutes_saved or 0.0),
        outbox_count=outbox_count,
        outbox_summary=_localized_runtime_overview_copy(
            source.get("outbox_summary"), kind="outbox", locale=locale),
        regression_cases=_nonnegative_int(source.get("regression_cases")),
        regression_quarantined=_nonnegative_int(
            source.get("regression_quarantined")),
        privacy_summary=_clean_text(
            source.get("privacy_summary"), localized_string(
                "default.privacy.local", locale=locale)),
        service_status=service_status,
        microphone_status=microphone_status,
        accessibility_status=accessibility_status,
        version=_clean_text(
            source.get("version"), localized_string(
                "default.build.development", locale=locale)),
        models=models,
        model_wallet_advisory=model_wallet_advisory,
        hotkey_label=hotkey_label,
        prefers_reduced_motion=(
            source.get("prefers_reduced_motion") is True),
        onboarding_steps=onboarding_steps,
        onboarding_complete=all(step.complete for step in onboarding_steps),
        onboarding_acknowledged=onboarding_acknowledged,
        status_phase=phase,
        status_title=status_title,
        status_detail=status_detail,
        degraded_issues=degraded_issues,
        last_result=_build_result_inspection(
            source,
            active_engine=active_engine,
            latency_ms=normalized_latency,
            word_count=last_word_count,
            locale=locale,
        ),
        verification=_localized_verification_copy(
            verification, locale=locale),
        notice=notice,
        notice_level=(notice_level if notice_level in {
            "info", "success", "error"} else "info"),
        settings_pane=(settings_pane if settings_pane in SETTINGS_PANES
                       else "Personalize"),
        settings=settings or UnifiedSettings(),
    )


class WhisperFaceViewModel:
    """Pure state/actions seam between the runtime and the native window."""

    def __init__(self, actions: GUIActions, *, locale: str = "en"):
        self.actions = actions
        self.locale = resolve_locale(locale)
        self.state = GUIState()
        self._onboarding_acknowledged = False
        self._hotkey_practiced = False
        self._inspected_voice_draft_ids: set[str] = set()
        self._revealed_voice_draft_ids: set[str] = set()
        self._voice_draft_clipboard_clear_available = False
        self._inspected_demonstration_ids: set[str] = set()
        self._revealed_demonstration_ids: set[str] = set()
        self.refresh()

    def localized(self, key: str, **values: Any) -> str:
        return localized_string(key, locale=self.locale, **values)

    def set_locale(self, locale: str | None) -> GUIState:
        """Apply a supported system locale and rebuild locale-owned state."""

        self.locale = resolve_locale(locale)
        return self.refresh()

    def refresh(self) -> GUIState:
        try:
            raw_snapshot = self.actions.status_snapshot()
            snapshot = dict(raw_snapshot) if isinstance(
                raw_snapshot, Mapping) else {}
            if (snapshot.get("hotkey_practiced") is True
                    or snapshot.get("first_dictation_complete") is True
                    or _nonnegative_int(snapshot.get("last_word_count")) > 0
                    or _status_contains(
                        _clean_text(snapshot.get("capture_state"), ""),
                        ("listen", "record", "captur"))):
                self._hotkey_practiced = True
            snapshot["hotkey_practiced"] = self._hotkey_practiced
            self.state = normalize_snapshot(
                snapshot,
                section=self.state.section,
                verification=self.state.verification,
                onboarding_acknowledged=self._onboarding_acknowledged,
                settings_pane=self.state.settings_pane,
                settings=self.state.settings,
                locale=self.locale,
            )
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "overview.notice.status.error", error=error),
                notice_level="error")
        return self.state

    def acknowledge_onboarding(self, acknowledged: bool = True) -> GUIState:
        """Remember that a completed first run need not be shown again."""
        self._onboarding_acknowledged = bool(acknowledged)
        self.state = replace(
            self.state,
            onboarding_acknowledged=self._onboarding_acknowledged,
        )
        return self.state

    def select_section(self, section: str) -> GUIState:
        if section not in SECTIONS:
            raise ValueError(self.localized(
                "validation.section.unknown", section=section))
        self.state = replace(
            self.state, section=section, notice="", notice_level="info")
        if section == "Settings":
            self.load_settings()
        return self.state

    def select_settings_pane(self, pane: str) -> GUIState:
        if pane not in SETTINGS_PANES:
            raise ValueError(self.localized(
                "validation.settings_pane.unknown", pane=pane))
        self.state = replace(
            self.state, settings_pane=pane, notice="", notice_level="info")
        return self.state

    def load_settings(self, *, notice: str = "",
                      notice_level: str = "info") -> GUIState:
        """Load private personalization only while the Settings page is used."""
        try:
            settings = normalize_settings(self.actions.settings_snapshot())
            self.state = replace(
                self.state, settings=settings, notice=notice,
                notice_level=notice_level)
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.settings.load_failed", error=error),
                notice_level="error")
        return self.state

    def set_app_tone(self, bundle: str, tone: str) -> GUIState:
        app_id = str(bundle).strip()
        normalized = str(tone).strip().casefold()
        if not app_id or len(app_id) > 255 or any(
                character.isspace() for character in app_id):
            raise ValueError(self.localized("validation.app.bundle"))
        if normalized not in TONE_CHOICES:
            raise ValueError(self.localized(
                "validation.tone.unsupported", tone=tone))
        try:
            self.actions.set_app_tone(app_id, normalized)
            return self.load_settings(
                notice=self.localized("settings.notice.tone_saved"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.tone.save_failed", error=error),
                notice_level="error")
            return self.state

    def _valid_snippet(self, name: str, text: str) -> tuple[str, str]:
        normalized_name = str(name).strip()
        value = str(text)
        if (not normalized_name or len(normalized_name) > 80
                or "\n" in normalized_name or "\r" in normalized_name):
            raise ValueError(self.localized("validation.snippet.name"))
        if not value.strip() or len(value) > 4000:
            raise ValueError(self.localized("validation.snippet.text"))
        return normalized_name, value

    def save_snippet(self, name: str, text: str, *,
                     expected_original: str | None = None) -> GUIState:
        try:
            normalized_name, value = self._valid_snippet(name, text)
        except ValueError as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.snippet.save_failed", error=error),
                notice_level="error")
            return self.state
        try:
            self.actions.save_snippet(
                normalized_name, expected_original, value)
            return self.load_settings(
                notice=self.localized("settings.notice.snippet_saved"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.snippet.save_failed", error=error),
                notice_level="error")
            return self.state

    def delete_snippet(self, name: str, expected_original: str) -> GUIState:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError(self.localized("validation.snippet.required"))
        if not isinstance(expected_original, str):
            raise ValueError(self.localized("validation.snippet.expected"))
        try:
            self.actions.delete_snippet(normalized_name, expected_original)
            return self.load_settings(
                notice=self.localized("settings.notice.snippet_deleted"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.snippet.delete_failed", error=error),
                notice_level="error")
            return self.state

    def _valid_vocabulary(
        self, values: Sequence[str], *, label_key: str,
    ) -> tuple[str, ...]:
        label = self.localized(label_key)
        if isinstance(values, (str, bytes)):
            raise ValueError(self.localized(
                "validation.vocabulary.list", label=label))
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip()
            folded = value.casefold()
            if not value:
                continue
            if len(value) > 80 or "\n" in value or "\r" in value:
                raise ValueError(self.localized(
                    "validation.vocabulary.term_length", label=label))
            if value.startswith(("-", "#")):
                raise ValueError(self.localized(
                    "validation.vocabulary.reserved", label=label))
            if folded not in seen:
                cleaned.append(value)
                seen.add(folded)
        if len(cleaned) > 500:
            raise ValueError(self.localized(
                "validation.vocabulary.maximum", label=label))
        return tuple(cleaned)

    def save_vocabulary(self, manual: Sequence[str],
                        banned: Sequence[str]) -> GUIState:
        try:
            terms = self._valid_vocabulary(
                manual, label_key="validation.vocabulary.preferred")
            exclusions = self._valid_vocabulary(
                banned, label_key="validation.vocabulary.excluded")
        except ValueError as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.vocabulary.save_failed", error=error),
                notice_level="error")
            return self.state
        overlap = {item.casefold() for item in terms} & {
            item.casefold() for item in exclusions}
        if overlap:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.vocabulary.save_failed",
                    error=self.localized("validation.vocabulary.overlap")),
                notice_level="error")
            return self.state
        try:
            self.actions.save_vocabulary(terms, exclusions)
            return self.load_settings(
                notice=self.localized("settings.notice.vocabulary_saved"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.vocabulary.save_failed", error=error),
                notice_level="error")
            return self.state

    def forget_learned(self, kind: str, key: str) -> GUIState:
        normalized_kind = str(kind).strip().casefold()
        if normalized_kind not in {"correction", "snippet"}:
            raise ValueError(self.localized("validation.correction.kind"))
        match = next((item for item in self.state.settings.corrections
                      if item.kind == normalized_kind and item.key == key), None)
        if match is None:
            raise ValueError(self.localized("validation.correction.unknown"))
        try:
            callback = (self.actions.forget_snippet_edit
                        if match.kind == "snippet"
                        else self.actions.forget_correction)
            result = callback(match.key)
            if match.kind == "snippet" and result is False:
                raise RuntimeError(self.localized(
                    "validation.correction.stale_snippet"))
            return self.load_settings(
                notice=self.localized(
                    "settings.notice.correction_forgotten"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.correction.forget_failed", error=error),
                notice_level="error")
            return self.state

    def inspect_acoustic_keywords(self) -> AcousticKeywordInspection:
        """Load candidate text only for an explicit inspection action."""
        try:
            return normalize_acoustic_keyword_inspection(
                self.actions.inspect_acoustic_keywords())
        except Exception as error:
            message = self.localized(
                "operation.keyword.inspect_failed", error=error)
            self.state = replace(
                self.state, notice=message, notice_level="error")
            raise ValueError(message) from error

    def export_acoustic_keywords(self) -> GUIState:
        try:
            self.actions.export_acoustic_keywords()
            self.state = replace(
                self.state,
                notice=self.localized("settings.notice.keyword_exported"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.keyword.export_failed", error=error),
                notice_level="error")
        return self.state

    def forget_acoustic_keyword(
        self, candidate: AcousticKeywordCandidate,
    ) -> GUIState:
        if not isinstance(candidate, AcousticKeywordCandidate):
            raise ValueError(self.localized("validation.keyword.unknown"))
        try:
            removed = self.actions.forget_acoustic_keyword(
                candidate.keyword, candidate.app_scope)
            if removed is False:
                raise KeyError(self.localized("validation.keyword.unknown"))
            self.state = replace(
                self.state,
                notice=self.localized("settings.notice.keyword_forgotten"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.keyword.forget_failed", error=error),
                notice_level="error")
        return self.state

    def forget_all_acoustic_keywords(self) -> GUIState:
        try:
            self.actions.forget_all_acoustic_keywords()
            self.state = replace(
                self.state,
                notice=self.localized("settings.notice.keywords_forgotten"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.keyword.forget_failed", error=error),
                notice_level="error")
        return self.state

    def show_next_onboarding_step(self) -> GUIState:
        """Route to the next useful setup surface without blocking capture."""
        step = next(
            (item for item in self.state.onboarding_steps if not item.complete),
            None,
        )
        if step is None:
            self.state = replace(
                self.state, section="Home",
                notice=self.localized("onboarding.complete"),
                notice_level="success")
        elif step.key in {"permissions", "models"}:
            self.state = replace(
                self.state, section="Advanced", notice=step.detail,
                notice_level="info")
        else:
            self.state = replace(
                self.state, section="Home", notice=step.detail,
                notice_level="info")
        return self.state

    def show_issue(self, index: int = 0) -> GUIState:
        """Route from degraded status to its truthful local recovery surface."""
        if not 0 <= index < len(self.state.degraded_issues):
            return self.state
        issue = self.state.degraded_issues[index]
        self.state = replace(
            self.state, section=issue.route, notice=issue.detail,
            notice_level="error" if issue.severity == "error" else "info")
        return self.state

    def choose_face(self, face: str) -> GUIState:
        normalized = str(face).strip().casefold()
        if normalized not in FACES:
            raise ValueError(self.localized(
                "validation.face.unsupported", face=face))
        try:
            self.actions.set_face(normalized)
            self.state = replace(
                self.state, face=normalized, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
        return self.state

    def set_flight_recorder(self, enabled: bool) -> GUIState:
        desired = bool(enabled)
        try:
            self.actions.set_flight_recorder(desired)
            self.state = replace(
                self.state, flight_recorder=desired, notice="",
                notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.flight.update_failed", error=error),
                notice_level="error")
        return self.state

    def set_acoustic_time_machine(self, enabled: bool) -> GUIState:
        desired = bool(enabled)
        try:
            self.actions.set_acoustic_time_machine(desired)
            self.state = replace(
                self.state, acoustic_time_machine=desired, notice="",
                notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.acoustic.update_failed", error=error),
                notice_level="error")
        return self.state

    def set_selective_relisten(self, enabled: bool) -> GUIState:
        desired = bool(enabled)
        try:
            self.actions.set_selective_relisten(desired)
            self.state = replace(
                self.state, selective_relisten_requested=desired,
                notice="", notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.relisten.update_failed", error=error),
                notice_level="error")
        return self.state

    def set_voice_object_commands(self, enabled: bool) -> GUIState:
        desired = bool(enabled)
        try:
            self.actions.set_voice_object_commands(desired)
            self.state = replace(
                self.state, voice_object_commands=desired, notice="",
                notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.voice_objects.update_failed", error=error),
                notice_level="error")
        return self.state

    def start_risky_action_confirmation(self, risk: str) -> GUIState:
        normalized = str(risk).strip().casefold()
        if normalized not in RISKY_ACTION_CLASSES:
            raise ValueError("unsupported risk class")
        try:
            if not self.actions.start_risky_action_confirmation(normalized):
                raise RuntimeError
            return self.refresh()
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.risky_confirmation.start_failed"),
                notice_level="error")
            return self.state

    def click_risky_action_confirmation(self) -> GUIState:
        try:
            if not self.actions.click_risky_action_confirmation():
                raise RuntimeError
            return self.refresh()
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.risky_confirmation.click_failed"),
                notice_level="error")
            return self.state

    def cancel_risky_action_confirmation(self) -> GUIState:
        try:
            if not self.actions.cancel_risky_action_confirmation():
                raise RuntimeError
            return self.refresh()
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.risky_confirmation.cancel_failed"),
                notice_level="error")
            return self.state

    def inspect_voice_object_drafts(self) -> tuple[VoiceDraftMetadata, ...]:
        """Load content-free metadata only after an explicit inspector action."""
        self._revealed_voice_draft_ids.clear()
        try:
            raw = self.actions.inspect_voice_object_drafts()
            if (isinstance(raw, (str, bytes))
                    or not isinstance(raw, Sequence)
                    or len(raw) > VOICE_DRAFT_INSPECT_LIMIT):
                raise ValueError
            drafts: list[VoiceDraftMetadata] = []
            identifiers: set[str] = set()
            sequences: set[int] = set()
            for item in raw:
                if not isinstance(item, Mapping) or set(item) != {
                        "item_id", "sequence", "destination", "state"}:
                    raise ValueError
                item_id = item["item_id"]
                sequence = item["sequence"]
                destination = item["destination"]
                state = item["state"]
                if (not isinstance(item_id, str) or not item_id
                        or len(item_id) > 128
                        or any(not (character.isalnum()
                                   or character in "._:-")
                               for character in item_id)
                        or item_id in identifiers
                        or not isinstance(sequence, int)
                        or isinstance(sequence, bool) or sequence < 1
                        or sequence in sequences
                        or destination not in VOICE_DRAFT_DESTINATIONS
                        or state not in VOICE_DRAFT_STATES):
                    raise ValueError
                identifiers.add(item_id)
                sequences.add(sequence)
                drafts.append(VoiceDraftMetadata(
                    item_id, sequence, destination, state))
            drafts.sort(key=lambda item: item.sequence)
            self._inspected_voice_draft_ids = identifiers
            return tuple(drafts)
        except Exception:
            self._inspected_voice_draft_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized("operation.voice_objects.inspect_failed"),
                notice_level="error",
            )
            raise ValueError(self.state.notice) from None

    def reveal_voice_object_draft(
            self, draft: VoiceDraftMetadata) -> RevealedVoiceDraft:
        """Reveal content transiently for one metadata row already inspected."""
        if (not isinstance(draft, VoiceDraftMetadata)
                or draft.item_id not in self._inspected_voice_draft_ids):
            raise ValueError(self.localized(
                "operation.voice_objects.reveal_failed"))
        try:
            raw = self.actions.reveal_voice_object_draft(draft.item_id)
            if not isinstance(raw, Mapping) or set(raw) != {
                    "sequence", "destination", "state", "content"}:
                raise ValueError
            sequence = raw["sequence"]
            destination = raw["destination"]
            state = raw["state"]
            content = raw["content"]
            if (sequence != draft.sequence
                    or destination != draft.destination
                    or state not in VOICE_DRAFT_STATES
                    or not isinstance(content, str)
                    or not content or "\x00" in content
                    or len(content) > VOICE_DRAFT_CONTENT_LIMIT):
                raise ValueError
            revealed = RevealedVoiceDraft(
                sequence, destination, state, content)
            self._revealed_voice_draft_ids.add(draft.item_id)
            return revealed
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized("operation.voice_objects.reveal_failed"),
                notice_level="error",
            )
            raise ValueError(self.state.notice) from None

    def compose_voice_object_email(
        self, draft: VoiceDraftMetadata,
    ) -> EmailComposeReceipt:
        """Request one native compose window after the GUI confirmation."""

        unavailable = EmailComposeReceipt("unavailable", False)
        if (not isinstance(draft, VoiceDraftMetadata)
                or draft.item_id not in self._inspected_voice_draft_ids
                or draft.destination != "email_draft"
                or draft.state != "queued"):
            return unavailable
        try:
            nonce = self.actions.issue_voice_object_email_compose_nonce()
            if (not isinstance(nonce, str) or not 16 <= len(nonce) <= 96
                    or any(not (character.isalnum() or character in "-_")
                           for character in nonce)):
                raise ValueError
            receipt = normalize_email_compose_receipt(
                self.actions.compose_voice_object_email(
                    nonce, draft.item_id))
            self._inspected_voice_draft_ids.clear()
            return receipt
        except Exception:
            self._inspected_voice_draft_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.voice_objects.compose_failed"),
                notice_level="error",
            )
            return unavailable

    def copy_voice_object_draft(
        self, draft: VoiceDraftMetadata,
    ) -> VoiceDraftCopyReceipt:
        """Copy one freshly revalidated task/calendar draft after confirmation."""

        unavailable = VoiceDraftCopyReceipt("unavailable", False)
        if (not isinstance(draft, VoiceDraftMetadata)
                or draft.item_id not in self._inspected_voice_draft_ids
                or draft.item_id not in self._revealed_voice_draft_ids
                or draft.destination not in {"task", "calendar_draft"}
                or draft.state != "queued"):
            return unavailable
        self._voice_draft_clipboard_clear_available = False
        try:
            nonce = self.actions.issue_voice_object_copy_nonce()
            if (not isinstance(nonce, str) or not 16 <= len(nonce) <= 96
                    or any(not (character.isalnum() or character in "-_")
                           for character in nonce)):
                raise ValueError
            receipt = normalize_voice_draft_copy_receipt(
                self.actions.copy_voice_object_draft(
                    nonce, draft.item_id, draft.destination))
            self._inspected_voice_draft_ids.clear()
            self._revealed_voice_draft_ids.clear()
            self._voice_draft_clipboard_clear_available = (
                receipt.state == "copied")
            return receipt
        except Exception:
            self._inspected_voice_draft_ids.clear()
            self._revealed_voice_draft_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized("operation.voice_objects.copy_failed"),
                notice_level="error",
            )
            return unavailable

    def clear_voice_object_draft_clipboard(self) -> VoiceDraftClearReceipt:
        """Clear the last owned copy after a distinct explicit GUI action."""

        unavailable = VoiceDraftClearReceipt("unavailable", False)
        if not self._voice_draft_clipboard_clear_available:
            return unavailable
        self._voice_draft_clipboard_clear_available = False
        try:
            nonce = self.actions.issue_voice_object_clear_clipboard_nonce()
            if (not isinstance(nonce, str) or not 16 <= len(nonce) <= 96
                    or any(not (character.isalnum() or character in "-_")
                           for character in nonce)):
                raise ValueError
            return normalize_voice_draft_clear_receipt(
                self.actions.clear_voice_object_draft_clipboard(nonce))
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized("operation.voice_objects.clear_failed"),
                notice_level="error",
            )
            return unavailable

    def transition_voice_object_draft(
            self, draft: VoiceDraftMetadata, *, target: str) -> GUIState:
        """Apply one explicit inert terminal transition to an inspected draft."""
        if (not isinstance(draft, VoiceDraftMetadata)
                or draft.item_id not in self._inspected_voice_draft_ids
                or target not in {"acknowledged", "cancelled"}):
            raise ValueError(self.localized(
                "operation.voice_objects.transition_failed"))
        action = (self.actions.acknowledge_voice_object_draft
                  if target == "acknowledged"
                  else self.actions.cancel_voice_object_draft)
        try:
            if not action(draft.item_id):
                raise ValueError
            self._inspected_voice_draft_ids.clear()
            self.refresh()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.voice_object_acknowledged"
                    if target == "acknowledged" else
                    "settings.notice.voice_object_cancelled"),
                notice_level="success",
            )
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.voice_objects.transition_failed"),
                notice_level="error",
            )
        return self.state

    def purge_terminal_voice_object_drafts(self) -> GUIState:
        """Purge only finished drafts after an explicit inspector action."""
        try:
            removed = self.actions.purge_terminal_voice_object_drafts()
            if (not isinstance(removed, int) or isinstance(removed, bool)
                    or removed < 0):
                raise ValueError
            self._inspected_voice_draft_ids.clear()
            self.refresh()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.voice_objects_purged", count=removed),
                notice_level="success",
            )
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized("operation.voice_objects.purge_failed"),
                notice_level="error",
            )
        return self.state

    @staticmethod
    def _demonstration_metadata(
            raw: Any) -> DemonstrationDraftMetadata:
        if not isinstance(raw, Mapping) or set(raw) != {
                "draft_id", "sequence", "domain", "state", "step_count"}:
            raise ValueError
        draft_id = raw["draft_id"]
        sequence = raw["sequence"]
        domain = raw["domain"]
        state = raw["state"]
        step_count = raw["step_count"]
        suffix = draft_id[5:] if isinstance(draft_id, str) else ""
        if (not isinstance(draft_id, str) or len(draft_id) != 37
                or not draft_id.startswith("demo-") or len(suffix) != 32
                or any(character not in "0123456789abcdef"
                       for character in suffix)
                or not isinstance(sequence, int) or isinstance(sequence, bool)
                or sequence < 1 or domain not in DEMONSTRATION_DOMAINS
                or state not in DEMONSTRATION_STATES
                or not isinstance(step_count, int)
                or isinstance(step_count, bool)
                or not 0 <= step_count <= DEMONSTRATION_STEP_LIMIT):
            raise ValueError
        return DemonstrationDraftMetadata(
            draft_id, sequence, domain, state, step_count)

    def inspect_demonstration_drafts(
            self) -> tuple[DemonstrationDraftMetadata, ...]:
        """List content-free metadata only after explicit authoring entry."""
        try:
            raw = self.actions.inspect_demonstration_drafts()
            if (isinstance(raw, (str, bytes))
                    or not isinstance(raw, Sequence)
                    or len(raw) > DEMONSTRATION_INSPECT_LIMIT):
                raise ValueError
            drafts = [self._demonstration_metadata(item) for item in raw]
            identifiers = {draft.draft_id for draft in drafts}
            sequences = {draft.sequence for draft in drafts}
            if len(identifiers) != len(drafts) or len(sequences) != len(drafts):
                raise ValueError
            drafts.sort(key=lambda item: item.sequence)
            self._inspected_demonstration_ids = identifiers
            self._revealed_demonstration_ids.clear()
            return tuple(drafts)
        except Exception:
            self._inspected_demonstration_ids.clear()
            self._revealed_demonstration_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.inspect_failed"),
                notice_level="error",
            )
            raise ValueError(self.state.notice) from None

    def create_demonstration_draft(
            self, domain: str) -> DemonstrationDraftMetadata:
        """Ask the runtime to allocate one opaque ID for a closed domain."""
        normalized = str(domain).strip().casefold()
        if normalized not in DEMONSTRATION_DOMAINS:
            raise ValueError(self.localized(
                "operation.demonstrations.create_failed"))
        try:
            draft = self._demonstration_metadata(
                self.actions.create_demonstration_draft(normalized))
            if (draft.domain != normalized or draft.state != "recording"
                    or draft.step_count != 0
                    or draft.draft_id in self._inspected_demonstration_ids):
                raise ValueError
            self._inspected_demonstration_ids.add(draft.draft_id)
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.demonstration_created"),
                notice_level="success",
            )
            return draft
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.create_failed"),
                notice_level="error",
            )
            raise ValueError(self.state.notice) from None

    def reveal_demonstration_draft(
            self, draft: DemonstrationDraftMetadata,
    ) -> RevealedDemonstrationDraft:
        """Reveal private descriptions for one already inspected metadata row."""
        if (not isinstance(draft, DemonstrationDraftMetadata)
                or draft.draft_id not in self._inspected_demonstration_ids):
            raise ValueError(self.localized(
                "operation.demonstrations.reveal_failed"))
        try:
            raw = self.actions.reveal_demonstration_draft(draft.draft_id)
            if not isinstance(raw, Mapping) or set(raw) != {
                    "sequence", "domain", "state", "steps"}:
                raise ValueError
            steps_raw = raw["steps"]
            if (raw["sequence"] != draft.sequence
                    or raw["domain"] != draft.domain
                    or raw["state"] not in DEMONSTRATION_STATES
                    or isinstance(steps_raw, (str, bytes))
                    or not isinstance(steps_raw, Sequence)
                    or len(steps_raw) > DEMONSTRATION_STEP_LIMIT):
                raise ValueError
            steps = []
            for item in steps_raw:
                if (not isinstance(item, Mapping)
                        or set(item) != {"action", "text"}
                        or item["action"] not in DEMONSTRATION_ACTIONS[
                            draft.domain]
                        or not isinstance(item["text"], str)
                        or not item["text"] or "\x00" in item["text"]
                        or len(item["text"]) > DEMONSTRATION_TEXT_LIMIT):
                    raise ValueError
                steps.append(DemonstrationStepPreview(
                    item["action"], item["text"]))
            if len(steps) != draft.step_count:
                raise ValueError
            self._revealed_demonstration_ids.add(draft.draft_id)
            return RevealedDemonstrationDraft(
                draft.sequence, draft.domain, raw["state"], tuple(steps))
        except Exception:
            self._revealed_demonstration_ids.discard(draft.draft_id)
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.reveal_failed"),
                notice_level="error",
            )
            raise ValueError(self.state.notice) from None

    def record_demonstration_step(
            self, draft: DemonstrationDraftMetadata, *, action: str,
            text: str) -> GUIState:
        """Store one bounded description after explicit Reveal/Edit."""
        if (not isinstance(draft, DemonstrationDraftMetadata)
                or draft.draft_id not in self._revealed_demonstration_ids
                or draft.state != "recording"
                or action not in DEMONSTRATION_ACTIONS.get(draft.domain, ())
                or not isinstance(text, str) or not text or "\x00" in text
                or len(text) > DEMONSTRATION_TEXT_LIMIT):
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.record_failed"),
                notice_level="error",
            )
            raise ValueError(self.state.notice)
        try:
            if not self.actions.record_demonstration_step(
                    draft.draft_id, action, text):
                raise ValueError
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.demonstration_step_recorded"),
                notice_level="success",
            )
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.record_failed"),
                notice_level="error",
            )
        return self.state

    def approve_demonstration_draft(
            self, draft: DemonstrationDraftMetadata) -> GUIState:
        """Explicitly approve a revealed recipe without execution authority."""
        if (not isinstance(draft, DemonstrationDraftMetadata)
                or draft.draft_id not in self._revealed_demonstration_ids
                or draft.state != "recording" or draft.step_count < 1):
            raise ValueError(self.localized(
                "operation.demonstrations.approve_failed"))
        try:
            if not self.actions.approve_demonstration_draft(draft.draft_id):
                raise ValueError
            self._inspected_demonstration_ids.clear()
            self._revealed_demonstration_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.demonstration_approved"),
                notice_level="success",
            )
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.approve_failed"),
                notice_level="error",
            )
        return self.state

    def cancel_demonstration_draft(
            self, draft: DemonstrationDraftMetadata) -> GUIState:
        """Explicitly roll back one inspected, unapproved recipe."""
        if (not isinstance(draft, DemonstrationDraftMetadata)
                or draft.draft_id not in self._inspected_demonstration_ids
                or draft.state != "recording"):
            raise ValueError(self.localized(
                "operation.demonstrations.cancel_failed"))
        try:
            if not self.actions.cancel_demonstration_draft(draft.draft_id):
                raise ValueError
            self._inspected_demonstration_ids.clear()
            self._revealed_demonstration_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.demonstration_cancelled"),
                notice_level="success",
            )
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.cancel_failed"),
                notice_level="error",
            )
        return self.state

    def delete_approved_demonstration_draft(
            self, draft: DemonstrationDraftMetadata) -> GUIState:
        """Explicitly remove one selected approved recipe and private text."""
        if (not isinstance(draft, DemonstrationDraftMetadata)
                or draft.draft_id not in self._inspected_demonstration_ids
                or draft.state != "approved"):
            raise ValueError(self.localized(
                "operation.demonstrations.delete_failed"))
        try:
            if not self.actions.delete_approved_demonstration_draft(
                    draft.draft_id):
                raise ValueError
            self._inspected_demonstration_ids.clear()
            self._revealed_demonstration_ids.clear()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "settings.notice.demonstration_deleted"),
                notice_level="success",
            )
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.demonstrations.delete_failed"),
                notice_level="error",
            )
        return self.state

    def inspect_result_evidence(self) -> ResultEvidenceInspection:
        """Reveal private latest-result detail without retaining it in state."""

        try:
            return normalize_result_evidence(
                self.actions.inspect_result_evidence())
        except Exception:
            self.state = replace(
                self.state,
                notice=self.localized("results.inspect.empty"),
                notice_level="error",
            )
            raise ValueError(self.state.notice) from None

    def play_retained_span(self) -> GUIState:
        try:
            played = bool(self.actions.play_retained_span())
            self.state = replace(
                self.state,
                notice=self.localized(
                    "results.audio.notice.played" if played else
                    "results.audio.notice.unavailable"),
                notice_level="success" if played else "info",
            )
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.acoustic.play_failed", error=error),
                notice_level="error")
        return self.state

    def clear_retained_spans(self) -> GUIState:
        try:
            self.actions.clear_retained_spans()
            self.refresh()
            self.state = replace(
                self.state,
                notice=self.localized("results.audio.notice.cleared"),
                notice_level="success",
            )
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.acoustic.clear_failed", error=error),
                notice_level="error")
        return self.state

    def set_paused(self, paused: bool) -> GUIState:
        desired = bool(paused)
        try:
            (self.actions.pause if desired else self.actions.resume)()
            capture_state = self.localized(
                "default.capture.paused" if desired
                else "default.capture.ready")
            phase, title, detail = _status_presentation(
                capture_state=capture_state,
                paused=desired,
                hotkey_label=self.state.hotkey_label,
                outbox_count=self.state.outbox_count,
                service_status=self.state.service_status,
                degraded_issues=self.state.degraded_issues,
                locale=self.locale,
            )
            self.state = replace(
                self.state,
                paused=desired,
                capture_state=capture_state,
                status_phase=phase,
                status_title=title,
                status_detail=detail,
                notice="",
                notice_level="info",
            )
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "overview.notice.capture.error", error=error),
                notice_level="error")
        return self.state

    def open_log(self) -> GUIState:
        try:
            self.actions.open_log()
            self.state = replace(
                self.state, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.log.open_failed", error=error),
                notice_level="error")
        return self.state

    def permission_recovery_needed(self) -> bool:
        """True only while the evidence-backed permission step is incomplete."""
        return bool(self.state.onboarding_steps) and not bool(
            self.state.onboarding_steps[0].complete)

    def open_system_settings(self) -> GUIState:
        """Open the user-controlled macOS permission surface without guessing a grant."""
        if not self.permission_recovery_needed():
            return self.state
        try:
            self.actions.open_system_settings()
            self.state = replace(
                self.state,
                notice=self.localized(
                    "diagnostics.notice.system_settings.opened"),
                notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.system_settings.open_failed", error=error),
                notice_level="error")
        return self.state

    def copy_support_snapshot(self) -> GUIState:
        """Copy the fixed public diagnostic projection through the injected seam."""
        try:
            self.actions.copy_support_snapshot(support_snapshot_text(self.state))
            self.state = replace(
                self.state,
                notice=self.localized("diagnostics.notice.support_snapshot.copied"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.support_snapshot.copy_failed", error=error),
                notice_level="error")
        return self.state

    def preview_point_and_speak(self, phrase: str) -> PointAndSpeakPreview:
        """Run one explicit preview without placing private text in GUI state."""

        if (not isinstance(phrase, str) or not phrase.strip()
                or len(phrase) > POINT_AND_SPEAK_MAX_PHRASE_CHARS
                or any(ord(character) < 32 for character in phrase)):
            raise ValueError(self.localized(
                "point_and_speak.validation.phrase",
                limit=POINT_AND_SPEAK_MAX_PHRASE_CHARS))
        try:
            return normalize_point_and_speak_preview(
                self.actions.preview_point_and_speak(phrase))
        except Exception:
            return PointAndSpeakPreview(state="unavailable")

    def press_point_and_speak(
        self, nonce: str, phrase: str, expected_role: str,
    ) -> PointAndSpeakActionResult:
        """Request one explicit press without retaining phrase or target data."""

        if (not isinstance(nonce, str) or not 16 <= len(nonce) <= 96
                or any(not (character.isalnum() or character in "-_")
                       for character in nonce)
                or not isinstance(phrase, str) or not phrase.strip()
                or not isinstance(expected_role, str)
                or expected_role not in POINT_AND_SPEAK_ACTION_ROLES
                or len(phrase) > POINT_AND_SPEAK_MAX_PHRASE_CHARS
                or any(ord(character) < 32 for character in phrase)):
            raise ValueError(self.localized(
                "point_and_speak.validation.phrase",
                limit=POINT_AND_SPEAK_MAX_PHRASE_CHARS))
        try:
            return normalize_point_and_speak_action(
                self.actions.press_point_and_speak(
                    nonce, phrase, expected_role))
        except Exception:
            return unavailable_point_and_speak_action()

    def issue_point_and_speak_nonce(self) -> str:
        """Request a short-lived process-session nonce only after confirmation."""

        nonce = self.actions.issue_point_and_speak_nonce()
        if (not isinstance(nonce, str) or not 16 <= len(nonce) <= 96
                or any(not (character.isalnum() or character in "-_")
                       for character in nonce)):
            raise ValueError("Point-and-Speak action nonce is unavailable")
        return nonce

    def preview_drop_to_target(
        self, phrase: str, role: str, source_kind: str, effect: str,
    ) -> DropTargetPreview:
        """Run one explicit inert preview without retaining target text."""

        if (not isinstance(phrase, str) or not phrase.strip()
                or len(phrase) > DROP_TARGET_MAX_PHRASE_CHARS
                or any(ord(character) < 32 for character in phrase)):
            raise ValueError(self.localized(
                "drop_target.validation.phrase",
                limit=DROP_TARGET_MAX_PHRASE_CHARS))
        if (role not in DROP_TARGET_ROLES
                or source_kind not in DROP_TARGET_SOURCE_KINDS
                or effect not in DROP_TARGET_EFFECTS):
            raise ValueError("Invalid Drop-to-Target capability declaration")
        try:
            return normalize_drop_target_preview(
                self.actions.preview_drop_to_target(
                    phrase, role, source_kind, effect))
        except Exception:
            return DropTargetPreview(state="unavailable")

    def open_source_and_license(self) -> GUIState:
        try:
            self.actions.open_source_and_license()
            self.state = replace(
                self.state, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.source.open_failed", error=error),
                notice_level="error")
        return self.state

    def open_local_license_notices(self) -> GUIState:
        try:
            self.actions.open_local_license_notices()
            self.state = replace(
                self.state, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state,
                notice=self.localized(
                    "operation.licenses.open_failed", error=error),
                notice_level="error")
        return self.state

    def copy_latest_outbox(self) -> GUIState:
        try:
            self.actions.copy_latest_outbox()
            self.state = replace(
                self.state, outbox_count=max(0, self.state.outbox_count - 1),
                notice=self.localized("overview.notice.outbox.copied"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "overview.notice.outbox.error", error=error),
                notice_level="error")
        return self.state

    def rerun_verification(self) -> GUIState:
        self.state = replace(
            self.state, verification=self.localized(
                "diagnostics.verification.running"), notice="",
            notice_level="info")
        self.state = replace(
            self.state, verification=self.verification_result())
        return self.state

    def verification_result(self) -> str:
        """Run the injected check without mutating UI state off-main."""
        try:
            result = self.actions.rerun_verification()
            if isinstance(result, Mapping):
                passed = result.get("passed")
                message = _clean_text(result.get("message"), "")
                status = message or self.localized(
                    "diagnostics.verification.passed" if passed is not False
                    else "diagnostics.verification.attention")
            elif result is False:
                status = self.localized(
                    "diagnostics.verification.attention")
            elif isinstance(result, str) and result.strip():
                status = result.strip()
            else:
                status = self.localized("diagnostics.verification.passed")
            return status
        except Exception as error:
            return self.localized(
                "diagnostics.verification.failed", error=error)

    def set_verification(self, status: str) -> GUIState:
        self.state = replace(self.state, verification=status)
        return self.state


def sync_accessibility(view: Any, value: str, *, label: str = "") -> None:
    """Keep VoiceOver state synchronized with a dynamic visual control."""
    try:
        if label:
            view.setAccessibilityLabel_(label)
        view.setAccessibilityValue_(value)
    except Exception:
        pass


def set_accessible_text(view: Any, value: str, *, label: str) -> None:
    """Atomically update a dynamic text field's visual and VoiceOver state."""
    view.setStringValue_(value)
    sync_accessibility(view, value, label=label)


try:  # The view-model above remains usable in headless test environments.
    import objc
    from AppKit import (
        NSApplication,
        NSAlert,
        NSBackingStoreBuffered,
        NSBezelStyleRounded,
        NSBox,
        NSBoxCustom,
        NSButton,
        NSColor,
        NSControlStateValueOff,
        NSControlStateValueOn,
        NSAppearanceNameAqua,
        NSAppearanceNameDarkAqua,
        NSEventModifierFlagCommand,
        NSFont,
        NSFontDescriptorSystemDesignRounded,
        NSImage,
        NSImageScaleProportionallyUpOrDown,
        NSImageView,
        NSLineBorder,
        NSMakeRect,
        NSNoBorder,
        NSNoTitle,
        NSProgressIndicator,
        NSPopUpButton,
        NSSavePanel,
        NSScrollView,
        NSSegmentedControl,
        NSSegmentStyleRounded,
        NSTextField,
        NSTextView,
        NSView,
        NSWindow,
        NSWorkspace,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSLocale, NSObject, NSTimer, NSUserDefaults
    from Quartz import CASpringAnimation

    APPKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only outside macOS installs
    APPKIT_AVAILABLE = False
    objc = None
    NSObject = object


if APPKIT_AVAILABLE:
    _ACCENT = NSColor.colorWithCalibratedRed_green_blue_alpha_(
        0.31, 0.36, 0.95, 1.0)
    _TEXT = NSColor.labelColor()
    _SECONDARY = NSColor.secondaryLabelColor()
    _CARD = NSColor.controlBackgroundColor()

    def _theme_color(
            color: tuple[float, float, float], alpha: float = 1.0) -> Any:
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(
            color[0], color[1], color[2], alpha)

    def _uses_dark_appearance(view: Any) -> bool:
        try:
            match = view.effectiveAppearance() \
                .bestMatchFromAppearancesWithNames_(
                    [NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
            return str(match) == str(NSAppearanceNameDarkAqua)
        except Exception:
            return False

    def _accessible(view: Any, label: str, help_text: str = "") -> Any:
        """Apply explicit VoiceOver copy without depending on visual text."""
        try:
            view.setAccessibilityLabel_(label)
            if help_text:
                view.setAccessibilityHelp_(help_text)
        except Exception:
            pass
        return view

    def _label(text: str, frame: Any, *, size: float = 13,
               weight: str = "regular", color: Any = None,
               accessibility_label: str = "", rounded: bool = False,
               wrap: bool = False, alignment: int | None = None) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(frame)
        font_weight = (
            0.6 if weight == "bold" else 0.35
            if weight == "medium" else 0.0)
        font = NSFont.systemFontOfSize_weight_(size, font_weight)
        if rounded:
            try:
                descriptor = font.fontDescriptor().fontDescriptorWithDesign_(
                    NSFontDescriptorSystemDesignRounded)
                if descriptor is not None:
                    font = NSFont.fontWithDescriptor_size_(descriptor, size)
            except Exception:
                pass
        label.setFont_(font)
        label.setTextColor_(color or _TEXT)
        label.setLineBreakMode_(0)
        if wrap:
            label.setUsesSingleLineMode_(False)
            label.setMaximumNumberOfLines_(0)
        if alignment is not None:
            label.setAlignment_(alignment)
        return _accessible(label, accessibility_label or text)

    def _button(title: str, frame: Any, target: Any, action: str,
                *, help_text: str = "") -> Any:
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        font = NSFont.systemFontOfSize_weight_(12.0, 0.45)
        try:
            descriptor = font.fontDescriptor().fontDescriptorWithDesign_(
                NSFontDescriptorSystemDesignRounded)
            if descriptor is not None:
                font = NSFont.fontWithDescriptor_size_(descriptor, 12.0)
        except Exception:
            pass
        button.setFont_(font)
        button.setWantsLayer_(True)
        if button.layer() is not None:
            button.layer().setCornerRadius_(
                SURFACE_SPECS["control"].radius)
        button.setTarget_(target)
        button.setAction_(action)
        return _accessible(button, title, help_text)

    def _card(frame: Any, treatment: str = "card") -> Any:
        spec = SURFACE_SPECS.get(treatment, SURFACE_SPECS["card"])
        palette = palette_for_appearance(
            _uses_dark_appearance(NSApplication.sharedApplication()))
        box = NSBox.alloc().initWithFrame_(frame)
        box.setBoxType_(NSBoxCustom)
        box.setBorderType_(NSLineBorder)
        box.setTitlePosition_(NSNoTitle)
        box.setFillColor_(_theme_color(palette.surface))
        box.setBorderColor_(_theme_color(palette.line, 0.18))
        box.setBorderWidth_(spec.border_width)
        box.setCornerRadius_(spec.radius)
        box.setWantsLayer_(True)
        layer = box.layer()
        if layer is not None and (spec.shadow_x or spec.shadow_y):
            try:
                layer.setShadowColor_(_theme_color(palette.line).CGColor())
                layer.setShadowOpacity_(0.94)
                layer.setShadowRadius_(0.0)
                layer.setShadowOffset_((spec.shadow_x, spec.shadow_y))
            except Exception:
                pass
        return box

    class WhisperFaceWindowController(NSObject):
        """One-window AppKit controller; created lazily by ``WhisperFaceGUI``."""

        @objc.python_method
        def _initialize(self, view_model: WhisperFaceViewModel,
                        *, read_system_state: bool) -> Any:
            self.view_model = view_model
            self.pages: dict[str, Any] = {}
            self.dynamic: dict[str, Any] = {}
            self.timer = None
            self.defaults = None
            self._face_images: dict[tuple[str, bool], Any] = {}
            self._onboarding_stage: str | None = None
            self._onboarding_presentation: OnboardingPresentation | None = None
            self._rendered_section: str | None = None
            if read_system_state:
                try:
                    preferred = NSLocale.preferredLanguages()
                    if preferred:
                        self.view_model.set_locale(str(preferred[0]))
                except Exception:
                    pass
                self.defaults = NSUserDefaults.alloc().initWithSuiteName_(
                    DEFAULTS_SUITE)
                self.view_model.acknowledge_onboarding(bool(
                    self.defaults.boolForKey_("onboardingComplete")))
                try:
                    reduce_motion = bool(
                        NSWorkspace.sharedWorkspace()
                        .accessibilityDisplayShouldReduceMotion())
                    if reduce_motion:
                        self.view_model.state = replace(
                            self.view_model.state,
                            prefers_reduced_motion=True)
                except Exception:
                    pass
            self._build_window()
            return self

        @objc.python_method
        def _l(self, key: str, **values: Any) -> str:
            return self.view_model.localized(key, **values)

        @objc.python_method
        def _face_image(self, face: str, *, talk: bool) -> Any:
            # The colored character, not the menu-bar silhouette. A template
            # glyph is drawn to survive 18 points; this window shows the face
            # at 34 and the onboarding hero at 146, where a flat silhouette is
            # just a blob.
            key = (face, talk)
            if key not in self._face_images:
                path = (Path(__file__).resolve().parent / "icons" / "faces"
                        / "color"
                        / f"{face}-{'talk' if talk else 'idle'}.svg")
                image = NSImage.alloc().initWithContentsOfFile_(str(path))
                if image is not None:
                    image.setTemplate_(False)
                self._face_images[key] = image
            return self._face_images[key]

        @objc.python_method
        def _apply_onboarding_theme(
                self, state: GUIState,
                presentation: OnboardingPresentation) -> None:
            palette = palette_for_appearance(
                _uses_dark_appearance(self.window))
            card = self.dynamic["onboarding_card"]
            card.setFillColor_(_theme_color(palette.bg))
            card.setBorderColor_(_theme_color(palette.line, 0.22))
            chip = self.dynamic["onboarding_face_chip"]
            chip.setFillColor_(_theme_color(palette.surface))
            chip.setBorderColor_(
                _theme_color(FACE_CHIP_COLORS[state.face], 0.85))
            self.dynamic["onboarding_progress"].setTextColor_(
                _theme_color(palette.brand))
            self.dynamic["onboarding_title"].setTextColor_(
                _theme_color(palette.ink))
            self.dynamic["onboarding_detail"].setTextColor_(
                _theme_color(palette.ink_soft))
            self.dynamic["onboarding_face_kicker"].setTextColor_(
                _theme_color(palette.ink_soft))
            self.dynamic["onboarding_status"].setTextColor_(
                _theme_color(
                    palette.brand if presentation.complete
                    else palette.accent))
            try:
                self.dynamic["onboarding_action"].setBezelColor_(
                    _theme_color(palette.brand))
            except Exception:
                pass
            for step, step_card, control in zip(
                    state.onboarding_steps,
                    self.dynamic["onboarding_step_cards"],
                    self.dynamic["onboarding_steps"]):
                current = step.key == presentation.current_key
                if step.complete:
                    fill = _theme_color(palette.brand, 0.20)
                    text = _theme_color(palette.brand)
                elif current:
                    fill = _theme_color(palette.accent, 0.28)
                    text = _theme_color(palette.ink)
                else:
                    fill = _theme_color(palette.surface, 0.76)
                    text = _theme_color(palette.ink_soft)
                step_card.setFillColor_(fill)
                control.setTextColor_(text)

        @objc.python_method
        def _animate_onboarding_face(
                self, presentation: OnboardingPresentation) -> None:
            stage = "complete" if presentation.complete else (
                presentation.current_key or "hidden")
            if (not presentation.visible or stage == self._onboarding_stage
                    or not bool(self.window.isVisible())):
                return
            self._onboarding_stage = stage
            if self.view_model.state.prefers_reduced_motion:
                return
            layer = self.dynamic["onboarding_face_chip"].layer()
            if layer is None:
                return
            spec = MOTION_SPECS["pop"]
            spring = CASpringAnimation.animationWithKeyPath_("transform.scale")
            spring.setFromValue_(spec.squash_x)
            spring.setToValue_(1.0)
            spring.setMass_(spec.mass)
            spring.setStiffness_(spec.stiffness)
            spring.setDamping_(spec.damping)
            spring.setInitialVelocity_(spec.initial_velocity)
            spring.setDuration_(spec.duration)
            spring.setRemovedOnCompletion_(True)
            layer.addAnimation_forKey_(spring, "onboarding-soft-pop")

        @objc.python_method
        def _apply_window_theme(self, state: GUIState) -> None:
            """Apply the shared palette to the whole working window."""
            palette = palette_for_appearance(
                _uses_dark_appearance(self.window))
            root = self.window.contentView()
            root.setWantsLayer_(True)
            if root.layer() is not None:
                root.layer().setBackgroundColor_(
                    _theme_color(palette.bg).CGColor())

            def theme_boxes(view: Any) -> None:
                for child in view.subviews():
                    try:
                        if child.isKindOfClass_(NSBox):
                            child.setFillColor_(_theme_color(palette.surface))
                            child.setBorderColor_(
                                _theme_color(palette.line, 0.18))
                    except Exception:
                        pass
                    if hasattr(child, "subviews"):
                        theme_boxes(child)

            theme_boxes(root)
            self.dynamic["window_header"].setFillColor_(
                _theme_color(palette.surface))
            self.dynamic["window_header"].setBorderColor_(
                _theme_color(palette.line))
            # The chip used to carry the face color because the face on top
            # was a black silhouette. The character is colored now, so the
            # color moves onto the character and the chip becomes a quiet
            # container with a face-tinted ring. Tinting the fill instead
            # muddies every warm face against the dark panel.
            chip = self.dynamic["window_face_chip"]
            chip.setFillColor_(_theme_color(palette.surface))
            chip.setBorderColor_(
                _theme_color(FACE_CHIP_COLORS[state.face], 0.85))
            self.dynamic["window_title"].setTextColor_(
                _theme_color(palette.ink))
            self.dynamic["window_subtitle"].setTextColor_(
                _theme_color(palette.ink_soft))
            self.dynamic["window_badge"].setTextColor_(
                _theme_color(palette.brand))
            self.dynamic["window_nav_shell"].setFillColor_(
                _theme_color(palette.surface, 0.86))
            try:
                self.section_control.setSelectedSegmentBezelColor_(
                    _theme_color(palette.brand))
            except Exception:
                pass

        @objc.python_method
        def _animate_section_change(self, state: GUIState) -> None:
            previous = self._rendered_section
            self._rendered_section = state.section
            if (previous is None or previous == state.section
                    or state.prefers_reduced_motion
                    or not bool(self.window.isVisible())):
                return
            page = self.pages[state.section]
            page.setWantsLayer_(True)
            layer = page.layer()
            if layer is None:
                return
            spec = MOTION_SPECS["release"]
            spring = CASpringAnimation.animationWithKeyPath_(
                "transform.scale")
            spring.setFromValue_(spec.squash_x)
            spring.setToValue_(1.0)
            spring.setMass_(spec.mass)
            spring.setStiffness_(spec.stiffness)
            spring.setDamping_(spec.damping)
            spring.setInitialVelocity_(spec.initial_velocity)
            spring.setDuration_(spec.duration)
            spring.setRemovedOnCompletion_(True)
            layer.addAnimation_forKey_(spring, "section-soft-release")
            face_layer = self.dynamic["window_face_chip"].layer()
            if face_layer is not None:
                face_layer.addAnimation_forKey_(
                    spring, "header-face-soft-release")

        def initWithViewModel_(self, view_model: WhisperFaceViewModel):
            self = objc.super(WhisperFaceWindowController, self).init()
            if self is None:
                return None
            return self._initialize(view_model, read_system_state=True)

        def initForSmokeWithViewModel_(
                self, view_model: WhisperFaceViewModel):
            """Construct without reading or writing any user/system state."""
            self = objc.super(WhisperFaceWindowController, self).init()
            if self is None:
                return None
            return self._initialize(view_model, read_system_state=False)

        def _build_window(self) -> None:
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                     NSWindowStyleMaskMiniaturizable)
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 820, 570), style, NSBackingStoreBuffered, False)
            self.window.setTitle_(APP_NAME)
            self.window.setDelegate_(self)
            self.window.center()
            root = self.window.contentView()
            root.setWantsLayer_(True)

            header = _card(
                NSMakeRect(20, 500, 780, 52), treatment="playful")
            face_chip = _card(
                NSMakeRect(10, 4, 44, 44), treatment="control")
            face_image = NSImageView.alloc().initWithFrame_(
                NSMakeRect(15, 9, 34, 34))
            face_image.setImageScaling_(
                NSImageScaleProportionallyUpOrDown)
            face_image.setEditable_(False)
            title = _label(
                APP_NAME, NSMakeRect(66, 24, 300, 23),
                size=19, weight="bold", rounded=True)
            subtitle = _label(
                self._l("app.subtitle"), NSMakeRect(67, 6, 430, 18),
                size=11, color=_SECONDARY)
            badge = _label(
                self._l("app.local_badge"),
                NSMakeRect(630, 25, 120, 16),
                size=10, weight="bold", color=_ACCENT,
                rounded=True, alignment=2)
            version = _label(
                self._l(
                    "app.version", version=application_build_version()),
                NSMakeRect(630, 9, 120, 14),
                size=8, weight="semibold", color=_SECONDARY,
                rounded=True, alignment=2)
            header.addSubview_(face_chip)
            header.addSubview_(face_image)
            header.addSubview_(title)
            header.addSubview_(subtitle)
            header.addSubview_(badge)
            header.addSubview_(version)
            root.addSubview_(header)

            nav_shell = _card(
                NSMakeRect(25, 447, 770, 38), treatment="work")
            root.addSubview_(nav_shell)

            self.section_control = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(6, 3, 758, 32))
            self.section_control.setSegmentCount_(len(SECTIONS))
            self.section_control.setSegmentStyle_(NSSegmentStyleRounded)
            for index, section in enumerate(SECTIONS):
                self.section_control.setLabel_forSegment_(
                    self._l(f"nav.{section.casefold()}"), index)
                self.section_control.setWidth_forSegment_(
                    758.0 / len(SECTIONS), index)
            self.section_control.setSelectedSegment_(0)
            self.section_control.setTarget_(self)
            self.section_control.setAction_("sectionChanged:")
            _accessible(
                self.section_control,
                self._l("settings.accessibility.sections.label"),
                self._l("settings.accessibility.sections.help"))
            nav_shell.addSubview_(self.section_control)
            self.dynamic.update(
                window_header=header,
                window_face_chip=face_chip,
                window_face=face_image,
                window_title=title,
                window_subtitle=subtitle,
                window_badge=badge,
                window_version=version,
                window_nav_shell=nav_shell,
            )

            page_frame = NSMakeRect(31, 25, 758, 402)
            builders = {
                "Home": self._build_home,
                "Settings": self._build_settings,
                "Advanced": self._build_advanced,
            }
            for section, builder in builders.items():
                page = NSView.alloc().initWithFrame_(page_frame)
                builder(page)
                page.setHidden_(section != "Home")
                root.addSubview_(page)
                self.pages[section] = page
            notice = _label("", NSMakeRect(35, 5, 750, 18),
                            size=11, color=NSColor.systemRedColor())
            root.addSubview_(notice)
            self.dynamic["notice"] = notice
            self.key_views_by_section = {
                "Home": (
                    self.dynamic["onboarding_action"],
                    self.dynamic["pause_button"],
                    self.dynamic["review_issue_button"],
                    self.dynamic["copy_outbox_button"],
                    self.dynamic["result_play_audio_button"],
                    self.dynamic["result_clear_audio_button"],
                    self.dynamic["result_inspect_button"],
                ),
                "Settings": (self.dynamic["settings_pane_control"],),
                "Advanced": (
                    self.dynamic["advanced_button"],
                    self.dynamic["selective_relisten_toggle"],
                    self.dynamic["open_log_button"],
                    self.dynamic["copy_support_snapshot_button"],
                    self.dynamic["verify_button"],
                    self.dynamic["license_button"],
                    self.dynamic["source_button"],
                    self.dynamic["open_system_settings_button"],
                    self.dynamic["export_support_bundle_button"],
                ),
            }
            self.window.setInitialFirstResponder_(self.section_control)
            self.render()

        def _build_home(self, page: Any) -> None:
            hero = _card(NSMakeRect(0, 226, 758, 176))
            phase = _label(
                self._l("overview.phase.ready"),
                NSMakeRect(24, 148, 320, 18),
                size=11, weight="bold", color=_ACCENT)
            status = _label(
                self._l("overview.status.ready.title"),
                NSMakeRect(24, 100, 520, 44), size=32, weight="bold")
            detail = _label("", NSMakeRect(26, 76, 520, 20),
                            size=12, color=_SECONDARY)
            engine = _label("", NSMakeRect(26, 50, 500, 20),
                            size=13, color=_SECONDARY)
            outbox = _label(
                self._l("overview.outbox.empty"),
                NSMakeRect(26, 20, 500, 20),
                size=12, color=_SECONDARY)
            pause = _button(
                self._l("overview.action.pause"),
                NSMakeRect(602, 100, 124, 40), self, "pauseChanged:",
                help_text=self._l("overview.action.pause.help"))
            fix = _button(
                self._l("overview.action.review"),
                NSMakeRect(590, 56, 136, 34),
                self, "reviewIssue:",
                help_text=self._l("overview.action.review.help"))
            copy_outbox = _button(
                self._l("overview.action.copy_outbox"),
                NSMakeRect(590, 18, 136, 34),
                self, "copyOutbox:",
                help_text=self._l("overview.action.copy_outbox.help"))
            hero.addSubview_(phase)
            hero.addSubview_(status)
            hero.addSubview_(detail)
            hero.addSubview_(engine)
            hero.addSubview_(outbox)
            hero.addSubview_(pause)
            hero.addSubview_(fix)
            hero.addSubview_(copy_outbox)
            page.addSubview_(hero)
            self.dynamic.update(
                overview_hero=hero,
                overview_phase=phase,
                overview_status=status,
                overview_detail=detail,
                overview_engine=engine,
                overview_outbox=outbox,
                pause_button=pause,
                review_issue_button=fix,
                copy_outbox_button=copy_outbox,
            )

            onboarding = _card(NSMakeRect(0, 0, 758, 402))
            onboarding.setBorderType_(NSLineBorder)
            onboarding.setBorderWidth_(1.0)
            _accessible(
                onboarding,
                self._l("overview.accessibility.onboarding.steps"))
            face_chip = _card(NSMakeRect(28, 138, 196, 196))
            face_chip.setCornerRadius_(58.0)
            # A hairline reads as an accident at this size; the ring is the
            # only thing carrying the face color on the hero.
            face_chip.setBorderWidth_(2.5)
            face_chip.setWantsLayer_(True)
            face_image = NSImageView.alloc().initWithFrame_(
                NSMakeRect(53, 163, 146, 146))
            face_image.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            face_image.setEditable_(False)
            onboarding.addSubview_(face_chip)
            onboarding.addSubview_(face_image)
            face_kicker = _label(
                self._l("onboarding.privacy"),
                NSMakeRect(28, 93, 196, 38),
                size=11, weight="medium", rounded=True, wrap=True,
                alignment=1)
            onboarding.addSubview_(face_kicker)
            onboarding_progress = _label(
                self._l("overview.onboarding.initial_progress"),
                NSMakeRect(260, 345, 450, 18),
                size=10, weight="bold", color=_ACCENT, rounded=True)
            onboarding_title = _label(
                self._l("onboarding.permissions.title"),
                NSMakeRect(258, 269, 458, 68),
                size=30, weight="bold", rounded=True, wrap=True)
            onboarding_detail = _label(
                "", NSMakeRect(260, 202, 440, 62),
                size=13, color=_SECONDARY, wrap=True)
            onboarding_status = _label(
                "", NSMakeRect(260, 170, 310, 22),
                size=12, weight="bold", rounded=True)
            onboarding_action = _button(
                self._l("onboarding.action.open_system_settings"),
                NSMakeRect(258, 116, 210, 42),
                self, "continueSetup:",
                help_text=self._l("onboarding.action.open_system_settings.help"))
            onboarding_action.setKeyEquivalent_("\r")
            onboarding.addSubview_(onboarding_progress)
            onboarding.addSubview_(onboarding_title)
            onboarding.addSubview_(onboarding_detail)
            onboarding.addSubview_(onboarding_status)
            onboarding.addSubview_(onboarding_action)
            onboarding_steps: list[Any] = []
            onboarding_step_cards: list[Any] = []
            for index, step_key in enumerate((
                    "permissions", "hotkey", "models", "first_dictation")):
                step_card = _card(NSMakeRect(
                    18 + index * 181, 18, 171, 58))
                step_card.setCornerRadius_(13.0)
                step = _label(
                    self._l(f"onboarding.step.{step_key}"),
                    NSMakeRect(11, 8, 149, 42),
                    size=9.5, weight="medium", color=_SECONDARY,
                    rounded=True, wrap=True)
                step_card.addSubview_(step)
                onboarding.addSubview_(step_card)
                onboarding_steps.append(step)
                onboarding_step_cards.append(step_card)
            page.addSubview_(onboarding)
            self.dynamic.update(
                onboarding_card=onboarding,
                onboarding_face_chip=face_chip,
                onboarding_face=face_image,
                onboarding_face_kicker=face_kicker,
                onboarding_progress=onboarding_progress,
                onboarding_title=onboarding_title,
                onboarding_detail=onboarding_detail,
                onboarding_status=onboarding_status,
                onboarding_action=onboarding_action,
                onboarding_steps=tuple(onboarding_steps),
                onboarding_step_cards=tuple(onboarding_step_cards),
            )

            cards = (("overview.metric.last.heading", "overview_last"),
                     ("overview.metric.words.heading", "overview_words"),
                     ("overview.metric.saved.heading", "overview_saved"))
            metric_cards: list[Any] = []
            for index, (heading_key, key) in enumerate(cards):
                card = _card(NSMakeRect(index * 257, 138, 244, 80))
                card.addSubview_(_label(
                    self._l(heading_key), NSMakeRect(18, 52, 208, 18),
                    size=11, color=_SECONDARY))
                value = _label(
                    self._l("overview.metric.last.empty"),
                    NSMakeRect(18, 14, 208, 31),
                    size=21, weight="bold")
                card.addSubview_(value)
                page.addSubview_(card)
                self.dynamic[key] = value
                metric_cards.append(card)
            self.dynamic["overview_metric_cards"] = tuple(metric_cards)

            result_card = _card(NSMakeRect(0, 0, 758, 130))
            result_summary = _label(
                self._l("results.summary.empty"), NSMakeRect(20, 94, 430, 27),
                size=18, weight="bold")
            result_mode = _label(
                self._l("results.mode.capture"), NSMakeRect(600, 96, 138, 22),
                size=12, weight="medium", color=_ACCENT)
            result_engine = _label(
                self._l("results.engine.waiting"),
                NSMakeRect(20, 70, 715, 20),
                size=12, color=_SECONDARY)
            result_audio = _label(
                self._l("results.audio.off"), NSMakeRect(20, 48, 560, 18),
                size=10, color=_SECONDARY)
            play_audio = _button(
                self._l("results.audio.play"), NSMakeRect(372, 8, 102, 30),
                self, "playRetainedSpan:",
                help_text=self._l("results.audio.play.help"))
            clear_audio = _button(
                self._l("results.audio.clear"), NSMakeRect(480, 8, 92, 30),
                self, "clearRetainedSpans:",
                help_text=self._l("results.audio.clear.help"))
            inspect_evidence = _button(
                self._l("results.inspect.action"),
                NSMakeRect(578, 8, 160, 30),
                self,
                "inspectResultEvidence:",
                help_text=self._l("results.inspect.action.help"),
            )
            result_card.addSubview_(result_summary)
            result_card.addSubview_(result_mode)
            result_card.addSubview_(result_engine)
            result_card.addSubview_(result_audio)
            result_card.addSubview_(play_audio)
            result_card.addSubview_(clear_audio)
            result_card.addSubview_(inspect_evidence)
            page.addSubview_(result_card)
            self.dynamic.update(
                home_result_card=result_card,
                result_summary=result_summary,
                result_engine=result_engine,
                result_mode=result_mode,
                result_audio=result_audio,
                result_inspect_button=inspect_evidence,
                result_play_audio_button=play_audio,
                result_clear_audio_button=clear_audio,
            )

        def _build_settings(self, page: Any) -> None:
            page.addSubview_(_label(
                self._l("settings.title"),
                NSMakeRect(4, 372, 500, 28), size=20, weight="bold"))
            page.addSubview_(_label(
                self._l("settings.subtitle"),
                NSMakeRect(5, 352, 720, 18), size=12, color=_SECONDARY))
            pane_control = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(0, 314, 758, 32))
            pane_control.setSegmentCount_(len(SETTINGS_PANES))
            pane_control.setSegmentStyle_(NSSegmentStyleRounded)
            for index, pane in enumerate(SETTINGS_PANES):
                pane_control.setLabel_forSegment_(self._l(
                    f"settings.pane.{pane.casefold()}"), index)
                pane_control.setWidth_forSegment_(
                    758.0 / len(SETTINGS_PANES), index)
            pane_control.setTarget_(self)
            pane_control.setAction_("settingsPaneChanged:")
            _accessible(
                pane_control,
                self._l("settings.accessibility.category.label"),
                self._l("settings.accessibility.category.help"))
            page.addSubview_(pane_control)

            content_frame = NSMakeRect(0, 0, 758, 306)
            panes = {name: NSView.alloc().initWithFrame_(content_frame)
                     for name in SETTINGS_PANES}
            for pane in panes.values():
                page.addSubview_(pane)

            personalize = panes["Personalize"]
            face_card = _card(NSMakeRect(0, 264, 758, 40))
            picker = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(18, 4, 720, 32))
            picker.setSegmentCount_(len(FACES))
            picker.setSegmentStyle_(NSSegmentStyleRounded)
            # Ten faces no longer fit a labelled row inside the 720pt picker,
            # so the segments are emoji-forward and share the width evenly.
            # Each segment's animal name stays available through the tooltip
            # and the menu-bar "Choose Face" submenu.
            seg_width = 720.0 / len(FACES)
            for index, face in enumerate(FACES):
                picker.setLabel_forSegment_(FACE_EMOJI[face], index)
                picker.setToolTip_forSegment_(
                    self._l(f"settings.face.{face}"), index)
                picker.setWidth_forSegment_(seg_width, index)
            picker.setTarget_(self)
            picker.setAction_("faceChanged:")
            _accessible(
                picker,
                self._l("settings.accessibility.face.label"),
                self._l("settings.accessibility.face.help"))
            face_card.addSubview_(picker)
            personalize.addSubview_(face_card)

            personalize_key_views: list[Any] = [picker]
            rows = (
                ("tones", "settings.personalize.tones", "editTone:"),
                ("snippets", "settings.personalize.snippets", "editSnippets:"),
                ("vocabulary", "settings.personalize.vocabulary", "editVocabulary:"),
                ("corrections", "settings.personalize.corrections", "reviewCorrections:"),
                ("keywords", "settings.personalize.keywords", "inspectKeywords:"),
                ("modes", "settings.personalize.modes", "viewModes:"),
            )
            for index, (key, title_key, selector) in enumerate(rows):
                y = 220 - index * 44
                card = _card(NSMakeRect(0, y, 758, 44))
                card.addSubview_(_label(
                    self._l(title_key), NSMakeRect(18, 22, 260, 18),
                    size=13, weight="bold"))
                detail = _label("", NSMakeRect(18, 4, 550, 17),
                                size=10, color=_SECONDARY)
                action_key = (
                    "settings.action.review" if key == "corrections" else
                    "settings.action.inspect" if key == "keywords" else
                    "settings.action.view" if key == "modes" else
                    "settings.action.edit")
                help_key = (
                    "settings.accessibility.forget.help"
                    if key in {"corrections", "keywords"}
                    else "settings.accessibility.modes.help"
                    if key == "modes"
                    else "settings.accessibility.edit.help")
                button = _button(
                    self._l(action_key), NSMakeRect(645, 6, 94, 32),
                    self, selector,
                    help_text=self._l(
                        help_key,
                        setting=self._l(title_key).casefold()))
                card.addSubview_(detail)
                card.addSubview_(button)
                personalize.addSubview_(card)
                personalize_key_views.append(button)
                self.dynamic[f"settings_{key}_detail"] = detail
                self.dynamic[f"settings_{key}_button"] = button

            privacy = panes["Privacy"]
            voice_card = _card(NSMakeRect(0, 232, 758, 58))
            voice_card.addSubview_(_label(
                self._l("settings.privacy.voice_objects"),
                NSMakeRect(18, 32, 250, 18), size=12, weight="bold"))
            voice_object_status = _label(
                "", NSMakeRect(18, 10, 320, 18),
                size=10, color=_SECONDARY)
            voice_objects = NSButton.alloc().initWithFrame_(
                NSMakeRect(505, 14, 100, 30))
            voice_objects.setButtonType_(3)
            voice_objects.setTitle_(self._l("settings.state.enabled"))
            voice_objects.setTarget_(self)
            voice_objects.setAction_("voiceObjectCommandsChanged:")
            _accessible(
                voice_objects,
                self._l("settings.accessibility.voice_objects.label"),
                self._l("settings.accessibility.voice_objects.help"))
            voice_card.addSubview_(voice_object_status)
            voice_card.addSubview_(voice_objects)
            inspect_voice_objects = _button(
                self._l("settings.privacy.voice_objects.inspect"),
                NSMakeRect(620, 14, 92, 30), self, "inspectVoiceObjects:",
                help_text=self._l(
                    "settings.privacy.voice_objects.inspect.help"))
            _accessible(
                inspect_voice_objects,
                self._l("settings.accessibility.voice_objects.inspector"),
                self._l("settings.privacy.voice_objects.inspect.help"))
            voice_card.addSubview_(inspect_voice_objects)
            privacy.addSubview_(voice_card)

            flight_card = _card(NSMakeRect(0, 152, 758, 58))
            flight_card.addSubview_(_label(
                self._l("settings.privacy.flight"),
                NSMakeRect(18, 32, 260, 18), size=12, weight="bold"))
            flight_card.addSubview_(_label(
                self._l("settings.privacy.flight.detail"),
                NSMakeRect(18, 10, 560, 18), size=10, color=_SECONDARY))
            flight = NSButton.alloc().initWithFrame_(
                NSMakeRect(625, 14, 110, 30))
            flight.setButtonType_(3)
            flight.setTitle_(self._l("settings.state.enabled"))
            flight.setTarget_(self)
            flight.setAction_("flightChanged:")
            _accessible(
                flight,
                self._l("settings.accessibility.flight.label"),
                self._l("settings.accessibility.flight.help"))
            flight_card.addSubview_(flight)
            privacy.addSubview_(flight_card)

            acoustic_card = _card(NSMakeRect(0, 72, 758, 58))
            acoustic_card.addSubview_(_label(
                self._l("settings.privacy.acoustic"),
                NSMakeRect(18, 32, 280, 18), size=12, weight="bold"))
            acoustic_card.addSubview_(_label(
                self._l("settings.privacy.acoustic.detail"),
                NSMakeRect(18, 10, 560, 18), size=10, color=_SECONDARY))
            acoustic = NSButton.alloc().initWithFrame_(
                NSMakeRect(625, 14, 110, 30))
            acoustic.setButtonType_(3)
            acoustic.setTitle_(self._l("settings.state.enabled"))
            acoustic.setTarget_(self)
            acoustic.setAction_("acousticTimeMachineChanged:")
            _accessible(
                acoustic,
                self._l("settings.accessibility.acoustic.label"),
                self._l("settings.accessibility.acoustic.help"))
            acoustic_card.addSubview_(acoustic)
            privacy.addSubview_(acoustic_card)

            self.dynamic.update(
                settings_pane_control=pane_control,
                settings_panes=panes,
                settings_key_views={
                    "Personalize": tuple(personalize_key_views),
                    "Privacy": (
                        voice_objects, inspect_voice_objects, flight,
                        acoustic),
                },
                face_picker=picker,
                flight_toggle=flight,
                acoustic_time_machine_toggle=acoustic,
                voice_object_commands_toggle=voice_objects,
                voice_object_commands_status=voice_object_status,
                voice_object_inspect_button=inspect_voice_objects,
            )

        def _build_advanced(self, page: Any) -> None:
            page.addSubview_(_label(self._l("nav.advanced"),
                                    NSMakeRect(4, 372, 200, 26),
                                    size=18, weight="bold"))
            # ⌘D lands here: the shortcut that used to live on the parked
            # Privacy "Open Diagnostics" button now belongs to this section.
            advanced_shortcut = _accessible(_button(
                self._l("nav.advanced"),
                NSMakeRect(210, 371, 100, 28), self, "openAdvanced:",
                help_text=self._l("advanced.accessibility.shortcut.help")),
                self._l("advanced.accessibility.shortcut"),
                self._l("advanced.accessibility.shortcut.help"))
            advanced_shortcut.setKeyEquivalent_("d")
            advanced_shortcut.setKeyEquivalentModifierMask_(
                NSEventModifierFlagCommand)
            page.addSubview_(advanced_shortcut)
            relisten = NSButton.alloc().initWithFrame_(
                NSMakeRect(552, 370, 206, 28))
            relisten.setButtonType_(3)
            relisten.setTitle_(self._l("models.relisten.label"))
            relisten.setTarget_(self)
            relisten.setAction_("selectiveRelistenChanged:")
            relisten.setToolTip_(self._l("models.relisten.help"))
            _accessible(
                relisten,
                self._l("models.accessibility.relisten"),
                self._l("models.relisten.help"))
            page.addSubview_(relisten)
            relisten_status = _label(
                self._l("models.relisten.status.evidence-required"),
                NSMakeRect(557, 350, 196, 16), size=10,
                color=_SECONDARY, alignment=1,
                accessibility_label=self._l(
                    "models.accessibility.relisten"))
            page.addSubview_(relisten_status)
            advisory = _label(
                self._l("models.wallet.unavailable"),
                NSMakeRect(5, 350, 540, 16), size=9, color=_SECONDARY,
                accessibility_label=self._l(
                    "models.accessibility.wallet"))
            page.addSubview_(advisory)
            rows = []
            for index in range(4):
                row = _card(NSMakeRect(0, 306 - index * 36, 758, 32))
                name = _label(self._l("models.waiting"),
                              NSMakeRect(16, 7, 300, 18),
                              size=12, weight="medium")
                detail = _label("", NSMakeRect(322, 9, 282, 15),
                                size=9, color=_SECONDARY)
                status = _label(self._l("models.unknown"),
                                NSMakeRect(612, 7, 130, 18),
                                size=11, weight="medium", color=_ACCENT)
                row.addSubview_(name)
                row.addSubview_(detail)
                row.addSubview_(status)
                page.addSubview_(row)
                rows.append((row, name, detail, status))
            model_guidance = _label(
                self._l("models.guidance"),
                NSMakeRect(5, 178, 740, 16), size=10, color=_SECONDARY)
            page.addSubview_(model_guidance)
            card = _card(NSMakeRect(0, 102, 758, 68))
            keys = (("diagnostics.service", "diag_service"),
                    ("diagnostics.microphone", "diag_microphone"),
                    ("diagnostics.accessibility", "diag_accessibility"),
                    ("diagnostics.regression", "diag_regression"),
                    ("diagnostics.motion", "diag_motion"),
                    ("diagnostics.build", "diag_version"))
            for index, (heading_key, key) in enumerate(keys):
                x = 16 + (index % 2) * 380
                y = 44 - (index // 2) * 20
                card.addSubview_(_label(
                    self._l(heading_key), NSMakeRect(x, y, 118, 17),
                    size=10, color=_SECONDARY))
                value = _label(
                    self._l("diagnostics.unknown"),
                    NSMakeRect(x + 122, y, 236, 17),
                    size=11, weight="medium")
                card.addSubview_(value)
                self.dynamic[key] = value
            page.addSubview_(card)
            open_log = _button(self._l("diagnostics.action.log"),
                               NSMakeRect(0, 62, 112, 34),
                               self, "openLog:")
            copy_support_snapshot = _button(
                self._l("diagnostics.action.copy_support"),
                NSMakeRect(120, 62, 176, 34), self, "copySupportSnapshot:",
                help_text=self._l("diagnostics.action.copy_support.help"))
            verify = _button(self._l("diagnostics.action.verify"),
                             NSMakeRect(304, 62, 148, 34),
                             self, "verify:")
            verify.setKeyEquivalent_("r")
            verify.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
            open_system_settings = _accessible(_button(
                self._l("diagnostics.action.open_system_settings"),
                NSMakeRect(0, 24, 176, 32), self, "openSystemSettings:",
                help_text=self._l(
                    "diagnostics.action.open_system_settings.help")),
                self._l("diagnostics.accessibility.open_system_settings"),
                self._l("diagnostics.action.open_system_settings.help"))
            export_support_bundle = _button(
                self._l("diagnostics.action.export_support"),
                NSMakeRect(184, 24, 184, 32), self, "exportSupportBundle:",
                help_text=self._l("diagnostics.action.export_support.help"))
            license_notices = _button(
                self._l("diagnostics.action.licenses"),
                NSMakeRect(460, 62, 138, 34),
                self, "openLicense:")
            source = _button(self._l("diagnostics.action.source"),
                             NSMakeRect(606, 62, 132, 34),
                             self, "openSource:")
            progress = NSProgressIndicator.alloc().initWithFrame_(
                NSMakeRect(382, 31, 18, 18))
            progress.setStyle_(1)
            progress.setDisplayedWhenStopped_(False)
            verification = _label(
                self._l("diagnostics.verification.not_run"),
                NSMakeRect(406, 30, 346, 18),
                size=11, color=_SECONDARY)
            page.addSubview_(open_log)
            page.addSubview_(copy_support_snapshot)
            page.addSubview_(verify)
            page.addSubview_(open_system_settings)
            page.addSubview_(export_support_bundle)
            page.addSubview_(license_notices)
            page.addSubview_(source)
            page.addSubview_(progress)
            page.addSubview_(verification)
            guidance = _label(
                self._l("diagnostics.ready"), NSMakeRect(382, 4, 370, 14),
                size=9, color=_SECONDARY)
            page.addSubview_(guidance)
            page.addSubview_(_label(
                self._l("diagnostics.license"),
                NSMakeRect(5, 4, 370, 14), size=9, color=_SECONDARY))
            self.dynamic.update(
                model_rows=rows,
                model_wallet_advisory=advisory,
                model_guidance=model_guidance,
                selective_relisten_toggle=relisten,
                selective_relisten_status=relisten_status,
                advanced_button=advanced_shortcut,
                open_system_settings_button=open_system_settings,
                open_log_button=open_log,
                copy_support_snapshot_button=copy_support_snapshot,
                export_support_bundle_button=export_support_bundle,
                verify_button=verify,
                license_button=license_notices,
                source_button=source,
                verify_progress=progress,
                verification=verification,
                diag_guidance=guidance,
            )

        def show(self) -> None:
            self.view_model.refresh()
            self.render()
            self.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            if self._onboarding_presentation is not None:
                self._animate_onboarding_face(
                    self._onboarding_presentation)
            if self.timer is None:
                self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    2.0, self, "refreshTimer:", None, True)

        @objc.python_method
        def show_voice_inbox(self) -> None:
            """Open the existing metadata-only Voice Inbox inspector."""
            self.show()
            self.view_model.select_section("Settings")
            self.view_model.select_settings_pane("Privacy")
            self.render()
            self.inspectVoiceObjects_(None)

        @objc.python_method
        def show_results(self) -> None:
            """Open Home and, with a result available, its evidence reveal."""
            self.show()
            self.view_model.select_section("Home")
            self.render()
            if self.view_model.state.last_result.available:
                self.inspectResultEvidence_(None)

        @objc.python_method
        def show_outbox(self) -> None:
            """Route to existing recovery controls without acting on a draft."""
            self.show()
            self.view_model.select_section("Home")
            self.render()

        @objc.python_method
        def _configure_key_view_loop(self, state: GUIState) -> int:
            """Make Tab order explicit while leaving arrows to native controls."""

            controls = list(self.key_views_by_section[state.section])
            if state.section == "Settings":
                controls.extend(
                    self.dynamic["settings_key_views"][state.settings_pane])

            def visible_and_enabled(control: Any) -> bool:
                if hasattr(control, "isEnabled") and not bool(
                        control.isEnabled()):
                    return False
                view = control
                while view is not None:
                    if bool(view.isHidden()):
                        return False
                    view = view.superview()
                return True

            chain = [self.section_control]
            chain.extend(
                control for control in controls
                if visible_and_enabled(control))
            for current, following in zip(chain, chain[1:] + chain[:1]):
                current.setNextKeyView_(following)
            return len(chain)

        def render(self) -> None:
            state = self.view_model.state
            self._apply_window_theme(state)
            for section, page in self.pages.items():
                page.setHidden_(section != state.section)
            selected = SECTIONS.index(state.section)
            self.section_control.setSelectedSegment_(selected)
            face_name = self._l(f"settings.face.{state.face}")
            self.dynamic["window_face"].setImage_(self._face_image(
                state.face, talk=state.status_phase == "recording"))
            sync_accessibility(
                self.dynamic["window_face"], state.status_title,
                label=face_name)
            self._animate_section_change(state)

            phase_keys = {
                "ready": "overview.phase.ready",
                "recording": "overview.phase.recording",
                "processing": "overview.phase.processing",
                "recovery": "overview.phase.recovery",
                "degraded": "overview.phase.degraded",
                "paused": "overview.phase.paused",
                "starting": "overview.phase.starting",
            }
            self.dynamic["overview_phase"].setStringValue_(
                self._l(phase_keys[state.status_phase])
                if state.status_phase in phase_keys
                else state.status_phase.upper())
            self.dynamic["overview_status"].setStringValue_(state.status_title)
            self.dynamic["overview_detail"].setStringValue_(state.status_detail)
            self.dynamic["overview_engine"].setStringValue_(
                self._l(
                    "overview.engine.active", engine=state.active_engine))
            outbox = (
                self._l(
                    "overview.outbox.pending", count=state.outbox_count,
                    summary=state.outbox_summary)
                if state.outbox_count else self._l("overview.outbox.empty"))
            self.dynamic["overview_outbox"].setStringValue_(outbox)
            for key, label_key in (
                ("overview_phase", "overview.accessibility.phase"),
                ("overview_status", "overview.accessibility.status"),
                ("overview_detail", "overview.accessibility.detail"),
                ("overview_engine", "overview.accessibility.engine"),
                ("overview_outbox", "overview.accessibility.outbox"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=self._l(label_key),
                )
            self.dynamic["copy_outbox_button"].setHidden_(
                state.outbox_count == 0)
            self.dynamic["review_issue_button"].setHidden_(
                not state.degraded_issues and (
                    state.onboarding_complete or state.onboarding_acknowledged))
            pause_title = self._l(
                "overview.action.resume" if state.paused
                else "overview.action.pause")
            self.dynamic["pause_button"].setTitle_(pause_title)
            sync_accessibility(
                self.dynamic["pause_button"],
                self._l(
                    "overview.action.pause.state.paused" if state.paused
                    else "overview.action.pause.state.active"),
                label=self._l(
                    "overview.action.pause.label", action=pause_title),
            )
            if state.last_latency_ms is None:
                last = self._l("overview.metric.last.empty")
            else:
                suffix = ""
                if state.last_word_count is not None:
                    words_key = (
                        "overview.metric.last.words.one"
                        if state.last_word_count == 1
                        else "overview.metric.last.words.many")
                    suffix = self._l(
                        words_key, count=state.last_word_count)
                last = self._l(
                    "overview.metric.last.value",
                    seconds=f"{state.last_latency_ms / 1000:.2f}",
                    words=suffix)
            self.dynamic["overview_last"].setStringValue_(last)
            self.dynamic["overview_words"].setStringValue_(
                self._l(
                    "overview.metric.words.value",
                    count=f"{state.words_today:,}"))
            self.dynamic["overview_saved"].setStringValue_(
                self._l(
                    "overview.metric.saved.value",
                    minutes=f"{state.minutes_saved:.0f}"))
            for key, label_key in (
                ("overview_last", "overview.accessibility.last"),
                ("overview_words", "overview.accessibility.words"),
                ("overview_saved", "overview.accessibility.saved"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=self._l(label_key),
                )

            presentation = onboarding_presentation(
                state.onboarding_steps,
                acknowledged=state.onboarding_acknowledged,
                locale=self.view_model.locale,
            )
            self._onboarding_presentation = presentation
            self.dynamic["onboarding_card"].setHidden_(
                not presentation.visible)
            self.dynamic["overview_hero"].setHidden_(presentation.visible)
            self.dynamic["home_result_card"].setHidden_(presentation.visible)
            for card in self.dynamic["overview_metric_cards"]:
                card.setHidden_(presentation.visible)
            for control, step in zip(
                    self.dynamic["onboarding_steps"], state.onboarding_steps):
                step_label = self._l(f"onboarding.step.{step.key}")
                summary = self._l(
                    "onboarding.step.summary",
                    step=step_label,
                    status=step.status)
                control.setStringValue_(summary)
                sync_accessibility(
                    control,
                    summary,
                    label=self._l(
                        "overview.accessibility.onboarding.step",
                        step=step_label,
                        status=step.status),
                )
            self.dynamic["onboarding_progress"].setStringValue_(
                presentation.progress)
            self.dynamic["onboarding_title"].setStringValue_(
                presentation.title)
            self.dynamic["onboarding_detail"].setStringValue_(
                presentation.detail)
            self.dynamic["onboarding_status"].setStringValue_(
                presentation.status)
            self.dynamic["onboarding_action"].setTitle_(
                presentation.action_title)
            try:
                self.dynamic["onboarding_action"].setAccessibilityHelp_(
                    presentation.action_help)
            except Exception:
                pass
            self.dynamic["onboarding_face"].setImage_(
                self._face_image(state.face, talk=presentation.complete))
            sync_accessibility(
                self.dynamic["onboarding_face"], face_name,
                label=self._l(
                    "overview.accessibility.onboarding.face",
                    face=face_name),
            )
            for key, label_key in (
                ("onboarding_progress",
                 "overview.accessibility.onboarding.progress"),
                ("onboarding_title",
                 "overview.accessibility.onboarding.title"),
                ("onboarding_detail",
                 "overview.accessibility.onboarding.detail"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=self._l(label_key),
                )
            sync_accessibility(
                self.dynamic["onboarding_status"], presentation.status,
                label=presentation.status,
            )
            sync_accessibility(
                self.dynamic["onboarding_action"], presentation.status,
                label=presentation.action_title,
            )
            self._apply_onboarding_theme(state, presentation)
            self._animate_onboarding_face(presentation)

            result = state.last_result
            self.dynamic["result_inspect_button"].setEnabled_(result.available)
            if result.available:
                self.dynamic["result_summary"].setStringValue_(result.summary)
                self.dynamic["result_engine"].setStringValue_(
                    self._l("results.engine.session", engine=result.engine))
            else:
                # The friendly empty copy moves onto the compact card now
                # that the playful full-page empty state is gone.
                self.dynamic["result_summary"].setStringValue_(
                    self._l("results.empty.title"))
                self.dynamic["result_engine"].setStringValue_(
                    self._l("results.empty.detail"))
            self.dynamic["result_mode"].setStringValue_(result.mode)
            if not result.acoustic_replay_enabled:
                replay_copy = self._l("results.audio.off")
            elif result.retained_span_count == 0:
                replay_copy = self._l("results.audio.empty")
            elif result.retained_span_count == 1:
                replay_copy = self._l("results.audio.available.one")
            else:
                replay_copy = self._l(
                    "results.audio.available.many",
                    count=result.retained_span_count)
            self.dynamic["result_audio"].setStringValue_(replay_copy)
            has_replay = (
                result.acoustic_replay_enabled
                and result.retained_span_count > 0)
            self.dynamic["result_play_audio_button"].setEnabled_(has_replay)
            self.dynamic["result_clear_audio_button"].setEnabled_(has_replay)
            for key, label_key in (
                ("result_summary", "results.accessibility.summary"),
                ("result_engine", "results.accessibility.engine"),
                ("result_mode", "results.accessibility.mode"),
                ("result_audio", "results.accessibility.audio"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=self._l(label_key),
                )
            self.dynamic["settings_pane_control"].setSelectedSegment_(
                SETTINGS_PANES.index(state.settings_pane))
            for pane, view in self.dynamic["settings_panes"].items():
                view.setHidden_(pane != state.settings_pane)
            settings = state.settings
            setting_summaries = {
                "tones": self._l(
                    "settings.personalize.tones.detail",
                    count=len(settings.app_tones)),
                "snippets": self._l(
                    "settings.personalize.snippets.detail",
                    count=len(settings.snippets)),
                "vocabulary": self._l(
                    "settings.personalize.vocabulary.detail",
                    terms=len(settings.manual_vocabulary),
                    bans=len(settings.banned_vocabulary)),
                "corrections": self._l(
                    "settings.personalize.corrections.detail",
                    count=len(settings.corrections)),
                "keywords": self._l(
                    "settings.personalize.keywords.detail"),
                "modes": self._l(
                    "settings.personalize.modes.detail"),
            }
            for key, value in setting_summaries.items():
                set_accessible_text(
                    self.dynamic[f"settings_{key}_detail"], value,
                    label=self._l(
                        f"settings.accessibility.{key}_summary.label"))
            self.dynamic["settings_tones_button"].setEnabled_(
                bool(settings.app_tones))
            self.dynamic["settings_snippets_button"].setEnabled_(True)
            self.dynamic["settings_corrections_button"].setEnabled_(
                bool(settings.corrections))
            self.dynamic["face_picker"].setSelectedSegment_(FACES.index(state.face))
            sync_accessibility(
                self.dynamic["face_picker"],
                self._l(f"settings.face.{state.face}"),
                label=self._l(
                    "settings.accessibility.face.label"),
            )
            self.dynamic["flight_toggle"].setState_(
                NSControlStateValueOn if state.flight_recorder
                else NSControlStateValueOff)
            self.dynamic["flight_toggle"].setTitle_(state.flight_state)
            sync_accessibility(
                self.dynamic["flight_toggle"], state.flight_state,
                label=self._l(
                    "settings.accessibility.flight.label"),
            )
            self.dynamic["acoustic_time_machine_toggle"].setState_(
                NSControlStateValueOn if state.acoustic_time_machine
                else NSControlStateValueOff)
            sync_accessibility(
                self.dynamic["acoustic_time_machine_toggle"],
                (self._l("settings.state.enabled")
                 if state.acoustic_time_machine else self._l(
                     "results.audio.off")),
                label=self._l("settings.accessibility.acoustic.label"),
            )
            self.dynamic["voice_object_commands_toggle"].setState_(
                NSControlStateValueOn if state.voice_object_commands
                else NSControlStateValueOff)
            voice_object_status = self._l(
                "settings.privacy.voice_objects.status",
                status=state.voice_object_inbox_status,
                count=state.voice_object_inbox_count,
            )
            self.dynamic["voice_object_commands_status"].setStringValue_(
                voice_object_status)
            sync_accessibility(
                self.dynamic["voice_object_commands_toggle"],
                voice_object_status,
                label=self._l("settings.accessibility.voice_objects.label"),
            )

            for index, (row, name, detail, status_label) in enumerate(
                    self.dynamic["model_rows"]):
                if index < len(state.models):
                    model = state.models[index]
                    name.setStringValue_(model.name)
                    detail.setStringValue_(
                        " · ".join(part for part in (model.role, model.detail) if part))
                    status_label.setStringValue_(model.status)
                    sync_accessibility(
                        name, model.name,
                        label=self._l("models.accessibility.name"))
                    sync_accessibility(
                        detail, str(detail.stringValue()),
                        label=self._l(
                            "models.accessibility.detail", name=model.name))
                    sync_accessibility(
                        status_label, model.status,
                        label=self._l(
                            "models.accessibility.status", name=model.name))
                    row.setHidden_(False)
                else:
                    row.setHidden_(index > 0)
                    set_accessible_text(
                        name, self._l("models.waiting"),
                        label=self._l("models.accessibility.name"))
                    set_accessible_text(
                        detail, self._l("models.waiting.detail"),
                        label=self._l(
                            "models.accessibility.detail",
                            name=self._l("models.waiting")))
                    set_accessible_text(
                        status_label, self._l("models.unknown"),
                        label=self._l(
                            "models.accessibility.status",
                            name=self._l("models.waiting")))
            set_accessible_text(
                self.dynamic["model_wallet_advisory"],
                state.model_wallet_advisory,
                label=self._l("models.accessibility.wallet"),
            )
            relisten_status_key = state.selective_relisten_status
            if relisten_status_key not in {
                    "off", "ready", "warming", "enabled-not-ready"}:
                relisten_status_key = "evidence-required"
            relisten_status = self._l(
                f"models.relisten.status.{relisten_status_key}")
            relisten_toggle = self.dynamic["selective_relisten_toggle"]
            relisten_toggle.setState_(
                NSControlStateValueOn
                if state.selective_relisten_requested
                else NSControlStateValueOff)
            relisten_toggle.setEnabled_(
                state.selective_relisten_evidence_ready
                or state.selective_relisten_requested)
            set_accessible_text(
                self.dynamic["selective_relisten_status"],
                relisten_status,
                label=self._l("models.accessibility.relisten"),
            )
            sync_accessibility(
                relisten_toggle, relisten_status,
                label=self._l("models.accessibility.relisten"),
            )
            model_issue = next(
                (issue for issue in state.degraded_issues
                 if issue.key in {"models", "fallback"}), None)
            self.dynamic["model_guidance"].setStringValue_(
                model_issue.detail if model_issue else
                self._l("models.guidance"))
            sync_accessibility(
                self.dynamic["model_guidance"],
                str(self.dynamic["model_guidance"].stringValue()),
                label=self._l("models.accessibility.guidance"),
            )
            self.dynamic["diag_service"].setStringValue_(state.service_status)
            self.dynamic["diag_microphone"].setStringValue_(state.microphone_status)
            self.dynamic["diag_accessibility"].setStringValue_(
                state.accessibility_status)
            self.dynamic["open_system_settings_button"].setHidden_(
                not self.view_model.permission_recovery_needed())
            regression = self._l(
                "diagnostics.regression.cases", count=state.regression_cases)
            if state.regression_quarantined:
                regression += self._l(
                    "diagnostics.regression.quarantined",
                    count=state.regression_quarantined)
            self.dynamic["diag_regression"].setStringValue_(regression)
            self.dynamic["diag_motion"].setStringValue_(
                self._l("diagnostics.motion.reduced")
                if state.prefers_reduced_motion else self._l(
                    "diagnostics.motion.standard"))
            self.dynamic["diag_version"].setStringValue_(state.version)
            self.dynamic["verification"].setStringValue_(state.verification)
            first_issue = state.degraded_issues[0] if state.degraded_issues else None
            self.dynamic["diag_guidance"].setStringValue_(
                self._l(
                    "diagnostics.issue", title=first_issue.title,
                    detail=first_issue.detail)
                if first_issue else self._l("diagnostics.ready"))
            self.dynamic["notice"].setStringValue_(state.notice)
            for key, label_key in (
                ("diag_service", "diagnostics.accessibility.service"),
                ("diag_microphone", "diagnostics.accessibility.microphone"),
                ("diag_accessibility", "diagnostics.accessibility.permission"),
                ("diag_regression", "diagnostics.accessibility.regression"),
                ("diag_motion", "diagnostics.accessibility.motion"),
                ("diag_version", "diagnostics.accessibility.build"),
                ("verification", "diagnostics.accessibility.verification"),
                ("diag_guidance", "diagnostics.accessibility.guidance"),
                ("notice", "diagnostics.accessibility.notice"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=self._l(label_key),
                )
            notice_color = (
                NSColor.systemRedColor() if state.notice_level == "error"
                else NSColor.systemGreenColor()
                if state.notice_level == "success" else _SECONDARY)
            self.dynamic["notice"].setTextColor_(notice_color)
            self._configure_key_view_loop(state)

        @objc.python_method
        def _text_editor(self, frame: Any, value: str, *,
                         label: str, help_text: str) -> tuple[Any, Any]:
            scroll = NSScrollView.alloc().initWithFrame_(frame)
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(1)
            editor = NSTextView.alloc().initWithFrame_(
                NSMakeRect(0, 0, frame.size.width, frame.size.height))
            editor.setString_(value)
            _accessible(editor, label, help_text)
            scroll.setDocumentView_(editor)
            return scroll, editor

        @objc.python_method
        def _confirm(self, title: str, message: str,
                     primary: str) -> bool:
            alert = NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.addButtonWithTitle_(primary)
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            return alert.runModal() == 1000

        def settingsPaneChanged_(self, sender: Any) -> None:
            self.view_model.select_settings_pane(
                SETTINGS_PANES[sender.selectedSegment()])
            self.render()

        @objc.python_method
        def _tone_dialog_form(
                self, tones: Sequence[AppToneSetting]) -> tuple[Any, Any, Any]:
            form = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 430, 76))
            app_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 42, 430, 28), False)
            app_popup.addItemsWithTitles_([
                f"{item.name} — {item.bundle}" for item in tones])
            _accessible(
                app_popup,
                self._l("settings.dialog.tone.app.label"),
                self._l("settings.dialog.tone.app.help"))
            tone_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 4, 430, 28), False)
            tone_popup.addItemsWithTitles_([
                self._l(f"settings.tone.{tone}")
                for tone in TONE_CHOICES])
            _accessible(
                tone_popup,
                self._l("settings.dialog.tone.choice.label"),
                self._l("settings.dialog.tone.choice.help"))
            self._tone_dialog_apps = tones
            self._tone_dialog_tone_popup = tone_popup
            app_popup.setTarget_(self)
            app_popup.setAction_("toneDialogAppChanged:")
            self.toneDialogAppChanged_(app_popup)
            form.addSubview_(app_popup)
            form.addSubview_(tone_popup)
            return form, app_popup, tone_popup

        def editTone_(self, _sender: Any) -> None:
            tones = self.view_model.state.settings.app_tones
            if not tones:
                return
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l("settings.dialog.tone.title"))
            alert.setInformativeText_(self._l(
                "settings.dialog.tone.message"))
            form, app_popup, tone_popup = self._tone_dialog_form(tones)
            alert.setAccessoryView_(form)
            alert.addButtonWithTitle_(self._l("settings.action.save"))
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            if alert.runModal() == 1000:
                selected = tones[app_popup.indexOfSelectedItem()]
                self.view_model.set_app_tone(
                    selected.bundle,
                    TONE_CHOICES[tone_popup.indexOfSelectedItem()])
                self.render()
            self._tone_dialog_apps = ()
            self._tone_dialog_tone_popup = None

        def toneDialogAppChanged_(self, sender: Any) -> None:
            apps = getattr(self, "_tone_dialog_apps", ())
            tone_popup = getattr(self, "_tone_dialog_tone_popup", None)
            index = int(sender.indexOfSelectedItem())
            if tone_popup is None or not 0 <= index < len(apps):
                return
            tone_popup.selectItemAtIndex_(TONE_CHOICES.index(
                tone_for_app_index(
                    apps, index, locale=self.view_model.locale)))

        @objc.python_method
        def _edit_snippet(self, snippet: SnippetSetting | None) -> None:
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(
                "settings.dialog.snippet.edit" if snippet else
                "settings.dialog.snippet.add"))
            form = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 500, 200))
            form.addSubview_(_label(
                self._l("settings.dialog.snippet.name"),
                NSMakeRect(0, 177, 500, 18), size=11, color=_SECONDARY))
            name = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 145, 500, 28))
            name.setStringValue_(snippet.name if snippet else "")
            name.setEditable_(snippet is None)
            _accessible(
                name,
                self._l("settings.dialog.snippet.name"),
                self._l("settings.dialog.snippet.name.help"))
            form.addSubview_(name)
            form.addSubview_(_label(
                self._l("settings.dialog.snippet.value"),
                NSMakeRect(0, 120, 500, 18), size=11, color=_SECONDARY))
            scroll, editor = self._text_editor(
                NSMakeRect(0, 0, 500, 116), snippet.text if snippet else "",
                label=self._l("settings.dialog.snippet.value"),
                help_text=self._l(
                    "settings.dialog.snippet.value.help"))
            form.addSubview_(scroll)
            alert.setAccessoryView_(form)
            alert.addButtonWithTitle_(self._l("settings.action.save"))
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            if alert.runModal() == 1000:
                self.view_model.save_snippet(
                    str(name.stringValue()), str(editor.string()),
                    expected_original=(snippet.text if snippet else None))
                self.render()

        def editSnippets_(self, _sender: Any) -> None:
            snippets = self.view_model.state.settings.snippets
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l("settings.personalize.snippets"))
            chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 0, 430, 28), False)
            chooser.addItemsWithTitles_([
                item.name for item in snippets] or [self._l(
                    "settings.empty.snippets")])
            chooser.setEnabled_(bool(snippets))
            _accessible(
                chooser,
                self._l("settings.dialog.snippet.chooser.label"),
                self._l("settings.dialog.snippet.chooser.help"))
            alert.setAccessoryView_(chooser)
            alert.addButtonWithTitle_(self._l("settings.action.edit"))
            alert.addButtonWithTitle_(self._l("settings.action.add"))
            alert.addButtonWithTitle_(self._l("settings.action.delete"))
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            response = alert.runModal()
            selected = (snippets[chooser.indexOfSelectedItem()]
                        if snippets else None)
            if response == 1000 and selected is not None:
                self._edit_snippet(selected)
            elif response == 1001:
                self._edit_snippet(None)
            elif response == 1002 and selected is not None and self._confirm(
                    self._l("settings.dialog.delete.title"),
                    self._l("settings.dialog.delete.message",
                                     name=selected.name),
                    self._l("settings.action.delete")):
                self.view_model.delete_snippet(selected.name, selected.text)
                self.render()

        def editVocabulary_(self, _sender: Any) -> None:
            settings = self.view_model.state.settings
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(
                "settings.dialog.vocabulary.title"))
            alert.setInformativeText_(self._l(
                "settings.dialog.vocabulary.message"))
            form = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 220))
            form.addSubview_(_label(
                self._l("settings.dialog.vocabulary.terms"),
                NSMakeRect(0, 198, 250, 18), size=11, color=_SECONDARY))
            form.addSubview_(_label(
                self._l("settings.dialog.vocabulary.bans"),
                NSMakeRect(270, 198, 250, 18), size=11, color=_SECONDARY))
            term_scroll, term_editor = self._text_editor(
                NSMakeRect(0, 0, 250, 194),
                "\n".join(settings.manual_vocabulary),
                label=self._l("settings.dialog.vocabulary.terms"),
                help_text=self._l(
                    "settings.dialog.vocabulary.terms.help"))
            ban_scroll, ban_editor = self._text_editor(
                NSMakeRect(270, 0, 250, 194),
                "\n".join(settings.banned_vocabulary),
                label=self._l("settings.dialog.vocabulary.bans"),
                help_text=self._l(
                    "settings.dialog.vocabulary.bans.help"))
            form.addSubview_(term_scroll)
            form.addSubview_(ban_scroll)
            alert.setAccessoryView_(form)
            alert.addButtonWithTitle_(self._l("settings.action.save"))
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            if alert.runModal() == 1000:
                self.view_model.save_vocabulary(
                    str(term_editor.string()).splitlines(),
                    str(ban_editor.string()).splitlines())
                self.render()

        def reviewCorrections_(self, _sender: Any) -> None:
            corrections = self.view_model.state.settings.corrections
            if not corrections:
                return
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(
                "settings.personalize.corrections"))
            alert.setInformativeText_(self._l(
                "settings.dialog.correction.chooser.message"))
            chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 0, 500, 28), False)
            chooser.addItemsWithTitles_([
                f"{item.source} → {item.target} · {item.count}×"
                for item in corrections])
            _accessible(
                chooser,
                self._l(
                    "settings.dialog.correction.chooser.label"),
                self._l(
                    "settings.dialog.correction.chooser.help"))
            alert.setAccessoryView_(chooser)
            alert.addButtonWithTitle_(self._l("settings.action.review"))
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            if alert.runModal() != 1000:
                return
            selected = corrections[chooser.indexOfSelectedItem()]
            review = NSAlert.alloc().init()
            review.setMessageText_(
                f"{selected.source} → {selected.target}")
            review.setInformativeText_(correction_review_text(
                selected, locale=self.locale))
            review.addButtonWithTitle_(
                self._l("settings.action.forget_mapping"))
            review.addButtonWithTitle_(self._l("settings.action.done"))
            if review.runModal() == 1000:
                self.view_model.forget_learned(selected.kind, selected.key)
                self.render()

        def inspectKeywords_(self, _sender: Any) -> None:
            try:
                inspection = self.view_model.inspect_acoustic_keywords()
            except ValueError:
                if self._confirm(
                        self._l("settings.dialog.keywords.invalid.title"),
                        self._l("settings.dialog.keywords.invalid.message"),
                        self._l("settings.action.forget_all")):
                    self.view_model.forget_all_acoustic_keywords()
                    self.render()
                return

            candidates = inspection.candidates
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(
                "settings.dialog.keywords.title"))
            alert.setInformativeText_(self._l(
                "settings.dialog.keywords.message") if candidates else self._l(
                    "settings.dialog.keywords.empty"))
            chooser = None
            if candidates:
                chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                    NSMakeRect(0, 0, 680, 28), False)
                chooser.addItemsWithTitles_([
                    self._l(
                        "settings.dialog.keywords.row",
                        keyword=item.keyword,
                        observations=item.observations,
                        confirmations=item.confirmations,
                        status=self._l(
                            "settings.dialog.keywords.status.eligible"
                            if item.eligible else
                            "settings.dialog.keywords.status.gathering"),
                        scope=self._l(
                            "settings.dialog.keywords.scope.global"
                            if item.app_scope is None else
                            "settings.dialog.keywords.scope.private_app"),
                    )
                    for item in candidates
                ])
                _accessible(
                    chooser,
                    self._l("settings.dialog.keywords.chooser.label"),
                    self._l("settings.dialog.keywords.chooser.help"))
                alert.setAccessoryView_(chooser)
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            alert.addButtonWithTitle_(self._l("settings.action.export"))
            if candidates:
                alert.addButtonWithTitle_(self._l("settings.action.forget"))
            alert.addButtonWithTitle_(self._l("settings.action.forget_all"))
            response = alert.runModal()
            if response == 1001:
                self.view_model.export_acoustic_keywords()
            elif candidates and response == 1002:
                selected = candidates[chooser.indexOfSelectedItem()]
                scope = self._l(
                    "settings.dialog.keywords.scope.global"
                    if selected.app_scope is None else
                    "settings.dialog.keywords.scope.private_app")
                if self._confirm(
                        self._l("settings.dialog.keywords.forget.title"),
                        self._l(
                            "settings.dialog.keywords.forget.message",
                            keyword=selected.keyword,
                            scope=scope.casefold()),
                        self._l("settings.action.forget")):
                    self.view_model.forget_acoustic_keyword(selected)
            elif response == (1003 if candidates else 1002) and self._confirm(
                    self._l("settings.dialog.keywords.forget_all.title"),
                    self._l("settings.dialog.keywords.forget_all.message"),
                    self._l("settings.action.forget_all")):
                self.view_model.forget_all_acoustic_keywords()
            self.render()

        def inspectVoiceObjects_(self, _sender: Any) -> None:
            try:
                drafts = self.view_model.inspect_voice_object_drafts()
            except ValueError:
                self.render()
                return
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(
                "settings.dialog.voice_objects.title"))
            alert.setInformativeText_(self._l(
                "settings.dialog.voice_objects.message" if drafts else
                "settings.dialog.voice_objects.empty"))
            chooser = None
            if drafts:
                chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                    NSMakeRect(0, 0, 520, 28), False)
                chooser.addItemsWithTitles_([
                    self._l(
                        "settings.dialog.voice_objects.row",
                        sequence=item.sequence,
                        destination=item.destination.replace(
                            "_", " ").title(),
                        state=item.state.title(),
                    )
                    for item in drafts
                ])
                _accessible(
                    chooser,
                    self._l(
                        "settings.accessibility.voice_objects.chooser"),
                    self._l(
                        "settings.dialog.voice_objects.message"))
                alert.setAccessoryView_(chooser)
                alert.addButtonWithTitle_(self._l("settings.action.reveal"))
                alert.addButtonWithTitle_(self._l(
                    "settings.action.acknowledge"))
                alert.addButtonWithTitle_(self._l(
                    "settings.action.cancel_draft"))
                alert.addButtonWithTitle_(self._l(
                    "settings.action.purge_finished"))
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            response = alert.runModal()
            if not drafts:
                return
            selected = drafts[chooser.indexOfSelectedItem()]
            if response == 1000:
                try:
                    revealed = self.view_model.reveal_voice_object_draft(
                        selected)
                except ValueError:
                    self.render()
                    return
                detail = NSAlert.alloc().init()
                detail.setMessageText_(self._l(
                    "settings.dialog.voice_objects.reveal.title",
                    sequence=revealed.sequence,
                    destination=revealed.destination.replace(
                        "_", " ").title()))
                detail.setInformativeText_(self._l(
                    "settings.dialog.voice_objects.reveal.message"))
                scroll, editor = self._text_editor(
                    NSMakeRect(0, 0, 540, 220), revealed.content,
                    label=self._l(
                        "settings.accessibility.voice_objects.content"),
                    help_text=self._l(
                        "settings.dialog.voice_objects.reveal.message"))
                editor.setEditable_(False)
                detail.setAccessoryView_(scroll)
                detail.addButtonWithTitle_(self._l("settings.action.done"))
                can_compose = (
                    selected.destination == "email_draft"
                    and selected.state == "queued"
                    and revealed.destination == "email_draft"
                    and revealed.state == "queued")
                can_copy = (
                    selected.destination in {"task", "calendar_draft"}
                    and selected.state == "queued"
                    and revealed.destination == selected.destination
                    and revealed.state == "queued")
                if can_compose:
                    detail.addButtonWithTitle_(self._l(
                        "settings.action.compose_email"))
                elif can_copy:
                    copy_button = detail.addButtonWithTitle_(self._l(
                        "settings.action.copy_draft"))
                    _accessible(
                        copy_button,
                        self._l("settings.action.copy_draft"),
                        self._l(
                            "settings.accessibility.voice_objects.copy"))
                detail_response = detail.runModal()
                if (can_compose and detail_response == 1001
                        and self._confirm(
                            self._l(
                                "settings.dialog.voice_objects.compose.title"),
                            self._l(
                                "settings.dialog.voice_objects.compose.message",
                                sequence=selected.sequence),
                            self._l("settings.action.compose_email"))):
                    receipt = self.view_model.compose_voice_object_email(
                        selected)
                    result = NSAlert.alloc().init()
                    result.setMessageText_(self._l(
                        "settings.dialog.voice_objects.compose.result."
                        f"{receipt.state}"))
                    result.setInformativeText_(self._l(
                        "settings.dialog.voice_objects.compose.receipt",
                        state=receipt.state.replace("_", " "),
                        attempted=self._l(
                            "point_and_speak.result.yes" if
                            receipt.attempted else
                            "point_and_speak.result.no")))
                    result.addButtonWithTitle_(self._l(
                        "settings.action.done"))
                    result.runModal()
                elif (can_copy and detail_response == 1001
                        and self._confirm(
                            self._l(
                                "settings.dialog.voice_objects.copy.title",
                                destination=selected.destination.replace(
                                    "_", " ")),
                            self._l(
                                "settings.dialog.voice_objects.copy.message",
                                sequence=selected.sequence),
                            self._l("settings.action.copy_draft"))):
                    receipt = self.view_model.copy_voice_object_draft(selected)
                    result = NSAlert.alloc().init()
                    result.setMessageText_(self._l(
                        "settings.dialog.voice_objects.copy.result."
                        f"{receipt.state}"))
                    result.setInformativeText_(self._l(
                        "settings.dialog.voice_objects.copy.receipt",
                        state=receipt.state.replace("_", " "),
                        attempted=self._l(
                            "point_and_speak.result.yes" if
                            receipt.attempted else
                            "point_and_speak.result.no")))
                    result.addButtonWithTitle_(self._l(
                        "settings.action.done"))
                    if receipt.state == "copied":
                        clear_button = result.addButtonWithTitle_(self._l(
                            "settings.action.clear_clipboard"))
                        _accessible(
                            clear_button,
                            self._l("settings.action.clear_clipboard"),
                            self._l(
                                "settings.accessibility.voice_objects."
                                "clear_clipboard"))
                    result_response = result.runModal()
                    if receipt.state == "copied" and result_response == 1001:
                        clear_receipt = (
                            self.view_model
                            .clear_voice_object_draft_clipboard())
                        clear_result = NSAlert.alloc().init()
                        clear_result.setMessageText_(self._l(
                            "settings.dialog.voice_objects.clear.result."
                            f"{clear_receipt.state}"))
                        clear_result.setInformativeText_(self._l(
                            "settings.dialog.voice_objects.clear.receipt",
                            state=clear_receipt.state.replace("_", " "),
                            attempted=self._l(
                                "point_and_speak.result.yes" if
                                clear_receipt.attempted else
                                "point_and_speak.result.no")))
                        clear_result.addButtonWithTitle_(self._l(
                            "settings.action.done"))
                        clear_result.runModal()
            elif response == 1001 and self._confirm(
                    self._l("settings.dialog.voice_objects.ack.title"),
                    self._l(
                        "settings.dialog.voice_objects.ack.message",
                        sequence=selected.sequence),
                    self._l("settings.action.acknowledge")):
                self.view_model.transition_voice_object_draft(
                    selected, target="acknowledged")
            elif response == 1002 and self._confirm(
                    self._l("settings.dialog.voice_objects.cancel.title"),
                    self._l(
                        "settings.dialog.voice_objects.cancel.message",
                        sequence=selected.sequence),
                    self._l("settings.action.cancel_draft")):
                self.view_model.transition_voice_object_draft(
                    selected, target="cancelled")
            elif response == 1003 and self._confirm(
                    self._l("settings.dialog.voice_objects.purge.title"),
                    self._l("settings.dialog.voice_objects.purge.message"),
                    self._l("settings.action.purge_finished")):
                self.view_model.purge_terminal_voice_object_drafts()
            self.render()

        def sectionChanged_(self, sender: Any) -> None:
            self.view_model.select_section(SECTIONS[sender.selectedSegment()])
            self.render()

        def faceChanged_(self, sender: Any) -> None:
            self.view_model.choose_face(FACES[sender.selectedSegment()])
            self.render()

        def flightChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_flight_recorder(enabled)
            self.render()

        def acousticTimeMachineChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_acoustic_time_machine(enabled)
            self.render()

        def selectiveRelistenChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_selective_relisten(enabled)
            self.render()

        def voiceObjectCommandsChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_voice_object_commands(enabled)
            self.render()

        def viewModes_(self, _sender: Any) -> None:
            """Show the six fixed voice-mode shortcuts in a small dialog."""
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l("settings.dialog.modes.title"))
            alert.setInformativeText_(self._l("settings.dialog.modes.message"))
            listing = "\n".join(
                self._l(
                    "settings.dialog.modes.row",
                    name=self._l(f"settings.mode.{mode}.name"),
                    shortcut=self._l(f"settings.mode.{mode}.shortcut"),
                    detail=self._l(f"settings.mode.{mode}.detail"),
                )
                for mode in MODE_GUIDE
            )
            scroll, editor = self._text_editor(
                NSMakeRect(0, 0, 460, 190), listing,
                label=self._l("settings.accessibility.modes.label"),
                help_text=self._l("settings.dialog.modes.message"),
            )
            editor.setEditable_(False)
            alert.setAccessoryView_(scroll)
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            alert.runModal()

        def inspectResultEvidence_(self, _sender: Any) -> None:
            try:
                evidence = self.view_model.inspect_result_evidence()
            except ValueError:
                self.render()
                return
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l("results.inspect.title"))
            alert.setInformativeText_(self._l("results.inspect.message"))
            scroll, editor = self._text_editor(
                NSMakeRect(0, 0, 580, 320),
                result_evidence_text(
                    evidence,
                    result=self.view_model.state.last_result,
                    locale=self.view_model.locale),
                label=self._l("results.accessibility.inspect.content"),
                help_text=self._l("results.inspect.message"),
            )
            editor.setEditable_(False)
            alert.setAccessoryView_(scroll)
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            alert.runModal()

        def playRetainedSpan_(self, _sender: Any) -> None:
            self.view_model.play_retained_span()
            self.render()

        def clearRetainedSpans_(self, _sender: Any) -> None:
            self.view_model.clear_retained_spans()
            self.render()

        def pauseChanged_(self, _sender: Any) -> None:
            self.view_model.set_paused(not self.view_model.state.paused)
            self.render()

        def continueSetup_(self, _sender: Any) -> None:
            state = self.view_model.state
            if state.onboarding_complete:
                if self.defaults is not None:
                    self.defaults.setBool_forKey_(True, "onboardingComplete")
                self.view_model.acknowledge_onboarding()
            elif self.view_model.permission_recovery_needed():
                self.view_model.open_system_settings()
            else:
                self.view_model.show_next_onboarding_step()
            self.render()

        def openSystemSettings_(self, _sender: Any) -> None:
            self.view_model.open_system_settings()
            self.render()

        def openAdvanced_(self, _sender: Any) -> None:
            self.view_model.select_section("Advanced")
            self.render()

        def reviewIssue_(self, _sender: Any) -> None:
            if self.view_model.state.degraded_issues:
                self.view_model.show_issue()
            else:
                self.view_model.show_next_onboarding_step()
            self.render()

        def openLog_(self, _sender: Any) -> None:
            self.view_model.open_log()
            self.render()

        def copySupportSnapshot_(self, _sender: Any) -> None:
            self.view_model.copy_support_snapshot()
            self.render()

        def exportSupportBundle_(self, _sender: Any) -> None:
            panel = NSSavePanel.savePanel()
            panel.setNameFieldStringValue_("whisper-face-support.json")
            panel.setCanCreateDirectories_(True)
            panel.setAllowedFileTypes_(["json"])
            if panel.runModal() != 1:
                return
            url = panel.URL()
            if url is None:
                return
            try:
                write_support_bundle(
                    Path(str(url.path())),
                    support_snapshot_text(self.view_model.state))
                self.view_model.state = replace(
                    self.view_model.state,
                    notice=self._l("diagnostics.notice.support_bundle.saved"),
                    notice_level="success")
            except SupportBundleError as error:
                self.view_model.state = replace(
                    self.view_model.state,
                    notice=self._l(
                        "operation.support_bundle.export_failed", error=error),
                    notice_level="error")
            self.render()

        def openSource_(self, _sender: Any) -> None:
            self.view_model.open_source_and_license()
            self.render()

        def openLicense_(self, _sender: Any) -> None:
            self.view_model.open_local_license_notices()
            self.render()

        def copyOutbox_(self, _sender: Any) -> None:
            self.view_model.copy_latest_outbox()
            self.render()

        def verify_(self, _sender: Any) -> None:
            progress = self.dynamic["verify_progress"]
            button = self.dynamic["verify_button"]
            running = self._l("diagnostics.verification.running")
            self.view_model.set_verification(running)
            if not self.view_model.state.prefers_reduced_motion:
                progress.startAnimation_(None)
            button.setEnabled_(False)
            set_accessible_text(
                self.dynamic["verification"], running,
                label=self._l("diagnostics.accessibility.verification"))

            def run() -> None:
                result = self.view_model.verification_result()
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "verificationFinished:", result, False)

            threading.Thread(target=run, name="whisper-face-verify",
                             daemon=True).start()

        def verificationFinished_(self, result: Any) -> None:
            self.view_model.set_verification(str(result))
            self.dynamic["verify_progress"].stopAnimation_(None)
            self.dynamic["verify_button"].setEnabled_(True)
            self.render()

        def refreshTimer_(self, _timer: Any) -> None:
            self.view_model.refresh()
            self.render()

        def windowWillClose_(self, _notification: Any) -> None:
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None


def run_native_appkit_smoke() -> Mapping[str, int]:
    """Construct and exercise the native UI without touching user state.

    This deliberately does not call ``show``, run an event loop, query
    permissions, load models/audio/services, read private settings, or use
    ``NSUserDefaults``. The installer invokes it only on macOS.
    """

    if not APPKIT_AVAILABLE:
        raise RuntimeError("native AppKit smoke requires macOS")

    runtime = {
        "face": "parrot",
        "flight_recorder": False,
        "capture_state": "Ready",
        "active_engine": "Smoke ASR",
        "service_status": "Not started",
        "microphone_status": "Not requested",
        "accessibility_status": "Not requested",
        "hotkey_label": "Right Option",
        "models": [{
            "name": "Smoke ASR",
            "role": "Construction fixture",
            "status": "Not loaded",
        }],
        "last_context_firewall": {
            "mode": "shadow-only",
            "disposition": "quarantine",
            "protected_influences": 1,
            "quarantined": 1,
            "private_context": "must never reach the GUI",
        },
        "last_consequence": {
            "route": "review",
            "risk_counts": {"name": 1},
            "high_risks": 1,
            "relisten_status": "skipped",
        },
    }
    private_settings: dict[str, Any] = {
        "app_tones": [
            {"bundle": "com.example.mail", "name": "Mail", "tone": "formal"},
            {"bundle": "com.example.code", "name": "Code", "tone": "code"},
        ],
        "snippets": [{"name": "signature", "text": "Cheers"}],
        "manual_vocabulary": ["Qwen"],
        "banned_vocabulary": ["Gwen"],
        "corrections": [
            {
                "key": "gwen",
                "source": "Gwen",
                "target": "Qwen",
                "count": 2,
                "kind": "correction",
            },
            {
                "key": "gwen",
                "source": "Snippet: gwen",
                "target": "Qwen snippet",
                "count": 1,
                "kind": "snippet",
            },
        ],
    }
    private_keyword_export: dict[str, Any] = {
        "schema_version": 1,
        "kind": "whisper-face/acoustic-keyword-memory-export",
        "policy": {
            "minimum_observations": 3,
            "minimum_confirmations": 2,
            "max_entries": 256,
            "recognition_effect": "none",
        },
        "candidates": [{
            "keyword": "Qwen",
            "app_scope": None,
            "observations": 1,
            "confirmations": 1,
            "eligible": False,
            "status": "needs-2-observations-and-1-confirmations",
        }],
    }
    calls: list[tuple[Any, ...]] = []
    keyword_reads = [0]

    def set_face(face: str) -> None:
        calls.append(("face", face))
        runtime["face"] = face

    def set_flight(enabled: bool) -> None:
        calls.append(("flight", enabled))
        runtime["flight_recorder"] = enabled

    def set_tone(bundle: str, tone: str) -> None:
        calls.append(("tone", bundle, tone))
        for item in private_settings["app_tones"]:
            if item["bundle"] == bundle:
                item["tone"] = tone

    def save_snippet(name: str, expected: str | None, text: str) -> None:
        calls.append(("save_snippet", name, expected, text))
        snippets = private_settings["snippets"]
        match = next((item for item in snippets if item["name"] == name), None)
        if match is None:
            snippets.append({"name": name, "text": text})
        else:
            match["text"] = text

    def delete_snippet(name: str, expected: str) -> None:
        calls.append(("delete_snippet", name, expected))
        private_settings["snippets"] = [
            item for item in private_settings["snippets"]
            if item["name"] != name]

    def save_vocabulary(
            terms: Sequence[str], bans: Sequence[str]) -> None:
        calls.append(("vocabulary", tuple(terms), tuple(bans)))
        private_settings["manual_vocabulary"] = list(terms)
        private_settings["banned_vocabulary"] = list(bans)

    def forget(kind: str, key: str) -> None:
        calls.append((f"forget_{kind}", key))
        private_settings["corrections"] = [
            item for item in private_settings["corrections"]
            if not (item["kind"] == kind and item["key"] == key)]

    def inspect_keywords() -> Mapping[str, Any]:
        keyword_reads[0] += 1
        return dict(private_keyword_export)

    def open_system_settings() -> None:
        calls.append(("open_system_settings",))

    actions = GUIActions(
        status_snapshot=lambda: dict(runtime),
        settings_snapshot=lambda: dict(private_settings),
        set_face=set_face,
        set_flight_recorder=set_flight,
        set_app_tone=set_tone,
        save_snippet=save_snippet,
        delete_snippet=delete_snippet,
        save_vocabulary=save_vocabulary,
        forget_correction=lambda key: forget("correction", key),
        forget_snippet_edit=lambda key: forget("snippet", key),
        inspect_acoustic_keywords=inspect_keywords,
        export_acoustic_keywords=lambda:
            calls.append(("export_acoustic_keywords",)),
        forget_acoustic_keyword=lambda keyword, scope:
            calls.append(("forget_acoustic_keyword", keyword, scope)),
        forget_all_acoustic_keywords=lambda:
            calls.append(("forget_all_acoustic_keywords",)),
        open_system_settings=open_system_settings,
    )
    model = WhisperFaceViewModel(actions, locale="en-US")
    controller = None

    def require(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)

    def accessible_value(control: Any, selector: str) -> str:
        value = getattr(control, selector)()
        return "" if value is None else str(value)

    try:
        NSApplication.sharedApplication()
        controller = WhisperFaceWindowController.alloc() \
            .initForSmokeWithViewModel_(model)
        require(controller is not None, "controller")
        require(controller.defaults is None, "defaults")
        require(not bool(controller.window.isVisible()), "window visibility")
        require(tuple(controller.pages) == SECTIONS, "sections")
        require(
            controller.dynamic["window_header"] is not None,
            "themed window header")
        require(
            controller.dynamic["window_face"].image() is not None,
            "themed window face")
        require(
            str(controller.dynamic["result_summary"].stringValue()) ==
            localized_string("results.empty.title"),
            "results empty state")
        require(
            str(controller.dynamic["result_engine"].stringValue()) ==
            localized_string("results.empty.detail"),
            "results empty state detail")
        require(
            accessible_value(
                controller.dynamic["result_summary"],
                "accessibilityLabel") == localized_string(
                    "results.accessibility.summary"),
            "results empty state accessibility")
        require(
            not bool(controller.dynamic["result_inspect_button"].isEnabled()),
            "results inspect disabled without a result")
        require(
            bool(controller.dynamic["home_result_card"].isHidden()),
            "last-dictation card yields to onboarding")
        require(
            tuple(controller.dynamic["settings_panes"]) == SETTINGS_PANES,
            "settings panes")
        require(model.locale == "en", "locale propagation")
        require(
            model.state.onboarding_steps[0].key == "permissions",
            "first-run permissions")
        require(
            str(controller.dynamic["onboarding_action"].keyEquivalent()) ==
            "\r", "onboarding key equivalent")
        require(
            accessible_value(
                controller.dynamic["onboarding_card"],
                "accessibilityLabel") == localized_string(
                    "overview.accessibility.onboarding.steps"),
            "onboarding walkthrough group accessibility")
        initial_walkthrough = tuple(
            str(control.stringValue())
            for control in controller.dynamic["onboarding_steps"])
        require(len(initial_walkthrough) == 4, "onboarding walkthrough steps")
        require(
            initial_walkthrough[0] == localized_string(
                "onboarding.step.summary",
                step=localized_string("onboarding.step.permissions"),
                status=localized_string("onboarding.status.attention")),
            "onboarding walkthrough permissions")
        require(
            accessible_value(
                controller.dynamic["onboarding_steps"][0],
                "accessibilityLabel") == localized_string(
                    "overview.accessibility.onboarding.step",
                    step=localized_string("onboarding.step.permissions"),
                    status=localized_string("onboarding.status.attention")),
            "onboarding walkthrough accessibility")
        require(
            int(controller._configure_key_view_loop(model.state)) >= 2,
            "overview key-view loop")
        require(
            controller.section_control.nextKeyView() is not None,
            "initial next key view")

        require(
            not bool(controller.dynamic[
                "open_system_settings_button"].isHidden()),
            "permission recovery visible")
        require(
            accessible_value(
                controller.dynamic["open_system_settings_button"],
                "accessibilityLabel") == localized_string(
                    "diagnostics.accessibility.open_system_settings"),
            "permission recovery accessibility")
        controller.continueSetup_(None)
        require(("open_system_settings",) in calls, "permission recovery")
        require(model.state.section == "Home", "permission recovery route")
        runtime.update(
            service_status="Running",
            microphone_status="Ready",
            accessibility_status="Granted",
        )
        model.refresh()
        controller.render()
        require(
            bool(controller.dynamic[
                "open_system_settings_button"].isHidden()),
            "permission recovery hidden")
        require(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key == "hotkey",
            "hotkey practice")
        require(
            str(controller.dynamic["onboarding_steps"][0].stringValue()) ==
            localized_string(
                "onboarding.step.summary",
                step=localized_string("onboarding.step.permissions"),
                status=localized_string("onboarding.status.done")),
            "onboarding walkthrough evidence")
        runtime["capture_state"] = "Listening"
        model.refresh()
        require(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key == "models",
            "model readiness")
        runtime["capture_state"] = "Ready"
        runtime["models"][0]["status"] = "Running"
        model.refresh()
        require(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key == "first_dictation",
            "first dictation")
        runtime["last_word_count"] = 4
        model.refresh()
        controller.render()
        require(
            str(controller.dynamic["result_summary"].stringValue()) ==
            localized_string("results.summary.words", words=4),
            "results empty state clears after dictation")
        require(
            bool(controller.dynamic["result_inspect_button"].isEnabled()),
            "results inspect enabled after dictation")
        require(model.state.onboarding_complete, "onboarding completion")
        require(
            not model.state.onboarding_acknowledged,
            "onboarding awaits explicit acknowledgement")
        require(
            not bool(controller.dynamic["onboarding_card"].isHidden()),
            "onboarding completion visible")
        require(
            str(controller.dynamic["onboarding_title"].stringValue()) ==
            localized_string("onboarding.complete.title"),
            "onboarding completion title")
        require(
            str(controller.dynamic["onboarding_action"].title()) ==
            localized_string("onboarding.action.finish"),
            "onboarding completion action")
        controller.continueSetup_(None)
        require(
            model.state.onboarding_acknowledged,
            "onboarding explicit acknowledgement")
        require(
            bool(controller.dynamic["onboarding_card"].isHidden()),
            "onboarding hidden after acknowledgement")
        require(
            not bool(controller.dynamic["overview_hero"].isHidden()),
            "overview visible after onboarding")
        require(
            not bool(controller.dynamic["home_result_card"].isHidden()),
            "last-dictation card visible after onboarding")
        require(
            str(controller.dynamic["overview_phase"].stringValue()) ==
            localized_string("overview.phase.ready"),
            "overview phase localization")
        require(
            str(controller.dynamic["overview_engine"].stringValue()) ==
            localized_string("overview.engine.active", engine="Smoke ASR"),
            "overview engine localization")
        require(
            str(controller.dynamic["overview_outbox"].stringValue()) ==
            localized_string("overview.outbox.empty"),
            "overview outbox localization")
        require(
            str(controller.dynamic["pause_button"].title()) ==
            localized_string("overview.action.pause"),
            "overview action localization")
        require(
            accessible_value(
                controller.dynamic["overview_outbox"],
                "accessibilityLabel") == localized_string(
                    "overview.accessibility.outbox"),
            "overview outbox accessibility")
        require(
            accessible_value(
                controller.dynamic["pause_button"],
                "accessibilityHelp") == localized_string(
                    "overview.action.pause.help"),
            "overview pause help")
        require(
            model.state.last_result.context_firewall_summary ==
            localized_string("results.firewall.quarantine.one"),
            "context firewall receipt")
        require(
            "must never" not in repr(model.state.last_result),
            "context firewall privacy")
        require(
            model.state.last_result.consequence_advisory == localized_string(
                "results.consequence.review.advisory"),
            "review consequence guidance")
        # The persistent evidence/assurance cards are gone; the explicit
        # evidence reveal must carry the entire trust surface instead.
        modal_text = result_evidence_text(
            ResultEvidenceInspection(), result=model.state.last_result)
        for expected, label in (
            (localized_string("results.consequence.review.advisory"),
             "review consequence guidance in evidence reveal"),
            (model.state.last_result.consequence_summary,
             "consequence receipt in evidence reveal"),
            (localized_string("results.firewall.quarantine.one"),
             "context firewall receipt in evidence reveal"),
            (localized_string(
                "results.context.summary",
                influence=model.state.last_result.context_influence),
             "context influence in evidence reveal"),
            (localized_string("results.evidence.stable"),
             "stable prefix in evidence reveal"),
            (localized_string("results.evidence.anchors"),
             "protected anchors in evidence reveal"),
            (localized_string("results.evidence.decisions"),
             "compiler decisions in evidence reveal"),
            (localized_string("results.evidence.alternatives"),
             "alternatives count in evidence reveal"),
            (localized_string("results.evidence.cleanup"),
             "cleanup kinds in evidence reveal"),
            (localized_string("results.evidence.proof"),
             "proof review in evidence reveal"),
        ):
            require(expected in modal_text, label)
        require("must never" not in modal_text, "evidence reveal privacy")
        require(
            str(controller.dynamic["result_inspect_button"].action()) ==
            "inspectResultEvidence:",
            "latest-result evidence action")
        require(
            accessible_value(
                controller.dynamic["result_inspect_button"],
                "accessibilityHelp") == localized_string(
                    "results.inspect.action.help"),
            "latest-result evidence accessibility")
        require(
            str(controller.dynamic["result_play_audio_button"].action()) ==
            "playRetainedSpan:",
            "retained span play action")
        require(
            str(controller.dynamic["result_clear_audio_button"].action()) ==
            "clearRetainedSpans:",
            "retained span clear action")
        require(
            not bool(controller.dynamic[
                "result_play_audio_button"].isEnabled()),
            "retained span play disabled without opt-in")

        for index, section in enumerate(SECTIONS):
            controller.section_control.setSelectedSegment_(index)
            controller.sectionChanged_(controller.section_control)
            visible = tuple(
                name for name, page in controller.pages.items()
                if not bool(page.isHidden()))
            require(model.state.section == section, f"section {section}")
            require(visible == (section,), f"render {section}")

        controller.section_control.setSelectedSegment_(SECTIONS.index("Settings"))
        controller.sectionChanged_(controller.section_control)
        pane_control = controller.dynamic["settings_pane_control"]
        for index, pane in enumerate(SETTINGS_PANES):
            pane_control.setSelectedSegment_(index)
            controller.settingsPaneChanged_(pane_control)
            visible = tuple(
                name for name, view in
                controller.dynamic["settings_panes"].items()
                if not bool(view.isHidden()))
            require(model.state.settings_pane == pane, f"pane {pane}")
            require(visible == (pane,), f"render pane {pane}")
        require(
            int(controller._configure_key_view_loop(model.state)) >= 2,
            "settings key-view loop")
        require(
            controller.section_control.nextKeyView() == pane_control,
            "settings Tab order")
        require(
            str(controller.dynamic["settings_keywords_button"].action()) ==
            "inspectKeywords:", "keyword inspection surface")
        require(
            str(controller.dynamic["settings_modes_button"].action()) ==
            "viewModes:", "voice modes surface")
        require(
            str(controller.dynamic["settings_modes_button"].title()) ==
            localized_string("settings.action.view"),
            "voice modes action title")
        require(
            str(controller.dynamic["settings_modes_detail"].stringValue()) ==
            localized_string("settings.personalize.modes.detail"),
            "voice modes summary")
        require(
            controller.dynamic["face_picker"] in
            controller.dynamic["settings_key_views"]["Personalize"],
            "face picker lives in Personalize")

        def ancestor_views(view: Any) -> tuple[Any, ...]:
            chain = []
            while view is not None:
                chain.append(view)
                view = view.superview()
            return tuple(chain)

        require(
            controller.dynamic["settings_panes"]["Personalize"] in
            ancestor_views(controller.dynamic["face_picker"]),
            "face picker pane placement")
        controller.openAdvanced_(None)
        require(model.state.section == "Advanced", "advanced route")
        require(
            str(controller.dynamic["advanced_button"].keyEquivalent()) ==
            "d", "advanced key equivalent")
        require(
            int(controller.dynamic[
                "advanced_button"].keyEquivalentModifierMask()) & int(
                    NSEventModifierFlagCommand),
            "advanced command modifier")
        require(
            str(controller.dynamic["advanced_button"].action()) ==
            "openAdvanced:", "advanced shortcut action")
        require(
            str(controller.dynamic["export_support_bundle_button"].action()) ==
            "exportSupportBundle:",
            "local support bundle action")
        require(
            str(controller.dynamic[
                "selective_relisten_toggle"].action()) ==
            "selectiveRelistenChanged:",
            "selective re-listen surface")
        require(
            controller.section_control.nextKeyView() ==
            controller.dynamic["advanced_button"],
            "advanced Tab order")
        require(
            str(controller.dynamic["verify_button"].keyEquivalent()) == "r",
            "verification key equivalent")
        require(
            int(controller.dynamic[
                "verify_button"].keyEquivalentModifierMask()) & int(
                    NSEventModifierFlagCommand),
            "verification command modifier")

        require(
            accessible_value(controller.section_control,
                             "accessibilityLabel") == localized_string(
                                 "settings.accessibility.sections.label"),
            "section accessibility")
        require(
            accessible_value(pane_control, "accessibilityHelp") ==
            localized_string("settings.accessibility.category.help"),
            "pane accessibility")
        require(
            accessible_value(controller.dynamic["face_picker"],
                             "accessibilityHelp") == localized_string(
                                 "settings.accessibility.face.help"),
            "face accessibility")
        require(
            accessible_value(controller.dynamic["flight_toggle"],
                             "accessibilityHelp") == localized_string(
                                 "settings.accessibility.flight.help"),
            "flight accessibility")

        form, app_popup, tone_popup = controller._tone_dialog_form(
            model.state.settings.app_tones)
        require(form is not None, "tone form")
        require(
            accessible_value(app_popup, "accessibilityLabel") ==
            localized_string("settings.dialog.tone.app.label"),
            "tone app accessibility")
        require(
            accessible_value(tone_popup, "accessibilityHelp") ==
            localized_string("settings.dialog.tone.choice.help"),
            "tone choice accessibility")
        app_popup.selectItemAtIndex_(1)
        controller.toneDialogAppChanged_(app_popup)
        require(
            int(tone_popup.indexOfSelectedItem()) ==
            TONE_CHOICES.index("code"),
            "tone synchronization")
        _scroll, editor = controller._text_editor(
            NSMakeRect(0, 0, 240, 80), "fixture",
            label=localized_string("settings.dialog.snippet.value"),
            help_text=localized_string(
                "settings.dialog.snippet.value.help"))
        require(
            accessible_value(editor, "accessibilityLabel") ==
            localized_string("settings.dialog.snippet.value"),
            "editor accessibility")

        model.set_app_tone("com.example.mail", "casual")
        model.save_snippet(
            "signature", "Kind regards", expected_original="Cheers")
        model.delete_snippet("signature", "Kind regards")
        model.save_vocabulary(["Qwen", "Parakeet"], ["Gwen"])
        model.forget_learned("correction", "gwen")
        model.forget_learned("snippet", "gwen")
        require(keyword_reads[0] == 0, "keyword inspection stays on demand")
        keyword_inspection = model.inspect_acoustic_keywords()
        require(keyword_reads[0] == 1, "explicit keyword inspection")
        require(
            keyword_inspection.candidates[0].keyword == "Qwen",
            "keyword inspection candidate")
        model.export_acoustic_keywords()
        model.forget_acoustic_keyword(keyword_inspection.candidates[0])
        model.forget_all_acoustic_keywords()
        model.choose_face("owl")
        model.set_flight_recorder(True)
        expected_calls = {
            ("tone", "com.example.mail", "casual"),
            ("save_snippet", "signature", "Cheers", "Kind regards"),
            ("delete_snippet", "signature", "Kind regards"),
            ("vocabulary", ("Qwen", "Parakeet"), ("Gwen",)),
            ("forget_correction", "gwen"),
            ("forget_snippet", "gwen"),
            ("export_acoustic_keywords",),
            ("forget_acoustic_keyword", "Qwen", None),
            ("forget_all_acoustic_keywords",),
            ("face", "owl"),
            ("flight", True),
        }
        require(expected_calls.issubset(set(calls)), "model actions")
        require(not bool(controller.window.isVisible()), "window activation")
        return {
            "sections": len(SECTIONS),
            "settings_panes": len(SETTINGS_PANES),
            "model_actions": len(expected_calls),
            "onboarding_steps": len(model.state.onboarding_steps),
            "key_equivalents": 3,
        }
    finally:
        if controller is not None:
            if controller.timer is not None:
                controller.timer.invalidate()
                controller.timer = None
            controller.window.setDelegate_(None)
            controller.window.orderOut_(None)
            controller.window.close()
            controller.pages.clear()
            controller.dynamic.clear()


class WhisperFaceGUI:
    """Retained facade suitable for ownership by the existing status bar."""

    def __init__(self, actions: GUIActions, *, locale: str = "en"):
        self.view_model = WhisperFaceViewModel(actions, locale=locale)
        self._controller: Any = None

    @property
    def available(self) -> bool:
        return APPKIT_AVAILABLE

    def show(self) -> None:
        if not APPKIT_AVAILABLE:
            raise RuntimeError("The Whisper Face window requires macOS AppKit")
        if self._controller is None:
            self._controller = WhisperFaceWindowController.alloc() \
                .initWithViewModel_(self.view_model)
        self._controller.show()

    def show_voice_inbox(self) -> None:
        """Show the native Voice Inbox entry without revealing a draft."""
        if not APPKIT_AVAILABLE:
            raise RuntimeError("The Whisper Face window requires macOS AppKit")
        if self._controller is None:
            self._controller = WhisperFaceWindowController.alloc() \
                .initWithViewModel_(self.view_model)
        self._controller.show_voice_inbox()

    def show_results(self) -> None:
        """Show the existing transcript-free Last Result inspector."""
        if not APPKIT_AVAILABLE:
            raise RuntimeError("The Whisper Face window requires macOS AppKit")
        if self._controller is None:
            self._controller = WhisperFaceWindowController.alloc() \
                .initWithViewModel_(self.view_model)
        self._controller.show_results()

    def show_outbox(self) -> None:
        """Show existing Voice Outbox recovery controls without acting."""
        if not APPKIT_AVAILABLE:
            raise RuntimeError("The Whisper Face window requires macOS AppKit")
        if self._controller is None:
            self._controller = WhisperFaceWindowController.alloc() \
                .initWithViewModel_(self.view_model)
        self._controller.show_outbox()


def create_gui(actions: GUIActions, *, locale: str = "en") -> WhisperFaceGUI:
    """Create (but do not display) the GUI facade."""

    return WhisperFaceGUI(actions, locale=locale)


__all__ = [
    "APPKIT_AVAILABLE",
    "AcousticKeywordCandidate",
    "AcousticKeywordInspection",
    "AppToneSetting",
    "CorrectionScopeSetting",
    "CorrectionSetting",
    "DROP_TARGET_MAX_PHRASE_CHARS",
    "DropTargetPreview",
    "DropTargetReceipt",
    "EmailComposeReceipt",
    "DegradedIssue",
    "DemonstrationDraftMetadata",
    "DemonstrationStepPreview",
    "FACES",
    "GUIActions",
    "GUIState",
    "ModelStatus",
    "NativeAppKitSmokeContract",
    "OnboardingPresentation",
    "OnboardingStep",
    "POINT_AND_SPEAK_MAX_PHRASE_CHARS",
    "PointAndSpeakActionReceipt",
    "PointAndSpeakActionResult",
    "PointAndSpeakPreview",
    "PointAndSpeakReceipt",
    "ProofEditInspection",
    "RESULT_EVIDENCE_STAGES",
    "ResultEvidenceInspection",
    "ResultInspection",
    "RevealedDemonstrationDraft",
    "RevealedVoiceDraft",
    "SECTIONS",
    "SETTINGS_PANES",
    "STRING_CATALOGS",
    "SUPPORTED_LOCALES",
    "SnippetSetting",
    "StageTiming",
    "UnifiedSettings",
    "VoiceDraftMetadata",
    "VoiceDraftCopyReceipt",
    "WhisperFaceGUI",
    "WhisperFaceViewModel",
    "correction_review_text",
    "create_gui",
    "localized_string",
    "native_appkit_smoke_contract",
    "onboarding_presentation",
    "normalize_snapshot",
    "normalize_acoustic_keyword_inspection",
    "normalize_point_and_speak_preview",
    "normalize_point_and_speak_action",
    "normalize_drop_target_preview",
    "normalize_email_compose_receipt",
    "normalize_result_evidence",
    "normalize_voice_draft_copy_receipt",
    "normalize_settings",
    "run_native_appkit_smoke",
    "resolve_locale",
    "result_evidence_text",
    "support_snapshot_text",
    "tone_for_app_index",
]
