---
title: "Acoustic Personalization"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [acoustics, keywords, calibration, privacy, evidence]
aliases: [acoustic-keyword-memory, acoustic-calibration, acoustic-time-machine]
summary: "Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer."
confidence: high
---

# Acoustic Personalization

## Definition

Three subsystems personalize the acoustic front end without
surveillance: the acoustic keyword memory (evidence for hard-name
pronunciations), the acoustic calibration policy (bounded capture
settings from closed numeric telemetry), and the Acoustic Time Machine
(a RAM-only replay buffer for consequence microspans). All three are
storage/policy layers whose runtime effects are gated elsewhere
([[activation-receipt]]).

## Key Properties

- **Keyword memory** (`acoustic_keyword_memory.py`): candidates keyed by
  casefolded keyword + app scope become eligible after 3 distinct
  observations AND 2 distinct confirmations, deduplicated by
  domain-separated digests of opaque evidence ids — never keyword text.
  `RECOGNITION_EFFECT = "none"`: the store cannot bias anything itself.
  Exportable and forgettable from the Pronunciation Keywords inspector;
  256-entry cap with deterministic eviction; no filesystem API by
  design. Only exact verified user corrections add idempotent global
  evidence ([[personalization]]).
- **Keyword activation**: a separate offline A/B evaluator
  (transcript-free, categorical, synthetic-can-never-keep) plus an
  [[activation-receipt]] grant at most bounded *prompt priority* in the
  [[asr-cascade]] biasing prompt — priority "cannot rewrite recognized
  text". Hashed app scopes exist (per-installation salt) but the live
  path currently records global scope only.
- **Calibration** (`acoustic_calibration.py`): consumes only the closed
  16-field numeric telemetry schema; validates internal consistency so
  spliced metrics reject; kills on nonfinite/clipping; insufficient on
  ambiguity; emits bounded gain-ceiling, noise-gate, VAD, and
  end-silence candidates. Reverb is permanently unavailable. The runtime
  applies settings only from a valid receipt, else defaults.
- **Time Machine** (`acoustic_time_machine.py`): opt-in, RAM-only, at
  most eight 2.4-s spans / 10 s total / exactly 16 kHz; random
  content-independent ids; wipes zero buffers on delete/clear/disable;
  the runtime adds a 60-second TTL. Results can replay
  consequence-selected spans from memory; disabling wipes them; no
  replay file is ever written.

## Related Concepts

- [[activation-receipt]] — how any of this gains runtime effect
- [[asr-cascade]] — where prompt priority lands
- [[consequence-receipts]] — selects the retained microspans
- [[privacy-and-security]] — the storage posture

## References

- acoustic_keyword_memory.py, acoustic_keyword_activation.py,
  acoustic_keyword_bias_evaluation.py, acoustic_calibration.py,
  acoustic_calibration_activation.py, acoustic_time_machine.py;
  docs/acoustic-accuracy-activation.md
- [[2026-07-26-trust-personalization-research]]
