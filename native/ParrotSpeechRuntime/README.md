# Parrot speech runtime

`parrot-speech-runtime` is Whisper Face's own program for running the
Parakeet Unified 0.6B model. It needs no third-party Swift package: it loads
the pinned Core ML files from the folder it is given and does everything
else with Apple's Foundation, Core ML and Accelerate. It speaks the wire
protocol of the FluidAudio helper beside it (`native/ParrotASRHelper`,
`PROTOCOL.md` there): protocol 1 byte for byte when launched the old way,
protocol 2, each word's time and confidence, when asked, and protocol 3,
sessions that decode a long take window by window while it is captured.

It exists because the FluidAudio library the helper is built on cannot be
upgraded cleanly: its newer releases fetch a prebuilt binary to build,
whatever is switched off. Rather than fork the library or ask its authors
for a change, Whisper Face runs the model itself.

## What it does

For each request (16 kHz mono Float32 over stdin, as for the helper):

1. Cuts the audio into 15 s windows that overlap by 2 s (one window up to
   14.96 s).
2. Computes 128-bin log-mel features for each window with Accelerate
   (n_fft 512, 25 ms Hann window, 10 ms hop, pre-emphasis 0.97, per-feature
   normalisation), exactly as NeMo's preprocessor and FluidAudio do.
3. Runs the int8 encoder on the CPU and Neural Engine.
4. Runs the greedy transducer loop on the CPU: the prediction network and
   the joint network, one token at a time, up to 10 tokens a frame. The
   token taken is the joint network's own `token_id`.
5. Merges the windows' token streams on their overlap, collapses seam
   duplicates, and turns tokens into text.
6. In protocol 2, turns the tokens' emission frames into word timings and
   confidences (`PROTOCOL.md`, "Protocol 2").
7. In protocol 3, keeps a session: it decodes each window as soon as the
   audio appended so far covers it, merges it onto the windows before it,
   and at `finish` decodes the rest and answers for the whole recording
   (`PROTOCOL.md`, "Protocol 3: sessions").

At start it loads the four model files and decodes one second of silence,
so a model that loads but cannot run fails before the ready line, not in
the middle of a dictation.

| File | What | Origin |
|---|---|---|
| `main.swift` | Modes, model load, the serve loop, the debug candidate dump | Whisper Face, after `native/ParrotASRHelper`'s `main.swift` |
| `Wire.swift` | Arguments, framing, session operations, JSON lines | Whisper Face |
| `Recognizer.swift` | Model files, compute units, windows, the merged transcript; protocol 3 sessions (`Session`) | Ported from FluidAudio 0.15.5; `Session` is Whisper Face's |
| `MelFeatures.swift` | Log-mel features | Ported from FluidAudio 0.15.5 |
| `TransducerDecoder.swift` | The decode loop and the joint network's candidates | Ported from FluidAudio 0.15.5 |
| `WindowMerge.swift` | Joining overlapping windows | Ported from FluidAudio 0.15.5 |
| `Vocabulary.swift` | Token ids to text | Ported from FluidAudio 0.15.5 |
| `WordEvidence.swift` | Protocol 2 words | Token timing rule ported from FluidAudio 0.18.3; word grouping is Whisper Face's |

Each ported file names the FluidAudio files, tag and commit it came from
and what was changed. FluidAudio is Apache-2.0; its licence text is
`LICENSE-Apache-2.0.txt` here, and `THIRD_PARTY_NOTICES.md` lists the ports.
Arithmetic that decides a transcript (the features, the window layout, the
decode loop, the merge) is kept in the library's order with the same calls,
which is why the transcripts are identical.

## How it ships

`setup.sh` builds it with `swift build -c release` into
`.models/swift-build-runtime`, installs it as `.models/bin/parrot-speech-runtime`,
and runs `--preload --protocol 3 --models <model folder>`. The build fetches
nothing. While the runtime is off by default, a failure in any of those three
steps does not stop the install: setup prints one line saying so, removes the
binary, and goes on, and the app uses the FluidAudio helper. `--verify` says
whether the runtime is installed; one that is installed must answer protocol 3
for that folder, and one that is absent passes.

`dictate.py` keeps using the FluidAudio helper by default. With the New
speech runtime switch on (Advanced → Models, saved as `new_speech_runtime`),
or `PARROT_ASR_RUNTIME=own` in the service's environment, which wins when it
is set, it asks the runtime first; a runtime that is not built, does not start, or fails a request
hands the same audio to the FluidAudio helper, and the helper falls back to
Whisper as it always has. `PROTOCOL.md`, "Which program answers", has the
details and how to switch it on and off. The default is to change only
after the owner has used it.

