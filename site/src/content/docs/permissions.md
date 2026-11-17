---
title: Permissions
description: The three macOS permissions Whisper Face needs, and why each one is needed.
group: Guide
order: 2
---

Whisper Face needs three macOS permissions. All three are required for dictation to work, and each is granted to **Whisper Face** under **System Settings → Privacy & Security**.

## Input Monitoring

This lets the app notice you holding the capture key while another app is in front. Its key listener receives key events in order to match the keys you bound; it does not store or log what you type. Without it, holding the key does nothing.

## Accessibility

This lets the app type into the field under your cursor. It also reads that field so it can paste in the right place and check what landed, and, when you press the key, reads nearby text, the selection, the window title, and the open document to help recognize the words in front of you. Nothing is read from password fields. See [Privacy](/docs/privacy) for exactly what is read.

## Microphone

This lets the app hear you. Audio is processed on your Mac and is never uploaded. When you are not holding the key, it is not listening — unless you turn on Flight Recorder, which keeps the microphone open so it can hold the last twenty seconds in memory.

## Granting them

When setup finishes, Whisper Face's first run asks for them. Open **System Settings → Privacy & Security** and turn on **Whisper Face** under **Input Monitoring** and **Accessibility**; macOS asks about the **Microphone** the first time you dictate. The app starts listening by itself once the first two are on. While one is still missing, the app's window shows an **Open System Settings** button that goes straight to that permission's pane.

## If a permission gets stuck

The build is not signed with a Developer ID, so macOS can treat an updated or rebuilt app as new. It then keeps Whisper Face listed and switched on, but the grant no longer applies. When that happens Whisper Face shows a small **Whisper Face Permissions** window with one button per missing permission, each opening its own page in System Settings. Turn **Whisper Face** off and on again there, or remove it and add it back; the window closes by itself once both are granted. If that does not help, **Still Not Working…** offers to clear Whisper Face's own two entries so macOS asks afresh.
