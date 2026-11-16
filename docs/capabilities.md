# What Whisper Face actually does today

An inventory of shipped capability, written to be quotable without
overclaiming. Whisper Face contains three very different kinds of thing, and
blurring them is the easiest way to say something untrue:

- **Shipped** — a user can rely on it right now. Where something needs an
  optional model that a default install does not download, it says so.
- **Built, gated** — the code is complete and tested, but it stays off until
  local physical evidence and an explicit manual review unlock it. Off is the
  default and the honest state.
- **Foundation** — real, tested code with no user-facing surface. It exists so
  a future capability can be built safely, and it deliberately cannot act.

Nothing below is a comparative claim. No head-to-head measurement against
another product exists; see
[the neutral competitor evaluation](benchmarks/competitor-evaluation.md).

Last reviewed: **14 November 2026**, against the source that includes the
November 2026 audit fixes. v0.5.0 is the first published build with them;
v0.4.0 and earlier behave differently in places (they install Ollama by
default, for example).

**What a default install contains.** On a Mac: Parakeet Unified (English) and
Whisper Tiny. Whisper large-v3-turbo and the Qwen3.5-4B cleanup model (with
the Ollama service it runs in) are opt-in, through `./setup.sh --models` or
`--with-all-models`. Windows installs Whisper Tiny, large-v3-turbo, and
Qwen3.5-4B, and has no Parakeet.

---

## Shipped

### Dictation

- **Hold-to-talk anywhere.** Hold Right Option (Right Alt on Windows), speak,
  release; cleaned text arrives at the cursor in whatever app has focus. The
  microphone is pre-warmed at login, so the start cue means *capture is
  ready*, not *key went down*. A take ends at ten minutes; a lost key-up is
  caught by polling the key's physical state; a microphone that delivers
  nothing for two seconds ends the take, and one that stops after speech
  keeps what it heard.
- **Rebindable keys and sounds (Mac).** The capture key, an optional undo key,
  and the feedback sounds are chosen under Settings → Controls, which also
  holds the “Actually” corrections switch. A capture key equal to the undo
  key is refused.
- **Eleven dictation languages.** English, Spanish, French, German, Italian,
  Portuguese, Dutch, Russian, Japanese, Korean, and Chinese. Parakeet is
  English-only, so the other ten are recognized by Whisper alone — Tiny on a
  default install, large-v3-turbo once installed — and cleaned
  deterministically. Compose, Reply, and Edit refuse outside English.
- **Rolling recognition.** Long dictation is transcribed while you are still
  talking, cut at natural pauses, so the decode at key release covers only
  the remainder rather than the whole recording.
- **Cascade on Mac.** For English, a warm native Parakeet Unified helper is
  the primary recognizer and Whisper Tiny decodes the same audio only to
  measure agreement; Tiny's text is never delivered on that route. When the
  two disagree and large-v3-turbo is installed, Turbo casts a two-of-three
  vote on the words. Without Turbo (the default), Parakeet's text is
  delivered unverified. A helper failure falls back to Whisper; a helper that
  had started restarts lazily, and one that cannot start is retried only
  after a logged backoff of 30 seconds doubling to five minutes.
- **Whispering works.** Quiet speech is gain-normalized before recognition.
- **Flight Recorder.** A default-off, twenty-second, RAM-only rolling buffer.
  Speak first, then tap the key to keep what you just said. While it is on,
  the microphone stays open. Its audio is never written to disk, and the
  buffer clears on use, pause, disable, and quit.
- **Six voice modes.** Modifier keys turn the same gesture into faithful
  capture, polished composition, context-aware reply, selected-text editing,
  spoken-code compilation, or a small allowlist of reversible editing
  commands. Command mode cannot run shell commands; edit mode requires a
  selection. *Compose, Reply, and Edit need the optional cleanup model and
  English*. Without the model (the default install) none of them inserts
  anything: the dictation is an instruction or a brief, not text to paste, so
  the field and any selection stay as they were and the caption reads
  "<Mode> mode needs the cleanup model — nothing changed". With the model
  installed, a rewrite that fails or is rejected also inserts nothing
  ("<Mode> mode got no rewrite from the cleanup model — nothing changed").
- **Per-app tone.** Casual in chat, formal in mail, technical in editors,
  verbatim in terminals — configurable per app, or forced for one dictation by
  opening with the tone and "tone", "style", or "mode": "formal tone, …".
