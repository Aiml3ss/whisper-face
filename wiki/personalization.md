---
title: "Personalization"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [learning, corrections, priors, regression, safety]
aliases: [learned-corrections, personal-priors, personal-regression-lab, shadow-candidate-gate]
summary: "Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate."
confidence: high
---

# Personalization

## Definition

Whisper Face learns only from what you actually fix. After a *verified*
insertion ([[insertion-transaction]]), the exact pasted range is
observed for ten seconds; word-shaped respellings (similarity 0.4-1.0
exclusive, alphanumeric, 2-30 chars, max 3 per dictation) become
correction evidence. A Personal Prior activates after two matching
observations in one app or three globally (ADR-0004) — and only if it
survives the Personal Regression Lab.

## Key Properties

- **Personal Regression Lab** (`personal_regression.py`): a bounded
  local suite of the user's exact corrected spans (256 cases, 80-char
  span cap). Candidates must materially improve the whole suite with
  zero regressions or evaluation errors. New contradicting evidence
  *demotes* an already-promoted prior; deserialization replays
  record/propose so unsafe mappings re-quarantine on load; apply is a
  single non-recursive substitution pass to prevent mapping chains
  (Gwen→Qwen then Qwen→When).
- **Shadow Candidate Gate** (`shadow_candidate_gate.py`): the single
  promotion contract shared by models, prompts, dictionaries, and
  priors. Any error or regression quarantines and the activation
  callback never runs; zero improvements is insufficient evidence; case
  text is repr-suppressed so it cannot leak through tracebacks.
- **Gated legacy paths**: promoted-or-quarantined heard-terms suppress
  the old ungated fixes/confusions, so a quarantine decision also blocks
  the legacy path.
- **Inspectable and forgettable**: every learned mapping is listed under
  Learned Corrections in the menu and can be forgotten; snippets learned
  from placeholder edits appear the same way.
- **Vocabulary mining** runs only after 180 s of dictation inactivity;
  transcripts trim to the recent 500.
- Priors reach recognition as scored span alternatives in the
  [[voice-compiler]], with anchor-aware thresholds; explicit corrections
  also feed [[acoustic-personalization]] keyword evidence.

## Related Concepts

- [[insertion-transaction]] — the verified-receipt precondition
- [[voice-compiler]] — where priors act
- [[activation-receipt]] — the same evidence philosophy for riskier
  features
- [[context-firewall]] — shadow-first sibling

## References

- personal_regression.py; shadow_candidate_gate.py; dictate.py
  `learn_from_corrections`, `compiler_personal_priors`; docs/adr/0004
- [[2026-07-26-trust-personalization-research]]
