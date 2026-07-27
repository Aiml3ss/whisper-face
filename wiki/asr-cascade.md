---
title: "ASR Cascade"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
tags: [asr, recognition, parakeet, whisper, performance]
aliases: [confidence-cascade, rolling-recognition, speculation]
summary: "The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold."
confidence: high
---

# ASR Cascade

## Definition

Whisper Face never waits for one big decode. On Mac, three local engines
cooperate: Whisper Tiny gives fast speculative previews, the native
Parakeet Unified helper (Core ML, via FluidAudio) is the primary
recognizer, and Whisper large-v3-turbo is the independent fallback. Long
speech is recognized *while you talk* through rolling chunk cuts, so a
60-second ramble pastes about as fast as a one-liner.

## Key Properties

- **Speculation**: a decode starts once a voiced segment reaches 0.8 s
  with 0.25 s of silence. With the Parakeet route active, Tiny is never
  accepted as final text — it exists for early HUD feedback; its
  disagreement is retained as an alternative hypothesis for the
  [[voice-compiler]].
- **Rolling cuts**: a chunk is cut at a natural pause once the segment
  is ≥4.0 s with ≥0.6 s silence; pending speculation is reused when
  still valid. Each chunk keeps exact start/end samples so word timings
  stay anchored across silence gaps.
- **Routing**: on Mac, the Parakeet helper is tried first. Helper
  failure of any kind falls back faithfully to Whisper Turbo and lazily
  restarts the helper next call. If Turbo's snapshot is absent, the
  runtime degrades to Tiny with a printed notice.
- **Cross-checked confidence** (#126): Parakeet exposes no calibrated
  confidence, so its result is cross-checked against Tiny's hypothesis
  and token agreement maps linearly onto 0.45–0.93. The interesting
  crossings: only agreement above ≈0.81 beats the old fixed 0.84 prior,
  confidence falls below the 0.70 context gate only when agreement is
  below ≈0.52, and a single Turbo escalation fires only for agreement
  below 0.35 on audio of 12 s or less — with the loser retained as an
  inspectable alternative. The fixed prior remains only for runs where
  no cross-check text exists.
- **English-only by proof, not hope** (#135): Parakeet Unified is an
  English-only checkpoint that answers `ok` for *any* audio — fed Dutch
  or Korean it returns a phonetic English transliteration rather than
  declining. A non-English dictation language therefore never reaches
  Parakeet: the cascade routes it to Whisper with the language forced
  explicitly (an unforced "en" made Whisper *translate* instead of
  transcribe). Eleven languages ship; space-less scripts also bypass the
  ASCII-token hallucination filter that used to discard correct CJK,
  Cyrillic, and Hangul transcripts.
- **Low-confidence retry**: a primary decode below 0.52 confidence gets
  one independent second decode at temperature 0.4; the more confident
  transcript wins, the loser is retained as an alternative.
- **Warmth**: models preload at login and a keep-warm tick every 240 s
  keeps first-dictation latency flat ([[installers-and-services]]).
- **The native helper**: a 92-line Swift executable pinned to FluidAudio
  0.15.5, fed framed Float32 PCM over stdin (8-byte little-endian count,
  10-minute cap) and answering JSON lines; audio never touches disk. The
  measured baseline that justifies the cascade: Parakeet 1.240% WER at
  113× realtime vs Turbo 1.717% at 4.4× vs Tiny 7.010% at 120×
  ([[benchmarks]]).

## Examples

- Whispering works: capture applies a sqrt loudness curve and
  gain-normalizes quiet speech before recognition.
- Context biasing: a 60-term / 700-char Whisper prompt is built from
  keyword hints and the glossary each utterance
  ([[acoustic-personalization]]).

## Related Concepts

- [[dictation-pipeline]] — where the cascade sits
- [[voice-compiler]] — consumes hypotheses + alternatives
- [[model-wallet]] — the pinned provider profiles
- [[windows-support]] — the Tiny→Turbo cascade without Parakeet

## References

- dictate.py `_speculative_frames`, `transcribe_detailed`,
  `Recorder._callback`; native/ParrotASRHelper
- [[2026-07-26-runtime-pipeline-research]]
