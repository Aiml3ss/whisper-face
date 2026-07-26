---
title: "Consequence Receipts"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [risk, verification, relisten, safety, evidence]
aliases: [selective-relisten, microspan-verifier, consequence-routing]
summary: "Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text."
confidence: high
---

# Consequence Receipts

## Definition

Consequence receipts are transcript-free risk/uncertainty evidence for
the parts of a dictation that could hurt if misrecognized: URLs, paths,
contacts, recipients, currency, dates, times, numbers, names, and
imperative actions. A bounded selective re-listen can verify up to two
timed microspans with an isolated Whisper Tiny verifier. The receipts
route the *presentation* (completion sound, "— Review" menu label) and
never change recognition, cleanup, insertion, or model routing.

## Key Properties

- **Risk plan**: every category is high severity; uncertainty reasons
  are closed (hypothesis confidence < 0.82, missing word evidence, word
  confidence < 0.78, hypothesis disagreement).
- **Selector**: candidates need native word timings; spans are padded
  0.08 s, rejected over 2.4 s or ≥75% of the utterance
  ("span-not-micro"), merged when overlapping, payload-first
  prioritized, and capped at 2 (the rest count as selection-limit).
- **Verifier stack**: `process_verifier.py` (fresh child per request)
  and `prewarmed_verifier.py` (one long-lived child) enforce absolute
  monotonic deadlines, refuse late responses even if queued, and
  discard the child on any malformed message.
  `whisper_verifier_adapter.py` pins Whisper Tiny by revision and
  decides confirmed / contradicted / inconclusive from normalized
  comparison. Execution refuses verifiers that do not statically
  declare process-isolated + strict-deadline + no-audio-retention, and
  rejects results from the same engine as the primary ASR.
- **Routes**: standard (no risks), protected (risks but none
  uncertain-high), verified (all uncertain-high confirmed), review
  (any contradiction or unverified uncertain-high). Review plays a Ping
  instead of a Pop and titles the menu "— Review".
- **Fail-closed activation**: the verifier runs only when macOS + user
  opt-in + a valid re-listen [[activation-receipt]] (40 balanced real
  recordings, closed thresholds, manual review) all hold, and only if
  already prewarmed — no dictation ever pays model load.
- Known gap: the routing benchmark's corpus asserts
  standard/protected/review but not the verified route
  ([[benchmarks]]).
- The "— Review" title still exists, but on a row that only appears once
  a first result exists ([[menu-bar]]); the full risk and verification
  evidence now reads out of the [[app-window]] evidence inspector.
- Of the three voice corpora, re-listen is the one that is
  straightforwardly recordable today, because its benchmark drives the
  verifier directly rather than through a receipt-gated runtime path
  ([[evidence-capture]]). Recording is capped at the adapter's 2.4 s
  microspan bound — enforced in code and pinned by a test, though the
  re-listen runbook still does not state it (issue #108).

## Related Concepts

- [[voice-compiler]] — builds and executes the plan
- [[protected-anchor]] — the same shapes, protective rather than
  advisory
- [[activation-receipt]] — the unlock pattern
- [[acoustic-personalization]] — microspan retention for replay

## References

- voice_compiler.py `build_consequence_plan`,
  `execute_consequence_plan`; process_verifier.py;
  prewarmed_verifier.py; whisper_verifier_adapter.py;
  docs/selective-relisten-activation.md
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-trust-personalization-research]]
