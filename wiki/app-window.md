---
title: "App Window"
type: concept
language: en
created: 2026-07-26
modified: 2026-11-15
tags: [gui, macos, ux, surfaces, onboarding]
aliases: [native-window, whisper-face-window, home-settings-advanced, gui]
summary: "The native Mac window is a sidebar of five pages — Home, then Personalize, Controls, Privacy and Advanced — built from system controls and grouped rows, with the whole trust surface behind one explicit evidence inspector."
confidence: high
---

# App Window

## Definition

`whisper_face_gui.py` (16,709 lines, AppKit, no browser runtime) is the
Mac app window opened from [[menu-bar]]'s first row. Underneath, the view
model still has three sections and three settings panes:

```python
SECTIONS = ("Home", "Settings", "Advanced")
SETTINGS_PANES = ("Personalize", "Controls", "Privacy")
```

On screen they are five pages in a sidebar. `SIDEBAR_ROWS` is a projection
of those tuples, not a second source of truth: each row names the
(section, pane) it shows. Closing the window does not stop dictation.

The previous five sections (Overview, Results, Settings, Models,
Diagnostics) and the original panes (Modes, Personalize, Privacy) went in
#104; the Controls pane arrived with the control features (#128, #135).

## The shell

- **Window**: a standard titled, resizable `NSWindow` whose content runs
  under a transparent title bar, held by an `NSSplitViewController`. The
  sidebar is a system sidebar item, so the sidebar material, the
  full-height layout and the divider come from whichever macOS is
  running. It has one fixed width (200 pt) and cannot be dragged shut.
