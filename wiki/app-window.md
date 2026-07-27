---
title: "App Window"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
tags: [gui, macos, ux, surfaces, onboarding]
aliases: [native-window, whisper-face-window, home-settings-advanced, gui]
summary: "The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector."
confidence: high
---

# App Window

## Definition

`whisper_face_gui.py` (8,587 lines, AppKit, no browser runtime) is the
Mac app window opened from [[menu-bar]]'s first row. Since 2026-07-26
(#104) it has three sections and two Settings panes:

```python
SECTIONS = ("Home", "Settings", "Advanced")
SETTINGS_PANES = ("Personalize", "Privacy")
```

The previous five sections (Overview, Results, Settings, Models,
Diagnostics) and three panes (Modes, Personalize, Privacy) are gone.
Closing the window does not stop dictation.

## The three sections

- **Home** = the old Overview plus the Results summary. A hero card
  (phase pill — READY / RECORDING / PROCESSING / RECOVERY AVAILABLE /
  ACTION NEEDED / PAUSED / STARTING LOCALLY — status, engine, a wrapping
  outbox line, and Pause / Review Setup / Copy & Dismiss); a metrics card
  (Last dictation, Words today, Time saved); and a compact **Last
  dictation** card carrying the summary, mode, engine and audio labels
  with Play Span, Clear ([[acoustic-personalization]]) and **Inspect
  Evidence**. First run replaces all of it with the onboarding poster.
- **Settings → Personalize** = the face picker (identity, not privacy)
  above six rows: App tones, Snippets, Vocabulary, Learned corrections
  ([[personalization]]), Pronunciation keywords, and **Voice modes**,
  whose View dialog lists the six Right Option shortcuts
  ([[voice-modes]]) — the replacement for the deleted zero-interactive
  Modes tab.
- **Settings → Privacy** = exactly three switches: Voice Object Commands
  (with an Inspect button, [[voice-objects]]), Flight Recorder, and
  Acoustic Time Machine.
- **Advanced** = Models and Diagnostics merged: the Selective Re-listen
  toggle and status, four model rows with readiness pills, the wallet
  shadow advisory ([[model-wallet]]), a 2×3 status card (Service,
  Microphone, Accessibility, Personal Regression Lab, Motion, Build),
  then Open Log, Copy Support Snapshot, Run Verification (⌘R), Export
  Support Bundle…, Open System Settings, License Notices, Exact Source
  ([[governance]]) and the license footnote.

## The evidence inspector carries the whole trust surface

The persistent evidence and assurance cards are gone as chrome. In
exchange, `result_evidence_text` gained a keyword-only `result`
parameter, and when it is supplied the inspector opens with a RESULT
SUMMARY section: [[stable-prefix]] words, [[protected-anchor]] count,
[[voice-compiler]] decisions with confidence, alternatives considered,
deduplicated cleanup kinds, [[proof-edit]] accepted/rejected, context
influence, the [[context-firewall]] summary, and the
[[consequence-receipts]] summary plus review advisory when non-empty —
followed by the existing ALTERNATIVES / PROTECTED ANCHORS / PROOF EDITS /
TIMING sections. The smoke test states the contract: the explicit
evidence reveal "must carry the entire trust surface instead".

## Four surfaces left the window — and only the window

Demonstrations authoring, the risky-action confirmation ceremony, the
[[point-and-speak]] preview/press dialogs and the Drop-to-Target preview
dialogs ([[inert-foundations]]) have no selector, button, dialog method
or catalog row on any page. What remains, verified at `1165335` (and the
chibi-clay rebuild #120 left the contract tuples, actions and key
equivalents unchanged): their
`GUIActions` fields (still listed in the contract's action names), their
view-model passthrough methods, their runtime modules, and their tests.
They are developer-invokable, not deleted.

*Precision*: #104's commit message credits
`tests/test_gui_settings_runtime.py` with covering all four. That file
covers only the risky-action runtime; the demonstrations, Point-and-Speak
and Drop-to-Target view-model layers are covered in
`tests/test_whisper_face_gui.py`.

## Shape, keyboard, and proof

- Resizable, opens at 1000×640, minimum 880×600; since the chibi-clay
  rebuild the sidebar is gone — a 64pt top bar carries the face chip,
  the `LOCAL FIRST` badge, Home/Settings pills and an Advanced tool
  button, with a `BUILD {version}` tag in the lower corner; Home leads
  with a live `LiveFaceView` character that breathes, blinks, glances,
  and lip-syncs the mic level while recording; content sits in a
  centred column capped at 720pt whose width constraint stays *below*
  the window's stay-put priority so long trust copy can never inflate
  the window ([[design-language]]).
- Keyboard contract: `return:continue-setup`, `command-d:advanced`,
  `command-r:verification`. ⌘D lives on the always-visible Advanced
  tool button, so it works from every section.
- **First run** is four steps plus a completion — "First, let me hear
  you", "Now try your key", "Getting your models ready", "Say something",
  "Nice. You’re ready." — on a poster with a 208pt tilted face chip, a
  real progress bar, four step chips, and the kicker "Everything you say
  stays on this Mac." Hotkey practice completes only after capture is
  actually observed.
- **The render probe**: `scripts/window_render_probe.py` builds the
  controller through the no-system-state smoke path, never orders the
  window front, and writes nine views (Home, both Settings panes,
  Advanced, and the five first-run stages) in both appearances — 18 PNGs
  — into the gitignored `.probe-renders/`. `--size WIDTHxHEIGHT` lets the
  880×600 minimum be reviewed directly.

## Related Concepts

- [[menu-bar]] — the surface that routes here
- [[design-language]] — the type, colour and motion this is built from
- [[whisper-faces]] — the character in the top-bar chip and the poster
- [[privacy-and-security]] — the support snapshot and bundle rules

## References

- whisper_face_gui.py (`SECTIONS`/`SETTINGS_PANES` :32-33, `_build_home`
  :5652, `_build_settings` :6028, `_build_advanced` :6207,
  `result_evidence_text` :2515, key equivalents :903-904);
  whisper_face_render.py (`LiveFaceView`);
  scripts/window_render_probe.py; README "Native Mac window"
- [[2026-07-26-interface-rebuild-research]]
