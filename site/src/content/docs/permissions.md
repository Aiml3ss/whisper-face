---
title: Permissions
description: What Whisper Face asks for, and why each one is needed.
group: Guide
order: 2
---

Whisper Face asks for two macOS permissions. Both are required for dictation to work, and both are used only for what their names say.

## Microphone

This lets the app hear you while you hold your hotkey. Audio is processed on your Mac and is never uploaded. When you are not holding the key, it is not listening.

## Accessibility

This lets the app type into the field under your cursor and read enough of that field to paste in the right spot. Without it, macOS blocks one app from inserting text into another, and Whisper Face cannot deliver your words.

## Granting them

macOS asks during first run. If you skipped a prompt, open **System Settings → Privacy & Security** and add Whisper Face under **Microphone** and **Accessibility**. The app's first-run window also shows live status for each, so you can see the moment a permission takes effect.

## If a permission gets stuck

macOS occasionally caches an old answer after an update. Re-running `Install.command` re-checks the permissions and points you at the exact setting to toggle if one is missing.
