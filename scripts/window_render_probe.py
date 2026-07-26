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

Writes window-{home,settings-personalize,settings-privacy,advanced}-
{light,dark}.png (plus a first-run onboarding bonus pair) into
``.probe-renders/`` by default.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / ".probe-renders"
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

# The first-run fixture drives the onboarding poster from real runtime
# evidence, the same way the smoke gate builds its checklist.
ONBOARDING_STATE = gui.normalize_snapshot({
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
})

model = WhisperFaceViewModel(GUIActions())
model.state = STATE
controller = WhisperFaceWindowController.alloc() \
    .initForSmokeWithViewModel_(model)
window = controller.window


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

    model.state = replace(STATE, section="Advanced")
    controller.render()
    snapshot(f"window-advanced-{suffix}")

    model.state = replace(ONBOARDING_STATE, section="Home")
    controller.render()
    snapshot(f"window-onboarding-{suffix}")

print(f"done → {OUT}")
