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
SECTIONS = ("Overview", "Appearance", "Privacy", "Models", "Diagnostics")
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
    verification: str = "Not run"
    notice: str = ""


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


def normalize_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    section: str = "Overview",
    verification: str = "Not run",
    notice: str = "",
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
    return GUIState(
        section=section if section in SECTIONS else "Overview",
        capture_state=_clean_text(source.get("capture_state"), "Ready"),
        paused=source.get("paused") is True,
        face=face,
        flight_recorder=source.get("flight_recorder") is True,
        flight_state=_clean_text(source.get("flight_state"), "Off"),
        active_engine=_clean_text(
            source.get("active_engine"), "Waiting for status"),
        last_latency_ms=max(0.0, latency) if latency is not None else None,
        last_word_count=last_word_count,
        words_today=_nonnegative_int(source.get("words_today")),
        minutes_saved=max(0.0, minutes_saved or 0.0),
        outbox_count=_nonnegative_int(source.get("outbox_count")),
        outbox_summary=_clean_text(source.get("outbox_summary"), ""),
        regression_cases=_nonnegative_int(source.get("regression_cases")),
        regression_quarantined=_nonnegative_int(
            source.get("regression_quarantined")),
        privacy_summary=_clean_text(
            source.get("privacy_summary"), "Local processing"),
        service_status=_clean_text(source.get("service_status"), "Unknown"),
        microphone_status=_clean_text(
            source.get("microphone_status"), "Unknown"),
        accessibility_status=_clean_text(
            source.get("accessibility_status"), "Unknown"),
        version=_clean_text(source.get("version"), "Development build"),
        models=_normalize_models(source.get("models")),
        verification=verification,
        notice=notice,
    )


