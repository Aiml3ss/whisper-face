# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyobjc-framework-Cocoa; sys_platform == 'darwin'",
#   "pyobjc-framework-Quartz; sys_platform == 'darwin'",
# ]
# ///
"""Render the real app window per section to PNGs, without showing it.

This is the visual verification loop for the native window: it constructs
``WhisperFaceWindowController`` exactly like ``run_native_appkit_smoke``
(headless, no user state, never ordered front), replays a representative
runtime fixture through the view model, and snapshots every section in both
appearances so layout and contrast can be reviewed as images.

Usage:
    uv run scripts/window_render_probe.py [output-dir]

Writes window-{home,settings-personalize,settings-controls,settings-privacy,
advanced}-
{light,dark}.png plus window-onboarding-{stage}-{light,dark}.png for every
first-run stage, into ``.probe-renders/`` by default.

Pass ``--size WIDTHxHEIGHT`` to review a different window size; the window's
minimum size is where truncation shows up first.

Pass ``--states`` to render the complete look: every state Home can be in
and every variant of the first-run steps, on top of the pages above, as
home-{state}-{light,dark}.png and setup-{variant}-{light,dark}.png. That
includes Home and the permissions step under Increase Contrast, and a
settings page reached while setup is unfinished.

Pass ``--overlay`` to review the recording overlay instead of the window:

    uv run scripts/window_render_probe.py --overlay [output-dir]

The overlay lives in ``dictate.py``, whose dependencies this script does not
carry, so it is drawn by the overlay's own smoke test: the probe runs
``dictate.py --native-gui-smoke-test`` with ``WHISPER_FACE_HUD_FRAMES_DIR``
set, which makes that test write every frame it checks as a transparent PNG
(every state in both appearances, with and without Increase Contrast, the
Reduce Motion stills, and all sixteen faces at their loudest). The frames
land in ``frames/`` and the probe lays them over a light and a dark desktop
in a handful of contact sheets, ``overlay-*.png``. A frame is written before
it is checked, so a failing check still leaves its picture behind.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARGS = list(sys.argv[1:])
SIZE = None
if "--size" in ARGS:
    index = ARGS.index("--size")
    width, _, height = ARGS.pop(index + 1).partition("x")
    ARGS.pop(index)
    SIZE = (float(width), float(height))
OVERLAY = "--overlay" in ARGS
if OVERLAY:
    ARGS.remove("--overlay")
STATES = "--states" in ARGS
if STATES:
    ARGS.remove("--states")
OUT = Path(ARGS[0]) if ARGS else REPO / ".probe-renders"
sys.path.insert(0, str(REPO))

# Must stay the name dictate.py reads (``HUD_FRAMES_ENV``); a test holds the
# two together.
HUD_FRAMES_ENV = "WHISPER_FACE_HUD_FRAMES_DIR"
SHEET_SCALE = 2.0
LIGHT_DESKTOP = (0.949, 0.953, 0.961)    # a document, a pale wallpaper
DARK_DESKTOP = (0.106, 0.114, 0.129)     # a dark editor, a night wallpaper


def render_overlay(out: Path) -> int:
    """Write every overlay frame and the contact sheets; 0 if the smoke
    test that drew them passed."""
    import os
    import subprocess

    from AppKit import (
        NSAttributedString,
        NSBezierPath,
        NSBitmapImageFileTypePNG,
        NSBitmapImageRep,
        NSColor,
        NSColorSpace,
        NSCompositingOperationSourceOver,
        NSDeviceRGBColorSpace,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSGraphicsContext,
        NSMakeRect,
    )
    from Foundation import NSData

    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for stale in list(frames.glob("*.png")) + list(out.glob("overlay-*.png")):
        stale.unlink()
    smoke = subprocess.run(
        ["uv", "run", "--locked", "--script", str(REPO / "dictate.py"),
         "--native-gui-smoke-test"],
        cwd=REPO, env={**os.environ, HUD_FRAMES_ENV: str(frames)},
        capture_output=True, text=True)
    print((smoke.stdout + smoke.stderr).strip())

    # Frames in the order the smoke test drew them.
    drawn = sorted(frames.glob("*.png"),
                   key=lambda path: (path.stat().st_mtime_ns, path.name))
    if not drawn:
        print("no overlay frames were written")
        return smoke.returncode or 1
    loaded = {
        path.stem: NSBitmapImageRep.imageRepWithData_(
            NSData.dataWithContentsOfFile_(str(path)))
        for path in drawn}

    def color(rgb, alpha=1.0):
        return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, alpha)

    def label(text, x, y, rgb, size=11.0, bold=False):
        font = (NSFont.boldSystemFontOfSize_(size) if bold
                else NSFont.systemFontOfSize_(size))
        NSAttributedString.alloc().initWithString_attributes_(text, {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: color(rgb),
        }).drawAtPoint_((x, y))

    def sheet(name, title, bands):
        """One PNG: a title, then bands of frames, each on its desktop.

        A band is ``(heading, desktop, columns, crop, [(caption, stem)])``;
        ``crop`` keeps only the left part of each frame, in points.
        """
        pad, gap, caption_height, heading_height = 20.0, 14.0, 16.0, 26.0
        frame_size = next(iter(loaded.values())).size()
        widths = []
        height = pad + 24.0
        placed = []
        for heading, desktop, columns, crop, cells in bands:
            cell_width = min(crop or frame_size.width, frame_size.width)
            rows = -(-len(cells) // columns)
            band_width = columns * cell_width + (columns + 1) * gap
            band_height = (heading_height + rows * (
                caption_height + frame_size.height) + gap)
            placed.append((height, band_width, band_height, cell_width))
            widths.append(band_width)
            height += band_height + gap
        width = max(widths) + 2.0 * pad
        height += pad - gap
        bitmap = NSBitmapImageRep.alloc() \
            .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, int(width * SHEET_SCALE), int(height * SHEET_SCALE),
                8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
        bitmap = bitmap.bitmapImageRepByRetaggingWithColorSpace_(
            NSColorSpace.sRGBColorSpace())
        bitmap.setSize_((width, height))
        context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(context)

        # Bitmap contexts are y-up; ``top`` measures down from the top.
        def from_top(top, tall):
            return height - top - tall

        color((1.0, 1.0, 1.0)).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, width, height))
        label(title, pad, from_top(pad, 16.0), (0.1, 0.1, 0.1), 13.0, True)
        for (heading, desktop, columns, crop, cells), (
                top, band_width, band_height, cell_width) in zip(
                    bands, placed):
            ink = ((0.12, 0.13, 0.15) if sum(desktop) > 1.5
                   else (0.92, 0.93, 0.95))
            color(desktop).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad, from_top(top, band_height),
                           band_width, band_height), 12.0, 12.0).fill()
            label(heading, pad + gap, from_top(top + 7.0, 14.0), ink,
                  12.0, True)
            for index, (caption, stem) in enumerate(cells):
                row, column = divmod(index, columns)
                x = pad + gap + column * (cell_width + gap)
                cell_top = top + heading_height + row * (
                    caption_height + frame_size.height)
                label(caption, x, from_top(cell_top + 1.0, 13.0), ink, 10.0)
                frame = loaded.get(stem)
                if frame is None:
                    label("(not drawn)", x, from_top(
                        cell_top + 50.0, 13.0), ink)
                    continue
                frame.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                    NSMakeRect(x, from_top(
                        cell_top + caption_height, frame_size.height),
                        cell_width, frame_size.height),
                    NSMakeRect(0, 0, cell_width, frame_size.height),
                    NSCompositingOperationSourceOver, 1.0, False, None)
        NSGraphicsContext.restoreGraphicsState()
        path = out / f"{name}.png"
        path.write_bytes(bytes(bitmap.representationUsingType_properties_(
            NSBitmapImageFileTypePNG, {})))
        print(f"wrote {path}")

    def stems(prefix, suffix):
        """``(what, stem)`` for the frames of one family and variant."""
        return [(stem[len(prefix):-len(suffix)], stem) for stem in loaded
                if stem.startswith(prefix) and stem.endswith(suffix)]

    desktops = (("On a light desktop", LIGHT_DESKTOP),
                ("On a dark desktop", DARK_DESKTOP))
    for appearance in ("light", "dark"):
        for contrast in ("", "-contrast"):
            suffix = f"-{appearance}{contrast}"
            cells = stems("state-", suffix)
            if not cells:
                continue
            sheet(f"overlay-states{suffix}",
                  f"Recording overlay: every state, {appearance} appearance"
                  + (", Increase Contrast" if contrast else ""),
                  [(heading, desktop, 2, None, cells)
                   for heading, desktop in desktops])
        cells = stems("face-", f"-{appearance}")
        if cells:
            sheet(f"overlay-faces-{appearance}",
                  f"Recording overlay: every face at its loudest, "
                  f"{appearance} appearance",
                  [(heading, desktop, 4, 216.0, cells)
                   for heading, desktop in desktops])
    cells = [(f"{what}, {appearance} appearance", stem)
             for appearance in ("light", "dark")
             for what, stem in stems("still-", f"-{appearance}")]
    if cells:
        sheet("overlay-reduce-motion",
              "Recording overlay: Reduce Motion (no level, no squash, "
              "dots at rest)",
              [(heading, desktop, 2, None, cells)
               for heading, desktop in desktops])
    print(f"done → {out}")
    return smoke.returncode


if OVERLAY:
    from AppKit import NSApplication as _Application

    _Application.sharedApplication()
    OUT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(render_overlay(OUT))

import whisper_face_gui as gui  # noqa: E402
from whisper_face_gui import (  # noqa: E402
    GUIActions,
    GUIState,
    ResultInspection,
    WhisperFaceViewModel,
    WhisperFaceWindowController,
)
from AppKit import (  # noqa: E402
    NSApplication,
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSBitmapImageFileTypePNG,
)

NSApplication.sharedApplication()
OUT.mkdir(parents=True, exist_ok=True)

STATE = GUIState(
    service_status="Running",
    microphone_status="Ready",
    accessibility_status="Granted",
    input_monitoring_status="Granted",
    version="2DEDE55",
    onboarding_acknowledged=True,
    active_engine="parakeet-unified",
    last_latency_ms=930.0,
    last_word_count=9,
    words_today=714,
    minutes_saved=13.0,
    outbox_count=1,
    outbox_summary="Paste may have landed — verify before reusing",
    last_result=ResultInspection(
        available=True,
        summary="0.93s · 9 words · 93% confidence",
        engine="parakeet-unified",
        stable_prefix_words=7,
        compiler_decisions=1,
        confidence=0.93,
        proof_edits_accepted=3,
        proof_edits_rejected=0,
        protected_anchor_count=2,
        alternatives_considered=2,
    ),
    # A default install, through the same normalizer the window uses.
    models=gui.normalize_snapshot({"models": [
        {"name": name, "role": role, "install_state": install_state,
         "required": required, "warm": install_state == "installed"}
        for name, role, install_state, required in (
            ("Parakeet Unified 0.6B", "Primary recognition", "installed",
             True),
            ("Whisper Tiny", "Fast recognition preview", "installed", True),
            ("Whisper large-v3-turbo", "Recognition fallback",
             "not_installed", False),
            ("qwen3.5:4b", "Selective cleanup", "not_installed", False),
        )]}).models,
)

# The first-run fixtures drive the onboarding poster from real runtime
# evidence, the same way the smoke gate builds its checklist. Each entry
# advances exactly one readiness signal, so the four steps and the explicit
# completion state can all be reviewed as images.
ONBOARDING_RUNTIME = {
    "service_status": "Running",
    "microphone_status": "Starting",
    "accessibility_status": "Needs attention",
    "input_monitoring_status": "Needs attention",
    "capture_state": "Ready",
    "hotkey_label": "Right Option",
    "models": [{
        "name": "Parakeet Unified 0.6B",
        "role": "Primary recognition",
        "install_state": "checking",
        "required": True,
        "warm": False,
    }],
}
GRANTED = {
    "microphone_status": "Ready",
    "accessibility_status": "Granted",
    "input_monitoring_status": "Granted",
}
READY_MODELS = [{
    "name": "Parakeet Unified 0.6B",
    "role": "Primary recognition",
    "install_state": "installed",
    "required": True,
    "warm": True,
}]
ONBOARDING_STAGES = (
    ("permissions", {}),
    ("hotkey", GRANTED),
    ("models", {**GRANTED, "hotkey_practiced": True}),
    ("first-dictation", {
        **GRANTED, "hotkey_practiced": True, "models": READY_MODELS}),
    ("complete", {
        **GRANTED, "hotkey_practiced": True, "models": READY_MODELS,
        "last_word_count": 9,
    }),
)
ONBOARDING_STATE = gui.normalize_snapshot(ONBOARDING_RUNTIME)

model = WhisperFaceViewModel(GUIActions())
model.state = STATE
controller = WhisperFaceWindowController.alloc() \
    .initForSmokeWithViewModel_(model)
window = controller.window
if SIZE is not None:
    window.setContentSize_(SIZE)


def snapshot(name: str) -> None:
    window.layoutIfNeeded()
    content = window.contentView()
    bounds = content.bounds()
    rep = content.bitmapImageRepForCachingDisplayInRect_(bounds)
    content.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
    data = rep.representationUsingType_properties_(
        NSBitmapImageFileTypePNG, {})
    path = OUT / f"{name}.png"
    path.write_bytes(bytes(data))
    print(f"wrote {path} ({data.length()} bytes)")


def home_phase(name: str, **changes) -> GUIState:
    """Home in one phase, with the status copy the runtime gives it."""
    detail = changes.pop("detail", None)
    if detail is None:
        try:
            detail = gui.localized_string(
                f"overview.status.{name}.detail",
                hotkey=changes.get("hotkey_label", STATE.hotkey_label))
        except (KeyError, ValueError):
            detail = ""
    return replace(STATE, **{
        "section": "Home", "outbox_count": 0, "outbox_summary": "",
        "status_phase": name, "status_detail": detail,
        "status_title": gui.localized_string(
            f"overview.status.{name}.title"),
        **changes})


def home_states() -> dict[str, GUIState]:
    """Every state Home can be in, by the name its picture gets."""
    copy = gui.localized_string
    microphone = gui.DegradedIssue(
        "microphone", copy("issue.microphone.title"),
        copy("issue.microphone.detail"))
    models = gui.DegradedIssue(
        "models", copy("issue.models.title"),
        copy("issue.models.detail", models="Parakeet Unified 0.6B"))
    ready = home_phase("ready")
    held = replace(STATE.last_result, acoustic_replay_enabled=True)
    return {
        "ready": ready,
        "ready-empty": replace(
            ready, last_latency_ms=None, last_word_count=None,
            words_today=0, minutes_saved=0.0,
            last_result=ResultInspection()),
        "ready-long-key": home_phase("ready", hotkey_label="Right Command"),
        "ready-fox": replace(ready, face="fox"),
        "ready-replay": replace(
            ready, last_result=replace(held, retained_span_count=0)),
        "ready-replay-held": replace(
            ready, last_result=replace(held, retained_span_count=2)),
        "paused": home_phase("paused", paused=True),
        "listening": home_phase("recording"),
        "working": home_phase("processing"),
        "starting": home_phase("starting"),
        "outbox": home_phase(
            "recovery", outbox_count=1,
            outbox_summary=copy("overview.outbox.summary.paste_attempted"),
            detail=copy("overview.status.recovery.detail.one")),
        # Five dictations into an app that hides its text: copies kept,
        # nothing to review, nothing red.
        "outbox-unchecked": home_phase(
            "ready", outbox_count=5, outbox_unchecked=5,
            outbox_summary=copy("overview.outbox.summary.pasted_unchecked")),
        "needs-attention-permission": home_phase(
            "degraded", degraded_issues=(microphone,),
            detail=microphone.detail, microphone_status="Unavailable",
            missing_permissions=("microphone",)),
        "needs-attention-model": home_phase(
            "degraded", degraded_issues=(models,), detail=models.detail),
        "outbox-and-attention": home_phase(
            "recovery", outbox_count=3,
            outbox_summary=copy("overview.outbox.summary.not_pasted"),
            detail=copy("overview.status.recovery.detail.many", count=3),
            degraded_issues=(microphone,)),
        "paused-outbox-attention": home_phase(
            "paused", paused=True, outbox_count=1,
            outbox_summary=copy("overview.outbox.summary.paste_attempted"),
            degraded_issues=(models,)),
    }


# First-run variants the five stages above do not reach, as changes to the
# runtime the first of them starts from.
SETUP_VARIANTS = {
    "permissions": {},
    "permissions-all-missing": {"microphone_status": "Unavailable"},
    "permissions-mixed": {
        "microphone_status": "Ready", "accessibility_status": "Granted"},
    "hotkey": ONBOARDING_STAGES[1][1],
    "hotkey-long": {
        **ONBOARDING_STAGES[1][1], "hotkey_label": "Right Command"},
    "models": ONBOARDING_STAGES[2][1],
    "models-many": {**ONBOARDING_STAGES[2][1], "models": [
        {"name": "Parakeet Unified 0.6B", "role": "Primary recognition",
         "install_state": "installed", "required": True, "warm": True},
        {"name": "Whisper Tiny", "role": "Fast recognition preview",
         "install_state": "checking", "required": True},
        {"name": "Whisper large-v3-turbo", "role": "Recognition fallback",
         "install_state": "not_installed", "required": False},
        {"name": "qwen3.5:4b", "role": "Selective cleanup",
         "install_state": "failed", "required": False}]},
    "first-dictation": ONBOARDING_STAGES[3][1],
    "complete": ONBOARDING_STAGES[4][1],
}


def setup_state(changes: dict, section: str = "Home") -> GUIState:
    return replace(
        gui.normalize_snapshot({**ONBOARDING_RUNTIME, **changes}),
        section=section)


def render_states(suffix: str) -> None:
    """Every Home state and first-run variant, in this appearance."""
    states = home_states()
    for name, state in states.items():
        model.state = state
        controller.render()
        snapshot(f"home-{name}-{suffix}")
    for name, changes in SETUP_VARIANTS.items():
        model.state = setup_state(changes)
        controller.render()
        snapshot(f"setup-{name}-{suffix}")
    # Increase Contrast, as the window reads it while it draws.
    increase_contrast = gui._increase_contrast
    gui._increase_contrast = lambda: True
    try:
        for name, state in (
                ("home-outbox-contrast", states["outbox"]),
                ("setup-permissions-contrast", setup_state({}))):
            model.state = state
            controller.render()
            snapshot(f"{name}-{suffix}")
    finally:
        gui._increase_contrast = increase_contrast
    # A page shortcut pressed during setup brings the sidebar back.
    model.state = setup_state({}, section="Advanced")
    controller.render()
    snapshot(f"setup-left-for-advanced-{suffix}")


for appearance_name, suffix in (
        (NSAppearanceNameAqua, "light"),
        (NSAppearanceNameDarkAqua, "dark")):
    appearance = NSAppearance.appearanceNamed_(appearance_name)
    NSApplication.sharedApplication().setAppearance_(appearance)
    window.setAppearance_(appearance)

    model.state = replace(STATE, section="Home")
    controller.render()
    snapshot(f"window-home-{suffix}")

    for pane in gui.SETTINGS_PANES:
        model.state = replace(
            STATE, section="Settings", settings_pane=pane)
        controller.render()
        snapshot(f"window-settings-{pane.lower()}-{suffix}")

    # The worst case for the Controls pane is a capture key that shares a
    # modifier with two voice modes and an undo key that is bound: both
    # detail lines are at their longest, which is where truncation shows up.
    # The "actually" switch is on, so its on-state copy is reviewed too.
    model.state = replace(
        STATE, section="Settings", settings_pane="Controls",
        hotkey="ctrl_r", hotkey_label="Right Control",
        hotkey_shared_modes=("reply", "command", "code"),
        undo_hotkey="f14", undo_hotkey_label="F14",
        undo_available=True, sound_theme="whisper",
        settings=replace(STATE.settings, self_correction=True))
    controller.render()
    snapshot(f"window-settings-controls-shared-{suffix}")

    model.state = replace(
        STATE, section="Settings", settings_pane="Privacy",
        recent_dictations=True)
    controller.render()
    snapshot(f"window-settings-privacy-recent-{suffix}")

    model.state = replace(STATE, section="Advanced")
    controller.render()
    snapshot(f"window-advanced-{suffix}")

    for stage, overrides in ONBOARDING_STAGES:
        model.state = replace(
            gui.normalize_snapshot({**ONBOARDING_RUNTIME, **overrides}),
            section="Home")
        controller.render()
        snapshot(f"window-onboarding-{stage}-{suffix}")

    if STATES:
        render_states(suffix)

print(f"done → {OUT}")
