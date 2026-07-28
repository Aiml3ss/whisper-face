---
title: Voice typing on Windows and Mac, from one pipeline
description: Whisper Face now has a Windows download. What is shared between the platforms, what is genuinely different, and why one of them is still called a preview.
date: '2026-07-28'
---

Whisper Face started on the Mac and stayed there for a while. As of 0.4.0 there is a Windows download.

This post is about what that does and does not mean, because "now on Windows" is the kind of claim that usually hides a lot.

## What is genuinely shared

Both platforms run the same `dictate.py`. Not a port, not a rewrite — the same file, with platform checks around the parts that cannot be shared. That means Windows gets:

- **The same hold-to-talk capture.** Hold a key, speak, release, text appears at the cursor.
- **The same recognition cascade.** A fast model speculates while you talk, a larger one confirms, and the confident answer wins.
- **The same cleanup discipline.** Names, numbers, dates, URLs, paths, and commands are protected anchors. The optional local language model may only make edits it can prove.
- **The same snippets, tones, vocabulary, and learned corrections**, stored the same way in the same shapes.
- **The same Flight Recorder** — a default-off, RAM-only rolling buffer, so you can speak first and decide to keep it after.

Sharing the pipeline is the whole point. A separate Windows implementation would drift, and the second implementation is always the one that quietly gets the trust properties wrong.

## What is different, and why

**No window. A tray icon.** The Mac app has a native window with Home, Settings, and Advanced. Windows has a tray menu: choose your face, Flight Recorder, pause, open log, quit. Everything the window does is not yet available there.

**The hotkey is Right Alt**, standing in for Right Option.

**Insertion is a plain paste.** This is the significant one. On the Mac, Whisper Face uses the Accessibility API to lease the destination field when you press the key, revalidate it before pasting, and read back what actually landed. If focus moved while you were talking, the text goes to a recoverable outbox instead of into the wrong window.

Windows has no equivalent API, so it uses the legacy paste path. Dictation works; the verified insertion transaction does not exist there. If you switch windows mid-sentence on Windows, the text goes where the cursor now is.

**Some Mac-only features stay Mac-only.** The native Core ML recognition helper is an Apple optimization. Selective re-listen depends on word timings the Windows path does not produce by default. Spoken edit commands, the acoustic time machine, and Point-and-Speak are all gated behind platform checks.

## Why it is called a preview

Because it has not been through a full dictation on a real Windows machine.

That deserves unpacking, because it sounds worse than it is and we would rather over-explain than let you find out later. The Windows installer runs in CI on a real Windows runner on every single change: it validates the locked dependency set, imports the Windows runtime, and runs the platform-independent test suites. The packaging is verified, the launcher is checked, the bundle's contents are digest-verified.

What none of that proves is that holding Right Alt in Word and speaking a sentence feels right. Recognition quality on a given machine's microphone, latency on a given CPU, whether the tray icon renders sensibly at your DPI, whether the login task starts cleanly after a reboot — those need someone to sit down at a Windows machine.

Until that has happened, calling it anything other than a preview would be describing an expectation as a result. The flag that drives the "preview" label on the site flips when the dictation actually happens, not when we think it will work.

## Getting it running

Download the Windows zip from the release, unzip it somewhere you can write, and run `Install.cmd`. That single file exists because Windows blocks a double-clicked PowerShell script by default; it launches the bundled installer for you.

Three things to know before you start:

- **winget must already be installed** (App Installer, from the Microsoft Store). The installer needs it and does not install it for you.
- **SmartScreen will warn you.** The build carries no Authenticode signature, so Windows cannot name a publisher. Right-click the downloaded ZIP → Properties → **Unblock** before extracting to avoid the prompt, or verify the download against the published `SHA256SUMS` first.
- **`Install.command` sits next to `Install.cmd`.** That one is the macOS installer and does nothing on Windows.

The installer is safe to run again. Rerunning replaces the login task and leaves your dictionary, snippets, tones, preferences, and transcripts alone.

## If it breaks

It might. Tell us — a preview with no reports is a preview forever. The most useful thing to include is whatever the console printed before it closed; if the window vanishes too fast, open a terminal in the unzipped folder and run `Install.cmd` from there so the error stays on screen.

[Download it](/#install), or read the [getting started guide](/docs/getting-started).
