---
title: "Model Wallet"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [models, routing, evidence, foundation]
aliases: [model-readiness-evidence, model-wallet-shadow]
summary: "A provider-neutral routing policy over the four pinned models — deliberately not wired to live routing, with filesystem evidence capped below readiness and a fail-closed shadow advisory."
confidence: high
---

# Model Wallet

## Definition

The model wallet (`model_wallet.py`) is an in-process, provider-neutral
policy over the four pinned models (Parakeet Unified, Whisper Tiny,
Whisper large-v3-turbo, Qwen3.5-4B): immutable profiles expose
capabilities, readiness, and bounded evidence; failover is sequential
and requires an explicit typed failure receipt. Live routing is
intentionally not wired — the wallet is a foundation whose authority is
deliberately withheld.

## Key Properties

- **Fail-closed execution contract**: only an explicit typed
  `AttemptReceipt` failure authorizes failover; a provider that raises,
  returns a non-receipt, or a mismatched receipt raises
  ProviderContractError rather than falling through.
- **Shadow advisory** (`model_wallet_shadow.py`): reports current-pin
  eligibility and deterministic advisory order without executing a
  model. The receipt self-validates: providers must be exactly the
  pinned set, `attempted` must be False, `fail_closed` must equal
  not-advisory-order, and tied preference ranks are rejected. Runtime
  evidence in is only provider id + readiness state + revision flag —
  no paths, exceptions, or model output.
- **Filesystem evidence** (`model_readiness_evidence.py`): read-only
  inspection of the four pin locations (`uv run
  model_readiness_evidence.py --format json`); a constructor assertion
  *raises* if asked to claim READY, because filesystem evidence cannot
  attest model readiness — exact-pin success is capped at RESOLVED, and
  readiness/capability/routing authority stay false. Bounded traversal,
  symlink containment, no paths in receipts.
- The Models pane labels all of this as a shadow advisory with no
  execution or routing.

## Related Concepts

- [[asr-cascade]] — the actual live routing today
- [[activation-receipt]] — the same layered-authority philosophy
- [[benchmarks]] — where wallet evidence gets exercised

## References

- model_wallet.py, model_wallet_shadow.py, model_readiness_evidence.py
- [[2026-07-26-trust-personalization-research]]