## It cannot reach the network or change a file

Measured on this Mac (macOS 27.3, Apple M4 Pro, Swift 6.4.2) on
2026-11-15, building commit `db37cbe` and later.

- **Building it fetches nothing.** `Package.swift` declares no dependency,
  so there is no `Package.resolved`. A release build from the committed
  tree, run under `sandbox-exec` with every network operation denied and
  with empty SwiftPM cache, configuration and security folders, completed
  in about 5 s and left nothing in them but SwiftPM's own manifest cache.
- **It links no networking API.** `otool -L` lists Foundation, Accelerate,
  Core ML, `libobjc`, `libSystem` and the Swift runtime and overlays, and no
  CFNetwork or Network framework. Its undefined C symbols are Accelerate's
  `vDSP_*`, `logf`, `cosf`, `expf`, `read`, `exit` and memory functions; its
  Objective-C classes are `MLModel`, `MLModelConfiguration`,
  `MLMultiArray`, `MLFeatureValue`, `NSFileManager`, `NSFileHandle`,
  `NSJSONSerialization`, `NSNumber` and `NSDecimalNumber`. None of
  `URLSession`, `NSURLConnection`, `CFNetwork`, `socket`, `connect`,
  `getaddrinfo`, `download` or `http` appears anywhere in the binary, nor
  any selector that removes, moves or writes a file (`removeItem`,
  `moveItem`, `unlink`, ...). The only URL it builds is
  `URL(fileURLWithPath:)` for the model files. For comparison, the
  FluidAudio helper links CFNetwork and its binary names `URLSession` 42
  times and `download` 88 times. The runtime's binary is 232 KB; the
  helper's is 7.0 MB.
- **It writes no file; Core ML keeps a compiled copy of the model.** The
  first load of a model folder makes Core ML compile it and keep the result
  under the program's name, `~/Library/Caches/parrot-speech-runtime`
  (about 17 MB per model folder), exactly as it does for the FluidAudio
  helper under `~/Library/Caches/parrot-asr-helper`. `setup.sh --uninstall`
  removes the runtime's. The runtime's own code opens nothing for writing.
- **It runs with the network and every file write denied.** Under
  `sandbox-exec -p '(version 1)(allow default)(deny network*)(deny
  file-write*)'` it loads the model, decodes, and answers `--verify
  --protocol 2` normally.
- **A missing or broken model is a clean failure.** Each case below was
  run in `--verify` and `--server` mode, with the network denied, against
  an APFS clone of the model folder. Every one printed exactly
  `{"error":"model load failed","ok":false}`, wrote nothing to stderr,
  exited 1, and left every file in the folder byte-identical (hashed before
  and after):

  | Model folder | Time to fail |
  |---|---|
  | encoder missing, vocabulary missing, empty folder, no folder | 0.03–0.04 s |
  | encoder weights truncated to 1 MB | 0.22–0.24 s |
  | `vocab.json` not valid JSON | 9.8 s the first time (Core ML compiles the encoder for a folder it has not seen), 0.12 s after |
  | joint network weights replaced with random bytes | 9.9 s, then 0.15 s |
  | decoder program replaced with text | 9.8 s, then 0.14 s |
  | `vocab.json` of the wrong size (1, 1,023 or 1,025 entries), or with an id that is negative or not a number | 10.9–11.3 s, the first time for each folder |

  A vocabulary of the wrong size used to load, answer protocol 2 and then
  decode every request to empty text, which would have sent every
  dictation to Whisper. It must now name each of the model's 1,024
  non-blank token ids exactly once (the blank id is 1,024), or the start
  fails. The check runs after the model files load, so a first load of
  such a folder still pays Core ML's compile.

  With every file write denied as well, the outcomes were the same, but a
  first load of an unseen folder took about 220 s instead of 10 s (Core ML
  could presumably not keep its compiled copy).
