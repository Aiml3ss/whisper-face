---
title: "Windows Support"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [windows, platform, parity]
aliases: [windows-path, tray-icon]
summary: "Windows shares the core capture, cascade, cleanup, snippets, tones, and learning pipeline with one-click installer parity — while the Cocoa HUD, Accessibility context, and several macOS-only trust features stay Mac."
confidence: high
---

# Windows Support

## Definition

Windows 10/11 x64 runs the same `dictate.py` with faster-whisper +
ctranslate2 (CUDA float16 preferred, CPU int8 fallback) and keeps
one-click installer parity as a release gate. Mac is the production
focus; Windows is the shared-pipeline sibling.

## Key Properties

- **Shared**: hold-to-talk capture, the Tiny→Turbo confidence cascade,
  [[cleanup-pipeline]], snippets, tones, Flight Recorder, and the
  learning pipeline ([[personalization]]).
- **Different**: a pystray tray icon with per-state PIL faces instead of
  the Cocoa HUD; no Accessibility, so context comes from the window
  title + clipboard and insertion takes the legacy paste path (leases
  are unavailable); Right Alt replaces Right Option and the Windows key
  replaces Command; no word timings by default, so prosody formatting
  and selective re-listen never activate.
- **The tray did not follow the Mac menu.** #101 shrank the macOS
  [[menu-bar]] because [[app-window]] could hold what it dropped;
  Windows has no such window, so its five-entry tray still carries
  Choose Face, Flight Recorder (RAM only), Pause Dictation, Open Log and
  Quit. Windows also writes a window title into the same transcript
  field that holds a macOS bundle id, which is why the evidence harnesses
  withhold that field entirely on Windows ([[evidence-capture]], issue
  #110).
- **macOS-only features** (gated by platform checks): the native
  Parakeet helper, [[voice-objects]], spoken edit commands,
  [[acoustic-personalization]] time machine, [[delayed-cleanup]],
  [[point-and-speak]], and the GUI window.
- **Installer**: winget bootstrap with PATH refreshes, ACL-locked
  private logs, Ollama started only if unreachable, a generated
  digest-receipted launcher shim, and a current-user Task Scheduler
  task (limited run level, restart on failure). Verify proves
  task→launcher→checkout binding and endpoint health; the
  Ollama PID identity check is documented as Mac-only.
- **CI**: the Windows smoke workflow syncs the locked env, imports the
  runtime, runs ~45 platform-independent tests, and parses `setup.ps1`
  — no longer a required status check ([[governance]]).

## Related Concepts

- [[installers-and-services]] — the PowerShell flow in detail
- [[asr-cascade]] — the cascade without Parakeet
- [[whisper-faces]] — tray-icon face parity

## References

- setup.ps1; dictate.py Windows shims and WindowsStatusBar;
  .github/workflows/windows-smoke.yml
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-ops-governance-research]]
