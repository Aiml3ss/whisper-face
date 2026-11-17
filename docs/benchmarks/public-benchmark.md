# The public benchmark

One command measures Whisper Face on a corpus anyone can rerun, and one more
turns the result into a page. This page says what is measured, how, and what
the numbers cannot show. The corpus is
[`benchmarks/public_benchmark_corpus.json`](../../benchmarks/public_benchmark_corpus.json),
the command is [`benchmark_public.py`](../../benchmark_public.py), and the page
is rendered by `public_scorecard.py benchmark`.

Version 1 measures Whisper Face only. No other app is in it, and nothing is
published about another app until the rule in
[Adding other apps](#adding-other-apps-later) is met.

## Running it

On an Apple Silicon Mac with the model and the speech runtime built:

```sh
uv run benchmark_public.py check
swift build -c release --package-path native/ParrotSpeechRuntime --scratch-path <build>
uv run benchmark_public.py run --runtime <build>/release/parrot-speech-runtime \
    --models <folder>/parakeet-unified-en-0.6b --out <folder outside the checkout>
uv run public_scorecard.py benchmark <out>/result.json --output <out>/scorecard.md
```

`setup.sh` installs the same runtime as `.models/bin/parrot-speech-runtime`
and the model under `~/Library/Application Support/FluidAudio/Models`, so an
installed Mac can pass those. `run` writes `result.json` and `scorecard.md`
itself; the last line renders the page again from a result, and refuses one
that breaks a rule below. The run takes about twice the corpus's audio
length (the app path plays each take in real time, once per configuration)
plus a minute.

`check` needs only a clone. `run` refuses to start when:

- the checkout has uncommitted changes (the revision would not describe the
  code measured);
- `--out` is inside the checkout (the audio it keeps is never committed);
- the model folder's download records do not name the pinned revision in
  `benchmarks/model_scorecard.json`;
- the corpus breaks one of its own rules.

## The corpus

116 takes, each one sentence or paragraph:

| Category | Takes | What it covers |
|---|---:|---|
| `names` | 30 | People and places with spellings a recognizer has to know: Siobhan, Niamh, Tadhg, Xochitl, Wojciech, Kirkcudbright, Llandudno |
| `terms` | 20 | Product and technical terms: Kubernetes, PostgreSQL, nginx, OAuth; and six invented product names nobody's model has heard |
| `near_miss` | 20 | Ordinary sentences that sound like a name or term without saying it ("add a column", "terra firma", "a fee on late returns"); any glossary term written here is a false insertion |
| `numbers` | 20 | Money, times, dates, percentages, phone numbers, versions, units; each with the written forms that count as right |
| `everyday` | 20 | Ordinary dictation, taken word for word from `benchmarks/default_path_sentences.json` |
| `long` | 6 | Paragraphs of 64 to 104 words (about 20 to 40 seconds), longer than one 15-second decoding window, mixing names, terms and numbers |

The glossary lists the 54 names and terms. Each take says which of them it
contains, and the corpus check fails if a take says one it does not declare.
For names whose spelling misleads a synthesizer, the take carries a `said`
respelling ("Neev" for Niamh) so the voice says the name the way its owners
do; the reference is always the real spelling.

**Speech.** Each take is spoken by `say` at 180 words a minute into a 16 kHz
mono file, with 0.3 s of silence added either side, as a person leaves while
holding the key. The voices are the six English voices that ship with macOS,
one per accent: Samantha (US), Daniel (UK), Karen (Australia), Moira
(Ireland), Rishi (India) and Tessa (South Africa). Each voice speaks a sixth
of the takes, which ones fixed by the seed, **20261117**. The same seed fixes
the order of every timed pass. `say` writes the same bytes for the same
voice, text and macOS build, and the result records each take's audio hash,
so a rerun can show it heard the same audio.

**Licence.** The sentences are first-party text under AGPL-3.0-only, like
the rest of the repository. No audio is distributed: it is made on the
machine that runs the benchmark, by that machine's system voices, whose
output is governed by Apple's macOS licence and not by this repository.
Read that licence before publishing any audio; the results and the page
contain text and numbers only.

**Privacy.** Every sentence was written for this corpus or already in the
repository. Every name is public (a product) or invented. There is no
recording, no owner data and no dictation history in it, and no owner data
may ever be added.

## What is measured

The run measures the app twice, on the same audio.

**1. The runtime alone, in process** (`runtime-in-process`). Each take is
sent whole to Whisper Face's own speech runtime, framed as the app's client
frames it (protocol 2), with the network denied by `sandbox-exec`. One
untimed decode warms the model, then the corpus is decoded in three passes,
each in its own seeded order. A take that decodes to different text on a
repeat stops the run. Latency is each take's median over the passes, from
writing the request to reading the answer.

**2. The app path, on the fake Mac** (`app-on-fake-mac`). The real
`dictate.py` is booted in the benchmark's process on `tests/fake_platform.py`,
the harness the release gate uses, as a fresh install with the "New speech
runtime" switch on and the same runtime binary as its speech program (also
sandboxed). For each take the capture key is held, the audio is played into
the fake microphone in 32 ms blocks at real time, and the key is released.
The app cuts the take at pauses, decodes the pieces while the key is held,
compiles the text and pastes it into a fake text field, exactly as for a
person. This runs twice: `defaults` (a fresh install) and `dictionary` (the
glossary given to the app's own dictionary through the same call the
Personalize window makes).

