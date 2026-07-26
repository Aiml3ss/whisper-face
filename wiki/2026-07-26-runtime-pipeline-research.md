---
title: "Runtime Pipeline Research Notes"
type: article
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [research, runtime, pipeline, dictate-py]
summary: "Imported research notes mapping dictate.py, the pure core modules, the verifier stack, delayed cleanup, and the end-to-end utterance flow at commit b49699f."
source_hash: "2fd5bd58eb40e913f9bafb55022e7ef6f6ca602288cbdf1eff01da485b7becf4"
status: published
---

# Runtime Pipeline Research Notes

## Summary

A deep map of the dictation runtime at commit `b49699f`: the anatomy of
`dictate.py` (a ~10.8k-line shell module owning every side effect), the
pure modules it imports (`parrot_core.py`, `voice_compiler.py`), the
process-isolated verifier stack, the delayed-cleanup machinery, the
Windows path, and a 32-step ordered utterance flow with thirteen
invariants.

## Content

The full brief lives at `.raw/runtime-pipeline-research.md`. The
knowledge it contains has been compiled into:
[[dictation-pipeline]], [[asr-cascade]], [[voice-compiler]],
[[protected-anchor]], [[proof-edit]], [[stable-prefix]],
[[cleanup-pipeline]], [[delayed-cleanup]], [[insertion-transaction]],
[[voice-modes]], [[consequence-receipts]], [[whisper-faces]],
[[windows-support]].

## Key Takeaways

- `dictate.py` is deliberately a shell; the algorithms live in pure,
  testable modules.
- At-most-once paste, stable-prefix-only feedback, and proof-edit-only
  cleanup are structural, not aspirational.
- SpanGraph is a documented concept implemented as
  `VoiceCompiler._fuse` — there is no class by that name.
- The networkless worker exists but is not imported by the runtime.

## Related

- [[whisper-face]] — the hub
- [[synth-2026-07-26-what-happens-when-i-dictate]] — the narrative
