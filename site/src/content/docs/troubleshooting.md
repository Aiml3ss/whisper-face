---
title: Troubleshooting
description: Recover a stuck dictation and find your Voice Outbox.
group: Trust
order: 4
---

Most problems come down to one of a few things. Here is where to look.

## My text did not appear

On a Mac, Whisper Face never guesses where to paste. If your focus or selection changed while it was recognizing, it keeps the result in the **Voice Outbox** instead of typing into the wrong place.

Open the menu-bar face and choose **Voice Outbox**, then use **Copy & Dismiss** to recover the text. The Outbox itself lives in memory, so grab anything you need before you quit the app (the text is also in your local `transcripts.jsonl` history).

## The hotkey does nothing

On a Mac, check that **Whisper Face** is turned on under both **Input Monitoring** and **Accessibility** (see [Permissions](/docs/permissions)); without Input Monitoring the key is never seen. After an update, if the switches look on and nothing works, use **Clear and Start Again** in the Whisper Face Permissions window: switching them off and on does not help. If the log says `hotkey listener keeps exiting`, the key listener cannot start, which almost always means a permission is missing. If the permissions are fine, run `setup.sh --verify`, then run `setup.sh` again to repair (see below for where it is).

On Windows the hotkey is **Right Alt**, not Right Option. On keyboard layouts with AltGr, AltGr starts dictation too.

## My dictation stopped early

A take ends on its own after ten minutes. If the microphone delivers nothing for two seconds, the take ends with “Listening failed — try again”. If it stops after hearing you (a headset disconnects, say), Whisper Face keeps what it heard and says the text may be incomplete.

## Compose, Reply, or Edit typed nothing

If the caption says “Compose mode needs the cleanup model — nothing changed” (or Reply, or Edit), the optional cleanup model is not installed. Those modes are rewrites done by that model, so without it they leave your text and your selection alone instead of pasting what you said to them. Add the model with `./setup.sh --models`, or use ordinary dictation. If the caption says the mode “got no rewrite from the cleanup model”, the model is installed but did not answer usefully that time; try again. In a language other than English the caption says the mode needs English.

## “Actually” did not correct my sentence

That is the default. "We meet Tuesday, actually Wednesday" is typed as you said it unless you turn on **Settings → Controls → “Actually” corrections** on a Mac. With it on, the sentence becomes "We meet Wednesday" — and "I thought it was Tuesday, actually Wednesday" becomes "I thought it was Wednesday", which is why it is off until you choose it. Saying "scratch that" takes back what you just said either way.

## Windows: the installer will not start

Three things stop it, in order of how often:

- **winget is missing.** Install *App Installer* from the Microsoft Store, then run `Install.cmd` again. Whisper Face needs winget and does not install it for you.
- **SmartScreen blocked it.** The build is not code-signed. Choose **More info** then **Run anyway**, or right-click the downloaded ZIP → Properties → **Unblock** before extracting to avoid the prompt entirely.
- **You are running it from inside the ZIP**, or from a folder you cannot write to. Extract the whole folder into a folder of its own, such as `C:\Users\you\Whisper Face`, first.

When setup fails, the window stays open and names `install.log` in the folder, which records the whole run. Attach it to a bug report.

## Windows: it installed but behaves differently

Expected, and by design. Windows has no main window — Whisper Face lives in the **tray**. It also has no equivalent of the macOS Accessibility API, so text is inserted with a plain paste rather than the verified insertion transaction, there is no Voice Outbox recovery, and corrections cannot be learned from what you fix. Windows is a preview; please report what you hit.

## Recognition feels slow the first time

The first dictation after launch warms the models. After that they stay warm and long sentences are recognized while you are still speaking.

## Something is genuinely broken

Open **Advanced** and save a support bundle. It is private, transcript-free, and stored where you choose. Share it only if you decide to, when you ask for help.

```bash
# a copy the app set up: check it, then run setup again to repair it
"$HOME/Library/Application Support/Whisper Face/checkout/setup.sh" --verify
"$HOME/Library/Application Support/Whisper Face/checkout/setup.sh"

# an install from source: the same, in its folder
./setup.sh --verify
./Install.command
```
