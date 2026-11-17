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

Pass ``--sheets`` to review the Personalize editors instead of the pages:

    uv run scripts/window_render_probe.py --sheets [output-dir]

Each editor (app tones, snippets, vocabulary, learned corrections and
pronunciation keywords) is a sheet on the window. The probe opens every one
through the action its button calls, with the window's own seam answering
instead of a person, and writes what the sheet drew as
sheet-{name}-{light,dark}.png: each editor filled from the fixture below,
the snippet form, a refused save, a list too long for the sheet, each
editor's empty state, and one under Increase Contrast. A sheet's picture is
its own content, exactly as the window draws it while the sheet has the
keyboard: the default button in the system's accent colour, the insertion
point in the first field. The sheet-in-window-{name}-{light,dark}.png
pictures put the same content over the Personalize page, with the window at
its minimum size (unless ``--size`` says otherwise), so the room it leaves
can be judged. The sheet is in the middle of the window there because that
is where AppKit puts one: since macOS 11 a sheet is a card centred on its
window, not a panel hung from the title bar. The dimming, the corners and
the shadow round it are the probe's stand-in for what the system draws.

Pass ``--installer`` to review the Mac download's setup window instead:

    uv run scripts/window_render_probe.py --installer [output-dir]

That window belongs to the Swift app in ``scripts/macos_launcher_app.py``,
which runs before Python exists on a new Mac, so it is drawn by the app
itself: the probe compiles the launcher into a temporary folder and runs it
with ``--render-setup-window``, which builds the window without showing it
and writes every screen in both appearances as installer-{screen}-{light,
dark}.png, at the window's own size. Nothing is installed, started or
opened; the compiled launcher is thrown away afterwards.

Pass ``--permissions`` to review the window the running app shows while
macOS withholds Accessibility or Input Monitoring:

    uv run scripts/window_render_probe.py --permissions [output-dir]

It writes permissions-{both,accessibility,input-monitoring,confirm,failed}-
{light,dark}.png. The window's seams are fakes here: nothing opens System
Settings and nothing touches a privacy setting.

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

Pass ``--alerts`` to review the two kinds of alert instead of the pages:

    uv run scripts/window_render_probe.py --alerts [output-dir]

``alert-update-{light,dark}.png`` is the menu bar's update alert, which
shows the app icon (``icons/WhisperFace.icns``) the way ``dictate.py``'s
``_brand_update_alert`` sets it; ``alert-window-{light,dark}.png`` is one of
the window's own confirmations, which shows the chosen face on its chip. Each
is laid out and drawn without being run, so nothing appears on screen.
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
SHEETS = "--sheets" in ARGS
if SHEETS:
    ARGS.remove("--sheets")
INSTALLER = "--installer" in ARGS
if INSTALLER:
    ARGS.remove("--installer")
PERMISSIONS = "--permissions" in ARGS
if PERMISSIONS:
    ARGS.remove("--permissions")
ALERTS = "--alerts" in ARGS
if ALERTS:
    ARGS.remove("--alerts")
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


def render_installer(out: Path) -> int:
    """Compile the launcher in a scratch folder and let it draw its setup
    window's screens; 0 if every picture was written."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="whisper-face-installer-probe.") as scratch:
        app = Path(scratch) / "Whisper Face.app"
        built = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "macos_launcher_app.py"),
             "build", "--app", str(app)], capture_output=True, text=True)
        if built.returncode:
            print(built.stderr.strip() or "the launcher did not build")
            return built.returncode
        drawn = subprocess.run(
            [str(app / "Contents" / "MacOS" / "Whisper Face"),
             "--render-setup-window", str(out.resolve())],
            capture_output=True, text=True, timeout=120)
        if drawn.returncode:
            print(drawn.stderr.strip() or "the setup window did not draw")
            return drawn.returncode
    print(f"done → {out}")
    return 0


if INSTALLER:
    OUT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(render_installer(OUT))


def render_permissions(out: Path) -> int:
    """Draw the permission window's stages with fake seams; never shown."""
    from AppKit import (NSApplication, NSAppearance, NSAppearanceNameAqua,
                        NSAppearanceNameDarkAqua, NSBitmapImageFileTypePNG)
    import whisper_face_gui as gui
    NSApplication.sharedApplication()

    def refuse(*_args, **_kwargs):
        raise AssertionError("the probe never acts")

    samples = (
        ("both", ("accessibility", "input_monitoring"), "missing"),
        ("accessibility", ("accessibility",), "missing"),
        ("input-monitoring", ("input_monitoring",), "missing"),
        ("confirm", ("accessibility", "input_monitoring"), "confirm"),
        ("failed", ("accessibility", "input_monitoring"), "failed"),
    )
    for name, missing, stage in samples:
        for suffix, appearance in (("light", NSAppearanceNameAqua),
                                   ("dark", NSAppearanceNameDarkAqua)):
            flow = gui.PermissionHelpFlow(
                missing=missing, opener=refuse, runner=refuse,
                restart=refuse, record=lambda _line: None)
            flow.stage = stage
            controller = gui.PermissionHelpWindowController.alloc() \
                .initWithFlow_(flow)
            controller.window.setAppearance_(
                NSAppearance.appearanceNamed_(appearance))
            controller.render()
            view = controller.window.contentView()
            view.layoutSubtreeIfNeeded()
            rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
            view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
            data = rep.representationUsingType_properties_(
                NSBitmapImageFileTypePNG, {})
            data.writeToFile_atomically_(
                str(out / f"permissions-{name}-{suffix}.png"), True)
    print(f"done → {out}")
    return 0


