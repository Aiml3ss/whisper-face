# Parrot ASR helper protocol

Wire contract between `dictate.py` (`ParakeetClient`, around line 6291) and the
native FluidAudio helper in `Sources/parrot-asr-helper/main.swift`. The helper
is a long-lived child process; audio crosses one pipe in RAM and never touches
disk. This document defines the framed request, the response schema the helper
emits today (v1), and the richer schema it will emit after a FluidAudio
upgrade (v2), so the Python side can implement tolerant parsing ahead of time.

## Process modes

| Invocation | Behavior |
| --- | --- |
| `parrot-asr-helper --server` (default) | Load models, print one ready line, then serve framed requests from stdin until EOF. |
| `parrot-asr-helper --preload` / `--verify` | Load models, print one status line, exit 0. |
| anything else | Usage line on stderr, exit 2. |

Ready line (server mode), one JSON object terminated by `\n`:

```json
{"load_s": 12.25, "model": "parakeet-unified", "ready": true}
```

Preload/verify line: same fields with `"ok": true` instead of `"ready"`. All
helper output lines are single-line JSON objects with sorted keys. Model load
logs from FluidAudio go to stderr, never stdout.

## Request framing (stdin)

One request is:

1. An 8-byte little-endian unsigned integer: the sample count `N`.
2. `N * 4` bytes: `N` IEEE-754 Float32 little-endian samples, 16 kHz mono,
   nominal range -1.0..1.0.

Python side (current implementation):

```python
payload = np.ascontiguousarray(audio, dtype="<f4")
struct.pack("<Q", len(payload)) + payload  # written as two chunks
```

Bounds: `0 < N <= 9_600_000` (ten minutes at 16 kHz). An out-of-range count
makes the helper emit `{"ok": false, "error": "invalid sample count"}` and
read the next 8 bytes as a new header. If the client already wrote the
payload, the stream is desynchronized from that point. The Python client
treats every non-`ok` response as fatal, closes the pipes, and kills the
process, so desync cannot persist across requests. Keep that contract: after
any error response, respawn rather than reuse.

A payload truncated by EOF ends the helper process (it throws
`truncatedAudio`). Clean EOF at a frame boundary exits the serve loop
normally.

## Response schema v1 (what the shipped helper emits)

Success:

```json
{"ok": true, "processing_s": 0.179, "text": "Hello world."}
```

Failure:

```json
{"ok": false, "error": "<description>"}
```

`processing_s` is wall time for the transcription call only (model load time
is reported separately in the ready line). `text` may be empty for silence.
Measured against the current build: a one-sentence response line is about 112
bytes.

## Response schema v2 (planned; additive, optional fields)

v2 keeps every v1 field unchanged and adds two optional fields on success:

```json
{
  "ok": true,
  "text": "Hello world, this is a dictation test.",
  "processing_s": 0.152,
  "avg_confidence": 0.967,
  "words": [
    {"word": "Hello", "start": 0.88, "end": 0.96, "confidence": 0.998},
    {"word": "world,", "start": 1.04, "end": 1.52, "confidence": 0.85}
  ]
}
```

Field semantics:

- `words[].word` — word text with punctuation attached, grouped from
  SentencePiece tokens on their word-boundary markers. Joining `words[].word`
  with single spaces reproduces `text` for typical output, but `text` remains
  the authoritative transcript; do not reconstruct it from `words`.
- `words[].start` / `words[].end` — seconds from the start of the submitted
  clip, rounded to 3 decimals. Resolution is one encoder frame (80 ms:
  `frameSamples 1280 / sampleRate 16000`). These are RNNT emission times, not
  forced alignment: the decoder emits a token once it has heard enough
  context, so a start can sit slightly after the word's true onset, and a gap
  between one word's end and the next word's start is a real pause. The last
  word's end is clamped to the clip duration upstream.
- `words[].confidence` — minimum of the word's token softmax probabilities,
  0..1, rounded to 3 decimals. Uncalibrated model confidence, chosen minimum
  rather than mean so one weak sub-word token marks the whole word.
- `avg_confidence` — mean softmax probability over all emitted tokens, 0..1,
  rounded to 3 decimals. Uncalibrated; matches how FluidAudio's own TDT path
  computes `ASRResult.confidence`.

Compatibility rules:

- Absent `words`/`avg_confidence` means v1 behavior. The helper omits both
  when the transcription produced no tokens.
- Consumers must ignore unknown fields, and must not fail when only some v2
  fields are present.
- Response stays a single JSON line. Measured size with real output: about 81
  bytes per word entry, 1016 bytes for an 11-word sentence. A ten-minute
  maximum-length dictation (~1,500 words at a fast pace) is roughly 125 KiB,
  which exceeds the current Python-side cap `PARAKEET_MAX_RESPONSE_BYTES =
  64 * 1024` (dictate.py:620). Raising that cap (1 MiB is comfortable) must
  land in the same change that adopts v2; until then the cap is safe because
  the shipped helper only emits v1-size lines.

## Python-side parsing guidance

Target dataclasses are in `parrot_core.py:124-142`:

```python
@dataclass
class RecognitionWord:
    text: str
    start: float = 0.0
    end: float = 0.0
    confidence: float = 0.5
    timing: str = "native"

@dataclass
class Recognition:
    text: str
    confidence: float = 1.0
    alternative: str | None = None
    verified: bool = False
    engine: str = ""
    words: tuple[RecognitionWord, ...] = ()
    audio_duration: float = 0.0
    native_processing_s: float | None = None
```

Mapping for `ParakeetClient` / `_parakeet_crosschecked`:

- `text` ← `response["text"]`, stripped (unchanged from today).
- `native_processing_s` ← `response["processing_s"]` (unchanged).
- `words` ← for each entry, `RecognitionWord(text=w["word"].strip(),
  start=float(w["start"]), end=float(w["end"]),
  confidence=min(1.0, max(0.0, float(w["confidence"]))), timing="native")`.
  Skip entries whose word text strips to empty. If any entry is malformed
  (missing key, non-numeric value), drop the whole `words` list and fall back
  to v1 behavior — partial word evidence is worse than none. The downstream
  harvest already validates native timings (monotonic, non-overlapping,
  inside the span; dictate.py around 9646-9677) and demotes bad ones to
  `timing="segment"`, mirroring how Whisper segment interpolation is labeled
  in `recognition_words_from_segments` (parrot_core.py:631).
- `Recognition.confidence` — policy decision for the dictate.py owner, not
  this document. Today the Parakeet route derives confidence from
  cross-engine agreement with a Whisper Tiny decode precisely because the
  helper exposed none (`_parakeet_crosschecked`, dictate.py:6540).
  `avg_confidence` is a direct but uncalibrated signal (mean softmax; RNNT
  softmax probabilities skew high). Reasonable first use: keep the
  agreement-based route confidence and use `avg_confidence` / per-word
  confidence as additional evidence (for example, gating context repair on
  low-confidence words) rather than replacing the agreement signal outright.
- `ParakeetClient.transcribe` currently returns `(text, processing_s)`.
  Adopting v2 means widening that return (or returning the parsed dict) plus
  raising `PARAKEET_MAX_RESPONSE_BYTES`; both belong to the dictate.py owner.

## Why the shipped helper still emits v1

The helper pins FluidAudio `exact: "0.15.5"` and uses `UnifiedAsrManager`
(Parakeet Unified 0.6B, offline 15 s full-attention encoder). In 0.15.5 that
engine's entire batch API is:

```swift
public func transcribe(_ samples: [Float]) async throws -> String
public func transcribe(_ buffer: AVAudioPCMBuffer) async throws -> String
```

Text only. Internally each window decodes to tokens carrying a frame
timestamp and a confidence (`UnifiedRnntDecoder` emissions), but
`transcribe` discards them at `tokenizer.decode`, and the types needed to
rebuild the pipeline (`ChunkProcessor`, `UnifiedRnntDecoder`) are internal
to the module. Verified against the pinned source, tag v0.15.5, revision
`19600a485baa4998812e4654b70d2bab8f2c9949`.

Richer results do exist elsewhere in 0.15.5, but only on other engines:

- `AsrManager` (TDT sliding-window family) returns full `ASRResult` — text,
  mean-softmax confidence, `tokenTimings: [TokenTiming]?` — but runs the
  `parakeet-tdt-0.6b-v2/v3-coreml` models, a different download and a
  different accuracy profile from the unified offline encoder we ship.