- **Gate tests hold it.** `tests/test_network_egress.py`
  (`SpeechRuntimeReachTests`) reads every source file: imports only
  Foundation, Core ML and Accelerate; no networking, process-starting or
  file-changing API named, including file handles opened by path, the C
  library's `remove`, `rename`, `truncate` and `mkdir`, and any class,
  selector or symbol looked up by a name built at run time
  (`NSClassFromString`, `dlsym`, `@_silgen_name`, ...); every URL from a
  file path. `tests/test_supply_chain_integrity.py`
  (`SpeechRuntimePackageTests`) holds the manifest to no dependencies, the
  FluidAudio helper's tools version and platform, and no other manifest or
  resolved file.
- **A hosted Mac builds it on every pull request.** The macOS gate job
  (`.github/workflows/macos-gate.yml`) runs the same `swift build -c
  release` as `setup.sh` and fails if the binary links CFNetwork, the
  Network framework or libcurl, or imports a symbol that opens, removes or
  renames a file, connects, starts a program, or looks a class or symbol up
  by name. `SpeechRuntimePackageTests` runs that step's script against
  stand-in tools to show each of those fails it.
- **An edit that could change a transcript cannot pass unmeasured.**
  `tests/test_dictate.py` (`SpeechRuntimeSourceTests`) hashes `Package.swift`
  and every Swift source and fails unless the digest equals the `sources:`
  line recorded below from the last parity run. Changing anything that is
  compiled means rerunning `scripts/speech_runtime_parity.py` and recording
  what it prints.

## Parity with the FluidAudio helper

The comparison is `scripts/speech_runtime_parity.py`. The reference was
today's helper: `native/ParrotASRHelper`'s `main.swift` built against an
unmodified checkout of FluidAudio v0.15.5 (`19600a48`). Both ran against a
clone of the pinned model files, whose hashes equal those recorded from the
installed model, never the installed folder itself, each under a
network-denying sandbox. The clips:

- the 76 sentences of `benchmarks/recognizer_transcripts.json`, spoken by
  `say` with the system voice (`--replay`);
- 375 clips of speech made on this Mac with 18 system voices at four rates
  (names, numbers, money, dates, short replies, technical dictation, 58 of
  them over 15 s, up to 36.7 s), and 6 more recorded the same way, from the
  helper protocol 2 work;
- 53 clips built to stress the arithmetic: lengths of 1 to 8,000 samples,
  lengths either side of every window boundary (239,360, 240,000, 446,720
  and 653,440 samples, from two starting points), 45 s to 10 min of
  continuous speech, silence, noise, speech 8 times too loud, 100 times too
  quiet, with a DC offset, and a square wave.

What it printed on this Mac, rerun after the vocabulary check was added,
for the first two groups. These three records were made on the sources
before protocol 3 (`sha256:b75b973d...`, the script's `sources:` line,
shown here as `sources (before protocol 3):`); protocol 3 changed how the
windows are called, not what they compute, and "Sessions (protocol 3)"
below records the run on the current sources against the runtime built
from these, on every kind of clip listed here:

```
sources (before protocol 3): sha256:b75b973d02aeb0aed84e53db56870c8cdce3132a9434c3a59a240d74064d7a1c
clips: 457 (398 of 15 s or less, 59 longer), 2958 s of audio
15 s or less: identical 398 of 398
longer than 15 s: identical 59 of 59
candidate protocol 1 answers with exactly ok, processing_s, text: 457 of 457
candidate protocol 2 text equal to its protocol 1 text: 457 of 457; with words: 456
differences: 0
latency, ms (processing_s / round trip):
  reference    p50   43.93 /   44.14   p95  158.24 /  159.07
  candidate-1  p50   43.36 /   43.50   p95  161.39 /  161.69
  candidate-2  p50   43.25 /   43.43   p95  157.62 /  158.57
  paired candidate-1 minus reference, round trip: p50 -0.67  p95 +2.52
  paired candidate-2 minus reference, round trip: p50 -0.75  p95 +2.21
peak memory, MiB (maximum resident set size / peak footprint):
  reference      641.4 /    72.8
  candidate-1    643.2 /    69.9
  candidate-2     73.7 /    67.3
```

(The first two processes found Core ML's cache cold and compiled the model,
which is the 641 MiB resident peak; the footprint is unaffected.) For the
stress clips:

```
sources (before protocol 3): sha256:b75b973d02aeb0aed84e53db56870c8cdce3132a9434c3a59a240d74064d7a1c
clips: 53 (30 of 15 s or less, 23 longer), 1941 s of audio
15 s or less: identical 30 of 30
longer than 15 s: identical 23 of 23
candidate protocol 1 answers with exactly ok, processing_s, text: 53 of 53
candidate protocol 2 text equal to its protocol 1 text: 53 of 53; with words: 40
differences: 0
```