if PERMISSIONS:
    OUT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(render_permissions(OUT))

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
    NSAlert,
    NSBitmapImageFileTypePNG,
    NSImage,
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
        # One of twenty still needs a home, and it is the longest line the
        # row can be asked to hold.
        "outbox-mixed": home_phase(
            "recovery", outbox_count=20, outbox_unchecked=19,
            outbox_summary=copy("overview.outbox.summary.paste_attempted"),
            detail=copy("overview.status.recovery.detail.one")),
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


# What the Personalize editors are filled from: several apps with different
# tones (one of them not on this Mac), several snippets including a long
# one, both vocabulary lists, a few learned corrections of both kinds and a
# few pronunciation keywords. No real person's words are in it.
SHEET_SETTINGS = {
    "app_tones": [
        {"bundle": "com.apple.MobileSMS", "name": "Messages",
         "tone": "casual"},
        {"bundle": "com.apple.mail", "name": "Mail", "tone": "formal"},
        {"bundle": "com.apple.Notes", "name": "Notes", "tone": "auto"},
        {"bundle": "com.apple.Safari", "name": "Safari", "tone": "default"},
        {"bundle": "com.apple.Terminal", "name": "Terminal", "tone": "code"},
        {"bundle": "com.example.field-notes", "name": "Field Notes",
         "tone": "verbatim"},
    ],
    "snippets": [
        {"name": "email", "text": "hello@example.com"},
        {"name": "signature", "text": "Kind regards,\nSam Rivera"},
        {"name": "standup", "text": (
            "Yesterday I finished the review and wrote up what we found, "
            "today I am pairing on the importer, and nothing is blocking "
            "me.\n\nAnything urgent, call me.")},
        {"name": "office address", "text": "12 Example Street\nSpringfield"},
    ],
    "manual_vocabulary": [
        "Qwen", "Parakeet", "Whisper Face", "Kubernetes", "Rivera"],
    "banned_vocabulary": ["Gwen", "Parrot Kit"],
    "corrections": [
        {"key": "gwen", "source": "Gwen", "target": "Qwen", "count": 4,
         "kind": "correction", "global_decision": "active",
         "app_scopes": [
             {"bundle": "com.apple.mail", "name": "Mail", "count": 3,
              "decision": "active"},
             {"bundle": "com.apple.Notes", "name": "Notes", "count": 1,
              "decision": "learning"}]},
        {"key": "cube control", "source": "cube control",
         "target": "kubectl", "count": 2, "kind": "correction",
         "global_decision": "learning",
         "app_scopes": [
             {"bundle": "com.apple.Terminal", "name": "Terminal",
              "count": 2, "decision": "active"}]},
        {"key": "teh", "source": "teh", "target": "the", "count": 1,
         "kind": "correction"},
        {"key": "signature", "source": "Snippet: signature",
         "target": "Kind regards,", "count": 1, "kind": "snippet"},
    ],
}
SHEET_KEYWORDS = (
    ("Qwen", None, 3, 2),
    ("Rivera", None, 2, 1),
    ("kubectl", "app-0123456789abcdef", 1, 0),
)
MANY_APPS = [
    {"bundle": bundle, "name": name, "tone": "auto"}
    for name, bundle in (
        ("Calendar", "com.apple.iCal"),
        ("Contacts", "com.apple.AddressBook"),
        ("Finder", "com.apple.finder"),
        ("Freeform", "com.apple.freeform"),
        ("Maps", "com.apple.Maps"),
        ("Music", "com.apple.Music"),
        ("Photos", "com.apple.Photos"),
        ("Preview", "com.apple.Preview"),
        ("Reminders", "com.apple.reminders"),
        ("TextEdit", "com.apple.TextEdit"))]