class WhisperFaceViewModel:
    """Pure state/actions seam between the runtime and the native window."""

    def __init__(self, actions: GUIActions):
        self.actions = actions
        self.state = GUIState()
        self.refresh()

    def refresh(self) -> GUIState:
        try:
            snapshot = self.actions.status_snapshot()
            self.state = normalize_snapshot(
                snapshot,
                section=self.state.section,
                verification=self.state.verification,
            )
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Status unavailable: {error}")
        return self.state

    def select_section(self, section: str) -> GUIState:
        if section not in SECTIONS:
            raise ValueError(f"unknown section: {section}")
        self.state = replace(self.state, section=section, notice="")
        return self.state

    def choose_face(self, face: str) -> GUIState:
        normalized = str(face).strip().casefold()
        if normalized not in FACES:
            raise ValueError(f"unsupported face: {face}")
        try:
            self.actions.set_face(normalized)
            self.state = replace(self.state, face=normalized, notice="")
        except Exception as error:
            self.state = replace(self.state, notice=f"Could not change face: {error}")
        return self.state

    def set_flight_recorder(self, enabled: bool) -> GUIState:
        desired = bool(enabled)
        try:
            self.actions.set_flight_recorder(desired)
            self.state = replace(
                self.state, flight_recorder=desired, notice="")
            return self.refresh()
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not update Flight Recorder: {error}")
        return self.state

    def set_paused(self, paused: bool) -> GUIState:
        desired = bool(paused)
        try:
            (self.actions.pause if desired else self.actions.resume)()
            self.state = replace(
                self.state,
                paused=desired,
                capture_state="Paused" if desired else "Ready",
                notice="",
            )
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not change capture state: {error}")
        return self.state

    def open_log(self) -> GUIState:
        try:
            self.actions.open_log()
            self.state = replace(self.state, notice="")
        except Exception as error:
            self.state = replace(self.state, notice=f"Could not open log: {error}")
        return self.state

    def open_source_and_license(self) -> GUIState:
        try:
            self.actions.open_source_and_license()
            self.state = replace(self.state, notice="")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not open source and license: {error}")
        return self.state

    def open_local_license_notices(self) -> GUIState:
        try:
            self.actions.open_local_license_notices()
            self.state = replace(self.state, notice="")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not open local license notices: {error}")
        return self.state

    def copy_latest_outbox(self) -> GUIState:
        try:
            self.actions.copy_latest_outbox()
            self.state = replace(
                self.state, outbox_count=max(0, self.state.outbox_count - 1),
                notice="Latest recoverable dictation copied and dismissed")
        except Exception as error:
            self.state = replace(
                self.state, notice=f"Could not copy Voice Outbox: {error}")
        return self.state

    def rerun_verification(self) -> GUIState:
        self.state = replace(self.state, verification="Running…", notice="")
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
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSObject, NSTimer

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

    def _label(text: str, frame: Any, *, size: float = 13,
               weight: str = "regular", color: Any = None) -> Any:
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
        return label

    def _button(title: str, frame: Any, target: Any, action: str) -> Any:
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(target)
        button.setAction_(action)
        return button

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
                self.section_control.setWidth_forSegment_(148, index)
            self.section_control.setSelectedSegment_(0)
            self.section_control.setTarget_(self)
            self.section_control.setAction_("sectionChanged:")
            root.addSubview_(self.section_control)

            page_frame = NSMakeRect(31, 25, 758, 402)
            builders = {
                "Overview": self._build_overview,
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
            self.render()

        def _build_overview(self, page: Any) -> None:
            hero = _card(NSMakeRect(0, 238, 758, 164))
            hero.addSubview_(_label("READY WHEN YOU ARE", NSMakeRect(24, 121, 260, 18),
                                      size=11, weight="bold", color=_ACCENT))
            status = _label("Ready", NSMakeRect(24, 73, 480, 45),
                            size=32, weight="bold")
            engine = _label("", NSMakeRect(26, 48, 500, 20),
                            size=13, color=_SECONDARY)
            outbox = _label(
                "Voice Outbox: all clear", NSMakeRect(26, 20, 500, 20),
                size=12, color=_SECONDARY)
            pause = _button("Pause", NSMakeRect(610, 64, 116, 38),
                            self, "pauseChanged:")
            copy_outbox = _button(
                "Copy & Dismiss", NSMakeRect(590, 18, 136, 30),
                self, "copyOutbox:")
            hero.addSubview_(status)
            hero.addSubview_(engine)
            hero.addSubview_(outbox)
            hero.addSubview_(pause)
            hero.addSubview_(copy_outbox)
            page.addSubview_(hero)
            self.dynamic.update(overview_status=status, overview_engine=engine,
                                overview_outbox=outbox,
                                pause_button=pause,
                                copy_outbox_button=copy_outbox)

            cards = (("Last dictation", "overview_last"),
                     ("Words today", "overview_words"),
                     ("Time saved", "overview_saved"))
            for index, (heading, key) in enumerate(cards):
                card = _card(NSMakeRect(index * 253, 82, 239, 134))
                card.addSubview_(_label(heading, NSMakeRect(18, 91, 200, 20),
                                        size=12, color=_SECONDARY))
                value = _label("—", NSMakeRect(18, 43, 205, 42),
                               size=25, weight="bold")
                card.addSubview_(value)
                page.addSubview_(card)
                self.dynamic[key] = value

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
            self.dynamic["model_rows"] = rows

        def _build_diagnostics(self, page: Any) -> None:
            page.addSubview_(_label("Diagnostics",
                                    NSMakeRect(4, 351, 500, 32),
                                    size=22, weight="bold"))
            page.addSubview_(_label(
                "A quick health check when something does not feel right.",
                NSMakeRect(5, 326, 650, 20), size=13, color=_SECONDARY))
            card = _card(NSMakeRect(0, 158, 758, 140))
            keys = (("Service", "diag_service"),
                    ("Microphone", "diag_microphone"),
                    ("Accessibility", "diag_accessibility"),
                    ("Personal Regression Lab", "diag_regression"),
                    ("Build", "diag_version"))
            for index, (heading, key) in enumerate(keys):
                y = 108 - index * 25
                card.addSubview_(_label(heading, NSMakeRect(20, y, 170, 19),
                                        size=12, color=_SECONDARY))
                value = _label("Unknown", NSMakeRect(185, y, 525, 19),
                               size=12, weight="medium")
                card.addSubview_(value)
                self.dynamic[key] = value
            page.addSubview_(card)
            open_log = _button("Open Log", NSMakeRect(0, 96, 120, 36),
                               self, "openLog:")
            verify = _button("Run Verification", NSMakeRect(132, 96, 152, 36),
                             self, "verify:")
            license_notices = _button(
                "License Notices", NSMakeRect(296, 96, 138, 36),
                self, "openLicense:")
            source = _button("Exact Source", NSMakeRect(446, 96, 120, 36),
                             self, "openSource:")
            progress = NSProgressIndicator.alloc().initWithFrame_(
                NSMakeRect(580, 101, 20, 20))
            progress.setStyle_(1)
            progress.setDisplayedWhenStopped_(False)
            verification = _label("Not run", NSMakeRect(608, 102, 140, 20),
                                  size=12, color=_SECONDARY)
            page.addSubview_(open_log)
            page.addSubview_(verify)
            page.addSubview_(license_notices)
            page.addSubview_(source)
            page.addSubview_(progress)
            page.addSubview_(verification)
            page.addSubview_(_label(
                "AGPL-3.0-only · no warranty · corresponding source available",
                NSMakeRect(5, 55, 620, 20), size=11, color=_SECONDARY))
            self.dynamic.update(verify_button=verify, verify_progress=progress,
                                verification=verification)

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
            for section, page in self.pages.items():
                page.setHidden_(section != state.section)
            selected = SECTIONS.index(state.section)
            self.section_control.setSelectedSegment_(selected)

            status = "Paused" if state.paused else state.capture_state
            self.dynamic["overview_status"].setStringValue_(status)
            self.dynamic["overview_engine"].setStringValue_(
                f"Active engine: {state.active_engine}")
            outbox = (f"Voice Outbox: {state.outbox_count} recoverable · "
                      f"{state.outbox_summary}"
                      if state.outbox_count else "Voice Outbox: all clear")
            self.dynamic["overview_outbox"].setStringValue_(outbox)
            self.dynamic["copy_outbox_button"].setHidden_(
                state.outbox_count == 0)
            self.dynamic["pause_button"].setTitle_(
                "Resume" if state.paused else "Pause")
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
            self.dynamic["face_picker"].setSelectedSegment_(FACES.index(state.face))
            self.dynamic["flight_toggle"].setState_(
                NSControlStateValueOn if state.flight_recorder
                else NSControlStateValueOff)
            self.dynamic["flight_toggle"].setTitle_(state.flight_state)
            self.dynamic["privacy_summary"].setStringValue_(state.privacy_summary)

            for index, (row, name, detail, status_label) in enumerate(
                    self.dynamic["model_rows"]):
                if index < len(state.models):
                    model = state.models[index]
                    name.setStringValue_(model.name)
                    detail.setStringValue_(
                        " · ".join(part for part in (model.role, model.detail) if part))
                    status_label.setStringValue_(model.status)
                    row.setHidden_(False)
                else:
                    row.setHidden_(index > 0)
                    name.setStringValue_("Waiting for model status")
                    detail.setStringValue_("Open this window after startup completes")
                    status_label.setStringValue_("Unknown")
            self.dynamic["diag_service"].setStringValue_(state.service_status)
            self.dynamic["diag_microphone"].setStringValue_(state.microphone_status)
            self.dynamic["diag_accessibility"].setStringValue_(
                state.accessibility_status)
            regression = f"{state.regression_cases} cases"
            if state.regression_quarantined:
                regression += f" · {state.regression_quarantined} quarantined"
            self.dynamic["diag_regression"].setStringValue_(regression)
            self.dynamic["diag_version"].setStringValue_(state.version)
            self.dynamic["verification"].setStringValue_(state.verification)
            self.dynamic["notice"].setStringValue_(state.notice)

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
            progress.startAnimation_(None)
            button.setEnabled_(False)
            self.dynamic["verification"].setStringValue_("Running…")

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
    "FACES",
    "GUIActions",
    "GUIState",
    "ModelStatus",
    "SECTIONS",
    "WhisperFaceGUI",
    "WhisperFaceViewModel",
    "create_gui",
    "normalize_snapshot",
]
