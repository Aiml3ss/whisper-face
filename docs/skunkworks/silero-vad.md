# Silero VAD: replace the RMS gates with a trained voice-activity model

Status: design, not built. Ships off; the RMS gates remain the shipped
default and the permanent fallback.

## Problem

Every speech/silence decision in the product is an RMS threshold. The
constants cluster in dictate.py (`MIN_SECONDS` through
`CAPTURE_BLOCK_SECONDS`): `SILENCE_RMS = 0.008` ("tail quieter than this
= you had finished talking"), `GATE_PEAK_RMS = 0.002` (just above mic
noise floor), `TAIL_SKIP_SILENCE = 0.12`, plus the rolling-cut and
speculation thresholds. Three consumers make live decisions from them:

- `Recorder._callback` counts `silent_samples` against
  `calibrated_vad_threshold()` to drive rolling chunk cuts and
  speculative decodes;
- `release_should_wait_for_tail` decides whether speech was still active
  at key release (`calibrated_end_silence_seconds()`);
- `extract_recent_utterance` finds the last speech island in the Flight
  Recorder's RAM buffer using 20 ms RMS windows and an adaptive
  `noise_floor * 3.0` threshold capped at 0.01.

Energy thresholds cannot tell speech from keyboard clatter, HVAC, or a
truck outside; they clip soft first syllables in quiet rooms and refuse
to see the end of speech in noisy ones. The
[acoustic calibration](../../wiki/acoustic-personalization.md) gate
exists to tune these numbers per machine, but it tunes the wrong axis:
better constants for a model of loudness, when the problem is that
loudness is not voice. Silero VAD is a small (~2 MB, MIT-licensed,
ONNX-distributed) trained model that outputs per-window speech
probability and is the standard fix for exactly this.

## Two integration options

**Option 1 — `onnxruntime` as a Python dependency.** Works on Mac and
Windows (Windows shares the capture path). Costs: a large wheel on
every install, a new supply-chain surface in the Python runtime, and a
second inference stack beside MLX. The installers' "provision
dependencies and verify their own work" burden grows for every user,
including those who never enable the feature.

**Option 2 (preferred) — Core-ML-converted Silero in the existing
native helper.** `parrot-asr-helper` already owns audio-adjacent native
inference: it is the process that loads Core ML models, reads framed
Float32 PCM on stdin, and answers JSON lines
(`native/ParrotASRHelper/Sources/parrot-asr-helper/main.swift`). A
one-time offline conversion (ONNX → Core ML, checked provenance,
revision + SHA-256 pinned exactly like the Parakeet assets) gives the
Mac a VAD with **zero new Python dependencies**. Windows keeps RMS until
someone justifies the onnxruntime cost there separately; the seam
introduced below makes that a drop-in later.

## Mechanism

### Helper: a VAD lane beside ASR

The helper gains a `--vad` mode (or, if [streaming
commit](streaming-commit.md) lands first, a frame-type field in its
session protocol — the two designs must share one framing decision).
Wire shape stays the house style: 8-byte little-endian sample count,
Float32 samples, one JSON line back:
`{"ok": true, "p": 0.87, "window_ms": 32}`. Silero operates on 512-sample
windows at 16 kHz (32 ms); the helper accepts batches of N windows per
frame and returns N probabilities, so IPC cost amortizes.

### Runtime: a `SpeechGate` seam, decisions off the audio callback

The audio callback runs at device cadence and must never block on IPC.
Design:

- A `SpeechGate` object owns a ring buffer and a feeder thread. The
  callback appends samples and reads the **latest available** decision
  (a `(speech_active, last_transition_at)` snapshot, lock-free read);
  the feeder ships windows to the helper and updates the snapshot as
  answers arrive. Decisions therefore lag audio by one batch (bounded,
  target ≤ 64 ms) — acceptable for every consumer, since chunk cuts
  need 0.6 s of silence and the tail check tolerates 120 ms.
- Hysteresis lives in `SpeechGate`, not the model: enter speech at
  p ≥ enter_threshold, leave at p ≤ exit_threshold with a minimum hang
  time, the standard two-threshold gate.