| Measure | Definition |
|---|---|
| Word error rate | Word edits over reference words, summed over takes. Case, punctuation and number grouping are ignored (`1,250` = `1250`, `25%` = `25 percent`, `p.m.` = `pm`); other differences in how a number is written count. |
| Takes exactly right | Takes whose words all match. |
| Name and term recall | Each glossary term a take says, counted as recalled when the output has it as a whole word, spelled exactly (case ignored). |
| False insertions | A glossary term written more often than the take says it, over all takes; reported separately for the near misses. Recall alone rewards guessing. |
| Formatting written exactly | Each number, time, date or amount target counts when one of its accepted written forms (`3:30 p.m.` or `3:30 PM`) appears whole. Punctuation precision and recall and capitalization are also reported, from `benchmark_asr.py`'s formatting scorer. |
| Recognition latency | Runtime alone: request to answer, per take length (up to 5 s, 5 to 15 s, over 15 s). |
| Release to pasted | App path: the app's own measure from key release to the paste finished, in the fake field. |
| Delivery on the fake Mac | Takes the runtime answered, takes typed exactly as the app meant, pastes the app verified by reading the field back, takes with nothing typed. |

Every result records the machine (model identifier, chip, memory), macOS
version and build, Python version, the app's version and full revision, the
SHA-256 of the runtime's sources and of the binary, the model's identity,
pinned revision and a hash of its files, the corpus hash, the audio hashes,
the seed, the passes and the exact command.

## What it cannot show

- **Real voices.** Synthetic speech is clean, evenly paced and accent-light.
  These numbers say how the app handles names, terms, numbers and long takes
  from clean speech; they say nothing about a real person in a real room,
  noise, crosstalk or a heavy accent.
- **Release to text on screen.** The fake field takes a paste instantly, and
  there is no window server. "Release to pasted" leaves out what a real app
  adds; only the outside probe can time text arriving on screen.
- **Delivery into real apps.** The fake Mac proves the app's own paste and
  readback logic, not what happens in a browser field, a secure field or an
  app that moves focus.
- **The Whisper Tiny cross-check.** On a default install Whisper Tiny decodes
  the same audio only to set a confidence. It cannot run inside the fake Mac,
  so in the app path it hears nothing and the app uses its fixed confidence.
  The text comes from Parakeet either way, but a disagreement on a real Mac
  can lower the confidence enough for context repair to engage.
- **The installed default program.** The app path asks Whisper Face's own
  runtime first, which is off by default; the FluidAudio helper it falls back
  to has written identical text on every clip compared so far
  (`native/ParrotSpeechRuntime/README.md`).
- **One machine.** Latency depends on the Mac and on what else it is doing.
  Compare latencies only between results from the same machine.
