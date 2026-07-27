---
title: "Benchmarks"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
tags: [benchmarks, evidence, performance, labs]
aliases: [performance-lab, no-runtime-authority, public-scorecard, evidence-publication]
summary: "A family of offline, transcript-free labs whose shared rule is no runtime authority: evidence can reject changes and build activation receipts, but nothing in the runtime moves on a benchmark's say-so."
confidence: high
---

# Benchmarks

## Definition

Every benchmark in the repository is offline, opt-in, and
transcript-free, and none can change shipping behavior. Reports are
aggregate-only; synthetic evidence never masquerades as physical
validation; `unavailable` is a first-class result. The canonical
demonstration: two proposed model changes and a `writev` framing change
were all rejected on lab evidence, and nothing in the runtime moved.

## The family

- **ASR bakeoff** (`benchmark_asr.py`): LibriSpeech comparison with a
  shared normalizer; research audio stays outside the repo. The
  shipping baseline (2026-07-21, M4 Pro): Parakeet 1.240% WER at 113×
  realtime, Turbo 1.717% at 4.4×, Tiny 7.010% at 120× — the measured
  justification for the [[asr-cascade]].
- **Warm-path profile**, **voice-compiler golden corpus**, **cleanup
  latency** (opt-in, local Ollama only), **cleanup proof recovery**,
  **consequence routing** (deterministic, worst-case-per-case p95
  gate), **insertion reliability** (simulation-only; explicitly no
  four-nines claim), plus the four activation-evidence producers
  ([[activation-receipt]]).
- **performance_lab.py**: the aggregation surface — corpus, evaluate,
  traces, startup, warm-path, lifecycle, stress, scorecard,
  audit-models. Fixed schema identifiers so user strings can never
  become aggregate keys; budget profiles; sparse data reports
  insufficient-samples instead of false regressions. Reports p50-p99
  latency, Correction Burden, zero-edit proxy, and verified-delivery
  rate by dimension.
- **public_scorecard.py**: aggregates the checked-in synthetic suites
  into a public JSON scorecard that never claims physical validation.
- **competitor_benchmark.py**: a neutral task protocol over externally
  collected observations; it never runs products, ranks, or treats
  marketing claims as measured evidence (measured / unavailable /
  claimed-only per task). `benchmarks/competitor_run_template.json` is a
  zero-measurement template so the evaluator is runnable from a clone.
- Corpora live in `benchmarks/` with case counts and privacy stamps,
  and `benchmarks/reproducibility.json` declares per corpus what a third
  party can actually re-run — a test fails if a case count, command, or
  corpus stops matching the repository.
- Known gap: the consequence-routing corpus asserts three of the four
  routes — `verified` has no corpus case ([[consequence-receipts]]).

## The capture side (added 2026-07-26)

The labs always had evaluators and never had a humane way to feed them
physical evidence. Four harnesses ([[evidence-capture]]) now record real
sessions — three voice corpora, a fifty-app insertion matrix, the
delayed-cleanup grid, and the lifecycle scenarios — and hand their
output to these same evaluators. They extend the no-runtime-authority
rule one step earlier: a capture tool cannot import an activation module,
execute a process, generate audio, or declare an approval flag, and tests
assert those properties over its own AST. Two consequences for this page:

- `capture_lifecycle_evidence.py` speaks `performance_lab`'s own five
  lifecycle scenario keys and reports which of its three
  `requires_physical_validation` ids a session discharges — the honest
  counterpart to a simulation that reports `real_apps_exercised: 0`.
- Building the harnesses was itself an audit: it produced two gate
  defects (issues #108, #110) rather than evidence. Both closed on
  2026-07-27 when #118 added session-scoped measurement mode — the
  history and the fix are recorded on [[activation-receipt]],
  [[delayed-cleanup]] and [[acoustic-personalization]].

## The publication side (added 2026-07-27)

The labs could measure but had no way to *publish* without a reviewer
holding the two evidence classes apart by hand. `public_scorecard.py
publish` makes that separation structural rather than editorial:

- Synthetic suites and physical sources are built by different
  functions. The synthetic builder has no parameter capable of marking a
  suite physical; the physical builder cannot run without a concretely
  named machine (hardware, OS version, 40-character repository
  revision — placeholders like `unknown` or `tbd` are rejected).
- A physical source is admitted only from a **registered** producer
  artifact that stamped itself physical, reports non-zero physical work,
  and satisfies that producer's honesty fields. A genuine but *empty*
  capture artifact still carries a physical-sounding `evidence_scope`,
  so scope alone is never sufficient. A re-listen report that counted a
  single synthetic sample is refused outright.
- A separation invariant re-checks the finished document, and runs again
  inside both renderers, so a hand-assembled report is held to the same
  rule as a built one. There is no combined total anywhere.
- The model scorecard became the enforced single source for the bakeoff
  table: every metric binds to a named measurement record or the
  explicit `unmeasured` state, no run may claim to be independently
  recalculable without a preserved artifact digest, and
  `performance_lab.py refresh-model-scorecard` copies metrics from a
  real `benchmark_asr` summary rather than accepting a hand edit.
- The 2026-07-21 M4 Pro run's raw artifacts were never preserved, so it
  is published as documented-run evidence with
  `independently_recalculable: false` rather than quietly implied to be
  reproducible.

## Related Concepts

- [[activation-receipt]] — benchmarks as evidence producers
- [[asr-cascade]] — the measured baseline
- [[governance]] — the release-gate test list

## References

- benchmark_*.py, performance_lab.py, public_scorecard.py,
  competitor_benchmark.py; benchmarks/; docs/benchmarks/
- docs/contributor-interfaces.md,
  docs/benchmarks/reproducible-corpora.md
- [[2026-07-26-ops-governance-research]]
