---
title: "Inert Foundations"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [foundations, inert, safety, protocol, macos]
aliases: [drop-to-target, demonstration-drafts, risky-action-confirmation, voice-input-protocol, networkless-worker]
summary: "The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution."
confidence: high
---

# Inert Foundations

## Definition

A family of modules builds the vocabulary for future voice actions
while being structurally incapable of surprise execution. Each separates
a pure decision from a narrow single-verb boundary, and most have no
execution verb at all.

## The members

- **Drop-to-Target** (`drop_to_target.py` +
  `macos_drop_to_target_snapshot.py`): a pure, synthetic-decision-only
  resolver over caller-declared capability policies (macOS AX cannot
  prove drop semantics, so the module never guesses them). It refuses —
  rather than redirects — when the best name-match cannot accept the
  drop. There is **no drop transaction module**: no nonce, lease, or
  execute path; nothing in the repo can initiate a drag. The only
  runtime consumer is a read-only Diagnostics preview stamped
  `execution: none`.
- **Demonstration drafts** (`demonstration_drafts.py`): inert Finder /
  Mail / Notes / menu recipes with described steps (not recorded
  events). Recording, approval, cancel-rollback, and explicit deletion
  are the whole lifecycle; approval "never interprets, replays,
  executes, or exports a step". Reprs redact step text; listings return
  only counts and states.
- **Risky-action ceremony** (`risky_action_confirmation.py`): a
  two-factor gate — pick one of four closed risk classes, speak exactly
  "confirm risky action", then click within a 30-second monotonic
  window. A click before voice does not advance; the phrase is consumed
  before compilation, logging, clipboard, or insertion; even a
  confirmed terminal has no payload or callback attached.
- **Voice input protocol trio** (`voice_input_protocol*.py`): a strict
  versioned in-process contract (capture_proposal → stable_prefix* →
  final_text → commit_receipt → ack_receipt | cancellation) with a
  canonical-bytes codec (1 MiB cap, decode must re-encode identically)
  and a bounded same-UID Unix-socket transport that is documented as
  not wired to dictation. Receipt pairs derive from
  [[insertion-transaction]] so only real combinations validate.
- **Networkless worker** (`macos_networkless_worker*.py`): an opt-in
  one-shot child under `sandbox-exec` that *proves* loopback bind and
  outbound connect are OS-denied before serving, accepts only a
  non-transcript capture proposal, and answers with a content-free
  cancellation. Not imported by the runtime.
- **Compatibility fingerprint** (`compatibility_fingerprint.py`):
  text-free bucket aggregation of insertion capability/outcome pairs;
  export requires explicit opt-in and a minimum count; deliberately has
  no transport and no runtime wiring.

## The shared shape

Pure decision, then an explicit human gesture, then (at most) one verb
reached through a single-use nonce popped under a lock and re-validated
against frozen evidence. Everything else fails closed to content-free
receipts. Corpora stamp themselves `physical_validation: false`.
Sending, scheduling, dragging, replaying, or agent execution could be
built on this substrate later — none can happen today by accident,
drift, replay, or a plausible-sounding phrase.

## Related Concepts

- [[voice-objects]], [[point-and-speak]] — the members with a single
  live verb
- [[privacy-and-security]] — the receipt discipline
- [[benchmarks]] — the synthetic corpora that exercise these

## References

- [[2026-07-26-voice-actions-research]]
