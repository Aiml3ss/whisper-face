---
title: Privacy
description: The short version — your voice stays on your Mac, and here is exactly what is kept.
group: Trust
order: 3
---

Whisper Face is local-first by design, not as a setting you have to find.

## What stays on your Mac

Listening, recognition, cleanup, and insertion all run on-device. There are no cloud calls in the dictation path, so you can turn off Wi-Fi and keep working. The optional cleanup model runs in a local Ollama service that Whisper Face reaches only at `127.0.0.1`, through a connection that ignores proxy settings; without that model, nothing is sent at all.

## What we never collect

- No audio is uploaded, ever.
- No transcript is stored on anyone else's server by us. (Your history lives in the install folder, so if that folder is synced to iCloud Drive or OneDrive, the sync service has a copy. Install it somewhere that is not synced.)
- No telemetry follows your usage of the app around.
- No behavioral profile is built from how or what you dictate.

## What is read when you press the key

To recognize the words in front of you, each press of the capture key reads, for that one dictation: the app's name, the focused field's text and selection, the window title, the open document (its last 6,000 characters) and up to 80 file names beside it, and the clipboard if it holds 800 characters or fewer. That context is not saved. Nothing is read from a password field, and a clipboard that a password manager marked as concealed or transient is skipped. In Reply and Edit modes the field text or selection is what gets rewritten, so the result is kept in your history like any other dictation.

## The clipboard

Whisper Face pastes by putting your text on the clipboard for a moment. It is marked so clipboard managers that follow the common convention neither record nor show it, it is kept off Universal Clipboard, and about a second later your previous clipboard is put back. Clipboard software that ignores the convention can still see it while it is there.

## What is kept on your machine

Local is not the same as nothing. Whisper Face keeps a dictation history on your disk: each completed dictation appends its raw and cleaned text, the app, and the time to `transcripts.jsonl`, which is what the learning loop and the recent-dictations list read from. Text that went to the Voice Outbox is logged too. The history holds at most 500 dictations and nothing older than 30 days, and it is trimmed every time a new one is added. There is no switch that turns it off yet. Alongside it live your preferences, dictionary, snippets, tones, and learned corrections.

All of it is local and readable only by your user account on a Mac. None of it is uploaded. But it is retained, and you should know that before you dictate something you would not want written down.

Some of it you can clear from inside the app — learned corrections (Forget removes the rule and everything learned about it), snippets, and acoustic keywords each have inspect-and-forget controls. The rest, including `transcripts.jsonl`, are ordinary files you delete from the filesystem, or with the uninstaller's personal-data option. That difference is worth knowing rather than discovering.

Audio is the exception: on your Mac it is held in memory and never written to disk. The optional Flight Recorder buffer is RAM-only and clears on use, pause, disable, and quit.

## Corrections stay yours

When you correct the same word twice, it can become a small local rule you can read and forget. Personalization lives on your machine as inspectable rules and regression cases, not as a cloud dossier.

## Support bundles

If you ask for help, **Advanced** can write a support bundle. It is saved with owner-only permissions to a location you choose, it is never uploaded on its own, and it contains only allowlisted health, permission, model, and aggregate result metadata — no transcripts.

## This website

This promise is about the app. The site is static, sets no cookies, and its source loads no analytics script. When this page was last reviewed (14 November 2026), Cloudflare, which hosts the site, was adding its cookieless Web Analytics beacon from its dashboard, so page views were being counted. That setting lives outside the source.

For the full policy, see [PRIVACY.md](https://github.com/Aiml3ss/whisper-face/blob/main/PRIVACY.md) in the repository.
