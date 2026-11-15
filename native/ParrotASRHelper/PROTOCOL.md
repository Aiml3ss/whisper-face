# Parrot ASR helper protocol

Wire contract between `dictate.py` (`ParakeetClient`) and the two native
programs that run the Parakeet Unified model:

- the FluidAudio helper, `Sources/parrot-asr-helper/main.swift` here, built
  on the FluidAudio 0.15.5 package. It speaks protocol 1 and is the default;
- Whisper Face's own runtime, `native/ParrotSpeechRuntime`, which loads the
  same model files with no package dependencies. It speaks protocols 1 and
  2 and is used when `PARROT_ASR_RUNTIME=own` ("Which program answers",
  below).

Each is a long-lived child process; audio crosses one pipe in RAM and never
touches disk.

Two protocols are defined. Protocol 1 is what the FluidAudio helper speaks.
Protocol 2 adds each word's time and confidence. The client asks every
program for protocol 2 and reads, reshapes and records the words of one
that answers it.

## Process modes

| Invocation | Behavior |
| --- | --- |
| `parrot-asr-helper --server` (default) | Load models, print one ready line, then serve framed requests from stdin until EOF. |
| `parrot-asr-helper --preload` / `--verify` | Load models, print one status line, exit 0. |
| anything else | Usage line on stderr, exit 2. |

`parrot-speech-runtime` takes the same modes. It refuses an unknown mode
before it loads anything, and before its status or ready line it decodes one
second of silence, so a model file that loads but cannot run fails at start
and never mid-dictation.

Arguments after the mode:

- `--protocol N` asks for protocol N. A program answers with the highest
  protocol it speaks that is not above N, and with protocol 1 when the
  argument is missing or unreadable. The FluidAudio helper reads only its
  first argument, so it ignores this and every other extra argument.
- `--models DIR` names the folder of the pinned Parakeet Unified snapshot
  that Python downloaded and verified (`PARAKEET_MODEL_DIR`). The runtime
  loads from exactly that folder and never downloads; the FluidAudio helper
  ignores it and uses FluidAudio's default folder, which is the same
  folder.

`dictate.py` launches either program as `<program> --server --protocol 2
--models <dir>`, with every `FLUID_*` variable removed from its environment
(FluidAudio reads tuning overrides from them). `setup.sh` runs the
FluidAudio helper with `--preload` and `--verify`, and the runtime with
`--preload` and `--verify`, each followed by `--protocol 2 --models <dir>`.
The runtime is optional while it is off by default: a runtime that does not
build or preload is removed and the install goes on. `--verify` passes when
it is absent and fails when one is present and does not answer protocol 2.

Ready line (server mode), one JSON object terminated by `\n`:

```json
{"load_s": 12.25, "model": "parakeet-unified", "ready": true}
```

Preload/verify line: same fields with `"ok": true` instead of `"ready"`. All
helper output lines are single-line JSON objects with sorted keys. Model load
logs from FluidAudio go to stderr, never stdout; the runtime writes nothing
to stderr except its usage line.

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

Input that ends inside a request ends the process. Measured on 2026-11-15:
the FluidAudio helper decodes whatever part of a cut-off payload arrived,
answers, and exits 0 at the next read; a cut-off header, or a count of 2^63
or more, crashes it. The runtime answers nothing for a cut-off header or
payload and exits 1, and answers `invalid sample count` for any count out of
range. Clean EOF at a frame boundary exits the serve loop normally. The
client never sends any of these: it writes whole requests, and closes the
process after any failure.

## Protocol 1 responses (what the FluidAudio helper emits)

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
A one-sentence response line is about 100 bytes.

## Protocol 2

### Ready and status lines

Protocol 2 adds three keys to the ready line and to the `--preload` /
`--verify` line:

```json
{"capabilities":["words"],"engine":"parrot-speech-runtime-1","load_s":0.14,"model":"parakeet-unified","protocol":2,"ready":true}
```

Python treats a helper as protocol 2 only when `protocol` is the integer 2
and `capabilities` is a list containing `"words"`; anything else is
protocol 1 (`parrot_core.helper_protocol`). `engine` names the program and
its version; Python records nothing from it.

A model folder that is missing or damaged makes the runtime print
`{"error":"model load failed","ok":false}` and exit 1, in every mode and
whatever protocol was asked for. It does not download and does not delete
anything.

### Success response

Keys sorted, one line:

```json
{"avg_confidence":0.913,"ok":true,"processing_s":0.057,"text":"Send $42 to Priya.",
 "words":[{"confidence":0.96,"end":0.96,"start":0.88,"word":"Send"},
          {"confidence":0.99,"end":1.28,"mark_confidence":0.999,"start":1.04,"word":"$42"},
          {"confidence":0.646,"end":2,"start":1.92,"word":"to"},
          {"confidence":0.649,"end":2.32,"mark_confidence":0.586,"start":2.08,"word":"Priya."}]}
```