def keyword_export(candidates) -> dict:
    """The runtime's keyword export, for these candidates."""
    return {
        "schema_version": 1,
        "kind": "whisper-face/acoustic-keyword-memory-export",
        "policy": {
            "minimum_observations": 3, "minimum_confirmations": 2,
            "max_entries": 256, "recognition_effect": "none"},
        "candidates": [{
            "keyword": keyword, "app_scope": scope,
            "observations": seen, "confirmations": confirmed,
            "eligible": seen >= 3 and confirmed >= 2,
            "status": ("eligible-not-connected-to-recognition"
                       if seen >= 3 and confirmed >= 2 else
                       f"needs-{3 - seen}-observations-and-"
                       f"{2 - confirmed}-confirmations"),
        } for keyword, scope, seen, confirmed in candidates],
    }


def sheet_picture(sheet):
    """What the sheet draws, as a bitmap of its own content."""
    content = sheet.window.contentView()
    content.layoutSubtreeIfNeeded()
    bounds = content.bounds()
    rep = content.bitmapImageRepForCachingDisplayInRect_(bounds)
    content.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
    return rep


def write_picture(rep, name: str) -> None:
    data = rep.representationUsingType_properties_(
        NSBitmapImageFileTypePNG, {})
    path = OUT / f"{name}.png"
    path.write_bytes(bytes(data))
    print(f"wrote {path} ({data.length()} bytes)")


def sheet_in_window(picture, name: str) -> None:
    """The sheet's picture over the Personalize page, for its size.

    Only the sheet's content and the page under it are the app's. The
    dimming, the round corners and the shadow are drawn here to stand in
    for the system's own.
    """
    from AppKit import (
        NSBezierPath,
        NSBitmapImageRep,
        NSColor,
        NSCompositingOperationSourceOver,
        NSGraphicsContext,
        NSMakeRect,
        NSShadow,
    )

    window.layoutIfNeeded()
    content = window.contentView()
    bounds = content.bounds()
    page = content.bitmapImageRepForCachingDisplayInRect_(bounds)
    content.cacheDisplayInRect_toBitmapImageRep_(bounds, page)
    width, height = bounds.size.width, bounds.size.height
    canvas = NSBitmapImageRep.alloc() \
        .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, page.pixelsWide(), page.pixelsHigh(), 8, 4, True, False,
            "NSDeviceRGBColorSpace", 0, 0)
    canvas.setSize_((width, height))
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(
        NSGraphicsContext.graphicsContextWithBitmapImageRep_(canvas))
    whole = NSMakeRect(0, 0, width, height)
    page.drawInRect_(whole)
    NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, 0.28).setFill()
    NSBezierPath.fillRect_(whole)
    size = picture.size()
    frame = NSMakeRect(
        (width - size.width) / 2.0, (height - size.height) / 2.0,
        size.width, size.height)
    card = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        frame, 12.0, 12.0)
    NSGraphicsContext.saveGraphicsState()
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, 0.35))
    shadow.setShadowBlurRadius_(28.0)
    shadow.setShadowOffset_((0.0, -10.0))
    shadow.set()
    NSColor.blackColor().setFill()
    card.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGraphicsContext.saveGraphicsState()
    card.addClip()
    picture.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
        frame, NSMakeRect(0, 0, size.width, size.height),
        NSCompositingOperationSourceOver, 1.0, False, None)
    NSGraphicsContext.restoreGraphicsState()
    NSGraphicsContext.restoreGraphicsState()
    write_picture(canvas, name)


def type_into(sheet, **typed):
    """Put text into the sheet's fields by name."""
    for name, text in typed.items():
        control = sheet.controls[name]
        if hasattr(control, "setString_"):
            control.setString_(text)
        else:
            control.setStringValue_(text)


