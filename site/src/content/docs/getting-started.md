---
title: Getting started
description: Install Whisper Face and dictate your first sentence in a couple of minutes.
group: Guide
order: 1
---

Whisper Face installs in one flow and checks its own work along the way. Here is the whole thing.

## Install on a Mac

1. Download the latest build and open it.
2. Drag the Whisper Face folder — and the app beside it — out of the disk image into a writable spot like Documents. The installer checks for this and refuses to run from the read-only image.
3. Run `Install.command` from that copy. It provisions the app, its locked dependencies, the speech models, the native helper, login services, and a round of health checks.
4. When macOS asks, grant microphone and accessibility access. That is what lets Whisper Face hear you and type on your behalf.

```bash
./Install.command
```

## Install on Windows

1. Download the Windows zip and unzip it somewhere writable like Documents. The installer will not run from inside the zip.
2. Double-click `Install.bat`. It provisions the app, its locked dependencies, the speech models, a login task, and a round of health checks.
3. If Windows says it protected your PC, choose **More info** and then **Run anyway**. The build is not code-signed yet; you can check your download against the release's `SHA256SUMS` first if you would rather verify than trust.
4. Allow microphone access when Windows asks.

Windows is a **preview**. It runs the same recognition and cleanup pipeline as the Mac, and its installer is exercised in CI on every change — but it has not yet been through a full dictation on real Windows hardware, so expect rough edges and please report them.

Two differences worth knowing before you start:

- Whisper Face lives in the **tray**, not a menu bar, and there is no main window. Face, Flight Recorder, pause, log, and quit are all in the tray menu.
- Text is inserted with a **plain paste** rather than the Mac's verified insertion transaction, because Windows has no equivalent of the Accessibility API the Mac path relies on.

The installer is meant to be run again safely on either platform. If something looks off later, run it once more and it will repair what it can.

## Your first dictation

Put your cursor in any text field — an email, a chat box, your editor — then hold your hotkey and talk. Let go when you are done. The text lands where your cursor was.

That is the whole loop: **hold, talk, release**.

The default hotkey is **Right Option** on a Mac and **Right Alt** on Windows.

## First run

The first time you open the app on a Mac, the window walks you through Permissions, Hotkey, Models, and Dictate together, with live status for each. Hotkey practice only counts as done once the app has actually seen a key capture, so you are never left guessing whether it worked. Windows has no window, so it has no guided first run — the installer's own health checks are the equivalent.

## Where things live

- The **menu-bar face** (Mac) or **tray icon** (Windows) is your quick control. On a Mac, click it to open the full window or reach the Voice Outbox.
- The **main window** (Mac only) has Home, Settings, and Advanced.
- **Advanced** can save a private support bundle if you ever need help. It stays on your disk and holds no transcripts.