- **Fallback is structural**: if the helper is absent, slow, or errors,
  `SpeechGate` reports `unavailable` and every call site uses the
  existing RMS logic unchanged. The RMS path is not deleted in any
  stage of this design; it is the floor. Helper failure already falls
  back faithfully and restarts lazily on the ASR side; the VAD lane
  follows the same rule.

### Wire points, one each

1. **`Recorder._callback`**: `rms < calibrated_vad_threshold()` becomes
   `not SPEECH_GATE.speech_active(now)` when the gate is available,
   feeding the same `silent_samples` / `voiced_since_cut` accounting.
   Chunk-cut and speculation logic above it is untouched — it consumes
   the counters, not the RMS.
2. **`release_should_wait_for_tail`**: `rec.silent_samples <
   calibrated_end_silence_seconds() * SAMPLE_RATE` becomes a question to
   the gate: how long since the last speech-to-silence transition. Same
   contract, better transition detection.
3. **`extract_recent_utterance`** (Flight Recorder): the 20 ms RMS
   window scan becomes a batch VAD pass over the buffered audio (this
   one is not latency-sensitive; it runs at tap time on ≤ 20 s of
   audio). Island selection, `FLIGHT_MAX_LAG` staleness, and padding
   logic are unchanged; only the voiced/unvoiced labeling of windows
   changes. `GATE_PEAK_RMS` remains as a final absolute floor so a
   silent held key still produces nothing.

### Calibration interplay

`calibrated_vad_threshold()` / `calibrated_end_silence_seconds()` are
the single seam the gated calibration feature flows through today, with
receipt-pinned bounds (`VAD_THRESHOLD_BOUNDS = (0.006, 0.05)` RMS,
`END_SILENCE_BOUNDS_MS = (180, 600)` in acoustic_calibration.py). Under
Silero, the tunable knobs change domain: RMS thresholds become
enter/exit **probability** thresholds and hang time. Concretely:

- `acoustic_calibration.py` gains a second candidate family
  (`vad_enter`, `vad_exit`, `vad_hang_ms`) with its own bounds
  (proposed: enter 0.35–0.75, exit 0.15–0.55, enter > exit + 0.1
  enforced; hang 60–400 ms). The policy stays pure and numeric.
- Policy pinning does the right thing automatically: receipts embed the
  thresholds they were approved under, so the schema addition bumps the
  policy and invalidates prior receipts — which currently costs nothing,
  since no receipt exists in the field and the calibration gate is only
  now becoming earnable through measurement mode.
- `end_silence` calibration remains meaningful and shared: it tunes how
  much post-speech hang the release path waits for, whichever gate
  (RMS or VAD) feeds it.

## What exists already vs what is new

Exists:

- The three wire points already route through named functions, and two
  of them through the calibration seam — the refactor surface is small
  and explicit.
- The helper process, its framing, pinning, startup verification
  (`--verify`), lazy restart, and the installer machinery that ships
  pinned model assets.
- The calibration policy/activation split, measurement mode
  (`--measure calibration:...` exists; a `vad` arm is a sibling), the
  guided capture harness `scripts/capture_voice_evidence.py` with its
  clean/quiet/noisy/long-pause task structure, and the activation
  receipt pattern.
- Acoustic telemetry fields (`voiced_fraction`, `trailing_silence_ms`,
  `silence_ratio` …) that the A/B evaluation reads.

New:

- The Core ML Silero artifact: conversion, provenance note under the
  LICENSES process, revision + hash pinning, installer distribution.
- Helper `--vad` lane; `SpeechGate` + feeder thread; the three call-site
  switches with RMS fallback.
- The `vad` candidate family in acoustic_calibration.py and its bounds;
  the `--measure vad:enter=…,exit=…,hang=…` arm; harness pass for it.
- Windows: nothing (explicitly deferred).

## Evidence gate

