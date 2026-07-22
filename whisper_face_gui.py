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
SECTIONS = (
    "Overview", "Results", "Appearance", "Privacy", "Models", "Diagnostics")
FACES = ("parrot", "fox", "owl", "cat", "bear")
FACE_LABELS = {
    "parrot": "Parrot",
    "fox": "Fox",
    "owl": "Owl",
    "cat": "Cat",
    "bear": "Bear",
}
FACE_EMOJI = {
    "parrot": "🦜",
    "fox": "🦊",
    "owl": "🦉",
    "cat": "🐱",
    "bear": "🐻",
}


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


@dataclass(frozen=True)
class GUIActions:
    """Integration API supplied by the running Whisper Face application."""

    status_snapshot: Callable[[], Mapping[str, Any]] = lambda: {}
    set_face: Callable[[str], None] = _noop
    set_flight_recorder: Callable[[bool], None] = _noop
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
) -> tuple[OnboardingStep, ...]:
    microphone_ready = _status_contains(
        microphone_status, ("ready", "granted", "available"))
    accessibility_ready = _status_contains(
        accessibility_status, ("ready", "granted", "trusted"))
    permissions_ready = microphone_ready and accessibility_ready
    model_ready = _models_ready(models)
    return (
        OnboardingStep(
            "permissions", "Allow Mac permissions",
            "Microphone captures speech; Accessibility safely inserts it into "
            "the field you chose.",
            "Done" if permissions_ready else "Needs attention",
            permissions_ready,
        ),
        OnboardingStep(
            "hotkey", f"Practice {hotkey_label}",
            f"Hold {hotkey_label} while speaking, then release. You can keep "
            "using the Mac normally.",
            "Done" if hotkey_practiced else "Try it now",
            hotkey_practiced,
        ),
        OnboardingStep(
            "models", "Confirm local models",
            "At least one local recognition engine must be ready; fallbacks can "
            "finish warming in the background.",
            "Done" if model_ready else "Warming up",
            model_ready,
        ),
        OnboardingStep(
            "first_dictation", "Make your first dictation",
            "Speak one sentence in any text field. Whisper Face will keep the "
            "result recoverable if focus changes.",
            "Done" if first_dictation_complete else "Your turn",
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
    )


class WhisperFaceViewModel:
    """Pure state/actions seam between the runtime and the native window."""

    def __init__(self, actions: GUIActions):
        self.actions = actions
        self.state = GUIState()
        self._onboarding_acknowledged = False
        self.refresh()

    def refresh(self) -> GUIState:
        try:
            snapshot = self.actions.status_snapshot()
            self.state = normalize_snapshot(
                snapshot,
                section=self.state.section,
                verification=self.state.verification,
                onboarding_acknowledged=self._onboarding_acknowledged,
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
                notice="Setup is complete — Whisper Face is ready.",
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
        NSBackingStoreBuffered,
        NSBezelStyleRounded,
        NSBox,
        NSBoxCustom,
        NSButton,
        NSColor,
        NSControlStateValueOff,
        NSControlStateValueOn,
        NSFont,
        NSMakeRect,
        NSNoBorder,
        NSNoTitle,
        NSProgressIndicator,
        NSSegmentedControl,
        NSSegmentStyleRounded,
        NSTextField,
        NSView,
        NSWindow,
        NSWorkspace,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSObject, NSTimer, NSUserDefaults

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

        def initWithViewModel_(self, view_model: WhisperFaceViewModel):
            self = objc.super(WhisperFaceWindowController, self).init()
            if self is None:
                return None
            self.view_model = view_model
            self.pages: dict[str, Any] = {}
            self.dynamic: dict[str, Any] = {}
            self.timer = None
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
                        self.view_model.state, prefers_reduced_motion=True)
            except Exception:
                pass
            self._build_window()
            return self

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
            subtitle = _label("Private, fast voice input on your Mac",
                              NSMakeRect(33, 493, 360, 20),
                              size=12, color=_SECONDARY)
            root.addSubview_(title)
            root.addSubview_(subtitle)

            self.section_control = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(31, 447, 758, 32))
            self.section_control.setSegmentCount_(len(SECTIONS))
            self.section_control.setSegmentStyle_(NSSegmentStyleRounded)
            for index, section in enumerate(SECTIONS):
                self.section_control.setLabel_forSegment_(section, index)
                self.section_control.setWidth_forSegment_(123, index)
            self.section_control.setSelectedSegment_(0)
            self.section_control.setTarget_(self)
            self.section_control.setAction_("sectionChanged:")
            _accessible(
                self.section_control, "Settings sections",
                "Use arrow keys to move between Whisper Face settings sections.")
            root.addSubview_(self.section_control)

            page_frame = NSMakeRect(31, 25, 758, 402)
            builders = {
                "Overview": self._build_overview,
                "Results": self._build_results,
                "Appearance": self._build_appearance,
                "Privacy": self._build_privacy,
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
                "Continue Setup", NSMakeRect(590, 40, 136, 36),
                self, "continueSetup:",
                help_text="Open the next incomplete first-run setup step.")
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
                NSMakeRect(5, 42, 740, 21),
                size=12, color=_SECONDARY)
            page.addSubview_(context)
            page.addSubview_(_label(
                "Whisper Face exposes decision counts, not private transcript text, "
                "in this settings window.",
                NSMakeRect(5, 15, 740, 20), size=11, color=_SECONDARY))
            self.dynamic.update(
                result_summary=result_summary,
                result_engine=result_engine,
                result_mode=result_mode,
                result_context=context,
            )

        def _build_appearance(self, page: Any) -> None:
            page.addSubview_(_label("Choose your Whisper Face",
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                "Your companion talks with you in the menu bar and listening HUD.",
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            card = _card(NSMakeRect(0, 152, 758, 148))
            picker = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(25, 55, 708, 58))
            picker.setSegmentCount_(len(FACES))
            picker.setSegmentStyle_(NSSegmentStyleRounded)
            for index, face in enumerate(FACES):
                picker.setLabel_forSegment_(
                    f"{FACE_EMOJI[face]}  {FACE_LABELS[face]}", index)
                picker.setWidth_forSegment_(137, index)
            picker.setTarget_(self)
            picker.setAction_("faceChanged:")
            _accessible(
                picker, "Whisper Face companion",
                "Choose the animal shown in the menu bar and listening HUD.")
            card.addSubview_(picker)
            page.addSubview_(card)
            page.addSubview_(_label(
                "More faces can be added later without changing your dictation setup.",
                NSMakeRect(5, 116, 650, 20), size=12, color=_SECONDARY))
            self.dynamic["face_picker"] = picker

        def _build_privacy(self, page: Any) -> None:
            page.addSubview_(_label("Privacy you can see",
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                "Recognition and cleanup run locally. You stay in control.",
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            card = _card(NSMakeRect(0, 190, 758, 108))
            card.addSubview_(_label("Flight Recorder", NSMakeRect(22, 61, 300, 24),
                                    size=16, weight="bold"))
            card.addSubview_(_label(
                "Keeps a rolling 20-second audio buffer in RAM only.",
                NSMakeRect(22, 34, 520, 20), size=12, color=_SECONDARY))
            flight = NSButton.alloc().initWithFrame_(NSMakeRect(625, 39, 110, 32))
            flight.setButtonType_(3)
            flight.setTitle_("Enabled")
            flight.setTarget_(self)
            flight.setAction_("flightChanged:")
            _accessible(
                flight, "Flight Recorder",
                "Toggle the rolling twenty second audio buffer held only in memory.")
            card.addSubview_(flight)
            page.addSubview_(card)
            privacy = _label("Local processing", NSMakeRect(5, 145, 700, 24),
                             size=14, weight="medium", color=_ACCENT)
            page.addSubview_(privacy)
            page.addSubview_(_label(
                "Whisper Face never needs cloud speech recognition to dictate.",
                NSMakeRect(5, 119, 700, 20), size=12, color=_SECONDARY))
            self.dynamic.update(flight_toggle=flight, privacy_summary=privacy)

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
            self.dynamic.update(verify_button=verify, verify_progress=progress,
                                verification=verification,
                                diag_guidance=guidance)

        def show(self) -> None:
            self.view_model.refresh()
            self.render()
            self.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            if self.timer is None:
                self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    2.0, self, "refreshTimer:", None, True)

        def render(self) -> None:
            state = self.view_model.state
            if state.onboarding_complete and not state.onboarding_acknowledged:
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
                    f"FIRST-RUN SETUP · {completed} OF {len(state.onboarding_steps)} COMPLETE")
                self.dynamic["onboarding_title"].setStringValue_(next_step.title)
                self.dynamic["onboarding_detail"].setStringValue_(next_step.detail)
                action_title = {
                    "permissions": "Review Permissions",
                    "hotkey": "Show Practice",
                    "models": "View Models",
                    "first_dictation": "Show How",
                }.get(next_step.key, "Continue Setup")
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
            ):
                sync_accessibility(
                    self.dynamic[key],
                    str(self.dynamic[key].stringValue()),
                    label=label,
                )
            self.dynamic["face_picker"].setSelectedSegment_(FACES.index(state.face))
            sync_accessibility(
                self.dynamic["face_picker"], FACE_LABELS[state.face],
                label="Whisper Face companion",
            )
            self.dynamic["flight_toggle"].setState_(
                NSControlStateValueOn if state.flight_recorder
                else NSControlStateValueOff)
            self.dynamic["flight_toggle"].setTitle_(state.flight_state)
            sync_accessibility(
                self.dynamic["flight_toggle"], state.flight_state,
                label="Flight Recorder",
            )
            self.dynamic["privacy_summary"].setStringValue_(state.privacy_summary)
            sync_accessibility(
                self.dynamic["privacy_summary"], state.privacy_summary,
                label="Privacy status",
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


class WhisperFaceGUI:
    """Retained facade suitable for ownership by the existing status bar."""

    def __init__(self, actions: GUIActions):
        self.view_model = WhisperFaceViewModel(actions)
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


def create_gui(actions: GUIActions) -> WhisperFaceGUI:
    """Create (but do not display) the GUI facade."""

    return WhisperFaceGUI(actions)


__all__ = [
    "APPKIT_AVAILABLE",
    "DegradedIssue",
    "FACES",
    "GUIActions",
    "GUIState",
    "ModelStatus",
    "OnboardingStep",
    "ResultInspection",
    "SECTIONS",
    "WhisperFaceGUI",
    "WhisperFaceViewModel",
    "create_gui",
    "normalize_snapshot",
]
