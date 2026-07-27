# What Whisper Face actually does today

An inventory of shipped capability, written to be quotable without
overclaiming. Whisper Face contains three very different kinds of thing, and
blurring them is the easiest way to say something untrue:

- **Shipped** — a user can rely on it right now.
- **Built, gated** — the code is complete and tested, but it stays off until
  local physical evidence and an explicit manual review unlock it. Off is the
  default and the honest state.
- **Foundation** — real, tested code with no user-facing surface. It exists so
  a future capability can be built safely, and it deliberately cannot act.

Nothing below is a comparative claim. No head-to-head measurement against
another product exists; see
[the neutral competitor evaluation](benchmarks/competitor-evaluation.md).

Last reviewed: **26 July 2026**.

---

## Shipped

### Dictation

- **Hold-to-talk anywhere.** Hold Right Option, speak, release; cleaned text
  arrives at the cursor in whatever app has focus. The microphone is
  pre-warmed at login, so the start cue means *capture is ready*, not *key
  went down*.
- **Rolling recognition.** Long dictation is transcribed while you are still
  talking, cut at natural pauses, so the decode at key release covers only
  the remainder rather than the whole recording.
- **Three-engine cascade on Mac.** Whisper Tiny speculates during end pauses,
  a warm native Parakeet Unified helper is the primary recognizer, and Whisper
  large-v3-turbo is the independent fallback. Any helper failure falls back
  faithfully and restarts lazily.
- **Whispering works.** Quiet speech is gain-normalized before recognition.
- **Flight Recorder.** A default-off, twenty-second, RAM-only rolling buffer.
  Speak first, then tap the key to keep what you just said. Audio is never
  written to disk, and the buffer clears on use, pause, disable, and quit.
- **Six voice modes.** Modifier keys turn the same gesture into faithful
  capture, polished composition, context-aware reply, selected-text editing,
  spoken-code compilation, or a small allowlist of reversible editing
  commands. Command mode cannot run shell commands; edit mode requires a
  selection.
- **Per-app tone.** Casual in chat, formal in mail, technical in editors,
  verbatim in terminals — configurable per app, or forced for one dictation by
  saying "formal tone, …".
- **Spoken structure.** "New line", "new paragraph", "scratch that", and
  explicit list lead-ins become real formatting.
- **Self-editing snippets.** Say "insert my email" and saved text arrives.
  Correct that exact insertion within ten seconds and the new value is saved
  for next time.

### Trust and safety

- **Cleanup cannot invent meaning.** Names, numbers, dates, URLs, paths,
  identifiers, and commands are protected anchors. The optional local language
  model may only make edits it can prove, each with an exact source span; if
  the declared edits do not reconstruct the model's own output, the whole
  result is discarded and deterministic cleanup is used instead.
- **Insertion is a transaction.** The destination is leased at key-down and
  revalidated before exactly one paste attempt. Drift in focus, selection, or
  surrounding text fails closed.
- **Voice Outbox.** Text that was not proven delivered lands in a bounded,
  RAM-only recovery queue instead of the wrong window, recoverable only by an
  explicit Copy & Dismiss.
- **Readback proof.** What actually landed is read back and compared. When a
  destination differs only by edge whitespace, delivery is still proven, under
  its own receipt reason; anything weaker stays a conflict.
- **Consequence receipts.** Names, numbers, currency, dates, recipients,
  contacts, URLs, paths, and commands get transcript-free risk receipts, and a
  review-worthy result says so.
- **Counterfactual Context Firewall.** Every finalized compile is compared
  against a context-free shadow compile; contextual influence on protected
  content is quarantined in an aggregate receipt. It observes only — it can
  never change text, cleanup, insertion, or routing.
- **Reversible personalization.** Corrections are learned only from the exact
  pasted range, must pass a private regression suite with zero regressions,
  are scoped by app, and are individually inspectable and forgettable.
  Contradicting evidence demotes a learned rule.
- **Result inspector.** Compiler decisions, protected anchors, accepted and
  rejected proof edits, alternatives considered, and timing — without storing
  a transcript dossier.

### The application