- `text` is authoritative and is the same text protocol 1 returns for the
  same audio: both come from one decode, and the words are built from the
  token stream that text was decoded from. Do not rebuild it from `words`.
- `words[].word`: SentencePiece tokens grouped on their word-boundary
  markers, punctuation attached.
- `words[].start` / `end`: seconds from the start of the clip, three
  decimals. These are RNNT emission times at the encoder's 80 ms frame
  resolution, not forced alignment: a start can sit after the word's true
  onset, and a gap between one word's end and the next one's start is a
  real pause. Several tokens emitted on one frame get the same start and no
  length. The last end is clamped to the clip.
- `words[].confidence`: the lowest softmax probability among the word's
  tokens that contain a letter or a digit (all its tokens when none do).
  Sentence punctuation is often decoded less surely than the word before
  it, and must not mark a clearly heard word as doubtful.
- `words[].mark_confidence`: the lowest probability among the word's
  punctuation-only tokens, present only when it has some.
- `avg_confidence`: mean probability over all emitted tokens.
- All confidences are uncalibrated model probabilities, three decimals.
- `words` and `avg_confidence` are absent when no token was emitted, and
  `words` is absent above 4,096 words (`WordEvidence.maximumWords` in the
  runtime; the client rejects more than 4,096 entries).
- A line is about 70 bytes per word; a ten-minute dictation stays well under
  the 1 MiB the client accepts (`PARAKEET_MAX_RESPONSE_BYTES`).

Error responses are as in protocol 1. The runtime launched without
`--protocol 2` answers exactly as protocol 1 does: the same ready line, and
responses with exactly the keys `ok`, `processing_s` and `text`.

### Compatibility

| Program \ client | Old client (`--server`) | Current client (`--server --protocol 2 --models DIR`) |
| --- | --- | --- |
| FluidAudio helper (protocol 1) | Protocol 1. | The helper reads only its mode and answers protocol 1; the client sees no `protocol` key and records `v1-helper`. |
| The runtime (protocol 2) | Protocol 1, with exactly the protocol 1 keys. | Protocol 2. |

`tests/test_dictate.py` (`HelperProtocolCompatibilityTests`) pins all four
cells with the fake helper of `tests/fake_platform.py` behind real pipes,
and checks that the FluidAudio helper's source reads only its first
argument; `SpeechRuntimeSourceTests` holds the runtime's side of the
contract in its Swift sources. On a Mac with the model,
`scripts/speech_runtime_parity.py` checks the real programs
(`native/ParrotSpeechRuntime/README.md` records what it printed).

## The Python side (shipped)

- `ParakeetClient.transcribe` returns a `parrot_core.ParakeetResult`: the
  protocol 1 pair `(text, processing_s)`, which every caller unpacks as
  before, carrying the validated words, `avg_confidence` and a status.
- `parrot_core.parse_helper_words` treats the words as untrusted input.
  Anything wrong anywhere (not a list, more than 4,096 entries, a word that
  is not text or is longer than 64 characters, a time that is missing, not
  finite, negative, out of order, ends before it starts or past the clip by
  more than 2 ms, a confidence outside 0..1) discards every word: partial
  evidence is worse than none. The text is never affected.
- `voice_compiler.project_word_evidence` turns the helper's words into the
  compiler's lexical tokens: ends clamped to the clip, a same-frame run
  sharing its span by character count, each word split into its lexical
  tokens ("world," is "world"; "$1,250.50" is "1" and "250.50"). The pieces
  must be strictly ordered, at least 1 ms long, and spell exactly the
  text's lexical tokens; otherwise the decode keeps no words. Times stay
  labelled `native`.
- Each dictation records `word_evidence` (a count) and
  `word_evidence_status` in its transcript metrics: `native`, `v1-helper`,
  `rejected` (malformed words), `misaligned` (words that do not spell the
  text), or `absent` (no words expected or sent). Several decodes in one
  dictation report the worst. `acoustic_confidence` records the lowest
  `avg_confidence`; it is never the route confidence.
- Two switches in `dictate.py` decide what the words may do, both `False`:
  `PROSODY_FORMATTING` (pauses between the helper's timed words may add
  commas and paragraphs) and `PARAKEET_WORD_CONFIDENCE_ROUTING` (a word's
  confidence may clear a name's or a number's review flag). With both off,
  helper words never reach the compiler, so typed text, the consequence
  route and the review cue are exactly what a protocol 1 helper gives
  (`tests/test_whole_program.py`, against
  `benchmarks/replay_text_and_route_baseline.json`). With only
  `PROSODY_FORMATTING` on, the words place spans in time but every span
  stays flagged `word-confidence-uncalibrated`. Turn routing on only once
  recordings of the owner's own voice show the confidence predicts errors.

