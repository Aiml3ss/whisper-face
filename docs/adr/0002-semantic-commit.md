# ADR-0002: Stable prefixes are committed conservatively

Status: accepted

## Context

Typing provisional text into arbitrary applications can corrupt user content
when later audio changes the hypothesis, focus moves, or selection state is no
longer the one captured at hotkey press.

## Decision

The streaming path computes Stable Prefixes continuously and publishes them to
the HUD by default. Target-application Semantic Commit is permitted only behind
an explicit preference and only while the original focus and insertion receipt
remain verifiably safe. Final release always compiles the complete VoiceIR.

## Consequences

- Users receive live feedback immediately without risking duplicate or stale
  insertion.
- The interface supports future live typing without redesigning recognition.
- Target-field live typing remains experimental until correction receipts can
  span multiple commits reliably.
