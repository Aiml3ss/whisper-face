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

*Corrected November 2026.* An edit counts only once it has stayed
unchanged for five polls (about a second) and is still what the range
holds when observation ends; an emptied field (a sent message) ends the
watch, and a paste sent unedited counts as evidence *against* a
candidate. No mapping joins two common English words (about 700, compared
without apostrophes), adds or removes a negation, or maps one number to
another. Only lab-promoted mappings apply, as exact whole-token
replacements, never inside an email address, @handle, or URL; the legacy
fixes/confusions tables no longer apply anything. One correction adds one
to the target's dictionary count and two promote it; common words are
never auto-promoted. Forget removes the mapping's count, history, and lab
evidence in every scope. An observed edit of a pasted snippet is stored
only as a pending suggestion (`snippet_edits[name]["suggested"]`) and
never rewrites the saved snippet; the window does not show suggestions
yet. Correction learning is Mac-only.

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
- **Gated legacy paths**: since November 2026 the old fixes/confusions
  tables apply nothing; only mappings the lab holds active are applied.
  On load, promotions are restored with a conflict check rather than by
  replaying the shadow gate.
- **Inspectable and forgettable**: every learned mapping is listed under
  **Learned corrections** and can be forgotten. Since 2026-07-26 that row lives
  on Settings → Personalize in [[app-window]] rather than in
  [[menu-bar]], alongside Snippets, Vocabulary and Pronunciation
  keywords; a row with nothing yet shows an invitation ("Fix the same
  word a few times and I’ll remember it") instead of a zero.
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