- **Sidebar**: the chosen face on a chip, "Whisper Face", and a one-word
  state with a dot (Ready, Listening, Working, Recoverable, Needs
  attention, Paused, Starting, or Setting up during first run; the dot is
  never amber, which would read as the system's microphone indicator).
  Then **Home**, a small "Settings" group label, and **Personalize**,
  **Controls**, **Privacy** and **Advanced**, each a 32 pt row with an SF
  Symbol; the row that is showing has the selection fill and a bold
  label. `Build {version}` sits at the foot. Pressing the face chip only
  wobbles it.
- **Title strip**: the page's name, read by VoiceOver as a heading, over a
  content column of up to 720 pt centred in the rest of the window. Its
  width constraint stays *below* the window's stay-put priority, so long
  trust copy can never inflate the window ([[design-language]]).
- **Notices** sit in a band at the foot of the column: a Review Setup
  notice clears by itself once the issue it describes is no longer
  reported; other error notices stay until the next action, and
  success/info notices last about 8 s. A page that scrolls runs to the
  window's bottom edge while no notice is showing and stops just above
  one that is; Home and setup always keep clear of the band, so they do
  not move when a notice comes or goes.

## The pages

- **Home** — a stage in amber or emerald (whichever sets off the chosen
  face) holds the live face, 200 pt, tilted, on its own hard shadow, and
  a speech bubble: one headline for the state ("Ready when you are"), one
  instruction that shows the bound capture key as a key cap, then the
  actions. Pause (Resume while paused) is always the amber primary; the
  one secondary is Copy & Dismiss while the Voice Outbox holds text,
  otherwise Review Setup when a review is due. When both are due, Review
  Setup moves to a row at the top of the Dictation group below, so no
  action is out of reach. Under the stage, three tilted stat stickers,
  one number each (Last dictation, Words today, Time saved), then one
  group of rows headed **Dictation**
  with the active engine beside the header: the last result's summary,
  mode and engine with **Inspect Evidence**, the retained-audio line with
  Play Span and Clear ([[acoustic-personalization]]), and the Voice
  Outbox line.
- **Personalize** = the face picker — all sixteen faces in a grid that
  fills its group, eight to a row on chips of up to 78 pt, the chosen one
  amber with its hard shadow and named beside the "Your face" header —
  then six rows led by the dictation
  **Language** pop-up — it decides which recognizer runs, so it outranks
  everything below it ([[asr-cascade]]) — then App tones, Snippets,
  Vocabulary, Learned corrections ([[personalization]]), and
  Pronunciation keywords. Each of those five opens an **editor sheet**
  on the window (see below), not a system alert.
- **Controls** (#128, #135) = how you drive it. **Keys**: the capture key
  (shown as a key cap, with Change…), an optional **Undo key** that undoes
  the last dictation, and **Voice modes**, whose View dialog lists the six
  capture-key shortcuts ([[voice-modes]]). Only the capture key is
  configurable; the six mode modifiers stay fixed — the dialog says so
  itself, "so capture behavior stays predictable and safe". A capture key
  equal to the undo key is refused. **Sounds**: macOS, the first-party
  set, or silent, as a segmented control, with Preview. **Corrections**:
  the **“Actually” corrections** switch (November 2026), off by default,
  which turns on the typed "X, actually Y" self-correction
  ([[cleanup-pipeline]]); its caption gives an example and the note under
  the group gives the cost.
- **Privacy** = four switches in two groups. *Audio kept in memory*:
  Flight Recorder and Acoustic Time Machine. *Dictations and drafts*:
  Recent dictations (with Browse…) and Voice Object Commands (with an
  Inspect button, [[voice-objects]]).
- **Advanced** = Models and Diagnostics merged, in three groups.
  **Models**: the Selective Re-listen switch, then four model rows, each
  with its state as a word and a symbol (Ready / Installed / Optional /
  Missing / Needs repair / Wrong version / Checking — an optional model
  that was never installed raises no warning, November 2026), then notes
  under the group. One of them is the wallet shadow advisory
  ([[model-wallet]]) in plain words: how many model files were found on
  this Mac and how many are warmed up, and that the checks never change
  which model runs. **Status**: Service, Microphone, Accessibility,
  Correction tests (the Personal Regression Lab), Motion, Build.
  **Support**: Verification (Run Verification, ⌘R), Log (Open Log),
  Support snapshot (Copy Support Snapshot), Support bundle (Export
  Support Bundle…), Permissions (Open System Settings), and License
  (License Notices, Exact Source; [[governance]]). The Permissions row
  appears only while a permission is reported missing and opens that
  permission's own Privacy & Security pane.

Each settings page and Advanced is a list of groups of rows inside a
scroll view: a section header, then a rounded group with a hairline
between rows, the label and caption on the left and system controls on
the right (`NSSwitch` at the small size System Settings uses, pop-ups,
segmented controls, regular push buttons). Home and first-run setup are
laid out to fit the window instead of scrolling, and their rows are the
same group: Home's Dictation rows and each setup step's rows are drawn by
the code that draws a settings group, so the fill, the edge, the
separators and the type are one look everywhere. A row button tells
VoiceOver what it does to what ("Edit snippets", "Change capture key");
the visible title stays short.

- **No undo or re-insert in the window** (November 2026): the window has
  no Undo Last Dictation button, and the recent-dictations dialog is
  reveal-only. Undo and Insert Again live in the [[menu-bar]] and on the
  undo key, where the destination app is still frontmost.

## First run

First-run setup takes the whole window: the sidebar folds away and the
title strip goes, and it comes back exactly as it was when setup ends.
Four steps plus a completion — "First, let me hear you", "Now try your
key", "Getting your models ready", "Say something", "Nice. You’re
ready." — each shown as a stage with the face (150 pt) and a bubble that
says what the step is for, then a group of rows the step is waiting on.
The permissions step has one row per permission (Microphone,
Accessibility, Input Monitoring) with its state and its own **Open System
Settings** button to that permission's pane; the first missing one is the
amber button and answers Return, a granted row shows a check mark, and
**Keep Going** stays disabled until nothing is missing. At the foot: a
progress bar, the four steps with their states, the step's one button,
and "Everything you say stays on this Mac." Hotkey practice completes
only after capture is actually observed.

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

- Resizable, opens at 1000×680, minimum 880×648 (1000×640 / 880×600
  before November 2026); closing it keeps it alive.
- The runtime is an accessory app, so its main menu is never drawn, but
  it is installed for its keys: Edit (undo, redo, cut, copy, paste,
  select all), View (⌘1 to ⌘5 for the five pages in sidebar order, plus a
  hidden ⌘D for Advanced that works while setup has the sidebar put
  away) and Window (⌘W, ⌘M).
- Keyboard contract: `return:continue-setup`, `command-d:advanced`,
  `command-r:verification`, and `command-1:home` through
  `command-5:advanced`. ⌘D also sits on the Advanced row itself. The
  sidebar rows behave as one list: Tab stops once, on the selected row
  (where the system's keyboard-navigation setting lets Tab reach buttons
  at all), and Up and Down move through the rows and show each page.
  VoiceOver finds the sidebar as one list, "Whisper Face pages".
- **Editor sheets**: the five Personalize editors are sheets built from
  the pages' own parts. The header is the chosen face on a 40 pt chip
  (the sidebar's chip, still), the title and one line under it; the body
  is the pages' grouped rows on the window's field colour and scrolls
  when the sheet reaches its cap (552 pt, which keeps it inside the
  window at its minimum); the footer is system buttons, Cancel then Save
  for a form and one Done for a list that applies as it goes, with any
  action on the whole list ("Add Snippet…", "Copy Export", "Forget All…")
  at its leading end. *App tones* lists every app with its own icon and
  its tone as a pop-up, and Save applies each tone that changed; a tone
  that is refused stops the save there, and the sheet names the app and
  the apps already saved, which Cancel does not take back.
  *Snippets* is a list with Edit and Delete on every row; adding or
  editing turns the same sheet into a form of two fields, Name and Text,
  and a refused save keeps the form up with what was typed, the reason
  under the field it is about and the keyboard in that field. The form
  teaches the phrase the runtime answers, "insert my" and the name, and
  a test holds its example to `SNIPPET_RE` and the inline cue.
  *Vocabulary* is its two lists, each a field in its own group. A field
  in a sheet has no border or fill of its own: it fills its group, which
  is its frame, under the section header that names it. *Learned
  corrections* and *Pronunciation keywords* are rows that say where each
  item stands, each with Forget; with no keyword listed, Copy Export and
  Forget All… are off. Deleting and forgetting still ask first,
  in a system alert whose safe button is the default. Return presses the
  primary button, Escape cancels (or is Done), Tab runs top to bottom and
  leaves a multi-line field, and Command-Return saves from inside one. A
  list that is filled again (a row forgotten, a form closed) keeps the
  keyboard on the control now in its place, and a line that is cut to
  fit its row shows all of it as its tooltip.
  An editor is a straight line in code: `_new_sheet`, then `_sheet_fill`
  and `_run_sheet` as often as it needs, then `_end_sheet`; one is open
  at a time. Since macOS 11 AppKit shows a sheet as a card in the middle
  of its window and puts it back there when its frame changes, so an
  open sheet is resized about its middle. The dialogs
  that are still system alerts (key capture, voice modes, recent
  dictations, the Voice Inbox, the evidence inspector and the
  confirmations) all run through `_run_modal`, which gives each the
  chosen face on its chip as its icon in place of the generic folder.
- **The render probe**: `scripts/window_render_probe.py` builds the
  controller through the no-system-state smoke path, never orders the
  window front, and writes twelve views (Home; Personalize, Controls,
  Privacy, plus Controls at its longest copy and Privacy with Recent
  dictations on; Advanced; and the five first-run stages) in both
  appearances — 24 PNGs — into the gitignored `.probe-renders/`.
  `--size WIDTHxHEIGHT` lets the 880×648 minimum be reviewed directly,
  `--states` adds every state Home can be in and every variant of the
  first-run steps (78 PNGs in all), `--sheets` draws the Personalize
  editors instead (each one filled, the snippet form, a refused save, a
  list too long for the sheet, each empty state, and two under Increase
  Contrast, as `sheet-*.png`, drawn as a sheet is while it has the
  keyboard, plus `sheet-in-window-*.png` with the sheet in the middle of
  the page at the window's minimum size), and `--overlay` draws the
  recording pill
  instead ([[whisper-faces]]).
- **The window's own check**: `dictate.py --native-gui-smoke-test` builds
  the window headless and measures the look, the keyboard and what
  VoiceOver reads. An install runs only its first part
  (`--construction-only`: build and draw each surface once); the release
  gate runs all of it.
- **Idle cost**: while the window is open a 2-second timer reads the
  runtime again, and renders only when the state, the appearance or
  Increase Contrast changed.

> 📝 **History of the shell**: from the chibi-clay rebuild (#120) until
> November 2026 the window had no sidebar — a 64pt top bar carried the
> face chip, a `LOCAL FIRST` badge, Home/Settings pills and an Advanced
> tool button, the three settings panes sat behind a segmented control,
> and first run was a poster over Home with a 208pt tilted face chip and
> four step chips. The November 2026 new look brought a system sidebar
> back, made every page a list of grouped rows, and turned first run into
> the focused flow above.

## Related Concepts

- [[menu-bar]] — the surface that routes here
- [[design-language]] — the type, colour and motion this is built from
- [[whisper-faces]] — the character on the sidebar chip, the Home stage
  and the setup stage
- [[privacy-and-security]] — the support snapshot and bundle rules

## References

- whisper_face_gui.py (`SECTIONS`/`SETTINGS_PANES` :35-41,
  `SIDEBAR_ROWS` :65, key equivalents :1336, `model_checks_note`
  :2982, `face_grid` :3394, `result_evidence_text` :3580,
  `SettingsRowView` :6785, `GroupBoxView` :6922, `arrange_section`
  :7016, `EditorSheet` :8437, `_build_window` :9392,
  `_build_sidebar` :9629, `_build_home` :9774, `_build_setup`
  :9984, `_build_settings` :10345, `_build_advanced` :10670,
  `_install_main_menu` :11803, `_run_modal` :11866, `_new_sheet`
  :11952);
  whisper_face_render.py (`LiveFaceView`);
  scripts/window_render_probe.py; README "Native Mac window"
- [[2026-07-26-interface-rebuild-research]]
