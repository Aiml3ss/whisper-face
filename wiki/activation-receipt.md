---
title: "Activation Receipt"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [evidence, safety, pattern, activation]
aliases: [evidence-gating, physical-evidence-receipt]
summary: "The house pattern for risky capabilities: features ship off and unlock only via a 0600 receipt this machine produced from manually reviewed physical evidence — with policy, model, and evidence pinning."
confidence: high
---

# Activation Receipt

## Definition

An activation receipt is a private, owner-only, 0600 file proving that a
feature's physical evidence bar was met on *this* machine and manually
reviewed. Four features use it: acoustic calibration, acoustic keyword
prompt priority, selective re-listen ([[consequence-receipts]]), and
[[delayed-cleanup]]. Missing, malformed, stale-policy, or insufficient
receipts all leave defaults in place — broken is indistinguishable from
absent.

## Key Properties

- **Producers** are `uv run` benchmarks taking a private manifest plus
  `--approve-runtime` and `--confirm-manual-review`; manual review is a
  separate non-defaultable input checked before any evidence.
- **Policy pinning**: receipts embed the thresholds they were approved
  under; validation compares them to current module constants, so
  bumping a threshold invalidates every existing receipt.
- **Model pinning**: the re-listen receipt pins engine, repo, and
  revision; changing the verifier model invalidates it.
- **Evidence pinning**: receipts carry a SHA-256 of the canonical source
  report.
- **Synthetic evidence can never activate** — evaluators refuse mixed
  batches and stamp `activation_claim: False` themselves.
- **Backing state must persist**: keyword activations re-join against
  currently eligible memory; forgetting a keyword removes its
  activation.
- **Never copied between machines**: the runbooks
  (docs/acoustic-accuracy-activation.md,
  docs/selective-relisten-activation.md) forbid copying receipts as a
  substitute for device-specific evidence, and forbid committing audio,
  manifests, or receipts to Git.
- The wider design language: layered authority (evidence ≠ eligibility ≠
  activation), content-free self-validating receipts, bounded evidence,
  fail closed ([[privacy-and-security]]).

## Examples

- Calibration: 40 balanced physical A/B cases across
  clean/quiet/noisy/long-pause, ≥3 improvements, zero regressions →
  bounded gain/noise-gate/VAD/end-silence settings apply; reverb is
  permanently unavailable because telemetry cannot measure it.
- Keyword priority: 20+20 caller-attested cases, ≥3 selection gains,
  zero regressions → one term earns bounded local-ASR prompt priority
  ([[acoustic-personalization]]).

## Related Concepts

- [[acoustic-personalization]], [[consequence-receipts]],
  [[delayed-cleanup]] — the four consumers
- [[benchmarks]] — the producers and their no-runtime-authority stance
- [[personalization]] — the same philosophy on the always-on path

## References

- acoustic_calibration_activation.py, acoustic_keyword_activation.py,
  relisten_activation.py, delayed_cleanup_activation.py
- [[2026-07-26-trust-personalization-research]]
