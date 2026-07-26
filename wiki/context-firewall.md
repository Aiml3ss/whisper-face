---
title: "Context Firewall"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [context, shadow, safety, evidence]
aliases: [counterfactual-context-firewall]
summary: "Every finalized, insertion-bound contextual compile is compared with a context-free shadow compile; protected influence is quarantined in a transcript-free receipt that can change nothing."
confidence: high
---

# Context Firewall

## Definition

The Counterfactual Context Firewall re-compiles each finalized,
insertion-bound utterance with context candidates and personal priors
stripped, then compares. If contextual influence touched anything
protected, the receipt's disposition is quarantine; benign influence is
only a shadow promotion candidate. The comparison is hardcoded
shadow-only: it cannot change the text, cleanup, insertion, or model
route.

## Key Properties

- Implemented as `context_firewall_receipt` in the [[voice-compiler]];
  driven once per insertion-bound compile by the runtime.
- An influence is protected when the changed token overlaps a
  [[consequence-receipts]] risk span, is a [[protected-anchor]], or —
  the adversarial case — when the *replacement introduces* a new
  factual/code-shaped anchor even though the original token was
  ordinary.
- Receipts are aggregate-only: mode, disposition, and fixed reason
  counts. The runtime allowlists every string on read and coerces
  unknowns to "unavailable"; an evaluator exception yields a fixed
  receipt-error block.
- Results explains the bounded receipt without exposing context or
  transcript text.

## Examples

- A document term nudges "gwen" to "Qwen": unprotected influence →
  promotion-candidate disposition, and the swap remains visible as a
  compiler Decision.
- Context rewrites a number: protected influence → quarantine
  disposition; the active text still pastes (the firewall observes, the
  compiler's own anchor thresholds are the actual guard).

## Related Concepts

- [[voice-compiler]] — host and mechanism
- [[personalization]] — the shadow-first design language it shares
- [[privacy-and-security]] — why receipts are content-free

## References

- voice_compiler.py `context_firewall_receipt`; dictate.py
  `runtime_context_firewall_evidence`, `store_context_firewall_receipt`
- [[2026-07-26-trust-personalization-research]]