- **Spoken structure.** "New line", "new paragraph", and explicit list
  lead-ins become real formatting. The phrase stays as words in a noun use
  ("a new line of products") and right after a count or quantity: a number,
  "dozen", "couple", "all", "most", "several", "of" ("We hired two new line
  cooks.", "12 new line staff"). So a command said without a pause right
  after a number ("Step 1 new line step 2") is typed as words; with a pause,
  which the recognizer writes as a comma or a full stop, it breaks the line.
  "Scratch that" as its own sentence drops
  the previous sentence; inline (or after a "..." pause) it drops the current
  clause.
- **Typed self-correction (off by default).** Out of the box "We meet
  Tuesday, actually Wednesday." is typed as spoken; "scratch that" is the way
  to take words back. The preference `self_correction` turns the rule on: the
  “Actually” corrections switch under Settings → Controls on Mac, or
  `"self_correction": true` in `preferences.json` on Windows, which has no
  settings window. When on, "Tuesday, actually Wednesday" becomes "Wednesday"
  only when both sides are the same kind of item (numbers, weekdays, months,
  clock times) and each is a whole item. "I didn't actually finish", a
  correction inside a range, score, or grouped number, and a correction after
  a negation ("not Tuesday, actually Wednesday") stay as spoken. What it gets
  wrong, and why it is opt-in: a sentence contrasting two things has the same
  shape, so "I thought it was Tuesday, actually Wednesday." becomes "I thought
  it was Wednesday." The same preference decides whether the cleanup model's
  proof may claim a self-correction.
- **Restart repair (on by default).** A phrase said twice in a row by
  accident is typed once: "I have nothing I have nothing going on" becomes
  "I have nothing going on". The rule (`parrot_core.find_restart`) is
  deterministic and English-only, and runs once, before the optional cleanup
  model sees the text; it does not run again on the model's text. Each
  repair is recorded as a `remove_repeat` cleanup edit.
  Three or more words said twice are repaired where the sentence goes on
  after them and the run holds a word that opens a phrase (a pronoun, an
  article, a preposition, an auxiliary, a conjunction). Two words only when
  they open like a restart and the sentence goes straight on: a subject
  pronoun or its contraction, an article, a preposition, a possessive, a
  clause opener ("so I so I told him", "there's a there's a problem"), or a
  question's verb and its subject ("can you can you hear me"). One word only
  from `i`, `the`, `a`, `an`, `to`, `of`, `and`, `we`, `my`, `i'm`, `it's`,
  `because`. A dash or an ellipsis at the restart point goes with the
  removed copy. A comma alone there counts only after a word no phrase ends
  on ("Go to the, to the store", "We can, we can do it"): "I'm sorry, I'm
  sorry I didn't mean it" is written that way on purpose, and so, with the
  same words, a restart such as "I have nothing, I have nothing going on" is
  kept too. Never repaired: anything said three or more times ("no way no
  way no way", "on and on and on we went"), anything across a sentence end
  or a line break, quoted text, a quotation after a verb of saying or
  singing ("the crowd chanted we will, we will rock you"), both copies set
  off by commas, runs that are half numbers, only letters or no word that
  opens a phrase ("X Y Z X Y Z", "red blue green red blue green"), a word
  being named ("type and and or", "the letter a, a vowel"), "oh my my",
  snippet expansions, spoken commands, a capitalised small word in
  mid-sentence ("Plan A a day"), "in in" ("log in in the morning"), a pair
  said twice at the end of a sentence ("I love you, I love you."), and "he
  said he said it"-style reports. Code mode (its alternatives included) and
  verbatim apps are never repaired. The preference `remove_repeats` turns it
  off: the Repeated words switch under Settings → Controls on Mac, or
  `"remove_repeats": false` in `preferences.json` on Windows.
- **Snippets.** Say "insert my email", alone or inside a sentence, and saved
  text arrives. A bare snippet name is never expanded, nor is a negated cue
  ("do not insert my email"), and an unfilled
  `EDIT ME` placeholder is never inserted. On Mac, editing a pasted snippet in
  place records only a pending suggestion; the saved snippet does not change,
  and the window does not show suggestions yet.

### Trust and safety

- **The optional cleanup model can only make edits it can prove.** Names,
  numbers, dates, URLs, paths, identifiers, and commands are protected
  anchors. In capture and code mode the optional local language model may only
  make edits it can prove, each covering whole tokens with an exact source
  span and leaving every anchor and number outside it intact; if the declared
  edits do not reconstruct the model's own output, the whole result is
  discarded and deterministic cleanup is used instead. Compose, Reply, and
  Edit are rewrites by design and are not held to the proof. The deterministic
  path applies only the written rules above (fillers, spoken structure,
  snippets on the explicit cue, and the typed self-correction only when it is
  turned on).
- **Bounded context.** App names, window titles, and dictionary terms bias
  the recognizer and protect words but never replace a recognized word. Other
  on-screen text (the selection, the open document, the repository, the
  focused field, the clipboard) can only *repair a name*: the recognizer
  must have been unsure of the take (confidence 0.70 or lower, scored for the
  whole utterance, not per word) and must itself have written the word as a
  name or identifier — capitalized in the middle
  of a sentence, or containing a digit, an underscore, or an inner capital —
  and the on-screen term must sound like it. So a visible "Qwen" repairs "Ask
  Gwen to review it." What it cannot do: replace or introduce a common word
  ("Looks good." stays, whatever is selected), touch a word the recognizer
  wrote in lower case or one capitalized only because it starts a sentence,
  or act when the recognizer was confident. From the focused field and the
  clipboard only identifier-shaped terms (`Qwen3_5`, `D55`, `camelCase`) are
  eligible. Each repair is recorded as an inspectable decision. Nothing is
  read from secure text fields or private clipboards.
- **Insertion is a transaction.** The destination is leased at key-down and
  revalidated before exactly one paste attempt. Drift in focus, selection, or
  surrounding text fails closed.
- **Voice Outbox.** Text that was not proven delivered lands in a bounded
  in-memory recovery queue instead of the wrong window, recoverable only by an
  explicit Copy & Dismiss. The same text is also written to the local
  transcript history. Undo payloads and blank text never enter it.
- **Readback proof.** What actually landed is read back and compared, within
  one adaptive window of up to 0.4 seconds for every app. When a destination
  differs only by edge whitespace, delivery is still proven, under its own
  receipt reason; anything weaker stays a conflict.
- **Undo (Mac).** The menu's Undo Last Dictation or the optional undo key
  takes back the last proven insertion, after checking the destination still
  holds it.
- **Consequence receipts.** Names, numbers, currency, dates, recipients,
  contacts, URLs, paths, and commands get transcript-free risk receipts, and a
  review-worthy result says so.
- **Counterfactual Context Firewall.** Every finalized compile is compared
  against a context-free shadow compile; contextual influence on protected
  content is quarantined in an aggregate receipt. It observes only — it can
  never change text, cleanup, insertion, or routing.
- **Reversible personalization (Mac).** Corrections are learned only from a
  settled edit of the exact pasted range, must pass a private regression suite
  with zero regressions, are scoped by app, and are individually inspectable
  and forgettable. No rule maps one common English word to another, adds or
  removes a negation, or maps one number to another, and an active rule is
  never applied inside an address, handle, or URL. Contradicting evidence,
  including dictating the heard word and leaving it alone, demotes a learned
  rule. Forget removes the rule's count, history, and evidence.
- **Result inspector.** Compiler decisions, protected anchors, accepted and
  rejected proof edits, alternatives considered, and timing, shown without
  transcript text. (The separate local `transcripts.jsonl` history does hold
  text: at most 500 entries and 30 days.)

### The application

- **Installers** for Mac and Windows that provision dependencies, pinned
  models, the native helper, login services, and health checks, and verify
  their own work. Reruns are idempotent and never overwrite personal files.
  A Mac install from a folder or clone needs an administrator account
  (Homebrew), the Command Line Tools with Swift 6, and macOS 14 or newer.
  From the next release, the Mac disk image is an app to drag into
  Applications that sets itself up in a window, with no Homebrew; it still
  asks Apple to install the Command Line Tools. That path has been checked
  only by its tests and a dry run, never on a clean Mac. The Windows
  installer has not been run on real Windows hardware.
- **Native Mac window** with a sidebar of five pages — Home, then
  Personalize, Controls, Privacy, and Advanced — covering status,
  personalization, privacy, models, and diagnostics. It has no undo or
  re-insert button; those live in the menu bar.
- **Menu-bar presence** with sixteen animated companion characters whose mouths
  move with your speech; recovery rows appear only when they have something.
- **Guided first run** through permissions, hotkey practice, model readiness,
  and a first real dictation, each confirmed by evidence rather than assumed.
- **Accessibility.** VoiceOver labels, keyboard navigation with the standard
  Edit, View, and Window shortcuts (Command-1 to Command-5 choose the page),
  contrast in light and dark with stronger edges under Increase Contrast,
  and Reduce Motion that disables animation entirely. No VoiceOver session has been recorded.
- **Local support bundle** — a transcript-free health export you choose to
  save; never uploaded.
- **Explicit updates (Mac).** **Check for Updates…** fetches the checkout's
  tracked branch, checks out its tip, reruns the installer, and rolls back to
  the last revision that installed cleanly on failure; no background polling,
  no silent updates. It does not check release tags or signatures, so it
  installs unreleased commits. A copy the drag-install app set up is
  refused: its updates come as a new download.

### Platform notes

- macOS is the production focus. Windows shares capture, Whisper
  recognition, cleanup, snippets, tones, and vocabulary mining, with a tray
  icon in place of the HUD, no Accessibility-based insertion transaction, and
  no correction learning. Right Alt is the Windows capture key; on AltGr
  layouts AltGr starts Capture.
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

The **Acoustic Time Machine** — RAM-only replay of the short spans behind a
consequential word, self-deleting after one minute — is not receipt-gated: it
is an ordinary opt-in Privacy preference on Mac, off by default.

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