class KeySheetWindow(gui.SheetWindow):
    """A sheet's window, saying it has the keyboard. An open sheet always
    does, and only then does the system draw its default button in the
    accent colour and the insertion point in its first field; a window
    that is never shown never has it. ``hasKeyAppearance`` is what the
    controls ask; it is not in AppKit's headers, so on a system that
    asks something else the pictures only lose the accent."""

    def isKeyWindow(self):
        return True

    def hasKeyAppearance(self):
        return True

    def _hasKeyAppearance(self):
        return True


def render_sheets(suffix: str) -> None:
    """Every Personalize editor, as the window draws it, in this
    appearance. Each is opened by the action its button calls and
    answered through the window's own seam, so the pictures are of the
    sheets the app shows and of nothing made for the probe."""
    # The window a sheet sits on, at the smallest it can be: the size a
    # sheet has to fit.
    if SIZE is None:
        window.setContentSize_(gui.WINDOW_MIN_SIZE)
    gui.SheetWindow = KeySheetWindow
    settings = {key: [dict(item) if isinstance(item, dict) else item
                      for item in value]
                for key, value in SHEET_SETTINGS.items()}
    keywords = list(SHEET_KEYWORDS)

    def forget(kind: str, key: str) -> None:
        settings["corrections"] = [
            item for item in settings["corrections"]
            if not (item["kind"] == kind and item["key"] == key)]

    model.actions = GUIActions(
        settings_snapshot=lambda: settings,
        inspect_acoustic_keywords=lambda: keyword_export(keywords),
        forget_correction=lambda key: forget("correction", key),
        forget_snippet_edit=lambda key: forget("snippet", key),
    )

    def show() -> None:
        model.state = replace(
            STATE, section="Settings", settings_pane="Personalize",
            settings=gui.normalize_settings(settings))
        controller.render()

    def run(action, *steps, placed=()) -> None:
        """Open one editor and answer it step by step. A step is
        ``(picture, answer)``: the name its picture gets, or None for
        none, and the button to press, a function that is handed the
        sheet and returns one, or an alert's response. The page behind
        starts each time as it was, with no notice left on it."""
        remaining = list(steps)
        show()

        def respond(dialog):
            picture, answer = remaining.pop(0)
            if picture:
                drawn = sheet_picture(dialog)
                write_picture(drawn, f"sheet-{picture}-{suffix}")
                if picture in placed:
                    sheet_in_window(
                        drawn, f"sheet-in-window-{picture}-{suffix}")
            return answer(dialog) if callable(answer) else answer

        controller.modal_responder = respond
        try:
            action(None)
        finally:
            controller.modal_responder = None
        if remaining:
            raise SystemExit(f"{len(remaining)} sheet steps were not run")

    def refuse(**typed):
        def answer(sheet):
            type_into(sheet, **typed)
            return "save"
        return answer

    run(controller.editTone_, ("tones", "cancel"), placed=("tones",))
    run(controller.editSnippets_,
        ("snippets", "edit:standup"),
        ("snippets-edit", "cancel"),
        (None, "add"),
        ("snippets-add", refuse(name="", text="hello@example.org")),
        ("snippets-add-refused", "cancel"),
        (None, "done"),
        placed=("snippets", "snippets-edit"))
    run(controller.editVocabulary_,
        ("vocabulary", refuse(terms="Qwen\n-Parakeet\nWhisper Face")),
        ("vocabulary-refused", "cancel"),
        placed=("vocabulary",))
    run(controller.reviewCorrections_, ("corrections", "done"),
        placed=("corrections",))
    run(controller.inspectKeywords_,
        ("keywords", "export"),
        ("keywords-copied", "done"),
        placed=("keywords",))

    # A list too long for the sheet: the sheet stops at its cap and the
    # body scrolls between the header and the footer.
    settings["app_tones"] = SHEET_SETTINGS["app_tones"] + MANY_APPS
    run(controller.editTone_, ("tones-many", "cancel"),
        placed=("tones-many",))

    # Increase Contrast, as the sheet reads it while it draws.
    increase_contrast = gui._increase_contrast
    gui._increase_contrast = lambda: True
    try:
        settings["app_tones"] = SHEET_SETTINGS["app_tones"]
        run(controller.editTone_, ("tones-contrast", "cancel"))
        run(controller.reviewCorrections_, ("corrections-contrast", "done"))
    finally:
        gui._increase_contrast = increase_contrast

    # Each editor with nothing in it. Corrections empties in the open
    # sheet, when the last one is forgotten (the alert in between is the
    # confirmation, answered with its first button).
    settings["corrections"] = [SHEET_SETTINGS["corrections"][2]]
    run(controller.reviewCorrections_,
        (None, "forget:correction:teh"), (None, 1000),
        ("corrections-empty", "done"))
    keywords.clear()
    settings.update(app_tones=[], snippets=[], manual_vocabulary=[],
                    banned_vocabulary=[], corrections=[])
    run(controller.editSnippets_, ("snippets-empty", "done"))
    run(controller.editVocabulary_, ("vocabulary-empty", "cancel"))
    run(controller.inspectKeywords_, ("keywords-empty", "done"))
    # With no apps the page's Edit button is off, so the app-tones sheet
    # is never opened empty; this is what it would say if it were.
    sheet = controller._new_sheet()
    try:
        controller._sheet_fill(
            sheet, lambda: controller._tones_page(sheet, (), {}, ""))
        write_picture(sheet_picture(sheet), f"sheet-tones-empty-{suffix}")
    finally:
        controller._end_sheet(sheet)
    model.actions = GUIActions()


