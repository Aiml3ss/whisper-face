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
import threading
from typing import Any, Callable, Mapping, Sequence


APP_NAME = "Whisper Face"
DEFAULTS_SUITE = "com.whisperface.app"
SECTIONS = ("Overview", "Results", "Settings", "Models", "Diagnostics")
SETTINGS_PANES = ("Modes", "Personalize", "Privacy")
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

# Stable semantic keys are intentionally separate from AppKit. Additional
# catalogs can be added without rewriting view logic or persistence schemas.
STRING_CATALOGS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "nav.overview": "Overview",
        "nav.results": "Results",
        "nav.settings": "Settings",
        "nav.models": "Models",
        "nav.diagnostics": "Diagnostics",
        "app.subtitle": "Private, fast voice input on your Mac",
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
        "overview.notice.outbox.copied": "Latest recoverable dictation copied and dismissed",
        "overview.notice.outbox.error": "Could not copy Voice Outbox: {error}",
        "overview.notice.capture.error": "Could not change capture state: {error}",
        "overview.notice.status.error": "Status unavailable: {error}",
        "onboarding.permissions.title": "Allow Mac permissions",
        "onboarding.permissions.detail": "Microphone captures speech; Accessibility safely inserts it into the field you chose.",
        "onboarding.hotkey.title": "Practice {hotkey}",
        "onboarding.hotkey.detail": "Hold {hotkey} while speaking, then release. You can keep using the Mac normally.",
        "onboarding.models.title": "Confirm local models",
        "onboarding.models.detail": "At least one local recognition engine must be ready; fallbacks can finish warming in the background.",
        "onboarding.first_dictation.title": "Make your first dictation",
        "onboarding.first_dictation.detail": "Speak one sentence in any text field. Whisper Face will keep the result recoverable if focus changes.",
        "onboarding.status.done": "Done",
        "onboarding.status.attention": "Needs attention",
        "onboarding.status.try": "Try it now",
        "onboarding.status.warming": "Warming up",
        "onboarding.status.turn": "Your turn",
        "onboarding.progress": "FIRST-RUN SETUP · {completed} OF {total} COMPLETE",
        "onboarding.action.permissions": "Review Permissions",
        "onboarding.action.hotkey": "Show Practice",
        "onboarding.action.models": "View Models",
        "onboarding.action.first_dictation": "Show How",
        "onboarding.action.continue": "Continue Setup",
        "onboarding.action.help": "Open the next incomplete first-run setup step.",
        "onboarding.complete": "Setup is complete — Whisper Face is ready.",
        "results.title": "Last Result",
        "results.subtitle": "Inspectable evidence from this session — no transcript history.",
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
        "results.privacy": "Whisper Face exposes decision counts, not private transcript text, in this settings window.",
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
        "results.accessibility.stable": "Stable prefix words",
        "results.accessibility.anchors": "Protected anchors",
        "results.accessibility.decisions": "Compiler decisions",
        "results.accessibility.cleanup": "Cleanup edits",
        "results.accessibility.proof": "Proof review",
        "results.accessibility.alternatives": "Alternatives considered",
        "results.accessibility.context": "Context influence",
        "results.accessibility.firewall": "Context safety shadow receipt",
        "results.accessibility.consequence": "Consequence decision receipt",
        "results.accessibility.consequence_advisory": "Review guidance",
        "models.title": "Your local voice stack",
        "models.subtitle": "Fast recognition, accurate fallback, and private cleanup.",
        "models.waiting": "Waiting for model status",
        "models.unknown": "Unknown",
        "models.waiting.detail": "Open this window after startup completes",
        "models.guidance": "Models prepare locally and can finish in the background.",
        "models.accessibility.name": "Model name",
        "models.accessibility.detail": "{name} role and detail",
        "models.accessibility.status": "{name} status",
        "models.accessibility.guidance": "Model guidance",
        "diagnostics.title": "Diagnostics",
        "diagnostics.subtitle": "A quick health check when something does not feel right.",
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
        "diagnostics.action.verify": "Run Verification",
        "diagnostics.action.licenses": "License Notices",
        "diagnostics.action.source": "Exact Source",
        "diagnostics.verification.not_run": "Not run",
        "diagnostics.verification.running": "Running…",
        "diagnostics.verification.passed": "All checks passed",
        "diagnostics.verification.attention": "Checks need attention",
        "diagnostics.verification.failed": "Verification failed: {error}",
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
        "diagnostics.accessibility.guidance": "Diagnostic guidance",
        "diagnostics.accessibility.notice": "Whisper Face notice",
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
        "settings.subtitle": "Modes, personal language, appearance, and privacy in one place.",
        "settings.pane.modes": "Modes",
        "settings.pane.personalize": "Personalize",
        "settings.pane.privacy": "Privacy",
        "settings.modes.title": "Hold Right Option with a modifier to choose a mode",
        "settings.modes.footer": "Shortcuts are fixed so capture behavior stays predictable and safe.",
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
        "settings.action.edit": "Edit",
        "settings.action.inspect": "Inspect",
        "settings.action.export": "Copy Export",
        "settings.action.done": "Done",
        "settings.action.forget_all": "Forget All",
        "settings.action.add": "Add",
        "settings.action.delete": "Delete",
        "settings.action.forget": "Forget",
        "settings.action.save": "Save",
        "settings.action.cancel": "Cancel",
        "settings.action.diagnostics": "Open Diagnostics",
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
        "settings.dialog.forget.title": "Forget learned correction?",
        "settings.dialog.forget.message": "Whisper Face will stop applying “{source} → {target}”.",
        "settings.dialog.correction.chooser.label": "Learned correction",
        "settings.dialog.correction.chooser.help": "Choose a learned correction to inspect and forget.",
        "settings.dialog.keywords.title": "Pronunciation keywords",
        "settings.dialog.keywords.message": "These candidates come only from exact corrections you made. They do not affect recognition yet.",
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
        "settings.privacy.face": "Companion",
        "settings.face.parrot": "Parrot",
        "settings.face.fox": "Fox",
        "settings.face.owl": "Owl",
        "settings.face.cat": "Cat",
        "settings.face.bear": "Bear",
        "settings.state.enabled": "Enabled",
        "settings.state.local_processing": "Local processing",
        "settings.accessibility.sections.label": "Settings sections",
        "settings.accessibility.sections.help": "Use arrow keys to move between Whisper Face settings sections.",
        "settings.accessibility.category.label": "Settings category",
        "settings.accessibility.category.help": "Choose modes, personalization, or privacy settings.",
        "settings.accessibility.edit.help": "Edit {setting}.",
        "settings.accessibility.forget.help": "Inspect or forget {setting}.",
        "settings.accessibility.face.label": "Whisper Face companion",
        "settings.accessibility.face.help": "Choose the animal shown in the menu bar and listening HUD.",
        "settings.accessibility.flight.label": "Flight Recorder",
        "settings.accessibility.flight.help": "Toggle the rolling twenty second audio buffer held only in memory.",
        "settings.accessibility.privacy_summary.label": "Privacy status",
        "settings.accessibility.diagnostics.help": "Open local service, permission, model, and installation diagnostics.",
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
        "operation.log.open_failed": "Could not open log: {error}",
        "operation.support_snapshot.copy_failed": "Could not copy support snapshot: {error}",
        "operation.source.open_failed": "Could not open source and license: {error}",
        "operation.licenses.open_failed": "Could not open local license notices: {error}",
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
FACES = ("parrot", "fox", "owl", "cat", "bear")
FACE_EMOJI = {
    "parrot": "🦜",
    "fox": "🦊",
    "owl": "🦉",
    "cat": "🐱",
    "bear": "🐻",
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
            "settings.accessibility.sections.label",
            "settings.accessibility.category.label",
            "settings.accessibility.face.label",
            "settings.accessibility.flight.label",
            "settings.dialog.tone.app.label",
            "settings.dialog.tone.choice.label",
            "settings.dialog.snippet.chooser.label",
            "settings.dialog.snippet.name",
            "settings.dialog.snippet.value",
            "settings.dialog.vocabulary.terms",
            "settings.dialog.vocabulary.bans",
            "settings.dialog.correction.chooser.label",
            "settings.dialog.keywords.chooser.label",
            "results.accessibility.firewall",
            "models.accessibility.guidance",
            "diagnostics.accessibility.verification",
        ),
        onboarding_steps=(
            "permissions", "hotkey", "models", "first_dictation"),
        key_equivalents=(
            "return:continue-setup",
            "command-d:diagnostics",
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
    settings_snapshot: Callable[[], Mapping[str, Any]] = lambda: {}
    set_face: Callable[[str], None] = _noop
    set_flight_recorder: Callable[[bool], None] = _noop
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
    copy_support_snapshot: Callable[[str], None] = _noop
    open_source_and_license: Callable[[], None] = _noop
    open_local_license_notices: Callable[[], None] = _noop
    copy_latest_outbox: Callable[[], None] = _noop
    rerun_verification: Callable[[], Any] = _noop


@dataclass(frozen=True)
class ModelStatus:
    name: str
    role: str = ""
    status: str = localized_string("default.status.unknown")
    detail: str = ""


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
class CorrectionSetting:
    key: str
    source: str
    target: str
    count: int = 0
    kind: str = "correction"


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
class DegradedIssue:
    """A local recovery hint; ``error`` affects the main readiness state."""

    key: str
    title: str
    detail: str
    route: str = "Diagnostics"
    severity: str = "error"


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


@dataclass(frozen=True)
class GUIState:
    section: str = "Overview"
    capture_state: str = localized_string("default.capture.ready")
    paused: bool = False
    face: str = "parrot"
    flight_recorder: bool = False
    flight_state: str = localized_string("default.flight.off")
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
    settings_pane: str = "Modes"
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
            corrections.append(CorrectionSetting(
                key=key,
                source=original,
                target=replacement,
                count=_nonnegative_int(item.get("count")),
                kind=kind,
            ))
    return UnifiedSettings(
        app_tones=tuple(tones),
        snippets=tuple(snippets),
        manual_vocabulary=_text_items(source.get("manual_vocabulary")),
        banned_vocabulary=_text_items(source.get("banned_vocabulary")),
        corrections=tuple(corrections),
    )


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
            route="Models",
        ))
    elif models:
        unavailable = [model.name for model in models if _status_contains(
            model.status, ("failed", "missing", "unavailable"))]
        if unavailable:
            issues.append(DegradedIssue(
                "fallback", copy("issue.fallback.title"),
                copy("issue.fallback.detail", models=", ".join(unavailable)),
                route="Models", severity="warning",
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
    section: str = "Overview",
    verification: str | None = None,
    notice: str = "",
    notice_level: str = "info",
    onboarding_acknowledged: bool = False,
    settings_pane: str = "Modes",
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
    unknown_status = localized_string("default.status.unknown", locale=locale)
    service_status = _clean_text(source.get("service_status"), unknown_status)
    microphone_status = _clean_text(
        source.get("microphone_status"), unknown_status)
    accessibility_status = _clean_text(
        source.get("accessibility_status"), unknown_status)
    models = _normalize_models(source.get("models"), locale=locale)
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
        section=section if section in SECTIONS else "Overview",
        capture_state=capture_state,
        paused=paused,
        face=face,
        flight_recorder=source.get("flight_recorder") is True,
        flight_state=_clean_text(
            source.get("flight_state"), localized_string(
                "default.flight.off", locale=locale)),
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
                       else "Modes"),
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
                self.state, section="Overview",
                notice=self.localized("onboarding.complete"),
                notice_level="success")
        elif step.key == "permissions":
            self.state = replace(
                self.state, section="Diagnostics", notice=step.detail,
                notice_level="info")
        elif step.key == "models":
            self.state = replace(
                self.state, section="Models", notice=step.detail,
                notice_level="info")
        else:
            self.state = replace(
                self.state, section="Overview", notice=step.detail,
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
        NSEventModifierFlagCommand,
        NSFont,
        NSMakeRect,
        NSNoBorder,
        NSNoTitle,
        NSProgressIndicator,
        NSPopUpButton,
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
    _REVIEW = NSColor.systemOrangeColor()
    _CARD = NSColor.controlBackgroundColor()

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
               accessibility_label: str = "") -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(frame)
        if weight == "bold":
            label.setFont_(NSFont.systemFontOfSize_weight_(size, 0.6))
        elif weight == "medium":
            label.setFont_(NSFont.systemFontOfSize_weight_(size, 0.35))
        else:
            label.setFont_(NSFont.systemFontOfSize_(size))
        label.setTextColor_(color or _TEXT)
        label.setLineBreakMode_(0)
        return _accessible(label, accessibility_label or text)

    def _button(title: str, frame: Any, target: Any, action: str,
                *, help_text: str = "") -> Any:
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(target)
        button.setAction_(action)
        return _accessible(button, title, help_text)

    def _card(frame: Any) -> Any:
        box = NSBox.alloc().initWithFrame_(frame)
        box.setBoxType_(NSBoxCustom)
        box.setBorderType_(NSNoBorder)
        box.setTitlePosition_(NSNoTitle)
        box.setFillColor_(_CARD)
        box.setCornerRadius_(12.0)
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

            title = _label(APP_NAME, NSMakeRect(32, 518, 300, 30),
                           size=24, weight="bold")
            subtitle = _label(self._l("app.subtitle"),
                              NSMakeRect(33, 493, 360, 20),
                              size=12, color=_SECONDARY)
            root.addSubview_(title)
            root.addSubview_(subtitle)

            self.section_control = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(31, 447, 758, 32))
            self.section_control.setSegmentCount_(len(SECTIONS))
            self.section_control.setSegmentStyle_(NSSegmentStyleRounded)
            for index, section in enumerate(SECTIONS):
                self.section_control.setLabel_forSegment_(
                    self._l(f"nav.{section.casefold()}"), index)
                self.section_control.setWidth_forSegment_(148, index)
            self.section_control.setSelectedSegment_(0)
            self.section_control.setTarget_(self)
            self.section_control.setAction_("sectionChanged:")
            _accessible(
                self.section_control,
                self._l("settings.accessibility.sections.label"),
                self._l("settings.accessibility.sections.help"))
            root.addSubview_(self.section_control)

            page_frame = NSMakeRect(31, 25, 758, 402)
            builders = {
                "Overview": self._build_overview,
                "Results": self._build_results,
                "Settings": self._build_settings,
                "Models": self._build_models,
                "Diagnostics": self._build_diagnostics,
            }
            for section, builder in builders.items():
                page = NSView.alloc().initWithFrame_(page_frame)
                builder(page)
                page.setHidden_(section != "Overview")
                root.addSubview_(page)
                self.pages[section] = page
            notice = _label("", NSMakeRect(35, 5, 750, 18),
                            size=11, color=NSColor.systemRedColor())
            root.addSubview_(notice)
            self.dynamic["notice"] = notice
            self.key_views_by_section = {
                "Overview": (
                    self.dynamic["onboarding_action"],
                    self.dynamic["pause_button"],
                    self.dynamic["review_issue_button"],
                    self.dynamic["copy_outbox_button"],
                ),
                "Results": (),
                "Settings": (self.dynamic["settings_pane_control"],),
                "Models": (),
                "Diagnostics": (
                    self.dynamic["open_log_button"],
                    self.dynamic["copy_support_snapshot_button"],
                    self.dynamic["verify_button"],
                    self.dynamic["license_button"],
                    self.dynamic["source_button"],
                ),
            }
            self.window.setInitialFirstResponder_(self.section_control)
            self.render()

        def _build_overview(self, page: Any) -> None:
            hero = _card(NSMakeRect(0, 224, 758, 178))
            phase = _label(
                self._l("overview.phase.ready"),
                NSMakeRect(24, 137, 320, 18),
                size=11, weight="bold", color=_ACCENT)
            status = _label(
                self._l("overview.status.ready.title"),
                NSMakeRect(24, 92, 520, 42), size=32, weight="bold")
            detail = _label("", NSMakeRect(26, 67, 520, 20),
                            size=12, color=_SECONDARY)
            engine = _label("", NSMakeRect(26, 42, 500, 20),
                            size=13, color=_SECONDARY)
            outbox = _label(
                self._l("overview.outbox.empty"),
                NSMakeRect(26, 16, 500, 20),
                size=12, color=_SECONDARY)
            pause = _button(
                self._l("overview.action.pause"),
                NSMakeRect(610, 89, 116, 38), self, "pauseChanged:",
                help_text=self._l("overview.action.pause.help"))
            fix = _button(
                self._l("overview.action.review"),
                NSMakeRect(590, 49, 136, 30),
                self, "reviewIssue:",
                help_text=self._l("overview.action.review.help"))
            copy_outbox = _button(
                self._l("overview.action.copy_outbox"),
                NSMakeRect(590, 13, 136, 30),
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
            self.dynamic.update(overview_phase=phase, overview_status=status,
                                overview_detail=detail, overview_engine=engine,
                                overview_outbox=outbox,
                                pause_button=pause,
                                review_issue_button=fix,
                                copy_outbox_button=copy_outbox)

            onboarding = _card(NSMakeRect(0, 91, 758, 116))
            onboarding_progress = _label(
                self._l("overview.onboarding.initial_progress"),
                NSMakeRect(20, 82, 190, 18),
                size=10, weight="bold", color=_ACCENT)
            onboarding_title = _label(
                self._l("onboarding.permissions.title"),
                NSMakeRect(20, 51, 500, 27),
                size=17, weight="bold")
            onboarding_detail = _label(
                "", NSMakeRect(20, 22, 540, 24), size=11, color=_SECONDARY)
            onboarding_action = _button(
                self._l("onboarding.action.continue"),
                NSMakeRect(590, 40, 136, 36),
                self, "continueSetup:",
                help_text=self._l("onboarding.action.help"))
            onboarding_action.setKeyEquivalent_("\r")
            onboarding.addSubview_(onboarding_progress)
            onboarding.addSubview_(onboarding_title)
            onboarding.addSubview_(onboarding_detail)
            onboarding.addSubview_(onboarding_action)
            page.addSubview_(onboarding)
            self.dynamic.update(
                onboarding_card=onboarding,
                onboarding_progress=onboarding_progress,
                onboarding_title=onboarding_title,
                onboarding_detail=onboarding_detail,
                onboarding_action=onboarding_action,
            )

            cards = (("overview.metric.last.heading", "overview_last"),
                     ("overview.metric.words.heading", "overview_words"),
                     ("overview.metric.saved.heading", "overview_saved"))
            for index, (heading_key, key) in enumerate(cards):
                card = _card(NSMakeRect(index * 253, 0, 239, 76))
                card.addSubview_(_label(
                    self._l(heading_key), NSMakeRect(16, 49, 200, 18),
                    size=11, color=_SECONDARY))
                value = _label(
                    self._l("overview.metric.last.empty"),
                    NSMakeRect(16, 13, 205, 31),
                    size=21, weight="bold")
                card.addSubview_(value)
                page.addSubview_(card)
                self.dynamic[key] = value

        def _build_results(self, page: Any) -> None:
            page.addSubview_(_label(
                self._l("results.title"), NSMakeRect(4, 351, 500, 32),
                size=22, weight="bold"))
            page.addSubview_(_label(
                self._l("results.subtitle"),
                NSMakeRect(5, 326, 690, 20), size=13, color=_SECONDARY))

            summary_card = _card(NSMakeRect(0, 216, 758, 89))
            result_summary = _label(
                self._l("results.summary.empty"), NSMakeRect(20, 45, 430, 27),
                size=18, weight="bold")
            result_engine = _label(
                self._l("results.engine.waiting"), NSMakeRect(20, 18, 500, 20),
                size=12, color=_SECONDARY)
            result_mode = _label(
                self._l("results.mode.capture"), NSMakeRect(620, 39, 110, 22),
                size=12, weight="medium", color=_ACCENT)
            summary_card.addSubview_(result_summary)
            summary_card.addSubview_(result_engine)
            summary_card.addSubview_(result_mode)
            page.addSubview_(summary_card)

            evidence_card = _card(NSMakeRect(0, 83, 758, 125))
            evidence_keys = (
                ("results.evidence.stable", "result_stable"),
                ("results.evidence.anchors", "result_anchors"),
                ("results.evidence.decisions", "result_decisions"),
                ("results.evidence.alternatives", "result_alternatives"),
                ("results.evidence.cleanup", "result_cleanup"),
                ("results.evidence.proof", "result_proof"),
            )
            for index, (heading_key, key) in enumerate(evidence_keys):
                x = 20 + (index % 2) * 370
                y = 91 - (index // 2) * 35
                evidence_card.addSubview_(_label(
                    self._l(heading_key), NSMakeRect(x, y, 140, 18),
                    size=11, color=_SECONDARY))
                value = _label("—", NSMakeRect(x + 145, y, 190, 18),
                               size=12, weight="medium")
                evidence_card.addSubview_(value)
                self.dynamic[key] = value
            page.addSubview_(evidence_card)
            firewall = _label(
                self._l("results.firewall.unavailable"),
                NSMakeRect(5, 62, 740, 17),
                size=11, weight="medium", color=_ACCENT)
            page.addSubview_(firewall)
            context = _label(
                self._l("results.context.unreported"),
                NSMakeRect(5, 45, 740, 17),
                size=11, color=_SECONDARY)
            page.addSubview_(context)
            consequence = _label(
                "", NSMakeRect(5, 28, 740, 17), size=10, color=_SECONDARY)
            page.addSubview_(consequence)
            consequence_advisory = _label(
                "", NSMakeRect(5, 12, 740, 16), size=10, weight="medium",
                color=_REVIEW)
            consequence_advisory.setHidden_(True)
            page.addSubview_(consequence_advisory)
            page.addSubview_(_label(
                self._l("results.privacy"),
                NSMakeRect(5, 1, 740, 11), size=9, color=_SECONDARY))
            self.dynamic.update(
                result_summary=result_summary,
                result_engine=result_engine,
                result_mode=result_mode,
                result_context=context,
                result_firewall=firewall,
                result_consequence=consequence,
                result_consequence_advisory=consequence_advisory,
            )

        def _build_settings(self, page: Any) -> None:
            page.addSubview_(_label(
                self._l("settings.title"),
                NSMakeRect(4, 351, 500, 32), size=22, weight="bold"))
            page.addSubview_(_label(
                self._l("settings.subtitle"),
                NSMakeRect(5, 326, 720, 20), size=13, color=_SECONDARY))
            pane_control = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(0, 284, 758, 32))
            pane_control.setSegmentCount_(len(SETTINGS_PANES))
            pane_control.setSegmentStyle_(NSSegmentStyleRounded)
            for index, pane in enumerate(SETTINGS_PANES):
                pane_control.setLabel_forSegment_(self._l(
                    f"settings.pane.{pane.casefold()}"), index)
                pane_control.setWidth_forSegment_(250, index)
            pane_control.setTarget_(self)
            pane_control.setAction_("settingsPaneChanged:")
            _accessible(
                pane_control,
                self._l("settings.accessibility.category.label"),
                self._l("settings.accessibility.category.help"))
            page.addSubview_(pane_control)

            content_frame = NSMakeRect(0, 0, 758, 268)
            panes = {name: NSView.alloc().initWithFrame_(content_frame)
                     for name in SETTINGS_PANES}
            for pane in panes.values():
                page.addSubview_(pane)

            modes = panes["Modes"]
            modes.addSubview_(_label(
                self._l("settings.modes.title"),
                NSMakeRect(5, 238, 720, 22), size=14, weight="medium"))
            for index, mode in enumerate(MODE_GUIDE):
                column, row = index % 2, index // 2
                card = _card(NSMakeRect(column * 379, 153 - row * 66, 365, 54))
                card.addSubview_(_label(
                    self._l(f"settings.mode.{mode}.name"),
                    NSMakeRect(14, 28, 105, 18),
                    size=12, weight="bold"))
                card.addSubview_(_label(
                    self._l(f"settings.mode.{mode}.shortcut"),
                    NSMakeRect(115, 28, 235, 18),
                    size=11, weight="medium", color=_ACCENT))
                card.addSubview_(_label(
                    self._l(f"settings.mode.{mode}.detail"),
                    NSMakeRect(14, 8, 330, 17),
                    size=10, color=_SECONDARY))
                modes.addSubview_(card)
            modes.addSubview_(_label(
                self._l("settings.modes.footer"),
                NSMakeRect(5, 12, 720, 18), size=11, color=_SECONDARY))

            personalize = panes["Personalize"]
            personalize_key_views: list[Any] = []
            rows = (
                ("tones", "settings.personalize.tones", "editTone:"),
                ("snippets", "settings.personalize.snippets", "editSnippets:"),
                ("vocabulary", "settings.personalize.vocabulary", "editVocabulary:"),
                ("corrections", "settings.personalize.corrections", "forgetCorrection:"),
                ("keywords", "settings.personalize.keywords", "inspectKeywords:"),
            )
            for index, (key, title_key, selector) in enumerate(rows):
                y = 216 - index * 52
                card = _card(NSMakeRect(0, y, 758, 44))
                card.addSubview_(_label(
                    self._l(title_key), NSMakeRect(18, 22, 260, 18),
                    size=13, weight="bold"))
                detail = _label("", NSMakeRect(18, 4, 550, 17),
                                size=10, color=_SECONDARY)
                action_key = (
                    "settings.action.forget" if key == "corrections" else
                    "settings.action.inspect" if key == "keywords" else
                    "settings.action.edit")
                help_key = (
                    "settings.accessibility.forget.help"
                    if key in {"corrections", "keywords"}
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
            privacy.addSubview_(_label(
                self._l("settings.privacy.title"),
                NSMakeRect(5, 238, 500, 22), size=14, weight="medium"))
            face_card = _card(NSMakeRect(0, 137, 758, 84))
            face_card.addSubview_(_label(
                self._l("settings.privacy.face"),
                NSMakeRect(18, 53, 200, 20), size=13, weight="bold"))
            picker = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(18, 12, 720, 36))
            picker.setSegmentCount_(len(FACES))
            picker.setSegmentStyle_(NSSegmentStyleRounded)
            for index, face in enumerate(FACES):
                picker.setLabel_forSegment_(
                    f"{FACE_EMOJI[face]} "
                    f"{self._l(f'settings.face.{face}')}", index)
                picker.setWidth_forSegment_(138, index)
            picker.setTarget_(self)
            picker.setAction_("faceChanged:")
            _accessible(
                picker,
                self._l("settings.accessibility.face.label"),
                self._l("settings.accessibility.face.help"))
            face_card.addSubview_(picker)
            privacy.addSubview_(face_card)

            flight_card = _card(NSMakeRect(0, 35, 758, 84))
            flight_card.addSubview_(_label(
                self._l("settings.privacy.flight"),
                NSMakeRect(18, 50, 260, 22), size=14, weight="bold"))
            flight_card.addSubview_(_label(
                self._l("settings.privacy.flight.detail"),
                NSMakeRect(18, 24, 535, 20), size=11, color=_SECONDARY))
            flight = NSButton.alloc().initWithFrame_(NSMakeRect(625, 26, 110, 32))
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
            privacy_summary = _label(
                self._l("settings.state.local_processing"),
                NSMakeRect(5, 5, 700, 20),
                size=11, weight="medium", color=_ACCENT)
            privacy.addSubview_(privacy_summary)
            diagnostics = _button(
                self._l("settings.action.diagnostics"),
                NSMakeRect(600, 230, 138, 30), self, "openDiagnostics:",
                help_text=self._l(
                    "settings.accessibility.diagnostics.help"))
            diagnostics.setKeyEquivalent_("d")
            diagnostics.setKeyEquivalentModifierMask_(
                NSEventModifierFlagCommand)
            privacy.addSubview_(diagnostics)

            self.dynamic.update(
                settings_pane_control=pane_control,
                settings_panes=panes,
                settings_key_views={
                    "Modes": (),
                    "Personalize": tuple(personalize_key_views),
                    "Privacy": (picker, flight, diagnostics),
                },
                face_picker=picker,
                flight_toggle=flight,
                privacy_summary=privacy_summary,
                diagnostics_button=diagnostics,
            )

        def _build_models(self, page: Any) -> None:
            page.addSubview_(_label(self._l("models.title"),
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                self._l("models.subtitle"),
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            rows = []
            for index in range(3):
                row = _card(NSMakeRect(0, 220 - index * 88, 758, 72))
                name = _label(self._l("models.waiting"), NSMakeRect(20, 36, 430, 22),
                              size=14, weight="medium")
                detail = _label("", NSMakeRect(20, 13, 530, 18),
                                size=11, color=_SECONDARY)
                status = _label(self._l("models.unknown"), NSMakeRect(610, 26, 120, 20),
                                size=12, weight="medium", color=_ACCENT)
                row.addSubview_(name)
                row.addSubview_(detail)
                row.addSubview_(status)
                page.addSubview_(row)
                rows.append((row, name, detail, status))
            guidance = _label(
                self._l("models.guidance"),
                NSMakeRect(5, 25, 740, 22), size=11, color=_SECONDARY)
            page.addSubview_(guidance)
            self.dynamic.update(model_rows=rows, model_guidance=guidance)

        def _build_diagnostics(self, page: Any) -> None:
            page.addSubview_(_label(self._l("diagnostics.title"),
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                self._l("diagnostics.subtitle"),
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            card = _card(NSMakeRect(0, 137, 758, 161))
            keys = (("diagnostics.service", "diag_service"),
                    ("diagnostics.microphone", "diag_microphone"),
                    ("diagnostics.accessibility", "diag_accessibility"),
                    ("diagnostics.regression", "diag_regression"),
                    ("diagnostics.motion", "diag_motion"),
                    ("diagnostics.build", "diag_version"))
            for index, (heading_key, key) in enumerate(keys):
                y = 133 - index * 23
                card.addSubview_(_label(self._l(heading_key), NSMakeRect(20, y, 170, 19),
                                        size=12, color=_SECONDARY))
                value = _label(self._l("diagnostics.unknown"), NSMakeRect(185, y, 525, 19),
                               size=12, weight="medium")
                card.addSubview_(value)
                self.dynamic[key] = value
            page.addSubview_(card)
            open_log = _button(self._l("diagnostics.action.log"), NSMakeRect(0, 89, 120, 36),
                               self, "openLog:")
            copy_support_snapshot = _button(
                self._l("diagnostics.action.copy_support"),
                NSMakeRect(132, 89, 180, 36), self, "copySupportSnapshot:",
                help_text=self._l("diagnostics.action.copy_support.help"))
            verify = _button(self._l("diagnostics.action.verify"), NSMakeRect(324, 89, 152, 36),
                             self, "verify:")
            verify.setKeyEquivalent_("r")
            verify.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
            license_notices = _button(
                self._l("diagnostics.action.licenses"), NSMakeRect(488, 89, 138, 36),
                self, "openLicense:")
            source = _button(self._l("diagnostics.action.source"), NSMakeRect(638, 89, 120, 36),
                             self, "openSource:")
            progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(5, 59, 20, 20))
            progress.setStyle_(1)
            progress.setDisplayedWhenStopped_(False)
            verification = _label(self._l("diagnostics.verification.not_run"), NSMakeRect(32, 59, 710, 20),
                                  size=12, color=_SECONDARY)
            page.addSubview_(open_log)
            page.addSubview_(copy_support_snapshot)
            page.addSubview_(verify)
            page.addSubview_(license_notices)
            page.addSubview_(source)
            page.addSubview_(progress)
            page.addSubview_(verification)
            guidance = _label(
                self._l("diagnostics.ready"), NSMakeRect(5, 36, 740, 18),
                size=11, color=_SECONDARY)
            page.addSubview_(guidance)
            page.addSubview_(_label(
                self._l("diagnostics.license"),
                NSMakeRect(5, 12, 620, 18), size=11, color=_SECONDARY))
            self.dynamic.update(
                open_log_button=open_log,
                copy_support_snapshot_button=copy_support_snapshot,
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
            if self.timer is None:
                self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    2.0, self, "refreshTimer:", None, True)

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
            if state.onboarding_complete and not state.onboarding_acknowledged:
                if self.defaults is not None:
                    self.defaults.setBool_forKey_(True, "onboardingComplete")
                state = self.view_model.acknowledge_onboarding()
            for section, page in self.pages.items():
                page.setHidden_(section != state.section)
            selected = SECTIONS.index(state.section)
            self.section_control.setSelectedSegment_(selected)

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

            completed = sum(
                step.complete for step in state.onboarding_steps)
            next_step = next(
                (step for step in state.onboarding_steps if not step.complete),
                None,
            )
            self.dynamic["onboarding_card"].setHidden_(
                next_step is None or state.onboarding_acknowledged)
            if next_step is not None:
                self.dynamic["onboarding_progress"].setStringValue_(
                    self._l(
                        "onboarding.progress", completed=completed,
                        total=len(state.onboarding_steps)))
                self.dynamic["onboarding_title"].setStringValue_(next_step.title)
                self.dynamic["onboarding_detail"].setStringValue_(next_step.detail)
                action_title = {
                    "permissions": self._l("onboarding.action.permissions"),
                    "hotkey": self._l("onboarding.action.hotkey"),
                    "models": self._l("onboarding.action.models"),
                    "first_dictation": self._l(
                        "onboarding.action.first_dictation"),
                }.get(next_step.key, self._l("onboarding.action.continue"))
                self.dynamic["onboarding_action"].setTitle_(action_title)
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
                    self.dynamic["onboarding_action"], next_step.status,
                    label=action_title,
                )

            result = state.last_result
            self.dynamic["result_summary"].setStringValue_(result.summary)
            self.dynamic["result_engine"].setStringValue_(
                self._l("results.engine.session", engine=result.engine))
            self.dynamic["result_mode"].setStringValue_(result.mode)
            self.dynamic["result_stable"].setStringValue_(
                self._l(
                    "results.value.words", count=result.stable_prefix_words))
            self.dynamic["result_anchors"].setStringValue_(
                str(result.protected_anchor_count))
            confidence = (
                self._l(
                    "results.value.confidence",
                    confidence=f"{result.confidence:.0%}")
                if result.confidence is not None else "")
            self.dynamic["result_decisions"].setStringValue_(
                f"{result.compiler_decisions}{confidence}")
            cleanup_kinds = ", ".join(dict.fromkeys(result.cleanup_edits))
            self.dynamic["result_cleanup"].setStringValue_(
                cleanup_kinds or self._l("results.value.none_reported"))
            rejected = (
                str(result.proof_edits_rejected)
                if result.proof_edits_rejected is not None else self._l(
                    "results.value.not_reported"))
            self.dynamic["result_proof"].setStringValue_(
                self._l(
                    "results.value.proof",
                    accepted=result.proof_edits_accepted,
                    rejected=rejected))
            self.dynamic["result_alternatives"].setStringValue_(
                str(result.alternatives_considered))
            self.dynamic["result_context"].setStringValue_(
                self._l(
                    "results.context.summary",
                    influence=result.context_influence))
            self.dynamic["result_firewall"].setStringValue_(
                result.context_firewall_summary)
            self.dynamic["result_consequence"].setStringValue_(
                result.consequence_summary)
            self.dynamic["result_consequence_advisory"].setStringValue_(
                result.consequence_advisory)
            self.dynamic["result_consequence_advisory"].setHidden_(
                not bool(result.consequence_advisory))
            self.dynamic["result_consequence_advisory"].setTextColor_(
                _REVIEW if result.consequence_advisory else _SECONDARY)
            for key, label_key in (
                ("result_summary", "results.accessibility.summary"),
                ("result_engine", "results.accessibility.engine"),
                ("result_mode", "results.accessibility.mode"),
                ("result_stable", "results.accessibility.stable"),
                ("result_anchors", "results.accessibility.anchors"),
                ("result_decisions", "results.accessibility.decisions"),
                ("result_cleanup", "results.accessibility.cleanup"),
                ("result_proof", "results.accessibility.proof"),
                ("result_alternatives", "results.accessibility.alternatives"),
                ("result_context", "results.accessibility.context"),
                ("result_firewall", "results.accessibility.firewall"),
                ("result_consequence", "results.accessibility.consequence"),
                ("result_consequence_advisory",
                 "results.accessibility.consequence_advisory"),
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
            self.dynamic["privacy_summary"].setStringValue_(state.privacy_summary)
            sync_accessibility(
                self.dynamic["privacy_summary"], state.privacy_summary,
                label=self._l(
                    "settings.accessibility.privacy_summary.label"),
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
            model_issue = next(
                (issue for issue in state.degraded_issues
                 if issue.route == "Models"), None)
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

        def forgetCorrection_(self, _sender: Any) -> None:
            corrections = self.view_model.state.settings.corrections
            if not corrections:
                return
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(
                "settings.personalize.corrections"))
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
            alert.addButtonWithTitle_(self._l("settings.action.forget"))
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            if alert.runModal() != 1000:
                return
            selected = corrections[chooser.indexOfSelectedItem()]
            if self._confirm(
                    self._l("settings.dialog.forget.title"),
                    self._l("settings.dialog.forget.message",
                                     source=selected.source,
                                     target=selected.target),
                    self._l("settings.action.forget")):
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

        def pauseChanged_(self, _sender: Any) -> None:
            self.view_model.set_paused(not self.view_model.state.paused)
            self.render()

        def continueSetup_(self, _sender: Any) -> None:
            self.view_model.show_next_onboarding_step()
            self.render()

        def openDiagnostics_(self, _sender: Any) -> None:
            self.view_model.select_section("Diagnostics")
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
            int(controller._configure_key_view_loop(model.state)) >= 3,
            "overview key-view loop")
        require(
            controller.section_control.nextKeyView() is not None,
            "initial next key view")

        controller.continueSetup_(None)
        require(model.state.section == "Diagnostics", "permission route")
        runtime.update(
            service_status="Running",
            microphone_status="Ready",
            accessibility_status="Granted",
        )
        model.refresh()
        require(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key == "hotkey",
            "hotkey practice")
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
        require(model.state.onboarding_complete, "onboarding completion")
        require(model.state.onboarding_acknowledged, "onboarding acknowledgement")
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
        require(
            str(controller.dynamic["result_consequence_advisory"].stringValue())
            == localized_string("results.consequence.review.advisory"),
            "review consequence guidance rendering")
        require(
            not bool(controller.dynamic[
                "result_consequence_advisory"].isHidden()),
            "review consequence guidance visibility")

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
            str(controller.dynamic["diagnostics_button"].keyEquivalent()) ==
            "d", "diagnostics key equivalent")
        require(
            str(controller.dynamic["settings_keywords_button"].action()) ==
            "inspectKeywords:", "keyword inspection surface")
        require(
            int(controller.dynamic[
                "diagnostics_button"].keyEquivalentModifierMask()) & int(
                    NSEventModifierFlagCommand),
            "diagnostics command modifier")
        controller.openDiagnostics_(None)
        require(model.state.section == "Diagnostics", "diagnostics route")
        require(
            str(controller.dynamic["verify_button"].keyEquivalent()) == "r",
            "verification key equivalent")
        require(
            int(controller.dynamic[
                "verify_button"].keyEquivalentModifierMask()) & int(
                    NSEventModifierFlagCommand),
            "verification command modifier")
        require(
            accessible_value(
                controller.dynamic["result_firewall"],
                "accessibilityLabel") == localized_string(
                    "results.accessibility.firewall"),
            "context firewall accessibility")
        require(
            accessible_value(
                controller.dynamic["result_consequence_advisory"],
                "accessibilityLabel") == localized_string(
                    "results.accessibility.consequence_advisory"),
            "review consequence guidance accessibility")

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


def create_gui(actions: GUIActions, *, locale: str = "en") -> WhisperFaceGUI:
    """Create (but do not display) the GUI facade."""

    return WhisperFaceGUI(actions, locale=locale)


__all__ = [
    "APPKIT_AVAILABLE",
    "AcousticKeywordCandidate",
    "AcousticKeywordInspection",
    "AppToneSetting",
    "CorrectionSetting",
    "DegradedIssue",
    "FACES",
    "GUIActions",
    "GUIState",
    "ModelStatus",
    "NativeAppKitSmokeContract",
    "OnboardingStep",
    "ResultInspection",
    "SECTIONS",
    "SETTINGS_PANES",
    "STRING_CATALOGS",
    "SUPPORTED_LOCALES",
    "SnippetSetting",
    "UnifiedSettings",
    "WhisperFaceGUI",
    "WhisperFaceViewModel",
    "create_gui",
    "localized_string",
    "native_appkit_smoke_contract",
    "normalize_snapshot",
    "normalize_acoustic_keyword_inspection",
    "normalize_settings",
    "run_native_appkit_smoke",
    "resolve_locale",
    "support_snapshot_text",
    "tone_for_app_index",
]
