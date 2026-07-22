"""Native macOS settings window for Whisper Face.

The runtime owns capture, models, preferences, and service lifecycle.  This
module owns presentation only: callers inject a small callback interface and
can therefore open the window from the menu bar without importing
``dictate.py`` here.  ``WhisperFaceViewModel`` is deliberately AppKit-free so
its behavior can be tested without displaying a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
        "settings.action.edit": "Edit",
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
        "settings.notice.loaded": "Settings loaded",
        "settings.notice.tone_saved": "App tone saved",
        "settings.notice.snippet_saved": "Snippet saved",
        "settings.notice.snippet_deleted": "Snippet deleted",
        "settings.notice.vocabulary_saved": "Vocabulary saved",
        "settings.notice.correction_forgotten": "Learned correction forgotten",
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
            "choose_face",
            "set_flight_recorder",
        ),
        accessibility_catalog_keys=(
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
    pause: Callable[[], None] = _noop
    resume: Callable[[], None] = _noop
    open_log: Callable[[], None] = _noop
    open_source_and_license: Callable[[], None] = _noop
    open_local_license_notices: Callable[[], None] = _noop
    copy_latest_outbox: Callable[[], None] = _noop
    rerun_verification: Callable[[], Any] = _noop


@dataclass(frozen=True)
class ModelStatus:
    name: str
    role: str = ""
    status: str = "Unknown"
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
class UnifiedSettings:
    app_tones: tuple[AppToneSetting, ...] = field(default_factory=tuple)
    snippets: tuple[SnippetSetting, ...] = field(default_factory=tuple)
    manual_vocabulary: tuple[str, ...] = field(default_factory=tuple)
    banned_vocabulary: tuple[str, ...] = field(default_factory=tuple)
    corrections: tuple[CorrectionSetting, ...] = field(default_factory=tuple)


def tone_for_app_index(apps: Sequence[AppToneSetting], index: int) -> str:
    """Resolve the persisted tone for one AppKit popup selection."""
    if not isinstance(index, int) or isinstance(index, bool) \
            or not 0 <= index < len(apps):
        raise IndexError("app tone selection is out of range")
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
    summary: str = "No dictation yet"
    engine: str = "Waiting for a result"
    mode: str = "Capture"
    stable_prefix_words: int = 0
    compiler_decisions: int = 0
    confidence: float | None = None
    cleanup_edits: tuple[str, ...] = field(default_factory=tuple)
    proof_edits_accepted: int = 0
    proof_edits_rejected: int | None = None
    protected_anchor_count: int = 0
    alternatives_considered: int = 0
    context_influence: str = "Context influence not reported by runtime"
    consequence_summary: str = "Consequence: Standard · no protected spans"


@dataclass(frozen=True)
class GUIState:
    section: str = "Overview"
    capture_state: str = "Ready"
    paused: bool = False
    face: str = "parrot"
    flight_recorder: bool = False
    flight_state: str = "Off"
    active_engine: str = "Waiting for status"
    last_latency_ms: float | None = None
    last_word_count: int | None = None
    words_today: int = 0
    minutes_saved: float = 0.0
    outbox_count: int = 0
    outbox_summary: str = ""
    regression_cases: int = 0
    regression_quarantined: int = 0
    privacy_summary: str = "Local processing"
    service_status: str = "Unknown"
    microphone_status: str = "Unknown"
    accessibility_status: str = "Unknown"
    version: str = "Development build"
    models: tuple[ModelStatus, ...] = field(default_factory=tuple)
    hotkey_label: str = "Right Option"
    prefers_reduced_motion: bool = False
    onboarding_steps: tuple[OnboardingStep, ...] = field(default_factory=tuple)
    onboarding_complete: bool = False
    onboarding_acknowledged: bool = False
    status_phase: str = "ready"
    status_title: str = "Ready when you are"
    status_detail: str = "Hold Right Option, speak, then release to insert."
    degraded_issues: tuple[DegradedIssue, ...] = field(default_factory=tuple)
    last_result: ResultInspection = field(default_factory=ResultInspection)
    verification: str = "Not run"
    notice: str = ""
    notice_level: str = "info"
    settings_pane: str = "Modes"
    settings: UnifiedSettings = field(default_factory=UnifiedSettings)


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


def _normalize_models(value: Any) -> tuple[ModelStatus, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    models: list[ModelStatus] = []
    for item in value:
        if isinstance(item, Mapping):
            name = _clean_text(item.get("name"), "Model")
            models.append(ModelStatus(
                name=name,
                role=_clean_text(item.get("role"), ""),
                status=_clean_text(item.get("status"), "Unknown"),
                detail=_clean_text(item.get("detail"), ""),
            ))
        elif isinstance(item, str) and item.strip():
            models.append(ModelStatus(item.strip()))
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
) -> tuple[DegradedIssue, ...]:
    issues: list[DegradedIssue] = []
    if _status_contains(service_status, (
            "failed", "stopped", "offline", "unavailable")):
        issues.append(DegradedIssue(
            "service", "The local service is not ready",
            "Run Verification for a repair path. Your settings and personal "
            "data stay on this Mac."))
    if _status_contains(microphone_status, (
            "needs attention", "denied", "missing", "failed", "unavailable")):
        issues.append(DegradedIssue(
            "microphone", "Microphone permission is needed",
            "Open System Settings › Privacy & Security › Microphone and enable "
            "Whisper Face. Other settings remain available."))
    if _status_contains(accessibility_status, (
            "needs attention", "denied", "not granted", "failed", "unavailable")):
        issues.append(DegradedIssue(
            "accessibility", "Safe insertion needs Accessibility permission",
            "Open System Settings › Privacy & Security › Accessibility. Until "
            "then, recoverable text stays in the Voice Outbox."))
    if models and not _models_ready(models):
        issues.append(DegradedIssue(
            "models", "Local recognition models are still unavailable",
            "Keep Whisper Face open while models finish preparing, then run "
            "Verification if their status does not change.",
            route="Models",
        ))
    elif models:
        unavailable = [model.name for model in models if _status_contains(
            model.status, ("failed", "missing", "unavailable"))]
        if unavailable:
            issues.append(DegradedIssue(
                "fallback", "A fallback model needs attention",
                f"Dictation can continue with a ready engine. Check: "
                f"{', '.join(unavailable)}.",
                route="Models", severity="warning",
            ))
    return tuple(issues)


def _build_result_inspection(
    source: Mapping[str, Any],
    *,
    active_engine: str,
    latency_ms: float | None,
    word_count: int | None,
) -> ResultInspection:
    def sequence_items(key: str) -> tuple[str, ...]:
        value = source.get(key)
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return ()
        return tuple(str(item).strip() for item in value
                     if str(item).strip())

    available = word_count is not None and word_count > 0
    if latency_ms is not None:
        summary = f"{word_count or 0} words in {latency_ms / 1000:.2f}s"
        available = True
    elif available:
        summary = f"{word_count} words"
    else:
        summary = "No dictation yet"
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
    consequence_parts = [
        f"Consequence: {route.replace('-', ' ').title()}",
        f"{high_risks} high-risk",
        f"{uncertain_risks} uncertain",
    ]
    if risk_counts:
        consequence_parts.append(
            ", ".join(f"{category} {count}"
                      for category, count in risk_counts))
    consequence_parts.append(
        f"Re-listen: {relisten.replace('-', ' ')}")
    return ResultInspection(
        available=available,
        summary=summary,
        engine=active_engine if available else "Waiting for a result",
        mode=_clean_text(source.get("last_mode"), "Capture"),
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
            "Context influence not reported by runtime"),
        consequence_summary=" · ".join(consequence_parts),
    )


def _status_presentation(
    *,
    capture_state: str,
    paused: bool,
    hotkey_label: str,
    outbox_count: int,
    service_status: str,
    degraded_issues: Sequence[DegradedIssue],
) -> tuple[str, str, str]:
    capture = capture_state.strip().casefold()
    if paused:
        return ("paused", "Dictation is paused",
                "Resume whenever you are ready. Settings and recovery still work.")
    if _status_contains(capture, ("listen", "record", "captur")):
        return ("recording", "Listening…",
                f"Keep holding {hotkey_label}; release when you finish speaking.")
    if _status_contains(capture, ("process", "clean", "insert", "compil")):
        return ("processing", "Making your words useful…",
                "Recognizing locally, protecting names and numbers, then "
                "checking the destination.")
    if outbox_count:
        noun = "dictation" if outbox_count == 1 else "dictations"
        return ("recovery", "Your words are safe",
                f"{outbox_count} {noun} need an explicit Copy & Dismiss review.")
    errors = [issue for issue in degraded_issues if issue.severity == "error"]
    if errors:
        return ("degraded", "One setup item needs attention", errors[0].detail)
    if _status_contains(service_status, ("starting", "warming", "unknown")):
        return ("starting", "Finishing local startup…",
                "You can leave this window open; readiness updates automatically.")
    return ("ready", "Ready when you are",
            f"Hold {hotkey_label}, speak, then release to insert.")


def normalize_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    section: str = "Overview",
    verification: str = "Not run",
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
    capture_state = _clean_text(source.get("capture_state"), "Ready")
    paused = source.get("paused") is True
    active_engine = _clean_text(
        source.get("active_engine"), "Waiting for status")
    outbox_count = _nonnegative_int(source.get("outbox_count"))
    service_status = _clean_text(source.get("service_status"), "Unknown")
    microphone_status = _clean_text(
        source.get("microphone_status"), "Unknown")
    accessibility_status = _clean_text(
        source.get("accessibility_status"), "Unknown")
    models = _normalize_models(source.get("models"))
    hotkey_label = _clean_text(source.get("hotkey_label"), "Right Option")
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
    )
    phase, status_title, status_detail = _status_presentation(
        capture_state=capture_state,
        paused=paused,
        hotkey_label=hotkey_label,
        outbox_count=outbox_count,
        service_status=service_status,
        degraded_issues=degraded_issues,
    )
    normalized_latency = max(0.0, latency) if latency is not None else None
    return GUIState(
        section=section if section in SECTIONS else "Overview",
        capture_state=capture_state,
        paused=paused,
        face=face,
        flight_recorder=source.get("flight_recorder") is True,
        flight_state=_clean_text(source.get("flight_state"), "Off"),
        active_engine=active_engine,
        last_latency_ms=normalized_latency,
        last_word_count=last_word_count,
        words_today=_nonnegative_int(source.get("words_today")),
        minutes_saved=max(0.0, minutes_saved or 0.0),
        outbox_count=outbox_count,
        outbox_summary=_clean_text(source.get("outbox_summary"), ""),
        regression_cases=_nonnegative_int(source.get("regression_cases")),
        regression_quarantined=_nonnegative_int(
            source.get("regression_quarantined")),
        privacy_summary=_clean_text(
            source.get("privacy_summary"), "Local processing"),
        service_status=service_status,
        microphone_status=microphone_status,
        accessibility_status=accessibility_status,
        version=_clean_text(source.get("version"), "Development build"),
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
        ),
        verification=verification,
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
                self.state, notice=f"Status unavailable: {error}",
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
            raise ValueError(f"unknown section: {section}")
        self.state = replace(
            self.state, section=section, notice="", notice_level="info")
        if section == "Settings":
            self.load_settings()
        return self.state

    def select_settings_pane(self, pane: str) -> GUIState:
        if pane not in SETTINGS_PANES:
            raise ValueError(f"unknown settings pane: {pane}")
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
                self.state, notice=f"Could not load settings: {error}",
                notice_level="error")
        return self.state

    def set_app_tone(self, bundle: str, tone: str) -> GUIState:
        app_id = str(bundle).strip()
        normalized = str(tone).strip().casefold()
        if not app_id or len(app_id) > 255 or any(
                character.isspace() for character in app_id):
            raise ValueError("app identifier must be a non-empty bundle ID")
        if normalized not in TONE_CHOICES:
            raise ValueError(f"unsupported tone: {tone}")
        try:
            self.actions.set_app_tone(app_id, normalized)
            return self.load_settings(
                notice=self.localized("settings.notice.tone_saved"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not save app tone: {error}",
                notice_level="error")
            return self.state

    @staticmethod
    def _valid_snippet(name: str, text: str) -> tuple[str, str]:
        normalized_name = str(name).strip()
        value = str(text)
        if (not normalized_name or len(normalized_name) > 80
                or "\n" in normalized_name or "\r" in normalized_name):
            raise ValueError("snippet name must be 1–80 characters on one line")
        if not value.strip() or len(value) > 4000:
            raise ValueError("snippet text must be 1–4000 characters")
        return normalized_name, value

    def save_snippet(self, name: str, text: str, *,
                     expected_original: str | None = None) -> GUIState:
        try:
            normalized_name, value = self._valid_snippet(name, text)
        except ValueError as error:
            self.state = replace(
                self.state, notice=f"Could not save snippet: {error}",
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
                self.state, notice=f"Could not save snippet: {error}",
                notice_level="error")
            return self.state

    def delete_snippet(self, name: str, expected_original: str) -> GUIState:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("snippet name is required")
        if not isinstance(expected_original, str):
            raise ValueError("expected snippet text must be a string")
        try:
            self.actions.delete_snippet(normalized_name, expected_original)
            return self.load_settings(
                notice=self.localized("settings.notice.snippet_deleted"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not delete snippet: {error}",
                notice_level="error")
            return self.state

    @staticmethod
    def _valid_vocabulary(values: Sequence[str], *, label: str) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)):
            raise ValueError(f"{label} must be a list of terms")
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip()
            folded = value.casefold()
            if not value:
                continue
            if len(value) > 80 or "\n" in value or "\r" in value:
                raise ValueError(f"{label} terms must be at most 80 characters")
            if value.startswith(("-", "#")):
                raise ValueError(
                    f"{label} terms cannot start with reserved '-' or '#'")
            if folded not in seen:
                cleaned.append(value)
                seen.add(folded)
        if len(cleaned) > 500:
            raise ValueError(f"{label} supports at most 500 terms")
        return tuple(cleaned)

    def save_vocabulary(self, manual: Sequence[str],
                        banned: Sequence[str]) -> GUIState:
        try:
            terms = self._valid_vocabulary(
                manual, label="preferred vocabulary")
            exclusions = self._valid_vocabulary(
                banned, label="excluded vocabulary")
        except ValueError as error:
            self.state = replace(
                self.state, notice=f"Could not save vocabulary: {error}",
                notice_level="error")
            return self.state
        overlap = {item.casefold() for item in terms} & {
            item.casefold() for item in exclusions}
        if overlap:
            self.state = replace(
                self.state,
                notice="Could not save vocabulary: a term cannot also be excluded",
                notice_level="error")
            return self.state
        try:
            self.actions.save_vocabulary(terms, exclusions)
            return self.load_settings(
                notice=self.localized("settings.notice.vocabulary_saved"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not save vocabulary: {error}",
                notice_level="error")
            return self.state

    def forget_learned(self, kind: str, key: str) -> GUIState:
        normalized_kind = str(kind).strip().casefold()
        if normalized_kind not in {"correction", "snippet"}:
            raise ValueError("unknown learned correction kind")
        match = next((item for item in self.state.settings.corrections
                      if item.kind == normalized_kind and item.key == key), None)
        if match is None:
            raise ValueError("unknown learned correction")
        try:
            callback = (self.actions.forget_snippet_edit
                        if match.kind == "snippet"
                        else self.actions.forget_correction)
            result = callback(match.key)
            if match.kind == "snippet" and result is False:
                raise RuntimeError(
                    "the learned snippet edit no longer exists")
            return self.load_settings(
                notice=self.localized(
                    "settings.notice.correction_forgotten"),
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not forget correction: {error}",
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
            raise ValueError(f"unsupported face: {face}")
        try:
            self.actions.set_face(normalized)
            self.state = replace(
                self.state, face=normalized, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not change face: {error}",
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
                self.state, notice=f"Could not update Flight Recorder: {error}",
                notice_level="error")
        return self.state

    def set_paused(self, paused: bool) -> GUIState:
        desired = bool(paused)
        try:
            (self.actions.pause if desired else self.actions.resume)()
            capture_state = "Paused" if desired else "Ready"
            phase, title, detail = _status_presentation(
                capture_state=capture_state,
                paused=desired,
                hotkey_label=self.state.hotkey_label,
                outbox_count=self.state.outbox_count,
                service_status=self.state.service_status,
                degraded_issues=self.state.degraded_issues,
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
                self.state, notice=f"Could not change capture state: {error}",
                notice_level="error")
        return self.state

    def open_log(self) -> GUIState:
        try:
            self.actions.open_log()
            self.state = replace(
                self.state, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not open log: {error}",
                notice_level="error")
        return self.state

    def open_source_and_license(self) -> GUIState:
        try:
            self.actions.open_source_and_license()
            self.state = replace(
                self.state, notice="", notice_level="info")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not open source and license: {error}",
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
                notice=f"Could not open local license notices: {error}",
                notice_level="error")
        return self.state

    def copy_latest_outbox(self) -> GUIState:
        try:
            self.actions.copy_latest_outbox()
            self.state = replace(
                self.state, outbox_count=max(0, self.state.outbox_count - 1),
                notice="Latest recoverable dictation copied and dismissed",
                notice_level="success")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not copy Voice Outbox: {error}",
                notice_level="error")
        return self.state

    def rerun_verification(self) -> GUIState:
        self.state = replace(
            self.state, verification="Running…", notice="",
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
                status = message or ("All checks passed" if passed is not False
                                     else "Checks need attention")
            elif result is False:
                status = "Checks need attention"
            elif isinstance(result, str) and result.strip():
                status = result.strip()
            else:
                status = "All checks passed"
            return status
        except Exception as error:
            return f"Verification failed: {error}"

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
                    self.dynamic["verify_button"],
                    self.dynamic["license_button"],
                    self.dynamic["source_button"],
                ),
            }
            self.window.setInitialFirstResponder_(self.section_control)
            self.render()

        def _build_overview(self, page: Any) -> None:
            hero = _card(NSMakeRect(0, 224, 758, 178))
            phase = _label("READY", NSMakeRect(24, 137, 320, 18),
                           size=11, weight="bold", color=_ACCENT)
            status = _label("Ready when you are", NSMakeRect(24, 92, 520, 42),
                            size=32, weight="bold")
            detail = _label("", NSMakeRect(26, 67, 520, 20),
                            size=12, color=_SECONDARY)
            engine = _label("", NSMakeRect(26, 42, 500, 20),
                            size=13, color=_SECONDARY)
            outbox = _label(
                "Voice Outbox: all clear", NSMakeRect(26, 16, 500, 20),
                size=12, color=_SECONDARY)
            pause = _button(
                "Pause", NSMakeRect(610, 89, 116, 38), self, "pauseChanged:",
                help_text="Pause or resume the global dictation hotkey.")
            fix = _button(
                "Review Setup", NSMakeRect(590, 49, 136, 30),
                self, "reviewIssue:",
                help_text="Show the most useful recovery guidance.")
            copy_outbox = _button(
                "Copy & Dismiss", NSMakeRect(590, 13, 136, 30),
                self, "copyOutbox:",
                help_text="Copy the latest recoverable dictation, then remove it from the Voice Outbox.")
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
                "SETUP", NSMakeRect(20, 82, 190, 18),
                size=10, weight="bold", color=_ACCENT)
            onboarding_title = _label(
                "Allow Mac permissions", NSMakeRect(20, 51, 500, 27),
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

            cards = (("Last dictation", "overview_last"),
                     ("Words today", "overview_words"),
                     ("Time saved", "overview_saved"))
            for index, (heading, key) in enumerate(cards):
                card = _card(NSMakeRect(index * 253, 0, 239, 76))
                card.addSubview_(_label(heading, NSMakeRect(16, 49, 200, 18),
                                        size=11, color=_SECONDARY))
                value = _label("—", NSMakeRect(16, 13, 205, 31),
                               size=21, weight="bold")
                card.addSubview_(value)
                page.addSubview_(card)
                self.dynamic[key] = value

        def _build_results(self, page: Any) -> None:
            page.addSubview_(_label(
                "Last Result", NSMakeRect(4, 351, 500, 32),
                size=22, weight="bold"))
            page.addSubview_(_label(
                "Inspectable evidence from this session — no transcript history.",
                NSMakeRect(5, 326, 690, 20), size=13, color=_SECONDARY))

            summary_card = _card(NSMakeRect(0, 213, 758, 92))
            result_summary = _label(
                "No dictation yet", NSMakeRect(20, 48, 430, 27),
                size=18, weight="bold")
            result_engine = _label(
                "Waiting for a result", NSMakeRect(20, 21, 500, 20),
                size=12, color=_SECONDARY)
            result_mode = _label(
                "Capture", NSMakeRect(620, 42, 110, 22),
                size=12, weight="medium", color=_ACCENT)
            summary_card.addSubview_(result_summary)
            summary_card.addSubview_(result_engine)
            summary_card.addSubview_(result_mode)
            page.addSubview_(summary_card)

            evidence_card = _card(NSMakeRect(0, 72, 758, 125))
            evidence_keys = (
                ("Stable prefix", "result_stable"),
                ("Protected anchors", "result_anchors"),
                ("Compiler decisions", "result_decisions"),
                ("Alternatives", "result_alternatives"),
                ("Cleanup edits", "result_cleanup"),
                ("Proof review", "result_proof"),
            )
            for index, (heading, key) in enumerate(evidence_keys):
                x = 20 + (index % 2) * 370
                y = 91 - (index // 2) * 35
                evidence_card.addSubview_(_label(
                    heading, NSMakeRect(x, y, 140, 18),
                    size=11, color=_SECONDARY))
                value = _label("—", NSMakeRect(x + 145, y, 190, 18),
                               size=12, weight="medium")
                evidence_card.addSubview_(value)
                self.dynamic[key] = value
            page.addSubview_(evidence_card)
            context = _label(
                "Context influence not reported by runtime",
                NSMakeRect(5, 44, 740, 20),
                size=12, color=_SECONDARY)
            page.addSubview_(context)
            consequence = _label(
                "Consequence: Standard · no protected spans",
                NSMakeRect(5, 22, 740, 20),
                size=11, color=_SECONDARY)
            page.addSubview_(consequence)
            page.addSubview_(_label(
                "Whisper Face exposes decision counts, not private transcript text, "
                "in this settings window.",
                NSMakeRect(5, 2, 740, 18), size=10, color=_SECONDARY))
            self.dynamic.update(
                result_summary=result_summary,
                result_engine=result_engine,
                result_mode=result_mode,
                result_context=context,
                result_consequence=consequence,
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
            )
            for index, (key, title_key, selector) in enumerate(rows):
                y = 202 - index * 62
                card = _card(NSMakeRect(0, y, 758, 52))
                card.addSubview_(_label(
                    self._l(title_key), NSMakeRect(18, 25, 260, 19),
                    size=13, weight="bold"))
                detail = _label("", NSMakeRect(18, 7, 550, 18),
                                size=11, color=_SECONDARY)
                action_key = ("settings.action.forget" if key == "corrections"
                              else "settings.action.edit")
                help_key = ("settings.accessibility.forget.help"
                            if key == "corrections"
                            else "settings.accessibility.edit.help")
                button = _button(
                    self._l(action_key), NSMakeRect(645, 10, 94, 32),
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
            page.addSubview_(_label("Your local voice stack",
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                "Fast recognition, accurate fallback, and private cleanup.",
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            rows = []
            for index in range(3):
                row = _card(NSMakeRect(0, 220 - index * 88, 758, 72))
                name = _label("Waiting for model status", NSMakeRect(20, 36, 430, 22),
                              size=14, weight="medium")
                detail = _label("", NSMakeRect(20, 13, 530, 18),
                                size=11, color=_SECONDARY)
                status = _label("Unknown", NSMakeRect(610, 26, 120, 20),
                                size=12, weight="medium", color=_ACCENT)
                row.addSubview_(name)
                row.addSubview_(detail)
                row.addSubview_(status)
                page.addSubview_(row)
                rows.append((row, name, detail, status))
            guidance = _label(
                "Models prepare locally and can finish in the background.",
                NSMakeRect(5, 25, 740, 22), size=11, color=_SECONDARY)
            page.addSubview_(guidance)
            self.dynamic.update(model_rows=rows, model_guidance=guidance)

        def _build_diagnostics(self, page: Any) -> None:
            page.addSubview_(_label("Diagnostics",
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                "A quick health check when something does not feel right.",
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            card = _card(NSMakeRect(0, 137, 758, 161))
            keys = (("Service", "diag_service"),
                    ("Microphone", "diag_microphone"),
                    ("Accessibility", "diag_accessibility"),
                    ("Personal Regression Lab", "diag_regression"),
                    ("Motion", "diag_motion"),
                    ("Build", "diag_version"))
            for index, (heading, key) in enumerate(keys):
                y = 133 - index * 23
                card.addSubview_(_label(heading, NSMakeRect(20, y, 170, 19),
                                        size=12, color=_SECONDARY))
                value = _label("Unknown", NSMakeRect(185, y, 525, 19),
                               size=12, weight="medium")
                card.addSubview_(value)
                self.dynamic[key] = value
            page.addSubview_(card)
            open_log = _button("Open Log", NSMakeRect(0, 89, 120, 36),
                               self, "openLog:")
            verify = _button("Run Verification", NSMakeRect(132, 89, 152, 36),
                             self, "verify:")
            verify.setKeyEquivalent_("r")
            verify.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
            license_notices = _button(
                "License Notices", NSMakeRect(296, 89, 138, 36),
                self, "openLicense:")
            source = _button("Exact Source", NSMakeRect(446, 89, 120, 36),
                             self, "openSource:")
            progress = NSProgressIndicator.alloc().initWithFrame_(
                NSMakeRect(580, 94, 20, 20))
            progress.setStyle_(1)
            progress.setDisplayedWhenStopped_(False)
            verification = _label("Not run", NSMakeRect(608, 95, 140, 20),
                                  size=12, color=_SECONDARY)
            page.addSubview_(open_log)
            page.addSubview_(verify)
            page.addSubview_(license_notices)
            page.addSubview_(source)
            page.addSubview_(progress)
            page.addSubview_(verification)
            guidance = _label(
                "Everything looks ready.", NSMakeRect(5, 54, 740, 24),
                size=11, color=_SECONDARY)
            page.addSubview_(guidance)
            page.addSubview_(_label(
                "AGPL-3.0-only · no warranty · corresponding source available",
                NSMakeRect(5, 24, 620, 20), size=11, color=_SECONDARY))
            self.dynamic.update(
                open_log_button=open_log,
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

            phase_labels = {
                "ready": "READY",
                "recording": "RECORDING",
                "processing": "PROCESSING",
                "recovery": "RECOVERY AVAILABLE",
                "degraded": "ACTION NEEDED",
                "paused": "PAUSED",
                "starting": "STARTING LOCALLY",
            }
            self.dynamic["overview_phase"].setStringValue_(
                phase_labels.get(state.status_phase, state.status_phase.upper()))
            self.dynamic["overview_status"].setStringValue_(state.status_title)
            self.dynamic["overview_detail"].setStringValue_(state.status_detail)
            self.dynamic["overview_engine"].setStringValue_(
                f"Active engine: {state.active_engine}")
            outbox = (f"Voice Outbox: {state.outbox_count} recoverable · "
                      f"{state.outbox_summary}"
                      if state.outbox_count else "Voice Outbox: all clear")
            self.dynamic["overview_outbox"].setStringValue_(outbox)
            for key, label in (
                ("overview_phase", "Dictation phase"),
                ("overview_status", "Dictation status"),
                ("overview_detail", "Dictation status detail"),
                ("overview_engine", "Active recognition engine"),
                ("overview_outbox", "Voice Outbox status"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=label,
                )
            self.dynamic["copy_outbox_button"].setHidden_(
                state.outbox_count == 0)
            self.dynamic["review_issue_button"].setHidden_(
                not state.degraded_issues and (
                    state.onboarding_complete or state.onboarding_acknowledged))
            pause_title = "Resume" if state.paused else "Pause"
            self.dynamic["pause_button"].setTitle_(pause_title)
            sync_accessibility(
                self.dynamic["pause_button"],
                "Dictation paused" if state.paused else "Dictation active",
                label=f"{pause_title} dictation",
            )
            if state.last_latency_ms is None:
                last = "—"
            else:
                suffix = (f" · {state.last_word_count} words"
                          if state.last_word_count is not None else "")
                last = f"{state.last_latency_ms / 1000:.2f}s{suffix}"
            self.dynamic["overview_last"].setStringValue_(last)
            self.dynamic["overview_words"].setStringValue_(
                f"{state.words_today:,}")
            self.dynamic["overview_saved"].setStringValue_(
                f"{state.minutes_saved:.0f} min")
            for key, label in (
                ("overview_last", "Last dictation duration and word count"),
                ("overview_words", "Words dictated today"),
                ("overview_saved", "Estimated time saved today"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=label,
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
                for key, label in (
                    ("onboarding_progress", "First run setup progress"),
                    ("onboarding_title", "Next setup step"),
                    ("onboarding_detail", "Setup step detail"),
                ):
                    sync_accessibility(
                        self.dynamic[key],
                        str(self.dynamic[key].stringValue()),
                        label=label,
                    )
                sync_accessibility(
                    self.dynamic["onboarding_action"], next_step.status,
                    label=action_title,
                )

            result = state.last_result
            self.dynamic["result_summary"].setStringValue_(result.summary)
            self.dynamic["result_engine"].setStringValue_(
                f"{result.engine} · session-only evidence")
            self.dynamic["result_mode"].setStringValue_(result.mode)
            self.dynamic["result_stable"].setStringValue_(
                f"{result.stable_prefix_words} words")
            self.dynamic["result_anchors"].setStringValue_(
                str(result.protected_anchor_count))
            confidence = (
                f" · {result.confidence:.0%} confidence"
                if result.confidence is not None else "")
            self.dynamic["result_decisions"].setStringValue_(
                f"{result.compiler_decisions}{confidence}")
            cleanup_kinds = ", ".join(dict.fromkeys(result.cleanup_edits))
            self.dynamic["result_cleanup"].setStringValue_(
                cleanup_kinds or "None reported")
            rejected = (
                str(result.proof_edits_rejected)
                if result.proof_edits_rejected is not None else "not reported")
            self.dynamic["result_proof"].setStringValue_(
                f"{result.proof_edits_accepted} accepted · "
                f"{rejected} rejected")
            self.dynamic["result_alternatives"].setStringValue_(
                str(result.alternatives_considered))
            self.dynamic["result_context"].setStringValue_(
                f"Context: {result.context_influence}")
            self.dynamic["result_consequence"].setStringValue_(
                result.consequence_summary)
            for key, label in (
                ("result_summary", "Last result summary"),
                ("result_engine", "Last result engine"),
                ("result_mode", "Last result mode"),
                ("result_stable", "Stable prefix words"),
                ("result_anchors", "Protected anchors"),
                ("result_decisions", "Compiler decisions"),
                ("result_cleanup", "Cleanup edits"),
                ("result_proof", "Proof review"),
                ("result_alternatives", "Alternatives considered"),
                ("result_context", "Context influence"),
                ("result_consequence", "Consequence decision receipt"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=label,
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
                        name, model.name, label="Model name")
                    sync_accessibility(
                        detail, str(detail.stringValue()),
                        label=f"{model.name} role and detail")
                    sync_accessibility(
                        status_label, model.status,
                        label=f"{model.name} status")
                    row.setHidden_(False)
                else:
                    row.setHidden_(index > 0)
                    set_accessible_text(
                        name, "Waiting for model status", label="Model name")
                    set_accessible_text(
                        detail, "Open this window after startup completes",
                        label="Model role and detail")
                    set_accessible_text(
                        status_label, "Unknown", label="Model status")
            model_issue = next(
                (issue for issue in state.degraded_issues
                 if issue.route == "Models"), None)
            self.dynamic["model_guidance"].setStringValue_(
                model_issue.detail if model_issue else
                "Models prepare locally and can finish in the background.")
            sync_accessibility(
                self.dynamic["model_guidance"],
                str(self.dynamic["model_guidance"].stringValue()),
                label="Model guidance",
            )
            self.dynamic["diag_service"].setStringValue_(state.service_status)
            self.dynamic["diag_microphone"].setStringValue_(state.microphone_status)
            self.dynamic["diag_accessibility"].setStringValue_(
                state.accessibility_status)
            regression = f"{state.regression_cases} cases"
            if state.regression_quarantined:
                regression += f" · {state.regression_quarantined} quarantined"
            self.dynamic["diag_regression"].setStringValue_(regression)
            self.dynamic["diag_motion"].setStringValue_(
                "Reduced motion" if state.prefers_reduced_motion
                else "Standard motion")
            self.dynamic["diag_version"].setStringValue_(state.version)
            self.dynamic["verification"].setStringValue_(state.verification)
            first_issue = state.degraded_issues[0] if state.degraded_issues else None
            self.dynamic["diag_guidance"].setStringValue_(
                f"{first_issue.title}: {first_issue.detail}"
                if first_issue else "Everything looks ready.")
            self.dynamic["notice"].setStringValue_(state.notice)
            for key, label in (
                ("diag_service", "Service status"),
                ("diag_microphone", "Microphone status"),
                ("diag_accessibility", "Accessibility permission status"),
                ("diag_regression", "Personal Regression Lab status"),
                ("diag_motion", "Motion setting"),
                ("diag_version", "Build version"),
                ("verification", "Verification result"),
                ("diag_guidance", "Diagnostic guidance"),
                ("notice", "Whisper Face notice"),
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=label,
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
                tone_for_app_index(apps, index)))

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
            self.view_model.set_verification("Running…")
            if not self.view_model.state.prefers_reduced_motion:
                progress.startAnimation_(None)
            button.setEnabled_(False)
            set_accessible_text(
                self.dynamic["verification"], "Running…",
                label="Verification result")

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
    calls: list[tuple[Any, ...]] = []

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
        model.choose_face("owl")
        model.set_flight_recorder(True)
        expected_calls = {
            ("tone", "com.example.mail", "casual"),
            ("save_snippet", "signature", "Cheers", "Kind regards"),
            ("delete_snippet", "signature", "Kind regards"),
            ("vocabulary", ("Qwen", "Parakeet"), ("Gwen",)),
            ("forget_correction", "gwen"),
            ("forget_snippet", "gwen"),
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
    "normalize_settings",
    "run_native_appkit_smoke",
    "resolve_locale",
    "tone_for_app_index",
]
