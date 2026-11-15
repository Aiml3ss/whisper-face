---
title: "Insertion Transaction"
type: concept
language: en
created: 2026-07-26
modified: 2026-11-15
tags: [insertion, safety, outbox, core, macos]
aliases: [insertion-lease, voice-outbox, exactly-once-insertion]
summary: "Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox."
confidence: high
---

# Insertion Transaction

## Definition

Insertion is a transaction (ADR-0003), not a hopeful paste. A
privacy-safe lease of the destination is captured at hotkey press;
final text may make **one** paste attempt only if the current
destination still matches; every outcome terminates in an insertion
receipt; and text that was not proven delivered lands in the Voice
Outbox instead of the wrong field.

## Key Properties

- **Lease**: destination identity, selection range, and a SHA-256
  fingerprint of surrounding text (the text itself is never retained).
  Readable AX fields get a full lease; opaque/terminal targets get an
  opaque lease sealed at release only if input-event counters are
  unchanged; no focused element at all fails closed into the outbox.
- **Exactly-once**: the coordinator (`insertion_integrity.py`) marks an
  entry terminal *before* invoking platform code, so reentrant or
  concurrent callbacks cannot double-paste; duplicate stages raise;
  completed utterances leave tombstones. A mid-paste exception leaves
  the entry unresolved-and-recoverable — never retried, because
  delivery may precede failure.
- **Drift detection**: focus, selection, or surrounding-text drift while
  recognition ran produces a conflict receipt with no paste attempt.
- **Readback**: where macOS can safely re-read the pasted range, the
  observed text verifies delivery. Since November 2026 every app gets
  the same adaptive window: the readback returns on the first matching
  read and otherwise polls for up to 0.4 s, because Chromium (in browsers
  and Electron alike) publishes AX values late. Accessibility ranges are
  converted from UTF-16 units to code points at the boundary. (Before,
  Electron got 0.35 s and native apps 0.02 s.)
  Since #115 a conflict names *how* the field differed through a closed
  shape vocabulary (observed-empty, trailing-whitespace,
  internal-whitespace, unicode-form, expected-is-substring,
  observed-is-prefix, divergent) carried as the `readback_shape` metrics
  key — categories only, no destination text — taken from the utterance's
  own Insertion Receipt since November 2026. Since #117 an
  edge-whitespace-only difference is proven delivery under its own
  receipt reason (`commit_verified_edge_whitespace`), because some
  editors trim or add whitespace at the very edges; nothing weaker
  qualifies, and a field reading back as pure whitespace cannot pass.
- **Join spacing** (#119): a dictation landing directly after existing
  text gets one leading space from `insertion_join_prefix`, decided from
  the character immediately before the insertion point and applied
  before staging, so the staged string, the paste, and the readback
  expectation agree on one string. Empty fields, trailing whitespace,
  opening brackets or quotes, and attaching punctuation add nothing;
  replacing a selection looks before the selection.
- **Voice Outbox**: a bounded (20-item) RAM-only recovery queue that
  distinguishes never-pasted from possibly-landed text; only the
  explicit Copy & Dismiss control recovers content. Since 2026-07-26 the
  count-only [[menu-bar]] row appears solely while the queue holds
  something, and it routes to Home, where the Dictation rows carry the
  warning line and the speech bubble carries the Copy & Dismiss control
  ([[app-window]]). Only
  *verified* receipts may train [[personalization]].
- **Unchecked paste** (November 2026): a paste that was sent and that
  the destination would not let be read back (receipt `unverifiable`
  with a paste attempted, `InsertionReceipt.pasted_unchecked`) is no
  longer reported as an error. The overlay shows its porcelain "Pasted"
  pill with the ordinary cue, the menu-bar face stays as it is, and the
  copy is kept in the outbox, where it is counted ("pasted, not
  double-checked") but does not put the window into its recovery phase.
  Nothing else changed: the receipt, the `outbox/` transcript path,
  `insertion_verified: false`, no correction learning and no undo. A
  readback conflict, an unknown paste outcome and a paste that was never
  attempted still get the coral error, and Copy & Dismiss takes those
  entries before any unchecked copy.
- **Why a destination was opaque**: for an opaque lease the transcript
  metrics also carry `insertion_opaque_reason`, `insertion_focus_kind`,
  `insertion_focus_role`, `insertion_focus_subrole`,
  `insertion_value_kind` and `insertion_selection_kind`: closed
  categories and Accessibility role names recorded at key-down, never a
  title, a value or a label, so that one dictation into an app says what
  the app exposes.
- Receipt states: verified, unverifiable, conflict, unresolved — with
  fixed reasons; the same vocabulary is reused by the in-process
  protocol ([[inert-foundations]]) and the compatibility fingerprint.
- **Read as evidence**: the fifty-app capture session
  ([[evidence-capture]]) reads exactly these receipts from the
  transcript-free keys of `transcripts.jsonl`, and treats the runtime's
  no-receipt sentinels as *not evidence* rather than as a result. The
  issue #110 gap here closed with #118: `commit_insertion` now exports
  the target / paste / readback capability buckets into the transcript
  metrics (`insertion_target`, `insertion_paste`, `insertion_readback`),
  computed from state the commit already holds and stored per-utterance
  so overlapping dictations cannot publish each other's destinations —
  the harness's full-observation path is real instead of reporting
  `capability_buckets_available: false`.

## Examples

- You switch windows mid-dictation → conflict receipt, no paste, the
  text waits in the outbox.
- A terminal hides its text → the insertion binds to the original
  app/element and reports delivery as unverified rather than guessing.

## Related Concepts

- [[dictation-pipeline]] — stage 9
- [[delayed-cleanup]] — requires a verified receipt
- [[personalization]] — trains only on verified receipts
- [[privacy-and-security]] — why fingerprints, not text

## References

- insertion_integrity.py; dictate.py `capture_insertion_lease`,
  `commit_insertion`, `insertion_readback`, `insertion_join_prefix`;
  docs/adr/0003
- [[2026-07-26-runtime-pipeline-research]]
