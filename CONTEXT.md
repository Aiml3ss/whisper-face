# Whisper Face domain context

Whisper Face is a local-first voice-input system. It turns an utterance
into text suitable for the focused application while preserving the speaker's
meaning, factual anchors, privacy, and control.

## Glossary

**Utterance** — one hotkey-delimited piece of captured speech.

**Recognition Hypothesis** — one engine's candidate transcription of all or
part of an Utterance, including confidence and optional word timing evidence.

**Word Evidence** — a word candidate with its engine, confidence, and audio
time range.

**Vocabulary Evidence** — what the speech runtime's term pass reports beside
its transcript: a place where one of the user's own terms could have been
emitted instead of the words heard, with the heard words, the summed logit
gap the term's path paid ("debt"), and each piece it took with its rank among
the model's candidates at that step. It is evidence, never text: the runtime
changes nothing, and the voice compiler's policy decides whether the term
would be written. **Favor my words**, a saved preference on by default,
writes the accepted ones (`apply_vocabulary_decisions`) before the voice
compiler and the clean-up, as `vocabulary_term` clean-up edits; off, they are
only counted.

**Dictionary Spelling** — the clean-up's deterministic rule that writes a
dictionary term in its own form where the words heard equal it ignoring only
spaces, hyphens and letter case ("git hub" → "GitHub"), recorded as a
`dictionary_spelling` clean-up edit.

**VoiceIR** — the intermediate representation of an Utterance: Recognition
Hypotheses, Context Candidates, Prosody Events, Correction Signals, and mode.
VoiceIR is the input to the Voice Compiler.

**SpanGraph** — aligned competing spans from Recognition Hypotheses. Its best
path is selected from acoustic confidence, engine agreement, phonetic/context
fit, and Personal Prior without freely inventing words.

**Context Candidate** — an ephemeral term or phrase retrieved from the focused
application, document, repository, selection, clipboard, or user dictionary.
Candidates from the app name, window title, and user dictionary are
anchor-only: they bias the recognizer prompt and are protected, but never
replace a recognized word. Nothing is taken from a secure text field or a
clipboard marked concealed or transient.

**Context Pack** — the ranked Context Candidates and formatting constraints
provided by a Context Adapter for one Utterance.

**Context Adapter** — an adapter that converts one context source into a
Context Pack. Context is ephemeral unless the user explicitly promotes it.

**Correction Signal** — evidence that the user changed previously pasted text,
scoped by application and surrounding language.

**Personal Prior** — a local preference over competing spans learned from
Correction Signals. A Personal Prior is contextual, inspectable, and
forgettable; it is not an unconditional global replacement. Only a prior the
Personal Regression Lab holds active is applied, as an exact whole-token
replacement in its app scope, and never inside an email address, handle, or
URL. No prior maps one common word to another, adds or removes a negation, or
maps one number to another.

**Protected Anchor** — factual or code-shaped content that cleanup must not
delete or change without explicit spoken instruction: names, numbers, dates,
URLs, paths, identifiers, acronyms, and commands.

**Proof Edit** — a bounded transformation with an exact source span, before and
after text, kind, and validation outcome. Capture-mode cleanup applies only
validated Proof Edits.

**Stable Prefix** — the leading text supported by enough completed audio and
cross-hypothesis agreement that later speech cannot invalidate it.

**Semantic Commit** — publishing a Stable Prefix to the HUD or, when explicitly
enabled and focus-safe, the target application.

**Insertion Lease** — a privacy-safe identity, selection, and bounded-context
fingerprint captured for the destination at hotkey press. Final text may make
one paste attempt only if the current destination still matches the lease.

**Insertion Receipt** — the terminal evidence for an insertion: verified,
unverifiable, conflicting, or unresolved, plus whether a paste was attempted.
A receipt is verified by reading the text back or, for a field that hides its
text but reports its selection range, **checked by position**: the cursor
moved by exactly the paste's length with the old selection replaced. A check
by position proves the right amount went in at the right place, not which
characters, so it is shown and kept like any checked paste but never undone,
rewritten later or learned from. Only receipts verified by reading the text
back may train Personal Priors.

**Voice Outbox** — a bounded, in-memory recovery queue for text that was not
proven delivered. It distinguishes text that was never pasted from text whose
paste may have landed, and requires explicit copy-and-dismiss recovery. A
paste that was sent into an app that hides its text, and so could not be read
back, is kept here too as an **Unchecked Paste**: counted, never presented as
a problem, and never treated as verified. Everything else in the queue still
needs a home and keeps the recovery treatment, including a paste into a field
that could be read and then answered nothing. It never holds undo payloads
or blank text. The same text is also written to the
local transcript history, so the queue is RAM-only but the words are not.

**Personal Regression Lab** — the bounded local suite of exact corrected spans
used to test a candidate Personal Prior before activation. On reload,
promotions are restored with a conflict check rather than by replaying the
gate. Conflicting or stale candidates are quarantined rather than applied, and
evidence against a candidate (a revert, the heard word left alone, a paste
sent unedited) counts too.

**Voice-Checked Update** — an update tried on the person's own accuracy
recordings before it replaces what they run (`voice_check.py`): the candidate's
speech engine and clean-up against the running version's, scored on word
mistakes, names written right and names written that were not said. Worse
beyond the stated tolerance holds the update until the person waits or
updates anyway. It is not the Personal Regression Lab, which tests learned
text mappings and knows nothing of audio.

**Prosody Event** — a pause, emphasis, pitch movement, or end-of-speech signal
derived from audio and aligned to Word Evidence.

**Zero-Edit Event** — pasted output still unchanged inside the exact observed
range after the correction-observation window. This is a proxy, not an
explicit statement of user acceptance.

**Correction Burden** — inserted, deleted, or replaced characters observed in
the exact pasted range during the correction-observation window.

**Voice Compiler** — the deep module that consumes VoiceIR and produces a
compiled transcript, Proof Edits, Stable Prefix, protected anchors, and
explainable decision metrics.
