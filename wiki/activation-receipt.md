---
title: "Activation Receipt"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
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

## How the three circular gates became earnable

> 📝 **Updated 2026-07-27** (#118, closing issues #108 and #110): three
> of the four receipts required evidence whose *candidate* arm the
> runtime only produced after the receipt that evidence would authorize
> — calibration settings applied only from a receipt, the biased keyword
> reached the ASR prompt only through the activation file, and delayed
> cleanup scheduled nothing without one. Selective re-listen never had
> the loop (its benchmark drives the verifier directly), and its shape
> is what the fix copies.
>
> `measurement_mode.py` closes the circle with a session-scoped
> `--measure` argument that applies the real candidate path — the same
> front end, the same Whisper prompt, the same transaction — while
> granting no authority. The module imports no activation module, opens
> no file, and cannot produce the settings type the runtime treats as
> proof; a startup banner, a menu-bar row, and the status snapshot all
> announce an active session, and it ends with the process. One
> malformed argument disables every arm. Every artifact recorded under
> an arm carries that arm's label and the validators carry the label
> into the receipt, so a measured corpus is disclosed, never laundered.
> No threshold moved, and manual review remains the only thing that
> installs anything.
>
> Earnable is not earned: none of the physical suites has been run, no
> receipt exists, and all four capabilities still ship off.

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
  relisten_activation.py, delayed_cleanup_activation.py,
  measurement_mode.py
- [[2026-07-26-trust-personalization-research]]
