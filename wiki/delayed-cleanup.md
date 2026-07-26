---
title: "Delayed Cleanup"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [cleanup, insertion, merge, activation, macos]
aliases: [insert-now-clean-later, three-way-merge]
summary: "Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt."
confidence: high
---

# Delayed Cleanup

## Definition

Delayed cleanup lets Mac capture-mode dictation paste the deterministic
result instantly while Voice-Compiler-proofed LLM cleanup finishes in a
daemon thread, then applies only safe edits to the destination. It is
default-off and unlocks only through an [[activation-receipt]] backed by
a manually reviewed physical suite.

## Key Properties

- **Four gates**: (1) requested only when the LLM is needed, mode is
  capture, and a valid 0600 owner-only receipt exists; (2) scheduled
  only after a *verified* insertion receipt, with a generation counter
  invalidating stale threads and correction learning suppressed for
  that utterance; (3) the proposal must reproduce the LLM's own output
  through [[proof-edit]]s; (4) the merge and apply must pass every
  check below.
- **Pure three-way merge** (`delayed_cleanup_merge.py`): an edit is
  admitted only if the destination was not reordered, a unique boundary
  anchor exists, the user has not touched that span, and the anchor
  still resolves exactly once. Overlapping mapped edits refuse *all*
  colliding edits rather than picking a winner. User edits always win.
- **Transactional apply**: read snapshot → merge → re-read → identity,
  revision, and text must all match → one whole-value compare-and-swap.
  Proposal IDs are single-use. The destination adapter
  (`macos_delayed_cleanup_destination.py`) tokenizes identity/revision
  with per-adapter HMAC keys so raw text and AX identifiers never leave,
  and documents the residual race: macOS Accessibility has no native
  compare-and-swap primitive.
- **Activation bar**: ≥50 caller-attested physical cases across native,
  web, Electron, and terminal editors, balanced applied/rejected
  outcomes, zero wrong-target/user-overwrite/duplicate failures, p95
  final-apply ≤150 ms, manual review. The suite has not been run in the
  repository, so the feature ships off and no live safety claim is made.

## The activation bar is currently unearnable

> ⚠️ **Known blocker (issue #110, open on `main`)**: building the capture
> harness ([[evidence-capture]]) established that the 50-case corpus
> cannot be produced at all today. Three defects, all still present:
>
> 1. **Bootstrap deadlock.** `schedule_delayed_cleanup` returns `False`
>    unless `DELAYED_CLEANUP_STATE["active"]`, which is only true once a
>    valid receipt exists — and that receipt requires 50 manually
>    reviewed cases produced *by the feature running*. With no receipt
>    the runtime schedules nothing, prints no `[delayed-cleanup]` line,
>    and every case blocks.
> 2. **`apply_ms` has no source.** The gate wants per-case apply timing
>    at p95 ≤150 ms, but nothing measures it: the log line carries only
>    `{outcome}; N applied, M held`. A stopwatch is not evidence at a
>    150 ms threshold, so cases block on `no-runtime-timing` rather than
>    defaulting to a guess.
> 3. **`duplicate-callback` is physically unreachable.** The gate demands
>    ≥8 cases of it, but the runtime passes the per-utterance `event_id`
>    as the proposal id, so two passes never share one and the adapter's
>    in-flight and completed-duplicate paths cannot be reached by any
>    operator action. (Deterministic single-use-id tests do cover them.)
>
> This is the same circular-gate family as #108
> ([[activation-receipt]]). `docs/evidence/physical-sessions.md` records
> all three for the operator, noting that running the session anyway
> records which blocker fired — "the evidence that these are gate defects
> rather than operator error".

## Related Concepts

- [[cleanup-pipeline]] — the synchronous sibling
- [[insertion-transaction]] — provides the verified receipt gate
- [[activation-receipt]] — the unlock pattern
- [[evidence-capture]] — the harness that surfaced the three defects
- [[proof-edit]] — the proposal contract

## References

- delayed_cleanup_merge.py; macos_delayed_cleanup_destination.py;
  delayed_cleanup_activation.py; README "Tuning"
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-voice-actions-research]]
