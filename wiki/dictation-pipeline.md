---
title: "Dictation Pipeline"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [pipeline, runtime, dictate-py, architecture]
aliases: [utterance-flow, finish-and-process]
summary: "The end-to-end path one utterance takes from key-down to pasted text, and the invariants that hold along it."
confidence: high
---

# Dictation Pipeline

## Definition

The dictation pipeline is the ordered sequence an utterance travels in
`dictate.py` from the Right Option key-down to a terminal insertion
receipt. `dictate.py` (10,683 lines at `1165335`, down from ~10.8k after
#101 removed the menu's second control panel) is a shell module owning
every side effect; nearly every algorithm is imported from pure modules
(`parrot_core.py`, `voice_compiler.py`, and friends).

## The flow

1. Key-down is enqueued from the macOS event tap; a worker allocates a
   `Recorder` and a pre-warmed audio slot, so the start cue means
   *capture-ready*, not *key-down*.
2. Frontmost app, input signature, and mode ([[voice-modes]]) resolve
   from press-time modifiers; ephemeral context is collected into a
   ContextPack; an insertion lease is captured
   ([[insertion-transaction]]).
3. While held: mic levels feed the HUD ([[whisper-faces]]); VAD-driven
   speculation and rolling chunk cuts decode long speech during the hold
   ([[asr-cascade]]); only the [[stable-prefix]] reaches the HUD caption.
4. Key-up: an opaque lease is sealed, a release-order ticket is issued,
   at most a 0.3 s tail is captured, and exactly one remainder decode
   runs. An energy gate drops too-short or too-quiet audio.
5. Chunks assemble into one Recognition with offset-corrected word
   timings; hallucinations, decode loops, and prompt echoes are dropped.
6. The [[voice-compiler]] builds VoiceIR and compiles;
   [[consequence-receipts]] and the [[context-firewall]] produce
   shadow evidence.
7. Early intercepts run in order: risky-action confirmation
   ([[inert-foundations]]), command mode, [[voice-objects]], spoken edit
   commands, whole-utterance snippets.
8. The [[cleanup-pipeline]] runs: deterministic plan always; the local
   LLM only when needed, guarded, circuit-broken, and accepted only via
   validated [[proof-edit]]s.
9. The [[insertion-transaction]] makes one paste attempt with readback;
   unverified results land in the outbox.
10. Afterwards: optional microspan retention
    ([[acoustic-personalization]]), then either [[delayed-cleanup]] is
    scheduled or correction learning starts ([[personalization]]);
    metrics append to `transcripts.jsonl` (0600). In practice the
    delayed-cleanup branch is never taken on any machine today: it
    requires a receipt whose evidence is currently unproducible.

## Invariants

- At-most-once paste; duplicates are structurally impossible.
- Nothing provisional is ever typed into another application.
- Capture/code cleanup applies only proof-validated bounded edits.
- [[protected-anchor]]s never invent words.
- [[consequence-receipts]] are evidence-only.
- Release order is preserved even though ASR overlaps.
- Correction learning is strictly downstream of a verified receipt.
- Private state is written atomically at 0600; a flock enforces a
  single instance.
- No window or menu chrome runs on this path: the crossfades, hovers and
  springs added in the 2026-07-26 rebuild are explicitly scoped to
  chrome, and every one of them is Reduce-Motion gated
  ([[design-language]]).

## Key tunables (top of dictate.py)

MIN_SECONDS 0.4 · TAIL_SECONDS 0.30 · GATE_PEAK_RMS 0.002 ·
LOW_CONFIDENCE 0.52 · FAST_ACCEPT_CONFIDENCE 0.70 ·
PARAKEET_ROUTE_CONFIDENCE 0.84 · CHUNK_MIN_SECONDS 4.0 ·
CHUNK_CUT_SILENCE 0.6 · LLM_CLEANUP_TIMEOUT (1, 4) s ·
CORRECTION_DELAY 10 s · VOICE_OUTBOX_MAX_ITEMS 20 · PHONE_PORT 8787.

## Related Concepts

- [[asr-cascade]] — stages 3-5 in detail
- [[voice-compiler]] — stage 6
- [[cleanup-pipeline]] — stage 8
- [[insertion-transaction]] — stage 9
- [[synth-2026-07-26-what-happens-when-i-dictate]] — narrative version

## References

- dictate.py `finish_and_process` (:9507-10195), `hotkey_worker`
- [[2026-07-26-runtime-pipeline-research]]