and for 167 clips an independent review made with 24 other voices and its
own sentences (names, money, a phone number, version strings, an email
address), lengths either side of each window boundary, sentence chains up
to 53 s, the full ten minutes, silence, noise, clipping, very quiet speech
and 1 to 8,000 samples:

```
sources (before protocol 3): sha256:b75b973d02aeb0aed84e53db56870c8cdce3132a9434c3a59a240d74064d7a1c
clips: 167 (110 of 15 s or less, 57 longer), 3049 s of audio
15 s or less: identical 110 of 110
longer than 15 s: identical 57 of 57
candidate protocol 1 answers with exactly ok, processing_s, text: 167 of 167
candidate protocol 2 text equal to its protocol 1 text: 167 of 167; with words: 145
differences: 0
```

On that set the paired round-trip difference has a median of -0.7 to
-0.9 ms, but its p95 was +5 to +9 ms in two runs, from a spread about
equally wide on both sides (p5 -7.8 to -8.5 ms), mostly on clips over 15 s.
The Mac was shared with other work at the time (load average 4 to 9). The
same clips paired the runtime built before the vocabulary check against
the one after, two builds whose decode code is identical: median +0.03 ms,
p5 -5.8 ms, p95 +6.6 ms. That spread is the measurement, not the runtime;
the warm-path benchmark below is the latency bar.

The one speech clip without words is "Um.", which both programs decode to
empty text; the 13 stress clips without words (under 0.1 s, silence, noise,
the square wave) decode to empty text from both. Every transcript, short and long, is identical: the
runtime keeps FluidAudio 0.15.5's window layout and merge, so no seam
difference arises. (The 0.18.3 build measured earlier changed three long
transcripts at the first seam; this runtime does not.)

Its protocol 2 answers for the 375 speech clips and the 53 stress clips,
run through the shipping `parrot_core.parse_helper_words` and
`voice_compiler.project_word_evidence`, gave usable words (`native`) for
all 414 decodes with text; none was rejected or misaligned, and the 14
without text carry no words. The largest answer, for the ten-minute clip,
was 98 KB, a tenth of the client's 1 MiB limit.

`benchmarks/recognizer_replay.py record` with the runtime as `--helper`,
launched the old way (`--server` only), printed "no transcript changed since
the last recording" for all 76 cases, as it did for the FluidAudio helper.

Answers differ only for input the app never sends. Measured with each
program: a count of zero or over ten minutes gets `invalid sample count`
from both. A count of 2^63 or more crashes the helper; the runtime answers
`invalid sample count`. A header cut off by the end of input crashes the
helper; the runtime exits 1. A payload cut off part way is decoded by the
helper from whatever arrived; the runtime answers nothing and exits 1.
Samples that are NaN or infinite give empty text from both.

## Sessions (protocol 3)

A session decodes a recording while it is still being captured. The
runtime keeps only the audio it has not decoded: each 15 s window is
decoded the moment the appended audio covers it (14.96 s, then every
12.96 s) and merged onto the windows before it with the same
`Recognizer.merge` that a single request uses, in the same order; `finish`
decodes the last window, which may be short, and finishes the transcript
with the same `Recognizer.finished`. Each window is decoded alone, from a
fresh decoder state, so its tokens cannot depend on audio after it: the
answer is the single request's answer by construction, and the parity
script's `--session` mode measures it.

Measured on this Mac (macOS 27.3, Apple M4 Pro) on 2026-11-17 with
`scripts/speech_runtime_parity.py --replay --session`, the reference being
this runtime built from the sources before protocol 3 (which matched the
FluidAudio helper on every clip above) and the candidate the current
sources, against an APFS clone of the pinned model files, network denied.
The clips: the 76 replay sentences; 75 clips of continuous speech made from
those and eight more sentences with 13 system voices at four rates, three
of each length from 15.5 s to 120 s and one each of 3, 5 and 10 minutes;
29 lengths either side of every window end and every point where a new
window starts to be needed, up to five windows; 10 stress clips (one
sample, 100 samples, 20 s of silence, 30 s of noise, speech 8 times too
loud and 100 times too quiet, a DC offset, a square wave, silence before
and after speech); and 240 short clips, made-up names and control
sentences, spoken by three system voices. The session sends each clip in appends of 8,000, 1, 512, 23,999,
160,000, 4,801 and 239,361 samples in turn, so window ends fall inside
appends of every size.

