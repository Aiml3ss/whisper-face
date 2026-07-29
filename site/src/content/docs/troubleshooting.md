---
title: Troubleshooting
description: Recover a stuck dictation and find your Voice Outbox.
group: Trust
order: 4
---

Most problems come down to one of a few things. Here is where to look.

## My text did not appear

On a Mac, Whisper Face never guesses where to paste. If your focus or selection changed while it was recognizing, it keeps the result in the **Voice Outbox** instead of typing into the wrong place.

Open the menu-bar face and choose **Voice Outbox**, then use **Copy & Dismiss** to recover the text. The Outbox lives in memory only, so grab anything you need before you quit the app.

## The hotkey does nothing

On a Mac, check that accessibility access is granted (see [Permissions](/docs/permissions)). If it is, re-run `Install.command` to re-link the native helper and re-verify capture.

On Windows the hotkey is **Right Alt**, not Right Option.

## Windows: the installer will not start

Three things stop it, in order of how often:

- **winget is missing.** Install *App Installer* from the Microsoft Store, then run `Install.cmd` again. Whisper Face needs winget and does not install it for you.
- **SmartScreen blocked it.** The build is not code-signed. Choose **More info** then **Run anyway**, or right-click the downloaded ZIP → Properties → **Unblock** before extracting to avoid the prompt entirely.
- **You are running it from inside the ZIP**, or from a folder you cannot write to. Extract the whole folder somewhere like `Documents` first.

If the window closes before you can read the error, open a terminal in the unzipped folder and run `Install.cmd` from there so the output stays on screen.

## Windows: it installed but behaves differently

Expected, and by design. Windows has no main window — Whisper Face lives in the **tray**. It also has no equivalent of the macOS Accessibility API, so text is inserted with a plain paste rather than the verified insertion transaction, there is no Voice Outbox recovery, and corrections cannot be learned from what you fix. Windows is a preview; please report what you hit.

## Recognition feels slow the first time

The first dictation after launch warms the models. After that they stay warm and long sentences are recognized while you are still speaking.

## Something is genuinely broken

Open **Diagnostics** and save a support bundle. It is private, transcript-free, and stored where you choose. Share it only if you decide to, when you ask for help.

```bash
# re-run the installer to repair a broken setup
./Install.command
```
