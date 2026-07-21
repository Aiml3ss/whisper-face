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

**VoiceIR** — the intermediate representation of an Utterance: Recognition
Hypotheses, Context Candidates, Prosody Events, Correction Signals, and mode.
VoiceIR is the input to the Voice Compiler.

**SpanGraph** — aligned competing spans from Recognition Hypotheses. Its best
path is selected from acoustic confidence, engine agreement, phonetic/context
fit, and Personal Prior without freely inventing words.

**Context Candidate** — an ephemeral term or phrase retrieved from the focused
application, document, repository, selection, clipboard, or user dictionary.

**Context Pack** — the ranked Context Candidates and formatting constraints
provided by a Context Adapter for one Utterance.

**Context Adapter** — an adapter that converts one context source into a
Context Pack. Context is ephemeral unless the user explicitly promotes it.

**Correction Signal** — evidence that the user changed previously pasted text,
scoped by application and surrounding language.

**Personal Prior** — a local preference over competing spans learned from
Correction Signals. A Personal Prior is contextual, inspectable, and
forgettable; it is not an unconditional global replacement.

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
Only verified receipts may train Personal Priors.

**Voice Outbox** — a bounded, RAM-only recovery queue for text that was not
proven delivered. It distinguishes text that was never pasted from text whose
paste may have landed, and requires explicit copy-and-dismiss recovery.

**Personal Regression Lab** — the bounded local suite of exact corrected spans
used to test a candidate Personal Prior before activation and after reload.
Conflicting or stale candidates are quarantined rather than applied.

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