- **Its makers ran it.** Every number here was produced by Whisper Face's own
  makers on their own corpus. The command and corpus are public so anyone
  can check them.

## Needs the outside probe

A result carries each of these as `null` with the status
`needs-outside-probe`. The page lists them, and a result that gives any of
them a value is refused.

| Measure | Why the run cannot give it |
|---|---|
| `release_to_text_on_screen_ms` | Needs a probe outside the app that timestamps text arriving in a real window. |
| `delivery_into_real_apps` | Needs real fields: focus moving during processing, a field that rejects paste, a secure field, a slow web field. |
| `bytes_sent_per_dictation` | Needs per-process network counters while dictating. This run denies the network to the runtime instead. |
| `idle_memory_mb` | Needs system tools on the installed app between dictations. |
| `time_to_first_dictation_s` | Needs a stopwatch protocol from a fresh download. |

## Rules a published result keeps

`public_scorecard.py benchmark` refuses to render, and `run` refuses to
write, a result that does not keep every one of these. `tests/test_benchmark_public.py`
breaks each on purpose.

1. It names the machine, the OS version and build, and Python, with no
   placeholder, and the app's full 40-character revision, measured on a
   checkout with no uncommitted change.
2. It names the model, its revision and a hash of its files, and the
   runtime's source and binary hashes.
3. It is a result of the corpus checked in now (same hash), with the
   corpus's seed, and says its speech is synthetic.
4. Every block of numbers keeps how it was measured: `runtime-in-process` or
   `app-on-fake-mac`.
5. Every number only the outside probe can give is empty.
6. Its accuracy numbers follow from the per-take texts it carries; an edited
   number is refused.
7. It carries every take once, each with a corpus voice.
8. It is a result for Whisper Face, or for a product the owner has recorded
   in `benchmarks/public_benchmark_rivals.json`.

## Adding other apps later

This is a design; none of it is built or run.

**The rule.** Nothing is published about another product until the Project
Owner holds an account or trial of it, has read its terms, and has recorded
in `benchmarks/public_benchmark_rivals.json` that those terms allow
publishing a benchmark of it. The renderer refuses any other product
without that entry. An agent never writes an entry. No maker is contacted;
they are free to rerun the benchmark.

**The outside probe.** Other apps cannot be booted on the fake Mac, so they
are measured from outside, and Whisper Face is measured the same way beside
them:

- The same corpus audio (the WAVs `run` writes) is played into a virtual
  audio input device that each app is set to use. Installing that device
  needs the owner's approval.
- A probe window with one text field has focus. It timestamps every change
  to its text, so "release to text on screen" is measured identically for
  every app, from the probe's key release to the last character arriving.
- Each app's own dictation key is pressed by the probe. If an app ignores
  synthetic key events, that app's latency needs a hardware key press and is
  marked as less reproducible.
- Each app runs on its defaults, then once on its makers' recommended
  settings with the corpus glossary given through its own dictionary, as
  Whisper Face's `dictionary` configuration does.
- Three runs per app, medians and spreads published, the app's version and
  the date recorded, and for a cloud app the network speed. The probe's
  numbers go into a result's `outside_probe` block with a third measurement
  class, and the renderer learns that class only when the probe exists.
- The text each app typed is scored by the same `score_takes`, so word error
  rate, recall, false insertions and formatting mean the same thing for
  every app.

The neutral stopwatch protocol in `benchmarks/competitor_tasks.json`
(`competitor_benchmark.py`, `benchmark_latency_rig.py`) stays as it is: it
covers setup and correction tasks the probe does not.

## Files

| File | What |
|---|---|
| `benchmarks/public_benchmark_corpus.json` | The corpus: takes, glossary, voices, seed, licence |
| `benchmarks/public_benchmark_rivals.json` | The rule for other products, and the owner's entries (none) |
| `benchmark_public.py` | `check` and `run` |
| `public_scorecard.py` | `benchmark`: the rules above, and the page |
| `tests/test_benchmark_public.py` | Each rule broken on purpose |