Reuse the acoustic-calibration bar wholesale, because this is the same
kind of change to the same subsystem: a receipt following the house
pattern, earned from **40 balanced physical A/B cases across the
existing corpus conditions — clean, quiet, noisy, long-pause — with ≥ 3
strict improvements and zero regressions**, recorded with
`scripts/capture_voice_evidence.py` running two passes (RMS arm,
VAD arm under `--measure vad`), manual review non-defaultable, every
artifact labeled with its arm.

Per-case judgments the operator can actually attest, in closed choices:

- first word intact / clipped;
- tail intact / truncated;
- false trigger during the silent portions (yes/no);
- for long-pause cases: the mid-utterance pause survived without a
  premature chunk cut audible as a mid-word seam.

Runtime-measured, blocking if absent: gate decision lag p95 (must stay
≤ 100 ms), helper VAD round-trip p95, and fallback count during the
session (a session that silently ran on RMS fallback is not VAD
evidence — the arm label plus a per-case gate-source field enforces
this).

## Risks and failure modes

- **A model in the capture path** is a new failure class: a Silero
  false-negative on this user's whisper could gate out real speech that
  the dumb RMS gate passes today. Whispering is a shipped, advertised
  capability. The quiet-condition arm of the corpus exists precisely for
  this; a single lost-whisper case is a regression and fails the suite.
- **IPC cadence**: feeding 32 ms windows over stdin at speech rate is
  ~31 messages/s if unbatched; batching to 4–8 windows per frame keeps
  it trivial, but the feeder must handle helper stall without ever
  backing up into the audio callback (drop to RMS, count it).
- **Two masters for end-of-speech** if streaming commit's EOU model also
  lands: EOU is an utterance-semantic signal, VAD an acoustic one. Rule:
  VAD owns the counters (`silent_samples`, tail wait); EOU may only
  accelerate span cuts. Written here so the designs cannot drift apart.
- **Conversion fidelity**: Core ML output must match ONNX reference
  output on a checked-in probe set within tolerance; the `--verify` path
  extends to assert it at install time.
- **Flight Recorder semantics**: the adaptive-noise-floor logic in
  `extract_recent_utterance` encodes a real insight (thresholds must
  track the room). The VAD pass keeps the staleness and minimum-length
  guards; if VAD marks the whole buffer voiced (music playing), the
  island search fails closed to returning nothing rather than pasting a
  20-second transcription of a podcast.

## Staged rollout

1. **Stage 0 — offline**: convert, pin, and evaluate Silero-CoreML
   against RMS labeling over recorded corpora (the calibration corpus
   WAVs, once recorded, plus checked-in synthetic probes for the
   conversion check). No runtime change.
2. **Stage 1 — helper lane + shadow gate**: `SpeechGate` runs live but
   only *logs* disagreement with the RMS decision (content-free
   counters: windows disagreeing, transitions each way). Zero behavior
   change; produces the telemetry that says whether this matters on this
   machine.
3. **Stage 2 — measurement mode**: `--measure vad:…` applies the gate
   for a session; record the 40-case physical A/B.
4. **Stage 3 — receipt-gated activation**, RMS fallback permanent.
5. **Stage 4 — Windows decision**, only if Mac results justify carrying
   onnxruntime weight.

## Open questions

- Which Silero release to pin (v4 vs v5 differ in windowing and
  quality), and does the Core ML conversion preserve the internal LSTM
  state handling correctly across our batch boundaries? State reset
  points must align with utterance starts.
- Should the shadow-gate disagreement counters become part of the
  standard acoustic telemetry schema (a schema bump touches
  `performance_lab.py` and calibration validation)?
- Is there appetite to fold `GATE_PEAK_RMS`'s "silent held key" check
  into the gate, or does it stay a separate absolute floor forever?
  (Proposed: stays forever; it is two lines and catches a dead-mic
  failure mode a speech model cannot.)
- The speculative-decode trigger (`SPECULATIVE_SILENCE = 0.25`) is tuned
  against RMS jitter; VAD hysteresis may let it fire earlier and win
  more speculation reuse. Worth measuring in Stage 1's disagreement
  telemetry before touching the constant.
