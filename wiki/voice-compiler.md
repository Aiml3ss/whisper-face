---
title: "Voice Compiler"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [compiler, voiceir, span-graph, evidence, core]
aliases: [VoiceIR, SpanGraph, span-graph]
summary: "The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning."
confidence: high
---

# Voice Compiler

## Definition

The Voice Compiler (`voice_compiler.py`, ~1.7k lines) consumes VoiceIR —
the intermediate representation of one utterance: recognition
hypotheses, context candidates, personal priors, prosody events, and
mode — and produces a compiled transcript, [[proof-edit]]s,
[[stable-prefix]], [[protected-anchor]]s, and explainable decision
metrics. It never touches I/O, the clipboard, or an application
(ADR-0001: compile evidence instead of rewriting transcripts).

## Key Properties

- **VoiceIR** is a frozen dataclass: hypotheses, ContextPack, personal
  priors, prosody, app bundle, mode, finalized. The runtime promotes ASR
  retry disagreements and learned alternatives into hypotheses at a
  small confidence discount.
- **SpanGraph is a concept, not a class.** The documented span-graph
  fusion is implemented by `VoiceCompiler._fuse`: per-token candidates
  scored by primary confidence plus engine bonuses, cross-hypothesis
  agreement (SequenceMatcher alignment, +0.07), context candidates
  (single-token, distinctive, phonetically similar, and only below 0.70
  primary confidence), and personal priors. The only in-code trace of
  the name is `Decision(source="span-graph", ...)`.
- **Anchors raise the bar.** A replacement needs a score delta of 0.12
  normally, 0.18 for anchors backed by exact context or a prior, 0.24
  otherwise, with a hard 0.30 floor for unbacked anchors.
- **Prosody formatting** inserts paragraph breaks for ≥0.9 s pauses and
  commas for ≥0.45 s, and flips a trailing period to a question mark on
  a rising contour — but only when timed native words align one-to-one
  with output tokens; interpolated timing never formats.
- **Edit verification** (`verify_edits`) replays each proposed edit
  against the text and accepts only a closed set of provable
  transformations ([[proof-edit]]).
- The compiler also computes [[consequence-receipts]] plans and the
  [[context-firewall]] shadow comparison.

## Examples

- "Tuesday — actually Wednesday" → the self-correction is a provable
  edit; just "Wednesday" survives.
- A context term from the focused document can replace a
  similar-sounding token only when it clears the anchor-aware
  threshold; every swap emits an inspectable Decision.

## Related Concepts

- [[protected-anchor]], [[proof-edit]], [[stable-prefix]] — the three
  contract surfaces
- [[dictation-pipeline]] — the caller
- [[personalization]] — where priors come from
- [[cleanup-pipeline]] — consumes the compiler's proof validation

## References

- voice_compiler.py (`VoiceIR`, `VoiceCompiler.compile`, `_fuse`,
  `verify_edits`); CONTEXT.md glossary; docs/adr/0001
- [[2026-07-26-runtime-pipeline-research]]