```
sources: sha256:8bd84eabb483afe8a86fab0632bd270638a44dac05486b156804e75758eeef1d
clips: 430 (325 of 15 s or less, 105 longer), 7363 s of audio
15 s or less: identical 325 of 325
longer than 15 s: identical 105 of 105
candidate protocol 1 answers with exactly ok, processing_s, text: 430 of 430
candidate protocol 2 text equal to its protocol 1 text: 430 of 430; with words: 425
differences: 0
candidate protocol 3 session text and words equal to its protocol 2 answer: 430 of 430 (325 of 325 of 15 s or less, 105 of 105 longer)
session finish against the whole clip in protocol 2, processing_s, ms:
  15 s or less  (325 clips) finish p50   38.56 p95   40.92 max   70.62   whole p50   38.78 p95   42.18 max  124.15
  15 to 30 s    ( 34 clips) finish p50   42.85 p95   70.18 max   71.17   whole p50  131.93 p95  185.55 max  186.94
  30 to 120 s   ( 68 clips) finish p50   49.73 p95   69.49 max   71.79   whole p50  317.54 p95  640.90 max  679.26
  over 120 s    (  3 clips) finish p50   62.37 p95   69.98 max   69.98   whole p50 1613.37 p95 3250.89 max 3250.89
latency, ms (processing_s / round trip):
  reference    p50   39.32 /   39.42   p95  455.20 /  456.55
  candidate-1  p50   39.43 /   39.51   p95  459.65 /  460.41
  candidate-2  p50   39.41 /   39.55   p95  465.78 /  468.19
  paired candidate-1 minus reference, round trip: p50 +0.06  p95 +3.92
  paired candidate-2 minus reference, round trip: p50 +0.12  p95 +7.06
peak memory, MiB (maximum resident set size / peak footprint):
  reference      135.2 /   118.1
  candidate-1    127.2 /   113.6
  candidate-2    131.9 /   111.1
  candidate-3     67.0 /    70.7
```

Text and words are identical on every clip, for one request and for a
session. What is left for key release, `finish`, takes at most one window
and its merge: 70 ms at p95 whatever the length, where one request for the
whole take grows with it (0.64 s at p95 for 30 s to 2 minutes, 3.3 s for
ten minutes on this run). A session holds at most one window of audio, so
the process that answered only sessions peaked at a 71 MiB footprint
against 111 to 118 MiB for the ones that took the ten-minute clip in one
request. (The paired p95 differences are the spread of the 10-minute and
5-minute decodes on a shared Mac; the medians agree within 0.12 ms.)

## Latency and memory

`benchmark_macos_asr_warm_path.py --run --samples 100 --duration 5` (the
warm-path benchmark, synthetic non-speech, 100 requests of 5 s after one
warm-up), three rounds each, interleaved, `current-two-write` framing:

| Round | FluidAudio helper p50 / p95 / max, ms | Runtime p50 / p95 / max, ms |
|---|---|---|
| 1 | 37.69 / 41.62 / 319.02 | 36.72 / 37.29 / 41.52 |
| 2 | 37.34 / 40.58 / 44.38 | 36.48 / 36.80 / 37.71 |
| 3 | 37.27 / 39.97 / 44.55 | 36.70 / 37.54 / 41.25 |

The runtime's p95 is 2.4 to 4.3 ms lower; the bar was "no worse than 5 ms
slower". Rerun with the vocabulary check (`sources:` above):

| Round | FluidAudio helper p50 / p95 / max, ms | Runtime p50 / p95 / max, ms |
|---|---|---|
| 1 | 39.00 / 39.98 / 40.58 | 38.45 / 39.04 / 41.32 |
| 2 | 38.60 / 41.96 / 419.42 | 38.46 / 39.24 / 39.76 |
| 3 | 37.35 / 41.80 / 353.94 | 36.69 / 36.99 / 38.07 |

The runtime's p95 is 0.9 to 4.8 ms lower. On the 457 speech clips above, round-trip p95 is 143.35 ms against
143.28 ms (clips of 15 s or less: 60.12 against 60.00), p50 39.49 against
40.18, and the per-clip paired difference has median -0.55 ms. On the stress
clips, whose ten-minute decode dominates, p95 is 651 ms against 665 ms.

