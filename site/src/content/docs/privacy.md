---
title: Privacy
description: The short version — your voice stays on your Mac.
group: Trust
order: 3
---

Whisper Face is local-first by design, not as a setting you have to find.

## What stays on your Mac

Everything about what you said. Listening, recognition, cleanup, and insertion all run on-device. There are no cloud calls in the dictation path, so you can turn off Wi-Fi and keep working.

## What we never collect

- No audio is uploaded, ever.
- No transcript is stored on anyone else's server.
- No telemetry follows your usage around.
- No behavioral profile is built from how or what you dictate.

## What is kept on your machine

Local is not the same as nothing. Whisper Face keeps a dictation history on your disk: each completed dictation appends its raw and cleaned text to `transcripts.jsonl`, which is what the learning loop and the recent-dictations list read from. Alongside it live your preferences, dictionary, snippets, tones, and learned corrections.

All of it is local, owner-only, and yours. None of it is uploaded. But it is retained, and you should know that before you dictate something you would not want written down.

Some of it you can clear from inside the app — snippets, learned corrections, and acoustic keywords each have inspect-and-forget controls. The rest are ordinary files you delete from the filesystem, or with the uninstaller's personal-data option. That difference is worth knowing rather than discovering.

Audio is the exception: it is held in memory and never written to disk. The optional Flight Recorder buffer is RAM-only and clears on use, pause, disable, and quit.

## Corrections stay yours

When you correct something, it becomes a small local rule you can read, edit, and delete. Personalization lives on your machine as inspectable rules and regression cases, not as a cloud dossier.

## Support bundles

If you ask for help, Diagnostics can write a support bundle. It is saved with owner-only permissions to a location you choose, it is never uploaded on its own, and it contains only allowlisted health, permission, model, and aggregate result metadata — no transcripts.

For the full policy, see [PRIVACY.md](https://github.com/Aiml3ss/whisper-face/blob/main/PRIVACY.md) in the repository.
