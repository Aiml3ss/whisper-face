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
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from support_bundle import SupportBundleError, write_support_bundle
from whisper_face_theme import (
    FACE_CHIP_COLORS,
    MOTION_SPECS,
    contrast_ratio,
    palette_for_appearance,
)


APP_NAME = "Whisper Face"
DEFAULTS_SUITE = "com.whisperface.app"
# The palette's error coral is a fill colour: as text on the light surfaces
# it reaches only 2.1-2.3:1, so light-mode error text uses this darker ink.
ERROR_TEXT_ON_LIGHT = (0.702, 0.255, 0.180)   # ≈ #B3412E
SECTIONS = ("Home", "Settings", "Advanced")
# Keys and sounds get their own pane rather than crowding Personalize: at the
# window minimum the character card plus six 44pt rows fills the pane, and
# shrinking the row rhythm or the character card to fit more
# would cost more than the extra segment does. A seventh row overflows into
# the notice band, so anything new here has to displace something.
SETTINGS_PANES = ("Personalize", "Controls", "Privacy")


@dataclass(frozen=True)
class SidebarRow:
    """One page in the window's sidebar.

    The sidebar is a projection, not a second source of truth: the view
    model keeps its sections and settings panes, and each row names the
    (section, pane) it shows. ``pane`` is None for a section that has no
    panes. ``key`` is the row's stable name for code (title-strip items,
    tests); ``label_key`` is its catalog string; ``symbol`` its SF Symbol.
    """

    key: str
    section: str
    pane: str | None
    label_key: str
    symbol: str


# In sidebar order, which is also the order of the Command-1 to Command-5
# shortcuts. The rows after Home sit under a small "Settings" group label,
# and Advanced closes that group.
SIDEBAR_ROWS: tuple[SidebarRow, ...] = (
    SidebarRow("home", "Home", None, "nav.home", "house"),
    SidebarRow("personalize", "Settings", "Personalize",
               "settings.pane.personalize", "face.smiling"),
    SidebarRow("controls", "Settings", "Controls",
               "settings.pane.controls", "keyboard"),
    SidebarRow("privacy", "Settings", "Privacy",
               "settings.pane.privacy", "hand.raised"),
    SidebarRow("advanced", "Advanced", None, "nav.advanced",
               "wrench.and.screwdriver"),
)


def sidebar_row_index(section: str, settings_pane: str) -> int:
    """The sidebar row that shows this section and settings pane.

    The settings pane only matters inside Settings: the view model keeps the
    last pane while another section is showing, and that must not move the
    selection.
    """

    for index, row in enumerate(SIDEBAR_ROWS):
        if row.section == section and row.pane in (None, settings_pane):
            return index
    raise ValueError(f"no sidebar row shows {section!r} / {settings_pane!r}")


def sidebar_row_destination(index: int) -> tuple[str, str | None]:
    """The (section, settings pane) a sidebar row opens."""

    if not 0 <= index < len(SIDEBAR_ROWS):
        raise ValueError(f"no sidebar row {index!r}")
    row = SIDEBAR_ROWS[index]
    return row.section, row.pane


# The one-word state beside the face in the sidebar, and the tone of its
# dot. Amber is left out on purpose: an amber dot reads as the system's
# microphone indicator.
SIDEBAR_STATES: Mapping[str, tuple[str, str]] = {
    "ready": ("shell.state.ready", "good"),
    "recording": ("shell.state.recording", "good"),
    "processing": ("shell.state.processing", "good"),
    "recovery": ("shell.state.recovery", "attention"),
    "degraded": ("shell.state.degraded", "attention"),
    "paused": ("shell.state.paused", "quiet"),
    "starting": ("shell.state.starting", "quiet"),
}
SIDEBAR_STATE_SETUP = ("shell.state.setup", "quiet")


def sidebar_state(status_phase: str, *, setting_up: bool) -> tuple[str, str]:
    """Catalog key and dot tone for the sidebar's state line.

    First-run setup outranks the runtime phase: until it is finished the
    honest one-word answer is "Setting up", whatever the service reports.
    """

    if setting_up:
        return SIDEBAR_STATE_SETUP
    return SIDEBAR_STATES.get(status_phase, SIDEBAR_STATES["starting"])


MODE_GUIDE = ("capture", "compose", "edit", "reply", "command", "code")
TONE_CHOICES = ("auto", "casual", "formal", "code", "verbatim", "default")
SOUND_THEMES = ("system", "whisper", "silent")
# Dictation languages, in the runtime's order. Deliberately a short measured
# list, not Whisper's full set: every one of these was round-tripped through
# large-v3-turbo before it was offered. Adding one is a data change here plus
# a row in the runtime's LANGUAGES table.
LANGUAGES = ("en", "es", "fr", "de", "it", "pt", "nl", "ru", "ja", "ko", "zh")
LANGUAGE_DEFAULT = "en"
# Parakeet Unified, the fast native recognizer, is English-only. Everything
# else routes to Whisper, which the picker says plainly in one quiet line
# rather than behind a warning.
NATIVE_RECOGNIZER_LANGUAGES = ("en",)
# macOS virtual key codes for every key that may be bound. Modifier-class and
# function keys only: binding a letter or digit would swallow ordinary typing
# in every application, which is worse than the problem the setting solves.
HOTKEY_KEYCODES = {
    61: "alt_r", 58: "alt_l",
    54: "cmd_r", 55: "cmd_l",
    62: "ctrl_r", 59: "ctrl_l",
    60: "shift_r", 56: "shift_l",
    122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6",
    98: "f7", 100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12",
    105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18",
    80: "f19", 90: "f20",
}
# Which voice-mode modifier a bindable key belongs to, for the picker's
# warning copy. Kept beside the keycodes because both describe the same
# vocabulary the runtime owns.
HOTKEY_MODIFIER_FAMILIES = {
    "shift_l": "shift", "shift_r": "shift",
    "cmd_l": "command", "cmd_r": "command",
    "ctrl_l": "control", "ctrl_r": "control",
}
RECENT_DICTATION_LIMIT = 10
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
            # As git writes it. The sidebar's foot is a quiet line of
            # small print; in capitals it read as debug output.
            return result.stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return "local"
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
#
# A no-break space (written \u00a0) keeps a button's name on one line
# inside a sentence, and keeps a sentence that wraps from leaving its last
# word alone on a line. VoiceOver reads it as a space.
STRING_CATALOGS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "nav.home": "Home",
        "nav.settings": "Settings",
        "nav.advanced": "Advanced",
        "menu.edit": "Edit",
        "menu.edit.undo": "Undo",
        "menu.edit.redo": "Redo",
        "menu.edit.cut": "Cut",
        "menu.edit.copy": "Copy",
        "menu.edit.paste": "Paste",
        "menu.edit.select_all": "Select All",
        "menu.window": "Window",
        "menu.window.close": "Close",
        "menu.window.minimize": "Minimize",
        "nav.selected": "Selected",
        "app.face_chip.label": "Whisper Face mascot",
        "app.face_chip.help": "Gives the mascot a friendly wobble. Nothing else changes.",
        "advanced.accessibility.shortcut": "Advanced section shortcut",
        "advanced.accessibility.shortcut.help": "Show the Advanced section (Command-D).",
        "app.subtitle": "Private, fast voice input on your Mac",
        "app.version": "Build {version}",
        "overview.phase.ready": "READY",
        "overview.phase.recording": "RECORDING",
        "overview.phase.processing": "PROCESSING",
        "overview.phase.recovery": "RECOVERY AVAILABLE",
        "overview.phase.degraded": "ACTION NEEDED",
        "overview.phase.paused": "PAUSED",
        "overview.phase.starting": "STARTING LOCALLY",
        "overview.status.ready.title": "Ready when you are",
        "overview.status.ready.detail": "Hold {hotkey}, speak, then release to\u00a0insert.",
        "overview.status.recording.title": "Listening…",
        "overview.status.recording.detail": "Keep holding {hotkey}; release when you finish\u00a0speaking.",
        "overview.status.processing.title": "Making your words useful…",
        "overview.status.processing.detail": "Recognizing locally, protecting names and numbers, then checking the\u00a0destination.",
        "overview.status.recovery.title": "Your words are safe",
        "overview.status.recovery.detail.one": "1 dictation needs an explicit Copy\u00a0&\u00a0Dismiss\u00a0review.",
        "overview.status.recovery.detail.many": "{count} dictations need an explicit Copy\u00a0&\u00a0Dismiss\u00a0review.",
        "overview.status.degraded.title": "One setup item needs attention",
        "overview.status.paused.title": "Dictation is paused",
        "overview.status.paused.detail": "Resume whenever you are ready. Settings and recovery still\u00a0work.",
        "overview.status.starting.title": "Finishing local startup…",
        "overview.status.starting.detail": "You can leave this window open; readiness updates\u00a0automatically.",
        "overview.engine.waiting": "Waiting for status",
        "overview.engine.warming": "Warming up",
        "overview.engine.active": "Active engine: {engine}",
        "overview.outbox.empty": "Voice Outbox: all clear",
        "overview.outbox.pending": "Voice Outbox: {count} recoverable · {summary}",
        "overview.outbox.unchecked": "Voice Outbox: {count} pasted, not double-checked",
        "overview.outbox.mixed": "Voice Outbox: {count} recoverable, {unchecked} pasted · {summary}",
        "overview.outbox.summary.paste_attempted": "Paste may have landed — verify before reusing",
        "overview.outbox.summary.not_pasted": "Not pasted — destination changed",
        "overview.outbox.summary.pasted_unchecked": "Pasted, not double-checked",
        "overview.action.pause": "Pause",
        "overview.action.resume": "Resume",
        "overview.action.pause.help": "Pause or resume the global dictation hotkey.",
        "overview.action.review": "Review Setup",
        "overview.action.review.help": "Show the most useful recovery guidance.",
        "overview.action.copy_outbox": "Copy & Dismiss",
        "overview.action.copy_outbox.help": "Copy the latest dictation kept in the Voice Outbox, then remove it from there.",
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
        "overview.notice.outbox.copied": "Latest Voice Outbox dictation copied and dismissed",
        "overview.notice.outbox.error": "Could not copy Voice Outbox: {error}",
        "overview.notice.capture.error": "Could not change capture state: {error}",
        "overview.notice.status.error": "Status unavailable: {error}",
        "onboarding.permissions.title": "First, let me hear you",
        "onboarding.permissions.detail": "Microphone so I can hear you, Accessibility so I can type for you, Input Monitoring so your key works. You grant them in System Settings — I never change them\u00a0myself.",
        "onboarding.hotkey.title": "Now try your key",
        "onboarding.hotkey.detail": "Hold {hotkey}, say a few words, then let go. This step only ticks once Whisper Face observes capture on this Mac — no\u00a0pretending.",
        "onboarding.models.title": "Getting your models ready",
        "onboarding.models.detail": "The recognition models unpack here the first time you run. One ready engine is all you need; the rest can keep warming in the\u00a0background.",
        "onboarding.first_dictation.title": "Say something",
        "onboarding.first_dictation.detail": "Click into any text field, hold your key, and speak one sentence. If the focus moves before it lands, your words wait in the Voice Outbox under Copy\u00a0&\u00a0Dismiss.",
        "onboarding.status.done": "Done",
        "onboarding.status.attention": "Needs attention",
        "onboarding.status.try": "Try it now",
        "onboarding.status.warming": "Warming up",
        "onboarding.status.turn": "Your turn",
        "onboarding.status.checking": "Checking",
        "onboarding.progress": "SETTING UP · {completed} OF {total} DONE",
        "onboarding.step.permissions": "Permissions",
        "onboarding.step.hotkey": "Your key",
        "onboarding.step.models": "Models",
        "onboarding.step.first_dictation": "First words",
        "onboarding.step.summary": "{step}\n{status}",
        "onboarding.action.permissions": "Review Permissions",
        "onboarding.action.open_system_settings": "Open System Settings",
        "onboarding.action.open_system_settings.help": "Open the Privacy & Security pane for the permission that is missing. Whisper Face does not change permissions.",
        "onboarding.action.hotkey": "Show Me How",
        "onboarding.action.models": "Check On Them",
        "onboarding.action.first_dictation": "Show Me How",
        "onboarding.action.continue": "Keep Going",
        "onboarding.action.help": "Open the next incomplete first-run setup step.",
        "onboarding.action.finish": "Start Dictating",
        "onboarding.action.finish.help": "Finish first-run setup and show the live dictation overview.",
        "onboarding.complete.progress": "ALL SET · {total} OF {total} CONFIRMED",
        "onboarding.complete.title": "Nice. You’re ready.",
        "onboarding.complete.detail": "Permissions, your key, a local model, and one real dictation — all four confirmed on this Mac. Hold your key anywhere you can type and just\u00a0talk.",
        "onboarding.complete.status": "Every step confirmed",
        "onboarding.accessibility.progress_track": "First run setup progress",
        "onboarding.privacy": "Everything you say stays on this Mac.",
        "onboarding.complete": "Setup is complete — Whisper Face is ready.",
        "results.summary.empty": "No dictation yet",
        "results.summary.words.one": "1 word",
        "results.summary.words.many": "{words} words",
        "results.summary.timed.one": "1 word in {seconds}s",
        "results.summary.timed.many": "{words} words in {seconds}s",
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
        "results.empty.title": "Your first dictation lands here",
        "results.empty.detail": "Say a sentence and I’ll show what I heard, protected, and delivered.",
        "results.audio.off": "Acoustic replay is off",
        "results.audio.empty": "Nothing held for replay right now — retained spans clear themselves the moment they expire",
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
        "results.value.words.one": "1 word",
        "results.value.words.many": "{count} words",
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
        "models.waiting.detail": "Leave this window open — each model appears as startup reports it",
        "models.guidance": "Models prepare locally and can finish in the background.",
        "models.state.ready": "Ready",
        "models.state.installed": "Installed",
        "models.state.optional": "Optional",
        "models.state.not_installed": "Missing",
        "models.state.failed": "Needs repair",
        "models.state.mismatch": "Wrong version",
        "models.state.checking": "Checking",
        "models.detail.ready": "Installed and answered on this Mac",
        "models.detail.installed": "Installed · warms up on first use",
        "models.detail.optional": "Not installed · add it with ./setup.sh --models",
        "models.detail.not_installed": "Not installed · dictation needs it, run Install.command again",
        "models.detail.failed": "Files are incomplete · run the installer again to repair",
        "models.detail.mismatch": "A different version is installed · run the installer again to repair",
        "models.detail.checking": "Checking the files on this Mac",
        "models.relisten.label": "Selective Re-listen",
        "models.relisten.help": "Recheck only uncertain names and numbers with the local Whisper Tiny verifier. Activation requires approved evidence from this Mac.",
        "models.speech_runtime.label": "New speech runtime",
        "models.speech_runtime.on": "On. Whisper Face runs the speech model with its own code, and goes back to the previous one by itself if anything\u00a0fails.",
        "models.speech_runtime.off": "Off. When on, Whisper Face runs the speech model with its own code, and goes back to the previous one by itself if anything\u00a0fails.",
        "models.speech_runtime.last.new": "{caption} The last dictation used the new\u00a0one.",
        "models.speech_runtime.last.previous": "{caption} The last dictation used the previous\u00a0one.",
        "models.speech_runtime.preparing": "{caption} It is still getting ready, so dictation uses the previous one until\u00a0then.",
        "models.speech_runtime.help": "Off by default. When on, Whisper Face runs the speech model with its own code from the next dictation, and goes back to the previous one by itself if anything fails.",
        "models.wallet.unavailable": "Model wallet shadow advisory only · No model execution or routing · Exact pin evidence unavailable · Runtime readiness and capability evidence remain separate.",
        "models.wallet.evidence": "Model wallet shadow advisory only · No model execution or routing · Exact files resolved {resolved}/4 · Warm path observed {warm}/4 · Runtime readiness attested 0/4 · Capability bounds available 0/4.",
        "models.wallet.informational": "Model wallet shadow advisory only · No model execution or routing · Eligible ordering is informational.",
        "models.accessibility.name": "Model name",
        "models.accessibility.detail": "{name} role and detail",
        "models.accessibility.status": "{name} status",
        "models.accessibility.guidance": "Model guidance",
        "models.accessibility.wallet": "Model wallet shadow advisory; no execution or routing",
        "models.accessibility.relisten": "Selective Re-listen activation",
        "models.accessibility.speech_runtime": "New speech runtime",
        "diagnostics.service": "Service",
        "diagnostics.microphone": "Microphone",
        "diagnostics.accessibility": "Accessibility",
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
        "diagnostics.action.open_system_settings.help": "Open the Privacy & Security pane for the permission that is missing. Whisper Face does not change permissions.",
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
        "diagnostics.regression.cases.one": "1 case",
        "diagnostics.regression.cases.many": "{count} cases",
        "diagnostics.motion.reduced": "Reduced motion",
        "diagnostics.motion.standard": "Standard motion",
        "diagnostics.issue": "{title}: {detail}",
        "diagnostics.accessibility.service": "Service status",
        "diagnostics.accessibility.microphone": "Microphone status",
        "diagnostics.accessibility.permission": "Accessibility permission status",
        "diagnostics.accessibility.regression": "Correction tests status",
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
        "issue.input_monitoring.title": "Your key needs Input Monitoring permission",
        "issue.input_monitoring.detail": "Open System Settings › Privacy & Security › Input Monitoring and enable Whisper Face.",
        "issue.models.title": "A model dictation needs is missing",
        "issue.models.detail": "Run Install.command again to restore {models}. Your settings stay.",
        "issue.fallback.title": "An optional model needs repair",
        "issue.fallback.detail": "Dictation still works. Run the installer again to repair {models}.",
        "settings.pane.personalize": "Personalize",
        "settings.pane.controls": "Controls",
        "settings.pane.privacy": "Privacy",
        "settings.controls.hotkey": "Capture key",
        "settings.controls.hotkey.detail": "Hold {hotkey} to dictate",
        "settings.controls.hotkey.shared": "Hold {hotkey} to dictate · {modes} need the other {family} key",
        "settings.controls.undo": "Undo key",
        "settings.controls.undo.detail": "Press {hotkey}, or use Undo Last Dictation in the menu bar",
        "settings.controls.undo.unset": "Not set — use Undo Last Dictation in the menu bar",
        "settings.controls.sound": "Feedback sounds",
        "settings.controls.sound.system": "macOS",
        "settings.controls.sound.whisper": "Whisper Face",
        "settings.controls.sound.silent": "Silent",
        "settings.controls.sound.detail.system": "Borrowed macOS system sounds",
        "settings.controls.sound.detail.whisper": "Short first-party cues for start, finish, and trouble",
        "settings.controls.sound.detail.silent": "No sound at all",
        "settings.controls.remove_repeats": "Repeated words",
        "settings.controls.remove_repeats.on": "On. “I have nothing I have nothing going on” types “I have nothing going\u00a0on”.",
        "settings.controls.remove_repeats.off": "Off. When on, “I have nothing I have nothing going on” types “I have nothing going\u00a0on”.",
        "settings.controls.self_correction": "“Actually” corrections",
        "settings.controls.self_correction.off": "Off. When on, “Meet at 3 pm, actually 4 pm” types “Meet at 4 pm” for numbers, days, months\u00a0and\u00a0times.",
        "settings.controls.self_correction.on": "On. “Meet at 3 pm, actually 4 pm” types “Meet at 4 pm” for numbers, days, months\u00a0and\u00a0times.",
        "settings.controls.self_correction.cost": "With “Actually” on, a real contrast changes too: “I thought it was Tuesday, actually Wednesday” loses “Tuesday”.",
        "settings.controls.action.change": "Change…",
        "settings.controls.action.preview": "Preview",
        "settings.dialog.hotkey.title": "Choose your capture key",
        "settings.dialog.hotkey.message": "Press the key you want to hold while dictating. Modifier keys and function keys only — an ordinary key would swallow your typing everywhere.",
        "settings.dialog.undo.title": "Choose your undo key",
        "settings.dialog.undo.message": "Press the key you want to press to undo the last dictation. Modifier keys and function keys only.",
        "settings.dialog.hotkey.waiting": "Waiting for a key…",
        "settings.dialog.hotkey.current": "Now bound to {hotkey}",
        "settings.dialog.hotkey.none": "Nothing bound yet",
        "settings.dialog.hotkey.captured": "{hotkey}",
        "settings.dialog.hotkey.rejected": "That key would swallow ordinary typing. Try a modifier key or a function key.",
        "settings.dialog.hotkey.conflict": "That is already your capture key. Pick a different one.",
        "settings.dialog.hotkey.shared": "{modes} will need the other {family} key.",
        "settings.dialog.hotkey.use": "Use This Key",
        "settings.dialog.hotkey.clear": "Clear",
        "settings.modifier.shift": "Shift",
        "settings.modifier.command": "Command",
        "settings.modifier.control": "Control",
        "settings.accessibility.hotkey.help": "Choose which key you hold to dictate.",
        "settings.accessibility.undo_hotkey.help": "Choose a key that undoes the last dictation.",
        "settings.accessibility.sound.label": "Feedback sounds",
        "settings.accessibility.sound.help": "Choose macOS sounds, the first-party set, or silence.",
        "settings.accessibility.sound_preview.help": "Play the finish cue for the selected sound setting.",
        "settings.accessibility.capture_field.label": "Key capture",
        "settings.accessibility.capture_field.help": "Press a modifier key or a function key to bind it.",
        "settings.privacy.recent": "Recent dictations",
        "settings.privacy.recent.detail": "Off. Nothing is listed until you turn this on.",
        "settings.privacy.recent.on": "On · last {count} kept locally, text hidden until you reveal it",
        "settings.privacy.recent.browse": "Browse…",
        "settings.privacy.recent.browse.help": "List recent dictations by time, length, and app.",
        "settings.accessibility.recent.label": "Recent dictations",
        "settings.accessibility.remove_repeats.label": "Repeated words",
        "settings.accessibility.remove_repeats.help": "On by default. A phrase said twice by accident is typed once: “I have nothing I have nothing going on” types “I have nothing going on”. Anything said three times, like “no way no way no way”, stays as you said it.",
        "settings.accessibility.self_correction.label": "“Actually” corrections",
        "settings.accessibility.self_correction.help": "Off by default. When on, “Meet at 3 pm, actually 4 pm” types “Meet at 4 pm”: between two numbers, days, months or times, only the second is kept. A sentence contrasting two things, like “I thought it was Tuesday, actually Wednesday”, changes too.",
        "settings.accessibility.recent.help": "Show recent dictations so their words can be revealed.",
        "settings.dialog.recent.title": "Recent dictations",
        "settings.dialog.recent.message": "Metadata only. Reveal a dictation to see its words. To insert one again, click where it should go and use Recent Dictations in the menu bar.",
        "settings.dialog.recent.empty": "Nothing recorded yet.",
        "settings.dialog.recent.row": "{when} · {words} · {app}",
        "settings.dialog.recent.words": "{count} words",
        "settings.dialog.recent.word": "1 word",
        "recent.age.now": "just now",
        "recent.age.minutes": "{count} min ago",
        "recent.age.hours": "{count} hr ago",
        "recent.age.days": "{count}d ago",
        "recent.app.unknown": "unknown app",
        "settings.dialog.recent.reveal": "Reveal Text…",
        "settings.dialog.recent.revealed.title": "Recent dictation",
        "settings.dialog.recent.revealed.gone": "That dictation is no longer in the local log.",
        "settings.accessibility.recent_list.label": "Recent dictations list",
        "settings.accessibility.recent_list.help": "Choose one recent dictation to reveal.",
        "settings.notice.hotkey": "Capture key is now {hotkey}.",
        "settings.notice.undo_hotkey": "Undo key is now {hotkey}.",
        "settings.notice.undo_hotkey.cleared": "Undo key cleared.",
        "settings.notice.hotkey.rejected": "That key cannot be a shortcut.",
        "settings.notice.undo.done": "Last dictation undone.",
        "settings.notice.undo.refused": "Undo declined: {reason}.",
        "settings.notice.recent.inserted": "Inserted again.",
        "settings.notice.recent.refused": "Insertion declined: {reason}.",
        "settings.mode.capture.name": "Capture",
        "settings.hotkey.default": "Right Option",
        "settings.mode.capture.shortcut": "{hotkey}",
        "settings.mode.capture.detail": "Faithful dictation",
        "settings.mode.compose.name": "Compose",
        "settings.mode.compose.shortcut": "Shift + {hotkey}",
        "settings.mode.compose.detail": "Compose and tighten",
        "settings.mode.edit.name": "Edit",
        "settings.mode.edit.shortcut": "Command + {hotkey}",
        "settings.mode.edit.detail": "Edit selected text",
        "settings.mode.reply.name": "Reply",
        "settings.mode.reply.shortcut": "Control + {hotkey}",
        "settings.mode.reply.detail": "Draft a direct reply",
        "settings.mode.command.name": "Command",
        "settings.mode.command.shortcut": "Command + Control + {hotkey}",
        "settings.mode.command.detail": "Editing commands",
        "settings.mode.code.name": "Code",
        "settings.mode.code.shortcut": "Shift + Control + {hotkey}",
        "settings.mode.code.detail": "Technical dictation",
        "settings.personalize.language": "Language",
        "settings.personalize.language.native":
            "English gets the fast on-device recognizer",
        "settings.personalize.language.whisper":
            "{language} runs on Whisper — accurate, a little slower",
        # Endonyms: someone looking for their own language scans for the name
        # they call it, in the script they read.
        "settings.language.en": "English",
        "settings.language.es": "Español",
        "settings.language.fr": "Français",
        "settings.language.de": "Deutsch",
        "settings.language.it": "Italiano",
        "settings.language.pt": "Português",
        "settings.language.nl": "Nederlands",
        "settings.language.ru": "Русский",
        "settings.language.ja": "日本語",
        "settings.language.ko": "한국어",
        "settings.language.zh": "中文",
        "settings.personalize.tones": "App tones",
        "settings.personalize.tones.detail.one": "1 recent or configured app",
        "settings.personalize.tones.detail.many": "{count} recent or configured apps",
        "settings.personalize.tones.empty": "Dictate somewhere and that app shows up here",
        "settings.personalize.snippets": "Snippets",
        "settings.personalize.snippets.detail.one": "1 saved phrase",
        "settings.personalize.snippets.detail.many": "{count} saved phrases",
        "settings.personalize.snippets.empty": "Save a phrase you type all the time",
        "settings.personalize.vocabulary": "Vocabulary",
        "settings.personalize.vocabulary.detail": "{terms} · {bans}",
        "settings.personalize.vocabulary.terms.one": "1 term",
        "settings.personalize.vocabulary.terms.many": "{count} terms",
        "settings.personalize.vocabulary.exclusions.one": "1 exclusion",
        "settings.personalize.vocabulary.exclusions.many": "{count} exclusions",
        "settings.personalize.vocabulary.empty": "Teach me the names I keep getting wrong",
        "settings.personalize.corrections": "Learned corrections",
        "settings.personalize.corrections.empty": "Fix the same word a few times and I’ll remember it",
        "settings.personalize.keywords": "Pronunciation keywords",
        "settings.personalize.modes": "Voice modes",
        "settings.personalize.modes.detail": "Six {hotkey} shortcuts choose capture behavior",
        "settings.dialog.modes.title": "Voice modes",
        "settings.dialog.modes.message": "Hold {hotkey} with a modifier to choose a mode. The modifier for each mode is fixed so capture behavior stays predictable and safe.",
        "settings.dialog.modes.row": "{name} · {shortcut}\n    {detail}",
        "settings.accessibility.modes.label": "Voice mode shortcuts",
        "settings.accessibility.modes.help": "View the six voice mode shortcuts.",
        "settings.accessibility.modes_summary.label": "Voice modes summary",
        "settings.action.view": "View",
        "settings.action.edit": "Edit",
        "settings.action.inspect": "Inspect",
        "settings.action.export": "Copy Export",
        "settings.action.done": "Done",
        "settings.action.forget_all": "Forget All",
        "settings.action.delete": "Delete",
        "settings.action.forget": "Forget",
        "settings.action.review": "Review",
        "settings.action.save": "Save",
        "settings.action.cancel": "Cancel",
        "settings.empty.tones": "No apps yet — dictate somewhere first",
        "settings.empty.snippets": "No snippets yet — add your first",
        "settings.empty.corrections": "Nothing learned yet — correct a word a few times",
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
        "settings.dialog.snippet.name.help": "Enter a short name for this snippet.",
        "settings.dialog.snippet.value.help": "Enter the text inserted when this snippet is used.",
        "settings.dialog.vocabulary.title": "Personal vocabulary",
        "settings.dialog.vocabulary.terms": "Preferred terms",
        "settings.dialog.vocabulary.bans": "Excluded terms",
        "settings.dialog.vocabulary.terms.help": "Enter preferred words or names, one per line.",
        "settings.dialog.vocabulary.bans.help": "Enter words that Whisper Face must not learn, one per line.",
        "settings.dialog.delete.title": "Delete snippet?",
        "settings.dialog.delete.message": "This removes “{name}” from this Mac.",
        "settings.dialog.correction.scope.global": "Applies: Whole-word matches in every app.",
        "settings.dialog.correction.scope.apps": "Applies: Whole-word matches in {apps}.",
        "settings.dialog.correction.scope.inactive": "Applies: Not active yet.",
        "settings.dialog.correction.scope.snippet": "Applies: This saved snippet now uses the replacement shown above.",
        "settings.dialog.correction.why.global": "Why: {count} exact corrections passed the local safety checks.",
        "settings.dialog.correction.why.apps": "Why: Repeated corrections passed the local safety checks in {apps}.",
        "settings.dialog.correction.why.held": "Why: Held back because the local correction cases disagree.",
        "settings.dialog.correction.why.learning": "Why: Still learning. Three matching corrections activate everywhere; two in one app activate there.",
        "settings.dialog.correction.why.snippet.one": "Why: You explicitly edited this snippet 1 time.",
        "settings.dialog.correction.why.snippet.many": "Why: You explicitly edited this snippet {count} times.",
        "settings.dialog.correction.observed": "Observed in: {apps}.",
        "settings.dialog.correction.observed.none": "Observed in: App information unavailable.",
        "settings.dialog.correction.privacy": "Stored locally: the corrected words, app scope, and counts only—never audio or surrounding transcript.",
        "settings.dialog.correction.privacy.snippet": "Stored locally: the snippet name, replacement, and edit count.",
        "settings.dialog.keywords.title": "Pronunciation keywords",
        "settings.dialog.keywords.observations.one": "1 observation",
        "settings.dialog.keywords.observations.many": "{count} observations",
        "settings.dialog.keywords.confirmations.one": "1 confirmation",
        "settings.dialog.keywords.confirmations.many": "{count} confirmations",
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
        "settings.privacy.voice_objects": "Voice Object Commands",
        "settings.privacy.voice_objects.detail": "Exact task, email, and calendar commands queue inert local drafts instead of pasting.",
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
        "settings.face.frog": "Frog",
        "settings.face.rabbit": "Rabbit",
        "settings.face.hedgehog": "Hedgehog",
        "settings.face.penguin": "Penguin",
        "settings.face.pickles": "Pickles",
        "settings.face.olive": "Olive",
        "settings.state.enabled": "Enabled",
        "settings.state.disabled": "Off",
        "settings.state.local_processing": "Local processing",
        "settings.accessibility.sections.label": "Settings sections",
        "settings.accessibility.sections.help": "Use arrow keys to move between Whisper Face settings sections.",
        "settings.accessibility.forget.help": "Inspect or forget {setting}.",
        "settings.accessibility.language.label": "Dictation language",
        "settings.accessibility.language.help": "Choose the language you dictate in. English uses the fast on-device recognizer; every other language is recognized by Whisper, which is accurate but slower.",
        "settings.accessibility.language_summary.label": "Dictation language summary",
        "settings.accessibility.face.label": "Whisper Face companion",
        "settings.accessibility.face.help": "Choose the animal shown in the menu bar and listening HUD.",
        "settings.accessibility.flight.label": "Flight Recorder",
        "settings.accessibility.flight.help": "Toggle the rolling twenty second audio buffer held only in memory.",
        "settings.accessibility.acoustic.label": "Acoustic Time Machine",
        "settings.accessibility.acoustic.help": "Opt in to selected consequential audio replay. Retained audio is wiped after one minute, and disabling clears it immediately.",
        "settings.accessibility.voice_objects.label": "Voice Object Commands",
        "settings.accessibility.voice_objects.help": "Opt in to exact spoken commands becoming inert local drafts. Nothing is sent or scheduled.",
        "settings.accessibility.voice_objects.chooser": "Queued draft metadata",
        "settings.accessibility.voice_objects.content": "Selected inert draft content",
        "settings.accessibility.voice_objects.copy": "Copy the revealed task or calendar draft after confirmation",
        "settings.accessibility.voice_objects.clear_clipboard": "Clear the copied draft only if the Mac clipboard is still unchanged",
        "settings.accessibility.tones_summary.label": "App tones summary",
        "settings.accessibility.snippets_summary.label": "Snippets summary",
        "settings.accessibility.vocabulary_summary.label": "Vocabulary summary",
        "settings.accessibility.corrections_summary.label": "Learned corrections summary",
        "settings.accessibility.keywords_summary.label": "Pronunciation keywords summary",
        "settings.accessibility.hotkey_summary.label": "Capture key summary",
        "settings.accessibility.undo_hotkey_summary.label": "Undo key summary",
        "settings.accessibility.undo_summary.label": "Undo availability",
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
        # Validation copy is user-facing: it names the field that stopped the
        # save and the change that will let it through.
        "validation.tone.selection": "That app tone selection is out of range. Pick an app from the list and try again.",
        "validation.section.unknown": "There is no section called {section}. Pick Home, Settings, or Advanced.",
        "validation.settings_pane.unknown": "There is no settings pane called {pane}. Pick Personalize or Privacy.",
        "validation.app.bundle": "That app needs a real bundle ID. Pick an app from the list instead of typing one.",
        "validation.tone.unsupported": "Whisper Face has no tone called {tone}. Pick one of the listed tones.",
        "validation.snippet.name": "A snippet name must be 1–80 characters on one line. Shorten it and save again.",
        "validation.snippet.text": "Snippet text must be 1–4000 characters. Add the text you want inserted, then save again.",
        "validation.snippet.required": "This snippet still needs a name. Give it one and save again.",
        "validation.snippet.expected": "That snippet could not be read as text, so nothing was saved. Reopen it and try again.",
        "validation.vocabulary.preferred": "preferred vocabulary",
        "validation.vocabulary.excluded": "excluded vocabulary",
        "validation.vocabulary.list": "Your {label} could not be read as a list of terms. Put one term per line and save again.",
        "validation.vocabulary.term_length": "Every {label} term must be at most 80 characters. Shorten the long one and save again.",
        "validation.vocabulary.reserved": "A term in your {label} cannot start with the reserved '-' or '#'. Remove that first character and save again.",
        "validation.vocabulary.maximum": "Your {label} holds at most 500 terms. Remove a few and save again.",
        "validation.vocabulary.overlap": "A term cannot also be excluded. Remove it from one of the two lists and save again.",
        "validation.correction.kind": "That is not a kind of learned correction Whisper Face stores. Reopen Learned corrections and pick one from the list.",
        "validation.correction.unknown": "That learned correction is no longer stored on this Mac. Reopen Learned corrections for the current list.",
        "validation.correction.stale_snippet": "That learned snippet edit no longer exists, so nothing was forgotten. Reopen Learned corrections for the current list.",
        "validation.keyword.unknown": "Whisper Face holds no evidence for that pronunciation keyword. Open Pronunciation keywords and pick one from the list.",
        "validation.face.unsupported": "There is no companion called {face}. Pick one of the ten faces.",
        "validation.language.unsupported": "Whisper Face does not dictate {language} yet. Pick one of the listed languages.",
        # Failure copy names what did not happen, what is still true, and the
        # one move that gets the person unstuck.
        "operation.settings.load_failed": "Could not load your settings: {error}. Nothing was changed — reopen this window to try again.",
        "operation.tone.save_failed": "Could not save that app tone: {error}. The previous tone is still in effect — try saving again.",
        "operation.snippet.save_failed": "Could not save that snippet: {error}. Nothing on this Mac changed — try saving again.",
        "operation.snippet.delete_failed": "Could not delete that snippet: {error}. It is still saved — try deleting it again.",
        "operation.vocabulary.save_failed": "Could not save your vocabulary: {error}. Your previous terms are still in effect — try saving again.",
        "operation.correction.forget_failed": "Could not forget that correction: {error}. It is still active — reopen Learned corrections and try again.",
        "operation.keyword.inspect_failed": "Could not inspect your pronunciation keywords: {error}. Nothing was changed — try Inspect again.",
        "operation.keyword.export_failed": "Could not copy the pronunciation keyword export: {error}. Nothing reached the clipboard — try Copy Export again.",
        "operation.keyword.forget_failed": "Could not forget that pronunciation keyword: {error}. Its evidence is unchanged — try Forget again.",
        "operation.face.change_failed": "Could not change your companion: {error}. The current face stays — pick it again.",
        "operation.remove_repeats.update_failed": "Could not change Repeated words: {error}. It stayed exactly as it was — try the switch again.",
        "operation.speech_runtime.update_failed": "Could not change New speech runtime: {error}. It stayed exactly as it was — try the switch again.",
        "operation.self_correction.update_failed": "Could not change “Actually” corrections: {error}. It stayed exactly as it was — try the switch again.",
        "operation.flight.update_failed": "Could not update Flight Recorder: {error}. It stayed exactly as it was — try the switch again.",
        "operation.acoustic.update_failed": "Could not update Acoustic Time Machine: {error}. It stayed exactly as it was — try the switch again.",
        "operation.relisten.update_failed": "Could not update Selective Re-listen: {error}. It stayed exactly as it was — try the switch again.",
        "operation.voice_objects.update_failed": "Could not update Voice Object Commands: {error}. It stayed exactly as it was — try the switch again.",
        "operation.voice_objects.inspect_failed": "Could not open the local Voice Inbox. No draft was read or changed — try Inspect again.",
        "operation.voice_objects.reveal_failed": "Could not reveal that local draft. It stays queued and unread — try Reveal again.",
        "operation.voice_objects.transition_failed": "Could not update that local draft. It stays exactly as it was — try the same action again.",
        "operation.voice_objects.purge_failed": "Could not purge the finished local drafts. They are all still listed — try Purge Finished again.",
        "operation.voice_objects.compose_failed": "Could not hand that email draft to macOS. Nothing was sent and the draft stays queued — try Open Compose Draft again.",
        "operation.voice_objects.copy_failed": "Could not copy that draft. Your clipboard is unchanged and the draft stays queued — try Copy Draft again.",
        "operation.voice_objects.clear_failed": "Could not clear the copied draft from the Mac clipboard. Copy something else to overwrite it yourself.",
        "operation.demonstrations.inspect_failed": "Could not open your local demonstration drafts. Nothing was changed — try Inspect again.",
        "operation.demonstrations.create_failed": "Could not create that local demonstration draft. Nothing was recorded — try creating it again.",
        "operation.demonstrations.reveal_failed": "Could not reveal that demonstration draft. It stays stored and unread — try Reveal again.",
        "operation.demonstrations.record_failed": "Could not record that demonstration step. Nothing was performed and the draft is unchanged — describe the step again.",
        "operation.demonstrations.approve_failed": "Could not approve that demonstration draft. It stays a draft — try Approve again.",
        "operation.demonstrations.cancel_failed": "Could not cancel that demonstration draft. It stays exactly as it was — try Cancel again.",
        "operation.demonstrations.delete_failed": "Could not delete that approved demonstration recipe. It is still stored — try Delete again.",
        "operation.risky_confirmation.start_failed": "Could not start the inert confirmation ceremony. Nothing was confirmed — try the action again.",
        "operation.risky_confirmation.click_failed": "Confirmation stayed blocked: this click was not preceded by a valid voice receipt. Say the confirmation phrase again, then click.",
        "operation.risky_confirmation.cancel_failed": "Could not cancel the inert confirmation ceremony. It stays blocked and nothing was confirmed — try Cancel again.",
        "operation.acoustic.play_failed": "Could not play that retained span: {error}. The span stays in memory until it expires — try Play Span again.",
        "operation.acoustic.clear_failed": "Could not clear the retained audio: {error}. It still expires on its own within one minute — try Clear again.",
        "operation.log.open_failed": "Could not open the log: {error}. Copy Support Snapshot works without file access if you need the details now.",
        "operation.system_settings.open_failed": "Could not open System Settings: {error}. Open it from the Apple menu, then go to Privacy & Security.",
        "operation.support_snapshot.copy_failed": "Could not copy the support snapshot: {error}. Nothing reached the clipboard — try Export Support Bundle instead.",
        "operation.support_bundle.export_failed": "Could not save the support bundle: {error}. Nothing was written — choose a different folder and try again.",
        "operation.source.open_failed": "Could not open the corresponding source: {error}. Check your default browser, then try Exact Source again.",
        "operation.licenses.open_failed": "Could not open the local license notices: {error}. They also sit beside dictate.py as LICENSE_POLICY.md and THIRD_PARTY_NOTICES.md.",
        "settings.dialog.voice_objects.title": "Voice Inbox",
        "settings.dialog.voice_objects.message": "Only bounded draft metadata is listed. Select Reveal to read one draft. A revealed queued email can request a native compose window; a revealed queued task or calendar draft can be copied. Each action requires a separate confirmation.",
        "settings.dialog.voice_objects.empty": "No local drafts are stored. Speak an exact task, email, or calendar command and it queues here — inert until you act on it.",
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

        # --- new look: shell

        "shell.sidebar.accessibility.label": "Whisper Face pages",
        "shell.sidebar.group.settings": "Settings",
        "shell.state.accessibility.label": "Whisper Face status",
        "shell.state.ready": "Ready",
        "shell.state.recording": "Listening",
        "shell.state.processing": "Working",
        "shell.state.recovery": "Recoverable",
        "shell.state.degraded": "Needs attention",
        "shell.state.paused": "Paused",
        "shell.state.starting": "Starting",
        "shell.state.setup": "Setting up",
        "menu.view": "View",

        # --- new look: pages

        "pages.personalize.face": "Your face",
        "pages.personalize.face.hint": "{face} · in the menu bar and while you dictate",
        "pages.personalize.words": "Language and words",
        "pages.personalize.corrections.detail.one": "1 word it now fixes your way",
        "pages.personalize.corrections.detail.many": "{count} words it now fixes your way",
        "pages.personalize.keywords.detail": "Names it learned to hear from your fixes",
        "pages.personalize.tones.action": "Edit app tones",
        "pages.personalize.snippets.action": "Edit snippets",
        "pages.personalize.vocabulary.action": "Edit vocabulary",
        "pages.personalize.corrections.action": "Review learned corrections",
        "pages.personalize.keywords.action": "Inspect pronunciation keywords",
        "pages.controls.hotkey.action": "Change capture key",
        "pages.controls.undo.action": "Change undo key",
        "pages.controls.modes.action": "View voice modes",
        "pages.controls.sound.action": "Preview feedback sounds",
        "pages.controls.keys": "Keys",
        "pages.controls.sounds": "Sounds",
        "pages.controls.corrections": "Corrections",
        "pages.privacy.audio": "Audio kept in memory",
        "pages.privacy.dictations": "Dictations and drafts",
        "pages.privacy.acoustic.detail": "Keeps short pieces of your last dictation’s audio in memory, never on disk, for at most a minute.",
        "pages.privacy.recent.action": "Browse recent dictations",
        "pages.privacy.voice_objects.action": "Inspect Voice Inbox",
        "pages.privacy.voice_objects.off": "Off",
        "pages.privacy.voice_objects.ready": "On",
        "pages.privacy.voice_objects.unavailable": "Not available on this Mac",
        "pages.privacy.voice_objects.status.one": "{status} · 1 draft waiting in the Voice Inbox",
        "pages.privacy.voice_objects.status.many": "{status} · {count} drafts waiting in the Voice Inbox",
        "pages.advanced.models": "Models",
        "pages.advanced.status": "Status",
        "pages.advanced.support": "Support",
        "pages.models.status.unavailable": "Not available",
        "pages.models.advisory": "Model checks are for information only; they never change which\u00a0model\u00a0runs.",
        "pages.models.advisory.evidence": "Model files found on this Mac: {found} of {total} · warmed up: {warm} of {total}. These checks are for information only; they never change which\u00a0model\u00a0runs.",
        "pages.models.advisory.unavailable": "Model file checks are not available on this Mac yet. They are for information only; they never change which\u00a0model\u00a0runs.",
        "pages.models.advisory.label": "Model checks",
        "pages.relisten.detail": "Rechecks names and numbers it was unsure of · {status}",
        "pages.relisten.status.off": "Off",
        "pages.relisten.status.ready": "On",
        "pages.relisten.status.warming": "On, getting ready",
        "pages.relisten.status.starting": "On, starting",
        "pages.relisten.status.unavailable": "Not available until this Mac passes its check",
        "pages.diagnostics.regression": "Correction tests",
        "pages.diagnostics.regression.held": " · {count} set aside",
        "pages.support.verify": "Verification",
        "pages.support.log": "Log",
        "pages.support.log.detail": "The app’s own log file on this Mac",
        "pages.support.snapshot": "Support snapshot",
        "pages.support.snapshot.detail": "A summary without your words, to paste into a report",
        "pages.support.bundle": "Support bundle",
        "pages.support.bundle.detail": "The same summary as a file, saved where you choose",
        "pages.support.permissions": "Permissions",
        "pages.support.permissions.detail": "A permission Whisper Face needs is missing",
        "pages.support.license": "License",
        "pages.support.licenses.action": "Open license notices",
        "pages.support.source.action": "Open the exact source",

        # --- new look: home and setup

        "key_cap.accessibility.role": "key",
        "home.stage.accessibility.label": "Whisper Face on Home",
        "home.group.dictation": "Dictation",
        "home.attention.accessibility.label": "Setup needs a review",
        "home.attention.fallback": "Setup is not finished",
        "home.replay.clear.action": "Clear retained spans",
        "onboarding.stage.accessibility.label": "Whisper Face, first-run guide",
        "onboarding.rows.accessibility.label": "What this step is waiting on",
        "onboarding.permission.microphone": "Microphone",
        "onboarding.permission.microphone.why": "So I can hear you while you hold your key",
        "onboarding.permission.accessibility": "Accessibility",
        "onboarding.permission.accessibility.why": "So I can type into the app you are using",
        "onboarding.permission.input_monitoring": "Input Monitoring",
        "onboarding.permission.input_monitoring.why": "So I notice the key you hold to talk",
        "onboarding.permission.granted": "Allowed",
        "onboarding.permission.checking": "Checking",
        "onboarding.permission.open.label": "Open {permission} settings",
        "onboarding.permission.open.help": "Open System Settings › Privacy & Security › {permission}. Whisper Face does not change permissions.",
        "onboarding.row.hotkey.caption": "Hold it, say a few words, then let go",
        "onboarding.row.models.caption": "Waiting for the first local model to report in",
        "onboarding.row.first_dictation.caption": "Click into a text field, hold your key and say one sentence",
        "onboarding.row.accessibility.status": "{row} status",
        "onboarding.action.waiting.help": "Available once every permission above is allowed in System Settings.",

        # --- new look: editors

        "sheets.notice.error.label": "Problem",
        "sheets.notice.result.label": "Result",
        "sheets.tones.message": "Choose how cleanup should sound in each app.",
        "sheets.tones.choice.label": "Cleanup tone for {app}",
        "sheets.tones.refused": "{app}: {reason}",
        "sheets.tones.saved_before": "Already saved: {apps}.",
        "sheets.snippets.message": "Say “insert my” and a snippet’s name, and its text is typed for you.",
        "sheets.snippets.add": "Add Snippet…",
        "sheets.snippets.edit.label": "Edit snippet {name}",
        "sheets.snippets.delete.label": "Delete snippet {name}",
        "sheets.snippet.message.add": "Give it a short name you can say out loud.",
        "sheets.snippet.message.edit": "Change the text this snippet types. Its name stays as it is.",
        "sheets.snippet.name": "Name",
        "sheets.snippet.text": "Text",
        "sheets.snippet.speak": "To use it, say “insert my” and then its name. A snippet named “email” is “insert my email”.",
        "sheets.vocabulary.message": "Words to get right, and words to leave alone.",
        "sheets.vocabulary.terms.note": "Names and words to spell exactly this way. One per line.",
        "sheets.vocabulary.bans.note": "Words Whisper Face should never pick up by itself. One per line.",
        "sheets.corrections.message": "Fixes Whisper Face has learned from your own corrections, and whether each one is on yet.",
        "sheets.corrections.words": "Words and names",
        "sheets.corrections.snippets": "Snippet edits",
        "sheets.corrections.count": "{count}×",
        "sheets.corrections.standing.global": "On in every app",
        "sheets.corrections.standing.apps": "On in {apps}",
        "sheets.corrections.standing.held": "Held back, because your corrections for it disagree",
        "sheets.corrections.standing.learning": "Still learning, so not on yet",
        "sheets.corrections.standing.snippet": "From an edit you made to this snippet",
        "sheets.corrections.seen": "Seen in {apps}",
        "sheets.corrections.rule": "Three matching corrections turn a fix on everywhere; two in one app turn it on there.",
        "sheets.corrections.forget.label": "Forget the correction {source} to {target}",
        "sheets.corrections.forget.title": "Forget this correction?",
        "sheets.corrections.forget.message": "“{source}” will no longer be changed to “{target}”.",
        "sheets.keywords.message": "Names picked up from your corrections, kept on this Mac. On their own they do not change what is recognized; only a name you test and approve separately can.",
        "sheets.keywords.caption": "{observations} · {confirmations} · {status} · {scope}",
        "sheets.keywords.status.eligible": "seen often enough",
        "sheets.keywords.status.gathering": "still learning",
        "sheets.keywords.scope.global": "every app",
        "sheets.keywords.scope.private_app": "one app only",
        "sheets.keywords.forget.label": "Forget the keyword {keyword}",
        "sheets.keywords.forget_all": "Forget All…",
        "sheets.keywords.empty": "No names yet. Fix a name Whisper Face mishears and it shows up here.",

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
    if "error" in values and "{error}." in template:
        # Templates end the interpolated reason with their own period.
        values = {**values, "error": str(values["error"]).rstrip(" .")}
    try:
        return template.format(**values)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid values for localized string {key!r}") from error


def plural_key(base: str, count: int) -> str:
    """Select the ``.one``/``.many`` catalog variant for a count.

    Pluralized copy lives entirely in the catalog as explicit key pairs
    (``overview.metric.last.words.one`` / ``.many``), so each language
    catalog can phrase both forms; the caller only picks which key applies.
    """

    return f"{base}.one" if count == 1 else f"{base}.many"
FACES = (
    "parrot", "fox", "owl", "cat", "bear",
    "dog", "wolf", "pig", "panda", "tiger",
    "frog", "rabbit", "hedgehog", "penguin",
    "pickles", "olive",
)
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
            "choose_language",
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
            "onboarding.accessibility.progress_track",
            "key_cap.accessibility.role",
            "home.stage.accessibility.label",
            "home.attention.accessibility.label",
            "onboarding.stage.accessibility.label",
            "onboarding.rows.accessibility.label",
            "onboarding.permission.open.label",
            "onboarding.row.accessibility.status",
            "onboarding.action.waiting.help",
            "home.replay.clear.action",
            "pages.personalize.tones.action",
            "pages.personalize.snippets.action",
            "pages.personalize.vocabulary.action",
            "pages.personalize.corrections.action",
            "pages.personalize.keywords.action",
            "pages.controls.hotkey.action",
            "pages.controls.undo.action",
            "pages.controls.modes.action",
            "pages.controls.sound.action",
            "pages.privacy.recent.action",
            "pages.privacy.voice_objects.action",
            "pages.support.licenses.action",
            "pages.support.source.action",
            "settings.accessibility.sections.label",
            "shell.sidebar.accessibility.label",
            "shell.state.accessibility.label",
            "settings.accessibility.face.label",
            "settings.accessibility.language.label",
            "settings.accessibility.flight.label",
            "settings.accessibility.acoustic.label",
            "settings.accessibility.remove_repeats.label",
            "settings.accessibility.self_correction.label",
            "settings.accessibility.voice_objects.label",
            "settings.accessibility.voice_objects.chooser",
            "settings.accessibility.voice_objects.content",
            "settings.accessibility.voice_objects.copy",
            "settings.accessibility.voice_objects.clear_clipboard",
            "settings.accessibility.modes.label",
            "sheets.tones.choice.label",
            "sheets.snippets.edit.label",
            "sheets.snippets.delete.label",
            "settings.dialog.snippet.name",
            "settings.dialog.snippet.value",
            "settings.dialog.vocabulary.terms",
            "settings.dialog.vocabulary.bans",
            "sheets.corrections.forget.label",
            "sheets.keywords.forget.label",
            "sheets.notice.error.label",
            "sheets.notice.result.label",
            "results.accessibility.audio",
            "models.accessibility.guidance",
            "pages.models.advisory.label",
            "models.accessibility.relisten",
            "models.accessibility.speech_runtime",
            "diagnostics.accessibility.verification",
            "diagnostics.accessibility.open_system_settings",
        ),
        onboarding_steps=(
            "permissions", "hotkey", "models", "first_dictation"),
        key_equivalents=(
            "return:continue-setup",
            "command-d:advanced",
            "command-r:verification",
            *(f"command-{index + 1}:{row.key}"
              for index, row in enumerate(SIDEBAR_ROWS)),
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
    # Key binding lives in the runtime: it owns the preference file and the
    # listener. The window captures a key, asks the runtime to describe it,
    # and shows whatever verdict comes back — there is no second copy of the
    # guard rules here.
    describe_hotkey: Callable[..., Mapping[str, Any]] = (
        lambda _name, **_kwargs: {
            "accepted": False, "reason": "unsupported_key",
            "name": "", "label": "", "shared_modes": ()})
    set_hotkey: Callable[[str], Mapping[str, Any]] = (
        lambda _name: {"accepted": False, "reason": "unsupported_key",
                       "name": "", "label": "", "shared_modes": ()})
    set_undo_hotkey: Callable[[str], Mapping[str, Any]] = (
        lambda _name: {"accepted": False, "reason": "unsupported_key",
                       "name": "", "label": "", "shared_modes": ()})
    set_sound_theme: Callable[[str], str] = lambda _name: "system"
    set_language: Callable[[str], str] = lambda _code: LANGUAGE_DEFAULT
    preview_sound: Callable[[str], bool] = lambda _cue: False
    set_recent_dictations: Callable[[bool], bool] = lambda _enabled: False
    set_self_correction: Callable[[bool], bool] = lambda _enabled: False
    set_remove_repeats: Callable[[bool], bool] = lambda _enabled: True
    set_new_speech_runtime: Callable[[bool], bool] = lambda _enabled: False
    recent_dictations: Callable[[], Sequence[Mapping[str, Any]]] = lambda: ()
    reveal_recent_dictation: Callable[[str], str | None] = (
        lambda _entry_id: None)
    insert_recent_dictation: Callable[[str], Mapping[str, Any]] = (
        lambda _entry_id: {"inserted": False, "reason": "unavailable"})
    undo_last_dictation: Callable[[], Mapping[str, Any]] = (
        lambda: {"undone": False, "reason": "nothing_to_undo"})
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
    open_system_settings: Callable[[str], None] = _noop
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
    # Machine fields from the runtime; display copy above is derived from
    # them, never the other way round.
    install_state: str = "unknown"
    required: bool = False
    warm: bool = False


MODEL_INSTALL_STATES = (
    "installed", "not_installed", "failed", "mismatch", "checking", "unknown")


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
    self_correction: bool = False
    remove_repeats: bool = True


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
    # How many of ``outbox_count`` are copies of a paste that was sent and
    # could not be read back. They are kept and counted and are not a
    # problem; only the rest still need a home. Zero unless the runtime
    # says otherwise, so an entry is never taken for one by default.
    outbox_unchecked: int = 0
    outbox_summary: str = ""
    regression_cases: int = 0
    regression_quarantined: int = 0
    privacy_summary: str = localized_string("default.privacy.local")
    service_status: str = localized_string("default.status.unknown")
    microphone_status: str = localized_string("default.status.unknown")
    accessibility_status: str = localized_string("default.status.unknown")
    input_monitoring_status: str = localized_string("default.status.unknown")
    # Permissions the runtime reports as missing, in the order to fix them.
    missing_permissions: tuple[str, ...] = field(default_factory=tuple)
    version: str = localized_string("default.build.development")
    models: tuple[ModelStatus, ...] = field(default_factory=tuple)
    model_wallet_advisory: str = localized_string(
        "models.wallet.unavailable")
    hotkey_label: str = localized_string("settings.hotkey.default")
    hotkey: str = "alt_r"
    hotkey_shared_modes: tuple[str, ...] = field(default_factory=tuple)
    undo_hotkey: str = ""
    undo_hotkey_label: str = ""
    undo_available: bool = False
    sound_theme: str = "system"
    language: str = LANGUAGE_DEFAULT
    recent_dictations: bool = False
    # Whisper Face's own speech runtime: whether it is asked first, and which
    # program ran the model for the last dictation ("new", "previous", or ""
    # when that is not known).
    new_speech_runtime: bool = False
    new_speech_runtime_last: str = ""
    # Switched on and still loading: the previous program answers meanwhile.
    new_speech_runtime_preparing: bool = False
    prefers_reduced_motion: bool = False
    onboarding_steps: tuple[OnboardingStep, ...] = field(default_factory=tuple)
    onboarding_complete: bool = False
    onboarding_acknowledged: bool = False
    status_phase: str = "ready"
    status_title: str = localized_string("overview.status.ready.title")
    status_detail: str = localized_string(
        "overview.status.ready.detail", hotkey=localized_string(
            "settings.hotkey.default"))
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


def _support_permission(value: str) -> str:
    """Granted, unavailable (missing) or unknown, from the runtime's words."""
    normalized = value.strip().casefold()
    return {"granted": "granted", "needs attention": "unavailable"}.get(
        normalized, "unknown")


def _support_model_status(model: ModelStatus) -> str:
    """Map the runtime's install fields onto the bundle's closed vocabulary."""
    if model.install_state == "installed":
        return "ready" if model.warm else "installed"
    if model.install_state == "checking":
        return "checking"
    if model.install_state in MODEL_BROKEN_STATES:
        return "unavailable"
    return _support_status(model.status)


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


def hotkey_name_for_keycode(keycode: object) -> str:
    """Canonical name for a captured macOS key, or "" when it is unbindable.

    Returning "" is the guard: an ordinary letter, digit, space, or return
    never becomes a shortcut, because holding one of those to dictate would
    swallow typing in every application.
    """
    try:
        code = int(keycode)
    except (TypeError, ValueError):
        return ""
    return HOTKEY_KEYCODES.get(code, "")


def hotkey_row_detail(state: GUIState, *, locale: str = "en") -> str:
    """One line describing the capture key, including any mode it shares.

    The six voice modes are modifier + capture key. Binding a Shift, Command,
    or Control key means the modes that need that modifier now need the key on
    the other side of the keyboard. That is a real consequence, so the picker
    states it rather than letting a mode quietly stop working.
    """
    shared = tuple(
        mode for mode in state.hotkey_shared_modes if mode in MODE_GUIDE)
    if not shared:
        return localized_string(
            "settings.controls.hotkey.detail", locale=locale,
            hotkey=state.hotkey_label)
    family = HOTKEY_MODIFIER_FAMILIES.get(state.hotkey, "")
    return localized_string(
        "settings.controls.hotkey.shared", locale=locale,
        hotkey=state.hotkey_label,
        modes=", ".join(
            localized_string(f"settings.mode.{mode}.name", locale=locale)
            for mode in shared),
        family=localized_string(
            f"settings.modifier.{family}", locale=locale) if family else "")


def hotkey_shared_mode_note(shared: Sequence[str], name: str, *,
                            locale: str = "en") -> str:
    """The same warning, phrased for inside the picker dialog."""
    modes = tuple(mode for mode in shared if mode in MODE_GUIDE)
    family = HOTKEY_MODIFIER_FAMILIES.get(name, "")
    if not modes or not family:
        return ""
    return localized_string(
        "settings.dialog.hotkey.shared", locale=locale,
        modes=", ".join(
            localized_string(f"settings.mode.{mode}.name", locale=locale)
            for mode in modes),
        family=localized_string(f"settings.modifier.{family}", locale=locale))


def recent_dictation_row(entry: Mapping[str, Any], *,
                         locale: str = "en") -> str:
    """Render one browser row from metadata alone — never from its text."""
    words = entry.get("words")
    words = words if isinstance(words, int) and not isinstance(words, bool) \
        else 0
    words = max(0, words)
    seconds = entry.get("age_seconds")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 0.0
    if not math.isfinite(seconds) or seconds < 60:
        when = localized_string("recent.age.now", locale=locale)
    elif seconds < 3600:
        when = localized_string(
            "recent.age.minutes", locale=locale, count=int(seconds // 60))
    elif seconds < 86400:
        when = localized_string(
            "recent.age.hours", locale=locale, count=int(seconds // 3600))
    else:
        when = localized_string(
            "recent.age.days", locale=locale, count=int(seconds // 86400))
    app = entry.get("app")
    return localized_string(
        "settings.dialog.recent.row", locale=locale, when=when,
        words=(localized_string("settings.dialog.recent.word", locale=locale)
               if words == 1 else localized_string(
                   "settings.dialog.recent.words", locale=locale,
                   count=words)),
        app=str(app) if isinstance(app, str) and app else localized_string(
            "recent.app.unknown", locale=locale))


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
            "accessibility_status": _support_permission(
                state.accessibility_status),
        },
        "build": (
            "local-checkout"
            if state.version.strip().casefold() == "local checkout"
            else "unknown"),
        "models": [
            {
                "family": _support_model_family(model.name),
                "status": _support_model_status(model),
            }
            for model in sorted(
                state.models,
                key=lambda model: (
                    _support_model_family(model.name),
                    _support_model_status(model),
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
            install_state = str(item.get("install_state", "unknown"))
            if install_state not in MODEL_INSTALL_STATES:
                install_state = "unknown"
            required = item.get("required") is True
            warm = install_state == "installed" and item.get("warm") is True
            if install_state == "unknown":
                status = _clean_text(item.get("status"), localized_string(
                    "default.status.unknown", locale=locale))
                detail = _clean_text(item.get("detail"), "")
            else:
                state_key = (
                    "ready" if warm
                    else "optional" if (
                        install_state == "not_installed" and not required)
                    else install_state)
                status = localized_string(
                    f"models.state.{state_key}", locale=locale)
                detail = localized_string(
                    f"models.detail.{state_key}", locale=locale)
            models.append(ModelStatus(
                name=name,
                role=_clean_text(item.get("role"), ""),
                status=status,
                detail=detail,
                install_state=install_state,
                required=required,
                warm=warm,
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
        self_correction=source.get("self_correction") is True,
        remove_repeats=source.get("remove_repeats") is not False,
    )


def _correction_standing(
        correction: CorrectionSetting) -> tuple[str, list[str]]:
    """Where a learned mapping stands, and the apps it is active in.

    ``snippet`` for a snippet edit; otherwise ``global`` when it is active
    everywhere, ``apps`` when it is active in the apps returned, ``held``
    when the local correction cases disagree, and ``learning``.
    """
    if correction.kind == "snippet":
        return "snippet", []
    active_apps = [
        scope.name for scope in correction.app_scopes
        if scope.decision == "active"
    ]
    if correction.global_decision == "active":
        return "global", active_apps
    if active_apps:
        return "apps", active_apps
    held_back = (
        correction.global_decision == "held_back"
        or any(scope.decision == "held_back"
               for scope in correction.app_scopes)
    )
    return ("held" if held_back else "learning"), []


def correction_row_caption(
    correction: CorrectionSetting,
    *,
    locale: str = "en",
) -> str:
    """One learned correction in a row: where it stands, in plain words,
    and under that the apps it was seen in when any are known."""

    standing, active_apps = _correction_standing(correction)
    lines = [localized_string(
        f"sheets.corrections.standing.{standing}", locale=locale,
        **({"apps": ", ".join(active_apps)} if standing == "apps" else {}))]
    seen = ", ".join(
        scope.name + " " + localized_string(
            "sheets.corrections.count", locale=locale, count=scope.count)
        for scope in correction.app_scopes)
    if seen:
        lines.append(localized_string(
            "sheets.corrections.seen", locale=locale, apps=seen))
    return "\n".join(lines)


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
                plural_key("settings.dialog.correction.why.snippet",
                           correction.count),
                locale=locale,
                count=correction.count,
            ),
            localized_string(
                "settings.dialog.correction.privacy.snippet", locale=locale),
        ))

    standing, active_apps = _correction_standing(correction)
    if standing == "global":
        scope_line = localized_string(
            "settings.dialog.correction.scope.global", locale=locale)
        why_line = localized_string(
            "settings.dialog.correction.why.global",
            locale=locale,
            count=correction.count,
        )
    elif standing == "apps":
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
            if standing == "held" else
            "settings.dialog.correction.why.learning",
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


MODEL_BROKEN_STATES = frozenset({"not_installed", "failed", "mismatch"})


def _models_readiness(models: Sequence[ModelStatus]) -> str:
    """Return ready, preparing or missing for the models dictation needs."""
    required = [model for model in models if model.required]
    if any(model.install_state in MODEL_BROKEN_STATES for model in required):
        return "missing"
    if required and all(
            model.install_state == "installed" for model in required):
        return "ready"
    return "preparing"


def model_pill_tone(model: ModelStatus) -> str:
    """Green for installed, quiet for a choice or a check, amber for faults."""
    if model.install_state == "installed":
        return "good"
    if model.install_state == "checking" or (
            model.install_state == "not_installed" and not model.required):
        return "neutral"
    return "warn"


# What the settings pages show for a state, in words. The runtime keeps its
# own vocabulary (RESOLVED, evidence reasons, shadow advisories) and the
# tests that pin it; these map it to a plain phrase and a tone at the last
# moment, so nothing internal is ever the visible label. A warning stays a
# warning: its tone draws a warning symbol beside the phrase.
STATUS_TONES = ("good", "neutral", "warn")
INTERNAL_STATUS_WORDS = (
    "RESOLVED", "UNAVAILABLE", "attested", "shadow", "advisory",
    "consequential", "spans", "evidence", "Regression", "mapping",
    "pinned", "quarantined", "verifier", "receipt", "wallet",
)
# The receipt's status token, which counts only as the runtime writes it, in
# capitals. "Unavailable" is also the plain word the microphone's health
# reports, and a Mac with no microphone shows it on Advanced.
INTERNAL_STATUS_TOKENS = ("UNAVAILABLE",)


def internal_words_in(text: str) -> list[str]:
    """The runtime's own words that ``text`` shows, in any case except the
    tokens, which are matched exactly."""
    folded = text.casefold()
    return [word for word in INTERNAL_STATUS_WORDS
            if (word in text if word in INTERNAL_STATUS_TOKENS
                else word.casefold() in folded)]


def model_status_display(
        model: ModelStatus, *, locale: str = "en") -> tuple[str, str]:
    """The phrase and tone a model row shows for its state.

    A known install state already arrives as catalog copy. Only a row
    whose state the runtime did not name carries the runtime's own status
    word, and that word is mapped rather than shown.
    """
    if model.install_state != "unknown":
        return model.status, model_pill_tone(model)
    word = model.status.strip().casefold()
    if word in {"resolved", "installed", "ready"}:
        return localized_string("models.state.installed", locale=locale), \
            "good"
    if word in {"unavailable", "missing", "not installed", "failed"}:
        return localized_string(
            "pages.models.status.unavailable", locale=locale), "warn"
    return localized_string("models.unknown", locale=locale), "neutral"


def model_detail_display(model: ModelStatus) -> str:
    """The caption under a model's name: its role, then what to know.

    The detail of a row whose state the runtime did not name is the
    runtime's own receipt wording, so only the role is shown for it.
    """
    if model.install_state == "unknown":
        return model.role
    return " · ".join(part for part in (model.role, model.detail) if part)


def model_checks_note(advisory: str, *, locale: str = "en") -> str:
    """The note under Models on Advanced, for the runtime's model checks.

    ``advisory`` is the sentence the runtime's receipt was reduced to
    (``GUIState.model_wallet_advisory``), which keeps its own wording for
    the tests and the support snapshot. The page says what it found in
    plain words: how many of the models' files are on this Mac and how
    many have warmed up, or that the checks are not available yet. A
    sentence that is neither claims nothing: the checks are for
    information only.
    """
    total = len(MODEL_WALLET_PROVIDER_IDS)
    for resolved in range(total + 1):
        for warm in range(total + 1):
            if advisory == localized_string(
                    "models.wallet.evidence", locale=locale,
                    resolved=resolved, warm=warm):
                return localized_string(
                    "pages.models.advisory.evidence", locale=locale,
                    found=resolved, warm=warm, total=total)
    if advisory == localized_string(
            "models.wallet.unavailable", locale=locale):
        return localized_string(
            "pages.models.advisory.unavailable", locale=locale)
    return localized_string("pages.models.advisory", locale=locale)


def speech_runtime_caption(enabled: bool, last: str, *,
                           preparing: bool = False,
                           locale: str = "en") -> str:
    """The New speech runtime row's caption, in catalog words only.

    What the switch does, then, switched on and still loading, that it is
    getting ready, or else which program answered the last dictation when
    that is known. Nothing the runtime reports is shown as text.
    """
    caption = localized_string(
        "models.speech_runtime.on" if enabled
        else "models.speech_runtime.off", locale=locale)
    if enabled and preparing:
        return localized_string(
            "models.speech_runtime.preparing", locale=locale, caption=caption)
    if last not in {"new", "previous"}:
        return caption
    return localized_string(
        f"models.speech_runtime.last.{last}", locale=locale, caption=caption)


def relisten_status_display(status: str, *, locale: str = "en") -> str:
    """Selective Re-listen's state as a plain phrase.

    Anything but the four running states is a reason it cannot turn on yet
    (missing or invalid evidence from this Mac), which reads the same way.
    """
    key = {
        "off": "off", "ready": "ready", "warming": "warming",
        "enabled-not-ready": "starting",
    }.get(status, "unavailable")
    return localized_string(
        "pages.relisten.detail", locale=locale,
        status=localized_string(f"pages.relisten.status.{key}",
                                locale=locale))


def voice_inbox_status_display(
        status: str, count: int, *, locale: str = "en") -> str:
    """Voice Object Commands' state and its waiting drafts, in words."""
    word = status.strip().casefold()
    if word not in {"off", "ready", "unavailable"}:
        word = "unavailable"
    return localized_string(
        plural_key("pages.privacy.voice_objects.status", count),
        locale=locale, count=count,
        status=localized_string(
            f"pages.privacy.voice_objects.{word}", locale=locale))


def system_status_tone(value: str) -> str:
    """Good, neutral or warn for one of the runtime's health words."""
    word = value.strip().casefold()
    if word in {"running", "ready", "granted"}:
        return "good"
    if word in {"unavailable", "needs attention", "stopped", "not running",
                "denied", "failed"}:
        return "warn"
    return "neutral"


PERMISSIONS = ("microphone", "accessibility", "input_monitoring")


def _permission_states(
    *, microphone_status: str, accessibility_status: str,
    input_monitoring_status: str,
) -> dict[str, str]:
    """Read the runtime's exact permission words as granted/missing/checking.

    The microphone reports Ready, Starting or Unavailable; the two event
    permissions report Granted, Needs attention or Unknown. Anything else is
    not evidence either way.
    """
    microphone = microphone_status.strip().casefold()
    states = {
        "microphone": (
            "granted" if microphone == "ready"
            else "missing" if microphone == "unavailable" else "checking"),
    }
    for key, value in (("accessibility", accessibility_status),
                       ("input_monitoring", input_monitoring_status)):
        normalized = value.strip().casefold()
        states[key] = (
            "granted" if normalized == "granted"
            else "missing" if normalized == "needs attention"
            else "checking")
    return states


def _build_onboarding_steps(
    *,
    permissions: Mapping[str, str],
    hotkey_label: str,
    hotkey_practiced: bool,
    models: Sequence[ModelStatus],
    first_dictation_complete: bool,
    locale: str = "en",
) -> tuple[OnboardingStep, ...]:
    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    permissions_ready = all(
        permissions.get(key) == "granted" for key in PERMISSIONS)
    permissions_missing = any(
        permissions.get(key) == "missing" for key in PERMISSIONS)
    model_readiness = _models_readiness(models)
    model_ready = model_readiness == "ready"
    return (
        OnboardingStep(
            "permissions", copy("onboarding.permissions.title"),
            copy("onboarding.permissions.detail"),
            copy("onboarding.status.done") if permissions_ready
            else copy("onboarding.status.attention") if permissions_missing
            else copy("onboarding.status.checking"),
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
            copy("onboarding.status.done") if model_ready
            else copy("onboarding.status.attention")
            if model_readiness == "missing"
            else copy("onboarding.status.warming"),
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
    permission_missing: bool = True,
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
    # While permissions are still being checked there is nothing to grant.
    open_settings = next_step.key == "permissions" and permission_missing
    action_key = {
        "permissions": ("onboarding.action.open_system_settings"
                        if open_settings else "onboarding.action.continue"),
        "hotkey": "onboarding.action.hotkey",
        "models": "onboarding.action.models",
        "first_dictation": "onboarding.action.first_dictation",
    }.get(next_step.key, "onboarding.action.continue")
    help_key = (
        "onboarding.action.open_system_settings.help"
        if open_settings
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


@dataclass(frozen=True)
class SetupRow:
    """One row of what the current first-run step is waiting on."""

    key: str
    symbol: str
    title: str
    caption: str = ""
    # The words at the row's trailing edge, and how they read: ``good``
    # carries a check mark, ``attention`` and ``quiet`` are words alone.
    status: str = ""
    tone: str = "quiet"
    # Set when the row offers its own button to that permission's pane.
    permission: str = ""
    # Set on the one row whose button is the step's primary action.
    primary: bool = False
    # A key shown as a key cap beside the status.
    key_cap: str = ""


SETUP_ROW_SYMBOLS: Mapping[str, str] = {
    "microphone": "mic.fill",
    "accessibility": "accessibility",
    "input_monitoring": "keyboard",
    "permissions": "hand.raised.fill",
    "hotkey": "keyboard",
    "models": "cpu",
    "first_dictation": "text.cursor",
}
# The most rows a step shows; the window builds exactly this many.
SETUP_ROW_LIMIT = 4


def setup_rows(state: GUIState, *, locale: str = "en") -> tuple[SetupRow, ...]:
    """The rows first-run setup shows for the step it is on.

    Derived from the same evidence as the steps themselves: one row for
    each permission with the state the runtime reports for it, the capture
    key that is actually bound, the models the runtime listed, and, once
    every step is confirmed, the four steps with their check marks. A
    permission the runtime reports as missing is the only row that carries
    a button; one still being checked has nothing to grant yet.

    The first missing permission's button is the step's primary action:
    it opens the pane the step's single button always opened, so it is
    the amber one and answers Return, and no second button repeats it.
    """

    def copy(key: str, **values: Any) -> str:
        return localized_string(key, locale=locale, **values)

    def step_row(step: OnboardingStep, *, caption: str = "",
                 key_cap: str = "") -> SetupRow:
        # A step that is the only row says how it stands in the header
        # over the rows, not a second time at the end of its own row.
        return SetupRow(
            step.key, SETUP_ROW_SYMBOLS.get(step.key, ""),
            copy(f"onboarding.step.{step.key}"), caption,
            step.status if step.complete else "",
            "good" if step.complete else "quiet", key_cap=key_cap)

    steps = state.onboarding_steps
    current = next((step for step in steps if not step.complete), None)
    if current is None:
        return tuple(
            step_row(step, key_cap=(
                state.hotkey_label if step.key == "hotkey" else ""))
            for step in steps)[:SETUP_ROW_LIMIT]
    if current.key == "permissions":
        states = _permission_states(
            microphone_status=state.microphone_status,
            accessibility_status=state.accessibility_status,
            input_monitoring_status=state.input_monitoring_status)
        rows = []
        first_missing = next(
            (key for key in PERMISSIONS if states[key] == "missing"), "")
        for key in PERMISSIONS:
            granted = states[key] == "granted"
            missing = states[key] == "missing"
            rows.append(SetupRow(
                key, SETUP_ROW_SYMBOLS[key],
                copy(f"onboarding.permission.{key}"),
                copy(f"onboarding.permission.{key}.why"),
                "" if missing else copy(
                    "onboarding.permission.granted" if granted
                    else "onboarding.permission.checking"),
                "good" if granted else "attention" if missing else "quiet",
                permission=key if missing else "",
                primary=key == first_missing))
        return tuple(rows)
    if current.key == "hotkey":
        return (step_row(
            current, caption=copy("onboarding.row.hotkey.caption"),
            key_cap=state.hotkey_label),)
    if current.key == "models":
        # Required models first: they are what the step is waiting on.
        models = sorted(state.models, key=lambda model: not model.required)
        if not models:
            return (step_row(
                current, caption=copy("onboarding.row.models.caption")),)
        return tuple(
            # Keyed by the model, not by where it sits in the list, so a
            # reordered list never reads as a row that just turned good.
            SetupRow(
                f"model:{model.name}", SETUP_ROW_SYMBOLS["models"],
                model.name, model.role, model.status,
                {"good": "good", "warn": "attention"}.get(
                    model_pill_tone(model), "quiet"))
            for model in models[:SETUP_ROW_LIMIT])
    return (step_row(
        current, caption=copy("onboarding.row.first_dictation.caption"),
        key_cap=state.hotkey_label),)


def setup_waits_on_a_row(rows: Sequence[SetupRow]) -> bool:
    """Whether the step's primary action is in one of its rows.

    While it is, the foot's Continue has nothing to continue to: it shows
    disabled until the runtime stops reporting a permission as missing.
    """
    return any(row.primary for row in rows)


def setup_rows_turned_good(
    previous: tuple[str, frozenset[str]] | None,
    stage: str,
    good: frozenset[str],
    newly_completed: frozenset[str],
) -> frozenset[str]:
    """Which setup rows earn a pop on this render.

    ``previous`` is the stage and the good rows of the last render. A row
    pops when it turns good while its step is still showing (a permission
    being granted). When the stage itself changes every row is new, so
    nothing pops, except on the completion step, where the one step that
    was just confirmed does.
    """
    if previous is None:
        return frozenset()
    previous_stage, previous_good = previous
    if stage == previous_stage:
        return good - previous_good
    if stage == "complete":
        return good & newly_completed
    return frozenset()


def home_dictation_landed(
    previous: tuple[Any, ...] | None,
    landed: tuple[Any, ...],
    *,
    available: bool,
    section: str,
) -> bool:
    """Whether Home's one signature reaction is due on this render.

    ``landed`` is what a dictation leaves behind (its latency, its words,
    the day's count, its summary). The face reacts only when that changed
    since the last render, a result is there to show, and Home is the page
    in front: never on the first render, and never on a refresh that
    changed nothing.
    """
    return (previous is not None and previous != landed
            and bool(available) and section == "Home")


def home_review_due(state: GUIState) -> bool:
    """Whether Home offers Review Setup: something is degraded, or setup
    was never finished or acknowledged."""
    return bool(state.degraded_issues) or not (
        state.onboarding_complete or state.onboarding_acknowledged)


def home_bubble_actions(state: GUIState) -> tuple[str, ...]:
    """Which buttons the bubble on Home carries, in order.

    The bubble holds the primary action and at most one secondary. Pause
    (or Resume) is always the primary. The secondary answers what the
    bubble is saying: Copy & Dismiss while the Voice Outbox holds text,
    otherwise Review Setup when a review is due. When both are due the
    review moves to its own row under the stage (``home_review_in_row``),
    so no action is ever out of reach.
    """
    if state.outbox_count:
        return ("pause", "copy_outbox")
    if home_review_due(state):
        return ("pause", "review")
    return ("pause",)


def home_review_in_row(state: GUIState) -> bool:
    """Whether Review Setup shows as a row because the bubble's one
    secondary place is taken by Copy & Dismiss."""
    return bool(state.outbox_count) and home_review_due(state)


def home_outbox_line(state: GUIState) -> tuple[str, dict[str, Any]]:
    """Catalog key and values for Home's Voice Outbox row.

    An empty outbox says so. One that holds only copies of pastes that
    could not be read back counts them for what they are. Anything that
    still needs a home keeps the recoverable count and its summary, and
    when both kinds are there each is counted as what it is, so the row
    and the headline agree on how many dictations need a review.
    """
    if not state.outbox_count:
        return "overview.outbox.empty", {}
    unchecked = min(max(0, state.outbox_unchecked), state.outbox_count)
    if unchecked >= state.outbox_count:
        return "overview.outbox.unchecked", {"count": state.outbox_count}
    if unchecked:
        return "overview.outbox.mixed", {
            "count": state.outbox_count - unchecked, "unchecked": unchecked,
            "summary": state.outbox_summary}
    return "overview.outbox.pending", {
        "count": state.outbox_count, "summary": state.outbox_summary}


# The face picker's grid: eight chips a row, the spec's gap between them,
# each chip as large as the group's width then allows.
FACE_GRID_COLUMNS = 8
FACE_CHIP_GAP = 10.0
FACE_CHIP_MAX = 78.0
FACE_CHIP_MIN = 40.0


def face_grid(width: float, count: int) -> tuple[int, float, float, int]:
    """Columns, chip side, left margin and rows for ``count`` face chips
    in a grid ``width`` points wide.

    Chips sit ``FACE_CHIP_GAP`` apart, eight to a row, and take the width
    between them: as large as it allows, in whole points so they draw
    crisp at any scale, and never more than ``FACE_CHIP_MAX``. What the
    whole points leave over (under eight points) is split evenly, so the
    grid is centred in its width. A width too narrow for eight chips of
    ``FACE_CHIP_MIN`` takes fewer to a row instead of smaller chips.
    """
    # One row until the chips would crowd. Sixteen at the 40pt floor need
    # 790pt with their gaps, more than the widest column, so the roster
    # wraps at eight a row and the chips grow into the room that leaves.
    count = max(1, int(count))
    columns = min(FACE_GRID_COLUMNS, count)

    def side(columns: int) -> float:
        return float(math.floor(min(FACE_CHIP_MAX, (
            width - (columns - 1) * FACE_CHIP_GAP) / columns)))

    while columns > 1 and side(columns) < FACE_CHIP_MIN:
        columns -= 1
    chip = max(1.0, side(columns))
    used = columns * chip + (columns - 1) * FACE_CHIP_GAP
    margin = float(max(0.0, math.floor((width - used) / 2.0)))
    return columns, chip, margin, -(-count // columns)


def _build_degraded_issues(
    *,
    service_status: str,
    permissions: Mapping[str, str],
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
    for key in PERMISSIONS:
        if permissions.get(key) == "missing":
            issues.append(DegradedIssue(
                key, copy(f"issue.{key}.title"),
                copy(f"issue.{key}.detail")))
    missing = [
        model.name for model in models
        if model.required and model.install_state in MODEL_BROKEN_STATES]
    if missing:
        issues.append(DegradedIssue(
            "models", copy("issue.models.title"),
            copy("issue.models.detail", models=", ".join(missing)),
        ))
    else:
        # An optional model that was never installed is a choice, not a
        # fault; only a damaged optional install is worth a warning.
        unavailable = [
            model.name for model in models
            if not model.required
            and model.install_state in {"failed", "mismatch"}]
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
                copy(plural_key("results.value.words",
                                result.stable_prefix_words),
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
            plural_key("results.summary.timed", word_count or 0),
            words=word_count or 0,
            seconds=f"{latency_ms / 1000:.2f}")
        available = True
    elif available:
        summary = copy(
            plural_key("results.summary.words", word_count),
            words=word_count)
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
    models_preparing: bool = False,
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
    if models_preparing or _status_contains(
            service_status, ("starting", "warming", "unknown")):
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
            "overview.outbox.summary.pasted_unchecked",
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
    # Entries that still need a home. A runtime that does not say counts
    # every entry as one, which is the careful reading.
    outbox_needs_home = outbox_count
    reported_needs_home = source.get("outbox_needs_home")
    if isinstance(reported_needs_home, int) \
            and not isinstance(reported_needs_home, bool):
        outbox_needs_home = min(outbox_count, max(0, reported_needs_home))
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
    input_monitoring_status = _clean_text(
        source.get("input_monitoring_status"), unknown_status)
    permissions = _permission_states(
        microphone_status=microphone_status,
        accessibility_status=accessibility_status,
        input_monitoring_status=input_monitoring_status)
    models = _normalize_models(source.get("models"), locale=locale)
    model_wallet_advisory = _model_wallet_advisory_copy(
        source.get("model_wallet_shadow"), locale=locale)
    hotkey_label = _clean_text(
        source.get("hotkey_label"), localized_string(
            "settings.hotkey.default", locale=locale))
    successful_dictation = (
        source.get("first_dictation_complete") is True
        or (last_word_count is not None and last_word_count > 0))
    hotkey_practiced = (
        source.get("hotkey_practiced") is True
        or successful_dictation
        or _status_contains(capture_state, ("listen", "record", "captur")))
    onboarding_steps = _build_onboarding_steps(
        permissions=permissions,
        hotkey_label=hotkey_label,
        hotkey_practiced=hotkey_practiced,
        models=models,
        first_dictation_complete=successful_dictation,
        locale=locale,
    )
    degraded_issues = _build_degraded_issues(
        service_status=service_status,
        permissions=permissions,
        models=models,
        locale=locale,
    )
    phase, status_title, status_detail = _status_presentation(
        capture_state=capture_state,
        paused=paused,
        hotkey_label=hotkey_label,
        # Only what still needs a home asks for a review. Copies of pastes
        # that could not be read back leave the phase as it would be.
        outbox_count=outbox_needs_home,
        service_status=service_status,
        degraded_issues=degraded_issues,
        models_preparing=(
            bool(models) and _models_readiness(models) == "preparing"),
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
        outbox_unchecked=outbox_count - outbox_needs_home,
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
        input_monitoring_status=input_monitoring_status,
        missing_permissions=tuple(
            key for key in PERMISSIONS if permissions[key] == "missing"),
        version=_clean_text(
            source.get("version"), localized_string(
                "default.build.development", locale=locale)),
        models=models,
        model_wallet_advisory=model_wallet_advisory,
        hotkey_label=hotkey_label,
        hotkey=_clean_text(source.get("hotkey"), "alt_r"),
        # Modes whose modifier the bound capture key also presses. Surfaced
        # so the picker can say so instead of a mode quietly not working.
        hotkey_shared_modes=tuple(
            str(mode) for mode in (source.get("hotkey_shared_modes") or ())
            if str(mode) in MODE_GUIDE),
        undo_hotkey=_clean_text(source.get("undo_hotkey"), ""),
        undo_hotkey_label=_clean_text(source.get("undo_hotkey_label"), ""),
        undo_available=source.get("undo_available") is True,
        sound_theme=(_clean_text(source.get("sounds"), "system")
                     if _clean_text(source.get("sounds"), "system")
                     in SOUND_THEMES else "system"),
        language=(_clean_text(source.get("language"), LANGUAGE_DEFAULT)
                  if _clean_text(source.get("language"), LANGUAGE_DEFAULT)
                  in LANGUAGES else LANGUAGE_DEFAULT),
        recent_dictations=source.get("recent_dictations") is True,
        new_speech_runtime=source.get("new_speech_runtime") is True,
        new_speech_runtime_last=(
            source.get("new_speech_runtime_last")
            if source.get("new_speech_runtime_last") in {"new", "previous"}
            else ""),
        new_speech_runtime_preparing=(
            source.get("new_speech_runtime_preparing") is True),
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


# How long a success or info notice outlives the status refresh. Errors have
# no lifetime: they stay until the next user action replaces them.
NOTICE_LIFETIME_SECONDS = 8.0


class WhisperFaceViewModel:
    """Pure state/actions seam between the runtime and the native window."""

    def __init__(self, actions: GUIActions, *, locale: str = "en",
                 clock: Callable[[], float] = time.monotonic):
        self.actions = actions
        self.locale = resolve_locale(locale)
        self._clock = clock
        self._notice_issued_at = clock()
        self._carrying_notice = False
        self._status_error_notice = ""
        self._issue_notice = ("", "")
        self.state = GUIState()
        self._onboarding_acknowledged = False
        self._hotkey_practiced = False
        self._inspected_voice_draft_ids: set[str] = set()
        self._revealed_voice_draft_ids: set[str] = set()
        self._voice_draft_clipboard_clear_available = False
        self._inspected_demonstration_ids: set[str] = set()
        self._revealed_demonstration_ids: set[str] = set()
        self.refresh()

    @property
    def state(self) -> GUIState:
        return self._state

    @state.setter
    def state(self, value: GUIState) -> None:
        # Every write goes through here, so a notice is stamped wherever it
        # was set, including by the window controller. Posting the same text
        # again stamps it again; only _carry keeps the old stamp.
        previous = self.__dict__.get("_state")
        if previous is None or (value.notice, value.notice_level) != (
                previous.notice, previous.notice_level) or (
                    value.notice and not self._carrying_notice):
            self._notice_issued_at = self._clock()
        self._state = value

    def _carry(self, value: GUIState) -> None:
        """Write state that keeps the current notice without reissuing it."""
        self._carrying_notice = True
        try:
            self.state = value
        finally:
            self._carrying_notice = False

    def _surviving_notice(self) -> tuple[str, str]:
        notice, level = self.state.notice, self.state.notice_level
        if not notice or notice == self._status_error_notice:
            return "", "info"
        if (level != "error" and self._clock() - self._notice_issued_at
                >= NOTICE_LIFETIME_SECONDS):
            return "", "info"
        return notice, level

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
            notice, notice_level = self._surviving_notice()
            self._carry(normalize_snapshot(
                snapshot,
                section=self.state.section,
                verification=self.state.verification,
                notice=notice,
                notice_level=notice_level,
                onboarding_acknowledged=self._onboarding_acknowledged,
                settings_pane=self.state.settings_pane,
                settings=self.state.settings,
                locale=self.locale,
            ))
            self._status_error_notice = ""
            # An issue's own notice is only true while that issue is reported.
            issue_key, issue_detail = self._issue_notice
            if issue_detail and self.state.notice == issue_detail and (
                    issue_key not in {
                        issue.key for issue in self.state.degraded_issues}):
                self.state = replace(self.state, notice="", notice_level="info")
                self._issue_notice = ("", "")
        except Exception as error:
            self._status_error_notice = self.localized(
                "overview.notice.status.error", error=error)
            self.state = replace(
                self.state, notice=self._status_error_notice,
                notice_level="error")
        return self.state

    def acknowledge_onboarding(self, acknowledged: bool = True) -> GUIState:
        """Remember that a completed first run need not be shown again."""
        self._onboarding_acknowledged = bool(acknowledged)
        self._carry(replace(
            self.state,
            onboarding_acknowledged=self._onboarding_acknowledged,
        ))
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
        self._issue_notice = (issue.key, issue.detail)
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

    def describe_hotkey(self, name: str, *,
                        allow_unbound: bool = False) -> Mapping[str, Any]:
        """Ask the runtime what a captured key means. Never decides itself."""
        try:
            verdict = self.actions.describe_hotkey(
                name, allow_unbound=allow_unbound)
        except Exception:
            verdict = None
        if not isinstance(verdict, Mapping):
            return {"accepted": False, "reason": "unsupported_key",
                    "name": "", "label": "", "shared_modes": ()}
        return verdict

    def choose_hotkey(self, name: str) -> GUIState:
        """Bind the capture key, or report exactly why the key was refused."""
        try:
            verdict = self.actions.set_hotkey(str(name))
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
            return self.state
        if not isinstance(verdict, Mapping) or not verdict.get("accepted"):
            self.state = replace(
                self.state,
                notice=self.localized("settings.notice.hotkey.rejected"),
                notice_level="error")
            return self.state
        label = str(verdict.get("label") or "")
        # Re-read the runtime first, then state the outcome: refresh rebuilds
        # state from the snapshot and would otherwise drop the notice.
        self.refresh()
        self.state = replace(
            self.state,
            hotkey=str(verdict.get("name") or self.state.hotkey),
            hotkey_label=label or self.state.hotkey_label,
            hotkey_shared_modes=tuple(
                str(mode) for mode in (verdict.get("shared_modes") or ())),
            notice=self.localized("settings.notice.hotkey", hotkey=label),
            notice_level="success")
        return self.state

    def choose_undo_hotkey(self, name: str) -> GUIState:
        """Bind (or clear) the undo key through the same guard rules."""
        try:
            verdict = self.actions.set_undo_hotkey(str(name))
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
            return self.state
        if not isinstance(verdict, Mapping) or not verdict.get("accepted"):
            self.state = replace(
                self.state,
                notice=self.localized("settings.notice.hotkey.rejected"),
                notice_level="error")
            return self.state
        label = str(verdict.get("label") or "")
        self.refresh()
        self.state = replace(
            self.state,
            undo_hotkey=str(verdict.get("name") or ""),
            undo_hotkey_label=label,
            notice=(self.localized("settings.notice.undo_hotkey",
                                   hotkey=label) if label
                    else self.localized("settings.notice.undo_hotkey.cleared")),
            notice_level="success")
        return self.state

    def choose_sound_theme(self, theme: str) -> GUIState:
        desired = str(theme).strip().casefold()
        if desired not in SOUND_THEMES:
            raise ValueError(self.localized(
                "validation.face.unsupported", face=theme))
        try:
            applied = self.actions.set_sound_theme(desired)
            self.state = replace(
                self.state,
                sound_theme=(applied if applied in SOUND_THEMES else desired),
                notice="", notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
        return self.state

    def choose_language(self, code: str) -> GUIState:
        """Persist the dictation language and re-read the runtime's answer."""
        desired = str(code).strip().casefold()
        if desired not in LANGUAGES:
            raise ValueError(self.localized(
                "validation.language.unsupported", language=code))
        try:
            applied = self.actions.set_language(desired)
            self.state = replace(
                self.state,
                language=(applied if applied in LANGUAGES else desired),
                notice="", notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
        return self.state

    def preview_sound(self) -> bool:
        """Play the finish cue so a theme can be heard before it is kept."""
        try:
            return bool(self.actions.preview_sound("Pop"))
        except Exception:
            return False

    def set_recent_dictations(self, enabled: bool) -> GUIState:
        desired = bool(enabled)
        try:
            self.actions.set_recent_dictations(desired)
            self.state = replace(
                self.state, recent_dictations=desired, notice="",
                notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.flight.update_failed", error=error),
                notice_level="error")
        return self.state

    def set_self_correction(self, enabled: bool) -> GUIState:
        try:
            stored = self.actions.set_self_correction(bool(enabled)) is True
            self.state = replace(
                self.state, settings=replace(
                    self.state.settings, self_correction=stored),
                notice="", notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.self_correction.update_failed", error=error),
                notice_level="error")
        return self.state

    def set_new_speech_runtime(self, enabled: bool) -> GUIState:
        try:
            stored = self.actions.set_new_speech_runtime(bool(enabled)) is True
            self.state = replace(
                self.state, new_speech_runtime=stored,
                notice="", notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.speech_runtime.update_failed", error=error),
                notice_level="error")
        return self.state

    def set_remove_repeats(self, enabled: bool) -> GUIState:
        try:
            stored = self.actions.set_remove_repeats(bool(enabled)) is True
            self.state = replace(
                self.state, settings=replace(
                    self.state.settings, remove_repeats=stored),
                notice="", notice_level="info")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.remove_repeats.update_failed", error=error),
                notice_level="error")
        return self.state

    def recent_dictations(self) -> tuple[Mapping[str, Any], ...]:
        """Bounded metadata for the browser; never carries transcript text."""
        if not self.state.recent_dictations:
            return ()
        try:
            entries = self.actions.recent_dictations()
        except Exception:
            return ()
        return tuple(
            entry for entry in (entries or ()) if isinstance(entry, Mapping))

    def reveal_recent_dictation(self, entry_id: str) -> str:
        """Return one entry's words, only because the user asked for them."""
        try:
            revealed = self.actions.reveal_recent_dictation(str(entry_id))
        except Exception:
            revealed = None
        return revealed if isinstance(revealed, str) else ""

    def insert_recent_dictation(self, entry_id: str) -> GUIState:
        try:
            result = self.actions.insert_recent_dictation(str(entry_id))
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
            return self.state
        inserted = isinstance(result, Mapping) and result.get("inserted")
        reason = str((result or {}).get("reason") or "unavailable")
        self.refresh()
        self.state = replace(
            self.state,
            notice=(self.localized("settings.notice.recent.inserted")
                    if inserted else
                    self.localized("settings.notice.recent.refused",
                                   reason=reason)),
            notice_level="success" if inserted else "error")
        return self.state

    def undo_last_dictation(self) -> GUIState:
        """Ask the runtime to restore the destination's prior text."""
        try:
            result = self.actions.undo_last_dictation()
        except Exception as error:
            self.state = replace(
                self.state, notice=self.localized(
                    "operation.face.change_failed", error=error),
                notice_level="error")
            return self.state
        undone = isinstance(result, Mapping) and result.get("undone")
        reason = str((result or {}).get("reason") or "nothing_to_undo")
        self.refresh()
        self.state = replace(
            self.state,
            notice=(self.localized("settings.notice.undo.done") if undone
                    else self.localized("settings.notice.undo.refused",
                                        reason=reason)),
            notice_level="success" if undone else "error")
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
                outbox_count=(self.state.outbox_count
                              - self.state.outbox_unchecked),
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
        """True only while the runtime reports a permission as missing."""
        return bool(self.state.missing_permissions)

    def open_system_settings(self, permission: str = "") -> GUIState:
        """Open the pane of a missing permission; never grant one.

        With no argument it is the first missing permission. A named one
        must itself be reported missing: a row never opens a pane for a
        permission that is granted or still being checked.
        """
        if not self.permission_recovery_needed():
            return self.state
        if permission and permission not in self.state.missing_permissions:
            return self.state
        try:
            self.actions.open_system_settings(
                permission or self.state.missing_permissions[0])
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
            remaining = max(0, self.state.outbox_count - 1)
            self.state = replace(
                self.state, outbox_count=remaining,
                # What still needs a home is copied first, so the unchecked
                # copies only start to go once nothing else is left.
                outbox_unchecked=min(self.state.outbox_unchecked, remaining),
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
        self._carry(replace(self.state, verification=status))
        return self.state


def select_sidebar_row(model: WhisperFaceViewModel, index: int) -> GUIState:
    """Show the page behind one sidebar row, through the view model.

    Uses only ``select_settings_pane`` and ``select_section``, and keeps what
    each did when the window had separate section and pane controls:

    - Moving between two settings panes only selects the pane. Settings are
      not read again, exactly as with the old pane switch.
    - Arriving in a section, or choosing the row that is already showing,
      selects the section, which for Settings reads the private settings.
      The pane is chosen first so that a failure to read them is the notice
      left on screen, not cleared by the pane selection after it.
    """

    section, pane = sidebar_row_destination(index)
    state = model.state
    reselected = sidebar_row_index(state.section, state.settings_pane) == index
    if pane is not None:
        model.select_settings_pane(pane)
    if state.section != section or reselected:
        model.select_section(section)
    return model.state


def voiceover_elements(view: Any) -> tuple[Any, ...]:
    """A view, and with it the element VoiceOver reads it through.

    A control that is drawn by a cell (a text field, a button, a pop-up,
    a segmented control) is not itself the accessibility element: its
    cell is. A label, help or value set on the view alone is never
    spoken, so whatever the helpers below set, they set on both. A view
    without a cell (a switch, a custom view) is its own element.
    """
    try:
        cell = view.cell()
    except Exception:
        cell = None
    return (view,) if cell is None else (view, cell)


def _accessible(view: Any, label: str, help_text: str = "") -> Any:
    """Apply explicit VoiceOver copy without depending on visual text."""
    for element in voiceover_elements(view):
        try:
            element.setAccessibilityLabel_(label)
            if help_text:
                element.setAccessibilityHelp_(help_text)
        except Exception:
            pass
    return view


def sync_accessibility(view: Any, value: str, *, label: str = "") -> None:
    """Keep VoiceOver state synchronized with a dynamic visual control."""
    for element in voiceover_elements(view):
        try:
            if label:
                element.setAccessibilityLabel_(label)
            element.setAccessibilityValue_(value)
        except Exception:
            pass


def set_accessible_text(view: Any, value: str, *, label: str) -> None:
    """Atomically update a dynamic text field's visual and VoiceOver state."""
    view.setStringValue_(value)
    sync_accessibility(view, value, label=label)


_ESCAPE_KEY_CODE = 53
_COMMAND_KEY_FLAG = 1 << 20


def alert_key_routes(alert: Any) -> tuple[int | None, int | None]:
    """Which button index Return and Escape answer in an alert.

    A button holds one key equivalent, and AppKit's default button cell is
    simply the button holding Return, so a safe button that holds Return
    cannot also hold Escape. When another button is destructive, Escape is
    routed to the Return button by ``alert_escape_handler``.
    """
    buttons = list(alert.buttons())
    keys = [str(button.keyEquivalent()) for button in buttons]
    return_index = keys.index("\r") if "\r" in keys else None
    if "\x1b" in keys:
        return return_index, keys.index("\x1b")
    if return_index is not None and any(
            _has_destructive_action(button)
            for index, button in enumerate(buttons) if index != return_index):
        return return_index, return_index
    return return_index, None


def _has_destructive_action(button: Any) -> bool:
    try:
        return bool(button.hasDestructiveAction())
    except Exception:
        return False


def alert_escape_handler(alert: Any) -> Callable[[Any], Any] | None:
    """A key-down monitor that gives Escape to the alert's safe button.

    None when AppKit already answers Escape through a key equivalent, or the
    alert has no safe button to give it to.
    """
    _return_index, escape_index = alert_key_routes(alert)
    if escape_index is None:
        return None
    target = list(alert.buttons())[escape_index]
    if str(target.keyEquivalent()) == "\x1b":
        return None
    window = alert.window()

    def handle(event: Any) -> Any:
        if event.window() != window:
            return event
        command_period = (
            bool(int(event.modifierFlags()) & _COMMAND_KEY_FLAG)
            and str(event.charactersIgnoringModifiers()) == ".")
        if int(event.keyCode()) == _ESCAPE_KEY_CODE or command_period:
            target.performClick_(None)
            return None
        return event

    return handle


try:  # The view-model above remains usable in headless test environments.
    import objc
    from AppKit import (
        NSAnimationContext,
        NSApplication,
        NSAlert,
        NSBackingStoreBuffered,
        NSBezelStyleRounded,
        NSBezierPath,
        NSButton,
        NSColor,
        NSColorSpace,
        NSControlStateValueOff,
        NSControlStateValueOn,
        NSAppearance,
        NSAppearanceNameAqua,
        NSAppearanceNameDarkAqua,
        NSEvent,
        NSEventMaskFlagsChanged,
        NSEventMaskKeyDown,
        NSEventModifierFlagCommand,
        NSEventModifierFlagShift,
        NSFont,
        NSFontAttributeName,
        NSFontDescriptorSystemDesignRounded,
        NSFontWeightBold,
        NSFontWeightMedium,
        NSFontWeightRegular,
        NSFontWeightSemibold,
        NSForegroundColorAttributeName,
        NSImage,
        NSImageLeading,
        NSImageScaleProportionallyUpOrDown,
        NSImageView,
        NSLayoutConstraint,
        NSMakeRect,
        NSMenu,
        NSMenuItem,
        NSNoBorder,
        NSProgressIndicator,
        NSPopUpButton,
        NSSavePanel,
        NSScrollView,
        NSSegmentedControl,
        NSSwitch,
        NSTextField,
        NSTextView,
        NSView,
        NSViewMinYMargin,
        NSViewWidthSizable,
        NSWindow,
        NSWindowOcclusionStateVisible,
        NSWorkspace,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    # The shell: the split view that holds the sidebar, and what the sidebar
    # and the title strip are made of. Every name has been in AppKit since
    # macOS 10.11 or earlier, so macOS 14 has them all; anything newer is
    # looked up with getattr where it is used, never imported here, because
    # one missing name would leave the window unavailable.
    from AppKit import (
        NSAccessibilityListRole,
        NSLayoutAttributeCenterY,
        NSSplitViewController,
        NSSplitViewItem,
        NSStackView,
        NSUserInterfaceLayoutOrientationHorizontal,
        NSViewController,
        NSWindowCloseButton,
        NSWindowMiniaturizeButton,
        NSWindowStyleMaskFullSizeContentView,
        NSWindowTitleHidden,
        NSWindowZoomButton,
    )
    # What the new look's brand components, Home and first-run setup draw
    # with. Guarded like everything above: all of it has been in AppKit,
    # Foundation or Core Graphics since macOS 10.12 or earlier, and one
    # name missing here leaves the window unavailable rather than the
    # module unimportable.
    from AppKit import (
        NSAccessibilityGroupRole,
        NSAccessibilityImageRole,
        NSAccessibilityRadioButtonRole,
        NSAccessibilityStaticTextRole,
        NSAffineTransform,
        NSBitmapImageRep,
        NSDeviceRGBColorSpace,
        NSCompositingOperationSourceOver,
        NSFontFeatureSelectorIdentifierKey,
        NSFontFeatureSettingsAttribute,
        NSFontFeatureTypeIdentifierKey,
        NSGraphicsContext,
        NSInsetRect,
        NSLineBreakByTruncatingTail,
        NSMakeSize,
        NSMutableParagraphStyle,
        NSParagraphStyleAttributeName,
        NSTextAlignmentCenter,
        NSTextAlignmentLeft,
        NSTextAlignmentRight,
        NSTextAttachment,
        NSViewNoIntrinsicMetric,
        NSZeroRect,
    )
    from Foundation import (
        NSAttributedString, NSLocale, NSMutableAttributedString, NSObject,
        NSTimer, NSUserDefaults)
    from Quartz import (
        CAMediaTimingFunction,
        CASpringAnimation,
        CGContextBeginTransparencyLayer,
        CGContextEndTransparencyLayer,
        CGContextRestoreGState,
        CGContextSaveGState,
        CGContextSetAlpha,
        kCAMediaTimingFunctionEaseOut,
    )

    from whisper_face_render import LiveFaceView

    APPKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only outside macOS installs
    APPKIT_AVAILABLE = False
    objc = None
    NSObject = object


if APPKIT_AVAILABLE:
    _TEXT = NSColor.labelColor()
    _SECONDARY = NSColor.secondaryLabelColor()

    # AA text inks live beside the palettes so their contrast can be tested
    # headlessly; see whisper_face_theme.BRAND_TEXT_ON_LIGHT.
    from whisper_face_theme import BRAND_TEXT_ON_LIGHT

    # The tokens and geometry the new look's brand components draw from.
    from whisper_face_theme import (
        BRAND_SHAPES,
        DISABLED_ALPHA,
        STAGE_CIRCLES,
        STAGE_FACE_BLEED,
        STAGE_FACE_SIZE,
        STAGE_FACE_TILT,
        STAGE_HEIGHT,
        STICKER_LABEL_ALPHA,
        STICKER_TILTS,
        STICKER_TINTS,
        TYPE_SPECS,
        BrandShape,
        color_token,
        hard_shadow_footprint,
        hard_shadow_frames,
        stage_layout,
        stage_token_for_face,
        tilted_footprint,
        tilted_inner_size,
    )

    # Window skeleton: a full-height sidebar of fixed width in the system's
    # sidebar material, and beside it the content side: a title strip over
    # a page column capped at a comfortable reading width and centered when
    # the window grows wider. At the window minimum the column shrinks to
    # CONTENT_MIN_WIDTH, which is what the sidebar and a full CONTENT_INSET
    # on each side leave over; every page must lay out at that width.
    SIDEBAR_WIDTH = 200.0
    CONTENT_WIDTH = 720.0
    CONTENT_MIN_WIDTH = 632.0
    # The narrowest margin the column may keep on the content side. With
    # CONTENT_MIN_WIDTH at the layout contract's column minimum this is
    # never reached: the margin is CONTENT_INSET at every window width.
    CONTENT_EDGE_MIN = 12.0

    # Layout contract for the new look. The shell uses SIDEBAR_WIDTH,
    # TITLE_STRIP_HEIGHT, CONTENT_INSET, SIDEBAR_ROW_HEIGHT and
    # SIDEBAR_FACE_SIZE; the row and group numbers are for the pages that
    # are rebuilt to sit in it, so a page can be laid out against the same
    # numbers the shell was.
    #
    # The title strip is the band on the content side that carries the page
    # title and its trailing toolbar items; content scrolls under it.
    TITLE_STRIP_HEIGHT = 52.0
    # Space between the content side's edges and the page column, and
    # between the last group and the bottom of the scrolling content.
    CONTENT_INSET = 24.0
    # The page column is the reading width every group of rows shares. It
    # never grows past the old content column, and at the window minimum it
    # is exactly what the sidebar and the two insets leave over.
    PAGE_COLUMN_MAX_WIDTH = CONTENT_WIDTH
    PAGE_COLUMN_MIN_WIDTH = 632.0
    # Rows: label left, control right. A row with a one-line caption is
    # taller; neither may be shorter than this at any text size.
    ROW_MIN_HEIGHT = 44.0
    ROW_CAPTION_MIN_HEIGHT = 50.0
    # Left inset of row text, and of the hairline between two rows.
    ROW_INSET = 14.0
    # Sidebar rows, and the face beside the app name above them.
    SIDEBAR_ROW_HEIGHT = 32.0
    SIDEBAR_FACE_SIZE = 44.0
    # Vertical rhythm: header to its group, and one group to the next
    # section header.
    GROUP_HEADER_GAP = 7.0
    GROUP_GAP = 18.0

    # The sidebar, top to bottom. The traffic lights sit over its first
    # 28 points, so the brand block starts clear of them.
    SIDEBAR_PADDING = 10.0          # rows, from the sidebar's side edges
    SIDEBAR_BRAND_TOP = 40.0        # top of the face chip
    SIDEBAR_BRAND_INSET = 14.0      # face chip, from the sidebar's edge
    SIDEBAR_BRAND_GAP = 14.0        # brand block to the first row
    SIDEBAR_ROW_GAP = 2.0
    SIDEBAR_ROW_RADIUS = 9.0
    SIDEBAR_ROW_INSET = 9.0         # icon, from the row's edge; icon to label
    SIDEBAR_ICON_SIZE = 16.0
    SIDEBAR_GROUP_LABEL_HEIGHT = 31.0   # 14 above an 11pt label, 4 below
    SIDEBAR_FOOT_INSET = 14.0       # build label, from the bottom edge
    # What the title bar keeps for itself at the top of a full-size content
    # view when the window cannot be asked (it can, once it exists).
    TITLE_BAR_HEIGHT = 28.0
    # Space between two trailing items in the title strip.
    TITLE_STRIP_ITEM_GAP = 8.0

    # Home, top to bottom: the stage, the three stat stickers, a section
    # header and one group of rows. The gaps are the least each may be; a
    # taller window lets the first two breathe, up to HOME_GAP_STRETCH more.
    HOME_STAGE_GAP = 8.0            # under the stage's hard shadow
    HOME_STICKER_GAP = 16.0         # between two stickers
    HOME_GROUP_GAP = 8.0            # stickers to the section header
    HOME_GAP_STRETCH = 12.0

    # Inside a speech bubble: padding round the copy, headline to
    # instruction, instruction to the buttons, and one button to the next.
    BUBBLE_PADDING_X = 20.0
    BUBBLE_PADDING_Y = 18.0
    BUBBLE_HEADLINE_GAP = 6.0
    BUBBLE_ACTIONS_GAP = 14.0
    BUBBLE_BUTTON_GAP = 10.0
    # The headline steps down to this size when it would not fit one line.
    BUBBLE_HEADLINE_SMALL = 23.0
    BUBBLE_DETAIL_SIZE = 14.0

    # First-run setup: a shorter stage, the step's rows, then a foot with
    # the route across the four steps and the one Continue button.
    SETUP_STAGE_HEIGHT = 200.0
    SETUP_FACE_SIZE = 150.0
    SETUP_ROW_COUNT = SETUP_ROW_LIMIT   # the most rows any step shows
    SETUP_ICON_SIZE = 34.0
    SETUP_ICON_RADIUS = 10.0
    SETUP_GAP = 16.0                # stage to rows, rows to the foot
    SETUP_PROGRESS_HEIGHT = 6.0
    SETUP_FOOT_HEIGHT = 40.0
    SETUP_KICKER_HEIGHT = 16.0
    SETUP_DETAIL_SIZE = 13.0

    # The notice band reserves one 11pt line; a two-line failure message
    # grows upward into the column's bottom padding rather than pushing the
    # page and clipping its last row at the window minimum.
    NOTICE_LINE_HEIGHT = 16.0
    # The window minimum. Its width is the sidebar, the column's minimum
    # and CONTENT_INSET on both sides of it. Its height is what Home, the
    # one page that cannot scroll, needs under the 52pt title strip with
    # everything it can show at once and room left for a two-line notice.
    # The native smoke fails if any page's content does not fit there.
    WINDOW_MIN_SIZE = (880.0, 648.0)
    WINDOW_DEFAULT_SIZE = (1000.0, 680.0)
    NOTICE_WRAPPED_HEIGHT = 28.0
    # The page column stops NOTICE_GAP above a one-line notice. A second
    # line rises through that gap and into the column's foot. A scrolling
    # page ends NOTICE_CLEARANCE above the notice, whatever its height; a
    # page that cannot scroll (Home) keeps NOTICE_RISE of its foot free at
    # all times, so a two-line notice never lands on its last group.
    NOTICE_GAP = 6.0
    NOTICE_CLEARANCE = 4.0
    # The notice sits NOTICE_FOOT above the window's bottom edge, and the
    # band it needs is its one line, that foot and the gap above it. The
    # band is kept only while a notice is showing: with none, a page that
    # scrolls runs to the window's edge. Home and setup cannot scroll, so
    # they lay themselves out above the band whether it is in use or not
    # and stay still when a notice comes and goes.
    NOTICE_FOOT = 10.0
    # Every title setup's foot button takes. The step marks beside it are
    # laid out for the widest, so they do not move from step to step.
    SETUP_FOOT_ACTION_KEYS = (
        "onboarding.action.continue",
        "onboarding.action.hotkey",
        "onboarding.action.models",
        "onboarding.action.first_dictation",
        "onboarding.action.finish",
    )
    NOTICE_BAND = NOTICE_FOOT + NOTICE_LINE_HEIGHT + NOTICE_GAP
    NOTICE_RISE = (NOTICE_WRAPPED_HEIGHT - NOTICE_LINE_HEIGHT - NOTICE_GAP
                   + NOTICE_CLEARANCE)

    # Mirrored from GUIState by render() so jelly springs on native controls
    # honor Reduce Motion exactly like the HUD path, without every control
    # needing a reference back to the view model.
    _REDUCE_MOTION = False

    _FONT_WEIGHTS = {
        "regular": NSFontWeightRegular,
        "medium": NSFontWeightMedium,
        "semibold": NSFontWeightSemibold,
        "bold": NSFontWeightBold,
    }

    def _font(size: float, weight: str = "regular",
              *, rounded: bool = False) -> Any:
        font = NSFont.systemFontOfSize_weight_(
            size, _FONT_WEIGHTS.get(weight, NSFontWeightRegular))
        if rounded:
            try:
                descriptor = font.fontDescriptor().fontDescriptorWithDesign_(
                    NSFontDescriptorSystemDesignRounded)
                if descriptor is not None:
                    rounded_font = NSFont.fontWithDescriptor_size_(
                        descriptor, size)
                    if rounded_font is not None:
                        font = rounded_font
            except Exception:
                pass
        return font

    def _symbol_image(name: str, *, size: float = 13.0,
                      weight: str = "medium") -> Any:
        """Return an SF Symbol template image, or None when unavailable."""
        try:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                name, None)
        except Exception:
            return None
        if image is None:
            return None
        try:
            import AppKit as _appkit
            configuration_type = getattr(
                _appkit, "NSImageSymbolConfiguration", None)
            if configuration_type is not None:
                configuration = configuration_type \
                    .configurationWithPointSize_weight_(
                        size, _FONT_WEIGHTS.get(weight, NSFontWeightMedium))
                configured = image.imageWithSymbolConfiguration_(configuration)
                if configured is not None:
                    return configured
        except Exception:
            pass
        return image

    def center_layer_anchor(layer: Any) -> bool:
        """Move a layer's anchor to its middle without moving the layer.

        AppKit hands view-backed layers an anchor of (0, 0), so a scale
        animation grows out of the bottom-left corner and the squash reads as
        a slide. Re-anchoring at the center makes squash-and-stretch behave
        the way the shared motion specs describe it; the position is shifted
        by the same amount the anchor moved so nothing jumps on screen, and
        later ``setFrame:`` calls from AppKit recompute the position from the
        new anchor on their own.
        """
        if layer is None:
            return False
        try:
            anchor = layer.anchorPoint()
            if abs(anchor.x - 0.5) < 1e-6 and abs(anchor.y - 0.5) < 1e-6:
                return True
            size = layer.bounds().size
            position = layer.position()
            layer.setAnchorPoint_((0.5, 0.5))
            layer.setPosition_((
                position.x + (0.5 - anchor.x) * size.width,
                position.y + (0.5 - anchor.y) * size.height,
            ))
        except Exception:
            return False
        return True

    def add_jelly_motion(view: Any, motion_name: str,
                         *, reduced_motion: bool = False) -> bool:
        """Translate one named motion into two native Core Animation springs.

        This is the same soft-body port the HUD uses in ``dictate.py``: every
        spring reads the shared ``MOTION_SPECS`` so window, HUD, and site keep
        one motion source of truth. Reduce Motion skips the springs entirely
        and reports False so callers can verify the gate.
        """
        if reduced_motion or view is None:
            return False
        try:
            view.setWantsLayer_(True)
            layer = view.layer()
        except Exception:
            return False
        if layer is None:
            return False
        center_layer_anchor(layer)
        spec = MOTION_SPECS[motion_name]
        for axis, start in (("x", spec.squash_x), ("y", spec.squash_y)):
            animation = CASpringAnimation.animationWithKeyPath_(
                f"transform.scale.{axis}")
            animation.setMass_(spec.mass)
            animation.setStiffness_(spec.stiffness)
            animation.setDamping_(spec.damping)
            animation.setInitialVelocity_(spec.initial_velocity)
            animation.setFromValue_(start)
            animation.setToValue_(1.0)
            animation.setDuration_(spec.duration)
            layer.addAnimation_forKey_(
                animation, f"whisper-face-{motion_name}-{axis}")
        return True

    # Window chrome only: content crossfades and the notice fade never touch
    # the dictation hot path, and Reduce Motion drops straight to the final
    # opacity instead of shortening the fade.
    CROSSFADE_SECONDS = 0.15

    def fade_in(view: Any, *, reduced_motion: bool = False,
                duration: float = CROSSFADE_SECONDS) -> bool:
        """Fade one chrome view up from transparent, ease-out and quick."""
        if view is None:
            return False
        try:
            if reduced_motion:
                view.setAlphaValue_(1.0)
                return False
            view.setAlphaValue_(0.0)
            NSAnimationContext.beginGrouping()
            context = NSAnimationContext.currentContext()
            context.setDuration_(duration)
            try:
                context.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(
                        kCAMediaTimingFunctionEaseOut))
            except Exception:
                pass
            view.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()
        except Exception:
            try:
                view.setAlphaValue_(1.0)
            except Exception:
                pass
            return False
        return True

    class JellyButton(NSButton):
        """Button that squashes on press and springs back on release."""

        def mouseDown_(self, event):
            add_jelly_motion(self, "press", reduced_motion=_REDUCE_MOTION)
            objc.super(JellyButton, self).mouseDown_(event)
            add_jelly_motion(self, "release", reduced_motion=_REDUCE_MOTION)

    class JellySwitch(NSSwitch):
        """Switch with the same press/release soft-body feedback."""

        def mouseDown_(self, event):
            add_jelly_motion(self, "press", reduced_motion=_REDUCE_MOTION)
            objc.super(JellySwitch, self).mouseDown_(event)
            add_jelly_motion(self, "release", reduced_motion=_REDUCE_MOTION)

    class JellySegmentedControl(NSSegmentedControl):
        """Segmented control with press/release soft-body feedback."""

        def mouseDown_(self, event):
            add_jelly_motion(self, "press", reduced_motion=_REDUCE_MOTION)
            objc.super(JellySegmentedControl, self).mouseDown_(event)
            add_jelly_motion(self, "release", reduced_motion=_REDUCE_MOTION)

    class FlowView(NSView):
        """A container laid out from the top down by whoever built it.

        ``on_layout`` is called whenever the view's size changes, from a
        frame change and from an Auto Layout pass alike, so the manual
        layout inside it follows the window without waiting for a render.
        """

        def initWithFrame_(self, frame):
            self = objc.super(FlowView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.on_layout = None
            self._arranging = False
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def arrange(self) -> None:
            if self.on_layout is None or self._arranging:
                return
            self._arranging = True
            try:
                self.on_layout()
            finally:
                self._arranging = False

        def resizeSubviewsWithOldSize_(self, _old_size):
            self.arrange()

        def layout(self):
            objc.super(FlowView, self).layout()
            self.arrange()

    class SpeechBubbleView(NSView):
        """The speech bubble: porcelain, ink outline, hard shadow, and a
        tail on its left edge that points at the face beside it.

        The frame is the whole footprint: the tail's reach on the left and
        the hard shadow on the right and below. ``content_rect`` is where
        the copy goes; ``set_tail_center`` moves the tail up or down the
        left edge. It is not a control, so its outline is the ordinary ink
        in both appearances. VoiceOver finds it as a group.
        """

        TAIL_LENGTH = 11.0
        TAIL_HALF = 10.0

        def initWithFrame_(self, frame):
            self = objc.super(SpeechBubbleView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.tail_center = 54.0
            try:
                self.setAccessibilityElement_(True)
                self.setAccessibilityRole_(NSAccessibilityGroupRole)
            except Exception:
                pass
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def footprint(self) -> Any:
            """The body and its shadow: the bounds without the tail."""
            bounds = self.bounds()
            return NSMakeRect(
                bounds.origin.x + self.TAIL_LENGTH, bounds.origin.y,
                max(0.0, bounds.size.width - self.TAIL_LENGTH),
                bounds.size.height)

        @objc.python_method
        def body_rect(self) -> Any:
            return hard_shadow_body_rect(
                self.footprint(), BRAND_SHAPES["bubble"])

        @objc.python_method
        def content_rect(self) -> Any:
            return NSInsetRect(
                self.body_rect(), BUBBLE_PADDING_X, BUBBLE_PADDING_Y)

        @objc.python_method
        def set_tail_center(self, y: float) -> None:
            """Put the tail's point ``y`` below the top, kept clear of the
            rounded corners."""
            shape = BRAND_SHAPES["bubble"]
            body = self.body_rect()
            margin = shape.radius + self.TAIL_HALF
            y = max(margin, min(float(y), body.size.height - margin))
            if abs(y - self.tail_center) > 0.01:
                self.tail_center = y
                self.setNeedsDisplay_(True)

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            shape = BRAND_SHAPES["bubble"]
            fill = _srgb_color(palette_for_appearance(dark).surface)
            ink = _outline_ink(dark)
            body = draw_hard_shadow_shape(
                self.footprint(), shape, fill=fill, ink=ink, dark=dark)
            # The tail is part of the body's edge: its fill covers the
            # stretch of outline it grows out of, and its two sides are
            # stroked in the same ink and width.
            edge = body.origin.x + shape.outline / 2.0
            center = body.origin.y + self.tail_center
            point = (body.origin.x - self.TAIL_LENGTH + shape.outline / 2.0,
                     center)
            wedge = NSBezierPath.bezierPath()
            wedge.moveToPoint_(
                (body.origin.x + shape.outline + 0.5,
                 center - self.TAIL_HALF - 1.5))
            wedge.lineToPoint_(point)
            wedge.lineToPoint_(
                (body.origin.x + shape.outline + 0.5,
                 center + self.TAIL_HALF + 1.5))
            wedge.closePath()
            fill.setFill()
            wedge.fill()
            sides = NSBezierPath.bezierPath()
            sides.moveToPoint_((edge, center - self.TAIL_HALF))
            sides.lineToPoint_(point)
            sides.lineToPoint_((edge, center + self.TAIL_HALF))
            sides.setLineWidth_(shape.outline)
            sides.setLineJoinStyle_(1)
            sides.setLineCapStyle_(1)
            ink.setStroke()
            sides.stroke()

    def _drawing_dark() -> bool:
        """Whether whatever is being drawn right now is in the dark
        appearance, for drawing that has no view to ask."""
        try:
            appearance = None
            if NSAppearance.respondsToSelector_("currentDrawingAppearance"):
                appearance = NSAppearance.currentDrawingAppearance()
            if appearance is None:
                appearance = NSAppearance.currentAppearance()
            match = appearance.bestMatchFromAppearancesWithNames_(
                [NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
            return str(match) == str(NSAppearanceNameDarkAqua)
        except Exception:
            return False

    def key_cap_attachment(key: str, font: Any) -> Any:
        """A key cap that sits in a line of copy and wraps with it.

        The cap is drawn by ``draw_key_cap``, the same code ``KeyCapView``
        uses, whenever the line is drawn, so it follows the appearance. It
        is lowered until its body sits on the line's x-height.
        """
        width, height = key_cap_size(key)

        def draw(rect: Any) -> bool:
            draw_key_cap(rect, key, _drawing_dark())
            return True

        image = NSImage.imageWithSize_flipped_drawingHandler_(
            NSMakeSize(width, height), True, draw)
        attachment = NSTextAttachment.alloc().init()
        attachment.setImage_(image)
        shape = BRAND_SHAPES["key_cap"]
        middle = float(font.xHeight()) / 2.0
        attachment.setBounds_(NSMakeRect(
            0.0, middle - shape.height / 2.0 - shape.shadow_down,
            width, height))
        return attachment

    def copy_with_key_cap(text: str, key: str, *, font: Any,
                          color: Any) -> Any:
        """``text`` as an attributed line, with ``key`` shown as a key cap
        the first time the copy names it."""
        attributes = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: color,
        }
        before, found, after = (
            text.partition(key) if key else (text, "", ""))
        line = NSMutableAttributedString.alloc().init()

        def add(piece: str) -> None:
            if piece:
                line.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_(
                        piece, attributes))

        add(before)
        if found:
            line.appendAttributedString_(
                NSAttributedString.attributedStringWithAttachment_(
                    key_cap_attachment(key, font)))
        add(after)
        return line

    class ArtFacePicker(NSView):
        """All the faces as one grid of chips, the chosen one in amber.

        Speaks the two segment methods the render path and the smoke test
        already use (``setSelectedSegment_`` / ``selectedSegment``), so the
        callers never learned it is not a segmented control. Each chip is a
        ``FaceChipButton``: a radio button for VoiceOver with the animal's
        name as its label and tooltip, a Tab stop like any button, Space to
        choose. The grid fills its width, eight chips a row (``face_grid``
        has the arithmetic), and it says how tall it is at a width so the
        page can lay out around it.
        """

        def initWithFrame_(self, frame):
            self = objc.super(ArtFacePicker, self).initWithFrame_(frame)
            if self is None:
                return None
            self.chips = []
            self.faces = ()
            self.selected = 0
            self.target = None
            self.pick_action = None
            try:
                # One radio group, read as a whole and then chip by chip.
                self.setAccessibilityElement_(True)
                self.setAccessibilityRole_("AXRadioGroup")
            except Exception:
                pass
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def populate(self, faces, images, tooltips, target, action):
            self.faces = tuple(faces)
            self.target = target
            self.pick_action = action
            for index, face in enumerate(self.faces):
                chip = FaceChipButton.alloc().initWithFrame_(
                    NSMakeRect(0, 0, 46, 47))
                chip.configure(images[index], tooltips[index])
                chip.setTag_(index)
                chip.setTarget_(self)
                chip.setAction_("chipPicked:")
                self.addSubview_(chip)
                self.chips.append(chip)
            self.arrange()
            return self

        @objc.python_method
        def grid(self, width: float) -> tuple[int, float, float, int]:
            """Columns, chip side, left margin and rows at ``width``.

            The last chip's hard shadow sticks out past its body, so the
            bodies share what is left once that is set aside.
            """
            return face_grid(
                width - BRAND_SHAPES["face_chip"].shadow_right,
                len(self.chips))

        @objc.python_method
        def height_for_width(self, width: float) -> float:
            _columns, chip, _margin, rows = self.grid(width)
            shape = BRAND_SHAPES["face_chip"]
            return (rows * chip + (rows - 1) * FACE_CHIP_GAP
                    + shape.shadow_down)

        @objc.python_method
        def arrange(self) -> None:
            width = float(self.bounds().size.width)
            columns, chip, margin, _rows = self.grid(width)
            shape = BRAND_SHAPES["face_chip"]
            step = chip + FACE_CHIP_GAP
            for index, view in enumerate(self.chips):
                row, column = divmod(index, columns)
                # The frame is the chip's whole footprint: the body plus
                # the room its hard shadow takes, right and down.
                frame = NSMakeRect(
                    margin + column * step, row * step,
                    chip + shape.shadow_right, chip + shape.shadow_down)
                current = view.frame()
                if (current.origin.x != frame.origin.x
                        or current.origin.y != frame.origin.y
                        or current.size.width != frame.size.width
                        or current.size.height != frame.size.height):
                    view.setFrame_(frame)

        def layout(self):
            objc.super(ArtFacePicker, self).layout()
            self.arrange()

        def chipPicked_(self, sender):
            self.setSelectedSegment_(sender.tag())
            if self.target is not None and self.pick_action:
                getattr(self.target, self.pick_action)(self)

        def setSelectedSegment_(self, index):
            self.selected = max(0, min(len(self.chips) - 1, int(index)))
            self._paint_selection()

        def selectedSegment(self):
            return self.selected

        @objc.python_method
        def _paint_selection(self):
            for index, chip in enumerate(self.chips):
                chip.set_selected(index == self.selected)

    def _theme_color(
            color: tuple[float, float, float], alpha: float = 1.0) -> Any:
        # sRGB, so the colour on screen is the hex the palette names and
        # the contrast the tests measure. Calibrated RGB drew every
        # mid-tone lighter than its hex.
        return NSColor.colorWithSRGBRed_green_blue_alpha_(
            color[0], color[1], color[2], alpha)

    def _uses_dark_appearance(view: Any) -> bool:
        try:
            match = view.effectiveAppearance() \
                .bestMatchFromAppearancesWithNames_(
                    [NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
            return str(match) == str(NSAppearanceNameDarkAqua)
        except Exception:
            return False

    def _brand_text_color(dark: bool) -> Any:
        palette = palette_for_appearance(dark)
        return _theme_color(palette.brand if dark else BRAND_TEXT_ON_LIGHT)

    def _error_text_color(dark: bool) -> Any:
        palette = palette_for_appearance(dark)
        return _theme_color(palette.error if dark else ERROR_TEXT_ON_LIGHT)

    def _label(text: str, frame: Any, *, size: float = 13,
               weight: str = "regular", color: Any = None,
               accessibility_label: str = "", rounded: bool = False,
               wrap: bool = False, lines: int = 0,
               alignment: int | None = None,
               truncate: bool = False) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(frame)
        label.setFont_(_font(size, weight, rounded=rounded))
        label.setTextColor_(color or _TEXT)
        label.setLineBreakMode_(4 if truncate else 0)
        if truncate or wrap:
            # Neither a truncating nor a wrapping label may widen the layout
            # (or the whole window) to fit one line of text: keep the pull
            # below the window's size-stay-put priority so the text shortens
            # or wraps instead of forcing the window past its minimum.
            try:
                label.setContentCompressionResistancePriority_forOrientation_(
                    400.0, 0)
                if wrap:
                    label.setPreferredMaxLayoutWidth_(frame.size.width)
            except Exception:
                pass
        if wrap:
            label.setUsesSingleLineMode_(False)
            # A bounded line count keeps a long string wrapping inside its
            # card instead of overflowing the layout below it; the last line
            # then tail-truncates rather than disappearing. Setting a
            # truncating break mode clears the cell's wrap flag, so it is
            # restored right after.
            label.setMaximumNumberOfLines_(lines)
            if lines:
                label.setLineBreakMode_(4)
                try:
                    label.cell().setWraps_(True)
                    label.cell().setScrollable_(False)
                except Exception:
                    label.setLineBreakMode_(0)
        if alignment is not None:
            label.setAlignment_(alignment)
        # A text is read as what it says. A label is for what it does not
        # say (whose state it is); one that only repeated the text would
        # be spoken as well as the text.
        return (_accessible(label, accessibility_label)
                if accessibility_label else label)

    class PushButton(JellyButton):
        """A system push button whose VoiceOver copy reaches VoiceOver.

        An NSButton is not itself the accessibility element: its cell is,
        so a label or help set on the button alone is never read. Every
        accessibility property set here, by ``_accessible`` at build or
        ``sync_accessibility`` at render, is handed to the cell as well.
        """

        def setAccessibilityLabel_(self, label):
            objc.super(PushButton, self).setAccessibilityLabel_(label)
            if self.cell() is not None:
                self.cell().setAccessibilityLabel_(label)

        def setAccessibilityHelp_(self, text):
            objc.super(PushButton, self).setAccessibilityHelp_(text)
            if self.cell() is not None:
                self.cell().setAccessibilityHelp_(text)

        def setAccessibilityValue_(self, value):
            objc.super(PushButton, self).setAccessibilityValue_(value)
            if self.cell() is not None:
                self.cell().setAccessibilityValue_(value)

    def _button(title: str, frame: Any, target: Any, action: str,
                *, help_text: str = "", symbol: str = "",
                label: str = "") -> Any:
        """A standard bordered push button at the system's regular size.

        The bezel's height and corners are the system's: the frame's height
        only says where the button is centred, and its width is a minimum,
        grown to fit the title.

        VoiceOver reads ``label``, or the title when none is given. A
        button in a row is given one that says what it does to what
        ("Change capture key" for Change…), because the row's title is
        not part of the button. A symbol is decoration: it never names
        the button.
        """
        button = PushButton.alloc().initWithFrame_(frame)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setBordered_(True)
        button.setControlSize_(0)
        button.setTitle_(title)
        if symbol:
            image = _symbol_image(symbol, size=12.0)
            if image is not None:
                try:
                    # An SF Symbol describes itself ("Search Document");
                    # the button's own words are what is read.
                    image.setAccessibilityDescription_("")
                except Exception:
                    pass
                button.setImage_(image)
                button.setImagePosition_(NSImageLeading)
        fitted = button.fittingSize()
        button.setFrame_(NSMakeRect(
            frame.origin.x,
            frame.origin.y + (frame.size.height - fitted.height) / 2.0,
            max(frame.size.width, fitted.width), fitted.height))
        button.setTarget_(target)
        button.setAction_(action)
        return _accessible(button, label or title, help_text)

    def _silence_image(view: Any) -> Any:
        """Keep a decorative image view out of the VoiceOver tree.

        VoiceOver talks to an image view's cell, which names an SF Symbol
        by its own description, so both the view and the cell are told.
        """
        for element in (view, view.cell()):
            try:
                element.setAccessibilityElement_(False)
            except Exception:
                pass
        return view

    # ------------------------------------------------------------------
    # Settings pages, laid out the way System Settings lays out a pane:
    # section headers over groups of rows, each row its label and caption
    # on the left and its controls on the right. Every frame is worked out
    # from the page's width whenever it changes, and from the copy whenever
    # a render changes it, so captions wrap instead of truncating at any
    # column width or text size, and a page taller than the window scrolls.
    # ------------------------------------------------------------------

    PAGE_TOP = 4.0              # title strip to the first section header
    SECTION_HEADER_INSET = 4.0  # headers and notes, from the group's edge
    NOTE_GAP = 6.0              # group to the note under it
    ROW_PADDING_Y = 9.0         # text block to a row's top and bottom
    ROW_CONTROL_PADDING_Y = 8.0  # tallest control to a row's top and bottom
    ROW_CONTROL_GAP = 12.0      # between bezelled controls; text to controls
    ROW_TEXT_GAP = 2.0          # row title to its caption
    STATUS_SYMBOL_SIZE = 14.0
    STATUS_SYMBOL_GAP = 5.0

    def _text_height(label: Any, width: float) -> float:
        """Height a wrapping label needs for its current text at ``width``."""
        try:
            size = label.cell().cellSizeForBounds_(
                NSMakeRect(0, 0, max(1.0, width), 10_000.0))
            return float(math.ceil(size.height))
        except Exception:
            return float(label.frame().size.height)

    def _natural_size(view: Any) -> tuple[float, float]:
        """A control's own size, as its alignment rectangle."""
        size = view.intrinsicContentSize()
        width, height = float(size.width), float(size.height)
        if width <= 0.0 or height <= 0.0:
            fitted = view.fittingSize()
            if width <= 0.0:
                width = float(fitted.width)
            if height <= 0.0:
                height = float(fitted.height)
        frame = view.frame().size
        if width <= 0.0:
            width = float(frame.width)
        if height <= 0.0:
            height = float(frame.height)
        return width, height

    def _place(view: Any, x: float, y: float, width: float,
               height: float) -> None:
        """Put a view's alignment rectangle at (x, y) in a flipped parent."""
        try:
            view.setFrame_(view.frameForAlignmentRect_(
                NSMakeRect(x, y, width, height)))
        except Exception:
            view.setFrame_(NSMakeRect(x, y, width, height))

    class StatusLabelView(NSView):
        """A state in words: an SF Symbol and a plain phrase beside it.

        The phrase is the secondary colour; the symbol carries the tone
        (a check for good, a dash for neutral, a triangle for a warning),
        so the meaning never rides on colour alone. VoiceOver reads the
        phrase as static text; the symbol is decoration.
        """

        def initWithFrame_(self, frame):
            self = objc.super(StatusLabelView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.tone = "neutral"
            self.icon = NSImageView.alloc().initWithFrame_(
                NSMakeRect(0, 0, STATUS_SYMBOL_SIZE, STATUS_SYMBOL_SIZE))
            self.icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            self.icon.setEditable_(False)
            # The phrase says the state; the symbol only repeats its tone.
            _silence_image(self.icon)
            self.text = _label("", NSMakeRect(0, 0, 10, 16), size=12)
            self.addSubview_(self.icon)
            self.addSubview_(self.text)
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def set_status(self, text: str, tone: str, *, symbol_color: Any,
                       text_color: Any) -> Any:
            self.tone = tone if tone in STATUS_TONES else "neutral"
            self.text.setStringValue_(text)
            self.text.setTextColor_(text_color)
            self.icon.setImage_(_symbol_image(
                {"good": "checkmark.circle.fill",
                 "warn": "exclamationmark.triangle.fill"}.get(
                     self.tone, "minus.circle"),
                size=12.0, weight="semibold"))
            try:
                self.icon.setContentTintColor_(symbol_color)
            except Exception:
                pass
            self.invalidateIntrinsicContentSize()
            self.setNeedsLayout_(True)
            return self

        @objc.python_method
        def text_origin(self) -> float:
            # A label's frame is wider than its text by its alignment
            # insets; the gap is measured to the text, not the frame.
            return (STATUS_SYMBOL_SIZE + STATUS_SYMBOL_GAP
                    - float(self.text.alignmentRectInsets().left))

        @objc.python_method
        def text_overhang(self) -> float:
            # The phrase ends flush with the view, like a plain value label
            # placed by its alignment rectangle in the next row.
            return float(self.text.alignmentRectInsets().right)

        def intrinsicContentSize(self):
            fitted = self.text.fittingSize()
            return NSMakeSize(
                self.text_origin() + math.ceil(fitted.width)
                - self.text_overhang(),
                max(STATUS_SYMBOL_SIZE + 2.0, float(fitted.height)))

        def layout(self):
            objc.super(StatusLabelView, self).layout()
            self.arrange()

        def resizeSubviewsWithOldSize_(self, _size):
            # A new frame from the row's layout lands after the window's
            # layout pass, so the parts follow the frame directly.
            self.arrange()

        @objc.python_method
        def arrange(self) -> None:
            bounds = self.bounds().size
            height = float(self.text.fittingSize().height)
            origin = self.text_origin()
            self.icon.setFrame_(NSMakeRect(
                0, (bounds.height - STATUS_SYMBOL_SIZE) / 2.0,
                STATUS_SYMBOL_SIZE, STATUS_SYMBOL_SIZE))
            self.text.setFrame_(NSMakeRect(
                origin, (bounds.height - height) / 2.0,
                max(0.0, bounds.width - origin + self.text_overhang()),
                height))

    class SettingsRowView(NSView):
        """One row of a settings group.

        Title and caption on the left, wrapping to the room the controls
        leave; the controls on the right, each at its own system size, in
        reading order, ``ROW_CONTROL_GAP`` apart and centred on the row. A
        control that is hidden takes no room. ``leading`` is one view ahead
        of the text (an icon, a setup step's icon tile), kept at the size
        it was made. A row may instead hold one custom piece of content
        (the face grid) through ``custom``, a pair of functions: its height
        at a width, and its layout at a size. Hiding the row hides
        everything in it and closes the gap.

        Every group in the window is made of these, Home's and first-run
        setup's included, so a row has the same inset, padding and minimum
        height wherever it is.
        """

        def initWithFrame_(self, frame):
            self = objc.super(SettingsRowView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.title = None
            self.caption = None
            self.accessories = []
            self.custom = None
            self.leading = None
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def configure(self, title: Any = None, caption: Any = None,
                      accessories: Sequence[Any] = (),
                      custom: Any = None, leading: Any = None) -> Any:
            self.title = title
            self.caption = caption
            self.accessories = list(accessories)
            self.custom = custom
            self.leading = leading
            for view in (leading, title, caption, *self.accessories):
                if view is not None:
                    self.addSubview_(view)
            return self

        @objc.python_method
        def text_origin(self) -> float:
            """Where the row's text starts: the row inset, and past the
            leading view when the row has one."""
            if self.leading is None or bool(self.leading.isHidden()):
                return ROW_INSET
            return (ROW_INSET + float(self.leading.frame().size.width)
                    + ROW_CONTROL_GAP)

        @objc.python_method
        def text_width(self, width: float) -> float:
            shown = [view for view in self.accessories
                     if not bool(view.isHidden())]
            controls = sum(_natural_size(view)[0] for view in shown)
            controls += ROW_CONTROL_GAP * len(shown)
            return max(
                40.0, width - self.text_origin() - ROW_INSET - controls)

        @objc.python_method
        def say_the_title_once(self) -> None:
            """Keep VoiceOver from saying the title twice in a row.

            A caption is often labelled with its row's title, so that it
            says what it is about. VoiceOver then reads the title, and at
            once the caption's label: the same words again. While that
            is so the title itself is silent and the caption carries it.
            """
            if self.title is None:
                return
            carried = False
            if (self.caption is not None
                    and not bool(self.caption.isHidden())
                    and str(self.caption.stringValue())):
                try:
                    label = self.caption.cell().accessibilityLabel()
                except Exception:
                    label = None
                carried = (bool(label) and str(label).strip()
                           == str(self.title.stringValue()).strip())
            try:
                self.title.cell().setAccessibilityElement_(not carried)
            except Exception:
                pass

        @objc.python_method
        def arrange(self, width: float) -> float:
            """Lay the row out at ``width``; return its height."""
            self.say_the_title_once()
            if self.custom is not None:
                height = float(self.custom[0](width))
                self.custom[1](width, height)
                return height
            shown = [view for view in self.accessories
                     if not bool(view.isHidden())]
            sizes = [_natural_size(view) for view in shown]
            text_x = self.text_origin()
            text_width = self.text_width(width)
            title_height = (_text_height(self.title, text_width)
                            if self.title is not None else 0.0)
            caption_height = (_text_height(self.caption, text_width)
                              if self.caption is not None
                              and str(self.caption.stringValue()) else 0.0)
            gap = ROW_TEXT_GAP if title_height and caption_height else 0.0
            text_height = title_height + gap + caption_height
            controls_height = max((h for _w, h in sizes), default=0.0)
            lead_height = (float(self.leading.frame().size.height)
                           if text_x > ROW_INSET else 0.0)
            height = max(
                ROW_CAPTION_MIN_HEIGHT if self.caption is not None
                else ROW_MIN_HEIGHT,
                text_height + 2.0 * ROW_PADDING_Y,
                controls_height + 2.0 * ROW_CONTROL_PADDING_Y,
                lead_height + 2.0 * ROW_CONTROL_PADDING_Y)
            top = (height - text_height) / 2.0
            if lead_height:
                self.leading.setFrameOrigin_(
                    (ROW_INSET, (height - lead_height) / 2.0))
            if self.title is not None:
                self.title.setFrame_(NSMakeRect(
                    text_x, top, text_width, title_height))
            if self.caption is not None:
                self.caption.setFrame_(NSMakeRect(
                    text_x, top + title_height + gap,
                    text_width, caption_height))
            x = width - ROW_INSET
            for view, (w, h) in reversed(list(zip(shown, sizes))):
                x -= w
                _place(view, x, (height - h) / 2.0, w, h)
                x -= ROW_CONTROL_GAP
            return height

    class GroupBoxView(NSView):
        """A group of rows: one rounded surface with a hairline edge.

        It draws its fill, edge and the hairlines between its visible rows
        itself, in whichever appearance it is in, so it follows a light or
        dark flip, and Increase Contrast, without waiting for a render.
        Its rows are laid out top-down by ``arrange``. This is the one
        group in the window: the settings pages, Home and first-run setup
        all hold their rows in it.
        """

        def initWithFrame_(self, frame):
            self = objc.super(GroupBoxView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.rows = []
            self.separators = []
            # What was last marked for drawing (width, height, where the
            # separators are), and how many times it has been marked.
            self.drawn_shape = None
            self.marked = 0
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def name_for_voiceover(self, label: str) -> Any:
            """Have VoiceOver meet the group as a group with this name."""
            try:
                self.setAccessibilityElement_(True)
                self.setAccessibilityRole_(NSAccessibilityGroupRole)
                self.setAccessibilityLabel_(label)
            except Exception:
                pass
            return self

        @objc.python_method
        def add_row(self, row: Any) -> Any:
            self.rows.append(row)
            self.addSubview_(row)
            return row

        @objc.python_method
        def visible_rows(self) -> list[Any]:
            return [row for row in self.rows if not bool(row.isHidden())]

        @objc.python_method
        def arrange(self, width: float) -> float:
            """Stack the visible rows top-down; return the group height."""
            top = 0.0
            separators = []
            for index, row in enumerate(self.visible_rows()):
                height = row.arrange(width)
                row.setFrame_(NSMakeRect(0, top, width, height))
                if index:
                    separators.append(top)
                top += height
            self.separators = separators
            # Its fill and edge follow its size and its separators follow
            # the rows: nothing else it draws can change here, so it is
            # marked for drawing only when one of those did.
            shape = (float(width), top, tuple(separators))
            if shape != self.drawn_shape:
                self.drawn_shape = shape
                self.marked += 1
                self.setNeedsDisplay_(True)
            return top

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            palette = palette_for_appearance(dark)
            shape = BRAND_SHAPES["group"]
            bounds = self.bounds()
            outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSInsetRect(bounds, 0.5, 0.5), shape.radius - 0.5,
                shape.radius - 0.5)
            _srgb_color(palette.surface).setFill()
            outline.fill()
            hairline = _token_color("hairline", dark)
            hairline.setStroke()
            outline.setLineWidth_(shape.outline)
            outline.stroke()
            hairline.setFill()
            for y in self.separators:
                # The first point of the row below: one whole row of
                # pixels at any scale, never half of two.
                NSBezierPath.fillRect_(NSMakeRect(
                    ROW_INSET, math.floor(y),
                    bounds.size.width - ROW_INSET, 1.0))

    def arrange_section(header: Any, trailing: Any, group: Any,
                        notes: Sequence[Any], y: float,
                        width: float) -> float:
        """Lay one section out from ``y`` down in a flipped parent, at
        ``width``; return where it ends.

        The header, with ``trailing`` (small print, when it has any) at
        the far end of the header's line and sitting on its baseline, then
        the group, then each note that has text. The settings pages, Home
        and first-run setup all place their groups through this, so the
        header's inset and its distance from the group are the same
        everywhere.
        """
        inner = width - 2.0 * SECTION_HEADER_INSET
        if header is not None:
            noted = trailing is not None and bool(
                str(trailing.stringValue()))
            if trailing is not None:
                trailing.setHidden_(not noted)
            header_width = inner
            if noted:
                header_width = min(inner / 2.0, math.ceil(_text_size(
                    str(header.stringValue()), header.font())[0]) + 6.0)
            height = _text_height(header, header_width)
            header.setFrame_(NSMakeRect(
                SECTION_HEADER_INSET, y, header_width, height))
            if noted:
                x = SECTION_HEADER_INSET + header_width + ROW_CONTROL_GAP
                note_height = _text_height(trailing, inner)
                try:
                    drop = (float(header.firstBaselineOffsetFromTop())
                            - float(trailing.firstBaselineOffsetFromTop()))
                except Exception:
                    drop = height - note_height
                trailing.setFrame_(NSMakeRect(
                    x, y + drop,
                    max(0.0, width - SECTION_HEADER_INSET - x),
                    note_height))
            y += height + GROUP_HEADER_GAP
        height = group.arrange(width)
        group.setFrame_(NSMakeRect(0, y, width, height))
        y += height
        for note in notes:
            has_text = bool(str(note.stringValue()))
            note.setHidden_(not has_text)
            if has_text:
                height = _text_height(note, inner)
                note.setFrame_(NSMakeRect(
                    SECTION_HEADER_INSET, y + NOTE_GAP, inner, height))
                y += NOTE_GAP + height
        return y

    class GroupedPageView(NSView):
        """A settings page's scrolling document: headers, groups, notes.

        ``add_section`` appends a header (optional), a group and the notes
        under it, and optionally the small print at the trailing end of
        the header's line; a section whose rows are all hidden takes no
        room, and a note with no text takes none either. ``arrange`` lays
        everything out at the width of the clip view it sits in and sizes
        itself to its content, which is what lets the scroll view scroll
        exactly as far as there is something to see.
        """

        def initWithFrame_(self, frame):
            self = objc.super(GroupedPageView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.sections = []
            self.arranging = False
            # Space kept under the last group. A sheet's body sets its own:
            # its footer follows it, where a page has the window's edge.
            self.foot = CONTENT_INSET
            # What the page was last laid out for, and how many times it
            # has been laid out.
            self.arranged_for = None
            self.arranged = 0
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def layout_inputs(self) -> tuple[Any, ...]:
            """Everything a layout of the page is worked out from: its
            width, which of its views show, and what each one says (a
            text's words, a button's title, a key cap's key). Colours and
            states that take no room are not part of it."""
            inputs: list[Any] = [round(self.content_width(), 2)]

            def read(view: Any) -> None:
                words = ""
                if bool(view.isKindOfClass_(NSTextField)):
                    words = str(view.stringValue())
                elif bool(view.isKindOfClass_(NSButton)):
                    words = str(view.title())
                elif bool(view.isKindOfClass_(KeyCapView)):
                    words = view.key
                inputs.append((bool(view.isHidden()), words))
                if not bool(view.respondsToSelector_("cell")):
                    for child in view.subviews():
                        read(child)

            for child in self.subviews():
                read(child)
            return tuple(inputs)

        @objc.python_method
        def add_section(self, header: Any, group: Any,
                        notes: Sequence[Any] = (),
                        *, trailing: Any = None) -> Any:
            for view in (header, trailing, group, *notes):
                if view is not None:
                    self.addSubview_(view)
            self.sections.append((header, group, tuple(notes), trailing))
            return group

        @objc.python_method
        def content_width(self) -> float:
            clip = self.superview()
            if clip is not None:
                return float(clip.bounds().size.width)
            return float(self.frame().size.width)

        @objc.python_method
        def arrange(self, force: bool = False) -> float:
            """Lay the page out, unless it already is laid out for its
            width and its copy (``force`` lays it out regardless). Every
            render asks, and so does every pass of the scroll view; most
            of them have changed nothing."""
            if self.arranging:
                return float(self.frame().size.height)
            if not force and self.layout_inputs() == self.arranged_for:
                return float(self.frame().size.height)
            self.arranging = True
            try:
                self.arranged += 1
                width = self.content_width()
                y = PAGE_TOP
                first = True
                for header, group, notes, trailing in self.sections:
                    shown = bool(group.visible_rows())
                    for view in (header, trailing, group, *notes):
                        if view is not None:
                            view.setHidden_(not shown)
                    if not shown:
                        continue
                    if not first:
                        y += GROUP_GAP
                    first = False
                    y = arrange_section(
                        header, trailing, group, notes, y, width)
                y += self.foot
                if (abs(float(self.frame().size.width) - width) > 0.25
                        or abs(float(self.frame().size.height) - y) > 0.25):
                    self.setFrameSize_(NSMakeSize(width, y))
                # Read again after the layout: it shows and hides headers
                # and notes itself.
                self.arranged_for = self.layout_inputs()
                return y
            finally:
                self.arranging = False

    class PageScrollView(NSScrollView):
        """The scroll view under a settings page: the system's scrollers,
        no border and no background of its own, and a document that is
        laid out again whenever the scroll view is."""

        def tile(self):
            objc.super(PageScrollView, self).tile()
            document = self.documentView()
            if document is not None and hasattr(document, "arrange"):
                document.arrange()

    # ------------------------------------------------------------------
    # Brand components of the new look. Home and first-run setup are built
    # from them; the settings pages use the buttons, key caps and chips.
    #
    # Each one draws itself from the tokens in ``whisper_face_theme`` and
    # asks its own appearance for light or dark while drawing, so it follows
    # an appearance flip without waiting for the next render. Outline and
    # hard shadow always go through ``draw_hard_shadow_shape``.
    # ------------------------------------------------------------------

    def _srgb_color(
            color: tuple[float, float, float], alpha: float = 1.0) -> Any:
        """A theme colour as the sRGB value its hex names.

        The brand components draw with this rather than ``_theme_color``,
        which reads the same numbers as calibrated RGB and so renders every
        mid-tone lighter than its hex. Here the colour on screen is the
        colour the contrast tests measured.
        """
        return NSColor.colorWithSRGBRed_green_blue_alpha_(
            color[0], color[1], color[2], alpha)

    def _increase_contrast() -> bool:
        """Whether the person asked the system to increase contrast."""
        try:
            return bool(
                NSWorkspace.sharedWorkspace()
                .accessibilityDisplayShouldIncreaseContrast())
        except Exception:
            return False

    def _token_color(name: str, dark: bool) -> Any:
        """A named colour token for this appearance and contrast setting."""
        shade = color_token(name, dark, _increase_contrast())
        return _srgb_color(shade.color, shade.alpha)

    def _outline_ink(dark: bool, *, on_bright: bool = False,
                     control: bool = False) -> Any:
        """The ink for an object's outline and hard shadow.

        Three cases, by what the outline goes round:

        - A bright fill (amber, the stage, the selected chip): pass
          ``on_bright=True``. It stands off a dark window by itself and
          would lose its edge to a light outline, so it keeps the dark ink
          in both appearances and under Increase Contrast.
        - A control whose own fill is dark in the dark appearance (the
          plain chunky button, the key cap): pass ``control=True``. The
          dark ink would leave it with no edge there, so its outline is
          the light ink at the lowest opacity that measures 3:1; its hard
          shadow is drawn with the ``on_bright`` ink and stays dark.
        - Anything else (the bubble, the stat stickers): the ordinary ink,
          which Increase Contrast turns light in the dark appearance.

        That is the outline. A hard shadow is never lighter than the
        surface it falls on: ``draw_hard_shadow_shape`` draws every
        shadow with the ``on_bright`` ink, whatever the outline is.
        """
        if control:
            shade = color_token("control_edge", dark, _increase_contrast())
        else:
            shade = color_token(
                "ink_outline", dark,
                _increase_contrast() and not (dark and on_bright))
        return _srgb_color(shade.color, shade.alpha)

    def _role_font(role: str) -> Any:
        """The font for one named type role in ``TYPE_SPECS``."""
        spec = TYPE_SPECS[role]
        font = NSFont.systemFontOfSize_weight_(spec.size, spec.weight)
        try:
            descriptor = font.fontDescriptor()
            if spec.rounded:
                rounded = descriptor.fontDescriptorWithDesign_(
                    NSFontDescriptorSystemDesignRounded)
                if rounded is not None:
                    descriptor = rounded
            if spec.tabular:
                # Number spacing (6), monospaced numbers (0): the feature
                # pair behind ``monospacedDigitSystemFont``, which cannot
                # be combined with the rounded design any other way.
                descriptor = descriptor.fontDescriptorByAddingAttributes_({
                    NSFontFeatureSettingsAttribute: [{
                        NSFontFeatureTypeIdentifierKey: 6,
                        NSFontFeatureSelectorIdentifierKey: 0,
                    }]})
            styled = NSFont.fontWithDescriptor_size_(descriptor, spec.size)
            if styled is not None:
                font = styled
        except Exception:
            pass
        return font

    def _text_size(text: str, font: Any) -> tuple[float, float]:
        size = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font}).size()
        return float(size.width), float(size.height)

    def _draw_text(text: str, font: Any, color: Any, rect: Any,
                   *, centered: bool = False) -> None:
        """Draw one truncating line, vertically centred in ``rect``.

        For flipped views only: every brand component is flipped, so its
        geometry reads top-down like the tokens that describe it.
        """
        style = NSMutableParagraphStyle.alloc().init()
        style.setLineBreakMode_(NSLineBreakByTruncatingTail)
        style.setAlignment_(
            NSTextAlignmentCenter if centered else NSTextAlignmentLeft)
        attributed = NSAttributedString.alloc().initWithString_attributes_(
            text, {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: color,
                NSParagraphStyleAttributeName: style,
            })
        height = float(attributed.size().height)
        attributed.drawInRect_(NSMakeRect(
            rect.origin.x,
            rect.origin.y + (rect.size.height - height) / 2.0,
            rect.size.width, height))

    def hard_shadow_body_rect(bounds: Any, shape: BrandShape,
                              *, pressed: bool = False) -> Any:
        """The rectangle an object's body takes inside a flipped ``bounds``.

        The bounds are the whole footprint, shadow included. Content, focus
        rings and hit areas use this rectangle, so they follow the body
        when it is pressed down onto its shadow.
        """
        body, _shadow = hard_shadow_frames(
            bounds.size.width, bounds.size.height, shape, pressed=pressed)
        return NSMakeRect(
            bounds.origin.x + body[0], bounds.origin.y + body[1],
            body[2], body[3])

    def hard_shadow_path(rect: Any, shape: BrandShape,
                         *, radius: float | None = None) -> Any:
        """Rounded outline of an object's body, for fills, clips and masks."""
        corner = shape.radius if radius is None else radius
        corner = max(0.0, min(
            corner, rect.size.width / 2.0, rect.size.height / 2.0))
        return NSBezierPath \
            .bezierPathWithRoundedRect_xRadius_yRadius_(rect, corner, corner)

    def draw_hard_shadow_shape(bounds: Any, shape: BrandShape, *, fill: Any,
                               ink: Any, dark: bool, pressed: bool = False,
                               shadow: bool = True,
                               radius: float | None = None,
                               overlay: Callable[[Any], None] | None = None
                               ) -> Any:
        """Draw one ink-outlined object with its hard shadow; return its body.

        The one place the sticker treatment is drawn, so every brand object
        gets the same outline and the same shadow. Call it from a flipped
        view's ``drawRect_`` with the view's bounds: the shadow is the body's
        own shape in the ink colour, offset right and down with no blur, and
        the outline is stroked inside the body's edge so nothing is drawn
        outside ``bounds``.

        ``ink`` is the outline's. The shadow has one ink of its own, the
        dark one, in both appearances (``dark`` says which) and under
        Increase Contrast: an outline may turn light to keep its edge on
        a dark surface, but a shadow lighter than what it falls on reads
        as a glow. ``overlay`` is called with the body's
        rectangle after the fill and before the outline, clipped to the
        body, for whatever belongs to the surface itself (the stage's
        circles): the outline is then drawn once, over it.
        """
        body, cast = hard_shadow_frames(
            bounds.size.width, bounds.size.height, shape, pressed=pressed)
        body_rect = NSMakeRect(
            bounds.origin.x + body[0], bounds.origin.y + body[1],
            body[2], body[3])
        if shadow and (shape.shadow_right or shape.shadow_down):
            _outline_ink(dark, on_bright=True).setFill()
            hard_shadow_path(NSMakeRect(
                bounds.origin.x + cast[0], bounds.origin.y + cast[1],
                cast[2], cast[3]), shape, radius=radius).fill()
        fill.setFill()
        hard_shadow_path(body_rect, shape, radius=radius).fill()
        if overlay is not None:
            NSGraphicsContext.saveGraphicsState()
            hard_shadow_path(body_rect, shape, radius=radius).addClip()
            overlay(body_rect)
            NSGraphicsContext.restoreGraphicsState()
        if shape.outline > 0.0:
            inset = shape.outline / 2.0
            corner = shape.radius if radius is None else radius
            outline = hard_shadow_path(
                NSInsetRect(body_rect, inset, inset), shape,
                radius=max(0.0, corner - inset))
            outline.setLineWidth_(shape.outline)
            ink.setStroke()
            outline.stroke()
        return body_rect

    def _begin_faded(alpha: float) -> Any:
        """Start drawing a group that fades as one piece, not per stroke."""
        context = NSGraphicsContext.currentContext().CGContext()
        CGContextSaveGState(context)
        CGContextSetAlpha(context, alpha)
        CGContextBeginTransparencyLayer(context, None)
        return context

    def _end_faded(context: Any) -> None:
        CGContextEndTransparencyLayer(context)
        CGContextRestoreGState(context)

    class BrandButton(JellyButton):
        """What the brand's custom buttons share.

        They are NSButtons, so target, action, key equivalents, the Tab
        loop and Space all behave as on any button, and they ride the same
        press spring as every jelly control. They draw themselves, top-down.

        VoiceOver talks to a button's cell rather than to the button, so
        every accessibility property set here is handed to the cell too:
        the label, help, role and value a caller sets are the ones read.
        """

        def isFlipped(self):
            return True

        def wantsUpdateLayer(self):
            # Always drawn by drawRect_, never by a cell-provided layer.
            return False

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        @objc.python_method
        def is_pressed(self) -> bool:
            try:
                return bool(self.isEnabled()) and bool(
                    self.cell().isHighlighted())
            except Exception:
                return False

        def setAccessibilityLabel_(self, label):
            objc.super(BrandButton, self).setAccessibilityLabel_(label)
            if self.cell() is not None:
                self.cell().setAccessibilityLabel_(label)

        def setAccessibilityHelp_(self, text):
            objc.super(BrandButton, self).setAccessibilityHelp_(text)
            if self.cell() is not None:
                self.cell().setAccessibilityHelp_(text)

        def setAccessibilityRole_(self, role):
            objc.super(BrandButton, self).setAccessibilityRole_(role)
            if self.cell() is not None:
                self.cell().setAccessibilityRole_(role)

        def setAccessibilityValue_(self, value):
            objc.super(BrandButton, self).setAccessibilityValue_(value)
            if self.cell() is not None:
                self.cell().setAccessibilityValue_(value)

    class ChunkyButton(BrandButton):
        """The brand's push button: ink outline, hard shadow, heavy title.

        ``configure(title, variant="amber" | "plain", small=False)`` sets it
        up; target, action, key equivalent and enabled state are NSButton's
        own. Amber is the one primary action a surface may carry; plain is
        the secondary beside it. The view's frame is the whole footprint:
        use ``intrinsicContentSize`` (or Auto Layout) and the body leaves
        room for its own shadow. Pressed, the body travels onto the shadow;
        disabled, it fades and the shadow goes.
        """

        def initWithFrame_(self, frame):
            self = objc.super(ChunkyButton, self).initWithFrame_(frame)
            if self is None:
                return None
            self.variant = "amber"
            self.small = False
            self.setBordered_(False)
            self.setTitle_("")
            return self

        @objc.python_method
        def configure(self, title: str, *, variant: str = "amber",
                      small: bool = False) -> Any:
            if variant not in ("amber", "plain"):
                raise ValueError(f"unknown chunky button variant: {variant}")
            self.variant = variant
            self.small = bool(small)
            self.setTitle_(title)
            _accessible(self, title)
            self.invalidateIntrinsicContentSize()
            self.setNeedsDisplay_(True)
            return self

        @objc.python_method
        def shape(self) -> BrandShape:
            return BRAND_SHAPES[
                "chunky_button_small" if self.small else "chunky_button"]

        @objc.python_method
        def title_font(self) -> Any:
            return _role_font(
                "chunky_button_small" if self.small else "chunky_button")

        @objc.python_method
        def body_rect(self) -> Any:
            return hard_shadow_body_rect(
                self.bounds(), self.shape(), pressed=self.is_pressed())

        def intrinsicContentSize(self):
            shape = self.shape()
            width, _height = _text_size(
                str(self.title()), self.title_font())
            return NSMakeSize(*hard_shadow_footprint(
                math.ceil(width) + 2.0 * shape.padding_x, shape.height,
                shape))

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            palette = palette_for_appearance(dark)
            enabled = bool(self.isEnabled())
            amber = self.variant == "amber"
            context = None if enabled else _begin_faded(DISABLED_ALPHA)
            # Amber stands off any window by itself. The plain button's
            # fill is dark in the dark appearance, so its outline is the
            # control edge; its shadow, like every shadow, is the dark ink.
            body = draw_hard_shadow_shape(
                self.bounds(), self.shape(),
                fill=_srgb_color(
                    palette.accent if amber else palette.surface),
                ink=_outline_ink(dark, on_bright=amber, control=not amber),
                dark=dark, pressed=self.is_pressed(), shadow=enabled)
            _draw_text(
                str(self.title()), self.title_font(),
                _token_color("on_amber_ink", dark) if amber
                else _srgb_color(palette.ink),
                NSInsetRect(body, self.shape().outline + 2.0, 0.0),
                centered=True)
            if context is not None:
                _end_faded(context)

        def focusRingMaskBounds(self):
            return self.body_rect()

        def drawFocusRingMask(self):
            hard_shadow_path(self.body_rect(), self.shape()).fill()

    def key_cap_size(key: str) -> tuple[float, float]:
        """The footprint a key cap needs for this legend, shadow included."""
        shape = BRAND_SHAPES["key_cap"]
        width, _height = _text_size(key, _role_font("key_cap"))
        return hard_shadow_footprint(
            max(shape.height, math.ceil(width) + 2.0 * shape.padding_x),
            shape.height, shape)

    def draw_key_cap(bounds: Any, key: str, dark: bool) -> Any:
        """Draw one key cap into a flipped ``bounds``; return its body.

        The one place a key cap is drawn: ``KeyCapView`` calls it, and so
        does the cap that sits inside a line of copy. A cap is a control's
        shape with a fill that is dark in the dark appearance, so its
        outline is the control edge and its shadow stays the dark ink.
        """
        palette = palette_for_appearance(dark)
        body = draw_hard_shadow_shape(
            bounds, BRAND_SHAPES["key_cap"],
            fill=_srgb_color(palette.surface),
            ink=_outline_ink(dark, control=True), dark=dark)
        _draw_text(
            key, _role_font("key_cap"), _srgb_color(palette.ink),
            NSInsetRect(body, 3.0, 0.0), centered=True)
        return body

    class KeyCapView(NSView):
        """A key, drawn as a key cap, wherever copy names one.

        ``set_key("Right Option")`` sets the legend. It is text, not a
        control: VoiceOver reads the key's name once, as static text
        described as a key in the reader's language, and it takes no focus.
        """

        def initWithFrame_(self, frame):
            self = objc.super(KeyCapView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.key = ""
            try:
                self.setAccessibilityElement_(True)
                self.setAccessibilityRole_(NSAccessibilityStaticTextRole)
                self.setAccessibilityRoleDescription_(
                    localized_string("key_cap.accessibility.role"))
            except Exception:
                pass
            return self

        @objc.python_method
        def set_key(self, key: str, *, locale: str = "en") -> Any:
            self.key = str(key)
            try:
                # The name is the label and nothing else: a value that
                # repeats it would be read a second time.
                self.setAccessibilityLabel_(self.key)
                self.setAccessibilityRoleDescription_(localized_string(
                    "key_cap.accessibility.role", locale=locale))
            except Exception:
                pass
            self.invalidateIntrinsicContentSize()
            self.setNeedsDisplay_(True)
            return self

        def isFlipped(self):
            return True

        def intrinsicContentSize(self):
            return NSMakeSize(*key_cap_size(self.key))

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        def drawRect_(self, _rect):
            draw_key_cap(self.bounds(), self.key, _uses_dark_appearance(self))

    class StickerTileView(NSView):
        """One stat as a tilted sticker: the number large, its label under.

        ``configure(number, label, index)`` picks the tint and the tilt for
        the tile's place in the row (0, 1 or 2). The frame is the upright
        box the tilted tile is drawn inside, so neighbours never overlap;
        ``intrinsicContentSize`` gives the height a tile needs. VoiceOver
        reads it as one piece of text: the label, then the number.
        """

        PADDING_TOP = 9.0
        PADDING_BOTTOM = 10.0
        NUMBER_MIN_SIZE = 17.0

        def initWithFrame_(self, frame):
            self = objc.super(StickerTileView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.number = ""
            self.label = ""
            self.index = 0
            try:
                self.setAccessibilityElement_(True)
                self.setAccessibilityRole_(NSAccessibilityStaticTextRole)
            except Exception:
                pass
            return self

        @objc.python_method
        def configure(self, number: str, label: str,
                      index: int = 0) -> Any:
            self.number = str(number)
            self.label = str(label)
            self.index = int(index) % len(STICKER_TINTS)
            try:
                self.setAccessibilityLabel_(self.label)
                self.setAccessibilityValue_(self.number)
            except Exception:
                pass
            self.setNeedsDisplay_(True)
            return self

        @objc.python_method
        def tilt(self) -> float:
            return STICKER_TILTS[self.index]

        @objc.python_method
        def tile_size(self) -> tuple[float, float]:
            """Upright size of the tile, shadow included, before tilting."""
            bounds = self.bounds().size
            return tilted_inner_size(
                bounds.width, bounds.height, self.tilt())

        @objc.python_method
        def number_font(self, width: float) -> Any:
            """The stat font, stepped down until the number fits ``width``.

            A long value gets smaller before it gets cut: the tile keeps
            its height and the font stops at ``NUMBER_MIN_SIZE``, after
            which the line truncates.
            """
            font = _role_font("stat_number")
            size = TYPE_SPECS["stat_number"].size
            while (size > self.NUMBER_MIN_SIZE
                   and _text_size(self.number, font)[0] > width):
                size -= 1.0
                font = NSFont.fontWithDescriptor_size_(
                    font.fontDescriptor(), size) or font
            return font

        @objc.python_method
        def body_height(self) -> float:
            _w, number = _text_size("0", _role_font("stat_number"))
            _w, label = _text_size("0", _role_font("stat_label"))
            return (self.PADDING_TOP + math.ceil(number) + math.ceil(label)
                    + self.PADDING_BOTTOM)

        def isFlipped(self):
            return True

        def intrinsicContentSize(self):
            shape = BRAND_SHAPES["sticker"]
            width = float(self.bounds().size.width) or 200.0
            _w, height = hard_shadow_footprint(
                0.0, self.body_height(), shape)
            return NSMakeSize(NSViewNoIntrinsicMetric, math.ceil(
                tilted_footprint(width, height, self.tilt())[1]))

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            shape = BRAND_SHAPES["sticker"]
            bounds = self.bounds()
            width, height = self.tile_size()
            if width <= 0.0 or height <= 0.0:
                return
            NSGraphicsContext.saveGraphicsState()
            turn = NSAffineTransform.transform()
            turn.translateXBy_yBy_(
                bounds.origin.x + bounds.size.width / 2.0,
                bounds.origin.y + bounds.size.height / 2.0)
            # Flipped view: a positive angle turns clockwise on screen,
            # which is the sense the tilts are written in.
            turn.rotateByDegrees_(self.tilt())
            turn.concat()
            body = draw_hard_shadow_shape(
                NSMakeRect(-width / 2.0, -height / 2.0, width, height),
                shape,
                fill=_token_color(STICKER_TINTS[self.index], dark),
                ink=_outline_ink(dark), dark=dark)
            ink = color_token("sticker_ink", dark, _increase_contrast())
            number_font = _role_font("stat_number")
            label_font = _role_font("stat_label")
            _w, number_height = _text_size("0", number_font)
            _w, label_height = _text_size("0", label_font)
            text_x = body.origin.x + shape.padding_x
            text_width = max(0.0, body.size.width - 2.0 * shape.padding_x)
            top = body.origin.y + max(
                shape.outline,
                (body.size.height - self.body_height()) / 2.0
                + self.PADDING_TOP)
            _draw_text(
                self.number, self.number_font(text_width),
                _srgb_color(ink.color),
                NSMakeRect(text_x, top, text_width,
                           math.ceil(number_height)))
            _draw_text(
                self.label, label_font,
                _srgb_color(ink.color, STICKER_LABEL_ALPHA),
                NSMakeRect(text_x, top + math.ceil(number_height),
                           text_width, math.ceil(label_height)))
            NSGraphicsContext.restoreGraphicsState()

    class StagePanelView(NSView):
        """The stage: a coloured panel the face stands on, content beside it.

        ``set_face("fox")`` picks the stage colour that sets the face off.
        ``set_face_view(view)`` and ``set_content_view(view)`` hand it the
        two things it lays out: the face on the left, leaning a little, and
        the content (the speech bubble) filling the rest. The frame is the
        whole footprint, hard shadow included. It is a group for VoiceOver;
        give it a label with ``_accessible``.
        """

        def initWithFrame_(self, frame):
            self = objc.super(StagePanelView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.stage_token = stage_token_for_face("")
            self.face_view = None
            self.content_view = None
            self.face_size = STAGE_FACE_SIZE
            try:
                self.setAccessibilityElement_(True)
                self.setAccessibilityRole_(NSAccessibilityGroupRole)
            except Exception:
                pass
            return self

        @objc.python_method
        def set_face(self, face: str) -> Any:
            self.stage_token = stage_token_for_face(face)
            self.setNeedsDisplay_(True)
            return self

        @objc.python_method
        def set_face_view(self, view: Any,
                          size: float = STAGE_FACE_SIZE) -> Any:
            if self.face_view is not None and self.face_view is not view:
                self.face_view.removeFromSuperview()
            self.face_view = view
            self.face_size = float(size)
            if view is not None:
                self.addSubview_(view)
            self.arrange()
            return self

        @objc.python_method
        def set_content_view(self, view: Any) -> Any:
            if (self.content_view is not None
                    and self.content_view is not view):
                self.content_view.removeFromSuperview()
            self.content_view = view
            if view is not None:
                self.addSubview_(view)
            self.arrange()
            return self

        @objc.python_method
        def arrange(self) -> None:
            """Place the face and the content for the current size."""
            size = self.bounds().size
            face, content = stage_layout(
                size.width, size.height, face_size=self.face_size)
            if self.face_view is not None:
                self._place(self.face_view, self.face_frame(face))
                if not self.face_leans_itself():
                    # A plain view is leaned by turning its frame. In a
                    # flipped view a positive angle is clockwise on
                    # screen, the sense the tilt token is written in.
                    try:
                        self.face_view.setFrameCenterRotation_(
                            STAGE_FACE_TILT)
                    except Exception:
                        pass
            if self.content_view is not None:
                self._place(self.content_view, NSMakeRect(*content))

        @objc.python_method
        def face_leans_itself(self) -> bool:
            """Whether the face draws its own lean (``LiveFaceView`` does).

            A view that leans inside its own bounds is never rotated, so a
            wobble or pop spring on its layer keeps its centred anchor: a
            frame rotation is re-applied on every layout pass, which puts
            the layer's anchor back in its corner in the middle of a spring.
            """
            try:
                return bool(self.face_view.respondsToSelector_("setTilt:"))
            except Exception:
                return False

        @objc.python_method
        def face_frame(self, box: Any) -> Any:
            """Where the face view goes for a face drawn in ``box``."""
            rect = NSMakeRect(*box)
            if not self.face_leans_itself():
                try:
                    self.face_view.setFrameCenterRotation_(0.0)
                except Exception:
                    pass
                return rect
            # Its frame is the face's box and the clear room round it, so
            # the lean, the squash and the shadow have somewhere to go.
            bleed = STAGE_FACE_BLEED * rect.size.width / STAGE_FACE_SIZE
            self.face_view.setBleed_(bleed)
            self.face_view.setTilt_(STAGE_FACE_TILT)
            return NSInsetRect(rect, -bleed, -bleed)

        @objc.python_method
        def _place(self, view: Any, frame: Any) -> None:
            """Set a frame only when it changes: an unchanged frame must
            not disturb a layer that is in the middle of a spring."""
            current = view.frame()
            if (abs(current.origin.x - frame.origin.x) > 0.01
                    or abs(current.origin.y - frame.origin.y) > 0.01
                    or abs(current.size.width - frame.size.width) > 0.01
                    or abs(current.size.height - frame.size.height) > 0.01):
                view.setFrame_(frame)

        def isFlipped(self):
            return True

        def resizeSubviewsWithOldSize_(self, _old_size):
            self.arrange()

        def layout(self):
            # Under Auto Layout the stage is sized by constraints and never
            # sees a frame-driven resize, so it arranges here as well.
            objc.super(StagePanelView, self).layout()
            self.arrange()

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            shape = BRAND_SHAPES["stage"]

            def circles(body: Any) -> None:
                # The circles belong to the stage's surface: the shadow
                # helper clips them to it and draws the outline over them.
                for circle in STAGE_CIRCLES:
                    x = (body.origin.x + body.size.width - circle.inset_x
                         - circle.diameter if circle.from_right
                         else body.origin.x + circle.inset_x)
                    y = (body.origin.y + body.size.height - circle.inset_y
                         - circle.diameter if circle.from_bottom
                         else body.origin.y + circle.inset_y)
                    wash = 1.0 if circle.lighten else 0.0
                    _srgb_color((wash, wash, wash), circle.alpha).setFill()
                    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(
                        x, y, circle.diameter, circle.diameter)).fill()

            draw_hard_shadow_shape(
                self.bounds(), shape,
                fill=_token_color(self.stage_token, dark),
                ink=_outline_ink(dark, on_bright=True), dark=dark,
                overlay=circles)

    class FaceChipButton(BrandButton):
        """One face in the picker grid.

        ``configure(image, name, selected=False)`` sets the art and the
        VoiceOver name; ``set_selected`` flips the choice. At rest the chip
        is a faint tile; selected it is amber with the ink outline and hard
        shadow. It is a radio button for VoiceOver, with its name as the
        label and the selection as the value. The body keeps the same place
        in both states, so choosing a face never nudges the art.
        """

        ART_INSET = 6.0

        def initWithFrame_(self, frame):
            self = objc.super(FaceChipButton, self).initWithFrame_(frame)
            if self is None:
                return None
            self.face_image = None
            self.selected = False
            self.setBordered_(False)
            self.setTitle_("")
            try:
                self.setAccessibilityRole_(NSAccessibilityRadioButtonRole)
            except Exception:
                pass
            return self

        @objc.python_method
        def configure(self, image: Any, name: str,
                      *, selected: bool = False) -> Any:
            self.face_image = image
            self.setToolTip_(name)
            _accessible(self, name)
            return self.set_selected(selected)

        @objc.python_method
        def set_selected(self, selected: bool) -> Any:
            self.selected = bool(selected)
            try:
                self.setAccessibilityValue_(1 if self.selected else 0)
            except Exception:
                pass
            self.setNeedsDisplay_(True)
            return self

        @objc.python_method
        def corner_radius(self) -> float:
            # Small chips keep the tile's proportions rather than its
            # radius, so a 44pt chip is not rounded into a circle.
            side = min(self.bounds().size.width, self.bounds().size.height)
            return min(BRAND_SHAPES["face_chip"].radius, side * 0.28)

        @objc.python_method
        def body_rect(self) -> Any:
            return hard_shadow_body_rect(
                self.bounds(), BRAND_SHAPES["face_chip"],
                pressed=self.selected and self.is_pressed())

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            palette = palette_for_appearance(dark)
            shape = BRAND_SHAPES["face_chip"]
            radius = self.corner_radius()
            body = self.body_rect()
            if self.selected:
                draw_hard_shadow_shape(
                    self.bounds(), shape,
                    fill=_srgb_color(palette.accent),
                    ink=_outline_ink(dark, on_bright=True), dark=dark,
                    pressed=self.is_pressed(), radius=radius)
            else:
                tile = hard_shadow_path(body, shape, radius=radius)
                _token_color("chip_rest", dark).setFill()
                tile.fill()
                if self.is_pressed():
                    # Pressed, the resting tile darkens by a hairline's
                    # worth of ink, so the press shows before the choice.
                    _token_color("hairline", dark).setFill()
                    tile.fill()
                if _increase_contrast():
                    edge = hard_shadow_path(
                        NSInsetRect(body, 0.5, 0.5), shape,
                        radius=radius - 0.5)
                    edge.setLineWidth_(1.0)
                    _token_color("hairline", dark).setStroke()
                    edge.stroke()
            if self.face_image is not None:
                inset = self.ART_INSET
                self.face_image \
                    .drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                        NSInsetRect(body, inset, inset), NSZeroRect,
                        NSCompositingOperationSourceOver, 1.0, True, None)

        def focusRingMaskBounds(self):
            return self.body_rect()

        def drawFocusRingMask(self):
            hard_shadow_path(
                self.body_rect(), BRAND_SHAPES["face_chip"],
                radius=self.corner_radius()).fill()

    # ------------------------------------------------------------------
    # The shell: what the sidebar is made of. The split view, the sidebar
    # material and the divider are the system's; these are the only custom
    # pieces, and they stay quiet, because navigation is something a Mac
    # user operates, not a brand moment.
    # ------------------------------------------------------------------

    class ShellView(NSView):
        """A plain container whose subviews are placed from the top down."""

        def isFlipped(self):
            return True

    class ShellSplitViewController(NSSplitViewController):
        """The window's two columns: the sidebar and the content side.

        The sidebar has one width, so its divider is a line, not a handle:
        it reports no area to grab, which also keeps the pointer from
        turning into a resize cursor over it.
        """

        def splitView_effectiveRect_forDrawnRect_ofDividerAtIndex_(
                self, _split_view, _proposed, _drawn, _index):
            return NSZeroRect

    class SidebarRowButton(BrandButton):
        """One page in the sidebar: an SF Symbol, a label, a selection fill.

        ``configure(title, symbol)`` sets it up and ``set_selected`` marks
        the page that is showing. It is an NSButton, so it takes its target,
        action and key equivalent like one and Space presses it. For the
        keyboard the rows behave as one list: where the system lets Tab
        reach buttons at all (its keyboard-navigation setting), Tab stops
        once, on the selected row, and Up and Down are handed to the
        window's delegate, which moves through the rows. Command-1 to
        Command-5 reach every row whatever that setting is.

        The selected row is filled with the theme's sidebar selection while
        the window is the key window and with a quiet neutral when it is
        not, the way a system sidebar dims. It does not squash when
        pressed: a sidebar stays still under the pointer.
        """

        KEY_DOWN_ARROW = 125
        KEY_UP_ARROW = 126

        def initWithFrame_(self, frame):
            self = objc.super(SidebarRowButton, self).initWithFrame_(frame)
            if self is None:
                return None
            self.selected = False
            self.emphasized = True
            self.setBordered_(False)
            self.setTitle_("")
            icon = NSImageView.alloc().initWithFrame_(NSMakeRect(
                SIDEBAR_ROW_INSET,
                (frame.size.height - SIDEBAR_ICON_SIZE) / 2.0,
                SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE))
            icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            icon.setEditable_(False)
            try:
                # The row is the one thing VoiceOver should find here.
                icon.setAccessibilityElement_(False)
                # Some symbols swap to a filled drawing in the dark
                # appearance. The rows are one family of outline icons in
                # both, so the picture is always resolved as in light; its
                # colour is set explicitly and does not depend on this.
                icon.setAppearance_(
                    NSAppearance.appearanceNamed_(NSAppearanceNameAqua))
            except Exception:
                pass
            self.addSubview_(icon)
            self.icon_view = icon
            return self

        @objc.python_method
        def configure(self, title: str, symbol: str) -> Any:
            self.setTitle_(title)
            image = _symbol_image(symbol, size=14.0, weight="medium")
            if image is not None:
                self.icon_view.setImage_(image)
            _accessible(self, title)
            self.sync_icon()
            self.setNeedsDisplay_(True)
            return self

        @objc.python_method
        def set_selected(self, selected: bool,
                         *, emphasized: bool = True) -> Any:
            self.selected = bool(selected)
            self.emphasized = bool(emphasized)
            for element in (self, self.cell()):
                try:
                    element.setAccessibilitySelected_(self.selected)
                except Exception:
                    pass
            self.sync_icon()
            self.setNeedsDisplay_(True)
            return self

        @objc.python_method
        def sync_icon(self) -> None:
            """The icon is ink on the selected row and soft ink elsewhere."""
            palette = palette_for_appearance(_uses_dark_appearance(self))
            try:
                self.icon_view.setContentTintColor_(_srgb_color(
                    palette.ink if self.selected else palette.ink_soft))
            except Exception:
                pass

        @objc.python_method
        def title_font(self) -> Any:
            # The selection fill is pale by design (about 1.2:1 against
            # the sidebar in light), so the label carries the state too:
            # bold on the page that is showing, medium on the others.
            return NSFont.systemFontOfSize_weight_(
                13.0, NSFontWeightBold if self.selected
                else NSFontWeightMedium)

        @objc.python_method
        def shape_path(self) -> Any:
            return NSBezierPath \
                .bezierPathWithRoundedRect_xRadius_yRadius_(
                    self.bounds(), SIDEBAR_ROW_RADIUS, SIDEBAR_ROW_RADIUS)

        def viewDidChangeEffectiveAppearance(self):
            self.sync_icon()
            self.setNeedsDisplay_(True)

        @objc.python_method
        def takes_tab_stop(self, buttons_take_tab: bool) -> bool:
            """One Tab stop for the whole sidebar: the row that is showing.

            Whether a button may take a Tab stop at all stays the system's
            decision (its keyboard-navigation setting), passed in here so
            the rule can be checked on a machine where it is off.
            """
            return bool(self.selected) and bool(buttons_take_tab)

        def canBecomeKeyView(self):
            return self.takes_tab_stop(bool(
                objc.super(SidebarRowButton, self).canBecomeKeyView()))

        def hitTest_(self, point):
            # The icon is a picture, not a second target inside the row.
            hit = objc.super(SidebarRowButton, self).hitTest_(point)
            return self if hit is not None else None

        def mouseDown_(self, event):
            # Past JellyButton on purpose: no press spring on navigation.
            objc.super(JellyButton, self).mouseDown_(event)

        def keyDown_(self, event):
            code = int(event.keyCode())
            if code in (self.KEY_DOWN_ARROW, self.KEY_UP_ARROW):
                window = self.window()
                delegate = window.delegate() if window is not None else None
                move = getattr(delegate, "move_sidebar_focus", None)
                if move is not None:
                    move(self, 1 if code == self.KEY_DOWN_ARROW else -1)
                    return
            objc.super(SidebarRowButton, self).keyDown_(event)

        def drawRect_(self, _rect):
            dark = _uses_dark_appearance(self)
            palette = palette_for_appearance(dark)
            bounds = self.bounds()
            if self.selected:
                fill = (_token_color("sidebar_selection", dark)
                        if self.emphasized
                        else _srgb_color(palette.ink, 0.10))
                fill.setFill()
                self.shape_path().fill()
            if self.is_pressed():
                _srgb_color(palette.ink, 0.08).setFill()
                self.shape_path().fill()
            if self.selected and _increase_contrast():
                edge = NSBezierPath \
                    .bezierPathWithRoundedRect_xRadius_yRadius_(
                        NSInsetRect(bounds, 0.5, 0.5),
                        SIDEBAR_ROW_RADIUS - 0.5, SIDEBAR_ROW_RADIUS - 0.5)
                edge.setLineWidth_(1.0)
                _srgb_color(palette.ink).setStroke()
                edge.stroke()
            text_x = 2.0 * SIDEBAR_ROW_INSET + SIDEBAR_ICON_SIZE
            _draw_text(
                str(self.title()), self.title_font(),
                _srgb_color(palette.ink),
                NSMakeRect(
                    text_x, 0.0,
                    bounds.size.width - text_x - SIDEBAR_ROW_INSET,
                    bounds.size.height))

        def focusRingMaskBounds(self):
            return self.bounds()

        def drawFocusRingMask(self):
            self.shape_path().fill()

    # ------------------------------------------------------------------
    # Editor sheets. An editor (app tones, snippets, vocabulary, learned
    # corrections, pronunciation keywords) is a sheet on the window, made
    # of the pages' own parts: a header with the chosen face on its chip,
    # a body of grouped rows that scrolls when it has to, and a footer of
    # system buttons. A dialog is structure, so it is quiet and native;
    # the face on its chip is the sheet's one brand moment.
    # ------------------------------------------------------------------

    SHEET_WIDTH = 540.0
    # Sheet edge to the header, the body and the footer's buttons.
    SHEET_INSET = 20.0
    SHEET_FACE_SIZE = 40.0
    SHEET_HEADER_GAP = 12.0     # face chip to the title beside it
    SHEET_TITLE_GAP = 2.0       # title to the line under it
    SHEET_BODY_GAP = 12.0       # header to the scrolling body
    # Under the last group, the page's own top margin again: the body sits
    # as far from the footer's rule as from the header.
    SHEET_BODY_FOOT = PAGE_TOP
    SHEET_FOOTER_GAP = 16.0     # body to the footer's buttons
    SHEET_BUTTON_MIN_WIDTH = 76.0
    # The sheet stays this far inside the window at its minimum size; a
    # body taller than that leaves scrolls.
    SHEET_WINDOW_MARGIN = 48.0
    SHEET_MAX_HEIGHT = WINDOW_MIN_SIZE[1] - 2.0 * SHEET_WINDOW_MARGIN
    SHEET_APP_ICON_SIZE = 28.0
    # A multi-line field's text, top to bottom, before it scrolls.
    SHEET_EDITOR_HEIGHT = 96.0
    # A multi-line field's own text is set in from its edge by this much
    # (an NSTextView's line fragment padding).
    SHEET_EDITOR_TEXT_PADDING = 5.0
    SHEET_EMPTY_PADDING = 24.0  # an empty list's sentence, above and below
    # The sidebar's face sits 4 points inside its 44 point chip; a chip of
    # another size keeps the proportion.
    FACE_CHIP_ART_INSET = 4.0 / SIDEBAR_FACE_SIZE
    # Sheet answers start clear of an alert's (1000, 1001, ...).
    SHEET_FIRST_TAG = 2000

    def sheet_alignment(view: Any) -> tuple[float, float, float, float]:
        """Where a control in a sheet is drawn: the left, top, right and
        bottom of its alignment rectangle, from the top of its flipped
        parent. A system button's frame is larger than its bezel, and by
        more below it than above."""
        frame, insets = view.frame(), view.alignmentRectInsets()
        return (
            float(frame.origin.x + insets.left),
            float(frame.origin.y + insets.top),
            float(frame.origin.x + frame.size.width - insets.right),
            float(frame.origin.y + frame.size.height - insets.bottom))

    def place_in_sheet(view: Any, x: float, y: float, width: float,
                       height: float) -> None:
        """Put a control's alignment rectangle exactly at (x, y), measured
        from the top of its flipped parent."""
        insets = view.alignmentRectInsets()
        view.setFrame_(NSMakeRect(
            x - insets.left, y - insets.top,
            width + insets.left + insets.right,
            height + insets.top + insets.bottom))

    def draw_face_chip(rect: Any, image: Any, face: str, dark: bool) -> None:
        """The chosen face on its chip, the way the sidebar shows it: the
        chip's own colour, the picker's corner proportion, a hairline
        edge, and the face a little inside it. For a flipped context."""
        side = min(float(rect.size.width), float(rect.size.height))
        radius = min(BRAND_SHAPES["face_chip"].radius, side * 0.28)
        edge = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSInsetRect(rect, 0.5, 0.5), radius - 0.5, radius - 0.5)
        _srgb_color(FACE_CHIP_COLORS[face]).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, radius, radius).fill()
        _token_color("hairline", dark).setStroke()
        edge.setLineWidth_(1.0)
        edge.stroke()
        if image is not None:
            inset = side * FACE_CHIP_ART_INSET
            image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                NSInsetRect(rect, inset, inset), NSZeroRect,
                NSCompositingOperationSourceOver, 1.0, True, None)

    def face_chip_icon(image: Any, face: str, side: float = 64.0) -> Any:
        """The face on its chip as an image, for a system alert's icon.

        It is drawn when it is shown, so it follows the appearance the
        alert is in. An alert with no icon of its own shows the
        application's, and this process has none: a generic folder.
        """
        def draw(rect: Any) -> bool:
            try:
                appearance = NSAppearance.currentDrawingAppearance()
                dark = str(appearance.bestMatchFromAppearancesWithNames_(
                    [NSAppearanceNameAqua, NSAppearanceNameDarkAqua])) == \
                    str(NSAppearanceNameDarkAqua)
            except Exception:
                dark = False
            draw_face_chip(rect, image, face, dark)
            return True

        return NSImage.imageWithSize_flipped_drawingHandler_(
            NSMakeSize(side, side), True, draw)

    def app_icon_image(bundle: str) -> tuple[Any, bool]:
        """An app's own icon, asked of the system by bundle identifier, and
        whether it was found. An app that is not on this Mac gets a
        plain app symbol: the dashed outline of an icon, which cannot be
        taken for a checkbox the way a solid one can."""
        try:
            workspace = NSWorkspace.sharedWorkspace()
            url = workspace.URLForApplicationWithBundleIdentifier_(bundle)
            if url is not None:
                image = workspace.iconForFile_(url.path())
                if image is not None:
                    return _drawn_image(image, SHEET_APP_ICON_SIZE), True
        except Exception:
            pass
        return _symbol_image("app.dashed", size=22.0, weight="regular"), False

    def _drawn_image(image: Any, side: float, scale: int = 3) -> Any:
        """``image`` drawn now, into a bitmap of its own, ``side`` points
        square.

        The system hands out an app's icon as something it renders when
        it is first asked to, and an image view that asks is shown a
        dashed placeholder until it has. Drawn into a bitmap it comes at
        once, so a row never opens with a placeholder for an icon.
        """
        pixels = int(side * scale)
        bitmap = NSBitmapImageRep.alloc() \
            .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, pixels, pixels, 8, 4, True, False,
                NSDeviceRGBColorSpace, 0, 0)
        bitmap = bitmap.bitmapImageRepByRetaggingWithColorSpace_(
            NSColorSpace.sRGBColorSpace())
        bitmap.setSize_(NSMakeSize(side, side))
        NSGraphicsContext.saveGraphicsState()
        try:
            NSGraphicsContext.setCurrentContext_(
                NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
            image.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(0, 0, side, side), NSZeroRect,
                NSCompositingOperationSourceOver, 1.0)
        finally:
            NSGraphicsContext.restoreGraphicsState()
        drawn = NSImage.alloc().initWithSize_(NSMakeSize(side, side))
        drawn.addRepresentation_(bitmap)
        return drawn

    class FaceChipView(NSView):
        """The chosen face on its chip, still: a sheet's header."""

        def initWithFrame_(self, frame):
            self = objc.super(FaceChipView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.face = FACES[0]
            self.face_image = None
            try:
                # The title beside it names the sheet; this is a picture.
                self.setAccessibilityElement_(False)
            except Exception:
                pass
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def configure(self, image: Any, face: str) -> Any:
            self.face_image = image
            self.face = face
            self.setNeedsDisplay_(True)
            return self

        def viewDidChangeEffectiveAppearance(self):
            self.setNeedsDisplay_(True)

        def drawRect_(self, _rect):
            draw_face_chip(self.bounds(), self.face_image, self.face,
                           _uses_dark_appearance(self))

    class SheetContentView(NSView):
        """A sheet's content, placed from the top down. It tells its sheet
        when the appearance it is in changes, so the sheet's colours
        follow a light or dark flip while it is open."""

        def isFlipped(self):
            return True

        def viewDidChangeEffectiveAppearance(self):
            repaint = getattr(self, "repaint", None)
            if repaint is not None:
                repaint()

    class SheetWindow(NSWindow):
        """The window a sheet is. Escape and Command-period reach it as
        ``cancelOperation:`` when no button holds Escape itself, which is
        how a sheet with one Done button is dismissed from the keyboard.
        Command-Return presses the default button from anywhere, a
        multi-line field included, where Return alone starts a new line."""

        def cancelOperation_(self, _sender):
            leave = getattr(self, "leave", None)
            if leave is not None:
                leave()

        def performKeyEquivalent_(self, event):
            try:
                command_return = (
                    bool(int(event.modifierFlags()) & _COMMAND_KEY_FLAG)
                    and str(event.charactersIgnoringModifiers()) == "\r")
                cell = self.defaultButtonCell()
                if command_return and cell is not None \
                        and bool(cell.isEnabled()):
                    cell.controlView().performClick_(self)
                    return True
            except Exception:
                pass
            return objc.super(SheetWindow, self).performKeyEquivalent_(event)

    class SheetTextView(NSTextView):
        """A multi-line field in a sheet. Tab and Shift-Tab move through
        the sheet instead of typing a tab (Option-Tab still types one),
        so the keyboard is never held inside the field, and Escape
        cancels the sheet instead of asking for a completion."""

        def insertTab_(self, sender):
            self.window().selectNextKeyView_(sender)

        def insertBacktab_(self, sender):
            self.window().selectPreviousKeyView_(sender)

        def cancelOperation_(self, sender):
            self.window().cancelOperation_(sender)

    class EditorSheet:
        """One editor: the sheet's window and what is on it, by name.

        ``controls`` are the fields and pop-ups a save reads, ``buttons``
        everything that can be pressed. Pressing a button answers the
        sheet: ``choice`` becomes ``(action, item)``, where the action is
        the button's name up to its first colon ("delete" for
        "delete:signature") and the item is what the button acts on.

        ``run`` shows the sheet on its window and waits for an answer.
        The sheet stays up between runs, so an editor is a straight line:
        fill the sheet, run it, act on the answer, fill it again. Given a
        responder (the native smoke's and the render probe's seam) it
        shows nothing: the responder is handed the sheet and answers it,
        by pressing a button itself or by returning a button's name.
        """

        def __init__(self) -> None:
            self.window: Any = None
            self.content: Any = None
            self.chip: Any = None
            self.title: Any = None
            self.message: Any = None
            self.scroll: Any = None
            self.document: Any = None
            self.rule: Any = None
            self.controls: dict[str, Any] = {}
            self.buttons: dict[str, Any] = {}
            self.items: dict[int, tuple[str, Any]] = {}
            self.footer: list[Any] = []
            self.key_views: list[Any] = []
            # Views that take the palette, by role, like the window's.
            self.themed: dict[str, list[Any]] = {}
            # What a list shows, beside the control that edits it.
            self.listed: list[tuple[Any, Any]] = []
            self.choice: tuple[str, Any] | None = None
            self.tag = 0
            self.parent: Any = None
            self.running = False
            self.next_tag = SHEET_FIRST_TAG
            # Where the keyboard was on each page the sheet has shown, by
            # the page's title: a list filled again keeps its place.
            self.focus_index: dict[str, int] = {}

        def register(self, name: str, button: Any, item: Any = None) -> int:
            """Name a button and what it acts on; return its tag."""
            if name in self.buttons:
                raise ValueError(f"two sheet buttons are named {name!r}")
            tag = self.next_tag
            self.next_tag += 1
            self.buttons[name] = button
            self.items[tag] = (name, item)
            return tag

        def choose(self, tag: int) -> None:
            """A button was pressed: record the answer and end the run."""
            name, item = self.items[int(tag)]
            self.choice = (name.split(":", 1)[0], item)
            self.tag = int(tag)
            if self.running:
                NSApplication.sharedApplication().stopModalWithCode_(
                    int(tag))

        def press(self, name: str) -> int:
            """Press a button by name, through its own target and action,
            as a click would. A button that is hidden or disabled cannot
            be pressed."""
            button = self.buttons[name]
            if bool(button.isHidden()) or not bool(button.isEnabled()):
                raise ValueError(f"the sheet's {name!r} cannot be pressed")
            button.sendAction_to_(button.action(), button.target())
            return self.tag

        def leave(self) -> None:
            """Escape: Cancel where the sheet has one, otherwise Done."""
            for name in ("cancel", "done"):
                if name in self.buttons:
                    self.press(name)
                    return

        def run(self, parent: Any, responder: Any = None) -> int:
            self.choice = None
            self.tag = 0
            if responder is not None:
                answer = responder(self)
                if self.choice is None:
                    if isinstance(answer, str):
                        self.press(answer)
                    else:
                        self.choose(int(answer))
                return self.tag
            if self.parent is None and parent is not None \
                    and bool(parent.isVisible()):
                # On its window, as a sheet. Without a window on screen
                # to hang from, the run below still shows it, as a
                # dialog of its own.
                try:
                    parent.beginSheet_completionHandler_(self.window, None)
                    self.parent = parent
                except Exception:
                    self.parent = None
            self.running = True
            try:
                NSApplication.sharedApplication().runModalForWindow_(
                    self.window)
            finally:
                self.running = False
            if self.choice is None:
                # The run ended without a button: treat it as Escape.
                self.leave()
            return self.tag

        def end(self) -> None:
            """Take the sheet down and let go of everything on it."""
            if self.parent is not None:
                try:
                    self.parent.endSheet_(self.window)
                except Exception:
                    pass
                self.parent = None
            if self.window is not None:
                self.window.orderOut_(None)
                self.window.leave = None
            if self.content is not None:
                self.content.repaint = None

    class WhisperFaceWindowController(NSObject):
        """One-window AppKit controller; created lazily by ``WhisperFaceGUI``."""

        @objc.python_method
        def _initialize(self, view_model: WhisperFaceViewModel,
                        *, read_system_state: bool) -> Any:
            self.view_model = view_model
            self.pages: dict[str, Any] = {}
            self.dynamic: dict[str, Any] = {}
            # Views that must re-read the palette when the appearance flips.
            # Roles: ink, ink_soft.
            self.themed: dict[str, list[Any]] = {}
            self.timer = None
            self.defaults = None
            # Set only by the native smoke: answers dialogs without a modal.
            self.modal_responder: Callable[[Any], int] | None = None
            self._face_images: dict[tuple[str, bool], Any] = {}
            # The face on its chip, as a system alert's icon, by face.
            self._face_icons: dict[str, Any] = {}
            # The editor sheet that is open, and where labels made while
            # one is being filled are kept for theming: the sheet, not
            # the window, so a closed sheet leaves nothing behind.
            self._sheet: Any = None
            self._theme_sink: dict[str, list[Any]] | None = None
            self._onboarding_stage: str | None = None
            self._onboarding_presentation: OnboardingPresentation | None = None
            self._onboarding_completed: frozenset[str] = frozenset()
            self._onboarding_good: (
                tuple[str, frozenset[str]] | None) = None
            self._home_landed: tuple[Any, ...] | None = None
            self._rendered_row: int | None = None
            self._rendered_notice: str | None = None
            # Shell state. Setup mode hides the sidebar and the title strip;
            # the window counts as active until it is told otherwise, so a
            # window that has never been on screen draws its active look.
            self.setup_mode = False
            self._window_active = True
            # What the last render was drawn from, and how many there
            # have been.
            self._rendered_from: tuple[Any, ...] | None = None
            self.renders = 0
            # Whether the last render saw Increase Contrast on.
            self._contrast: bool | None = None
            # Trailing title-strip items: (row key, view, visible-when).
            self.title_items: list[tuple[str, Any, Any]] = []
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
        def _register(self, role: str, *views: Any) -> None:
            themed = (self.themed if self._theme_sink is None
                      else self._theme_sink)
            themed.setdefault(role, []).extend(views)

        @objc.python_method
        def _ink(self, text: str, frame: Any, **kwargs: Any) -> Any:
            label = _label(text, frame, **kwargs)
            self._register("ink", label)
            return label

        @objc.python_method
        def _soft(self, text: str, frame: Any, **kwargs: Any) -> Any:
            label = _label(text, frame, **kwargs)
            self._register("ink_soft", label)
            return label

        @objc.python_method
        def _chunky(self, title: str, action: str, *,
                    variant: str = "amber", small: bool = False,
                    help_text: str = "") -> Any:
            """A chunky button wired to this controller, at its own size."""
            button = ChunkyButton.alloc().initWithFrame_(
                NSMakeRect(0, 0, 10, 10)).configure(
                    title, variant=variant, small=small)
            button.setTarget_(self)
            button.setAction_(action)
            button.setFrameSize_(button.intrinsicContentSize())
            return _accessible(button, title, help_text)

        @objc.python_method
        def _set_chunky_title(self, button: Any, title: str,
                              variant: str = "") -> None:
            """Retitle a chunky button, and restyle it when a variant is
            given, and let it take its new width."""
            variant = variant or button.variant
            if str(button.title()) != title or button.variant != variant:
                button.configure(title, variant=variant, small=button.small)
                button.setFrameSize_(button.intrinsicContentSize())

        @objc.python_method
        def _fill(self, parent: Any, view: Any) -> Any:
            """Pin a view to all four edges of its parent."""
            view.setTranslatesAutoresizingMaskIntoConstraints_(False)
            parent.addSubview_(view)
            NSLayoutConstraint.activateConstraints_([
                view.topAnchor().constraintEqualToAnchor_(
                    parent.topAnchor()),
                view.leadingAnchor().constraintEqualToAnchor_(
                    parent.leadingAnchor()),
                view.trailingAnchor().constraintEqualToAnchor_(
                    parent.trailingAnchor()),
                view.bottomAnchor().constraintEqualToAnchor_(
                    parent.bottomAnchor()),
            ])
            return view

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
        def _wrapped_height(self, label: Any, width: float) -> float:
            """Height this label needs for its current text at ``width``."""
            try:
                size = label.cell().cellSizeForBounds_(
                    NSMakeRect(0, 0, width, 10_000.0))
                return float(size.height)
            except Exception:
                return float(label.frame().size.height)

        @objc.python_method
        def _fit_headline(self, label: Any, width: float) -> float:
            """Give a bubble headline the largest size that fits; return
            the height it then needs at ``width``.

            The headline is 27pt when it fits one line and steps down to
            ``BUBBLE_HEADLINE_SMALL`` when it would not; at either size it
            may take two lines before it is cut.
            """
            text = str(label.stringValue())
            font = _role_font("bubble_headline")
            if _text_size(text, font)[0] > width - 4.0:
                font = NSFont.fontWithDescriptor_size_(
                    font.fontDescriptor(), BUBBLE_HEADLINE_SMALL) or font
            label.setFont_(font)
            line = math.ceil(_text_size("Xg", font)[1])
            needed = math.ceil(self._wrapped_height(label, width))
            return float(min(2 * line, max(line, needed)))

        @objc.python_method
        def _layout_actions(self, actions: Any, width: float) -> float:
            """Line a bubble's visible buttons up from the left; return the
            row's height, or 0 when it shows none."""
            x = 0.0
            height = 0.0
            for button in actions.subviews():
                if bool(button.isHidden()):
                    continue
                size = button.intrinsicContentSize()
                button.setFrame_(NSMakeRect(x, 0, size.width, size.height))
                x += size.width + BUBBLE_BUTTON_GAP
                height = max(height, float(size.height))
            return height

        @objc.python_method
        def _layout_bubble(self, host: Any, bubble: Any, headline: Any,
                           detail: Any, actions: Any = None) -> None:
            """Fit a bubble to its copy and centre it in the room it has.

            The bubble hugs what it says: headline, instruction, then the
            buttons. When the copy would not fit the stage, the instruction
            gives up lines (the whole of it stays in its VoiceOver value
            and its tooltip); the headline and the buttons never do.
            """
            size = host.bounds().size
            if size.width <= 0.0 or size.height <= 0.0:
                return
            shape = BRAND_SHAPES["bubble"]
            width = max(40.0, size.width - bubble.TAIL_LENGTH
                        - shape.shadow_right - 2.0 * BUBBLE_PADDING_X)
            headline_height = self._fit_headline(headline, width + 4.0)
            actions_height = (
                self._layout_actions(actions, width)
                if actions is not None else 0.0)
            below = (BUBBLE_ACTIONS_GAP + actions_height
                     if actions_height else 0.0)
            detail_height = 0.0
            if str(detail.stringValue()):
                line = math.ceil(_text_size(
                    "Xg", _font(BUBBLE_DETAIL_SIZE))[1])
                room = (size.height - shape.shadow_down
                        - 2.0 * BUBBLE_PADDING_Y - headline_height
                        - BUBBLE_HEADLINE_GAP - below)
                detail.setMaximumNumberOfLines_(0)
                needed = math.ceil(self._wrapped_height(detail, width + 4.0))
                if needed <= room:
                    detail_height = float(needed)
                else:
                    lines = max(1, int(room // line))
                    detail.setMaximumNumberOfLines_(lines)
                    detail_height = float(min(needed, math.ceil(
                        self._wrapped_height(detail, width + 4.0))))
                detail.setToolTip_(
                    str(detail.accessibilityValue() or "")
                    if needed > room else None)
            body_height = (2.0 * BUBBLE_PADDING_Y + headline_height
                           + (BUBBLE_HEADLINE_GAP + detail_height
                              if detail_height else 0.0) + below)
            footprint = min(size.height, body_height + shape.shadow_down)
            bubble.setFrame_(NSMakeRect(
                0.0, math.floor((size.height - footprint) / 2.0),
                size.width, footprint))
            content = bubble.content_rect()
            # A label draws its text two points inside its frame.
            x = content.origin.x - 2.0
            y = content.origin.y
            headline.setFrame_(NSMakeRect(
                x, y, width + 4.0, headline_height))
            y += headline_height
            detail.setHidden_(detail_height <= 0.0)
            if detail_height:
                y += BUBBLE_HEADLINE_GAP
                detail.setFrame_(NSMakeRect(x, y, width + 4.0, detail_height))
                y += detail_height
            if actions is not None:
                actions.setHidden_(actions_height <= 0.0)
                actions.setFrame_(NSMakeRect(
                    content.origin.x, y + BUBBLE_ACTIONS_GAP,
                    width, actions_height))
            # The tail leaves the bubble a little above its middle, level
            # with the face it points at.
            bubble.set_tail_center((footprint - shape.shadow_down) * 0.42)
            bubble.setNeedsDisplay_(True)

        @objc.python_method
        def _layout_home_bubble(self) -> None:
            if "home_bubble" not in self.dynamic:
                return
            self._layout_bubble(
                self.dynamic["home_bubble_host"], self.dynamic["home_bubble"],
                self.dynamic["overview_status"],
                self.dynamic["overview_detail"],
                self.dynamic["overview_actions"])

        @objc.python_method
        def _setup_action_reserve(self) -> float:
            """The width of setup's foot button at its widest title."""
            reserve = getattr(self, "_setup_reserve", None)
            if reserve is None:
                action = self.dynamic["onboarding_action"]
                shape = action.shape()
                widest = max(
                    _text_size(self._l(key), action.title_font())[0]
                    for key in SETUP_FOOT_ACTION_KEYS)
                reserve = self._setup_reserve = float(hard_shadow_footprint(
                    math.ceil(widest) + 2.0 * shape.padding_x, shape.height,
                    shape)[0])
            return reserve

        @objc.python_method
        def _reach_into_notice_band(self, view: Any) -> float:
            """How far ``view`` reaches down into the band at the window's
            foot that a notice takes. A page that cannot scroll leaves that
            much of itself empty, so it looks the same with and without a
            notice. Measured from where the views are, not from whether a
            notice is showing, so it is right in the middle of a change."""
            area = self.dynamic.get("content_side")
            if area is None or view.window() is None:
                return 0.0
            # Window coordinates: y grows upward from the bottom edge.
            foot = float(view.convertRect_toView_(
                view.bounds(), None).origin.y)
            band_top = float(area.convertRect_toView_(
                area.bounds(), None).origin.y) + NOTICE_BAND
            return max(0.0, min(NOTICE_BAND, band_top - foot))

        @objc.python_method
        def _layout_home(self) -> None:
            """Home from the top down, for whatever size the page has.

            Stage, stat stickers, the section header and the group of rows.
            Everything is placed from the page's own width, so the column's
            narrowest and widest both come out of the same arithmetic, and
            a taller window only opens the two gaps a little. Home cannot
            scroll, so the foot of the page is left free for a notice
            that takes a second line.
            """
            home = self.dynamic.get("home_cluster")
            if home is None or "home_rows" not in self.dynamic:
                return
            size = home.bounds().size
            width = float(size.width)
            height = max(0.0, float(size.height)
                         - self._reach_into_notice_band(home) - NOTICE_RISE)
            if width <= 0.0:
                return
            stage_height = STAGE_HEIGHT + BRAND_SHAPES["stage"].shadow_down
            tiles = self.dynamic["home_stickers"]
            tile_width = math.floor(
                (width - (len(tiles) - 1) * HOME_STICKER_GAP) / len(tiles))
            tile_height = 0.0
            for tile in tiles:
                tile.setFrameSize_((tile_width, max(
                    1.0, float(tile.frame().size.height))))
                tile_height = max(
                    tile_height, float(tile.intrinsicContentSize().height))
            # The header and the group of rows, placed inside their own
            # container by the layout every settings page uses.
            group_height = arrange_section(
                self.dynamic["home_group_header"],
                self.dynamic["overview_engine"],
                self.dynamic["home_result_card"], (), 0.0, width)
            fixed = (stage_height + HOME_STAGE_GAP + tile_height
                     + HOME_GROUP_GAP + group_height)
            stretch = max(0.0, min(HOME_GAP_STRETCH, (height - fixed) / 3.0))

            y = 0.0
            self.dynamic["home_stage"].setFrame_(
                NSMakeRect(0, y, width, stage_height))
            y += stage_height + HOME_STAGE_GAP + stretch
            strip = self.dynamic["overview_metric_cards"][0]
            strip.setFrame_(NSMakeRect(0, y, width, tile_height))
            for index, tile in enumerate(tiles):
                tile.setFrame_(NSMakeRect(
                    index * (tile_width + HOME_STICKER_GAP), 0,
                    tile_width, tile_height))
            y += tile_height + HOME_GROUP_GAP + stretch
            self.dynamic["home_group"].setFrame_(
                NSMakeRect(0, y, width, group_height))
            self._layout_home_bubble()

        @objc.python_method
        def _layout_setup_bubble(self) -> None:
            if "onboarding_bubble" not in self.dynamic:
                return
            self._layout_bubble(
                self.dynamic["onboarding_bubble_host"],
                self.dynamic["onboarding_bubble"],
                self.dynamic["onboarding_title"],
                self.dynamic["onboarding_detail"])

        @objc.python_method
        def _layout_onboarding(self) -> None:
            """First-run setup from the top down, for the page's size.

            The stage, the header and rows of the step that is showing,
            then the foot: the progress bar, the route across the four
            steps and the one Continue button. The block is placed as if
            every step had the most rows a step can have, and the foot
            stays where that puts it, so neither the stage nor the way
            forward moves when the next step has fewer rows.
            """
            setup = self.dynamic.get("onboarding_card")
            if setup is None or "onboarding_rows" not in self.dynamic:
                return
            size = setup.bounds().size
            width = float(size.width)
            height = max(0.0, float(size.height)
                         - self._reach_into_notice_band(setup))
            if width <= 0.0:
                return
            dynamic = self.dynamic
            stage_height = (SETUP_STAGE_HEIGHT
                            + BRAND_SHAPES["stage"].shadow_down)
            foot_height = (SETUP_PROGRESS_HEIGHT + 12.0 + SETUP_FOOT_HEIGHT)
            # The header and the group of rows, placed inside their own
            # container by the layout every settings page uses. The room
            # kept for them is what the most rows a step has would take.
            card = dynamic["onboarding_rows_card"]
            shown_height = arrange_section(
                dynamic["onboarding_status"], None, card, (), 0.0, width)
            rows = card.visible_rows()
            row_height = max(
                (float(row.frame().size.height) for row in rows),
                default=ROW_CAPTION_MIN_HEIGHT)
            group_height = (shown_height - float(card.frame().size.height)
                            + SETUP_ROW_COUNT * row_height)
            block = (stage_height + SETUP_GAP + group_height + SETUP_GAP
                     + foot_height + 10.0 + SETUP_KICKER_HEIGHT)

            y = max(0.0, math.floor((height - block) / 2.0))
            dynamic["onboarding_stage"].setFrame_(
                NSMakeRect(0, y, width, stage_height))
            y += stage_height + SETUP_GAP
            dynamic["onboarding_group"].setFrame_(
                NSMakeRect(0, y, width, group_height))
            y += group_height + SETUP_GAP

            foot = dynamic["onboarding_foot"]
            foot.setFrame_(NSMakeRect(0, y, width, foot_height))
            track = dynamic["onboarding_progress_track"]
            track.setFrame_(NSMakeRect(0, 0, width, SETUP_PROGRESS_HEIGHT))
            state = self.view_model.state
            total = len(state.onboarding_steps) or 1
            done = sum(step.complete for step in state.onboarding_steps)
            action = dynamic["onboarding_action"]
            action_size = action.intrinsicContentSize()
            top = SETUP_PROGRESS_HEIGHT + 12.0
            action.setFrame_(NSMakeRect(
                width - action_size.width,
                top + (SETUP_FOOT_HEIGHT - action_size.height) / 2.0,
                action_size.width, action_size.height))
            markers = dynamic["onboarding_step_cards"]
            # The steps keep their places from one step to the next: the
            # room beside them is what the widest title the button takes
            # needs, not what this step's title happens to need.
            route = max(0.0, width - 16.0 - max(
                float(action_size.width), self._setup_action_reserve()))
            marker_width = math.floor(route / max(1, len(markers)))
            # The bar is filled as far as the step that is open, so its
            # end sits over that step's mark; all the way when none is.
            fill = dynamic["onboarding_progress_fill"]
            fill.setFrame_(NSMakeRect(
                0, 0, width if done >= total else done * marker_width,
                SETUP_PROGRESS_HEIGHT))
            fill.setHidden_(done <= 0)
            for index, (marker, icon, label) in enumerate(zip(
                    markers, dynamic["onboarding_step_icons"],
                    dynamic["onboarding_steps"])):
                marker.setFrame_(NSMakeRect(
                    index * marker_width, top, marker_width,
                    SETUP_FOOT_HEIGHT))
                icon.setFrame_(NSMakeRect(
                    0, (SETUP_FOOT_HEIGHT - 18.0) / 2.0, 18.0, 18.0))
                label.setFrame_(NSMakeRect(
                    24.0, (SETUP_FOOT_HEIGHT - 30.0) / 2.0,
                    max(0.0, marker_width - 24.0 - 6.0), 30.0))
            y += foot_height + 10.0
            dynamic["onboarding_face_kicker"].setFrame_(
                NSMakeRect(0, y, width, SETUP_KICKER_HEIGHT))
            self._layout_setup_bubble()

        @objc.python_method
        def _apply_onboarding_theme(
                self, state: GUIState,
                presentation: OnboardingPresentation) -> None:
            """Colours for first-run setup, read from the palette and the
            tokens in sRGB like every other part of the new look."""
            dark = getattr(self, "_dark", False)
            palette = palette_for_appearance(dark)
            dynamic = self.dynamic
            ink = _srgb_color(palette.ink)
            soft = _srgb_color(palette.ink_soft)
            brand_ink = _token_color("brand_ink", dark)
            dynamic["onboarding_title"].setTextColor_(ink)
            dynamic["onboarding_detail"].setTextColor_(soft)
            dynamic["onboarding_face_kicker"].setTextColor_(soft)
            track_layer = dynamic["onboarding_progress_track"].layer()
            if track_layer is not None:
                track_layer.setBackgroundColor_(_srgb_color(
                    palette.brand, 0.26 if dark else 0.20).CGColor())
            fill_layer = dynamic["onboarding_progress_fill"].layer()
            if fill_layer is not None:
                # The emerald that reads as text: it measures over 3:1 on
                # the field in both appearances, which the switch green
                # does not in the light one.
                fill_layer.setBackgroundColor_(brand_ink.CGColor())
            sync_accessibility(
                dynamic["onboarding_progress_track"], presentation.progress,
                label=self._l("onboarding.accessibility.progress_track"))
            for row in dynamic["onboarding_rows"]:
                layer = row["tile"].layer()
                if layer is not None:
                    layer.setBackgroundColor_(
                        _srgb_color(palette.brand, 0.30).CGColor())
                try:
                    row["icon"].setContentTintColor_(ink)
                except Exception:
                    pass
            for index, (step, control, icon) in enumerate(zip(
                    state.onboarding_steps, dynamic["onboarding_steps"],
                    dynamic["onboarding_step_icons"])):
                current = step.key == presentation.current_key
                if step.complete:
                    text, tint = brand_ink, brand_ink
                    symbol = "checkmark.circle.fill"
                elif current:
                    text, tint = ink, ink
                    symbol = f"{index + 1}.circle.fill"
                else:
                    text, tint = soft, soft
                    symbol = f"{index + 1}.circle"
                control.setTextColor_(text)
                image = _symbol_image(symbol, size=16.0, weight="semibold")
                if image is not None:
                    icon.setImage_(image)
                try:
                    icon.setContentTintColor_(tint)
                except Exception:
                    pass

        @objc.python_method
        def _animate_onboarding_face(
                self, presentation: OnboardingPresentation) -> None:
            """Wobble the character once when a new step takes the stage."""
            stage = "complete" if presentation.complete else (
                presentation.current_key or "hidden")
            if (not presentation.visible or stage == self._onboarding_stage
                    or not bool(self.window.isVisible())):
                return
            self._onboarding_stage = stage
            add_jelly_motion(
                self.dynamic["onboarding_face"], "wobble",
                reduced_motion=self.view_model.state.prefers_reduced_motion)

        @objc.python_method
        def _animate_onboarding_steps(self, state: GUIState) -> None:
            """Pop only what just turned good: a step's marker, and the
            check mark of a permission the moment it is granted."""
            completed = frozenset(
                step.key for step in state.onboarding_steps if step.complete)
            good = frozenset(
                row["key"] for row in self.dynamic["onboarding_rows"]
                if row.get("tone") == "good"
                and not bool(row["view"].isHidden()))
            stage = ("complete" if len(completed) == len(
                state.onboarding_steps) else next(
                    (step.key for step in state.onboarding_steps
                     if not step.complete), ""))
            newly = completed - self._onboarding_completed
            newly_granted = setup_rows_turned_good(
                self._onboarding_good, stage, good, newly)
            self._onboarding_completed = completed
            self._onboarding_good = (stage, good)
            if not bool(self.window.isVisible()):
                return
            reduced = state.prefers_reduced_motion
            for step, marker in zip(
                    state.onboarding_steps,
                    self.dynamic["onboarding_step_cards"]):
                if step.key in newly:
                    add_jelly_motion(marker, "pop", reduced_motion=reduced)
            for row in self.dynamic["onboarding_rows"]:
                if row["key"] in newly_granted:
                    add_jelly_motion(
                        row["check"], "pop", reduced_motion=reduced)

        @objc.python_method
        def _animate_home_landing(self, state: GUIState) -> None:
            """The one signature reaction on Home: the face pops when a
            dictation lands. Reduce Motion and a closed window skip it."""
            landed = (state.last_latency_ms, state.last_word_count,
                      state.words_today, state.last_result.summary)
            previous = self._home_landed
            self._home_landed = landed
            if not home_dictation_landed(
                    previous, landed,
                    available=state.last_result.available,
                    section=state.section) or not bool(
                        self.window.isVisible()):
                return
            add_jelly_motion(
                self.dynamic["home_face_view"], "pop",
                reduced_motion=state.prefers_reduced_motion)

        @objc.python_method
        def _apply_window_theme(self, state: GUIState) -> None:
            """Apply the shared palette to the whole working window."""
            dark = _uses_dark_appearance(self.window)
            palette = palette_for_appearance(dark)
            self._dark = dark
            # The field belongs to the content side only: the sidebar keeps
            # the system's material.
            field = self.dynamic["content_side"]
            if field.layer() is not None:
                field.layer().setBackgroundColor_(
                    _theme_color(palette.bg).CGColor())
            # Groups draw their own fill, edge and separators from the
            # tokens and follow the appearance on their own.
            self._paint_labels(self.themed, palette)
            # Home's stage, bubble, buttons and stickers draw themselves
            # from the tokens and follow the appearance on their own.

        @objc.python_method
        def _paint_labels(self, themed: Mapping[str, Sequence[Any]],
                          palette: Any) -> None:
            """Give the labels kept by role their colours: the window's,
            and an editor sheet's the same way."""
            for label in themed.get("ink", ()):
                label.setTextColor_(_theme_color(palette.ink))
            for label in themed.get("ink_soft", ()):
                label.setTextColor_(_theme_color(palette.ink_soft))

        @objc.python_method
        def _sync_sidebar(self, state: GUIState, row_index: int) -> None:
            """The sidebar for this state: face, state line, selected row.

            Everything here is drawn in sRGB like the rest of the new look,
            on the system's sidebar material, so it reads the palette but
            never paints the sidebar itself.
            """
            dark = getattr(self, "_dark", False)
            palette = palette_for_appearance(dark)
            # The face sits on its own chip colour, which is what keeps its
            # ink outline visible on a dark sidebar.
            chip_layer = self.dynamic["window_face_chip"].layer()
            if chip_layer is not None:
                chip_layer.setBackgroundColor_(
                    _srgb_color(FACE_CHIP_COLORS[state.face]).CGColor())
                chip_layer.setBorderColor_(
                    _token_color("hairline", dark).CGColor())
            face_name = self._l(f"settings.face.{state.face}")
            self.dynamic["window_face"].setImage_(self._face_image(
                state.face, talk=state.status_phase == "recording"))
            sync_accessibility(
                self.dynamic["window_face_button"], face_name,
                label=self._l("app.face_chip.label"))
            self.dynamic["window_title"].setTextColor_(
                _srgb_color(palette.ink))

            state_key, tone = sidebar_state(
                state.status_phase,
                setting_up=not (state.onboarding_complete
                                or state.onboarding_acknowledged))
            state_label = self.dynamic["window_state"]
            set_accessible_text(
                state_label, self._l(state_key),
                label=self._l("shell.state.accessibility.label"))
            state_label.setTextColor_(_srgb_color(palette.ink_soft))
            dot_layer = self.dynamic["window_state_dot"].layer()
            if dot_layer is not None:
                dot = (_token_color("switch_on", dark) if tone == "good"
                       else _srgb_color(
                           palette.error if dark else ERROR_TEXT_ON_LIGHT)
                       if tone == "attention"
                       else _srgb_color(palette.ink_soft))
                dot_layer.setBackgroundColor_(dot.CGColor())

            self.dynamic["sidebar_group_label"].setTextColor_(
                _srgb_color(palette.ink_soft))
            self.dynamic["window_version"].setTextColor_(
                _srgb_color(palette.ink_soft))
            for index, row in enumerate(self.sidebar_rows):
                selected = index == row_index
                row.set_selected(selected, emphasized=self._window_active)
                sync_accessibility(
                    row, self._l("nav.selected") if selected else "",
                    label=self._l(SIDEBAR_ROWS[index].label_key))

        @objc.python_method
        def _sync_title_strip(self, state: GUIState, row_index: int) -> None:
            """The page's name, and the trailing items that belong to it."""
            dark = getattr(self, "_dark", False)
            row = SIDEBAR_ROWS[row_index]
            title = self.dynamic["page_title"]
            # A heading reads its own text; no separate label to repeat it.
            title.setStringValue_(self._l(row.label_key))
            title.setTextColor_(
                _srgb_color(palette_for_appearance(dark).ink))
            for row_key, view, visible in self.title_items:
                view.setHidden_(
                    row_key != row.key
                    or (visible is not None and not visible(state)))

        @objc.python_method
        def add_title_item(self, row_key: str, view: Any,
                           *, visible: Callable[[GUIState], bool] | None
                           = None) -> Any:
            """Put a control at the trailing end of one page's title strip.

            ``row_key`` is a ``SidebarRow.key``. The item shows only while
            that page is showing and, when ``visible`` is given, only while
            it answers True for the current state; the shell owns the
            item's hidden flag, so a page does not hide it itself. Items
            line up from the page column's trailing edge in the order they
            were added, centred in the strip, and join the Tab loop between
            the sidebar and the page. The strip lays the view out with
            Auto Layout: a control with an intrinsic size needs nothing
            more, anything else needs its own width and height constraints
            (its frame is not used).
            """
            if row_key not in {row.key for row in SIDEBAR_ROWS}:
                raise ValueError(f"no sidebar row is named {row_key!r}")
            self.title_items.append((row_key, view, visible))
            self.dynamic["title_strip_items"].addArrangedSubview_(view)
            state = self.view_model.state
            current = SIDEBAR_ROWS[sidebar_row_index(
                state.section, state.settings_pane)].key
            view.setHidden_(
                row_key != current
                or (visible is not None and not visible(state)))
            return view

        @objc.python_method
        def set_setup_mode(self, enabled: bool) -> None:
            """Give first-run setup the whole window, or give it back.

            On, the sidebar and the title strip are gone and the page
            column runs from just under the title bar to the notice line,
            centred in the full window width at its usual width. Off, the
            shell returns exactly as it was: the selected row is derived
            from the view model on every render, so nothing is lost. The
            page shortcuts keep working either way; drive this from what
            the render shows rather than from a button, so that leaving the
            setup page brings the shell back.
            """
            enabled = bool(enabled)
            if enabled == self.setup_mode:
                return
            self.setup_mode = enabled
            self.sidebar_item.setCollapsed_(enabled)
            self.dynamic["title_strip"].setHidden_(enabled)
            self.dynamic["title_strip_height"].setConstant_(
                self._title_bar_height() if enabled
                else TITLE_STRIP_HEIGHT)
            if getattr(self, "key_views_by_section", None) is not None:
                self._configure_key_view_loop(self.view_model.state)

        @objc.python_method
        def _title_bar_height(self) -> float:
            """How much of the content view the title bar lies over."""
            try:
                over = float(
                    self.window.frame().size.height
                    - self.window.contentLayoutRect().size.height)
                if over > 0.0:
                    return over
            except Exception:
                pass
            return TITLE_BAR_HEIGHT

        @objc.python_method
        def _select_sidebar_row(self, index: int) -> None:
            select_sidebar_row(self.view_model, index)
            self.render()

        @objc.python_method
        def move_sidebar_focus(self, row: Any, delta: int) -> bool:
            """Up and Down on a focused row: move to its neighbour and show
            that page, the way a system sidebar follows the arrow keys."""
            index = int(row.tag()) + int(delta)
            if not 0 <= index < len(self.sidebar_rows):
                return False
            self.window.makeFirstResponder_(self.sidebar_rows[index])
            self._select_sidebar_row(index)
            return True

        @objc.python_method
        def _set_window_active(self, active: bool) -> None:
            """Selection is emphasized only in the key window."""
            self._window_active = bool(active)
            for row in self.sidebar_rows:
                row.set_selected(
                    row.selected, emphasized=self._window_active)

        @objc.python_method
        def _animate_section_change(
                self, state: GUIState, row_index: int) -> None:
            """Crossfade the incoming page instead of hard-cutting it."""
            previous = self._rendered_row
            self._rendered_row = row_index
            if (previous is None or previous == row_index
                    or state.prefers_reduced_motion
                    or not bool(self.window.isVisible())):
                return
            page = self.pages[state.section]
            page.setWantsLayer_(True)
            fade_in(page, reduced_motion=state.prefers_reduced_motion)

        @objc.python_method
        def _animate_notice(self, state: GUIState) -> None:
            """Size the notice to its copy, then fade a fresh one up."""
            notice = self.dynamic["notice"]
            text = state.notice
            # The band at the window's foot is the notice's while there is
            # one, and the page's when there is none.
            foot = self.dynamic.get("column_foot")
            if foot is not None:
                foot.setConstant_(-NOTICE_BAND if text else 0.0)
            for limit in self.dynamic.get("notice_limits", ()):
                limit.setActive_(bool(text))
            constraint = self.dynamic.get("notice_height")
            if constraint is not None:
                needed = self._wrapped_height(
                    notice, float(notice.frame().size.width) or CONTENT_WIDTH)
                constraint.setConstant_(
                    NOTICE_WRAPPED_HEIGHT
                    if needed > NOTICE_LINE_HEIGHT + 1.0
                    else NOTICE_LINE_HEIGHT)
            if text == getattr(self, "_rendered_notice", None):
                return
            self._rendered_notice = text
            if not text or not bool(self.window.isVisible()):
                notice.setAlphaValue_(1.0)
                return
            fade_in(notice, reduced_motion=state.prefers_reduced_motion)

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
            # A standard titled window whose content view runs under the
            # title bar, so the sidebar is full height with the traffic
            # lights over it. The title bar itself stays the system's: it
            # is only made transparent and its title hidden, because the
            # page's name is in the title strip.
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                     NSWindowStyleMaskMiniaturizable |
                     NSWindowStyleMaskResizable |
                     NSWindowStyleMaskFullSizeContentView)
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, *WINDOW_DEFAULT_SIZE), style,
                NSBackingStoreBuffered, False)
            # The controller is a plain NSObject, not an NSWindowController,
            # so AppKit would free the window on close while self.window
            # still points at it; the next open would message freed memory.
            self.window.setReleasedWhenClosed_(False)
            self.window.setTitle_(APP_NAME)
            self.window.setTitlebarAppearsTransparent_(True)
            self.window.setTitleVisibility_(NSWindowTitleHidden)
            self.window.setDelegate_(self)
            self._install_main_menu()

            # ---- Two columns, held by the system's split view controller.
            # A sidebar item gets the sidebar material, the full-height
            # layout and the divider from whichever macOS is running, so
            # none of that is drawn here.
            split = ShellSplitViewController.alloc().init()
            divider = float(split.splitView().dividerThickness())
            sidebar_width = SIDEBAR_WIDTH - divider
            sidebar = self._build_sidebar(sidebar_width)
            sidebar_controller = NSViewController.alloc().init()
            sidebar_controller.setView_(sidebar)
            sidebar_item = NSSplitViewItem.sidebarWithViewController_(
                sidebar_controller)
            # One width, and no dragging it shut: together with the divider
            # the sidebar takes exactly SIDEBAR_WIDTH. Setup mode collapses
            # it in code.
            sidebar_item.setMinimumThickness_(sidebar_width)
            sidebar_item.setMaximumThickness_(sidebar_width)
            sidebar_item.setCanCollapse_(False)
            # A sidebar item would otherwise spring open when something is
            # dragged to the window's edge while setup mode has it shut,
            # and newer systems may fold it away when a window narrows.
            sidebar_item.setSpringLoaded_(False)
            try:
                if sidebar_item.respondsToSelector_(
                        "setCanCollapseFromWindowResize:"):
                    sidebar_item.setCanCollapseFromWindowResize_(False)
            except Exception:
                pass

            # ---- Content side: title strip over a centered, capped column.
            content_area = NSView.alloc().initWithFrame_(NSMakeRect(
                0, 0, WINDOW_DEFAULT_SIZE[0] - SIDEBAR_WIDTH,
                WINDOW_DEFAULT_SIZE[1]))
            # Home's backdrop bleeds past its column on purpose; it must
            # stop at the content side's edge, not paint over the sidebar.
            content_area.setWantsLayer_(True)
            if content_area.layer() is not None:
                content_area.layer().setMasksToBounds_(True)
            content_controller = NSViewController.alloc().init()
            content_controller.setView_(content_area)
            split.addSplitViewItem_(sidebar_item)
            split.addSplitViewItem_(
                NSSplitViewItem.splitViewItemWithViewController_(
                    content_controller))
            self.split_controller = split
            self.sidebar = sidebar
            self.sidebar_item = sidebar_item
            self.window.setContentViewController_(split)
            # A content view controller sizes the window to its view, so
            # the sizes are set after it is installed.
            self.window.setContentMinSize_(WINDOW_MIN_SIZE)
            self.window.setContentSize_(WINDOW_DEFAULT_SIZE)
            self.window.center()

            strip = NSView.alloc().initWithFrame_(NSMakeRect(
                0, 0, WINDOW_DEFAULT_SIZE[0] - SIDEBAR_WIDTH,
                TITLE_STRIP_HEIGHT))
            strip.setTranslatesAutoresizingMaskIntoConstraints_(False)
            content_area.addSubview_(strip)
            self.title_strip = strip
            page_title = _label(
                "", NSMakeRect(0, 0, 320, 22), truncate=True)
            page_title.setFont_(_role_font("page_title"))
            page_title.setTranslatesAutoresizingMaskIntoConstraints_(False)
            try:
                # VoiceOver reads the page's name as a heading. The role
                # constant is looked up, not imported, and has this value.
                import AppKit as _appkit
                heading = getattr(
                    _appkit, "NSAccessibilityHeadingRole", "AXHeading")
                page_title.setAccessibilityRole_(heading)
                page_title.cell().setAccessibilityRole_(heading)
            except Exception:
                pass
            strip.addSubview_(page_title)
            items = NSStackView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 10, 10))
            items.setOrientation_(
                NSUserInterfaceLayoutOrientationHorizontal)
            items.setAlignment_(NSLayoutAttributeCenterY)
            items.setSpacing_(TITLE_STRIP_ITEM_GAP)
            items.setTranslatesAutoresizingMaskIntoConstraints_(False)
            strip.addSubview_(items)

            # Failure copy now names the next move, so the notice takes a
            # second line when it needs one. The reserved band stays one
            # line tall: the column keeps a fixed bottom so a two-line
            # notice grows into the padding instead of shoving every page
            # up and clipping the last row at the window minimum.
            notice = _label("", NSMakeRect(0, 0, 720, NOTICE_LINE_HEIGHT),
                            size=11, wrap=True, lines=2)
            notice.setTranslatesAutoresizingMaskIntoConstraints_(False)
            content_area.addSubview_(notice)
            column = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, 580))
            column.setTranslatesAutoresizingMaskIntoConstraints_(False)
            content_area.addSubview_(column)

            notice_height = notice.heightAnchor() \
                .constraintEqualToConstant_(NOTICE_LINE_HEIGHT)
            # The column ends at the window's edge until a notice shows;
            # ``_animate_notice`` lifts it clear of the band then.
            column_foot = column.bottomAnchor() \
                .constraintEqualToAnchor_constant_(
                    content_area.bottomAnchor(), 0.0)
            strip_height = strip.heightAnchor() \
                .constraintEqualToConstant_(TITLE_STRIP_HEIGHT)
            preferred_width = column.widthAnchor() \
                .constraintEqualToConstant_(CONTENT_WIDTH)
            # Below NSLayoutPriorityWindowSizeStayPut (500) so preferring the
            # full reading width can never grow the window itself.
            preferred_width.setPriority_(450)
            # The comfortable inset outranks the full width but not the
            # column's minimum: a narrow window keeps CONTENT_MIN_WIDTH and
            # falls back to CONTENT_EDGE_MIN.
            preferred_inset = column.leadingAnchor() \
                .constraintGreaterThanOrEqualToAnchor_constant_(
                    content_area.leadingAnchor(), CONTENT_INSET)
            preferred_inset.setPriority_(480)
            NSLayoutConstraint.activateConstraints_([
                strip.leadingAnchor().constraintEqualToAnchor_(
                    content_area.leadingAnchor()),
                strip.trailingAnchor().constraintEqualToAnchor_(
                    content_area.trailingAnchor()),
                strip.topAnchor().constraintEqualToAnchor_(
                    content_area.topAnchor()),
                strip_height,
                page_title.leadingAnchor().constraintEqualToAnchor_(
                    column.leadingAnchor()),
                page_title.centerYAnchor().constraintEqualToAnchor_(
                    strip.centerYAnchor()),
                page_title.trailingAnchor()
                .constraintLessThanOrEqualToAnchor_constant_(
                    items.leadingAnchor(), -TITLE_STRIP_ITEM_GAP),
                items.trailingAnchor().constraintEqualToAnchor_(
                    column.trailingAnchor()),
                items.centerYAnchor().constraintEqualToAnchor_(
                    strip.centerYAnchor()),
                column.topAnchor().constraintEqualToAnchor_(
                    strip.bottomAnchor()),
                column.centerXAnchor().constraintEqualToAnchor_(
                    content_area.centerXAnchor()),
                column.widthAnchor().constraintLessThanOrEqualToConstant_(
                    CONTENT_WIDTH),
                column.widthAnchor().constraintGreaterThanOrEqualToConstant_(
                    CONTENT_MIN_WIDTH),
                column.leadingAnchor()
                .constraintGreaterThanOrEqualToAnchor_constant_(
                    content_area.leadingAnchor(), CONTENT_EDGE_MIN),
                preferred_inset,
                preferred_width,
                notice.leadingAnchor().constraintEqualToAnchor_(
                    column.leadingAnchor()),
                notice.trailingAnchor().constraintEqualToAnchor_(
                    column.trailingAnchor()),
                notice_height,
                notice.bottomAnchor().constraintEqualToAnchor_constant_(
                    content_area.bottomAnchor(), -NOTICE_FOOT),
                column_foot,
            ])
            self.dynamic.update(
                content_side=content_area,
                page_column=column,
                column_foot=column_foot,
                title_strip=strip,
                title_strip_height=strip_height,
                title_strip_items=items,
                page_title=page_title,
                notice_height=notice_height,
                notice=notice,
            )

            builders = {
                "Home": self._build_home,
                "Settings": self._build_settings,
                "Advanced": self._build_advanced,
            }
            for section, builder in builders.items():
                page = NSView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, CONTENT_WIDTH, 580))
                page.setTranslatesAutoresizingMaskIntoConstraints_(False)
                column.addSubview_(page)
                NSLayoutConstraint.activateConstraints_([
                    page.topAnchor().constraintEqualToAnchor_(
                        column.topAnchor()),
                    page.leadingAnchor().constraintEqualToAnchor_(
                        column.leadingAnchor()),
                    page.trailingAnchor().constraintEqualToAnchor_(
                        column.trailingAnchor()),
                    page.bottomAnchor().constraintEqualToAnchor_(
                        column.bottomAnchor()),
                ])
                builder(page)
                page.setHidden_(section != "Home")
                self.pages[section] = page
            self.key_views_by_section = {
                "Home": self.dynamic["home_key_views"],
                # Settings has no control of its own any more: the pane
                # is chosen in the sidebar, and each pane brings its own.
                "Settings": (),
                "Advanced": (
                    self.dynamic["selective_relisten_toggle"],
                    self.dynamic["new_speech_runtime_toggle"],
                    self.dynamic["verify_button"],
                    self.dynamic["open_log_button"],
                    self.dynamic["copy_support_snapshot_button"],
                    self.dynamic["export_support_bundle_button"],
                    self.dynamic["open_system_settings_button"],
                    self.dynamic["license_button"],
                    self.dynamic["source_button"],
                ),
            }
            self.render()

        @objc.python_method
        def _build_sidebar(self, width: float) -> Any:
            """The sidebar's contents, laid out from the top down.

            Face, name and state; Home; the Settings group; the build at
            the bottom. Rows are the only controls and all of them go into
            ``self.sidebar_rows`` in ``SIDEBAR_ROWS`` order.
            """
            sidebar = ShellView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, WINDOW_DEFAULT_SIZE[1]))

            # ---- Brand block, clear of the traffic lights above it.
            chip_size = SIDEBAR_FACE_SIZE
            header = ShellView.alloc().initWithFrame_(NSMakeRect(
                0, SIDEBAR_BRAND_TOP, width, chip_size))
            header.setAutoresizingMask_(NSViewWidthSizable)
            chip_frame = NSMakeRect(
                SIDEBAR_BRAND_INSET, 0, chip_size, chip_size)
            face_chip = ShellView.alloc().initWithFrame_(chip_frame)
            face_chip.setWantsLayer_(True)
            if face_chip.layer() is not None:
                # The picker's chip proportions, so the two read as kin.
                face_chip.layer().setCornerRadius_(min(
                    BRAND_SHAPES["face_chip"].radius, chip_size * 0.28))
                face_chip.layer().setBorderWidth_(1.0)
            face_image = NSImageView.alloc().initWithFrame_(
                NSInsetRect(chip_frame, 4.0, 4.0))
            face_image.setImageScaling_(
                NSImageScaleProportionallyUpOrDown)
            face_image.setEditable_(False)
            # The button over it names it, and says which face it is.
            _silence_image(face_image)
            face_button = JellyButton.alloc().initWithFrame_(chip_frame)
            face_button.setTitle_("")
            face_button.setBordered_(False)
            face_button.setTransparent_(True)
            face_button.setTarget_(self)
            face_button.setAction_("faceChipPressed:")
            _accessible(
                face_button,
                self._l("app.face_chip.label"),
                self._l("app.face_chip.help"))
            text_x = SIDEBAR_BRAND_INSET + chip_size + 8.0
            text_width = width - text_x - 8.0
            title = _label(
                APP_NAME, NSMakeRect(text_x, 4, text_width, 19),
                size=15, weight="bold", rounded=True, truncate=True)
            state_dot = ShellView.alloc().initWithFrame_(
                NSMakeRect(text_x + 1.0, 29.5, 7.0, 7.0))
            state_dot.setWantsLayer_(True)
            if state_dot.layer() is not None:
                state_dot.layer().setCornerRadius_(3.5)
            state_label = _label(
                "", NSMakeRect(text_x + 13.0, 25, text_width - 13.0, 16),
                size=12, weight="semibold", truncate=True)
            for view in (title, state_label):
                view.setAutoresizingMask_(NSViewWidthSizable)
            for view in (face_chip, face_image, face_button, title,
                         state_dot, state_label):
                header.addSubview_(view)
            sidebar.addSubview_(header)

            # ---- The pages. VoiceOver finds them as one list.
            row_width = width - 2.0 * SIDEBAR_PADDING
            rows: list[Any] = []
            nav = ShellView.alloc().initWithFrame_(NSMakeRect(
                0, SIDEBAR_BRAND_TOP + chip_size + SIDEBAR_BRAND_GAP,
                width, 10))
            nav.setAutoresizingMask_(NSViewWidthSizable)
            try:
                nav.setAccessibilityElement_(True)
                nav.setAccessibilityRole_(NSAccessibilityListRole)
            except Exception:
                pass
            _accessible(nav, self._l("shell.sidebar.accessibility.label"))
            y = 0.0
            group_label = None
            for index, spec in enumerate(SIDEBAR_ROWS):
                if index == 1:
                    group_label = _label(
                        self._l("shell.sidebar.group.settings"),
                        NSMakeRect(
                            SIDEBAR_PADDING + SIDEBAR_ROW_INSET, y + 14.0,
                            row_width - 2.0 * SIDEBAR_ROW_INSET, 14),
                        size=11, weight="semibold", truncate=True)
                    group_label.setAutoresizingMask_(NSViewWidthSizable)
                    nav.addSubview_(group_label)
                    y += SIDEBAR_GROUP_LABEL_HEIGHT
                row = SidebarRowButton.alloc().initWithFrame_(NSMakeRect(
                    SIDEBAR_PADDING, y, row_width, SIDEBAR_ROW_HEIGHT))
                label = self._l(spec.label_key)
                row.configure(label, spec.symbol)
                row.setTag_(index)
                row.setTarget_(self)
                row.setAction_("selectSidebarRow:")
                row.setAutoresizingMask_(NSViewWidthSizable)
                _accessible(
                    row, label,
                    self._l("settings.accessibility.sections.help"))
                nav.addSubview_(row)
                rows.append(row)
                y += SIDEBAR_ROW_HEIGHT + SIDEBAR_ROW_GAP
            nav.setFrameSize_((width, y - SIDEBAR_ROW_GAP))
            sidebar.addSubview_(nav)
            self.sidebar_rows = tuple(rows)
            # ⌘D always routes to Advanced; its row is in the sidebar on
            # every page, so the shortcut works window-wide.
            advanced_row = rows[-1]
            advanced_row.setKeyEquivalent_("d")
            advanced_row.setKeyEquivalentModifierMask_(
                NSEventModifierFlagCommand)
            _accessible(
                advanced_row, self._l("nav.advanced"),
                self._l("advanced.accessibility.shortcut.help"))
            self.dynamic["advanced_button"] = advanced_row

            # ---- The build, at the foot. The window has no update action
            # of its own, so nothing sits beside it.
            version = _label(
                self._l(
                    "app.version", version=application_build_version()),
                NSMakeRect(
                    SIDEBAR_PADDING + SIDEBAR_ROW_INSET,
                    WINDOW_DEFAULT_SIZE[1] - SIDEBAR_FOOT_INSET - 14.0,
                    row_width - 2.0 * SIDEBAR_ROW_INSET, 14),
                size=11, weight="medium", truncate=True)
            # In a flipped view the flexible "min Y" margin is the one
            # above, which pins the label to the bottom edge.
            version.setAutoresizingMask_(
                NSViewWidthSizable | NSViewMinYMargin)
            sidebar.addSubview_(version)

            self.dynamic.update(
                window_header=header,
                window_face_chip=face_chip,
                window_face=face_image,
                window_face_button=face_button,
                window_title=title,
                window_state=state_label,
                window_state_dot=state_dot,
                window_version=version,
                sidebar_list=nav,
                sidebar_group_label=group_label,
            )
            return sidebar

        def _build_home(self, page: Any) -> None:
            """Home: the stage, the stat stickers and one group of rows.

            The stage is the page's one loud thing: the living face, and
            beside it the bubble with the status, one instruction and the
            actions. Everything under it is quiet: three stickers for the
            day's numbers, then plain rows. First-run setup is built on the
            same page and takes it over while setup is unfinished.
            """
            width = CONTENT_WIDTH
            home = FlowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, 580))
            home.on_layout = self._layout_home
            self._fill(page, home)

            # ---- The stage: the face on the left, the bubble beside it.
            stage = StagePanelView.alloc().initWithFrame_(NSMakeRect(
                0, 0, width,
                STAGE_HEIGHT + BRAND_SHAPES["stage"].shadow_down))
            _accessible(stage, self._l("home.stage.accessibility.label"))
            face_view = LiveFaceView.alloc().initWithFrame_(
                NSMakeRect(0, 0, STAGE_FACE_SIZE, STAGE_FACE_SIZE))
            # On a stage the face stands on its own hard shadow, and the
            # bubble does the talking, so the speech puffs are left out.
            face_view.setShadow_(True)
            face_view.setWhispers_(False)
            face_view.setWobbleHook_(lambda: add_jelly_motion(
                face_view, "wobble", reduced_motion=_REDUCE_MOTION))
            host = FlowView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 210))
            host.on_layout = self._layout_home_bubble
            bubble = SpeechBubbleView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 400, 150))
            host.addSubview_(bubble)
            # The state in capitals was a label above the headline. It is
            # no longer drawn; the bubble carries it for VoiceOver, and the
            # field keeps the value for whatever reads it.
            phase = _label(
                self._l("overview.phase.ready"), NSMakeRect(0, 0, 10, 10))
            phase.setHidden_(True)
            status = self._ink(
                self._l("overview.status.ready.title"),
                NSMakeRect(0, 0, 360, 34), wrap=True, lines=2)
            status.setFont_(_role_font("bubble_headline"))
            detail = _label(
                "", NSMakeRect(0, 0, 360, 18), size=BUBBLE_DETAIL_SIZE,
                wrap=True)
            try:
                detail.cell().setTruncatesLastVisibleLine_(True)
            except Exception:
                pass
            actions = FlowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 360, 40))
            pause = self._chunky(
                self._l("overview.action.pause"), "pauseChanged:",
                help_text=self._l("overview.action.pause.help"))
            fix = self._chunky(
                self._l("overview.action.review"), "reviewIssue:",
                variant="plain",
                help_text=self._l("overview.action.review.help"))
            copy_outbox = self._chunky(
                self._l("overview.action.copy_outbox"), "copyOutbox:",
                variant="plain",
                help_text=self._l("overview.action.copy_outbox.help"))
            for button in (pause, fix, copy_outbox):
                actions.addSubview_(button)
            for view in (phase, status, detail, actions):
                bubble.addSubview_(view)
            stage.set_face_view(face_view, STAGE_FACE_SIZE)
            stage.set_content_view(host)
            home.addSubview_(stage)

            # ---- The day's numbers, as three tilted stickers.
            strip = FlowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, 72))
            tiles = []
            for index, (heading_key, key) in enumerate((
                    ("overview.metric.last.heading", "overview_last"),
                    ("overview.metric.words.heading", "overview_words"),
                    ("overview.metric.saved.heading", "overview_saved"))):
                tile = StickerTileView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, 200, 72)).configure(
                        self._l("overview.metric.last.empty"),
                        self._l(heading_key), index)
                strip.addSubview_(tile)
                tiles.append(tile)
                self.dynamic[key] = tile
            home.addSubview_(strip)

            # ---- The last dictation and what is waiting, as plain rows:
            # a section header over one group, as on every settings page.
            group = FlowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, 180))
            header = self._section_header(self._l("home.group.dictation"))
            engine = self._header_note()

            # Review Setup, as a row, only while the bubble's one secondary
            # place is taken by Copy & Dismiss.
            attention_icon = NSImageView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 16, 16))
            attention_icon.setImageScaling_(
                NSImageScaleProportionallyUpOrDown)
            attention_icon.setEditable_(False)
            image = _symbol_image(
                "exclamationmark.triangle.fill", size=14.0, weight="semibold")
            if image is not None:
                attention_icon.setImage_(image)
            # The row's own words say what needs a review.
            _silence_image(attention_icon)
            review_row = _button(
                self._l("overview.action.review"), NSMakeRect(0, 0, 60, 28),
                self, "reviewIssue:",
                help_text=self._l("overview.action.review.help"))
            attention, attention_title, _caption = self._settings_row(
                "", accessories=(review_row,), leading=attention_icon,
                wrap=False)

            # How the last dictation was taken: a quiet value at the row's
            # end, like every other value in the window.
            result_mode = self._value_label(self._l("results.mode.capture"))
            _accessible(result_mode, self._l("results.accessibility.mode"))
            inspect_evidence = _button(
                self._l("results.inspect.action"), NSMakeRect(0, 0, 60, 28),
                self, "inspectResultEvidence:",
                symbol="doc.text.magnifyingglass",
                help_text=self._l("results.inspect.action.help"),
            )
            result_row, result_summary, result_engine = self._settings_row(
                self._l("results.summary.empty"),
                caption=self._l("results.engine.waiting"),
                accessories=(result_mode, inspect_evidence), wrap=False)

            play_audio = _button(
                self._l("results.audio.play"), NSMakeRect(0, 0, 60, 28),
                self, "playRetainedSpan:", symbol="play.fill",
                help_text=self._l("results.audio.play.help"))
            clear_audio = _button(
                self._l("results.audio.clear"), NSMakeRect(0, 0, 60, 28),
                self, "clearRetainedSpans:", symbol="xmark.bin",
                label=self._l("home.replay.clear.action"),
                help_text=self._l("results.audio.clear.help"))
            replay_row, _title, result_audio = self._settings_row(
                None, caption=self._l("results.audio.off"),
                accessories=(play_audio, clear_audio), wrap=False)
            # The one line of this row may wrap once instead of being cut.
            result_audio.setUsesSingleLineMode_(False)
            result_audio.setMaximumNumberOfLines_(2)
            try:
                result_audio.cell().setWraps_(True)
                result_audio.cell().setScrollable_(False)
                result_audio.cell().setTruncatesLastVisibleLine_(True)
            except Exception:
                pass

            outbox_row, outbox, _caption = self._settings_row(
                self._l("overview.outbox.empty"), wrap=False)

            rows = (attention, result_row, replay_row, outbox_row)
            # VoiceOver meets the group as a group: it says what the
            # header over it says.
            card = self._settings_group(rows).name_for_voiceover(
                self._l("home.group.dictation"))
            for view in (header, engine, card):
                group.addSubview_(view)
            home.addSubview_(group)

            self.dynamic.update(
                home_cluster=home,
                home_stage=stage,
                overview_hero=stage,
                home_face_view=face_view,
                home_bubble_host=host,
                home_bubble=bubble,
                overview_actions=actions,
                overview_phase=phase,
                overview_status=status,
                overview_detail=detail,
                overview_engine=engine,
                overview_outbox=outbox,
                pause_button=pause,
                review_issue_button=fix,
                copy_outbox_button=copy_outbox,
                overview_metric_cards=(strip,),
                home_stickers=tuple(tiles),
                home_group=group,
                home_group_header=header,
                home_result_card=card,
                home_rows=rows,
                home_attention_row=attention,
                home_attention_icon=attention_icon,
                home_attention_title=attention_title,
                review_issue_row_button=review_row,
                result_summary=result_summary,
                result_engine=result_engine,
                result_mode=result_mode,
                result_audio=result_audio,
                result_inspect_button=inspect_evidence,
                result_play_audio_button=play_audio,
                result_clear_audio_button=clear_audio,
            )
            self._build_setup(page)
            # Tab order follows the eye: setup's rows and its Continue
            # button, then Home's bubble and its rows from the top down.
            self.dynamic["home_key_views"] = (
                *(row["button"] for row in self.dynamic["onboarding_rows"]),
                self.dynamic["onboarding_action"],
                pause, fix, copy_outbox, review_row, inspect_evidence,
                play_audio, clear_audio,
            )

        @objc.python_method
        def _build_setup(self, page: Any) -> None:
            """First-run setup: a stage that says what the step is for, the
            rows the step is waiting on, and one Continue button.

            It has the page to itself while setup is unfinished; the render
            puts the window in setup mode for as long as it is showing.
            """
            width = CONTENT_WIDTH
            setup = FlowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, 580))
            setup.on_layout = self._layout_onboarding
            _accessible(
                setup, self._l("overview.accessibility.onboarding.steps"))
            self._fill(page, setup)

            stage = StagePanelView.alloc().initWithFrame_(NSMakeRect(
                0, 0, width,
                SETUP_STAGE_HEIGHT + BRAND_SHAPES["stage"].shadow_down))
            _accessible(
                stage, self._l("onboarding.stage.accessibility.label"))
            face = LiveFaceView.alloc().initWithFrame_(
                NSMakeRect(0, 0, SETUP_FACE_SIZE, SETUP_FACE_SIZE))
            face.setShadow_(True)
            face.setWhispers_(False)
            try:
                # It took the place of an image view, and VoiceOver still
                # finds it as the guide's picture.
                face.setAccessibilityElement_(True)
                face.setAccessibilityRole_(NSAccessibilityImageRole)
            except Exception:
                pass
            host = FlowView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 164))
            host.on_layout = self._layout_setup_bubble
            bubble = SpeechBubbleView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 400, 140))
            try:
                # Home's bubble carries the phase to VoiceOver. This one
                # has nothing of its own to say: its title and detail are
                # read where they stand, not inside an unnamed group.
                bubble.setAccessibilityElement_(False)
            except Exception:
                pass
            host.addSubview_(bubble)
            onboarding_title = _label(
                self._l("onboarding.permissions.title"),
                NSMakeRect(0, 0, 360, 34), wrap=True, lines=2)
            onboarding_title.setFont_(_role_font("bubble_headline"))
            onboarding_detail = _label(
                "", NSMakeRect(0, 0, 360, 68), size=SETUP_DETAIL_SIZE,
                wrap=True)
            try:
                onboarding_detail.cell().setTruncatesLastVisibleLine_(True)
            except Exception:
                pass
            bubble.addSubview_(onboarding_title)
            bubble.addSubview_(onboarding_detail)
            stage.set_face_view(face, SETUP_FACE_SIZE)
            stage.set_content_view(host)
            setup.addSubview_(stage)

            # How the step stands is the header over its rows.
            onboarding_status = self._section_header("")
            # The progress in words is the bar's VoiceOver value; the field
            # keeps the value and is not drawn.
            onboarding_progress = _label(
                self._l("overview.onboarding.initial_progress"),
                NSMakeRect(0, 0, 10, 10))
            onboarding_progress.setHidden_(True)
            setup.addSubview_(onboarding_progress)

            # One row for each thing the step is waiting on: its icon on a
            # tile, what it is and why, then how it stands (a key cap, its
            # state in words, or the button to a permission's pane).
            rows: list[dict[str, Any]] = []
            for index in range(SETUP_ROW_COUNT):
                tile = NSView.alloc().initWithFrame_(NSMakeRect(
                    0, 0, SETUP_ICON_SIZE, SETUP_ICON_SIZE))
                tile.setWantsLayer_(True)
                if tile.layer() is not None:
                    tile.layer().setCornerRadius_(SETUP_ICON_RADIUS)
                icon = NSImageView.alloc().initWithFrame_(NSMakeRect(
                    8, 8, SETUP_ICON_SIZE - 16, SETUP_ICON_SIZE - 16))
                icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                icon.setEditable_(False)
                _silence_image(icon)
                tile.addSubview_(icon)
                state = self._status_label()
                cap = KeyCapView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, 40, 23))
                button = self._chunky(
                    self._l("onboarding.action.open_system_settings"),
                    "openPermissionSettings:", variant="plain", small=True)
                button.setTag_(index)
                row, title, caption = self._settings_row(
                    "", caption="", accessories=(cap, state, button),
                    leading=tile, wrap=False)
                rows.append({
                    "view": row, "tile": tile, "icon": icon, "title": title,
                    "caption": caption, "state": state, "check": state.icon,
                    "status": state.text, "cap": cap, "button": button,
                    "key": "", "tone": "quiet", "permission": "",
                })
            card = self._settings_group(
                [row["view"] for row in rows]).name_for_voiceover(
                    self._l("onboarding.rows.accessibility.label"))
            step_group = FlowView.alloc().initWithFrame_(NSMakeRect(
                0, 0, width,
                SETUP_ROW_COUNT * ROW_CAPTION_MIN_HEIGHT + 24.0))
            step_group.addSubview_(onboarding_status)
            step_group.addSubview_(card)
            setup.addSubview_(step_group)

            # The foot: how far along, the route, and the one way forward.
            foot = FlowView.alloc().initWithFrame_(NSMakeRect(
                0, 0, width, SETUP_PROGRESS_HEIGHT + 12.0 + SETUP_FOOT_HEIGHT))
            progress_track = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, SETUP_PROGRESS_HEIGHT))
            progress_fill = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 0, SETUP_PROGRESS_HEIGHT))
            for bar in (progress_track, progress_fill):
                bar.setWantsLayer_(True)
                if bar.layer() is not None:
                    bar.layer().setCornerRadius_(SETUP_PROGRESS_HEIGHT / 2.0)
            try:
                progress_track.setAccessibilityElement_(True)
                progress_track.setAccessibilityRole_(
                    NSAccessibilityStaticTextRole)
                progress_fill.setAccessibilityElement_(False)
            except Exception:
                pass
            _accessible(
                progress_track,
                self._l("onboarding.accessibility.progress_track"))
            foot.addSubview_(progress_track)
            foot.addSubview_(progress_fill)
            onboarding_steps: list[Any] = []
            step_markers: list[Any] = []
            step_icons: list[Any] = []
            for step_key in (
                    "permissions", "hotkey", "models", "first_dictation"):
                marker = NSView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, 110, SETUP_FOOT_HEIGHT))
                step_icon = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(0, 11, 18, 18))
                step_icon.setImageScaling_(
                    NSImageScaleProportionallyUpOrDown)
                step_icon.setEditable_(False)
                # The marker's own text says the step and how it stands.
                _silence_image(step_icon)
                step = _label(
                    self._l(f"onboarding.step.{step_key}"),
                    NSMakeRect(24, 5, 80, 30),
                    size=11, weight="medium", rounded=True, wrap=True,
                    lines=2)
                marker.addSubview_(step_icon)
                marker.addSubview_(step)
                foot.addSubview_(marker)
                onboarding_steps.append(step)
                step_markers.append(marker)
                step_icons.append(step_icon)
            onboarding_action = self._chunky(
                self._l("onboarding.action.open_system_settings"),
                "continueSetup:",
                help_text=self._l(
                    "onboarding.action.open_system_settings.help"))
            onboarding_action.setKeyEquivalent_("\r")
            foot.addSubview_(onboarding_action)
            setup.addSubview_(foot)

            face_kicker = _label(
                self._l("onboarding.privacy"),
                NSMakeRect(0, 0, width, SETUP_KICKER_HEIGHT),
                size=11, weight="medium", rounded=True, truncate=True,
                alignment=int(NSTextAlignmentCenter))
            setup.addSubview_(face_kicker)

            self.dynamic.update(
                onboarding_card=setup,
                onboarding_stage=stage,
                onboarding_bubble_host=host,
                onboarding_bubble=bubble,
                onboarding_face=face,
                onboarding_face_kicker=face_kicker,
                onboarding_progress=onboarding_progress,
                onboarding_progress_track=progress_track,
                onboarding_progress_fill=progress_fill,
                onboarding_title=onboarding_title,
                onboarding_detail=onboarding_detail,
                onboarding_status=onboarding_status,
                onboarding_group=step_group,
                onboarding_rows_card=card,
                onboarding_rows=tuple(rows),
                onboarding_foot=foot,
                onboarding_action=onboarding_action,
                onboarding_steps=tuple(onboarding_steps),
                onboarding_step_cards=tuple(step_markers),
                onboarding_step_icons=tuple(step_icons),
            )

        @objc.python_method
        def _switch(self, frame: Any, action: str, *, label: str,
                    help_text: str) -> Any:
            switch = JellySwitch.alloc().initWithFrame_(frame)
            switch.setTarget_(self)
            switch.setAction_(action)
            # The small size System Settings uses for a switch in a row.
            switch.setControlSize_(1)
            return _accessible(switch, label, help_text)

        @objc.python_method
        def _grouped_page(self, container: Any, name: str) -> Any:
            """Fill ``container`` with a scrolling settings page; return its
            document, which the page's sections are added to."""
            scroll = PageScrollView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, 460))
            scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(False)
            scroll.setAutohidesScrollers_(True)
            scroll.setBorderType_(NSNoBorder)
            scroll.setDrawsBackground_(False)
            try:
                scroll.setAutomaticallyAdjustsContentInsets_(False)
            except Exception:
                pass
            scroll.contentView().setDrawsBackground_(False)
            document = GroupedPageView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, 460))
            document.setAutoresizingMask_(NSViewWidthSizable)
            scroll.setDocumentView_(document)
            container.addSubview_(scroll)
            # The page runs to the column's foot, except that a two-line
            # notice grows up into the column's bottom padding: then the
            # page stops above it, so nothing scrolls under the notice.
            to_foot = scroll.bottomAnchor().constraintEqualToAnchor_(
                container.bottomAnchor())
            to_foot.setPriority_(490)
            constraints = [
                scroll.topAnchor().constraintEqualToAnchor_(
                    container.topAnchor()),
                scroll.leadingAnchor().constraintEqualToAnchor_(
                    container.leadingAnchor()),
                scroll.trailingAnchor().constraintEqualToAnchor_(
                    container.trailingAnchor()),
                scroll.bottomAnchor()
                .constraintLessThanOrEqualToAnchor_(
                    container.bottomAnchor()),
                to_foot,
            ]
            NSLayoutConstraint.activateConstraints_(constraints)
            notice = self.dynamic.get("notice")
            if notice is not None:
                # Only while a notice is showing; see ``_animate_notice``.
                self.dynamic.setdefault("notice_limits", []).append(
                    scroll.bottomAnchor()
                    .constraintLessThanOrEqualToAnchor_constant_(
                        notice.topAnchor(), -NOTICE_CLEARANCE))
            self.dynamic.setdefault("page_documents", {})[name] = document
            self.dynamic.setdefault("page_scrolls", {})[name] = scroll
            return document

        @objc.python_method
        def _section_header(self, text: str) -> Any:
            """A section's name over its group: rounded, bold, secondary."""
            header = self._soft(
                text, NSMakeRect(0, 0, CONTENT_WIDTH, 17), wrap=True)
            header.setFont_(_role_font("section_header"))
            try:
                import AppKit as _appkit
                heading = getattr(
                    _appkit, "NSAccessibilityHeadingRole", "AXHeading")
                header.setAccessibilityRole_(heading)
                header.cell().setAccessibilityRole_(heading)
            except Exception:
                pass
            return header

        @objc.python_method
        def _page_note(self, text: str = "", **kwargs: Any) -> Any:
            """Small print under a group, wrapping to the page's width."""
            return self._soft(
                text, NSMakeRect(0, 0, CONTENT_WIDTH, 15), size=12,
                wrap=True, **kwargs)

        @objc.python_method
        def _settings_row(self, title: str | None, *,
                          caption: str | None = None,
                          accessories: Sequence[Any] = (),
                          leading: Any = None, wrap: bool = True) -> Any:
            """A row's title, optional caption and controls, as one view.

            Returns ``(row, title_label, caption_label)``; a label is None
            for a row made without it. Every row in the window comes from
            here, so the title and the caption are the same type wherever
            they are. Text wraps and the row grows with it; ``wrap=False``
            is for a page that cannot scroll (Home, first-run setup), where
            a line that does not fit is cut instead.
            """
            fit = {"wrap": True} if wrap else {"truncate": True}
            title_label = None
            if title is not None:
                title_label = self._ink(
                    title, NSMakeRect(0, 0, 240, 16), size=13,
                    weight="semibold", **fit)
            caption_label = None
            if caption is not None:
                caption_label = self._soft(
                    caption, NSMakeRect(0, 0, 240, 15), size=12, **fit)
            row = SettingsRowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, ROW_MIN_HEIGHT))
            row.configure(
                title_label, caption_label, accessories, leading=leading)
            return row, title_label, caption_label

        @objc.python_method
        def _header_note(self, text: str = "") -> Any:
            """Small print at the far end of a section header's line."""
            return self._soft(
                text, NSMakeRect(0, 0, 240, 15), size=12, truncate=True,
                alignment=int(NSTextAlignmentRight))

        @objc.python_method
        def _settings_group(self, rows: Sequence[Any]) -> Any:
            """A group of rows: the one rounded surface with a hairline
            edge that every page, Home and first-run setup hold rows in."""
            group = GroupBoxView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, 100))
            for row in rows:
                group.add_row(row)
            return group

        @objc.python_method
        def _value_label(self, text: str = "") -> Any:
            """A value shown at a row's trailing edge, in one line."""
            return self._soft(text, NSMakeRect(0, 0, 120, 16), size=12)

        @objc.python_method
        def _status_label(self) -> Any:
            """A state in words with its symbol; tone arrives at render."""
            return StatusLabelView.alloc().initWithFrame_(
                NSMakeRect(0, 0, 120, 16))

        @objc.python_method
        def _show_status(self, view: Any, text: str, tone: str) -> None:
            """Write one state: the phrase in the secondary colour and the
            symbol in its tone's colour, readable in both appearances."""
            dark = getattr(self, "_dark", False)
            palette = palette_for_appearance(dark)
            symbol = (_token_color("brand_ink", dark) if tone == "good"
                      else _error_text_color(dark) if tone == "warn"
                      else _srgb_color(palette.ink_soft))
            view.set_status(text, tone, symbol_color=symbol,
                            text_color=_srgb_color(palette.ink_soft))

        @objc.python_method
        def _arrange_page(self, name: str) -> None:
            """Lay a page out again after its copy or its rows changed."""
            document = self.dynamic.get("page_documents", {}).get(name)
            if document is not None:
                document.arrange()

        def _build_settings(self, page: Any) -> None:
            width = CONTENT_WIDTH
            # The title strip names the pane, so the page starts with its
            # first section, flush under the strip.
            panes = {}
            for name in SETTINGS_PANES:
                pane = NSView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, width, 460))
                pane.setTranslatesAutoresizingMaskIntoConstraints_(False)
                page.addSubview_(pane)
                NSLayoutConstraint.activateConstraints_([
                    pane.topAnchor().constraintEqualToAnchor_(
                        page.topAnchor()),
                    pane.leadingAnchor().constraintEqualToAnchor_(
                        page.leadingAnchor()),
                    pane.trailingAnchor().constraintEqualToAnchor_(
                        page.trailingAnchor()),
                    pane.bottomAnchor().constraintEqualToAnchor_(
                        page.bottomAnchor()),
                ])
                panes[name] = pane

            # One builder per pane, so a pane can be rebuilt without touching
            # its neighbours. Each returns its controls in Tab order.
            self.dynamic.update(
                settings_panes=panes,
                settings_key_views={
                    "Personalize": self._build_settings_personalize(
                        panes["Personalize"]),
                    "Controls": self._build_settings_controls(
                        panes["Controls"]),
                    "Privacy": self._build_settings_privacy(
                        panes["Privacy"]),
                },
            )

        @objc.python_method
        def _build_settings_personalize(
                self, personalize: Any) -> tuple[Any, ...]:
            """Personalize: the face grid, then language and your words."""
            document = self._grouped_page(personalize, "Personalize")
            # Every face as a chip, the chosen one amber. Animal names are
            # the chips' tooltips and VoiceOver labels, and the header's
            # line names the face that is chosen.
            picker = ArtFacePicker.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH - 2 * ROW_INSET, 140))
            picker.populate(
                FACES,
                [self._face_image(face, talk=False) for face in FACES],
                [self._l(f"settings.face.{face}") for face in FACES],
                self, "faceChanged_")
            _accessible(
                picker,
                self._l("settings.accessibility.face.label"),
                self._l("settings.accessibility.face.help"))
            # Which face is chosen, and where it shows, at the far end of
            # the header's line.
            hint = self._header_note()
            face_row = SettingsRowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, 180))
            face_row.addSubview_(picker)
            chip_shape = BRAND_SHAPES["face_chip"]

            # The chips' bodies fill the group from inset to inset. Their
            # hard shadows fall in the inset on the right and below, so
            # the last chip's shadow stays inside the group.
            def face_height(width: float) -> float:
                grid = picker.height_for_width(
                    width - 2.0 * ROW_INSET + chip_shape.shadow_right)
                return 2.0 * ROW_INSET + grid - chip_shape.shadow_down

            def face_layout(width: float, _height: float) -> None:
                inner = width - 2.0 * ROW_INSET + chip_shape.shadow_right
                picker.setFrame_(NSMakeRect(
                    ROW_INSET, ROW_INSET, inner,
                    picker.height_for_width(inner)))
                picker.arrange()

            face_row.configure(custom=(face_height, face_layout))
            document.add_section(
                self._section_header(self._l("pages.personalize.face")),
                self._settings_group([face_row]), trailing=hint)

            personalize_key_views: list[Any] = list(picker.chips)
            # Language leads the list: it decides which recognizer runs, so
            # it outranks every personalization below it.
            rows = (
                ("language", "settings.personalize.language", None),
                ("tones", "settings.personalize.tones", "editTone:"),
                ("snippets", "settings.personalize.snippets", "editSnippets:"),
                ("vocabulary", "settings.personalize.vocabulary", "editVocabulary:"),
                ("corrections", "settings.personalize.corrections", "reviewCorrections:"),
                ("keywords", "settings.personalize.keywords", "inspectKeywords:"),
            )
            row_views = []
            for key, title_key, selector in rows:
                if key == "language":
                    # A pop-up, not a segmented control: eleven choices.
                    popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                        NSMakeRect(0, 0, 190, 28), False)
                    popup.addItemsWithTitles_([
                        self._l(f"settings.language.{code}")
                        for code in LANGUAGES])
                    popup.setTarget_(self)
                    popup.setAction_("languageChanged:")
                    _accessible(
                        popup,
                        self._l("settings.accessibility.language.label"),
                        self._l("settings.accessibility.language.help"))
                    row, _title, detail = self._settings_row(
                        self._l(title_key), caption="", accessories=(popup,))
                    row_views.append(row)
                    personalize_key_views.append(popup)
                    self.dynamic["settings_language_detail"] = detail
                    self.dynamic["settings_language_popup"] = popup
                    continue
                action_key = (
                    "settings.action.review" if key == "corrections" else
                    "settings.action.inspect" if key == "keywords" else
                    "settings.action.edit")
                # Three of these are titled Edit: the label says what each
                # one edits. Review and Inspect also forget, which their
                # help adds; an Edit button's label already says it all.
                button = _button(
                    self._l(action_key), NSMakeRect(0, 0, 60, 28),
                    self, selector,
                    label=self._l(f"pages.personalize.{key}.action"),
                    help_text=self._l(
                        "settings.accessibility.forget.help",
                        setting=self._l(title_key).casefold())
                    if key in {"corrections", "keywords"} else "")
                row, _title, detail = self._settings_row(
                    self._l(title_key), caption="", accessories=(button,))
                row_views.append(row)
                personalize_key_views.append(button)
                self.dynamic[f"settings_{key}_detail"] = detail
                self.dynamic[f"settings_{key}_button"] = button
            document.add_section(
                self._section_header(self._l("pages.personalize.words")),
                self._settings_group(row_views))

            self.dynamic.update(face_picker=picker, face_hint=hint)
            return tuple(personalize_key_views)

        @objc.python_method
        def _build_settings_controls(self, controls: Any) -> tuple[Any, ...]:
            """Controls: the keys, the sounds, and spoken corrections."""
            document = self._grouped_page(controls, "Controls")
            control_rows = (
                ("hotkey", "settings.controls.hotkey", "changeHotkey:",
                 "settings.controls.action.change",
                 "pages.controls.hotkey.action",
                 "settings.accessibility.hotkey.help"),
                ("undo_hotkey", "settings.controls.undo",
                 "changeUndoHotkey:", "settings.controls.action.change",
                 "pages.controls.undo.action",
                 "settings.accessibility.undo_hotkey.help"),
                # Voice modes are six modifier combinations of the capture
                # key, so they sit beside the key.
                ("modes", "settings.personalize.modes", "viewModes:",
                 "settings.action.view",
                 "pages.controls.modes.action",
                 "settings.accessibility.modes.help"),
            )
            control_key_views: list[Any] = []
            key_rows = []
            for (key, title_key, selector, action_key, label_key,
                 help_key) in control_rows:
                button = _button(
                    self._l(action_key), NSMakeRect(0, 0, 60, 28),
                    self, selector, label=self._l(label_key),
                    help_text=self._l(help_key))
                accessories: list[Any] = [button]
                if key in {"hotkey", "undo_hotkey"}:
                    # The bound key, drawn as a key cap where the row names
                    # it; the undo key's cap hides while none is bound.
                    keycap = KeyCapView.alloc().initWithFrame_(
                        NSMakeRect(0, 0, 60, 23))
                    accessories.insert(0, keycap)
                    self.dynamic[f"{key}_keycap"] = keycap
                row, _title, detail = self._settings_row(
                    self._l(title_key), caption="", accessories=accessories)
                key_rows.append(row)
                control_key_views.append(button)
                self.dynamic[f"settings_{key}_detail"] = detail
                self.dynamic[f"settings_{key}_button"] = button
            document.add_section(
                self._section_header(self._l("pages.controls.keys")),
                self._settings_group(key_rows))

            # Sound is a three-way choice, so it earns a segmented control
            # rather than a switch: "off" is one of three answers, not the
            # absence of an answer.
            sound_control = JellySegmentedControl.alloc().initWithFrame_(
                NSMakeRect(0, 0, 240, 24))
            sound_control.setSegmentCount_(len(SOUND_THEMES))
            for index, theme in enumerate(SOUND_THEMES):
                sound_control.setLabel_forSegment_(
                    self._l(f"settings.controls.sound.{theme}"), index)
            sound_control.setTarget_(self)
            sound_control.setAction_("soundThemeChanged:")
            _accessible(
                sound_control,
                self._l("settings.accessibility.sound.label"),
                self._l("settings.accessibility.sound.help"))
            sound_preview = _button(
                self._l("settings.controls.action.preview"),
                NSMakeRect(0, 0, 60, 28), self, "previewSound:",
                label=self._l("pages.controls.sound.action"),
                help_text=self._l("settings.accessibility.sound_preview.help"))
            sound_row, _sound_title, sound_detail = self._settings_row(
                self._l("settings.controls.sound"), caption="",
                accessories=(sound_control, sound_preview))
            control_key_views.extend([sound_control, sound_preview])
            document.add_section(
                self._section_header(self._l("pages.controls.sounds")),
                self._settings_group([sound_row]))

            # What each switch does is its caption. The repeat repair comes
            # first: it is on for everyone. "Actually" sits last, beside the
            # note under the group, which is the one sentence it gets wrong.
            repeats_switch = self._switch(
                NSMakeRect(0, 0, 40, 22), "removeRepeatsChanged:",
                label=self._l("settings.accessibility.remove_repeats.label"),
                help_text=self._l(
                    "settings.accessibility.remove_repeats.help"))
            repeats_row, _title, repeats_detail = self._settings_row(
                self._l("settings.controls.remove_repeats"), caption="",
                accessories=(repeats_switch,))
            control_key_views.append(repeats_switch)
            correction_switch = self._switch(
                NSMakeRect(0, 0, 40, 22), "selfCorrectionChanged:",
                label=self._l("settings.accessibility.self_correction.label"),
                help_text=self._l(
                    "settings.accessibility.self_correction.help"))
            correction_row, _title, correction_detail = self._settings_row(
                self._l("settings.controls.self_correction"), caption="",
                accessories=(correction_switch,))
            correction_cost = self._page_note(
                self._l("settings.controls.self_correction.cost"))
            control_key_views.append(correction_switch)
            document.add_section(
                self._section_header(self._l("pages.controls.corrections")),
                self._settings_group([repeats_row, correction_row]),
                (correction_cost,))

            self.dynamic.update(
                sound_theme_control=sound_control,
                sound_preview_button=sound_preview,
                sound_theme_detail=sound_detail,
                self_correction_toggle=correction_switch,
                self_correction_detail=correction_detail,
                remove_repeats_toggle=repeats_switch,
                remove_repeats_detail=repeats_detail,
            )
            return tuple(control_key_views)

        @objc.python_method
        def _build_settings_privacy(self, privacy: Any) -> tuple[Any, ...]:
            """Privacy: one switch per thing that may be kept."""
            document = self._grouped_page(privacy, "Privacy")
            privacy_rows = {
                "flight": ("settings.privacy.flight", "flightChanged:",
                           "settings.accessibility.flight.label",
                           "settings.accessibility.flight.help"),
                "acoustic": ("settings.privacy.acoustic",
                             "acousticTimeMachineChanged:",
                             "settings.accessibility.acoustic.label",
                             "settings.accessibility.acoustic.help"),
                # Off by default, and privacy-consistent with the Voice
                # Inbox: the list is metadata until an explicit reveal.
                "recent": ("settings.privacy.recent",
                           "recentDictationsChanged:",
                           "settings.accessibility.recent.label",
                           "settings.accessibility.recent.help"),
                "voice_objects": ("settings.privacy.voice_objects",
                                  "voiceObjectCommandsChanged:",
                                  "settings.accessibility.voice_objects.label",
                                  "settings.accessibility.voice_objects.help"),
            }
            browse_recent = _button(
                self._l("settings.privacy.recent.browse"),
                NSMakeRect(0, 0, 60, 28), self, "browseRecentDictations:",
                label=self._l("pages.privacy.recent.action"),
                help_text=self._l("settings.privacy.recent.browse.help"))
            inspect_voice_objects = _button(
                self._l("settings.privacy.voice_objects.inspect"),
                NSMakeRect(0, 0, 60, 28), self, "inspectVoiceObjects:",
                label=self._l("pages.privacy.voice_objects.action"),
                help_text=self._l(
                    "settings.privacy.voice_objects.inspect.help"))
            buttons = {"recent": browse_recent,
                       "voice_objects": inspect_voice_objects}
            controls: dict[str, tuple[Any, Any, Any]] = {}
            for key, (title_key, selector, label_key,
                      help_key) in privacy_rows.items():
                switch = self._switch(
                    NSMakeRect(0, 0, 40, 22), selector,
                    label=self._l(label_key), help_text=self._l(help_key))
                accessories = ([buttons[key]] if key in buttons else []) + [
                    switch]
                row, _title, detail = self._settings_row(
                    self._l(title_key), caption="", accessories=accessories)
                controls[key] = (row, switch, detail)
            flight_row, flight, flight_detail = controls["flight"]
            flight_detail.setStringValue_(
                self._l("settings.privacy.flight.detail"))
            acoustic_row, acoustic, acoustic_detail = controls["acoustic"]
            acoustic_detail.setStringValue_(
                self._l("pages.privacy.acoustic.detail"))
            recent_row, recent, recent_detail = controls["recent"]
            recent_detail.setStringValue_(
                self._l("settings.privacy.recent.detail"))
            (voice_row, voice_objects,
             voice_object_status) = controls["voice_objects"]
            document.add_section(
                self._section_header(self._l("pages.privacy.audio")),
                self._settings_group([flight_row, acoustic_row]))
            document.add_section(
                self._section_header(self._l("pages.privacy.dictations")),
                self._settings_group([recent_row, voice_row]))

            self.dynamic.update(
                recent_dictations_toggle=recent,
                recent_dictations_detail=recent_detail,
                recent_dictations_browse=browse_recent,
                flight_toggle=flight,
                acoustic_time_machine_toggle=acoustic,
                voice_object_commands_toggle=voice_objects,
                voice_object_commands_status=voice_object_status,
                voice_object_inspect_button=inspect_voice_objects,
            )
            # Tab follows the page: top to bottom, left to right.
            return (
                flight, acoustic, browse_recent, recent,
                inspect_voice_objects, voice_objects)

        def _build_advanced(self, page: Any) -> None:
            document = self._grouped_page(page, "Advanced")
            # ⌘D lives on the always-visible sidebar row, so the shortcut
            # reaches Advanced from every section.

            # ---- Models: Selective Re-listen, then the local models, each
            # with its state in words at the trailing edge.
            relisten = self._switch(
                NSMakeRect(0, 0, 40, 22), "selectiveRelistenChanged:",
                label=self._l("models.accessibility.relisten"),
                help_text=self._l("models.relisten.help"))
            relisten.setToolTip_(self._l("models.relisten.help"))
            relisten_row, _title, relisten_status = self._settings_row(
                self._l("models.relisten.label"), caption="",
                accessories=(relisten,))
            _accessible(relisten_status, self._l(
                "models.accessibility.relisten"))
            # Which program runs the speech model: a switch whose caption
            # says what it does and, once known, which one answered last.
            speech_runtime = self._switch(
                NSMakeRect(0, 0, 40, 22), "newSpeechRuntimeChanged:",
                label=self._l("models.accessibility.speech_runtime"),
                help_text=self._l("models.speech_runtime.help"))
            speech_runtime_row, _title, speech_runtime_detail = (
                self._settings_row(
                    self._l("models.speech_runtime.label"), caption="",
                    accessories=(speech_runtime,)))
            model_rows = []
            model_status_views = []
            group_rows = [relisten_row, speech_runtime_row]
            for _index in range(4):
                status = self._status_label()
                row, name, detail = self._settings_row(
                    self._l("models.waiting"), caption="",
                    accessories=(status,))
                group_rows.append(row)
                model_rows.append((row, name, detail, status.text))
                model_status_views.append(status)
            model_guidance = self._page_note(self._l("models.guidance"))
            # The receipt's own wording stays the runtime's; the page says
            # what it found in plain words.
            advisory = self._page_note(
                self._l("pages.models.advisory"),
                accessibility_label=self._l("pages.models.advisory.label"))
            document.add_section(
                self._section_header(self._l("pages.advanced.models")),
                self._settings_group(group_rows),
                (model_guidance, advisory))

            # ---- Status: one row per thing the runtime reports.
            keys = (("diagnostics.service", "diag_service", True),
                    ("diagnostics.microphone", "diag_microphone", True),
                    ("diagnostics.accessibility", "diag_accessibility", True),
                    ("pages.diagnostics.regression", "diag_regression",
                     False),
                    ("diagnostics.motion", "diag_motion", False),
                    ("diagnostics.build", "diag_version", False))
            status_rows = []
            diag_status_views = {}
            for heading_key, key, stated in keys:
                if stated:
                    view = self._status_label()
                    value = view.text
                    diag_status_views[key] = view
                else:
                    view = value = self._value_label(
                        self._l("diagnostics.unknown"))
                row, _title, _caption = self._settings_row(
                    self._l(heading_key), accessories=(view,))
                status_rows.append(row)
                self.dynamic[key] = value
            guidance = self._page_note(self._l("diagnostics.ready"))
            document.add_section(
                self._section_header(self._l("pages.advanced.status")),
                self._settings_group(status_rows), (guidance,))

            # ---- Support: verification, then what to send with a report.
            progress = NSProgressIndicator.alloc().initWithFrame_(
                NSMakeRect(0, 0, 16, 16))
            progress.setStyle_(1)
            progress.setDisplayedWhenStopped_(False)
            progress.setControlSize_(1)
            verify = _button(self._l("diagnostics.action.verify"),
                             NSMakeRect(0, 0, 60, 28), self, "verify:")
            verify.setKeyEquivalent_("r")
            verify.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
            verify_row, _title, verification = self._settings_row(
                self._l("pages.support.verify"),
                caption=self._l("diagnostics.verification.not_run"),
                accessories=(progress, verify))
            open_log = _button(self._l("diagnostics.action.log"),
                               NSMakeRect(0, 0, 60, 28), self, "openLog:")
            log_row, _title, _caption = self._settings_row(
                self._l("pages.support.log"),
                caption=self._l("pages.support.log.detail"),
                accessories=(open_log,))
            copy_support_snapshot = _button(
                self._l("diagnostics.action.copy_support"),
                NSMakeRect(0, 0, 60, 28), self, "copySupportSnapshot:",
                help_text=self._l("diagnostics.action.copy_support.help"))
            snapshot_row, _title, _caption = self._settings_row(
                self._l("pages.support.snapshot"),
                caption=self._l("pages.support.snapshot.detail"),
                accessories=(copy_support_snapshot,))
            export_support_bundle = _button(
                self._l("diagnostics.action.export_support"),
                NSMakeRect(0, 0, 60, 28), self, "exportSupportBundle:",
                help_text=self._l("diagnostics.action.export_support.help"))
            bundle_row, _title, _caption = self._settings_row(
                self._l("pages.support.bundle"),
                caption=self._l("pages.support.bundle.detail"),
                accessories=(export_support_bundle,))
            open_system_settings = _button(
                self._l("diagnostics.action.open_system_settings"),
                NSMakeRect(0, 0, 60, 28),
                self, "openSystemSettings:",
                label=self._l(
                    "diagnostics.accessibility.open_system_settings"),
                help_text=self._l(
                    "diagnostics.action.open_system_settings.help"))
            permissions_row, _title, _caption = self._settings_row(
                self._l("pages.support.permissions"),
                caption=self._l("pages.support.permissions.detail"),
                accessories=(open_system_settings,))
            license_notices = _button(
                self._l("diagnostics.action.licenses"),
                NSMakeRect(0, 0, 60, 28), self, "openLicense:",
                label=self._l("pages.support.licenses.action"))
            source = _button(
                self._l("diagnostics.action.source"),
                NSMakeRect(0, 0, 60, 28), self, "openSource:",
                label=self._l("pages.support.source.action"))
            license_row, _title, _caption = self._settings_row(
                self._l("pages.support.license"),
                caption=self._l("diagnostics.license"),
                accessories=(license_notices, source))
            document.add_section(
                self._section_header(self._l("pages.advanced.support")),
                self._settings_group([
                    verify_row, log_row, snapshot_row, bundle_row,
                    permissions_row, license_row]))
            self.dynamic.update(
                model_rows=model_rows,
                model_status_views=model_status_views,
                diag_status_views=diag_status_views,
                model_wallet_advisory=advisory,
                model_guidance=model_guidance,
                selective_relisten_toggle=relisten,
                selective_relisten_status=relisten_status,
                new_speech_runtime_toggle=speech_runtime,
                new_speech_runtime_detail=speech_runtime_detail,
                open_system_settings_button=open_system_settings,
                open_system_settings_row=permissions_row,
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
            entering = not bool(self.window.isVisible())
            self.view_model.refresh()
            self.render()
            # Activate BEFORE ordering front. This process runs as an
            # accessory (no Dock icon), so a window ordered front while the
            # app is still inactive joins a background window list and stays
            # behind whatever the user was looking at; activating afterwards
            # does not re-order it. The symptom is having to click "Open
            # Whisper Face" twice, because the second click finds the app
            # already active. orderFrontRegardless then covers the case where
            # macOS declines the activation.
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
            self.window.orderFrontRegardless()
            if self._onboarding_presentation is not None:
                self._animate_onboarding_face(
                    self._onboarding_presentation)
            if self.timer is None:
                self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    2.0, self, "refreshTimer:", None, True)
            self._sync_face_clock()
            if entering:
                self._entrance_stagger()

        @objc.python_method
        def _sync_face_clock(self) -> None:
            """One gate for the 30fps face clock: it runs only while the
            window is visible and unoccluded, Home is the section, and
            Reduce Motion is off. Everything else stops it — including
            navigating to Settings/Advanced and Reduce Motion flips, both
            reported by review as leaks."""
            face_view = self.dynamic.get("home_face_view")
            if face_view is None:
                return
            try:
                occluded = not bool(
                    self.window.occlusionState()
                    & NSWindowOcclusionStateVisible)
            except Exception:
                occluded = False
            state = self.view_model.state
            # Home's face is hidden while setup has the page.
            live = (bool(self.window.isVisible()) and not occluded
                    and state.section == "Home"
                    and not self.setup_mode
                    and not state.prefers_reduced_motion)
            if live:
                face_view.startLiving()
            else:
                face_view.stopLiving()

        @objc.python_method
        def _entrance_stagger(self) -> None:
            """Reveal Home in semantic chunks, ~90ms apart, on open only.

            The face pops with the first chunk; a second open while the
            window is already visible never replays the entrance.  Reduce
            Motion shows everything immediately.
            """
            state = self.view_model.state
            if state.prefers_reduced_motion or state.section != "Home":
                return
            setting_up = not bool(self.dynamic["onboarding_card"].isHidden())
            blocks = [
                self.dynamic["onboarding_stage"],
                self.dynamic["onboarding_rows_card"],
                self.dynamic["onboarding_foot"],
            ] if setting_up else [
                self.dynamic["home_stage"],
                self.dynamic["overview_metric_cards"][0],
                self.dynamic["home_group"],
            ]
            delay = 0.0
            for view in blocks:
                if view is None or bool(view.isHidden()):
                    continue
                view.setAlphaValue_(0.0)
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    delay, self, "staggerReveal:", view, False)
                delay += 0.09
            add_jelly_motion(
                self.dynamic[
                    "onboarding_face" if setting_up else "home_face_view"],
                "pop", reduced_motion=state.prefers_reduced_motion)

        def staggerReveal_(self, timer: Any) -> None:
            view = timer.userInfo()
            if view is not None:
                fade_in(view, reduced_motion=bool(
                    self.view_model.state.prefers_reduced_motion))

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

            # Sidebar, then the title strip's items for this page, then
            # the page itself.
            row_index = sidebar_row_index(
                state.section, state.settings_pane)
            row = SIDEBAR_ROWS[row_index]
            controls = [view for row_key, view, _visible in self.title_items
                        if row_key == row.key]
            controls.extend(self.key_views_by_section[state.section])
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

            # Setup mode has no sidebar to stop at.
            chain = [] if self.setup_mode else list(self.sidebar_rows)
            chain.extend(
                control for control in controls
                if visible_and_enabled(control))
            for current, following in zip(chain, chain[1:] + chain[:1]):
                current.setNextKeyView_(following)
            # Keyboard entry starts where the sidebar's one Tab stop is:
            # the row that is showing, or the page when there is no sidebar.
            first = (chain[0] if self.setup_mode and chain
                     else self.sidebar_rows[row_index])
            self.window.setInitialFirstResponder_(first)
            return len(chain)

        def render(self) -> None:
            global _REDUCE_MOTION
            state = self.view_model.state
            # Jelly controls read this gate on every press so Reduce Motion
            # silences the springs exactly like the HUD path.
            _REDUCE_MOTION = bool(state.prefers_reduced_motion)
            # One method per surface, always in this order. The shell goes
            # first because it resolves the appearance every other surface
            # reads back as ``self._dark``; the finish goes last because it
            # depends on what the surfaces enabled and hid.
            self._render_shell(state)
            self._render_home(state)
            self._render_onboarding(state)
            self._render_settings_shell(state)
            self._render_settings_personalize(state)
            self._render_settings_controls(state)
            self._render_settings_privacy(state)
            self._render_advanced(state)
            self._render_notices(state)
            self._render_finish(state)
            # A view that draws itself reads Increase Contrast when it
            # draws, and AppKit promises no redraw when that setting
            # flips. A page that is already laid out is not marked by its
            # layout either, so say so here, once per flip.
            contrast = _increase_contrast()
            if self._contrast is not None and contrast != self._contrast:
                self._redraw_everything()
            self._contrast = contrast
            self._rendered_from = self._what_a_render_reads()
            self.renders += 1

        @objc.python_method
        def _redraw_everything(self) -> None:
            """Mark every view in the window for drawing."""
            def mark(view: Any) -> None:
                if isinstance(view, GroupBoxView):
                    view.marked += 1
                view.setNeedsDisplay_(True)
                for child in view.subviews():
                    mark(child)

            mark(self.window.contentView())

        @objc.python_method
        def _what_a_render_reads(self) -> tuple[Any, ...]:
            """What a render reads: the view model's state, and whether
            the window is drawn dark and with increased contrast. Nothing
            else decides what a render writes."""
            return (self.view_model.state,
                    _uses_dark_appearance(self.window),
                    _increase_contrast())

        @objc.python_method
        def _render_shell(self, state: GUIState) -> None:
            """Window chrome: palette, visible page, sidebar, title strip."""
            self._apply_window_theme(state)
            for section, page in self.pages.items():
                page.setHidden_(section != state.section)
            # The sidebar and the title strip are a projection of the view
            # model: the row is worked out from its section and pane here,
            # on every render, and stored nowhere else.
            row_index = sidebar_row_index(state.section, state.settings_pane)
            self._sync_sidebar(state, row_index)
            self._sync_title_strip(state, row_index)
            self._animate_section_change(state, row_index)

        @objc.python_method
        def _render_home(self, state: GUIState) -> None:
            """Home: the stage, the stat stickers and the rows under them."""
            dark = getattr(self, "_dark", False)
            palette = palette_for_appearance(dark)
            face_name = self._l(f"settings.face.{state.face}")
            self.dynamic["home_stage"].set_face(state.face)
            face_view = self.dynamic["home_face_view"]
            face_view.setFace_(state.face)
            face_view.setReduceMotion_(bool(state.prefers_reduced_motion))
            # Mode only: the live level arrives through feed_level so the
            # 2s refresh can never stomp a mid-sentence mouth.
            face_view.setMode_({
                "recording": "recording",
                "processing": "processing",
                "degraded": "error",
            }.get(state.status_phase, "idle"))
            sync_accessibility(
                face_view, state.status_title, label=face_name)

            phase_keys = {
                "ready": "overview.phase.ready",
                "recording": "overview.phase.recording",
                "processing": "overview.phase.processing",
                "recovery": "overview.phase.recovery",
                "degraded": "overview.phase.degraded",
                "paused": "overview.phase.paused",
                "starting": "overview.phase.starting",
            }
            phase = (self._l(phase_keys[state.status_phase])
                     if state.status_phase in phase_keys
                     else state.status_phase.upper())
            # The phase is not drawn any more. The field keeps its value
            # and the bubble says it to VoiceOver as it enters the group.
            self.dynamic["overview_phase"].setStringValue_(phase)
            sync_accessibility(
                self.dynamic["home_bubble"], phase,
                label=self._l("overview.accessibility.phase"))
            self.dynamic["overview_status"].setStringValue_(state.status_title)
            # The instruction shows the capture key that is bound, as a key
            # cap, wherever the status copy names it.
            names_key = state.status_phase in ("ready", "recording")
            detail = self.dynamic["overview_detail"]
            detail.setAttributedStringValue_(copy_with_key_cap(
                state.status_detail,
                state.hotkey_label if names_key else "",
                font=_font(BUBBLE_DETAIL_SIZE),
                color=_srgb_color(palette.ink_soft)))
            self.dynamic["overview_engine"].setStringValue_(
                self._l(
                    "overview.engine.active", engine=state.active_engine))
            outbox_key, outbox_values = home_outbox_line(state)
            outbox = self._l(outbox_key, **outbox_values)
            self.dynamic["overview_outbox"].setStringValue_(outbox)
            for key, label_key, value in (
                ("overview_phase", "overview.accessibility.phase", phase),
                ("overview_status", "overview.accessibility.status",
                 state.status_title),
                ("overview_detail", "overview.accessibility.detail",
                 state.status_detail),
                ("overview_engine", "overview.accessibility.engine",
                 str(self.dynamic["overview_engine"].stringValue())),
                ("overview_outbox", "overview.accessibility.outbox", outbox),
            ):
                sync_accessibility(
                    self.dynamic[key], value, label=self._l(label_key))

            # The bubble holds the primary action and at most one more.
            shown = home_bubble_actions(state)
            self.dynamic["copy_outbox_button"].setHidden_(
                "copy_outbox" not in shown)
            self.dynamic["review_issue_button"].setHidden_(
                "review" not in shown)
            in_row = home_review_in_row(state)
            self.dynamic["home_attention_row"].setHidden_(not in_row)
            issue = state.degraded_issues[0] if state.degraded_issues else None
            attention_title = (
                issue.title if issue is not None
                else self._l("home.attention.fallback"))
            self.dynamic["home_attention_title"].setStringValue_(
                attention_title)
            sync_accessibility(
                self.dynamic["home_attention_title"], attention_title,
                label=self._l("home.attention.accessibility.label"))
            try:
                self.dynamic["home_attention_icon"].setContentTintColor_(
                    _error_text_color(dark)
                    if issue is None or issue.severity == "error"
                    else _srgb_color(palette.ink_soft))
            except Exception:
                pass
            pause_title = self._l(
                "overview.action.resume" if state.paused
                else "overview.action.pause")
            self._set_chunky_title(self.dynamic["pause_button"], pause_title)
            sync_accessibility(
                self.dynamic["pause_button"],
                self._l(
                    "overview.action.pause.state.paused" if state.paused
                    else "overview.action.pause.state.active"),
                label=self._l(
                    "overview.action.pause.label", action=pause_title),
            )
            # A sticker is one big number. The first one shows how long the
            # last dictation took; how many words it was is in the row
            # under the stickers, and VoiceOver still hears both here.
            if state.last_latency_ms is None:
                last = last_number = self._l("overview.metric.last.empty")
            else:
                suffix = ""
                if state.last_word_count is not None:
                    words_key = (
                        "overview.metric.last.words.one"
                        if state.last_word_count == 1
                        else "overview.metric.last.words.many")
                    suffix = self._l(
                        words_key, count=state.last_word_count)
                seconds = f"{state.last_latency_ms / 1000:.2f}"
                last = self._l(
                    "overview.metric.last.value",
                    seconds=seconds, words=suffix)
                last_number = self._l(
                    "overview.metric.last.value", seconds=seconds, words="")
            words = self._l(
                "overview.metric.words.value",
                count=f"{state.words_today:,}")
            saved = self._l(
                "overview.metric.saved.value",
                minutes=f"{state.minutes_saved:,.0f}")
            for index, (key, heading_key, label_key, number, value
                        ) in enumerate((
                ("overview_last", "overview.metric.last.heading",
                 "overview.accessibility.last", last_number, last),
                ("overview_words", "overview.metric.words.heading",
                 "overview.accessibility.words", words, words),
                ("overview_saved", "overview.metric.saved.heading",
                 "overview.accessibility.saved", saved, saved),
            )):
                self.dynamic[key].configure(
                    number, self._l(heading_key), index)
                # The sticker shows the short heading; VoiceOver keeps the
                # fuller label it always had.
                sync_accessibility(
                    self.dynamic[key], value, label=self._l(label_key))

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
            self._layout_home()
            self._animate_home_landing(state)

        @objc.python_method
        def _render_onboarding(self, state: GUIState) -> None:
            """First-run setup, and Home giving it the page while it is up."""
            face_name = self._l(f"settings.face.{state.face}")
            presentation = onboarding_presentation(
                state.onboarding_steps,
                acknowledged=state.onboarding_acknowledged,
                locale=self.view_model.locale,
                permission_missing=bool(state.missing_permissions),
            )
            self._onboarding_presentation = presentation
            # Setup has the window to itself only while it is what Home is
            # showing: a page shortcut pressed during setup shows that page
            # with the shell back around it, and coming Home returns here.
            self.set_setup_mode(
                presentation.visible and state.section == "Home")
            self.dynamic["onboarding_card"].setHidden_(
                not presentation.visible)
            # Every block of the live Home hides while setup is up, so
            # nothing of it shows through and none of its controls stays
            # in the Tab order.
            self.dynamic["home_cluster"].setHidden_(presentation.visible)
            self.dynamic["home_stage"].setHidden_(presentation.visible)
            self.dynamic["home_group"].setHidden_(presentation.visible)
            self.dynamic["home_result_card"].setHidden_(presentation.visible)
            for card in self.dynamic["overview_metric_cards"]:
                card.setHidden_(presentation.visible)
            # Once every step is confirmed the rows list the four steps
            # with their check marks, so the route does not say it again.
            for marker in self.dynamic["onboarding_step_cards"]:
                marker.setHidden_(presentation.complete)
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
            # The bubble shows the capture key that is bound as a key cap
            # where the step's copy names it, as Home's bubble does.
            detail = self.dynamic["onboarding_detail"]
            detail.setAttributedStringValue_(copy_with_key_cap(
                presentation.detail,
                state.hotkey_label
                if presentation.current_key == "hotkey" else "",
                font=_font(SETUP_DETAIL_SIZE),
                color=_srgb_color(palette_for_appearance(
                    getattr(self, "_dark", False)).ink_soft)))
            # How the step stands is the header over its rows.
            self.dynamic["onboarding_status"].setStringValue_(
                presentation.status)
            # One way forward per step. While a permission is missing the
            # way forward is that permission's pane, and the button to it
            # is in its row; the foot's Continue waits, disabled, until
            # the runtime stops reporting one as missing.
            rows = setup_rows(state, locale=self.view_model.locale)
            waiting = setup_waits_on_a_row(rows)
            action = self.dynamic["onboarding_action"]
            action_title = (self._l("onboarding.action.continue")
                            if waiting else presentation.action_title)
            self._set_chunky_title(
                action, action_title, "plain" if waiting else "amber")
            action.setEnabled_(not waiting)
            action.setKeyEquivalent_("" if waiting else "\r")
            try:
                action.setAccessibilityHelp_(
                    self._l("onboarding.action.waiting.help")
                    if waiting else presentation.action_help)
            except Exception:
                pass
            # The character opens its mouth exactly where the copy asks the
            # person to speak, and again for the celebration.
            face = self.dynamic["onboarding_face"]
            self.dynamic["onboarding_stage"].set_face(state.face)
            face.setFace_(state.face)
            face.setReduceMotion_(bool(state.prefers_reduced_motion))
            face.setPose_(
                "talk" if (presentation.complete
                           or presentation.current_key == "first_dictation")
                else "idle")
            sync_accessibility(
                face, face_name,
                label=self._l(
                    "overview.accessibility.onboarding.face",
                    face=face_name),
            )
            # The key cap stands in for the key's name in the drawn line,
            # so VoiceOver is given the whole sentence, key included.
            for key, label_key, spoken in (
                ("onboarding_progress",
                 "overview.accessibility.onboarding.progress",
                 presentation.progress),
                ("onboarding_title",
                 "overview.accessibility.onboarding.title",
                 presentation.title),
                ("onboarding_detail",
                 "overview.accessibility.onboarding.detail",
                 presentation.detail),
            ):
                sync_accessibility(
                    self.dynamic[key], spoken, label=self._l(label_key))
            # The header over the rows is read as what it says.
            sync_accessibility(
                self.dynamic["onboarding_status"], presentation.status)
            sync_accessibility(
                action, presentation.status, label=action_title)
            self._render_setup_rows(rows)
            self._layout_onboarding()
            self._apply_onboarding_theme(state, presentation)
            self._animate_onboarding_face(presentation)
            self._animate_onboarding_steps(state)

        @objc.python_method
        def _render_setup_rows(self, specs: Sequence[SetupRow]) -> None:
            """Fill the setup rows with what this step is waiting on."""
            locale = self.view_model.locale
            for index, row in enumerate(self.dynamic["onboarding_rows"]):
                spec = specs[index] if index < len(specs) else None
                row["view"].setHidden_(spec is None)
                if spec is None:
                    row.update(key="", tone="quiet", permission="")
                    row["button"].setHidden_(True)
                    row["button"].setKeyEquivalent_("")
                    continue
                row.update(
                    key=spec.key, tone=spec.tone, permission=spec.permission)
                image = _symbol_image(
                    spec.symbol, size=15.0, weight="semibold")
                row["icon"].setImage_(image)
                row["title"].setStringValue_(spec.title)
                row["caption"].setStringValue_(spec.caption)
                row["caption"].setHidden_(not spec.caption)
                # How the row stands, said the way every page says a
                # state: a symbol for the tone, then the words.
                row["state"].setHidden_(not spec.status)
                self._show_status(
                    row["state"], spec.status,
                    {"good": "good", "attention": "warn"}.get(
                        spec.tone, "neutral"))
                row["cap"].setHidden_(not spec.key_cap)
                if spec.key_cap:
                    row["cap"].set_key(spec.key_cap, locale=locale)
                button = row["button"]
                button.setHidden_(not spec.permission)
                # The step's primary action is the first missing
                # permission's own button: the one amber button, and the
                # one that answers Return.
                self._set_chunky_title(
                    button, str(button.title()),
                    "amber" if spec.primary else "plain")
                button.setKeyEquivalent_("\r" if spec.primary else "")
                if spec.permission:
                    _accessible(
                        button,
                        self._l("onboarding.permission.open.label",
                                permission=spec.title),
                        self._l("onboarding.permission.open.help",
                                permission=spec.title))
                # The row's words read as they are; its state is told
                # which row it belongs to.
                sync_accessibility(
                    row["status"], spec.status,
                    label=self._l(
                        "onboarding.row.accessibility.status",
                        row=spec.title))

        @objc.python_method
        def _render_settings_shell(self, state: GUIState) -> None:
            """Settings: which pane is showing."""
            for pane, view in self.dynamic["settings_panes"].items():
                view.setHidden_(pane != state.settings_pane)

        @objc.python_method
        def _render_setting_summaries(
                self, summaries: Mapping[str, str]) -> None:
            """Write the one-line summaries under settings rows."""
            for key, value in summaries.items():
                set_accessible_text(
                    self.dynamic[f"settings_{key}_detail"], value,
                    label=self._l(
                        f"settings.accessibility.{key}_summary.label"))

        @objc.python_method
        def _render_settings_personalize(self, state: GUIState) -> None:
            """Settings, Personalize: face, language and personal data."""
            settings = state.settings
            # An empty personalization row is an invitation, not a zero: the
            # count only earns the line once there is something to count.
            setting_summaries = {
                "tones": self._l(
                    plural_key("settings.personalize.tones.detail",
                               len(settings.app_tones)),
                    count=len(settings.app_tones))
                if settings.app_tones else self._l(
                    "settings.personalize.tones.empty"),
                "snippets": self._l(
                    plural_key("settings.personalize.snippets.detail",
                               len(settings.snippets)),
                    count=len(settings.snippets))
                if settings.snippets else self._l(
                    "settings.personalize.snippets.empty"),
                "vocabulary": self._l(
                    "settings.personalize.vocabulary.detail",
                    terms=self._l(
                        plural_key("settings.personalize.vocabulary.terms",
                                   len(settings.manual_vocabulary)),
                        count=len(settings.manual_vocabulary)),
                    bans=self._l(
                        plural_key(
                            "settings.personalize.vocabulary.exclusions",
                            len(settings.banned_vocabulary)),
                        count=len(settings.banned_vocabulary)))
                if (settings.manual_vocabulary or settings.banned_vocabulary)
                else self._l("settings.personalize.vocabulary.empty"),
                "corrections": self._l(
                    plural_key("pages.personalize.corrections.detail",
                               len(settings.corrections)),
                    count=len(settings.corrections))
                if settings.corrections else self._l(
                    "settings.personalize.corrections.empty"),
                "keywords": self._l(
                    "pages.personalize.keywords.detail"),
            }
            self._render_setting_summaries(setting_summaries)
            # One quiet line, stated the same way every time: English gets
            # the fast native recognizer, everything else gets Whisper.
            language_name = self._l(f"settings.language.{state.language}")
            set_accessible_text(
                self.dynamic["settings_language_detail"],
                self._l("settings.personalize.language.native")
                if state.language in NATIVE_RECOGNIZER_LANGUAGES
                else self._l("settings.personalize.language.whisper",
                             language=language_name),
                label=self._l(
                    "settings.accessibility.language_summary.label"))
            self.dynamic["settings_language_popup"].selectItemAtIndex_(
                LANGUAGES.index(state.language))
            sync_accessibility(
                self.dynamic["settings_language_popup"], language_name,
                label=self._l("settings.accessibility.language.label"))
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
            self.dynamic["face_hint"].setStringValue_(self._l(
                "pages.personalize.face.hint",
                face=self._l(f"settings.face.{state.face}")))
            self._arrange_page("Personalize")

        @objc.python_method
        def _render_settings_controls(self, state: GUIState) -> None:
            """Settings, Controls: keys, sounds and spoken corrections."""
            settings = state.settings
            setting_summaries = {
                # Every reference to the shortcut reads the key that is
                # actually bound, not the key that used to be hardcoded.
                "modes": self._l(
                    "settings.personalize.modes.detail",
                    hotkey=state.hotkey_label),
                "hotkey": hotkey_row_detail(state, locale=self.view_model.locale),
                "undo_hotkey": (
                    self._l("settings.controls.undo.detail",
                            hotkey=state.undo_hotkey_label)
                    if state.undo_hotkey_label
                    else self._l("settings.controls.undo.unset")),
            }
            self._render_setting_summaries(setting_summaries)
            set_accessible_text(
                self.dynamic["sound_theme_detail"],
                self._l(f"settings.controls.sound.detail.{state.sound_theme}"),
                label=self._l("settings.accessibility.sound.label"))
            self.dynamic["sound_theme_control"].setSelectedSegment_(
                SOUND_THEMES.index(state.sound_theme))
            sync_accessibility(
                self.dynamic["sound_theme_control"],
                self._l(f"settings.controls.sound.{state.sound_theme}"),
                label=self._l("settings.accessibility.sound.label"))
            # Nothing to preview when the answer is silence.
            self.dynamic["sound_preview_button"].setEnabled_(
                state.sound_theme != "silent")
            remove_repeats = settings.remove_repeats
            self.dynamic["remove_repeats_toggle"].setState_(
                NSControlStateValueOn if remove_repeats
                else NSControlStateValueOff)
            set_accessible_text(
                self.dynamic["remove_repeats_detail"],
                self._l("settings.controls.remove_repeats.on"
                        if remove_repeats
                        else "settings.controls.remove_repeats.off"),
                label=self._l("settings.accessibility.remove_repeats.label"))
            sync_accessibility(
                self.dynamic["remove_repeats_toggle"],
                self._l("settings.state.enabled") if remove_repeats
                else self._l("settings.state.disabled"),
                label=self._l("settings.accessibility.remove_repeats.label"))
            self_correction = settings.self_correction
            self.dynamic["self_correction_toggle"].setState_(
                NSControlStateValueOn if self_correction
                else NSControlStateValueOff)
            set_accessible_text(
                self.dynamic["self_correction_detail"],
                self._l("settings.controls.self_correction.on"
                        if self_correction
                        else "settings.controls.self_correction.off"),
                label=self._l("settings.accessibility.self_correction.label"))
            sync_accessibility(
                self.dynamic["self_correction_toggle"],
                self._l("settings.state.enabled") if self_correction
                else self._l("settings.state.disabled"),
                label=self._l("settings.accessibility.self_correction.label"))
            # The bound keys, as key caps. No undo key bound: no cap, and
            # the caption says so.
            self.dynamic["hotkey_keycap"].set_key(state.hotkey_label)
            undo_keycap = self.dynamic["undo_hotkey_keycap"]
            undo_keycap.set_key(state.undo_hotkey_label or "")
            undo_keycap.setHidden_(not state.undo_hotkey_label)
            self._arrange_page("Controls")

        @objc.python_method
        def _render_settings_privacy(self, state: GUIState) -> None:
            """Settings, Privacy: what is kept, and for how long."""
            self.dynamic["recent_dictations_toggle"].setState_(
                NSControlStateValueOn if state.recent_dictations
                else NSControlStateValueOff)
            set_accessible_text(
                self.dynamic["recent_dictations_detail"],
                self._l("settings.privacy.recent.on",
                        count=RECENT_DICTATION_LIMIT)
                if state.recent_dictations
                else self._l("settings.privacy.recent.detail"),
                label=self._l("settings.accessibility.recent.label"))
            sync_accessibility(
                self.dynamic["recent_dictations_toggle"],
                self._l("settings.state.enabled") if state.recent_dictations
                else self._l("settings.state.disabled"),
                label=self._l("settings.accessibility.recent.label"))
            self.dynamic["recent_dictations_browse"].setEnabled_(
                state.recent_dictations)
            self.dynamic["flight_toggle"].setState_(
                NSControlStateValueOn if state.flight_recorder
                else NSControlStateValueOff)
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
            voice_object_status = voice_inbox_status_display(
                state.voice_object_inbox_status,
                state.voice_object_inbox_count,
                locale=self.view_model.locale)
            self.dynamic["voice_object_commands_status"].setStringValue_(
                voice_object_status)
            sync_accessibility(
                self.dynamic["voice_object_commands_toggle"],
                voice_object_status,
                label=self._l("settings.accessibility.voice_objects.label"),
            )
            self._arrange_page("Privacy")

        @objc.python_method
        def _render_advanced(self, state: GUIState) -> None:
            """Advanced: models, selective re-listen and diagnostics."""
            locale = self.view_model.locale
            for index, ((row, name, detail, status_label), status_view) in \
                    enumerate(zip(self.dynamic["model_rows"],
                                  self.dynamic["model_status_views"])):
                if index < len(state.models):
                    model = state.models[index]
                    phrase, tone = model_status_display(model, locale=locale)
                    name.setStringValue_(model.name)
                    detail.setStringValue_(model_detail_display(model))
                    self._show_status(status_view, phrase, tone)
                    sync_accessibility(
                        name, model.name,
                        label=self._l("models.accessibility.name"))
                    sync_accessibility(
                        detail, str(detail.stringValue()),
                        label=self._l(
                            "models.accessibility.detail", name=model.name))
                    sync_accessibility(
                        status_label, phrase,
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
                    self._show_status(
                        status_view, self._l("models.unknown"), "neutral")
                    sync_accessibility(
                        status_label, self._l("models.unknown"),
                        label=self._l(
                            "models.accessibility.status",
                            name=self._l("models.waiting")))
            # The runtime's advisory receipt keeps its own wording for the
            # tests and the support snapshot; the page says what it found.
            set_accessible_text(
                self.dynamic["model_wallet_advisory"],
                model_checks_note(state.model_wallet_advisory, locale=locale),
                label=self._l("pages.models.advisory.label"),
            )
            relisten_status = relisten_status_display(
                state.selective_relisten_status, locale=locale)
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
            speech_runtime = state.new_speech_runtime
            self.dynamic["new_speech_runtime_toggle"].setState_(
                NSControlStateValueOn if speech_runtime
                else NSControlStateValueOff)
            set_accessible_text(
                self.dynamic["new_speech_runtime_detail"],
                speech_runtime_caption(
                    speech_runtime, state.new_speech_runtime_last,
                    preparing=state.new_speech_runtime_preparing,
                    locale=locale),
                label=self._l("models.accessibility.speech_runtime"))
            sync_accessibility(
                self.dynamic["new_speech_runtime_toggle"],
                self._l("settings.state.enabled") if speech_runtime
                else self._l("settings.state.disabled"),
                label=self._l("models.accessibility.speech_runtime"))
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
            for key, value in (
                    ("diag_service", state.service_status),
                    ("diag_microphone", state.microphone_status),
                    ("diag_accessibility", state.accessibility_status)):
                self._show_status(
                    self.dynamic["diag_status_views"][key], value,
                    system_status_tone(value))
            # The row goes with its button: nothing to open, nothing shown.
            recovery = self.view_model.permission_recovery_needed()
            self.dynamic["open_system_settings_row"].setHidden_(not recovery)
            self.dynamic["open_system_settings_button"].setHidden_(
                not recovery)
            regression = self._l(
                plural_key("diagnostics.regression.cases",
                           state.regression_cases),
                count=state.regression_cases)
            if state.regression_quarantined:
                regression += self._l(
                    "pages.diagnostics.regression.held",
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
            for key, label_key in (
                ("diag_service", "diagnostics.accessibility.service"),
                ("diag_microphone", "diagnostics.accessibility.microphone"),
                ("diag_accessibility", "diagnostics.accessibility.permission"),
                ("diag_regression", "diagnostics.accessibility.regression"),
                ("diag_motion", "diagnostics.accessibility.motion"),
                ("diag_version", "diagnostics.accessibility.build"),
                ("verification", "diagnostics.accessibility.verification"),
                ("diag_guidance", "diagnostics.accessibility.guidance"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=self._l(label_key),
                )
            for view in self.dynamic["diag_status_views"].values():
                view.invalidateIntrinsicContentSize()
            self._arrange_page("Advanced")

        @objc.python_method
        def _render_notices(self, state: GUIState) -> None:
            """The notice line under every page."""
            dark = getattr(self, "_dark", False)
            self.dynamic["notice"].setStringValue_(state.notice)
            sync_accessibility(
                self.dynamic["notice"],
                str(self.dynamic["notice"].stringValue()),
                label=self._l("diagnostics.accessibility.notice"),
            )
            notice_palette = palette_for_appearance(dark)
            notice_color = (
                _error_text_color(dark)
                if state.notice_level == "error"
                else _brand_text_color(dark)
                if state.notice_level == "success"
                else _theme_color(notice_palette.ink_soft))
            self.dynamic["notice"].setTextColor_(notice_color)
            self._animate_notice(state)

        @objc.python_method
        def _render_finish(self, state: GUIState) -> None:
            """What depends on every surface having rendered."""
            self._sync_face_clock()
            self._configure_key_view_loop(state)

        @objc.python_method
        def _text_editor(self, frame: Any, value: str, *,
                         label: str, help_text: str,
                         text_view: Any = None,
                         bordered: bool = True) -> tuple[Any, Any]:
            """A multi-line field in its scroll view. In an alert it has
            the system's line border and fill; a sheet asks for neither,
            because there the group it fills is its frame."""
            scroll = NSScrollView.alloc().initWithFrame_(frame)
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(1 if bordered else NSNoBorder)
            editor = (text_view or NSTextView).alloc().initWithFrame_(
                NSMakeRect(0, 0, frame.size.width, frame.size.height))
            if not bordered:
                scroll.setDrawsBackground_(False)
                editor.setDrawsBackground_(False)
            # Editors hold exact strings (vocabulary the spell checker does
            # not know, code and shell snippets), so nothing rewrites them.
            editor.setRichText_(False)
            editor.setAutomaticQuoteSubstitutionEnabled_(False)
            editor.setAutomaticDashSubstitutionEnabled_(False)
            editor.setAutomaticTextReplacementEnabled_(False)
            editor.setAutomaticSpellingCorrectionEnabled_(False)
            editor.setAllowsUndo_(True)
            editor.setString_(value)
            _accessible(editor, label, help_text)
            scroll.setDocumentView_(editor)
            return scroll, editor

        @objc.python_method
        def _install_main_menu(self) -> None:
            """Give the window the standard Edit, View and Window keys.

            The runtime is an accessory app, so this menu is never drawn, but
            AppKit still routes Cmd-W, Cmd-M and the edit shortcuts through
            it; without one they do nothing in the window and its editors.
            View carries the five pages on Cmd-1 to Cmd-5, and Cmd-D for
            Advanced once more, unseen. Its items have no target: the action
            travels the responder chain to the window's delegate, which is
            this controller, so they are live exactly while the window is
            the key window.
            """
            app = NSApplication.sharedApplication()
            if app.mainMenu() is not None:
                return
            main = NSMenu.alloc().initWithTitle_(APP_NAME)
            command = NSEventModifierFlagCommand
            for title_key, entries in (
                ("menu.edit", (
                    ("menu.edit.undo", "undo:", "z", command),
                    ("menu.edit.redo", "redo:", "z",
                     command | NSEventModifierFlagShift),
                    ("menu.edit.cut", "cut:", "x", command),
                    ("menu.edit.copy", "copy:", "c", command),
                    ("menu.edit.paste", "paste:", "v", command),
                    ("menu.edit.select_all", "selectAll:", "a", command),
                )),
                ("menu.view", (
                    *((row.label_key, "selectSidebarRow:", str(index + 1),
                       command, index)
                      for index, row in enumerate(SIDEBAR_ROWS)),
                    # Command-D is on the Advanced row itself, which answers
                    # first. This hidden twin keeps the shortcut alive while
                    # setup mode has the sidebar, and the row, put away.
                    ("nav.advanced", "selectSidebarRow:", "d", command,
                     len(SIDEBAR_ROWS) - 1, "hidden"),
                )),
                ("menu.window", (
                    ("menu.window.close", "performClose:", "w", command),
                    ("menu.window.minimize", "performMiniaturize:", "m",
                     command),
                )),
            ):
                submenu = NSMenu.alloc().initWithTitle_(self._l(title_key))
                for item_key, action, key, mask, *extra in entries:
                    item = NSMenuItem.alloc() \
                        .initWithTitle_action_keyEquivalent_(
                            self._l(item_key), action, key)
                    item.setKeyEquivalentModifierMask_(mask)
                    if extra:
                        item.setTag_(extra[0])
                    if "hidden" in extra:
                        item.setHidden_(True)
                        item.setAllowsKeyEquivalentWhenHidden_(True)
                    submenu.addItem_(item)
                holder = NSMenuItem.alloc() \
                    .initWithTitle_action_keyEquivalent_(
                        self._l(title_key), None, "")
                holder.setSubmenu_(submenu)
                main.addItem_(holder)
            app.setMainMenu_(main)

        @objc.python_method
        def _run_modal(self, dialog: Any) -> int:
            """Run one dialog; the native smoke answers through a responder.

            Every dialog the window opens comes through here: an editor
            sheet, which runs itself on the window, or a system alert,
            which is given the chosen face as its icon on the way.
            """
            responder = getattr(self, "modal_responder", None)
            if isinstance(dialog, EditorSheet):
                return dialog.run(self.window, responder)
            alert = dialog
            self._show_the_face(alert)
            escape = alert_escape_handler(alert)
            monitor = None
            if escape is not None:
                try:
                    monitor = (
                        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                            NSEventMaskKeyDown, escape))
                except Exception:
                    monitor = None
            try:
                if responder is not None:
                    return int(responder(alert))
                return int(alert.runModal())
            finally:
                if monitor is not None:
                    try:
                        NSEvent.removeMonitor_(monitor)
                    except Exception:
                        pass

        @objc.python_method
        def _show_the_face(self, alert: Any) -> None:
            """A system alert's icon is the chosen face on its chip.

            Left alone, an alert shows the application's icon, and this
            process has none, so AppKit draws a generic folder.
            """
            face = self.view_model.state.face
            if face not in self._face_icons:
                self._face_icons[face] = face_chip_icon(
                    self._face_image(face, talk=False), face)
            icon = self._face_icons[face]
            try:
                alert.setIcon_(icon)
            except Exception:
                pass

        @objc.python_method
        def _confirm(self, title: str, message: str,
                     primary: str, *, destructive: bool = True) -> bool:
            alert = NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.addButtonWithTitle_(primary)
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))
            if destructive:
                self._default_to_safe(alert, destructive=0, safe=1)
            else:
                # AppKit gives Escape only to a button titled exactly
                # "Cancel" in English, so it is set for every locale.
                buttons = alert.buttons()
                buttons[0].setKeyEquivalent_("\r")
                buttons[1].setKeyEquivalent_("\x1b")
            return self._run_modal(alert) == 1000

        @objc.python_method
        def _default_to_safe(self, alert: Any, *, destructive: int,
                             safe: int) -> None:
            """Return and Escape pick the safe button; the other needs a click.

            The safe button holds Return, which also draws it as the default.
            Escape reaches it through ``alert_escape_handler`` in _run_modal.
            """
            buttons = alert.buttons()
            buttons[destructive].setKeyEquivalent_("")
            try:
                buttons[destructive].setHasDestructiveAction_(True)
            except Exception:
                pass
            buttons[safe].setKeyEquivalent_("\r")

        # ---- Editor sheets: the parts every editor is filled from. ----

        @objc.python_method
        def _new_sheet(self) -> Any:
            """An empty editor sheet, in the appearance the window is in.

            The header is here once: the chosen face on its chip, the
            title and the line under it. ``_sheet_fill`` gives it a body
            and a footer, as often as the editor needs.

            One editor is open at a time: every button on a sheet answers
            through ``sheetChose_``, which knows the open sheet and no
            other, so a second one is refused, not opened over the first.
            """
            if self._sheet is not None:
                raise RuntimeError("an editor sheet is already open")
            sheet = EditorSheet()
            window = SheetWindow.alloc() \
                .initWithContentRect_styleMask_backing_defer_(
                    NSMakeRect(0, 0, SHEET_WIDTH, 200.0),
                    NSWindowStyleMaskTitled, NSBackingStoreBuffered, False)
            window.setReleasedWhenClosed_(False)
            window.setTitle_(APP_NAME)
            # None unless something chose an appearance for the window;
            # the sheet then follows the system as the window does.
            window.setAppearance_(self.window.appearance())
            # Tab runs in the order the sheet is laid out, not in the
            # order AppKit would work out from the frames.
            window.setAutorecalculatesKeyViewLoop_(False)
            content = SheetContentView.alloc().initWithFrame_(
                NSMakeRect(0, 0, SHEET_WIDTH, 200.0))
            content.setWantsLayer_(True)
            window.setContentView_(content)

            face = self.view_model.state.face
            chip = FaceChipView.alloc().initWithFrame_(NSMakeRect(
                SHEET_INSET, SHEET_INSET, SHEET_FACE_SIZE, SHEET_FACE_SIZE))
            chip.configure(self._face_image(face, talk=False), face)
            title = _label("", NSMakeRect(0, 0, 240, 22), wrap=True)
            title.setFont_(_role_font("page_title"))
            try:
                # VoiceOver reads the sheet's name as a heading, as it
                # does a page's. The role is looked up, not imported.
                import AppKit as _appkit
                heading = getattr(
                    _appkit, "NSAccessibilityHeadingRole", "AXHeading")
                title.setAccessibilityRole_(heading)
                title.cell().setAccessibilityRole_(heading)
            except Exception:
                pass
            message = _label("", NSMakeRect(0, 0, 240, 16), wrap=True)
            scroll = PageScrollView.alloc().initWithFrame_(NSMakeRect(
                SHEET_INSET, 0, SHEET_WIDTH - 2.0 * SHEET_INSET, 100.0))
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(False)
            scroll.setAutohidesScrollers_(True)
            scroll.setBorderType_(NSNoBorder)
            scroll.setDrawsBackground_(False)
            try:
                scroll.setAutomaticallyAdjustsContentInsets_(False)
            except Exception:
                pass
            scroll.contentView().setDrawsBackground_(False)
            # A hairline over the footer, only while the body scrolls
            # under it.
            rule = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, SHEET_WIDTH, 1.0))
            rule.setWantsLayer_(True)
            rule.setHidden_(True)
            for view in (chip, title, message, scroll, rule):
                content.addSubview_(view)

            sheet.window, sheet.content, sheet.chip = window, content, chip
            sheet.title, sheet.message = title, message
            sheet.scroll, sheet.rule = scroll, rule
            window.leave = sheet.leave
            content.repaint = lambda: self._paint_sheet(sheet)
            self._sheet = sheet
            return sheet

        @objc.python_method
        def _end_sheet(self, sheet: Any) -> None:
            sheet.end()
            if self._sheet is sheet:
                self._sheet = None

        @objc.python_method
        def _run_sheet(self, sheet: Any) -> tuple[str, Any]:
            """Run the sheet until a button answers it; return the answer,
            ``(action, item)``."""
            self._run_modal(sheet)
            return sheet.choice

        def sheetChose_(self, sender: Any) -> None:
            """A button on the open sheet was pressed."""
            sheet = self._sheet
            if sheet is not None:
                sheet.choose(int(sender.tag()))

        @objc.python_method
        def _paint_sheet(self, sheet: Any) -> None:
            """The sheet's colours for the appearance it is in: the
            window's field colour behind it, and its labels by role."""
            dark = _uses_dark_appearance(sheet.window)
            palette = palette_for_appearance(dark)
            if sheet.content.layer() is not None:
                sheet.content.layer().setBackgroundColor_(
                    _theme_color(palette.bg).CGColor())
            if sheet.rule.layer() is not None:
                sheet.rule.layer().setBackgroundColor_(
                    _token_color("hairline", dark).CGColor())
            self._paint_labels(sheet.themed, palette)
            for label in sheet.themed.get("error", ()):
                label.setTextColor_(_error_text_color(dark))
            for label in sheet.themed.get("success", ()):
                label.setTextColor_(_brand_text_color(dark))
            for icon in sheet.themed.get("symbol", ()):
                try:
                    icon.setContentTintColor_(_srgb_color(palette.ink_soft))
                except Exception:
                    pass

        @objc.python_method
        def _sheet_fill(self, sheet: Any,
                        build: Callable[[], Mapping[str, Any]]) -> None:
            """Empty the sheet, then lay out the page ``build`` describes.

            ``build`` makes the page's rows and buttons and returns what
            ``_sheet_layout`` takes. Labels it makes are themed with the
            sheet, not with the window.
            """
            # Where the keyboard is, if it is on one of the page's
            # controls: a list that is filled again (a row forgotten, a
            # form closed) gets it back in the same place, not nowhere.
            page = str(sheet.title.stringValue())
            responder = sheet.window.firstResponder()
            if responder in sheet.key_views:
                sheet.focus_index[page] = sheet.key_views.index(responder)
            else:
                sheet.focus_index.pop(page, None)
            for button in sheet.footer:
                button.removeFromSuperview()
            sheet.footer, sheet.key_views = [], []
            sheet.controls, sheet.buttons, sheet.items = {}, {}, {}
            sheet.themed = {"ink": [sheet.title],
                            "ink_soft": [sheet.message]}
            self._theme_sink = sheet.themed
            try:
                self._sheet_layout(sheet, **build())
            finally:
                self._theme_sink = None
            self._paint_sheet(sheet)

        @objc.python_method
        def _sheet_layout(self, sheet: Any, *, title: str, message: str,
                          sections: Sequence[tuple[Any, Any, Any]],
                          buttons: Sequence[Any],
                          leading: Sequence[Any] = (),
                          focus: Any = None) -> None:
            """Lay the sheet out from the top down and size it.

            ``sections`` are ``(header, rows, notes)``, placed by the
            pages' own document. ``buttons`` are the footer's, in reading
            order, at its trailing end; ``leading`` sit at its other end.
            The sheet is as tall as all of it up to ``SHEET_MAX_HEIGHT``;
            past that the body scrolls between the header and the footer.
            """
            # ---- Header: the face on its chip, the title, one line.
            sheet.title.setStringValue_(title)
            sheet.message.setStringValue_(message)
            sheet.message.setHidden_(not message)
            text_x = SHEET_INSET + SHEET_FACE_SIZE + SHEET_HEADER_GAP
            text_width = SHEET_WIDTH - text_x - SHEET_INSET
            title_height = _text_height(sheet.title, text_width)
            message_height = (_text_height(sheet.message, text_width)
                              if message else 0.0)
            block = title_height + (
                SHEET_TITLE_GAP + message_height if message else 0.0)
            header_height = max(SHEET_FACE_SIZE, block)
            y = SHEET_INSET + (header_height - block) / 2.0
            sheet.title.setFrame_(NSMakeRect(
                text_x, y, text_width, title_height))
            sheet.message.setFrame_(NSMakeRect(
                text_x, y + title_height + SHEET_TITLE_GAP, text_width,
                message_height))
            body_top = SHEET_INSET + header_height + SHEET_BODY_GAP

            # ---- Footer: system buttons, measured before the body so
            # the body knows what room is left.
            sheet.footer = [*leading, *buttons]
            sizes = {}
            for button in sheet.footer:
                left, top, right, bottom = sheet_alignment(button)
                sizes[button] = (
                    max(right - left, SHEET_BUTTON_MIN_WIDTH)
                    if button in buttons else right - left, bottom - top)
            button_height = max(
                (height for _width, height in sizes.values()), default=0.0)
            footer_height = SHEET_FOOTER_GAP + button_height + SHEET_INSET

            # ---- Body: the pages' own document in a scroll view.
            body_width = SHEET_WIDTH - 2.0 * SHEET_INSET
            sheet.scroll.setFrame_(NSMakeRect(
                SHEET_INSET, body_top, body_width, 100.0))
            document = GroupedPageView.alloc().initWithFrame_(
                NSMakeRect(0, 0, body_width, 100.0))
            document.foot = SHEET_BODY_FOOT
            document.setAutoresizingMask_(NSViewWidthSizable)
            key_views: list[Any] = []
            for header, rows, notes in sections:
                document.add_section(
                    self._section_header(header) if header else None,
                    self._settings_group(rows), notes)
                for row in rows:
                    key_views.extend(row.key_views)
            sheet.scroll.setDocumentView_(document)
            sheet.document = document
            content_height = document.arrange(force=True)
            body_height = min(
                content_height, SHEET_MAX_HEIGHT - body_top - footer_height)
            sheet.scroll.setFrame_(NSMakeRect(
                SHEET_INSET, body_top, body_width, body_height))
            document.arrange(force=True)
            document.scrollPoint_((0.0, 0.0))
            self._tip_what_is_cut(document)
            body_foot = body_top + body_height
            sheet.rule.setFrame_(NSMakeRect(0, body_foot, SHEET_WIDTH, 1.0))
            sheet.rule.setHidden_(content_height <= body_height + 0.5)

            # ---- Footer: trailing buttons from the trailing edge back,
            # leading ones from the leading edge.
            top = body_foot + SHEET_FOOTER_GAP
            x = SHEET_WIDTH - SHEET_INSET
            for button in reversed(list(buttons)):
                width, height = sizes[button]
                x -= width
                place_in_sheet(
                    button, x, top + (button_height - height) / 2.0,
                    width, height)
                x -= ROW_CONTROL_GAP
            x = SHEET_INSET
            for button in leading:
                width, height = sizes[button]
                place_in_sheet(
                    button, x, top + (button_height - height) / 2.0,
                    width, height)
                x += width + ROW_CONTROL_GAP
            # The button that holds Return becomes the window's default
            # button, and is drawn as one, when it joins the window.
            for button in sheet.footer:
                sheet.content.addSubview_(button)

            # ---- Size. Since macOS 11 a sheet is a card in the middle
            # of its window, and AppKit puts it back there whenever its
            # frame changes. So an open sheet grows and shrinks about its
            # middle: it is resized where AppKit is about to put it, with
            # no jump in between.
            height = top + button_height + SHEET_INSET
            window = sheet.window
            # VoiceOver names the sheet by what it is, not by the app.
            window.setTitle_(title)
            if sheet.parent is None:
                window.setContentSize_(NSMakeSize(SHEET_WIDTH, height))
            else:
                old = window.frame()
                new = window.frameRectForContentRect_(
                    NSMakeRect(0, 0, SHEET_WIDTH, height))
                window.setFrame_display_(NSMakeRect(
                    old.origin.x + (old.size.width - new.size.width) / 2.0,
                    old.origin.y + (old.size.height - new.size.height) / 2.0,
                    new.size.width, new.size.height), True)

            # ---- Keyboard: Tab runs top to bottom, then the footer in
            # reading order; the first field that takes typing has focus.
            # A page with no field puts the keyboard back where it was
            # when the page was last up, on the control now in that place.
            key_views.extend(sheet.footer)
            for view, following in zip(
                    key_views, key_views[1:] + key_views[:1]):
                view.setNextKeyView_(following)
            sheet.key_views = key_views
            window.setInitialFirstResponder_(
                focus or (key_views[0] if key_views else None))
            was_at = sheet.focus_index.get(title)
            if focus is None and was_at is not None and key_views:
                focus = key_views[min(was_at, len(key_views) - 1)]
            window.makeFirstResponder_(focus)

        @objc.python_method
        def _tip_what_is_cut(self, document: Any) -> None:
            """A row's title or caption that is cut to one line shows all
            of its words as its tooltip, ahead of any help it already has
            (an app's row has its bundle identifier there)."""
            for _header, group, _notes, _trailing in document.sections:
                for row in group.rows:
                    for label in (row.title, row.caption):
                        if label is None or bool(label.cell().wraps()):
                            continue
                        if float(label.cell().cellSize().width) \
                                <= float(label.frame().size.width) + 1.0:
                            continue
                        words, tip = str(label.stringValue()), label.toolTip()
                        label.setToolTip_(
                            f"{words}\n{tip}" if tip else words)

        @objc.python_method
        def _sheet_button(self, sheet: Any, name: str, title: str, *,
                          label: str = "", help_text: str = "",
                          item: Any = None, key: str = "") -> Any:
            """A system button on the sheet that answers it when pressed.

            ``name`` is how the button is reached; what comes before its
            first colon is the action the editor is told of, and ``item``
            is what it acts on. ``key`` is Return for the default button
            and Escape for Cancel.
            """
            if name in sheet.buttons:
                name = f"{name}#{len(sheet.buttons)}"
            button = _button(
                title, NSMakeRect(0, 0, 60, 28), self, "sheetChose:",
                label=label, help_text=help_text)
            button.setTag_(sheet.register(name, button, item))
            if key:
                button.setKeyEquivalent_(key)
            return button

        @objc.python_method
        def _sheet_footer(self, sheet: Any, *, save: bool) -> list[Any]:
            """The footer's trailing buttons: Cancel and Save for a form,
            one Done for a list that applies as it goes. The primary one
            holds Return; Cancel holds Escape, and a lone Done is given
            Escape by the sheet's window."""
            if not save:
                return [self._sheet_button(
                    sheet, "done", self._l("settings.action.done"),
                    key="\r")]
            return [
                self._sheet_button(
                    sheet, "cancel", self._l("settings.action.cancel"),
                    key="\x1b"),
                self._sheet_button(
                    sheet, "save", self._l("settings.action.save"),
                    key="\r"),
            ]

        @objc.python_method
        def _sheet_row(self, title: str | None, *,
                       caption: str | None = None,
                       accessories: Sequence[Any] = (),
                       leading: Any = None, wrap: bool = True,
                       keys: Sequence[Any] | None = None
                       ) -> tuple[Any, Any, Any]:
            """A row of the pages' own make, with the controls Tab stops
            on (its accessories, unless ``keys`` says otherwise)."""
            row, title_label, caption_label = self._settings_row(
                title, caption=caption, accessories=accessories,
                leading=leading, wrap=wrap)
            row.key_views = list(accessories if keys is None else keys)
            return row, title_label, caption_label

        @objc.python_method
        def _sheet_empty_row(self, text: str) -> Any:
            """The one row of a list with nothing in it: a sentence."""
            label = self._soft(
                text, NSMakeRect(0, 0, 240, 16), size=13, wrap=True,
                alignment=int(NSTextAlignmentCenter))
            row = SettingsRowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, SHEET_WIDTH, ROW_MIN_HEIGHT))
            row.addSubview_(label)

            def height(width: float) -> float:
                return 2.0 * SHEET_EMPTY_PADDING + _text_height(
                    label, width - 2.0 * ROW_INSET)

            def layout(width: float, tall: float) -> None:
                label.setFrame_(NSMakeRect(
                    ROW_INSET, SHEET_EMPTY_PADDING,
                    width - 2.0 * ROW_INSET,
                    tall - 2.0 * SHEET_EMPTY_PADDING))

            row.configure(custom=(height, layout))
            row.key_views = []
            return row

        @objc.python_method
        def _sheet_editor_row(self, sheet: Any, name: str, value: str, *,
                              label: str, help_text: str,
                              height: float = SHEET_EDITOR_HEIGHT) -> Any:
            """A row that is one multi-line field and nothing else.

            The field has no border and no fill of its own: the group it
            is in is its frame, in the group's colour, and the section
            header over the group is its name. Its text starts where a
            row's title starts. It is the same exact editor every dialog
            uses; here it also hands Tab on.
            """
            scroll, editor = self._text_editor(
                NSMakeRect(0, 0, SHEET_WIDTH, height), value,
                label=label, help_text=help_text, text_view=SheetTextView,
                bordered=False)
            scroll.setAutohidesScrollers_(True)
            editor.setFont_(NSFont.systemFontOfSize_(13.0))
            editor.setTextContainerInset_(NSMakeSize(
                ROW_INSET - 1.0 - SHEET_EDITOR_TEXT_PADDING, 0.0))
            editor.textContainer().setLineFragmentPadding_(
                SHEET_EDITOR_TEXT_PADDING)
            # The field wraps to its own width and grows down as it is
            # typed into; its scroll view does the scrolling.
            editor.setMinSize_(NSMakeSize(0.0, height))
            editor.setMaxSize_(NSMakeSize(1.0e7, 1.0e7))
            editor.setVerticallyResizable_(True)
            editor.setHorizontallyResizable_(False)
            editor.setAutoresizingMask_(NSViewWidthSizable)
            editor.textContainer().setWidthTracksTextView_(True)
            # Typed in the pages' ink, on the group's own colour.
            self._register("ink", editor)
            row = SettingsRowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, SHEET_WIDTH, height))
            row.addSubview_(scroll)

            def tall(_width: float) -> float:
                return height + 2.0 * ROW_PADDING_Y

            def layout(width: float, _tall: float) -> None:
                # One point inside the group's edge at each side, and
                # clear of its corners above and below.
                scroll.setFrame_(NSMakeRect(
                    1.0, ROW_PADDING_Y, width - 2.0, height))

            row.configure(custom=(tall, layout))
            row.key_views = [editor]
            sheet.controls[name] = editor
            return row

        @objc.python_method
        def _sheet_field_row(self, sheet: Any, name: str, value: str, *,
                             label: str, help_text: str,
                             editable: bool = True) -> Any:
            """A row that is one single-line field and nothing else, made
            the way the multi-line one is: no border, the group for its
            frame, the section header for its name. One that cannot be
            edited shows its words in the secondary ink."""
            field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(0, 0, 240, 17))
            field.setStringValue_(value)
            field.setFont_(_font(13))
            field.setBezeled_(False)
            field.setBordered_(False)
            field.setDrawsBackground_(False)
            field.setEditable_(editable)
            field.setSelectable_(True)
            # Like the multi-line field, it shows the keyboard is in it
            # by its insertion point; a ring round one line of text in
            # the middle of a group would be a box in a box again.
            field.setFocusRingType_(1)
            field.setUsesSingleLineMode_(True)
            field.setLineBreakMode_(NSLineBreakByTruncatingTail)
            if editable:
                field.cell().setScrollable_(True)
            self._register("ink" if editable else "ink_soft", field)
            _accessible(field, label, help_text)
            row = SettingsRowView.alloc().initWithFrame_(
                NSMakeRect(0, 0, SHEET_WIDTH, ROW_MIN_HEIGHT))
            row.addSubview_(field)

            def tall(_width: float) -> float:
                return ROW_MIN_HEIGHT

            def layout(width: float, height: float) -> None:
                line = float(math.ceil(field.cell().cellSize().height))
                field.setFrame_(NSMakeRect(
                    ROW_INSET, (height - line) / 2.0,
                    width - 2.0 * ROW_INSET, line))

            row.configure(custom=(tall, layout))
            row.key_views = [field] if editable else []
            sheet.controls[name] = field
            return row

        @objc.python_method
        def _sheet_notice(self, sheet: Any, text: str,
                          level: str = "error") -> Any:
            """What the last thing the sheet did came to, as a line under
            a group: a refusal in the error colour, a result in the brand
            ink. With nothing to say it takes no room."""
            note = _label(text, NSMakeRect(0, 0, 240, 15), size=12, wrap=True)
            self._register(
                level if level in {"error", "success"} else "ink_soft", note)
            if text:
                sync_accessibility(note, text, label=self._l(
                    "sheets.notice.error.label" if level == "error"
                    else "sheets.notice.result.label"))
                self._announce(sheet, text)
            sheet.controls["notice"] = note
            return note

        @objc.python_method
        def _announce(self, sheet: Any, text: str) -> None:
            """Have VoiceOver say what the sheet now says, at once: the
            line appears under a group, where focus is not."""
            try:
                import AppKit as _appkit
                _appkit.NSAccessibilityPostNotificationWithUserInfo(
                    sheet.window,
                    _appkit.NSAccessibilityAnnouncementRequestedNotification,
                    {_appkit.NSAccessibilityAnnouncementKey: text,
                     _appkit.NSAccessibilityPriorityKey:
                         _appkit.NSAccessibilityPriorityHigh})
            except Exception:
                pass

        # ---- The five Personalize editors. Each is a straight line: fill
        # the sheet, run it, act on the answer, and fill it again while
        # the sheet stays open.

        @objc.python_method
        def _tones_page(self, sheet: Any, tones: Sequence[AppToneSetting],
                        chosen: Mapping[str, str],
                        notice: str) -> Mapping[str, Any]:
            """Every app with its own icon and its tone as a pop-up."""
            rows = []
            sheet.listed = []
            for index, item in enumerate(tones):
                popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                    NSMakeRect(0, 0, 130, 28), False)
                popup.addItemsWithTitles_([
                    self._l(f"settings.tone.{tone}")
                    for tone in TONE_CHOICES])
                popup.selectItemAtIndex_(TONE_CHOICES.index(
                    chosen.get(item.bundle) or tone_for_app_index(
                        tones, index, locale=self.view_model.locale)))
                _accessible(
                    popup,
                    self._l("sheets.tones.choice.label", app=item.name),
                    self._l("settings.dialog.tone.choice.help"))
                image, found = app_icon_image(item.bundle)
                icon = NSImageView.alloc().initWithFrame_(NSMakeRect(
                    0, 0, SHEET_APP_ICON_SIZE, SHEET_APP_ICON_SIZE))
                icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                icon.setEditable_(False)
                icon.setImage_(image)
                # The row's title names the app; the icon is a picture.
                _silence_image(icon)
                if not found:
                    self._register("symbol", icon)
                row, title, _caption = self._sheet_row(
                    item.name, accessories=(popup,), leading=icon,
                    wrap=False)
                # The bundle identifier is not shown; it is the row's help.
                title.setToolTip_(item.bundle)
                icon.setToolTip_(item.bundle)
                sheet.controls[f"tone:{item.bundle}"] = popup
                sheet.listed.append((item, popup))
                rows.append(row)
            if not rows:
                rows = [self._sheet_empty_row(
                    self._l("settings.empty.tones"))]
            return {
                "title": self._l("settings.personalize.tones"),
                "message": self._l("sheets.tones.message"),
                "sections": [(None, rows,
                              [self._sheet_notice(sheet, notice)])],
                "buttons": self._sheet_footer(sheet, save=True),
            }

        def editTone_(self, _sender: Any) -> None:
            tones = self.view_model.state.settings.app_tones
            if not tones:
                return
            sheet = self._new_sheet()
            chosen: dict[str, str] = {}
            notice = ""
            try:
                while True:
                    self._sheet_fill(sheet, lambda: self._tones_page(
                        sheet, tones, chosen, notice))
                    action, _item = self._run_sheet(sheet)
                    if action != "save":
                        return
                    chosen = {
                        item.bundle: TONE_CHOICES[
                            popup.indexOfSelectedItem()]
                        for item, popup in sheet.listed}
                    # Save applies every tone that changed, one app at a
                    # time, and stops at the first that is refused.
                    notice, saved = "", []
                    for index, item in enumerate(tones):
                        if chosen[item.bundle] == tone_for_app_index(
                                tones, index, locale=self.view_model.locale):
                            continue
                        try:
                            state = self.view_model.set_app_tone(
                                item.bundle, chosen[item.bundle])
                        except ValueError as refusal:
                            notice = str(refusal)
                        else:
                            if state.notice_level == "error":
                                notice = state.notice
                        if notice:
                            # Which app it was, and what Cancel can no
                            # longer take back: the tones saved before it.
                            notice = self._l(
                                "sheets.tones.refused", app=item.name,
                                reason=notice)
                            if saved:
                                notice += " " + self._l(
                                    "sheets.tones.saved_before",
                                    apps=", ".join(saved))
                            break
                        saved.append(item.name)
                    self.render()
                    if not notice:
                        return
                    # What was saved before the refusal stays saved; the
                    # sheet keeps every choice and says what went wrong.
                    tones = self.view_model.state.settings.app_tones
            finally:
                self._end_sheet(sheet)

        @objc.python_method
        def _snippets_page(self, sheet: Any,
                           snippets: Sequence[SnippetSetting],
                           notice: str) -> Mapping[str, Any]:
            """The snippets as rows, each with Edit and Delete."""
            rows = []
            for snippet in snippets:
                edit = self._sheet_button(
                    sheet, f"edit:{snippet.name}",
                    self._l("settings.action.edit"),
                    label=self._l(
                        "sheets.snippets.edit.label", name=snippet.name),
                    item=snippet)
                delete = self._sheet_button(
                    sheet, f"delete:{snippet.name}",
                    self._l("settings.action.delete"),
                    label=self._l(
                        "sheets.snippets.delete.label", name=snippet.name),
                    item=snippet)
                first_line = next(
                    (line.strip() for line in snippet.text.splitlines()
                     if line.strip()), "")
                row, title, _caption = self._sheet_row(
                    snippet.name, caption=first_line,
                    accessories=(edit, delete), wrap=False)
                rows.append(row)
            if not rows:
                rows = [self._sheet_empty_row(
                    self._l("settings.empty.snippets"))]
            return {
                "title": self._l("settings.personalize.snippets"),
                "message": self._l("sheets.snippets.message"),
                "sections": [(None, rows,
                              [self._sheet_notice(sheet, notice)])],
                "buttons": self._sheet_footer(sheet, save=False),
                "leading": [self._sheet_button(
                    sheet, "add", self._l("sheets.snippets.add"))],
            }

        @objc.python_method
        def _snippet_form(self, sheet: Any, snippet: SnippetSetting | None,
                          typed_name: str, typed_text: str,
                          notice: str) -> Mapping[str, Any]:
            """One snippet as a form: its name and its text, each a field
            in its own group, and how to say it under the name."""
            # A saved snippet keeps its name: the name is what is said.
            name_row = self._sheet_field_row(
                sheet, "name", typed_name,
                label=self._l("settings.dialog.snippet.name"),
                help_text=self._l("settings.dialog.snippet.name.help"),
                editable=snippet is None)
            text_row = self._sheet_editor_row(
                sheet, "text", typed_text,
                label=self._l("settings.dialog.snippet.value"),
                help_text=self._l("settings.dialog.snippet.value.help"),
                height=132.0)
            # A refusal goes under the field it is about, and the
            # keyboard goes into that field.
            about_name = bool(notice) and any(
                self._l(key) in notice for key in (
                    "validation.snippet.name",
                    "validation.snippet.required"))
            refusal = [self._sheet_notice(sheet, notice)]
            return {
                "title": self._l(
                    "settings.dialog.snippet.edit" if snippet else
                    "settings.dialog.snippet.add"),
                "message": self._l(
                    "sheets.snippet.message.edit" if snippet else
                    "sheets.snippet.message.add"),
                "sections": [
                    (self._l("sheets.snippet.name"), [name_row], [
                        *(refusal if about_name else []),
                        self._page_note(self._l("sheets.snippet.speak"))]),
                    (self._l("sheets.snippet.text"), [text_row],
                     [] if about_name else refusal),
                ],
                "buttons": self._sheet_footer(sheet, save=True),
                "focus": sheet.controls[
                    "name" if snippet is None and (about_name or not notice)
                    else "text"],
            }

        @objc.python_method
        def _edit_snippet(self, sheet: Any,
                          snippet: SnippetSetting | None) -> None:
            """Add a snippet, or edit one, as a form in the open sheet."""
            typed_name = snippet.name if snippet else ""
            typed_text = snippet.text if snippet else ""
            notice = ""
            # A refused save keeps the form up with what was typed and the
            # reason beside it, so nothing the user wrote is lost.
            while True:
                self._sheet_fill(sheet, lambda: self._snippet_form(
                    sheet, snippet, typed_name, typed_text, notice))
                action, _item = self._run_sheet(sheet)
                if action != "save":
                    return
                typed_name = str(sheet.controls["name"].stringValue())
                typed_text = str(sheet.controls["text"].string())
                if not typed_name.strip():
                    # Nothing to shorten and nothing to send: the form
                    # asks for the name that is missing.
                    notice = self._l("validation.snippet.required")
                    continue
                state = self.view_model.save_snippet(
                    typed_name, typed_text,
                    expected_original=(snippet.text if snippet else None))
                self.render()
                if state.notice_level != "error":
                    return
                notice = state.notice

        def editSnippets_(self, _sender: Any) -> None:
            sheet = self._new_sheet()
            notice = ""
            try:
                while True:
                    snippets = self.view_model.state.settings.snippets
                    self._sheet_fill(sheet, lambda: self._snippets_page(
                        sheet, snippets, notice))
                    action, selected = self._run_sheet(sheet)
                    notice = ""
                    if action == "add":
                        self._edit_snippet(sheet, None)
                    elif action == "edit":
                        self._edit_snippet(sheet, selected)
                    elif action == "delete":
                        if self._confirm(
                                self._l("settings.dialog.delete.title"),
                                self._l("settings.dialog.delete.message",
                                        name=selected.name),
                                self._l("settings.action.delete")):
                            state = self.view_model.delete_snippet(
                                selected.name, selected.text)
                            self.render()
                            if state.notice_level == "error":
                                notice = state.notice
                    else:
                        return
            finally:
                self._end_sheet(sheet)

        @objc.python_method
        def _vocabulary_page(self, sheet: Any, typed_terms: str,
                             typed_bans: str,
                             notice: str) -> Mapping[str, Any]:
            """The two lists, each a field in its own group."""
            terms = self._sheet_editor_row(
                sheet, "terms", typed_terms,
                label=self._l("settings.dialog.vocabulary.terms"),
                help_text=self._l("settings.dialog.vocabulary.terms.help"))
            bans = self._sheet_editor_row(
                sheet, "bans", typed_bans,
                label=self._l("settings.dialog.vocabulary.bans"),
                help_text=self._l("settings.dialog.vocabulary.bans.help"))
            # A refusal names the list it is about; it goes under that
            # list, and under the second when it is about both.
            about_terms = bool(notice) and self._l(
                "validation.vocabulary.preferred") in notice
            return {
                "title": self._l("settings.dialog.vocabulary.title"),
                "message": self._l("sheets.vocabulary.message"),
                "sections": [
                    (self._l("settings.dialog.vocabulary.terms"), [terms], [
                        *([self._sheet_notice(sheet, notice)]
                          if about_terms else []),
                        self._page_note(self._l(
                            "sheets.vocabulary.terms.note"))]),
                    (self._l("settings.dialog.vocabulary.bans"), [bans], [
                        *([] if about_terms
                          else [self._sheet_notice(sheet, notice)]),
                        self._page_note(self._l(
                            "sheets.vocabulary.bans.note"))]),
                ],
                "buttons": self._sheet_footer(sheet, save=True),
                "focus": sheet.controls[
                    "terms" if about_terms or not notice else "bans"],
            }

        def editVocabulary_(self, _sender: Any) -> None:
            settings = self.view_model.state.settings
            typed_terms = "\n".join(settings.manual_vocabulary)
            typed_bans = "\n".join(settings.banned_vocabulary)
            notice = ""
            sheet = self._new_sheet()
            try:
                # A refused save keeps the sheet up with both lists as
                # typed and the reason under the list it is about.
                while True:
                    self._sheet_fill(sheet, lambda: self._vocabulary_page(
                        sheet, typed_terms, typed_bans, notice))
                    action, _item = self._run_sheet(sheet)
                    if action != "save":
                        return
                    typed_terms = str(sheet.controls["terms"].string())
                    typed_bans = str(sheet.controls["bans"].string())
                    state = self.view_model.save_vocabulary(
                        typed_terms.splitlines(), typed_bans.splitlines())
                    self.render()
                    if state.notice_level != "error":
                        return
                    notice = state.notice
            finally:
                self._end_sheet(sheet)

        @objc.python_method
        def _corrections_page(self, sheet: Any,
                              corrections: Sequence[CorrectionSetting],
                              notice: str) -> Mapping[str, Any]:
            """What was learned as rows: what was heard and what is
            written instead, where it stands, and Forget. What the old
            review dialog spelled out for one correction at a time is
            here for all of them: where each applies in its row, the rule
            that turns one on and what is stored under the group, and the
            whole explanation as each row's help."""
            sections = []
            for kind, header_key in (
                    ("correction", "sheets.corrections.words"),
                    ("snippet", "sheets.corrections.snippets")):
                rows = []
                privacy = ""
                for item in corrections:
                    if item.kind != kind:
                        continue
                    review = correction_review_text(
                        item, locale=self.view_model.locale)
                    # Its last line says what is stored: once per group.
                    privacy = review.splitlines()[-1]
                    forget = self._sheet_button(
                        sheet, f"forget:{item.kind}:{item.key}",
                        self._l("settings.action.forget"),
                        label=self._l(
                            "sheets.corrections.forget.label",
                            source=item.source, target=item.target),
                        item=item)
                    count = self._value_label(self._l(
                        "sheets.corrections.count", count=item.count))
                    # The runtime names a snippet edit "Snippet: name";
                    # under the "Snippet edits" header the name is enough.
                    source = (item.key if kind == "snippet"
                              and item.source.endswith(f": {item.key}")
                              else item.source)
                    row, title, caption = self._sheet_row(
                        f"{source} → {item.target}",
                        caption=correction_row_caption(
                            item, locale=self.view_model.locale),
                        accessories=(count, forget), keys=(forget,))
                    for label in (title, caption):
                        label.setToolTip_(review)
                    rows.append(row)
                if rows:
                    sections.append((self._l(header_key), rows, [
                        *([self._page_note(self._l(
                            "sheets.corrections.rule"))]
                          if kind == "correction" else []),
                        self._page_note(privacy)]))
            if not sections:
                sections = [(None, [self._sheet_empty_row(
                    self._l("settings.empty.corrections"))], [])]
            header, rows, notes = sections[-1]
            sections[-1] = (
                header, rows, [self._sheet_notice(sheet, notice), *notes])
            return {
                "title": self._l("settings.personalize.corrections"),
                "message": self._l("sheets.corrections.message"),
                "sections": sections,
                "buttons": self._sheet_footer(sheet, save=False),
            }

        def reviewCorrections_(self, _sender: Any) -> None:
            if not self.view_model.state.settings.corrections:
                return
            sheet = self._new_sheet()
            notice = ""
            try:
                while True:
                    corrections = self.view_model.state.settings.corrections
                    self._sheet_fill(sheet, lambda: self._corrections_page(
                        sheet, corrections, notice))
                    action, selected = self._run_sheet(sheet)
                    if action != "forget":
                        return
                    notice = ""
                    # Forgetting cannot be undone, so it asks first, and
                    # Return and Escape both leave the correction alone.
                    if self._confirm(
                            self._l("sheets.corrections.forget.title"),
                            self._l("sheets.corrections.forget.message",
                                    source=selected.source,
                                    target=selected.target),
                            self._l("settings.action.forget")):
                        state = self.view_model.forget_learned(
                            selected.kind, selected.key)
                        self.render()
                        if state.notice_level == "error":
                            notice = state.notice
            finally:
                self._end_sheet(sheet)

        @objc.python_method
        def _keywords_page(self, sheet: Any,
                           candidates: Sequence[AcousticKeywordCandidate],
                           notice: tuple[str, str]) -> Mapping[str, Any]:
            """The keywords as rows: what each has been seen to do, and
            Forget; copying them all out and forgetting them all sit at
            the footer's leading end."""
            rows = []
            for item in candidates:
                scope = "" if item.app_scope is None else f":{item.app_scope}"
                forget = self._sheet_button(
                    sheet, f"forget:{item.keyword}{scope}",
                    self._l("settings.action.forget"),
                    label=self._l(
                        "sheets.keywords.forget.label",
                        keyword=item.keyword),
                    item=item)
                row, _title, _caption = self._sheet_row(
                    item.keyword,
                    caption=self._l(
                        "sheets.keywords.caption",
                        observations=self._l(
                            plural_key(
                                "settings.dialog.keywords.observations",
                                item.observations),
                            count=item.observations),
                        confirmations=self._l(
                            plural_key(
                                "settings.dialog.keywords.confirmations",
                                item.confirmations),
                            count=item.confirmations),
                        status=self._l(
                            "sheets.keywords.status.eligible"
                            if item.eligible else
                            "sheets.keywords.status.gathering"),
                        scope=self._l(
                            "sheets.keywords.scope.global"
                            if item.app_scope is None else
                            "sheets.keywords.scope.private_app")),
                    accessories=(forget,))
                rows.append(row)
            if not rows:
                rows = [self._sheet_empty_row(
                    self._l("sheets.keywords.empty"))]
            whole_list = [
                self._sheet_button(
                    sheet, "export", self._l("settings.action.export")),
                self._sheet_button(
                    sheet, "forget_all",
                    self._l("sheets.keywords.forget_all")),
            ]
            for button in whole_list:
                # With no keyword there is nothing to copy or to forget.
                button.setEnabled_(bool(candidates))
            return {
                "title": self._l("settings.dialog.keywords.title"),
                "message": self._l("sheets.keywords.message"),
                "sections": [(None, rows,
                              [self._sheet_notice(sheet, *notice)])],
                "buttons": self._sheet_footer(sheet, save=False),
                "leading": whole_list,
            }

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

            sheet = self._new_sheet()
            notice = ("", "info")
            try:
                while True:
                    candidates = inspection.candidates
                    self._sheet_fill(sheet, lambda: self._keywords_page(
                        sheet, candidates, notice))
                    action, selected = self._run_sheet(sheet)
                    notice = ("", "info")
                    if action == "export":
                        state = self.view_model.export_acoustic_keywords()
                        notice = (state.notice, state.notice_level)
                        continue
                    if action == "forget":
                        scope = self._l(
                            "settings.dialog.keywords.scope.global"
                            if selected.app_scope is None else
                            "settings.dialog.keywords.scope.private_app")
                        if not self._confirm(
                                self._l(
                                    "settings.dialog.keywords.forget.title"),
                                self._l(
                                    "settings.dialog.keywords.forget.message",
                                    keyword=selected.keyword,
                                    scope=scope.casefold()),
                                self._l("settings.action.forget")):
                            continue
                        state = self.view_model.forget_acoustic_keyword(
                            selected)
                    elif action == "forget_all":
                        if not self._confirm(
                                self._l("settings.dialog.keywords."
                                        "forget_all.title"),
                                self._l("settings.dialog.keywords."
                                        "forget_all.message"),
                                self._l("settings.action.forget_all")):
                            continue
                        state = self.view_model.forget_all_acoustic_keywords()
                    else:
                        break
                    # The result is said in the sheet, and the list is
                    # read again so it shows what is left.
                    notice = (state.notice, state.notice_level)
                    try:
                        inspection = (
                            self.view_model.inspect_acoustic_keywords())
                    except ValueError:
                        break
            finally:
                self._end_sheet(sheet)
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
            response = self._run_modal(alert)
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
                detail_response = self._run_modal(detail)
                if (can_compose and detail_response == 1001
                        and self._confirm(
                            self._l(
                                "settings.dialog.voice_objects.compose.title"),
                            self._l(
                                "settings.dialog.voice_objects.compose.message",
                                sequence=selected.sequence),
                            self._l("settings.action.compose_email"),
                            destructive=False)):
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
                    self._run_modal(result)
                elif (can_copy and detail_response == 1001
                        and self._confirm(
                            self._l(
                                "settings.dialog.voice_objects.copy.title",
                                destination=selected.destination.replace(
                                    "_", " ")),
                            self._l(
                                "settings.dialog.voice_objects.copy.message",
                                sequence=selected.sequence),
                            self._l("settings.action.copy_draft"),
                            destructive=False)):
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
                    result_response = self._run_modal(result)
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
                        self._run_modal(clear_result)
            elif response == 1001 and self._confirm(
                    self._l("settings.dialog.voice_objects.ack.title"),
                    self._l(
                        "settings.dialog.voice_objects.ack.message",
                        sequence=selected.sequence),
                    self._l("settings.action.acknowledge"),
                    destructive=False):
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

        def faceChipPressed_(self, _sender: Any) -> None:
            """Pure delight: the mascot wobbles, nothing else changes."""
            reduced = self.view_model.state.prefers_reduced_motion
            add_jelly_motion(
                self.dynamic["window_face_chip"], "wobble",
                reduced_motion=reduced)
            add_jelly_motion(
                self.dynamic["window_face"], "wobble",
                reduced_motion=reduced)

        def selectSidebarRow_(self, sender: Any) -> None:
            """A sidebar row was pressed, or its Command-number chosen.

            Both carry the row's index as their tag.
            """
            self._select_sidebar_row(int(sender.tag()))

        def faceChanged_(self, sender: Any) -> None:
            self.view_model.choose_face(FACES[sender.selectedSegment()])
            self.render()

        def languageChanged_(self, sender: Any) -> None:
            self.view_model.choose_language(
                LANGUAGES[int(sender.indexOfSelectedItem())])
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

        def soundThemeChanged_(self, sender: Any) -> None:
            self.view_model.choose_sound_theme(
                SOUND_THEMES[int(sender.selectedSegment())])
            self.render()

        def previewSound_(self, _sender: Any) -> None:
            self.view_model.preview_sound()

        def selfCorrectionChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_self_correction(enabled)
            self.render()

        def newSpeechRuntimeChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_new_speech_runtime(enabled)
            self.render()

        def removeRepeatsChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_remove_repeats(enabled)
            self.render()

        def recentDictationsChanged_(self, sender: Any) -> None:
            enabled = sender.state() == NSControlStateValueOn
            self.view_model.set_recent_dictations(enabled)
            self.render()

        @objc.python_method
        def _capture_hotkey(self, *, title_key: str, message_key: str,
                            current_label: str,
                            allow_unbound: bool) -> None:
            """Run one live key-capture dialog and apply what it captured.

            Live capture beats a list of key names: the honest set of usable
            keys is large, and "press the key you want" needs no translation
            from a name to a physical key. A local event monitor does the
            capturing so the dialog does not depend on which view happens to
            hold first responder.
            """
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l(title_key))
            alert.setInformativeText_(self._l(message_key))
            field = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 62))
            preview = self._ink(
                current_label or self._l("settings.dialog.hotkey.none"),
                NSMakeRect(0, 30, 420, 26), size=22, weight="semibold",
                rounded=True)
            note = self._soft(
                self._l("settings.dialog.hotkey.waiting"),
                NSMakeRect(0, 8, 420, 18), size=11, wrap=True, lines=2)
            field.addSubview_(preview)
            field.addSubview_(note)
            _accessible(
                field,
                self._l("settings.accessibility.capture_field.label"),
                self._l("settings.accessibility.capture_field.help"))
            alert.setAccessoryView_(field)
            alert.addButtonWithTitle_(self._l("settings.dialog.hotkey.use"))
            if allow_unbound:
                alert.addButtonWithTitle_(
                    self._l("settings.dialog.hotkey.clear"))
            alert.addButtonWithTitle_(self._l("settings.action.cancel"))

            captured: dict[str, str] = {"name": ""}

            def observe(event: Any) -> Any:
                name = hotkey_name_for_keycode(
                    getattr(event, "keyCode", lambda: -1)())
                if not name:
                    set_accessible_text(
                        note, self._l("settings.dialog.hotkey.rejected"),
                        label=self._l(
                            "settings.accessibility.capture_field.label"))
                    return None          # never let it reach the dialog
                verdict = self.view_model.describe_hotkey(name)
                captured["name"] = name
                set_accessible_text(
                    preview, str(verdict.get("label") or name),
                    label=self._l(
                        "settings.accessibility.capture_field.label"))
                set_accessible_text(
                    note,
                    hotkey_shared_mode_note(
                        verdict.get("shared_modes") or (), name,
                        locale=self.view_model.locale)
                    or self._l("settings.dialog.hotkey.current",
                               hotkey=str(verdict.get("label") or name)),
                    label=self._l(
                        "settings.accessibility.capture_field.label"))
                return None

            monitor = None
            try:
                monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskKeyDown | NSEventMaskFlagsChanged, observe)
            except Exception:
                # Without a monitor the dialog still opens and can be
                # cancelled; it simply cannot capture. Fail visible, not
                # silent, and never crash the settings window.
                set_accessible_text(
                    note, self._l("settings.dialog.hotkey.rejected"),
                    label=self._l(
                        "settings.accessibility.capture_field.label"))
            try:
                response = self._run_modal(alert)
            finally:
                if monitor is not None:
                    try:
                        NSEvent.removeMonitor_(monitor)
                    except Exception:
                        pass
            use, clear = 1000, 1001 if allow_unbound else None
            if response == use and captured["name"]:
                if allow_unbound:
                    self.view_model.choose_undo_hotkey(captured["name"])
                else:
                    self.view_model.choose_hotkey(captured["name"])
            elif clear is not None and response == clear:
                self.view_model.choose_undo_hotkey("")
            self.render()

        def changeHotkey_(self, _sender: Any) -> None:
            self._capture_hotkey(
                title_key="settings.dialog.hotkey.title",
                message_key="settings.dialog.hotkey.message",
                current_label=self.view_model.state.hotkey_label,
                allow_unbound=False)

        def changeUndoHotkey_(self, _sender: Any) -> None:
            self._capture_hotkey(
                title_key="settings.dialog.undo.title",
                message_key="settings.dialog.undo.message",
                current_label=self.view_model.state.undo_hotkey_label,
                allow_unbound=True)

        def browseRecentDictations_(self, _sender: Any) -> None:
            """List recent dictations as metadata; text needs a second step."""
            entries = self.view_model.recent_dictations()
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l("settings.dialog.recent.title"))
            alert.setInformativeText_(
                self._l("settings.dialog.recent.message") if entries
                else self._l("settings.dialog.recent.empty"))
            chooser = None
            if entries:
                chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                    NSMakeRect(0, 0, 460, 26), False)
                # Rows are metadata, so two can read the same, and
                # addItemWithTitle: drops duplicates. Each item carries its
                # entry id instead.
                for entry in entries:
                    item = NSMenuItem.alloc() \
                        .initWithTitle_action_keyEquivalent_(
                            recent_dictation_row(
                                entry, locale=self.view_model.locale),
                            None, "")
                    item.setRepresentedObject_(str(entry.get("id") or ""))
                    chooser.menu().addItem_(item)
                _accessible(
                    chooser,
                    self._l("settings.accessibility.recent_list.label"),
                    self._l("settings.accessibility.recent_list.help"))
                alert.setAccessoryView_(chooser)
                alert.addButtonWithTitle_(
                    self._l("settings.dialog.recent.reveal"))
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            response = self._run_modal(alert)
            if chooser is None or chooser.selectedItem() is None:
                return
            entry_id = str(chooser.selectedItem().representedObject() or "")
            if response == 1000:
                revealed = self.view_model.reveal_recent_dictation(entry_id)
                detail = NSAlert.alloc().init()
                detail.setMessageText_(
                    self._l("settings.dialog.recent.revealed.title"))
                detail.setInformativeText_(
                    revealed or self._l(
                        "settings.dialog.recent.revealed.gone"))
                detail.addButtonWithTitle_(self._l("settings.action.done"))
                self._run_modal(detail)
            self.render()

        def viewModes_(self, _sender: Any) -> None:
            """Show the six voice-mode shortcuts, using the key that is bound."""
            alert = NSAlert.alloc().init()
            alert.setMessageText_(self._l("settings.dialog.modes.title"))
            hotkey = self.view_model.state.hotkey_label
            alert.setInformativeText_(
                self._l("settings.dialog.modes.message", hotkey=hotkey))
            listing = "\n".join(
                self._l(
                    "settings.dialog.modes.row",
                    name=self._l(f"settings.mode.{mode}.name"),
                    shortcut=self._l(
                        f"settings.mode.{mode}.shortcut", hotkey=hotkey),
                    detail=self._l(f"settings.mode.{mode}.detail"),
                )
                for mode in MODE_GUIDE
            )
            scroll, editor = self._text_editor(
                NSMakeRect(0, 0, 460, 190), listing,
                label=self._l("settings.accessibility.modes.label"),
                help_text=self._l(
                    "settings.dialog.modes.message", hotkey=hotkey),
            )
            editor.setEditable_(False)
            alert.setAccessoryView_(scroll)
            alert.addButtonWithTitle_(self._l("settings.action.done"))
            self._run_modal(alert)

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
            self._run_modal(alert)

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

        def openPermissionSettings_(self, sender: Any) -> None:
            """A setup row's own button: open that permission's pane."""
            rows = self.dynamic["onboarding_rows"]
            index = int(sender.tag()) if sender is not None else -1
            permission = (
                rows[index]["permission"] if 0 <= index < len(rows) else "")
            if permission:
                self.view_model.open_system_settings(permission)
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
            """Every 2 seconds: read the runtime again, and render only if
            that, or the appearance, changed what a render would write.
            Most ticks change nothing, and then nothing is laid out or
            marked for drawing."""
            self.view_model.refresh()
            if self._what_a_render_reads() != self._rendered_from:
                self.render()

        def windowDidResize_(self, _notification: Any) -> None:
            """Home and setup lay themselves out again as their page takes
            its new size; this only covers a resize that lands before the
            page's own layout pass does."""
            self._layout_home()
            self._layout_onboarding()

        def windowDidBecomeKey_(self, _notification: Any) -> None:
            self._set_window_active(True)

        def windowDidResignKey_(self, _notification: Any) -> None:
            self._set_window_active(False)

        def windowWillClose_(self, _notification: Any) -> None:
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            self.dynamic["home_face_view"].stopLiving()

        def windowDidChangeOcclusionState_(self, _notification: Any) -> None:
            self._sync_face_clock()

        @objc.python_method
        def feed_level(self, level: float, mode: str) -> None:
            """Live audio seam from the runtime; main-thread, cheap no-op
            when the window is closed or the clock is off."""
            face_view = self.dynamic.get("home_face_view")
            if face_view is None or not face_view.isLiving():
                return
            face_view.setLevel_mode_(level, mode)


# What ``setup.sh`` allows its construction check, and what the release gate
# allows the check's own work: so many times the processor time that
# importing the app took just before it. An install runs the check while the
# dictation service is starting, on Macs several times slower than a
# developer's (confined to an M4 Pro's efficiency cores it takes about six
# times as long), and there the import is most of the minute. Measured
# 2026-11-15: the work costs about half the import on an M4 Pro, and the
# hosted macOS 15 runner is about two and a half times slower at both, so a
# limit in seconds that fits one of them fails by chance on the other.
# ``tests/test_installers.py`` keeps the first number equal to the
# installer's.
INSTALLER_SMOKE_TIMEOUT_SECONDS = 60
INSTALLER_SMOKE_WORK_PER_IMPORT = 2.0


def run_native_appkit_smoke(*, construction_only: bool = False
                            ) -> Mapping[str, int]:
    """Construct and exercise the native UI without touching user state.

    This deliberately does not call ``show``, run an event loop, query
    permissions, load models/audio/services, read private settings, or use
    ``NSUserDefaults``. The installer invokes it only on macOS.

    ``construction_only`` is what an install runs: build the window, draw
    setup, every page and Home, Home in both appearances, and close. It asks
    whether AppKit can build and draw the window on this Mac. The walk
    without it is the release gate's: every measurement of the look, the
    keyboard and what VoiceOver reads, none of which differs from one Mac
    to the next.
    """

    if not APPKIT_AVAILABLE:
        raise RuntimeError("native AppKit smoke requires macOS")

    runtime = {
        "face": "parrot",
        "flight_recorder": False,
        "capture_state": "Ready",
        "active_engine": "Smoke ASR",
        "service_status": "Running",
        "microphone_status": "Unavailable",
        "accessibility_status": "Needs attention",
        "input_monitoring_status": "Needs attention",
        "hotkey": "alt_r",
        "hotkey_label": "Right Option",
        "hotkey_shared_modes": [],
        "undo_hotkey": "",
        "undo_hotkey_label": "",
        "undo_available": False,
        "sounds": "system",
        "language": LANGUAGE_DEFAULT,
        "recent_dictations": False,
        "new_speech_runtime": False,
        # Shaped like runtime_status_snapshot before the first readiness
        # receipt: nothing inspected yet.
        "models": [{
            "name": "Parakeet Unified 0.6B",
            "role": "Primary recognition",
            "install_state": "checking",
            "required": True,
            "warm": False,
            "status": "UNAVAILABLE",
            "detail": "Exact pinned files not resolved",
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

    def set_language(code: str) -> str:
        calls.append(("language", code))
        runtime["language"] = code
        return code

    def set_flight(enabled: bool) -> None:
        calls.append(("flight", enabled))
        runtime["flight_recorder"] = enabled

    def set_tone(bundle: str, tone: str) -> None:
        if tone == "verbatim":
            raise OSError("the tones file is read-only")
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

    def open_system_settings(pane: str) -> None:
        calls.append(("open_system_settings", pane))

    # The runtime owns the guard rules; the smoke fixture mirrors the shape it
    # returns so the window is exercised against a real verdict, not a stub.
    _hotkey_labels = {
        "alt_r": "Right Option", "alt_l": "Left Option",
        "cmd_r": "Right Command", "cmd_l": "Left Command",
        "ctrl_r": "Right Control", "ctrl_l": "Left Control",
        "shift_r": "Right Shift", "shift_l": "Left Shift",
    }

    def describe_hotkey(name: str, *, allow_unbound: bool = False
                        ) -> Mapping[str, Any]:
        label = _hotkey_labels.get(name) or (
            name.upper() if name.startswith("f") and name[1:].isdigit()
            else "")
        if not label:
            if allow_unbound and not name:
                return {"accepted": True, "reason": "unbound", "name": "",
                        "label": "", "shared_modes": ()}
            return {"accepted": False, "reason": "unsupported_key",
                    "name": "", "label": "", "shared_modes": ()}
        return {"accepted": True, "reason": "ok", "name": name,
                "label": label, "shared_modes": ()}

    def set_hotkey(name: str) -> Mapping[str, Any]:
        verdict = describe_hotkey(name)
        if verdict["accepted"]:
            calls.append(("hotkey", name))
            runtime["hotkey"] = name
            runtime["hotkey_label"] = verdict["label"]
        return verdict

    def set_undo_hotkey(name: str) -> Mapping[str, Any]:
        verdict = describe_hotkey(name, allow_unbound=True)
        if verdict["accepted"]:
            calls.append(("undo_hotkey", name))
            runtime["undo_hotkey"] = verdict["name"]
            runtime["undo_hotkey_label"] = verdict["label"]
        return verdict

    def set_sound_theme(name: str) -> str:
        calls.append(("sound_theme", name))
        runtime["sounds"] = name
        return name

    def set_recent_dictations(enabled: bool) -> bool:
        calls.append(("recent_dictations", enabled))
        runtime["recent_dictations"] = bool(enabled)
        return bool(enabled)

    def set_self_correction(enabled: bool) -> bool:
        calls.append(("self_correction", enabled))
        private_settings["self_correction"] = bool(enabled)
        return bool(enabled)

    def set_remove_repeats(enabled: bool) -> bool:
        calls.append(("remove_repeats", enabled))
        private_settings["remove_repeats"] = bool(enabled)
        return bool(enabled)

    def set_new_speech_runtime(enabled: bool) -> bool:
        calls.append(("new_speech_runtime", enabled))
        runtime["new_speech_runtime"] = bool(enabled)
        return bool(enabled)

    actions = GUIActions(
        status_snapshot=lambda: dict(runtime),
        settings_snapshot=lambda: dict(private_settings),
        set_face=set_face,
        set_language=set_language,
        describe_hotkey=describe_hotkey,
        set_hotkey=set_hotkey,
        set_undo_hotkey=set_undo_hotkey,
        set_sound_theme=set_sound_theme,
        preview_sound=lambda cue: calls.append(("preview_sound", cue)) or True,
        set_recent_dictations=set_recent_dictations,
        set_self_correction=set_self_correction,
        set_remove_repeats=set_remove_repeats,
        set_new_speech_runtime=set_new_speech_runtime,
        # Two entries share one metadata row, as two short dictations into
        # the same app within a minute do.
        recent_dictations=lambda: (
            {"id": "smoke-1", "at": 0.0, "age_seconds": 90.0,
             "words": 4, "app": "Mail"},
            {"id": "smoke-2", "at": 0.0, "age_seconds": 90.0,
             "words": 1, "app": "Slack"},
            {"id": "smoke-3", "at": 0.0, "age_seconds": 90.0,
             "words": 1, "app": "Slack"}),
        reveal_recent_dictation=lambda entry_id: calls.append(
            ("reveal_recent", entry_id)) and None,
        insert_recent_dictation=lambda entry_id: {
            "inserted": False, "reason": "unavailable"},
        undo_last_dictation=lambda: {
            "undone": False, "reason": "nothing_to_undo"},
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

        window = controller.window
        # ---- Home and setup: helpers for what is actually drawn and
        # where things landed. Nothing here is ever on screen.
        def rendered(view: Any) -> Any:
            """A view's own drawing as an sRGB bitmap, sized in points."""
            view.setNeedsDisplay_(True)
            bounds = view.bounds()
            rep = view.bitmapImageRepForCachingDisplayInRect_(bounds)
            view.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
            rep = rep \
                .bitmapImageRepByConvertingToColorSpace_renderingIntent_(
                    NSColorSpace.sRGBColorSpace(), 0)
            rep.setSize_(bounds.size)
            return rep

        def pixel(rep: Any, x: float, y: float
                  ) -> tuple[float, float, float, float]:
            """Colour and opacity at a point, from the top left."""
            scale = rep.pixelsWide() / rep.size().width
            color = rep.colorAtX_y_(
                min(rep.pixelsWide() - 1, max(0, int(x * scale))),
                min(rep.pixelsHigh() - 1, max(0, int(y * scale))))
            return (float(color.redComponent()),
                    float(color.greenComponent()),
                    float(color.blueComponent()),
                    float(color.alphaComponent()))

        def near(color: Sequence[float], expected: Sequence[float],
                 tolerance: float = 0.03) -> bool:
            return all(abs(got - want) <= tolerance
                       for got, want in zip(color, expected))

        def frame_in_window(view: Any) -> Any:
            return view.convertRect_toView_(view.bounds(), None)

        def leaf_views(view: Any) -> list[Any]:
            """The shown views with nothing shown inside them."""
            inside = view
            if bool(view.isKindOfClass_(NSButton)) or bool(
                    view.isKindOfClass_(NSTextField)):
                # A control is one thing, whatever it is built from.
                inside = None
            children = [] if inside is None else [
                child for child in inside.subviews()
                if not bool(child.isHidden())]
            if not children:
                return [view]
            return [leaf for child in children
                    for leaf in leaf_views(child)]

        def key_views_from(start: Any) -> list[Any]:
            """Every view Tab reaches from ``start``, once round the loop."""
            seen = [start]
            view = start.nextKeyView()
            while view is not None and view != start and len(seen) < 400:
                seen.append(view)
                view = view.nextKeyView()
            return seen

        def text_fits(view: Any) -> bool:
            """Whether a label shows all of its text in its frame."""
            if not bool(view.isKindOfClass_(NSTextField)):
                return True
            size = view.frame().size
            cell = view.cell()
            if bool(cell.wraps()):
                needed = cell.cellSizeForBounds_(
                    NSMakeRect(0, 0, size.width, 10_000.0))
                return float(needed.height) <= float(size.height) + 1.0
            return float(cell.cellSize().width) <= float(size.width) + 1.0

        def laid_out(container: Any, label: str,
                     *, over: Sequence[Any] = ()) -> None:
            """Nothing in ``container`` is cut short, outside it, or on
            top of something else. ``over`` names views that lie on
            another on purpose."""
            window.contentView().layoutSubtreeIfNeeded()
            bounds = frame_in_window(container)
            placed = []
            for leaf in leaf_views(container):
                # Where a control is, is its alignment rectangle: a system
                # button's frame is wider than the bezel it draws.
                rect = leaf.superview().convertRect_toView_(
                    leaf.alignmentRectForFrame_(leaf.frame()), None)
                require(text_fits(leaf), f"{label}: no text is cut short")
                require(
                    rect.origin.x >= bounds.origin.x - 0.5
                    and rect.origin.y >= bounds.origin.y - 0.5
                    and rect.origin.x + rect.size.width
                    <= bounds.origin.x + bounds.size.width + 0.5
                    and rect.origin.y + rect.size.height
                    <= bounds.origin.y + bounds.size.height + 0.5,
                    f"{label}: nothing is outside its page")
                if rect.size.width > 0.0 and rect.size.height > 0.0 \
                        and leaf not in over:
                    placed.append(rect)
            for index, first in enumerate(placed):
                for second in placed[index + 1:]:
                    require(
                        not (first.origin.x + 0.5
                             < second.origin.x + second.size.width
                             and second.origin.x + 0.5
                             < first.origin.x + first.size.width
                             and first.origin.y + 0.5
                             < second.origin.y + second.size.height
                             and second.origin.y + 0.5
                             < first.origin.y + first.size.height),
                        f"{label}: nothing overlaps")

        # ---- What VoiceOver hears, read through the accessibility API.
        # AppKit says which element it reads for a view (a control's
        # cell, or the view itself); the label must be on that one.
        import AppKit as _appkit_module
        unignored_descendant = getattr(
            _appkit_module, "NSAccessibilityUnignoredDescendant", None)
        require(
            unignored_descendant is not None,
            "AppKit says which element VoiceOver reads for a view")

        def voiceover_stops(root: Any) -> list[Any]:
            """Every shown control, text and image under ``root``, and
            every other view that is an accessibility element itself."""
            found: list[Any] = []

            def walk(view: Any) -> None:
                if bool(view.isHidden()):
                    return
                if bool(view.isKindOfClass_(NSScrollView)):
                    # The scrollers are the system's; the page is ours.
                    walk(view.documentView())
                    return
                if bool(view.respondsToSelector_("cell")):
                    found.append(view)
                    return
                if bool(view.isAccessibilityElement()):
                    found.append(view)
                for child in view.subviews():
                    walk(child)

            walk(root)
            return found

        def check_voiceover(page_name: str, root: Any,
                            *, plain_words: bool = False) -> list[Any]:
            """On one page: each control and each labelled text has its
            label on the element VoiceOver reads; no text's label only
            repeats it; no image is a VoiceOver stop; no two buttons say
            the same thing, and a button in a row says what it does to
            what. ``plain_words`` also holds every label to the words
            the settings pages may show. Returns what it checked."""
            hidden = []
            holder = root
            while holder is not None:
                if bool(holder.isHidden()):
                    hidden.append(holder)
                holder = holder.superview()
            for holder in hidden:
                holder.setHidden_(False)
            try:
                stops = voiceover_stops(root)
                said: dict[str, Any] = {}
                # Every text on the page that VoiceOver reads out.
                heard = {
                    str(view.stringValue()) for view in stops
                    if bool(view.isKindOfClass_(NSTextField))
                    and unignored_descendant(view) is not None}
                for view in stops:
                    element = unignored_descendant(view)
                    label = accessible_value(view, "accessibilityLabel")
                    text = (str(view.stringValue())
                            if bool(view.isKindOfClass_(NSTextField))
                            else str(view.title())
                            if bool(view.isKindOfClass_(NSButton)) else "")
                    what = (f"{page_name}: {type(view).__name__} "
                            f"{text or label!r}")
                    if bool(view.isKindOfClass_(NSImageView)):
                        require(
                            element is None
                            and not bool(view.isAccessibilityElement())
                            and not bool(
                                view.cell().isAccessibilityElement()),
                            f"{what}: an image is not a VoiceOver stop")
                        continue
                    if element is None:
                        continue
                    spoken = accessible_value(element, "accessibilityLabel")
                    require(
                        spoken == label,
                        f"{what}: VoiceOver reads {spoken!r} where the "
                        f"label is {label!r}")
                    require(
                        not plain_words or not internal_words_in(label),
                        f"{what}: the label uses an internal word")
                    if bool(view.isKindOfClass_(NSTextField)):
                        require(
                            not label or label != text,
                            f"{what}: the label only repeats the text")
                        require(
                            not label or label not in heard,
                            f"{what}: the label says again what the text "
                            f"{label!r} beside it has just said")
                        continue
                    require(bool(label), f"{what}: no VoiceOver label")
                    require(
                        accessible_value(element, "accessibilityHelp")
                        == accessible_value(view, "accessibilityHelp"),
                        f"{what}: VoiceOver reads the help on its element")
                    if not bool(view.isKindOfClass_(NSButton)):
                        continue
                    require(
                        label not in said,
                        f"{page_name}: two buttons both say {label!r}")
                    said[label] = view
                    in_row = bool(view.superview().isKindOfClass_(
                        SettingsRowView))
                    require(
                        not in_row or len(label.split()) >= 2,
                        f"{what}: a row's button says what it does to "
                        f"what, not only {label!r}")
                return stops
            finally:
                for holder in hidden:
                    holder.setHidden_(True)

        if construction_only:
            root = window.contentView()

            drawn_surfaces: list[str] = []

            def draw(what: str, *appearances: Any) -> None:
                for name in appearances or (NSAppearanceNameAqua,):
                    drawn_surfaces.append(what)
                    window.setAppearance_(
                        NSAppearance.appearanceNamed_(name))
                    controller.render()
                    root.layoutSubtreeIfNeeded()
                    drawn = rendered(root)
                    require(
                        drawn is not None and int(drawn.pixelsWide()) > 0,
                        f"construction: {what} draws")

            require(
                model.state.section == "Home" and controller.setup_mode,
                "construction: a first run opens in setup")
            draw("first-run setup")
            for index in range(1, len(SIDEBAR_ROWS)):
                controller._select_sidebar_row(index)
                require(
                    model.state.section != "Home"
                    and not controller.setup_mode,
                    f"construction: page {index} shows")
                draw(f"page {index}")
            controller._select_sidebar_row(0)
            # Setup, finished: permissions, the key tried, the models
            # ready, a first dictation, and the last step acknowledged.
            runtime.update(
                service_status="Running",
                microphone_status="Ready",
                accessibility_status="Granted",
                input_monitoring_status="Granted",
                capture_state="Listening",
            )
            model.refresh()
            runtime["capture_state"] = "Ready"
            runtime["models"][0].update(
                install_state="installed", warm=True, status="RESOLVED")
            model.refresh()
            runtime["last_word_count"] = 4
            model.refresh()
            controller.render()
            controller.continueSetup_(None)
            require(
                model.state.section == "Home" and not controller.setup_mode
                and not bool(
                    controller.dynamic["home_cluster"].isHidden()),
                "construction: Home shows once setup is finished")
            draw("Home", NSAppearanceNameAqua, NSAppearanceNameDarkAqua)
            window.setAppearance_(None)
            controller.window.close()
            return {
                "sections": len(SECTIONS),
                "sidebar_rows": len(controller.sidebar_rows),
                "settings_panes": len(SETTINGS_PANES),
                "onboarding_steps": len(model.state.onboarding_steps),
                # What the check cost, for the release gate to hold it to.
                "drawn": len(drawn_surfaces),
                "renders": controller.renders,
            }

        def setup_route() -> tuple[list[float], float | None, float]:
            """Where setup's step marks start, where its bar's fill ends
            (None while it has none), and where the bar itself ends."""
            controller._layout_onboarding()
            # All three start at the leading edge of setup's foot.
            marks = [
                round(float(marker.frame().origin.x), 1)
                for marker in controller.dynamic["onboarding_step_cards"]]
            fill = controller.dynamic["onboarding_progress_fill"]
            track = controller.dynamic["onboarding_progress_track"]
            return (
                marks,
                None if bool(fill.isHidden())
                else round(float(fill.frame().size.width), 1),
                round(float(track.frame().size.width), 1))

        # ---- First run: the render itself puts the window in setup mode,
        # because setup is what Home is showing.
        setup = controller.dynamic["onboarding_card"]
        first_marks, first_fill, _track_end = setup_route()
        require(
            len(first_marks) == 4 and first_fill is None
            and first_marks == sorted(set(first_marks)),
            "setup's four step marks run left to right under an empty bar")
        require(
            model.state.section == "Home"
            and not model.state.onboarding_complete
            and controller.setup_mode
            and bool(controller.sidebar_item.isCollapsed())
            and bool(controller.title_strip.isHidden())
            and not bool(setup.isHidden())
            and bool(controller.dynamic["home_cluster"].isHidden()),
            "first run opens in setup mode, driven by the render")
        # A page shortcut pressed during setup shows that page with the
        # shell back around it; coming Home returns to setup.
        controller._select_sidebar_row(len(SIDEBAR_ROWS) - 1)
        require(
            model.state.section == "Advanced" and not controller.setup_mode
            and not bool(controller.sidebar_item.isCollapsed())
            and not bool(controller.title_strip.isHidden()),
            "a page shortcut during setup brings the shell back")
        controller._select_sidebar_row(0)
        require(
            model.state.section == "Home" and controller.setup_mode
            and bool(controller.sidebar_item.isCollapsed()),
            "coming Home during setup returns to setup mode")

        # The stage: the stage colour for the face, a face that leans in
        # its own view with its hard shadow, and a bubble for the step.
        setup_stage = controller.dynamic["onboarding_stage"]
        setup_face = controller.dynamic["onboarding_face"]
        require(
            bool(setup_stage.isKindOfClass_(StagePanelView))
            and setup_stage.stage_token == stage_token_for_face(
                model.state.face)
            and setup_face.superview() == setup_stage
            and bool(setup_face.isKindOfClass_(LiveFaceView))
            and setup_face.shadow and not setup_face.whispers
            and abs(setup_face.tilt - STAGE_FACE_TILT) < 1e-6
            and abs(float(setup_face.frameCenterRotation())) < 1e-6
            and abs(setup_face.art_side() - SETUP_FACE_SIZE) < 0.5
            and not setup_face.isLiving()
            and bool(setup_face.isAccessibilityElement())
            and accessible_value(setup_face, "accessibilityRole")
            == "AXImage"
            and accessible_value(setup_face, "accessibilityLabel")
            == localized_string(
                "overview.accessibility.onboarding.face",
                face=localized_string(
                    f"settings.face.{model.state.face}")),
            "the setup face is drawn from the character ops, leaning, "
            "with its hard shadow")
        setup_bubble = controller.dynamic["onboarding_bubble"]
        require(
            bool(setup_bubble.isKindOfClass_(SpeechBubbleView))
            and controller.dynamic["onboarding_title"].superview()
            == setup_bubble
            and controller.dynamic["onboarding_detail"].superview()
            == setup_bubble
            and str(controller.dynamic["onboarding_title"].stringValue())
            == localized_string("onboarding.permissions.title")
            and str(controller.dynamic["onboarding_detail"].stringValue())
            == localized_string("onboarding.permissions.detail"),
            "the setup bubble says what the step is for")

        # The permissions step: one row for each permission, with its
        # state and its own button to that permission's pane.
        setup_row_views = controller.dynamic["onboarding_rows"]
        require(
            len(setup_row_views) == SETUP_ROW_COUNT == SETUP_ROW_LIMIT
            and [row["key"] for row in setup_row_views[:3]]
            == list(PERMISSIONS)
            and not any(bool(row["view"].isHidden())
                        for row in setup_row_views[:3])
            and bool(setup_row_views[3]["view"].isHidden()),
            "setup shows one row for each permission")
        for index, (row, permission) in enumerate(
                zip(setup_row_views, PERMISSIONS)):
            name = localized_string(f"onboarding.permission.{permission}")
            require(
                str(row["title"].stringValue()) == name
                and str(row["caption"].stringValue()) == localized_string(
                    f"onboarding.permission.{permission}.why")
                and row["icon"].image() is not None,
                f"the {permission} row names it and says why")
            # The first missing permission's button is the step's primary
            # action: amber, and the one that answers Return.
            primary = index == 0
            require(
                not bool(row["button"].isHidden())
                and bool(row["state"].isHidden())
                and bool(row["button"].isKindOfClass_(ChunkyButton))
                and row["button"].variant == (
                    "amber" if primary else "plain")
                and str(row["button"].keyEquivalent()) == (
                    "\r" if primary else "")
                and str(row["button"].action())
                == "openPermissionSettings:"
                and str(row["button"].title()) == localized_string(
                    "onboarding.action.open_system_settings")
                and accessible_value(
                    row["button"].cell(), "accessibilityLabel")
                == localized_string(
                    "onboarding.permission.open.label", permission=name),
                f"a missing {permission} permission has its own button")
            before = len(calls)
            row["button"].performClick_(None)
            require(
                calls[before:] == [("open_system_settings", permission)],
                f"the {permission} row opens that permission's pane")
        # One way forward at a time, and it is never called Allow. While a
        # permission is missing the way forward is its row's button, so
        # the foot's Continue waits, disabled, and nothing repeats a row.
        setup_buttons = [
            leaf for leaf in leaf_views(setup)
            if bool(leaf.isKindOfClass_(NSButton))]
        action = controller.dynamic["onboarding_action"]
        require(
            [button for button in setup_buttons
             if str(button.action()) == "continueSetup:"] == [action]
            and bool(action.isKindOfClass_(ChunkyButton))
            and action.variant == "plain"
            and not bool(action.isEnabled())
            and str(action.keyEquivalent()) == ""
            and str(action.title()) == localized_string(
                "onboarding.action.continue")
            and accessible_value(action.cell(), "accessibilityHelp")
            == localized_string("onboarding.action.waiting.help"),
            "setup has one Continue button, and it waits while a "
            "permission is missing")
        require(
            [button for button in setup_buttons
             if isinstance(button, ChunkyButton)
             and button.variant == "amber"] == [setup_row_views[0]["button"]]
            and [button for button in setup_buttons
                 if str(button.keyEquivalent()) == "\r"]
            == [setup_row_views[0]["button"]]
            and [button for button in setup_buttons
                 if bool(button.isEnabled())]
            == [row["button"] for row in setup_row_views[:3]]
            and not any("allow" in str(button.title()).casefold()
                        for button in setup_buttons),
            "one amber button answers Return, the rows' own buttons are "
            "the only ones to press, and no button is called Allow")
        require(
            controller.window.initialFirstResponder()
            == setup_row_views[0]["button"]
            and all(row["button"] in key_views_from(
                setup_row_views[0]["button"])
                for row in setup_row_views[:3])
            and action not in key_views_from(setup_row_views[0]["button"])
            and not any(row in key_views_from(setup_row_views[0]["button"])
                        for row in controller.sidebar_rows),
            "in setup, Tab runs through the rows' buttons and never into "
            "the hidden sidebar or the waiting Continue")
        laid_out(setup, "setup, permissions", over=(
            controller.dynamic["onboarding_progress_fill"],))
        require(
            controller.dynamic["onboarding_face"].pose == "idle",
            "the setup face keeps its mouth shut on the permissions step")
        # What only decorates stays out of VoiceOver's way: an image view
        # is read through its cell, so both are told; a separator is not
        # an empty group; the bubble's copy is read where it stands.
        quiet = [setup_bubble]
        for image_view in (
                *(row["icon"] for row in setup_row_views),
                *(row["check"] for row in setup_row_views),
                *controller.dynamic["onboarding_step_icons"]):
            quiet.extend((image_view, image_view.cell()))
        require(
            not any(bool(element.isAccessibilityElement())
                    for element in quiet),
            "setup's symbols and bubble are not VoiceOver stops of their "
            "own")

        # A permission granted while the step is showing: its row trades
        # the button for the check mark and the word, live, and the
        # primary action moves to the next permission still missing.
        runtime["microphone_status"] = "Ready"
        model.refresh()
        controller.render()
        root = window.contentView()
        root.layoutSubtreeIfNeeded()
        granted_row, next_row, last_row = setup_row_views[:3]
        check_frame = frame_in_window(granted_row["check"])
        row_frame = frame_in_window(granted_row["view"])
        require(
            not bool(granted_row["state"].isHidden())
            and granted_row["state"].tone == "good"
            and bool(granted_row["state"].isKindOfClass_(StatusLabelView))
            and granted_row["check"].image() is not None
            and not bool(granted_row["check"].isHidden())
            and check_frame.size.width >= 12.0
            and check_frame.size.height >= 12.0
            and row_frame.origin.x <= check_frame.origin.x
            and check_frame.origin.x + check_frame.size.width
            <= row_frame.origin.x + row_frame.size.width
            and row_frame.origin.y <= check_frame.origin.y
            and check_frame.origin.y + check_frame.size.height
            <= row_frame.origin.y + row_frame.size.height
            and not bool(granted_row["status"].isHidden())
            and granted_row["status"].superview() == granted_row["state"]
            and str(granted_row["status"].stringValue())
            == localized_string("onboarding.permission.granted")
            and bool(granted_row["button"].isHidden())
            and str(granted_row["button"].keyEquivalent()) == "",
            "a granted permission shows its check mark and the word, and "
            "no button")
        require(
            next_row["button"].variant == "amber"
            and str(next_row["button"].keyEquivalent()) == "\r"
            and last_row["button"].variant == "plain"
            and str(last_row["button"].keyEquivalent()) == ""
            and controller.window.initialFirstResponder()
            == next_row["button"]
            and not bool(action.isEnabled()),
            "the primary action moves to the next missing permission")
        laid_out(setup, "setup, one permission granted", over=(
            controller.dynamic["onboarding_progress_fill"],))
        require(
            len(check_voiceover("setup", setup)) >= 20
            and accessible_value(
                granted_row["status"].cell(), "accessibilityLabel")
            == localized_string(
                "onboarding.row.accessibility.status",
                row=localized_string("onboarding.permission.microphone"))
            and accessible_value(
                next_row["button"].cell(), "accessibilityLabel")
            == localized_string(
                "onboarding.permission.open.label",
                permission=localized_string(
                    "onboarding.permission.accessibility")),
            "VoiceOver hears whose state a setup row's words are, and "
            "which pane its button opens")
        runtime["microphone_status"] = "Unavailable"
        model.refresh()
        controller.render()
        require(
            bool(granted_row["state"].isHidden())
            and not bool(granted_row["button"].isHidden())
            and granted_row["button"].variant == "amber",
            "a permission taken away again loses its check mark")
        # The shell is checked next, with the sidebar showing.
        controller.set_setup_mode(False)

        # ---- The shell: a system split view with a full-height sidebar
        # under the traffic lights, and a title strip on the content side.
        root_view = window.contentView()
        root_view.layoutSubtreeIfNeeded()
        require(
            bool(int(window.styleMask())
                 & int(NSWindowStyleMaskFullSizeContentView))
            and bool(window.titlebarAppearsTransparent())
            and int(window.titleVisibility()) == int(NSWindowTitleHidden)
            and str(window.title()) == APP_NAME,
            "full-size content view under a transparent, untitled title bar")
        split = controller.split_controller
        require(
            window.contentViewController() == split
            and bool(split.isKindOfClass_(NSSplitViewController))
            and len(split.splitViewItems()) == 2
            and split.splitViewItems()[0] == controller.sidebar_item,
            "the system split view holds the sidebar and the content side")
        # 1 is NSSplitViewItemBehaviorSidebar: the item the system gives
        # the sidebar material and the full-height layout.
        require(
            int(controller.sidebar_item.behavior()) == 1
            and not bool(controller.sidebar_item.canCollapse())
            and not bool(controller.sidebar_item.isSpringLoaded())
            and not bool(controller.sidebar_item.isCollapsed()),
            "the sidebar is a system sidebar item that stays open")

        def in_window(view: Any) -> Any:
            return view.convertRect_toView_(view.bounds(), None)

        def ancestors_of(view: Any) -> list[Any]:
            chain = []
            while view is not None:
                chain.append(view)
                view = view.superview()
            return chain

        def key_loop_from(start: Any) -> list[Any]:
            """Every view Tab reaches from ``start``, once round the loop."""
            seen = [start]
            view = start.nextKeyView()
            while view is not None and view != start and len(seen) < 400:
                seen.append(view)
                view = view.nextKeyView()
            return seen

        def intersects(first: Any, second: Any) -> bool:
            return (
                first.origin.x < second.origin.x + second.size.width
                and second.origin.x < first.origin.x + first.size.width
                and first.origin.y < second.origin.y + second.size.height
                and second.origin.y < first.origin.y + first.size.height)

        def shown_leaves(view: Any) -> list[Any]:
            # A control is one thing, whatever its private parts are:
            # newer systems build a text field, a button and an image view
            # around a view of their own, and a walk that went into it
            # would never meet the control.
            children = [child for child in view.subviews()
                        if not bool(child.isHidden())]
            if not children or bool(view.respondsToSelector_("cell")):
                return [view]
            return [leaf for child in children
                    for leaf in shown_leaves(child)]

        content_height = float(root_view.bounds().size.height)
        sidebar_frame = in_window(controller.sidebar)
        content_frame = in_window(controller.dynamic["content_side"])
        divider = float(split.splitView().dividerThickness())
        require(
            abs(sidebar_frame.origin.x) < 0.5
            and abs(sidebar_frame.size.width + divider - SIDEBAR_WIDTH) < 0.5
            and abs(sidebar_frame.size.height - content_height) < 0.5
            and abs(content_frame.origin.x - SIDEBAR_WIDTH) < 0.5
            and abs(content_frame.size.height - content_height) < 0.5,
            "the sidebar is SIDEBAR_WIDTH wide, on the left, full height")
        lights = [
            window.standardWindowButton_(kind) for kind in (
                NSWindowCloseButton, NSWindowMiniaturizeButton,
                NSWindowZoomButton)]
        require(all(light is not None for light in lights), "traffic lights")
        for light in lights:
            light_frame = in_window(light)
            require(
                intersects(light_frame, sidebar_frame),
                "the traffic lights sit over the sidebar")
            for leaf in shown_leaves(controller.sidebar):
                require(
                    not intersects(light_frame, in_window(leaf)),
                    "nothing in the sidebar is under the traffic lights")
        require(
            controller.dynamic["content_side"].layer() is not None
            and bool(controller.dynamic[
                "content_side"].layer().masksToBounds()),
            "nothing on the content side can paint over the sidebar")
        strip_frame = in_window(controller.title_strip)
        column_frame = in_window(controller.dynamic["page_column"])
        title_frame = in_window(controller.dynamic["page_title"])
        require(
            abs(strip_frame.size.height - TITLE_STRIP_HEIGHT) < 0.5
            and abs(strip_frame.origin.y + strip_frame.size.height
                    - content_height) < 0.5
            and abs(strip_frame.origin.x - SIDEBAR_WIDTH) < 0.5,
            "the title strip tops the content side")
        require(
            abs(column_frame.origin.y + column_frame.size.height
                - strip_frame.origin.y) < 0.5,
            "the page column starts where the title strip ends")
        # A label's text starts its alignment inset in from its frame.
        require(
            abs(title_frame.origin.x + float(
                controller.dynamic["page_title"].alignmentRectInsets().left)
                - column_frame.origin.x) < 0.5
            and strip_frame.origin.y <= title_frame.origin.y
            and title_frame.origin.y + title_frame.size.height
            <= strip_frame.origin.y + strip_frame.size.height,
            "the page title sits in the strip on the column's leading edge")
        require(
            accessible_value(
                controller.dynamic["page_title"].cell(),
                "accessibilityRole") == "AXHeading",
            "VoiceOver reads the page title as a heading")
        require(
            accessible_value(
                controller.dynamic["sidebar_list"],
                "accessibilityRole") == str(NSAccessibilityListRole)
            and accessible_value(
                controller.dynamic["sidebar_list"],
                "accessibilityLabel") == localized_string(
                    "shell.sidebar.accessibility.label"),
            "VoiceOver finds the sidebar rows as one list")
        require(
            all(row.superview() == controller.dynamic["sidebar_list"]
                for row in controller.sidebar_rows),
            "every row is in the sidebar list")
        require(
            str(controller.dynamic["window_version"].stringValue()) ==
            localized_string(
                "app.version", version=application_build_version())
            and controller.sidebar in tuple(ancestors_of(
                controller.dynamic["window_version"])),
            "the build label moved to the foot of the sidebar")
        require(
            not any(
                word.isalpha() and len(word) > 1 and word.isupper()
                for word in str(controller.dynamic[
                    "window_version"].stringValue()).split()),
            "the build label is not set in capitals")
        require(
            str(controller.dynamic["window_title"].stringValue())
            == APP_NAME
            and accessible_value(
                controller.dynamic["window_title"].cell(),
                "accessibilityLabel") in ("", APP_NAME),
            "VoiceOver hears the sidebar's title as the app's name, not "
            "as a tagline that is not on screen")
        require(
            str(controller.dynamic["window_state"].stringValue()) ==
            localized_string("shell.state.setup"),
            "the sidebar says setup is under way")
        require(
            controller.dynamic["home_face_view"] is not None
            and not controller.dynamic["home_face_view"].isLiving(),
            "living face view built, clock off until shown")
        require(
            [row.key for row in SIDEBAR_ROWS] == [
                "home", "personalize", "controls", "privacy", "advanced"]
            and len(controller.sidebar_rows) == len(SIDEBAR_ROWS),
            "five sidebar rows in order")
        previous_top = None
        for index, spec in enumerate(SIDEBAR_ROWS):
            row = controller.sidebar_rows[index]
            row_frame = in_window(row)
            for element, kind in ((row, "row"), (row.cell(), "cell")):
                require(
                    accessible_value(element, "accessibilityLabel") ==
                    localized_string(spec.label_key),
                    f"sidebar {kind} label {spec.key}")
            require(
                str(row.title()) == localized_string(spec.label_key)
                and row.icon_view.image() is not None,
                f"sidebar row {spec.key} has a label and a symbol")
            require(
                int(row.tag()) == index
                and str(row.action()) == "selectSidebarRow:"
                and row.target() == controller,
                f"sidebar row {spec.key} action")
            require(
                abs(row_frame.size.height - SIDEBAR_ROW_HEIGHT) < 0.5
                and row_frame.origin.x >= sidebar_frame.origin.x
                and row_frame.origin.x + row_frame.size.width
                <= sidebar_frame.origin.x + sidebar_frame.size.width,
                f"sidebar row {spec.key} is 32 points tall, in the sidebar")
            require(
                previous_top is None or row_frame.origin.y
                + row_frame.size.height <= previous_top,
                f"sidebar row {spec.key} is below the one before it")
            previous_top = row_frame.origin.y
            require(
                bool(row.selected) == (index == 0)
                and bool(row.isAccessibilitySelected()) == (index == 0)
                and accessible_value(row.cell(), "accessibilityValue") == (
                    localized_string("nav.selected") if index == 0 else ""),
                f"sidebar row {spec.key} selection at start")
            require(
                bool(row.title_font().isEqual_(
                    NSFont.systemFontOfSize_weight_(
                        13.0, NSFontWeightBold if index == 0
                        else NSFontWeightMedium))),
                f"sidebar row {spec.key}: the row that is showing is set "
                "in bold and no other is, so the pale fill is not the "
                "only sign of it")
        require(
            accessible_value(
                controller.sidebar_rows[0], "accessibilityHelp") ==
            localized_string("settings.accessibility.sections.help"),
            "sidebar row help")
        for switch_key in (
                "flight_toggle", "acoustic_time_machine_toggle",
                "voice_object_commands_toggle", "selective_relisten_toggle"):
            require(
                bool(controller.dynamic[switch_key].isKindOfClass_(NSSwitch)),
                f"{switch_key} is a native switch")
        jelly_probe = controller.dynamic["pause_button"]
        require(
            not add_jelly_motion(jelly_probe, "press", reduced_motion=True),
            "reduce motion silences jelly springs")
        require(
            add_jelly_motion(jelly_probe, "press", reduced_motion=False),
            "jelly springs attach")
        for axis in ("x", "y"):
            require(
                jelly_probe.layer().animationForKey_(
                    f"whisper-face-press-{axis}") is not None,
                f"jelly {axis} spring is motion-spec driven")
        anchor = jelly_probe.layer().anchorPoint()
        require(
            abs(anchor.x - 0.5) < 1e-6 and abs(anchor.y - 0.5) < 1e-6,
            "jelly springs squash around the layer centre")
        require(
            {"press", "release", "wobble", "pop"} <= set(MOTION_SPECS),
            "shared motion specs cover the jelly vocabulary")
        require(
            not fade_in(controller.dynamic["notice"], reduced_motion=True),
            "reduce motion silences the chrome crossfade")
        require(
            len(controller.dynamic["onboarding_step_icons"]) == 4,
            "onboarding step chips carry progress markers")
        require(
            bool(controller.dynamic["onboarding_progress_fill"].isHidden()),
            "onboarding progress bar starts empty")
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
            str(setup_row_views[0]["button"].keyEquivalent()) == "\r"
            and str(controller.dynamic[
                "onboarding_action"].keyEquivalent()) == "",
            "onboarding key equivalent")
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
            int(controller._configure_key_view_loop(model.state)) >= 4,
            "overview key-view loop")
        require(
            controller.sidebar_rows[0].nextKeyView() is not None,
            "initial next key view")
        require(
            bool(controller.sidebar_rows[0].selected)
            and controller.window.initialFirstResponder() ==
            controller.sidebar_rows[0],
            "keyboard entry starts at the sidebar, on the selected row")

        permissions_row = controller.dynamic["open_system_settings_row"]
        support_group = permissions_row.superview()

        def support_rows_shown() -> list[str]:
            return [str(row.title.stringValue())
                    for row in support_group.visible_rows()]

        require(
            not bool(controller.dynamic[
                "open_system_settings_button"].isHidden())
            and not bool(permissions_row.isHidden())
            and localized_string("pages.support.permissions")
            in support_rows_shown()
            and len(support_group.separators)
            == len(support_group.visible_rows()) - 1,
            "permission recovery visible")
        require(
            accessible_value(
                controller.dynamic["open_system_settings_button"],
                "accessibilityLabel") == localized_string(
                    "diagnostics.accessibility.open_system_settings"),
            "permission recovery accessibility")
        # The button that answers Return is the first missing
        # permission's own; the action behind the foot's Continue still
        # opens that same pane if anything sends it.
        before = len(calls)
        setup_row_views[0]["button"].performClick_(None)
        controller.continueSetup_(None)
        require(
            calls[before:] == [("open_system_settings", "microphone")] * 2,
            "permission recovery opens the missing permission's pane")
        require(model.state.section == "Home", "permission recovery route")
        require(
            str(setup_row_views[0]["button"].title()) ==
            localized_string("onboarding.action.open_system_settings")
            and str(controller.dynamic["onboarding_action"].title()) ==
            localized_string("onboarding.action.continue")
            and not bool(
                controller.dynamic["onboarding_action"].isEnabled()),
            "a missing permission offers System Settings")
        runtime.update(
            microphone_status="Starting",
            accessibility_status="Granted",
            input_monitoring_status="Granted",
        )
        model.refresh()
        controller.render()
        onboarding_action = controller.dynamic["onboarding_action"]
        require(
            str(onboarding_action.title()) ==
            localized_string("onboarding.action.continue")
            and bool(onboarding_action.isEnabled())
            and onboarding_action.variant == "amber"
            and str(onboarding_action.keyEquivalent()) == "\r"
            and all(bool(row["button"].isHidden())
                    and str(row["button"].keyEquivalent()) == ""
                    for row in setup_row_views),
            "a permission still being checked offers no System Settings")
        require(
            not any(bool(row["state"].isHidden())
                    for row in setup_row_views[:3])
            and [row["state"].tone for row in setup_row_views[:3]]
            == ["neutral", "good", "good"]
            and [str(row["status"].stringValue())
                 for row in setup_row_views[:3]] == [
                localized_string("onboarding.permission.checking"),
                localized_string("onboarding.permission.granted"),
                localized_string("onboarding.permission.granted")],
            "each permission row says how it stands")
        onboarding_face = controller.dynamic["onboarding_face"]
        runtime.update(
            service_status="Running",
            microphone_status="Ready",
            accessibility_status="Granted",
            input_monitoring_status="Granted",
        )
        model.refresh()
        controller.render()
        require(
            not model.permission_recovery_needed()
            and bool(controller.dynamic[
                "open_system_settings_button"].isHidden())
            and bool(permissions_row.isHidden())
            and permissions_row not in support_group.visible_rows()
            and localized_string("pages.support.permissions")
            not in support_rows_shown()
            and len(support_group.separators)
            == len(support_group.visible_rows()) - 1,
            "permission recovery hidden: with nothing missing the "
            "Permissions row is gone, and takes no room")
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
        # The bubble shows the key that is bound as a key cap, and
        # VoiceOver still hears the whole sentence, key included.
        setup_detail = controller.dynamic["onboarding_detail"]
        setup_copy = str(setup_detail.attributedStringValue().string())
        require(
            setup_copy.count("\ufffc") == 1
            and model.state.hotkey_label not in setup_copy
            and setup_copy.replace("\ufffc", model.state.hotkey_label)
            == localized_string(
                "onboarding.hotkey.detail", hotkey=model.state.hotkey_label)
            and accessible_value(setup_detail.cell(), "accessibilityValue")
            == localized_string(
                "onboarding.hotkey.detail", hotkey=model.state.hotkey_label),
            "the setup bubble names the bound key with a key cap")
        # The face opens its mouth exactly where the copy asks the person
        # to speak, and again for the celebration; nowhere else.
        require(
            onboarding_face.pose == "idle"
            and bool(onboarding_action.isEnabled())
            and onboarding_action.variant == "amber"
            and str(onboarding_action.keyEquivalent()) == "\r",
            "the setup face listens while the key is tried")
        runtime["capture_state"] = "Listening"
        model.refresh()
        controller.render()
        require(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key == "models",
            "model readiness")
        # A model the runtime could not install stands out on this step
        # the way a fault does on every page: the warning symbol.
        runtime["models"][0].update(install_state="not_installed")
        model.refresh()
        controller.render()
        require(
            setup_row_views[0]["key"].startswith("model:")
            and setup_row_views[0]["tone"] == "attention"
            and not bool(setup_row_views[0]["state"].isHidden())
            and setup_row_views[0]["state"].tone == "warn",
            "a required model that is not installed carries the warning "
            "symbol on the models step")
        runtime["models"][0].update(install_state="checking")
        model.refresh()
        controller.render()
        require(
            setup_row_views[0]["state"].tone == "neutral",
            "a model still being checked carries no warning")
        require(
            onboarding_face.pose == "idle",
            "the setup face waits while the models get ready")
        runtime["capture_state"] = "Ready"
        runtime["models"][0].update(
            install_state="installed", warm=True, status="RESOLVED")
        model.refresh()
        controller.render()
        require(
            next(step for step in model.state.onboarding_steps
                 if not step.complete).key == "first_dictation",
            "first dictation")
        require(
            onboarding_face.pose == "talk",
            "the setup face talks where the copy asks for the first words")
        require(
            not any(bool(marker.isHidden()) for marker in
                    controller.dynamic["onboarding_step_cards"]),
            "the route shows while a step is still open")
        # The foot button's title changes from step to step and the marks
        # beside it stay where they were; the bar's fill ends over the
        # mark of the step that is open.
        marks, fill_end, _track_end = setup_route()
        require(
            str(controller.dynamic["onboarding_action"].title())
            != localized_string("onboarding.action.continue")
            and marks == first_marks and fill_end == marks[3],
            "setup's step marks keep their places from step to step, and "
            "the bar is filled as far as the open step's mark")
        runtime["last_word_count"] = 4
        model.refresh()
        controller.render()
        marks, fill_end, track_end = setup_route()
        require(
            fill_end == track_end,
            "the bar is full when every step is done")
        require(
            str(controller.dynamic["result_summary"].stringValue()) ==
            localized_string("results.summary.words.many", words=4),
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
        require(
            onboarding_face.pose == "talk",
            "the setup face talks for the celebration")
        require(
            [row["key"] for row in setup_row_views] == list(
                step.key for step in model.state.onboarding_steps)
            and not any(bool(row["state"].isHidden())
                        or row["state"].tone != "good"
                        or row["check"].image() is None
                        for row in setup_row_views)
            and all(bool(marker.isHidden()) for marker in
                    controller.dynamic["onboarding_step_cards"]),
            "every confirmed step is listed once, with its check mark")
        require(
            all(not str(row["caption"].stringValue())
                and abs(float(row["view"].frame().size.height)
                        - ROW_CAPTION_MIN_HEIGHT) < 0.01
                for row in setup_row_views),
            "a setup row with no caption to show keeps the height of a "
            "row with one")
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
        # ---- Home, as it stands once setup is acknowledged. Colours are
        # read in the light appearance, whatever this Mac is set to.
        controller.window.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameAqua))
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        light = palette_for_appearance(False)
        home = controller.dynamic["home_cluster"]
        require(
            not controller.setup_mode
            and not bool(controller.sidebar_item.isCollapsed())
            and not bool(controller.title_strip.isHidden())
            and not bool(home.isHidden()) and bool(setup.isHidden()),
            "finishing setup leaves setup mode and shows Home")
        require(
            not [item for item in controller.title_items
                 if item[0] == "home"]
            and all(bool(child.isKindOfClass_(FlowView))
                    for child in controller.pages["Home"].subviews()),
            "Home has no title-strip item and no backdrop of its own: the "
            "page holds Home and setup and nothing else")

        # The stage: the stage colour for the face, outlined, on its hard
        # shadow, as wide as the column.
        stage = controller.dynamic["home_stage"]
        stage_shape = BRAND_SHAPES["stage"]
        stage_frame = frame_in_window(stage)
        column_frame = frame_in_window(controller.dynamic["page_column"])
        require(
            bool(stage.isKindOfClass_(StagePanelView))
            and stage.stage_token == stage_token_for_face(model.state.face)
            and abs(stage_frame.size.width - column_frame.size.width) < 0.5
            and abs(stage_frame.size.height - STAGE_HEIGHT
                    - stage_shape.shadow_down) < 0.5
            and abs(stage_frame.origin.y + stage_frame.size.height
                    - column_frame.origin.y - column_frame.size.height)
            < 0.5,
            "Home opens with the stage, across the top of the column")
        stage_rep = rendered(stage)
        stage_color = color_token(stage.stage_token, False).color
        require(
            near(pixel(stage_rep, 300.0, STAGE_HEIGHT - 8.0), stage_color)
            and near(pixel(stage_rep, 0.75, STAGE_HEIGHT / 2.0), light.line)
            and near(pixel(stage_rep, stage_frame.size.width / 2.0,
                           stage_frame.size.height - 0.75), light.line),
            "the stage is its stage colour, ink-outlined, on a hard shadow")

        # The face: alive, about 200 points, leaning inside its own view
        # (never a rotated frame), on its hard shadow, without speech puffs.
        face_view = controller.dynamic["home_face_view"]
        face_frame = face_view.frame()
        require(
            face_view.superview() == stage
            and bool(face_view.isKindOfClass_(LiveFaceView))
            and face_view.shadow and not face_view.whispers
            and abs(face_view.tilt - STAGE_FACE_TILT) < 1e-6
            and abs(float(face_view.frameCenterRotation())) < 1e-6
            and abs(face_view.art_side() - STAGE_FACE_SIZE) < 0.5
            and abs(face_frame.size.width - STAGE_FACE_SIZE
                    - 2.0 * STAGE_FACE_BLEED) < 0.5
            # The lean's reach at the corners plus the widest squash.
            and STAGE_FACE_BLEED / STAGE_FACE_SIZE >= 0.03 + 0.0275,
            "the stage face is 200 points, leaning in its own view with "
            "clear room on every side")
        face_points = [
            (float(x), float(y))
            for x in range(10, int(face_frame.size.width), 14)
            for y in range(10, int(face_frame.size.height), 14)]
        leaning = rendered(face_view)
        face_view.setTilt_(0.0)
        upright = rendered(face_view)
        face_view.setTilt_(STAGE_FACE_TILT)
        face_view.setShadow_(False)
        bare = rendered(face_view)
        face_view.setShadow_(True)
        require(
            sum(not near(pixel(leaning, x, y), pixel(upright, x, y), 0.05)
                for x, y in face_points) >= 8,
            "the stage face is drawn leaning")
        require(
            sum(not near(pixel(leaning, x, y), pixel(bare, x, y), 0.05)
                for x, y in face_points) >= 4,
            "the stage face is drawn with its hard shadow")
        edge = float(face_frame.size.width) - 0.75
        require(
            all(pixel(leaning, along, across)[3] < 0.02
                and pixel(leaning, across, along)[3] < 0.02
                for along in range(0, int(face_frame.size.width), 3)
                for across in (0.75, edge)),
            "the lean and the shadow are never clipped by the face's view")

        # The bubble: porcelain, outlined, on its hard shadow, with a tail
        # toward the face, and the old capitals said only to VoiceOver.
        bubble = controller.dynamic["home_bubble"]
        bubble_shape = BRAND_SHAPES["bubble"]
        status = controller.dynamic["overview_status"]
        detail = controller.dynamic["overview_detail"]
        bubble_actions = controller.dynamic["overview_actions"]
        require(
            bool(bubble.isKindOfClass_(SpeechBubbleView))
            and all(view.superview() == bubble for view in (
                status, detail, bubble_actions,
                controller.dynamic["overview_phase"]))
            and bool(controller.dynamic["overview_phase"].isHidden())
            and accessible_value(bubble, "accessibilityLabel")
            == localized_string("overview.accessibility.phase")
            and accessible_value(bubble, "accessibilityValue")
            == localized_string("overview.phase.ready"),
            "the bubble holds the status copy; the phase label is not "
            "drawn and still reaches VoiceOver")
        bubble_rep = rendered(bubble)
        bubble_body = bubble.body_rect()
        low = bubble_body.origin.y + bubble_body.size.height
        require(
            near(pixel(bubble_rep, bubble_body.origin.x + 8.0, low - 34.0),
                 light.surface)
            and near(pixel(bubble_rep, bubble_body.origin.x + 0.75,
                           low - 34.0), light.line)
            and near(pixel(bubble_rep,
                           bubble_body.origin.x
                           + bubble_body.size.width / 2.0,
                           low + bubble_shape.shadow_down - 0.75),
                     light.line),
            "the bubble is porcelain, ink-outlined, on a hard shadow")
        require(
            pixel(bubble_rep, bubble_body.origin.x - bubble.TAIL_LENGTH
                  + 4.0, bubble_body.origin.y + bubble.tail_center)[3] > 0.9
            and pixel(bubble_rep, bubble_body.origin.x - 5.0,
                      bubble_body.origin.y + bubble.tail_center
                      - bubble.TAIL_HALF - 4.0)[3] < 0.05
            and frame_in_window(face_view).origin.x
            < frame_in_window(bubble).origin.x,
            "the bubble's tail points at the face beside it")
        require(
            str(status.stringValue()) == model.state.status_title
            and abs(float(status.font().pointSize())
                    - TYPE_SPECS["bubble_headline"].size) < 0.01
            and accessible_value(status, "accessibilityLabel")
            == localized_string("overview.accessibility.status"),
            "the status is the bubble's one heavy headline")

        # One instruction line, showing the capture key that is bound.
        def key_cap_in(line: Any, key: str, sentence: str) -> bool:
            text = line.attributedStringValue()
            plain = str(text.string())
            if (key in plain or plain.count("￼") != 1
                    or plain.replace("￼", key) != sentence):
                return False
            attachment, _range = text.attribute_atIndex_effectiveRange_(
                "NSAttachment", plain.index("￼"), None)
            size = attachment.bounds().size
            return (abs(size.width - key_cap_size(key)[0]) < 0.5
                    and abs(size.height - key_cap_size(key)[1]) < 0.5)

        require(
            model.state.hotkey_label in model.state.status_detail
            and key_cap_in(detail, model.state.hotkey_label,
                           model.state.status_detail)
            and accessible_value(detail.cell(), "accessibilityValue")
            == model.state.status_detail,
            "the instruction shows the bound capture key as a key cap, and "
            "VoiceOver still hears the key's name")
        home_state = model.state
        model.state = replace(
            home_state, hotkey="f13", hotkey_label="F13",
            status_detail=localized_string(
                "overview.status.ready.detail", hotkey="F13"))
        controller.render()
        require(
            key_cap_in(detail, "F13", model.state.status_detail),
            "the key cap follows the key that is bound, not Right Option")
        model.state = home_state
        controller.render()
        root_view.layoutSubtreeIfNeeded()

        # The primary action: an amber chunky button on its hard shadow,
        # whose label reaches the cell VoiceOver reads, and which travels
        # onto its shadow while it is pressed.
        pause = controller.dynamic["pause_button"]
        button_shape = BRAND_SHAPES["chunky_button"]
        require(
            all(bool(controller.dynamic[key].isKindOfClass_(ChunkyButton))
                and controller.dynamic[key].superview() == bubble_actions
                for key in ("pause_button", "review_issue_button",
                            "copy_outbox_button"))
            and pause.variant == "amber"
            and controller.dynamic["review_issue_button"].variant == "plain"
            and controller.dynamic["copy_outbox_button"].variant == "plain",
            "the bubble's actions are chunky buttons, one of them amber")
        pause_rep = rendered(pause)
        pause_body = pause.body_rect()
        middle = pause_body.size.height / 2.0
        require(
            abs(pause.bounds().size.height - button_shape.height
                - button_shape.shadow_down) < 0.5
            and near(pixel(pause_rep, pause_body.origin.x + 6.0, middle),
                     light.accent)
            and near(pixel(pause_rep, 0.75, middle), light.line)
            and near(pixel(pause_rep, pause_body.size.width / 2.0,
                           pause.bounds().size.height - 0.75), light.line),
            "the primary action is amber, ink-outlined, on a hard shadow")
        require(
            accessible_value(pause.cell(), "accessibilityLabel")
            == localized_string(
                "overview.action.pause.label",
                action=localized_string("overview.action.pause"))
            and accessible_value(pause.cell(), "accessibilityHelp")
            == localized_string("overview.action.pause.help")
            and accessible_value(pause.cell(), "accessibilityValue")
            == localized_string("overview.action.pause.state.active"),
            "the button's label, help and state reach the cell")
        pause.highlight_(True)
        pressed = pause.is_pressed()
        pressed_body = pause.body_rect()
        pressed_rep = rendered(pause)
        pause.highlight_(False)
        require(
            pressed and not pause.is_pressed()
            and abs(pressed_body.origin.x
                    - (button_shape.shadow_right - 1.0)) < 0.01
            and abs(pressed_body.origin.y
                    - (button_shape.shadow_down - 1.0)) < 0.01
            and pixel(pressed_rep, 0.75, middle)[3] < 0.05
            and near(pixel(pressed_rep, pressed_body.origin.x + 6.0,
                           pressed_body.origin.y + middle), light.accent),
            "a pressed chunky button travels onto its shadow")

        # The day's numbers: three opaque stickers in the three tints,
        # each drawn at its own tilt.
        tiles = controller.dynamic["home_stickers"]
        require(
            len(tiles) == 3 and all(
                bool(tile.isKindOfClass_(StickerTileView))
                and tile.index == index
                for index, tile in enumerate(tiles)),
            "the three numbers are sticker tiles")
        for index, (tile, label_key) in enumerate(zip(tiles, (
                "overview.accessibility.last",
                "overview.accessibility.words",
                "overview.accessibility.saved"))):
            tile_rep = rendered(tile)
            tile_size = tile.bounds().size
            tile_width, tile_height = tile.tile_size()
            lean = math.radians(tile.tilt())
            reach = tile_width / 2.0 - 20.0
            top = (tile_size.height / 2.0
                   - tile_height / 2.0 * math.cos(lean))
            # The tile's top edge at each end, and a row between them:
            # only the end the tilt lifts is inside the tile there.
            ends = (top - reach * math.sin(lean),
                    top + reach * math.sin(lean))
            row = sum(ends) / 2.0
            left = pixel(tile_rep, tile_size.width / 2.0 - reach, row)[3]
            right = pixel(tile_rep, tile_size.width / 2.0 + reach, row)[3]
            lifted, dropped = (left, right) if tile.tilt() > 0.0 else (
                right, left)
            require(
                abs(tile.tilt() - STICKER_TILTS[index]) < 1e-6
                and lifted > 0.6 and dropped < 0.4,
                f"sticker {index} is drawn at its tilt")
            require(
                near(pixel(tile_rep, tile_size.width - 40.0,
                           tile_size.height / 2.0),
                     color_token(STICKER_TINTS[index], False).color)
                and pixel(tile_rep, tile_size.width - 40.0,
                          tile_size.height / 2.0)[3] > 0.99,
                f"sticker {index} is opaque, in its tint")
            require(
                accessible_value(tile, "accessibilityLabel")
                == localized_string(label_key)
                and accessible_value(tile, "accessibilityValue")
                .startswith(tile.number),
                f"sticker {index} keeps its VoiceOver label and value")
        # One big number each. The last dictation's sticker shows how long
        # it took and leaves the word count to the row under it and to
        # VoiceOver; thousands are grouped the same way on every sticker.
        sticker_numbers = {
            key: runtime.get(key) for key in (
                "last_latency_ms", "last_word_count", "words_today",
                "minutes_saved")}
        runtime.update(
            last_latency_ms=930.0, last_word_count=9, words_today=123456,
            minutes_saved=1234.0)
        model.refresh()
        controller.render()
        require(
            [tile.number for tile in tiles] == [
                localized_string(
                    "overview.metric.last.value", seconds="0.93", words=""),
                localized_string(
                    "overview.metric.words.value", count="123,456"),
                localized_string(
                    "overview.metric.saved.value", minutes="1,234")]
            and not any(
                separator in tiles[0].number for separator in "·,; ")
            and accessible_value(tiles[0], "accessibilityValue")
            == localized_string(
                "overview.metric.last.value", seconds="0.93",
                words=localized_string(
                    "overview.metric.last.words.many", count=9))
            and all(
                tile.number_font(
                    tile.tile_size()[0] - 8.0
                    - 2.0 * BRAND_SHAPES["sticker"].padding_x)
                .pointSize() == TYPE_SPECS["stat_number"].size
                for tile in tiles),
            "each sticker shows one number at full size; VoiceOver still "
            "hears how many words the last dictation was")
        for key, value in sticker_numbers.items():
            if value is None:
                runtime.pop(key, None)
            else:
                runtime[key] = value
        model.refresh()
        controller.render()

        # Every state Home can be in: the bubble carries the primary
        # action and at most one more, no action is out of reach, and
        # nothing is cut short, overlapping or outside the page.
        attention_issue = DegradedIssue(
            "microphone", localized_string("issue.microphone.title"),
            localized_string("issue.microphone.detail"))

        def phase(name: str, detail_key: str = "", **changes: Any
                  ) -> dict[str, Any]:
            return {
                "status_phase": name,
                "status_title": localized_string(
                    f"overview.status.{name}.title"),
                "status_detail": changes.pop("status_detail", None)
                or localized_string(
                    detail_key or f"overview.status.{name}.detail",
                    hotkey=home_state.hotkey_label),
                **changes}

        outbox = {"outbox_count": 2, "outbox_summary": localized_string(
            "overview.outbox.summary.not_pasted")}
        home_states = (
            ("ready", phase("ready"), ("pause_button",), False),
            ("paused", phase("paused", paused=True),
             ("pause_button",), False),
            ("listening", phase("recording"), ("pause_button",), False),
            ("working", phase("processing"), ("pause_button",), False),
            ("starting", phase("starting"), ("pause_button",), False),
            ("the Voice Outbox holding text",
             phase("recovery", "overview.status.recovery.detail.one",
                   **outbox),
             ("pause_button", "copy_outbox_button"), False),
            ("needs attention",
             phase("degraded", status_detail=attention_issue.detail,
                   degraded_issues=(attention_issue,)),
             ("pause_button", "review_issue_button"), False),
            ("the outbox and a setup item together",
             phase("recovery", "overview.status.recovery.detail.one",
                   degraded_issues=(attention_issue,), **outbox),
             ("pause_button", "copy_outbox_button"), True),
        )
        review_row = controller.dynamic["review_issue_row_button"]
        for name, changes, in_bubble, review_in_row in home_states:
            model.state = replace(home_state, **changes)
            controller.render()
            root_view.layoutSubtreeIfNeeded()
            shown = tuple(
                key for key in ("pause_button", "review_issue_button",
                                "copy_outbox_button")
                if not bool(controller.dynamic[key].isHidden()))
            require(
                shown == in_bubble == tuple(
                    {"pause": "pause_button", "review": "review_issue_button",
                     "copy_outbox": "copy_outbox_button"}[action]
                    for action in home_bubble_actions(model.state))
                and len(shown) <= 2,
                f"Home, {name}: the bubble has the primary action and at "
                "most one secondary")
            room = bubble.convertRect_toView_(bubble.content_rect(), None)
            frames = [frame_in_window(controller.dynamic[key])
                      for key in shown]
            require(
                frames[0].origin.x >= room.origin.x - 0.5
                and all(first.origin.x + first.size.width + 8.0
                        <= second.origin.x
                        for first, second in zip(frames, frames[1:]))
                and frames[-1].origin.x + frames[-1].size.width
                <= room.origin.x + room.size.width + 0.5,
                f"Home, {name}: the bubble's buttons stand apart inside it")
            require(
                bool(controller.dynamic["home_attention_row"].isHidden())
                != review_in_row
                and str(review_row.action()) == "reviewIssue:"
                and (not review_in_row or str(controller.dynamic[
                    "home_attention_title"].stringValue())
                    == attention_issue.title),
                f"Home, {name}: Review Setup is a row exactly when the "
                "bubble has no room for it")
            reachable = key_views_from(controller.sidebar_rows[0])
            require(
                all(controller.dynamic[key] in reachable for key in shown)
                and (review_row in reachable) == review_in_row
                and bool(home_review_due(model.state)) == (
                    "review_issue_button" in shown or review_in_row),
                f"Home, {name}: every action it offers is in the Tab loop")
            laid_out(home, f"Home, {name}")
            require(
                len(check_voiceover(f"Home, {name}", home)) >= 15,
                f"Home, {name}: VoiceOver")
        require(
            [accessible_value(controller.dynamic[key].cell(),
                              "accessibilityLabel")
             for key in ("review_issue_row_button", "result_inspect_button",
                         "result_play_audio_button",
                         "result_clear_audio_button")]
            == [localized_string("overview.action.review"),
                localized_string("results.inspect.action"),
                localized_string("results.audio.play"),
                localized_string("home.replay.clear.action")]
            and all(controller.dynamic[key].image() is not None
                    for key in ("result_inspect_button",
                                "result_play_audio_button",
                                "result_clear_audio_button")),
            "Home's row buttons are named by their words, never by the "
            "symbol beside them")
        model.state = replace(home_state, **phase("paused", paused=True))
        controller.render()
        require(
            str(pause.title()) == localized_string("overview.action.resume")
            and accessible_value(pause.cell(), "accessibilityValue")
            == localized_string("overview.action.pause.state.paused"),
            "the primary action says Resume while dictation is paused")
        model.state = home_state
        controller.window.setAppearance_(None)
        controller.render()
        root_view.layoutSubtreeIfNeeded()
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

        # Every row, pressed the way a click presses it, shows its page
        # and its pane, names it in the title strip, and is the one row
        # the sidebar marks selected. Privacy is pressed last on purpose,
        # so the walk ends in Settings on its last pane.
        visited_sections = []
        for index in (0, 4, 1, 2, 3):
            spec = SIDEBAR_ROWS[index]
            controller.sidebar_rows[index].performClick_(None)
            visible = tuple(
                name for name, page in controller.pages.items()
                if not bool(page.isHidden()))
            require(
                model.state.section == spec.section,
                f"section {spec.section}")
            require(visible == (spec.section,), f"render {spec.section}")
            visited_sections.append(spec.section)
            if spec.pane is not None:
                visible_panes = tuple(
                    name for name, view in
                    controller.dynamic["settings_panes"].items()
                    if not bool(view.isHidden()))
                require(
                    model.state.settings_pane == spec.pane,
                    f"pane {spec.pane}")
                require(
                    visible_panes == (spec.pane,),
                    f"render pane {spec.pane}")
            require(
                sidebar_row_index(
                    model.state.section, model.state.settings_pane) == index
                and [bool(row.selected) for row in controller.sidebar_rows]
                == [position == index for position in range(
                    len(SIDEBAR_ROWS))],
                f"the selected row follows the view model to {spec.key}")
            require(
                str(controller.dynamic["page_title"].stringValue()) ==
                localized_string(spec.label_key),
                f"title strip names {spec.key}")
        require(set(visited_sections) == set(SECTIONS), "every section shown")
        # What the rows draw: only the selected one is filled, with the
        # theme's selection in the key window and a neutral out of it.
        def row_edge_pixel(row: Any) -> tuple[float, float, float, float]:
            bounds = row.bounds()
            bitmap = row.bitmapImageRepForCachingDisplayInRect_(bounds)
            row.cacheDisplayInRect_toBitmapImageRep_(bounds, bitmap)
            scale = bitmap.pixelsWide() / bounds.size.width
            color = bitmap.colorAtX_y_(
                int(4 * scale), int(bounds.size.height / 2.0 * scale))
            return (float(color.redComponent()),
                    float(color.greenComponent()),
                    float(color.blueComponent()),
                    float(color.alphaComponent()))

        selected_pixel = row_edge_pixel(controller.sidebar_rows[3])
        require(
            selected_pixel[3] > 0.2
            and selected_pixel[1] > selected_pixel[0] + 0.1,
            "the selected row is filled with the theme's selection")
        require(
            row_edge_pixel(controller.sidebar_rows[0])[3] < 0.02,
            "an unselected row draws no fill")
        controller.windowDidResignKey_(None)
        dimmed_pixel = row_edge_pixel(controller.sidebar_rows[3])
        require(
            0.02 < dimmed_pixel[3] < selected_pixel[3]
            and abs(dimmed_pixel[1] - dimmed_pixel[0]) < 0.15,
            "the selection is a quiet neutral out of the key window")
        controller.windowDidBecomeKey_(None)
        # The selection is read from the view model, not kept by the rows:
        # changing the model alone moves it on the next render.
        model.select_settings_pane("Controls")
        controller.render()
        require(
            [bool(row.selected) for row in controller.sidebar_rows] ==
            [False, False, True, False, False],
            "the sidebar is a projection of the view model")
        model.select_settings_pane("Privacy")
        controller.render()
        # Up and Down move through the rows and stop at the ends. The key
        # events are handed to the focused row itself, as the window would
        # hand them; they are made here and never posted to the system.
        def arrow(row: Any, key_code: int) -> None:
            key_down = 10  # NSEventTypeKeyDown
            row.keyDown_(NSEvent
                         .keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(  # noqa: E501
                             key_down, (0.0, 0.0), 0, 0.0,
                             window.windowNumber(), None, "", "", False,
                             key_code))

        down = SidebarRowButton.KEY_DOWN_ARROW
        up = SidebarRowButton.KEY_UP_ARROW
        arrow(controller.sidebar_rows[3], down)
        require(
            model.state.section == "Advanced"
            and bool(controller.sidebar_rows[4].selected)
            and window.firstResponder() == controller.sidebar_rows[4],
            "Down moves to the next row and shows its page")
        arrow(controller.sidebar_rows[4], down)
        require(
            model.state.section == "Advanced"
            and not controller.move_sidebar_focus(
                controller.sidebar_rows[4], 1),
            "Down stops at the last row")
        arrow(controller.sidebar_rows[4], up)
        require(
            model.state.section == "Settings"
            and model.state.settings_pane == "Privacy"
            and window.firstResponder() == controller.sidebar_rows[3],
            "Up moves to the previous row and shows its page")
        require(
            not controller.move_sidebar_focus(controller.sidebar_rows[0], -1),
            "Up stops at the first row")
        # A window that is not the key window dims its selection.
        controller.windowDidResignKey_(None)
        require(
            not any(bool(row.emphasized) for row in controller.sidebar_rows)
            and bool(controller.sidebar_rows[3].selected),
            "an inactive window keeps its selection and dims it")
        controller.windowDidBecomeKey_(None)
        require(
            all(bool(row.emphasized) for row in controller.sidebar_rows),
            "the key window emphasizes its selection again")
        require(
            int(controller._configure_key_view_loop(model.state)) >= 4,
            "settings key-view loop")
        # Tab goes to the sidebar, then into the page. The rows are linked
        # in order and only the selected one may be a stop, so the sidebar
        # is one stop like a list. Whether buttons take Tab stops at all is
        # the system's keyboard-navigation setting, read here from another
        # button in the sidebar.
        for upper, lower in zip(
                controller.sidebar_rows, controller.sidebar_rows[1:]):
            require(upper.nextKeyView() == lower, "Tab walks the sidebar")
        buttons_take_tab = bool(
            controller.dynamic["window_face_button"].canBecomeKeyView())
        require(
            [bool(row.canBecomeKeyView()) for row in controller.sidebar_rows]
            == [False, False, False, buttons_take_tab, False]
            and [row.takes_tab_stop(True) for row in controller.sidebar_rows]
            == [False, False, False, True, False]
            and not any(row.takes_tab_stop(False)
                        for row in controller.sidebar_rows),
            "the selected row is the sidebar's one Tab stop")
        require(
            controller.window.initialFirstResponder() ==
            controller.sidebar_rows[3],
            "keyboard entry follows the selected row")
        require(
            controller.sidebar_rows[-1].nextKeyView() ==
            controller.dynamic["settings_key_views"]["Privacy"][0],
            "settings Tab order")
        # A trailing item in the title strip belongs to one page: it shows
        # only there, lines up on the column's trailing edge inside the
        # strip, and takes the Tab stop between the sidebar and the page.
        probe_item = NSButton.alloc().initWithFrame_(
            NSMakeRect(0, 0, 96, 24))
        probe_item.setTitle_("Probe")
        probe_item.setBezelStyle_(NSBezelStyleRounded)
        outbox_only = controller.add_title_item(
            "privacy", NSButton.alloc().initWithFrame_(
                NSMakeRect(0, 0, 96, 24)),
            visible=lambda state: False)
        controller.add_title_item("privacy", probe_item)
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        item_frame = in_window(probe_item)
        # The stack's frame is its items' alignment rectangles: the edge
        # of the bezel, not of the shadow room around it.
        items_frame = in_window(controller.dynamic["title_strip_items"])
        strip_frame = in_window(controller.title_strip)
        column_frame = in_window(controller.dynamic["page_column"])
        require(
            not bool(probe_item.isHidden())
            and bool(outbox_only.isHidden())
            and probe_item.superview() ==
            controller.dynamic["title_strip_items"]
            and items_frame.size.width > 0.0
            and abs(items_frame.origin.x + items_frame.size.width
                    - column_frame.origin.x - column_frame.size.width) < 0.5
            and strip_frame.origin.y <= item_frame.origin.y
            and item_frame.origin.y + item_frame.size.height
            <= strip_frame.origin.y + strip_frame.size.height,
            "a title-strip item sits trailing, inside the strip")
        require(
            controller.sidebar_rows[-1].nextKeyView() == probe_item
            and probe_item.nextKeyView() ==
            controller.dynamic["settings_key_views"]["Privacy"][0],
            "Tab goes sidebar, title strip, page")
        try:
            controller.add_title_item("settings", probe_item)
        except ValueError:
            pass
        else:
            raise AssertionError("a title item needs a real sidebar row")
        controller.sidebar_rows[2].performClick_(None)
        require(
            bool(probe_item.isHidden())
            and controller.sidebar_rows[-1].nextKeyView() ==
            controller.dynamic["settings_key_views"]["Controls"][0],
            "a title-strip item leaves with its page")
        for _row_key, item, _visible in controller.title_items:
            item.removeFromSuperview()
        controller.title_items.clear()

        # Setup mode: the sidebar and the title strip go, the page column
        # takes the height they held and the whole window's width to sit
        # in, and leaving it restores the shell with the same row selected.
        normal_column = in_window(controller.dynamic["page_column"])
        controller.set_setup_mode(True)
        root_view.layoutSubtreeIfNeeded()
        setup_column = in_window(controller.dynamic["page_column"])
        setup_content = in_window(controller.dynamic["content_side"])
        require(
            controller.setup_mode
            and bool(controller.sidebar_item.isCollapsed())
            and bool(controller.title_strip.isHidden()),
            "setup mode hides the sidebar and the title strip")
        require(
            abs(setup_content.origin.x) < 0.5
            and abs(setup_content.size.width
                    - root_view.bounds().size.width) < 0.5,
            "setup mode gives the content side the whole window")
        title_bar = float(
            window.frame().size.height
            - window.contentLayoutRect().size.height)
        require(
            abs(setup_column.origin.y + setup_column.size.height
                - (content_height - title_bar)) < 0.5
            and abs(setup_column.origin.y - normal_column.origin.y) < 0.5
            and abs(setup_column.size.width - normal_column.size.width) < 0.5
            and abs(setup_column.origin.x + setup_column.size.width / 2.0
                    - root_view.bounds().size.width / 2.0) < 0.5,
            "setup mode runs the column from under the title bar, centred")
        for light in lights:
            require(
                not intersects(in_window(light), setup_column),
                "the setup page stays clear of the traffic lights")
        require(
            not any(row in key_loop_from(
                controller.dynamic["settings_key_views"]["Controls"][0])
                for row in controller.sidebar_rows)
            and not any(bool(row.canBecomeKeyView())
                        for row in controller.sidebar_rows)
            and controller.window.initialFirstResponder() ==
            controller.dynamic["settings_key_views"]["Controls"][0],
            "setup mode leaves the hidden sidebar out of the Tab loop")
        controller.set_setup_mode(False)
        root_view.layoutSubtreeIfNeeded()
        restored_column = in_window(controller.dynamic["page_column"])
        require(
            not controller.setup_mode
            and not bool(controller.sidebar_item.isCollapsed())
            and not bool(controller.title_strip.isHidden())
            and abs(restored_column.origin.x - normal_column.origin.x) < 0.5
            and abs(restored_column.size.height
                    - normal_column.size.height) < 0.5
            and bool(controller.sidebar_rows[2].selected)
            and controller.sidebar_rows[-1].nextKeyView() ==
            controller.dynamic["settings_key_views"]["Controls"][0],
            "leaving setup mode restores the shell and its selected row")
        controller.sidebar_rows[3].performClick_(None)
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
            localized_string("settings.personalize.modes.detail",
                             hotkey=runtime["hotkey_label"]),
            "voice modes summary")
        require(
            all(chip in
                controller.dynamic["settings_key_views"]["Personalize"]
                for chip in controller.dynamic["face_picker"].chips),
            "face picker chips are Personalize key views")
        # Controls: the capture key, the undo key, and the sound setting are
        # all reachable, all labelled, and all in the key-view loop.
        require(
            str(controller.dynamic["settings_hotkey_button"].action()) ==
            "changeHotkey:", "capture key picker surface")
        require(
            str(controller.dynamic["settings_undo_hotkey_button"].action()) ==
            "changeUndoHotkey:", "undo key picker surface")
        require(
            str(controller.dynamic["settings_hotkey_detail"].stringValue()) ==
            localized_string("settings.controls.hotkey.detail",
                             hotkey=runtime["hotkey_label"]),
            "capture key summary names the bound key")
        require(
            str(controller.dynamic[
                "settings_undo_hotkey_detail"].stringValue()) ==
            localized_string("settings.controls.undo.unset"),
            "undo key summary reports an unbound key")
        require(
            int(controller.dynamic["sound_theme_control"].segmentCount()) ==
            len(SOUND_THEMES), "sound theme segments")
        require(
            bool(controller.dynamic["sound_preview_button"].isEnabled()),
            "sound preview available while a sound is chosen")
        require(
            not bool(controller.dynamic[
                "recent_dictations_browse"].isEnabled()),
            "recent dictations stay unbrowsable while off")

        def window_actions(view: Any) -> set[str]:
            found = set()
            if view.respondsToSelector_("action") and view.action():
                found.add(str(view.action()))
            for child in view.subviews():
                found |= window_actions(child)
            return found

        # Undo and re-insert act on the frontmost app's focused field. From a
        # window that is itself frontmost they can only act on the window,
        # so they live in the menu bar and on the bound key instead.
        require(
            not window_actions(controller.window.contentView()) & {
                "undoLastDictation:", "insertRecentDictation:"},
            "no window control acts on the frontmost app")
        for pane_name in SETTINGS_PANES:
            require(
                len(controller.dynamic["settings_key_views"][pane_name]) >= 2,
                f"{pane_name} key views")
        require(
            accessible_value(
                controller.dynamic["sound_theme_control"],
                "accessibilityLabel") == localized_string(
                    "settings.accessibility.sound.label"),
            "sound control accessibility label")
        require(
            accessible_value(
                controller.dynamic["recent_dictations_toggle"],
                "accessibilityLabel") == localized_string(
                    "settings.accessibility.recent.label"),
            "recent dictations accessibility label")
        self_correction_toggle = controller.dynamic["self_correction_toggle"]
        require(
            accessible_value(self_correction_toggle, "accessibilityLabel")
            == localized_string(
                "settings.accessibility.self_correction.label")
            and self_correction_toggle in controller.dynamic[
                "settings_key_views"]["Controls"],
            "the actually switch is labelled and reachable by keyboard")
        require(
            int(self_correction_toggle.state()) == int(NSControlStateValueOff)
            and str(controller.dynamic["self_correction_detail"]
                    .stringValue()) == localized_string(
                        "settings.controls.self_correction.off"),
            "the actually correction starts off and says so")
        self_correction_toggle.setState_(NSControlStateValueOn)
        controller.selfCorrectionChanged_(self_correction_toggle)
        require(
            ("self_correction", True) in calls
            and model.state.settings.self_correction
            and int(self_correction_toggle.state()) ==
            int(NSControlStateValueOn)
            and str(controller.dynamic["self_correction_detail"]
                    .stringValue()) == localized_string(
                        "settings.controls.self_correction.on")
            and accessible_value(
                self_correction_toggle, "accessibilityValue") ==
            localized_string("settings.state.enabled"),
            "the actually switch turns the correction on")
        self_correction_toggle.setState_(NSControlStateValueOff)
        controller.selfCorrectionChanged_(self_correction_toggle)
        require(not model.state.settings.self_correction,
                "the actually switch turns the correction off")
        # The smoke's settings file predates the key, as an install from
        # before the switch does: the repair is on.
        repeats_toggle = controller.dynamic["remove_repeats_toggle"]
        require(
            accessible_value(repeats_toggle, "accessibilityLabel")
            == localized_string(
                "settings.accessibility.remove_repeats.label")
            and repeats_toggle in controller.dynamic[
                "settings_key_views"]["Controls"],
            "the repeated words switch is labelled and reachable by keyboard")
        require(
            "remove_repeats" not in private_settings
            and int(repeats_toggle.state()) == int(NSControlStateValueOn)
            and str(controller.dynamic["remove_repeats_detail"]
                    .stringValue()) == localized_string(
                        "settings.controls.remove_repeats.on"),
            "the repeated words repair starts on and says so")
        repeats_toggle.setState_(NSControlStateValueOff)
        controller.removeRepeatsChanged_(repeats_toggle)
        require(
            ("remove_repeats", False) in calls
            and not model.state.settings.remove_repeats
            and int(repeats_toggle.state()) == int(NSControlStateValueOff)
            and str(controller.dynamic["remove_repeats_detail"]
                    .stringValue()) == localized_string(
                        "settings.controls.remove_repeats.off")
            and accessible_value(repeats_toggle, "accessibilityValue") ==
            localized_string("settings.state.disabled"),
            "the repeated words switch turns the repair off")
        repeats_toggle.setState_(NSControlStateValueOn)
        controller.removeRepeatsChanged_(repeats_toggle)
        require(model.state.settings.remove_repeats
                and private_settings["remove_repeats"] is True,
                "the repeated words switch turns the repair back on")
        # New speech runtime: off for a new install, labelled, reachable by
        # keyboard on Advanced, and its caption names which program answered
        # the last dictation once the runtime says so.
        speech_toggle = controller.dynamic["new_speech_runtime_toggle"]
        speech_detail = controller.dynamic["new_speech_runtime_detail"]
        require(
            accessible_value(speech_toggle, "accessibilityLabel")
            == localized_string("models.accessibility.speech_runtime")
            and speech_toggle in controller.key_views_by_section["Advanced"],
            "the new speech runtime switch is labelled and reachable by "
            "keyboard")
        require(
            int(speech_toggle.state()) == int(NSControlStateValueOff)
            and str(speech_detail.stringValue()) == localized_string(
                "models.speech_runtime.off"),
            "the new speech runtime starts off and says so")
        speech_toggle.setState_(NSControlStateValueOn)
        controller.newSpeechRuntimeChanged_(speech_toggle)
        require(
            ("new_speech_runtime", True) in calls
            and model.state.new_speech_runtime
            and int(speech_toggle.state()) == int(NSControlStateValueOn)
            and str(speech_detail.stringValue()) == localized_string(
                "models.speech_runtime.on")
            and accessible_value(speech_toggle, "accessibilityValue")
            == localized_string("settings.state.enabled"),
            "the new speech runtime switch turns it on")
        for last in ("new", "previous"):
            runtime["new_speech_runtime_last"] = last
            model.refresh()
            controller.render()
            require(
                str(speech_detail.stringValue()) == localized_string(
                    f"models.speech_runtime.last.{last}",
                    caption=localized_string("models.speech_runtime.on")),
                f"the new speech runtime caption names the {last} program")
        # Still loading in the background: the caption says it is getting
        # ready, in place of the last program, and stops once it is ready.
        runtime["new_speech_runtime_preparing"] = True
        model.refresh()
        controller.render()
        require(
            str(speech_detail.stringValue()) == localized_string(
                "models.speech_runtime.preparing",
                caption=localized_string("models.speech_runtime.on")),
            "the new speech runtime caption says it is getting ready")
        runtime.pop("new_speech_runtime_preparing")
        model.refresh()
        controller.render()
        require(
            str(speech_detail.stringValue()) == localized_string(
                "models.speech_runtime.last.previous",
                caption=localized_string("models.speech_runtime.on")),
            "the new speech runtime caption drops getting ready once ready")
        runtime.pop("new_speech_runtime_last")
        speech_toggle.setState_(NSControlStateValueOff)
        controller.newSpeechRuntimeChanged_(speech_toggle)
        require(
            not model.state.new_speech_runtime
            and not runtime["new_speech_runtime"]
            and str(speech_detail.stringValue()) == localized_string(
                "models.speech_runtime.off"),
            "the new speech runtime switch turns it back off")
        require(
            hotkey_name_for_keycode(61) == "alt_r"
            and hotkey_name_for_keycode(0) == ""
            and hotkey_name_for_keycode(None) == "",
            "key capture guard")
        # Drive the seams the pickers use, without opening a modal dialog.
        model.choose_hotkey("f13")
        require(model.state.hotkey_label == "F13", "capture key rebinds")
        require(("hotkey", "f13") in calls, "capture key reaches the runtime")
        model.choose_hotkey("a")
        require(model.state.hotkey_label == "F13",
                "a typing key never becomes the capture key")
        require(model.state.notice_level == "error", "refusal is reported")
        model.choose_undo_hotkey("f14")
        require(model.state.undo_hotkey_label == "F14", "undo key binds")
        model.choose_undo_hotkey("")
        require(not model.state.undo_hotkey_label, "undo key clears")
        model.choose_sound_theme("silent")
        controller.render()
        require(
            not bool(controller.dynamic["sound_preview_button"].isEnabled()),
            "silence has nothing to preview")
        model.choose_sound_theme("whisper")
        model.set_recent_dictations(True)
        controller.render()
        require(
            bool(controller.dynamic["recent_dictations_browse"].isEnabled()),
            "recent dictations become browsable once enabled")
        require(
            len(model.recent_dictations()) == 3, "recent metadata reaches UI")
        recent_dialogs: list[Any] = []

        def reveal_last_row(alert: Any) -> int:
            recent_dialogs.append(alert)
            if len(recent_dialogs) == 1:
                alert.accessoryView().selectItemAtIndex_(2)
            return 1000

        controller.modal_responder = reveal_last_row
        controller.browseRecentDictations_(None)
        controller.modal_responder = None
        require(
            int(recent_dialogs[0].accessoryView().numberOfItems()) == 3,
            "every recent dictation is listed, even with equal rows")
        require(
            ("reveal_recent", "smoke-3") in calls,
            "recent dictation acts on the selected entry")
        require(
            localized_string("settings.dialog.recent.reveal") in [
                str(button.title()) for button in recent_dialogs[0].buttons()]
            and len(recent_dialogs[0].buttons()) == 2,
            "recent dictations offer reveal only")
        require(
            recent_dictation_row(model.recent_dictations()[0])
            == "1 min ago · 4 words · Mail", "recent row is metadata only")
        model.set_recent_dictations(False)
        require(model.recent_dictations() == (),
                "nothing is listed while the setting is off")
        model.choose_sound_theme("system")
        model.choose_hotkey("alt_r")
        controller.render()

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
            "selectSidebarRow:", "advanced shortcut action")
        require(
            controller.dynamic["advanced_button"] ==
            controller.sidebar_rows[-1]
            and sidebar_row_destination(int(
                controller.dynamic["advanced_button"].tag())) ==
            ("Advanced", None), "advanced shortcut destination")
        require(
            str(controller.dynamic["export_support_bundle_button"].action()) ==
            "exportSupportBundle:",
            "local support bundle action")
        require(
            str(controller.dynamic[
                "selective_relisten_toggle"].action()) ==
            "selectiveRelistenChanged:",
            "selective re-listen surface")
        # The Selective Re-listen switch stays disabled without evidence, so
        # the loop hands Tab to the first enabled Advanced control, the New
        # speech runtime switch, and from there to Run Verification.
        require(
            controller.sidebar_rows[-1].nextKeyView() ==
            controller.dynamic["new_speech_runtime_toggle"]
            and controller.dynamic["new_speech_runtime_toggle"]
            .nextKeyView() == controller.dynamic["verify_button"]
            and controller.dynamic["verify_button"].nextKeyView() ==
            controller.dynamic["open_log_button"],
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
            accessible_value(controller.sidebar_rows[0],
                             "accessibilityLabel") == localized_string(
                                 "nav.home"),
            "section accessibility")
        require(
            accessible_value(
                controller.dynamic["advanced_button"],
                "accessibilityHelp") == localized_string(
                    "advanced.accessibility.shortcut.help"),
            "advanced shortcut accessibility")
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

        _scroll, editor = controller._text_editor(
            NSMakeRect(0, 0, 240, 80), "fixture",
            label=localized_string("settings.dialog.snippet.value"),
            help_text=localized_string(
                "settings.dialog.snippet.value.help"))
        require(
            accessible_value(editor, "accessibilityLabel") ==
            localized_string("settings.dialog.snippet.value"),
            "editor accessibility")
        require(
            not bool(editor.isRichText())
            and not bool(editor.isAutomaticQuoteSubstitutionEnabled())
            and not bool(editor.isAutomaticDashSubstitutionEnabled())
            and not bool(editor.isAutomaticTextReplacementEnabled())
            and not bool(editor.isAutomaticSpellingCorrectionEnabled())
            and bool(editor.allowsUndo()),
            "editors keep exactly what was typed")
        menu_keys = {
            (str(item.keyEquivalent()), str(item.action()))
            for top in NSApplication.sharedApplication().mainMenu().itemArray()
            if top.submenu() is not None
            for item in top.submenu().itemArray()}
        require(
            {("w", "performClose:"), ("m", "performMiniaturize:"),
             ("z", "undo:"), ("x", "cut:"), ("c", "copy:"),
             ("v", "paste:"), ("a", "selectAll:")} <= menu_keys,
            "window and edit shortcuts have a main menu to route through")
        # Command-1 to Command-5 are the five pages, in sidebar order. The
        # items carry no target, so the action reaches whichever window is
        # key through its delegate, which is the controller.
        page_items = [
            item
            for top in NSApplication.sharedApplication().mainMenu().itemArray()
            if top.submenu() is not None
            for item in top.submenu().itemArray()
            if str(item.action()) == "selectSidebarRow:"]
        hidden_items = [
            item for item in page_items if bool(item.isHidden())]
        page_items = [
            item for item in page_items if not bool(item.isHidden())]
        require(
            [(str(item.keyEquivalent()), int(item.tag()),
              bool(item.allowsKeyEquivalentWhenHidden()))
             for item in hidden_items] ==
            [("d", len(SIDEBAR_ROWS) - 1, True)],
            "Command-D reaches Advanced when the sidebar is put away")
        require(
            [(str(item.keyEquivalent()), int(item.tag()), str(item.title()))
             for item in page_items] ==
            [(str(index + 1), index, localized_string(spec.label_key))
             for index, spec in enumerate(SIDEBAR_ROWS)],
            "Command-1 to Command-5 select the five pages")
        require(
            all(int(item.keyEquivalentModifierMask())
                == int(NSEventModifierFlagCommand)
                and item.target() is None
                for item in page_items + hidden_items),
            "the page shortcuts are plain Command keys on the responder chain")
        require(
            controller.window.delegate() == controller
            and bool(controller.respondsToSelector_("selectSidebarRow:")),
            "the window's delegate answers the page shortcuts")
        state_before_shortcuts = model.state
        for index in (4, 0, 3):
            controller.selectSidebarRow_(page_items[index])
            require(
                sidebar_row_index(
                    model.state.section, model.state.settings_pane) == index
                and bool(controller.sidebar_rows[index].selected),
                f"Command-{index + 1} shows {SIDEBAR_ROWS[index].key}")
        model.state = state_before_shortcuts
        controller.render()

        # ---- The Personalize editors. Each is a sheet, opened here by the
        # action its button calls and answered through the seam a click
        # ends a run through. Nothing is shown.
        def is_sheet(dialog: Any) -> bool:
            return isinstance(dialog, EditorSheet)

        def drive(action: Callable[[Any], None], *steps: Any) -> list[Any]:
            """Open one editor and answer it, a step for each time it, or
            a confirmation it raises, waits. A step is a sheet button's
            name, an alert's response, or a function handed the dialog
            that returns one of those. Returns the dialogs, in order."""
            remaining = list(steps)
            seen: list[Any] = []

            def respond(dialog: Any) -> Any:
                seen.append(dialog)
                require(bool(remaining),
                        "an editor waited for more answers than expected")
                step = remaining.pop(0)
                # A number answers an alert and a name a sheet: one where
                # the other was due means a confirmation that was not
                # asked for, or one that was skipped.
                require(
                    callable(step)
                    or isinstance(step, str) == is_sheet(dialog),
                    f"{'a sheet' if is_sheet(dialog) else 'an alert'} came "
                    f"up where {step!r} was the answer")
                return step(dialog) if callable(step) else step

            controller.modal_responder = respond
            try:
                action(None)
            finally:
                controller.modal_responder = None
            require(not remaining, "an editor closed before it was answered")
            return seen

        def notice_of(sheet: Any) -> str:
            note = sheet.controls["notice"]
            return "" if bool(note.isHidden()) else str(note.stringValue())

        def calls_of(kind: str) -> list[tuple[Any, ...]]:
            return [call for call in calls if call[0] == kind]

        themed_before = {
            role: len(views) for role, views in controller.themed.items()}
        model.load_settings()

        # App tones: every app in a row, its tone a pop-up. Cancel changes
        # nothing; Save applies what changed and nothing else.
        def read_tones_then_cancel(sheet: Any) -> str:
            code = sheet.controls["tone:com.example.code"]
            require(
                [item.bundle for item, _popup in sheet.listed] ==
                ["com.example.mail", "com.example.code"]
                and int(code.indexOfSelectedItem()) ==
                TONE_CHOICES.index("code")
                and int(code.numberOfItems()) == len(TONE_CHOICES),
                "every app has a row, its pop-up on the tone it has")
            require(
                accessible_value(code.cell(), "accessibilityLabel") ==
                localized_string("sheets.tones.choice.label", app="Code")
                and accessible_value(code.cell(), "accessibilityHelp") ==
                localized_string("settings.dialog.tone.choice.help"),
                "a tone pop-up says whose tone it is")
            sheet.controls["tone:com.example.mail"].selectItemAtIndex_(
                TONE_CHOICES.index("verbatim"))
            return "cancel"

        tone_calls = len(calls_of("tone"))
        tone_dialogs = drive(controller.editTone_, read_tones_then_cancel)
        require(
            len(calls_of("tone")) == tone_calls
            and model.state.settings.app_tones[0].tone == "formal",
            "cancelling the app tones changes no tone")

        def choose_casual(sheet: Any) -> str:
            sheet.controls["tone:com.example.mail"].selectItemAtIndex_(
                TONE_CHOICES.index("casual"))
            return "save"

        tone_dialogs += drive(controller.editTone_, choose_casual)
        require(
            calls_of("tone")[tone_calls:] ==
            [("tone", "com.example.mail", "casual")]
            and model.state.settings.app_tones[0].tone == "casual",
            "saving the app tones applies the one that changed, and only it")
        require(
            str(controller.dynamic["notice"].stringValue()) ==
            localized_string("settings.notice.tone_saved"),
            "the page under the sheet is drawn again after a save")

        def refuse_a_tone(sheet: Any) -> str:
            sheet.controls["tone:com.example.mail"].selectItemAtIndex_(
                TONE_CHOICES.index("formal"))
            sheet.controls["tone:com.example.code"].selectItemAtIndex_(
                TONE_CHOICES.index("verbatim"))
            return "save"

        def tone_was_refused(sheet: Any) -> str:
            require(
                notice_of(sheet).startswith("Code: ")
                and notice_of(sheet).endswith(" " + localized_string(
                    "sheets.tones.saved_before", apps="Mail"))
                and "read-only" in notice_of(sheet)
                and int(sheet.controls["tone:com.example.code"]
                        .indexOfSelectedItem()) ==
                TONE_CHOICES.index("verbatim")
                and int(sheet.controls["tone:com.example.mail"]
                        .indexOfSelectedItem()) ==
                TONE_CHOICES.index("formal"),
                "a refused tone keeps the sheet up with every choice, "
                "and says which app it was, why, and what was already "
                "saved")
            return "cancel"

        refused = drive(controller.editTone_, refuse_a_tone, tone_was_refused)
        tone_dialogs += refused
        require(
            refused[0] is refused[1]
            and calls_of("tone")[tone_calls:] == [
                ("tone", "com.example.mail", "casual"),
                ("tone", "com.example.mail", "formal")]
            and model.state.settings.app_tones[1].tone == "code",
            "a refused tone stops the save there, in the same sheet")

        # The first app refused, with another changed after it: the save
        # stops there, so the one after is never tried and nothing was
        # saved before it to speak of.
        def refuse_the_first(sheet: Any) -> str:
            sheet.controls["tone:com.example.mail"].selectItemAtIndex_(
                TONE_CHOICES.index("verbatim"))
            sheet.controls["tone:com.example.code"].selectItemAtIndex_(
                TONE_CHOICES.index("casual"))
            return "save"

        def first_was_refused(sheet: Any) -> str:
            already = localized_string(
                "sheets.tones.saved_before", apps="").split(":")[0]
            require(
                notice_of(sheet).startswith("Mail: ")
                and "read-only" in notice_of(sheet)
                and bool(already) and already not in notice_of(sheet),
                "a tone refused before any was saved says which app, "
                "and claims nothing was saved")
            return "cancel"

        tone_calls_before = len(calls_of("tone"))
        tone_dialogs += drive(
            controller.editTone_, refuse_the_first, first_was_refused)
        require(
            len(calls_of("tone")) == tone_calls_before
            and model.state.settings.app_tones[1].tone == "code",
            "a refused tone stops the save: the app after it is not tried")
        model.set_app_tone("com.example.mail", "casual")

        # Snippets: a list with Edit and Delete on each row, and the same
        # sheet turned into a form to add or edit one.
        def retype_the_snippet(sheet: Any) -> str:
            name, text = sheet.controls["name"], sheet.controls["text"]
            require(
                str(sheet.title.stringValue()) ==
                localized_string("settings.dialog.snippet.edit")
                and str(name.stringValue()) == "signature"
                and not bool(name.isEditable())
                and str(text.string()) == "Cheers"
                and sheet.window.initialFirstResponder() == text,
                "editing a snippet opens it as a form, the name fixed and "
                "the text ready to type in")
            text.setString_("Kind regards")
            return "save"

        def snippet_was_saved(sheet: Any) -> str:
            row = sheet.document.sections[0][1].rows[0]
            require(
                str(sheet.title.stringValue()) ==
                localized_string("settings.personalize.snippets")
                and str(row.title.stringValue()) == "signature"
                and str(row.caption.stringValue()) == "Kind regards",
                "a saved snippet is back in the list, with its new text")
            return "delete:signature"

        def no_snippets_left(sheet: Any) -> str:
            require(
                not [name for name in sheet.buttons
                     if name.startswith(("edit:", "delete:"))]
                and "add" in sheet.buttons,
                "the last snippet deleted leaves the list empty, and Add")
            return "done"

        snippet_dialogs = drive(
            controller.editSnippets_, "edit:signature", retype_the_snippet,
            snippet_was_saved, 1000, no_snippets_left)
        require(
            [is_sheet(dialog) for dialog in snippet_dialogs] ==
            [True, True, True, False, True]
            and all(dialog is snippet_dialogs[0]
                    for dialog in snippet_dialogs if is_sheet(dialog))
            and str(snippet_dialogs[3].messageText()) == localized_string(
                "settings.dialog.delete.title", locale=model.locale),
            "the snippets are one sheet from list to form and back, and "
            "deleting one asks first")

        # Vocabulary: the two lists, each a field in its own group.
        def type_the_vocabulary(sheet: Any) -> str:
            terms, bans = sheet.controls["terms"], sheet.controls["bans"]
            require(
                str(terms.string()) == "Qwen"
                and str(bans.string()) == "Gwen"
                and sheet.window.initialFirstResponder() == terms,
                "the vocabulary opens with both lists, one term per line")
            terms.setString_("Qwen\nParakeet")
            return "save"

        vocabulary_dialogs = drive(
            controller.editVocabulary_, type_the_vocabulary)

        # Learned corrections: rows with Forget, which asks first.
        def nothing_learned_left(sheet: Any) -> str:
            require(
                not [name for name in sheet.buttons
                     if name.startswith("forget:")],
                "the last correction forgotten leaves the list empty")
            return "done"

        correction_dialogs = drive(
            controller.reviewCorrections_, "forget:correction:gwen", 1000,
            "forget:snippet:gwen", 1000, nothing_learned_left)
        require(
            [is_sheet(dialog) for dialog in correction_dialogs] ==
            [True, False, True, False, True]
            and str(correction_dialogs[1].messageText()) ==
            localized_string("sheets.corrections.forget.title")
            and str(correction_dialogs[1].informativeText()) ==
            localized_string(
                "sheets.corrections.forget.message",
                source="Gwen", target="Qwen"),
            "forgetting a learned correction asks first, and says which")

        # Pronunciation keywords: read only when asked for, then rows with
        # Forget, and Copy Export and Forget All at the footer's start.
        require(keyword_reads[0] == 0, "keyword inspection stays on demand")

        def export_was_copied(sheet: Any) -> str:
            require(
                notice_of(sheet) ==
                localized_string("settings.notice.keyword_exported"),
                "copying the keyword export says so in the sheet")
            return "forget:Qwen"

        keyword_dialogs = drive(
            controller.inspectKeywords_, "export", export_was_copied, 1000,
            "forget_all", 1000, "done")
        require(
            [is_sheet(dialog) for dialog in keyword_dialogs] ==
            [True, True, False, True, False, True]
            and keyword_reads[0] == 3,
            "the keywords are read when the sheet opens and again after "
            "each one is forgotten")
        # Saving, cancelling and copying an export raise no alert at all:
        # the only alerts above are the confirmations of something that
        # cannot be undone.
        require(
            all(is_sheet(dialog)
                for dialog in tone_dialogs + vocabulary_dialogs),
            "no editor is a system alert")

        # A refused save keeps the sheet up, with what was typed and the
        # reason under the field it is about.
        def type_reserved_term(sheet: Any) -> str:
            sheet.controls["terms"].setString_("-reserved\nQwen")
            return "save"

        def vocabulary_was_refused(sheet: Any) -> str:
            terms_notes = sheet.document.sections[0][2]
            require(
                str(sheet.controls["terms"].string()) == "-reserved\nQwen",
                "rejected vocabulary keeps the typed terms")
            require(
                "reserved" in notice_of(sheet)
                and sheet.controls["notice"] in terms_notes,
                "rejected vocabulary names the reason under the list it "
                "is about")
            return "cancel"

        dialogs = drive(
            controller.editVocabulary_, type_reserved_term,
            vocabulary_was_refused)
        require(
            len(dialogs) == 2 and dialogs[0] is dialogs[1]
            and all(is_sheet(dialog) for dialog in dialogs),
            "rejected vocabulary stays in the sheet it was typed in")
        require(
            not any(call[0] == "vocabulary" and "-reserved" in call[1]
                    for call in calls),
            "rejected vocabulary never reaches the runtime")
        model.refresh()
        require(
            model.state.notice_level == "error",
            "rejection notice survives the status refresh")

        def type_long_name(sheet: Any) -> str:
            require(
                str(sheet.title.stringValue()) ==
                localized_string("settings.dialog.snippet.add")
                and bool(sheet.controls["name"].isEditable())
                and sheet.window.initialFirstResponder() ==
                sheet.controls["name"],
                "adding a snippet opens an empty form, the name first")
            sheet.controls["name"].setStringValue_("n" * 81)
            sheet.controls["text"].setString_("Typed text")
            return "save"

        def snippet_was_refused(sheet: Any) -> str:
            require(
                str(sheet.controls["name"].stringValue()) == "n" * 81
                and str(sheet.controls["text"].string()) == "Typed text"
                and "1–80" in notice_of(sheet)
                and sheet.controls["notice"] in
                sheet.document.sections[0][2],
                "rejected snippet keeps what was typed, and says why "
                "under the fields")
            return "cancel"

        dialogs = drive(
            controller.editSnippets_, "add", type_long_name,
            snippet_was_refused, "done")
        require(
            len(dialogs) == 4 and all(is_sheet(dialog) for dialog in dialogs)
            and not calls_of("save_snippet")[1:],
            "a rejected snippet stays in the sheet and saves nothing")
        require(
            {role: len(views) for role, views in controller.themed.items()}
            == themed_before,
            "a closed sheet leaves none of its labels with the window")

        private_settings["corrections"] = [{
            "key": "teh", "source": "teh", "target": "the", "count": 3,
            "kind": "correction"}]
        private_settings["snippets"] = [
            {"name": "signature", "text": "Kind regards"}]
        model.load_settings()
        dialogs = []

        # Keys cannot be pressed here, so the key-down monitor _run_modal
        # installs is captured instead and handed stand-in key events while
        # its dialog is up. Each button's action is pointed at a checkbox,
        # which flips when that button is clicked and does nothing else.
        key_monitors: list[dict[str, Any]] = []
        key_outcomes: list[dict[str, Any]] = []

        class RecordingKeyMonitors:
            @staticmethod
            def addLocalMonitorForEventsMatchingMask_handler_(
                    mask: int, handler: Callable[[Any], Any]) -> Any:
                token = object()
                key_monitors.append({
                    "token": token, "mask": mask, "handler": handler,
                    "removed": False})
                return token

            @staticmethod
            def removeMonitor_(token: Any) -> None:
                for entry in key_monitors:
                    if entry["token"] is token:
                        entry["removed"] = True

        class StandInKey:
            def __init__(self, window: Any, key_code: int, characters: str,
                         flags: int = 0) -> None:
                self._window, self._key_code = window, key_code
                self._characters, self._flags = characters, flags

            def window(self) -> Any:
                return self._window

            def keyCode(self) -> int:
                return self._key_code

            def charactersIgnoringModifiers(self) -> str:
                return self._characters

            def modifierFlags(self) -> int:
                return self._flags

        def press_keys(alert: Any) -> None:
            flags = []
            for button in alert.buttons():
                flag = NSButton.checkboxWithTitle_target_action_(
                    "", None, None)
                button.setTarget_(flag)
                button.setAction_("performClick:")
                flags.append(flag)
            live = [entry for entry in key_monitors if not entry["removed"]]
            outcome: dict[str, Any] = {
                "title": str(alert.messageText()), "monitors": len(live),
                "masks": [entry["mask"] for entry in live]}
            for name, key_code, characters, modifiers in (
                    ("escape", _ESCAPE_KEY_CODE, "\x1b", 0),
                    ("command_period", 47, ".", _COMMAND_KEY_FLAG),
                    ("return", 36, "\r", 0)):
                for flag in flags:
                    flag.setState_(NSControlStateValueOff)
                event = StandInKey(
                    alert.window(), key_code, characters, modifiers)
                swallowed = any(
                    entry["handler"](event) is not event for entry in live)
                outcome[name] = (
                    "swallowed" if swallowed else "passed",
                    [index for index, flag in enumerate(flags)
                     if int(flag.state()) == NSControlStateValueOn])
            key_outcomes.append(outcome)

        def press_keys_then(response: int) -> Callable[[Any], int]:
            def respond(alert: Any) -> int:
                dialogs.append(alert)
                press_keys(alert)
                return response
            return respond

        cancelled = {"escape": ("swallowed", [1]),
                     "command_period": ("swallowed", [1]),
                     "return": ("passed", [])}
        appkit_keys = {"monitors": 0,
                       "escape": ("passed", []),
                       "command_period": ("passed", []),
                       "return": ("passed", [])}

        def keys_were(outcome: dict[str, Any],
                      expected: dict[str, Any]) -> bool:
            return all(outcome[name] == value
                       for name, value in expected.items())

        appkit_key_monitors = NSEvent
        globals()["NSEvent"] = RecordingKeyMonitors
        try:
            drive(controller.reviewCorrections_, "forget:correction:teh",
                  press_keys_then(1000), nothing_learned_left)
        finally:
            globals()["NSEvent"] = appkit_key_monitors
        require(
            len(dialogs) == 1
            and str(dialogs[0].messageText()) ==
            localized_string("sheets.corrections.forget.title")
            and "teh" in str(dialogs[0].informativeText()),
            "forgetting a learned correction names it and asks")
        require(("forget_correction", "teh") in calls,
                "learned correction can be forgotten")
        require(
            str(dialogs[0].buttons()[1].keyEquivalent()) == "\r"
            and str(dialogs[0].buttons()[0].keyEquivalent()) == ""
            and bool(dialogs[0].buttons()[0].hasDestructiveAction()),
            "forgetting a learned correction defaults to Cancel")
        require(alert_key_routes(dialogs[0]) == (1, 1),
                "Return and Escape both leave a learned correction alone")
        require(
            keys_were(key_outcomes[0], cancelled)
            and key_outcomes[0]["masks"] == [NSEventMaskKeyDown],
            "Escape picks Cancel when a learned correction is forgotten")
        require(len(key_monitors) == 1 and key_monitors[0]["removed"],
                "forgetting a learned correction removes its key monitor")
        dialogs.clear()
        key_monitors.clear()
        key_outcomes.clear()

        deletions_before = sum(call[0] == "delete_snippet" for call in calls)
        globals()["NSEvent"] = RecordingKeyMonitors
        try:
            drive(controller.editSnippets_, "delete:signature",
                  press_keys_then(1001), "done")
            controller.modal_responder = press_keys_then(1000)
            controller._confirm("Delete?", "Gone for good.", "Delete")
            for primary in ("Compose", "Acknowledge", "Copy Draft"):
                controller._confirm("Sure?", "Nothing is lost.", primary,
                                    destructive=False)
        finally:
            globals()["NSEvent"] = appkit_key_monitors
        require(
            len(dialogs) == 5
            and str(dialogs[0].messageText()) == localized_string(
                "settings.dialog.delete.title", locale=model.locale),
            "deleting a snippet asks first")
        for dialog, outcome in ((dialogs[0], key_outcomes[0]),
                                (dialogs[1], key_outcomes[1])):
            require(
                str(dialog.buttons()[1].keyEquivalent()) == "\r"
                and str(dialog.buttons()[0].keyEquivalent()) == "",
                f"{outcome['title']}: the safe button is the default")
            require(bool(dialog.buttons()[0].hasDestructiveAction()),
                    f"{outcome['title']}: the deletion is marked destructive")
            require(
                keys_were(outcome, cancelled)
                and outcome["masks"] == [NSEventMaskKeyDown],
                f"{outcome['title']}: Escape and Command-period cancel, "
                "Return is left to the safe button")
        require(
            sum(call[0] == "delete_snippet" for call in calls)
            == deletions_before,
            "a cancelled snippet deletion deletes nothing")
        for dialog, outcome in zip(dialogs[2:], key_outcomes[2:]):
            primary = str(dialog.buttons()[0].title())
            require(
                alert_key_routes(dialog) == (0, 1)
                and str(dialog.buttons()[1].keyEquivalent()) == "\x1b"
                and not bool(dialog.buttons()[0].hasDestructiveAction())
                and keys_were(outcome, appkit_keys),
                f"{primary}: Return confirms and Escape cancels")
        require(
            len(key_monitors) == 2
            and all(entry["removed"] for entry in key_monitors),
            "each destructive confirmation removes its key monitor")
        controller.modal_responder = None
        # No folder: every alert that was raised above, like every other
        # the window raises, carries the chosen face on its chip.
        face_icon = controller._face_icons.get(model.state.face)
        require(
            face_icon is not None
            and all(dialog.icon() == face_icon
                    for dialog in dialogs + [recent_dialogs[0]]),
            "a system alert's icon is the chosen face")
        model.choose_face("owl")
        model.choose_language("ja")
        controller.render()
        require(
            int(controller.dynamic[
                "settings_language_popup"].indexOfSelectedItem()) ==
            LANGUAGES.index("ja"),
            "language synchronization")
        model.choose_language(LANGUAGE_DEFAULT)
        controller.render()
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
            ("language", "ja"),
            ("flight", True),
        }
        require(expected_calls.issubset(set(calls)), "model actions")
        require(not bool(controller.window.isVisible()), "window activation")
        def visible_leaves(view: Any) -> list[Any]:
            # A scrolling page is one piece here: what it holds may run
            # below the window on purpose, and check_grouped_pages holds
            # its inside to the same standard instead.
            children = [child for child in view.subviews()
                        if not bool(child.isHidden())]
            if not children or bool(view.isKindOfClass_(NSScrollView)):
                return [view]
            return [leaf for child in children
                    for leaf in visible_leaves(child)]

        def page_leaves(view: Any) -> list[Any]:
            # A control is one thing, whatever its private parts are.
            children = [child for child in view.subviews()
                        if not bool(child.isHidden())]
            if not children or bool(view.respondsToSelector_("cell")):
                return [view]
            return [leaf for child in children
                    for leaf in page_leaves(child)]

        def page_frames(document: Any) -> list[tuple[Any, ...]]:
            """Where everything a page shows is, and how tall the page
            is: what a layout of it comes to."""
            size = document.frame().size
            placed: list[tuple[Any, ...]] = [
                ("page", round(float(size.width), 2),
                 round(float(size.height), 2))]
            for leaf in page_leaves(document):
                rect = document.convertRect_fromView_(
                    leaf.frame(), leaf.superview())
                placed.append((
                    type(leaf).__name__,
                    round(float(rect.origin.x), 2),
                    round(float(rect.origin.y), 2),
                    round(float(rect.size.width), 2),
                    round(float(rect.size.height), 2)))
            return placed

        def check_rows(name: str, groups: Sequence[Any]) -> None:
            """Every row keeps the spec's minimum height (44 pt, 50 with
            a caption), and every key cap that shows names a key."""
            for group in groups:
                for row in group.visible_rows():
                    height = float(row.frame().size.height)
                    floor = (0.0 if row.custom is not None
                             else ROW_CAPTION_MIN_HEIGHT
                             if row.caption is not None
                             else ROW_MIN_HEIGHT)
                    require(
                        height >= floor - 0.01,
                        f"{name}: a row is {height:g} pt tall, under "
                        f"the {floor:g} pt minimum")
                    for view in row.accessories:
                        if bool(view.isKindOfClass_(KeyCapView)) \
                                and not bool(view.isHidden()):
                            require(
                                bool(view.key.strip())
                                and view.frame().size.width
                                >= BRAND_SHAPES["key_cap"].height,
                                f"{name}: a key cap shows with no key")

        def check_grouped_pages(
                size_name: str, *, may_scroll: Sequence[str] = ()) -> None:
            """The four settings pages at this window size.

            Each scrolls in a system scroll view as wide as the page
            column; inside it every visible view sits within the document,
            no two overlap, no label is cut short, the first group shows
            whole without scrolling, and the three settings panes show
            every group without scrolling at all. ``may_scroll`` names the
            panes let off that last rule here: Personalize, whose face
            grid fills its group, and only at the window minimum while a
            two-line notice has taken the foot of the page.
            """
            documents = controller.dynamic["page_documents"]
            scrolls = controller.dynamic["page_scrolls"]
            require(
                set(documents) == set(SETTINGS_PANES) | {"Advanced"},
                "every settings page is a grouped, scrolling page")
            column = in_window(controller.dynamic["page_column"])
            for name, document in documents.items():
                scroll = scrolls[name]
                holders = [controller.pages["Advanced"]] if name == \
                    "Advanced" else [controller.pages["Settings"],
                                     controller.dynamic["settings_panes"][name]]
                was_hidden = [bool(view.isHidden()) for view in holders]
                for view in holders:
                    view.setHidden_(False)
                root_view.layoutSubtreeIfNeeded()
                # Measured as the last render left it. Laying it out again
                # must then change nothing: a render that forgot to would
                # leave a key cap over a title or a caption cut short.
                left = page_frames(document)
                document.arrange(force=True)
                require(
                    page_frames(document) == left,
                    f"the render left {name} laid out at {size_name}")
                clip = scroll.contentView()
                width = float(clip.bounds().size.width)
                scroll_frame = in_window(scroll)
                require(
                    abs(scroll_frame.origin.x - column.origin.x) < 0.5
                    and abs(scroll_frame.size.width - column.size.width)
                    < 0.5
                    and abs(float(document.frame().size.width) - width)
                    < 0.5 and width >= column.size.width - 16.0,
                    f"{name} fills the page column at {size_name}")
                bounds = document.bounds()
                placed = []
                for leaf in page_leaves(document):
                    if leaf is document or bool(
                            leaf.isKindOfClass_(GroupBoxView)):
                        continue
                    own = leaf.alignmentRectForFrame_(leaf.frame())
                    holder = leaf.superview().bounds()
                    # Inside whatever holds it, too: a row, the face grid.
                    require(
                        own.origin.x >= holder.origin.x - 0.5
                        and own.origin.y >= holder.origin.y - 0.5
                        and own.origin.x + own.size.width
                        <= holder.origin.x + holder.size.width + 0.5
                        and own.origin.y + own.size.height
                        <= holder.origin.y + holder.size.height + 0.5,
                        f"{name}: a {type(leaf).__name__} spills out of "
                        f"its {type(leaf.superview()).__name__} at "
                        f"{size_name}")
                    rect = document.convertRect_fromView_(
                        own, leaf.superview())
                    require(
                        rect.origin.x >= -0.5 and rect.origin.y >= -0.5
                        and rect.origin.x + rect.size.width
                        <= bounds.size.width + 0.5
                        and rect.origin.y + rect.size.height
                        <= bounds.size.height + 0.5,
                        f"{name}: a view leaves its page at {size_name}")
                    if bool(leaf.isKindOfClass_(NSTextField)):
                        needed = leaf.cell().cellSizeForBounds_(NSMakeRect(
                            0, 0, leaf.frame().size.width, 10_000.0))
                        require(
                            needed.height <= leaf.frame().size.height + 1.0
                            and needed.width
                            <= leaf.frame().size.width + 1.0,
                            f"{name}: {leaf.stringValue()!r} is cut short "
                            f"at {size_name}")
                    placed.append((leaf, NSInsetRect(rect, 0.5, 0.5)))
                for index, (first, first_rect) in enumerate(placed):
                    for second, second_rect in placed[index + 1:]:
                        require(
                            not intersects(first_rect, second_rect),
                            f"{name}: two views overlap at {size_name}: "
                            f"{type(first).__name__} and "
                            f"{type(second).__name__}")
                groups = [view for view in document.subviews()
                          if bool(view.isKindOfClass_(GroupBoxView))
                          and not bool(view.isHidden())]
                check_rows(f"{name} at {size_name}", groups)
                if name == "Advanced":
                    # A title-only row is exactly the minimum, and the
                    # Permissions row shows only while one is missing.
                    status_row = documents["Advanced"].sections[1][1] \
                        .visible_rows()[0]
                    recovery_row = controller.dynamic[
                        "open_system_settings_row"]
                    require(
                        status_row.caption is None
                        and abs(float(status_row.frame().size.height)
                                - ROW_MIN_HEIGHT) < 0.01
                        and bool(recovery_row.isHidden())
                        != model.permission_recovery_needed()
                        and bool(controller.dynamic[
                            "open_system_settings_button"].isHidden())
                        == bool(recovery_row.isHidden()),
                        f"Advanced's rows are the minimum height, and "
                        f"Permissions shows only when needed, at "
                        f"{size_name}")
                if name == "Controls":
                    # The bound keys as key caps; no undo key, no cap.
                    undo_cap = controller.dynamic["undo_hotkey_keycap"]
                    capture_cap = controller.dynamic["hotkey_keycap"]
                    require(
                        not bool(capture_cap.isHidden())
                        and capture_cap.key == model.state.hotkey_label
                        and bool(undo_cap.isHidden())
                        != bool(model.state.undo_hotkey_label)
                        and undo_cap.key == model.state.undo_hotkey_label,
                        f"Controls shows the keys that are bound, and no "
                        f"cap for one that is not, at {size_name}")
                visible = clip.documentVisibleRect()
                first_group = min(groups, key=lambda view: view.frame().origin.y)
                last_group = max(groups, key=lambda view: view.frame().origin.y)
                require(
                    first_group.frame().origin.y >= visible.origin.y
                    and first_group.frame().origin.y
                    + first_group.frame().size.height
                    <= visible.origin.y + visible.size.height + 0.5,
                    f"{name}: the first group shows whole at {size_name}")
                if name == "Personalize":
                    # The face grid fills its group: eight chips a row,
                    # the spec's gap between their bodies, each as large
                    # as the group then allows, the same inset all round,
                    # and the last chip's hard shadow inside the group.
                    picker = controller.dynamic["face_picker"]
                    chip_shape = BRAND_SHAPES["face_chip"]
                    bodies = []
                    for chip in picker.chips:
                        frame = first_group.convertRect_fromView_(
                            chip.frame(), picker)
                        bodies.append((
                            float(frame.origin.x), float(frame.origin.y),
                            float(frame.size.width)
                            - chip_shape.shadow_right,
                            float(frame.size.height)
                            - chip_shape.shadow_down))
                    group_size = first_group.bounds().size
                    side = math.floor(min(FACE_CHIP_MAX, (
                        group_size.width - 2.0 * ROW_INSET
                        - (FACE_GRID_COLUMNS - 1) * FACE_CHIP_GAP)
                        / FACE_GRID_COLUMNS))
                    left = bodies[0][0] - ROW_INSET
                    right = (group_size.width - ROW_INSET
                             - bodies[FACE_GRID_COLUMNS - 1][0] - side)
                    require(
                        first_group == picker.superview().superview()
                        and len(bodies) == len(FACES) == 16
                        and side >= 66.0
                        and all(abs(width - side) < 0.01
                                and abs(height - side) < 0.01
                                for _x, _y, width, height in bodies)
                        and all(abs(bodies[index + 1][0] - bodies[index][0]
                                    - side - FACE_CHIP_GAP) < 0.01
                                and bodies[index + 1][1] == bodies[index][1]
                                for index in range(FACE_GRID_COLUMNS - 1))
                        and abs(bodies[FACE_GRID_COLUMNS][1] - bodies[0][1]
                                - side - FACE_CHIP_GAP) < 0.01
                        and bodies[FACE_GRID_COLUMNS][0] == bodies[0][0]
                        and 0.0 <= left <= 3.5 and abs(right - left) <= 1.0
                        and abs(bodies[0][1] - ROW_INSET) < 0.01
                        and abs(group_size.height - ROW_INSET
                                - bodies[-1][1] - side) < 0.01
                        and bodies[-1][0] + side + chip_shape.shadow_right
                        <= group_size.width - 0.5
                        and bodies[-1][1] + side + chip_shape.shadow_down
                        <= group_size.height - 0.5,
                        f"the face grid fills its group at {size_name}")
                    # The line that names the chosen face rides the
                    # header's line, at its far end, outside the group.
                    hint = controller.dynamic["face_hint"]
                    header = document.sections[0][0]
                    hint_frame, header_frame = hint.frame(), header.frame()
                    require(
                        hint.superview() == document
                        and document.sections[0][3] == hint
                        and not bool(hint.isHidden())
                        and str(hint.stringValue()) == localized_string(
                            "pages.personalize.face.hint",
                            face=localized_string(
                                f"settings.face.{model.state.face}"))
                        and abs(hint_frame.origin.x + hint_frame.size.width
                                - (width - SECTION_HEADER_INSET)) < 0.5
                        and hint_frame.origin.x
                        >= header_frame.origin.x + header_frame.size.width
                        and hint_frame.origin.y + hint_frame.size.height
                        <= first_group.frame().origin.y
                        and hint_frame.origin.y >= header_frame.origin.y
                        and abs(
                            hint_frame.origin.y
                            + hint.firstBaselineOffsetFromTop()
                            - header_frame.origin.y
                            - header.firstBaselineOffsetFromTop()) < 0.51
                        and float(hint.font().pointSize())
                        < float(header.font().pointSize()),
                        f"the chosen face is named on the header's line "
                        f"at {size_name}")
                if name in SETTINGS_PANES and name not in may_scroll:
                    require(
                        last_group.frame().origin.y
                        + last_group.frame().size.height
                        <= visible.origin.y + visible.size.height + 0.5,
                        f"{name} shows every group without scrolling "
                        f"at {size_name}")
                for view, hidden in zip(holders, was_hidden):
                    view.setHidden_(hidden)
            root_view.layoutSubtreeIfNeeded()

        # At the window minimum every page's content stays clear of a
        # two-line failure notice in the band below it.
        controller.window.setContentSize_(controller.window.contentMinSize())
        model.state = replace(
            model.state, notice=localized_string(
                "operation.snippet.save_failed",
                error=localized_string("validation.snippet.name")),
            notice_level="error")
        controller.render()
        root_view = controller.window.contentView()
        root_view.layoutSubtreeIfNeeded()
        # The shell at the minimum: the sidebar keeps its width, and the
        # column is at its narrowest with the narrowest margin beside it.
        minimum_column = in_window(controller.dynamic["page_column"])
        minimum_inset = min(CONTENT_INSET, (
            WINDOW_MIN_SIZE[0] - SIDEBAR_WIDTH - CONTENT_MIN_WIDTH) / 2.0)
        require(
            minimum_inset >= CONTENT_EDGE_MIN
            and abs(in_window(controller.sidebar).size.width + divider
                    - SIDEBAR_WIDTH) < 0.5
            and abs(minimum_column.size.width - (
                WINDOW_MIN_SIZE[0] - SIDEBAR_WIDTH - 2.0 * minimum_inset))
            < 0.5
            and abs(minimum_column.origin.x - SIDEBAR_WIDTH
                    - minimum_inset) < 0.5
            and abs(float(root_view.bounds().size.width)
                    - WINDOW_MIN_SIZE[0]) < 0.5,
            "the shell fits the window minimum")
        for page_name, page in controller.pages.items():
            was_hidden = bool(page.isHidden())
            page.setHidden_(False)
            root_view.layoutSubtreeIfNeeded()
            notice_frame = root_view.convertRect_fromView_(
                controller.dynamic["notice"].bounds(),
                controller.dynamic["notice"])
            lowest = min(
                root_view.convertRect_fromView_(leaf.bounds(), leaf).origin.y
                for leaf in visible_leaves(page))
            page.setHidden_(was_hidden)
            require(
                lowest >= notice_frame.origin.y + notice_frame.size.height,
                f"{page_name} fits at the window minimum")
        # With that two-line notice at the foot of the page the face grid
        # may send Personalize's last rows below the fold; every other
        # pane, and Personalize whenever no notice is showing, shows every
        # group without scrolling.
        noticed_state = model.state
        notice_view = controller.dynamic["notice"]
        require(
            in_window(notice_view).size.height
            >= NOTICE_WRAPPED_HEIGHT - 0.5,
            "the failure notice takes two lines at the window minimum")
        check_grouped_pages(
            "the window minimum under a two-line notice",
            may_scroll=("Personalize",))
        model.state = replace(noticed_state, notice="", notice_level="info")
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        require(
            in_window(notice_view).size.height <= NOTICE_LINE_HEIGHT + 0.5,
            "no notice, no second line")
        check_grouped_pages("the window minimum")

        # A render leaves a page laid out for the copy it just wrote.
        # Each state below changes what rows say or which rows show; the
        # page is measured straight after the render, with no layout
        # pass in between, and must be where a fresh layout puts it.
        calm_state = model.state

        def left_laid_out(name: str, moves: bool, **changes: Any) -> None:
            document = controller.dynamic["page_documents"][name]
            before = page_frames(document)
            before_arranged = document.arranged
            before_marked = sum(
                section[1].marked for section in document.sections)
            model.state = replace(calm_state, **changes)
            controller.render()
            arranged = document.arranged
            left = page_frames(document)
            require(
                document.arranged > before_arranged,
                f"a render lays {name} out again for its new copy")
            document.arrange(force=True)
            require(
                page_frames(document) == left
                and document.arranged == arranged + 1,
                f"a render leaves {name} laid out for its new copy")
            require(
                not moves or (left != before and sum(
                    section[1].marked for section in document.sections)
                    > before_marked),
                f"{name}: the new copy moves its rows, and the groups "
                "they are in are marked for drawing")
            model.state = calm_state
            controller.render()
            require(
                page_frames(document) == before,
                f"a render leaves {name} laid out when the copy goes back")

        require(
            not calm_state.undo_hotkey_label,
            "the smoke starts with no undo key bound")
        left_laid_out(
            "Controls", True, hotkey="ctrl_r", hotkey_label="Right Control",
            hotkey_shared_modes=("reply", "command", "code"),
            undo_hotkey="f14", undo_hotkey_label="F14", undo_available=True,
            sound_theme="whisper",
            settings=replace(calm_state.settings, self_correction=True))
        left_laid_out("Personalize", False, face="fox", language="ja")
        left_laid_out(
            "Privacy", False, recent_dictations=True,
            voice_object_commands=True, voice_object_inbox_status="Ready",
            voice_object_inbox_count=3)
        left_laid_out(
            "Advanced", True, models=calm_state.models[:1],
            model_wallet_advisory=localized_string(
                "models.wallet.evidence", resolved=3, warm=2),
            missing_permissions=("microphone",),
            microphone_status="Unavailable")
        # With an undo key bound its cap shows, with its key on it; and
        # with a permission missing the Permissions row is back.
        model.state = replace(
            calm_state, undo_hotkey="f14", undo_hotkey_label="F14",
            undo_available=True, missing_permissions=("microphone",))
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        check_grouped_pages("the window minimum, an undo key bound")
        require(
            not bool(controller.dynamic["undo_hotkey_keycap"].isHidden())
            and controller.dynamic["undo_hotkey_keycap"].key == "F14"
            and not bool(controller.dynamic[
                "open_system_settings_row"].isHidden()),
            "a bound undo key shows as a key cap, and a missing "
            "permission brings the Permissions row back")
        model.state = noticed_state
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        # Fitting is also sideways. At the window minimum the column is at
        # its narrowest, so Home is checked there with everything it can
        # show at once (the Voice Outbox holding text and a setup item
        # needing attention, which adds the review row), and setup with
        # the most rows a step has: nothing cut short, overlapping or
        # outside the page, and all of it above a two-line notice.
        fitted_state = model.state

        def groups_in(view: Any) -> list[Any]:
            if bool(view.isHidden()):
                return []
            if bool(view.isKindOfClass_(GroupBoxView)):
                return [view]
            return [group for child in view.subviews()
                    for group in groups_in(child)]

        def above_the_notice(page_view: Any) -> bool:
            """Everything shown, and the edge of every group that holds
            rows, stands clear of the notice below it."""
            notice_frame = frame_in_window(controller.dynamic["notice"])
            return min(
                frame_in_window(view).origin.y
                for view in leaf_views(page_view) + groups_in(page_view)
            ) >= (notice_frame.origin.y + notice_frame.size.height
                  + NOTICE_CLEARANCE - 0.5)

        def places() -> list[tuple[float, ...]]:
            """Where Home's and setup's parts are in the window."""
            controller._layout_home()
            controller._layout_onboarding()
            return [
                tuple(round(float(number), 1) for number in (
                    frame.origin.x, frame.origin.y,
                    frame.size.width, frame.size.height))
                for frame in (
                    frame_in_window(controller.dynamic[key])
                    for key in ("home_stage", "home_group",
                                "onboarding_stage", "onboarding_foot"))]

        model.state = replace(
            fitted_state, section="Home", onboarding_acknowledged=True,
            outbox_count=1, outbox_summary=localized_string(
                "overview.outbox.summary.paste_attempted"),
            degraded_issues=(DegradedIssue(
                "microphone", localized_string("issue.microphone.title"),
                localized_string("issue.microphone.detail")),))
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        # Home cannot scroll: at its tightest fit it is in the same place
        # with a notice showing and without one.
        under_notice = places()
        model.state = replace(model.state, notice="", notice_level="info")
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        require(
            bool(fitted_state.notice) and places() == under_notice,
            "Home, at the window minimum with everything showing, does "
            "not move when a notice comes or goes")
        model.state = replace(
            model.state, notice=fitted_state.notice,
            notice_level=fitted_state.notice_level)
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        home = controller.dynamic["home_cluster"]
        require(
            abs(frame_in_window(home).size.width - CONTENT_MIN_WIDTH) < 0.5
            and not bool(controller.dynamic["pause_button"].isHidden())
            and not bool(controller.dynamic["copy_outbox_button"].isHidden())
            and bool(controller.dynamic["review_issue_button"].isHidden())
            and not bool(controller.dynamic["home_attention_row"].isHidden())
            and not bool(controller.dynamic["overview_actions"].isHidden()),
            "Home shows every action it has at the window minimum")
        laid_out(home, "Home at the window minimum")
        require(
            above_the_notice(home),
            "Home, with everything showing, fits at the window minimum")
        model.state = replace(
            fitted_state, section="Home", onboarding_acknowledged=False)
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        setup = controller.dynamic["onboarding_card"]
        # Without the sidebar the column has the whole window to sit in,
        # so setup is never narrower than the column's widest.
        require(
            controller.setup_mode
            and not any(bool(row["view"].isHidden())
                        for row in controller.dynamic["onboarding_rows"])
            and frame_in_window(setup).size.width
            >= CONTENT_MIN_WIDTH - 0.5,
            "setup shows its most rows at the window minimum")
        laid_out(setup, "setup at the window minimum", over=(
            controller.dynamic["onboarding_progress_fill"],))
        require(
            above_the_notice(setup)
            and not any(
                intersects(in_window(light), frame_in_window(leaf))
                for light in lights for leaf in leaf_views(setup)),
            "setup fits at the window minimum, clear of the traffic lights")
        model.state = fitted_state
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        controller.window.setContentSize_(WINDOW_DEFAULT_SIZE)
        root_view.layoutSubtreeIfNeeded()
        check_grouped_pages("the default size under a notice")
        model.state = replace(fitted_state, notice="", notice_level="info")
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        check_grouped_pages("the default size")
        # The band at the window's foot is the notice's only while there
        # is one. With none, a page that scrolls runs to the window's
        # bottom edge; Home and setup cannot scroll, so they keep clear of
        # the band either way and do not move when a notice comes.

        page_scrolls = controller.dynamic["page_scrolls"]
        without_notice = places()
        require(
            len(page_scrolls) == 4 and all(
                abs(float(frame_in_window(scroll).origin.y)) < 0.5
                for scroll in page_scrolls.values()),
            "with no notice showing, a page that scrolls runs to the "
            "window's bottom edge")
        model.state = fitted_state
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        notice_now = frame_in_window(controller.dynamic["notice"])
        require(
            bool(fitted_state.notice)
            and all(
                float(frame_in_window(scroll).origin.y)
                >= float(notice_now.origin.y + notice_now.size.height)
                + NOTICE_CLEARANCE - 0.5
                for scroll in page_scrolls.values())
            and places() == without_notice,
            "a notice takes the band at the window's foot back from a "
            "page that scrolls, and Home and setup stay where they were")
        default_column = in_window(controller.dynamic["page_column"])
        require(
            abs(default_column.size.width - CONTENT_WIDTH) < 0.5
            and default_column.origin.x - SIDEBAR_WIDTH >= CONTENT_INSET,
            "the default window gives the column its full width and inset")

        # ---- One look for a group of rows. Home's group and setup's are
        # the group every settings page has: the same view, so the same
        # fill, hairline edge, corners and separators in both appearances
        # and under Increase Contrast, and rows of the same make.
        def shown_while(view: Any, measure: Callable[[], Any]) -> Any:
            """Run ``measure`` with ``view`` and what holds it showing."""
            hidden = [holder for holder in ancestors_of(view)
                      if bool(holder.isHidden())]
            for holder in hidden:
                holder.setHidden_(False)
            try:
                root_view.layoutSubtreeIfNeeded()
                return measure()
            finally:
                for holder in hidden:
                    holder.setHidden_(True)

        def group_look(group: Any) -> tuple[Any, ...]:
            """What a group draws where no row draws: the fill, the top
            edge, just outside a corner, a separator, and the fill where
            a separator has not started yet."""
            rep = rendered(group)
            width = float(group.bounds().size.width)
            line = math.floor(group.separators[0]) + 0.5
            return (
                pixel(rep, width - 3.0, line - 4.0),
                pixel(rep, width / 2.0, 0.5),
                pixel(rep, 1.5, 1.5),
                pixel(rep, ROW_INSET + 2.0, line),
                pixel(rep, ROW_INSET - 3.0, line))

        def with_increased_contrast(measure: Callable[[], Any]) -> Any:
            saved = globals()["_increase_contrast"]
            globals()["_increase_contrast"] = lambda: True
            try:
                return measure()
            finally:
                globals()["_increase_contrast"] = saved

        home_group = controller.dynamic["home_result_card"]
        setup_group = controller.dynamic["onboarding_rows_card"]
        page_group = controller.dynamic["page_documents"]["Controls"] \
            .sections[0][1]
        every_group = (home_group, setup_group, page_group)
        require(
            all(type(group) is GroupBoxView for group in every_group)
            and all(type(row) is SettingsRowView
                    for group in every_group for row in group.rows)
            and all(len(group.separators) >= 1 for group in every_group),
            "Home, setup and the settings pages hold their rows in the "
            "same group view")
        check_rows("Home", [home_group])
        check_rows("setup", [setup_group])
        # Over each of them is a section header that VoiceOver reads as a
        # heading, as on a settings page, and Home's and setup's groups
        # keep the names VoiceOver met them by.
        headers = (
            controller.dynamic["home_group_header"],
            controller.dynamic["onboarding_status"],
            controller.dynamic["page_documents"]["Controls"].sections[0][0])
        require(
            all(accessible_value(header.cell(), "accessibilityRole")
                == "AXHeading" and header.font() == headers[2].font()
                for header in headers)
            and all(abs(float(header.frame().origin.x)
                        - SECTION_HEADER_INSET) < 0.01
                    for header in headers)
            and bool(home_group.isAccessibilityElement())
            and accessible_value(home_group, "accessibilityRole")
            == "AXGroup"
            and accessible_value(home_group, "accessibilityLabel")
            == localized_string("home.group.dictation")
            and bool(setup_group.isAccessibilityElement())
            and accessible_value(setup_group, "accessibilityLabel")
            == localized_string("onboarding.rows.accessibility.label"),
            "Home's and setup's groups sit under a section header like "
            "any other, and keep their VoiceOver names")
        for dark_look in (False, True):
            controller.window.setAppearance_(NSAppearance.appearanceNamed_(
                NSAppearanceNameDarkAqua if dark_look
                else NSAppearanceNameAqua))
            controller.render()
            surface = palette_for_appearance(dark_look).surface
            for contrast in (False, True):
                def looks() -> list[tuple[Any, ...]]:
                    return [shown_while(group, lambda: group_look(group))
                            for group in every_group]
                home_look, setup_look, page_look = (
                    with_increased_contrast(looks) if contrast else looks())
                where = (f"{'dark' if dark_look else 'light'} appearance"
                         + (", Increase Contrast" if contrast else ""))
                edge = color_token("hairline", dark_look, contrast).over(
                    surface)
                fill, top_edge, corner, separator, before = page_look
                require(
                    near(fill, surface) and fill[3] > 0.99
                    and near(separator, edge) and near(before, surface)
                    and corner[3] < 0.05
                    and not near(top_edge, surface, 0.01),
                    f"a group is the surface with a hairline edge, round "
                    f"corners and separators from the row inset, in {where}")
                for name, look in (("Home", home_look),
                                   ("setup", setup_look)):
                    require(
                        all(near(drawn, expected, 0.01)
                            for drawn, expected in zip(look, page_look)),
                        f"{name}'s group draws what a settings group "
                        f"draws, in {where}")
        controller.window.setAppearance_(None)
        controller.render()
        # The comparisons above hold when all three change alike, so the
        # look's own numbers are measured too. The corner: at 3.3 points
        # in from both edges a radius of 13 has not started, and at 5
        # points it is past its edge and into the fill. Anything under
        # about 11 or over about 15 fails one of the two.
        shape_rep = shown_while(page_group, lambda: rendered(page_group))
        page_surface = palette_for_appearance(
            _uses_dark_appearance(controller.window)).surface
        require(
            BRAND_SHAPES["group"].radius == 13.0
            and pixel(shape_rep, 3.3, 3.3)[3] < 0.05
            and near(pixel(shape_rep, 5.0, 5.0), page_surface)
            and pixel(shape_rep, 5.0, 5.0)[3] > 0.99,
            "a group's corners are drawn at a radius of 13 points")
        # Type: a row's title is 13 point and its caption 12.
        page_row = page_group.rows[0]
        require(
            float(page_row.title.font().pointSize()) == 13.0
            and float(page_row.caption.font().pointSize()) == 12.0,
            "a row's title is set at 13 points and its caption at 12")
        # Rhythm: a header sits GROUP_HEADER_GAP over its group and a note
        # NOTE_GAP under it, on every page that has one.
        header_gaps, note_gaps = [], []
        for document in controller.dynamic["page_documents"].values():
            document.arrange(force=True)
            for header, group, notes, _trailing in document.sections:
                if bool(group.isHidden()):
                    continue
                top = float(group.frame().origin.y)
                foot = top + float(group.frame().size.height)
                if header is not None:
                    header_gaps.append(round(top - float(
                        header.frame().origin.y
                        + header.frame().size.height), 2))
                shown_notes = [note for note in notes
                               if not bool(note.isHidden())]
                if shown_notes:
                    note_gaps.append(round(
                        float(shown_notes[0].frame().origin.y) - foot, 2))
        # A row that has one line of text has it on the row's middle.
        off_centre = []
        for group in [
                section[1] for document in
                controller.dynamic["page_documents"].values()
                for section in document.sections] + [
                    home_group, setup_group]:
            for row in group.visible_rows():
                lines = [text for text in (row.title, row.caption)
                         if text is not None and str(text.stringValue())
                         and not bool(text.isHidden())]
                if row.custom is None and len(lines) == 1:
                    frame = lines[0].frame()
                    off_centre.append(abs(
                        float(frame.origin.y + frame.size.height / 2.0)
                        - float(row.frame().size.height) / 2.0))
        require(
            len(off_centre) >= 4 and max(off_centre) < 0.6,
            "a row with a single line of text has it on the row's middle")
        require(
            len(header_gaps) >= 8 and len(note_gaps) >= 2
            and GROUP_HEADER_GAP >= 6.0 and NOTE_GAP >= 6.0
            and set(header_gaps) == {GROUP_HEADER_GAP}
            and set(note_gaps) == {NOTE_GAP},
            "a section header stands clear of its group, and a note clear "
            "of the group over it, by the same gap on every page")
        # Home's two gaps open up where the window gives them room: at
        # the default size the stickers stand at least 16 points under
        # the stage's shadow, not the 8 they keep at a tight fit.
        size_before = controller.window.contentView().frame().size
        controller.window.setContentSize_(WINDOW_DEFAULT_SIZE)
        root_view.layoutSubtreeIfNeeded()
        controller._layout_home()
        home_stage_frame = controller.dynamic["home_stage"].frame()
        require(
            float(controller.dynamic["overview_metric_cards"][0]
                  .frame().origin.y)
            - float(home_stage_frame.origin.y + home_stage_frame.size.height)
            >= 16.0,
            "Home's gaps open up at the default window size")
        controller.window.setContentSize_(size_before)
        root_view.layoutSubtreeIfNeeded()
        controller._layout_home()
        # The rows in them are the same make: title and caption in the
        # same type, text from the same inset, the same minimum heights.
        for name, group in (("Home", home_group), ("setup", setup_group)):
            titled = next(row for row in group.rows
                          if row.title is not None and row.caption is not None)
            require(
                titled.title.font() == page_row.title.font()
                and titled.caption.font() == page_row.caption.font()
                and abs(titled.text_origin() - (
                    ROW_INSET if titled.leading is None else
                    ROW_INSET + titled.leading.frame().size.width
                    + ROW_CONTROL_GAP)) < 0.01
                and abs(float(titled.title.frame().origin.x)
                        - titled.text_origin()) < 0.01
                and abs(float(titled.caption.frame().origin.x)
                        - titled.text_origin()) < 0.01
                and (titled.leading is None or (
                    abs(float(titled.leading.frame().origin.x) - ROW_INSET)
                    < 0.01
                    and abs(float(titled.leading.frame().origin.y)
                            + float(titled.leading.frame().size.height) / 2.0
                            - float(titled.frame().size.height) / 2.0)
                    < 0.01)),
                f"{name}'s rows are set in the same type, from the same "
                "inset, as a settings row")
        # What stands at a row's end is the same make too: Home's value
        # is a value label like Advanced's, a setup row's state is the
        # status view a model's state is, in the same two colours, and
        # the three headers are one colour.
        mode_value = controller.dynamic["result_mode"]
        plain_value = controller.dynamic["diag_motion"]
        setup_state = controller.dynamic["onboarding_rows"][0]["state"]
        model_state = controller.dynamic["model_status_views"][0]
        require(
            mode_value.font() == plain_value.font()
            and bool(mode_value.textColor().isEqual_(
                plain_value.textColor()))
            and mode_value in controller.dynamic[
                "result_inspect_button"].superview().accessories
            and type(setup_state) is type(model_state) is StatusLabelView
            and setup_state.tone == model_state.tone == "good"
            and setup_state.text.font() == model_state.text.font()
            and bool(setup_state.text.textColor().isEqual_(
                model_state.text.textColor()))
            and bool(setup_state.icon.contentTintColor().isEqual_(
                model_state.icon.contentTintColor()))
            and bool(setup_state.icon.contentTintColor().isEqual_(
                _token_color("brand_ink", controller._dark)))
            and all(bool(header.textColor().isEqual_(
                headers[2].textColor())) for header in headers),
            "a value, a state and a section header look the same on "
            "Home and in setup as on a settings page")

        # In light appearance, read the colours the window actually draws: a
        # default install's optional model is quiet, not amber, and error
        # text (the failure notice still shown, the degraded phase) is AA.
        # Read them as sRGB, the space the palette's hexes are written in.
        def drawn_rgb(color: Any) -> tuple[float, float, float]:
            rgb = color.colorUsingColorSpace_(
                NSColorSpace.sRGBColorSpace())
            return (float(rgb.redComponent()), float(rgb.greenComponent()),
                    float(rgb.blueComponent()))

        controller.window.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameAqua))
        runtime["models"] = [
            {"name": "Parakeet Unified 0.6B", "role": "Primary recognition",
             "install_state": "installed", "required": True, "warm": True,
             "status": "RESOLVED", "detail": "Exact pinned files resolved"},
            {"name": "Whisper large-v3-turbo", "role": "Optional recognition",
             "install_state": "not_installed", "required": False,
             "warm": False, "status": "UNAVAILABLE",
             "detail": "Exact pinned files not resolved"},
            # A row whose state the runtime did not name carries its own
            # receipt words; the page must not show them.
            {"name": "Whisper Tiny", "role": "Fast recognition preview",
             "status": "RESOLVED",
             "detail": "Exact pinned files resolved · Runtime readiness "
                       "not attested · Capability bounds unavailable"},
        ]
        runtime["microphone_status"] = "Unavailable"
        model.refresh()
        model.state = replace(
            model.state, selective_relisten_status="receipt-invalid")
        controller.render()
        light = palette_for_appearance(False)
        light_surface = light.surface
        optional_index = next(
            index for index, row in enumerate(model.state.models)
            if row.install_state == "not_installed" and not row.required)
        optional_status = controller.dynamic["model_status_views"][
            optional_index]
        require(
            optional_status.tone == "neutral"
            and controller.dynamic["model_rows"][optional_index][3]
            == optional_status.text
            and all(abs(drawn - expected) < 0.01 for drawn, expected in zip(
                drawn_rgb(optional_status.text.textColor()), light.ink_soft))
            and contrast_ratio(light.ink_soft, light_surface) >= 4.5,
            "an optional model that was never installed is stated quietly, "
            "in words that meet AA")
        unnamed_index = next(
            index for index, row in enumerate(model.state.models)
            if row.install_state == "unknown")
        unnamed_status = controller.dynamic["model_status_views"][
            unnamed_index]
        require(
            str(unnamed_status.text.stringValue()) == localized_string(
                "models.state.installed")
            and unnamed_status.tone == "good"
            and str(controller.dynamic["model_rows"][unnamed_index][2]
                    .stringValue()) == "Fast recognition preview",
            "a runtime status word is mapped to a plain phrase")
        microphone = controller.dynamic["diag_status_views"][
            "diag_microphone"]
        warn_symbol = drawn_rgb(microphone.icon.contentTintColor())
        require(
            microphone.tone == "warn"
            and str(microphone.text.stringValue()) == "Unavailable"
            and contrast_ratio(warn_symbol, light_surface) >= 3.0,
            "a health warning keeps a warning symbol that stands out")

        # The note under Models says what the runtime's model checks
        # found, in plain words, and the page is laid out again for it.
        before_notes = model.state
        advisory_note = controller.dynamic["model_wallet_advisory"]
        for found, expected in (
                (localized_string(
                    "models.wallet.evidence", resolved=3, warm=2),
                 localized_string(
                     "pages.models.advisory.evidence", found=3, warm=2,
                     total=len(MODEL_WALLET_PROVIDER_IDS))),
                (localized_string("models.wallet.unavailable"),
                 localized_string("pages.models.advisory.unavailable")),
                (localized_string("models.wallet.informational"),
                 localized_string("pages.models.advisory"))):
            model.state = replace(
                before_notes, section="Advanced",
                model_wallet_advisory=found)
            controller.render()
            root_view.layoutSubtreeIfNeeded()
            note_frame = advisory_note.frame()
            require(
                str(advisory_note.stringValue()) == expected
                == model_checks_note(found)
                and not bool(advisory_note.isHidden())
                and not internal_words_in(expected)
                and accessible_value(advisory_note, "accessibilityLabel")
                == localized_string("pages.models.advisory.label")
                and advisory_note.cell().cellSizeForBounds_(NSMakeRect(
                    0, 0, note_frame.size.width, 10_000.0)).height
                <= note_frame.size.height + 1.0,
                f"the model checks note says what was found: {expected!r}")
        require(
            "3 of 4" in model_checks_note(localized_string(
                "models.wallet.evidence", resolved=3, warm=2))
            and "2 of 4" in model_checks_note(localized_string(
                "models.wallet.evidence", resolved=3, warm=2)),
            "the model checks note carries the counts")
        model.state = before_notes
        controller.render()
        root_view.layoutSubtreeIfNeeded()

        # The face grid's chips, from what they draw: the chosen one is
        # amber with the ink outline and its hard shadow, the others are
        # a faint tile, and a press on one of those shows before it is
        # chosen.
        face_picker = controller.dynamic["face_picker"]
        before_faces = model.state
        for other_face in ("fox", "olive", before_faces.face):
            # Wherever the choice goes, the one amber chip goes with it.
            model.state = replace(before_faces, face=other_face)
            controller.render()
            require(
                [index for index, chip in enumerate(face_picker.chips)
                 if chip.selected] == [FACES.index(other_face)]
                and [accessible_value(chip.cell(), "accessibilityValue")
                     for chip in face_picker.chips]
                == ["1" if face == other_face else "0" for face in FACES],
                f"the selection is on {other_face}'s chip and no other")
            # The sidebar's face is a picture; the button over it is
            # what VoiceOver stops on, and it says which face it is.
            mascot = controller.dynamic["window_face_button"].cell()
            require(
                accessible_value(mascot, "accessibilityLabel")
                == localized_string("app.face_chip.label")
                and accessible_value(mascot, "accessibilityValue")
                == localized_string(f"settings.face.{other_face}")
                and controller.dynamic["window_face"].image() is not None,
                f"the sidebar's face button says it is the {other_face}")
        chosen_index = FACES.index(model.state.face)

        def chip_look(chip: Any) -> tuple[Any, ...]:
            """A chip's fill beside its art, its left edge, and the row
            under its body where only a hard shadow draws."""
            def measure() -> tuple[Any, ...]:
                rep = rendered(chip)
                body = hard_shadow_body_rect(
                    chip.bounds(), BRAND_SHAPES["face_chip"])
                middle = body.origin.y + body.size.height / 2.0
                return (
                    pixel(rep, body.origin.x + 4.0, middle),
                    pixel(rep, body.origin.x + 0.75, middle),
                    pixel(rep, body.origin.x + body.size.width / 2.0,
                          chip.bounds().size.height - 0.75))
            return shown_while(chip, measure)

        chosen_fill, chosen_edge, chosen_shadow = chip_look(
            face_picker.chips[chosen_index])
        require(
            [index for index, chip in enumerate(face_picker.chips)
             if chip.selected] == [chosen_index]
            and int(face_picker.selectedSegment()) == chosen_index
            and near(chosen_fill, light.accent) and chosen_fill[3] > 0.99
            and near(chosen_edge, light.line) and chosen_edge[3] > 0.99
            and near(chosen_shadow, light.line) and chosen_shadow[3] > 0.99,
            "the chosen face's chip, and only it, is amber with the ink "
            "outline and a hard shadow")
        resting = [chip_look(chip)
                   for index, chip in enumerate(face_picker.chips)
                   if index != chosen_index]
        rest_alpha = color_token("chip_rest", False).alpha
        require(
            len(resting) == len(FACES) - 1
            and all(abs(fill[3] - rest_alpha) < 0.03
                    and abs(edge[3] - rest_alpha) < 0.03
                    and shadow[3] < 0.02
                    for fill, edge, shadow in resting),
            "every other chip is a faint tile with no outline and no "
            "shadow")
        pressed_chip = face_picker.chips[(chosen_index + 1) % len(FACES)]
        pressed_chip.cell().setHighlighted_(True)
        pressed_fill = chip_look(pressed_chip)[0]
        pressed_chip.cell().setHighlighted_(False)
        require(
            pressed_fill[3] > rest_alpha + 0.08
            and not pressed_chip.selected,
            "a press on a chip that is not chosen shows")

        # Nothing a default install shows on the four pages uses the
        # runtime's internal words as its label, and every control's
        # VoiceOver label is on the element VoiceOver reads: its cell.
        controller.window.setContentSize_(WINDOW_DEFAULT_SIZE)
        page_controls = [
            control for name in SETTINGS_PANES
            for control in controller.dynamic["settings_key_views"][name]]
        page_controls.extend(controller.key_views_by_section["Advanced"])
        for name, document in controller.dynamic["page_documents"].items():
            holders = [controller.pages["Advanced"]] if name == \
                "Advanced" else [controller.pages["Settings"],
                                 controller.dynamic["settings_panes"][name]]
            was_hidden = [bool(view.isHidden()) for view in holders]
            for view in holders:
                view.setHidden_(False)
            shown_text = [
                str(leaf.stringValue()) for leaf in shown_leaves(document)
                if bool(leaf.isKindOfClass_(NSTextField))
                and not any(bool(view.isHidden())
                            for view in ancestors_of(leaf))]
            for view, hidden in zip(holders, was_hidden):
                view.setHidden_(hidden)
            # The scan has to have read the page: every row's title at the
            # least. On a system where a text field is not a leaf of the
            # view tree it once read nothing and passed.
            titles = [
                str(row.title.stringValue())
                for _header, group, _notes, _trailing in document.sections
                for row in group.rows
                if row.title is not None and not bool(row.isHidden())
                and not bool(row.title.isHidden())]
            require(
                titles and all(title in shown_text for title in titles),
                f"{name}: the internal-word scan reads every row's title")
            for text in shown_text:
                require(
                    not internal_words_in(text),
                    f"{name} shows an internal word: {text!r}")
        # The same goes for everything else VoiceOver stops on, on each
        # of the four pages and in the sidebar beside them.
        checked = [
            view for name, document in
            controller.dynamic["page_documents"].items()
            for view in check_voiceover(name, document, plain_words=True)]
        checked += check_voiceover("the sidebar", controller.sidebar)
        require(
            len(checked) >= 120
            and all(control in checked or bool(control.isHidden())
                    or bool(control.superview().isHidden())
                    for control in page_controls),
            f"every control on the four pages was read ({len(checked)})")
        # What each row's button says: what it does, to what.
        row_button_labels = {
            "settings_tones_button": "pages.personalize.tones.action",
            "settings_snippets_button": "pages.personalize.snippets.action",
            "settings_vocabulary_button":
                "pages.personalize.vocabulary.action",
            "settings_corrections_button":
                "pages.personalize.corrections.action",
            "settings_keywords_button": "pages.personalize.keywords.action",
            "settings_hotkey_button": "pages.controls.hotkey.action",
            "settings_undo_hotkey_button": "pages.controls.undo.action",
            "settings_modes_button": "pages.controls.modes.action",
            "sound_preview_button": "pages.controls.sound.action",
            "recent_dictations_browse": "pages.privacy.recent.action",
            "voice_object_inspect_button":
                "pages.privacy.voice_objects.action",
            "open_system_settings_button":
                "diagnostics.accessibility.open_system_settings",
            "license_button": "pages.support.licenses.action",
            "source_button": "pages.support.source.action",
        }
        for key, label_key in row_button_labels.items():
            require(
                accessible_value(
                    controller.dynamic[key].cell(), "accessibilityLabel")
                == localized_string(label_key)
                and label_key in native_appkit_smoke_contract()
                .accessibility_catalog_keys,
                f"{key} says {localized_string(label_key)!r}")
        # A state's words say whose state they are, on the element read.
        first_model = controller.dynamic["model_rows"][0]
        require(
            accessible_value(first_model[3].cell(), "accessibilityLabel")
            == localized_string(
                "models.accessibility.status",
                name=str(first_model[1].stringValue()))
            and accessible_value(first_model[1].cell(), "accessibilityLabel")
            == localized_string("models.accessibility.name"),
            "VoiceOver hears whose status a model's status is")
        require(model.state.notice_level == "error", "light error notice")
        require(model.state.status_phase == "degraded", "light degraded phase")
        for key in ("notice",):
            drawn = drawn_rgb(controller.dynamic[key].textColor())
            require(
                min(contrast_ratio(drawn, light.bg),
                    contrast_ratio(drawn, light.surface)) >= 4.5,
                f"{key} error text meets AA in light appearance")
        # The phase in capitals is no longer drawn. What the bubble says
        # instead, the headline and the instruction, is AA on porcelain.
        model.state = replace(model.state, section="Home")
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        instruction_ink, _range = controller.dynamic["overview_detail"] \
            .attributedStringValue().attribute_atIndex_effectiveRange_(
                NSForegroundColorAttributeName, 0, None)
        for key, ink in (
                ("overview_status",
                 controller.dynamic["overview_status"].textColor()),
                ("overview_detail", instruction_ink)):
            require(
                contrast_ratio(drawn_rgb(ink), light.surface) >= 4.5,
                f"{key} meets AA on the bubble in light appearance")

        def progress_fill_rgb() -> tuple[float, float, float]:
            return drawn_rgb(NSColor.colorWithCGColor_(controller.dynamic[
                "onboarding_progress_fill"].layer().backgroundColor()))

        require(
            contrast_ratio(progress_fill_rgb(), light.bg) >= 3.0,
            "setup's progress fill stands off the field in light appearance")

        # ---- Edges in the dark appearance. A bright fill keeps the dark
        # ink. A control whose own fill is dark there (the plain chunky
        # button, the key cap) gets a light edge that measures 3:1 against
        # the surface it sits on, and keeps a dark hard shadow.
        controller.window.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        controller.render()
        root_view.layoutSubtreeIfNeeded()
        night = palette_for_appearance(True)
        require(
            contrast_ratio(progress_fill_rgb(), night.bg) >= 3.0,
            "setup's progress fill stands off the field in dark appearance")
        plain_button = controller.dynamic["review_issue_button"]
        require(
            not bool(plain_button.isHidden())
            and plain_button.variant == "plain",
            "dark appearance: the plain secondary is showing")
        plain_rep = rendered(plain_button)
        plain_body = plain_button.body_rect()
        plain_edge = pixel(
            plain_rep, 0.75, plain_body.size.height / 2.0)[:3]
        require(
            near(pixel(plain_rep, plain_body.origin.x + 6.0,
                       plain_body.size.height / 2.0), night.surface)
            and contrast_ratio(plain_edge, night.surface) >= 3.0
            and (_increase_contrast() or near(
                plain_edge,
                color_token("control_edge", True).over(night.surface))),
            "dark appearance: a plain chunky button has a 3:1 edge")
        require(
            near(pixel(plain_rep, plain_body.size.width / 2.0,
                       plain_button.bounds().size.height - 0.75),
                 night.line),
            "dark appearance: a plain chunky button keeps a dark shadow")
        probe_cap = KeyCapView.alloc().initWithFrame_(
            NSMakeRect(0, 0, 10, 10)).set_key("F13")
        probe_cap.setFrameSize_(probe_cap.intrinsicContentSize())
        controller.dynamic["content_side"].addSubview_(probe_cap)
        cap_rep = rendered(probe_cap)
        cap_shape = BRAND_SHAPES["key_cap"]
        cap_edge = pixel(cap_rep, 0.5, cap_shape.height / 2.0)[:3]
        cap_shadow = pixel(
            cap_rep, probe_cap.bounds().size.width / 2.0,
            probe_cap.bounds().size.height - 0.75)
        probe_cap.removeFromSuperview()
        require(
            contrast_ratio(cap_edge, night.surface) >= 3.0
            and near(cap_shadow, night.line)
            and accessible_value(probe_cap, "accessibilityLabel") == "F13"
            and accessible_value(probe_cap, "accessibilityValue") == ""
            and accessible_value(probe_cap, "accessibilityRoleDescription")
            == localized_string("key_cap.accessibility.role"),
            "dark appearance: a key cap has a 3:1 edge and a dark shadow, "
            "and VoiceOver reads its name once, as a key")
        amber_rep = rendered(controller.dynamic["pause_button"])
        require(
            near(pixel(amber_rep, 0.75, plain_body.size.height / 2.0),
                 night.line),
            "dark appearance: the amber button keeps the dark ink")

        # Increase Contrast in the dark appearance: the bubble's and the
        # stickers' outline takes the light ink, and their hard shadow
        # stays dark. A light shadow on a dark window reads as a glow.
        home_bubble = controller.dynamic["home_bubble"]
        sticker = controller.dynamic["home_stickers"][0]

        def bubble_inks() -> tuple[Any, Any]:
            """The bubble's outline, on its top edge, and its hard
            shadow, under its bottom edge."""
            rep = rendered(home_bubble)
            size = home_bubble.bounds().size
            return (pixel(rep, size.width / 2.0, 0.75),
                    pixel(rep, size.width / 2.0, size.height - 0.75))

        def sticker_inks() -> tuple[Any, Any]:
            """The same two for a sticker, at its middle, where its tilt
            moves nothing."""
            rep = rendered(sticker)
            size = sticker.bounds().size
            _tile_width, tile_height = sticker.tile_size()
            return (pixel(rep, size.width / 2.0,
                          (size.height - tile_height) / 2.0 + 0.75),
                    pixel(rep, size.width / 2.0,
                          (size.height + tile_height) / 2.0 - 0.75))

        for name, inks in (("bubble", bubble_inks),
                           ("first sticker", sticker_inks)):
            outline, cast = inks()
            require(
                near(outline, night.line) and near(cast, night.line)
                and cast[3] > 0.99,
                f"dark appearance: the {name} is outlined and shadowed "
                "in the dark ink")
            outline, cast = with_increased_contrast(inks)
            require(
                near(outline, night.ink) and near(cast, night.line)
                and cast[3] > 0.99
                and contrast_ratio(cast[:3], night.bg) < 1.5,
                f"dark appearance, Increase Contrast: the {name}'s "
                "outline turns light and its hard shadow stays dark")
        controller.window.setAppearance_(None)

        # ---- A render that changes nothing lays nothing out and marks
        # nothing for drawing. A page is laid out only when its width or
        # its copy changed, a group is marked only when its size or its
        # separators did, and a tick of the 2-second timer that finds
        # nothing new does not render at all.
        page_documents = controller.dynamic["page_documents"]

        def work_done() -> tuple[Any, ...]:
            """How many times each page has been laid out, and each
            group, Home's and setup's included, marked for drawing."""
            return (
                tuple(sorted((name, document.arranged)
                             for name, document in page_documents.items())),
                tuple((name, tuple(section[1].marked
                                   for section in document.sections))
                      for name, document in page_documents.items()),
                home_group.marked, setup_group.marked)

        controller.render()
        root_view.layoutSubtreeIfNeeded()
        controller.render()
        settled = work_done()
        controller.render()
        controller.render()
        require(
            work_done() == settled,
            "two renders of an unchanged state lay no page out a second "
            "time and mark no group for drawing a second time")
        # New copy on one page lays that page out once, and no other.
        steady_state = model.state
        for name, changes in (
                ("Personalize", {"language": "ja"}),
                ("Controls", {"sound_theme": "silent"}),
                ("Privacy", {"recent_dictations": True}),
                ("Advanced", {"verification": "Passed just now"})):
            before = dict(work_done()[0])
            model.state = replace(steady_state, **changes)
            controller.render()
            controller.render()
            after = dict(work_done()[0])
            require(
                {page: after[page] - before[page] for page in after}
                == {page: int(page == name) for page in after},
                f"new copy on {name} lays out {name}, once, and no other "
                f"page")
            model.state = steady_state
            controller.render()
        # A change of width lays every page out, once.
        before = dict(work_done()[0])
        controller.window.setContentSize_(WINDOW_MIN_SIZE)
        root_view.layoutSubtreeIfNeeded()
        controller.render()
        controller.render()
        after = dict(work_done()[0])
        require(
            all(after[page] > before[page] for page in after)
            and all(round(document.content_width(), 2)
                    == document.arranged_for[0]
                    for document in page_documents.values()),
            "a new width lays every page out again")
        controller.window.setContentSize_(WINDOW_DEFAULT_SIZE)
        root_view.layoutSubtreeIfNeeded()
        controller.render()
        # The timer: the first tick below catches the view model up with
        # the runtime; the second finds nothing new.
        controller.refreshTimer_(None)
        root_view.layoutSubtreeIfNeeded()
        idle_renders, idle_work = controller.renders, work_done()
        controller.refreshTimer_(None)
        controller.refreshTimer_(None)
        # No render ran, so nothing was written to any view: nothing is
        # laid out and nothing is marked for drawing.
        require(
            controller.renders == idle_renders and work_done() == idle_work,
            "a timer tick that finds nothing new does not render, lays "
            "nothing out and marks no group for drawing")
        # Something new in the runtime, a flip of the appearance, or
        # Increase Contrast: each is rendered on the next tick.
        runtime["words_today"] = int(runtime.get("words_today", 0)) + 7
        controller.refreshTimer_(None)
        require(
            controller.renders == idle_renders + 1,
            "a timer tick renders what changed in the runtime")
        was_dark = _uses_dark_appearance(controller.window)
        controller.window.setAppearance_(NSAppearance.appearanceNamed_(
            NSAppearanceNameAqua if was_dark else NSAppearanceNameDarkAqua))
        controller.refreshTimer_(None)
        require(
            controller.renders == idle_renders + 2
            and controller._dark != was_dark,
            "a timer tick follows a flip of the appearance")
        controller.window.setAppearance_(None)
        controller.refreshTimer_(None)
        ticks = controller.renders

        def group_marks() -> list[int]:
            done = work_done()
            return [mark for _name, marks in done[1] for mark in marks] \
                + [done[2], done[3]]

        marked_before = group_marks()
        with_increased_contrast(lambda: controller.refreshTimer_(None))
        require(
            controller.renders == ticks + 1,
            "a timer tick follows Increase Contrast")
        # A group draws its hairline stronger under Increase Contrast, and
        # nothing about its layout changed: the render marks it itself.
        require(
            len(marked_before) > 10
            and group_marks() == [mark + 1 for mark in marked_before],
            "a flip of Increase Contrast marks every group for drawing, "
            "once")
        controller.refreshTimer_(None)

        # ---- The look of an editor sheet. Each one is built without being
        # shown and held to what the pages are held to: its groups are the
        # pages' groups, placed by the pages' code; its header carries the
        # chosen face; its footer is system buttons with a default and an
        # Escape; it stops at its cap and scrolls; nothing is cut short,
        # outside what holds it or on top of anything; Tab runs top to
        # bottom; and VoiceOver reads every control for what it does.
        private_settings.update(
            app_tones=[
                {"bundle": "com.apple.finder", "name": "Finder",
                 "tone": "formal"},
                {"bundle": "com.example.code", "name": "Code",
                 "tone": "code"},
                {"bundle": "com.example.mail", "name": "Mail",
                 "tone": "auto"},
            ],
            snippets=[
                {"name": "signature", "text": "Kind regards"},
                {"name": "address", "text": "1 Example Street\nSpringfield"},
                {"name": "standup", "text": "Nothing is blocking me. " * 12},
            ],
            manual_vocabulary=["Qwen", "Parakeet"],
            banned_vocabulary=["Gwen"],
            corrections=[
                {"key": "gwen", "source": "Gwen", "target": "Qwen",
                 "count": 4, "kind": "correction",
                 "global_decision": "active",
                 "app_scopes": [{"bundle": "com.example.mail",
                                 "name": "Mail", "count": 3,
                                 "decision": "active"}]},
                {"key": "teh", "source": "teh", "target": "the", "count": 1,
                 "kind": "correction"},
                {"key": "signature", "source": "Snippet: signature",
                 "target": "Kind regards,", "count": 1, "kind": "snippet"},
            ])
        private_keyword_export["candidates"].append({
            "keyword": "Parakeet", "app_scope": "app-0123456789abcdef",
            "observations": 3, "confirmations": 2, "eligible": True,
            "status": "eligible-not-connected-to-recognition"})
        model.load_settings()
        controller.render()
        sheets_checked: list[str] = []

        def sheet_leaves(sheet: Any, view: Any) -> list[Any]:
            """What a sheet shows, piece by piece. A control is one piece
            whatever it is built from, and so is a multi-line field with
            its scroll view; the body's scroll view is looked into."""
            if bool(view.isHidden()):
                return []
            if view is sheet.scroll:
                return sheet_leaves(sheet, view.documentView())
            if bool(view.isKindOfClass_(NSScrollView)) \
                    or bool(view.respondsToSelector_("cell")):
                return [view]
            children = [child for child in view.subviews()
                        if not bool(child.isHidden())]
            if not children:
                return [view]
            return [leaf for child in children
                    for leaf in sheet_leaves(sheet, child)]

        def takes_typing(view: Any) -> bool:
            return bool(view.isKindOfClass_(NSTextView)) or (
                bool(view.isKindOfClass_(NSTextField))
                and bool(view.isEditable()))

        edges = sheet_alignment

        def check_sheet(sheet: Any, name: str, *, cut: Sequence[Any] = (),
                        scrolls: bool = False) -> None:
            """One sheet as it stands. ``cut`` names the labels that are
            cut to one line on purpose; ``scrolls`` says whether this one
            is expected to be at its cap with its body scrolling."""
            sheets_checked.append(name)
            content, document = sheet.content, sheet.document
            content.layoutSubtreeIfNeeded()
            size = content.bounds().size
            height = float(size.height)
            body = sheet.scroll.frame()
            body_foot = float(body.origin.y + body.size.height)
            # Made of the pages' parts, placed by the pages' code.
            groups = [group for _header, group, _notes, _trailing
                      in document.sections]
            require(
                type(document) is GroupedPageView
                and type(sheet.scroll) is PageScrollView
                and sheet.scroll.documentView() == document
                and bool(groups)
                and all(type(group) is GroupBoxView for group in groups)
                and all(type(row) is SettingsRowView
                        for group in groups for row in group.rows),
                f"{name}: the body is a page of the pages' own groups "
                "and rows")
            check_rows(name, groups)
            page_header = controller.dynamic["page_documents"]["Controls"] \
                .sections[0][0]
            for header, group, notes, _trailing in document.sections:
                top = float(group.frame().origin.y)
                foot = top + float(group.frame().size.height)
                require(
                    abs(float(group.frame().origin.x)) < 0.01
                    and abs(float(group.frame().size.width)
                            - float(sheet.scroll.contentView()
                                    .bounds().size.width)) < 0.01,
                    f"{name}: a group fills the body's width")
                if header is not None:
                    require(
                        abs(float(header.frame().origin.x)
                            - SECTION_HEADER_INSET) < 0.01
                        and abs(top - float(
                            header.frame().origin.y
                            + header.frame().size.height)
                            - GROUP_HEADER_GAP) < 0.01
                        and header.font() == page_header.font()
                        and accessible_value(
                            header.cell(), "accessibilityRole")
                        == "AXHeading",
                        f"{name}: a section header sits over its group as "
                        "on a page, and is a heading")
                shown_notes = [note for note in notes
                               if not bool(note.isHidden())]
                if shown_notes:
                    require(
                        abs(float(shown_notes[0].frame().origin.y) - foot
                            - NOTE_GAP) < 0.01,
                        f"{name}: a note sits under its group as on a "
                        "page")
            # Size: inside the window at its minimum, and no taller than
            # the cap; past it the body scrolls and nothing else does.
            body_scrolls = float(document.frame().size.height) \
                > float(body.size.height) + 0.5
            require(
                abs(float(size.width) - SHEET_WIDTH) < 0.5
                and height <= SHEET_MAX_HEIGHT + 0.5
                and SHEET_WIDTH <= WINDOW_MIN_SIZE[0]
                - 2.0 * SHEET_WINDOW_MARGIN
                and SHEET_MAX_HEIGHT <= WINDOW_MIN_SIZE[1]
                - 2.0 * SHEET_WINDOW_MARGIN
                and abs(float(sheet.window.contentView().frame()
                              .size.height) - height) < 0.5,
                f"{name}: the sheet fits inside the window at its minimum")
            require(
                body_scrolls == scrolls
                and bool(sheet.rule.isHidden()) == (not scrolls)
                and (not scrolls or abs(height - SHEET_MAX_HEIGHT) < 0.5)
                and (scrolls or abs(float(document.frame().size.height)
                                    - float(body.size.height)) < 0.5)
                and abs(float(sheet.rule.frame().origin.y) - body_foot)
                < 0.5
                and float(sheet.scroll.documentVisibleRect().origin.y)
                < 0.5,
                f"{name}: the body is as tall as its content up to the "
                "cap, and scrolls from the top past it")
            # Header: the chosen face on its chip, the title, one line.
            face = model.state.face
            chip, title, message = sheet.chip, sheet.title, sheet.message
            require(
                type(chip) is FaceChipView and chip.face == face
                and chip.face_image == controller._face_image(
                    face, talk=False)
                and chip.superview() == content
                and edges(chip) == (
                    SHEET_INSET, SHEET_INSET, SHEET_INSET + SHEET_FACE_SIZE,
                    SHEET_INSET + SHEET_FACE_SIZE)
                and not bool(chip.isAccessibilityElement()),
                f"{name}: the header starts with the chosen face on its "
                "chip")
            text_left = SHEET_INSET + SHEET_FACE_SIZE + SHEET_HEADER_GAP
            require(
                title.font() == controller.dynamic["page_title"].font()
                and accessible_value(title.cell(), "accessibilityRole")
                == "AXHeading"
                and bool(str(title.stringValue()))
                and bool(str(message.stringValue()))
                and abs(float(title.frame().origin.x) - text_left) < 0.01
                and abs(float(message.frame().origin.x) - text_left) < 0.01
                and float(title.frame().origin.y) >= SHEET_INSET - 0.01
                and float(message.frame().origin.y)
                >= float(title.frame().origin.y
                         + title.frame().size.height)
                and float(message.frame().origin.y
                          + message.frame().size.height)
                <= float(body.origin.y) + 0.01
                and float(chip.frame().origin.y + chip.frame().size.height)
                <= float(body.origin.y) + 0.01,
                f"{name}: the title is a heading in the page-title type, "
                "with its line under it, both leading aligned and above "
                "the body")
            # Footer: system buttons at the regular size. The primary one
            # is at the trailing edge, holds Return and is the window's
            # default; Cancel is before it and holds Escape; an action
            # that adds or acts on the whole list is at the leading edge.
            buttons = sheet.buttons
            primary = buttons["save" if "save" in buttons else "done"]
            trailing = [buttons[key] for key in ("cancel", "save", "done")
                        if key in buttons]
            leading = [button for button in sheet.footer
                       if button not in trailing]
            require(
                bool(sheet.footer)
                and all(type(button) is PushButton
                        and int(button.bezelStyle())
                        == int(NSBezelStyleRounded)
                        and bool(button.isBordered())
                        and int(button.controlSize()) == 0
                        and button.superview() == content
                        and button.target() == controller
                        and str(button.action()) == "sheetChose:"
                        for button in sheet.footer),
                f"{name}: the footer is system push buttons at the "
                "regular size")
            require(
                str(primary.keyEquivalent()) == "\r"
                and sheet.window.defaultButtonCell() == primary.cell()
                and abs(edges(primary)[2] - (SHEET_WIDTH - SHEET_INSET))
                < 0.5
                and abs(edges(primary)[3] - (height - SHEET_INSET)) < 0.5
                and edges(primary)[2] - edges(primary)[0]
                >= SHEET_BUTTON_MIN_WIDTH - 0.5,
                f"{name}: the primary button is at the trailing edge, "
                "holds Return and is the default button")
            if "cancel" in buttons:
                cancel = buttons["cancel"]
                require(
                    "save" in buttons and "done" not in buttons
                    and str(cancel.keyEquivalent()) == "\x1b"
                    and abs(edges(primary)[0] - edges(cancel)[2]
                            - ROW_CONTROL_GAP) < 0.5
                    and abs(edges(cancel)[1] - edges(primary)[1]) < 0.5,
                    f"{name}: Cancel sits before Save and holds Escape")
            else:
                require(
                    len(trailing) == 1,
                    f"{name}: a list that applies as it goes has one Done")
            if leading:
                require(
                    abs(edges(leading[0])[0] - SHEET_INSET) < 0.5
                    and all(edges(button)[2] < edges(trailing[0])[0]
                            and abs(edges(button)[1] - edges(primary)[1])
                            < 1.0 for button in leading)
                    and all(edges(after)[0] - edges(before)[2]
                            >= ROW_CONTROL_GAP - 0.5
                            for before, after in zip(leading, leading[1:])),
                    f"{name}: actions on the whole list sit at the "
                    "footer's leading edge")
            require(
                all(edges(button)[1] >= body_foot + SHEET_FOOTER_GAP - 0.5
                    for button in sheet.footer),
                f"{name}: the footer is under the body, never in it")
            # Nothing is cut short, outside what holds it, or on top of
            # something else: in the body, and round it.
            inside, around = [], []
            for leaf in sheet_leaves(sheet, content):
                if leaf is content or bool(
                        leaf.isKindOfClass_(GroupBoxView)):
                    continue
                own = leaf.alignmentRectForFrame_(leaf.frame())
                holder = leaf.superview().bounds()
                what = f"{name}: a {type(leaf).__name__}"
                require(
                    own.origin.x >= holder.origin.x - 0.5
                    and own.origin.y >= holder.origin.y - 0.5
                    and own.origin.x + own.size.width
                    <= holder.origin.x + holder.size.width + 0.5
                    and own.origin.y + own.size.height
                    <= holder.origin.y + holder.size.height + 0.5,
                    f"{what} spills out of what holds it")
                require(
                    leaf in cut or takes_typing(leaf) or text_fits(leaf),
                    f"{what} is cut short")
                in_body = document in ancestors_of(leaf)
                space = document if in_body else content
                rect = space.convertRect_fromView_(own, leaf.superview())
                if rect.size.width > 0.0 and rect.size.height > 0.0:
                    (inside if in_body else around).append(rect)
            around.append(body)
            for placed in (inside, around):
                for index, first in enumerate(placed):
                    for second in placed[index + 1:]:
                        require(
                            not intersects(first, second),
                            f"{name}: two things overlap")
            require(
                len(inside) >= 1 and len(around) >= 5,
                f"{name}: the layout scan read the sheet "
                f"({len(inside)}, {len(around)})")
            # Keyboard: Tab runs through every control top to bottom, then
            # the footer in reading order, and comes round again; the
            # first control that takes typing has the focus.
            keys = sheet.key_views
            controls = [
                leaf for leaf in sheet_leaves(sheet, content)
                if bool(leaf.isKindOfClass_(NSButton))
                or takes_typing(leaf)] + [
                leaf.documentView()
                for leaf in sheet_leaves(sheet, content)
                if bool(leaf.isKindOfClass_(NSScrollView))]
            body_keys = [view for view in keys if view not in sheet.footer]
            order = [
                (round(float(document.convertRect_fromView_(
                    view.bounds(), view).origin.y)),
                 round(float(document.convertRect_fromView_(
                     view.bounds(), view).origin.x)))
                for view in body_keys]
            typing = [view for view in keys if takes_typing(view)]
            require(
                bool(keys) and len(set(keys)) == len(keys)
                and set(controls) == set(keys)
                and keys == body_keys + sheet.footer
                and order == sorted(order)
                and [edges(button)[0] for button in sheet.footer]
                == sorted(edges(button)[0] for button in sheet.footer)
                and all(view.nextKeyView() == following
                        for view, following in zip(
                            keys, keys[1:] + keys[:1]))
                and sheet.window.initialFirstResponder()
                == (typing[0] if typing else keys[0])
                and not bool(sheet.window.autorecalculatesKeyViewLoop()),
                f"{name}: Tab reaches every control, top to bottom and "
                "then the footer, starting at the first field")
            # VoiceOver: the label is on the element it reads, a row's
            # button says what it does to what, no image is a stop, and
            # nothing shown or said uses one of the runtime's own words.
            stops = check_voiceover(name, content, plain_words=True)
            shown_words = [
                str(leaf.stringValue()) if bool(
                    leaf.isKindOfClass_(NSTextField)) else str(leaf.title())
                for leaf in sheet_leaves(sheet, content)
                if (bool(leaf.isKindOfClass_(NSTextField))
                    and not bool(leaf.isEditable()))
                or bool(leaf.isKindOfClass_(NSButton))]
            require(
                all(control in stops for control in keys)
                and str(title.stringValue()) in shown_words
                and str(message.stringValue()) in shown_words
                and len(shown_words) >= 4,
                f"{name}: the VoiceOver walk and the word scan read the "
                "sheet")
            for words in shown_words:
                require(
                    not internal_words_in(words),
                    f"{name} shows an internal word: {words!r}")

        def checked(name: str, answer: Any = None, **options: Any) -> Any:
            """A step that checks the sheet it is handed, then answers."""
            def step(sheet: Any) -> Any:
                check_sheet(sheet, name, **options)
                if answer is None:
                    # Escape, as the sheet's window receives it.
                    sheet.window.cancelOperation_(None)
                    require(sheet.choice is not None,
                            f"{name}: Escape answers the sheet")
                    return None
                return answer(sheet) if callable(answer) else answer
            return step

        # App tones: a real app's own icon, a plain symbol for one that
        # is not on this Mac, and Escape cancels.
        def tones_rows(sheet: Any) -> Any:
            rows = sheet.document.sections[0][1].rows
            finder, missing = rows[0].leading, rows[1].leading
            drawn = rendered(finder)
            colours = {
                tuple(round(part, 1) for part in pixel(
                    drawn, x, y)[:3])
                for x in range(6, 24, 3) for y in range(6, 24, 3)}
            require(
                app_icon_image("com.apple.finder")[1]
                and bool(finder.image().representations()[0]
                         .isKindOfClass_(NSBitmapImageRep))
                and len(colours) >= 3,
                "an app that is on this Mac shows its own icon, drawn "
                "when the row is made")
            require(
                not app_icon_image("com.example.code")[1]
                and missing in sheet.themed.get("symbol", ())
                and bool(missing.image().isTemplate())
                and all(float(row.leading.frame().size.width)
                        == SHEET_APP_ICON_SIZE for row in rows)
                and str(rows[1].title.toolTip()) == "com.example.code"
                and not any("com.example" in str(leaf.stringValue())
                            for leaf in sheet_leaves(sheet, sheet.content)
                            if bool(leaf.isKindOfClass_(NSTextField))),
                "an app that is not on this Mac shows a plain symbol, and "
                "a bundle identifier is help, never text")
            return None

        tones_escaped = drive(controller.editTone_, lambda sheet: (
            tones_rows(sheet), checked("app tones")(sheet))[1])
        require(
            tones_escaped[0].choice == ("cancel", None),
            "Escape cancels a sheet that has a Cancel")

        # Snippets: the list, where a long first line is cut to one line
        # and Escape is Done; then the form, where Tab leaves the text
        # and Escape typed in it cancels the form, not the sheet.
        def snippet_list(sheet: Any) -> Any:
            rows = sheet.document.sections[0][1].rows
            long_row = rows[2]
            require(
                abs(float(long_row.frame().size.height)
                    - float(rows[0].frame().size.height)) < 0.01
                and not text_fits(long_row.caption)
                and str(rows[1].caption.stringValue())
                == "1 Example Street",
                "a snippet's row shows the first line of its text, cut "
                "to one line")
            for action, label_key in (
                    ("edit", "sheets.snippets.edit.label"),
                    ("delete", "sheets.snippets.delete.label")):
                button = sheet.buttons[f"{action}:signature"]
                require(
                    accessible_value(button.cell(), "accessibilityLabel")
                    == localized_string(label_key, name="signature")
                    and button.superview() == rows[0],
                    f"a snippet's {action} button says what it does to "
                    "which snippet")
            check_sheet(sheet, "snippets", cut=(long_row.caption,))
            require(
                str(long_row.caption.toolTip()) ==
                str(long_row.caption.stringValue())
                and long_row.title.toolTip() is None
                and rows[0].title.toolTip() is None
                and rows[0].caption.toolTip() is None,
                "a line that is cut shows all of it as its tooltip, and "
                "a line that fits has none")
            try:
                sheet.press("edit:nothing")
            except KeyError:
                pass
            else:
                require(False, "a sheet has no button it was not given")
            sheet.buttons["delete:address"].setEnabled_(False)
            try:
                sheet.press("delete:address")
            except ValueError:
                pass
            else:
                require(False, "a disabled button cannot be pressed")
            # The keyboard is on this row's Edit when it is pressed.
            require(
                bool(sheet.window.makeFirstResponder_(
                    sheet.buttons["edit:address"])),
                "a row's button can hold the keyboard")
            return "edit:address"

        def bare_field(editor: Any) -> bool:
            """A multi-line field with no border and no fill of its own,
            filling its row to the group's edge at each side."""
            scroll = editor.enclosingScrollView()
            row = scroll.superview()
            return (
                int(scroll.borderType()) == int(NSNoBorder)
                and not bool(scroll.drawsBackground())
                and not bool(editor.drawsBackground())
                and type(row) is SettingsRowView
                and len(row.superview().rows) == 1
                and abs(float(scroll.frame().origin.x) - 1.0) < 0.01
                and abs(float(scroll.frame().size.width)
                        - (float(row.frame().size.width) - 2.0)) < 0.01
                and abs(float(editor.textContainerInset().width)
                        + float(editor.textContainer().lineFragmentPadding())
                        + 1.0 - ROW_INSET) < 0.01)

        def snippet_form(sheet: Any) -> Any:
            check_sheet(sheet, "snippet form")
            text, cancel = sheet.controls["text"], sheet.buttons["cancel"]
            name = sheet.controls["name"]
            named, texted = sheet.document.sections
            require(
                [str(section[0].stringValue())
                 for section in (named, texted)] == [
                    localized_string("sheets.snippet.name"),
                    localized_string("sheets.snippet.text")]
                and name.superview() == named[1].rows[0]
                and not bool(name.isBezeled())
                and not bool(name.drawsBackground())
                and not bool(name.isEditable())
                and abs(float(name.frame().origin.x) - ROW_INSET) < 0.01
                and bare_field(text)
                and [str(note.stringValue()) for note in named[2]
                     if not bool(note.isHidden())] == [
                    localized_string("sheets.snippet.speak")]
                and str(sheet.window.title()) ==
                localized_string("settings.dialog.snippet.edit"),
                "the form is two fields, each filling its own group under "
                "its name, with no border of its own; how to say the "
                "snippet is under its name")
            require(
                type(text) is SheetTextView
                and sheet.window.firstResponder() == text
                and str(text.font().fontName()) ==
                str(NSFont.systemFontOfSize_(13.0).fontName())
                and not bool(text.isRichText())
                and not bool(text.isAutomaticQuoteSubstitutionEnabled())
                and not bool(text.isAutomaticDashSubstitutionEnabled())
                and not bool(text.isAutomaticTextReplacementEnabled())
                and not bool(text.isAutomaticSpellingCorrectionEnabled())
                and bool(text.allowsUndo()),
                "the form's text field has the focus, the system's type, "
                "and keeps exactly what is typed")
            require(
                text.nextKeyView() == cancel,
                "Tab leaves the form's text for the footer")
            text.cancelOperation_(None)
            return None

        def back_in_the_list(sheet: Any) -> Any:
            require(
                str(sheet.title.stringValue()) ==
                localized_string("settings.personalize.snippets")
                and "add" in sheet.buttons,
                "cancelling the form goes back to the list")
            require(
                sheet.window.firstResponder() ==
                sheet.buttons["edit:address"],
                "back in the list, the keyboard is where it was")
            sheet.window.cancelOperation_(None)
            require(sheet.choice == ("done", None),
                    "Escape is Done in a sheet with no Cancel")
            return None

        snippets_escaped = drive(
            controller.editSnippets_, snippet_list, snippet_form,
            back_in_the_list)
        require(
            snippets_escaped[0].choice == ("done", None),
            "Escape is Done in a sheet with no Cancel")
        def new_snippet_form(sheet: Any) -> Any:
            check_sheet(sheet, "new snippet form")
            name = sheet.controls["name"]
            require(
                bool(name.isEditable()) and not bool(name.isBezeled())
                and not bool(name.drawsBackground())
                and name.superview() == sheet.document.sections[0][1].rows[0]
                and sheet.window.firstResponder() in (
                    name, name.currentEditor())
                and name.nextKeyView() == sheet.controls["text"],
                "a new snippet's name is a field that fills its group, "
                "and has the keyboard")
            sheet.controls["text"].setString_("Typed text")
            return "save"

        def name_is_asked_for(sheet: Any) -> Any:
            named = sheet.document.sections[0]
            require(
                notice_of(sheet) ==
                localized_string("validation.snippet.required")
                and sheet.controls["notice"] in named[2]
                and str(sheet.controls["text"].string()) == "Typed text"
                and sheet.window.firstResponder() in (
                    sheet.controls["name"],
                    sheet.controls["name"].currentEditor()),
                "a snippet saved with no name is asked for one, under "
                "the name, with the keyboard in it and the text kept")
            sheet.controls["name"].setStringValue_("greeting")
            sheet.controls["text"].setString_("   ")
            return "save"

        def text_is_asked_for(sheet: Any) -> Any:
            named, texted = sheet.document.sections
            require(
                "1–4000" in notice_of(sheet)
                and sheet.controls["notice"] in texted[2]
                and sheet.controls["notice"] not in named[2]
                and sheet.window.firstResponder() == sheet.controls["text"]
                and str(sheet.controls["name"].stringValue()) == "greeting",
                "a snippet refused for its text says so under the text, "
                "with the keyboard in it and the name kept")
            return "cancel"

        saves_before = len(calls_of("save_snippet"))
        drive(controller.editSnippets_, "add", new_snippet_form,
              name_is_asked_for, text_is_asked_for, "done")
        require(
            len(calls_of("save_snippet")) == saves_before,
            "a snippet with no name, or no text, saves nothing")

        # Vocabulary: Tab moves from one list to the other instead of
        # typing a tab, and Shift-Tab back.
        def vocabulary_fields(sheet: Any) -> Any:
            check_sheet(sheet, "vocabulary")
            terms, bans = sheet.controls["terms"], sheet.controls["bans"]
            require(
                sheet.window.firstResponder() == terms
                and [str(header.stringValue()) for header, _group, _notes,
                     _trailing in sheet.document.sections] == [
                    localized_string("settings.dialog.vocabulary.terms"),
                    localized_string("settings.dialog.vocabulary.bans")]
                and all(notes and str(notes[-1].stringValue())
                        for _header, _group, notes, _trailing
                        in sheet.document.sections),
                "each vocabulary list is a field in its own group, with "
                "a header and a line that says what it is for")
            require(
                bare_field(terms) and bare_field(bans)
                and str(sheet.window.title()) ==
                localized_string("settings.dialog.vocabulary.title"),
                "a vocabulary list fills its group with no border of its "
                "own, and the sheet's window is named for the sheet")
            terms.insertTab_(None)
            moved = sheet.window.firstResponder() == bans
            bans.insertBacktab_(None)
            require(
                moved and sheet.window.firstResponder() == terms
                and str(terms.string()) == "Qwen\nParakeet",
                "Tab and Shift-Tab move between the lists and type "
                "nothing")
            return "cancel"

        drive(controller.editVocabulary_, vocabulary_fields)

        def exclude_a_reserved_term(sheet: Any) -> Any:
            sheet.controls["bans"].setString_("#Gwen")
            return "save"

        def keyboard_is_in_the_second_list(sheet: Any) -> Any:
            require(
                sheet.controls["notice"] in sheet.document.sections[1][2]
                and sheet.window.firstResponder() == sheet.controls["bans"]
                and "A excluded" not in notice_of(sheet)
                and localized_string("validation.vocabulary.excluded")
                in notice_of(sheet),
                "a refusal about the excluded terms is under that list, "
                "with the keyboard in it")
            return "cancel"

        drive(controller.editVocabulary_, exclude_a_reserved_term,
              keyboard_is_in_the_second_list)

        # Return starts a new line in a multi-line field, so Command-Return
        # presses the default button from there.
        def command_return(sheet: Any) -> Any:
            event = NSEvent \
                .keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                    10, (0.0, 0.0), _COMMAND_KEY_FLAG, 0.0,
                    sheet.window.windowNumber(), None, "\r", "\r", False,
                    36)
            plain = NSEvent \
                .keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                    10, (0.0, 0.0), _COMMAND_KEY_FLAG, 0.0,
                    sheet.window.windowNumber(), None, "j", "j", False, 38)
            require(
                sheet.window.firstResponder() == sheet.controls["terms"]
                and not bool(sheet.window.performKeyEquivalent_(plain))
                and sheet.choice is None,
                "another Command key is not the sheet's")
            sheet.controls["bans"].setString_("Gwen\nGwyn")
            require(
                bool(sheet.window.performKeyEquivalent_(event)),
                "Command-Return is the sheet's")
            return None

        saved_before = len(calls_of("vocabulary"))
        command_saved = drive(controller.editVocabulary_, command_return)
        require(
            command_saved[0].choice == ("save", None)
            and calls_of("vocabulary")[saved_before:] ==
            [("vocabulary", ("Qwen", "Parakeet"), ("Gwen", "Gwyn"))],
            "Command-Return saves from inside a multi-line field")

        # Learned corrections and pronunciation keywords: rows that say
        # where each stands, and a button that says what it forgets.
        def correction_rows(sheet: Any) -> Any:
            check_sheet(sheet, "learned corrections")
            words, edits = sheet.document.sections
            row = words[1].rows[0]
            forget = sheet.buttons["forget:correction:gwen"]
            require(
                [str(header.stringValue()) for header in
                 (words[0], edits[0])] == [
                    localized_string("sheets.corrections.words"),
                    localized_string("sheets.corrections.snippets")]
                and str(row.title.stringValue()) == "Gwen → Qwen"
                and str(row.caption.stringValue()) ==
                "On in every app\nSeen in Mail 3×"
                and str(row.caption.toolTip()) == correction_review_text(
                    model.state.settings.corrections[0])
                and accessible_value(forget.cell(), "accessibilityLabel")
                == localized_string(
                    "sheets.corrections.forget.label",
                    source="Gwen", target="Qwen")
                and forget.superview() == row
                and str(words[2][-1].stringValue()) ==
                correction_review_text(
                    model.state.settings.corrections[0]).splitlines()[-1],
                "a learned correction's row says what is written for "
                "what, and where it stands; its group says what is "
                "stored")
            require(
                str(edits[1].rows[0].title.stringValue()) ==
                "signature → Kind regards,"
                and accessible_value(
                    sheet.buttons["forget:snippet:signature"].cell(),
                    "accessibilityLabel") == localized_string(
                        "sheets.corrections.forget.label",
                        source="Snippet: signature",
                        target="Kind regards,"),
                "a snippet edit's row names the snippet once; its button "
                "still says in full what it forgets")
            # The keyboard is on the first row's Forget when it is pressed.
            require(
                bool(sheet.window.makeFirstResponder_(
                    sheet.buttons["forget:correction:gwen"])),
                "a row's button can hold the keyboard")
            return "forget:correction:gwen"

        def keyboard_is_on_the_next_row(sheet: Any) -> Any:
            require(
                "forget:correction:gwen" not in sheet.buttons
                and sheet.window.firstResponder() ==
                sheet.buttons["forget:correction:teh"],
                "after a row is forgotten the keyboard is on the row "
                "now in its place")
            return "done"

        corrections_kept = private_settings["corrections"]
        drive(controller.reviewCorrections_, correction_rows, 1000,
              keyboard_is_on_the_next_row)
        private_settings["corrections"] = corrections_kept
        model.load_settings()

        def keyword_rows(sheet: Any) -> Any:
            check_sheet(sheet, "pronunciation keywords")
            rows = sheet.document.sections[0][1].rows
            require(
                [str(row.title.stringValue()) for row in rows] ==
                ["Qwen", "Parakeet"]
                and str(rows[1].caption.stringValue()) == localized_string(
                    "sheets.keywords.caption",
                    observations="3 observations",
                    confirmations="2 confirmations",
                    status=localized_string(
                        "sheets.keywords.status.eligible"),
                    scope=localized_string(
                        "sheets.keywords.scope.private_app"))
                and accessible_value(
                    sheet.buttons["forget:Qwen"].cell(),
                    "accessibilityLabel") == localized_string(
                        "sheets.keywords.forget.label", keyword="Qwen")
                and [str(button.title()) for button in sheet.footer] == [
                    localized_string("settings.action.export"),
                    localized_string("sheets.keywords.forget_all"),
                    localized_string("settings.action.done")],
                "a keyword's row says what was seen of it, and the "
                "footer copies them out or forgets them all")
            require(
                all(bool(button.isEnabled()) for button in sheet.footer),
                "with keywords listed, every footer button is on")
            return "done"

        drive(controller.inspectKeywords_, keyword_rows)

        # Each editor with nothing in it says so in a sentence.
        filled = {key: private_settings[key] for key in (
            "app_tones", "snippets", "manual_vocabulary",
            "banned_vocabulary", "corrections")}
        kept_candidates = list(private_keyword_export["candidates"])

        def says(sheet: Any, key: str) -> bool:
            rows = sheet.document.sections[0][1].rows
            return len(rows) == 1 and localized_string(key) in [
                str(view.stringValue()) for view in rows[0].subviews()]

        private_settings.update(
            snippets=[], manual_vocabulary=[], banned_vocabulary=[],
            corrections=[filled["corrections"][1]])
        private_keyword_export["candidates"] = []
        model.load_settings()
        drive(controller.editSnippets_, lambda sheet: (
            require(says(sheet, "settings.empty.snippets"),
                    "an empty snippet list says so"),
            checked("no snippets", "done")(sheet))[1])
        drive(controller.editVocabulary_, lambda sheet: (
            require(not str(sheet.controls["terms"].string())
                    and not str(sheet.controls["bans"].string()),
                    "an empty vocabulary opens with two empty fields"),
            checked("no vocabulary", "cancel")(sheet))[1])
        drive(controller.reviewCorrections_, "forget:correction:teh", 1000,
              lambda sheet: (
                  require(says(sheet, "settings.empty.corrections"),
                          "an empty list of corrections says so"),
                  checked("no corrections", "done")(sheet))[1])
        drive(controller.inspectKeywords_, lambda sheet: (
            require(says(sheet, "sheets.keywords.empty"),
                    "an empty keyword list says so"),
            require(
                [bool(button.isEnabled()) for button in sheet.footer]
                == [False, False, True],
                "with no keyword there is nothing to copy or forget: "
                "only Done is on"),
            checked("no keywords", "done")(sheet))[2])
        private_keyword_export["candidates"] = kept_candidates

        # A list too long for the sheet: the sheet stops at its cap and
        # the body scrolls; the header and the footer stay where they are.
        private_settings.update(filled)
        private_settings["app_tones"] = filled["app_tones"] + [
            {"bundle": f"com.example.app-{index}", "name": f"App {index}",
             "tone": "auto"} for index in range(12)]
        model.load_settings()

        def long_list(sheet: Any) -> Any:
            check_sheet(sheet, "a long list of apps", scrolls=True)
            document = sheet.document
            clip = sheet.scroll.contentView()
            before = [edges(view) for view in (
                sheet.chip, sheet.title, *sheet.footer)]
            document.scrollPoint_((0.0, 1.0e6))
            last = document.sections[0][1].rows[-1]
            foot = document.convertRect_fromView_(
                last.bounds(), last)
            visible = clip.documentVisibleRect()
            require(
                float(visible.origin.y) > 0.0
                and float(foot.origin.y + foot.size.height)
                <= float(visible.origin.y + visible.size.height) + 0.5
                and [edges(view) for view in (
                    sheet.chip, sheet.title, *sheet.footer)] == before,
                "scrolling to the end shows the last row, and moves "
                "neither the header nor the footer")
            return "cancel"

        drive(controller.editTone_, long_list)
        long_name = "Notes for the Customer Success Team " * 3
        private_settings["app_tones"] = filled["app_tones"] + [
            {"bundle": "com.example.long", "name": long_name.strip(),
             "tone": "auto"}]
        model.load_settings()

        def long_app_name(sheet: Any) -> Any:
            rows = sheet.document.sections[0][1].rows
            require(
                not text_fits(rows[-1].title)
                and str(rows[-1].title.toolTip()) ==
                f"{long_name.strip()}\ncom.example.long"
                and str(rows[0].title.toolTip()) == "com.apple.finder"
                and abs(float(rows[-1].frame().size.height)
                        - float(rows[0].frame().size.height)) < 0.01,
                "an app name too long for its row is cut to one line, "
                "with all of it ahead of the bundle identifier as help")
            return "cancel"

        drive(controller.editTone_, long_app_name)
        private_settings["app_tones"] = filled["app_tones"]
        model.load_settings()

        # The header's chip is the sidebar's chip at another size: the
        # same corner and inset in proportion, the face's own colour, a
        # hairline edge, the face inside it.
        sidebar_chip = controller.dynamic["window_face_chip"]
        sidebar_art = controller.dynamic["window_face"]
        require(
            abs(float(sidebar_chip.layer().cornerRadius())
                / SIDEBAR_FACE_SIZE
                - min(BRAND_SHAPES["face_chip"].radius,
                      SHEET_FACE_SIZE * 0.28) / SHEET_FACE_SIZE) < 0.001
            and abs((float(sidebar_art.frame().origin.x)
                     - float(sidebar_chip.frame().origin.x))
                    / SIDEBAR_FACE_SIZE - FACE_CHIP_ART_INSET) < 0.001
            and float(sidebar_chip.layer().borderWidth()) == 1.0,
            "the sheet's chip keeps the sidebar chip's proportions")

        # Both appearances, and Increase Contrast, as the pages do it:
        # the window's field colour behind, the pages' ink on the labels,
        # and a group that draws exactly what a page's group draws.
        def sheet_look(dark_look: bool, contrast: bool) -> Callable:
            def step(sheet: Any) -> Any:
                palette = palette_for_appearance(dark_look)
                where = (f"{'dark' if dark_look else 'light'} appearance"
                         + (", Increase Contrast" if contrast else ""))
                drawn = rendered(sheet.content)
                group = sheet.document.sections[0][1]
                colour = lambda label: tuple(
                    float(part) for part in (
                        label.textColor().redComponent(),
                        label.textColor().greenComponent(),
                        label.textColor().blueComponent()))
                row = group.rows[0]
                require(
                    _uses_dark_appearance(sheet.window) == dark_look
                    and near(pixel(drawn, SHEET_WIDTH / 2.0, 4.0),
                             palette.bg, 0.01)
                    and near(pixel(drawn, 4.0, float(
                        sheet.content.bounds().size.height) - 4.0),
                        palette.bg, 0.01),
                    f"a sheet is on the window's field colour, in {where}")
                require(
                    near(colour(sheet.title), palette.ink, 0.01)
                    and near(colour(sheet.message), palette.ink_soft, 0.01)
                    and near(colour(row.title), palette.ink, 0.01)
                    and row.title.font() == page_group.rows[0].title.font(),
                    f"a sheet's labels take the pages' ink, in {where}")
                require(
                    len(group.separators) >= 1
                    and all(near(got, expected, 0.01)
                            for got, expected in zip(
                                group_look(group), shown_while(
                                    page_group,
                                    lambda: group_look(page_group)))),
                    f"a sheet's group draws what a settings group draws: "
                    f"the fill, the edge, the corner and a separator, "
                    f"in {where}")
                chip = rendered(sheet.chip)
                side = SHEET_FACE_SIZE
                art = {tuple(round(part, 1) for part in pixel(
                    chip, x, y)[:3])
                    for x in range(8, 33, 4) for y in range(8, 33, 4)}
                chip_colour = FACE_CHIP_COLORS[model.state.face]
                chip_edge = color_token(
                    "hairline", dark_look, contrast).over(chip_colour)
                require(
                    near(pixel(chip, 2.5, side / 2.0), chip_colour, 0.02)
                    and pixel(chip, 0.5, 0.5)[3] < 0.05
                    and near(pixel(chip, side / 2.0, 0.25), chip_edge,
                             0.02)
                    and not near(chip_edge, chip_colour, 0.02)
                    and len(art) >= 2,
                    f"the header's chip is the face's colour with a "
                    f"hairline edge, round corners and the face in it, "
                    f"in {where}")
                return "cancel"
            return step

        # A field in a sheet has no surface of its own: where nothing is
        # typed it shows the group it fills, and what is typed is in the
        # pages' ink. (A system text view left to itself is a neutral
        # grey in the dark appearance, darker than the sheet round it.)
        def field_look(dark_look: bool) -> Callable:
            def step(sheet: Any) -> Any:
                palette = palette_for_appearance(dark_look)
                drawn = rendered(sheet.content)
                for name in ("terms", "bans"):
                    editor = sheet.controls[name]
                    scroll = editor.enclosingScrollView()
                    box = sheet.content.convertRect_fromView_(
                        scroll.bounds(), scroll)
                    foot = float(box.origin.y + box.size.height) - 6.0
                    ink = editor.textColor()
                    require(
                        near(pixel(drawn, float(box.origin.x) + 6.0, foot),
                             palette.surface, 0.01)
                        and near(pixel(drawn, float(
                            box.origin.x + box.size.width) - 40.0, foot),
                            palette.surface, 0.01)
                        and near((float(ink.redComponent()),
                                  float(ink.greenComponent()),
                                  float(ink.blueComponent())),
                                 palette.ink, 0.01),
                        f"a field in a sheet is the colour of the group "
                        f"it fills, typed in the pages' ink, in the "
                        f"{'dark' if dark_look else 'light'} appearance")
                return "cancel"
            return step

        for dark_look in (False, True):
            controller.window.setAppearance_(NSAppearance.appearanceNamed_(
                NSAppearanceNameDarkAqua if dark_look
                else NSAppearanceNameAqua))
            controller.render()
            drive(controller.editVocabulary_, field_look(dark_look))
            for contrast in (False, True):
                look = lambda: drive(
                    controller.editTone_, sheet_look(dark_look, contrast))
                if contrast:
                    with_increased_contrast(look)
                else:
                    look()
        # A flip of the appearance while a sheet is open repaints it.
        controller.window.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameAqua))

        def flip(sheet: Any) -> Any:
            light = pixel(rendered(sheet.content), SHEET_WIDTH / 2.0, 4.0)
            sheet.window.setAppearance_(
                NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
            sheet.content.layoutSubtreeIfNeeded()
            dark = pixel(rendered(sheet.content), SHEET_WIDTH / 2.0, 4.0)
            require(
                near(light, palette_for_appearance(False).bg, 0.01)
                and near(dark, palette_for_appearance(True).bg, 0.01),
                "a sheet follows a flip of the appearance while it is "
                "open")
            return "cancel"

        drive(controller.editTone_, flip)
        controller.window.setAppearance_(None)
        controller.render()

        # ---- The path a real click takes. With no responder a sheet is
        # put on its window and a modal session runs until a button stops
        # it. Nothing may be shown here, and a check that fails must
        # still end, so for as long as this lasts the application the
        # window module asks is a stand-in (as NSEvent was above): no
        # real modal session can start, whatever is broken. The window
        # the sheet hangs from is a stand-in too. Both write down what
        # they are asked, and the session presses a button the way a
        # click does.
        modal_log: list[tuple[Any, ...]] = []

        class HostWindow:
            def isVisible(self) -> bool:
                return True

            def beginSheet_completionHandler_(
                    self, window: Any, handler: Any) -> None:
                modal_log.append(("begin", window, handler))

            def endSheet_(self, window: Any) -> None:
                modal_log.append(("end", window))

        class ModalSession:
            sheet: Any = None
            answers: list[Any] = []

            @classmethod
            def sharedApplication(cls) -> Any:
                return cls

            @classmethod
            def runModalForWindow_(cls, window: Any) -> int:
                modal_log.append(("run", window, cls.sheet.running))
                answer = cls.answers.pop(0) if cls.answers else None
                if answer is not None:
                    cls.sheet.press(answer)
                return cls.sheet.tag

            @classmethod
            def stopModalWithCode_(cls, code: int) -> None:
                modal_log.append(("stop", code, cls.sheet.running))

        def refused_page(sheet: Any, notice: str) -> Callable:
            return lambda: controller._vocabulary_page(
                sheet, "Qwen", "Gwen", notice)

        host = HostWindow()
        second_editors: list[Any] = []
        appkit_application = NSApplication
        globals()["NSApplication"] = ModalSession
        sheet = controller._new_sheet()
        try:
            ModalSession.sheet = sheet
            controller._sheet_fill(sheet, refused_page(sheet, ""))
            ModalSession.answers = ["save"]
            answered = sheet.run(host, None)
            save_tag = int(sheet.buttons["save"].tag())
            require(
                modal_log == [
                    ("begin", sheet.window, None),
                    ("run", sheet.window, True),
                    ("stop", save_tag, True)]
                and answered == save_tag and sheet.choice == ("save", None)
                and sheet.parent is host and not sheet.running,
                "a sheet is put on its window, runs a modal session, and "
                "a button stops the session with its own answer")
            # Filled again while it is up, it stays on the window and
            # stays in the middle of it, where AppKit holds a sheet.
            before = sheet.window.frame()
            controller._sheet_fill(sheet, refused_page(
                sheet, "One line of refusal, to make the sheet taller."))
            after = sheet.window.frame()
            require(
                float(after.size.height) > float(before.size.height) + 4.0
                # (To the half point a window's frame is rounded to.)
                and abs(float(after.origin.y + after.size.height / 2.0)
                        - float(before.origin.y + before.size.height / 2.0))
                <= 0.5
                and abs(float(after.origin.x + after.size.width / 2.0)
                        - float(before.origin.x + before.size.width / 2.0))
                <= 0.5,
                "an open sheet that grows stays where it is, about its "
                "middle")
            # Run again through the window's own seam: no second sheet
            # is begun, and a session that ends with no button pressed
            # is Escape.
            ModalSession.answers = [None]
            answer = controller._run_sheet(sheet)
            require(
                modal_log[3:] == [("run", sheet.window, True)]
                and answer == ("cancel", None)
                and sheet.tag == int(sheet.buttons["cancel"].tag())
                and controller._sheet is sheet,
                "a sheet that is already up is run again where it is, "
                "and a run that ends with no answer is Cancel")
            # One editor at a time: a second is refused, not opened. (If
            # one were opened, the responder would answer it.)
            refused_second = False
            controller.modal_responder = lambda dialog: (
                second_editors.append(dialog), "cancel")[1]
            try:
                controller.editVocabulary_(None)
            except Exception:
                refused_second = True
            finally:
                controller.modal_responder = None
            require(
                refused_second and not second_editors
                and controller._sheet is sheet and len(modal_log) == 4,
                "a second editor is refused while one is open")
        finally:
            controller.modal_responder = None
            controller._end_sheet(sheet)
            globals()["NSApplication"] = appkit_application
        require(
            modal_log[4:] == [("end", sheet.window)]
            and sheet.parent is None and controller._sheet is None
            and not bool(sheet.window.isVisible()),
            "closing an editor takes its sheet off the window it is on")

        # A refusal is said aloud as it appears: it is under a group,
        # where the keyboard is not.
        import AppKit as _appkit_for_announcing
        announced: list[tuple[Any, ...]] = []
        post = _appkit_for_announcing.NSAccessibilityPostNotificationWithUserInfo
        _appkit_for_announcing.NSAccessibilityPostNotificationWithUserInfo = (
            lambda element, name, info: announced.append(
                (element, str(name), dict(info))))
        try:
            said_in = drive(
                controller.editVocabulary_, type_reserved_term, "cancel")
        finally:
            _appkit_for_announcing \
                .NSAccessibilityPostNotificationWithUserInfo = post
        refusal_said = [
            entry for entry in announced
            if entry[1] == str(_appkit_for_announcing
                               .NSAccessibilityAnnouncementRequestedNotification)]
        require(
            len(refusal_said) == 1
            and refusal_said[0][0] == said_in[0].window
            and "reserved" in refusal_said[0][2][
                _appkit_for_announcing.NSAccessibilityAnnouncementKey]
            and refusal_said[0][2][
                _appkit_for_announcing.NSAccessibilityPriorityKey] ==
            _appkit_for_announcing.NSAccessibilityPriorityHigh,
            "a refusal in a sheet is announced to VoiceOver, once, with "
            "its words")

        # The alerts that are left carry the face too: its chip, drawn as
        # the icon, in the place of the folder.
        seen_alerts = drive(controller.viewModes_, 1000)
        icon = seen_alerts[0].icon()
        icon_rep = NSBitmapImageRep.alloc().initWithData_(
            icon.TIFFRepresentation())
        icon_rep.setSize_(icon.size())
        require(
            not is_sheet(seen_alerts[0])
            and icon == controller._face_icons[model.state.face]
            and near(pixel(icon_rep, 4.0, float(icon.size().height) / 2.0),
                     FACE_CHIP_COLORS[model.state.face], 0.03)
            and pixel(icon_rep, 0.5, 0.5)[3] < 0.05,
            "an alert that is still an alert shows the chosen face on "
            "its chip, not a folder")
        require(
            sheets_checked == [
                "app tones", "snippets", "snippet form",
                "new snippet form", "vocabulary", "learned corrections",
                "pronunciation keywords", "no snippets", "no vocabulary",
                "no corrections", "no keywords", "a long list of apps"]
            and controller._sheet is None
            and {role: len(views)
                 for role, views in controller.themed.items()}
            == themed_before,
            "every editor's sheet was built, checked and let go")

        # Close, then do what the next "Open Whisper Face" does short of
        # ordering the window on screen.
        require(
            not bool(controller.window.isReleasedWhenClosed()),
            "closing keeps the window")
        controller.window.close()
        require(not bool(controller.window.isVisible()), "closed window hidden")
        model.refresh()
        controller.render()
        return {
            "sections": len(SECTIONS),
            "sidebar_rows": len(controller.sidebar_rows),
            "settings_panes": len(SETTINGS_PANES),
            "model_actions": len(expected_calls),
            "onboarding_steps": len(model.state.onboarding_steps),
            "key_equivalents": len(
                native_appkit_smoke_contract().key_equivalents),
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

    def feed_level(self, level: float, mode: str) -> None:
        """Forward a live mic level to the window face, if one is alive.

        Called from the HUD's main-thread tick; must stay a cheap no-op
        when the window has never been opened, is closed, or is occluded.
        """
        controller = self._controller
        if controller is None:
            return
        try:
            controller.feed_level(float(level), str(mode))
        except Exception:
            pass

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
    "SetupRow",
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
    "SIDEBAR_ROWS",
    "SIDEBAR_STATES",
    "STRING_CATALOGS",
    "SUPPORTED_LOCALES",
    "SidebarRow",
    "SnippetSetting",
    "StageTiming",
    "UnifiedSettings",
    "VoiceDraftMetadata",
    "VoiceDraftCopyReceipt",
    "WhisperFaceGUI",
    "WhisperFaceViewModel",
    "correction_review_text",
    "correction_row_caption",
    "create_gui",
    "localized_string",
    "native_appkit_smoke_contract",
    "onboarding_presentation",
    "home_bubble_actions",
    "home_dictation_landed",
    "home_review_due",
    "home_outbox_line",
    "home_review_in_row",
    "face_grid",
    "model_checks_note",
    "setup_rows",
    "setup_rows_turned_good",
    "setup_waits_on_a_row",
    "plural_key",
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
    "select_sidebar_row",
    "sidebar_row_destination",
    "sidebar_row_index",
    "sidebar_state",
    "result_evidence_text",
    "support_snapshot_text",
    "tone_for_app_index",
]
