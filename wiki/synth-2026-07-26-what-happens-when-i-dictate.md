---
title: "What Happens When I Dictate?"
type: synthesis
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [pipeline, narrative, overview]
summary: "From key-down to pasted text: the warm capture, rolling recognition, evidence compilation, guarded cleanup, and the single transactional paste."
query: "What happens, end to end, when I hold the key and speak?"
based_on: [dictation-pipeline, asr-cascade, voice-compiler, cleanup-pipeline, insertion-transaction, consequence-receipts, personalization]
confidence: high
---

# What Happens When I Dictate?

## Question

> What happens, end to end, when I hold Right Option and speak?

## Answer

The moment you press, Whisper Face grabs a pre-warmed microphone stream
and leases your current text field — identity, selection, and a
fingerprint of nearby text. The start cue plays only once capture is
truly ready. While you talk, a fast model speculates on natural pauses
and long speech is cut into chunks and recognized in the background;
the HUD's character lip-syncs to your level and shows only the
[[stable-prefix]] — nothing provisional ever reaches the target app.

On release, at most a 0.3-second tail is captured and one remainder
decode runs. The chunks assemble into a single recognition with real
word timings; hallucinations and decode loops are dropped. The
[[voice-compiler]] fuses hypotheses, ephemeral context, and your
personal priors into compiled text, keeping [[protected-anchor]]s
intact, and consequence-sensitive spans get transcript-free risk
receipts — optionally verified by an isolated microspan re-listen
([[consequence-receipts]]).

Cleanup is deterministic wherever possible; the local LLM wakes only
for semantic work, under a hard deadline and a circuit breaker, and its
edits count only if they provably reconstruct its own output
([[proof-edit]], [[cleanup-pipeline]]). Then comes exactly one paste
attempt: the lease is revalidated, drift fails closed, readback
verifies delivery, and anything unproven waits in the Voice Outbox
instead of landing in the wrong field ([[insertion-transaction]]).

Afterwards, if you correct the pasted text within ten seconds, that
exact-range correction becomes evidence — and a Personal Prior only
after passing your private regression suite ([[personalization]]).
Every stage logs content-free metrics to your local transcript record.

## Evidence

| Source Page | Key Point | Relevance |
|-------------|-----------|-----------|
| [[dictation-pipeline]] | The 15-stage ordered flow and invariants | high |
| [[asr-cascade]] | Speculation, rolling cuts, Parakeet routing | high |
| [[voice-compiler]] | Evidence fusion and decisions | high |
| [[cleanup-pipeline]] | Deterministic-first, guarded LLM | high |
| [[insertion-transaction]] | Lease, exactly-once, outbox | high |
| [[consequence-receipts]] | Risk receipts and re-listen | medium |
| [[personalization]] | The correction loop | medium |

## Gaps

- Windows follows the same shape without leases, word timings, or the
  native helper ([[windows-support]]).

## Confidence: high

Synthesized directly from the compiled concept pages, which trace to
code anchors at commit `b49699f`.
