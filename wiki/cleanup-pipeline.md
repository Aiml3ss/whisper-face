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

Cleanup turns raw recognition into presentable text: fillers and false
starts removed, self-corrections applied, punctuation fixed, spoken
structure honored. Safe edits are compiled deterministically
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
  work; explicit list lead-ins become dash lists.
- **Snippet masking**: inline snippet triggers are masked to
  private-use-area sentinels before cleanup and restored after, so
  boilerplate is never reflowed and the model never sees expansions.
- A standalone proof-recovery mediator and a latency lab exist for
  benchmarking prompts and models, with no runtime authority
  ([[benchmarks]]).

## Examples

- "two things: ship it and second, write the notes" → a tidy dash list,
  deterministically.
- Ollama down → breaker opens, every dictation still pastes with
  deterministic cleanup; a probe closes the breaker later.

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