Loading a warm model took 0.12–0.19 s in the final runs, against the
helper's 0.08–0.21 s; the runtime's includes the one-second decode of
silence. A first load for a
model folder Core ML has not compiled for takes about 10 s for either
program; `setup.sh --preload` pays it at install time, before the
dictation service is started. A review measured 9.5–10.1 s for the runtime
and 10.2–11.9 s for the helper from a cold cache, and the owner's Mac
10.13 s for the runtime after an install. The helper's start-up limit
(`PARAKEET_STARTUP_TIMEOUT`, 10 s) is too short for that, so the runtime
does not use it: it loads in the background, with
`PARAKEET_RUNTIME_START_ALLOWANCE` (60 s), while the helper answers, and
takes over once it is ready; warm-up waits for that load so the helper is
not started cold beside it. The helper keeps its 10 s limit for the request
that starts it, but a start that passes it with nothing read goes on
loading in the background for the same allowance instead of being stopped.
Core ML keeps its compiled copy per program name and per macOS build, so
every macOS update makes the first load of either program cold.

Peak memory, from `/usr/bin/time -l` around each process for a whole run:

| Run | FluidAudio helper, max RSS / peak footprint | Runtime, protocol 1 | Runtime, protocol 2 |
|---|---|---|---|
| 457 speech clips | 88.9 / 89.1 MiB | 73.1 / 73.0 MiB | 71.2 / 72.6 MiB |
| 53 stress clips, up to 10 min | 227.4 / 264.5 MiB | 113.0 / 144.1 MiB | 113.8 / 144.9 MiB |

An early build of the runtime read each request through `FileHandle` and
never drained the autorelease pool between requests, so its footprint grew
with every request (307 MiB over the 457 clips). Requests are now read
straight into the sample buffer and each request runs in its own pool;
over 60 requests its resident memory stayed at 58–59 MiB, where the
helper's settled at 45–62 MiB.

## The joint network's candidates

At every step the joint model computes its 64 best tokens and their logits
(`top_k_ids`, `top_k_logits`). FluidAudio throws them away. Here they stay
reachable inside the decode loop: `TransducerDecoder.stepObserver`, when
set, is called after every joint step, blank steps included, with a
`DecodeStep` (window, frame, symbol, the token taken, its probability, and
the `JointCandidates`). The observer gets a copy and returns nothing; the
decoder has already taken the model's own choice. Nothing in a release build
sets it. This is where later work on favouring the user's vocabulary while
decoding starts, and nothing in this version uses the candidates to change
a transcript (`tests/test_dictate.py`, `SpeechRuntimeSourceTests`).

A debug build can dump them for one clip:

```sh
swift build -c debug --package-path native/ParrotSpeechRuntime \
    --scratch-path /tmp/runtime-debug
python3 -c "import numpy as np; np.load('clip.npy').astype('<f4').tofile('clip.f32')"
/tmp/runtime-debug/debug/parrot-speech-runtime --dump-candidates \
    --audio clip.f32 --models "$HOME/Library/Application Support/FluidAudio/Models/parakeet-unified-en-0.6b"
```

It prints one JSON line per joint step (`window_start_frame`, `frame`,
`symbol`, `chosen`, `chosen_piece`, `probability`, and `ids`, `pieces` and
`logits` of the 64 candidates, best first), then `{"done":true,"text":...}`.
A release build answers `--dump-candidates` with its usage line and exit 2.

A step from the clip "Our CEO, Dana, approved the budget." (three of the
64 candidates shown):

```json
{"chosen":112,"chosen_piece":"▁C","frame":13,"ids":[112,1024,67],"logits":[-1.831,-9.953,-11.641],"pieces":["▁C","<blank>","▁S"],"probability":1,"symbol":0,"window_start_frame":0}
```

On that clip there were 64 joint steps, 46 of them blank, and the model's
first candidate was the token taken at every step.

## Rebuilding and rerunning the comparison

On a Mac with the model installed and both programs built by `setup.sh`:

```sh
uv run scripts/speech_runtime_parity.py --replay
uv run scripts/speech_runtime_parity.py --replay --corpus <folder of .npy clips or recordings>
uv run scripts/speech_runtime_parity.py --replay --session --corpus <folder>
```

`--home DIR` points both programs at a copy of the model files under
`DIR/Library/Application Support/FluidAudio/Models` instead of the installed
one; each program runs under `sandbox-exec` with the network denied.
