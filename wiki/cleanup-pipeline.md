---
title: "Cleanup Pipeline"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [cleanup, llm, qwen, deterministic, safety]
aliases: [structured-cleanup, quick-clean]
summary: "Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits."
confidence: high
---

# Cleanup Pipeline

## Definition

Cleanup turns raw recognition into presentable text: fillers removed,
punctuation fixed, spoken structure honored. The typed "X, actually Y"
self-correction is off by default and applies only when the user turns
it on. Safe edits are compiled deterministically
(`compile_cleanup` / `compile_code_dictation` in `parrot_core.py`); the
local LLM (pinned Qwen3.5-4B via Ollama) is used only for semantic
cleanup and explicit writing modes, and its output is accepted only
through validated [[proof-edit]]s.

## Key Properties

- **Deterministic first**: `needs_llm_cleanup` routes most utterances
  through the pure compiler; a lone ordinal in ordinary prose stays on
  the fast path instead of waking the LLM.
- **Guarded LLM**: the structured JSON edit plan is checked against
  refusals, over-deletion, and truncation; nearby context is explicitly
  marked untrusted; the whole call runs under a (1, 4) s
  connect/read deadline that falls back rather than blocking paste.
- **Circuit breaker**: `cleanup_circuit_breaker.py` admits one call at a
  time; a transport failure opens a cooldown doubling 60→300 s; one
  successful probe resets it; an output-guard rejection releases
  without opening the breaker. Bypasses fall through to deterministic
  cleanup.
- **Proof acceptance**: in capture/code modes, if the declared edits do
  not reconstruct the model's own output byte-for-byte, the whole LLM
  result is discarded ([[proof-edit]]). Compose/reply/edit modes keep
  their broad-rewrite contract by design ([[voice-modes]]).
- **Spoken structure**: "new line" / "new paragraph" / "scratch that"
  work; explicit list lead-ins become dash lists. "New line" stays as
  words in a noun use ("a new line of products") and right after a count
  or quantity — a number, "dozen", "couple", "all", "most", "several",
  "of" — so "We hired two new line cooks." is one line. An unpaused
  command right after a number ("Step 1 new line step 2") is therefore
  typed as words; with a pause (a comma or full stop in the recognizer's
  text) it breaks the line (November 2026).
- **Self-correction is opt-in** (November 2026): the preference
  `self_correction` (default false; Settings → Controls → “Actually”
  corrections) passes into `compile_cleanup`, `compile_code_dictation`
  and `verify_edits`. On, "We meet Tuesday, actually Wednesday." becomes
  "We meet Wednesday." between two numbers, weekdays, months or clock
  times. It is off by default because a contrast has the same shape: "I
  thought it was Tuesday, actually Wednesday." would become "I thought
  it was Wednesday."
- **A word said a few times is kept**: `collapse_repeats` cuts only a
  run of eight or more of one word (a decode loop), so "very, very, very
  good" is typed whole.
- **Snippet masking**: inline snippets fire only on the spoken cue
  "insert my <name>" (never on a bare snippet name, never when the cue is
  negated as in "do not insert my email", and never for a value still
  starting with `EDIT ME`). The expansion is masked to private-use-area
  sentinels before cleanup and restored after, so boilerplate is never
  reflowed and the model never sees expansions (corrected November 2026).
- A standalone proof-recovery mediator and a latency lab exist for
  benchmarking prompts and models, with no runtime authority
  ([[benchmarks]]).

## Examples

- "two things: ship it and second, write the notes" → a tidy dash list,
  deterministically.
- Ollama down → breaker opens, every ordinary dictation still pastes
  with deterministic cleanup; a probe closes the breaker later. Compose,
  Reply and Edit insert nothing in that case ([[voice-modes]]).

## Related Concepts

- [[proof-edit]] — the acceptance contract
- [[delayed-cleanup]] — the insert-now variant
- [[voice-modes]] — which modes get which contract
- [[voice-compiler]] — validates the edits

## References

- parrot_core.py `compile_cleanup`; dictate.py `llm_clean_with_edits`,
  `_guard_cleaned_output`; cleanup_circuit_breaker.py; eval_cleanup.py
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-trust-personalization-research]]
