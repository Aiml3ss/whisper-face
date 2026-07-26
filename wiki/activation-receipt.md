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

## Producing the evidence

Since 2026-07-26 the pattern has a producer side. Four guided,
resumable harnesses ([[evidence-capture]]) record real sessions —
16 kHz corpora for the three voice gates, a fifty-app insertion matrix,
the delayed-cleanup grid, and the lifecycle scenarios — and hand their
output to the existing evaluators. They are structurally incapable of
approving anything: AST-level tests assert they import no activation
module, execute no process, generate no audio, and cannot even *declare*
`--confirm-manual-review` or `--approve-runtime`. Each prints the exact
benchmark invocation and stops. Manual review stays where it was: a
non-defaultable flag the operator passes to the benchmark, after
listening.

## Three of the four gates cannot currently be earned

> ⚠️ **Known blocker (issues #108 and #110, open on `main`)**: three of
> the four receipts require evidence whose *candidate* arm the runtime
> only produces after the receipt that evidence would authorize.
>
> - **Calibration** applies its settings only from a valid receipt, so
>   the candidate recordings cannot exercise the calibrated front end.
> - **Keyword priority** reaches the ASR prompt only through the
>   activation file, so the biased arm cannot be measured without it.
> - **Delayed cleanup** adds two more: the gate wants p95 apply timing
>   the runtime never measures, and ≥8 cases of a `duplicate-callback`
>   scenario that no operator action can reach.
>
> Selective re-listen has no such circularity — its benchmark drives the
> verifier directly. The proposed fix in #108 is an explicitly labelled
> measurement-only override that is recorded in the manifest and grants
> no runtime authority of its own. Until then, three of the four
> capabilities on this page are unearnable in practice, not merely
> un-activated — a stronger and less comfortable statement than "the
> suite has not been run".

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
- [[evidence-capture]] — the harnesses that record what they evaluate
- [[personalization]] — the same philosophy on the always-on path

## References

- acoustic_calibration_activation.py, acoustic_keyword_activation.py,
  relisten_activation.py, delayed_cleanup_activation.py
- [[2026-07-26-trust-personalization-research]]