def render_alerts(suffix: str) -> None:
    """Lay out each kind of alert and draw its panel, never running it."""
    from AppKit import (
        NSBezierPath, NSBitmapImageRep, NSColor, NSColorSpace,
        NSCompositingOperationSourceOver, NSGraphicsContext, NSMakeRect,
    )

    def picture(alert, name: str) -> None:
        alert.layout()
        panel = alert.window()
        appearance = window.appearance()
        panel.setAppearance_(appearance)
        content = panel.contentView()
        content.layoutSubtreeIfNeeded()
        bounds = content.bounds()
        drawn = content.bitmapImageRepForCachingDisplayInRect_(bounds)
        content.cacheDisplayInRect_toBitmapImageRep_(bounds, drawn)
        # The panel's material is the system's and is not in the picture;
        # the window background colour stands in for it.
        canvas = NSBitmapImageRep.alloc() \
            .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, drawn.pixelsWide(), drawn.pixelsHigh(), 8, 4, True,
                False, "NSDeviceRGBColorSpace", 0, 0)
        canvas.setSize_(bounds.size)

        def draw():
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.setCurrentContext_(
                NSGraphicsContext.graphicsContextWithBitmapImageRep_(canvas))
            whole = NSMakeRect(0, 0, bounds.size.width, bounds.size.height)
            NSColor.windowBackgroundColor().colorUsingColorSpace_(
                NSColorSpace.sRGBColorSpace()).setFill()
            NSBezierPath.fillRect_(whole)
            drawn.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                whole, whole, NSCompositingOperationSourceOver, 1.0, False,
                None)
            NSGraphicsContext.restoreGraphicsState()

        appearance.performAsCurrentDrawingAppearance_(draw)
        write_picture(canvas, name)

    update = NSAlert.alloc().init()
    icon = NSImage.alloc().initWithContentsOfFile_(
        str(REPO / "icons" / "WhisperFace.icns"))
    if icon is None:
        raise SystemExit("icons/WhisperFace.icns did not load")
    update.setIcon_(icon)
    update.setMessageText_("An update is available.")
    update.setInformativeText_(
        "Build b7f028f \u2192 c0ffee1\n3 new commits available. Update now?")
    update.addButtonWithTitle_("Update")
    update.addButtonWithTitle_("Later")
    picture(update, f"alert-update-{suffix}")

    confirm = NSAlert.alloc().init()
    controller._show_the_face(confirm)
    confirm.setMessageText_("Clear recent dictations?")
    confirm.setInformativeText_("This removes them from this Mac.")
    confirm.addButtonWithTitle_("Clear")
    confirm.addButtonWithTitle_("Cancel")
    picture(confirm, f"alert-window-{suffix}")


for appearance_name, suffix in (
        (NSAppearanceNameAqua, "light"),
        (NSAppearanceNameDarkAqua, "dark")):
    appearance = NSAppearance.appearanceNamed_(appearance_name)
    NSApplication.sharedApplication().setAppearance_(appearance)
    window.setAppearance_(appearance)

    if SHEETS:
        render_sheets(suffix)
        continue

    if ALERTS:
        render_alerts(suffix)
        continue

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
