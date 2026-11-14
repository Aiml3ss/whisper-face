---
title: Getting started
description: What you need, what the installer does, and how to get past macOS's warning to your first dictation.
group: Guide
order: 1
---

Whisper Face installs in one flow and checks its own work along the way. Here is the whole thing, including the parts that take a while.

## Before you start (Mac)

- An Apple Silicon Mac on macOS 14 or newer, and an administrator account. The installer uses Homebrew, which asks for your password.
- The Xcode Command Line Tools with Swift 6 or newer. If they are missing, run `xcode-select --install` first; the installer stops before downloading anything without them.
- About 5 GB free, or 8 GB with the optional models. The disk image is small; on a fresh Mac the installer and the Command Line Tools download a few gigabytes. Allow 15 to 45 minutes. These are estimates; no clean-machine install has been timed yet.

A default install downloads two speech models: Parakeet Unified for English (about 565 MB) and Whisper Tiny (about 75 MB), which also recognizes the other ten languages. Two larger models are optional: Whisper large-v3-turbo (about 1.6 GB) improves recognition outside English and settles disagreements in English, and the Qwen3.5-4B cleanup model (about 3.4 GB, run by Ollama) adds semantic cleanup and the Compose, Reply, and Edit modes. Add them later with `./setup.sh --models`.

## Install on a Mac

1. Download the latest build and open it.
2. Drag the Whisper Face folder — and the app beside it — out of the disk image into a new folder of its own in your home folder, such as `~/Whisper Face`. Your dictation history and settings live in that folder, so avoid Documents or Desktop if iCloud Drive syncs them. The installer refuses to run from the read-only image.
3. Let macOS open it. The build is not signed with a Developer ID or notarized, so macOS blocks `Install.command` the first time. On **macOS 15 or later**, open it once and choose **Done**, then go to **System Settings → Privacy & Security**, scroll down, and choose **Open Anyway**; macOS asks for your password. On **macOS 14**, Control-click `Install.command`, choose **Open**, then confirm.
4. Run `Install.command` from that copy. It provisions the app, its locked dependencies, the speech models, the native helper, the login service, and a round of health checks.
5. On a first install it ends by saying it is not finished yet. Open **System Settings → Privacy & Security** and turn on **Whisper Face** under **Input Monitoring**, **Accessibility**, and **Microphone** (see [Permissions](/docs/permissions)). It starts listening by itself once the first two are on.

```bash
./Install.command
```

To check the install later without changing anything, run `./setup.sh --verify` in that folder.

## Install on Windows

Windows is an **unverified preview**. CI runs the shared code and tests on Windows, but neither the installer nor a full dictation has been run on real Windows hardware yet, so it may not install. Please report what you hit.

1. Download the Windows zip and unzip it into a folder of its own, such as `C:\Users\you\Whisper Face`. Avoid Documents or Desktop if OneDrive syncs them. The installer will not run from inside the zip.
2. Double-click `Install.cmd`. It starts the bundled setup script for you — Windows blocks a double-clicked PowerShell script by default — and provisions the app, its locked dependencies, the speech models (Whisper Tiny, Whisper large-v3-turbo, and the Qwen3.5-4B cleanup model, about 5 GB in all), a login task, and a round of health checks. It needs [winget](https://learn.microsoft.com/windows/package-manager/winget/) (App Installer, from the Microsoft Store) the first time, and about 8 GB free.
3. If Windows says it protected your PC, choose **More info** and then **Run anyway**. The build is not code-signed yet; you can check your download against the release's `SHA256SUMS-windows` first if you would rather verify than trust.
4. Allow microphone access when Windows asks.

The folder also contains `Install.command` and `setup.sh`. Those are the macOS versions of the same installer and do nothing on Windows — the similar name is all they share with `Install.cmd`.

To check an existing install without changing anything, run `Install.cmd --verify` from a Command Prompt in that folder, or `.\setup.ps1 --verify` in PowerShell.

Two differences worth knowing before you start:

- Whisper Face lives in the **tray**, not a menu bar, and there is no main window. Face, Flight Recorder, pause, log, and quit are all in the tray menu.
- Text is inserted with a **plain paste** rather than the Mac's verified insertion transaction, because Windows has no equivalent of the Accessibility API the Mac path relies on.

The installer is meant to be run again safely on either platform. If something looks off later, run it once more and it will repair what it can.

## Your first dictation

Put your cursor in any text field — an email, a chat box, your editor — then hold your hotkey and talk. Let go when you are done. The text lands where your cursor was.

That is the whole loop: **hold, talk, release**.

The default hotkey is **Right Option** on a Mac and **Right Alt** on Windows. On a Mac you can choose a different capture key, and an undo key, under **Settings → Controls**.

## First run

The first time you open the app on a Mac, the window walks you through Permissions, Hotkey, Models, and Dictate together, with live status for each. Hotkey practice only counts as done once the app has actually seen a key capture, so you are never left guessing whether it worked. Windows has no window, so it has no guided first run — the installer's own health checks are the equivalent.

## Where things live

- The **menu-bar face** (Mac) or **tray icon** (Windows) is your quick control. On a Mac, click it to open the full window or reach the Voice Outbox.
- The **main window** (Mac only) has Home, Settings, and Advanced.
- **Advanced** can save a private support bundle if you ever need help. It stays on your disk and holds no transcripts.
