---
title: "Stable Prefix"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [feedback, hud, glossary, core]
aliases: [semantic-commit]
summary: "The leading text supported by enough completed audio and cross-hypothesis agreement that later speech cannot invalidate it — the only text the HUD ever shows live."
confidence: high
---

# Stable Prefix

## Definition

The stable prefix is the leading text supported by enough completed
audio and cross-hypothesis agreement that later speech cannot
invalidate it. Publishing it (a "semantic commit", ADR-0002) is how
Whisper Face gives live feedback without ever typing provisional text
into another application.

## Key Properties

- Computed by `VoiceCompiler._stable_prefix` for non-finalized
  compiles: with one hypothesis it drops the last two tokens; with
  several it takes the longest normalized-equal token prefix across all
  hypotheses, then slices the *primary* text so original punctuation
  and casing survive. Finalized compiles set the stable prefix to the
  full formatted text.
- The HUD caption (`_caption_add` in dictate.py) is the **only** place
  rolling text appears, and it publishes `compiled.stable_prefix` —
  never raw chunk text.
- The architectural rule (docs/architecture-and-interop.md): stable
  prefixes are HUD feedback only; final text is compiled from the
  complete VoiceIR.
- The in-process [[inert-foundations|voice input protocol]] models the
  same lifecycle: stable prefixes must be prefix-monotone with
  non-decreasing stability timestamps.

## Examples

- Mid-sentence, the HUD shows "quick note — grab coffee" while the tail
  "…and catch the train" is still being decoded; the target app receives
  nothing until the single final paste.

## Related Concepts

- [[voice-compiler]] — computes it
- [[dictation-pipeline]] — where it surfaces
- [[whisper-faces]] — the HUD that displays it
- [[insertion-transaction]] — the single commit that follows

## References

- voice_compiler.py `_stable_prefix`; dictate.py `_caption_add`;
  docs/adr/0002
- [[2026-07-26-runtime-pipeline-research]]
