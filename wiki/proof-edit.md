---
title: "Proof Edit"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [safety, cleanup, glossary, core]
aliases: [edit-proof, proof-edits]
summary: "A bounded transformation with an exact source span, before/after text, kind, and validation outcome — capture-mode cleanup applies only validated proof edits."
confidence: high
---

# Proof Edit

## Definition

A proof edit is a bounded transformation with an exact source span,
before and after text, a kind, and a validation outcome. In capture and
code modes, LLM cleanup is accepted **only** as a set of proof edits
that reconstruct the model's own output exactly; otherwise the entire
LLM result is discarded and deterministic cleanup pastes instead.

## Key Properties

- Validated by `VoiceCompiler.verify_edits`: each edit's span must
  locate exactly in the current text, and only a closed set of
  transformations is provable — identical lexical content
  (punctuation/case/whitespace changes), removal of provable filler
  words, an explicit "X, actually Y" self-correction, dropped
  new-line/new-paragraph markers, and proven list/enumeration variants.
- Fixed rejection reasons include: protected anchor removed, edit span
  not bounded (>240 chars), excessive expansion, whole-message rewrite,
  unproved lexical transformation.
- The same proof machinery gates [[delayed-cleanup]] proposals, and a
  standalone bounded proof-recovery mediator exists for benchmarks (no
  runtime authority; see [[benchmarks]]).
- Proof edits are counted (accepted/rejected) in every transcript
  metrics record — explainability is part of the contract.

## Examples

- "um so basically the fix works" → filler removals are individually
  provable edits.
- A model that rewrites a sentence wholesale fails reconstruction; the
  user gets deterministic cleanup rather than a plausible paraphrase.

## Related Concepts

- [[voice-compiler]] — the validator
- [[cleanup-pipeline]] — the producer of edit proposals
- [[protected-anchor]] — the hard boundary inside edits
- [[delayed-cleanup]] — proofs applied after insertion

## References

- voice_compiler.py `verify_edits`, `_validate_edit`;
  cleanup_proof_recovery.py; CONTEXT.md
- [[2026-07-26-runtime-pipeline-research]]
