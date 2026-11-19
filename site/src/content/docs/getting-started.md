---
title: Getting started
description: What you need, what the app's setup does, and how to get past macOS's warning to your first dictation.
group: Guide
order: 1
---

Whisper Face sets itself up in one flow and checks its own work along the way. Here is the whole thing, including the parts that take a while.

## Before you start (Mac)

- An Apple Silicon Mac on macOS 14 or newer.
- About 5 GB free, or 8 GB with the optional models. The setup window downloads about 1 GB. If your Mac lacks Apple's command line tools, it asks macOS to install them first, which is a separate download. No clean-machine setup has been timed yet.

A default install downloads two speech models: Parakeet Unified for English (about 565 MB) and Whisper Tiny (about 75 MB), which also recognizes the other ten languages. Two larger models are optional: Whisper large-v3-turbo (about 1.6 GB) improves recognition outside English and settles disagreements in English, and the Qwen3.5-4B cleanup model (about 3.4 GB, run by Ollama) adds semantic cleanup and the Compose, Reply, and Edit modes. Without it those three modes type nothing and say "Compose mode needs the cleanup model — nothing changed" (or Reply, or Edit); ordinary dictation does not need it. Add them later with `setup.sh --models` (see [From source](#from-source-mac) for where it is).

## Install on a Mac

1. **Download and drag.** Open the disk image and drag **Whisper Face** onto **Applications**.
2. **Open it.** Open Whisper Face from Applications. From 0.7.1 it is signed with an Apple Developer ID and notarized by Apple, so macOS only asks once whether to open an app downloaded from the internet: choose **Open**. Releases up to 0.7.0 were unsigned: macOS blocked their first open until you chose **Open Anyway** in **System Settings → Privacy & Security** (on macOS 14, Control-click the app, choose **Open**, then confirm).
3. **Let it set itself up.** A setup window says what it will download, about 1 GB: the speech model, a small preview model, and the parts they run on. If your Mac lacks Apple's command line tools, it asks macOS to install them first, in a window of macOS's own, and carries on when they are in. Choose **Set Up** and it ticks off each step. If one fails, it says why in plain words and offers **Try Again** and **Show Log**.
4. **Turn on the permissions.** When setup finishes, Whisper Face's first run asks for them. Open **System Settings → Privacy & Security** and turn on **Whisper Face** under **Input Monitoring** and **Accessibility**; macOS asks about the **Microphone** the first time you dictate (see [Permissions](/docs/permissions)). It starts listening by itself once the first two are on.

After that, opening the app simply starts Whisper Face. It keeps its files in `~/Library/Application Support/Whisper Face/checkout`. To update, drag a newer app over the old one and open it, or choose **Check for Updates…** from the menu-bar face.

## From source (Mac)

If you would rather clone the repository, `Install.command` still sets everything up from that folder. It needs an administrator account (it installs Homebrew, which asks for your password) and the Xcode Command Line Tools with Swift 6 or newer; run `xcode-select --install` first if they are missing. On a fresh Mac allow 15 to 45 minutes.

```bash
git clone https://github.com/Aiml3ss/whisper-face.git ~/"Whisper Face"
cd ~/"Whisper Face"
./Install.command
```

Your history and settings live in that folder, so avoid Documents or Desktop if iCloud Drive syncs them. To check the install later without changing anything, run `./setup.sh --verify` there. For a copy the app set up, the same script is at `~/Library/Application Support/Whisper Face/checkout/setup.sh`.

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

What arrives is what you said, tidied: filler dropped, punctuation fixed, and "new line", "new paragraph", and "scratch that" obeyed. One more rule is yours to turn on. **Settings → Controls → “Actually” corrections** is off by default; with it on, "We meet Tuesday, actually Wednesday" is typed as "We meet Wednesday". It is off because it would also change a sentence that contrasts two things: "I thought it was Tuesday, actually Wednesday" would be typed as "I thought it was Wednesday".

## First run

The first time you open the app on a Mac, the window walks you through Permissions, Hotkey, Models, and Dictate together, with live status for each. Hotkey practice only counts as done once the app has actually seen a key capture, so you are never left guessing whether it worked. Windows has no window, so it has no guided first run — the installer's own health checks are the equivalent.

## Where things live

- The **menu-bar face** (Mac) or **tray icon** (Windows) is your quick control. On a Mac, click it to open the full window or reach the Voice Outbox.
- The **main window** (Mac only) has Home, Settings, and Advanced.
- **Advanced** can save a private support bundle if you ever need help. It stays on your disk and holds no transcripts.
