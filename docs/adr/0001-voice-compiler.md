# ADR-0001: Compile evidence instead of rewriting transcripts

Status: accepted

## Context

The runtime already combines Whisper recognition, context retrieval,
deterministic cleanup, a local LLM, and correction learning. Those decisions
were distributed across the capture path, making it difficult to explain why a
word won or to test semantic safety through one interface.

## Decision

Introduce VoiceIR and a deep Voice Compiler module. Recognition engines and
Context Adapters provide evidence. The Voice Compiler aligns Recognition
Hypotheses into a SpanGraph, applies Personal Priors conservatively, protects
anchors, and accepts capture-mode semantic cleanup only as validated Proof
Edits.

The LLM is an optional editor, never the source of acoustic truth. Explicit
compose, reply, and edit modes may still request broader rewriting under their
existing contracts.

## Consequences

- One interface becomes the test surface for recognition fusion, personal
  ranking, semantic safety, and explanations.
- New recognition engines can be added as adapters without spreading routing
  rules through the runtime.
- Conservative validation may initially reject some useful cleanup. The safe
  deterministic result remains available.
- The compiler and its benchmark must remain platform-independent.
