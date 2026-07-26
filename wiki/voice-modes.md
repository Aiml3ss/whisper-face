---
title: "Voice Modes"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [modes, tones, snippets, hotkey, ux]
aliases: [six-modes, tones, snippets, flight-recorder]
summary: "Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use."
confidence: high
---

# Voice Modes

## Definition

The same hold-to-talk gesture becomes six explicit modes via the
modifiers held at press (`mode_from_modifiers` in `parrot_core.py`):
none = faithful **capture**, Shift = polished **compose**, Control =
context-aware **reply**, Command = selected-text **edit**,
Shift+Control = spoken-**code** compilation, Command+Control = a small
allowlisted **command** mode.

## Key Properties

- **Contracts differ by mode**: capture/code accept cleanup only as
  validated [[proof-edit]]s; compose/reply/edit deliberately keep a
  broad-rewrite contract. Edit mode requires a selection, so ordinary
  dictation cannot rewrite an unseen document. Command mode recognizes
  only undo, redo, select all, copy, cut, paste, delete selection, new
  line, and escape — it cannot launch shell commands.
- **Tones**: casual in chat apps (no trailing period), formal in Mail,
  technical in editors, verbatim in terminals — resolved menu override
  first, then per-app sets, then default. A spoken override ("Formal
  tone, …") applies once and requires punctuation after the tone word so
  "Formal education is…" cannot match.
- **Snippets**: "insert my email" pastes saved text. Whole-utterance
  snippets paste directly; inline triggers are masked to sentinel
  characters through cleanup ([[cleanup-pipeline]]). Editing the exact
  pasted placeholder within ten seconds saves the replacement
  ([[personalization]]).
- **Spoken structure**: "new line" / "new paragraph" / "scratch that";
  explicit list lead-ins become dash lists.
- **Flight Recorder**: default-off, menu-toggled, 20-second RAM-only
  ring buffer; tap Right Option afterwards to paste the latest
  utterance. A hold consumes and clears the buffer, and a tap selects
  only speech that ended before key-down so the start cue is never
  captured. Audio never touches disk.
- Voice Object command diversion ([[voice-objects]]) and the
  risky-action phrase ([[inert-foundations]]) intercept before ordinary
  paste, in capture mode only.

## Related Concepts

- [[dictation-pipeline]] — where modes resolve
- [[cleanup-pipeline]] — mode-dependent contracts
- [[whisper-faces]] — the menu-bar surface for tones and corrections

## References

- parrot_core.py `mode_from_modifiers`, `classify_edit_command`;
  dictate.py tone tables, snippets, FlightRecorder; README "Daily use"
- [[2026-07-26-runtime-pipeline-research]]
