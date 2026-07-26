---
title: "Point-and-Speak"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [actions, accessibility, transaction, macos]
aliases: [ax-press, target-resolution]
summary: "Say a target's name, preview the single confident match, then explicitly confirm one AXPress on a strongly named control — with nonces, leases, exact rechecks, and text fields excluded by construction."
confidence: high
---

# Point-and-Speak

## Definition

Point-and-Speak resolves a bounded spoken phrase against the frontmost
app's accessibility tree and, only after a separate Press-once
confirmation, performs exactly one `AXPress` on a strongly named button,
checkbox, radio button, tab, menu item, or link. Text fields and every
unlisted role remain inert.

Until 2026-07-26 it was reachable as an explicit Mac Diagnostics action.
#104 removed the preview and press dialogs from the window; the resolver,
the transaction, the AX snapshot, the `GUIActions` fields and the
view-model passthroughs all remain, so it is developer-invokable today
and has no user surface ([[app-window]]).

## Key Properties

- **Pure resolver** over closed snapshots (names, roles, geometry,
  state — never AX values or document text): hard filters remove
  candidates contradicting declared role/selection/focus; gates are
  confidence ≥0.82 and margin ≥0.12; below either returns ambiguous
  with *no target id*. Receipts expose buckets, not raw scores.
- **The runtime gate is stricter than the resolver**: untruncated
  capture, exact/normalized evidence, very-high confidence bucket, wide
  margin bucket — else the nonce is spent on UNAVAILABLE.
- **Exactly-once transaction**: single-use nonces popped under a lock
  before any callback; lease age ≤2.0 s; role must be press-safe and
  must equal the role declared at preview; the recheck re-verifies
  trusted access, the same focused application and window *elements*,
  and byte-exact equality of the projected target facts. Any drift,
  replay, expiry, weak evidence, unsupported role, or action failure
  does nothing.
- **Privacy**: phrases, names, and target identifiers stay transient;
  routine status and support snapshots receive only content-free
  evidence. The preview returns a name and role but never a target id.
- The 17-case synthetic resolver corpus has zero wrong-target
  resolutions, and no physical-app accuracy claim is made
  ([[benchmarks]]).

## Related Concepts

- [[inert-foundations]] — drop-to-target, the read-only sibling
- [[voice-objects]] — the same nonce-ceremony language
- [[privacy-and-security]] — bounded AX evidence rules
- [[app-window]] — the window that no longer exposes it

## References

- point_and_speak_resolver.py, point_and_speak_transaction.py,
  macos_point_and_speak_snapshot.py; benchmarks/point_and_speak_cases.json
- [[2026-07-26-voice-actions-research]]