- **One-click installers** for Mac and Windows that provision dependencies,
  pinned models, the native helper, login services, and health checks, and
  verify their own work. Reruns are idempotent and never overwrite personal
  files.
- **Native Mac window** with three sections — Home, Settings, Advanced —
  covering status, personalization, privacy, models, and diagnostics.
- **Menu-bar presence** with fourteen animated companion characters whose mouths
  move with your speech; recovery rows appear only when they have something.
- **Guided first run** through permissions, hotkey practice, model readiness,
  and a first real dictation, each confirmed by evidence rather than assumed.
- **Accessibility.** VoiceOver labels, full keyboard navigation, contrast in
  light and dark, and Reduce Motion that disables animation entirely.
- **Local support bundle** — a transcript-free health export you choose to
  save; never uploaded.
- **Explicit updates.** A menu check that fetches, applies, and rolls back on
  failure; no background polling, no silent updates.

### Platform notes

- macOS is the production focus. Windows shares capture, the cascade,
  cleanup, snippets, tones, and learning, with a tray icon in place of the HUD
  and no Accessibility-based insertion transaction.
- Distribution is currently an **unsigned preview**: macOS warns that the
  developer cannot be verified, and permissions may need re-granting after an
  update. Signing and notarization are not done.

---

## Built, gated behind local evidence

These ship **off**. Each unlocks only from a private receipt this machine
produced from its own physical evidence, after an explicit manual review.
Synthetic evidence can never activate any of them, and a missing or malformed
receipt is indistinguishable from no receipt at all.

- **Selective re-listen** — re-verifying uncertain, consequence-bearing spans
  with an independent, process-isolated recognizer under a hard deadline.
- **Acoustic calibration** — bounded gain, noise-gate, voice-activity, and
  end-of-speech settings derived from local capture telemetry.
- **Acoustic keyword priority** — giving one hard-to-hear name bounded
  priority in the recognizer's prompt.
- **Delayed cleanup** — inserting the deterministic result immediately, then
  merging proven cleanup edits into untouched spans afterwards.
- **Acoustic Time Machine** — opt-in, RAM-only replay of the short spans
  behind a consequential word, self-deleting after one minute.

A guided capture harness exists for each corpus, so recording the evidence is
a session rather than a research project. The harnesses record what happened
and hand it to the existing gates; they structurally cannot approve anything.

---

## Foundations without a user surface

Real, tested, and deliberately unable to act. They exist so that sending,
scheduling, or automating could later be built safely — none of it can happen
today by accident, drift, or a plausible-sounding phrase.

- **Voice Objects and the Voice Inbox** — spoken commands can queue inert
  local task, email, and calendar drafts. Revealing one is explicit; a second
  confirmation can copy it or request a Mail compose window. Nothing sends or
  schedules.
- **Point-and-Speak** — resolving a named on-screen control and, after a
  separate confirmation, performing exactly one press on a strongly named
  button, checkbox, radio button, tab, menu item, or link. Text fields are
  excluded by construction.
- **Drop-to-Target** — deciding whether a named target could accept a drag.
  There is no drag path in the codebase at all.
- **Demonstration drafts** — authoring inert Finder, Mail, Notes, or menu
  recipes. Approval marks them approved; there is no replay.
- **Risky-action confirmation** — a two-factor voice-then-click ceremony whose
  confirmed state has no payload and no callback.
- **Voice Input Protocol** — a strict versioned in-process contract with a
  canonical codec and a bounded local socket transport, not wired to
  dictation.
- **Model wallet** — provider-neutral routing policy and read-only readiness
  evidence, with live routing intentionally not connected.
- **Networkless worker** — a sandboxed child that proves the OS denies it
  network access before it will serve.

---

## Measurement posture

- Internal corpora are **synthetic**. They catch regressions; they are not
  evidence about real-world accuracy, and are never presented as such.
- No cross-product comparison has been run.
- Physical evidence still outstanding: a 50-application insertion matrix,
  hardware and Windows verification, lifecycle and stress runs, a fresh-Mac
  onboarding walkthrough, and competitor task runs.
- The four-nines insertion reliability claim is explicitly **not** made; it
  would require repeated trials per surface class, which is a soak harness
  rather than a sitting.
