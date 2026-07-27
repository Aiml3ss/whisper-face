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
880x600 minimum is where truncation shows up first.
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
OUT = Path(ARGS[0]) if ARGS else REPO / ".probe-renders"
sys.path.insert(0, str(REPO))

import whisper_face_gui as gui  # noqa: E402
from whisper_face_gui import (  # noqa: E402
    GUIActions,
    GUIState,
    ModelStatus,
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
    models=(
        ModelStatus("Parakeet Unified", "primary recognition", "Ready"),
        ModelStatus("Whisper Tiny", "fast preview", "Ready"),
        ModelStatus("Whisper large-v3-turbo", "fallback", "Warming"),
        ModelStatus("Qwen3.5-4B", "semantic cleanup", "Ready"),
    ),
)

# The first-run fixtures drive the onboarding poster from real runtime
# evidence, the same way the smoke gate builds its checklist. Each entry
# advances exactly one readiness signal, so the four steps and the explicit
# completion state can all be reviewed as images.
ONBOARDING_RUNTIME = {
    "service_status": "Running",
    "microphone_status": "Not requested",
    "accessibility_status": "Not requested",
    "capture_state": "Ready",
    "hotkey_label": "Right Option",
    "models": [{
        "name": "Parakeet Unified",
        "role": "primary recognition",
        "status": "Warming",
    }],
}
ONBOARDING_STAGES = (
    ("permissions", {}),
    ("hotkey", {
        "microphone_status": "Ready",
        "accessibility_status": "Granted",
    }),
    ("models", {
        "microphone_status": "Ready",
        "accessibility_status": "Granted",
        "hotkey_practiced": True,
    }),
    ("first-dictation", {
        "microphone_status": "Ready",
        "accessibility_status": "Granted",
        "hotkey_practiced": True,
        "models": [{
            "name": "Parakeet Unified",
            "role": "primary recognition",
            "status": "Ready",
        }],
    }),
    ("complete", {
        "microphone_status": "Ready",
        "accessibility_status": "Granted",
        "hotkey_practiced": True,
        "models": [{
            "name": "Parakeet Unified",
            "role": "primary recognition",
            "status": "Ready",
        }],
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
    model.state = replace(
        STATE, section="Settings", settings_pane="Controls",
        hotkey="ctrl_r", hotkey_label="Right Control",
        hotkey_shared_modes=("reply", "command", "code"),
        undo_hotkey="f14", undo_hotkey_label="F14",
        undo_available=True, sound_theme="whisper")
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

print(f"done → {OUT}")