### Which program answers

The **New speech runtime** switch in the window (Advanced → Models), saved
as `new_speech_runtime` in `preferences.json`, chooses. It is off unless the
file says `true`, and a change applies from the next dictation with no
restart (`apply_speech_runtime` in `dictate.py` sets which client is asked
first; switched off, the runtime is closed). `PARROT_ASR_RUNTIME` in the
service's environment, when it is set to anything, wins over the switch:
`own` (case and surrounding space ignored) selects the runtime, any other
value the helper, and the switch then shows what runs and changes nothing.
Off is the default, `fluidaudio`: the FluidAudio helper alone, exactly as
before the runtime existed, and the runtime is never started. The switch's
caption says which program answered the last dictation once one has, and
that it is still getting ready while it loads.

The runtime starts in the background: at app start and the moment the
switch is turned on, and again when a request finds it not running (after
a bench, or a crash). No request waits for it. While it loads, every
request goes to the helper exactly as with the switch off; once its ready
line arrives, the next request goes to it. A load is given
`PARAKEET_RUNTIME_START_ALLOWANCE` (60 s), not the helper's
`PARAKEET_STARTUP_TIMEOUT` (10 s): with Core ML's compiled-model cache cold
a first load takes about 10 s (measured at 10.13 s, then 0.13 s), and
stopping it then leaves the cache cold for the next try. The FluidAudio
helper still starts inside the request, within its 10 s, as before.

With the runtime selected, `PARAKEET` (the FluidAudio helper's client) asks
`OWN_PARAKEET` (the runtime's) first for every request:

- not built: the helper answers, and the log says so once;
- still loading: the helper answers, and the load goes on;
- does not start (no ready line within the allowance, the
  `model load failed` line, or an exit): it is stopped, the helper
  answers, and the runtime is not started again for 30 s, doubling with
  each failure in a row, up to 5 minutes;
- fails a request (an error line, a timeout, a malformed or oversized
  response): it is closed, the helper decodes the same audio for the same
  dictation, and the runtime is benched the same way. Starting again does
  not clear the count, only an answer does, so a runtime that starts and
  then fails every request waits 30 s, 60 s, 120 s and so on, up to 5
  minutes, as one that never starts does;
- answers with empty text: that is the answer. Both programs run the same
  model and give the same text, so the helper is not asked to decode the
  same audio again;
- answers again after a fallback: the helper is closed, so the two are not
  both held in memory until the app restarts; the next fallback starts it
  again;
- the helper's own failures fall to Whisper, as they always have.

The Parakeet route itself still requires the FluidAudio helper to be
installed (`setup.sh` requires it; the runtime is optional), so the runtime is only ever asked
where its fallback exists. To try it on an installed Mac, turn on New speech
runtime under Advanced, or from a shell:

```sh
launchctl setenv PARROT_ASR_RUNTIME own
launchctl kickstart -k "gui/$(id -u)/com.berg.dictate"
```

and `launchctl unsetenv PARROT_ASR_RUNTIME` with the same kickstart to go
back. `dictate.log` names the program that started ("Parakeet Unified ready
in 0.14s (Whisper Face's own runtime)") and every fallback.

## Why protocol 2 comes from Whisper Face's own runtime

The FluidAudio helper pins `exact: "0.15.5"`, whose `UnifiedAsrManager` has
only `transcribe(_:) -> String`. Later FluidAudio releases report token
timings, but cannot be built without fetching a prebuilt third-party binary:
measured with Swift 6.4.2 on 2026-11-15, with every SwiftPM command run with
outbound connections blocked, `.package(..., exact: "0.18.3", traits: [])`
keeps TTS (and its GPL-derived LuxTTS G2P data), the diarizers and NeMo text
normalisation out of the compiled helper, but `swift package resolve` and
`swift build` still try to download `NemoTextProcessing.xcframework.zip`,
which FluidAudio's own manifest declares, and fail when they cannot. On a
toolchain older than 6.2 all three would be linked. Neither meets the
project's licence and supply-chain rule, so the FluidAudio helper stays on
0.15.5.

Protocol 2 is spoken instead by `native/ParrotSpeechRuntime`, Whisper
Face's own program for the same model files. It has no package
dependencies, so building it fetches nothing; it loads the Core ML files
from the folder it is given and does the rest with Foundation, Core ML and
Accelerate: log-mel features, the encoder over 15 s windows, the greedy
transducer decode loop, tokens to text, the merge of overlapping windows,
and the word timings and confidences above. The parts that reproduce
FluidAudio 0.15.5's behaviour are ported from its Apache-2.0 source and say
so; `native/ParrotSpeechRuntime/README.md` lists them, records the parity
measurements against the FluidAudio helper and the proof that the runtime
cannot reach the network, and says how to rebuild both.

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
