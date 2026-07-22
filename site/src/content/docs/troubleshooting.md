---
title: Troubleshooting
description: Recover a stuck dictation and find your Voice Outbox.
group: Trust
order: 4
---

Most problems come down to one of a few things. Here is where to look.

## My text did not appear

Whisper Face never guesses where to paste. If your focus or selection changed while it was recognizing, it keeps the result in the **Voice Outbox** instead of typing into the wrong place.

Open the menu-bar face and choose **Voice Outbox**, then use **Copy & Dismiss** to recover the text. The Outbox lives in memory only, so grab anything you need before you quit the app.

## The hotkey does nothing

Check that accessibility access is granted (see [Permissions](/docs/permissions)). If it is, re-run `Install.command` to re-link the native helper and re-verify capture.

## Recognition feels slow the first time

The first dictation after launch warms the models. After that they stay warm and long sentences are recognized while you are still speaking.

## Something is genuinely broken

Open **Diagnostics** and save a support bundle. It is private, transcript-free, and stored where you choose. Share it only if you decide to, when you ask for help.

```bash
# re-run the installer to repair a broken setup
./Install.command
```
