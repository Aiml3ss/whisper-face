---
title: Getting started
description: Install Whisper Face and dictate your first sentence in a couple of minutes.
group: Guide
order: 1
---

Whisper Face installs in one flow and checks its own work along the way. Here is the whole thing.

## Install

1. Download the latest build and open it.
2. Run `Install.command`. It provisions the app, its locked dependencies, the speech models, the native helper, login services, and a round of health checks.
3. When macOS asks, grant microphone and accessibility access. That is what lets Whisper Face hear you and type on your behalf.

```bash
./Install.command
```

The installer is meant to be run again safely. If something looks off later, run it once more and it will repair what it can.

## Your first dictation

Put your cursor in any text field — an email, a chat box, your editor — then hold your hotkey and talk. Let go when you are done. The text lands where your cursor was.

That is the whole loop: **hold, talk, release**.

## First run

The first time you open the app, the window walks you through Permissions, Hotkey, Models, and Dictate together, with live status for each. Hotkey practice only counts as done once the app has actually seen a key capture, so you are never left guessing whether it worked.

## Where things live

- The **menu-bar face** is your quick control. Click it to open the full window or reach the Voice Outbox.
- The **main window** has Overview, Results, Settings, Models, and Diagnostics.
- **Diagnostics** can save a private support bundle if you ever need help. It stays on your disk and holds no transcripts.
