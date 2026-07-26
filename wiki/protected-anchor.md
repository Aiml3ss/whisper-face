---
title: "Protected Anchor"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [safety, cleanup, glossary, core]
aliases: [anchors, factual-anchors]
summary: "Factual or code-shaped content — names, numbers, dates, URLs, paths, identifiers, commands — that cleanup must not delete or change without explicit spoken instruction."
confidence: high
---

# Protected Anchor

## Definition

A protected anchor is factual or code-shaped content that cleanup must
not delete or change without explicit spoken instruction: names,
numbers, dates, URLs, paths, identifiers, acronyms, and commands
(CONTEXT.md glossary). Anchors are the reason a polished dictation
cannot silently corrupt the parts that matter.

## Key Properties

- Extracted by `protected_anchors(text, context)` in the
  [[voice-compiler]]: regex-detected factual shapes, command words, and
  context candidates with high weight that already appear in the text.
- **Anchors can never invent words** — they only protect terms the
  recognizer already produced. Protection is subtractive safety, not
  generative bias.
- In span fusion, anchors raise the replacement threshold (up to a 0.30
  hard floor for anchors with no context/prior backing).
- In [[proof-edit]] validation, removing an anchor rejects the edit
  unless it is an explicit spoken self-correction.
- In the [[context-firewall]], a changed token that overlaps an anchor
  (or a replacement that *introduces* a new anchor shape) marks the
  contextual influence as protected and quarantines the receipt.
- In [[cleanup-pipeline]] proof recovery, anchors missing from a
  candidate reject it unless explicitly abandoned via "scratch that".

## Examples

- "email andrew at aiml3ss dot com about the 4pm" — the address and time
  survive any cleanup path.
- A context term can still *correct* an anchor ("Gwen" → "Qwen") when
  acoustic + context evidence clears the raised threshold, and the swap
  is recorded as an inspectable Decision.

## Related Concepts

- [[voice-compiler]] — extraction and threshold logic
- [[proof-edit]] — enforcement during cleanup
- [[consequence-receipts]] — the risk taxonomy over the same shapes
- [[context-firewall]] — anchor-aware influence quarantine

## References

- voice_compiler.py `protected_anchors`; CONTEXT.md
- [[2026-07-26-runtime-pipeline-research]]
