---
title: Why your voice stays local
description: Local-first is not a privacy setting here. It is the architecture.
date: '2026-07-10'
---

Plenty of apps have a privacy page. Fewer are built so that the private thing never leaves in the first place.

Whisper Face runs the whole dictation path on your Mac: it listens, recognizes, cleans up, and types without a round trip to a server. That is not a toggle you switch on. It is how the pipeline is put together, which is why it keeps working when you turn off Wi-Fi.

## Evidence over vibes

Being local is the easy part to claim. The harder promise is that the text reflects what you actually said. Whisper Face runs more than one local recognizer and treats their output as the authority. The cleanup step can improve how a sentence reads, but it cannot invent meaning that was not there. Names, numbers, code, and commands survive intact instead of being smoothed into something that merely sounds right.

## Corrections that do not phone home

When you fix a word, that correction becomes a small local rule you can open and remove. Personalization is a file on your machine, not a profile in someone's warehouse.

## When you need help

If something breaks, Diagnostics writes a support bundle with owner-only permissions, no transcripts, and no automatic upload. You decide whether it ever leaves your disk.

Local-first only means something when the design makes leaking hard. That is the bar we are building to.
