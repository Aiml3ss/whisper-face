---
title: "ASR Cascade"
type: concept
language: en
created: 2026-07-26
modified: 2026-11-15
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
- **Whole takes** (November 2026): on the Parakeet route no chunk is cut
  until the take is longer than one Parakeet window (14.96 s), so a take
  up to that length is decoded as one request, exactly the offline
  decode of the recording with no joins. A speculation then covers the
  whole take so far and feeds the HUD caption, as chunks did. Longer
  takes and the Whisper routes cut chunks as before.
- **Sessions for long takes** (November 2026): with Whisper Face's own
  runtime loaded (protocol 3), a take that outgrows one window is also
  streamed to the runtime while it is captured; the runtime decodes each
  15 s window as it completes, so release decodes only the last one
  (finish p95 about 70 ms on this Mac for takes from 15 s to 10 min,
  against 0.64 s at p95 for 30 s to 2 min in one request). The answer is the
  runtime's offline decode of the whole take, word for word, and stands
  for the take only when the cross-check has nothing left to do: no gain
  needed, every rolling chunk came back from Parakeet, and every word after
  the last cut clears `CROSSCHECK_SKIP_CONFIDENCE`. Otherwise the take is
  decoded from its chunks as before. When every chunk and the last part
  are decoded already at release (a speculation stood because the speaker
  paused before letting go), the session is not finished at all: its last
  window would be a decode at release that the joined parts do not need.
  On the public benchmark, whose takes end in 0.3 s of silence, finishing
  it anyway had cost long takes about 75 ms at release.
- **Cross-check timing**: the time Tiny's cross-check and a Turbo
  escalation took is recorded per dictation (`crosscheck_ms`,
  `escalation_ms` in the release-path trace). A decode whose every word
  the runtime scores at 0.90 or more (`CROSSCHECK_SKIP_CONFIDENCE`,
  calibrated on the owner's 40 recorded sentences, experiment E3) is never
  escalated to Turbo (`PARAKEET_CONFIDENT_SKIPS_ESCALATION`, on); Tiny
  still runs beside it. With `PARAKEET_WORD_CONFIDENCE_ROUTING` on (off as
  shipped) such a decode skips Tiny too, and any other decode runs Tiny
  after the helper answers instead of beside it.
- **Routing**: on Mac, the Parakeet helper is tried first. Helper
  failure falls back faithfully to Whisper (Turbo when installed, else
  Tiny with a printed notice). A helper that had started restarts lazily
  on the next call; one that fails to start or cannot be spawned is
  logged once and retried only after a backoff of 30 s, doubling to a
  300 s cap (November 2026). Turbo is not part of a default install.
- **Cross-checked confidence** (#126): Parakeet exposes no calibrated
  confidence, so its result is cross-checked against Tiny's hypothesis
  and token agreement maps linearly onto 0.45–0.93. The interesting
  crossings: only agreement above ≈0.81 beats the old fixed 0.84 prior,
  confidence falls below the 0.70 context gate only when agreement is
  below ≈0.52, and a single Turbo escalation fires only for agreement
  below 0.35 on audio of 12 s or less — with the loser retained as an
  inspectable alternative. The fixed prior remains only for runs where
  no cross-check text exists. Since November 2026 the escalation runs
  only when Turbo is installed and is a two-of-three vote on the words
  (more than half the tokens matching), not a confidence comparison:
  Turbo's text wins only when it agrees with Tiny and not Parakeet, and
  Tiny's text is never delivered. Without Turbo, Parakeet's text is
  delivered unverified with Tiny's as the alternative. The quiet-speech
  gain is applied once per cascade.
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
- **Word evidence (protocol 2)**: the client asks the helper for each
  word's time and confidence (`--protocol 2`) and names the verified model
  folder (`--models`). The FluidAudio helper speaks protocol 1 and ignores
  both, so by default Parakeet decodes carry no words. When a protocol 2
  program answers, its words are validated, reshaped into the compiler's
  tokens and recorded (`word_evidence_status`), but two switches, both off,
  keep them out of formatting and routing.
- **Whisper Face's own speech runtime**: `native/ParrotSpeechRuntime`
  runs the same Parakeet model files with no package dependencies (Core ML
  and Accelerate only, nothing fetched to build it, no networking API) and
  speaks protocols 1, 2 and 3 (sessions). Its transcripts match the FluidAudio helper's
  on every measured clip. `setup.sh` requires the helper and builds the runtime if it can (a runtime
  that does not build or load is skipped with one line, not an install failure);
  the New speech runtime switch (Advanced → Models, preference
  `new_speech_runtime`; the runtime loads in the background, the helper
  answering until it is ready) or
  `PARROT_ASR_RUNTIME=own`, which wins when set, asks the runtime first, and anything it cannot
  answer goes to the FluidAudio helper, then Whisper. Off by default until
  the owner has used it. Details, parity and attribution:
  `native/ParrotSpeechRuntime/README.md`; wire contract and switch:
  `native/ParrotASRHelper/PROTOCOL.md`.

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
