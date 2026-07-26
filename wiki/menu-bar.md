---
title: "Menu Bar"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [menu-bar, macos, ux, surfaces]
aliases: [status-bar, statusbar, menu, tray]
summary: "Six choices by default and four rows that appear only when they have something to offer — the everyday control surface, with everything else moved into the window."
confidence: high
---

# Menu Bar

## Definition

The macOS menu is the fastest everyday control and deliberately not a
second control panel. Since 2026-07-26 (#101) it holds **six choices**,
plus four rows that appear only when they have something to offer. The
in-code rationale sits directly above the construction
(`dictate.py:2727-2733`): someone opening the menu fifty times a day used
to meet thirteen visible options, a six-row mode cheat sheet, and a dense
evidence submenu every time.

## Always present

In assembly order (`StatusBar.init`, `dictate.py:2759-2772`):

1. **Open Whisper Face…** — the route to [[app-window]]
2. the usage lines — two non-actionable rows refreshed on open:
   `Today: {n} dictations · {n} words` and `Last 7 days: {n} · {n} words`
3. **Pause Dictation** / **Resume Dictation**
4. **Choose Face** — the only submenu in the whole menu, rebuilt each
   open over the ten [[whisper-faces]] and radio-checked
5. **Check for Updates…** ([[distribution]])
6. **Quit Whisper Face**

That is seven `NSMenuItem`s but six *choices*: the usage lines are one
disabled group of two.

## Rows that appear only when useful

- **Last Recognition** — shown once a first result exists
  (`refresh_recognition_item`); titled `Last Recognition — Review` only
  on the `review` consequence route ([[consequence-receipts]]). Opens the
  evidence inspector on [[app-window]].
- **Voice Outbox** — shown while the recovery queue is non-empty, with a
  bounded count; routes to Home ([[insertion-transaction]]).
- **Voice Inbox** — shown while local drafts are queued, with a bounded
  count ([[voice-objects]]).
- **Selective Re-listen** — the evidence-gated toggle. It is shown when
  `evidence_ready or requested`: that is, when a valid local re-listen
  [[activation-receipt]] exists *or* the preference is on. A dormant
  default install — preference off, no receipt — shows no row at all, so
  there is never a dead switch. Titles carry the live state
  (`: On` / `: Warming` / `: Starting` / `: Off`).

## What moved, and where it lives now

Nothing was lost. Tones, snippets, vocabulary, learned corrections,
pronunciation keywords and the mode reference are under Settings →
Personalize; the Flight Recorder toggle under Settings → Privacy;
alternatives and decision evidence in the Home evidence inspector;
**Open Log** under Advanced — all reachable through the menu's first row
([[app-window]]).

Two consequences in the code: `builtin_tone` died with the tones submenu
and has zero matches repo-wide, while `tone_for` keeps its
menu-override-then-per-app-sets resolution unchanged ([[voice-modes]]);
and the Flight Recorder `NSMenuItem` object survives *unattached*,
because the pause path and the window's Privacy toggle still drive its
state helpers.

## Windows is a different surface

The pystray tray was untouched by #101 and still shows five entries:
Choose Face, Flight Recorder (RAM only), Pause Dictation, Open Log, Quit.
It keeps two rows macOS moved into the window, because Windows has no
window to move them into ([[windows-support]]).

> 📝 **Updated from [[2026-07-26-interface-rebuild-research]]**: this
> page replaces the thirteen-item menu described by the wiki's first
> build. `README.md:437` and `:497` still describe the old shape — a Last
> Recognition *submenu* ending in "Open Last Result…" and an
> always-available Voice Inbox entry; neither matches the code.

## Related Concepts

- [[app-window]] — where everything the menu dropped now lives
- [[whisper-faces]] — the face and its hand-authored menu-bar silhouette
- [[voice-modes]] — the gesture the menu sits around
- [[design-language]] — the springs the menu-bar face runs

## References

- dictate.py `StatusBar` (:2684-2990), `usage_stats` (:2566-2595),
  `recognition_root_title` (:5879-5882), `WindowsStatusBar` (:3311-3323)
- [[2026-07-26-interface-rebuild-research]]
