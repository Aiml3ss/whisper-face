---
title: "Delayed Cleanup"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
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
  web, Electron, and terminal editors and the four scenarios (unchanged,
  edit-elsewhere, edit-overlap, focus-drift), balanced applied/rejected
  outcomes, zero wrong-target/user-overwrite/duplicate failures, p95
  final-apply ≤150 ms, manual review. The suite has not been run in the
  repository, so the feature ships off and no live safety claim is made.

## The activation bar became earnable on 2026-07-27

> 📝 **Updated**: issue #110 recorded three defects that made the 50-case
> corpus unproducible. #118 fixed all three, closing the issue:
>
> 1. **Measurement mode breaks the bootstrap deadlock.** Starting the
>    runtime with `--measure delayed-cleanup` (`measurement_mode.py`)
>    lets `schedule_delayed_cleanup` run the same real transaction with
>    no receipt. The override is session-scoped and argument-only,
>    prints a startup banner, shows a menu-bar row, and grants no
>    authority: `DELAYED_CLEANUP_STATE["active"]` stays receipt-only,
>    every case recorded under it carries the
>    `measured-delayed-cleanup` label, and the receipt reports the count
>    as `measurement_mode_cases`.
> 2. **`apply_ms` has a source.** `_run_delayed_cleanup` now times the
>    transactional apply itself (read, merge, compare-and-swap) and
>    appends `; <float> ms` to the `[delayed-cleanup]` line the capture
>    tool parses. The proposal build is excluded because the 150 ms
>    budget is an apply budget; a pass that never reached an apply
>    prints no duration, so that case still blocks on
>    `no-runtime-timing` rather than guessing.
> 3. **`duplicate-callback` was dropped, not made reachable.** The
>    runtime derives the proposal id from the per-utterance event id,
>    so the adapter's duplicate paths cannot be entered by any operator
>    action; earning eight "physical" cases would have required a
>    synthetic injection wearing a physical label. `SCENARIOS` is now
>    the four listed above, and `test_delayed_cleanup_merge` covers the
>    single-use-id contract deterministically.
>
> Earnable is not earned: the physical 50-case session has still not
> been run, no receipt exists, and the feature still ships off.

## Related Concepts

- [[cleanup-pipeline]] — the synchronous sibling
- [[insertion-transaction]] — provides the verified receipt gate
- [[activation-receipt]] — the unlock pattern
- [[evidence-capture]] — the harness that surfaced the three defects
- [[proof-edit]] — the proposal contract

## References

- delayed_cleanup_merge.py; macos_delayed_cleanup_destination.py;
  delayed_cleanup_activation.py; measurement_mode.py; README "Tuning"
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-voice-actions-research]]