- `StreamingUnifiedAsrManager` exposes `consumeTokenTimings()` /
  `consumeWordTimings()` with per-token confidence, but uses the chunked
  streaming encoder export, which FluidAudio's own comments measure at worse
  WER than the offline encoder (2.15% vs 1.82% LibriSpeech test-clean).

Switching engine to gain evidence would change recognition behavior and
model downloads; that is a product decision, not a protocol extension, so v1
stands until the dependency moves.

No FluidAudio engine offers n-best hypotheses in 0.15.5; all transcription
decoding is greedy (the only beam search is the internal CTC keyword spotter
used for vocabulary boosting).

## The upgrade that enables v2 (verified)

Upstream `FluidInference/FluidAudio` main gained exactly the missing API in
PR #814, merged 2026-07-22, commit
`f8529217d468a643c0b77bcf7e382569675b9559` ("Expose token timings from the
Unified offline batch path"):

```swift
public struct TranscriptionWithTimings: Sendable {   // nested in UnifiedAsrManager
    public let text: String
    public let tokenTimings: [TokenTiming]
}
public func transcribeWithTimings(_ samples: [Float]) async throws -> TranscriptionWithTimings
```

`TokenTiming` (unchanged since 0.15.5): `token: String`, `tokenId: Int`,
`startTime: TimeInterval`, `endTime: TimeInterval`, `confidence: Float`.
`transcribe` and `transcribeWithTimings` share one decode path upstream, so
the text cannot drift between them. As of 2026-07-27 no tagged release
contains it; v0.15.5 is the newest tag.

Verified on this machine (Apple toolchain, swift build, debug):

- The unchanged helper builds against the 0.15.5 pin: `Build complete!`.
- A scratch copy of this package pinned to revision `f8529217…` with the v2
  changes below builds clean and, run against the locally cached model
  bundle, transcribes a synthesized test sentence identically to the v1
  build while adding correct `words` and `avg_confidence` (the sample
  response above is its real output).

Costs, measured, of moving the pin to that revision:

- 28 commits of churn past v0.15.5, mostly TTS/diarization/download. The
  ASR-side changes (seam-gap repair #761, final-window end-alignment #800)
  rework `ChunkProcessor`, which the unified batch path uses only when
  merging overlapping windows — audio over 15 s. Utterances up to 15 s
  decode in a single window and are unaffected; long-dictation merges should
  be re-validated after the upgrade.
- Dependency resolution now downloads a binary artifact,
  `NemoTextProcessing.xcframework` (~47 MB) from
  `FluidInference/text-processing-rs` v0.3.0, pulled in by FluidAudio's TTS
  text-normalization work. It links statically: the helper binary grows from
  12 MB to 23 MB (debug) with no new dylibs, so the signed-single-binary
  story is unchanged, but the build acquires a second-repo binary supply
  chain.
- `Package.swift`: one line, `exact: "0.15.5"` →
  `revision: "f8529217d468a643c0b77bcf7e382569675b9559"` (or the next tagged
  release once one exists — preferred; see
  `docs/agents/fluidaudio-upstream-ask.md`).

The verified v2 change to `main.swift`, in full (everything else in the file
stays as it is):

```swift
// In serve(_:), replacing the transcribe call and success emit:
let result = try await manager.transcribeWithTimings(samples)
var response: [String: Any] = [
    "ok": true,
    "text": result.text,
    "processing_s": seconds(started.duration(to: .now)),
]
let tokens = result.tokenTimings.filter {
    !$0.token.isEmpty && $0.token != "<blank>" && $0.token != "<pad>"
}
if !tokens.isEmpty {
    response["words"] = wordEvidence(tokens)
    response["avg_confidence"] = rounded(
        tokens.reduce(0.0) { $0 + Double($1.confidence) }
            / Double(tokens.count))
}
emit(response)

// New helpers:

/// Group SentencePiece tokens into words on their leading-space boundaries
/// (the same rule as FluidAudio's `buildWordTimings`, which drops per-token
/// confidence). A word's confidence is the minimum of its tokens' softmax
/// probabilities: uncalibrated, but conservative for downstream gating.
private static func wordEvidence(_ tokens: [TokenTiming]) -> [[String: Any]] {
    var words: [[String: Any]] = []
    var text = ""
    var start = 0.0
    var end = 0.0
    var confidence = 1.0
    func flush() {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        words.append([
            "word": trimmed,
            "start": rounded(start),
            "end": rounded(end),
            "confidence": rounded(confidence),
        ])
    }
    for token in tokens {
        if token.token.hasPrefix(" ") || text.isEmpty {
            flush()
            text = ""
            confidence = 1.0
            start = token.startTime
        }
        text += token.token
        confidence = min(confidence, Double(token.confidence))
        end = token.endTime
    }
    flush()
    return words
}

/// Three decimals: 1 ms timing resolution (the model's is 80 ms) and a
/// bounded response line for the Python side's size cap.
private static func rounded(_ value: Double) -> Double {
    (value * 1000).rounded() / 1000
}
```

## Appendix: FluidAudio 0.15.5 streaming and end-of-utterance APIs

Reference for a future streaming project; nothing here is used by the
current helper. All paths relative to the FluidAudio repository.

**`StreamingAsrManager` protocol** (`ASR/Parakeet/Streaming/StreamingAsrManager.swift`)
— common surface for streaming engines: `loadModels()`,
`appendAudio(AVAudioPCMBuffer)`, `processBufferedAudio()`,
`finish() -> String`, `reset()`, `cleanup()`,
`setPartialTranscriptCallback((String) -> Void)`,
`getPartialTranscript()`. Optional capability protocols:
`StreamingAsrTokenTimestampProvider` (`getTokenTimestampsMs() -> [Int]`),
`StreamingAsrRawTokenProvider` (`getRawTokenStrings() -> [String]`),
`StreamingAsrEouProvider` (`getEouTimestampsMs() -> [Int]`).

**`StreamingUnifiedAsrManager`** (`ASR/Parakeet/Unified/StreamingUnifiedAsrManager.swift`)
— same model family and HuggingFace repo as our batch engine but a separate
chunked-attention streaming encoder bundle per latency tier (default context
[70, 13, 13] frames = 5.6 s left / 1.04 s chunk / 1.04 s right, ~2.08 s
theoretical latency; `ParakeetModelVariant` tiers 320/640/1120/2080 ms).
Beyond the protocol: `consumeTokenTimings() -> [TokenTiming]` and
`consumeWordTimings() -> [WordTiming]`, draining calls with per-token
confidence, designed for word→speaker attribution. This is the streaming
counterpart of the v2 evidence above.

**`SlidingWindowAsrManager`** (`ASR/Parakeet/SlidingWindow/SlidingWindowAsrManager.swift`)
— TDT models (`parakeet-tdt-0.6b-v2/v3-coreml`) driven as pseudo-streaming
with overlapping windows: `startStreaming(source:)`,
`streamAudio(AVAudioPCMBuffer)`, `finish() -> String`, and a
`transcriptionUpdates: AsyncStream<SlidingWindowTranscriptionUpdate>` whose
updates carry `text`, `isConfirmed`, `confidence`, `timestamp`,
`tokenIds`, `tokenTimings`. Also the vocabulary-boosting host
(`configureVocabularyBoosting`).

**`StreamingEouAsrManager`** (`ASR/Parakeet/Streaming/EOU/StreamingEouAsrManager.swift`)
— dedicated end-of-utterance engine, `parakeet-realtime-eou-120m-coreml`
(cache-aware encoder; 160/320/1280 ms chunk variants, each a separate model
download). Emits incremental text via `process(audioBuffer:) -> String`,
fires `setEouCallback((String) -> Void)` when the model detects an utterance
end (debounced by `eouDebounceMs`, default 1280), and implements all three
provider protocols above. Relevant if push-to-talk ever gives way to
open-mic endpointing.

**Batch with evidence in 0.15.5** — only the TDT `AsrManager`
(`ASR/Parakeet/SlidingWindow/TDT/AsrManager.swift`):
`transcribe(_ audioSamples: [Float], decoderState: inout TdtDecoderState,
language: Language? = nil) async throws -> ASRResult` where `ASRResult` has
`text`, `confidence` (mean token softmax clamped to 0.1..1.0, 0.1 when
empty), `duration`, `processingTime`, `tokenTimings: [TokenTiming]?`.
`buildWordTimings(from: [TokenTiming]) -> [WordTiming]` (public, top-level,
`AsrTypes.swift`) groups tokens into `WordTiming { word, startTime,
endTime }` — note it drops confidence, which is why the v2 helper groups
tokens itself.
