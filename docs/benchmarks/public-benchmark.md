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
that breaks a rule below. The run takes about 25 minutes on an M4 Pro: the
app path plays each take in real time, once per configuration, and the
corpus is about 8.5 minutes of audio.

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
(Ireland), Rishi (India) and Tessa (South Africa). Each voice speaks 19 or
20 of the takes, which ones fixed by the seed, **20261117**. The same seed fixes
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
- **One machine, one sitting.** Latency depends on the Mac and on what else
  it is doing. Compare latencies only between results from the same
  machine, run one after the other. Most of a short take's release time is
  the app's fixed 50 ms wait before the paste keystroke, and how late that
  wait wakes up moves by 5 to 10 ms between runs: the same 0.6.0 revision
  measured a short-take p50 anywhere from 62 to 72 ms over seven runs in
  one afternoon. A difference smaller than that between two results run
  apart is not a difference.
- **What the fake microphone does not cost.** Stopping the fake stream is
  free; stopping a real one waits on the audio device (about 100 ms at the
  median on the owner's Mac). Since the release-latency change the app no
  longer waits for it before the paste, so the fake Mac understates what
  that change gained. Every take also ends in 0.3 s of silence, long enough
  for the last part to be decoded before the key-up; a person who lets go
  mid-word waits for that decode, which the fake Mac never shows.
- **Its makers ran it.** Every number here was produced by Whisper Face's own
  makers on their own corpus. The command and corpus are public so anyone
  can check them.

## Needs the outside probe

A result carries each of these as `null` with the status
`needs-outside-probe`. The page lists them, and a result that gives any of
them a value is refused. The one exception is `release_to_text_on_screen_ms`,
which `attach-probe` fills from the outside probe (next section).

| Measure | Why the run cannot give it |
|---|---|
| `release_to_text_on_screen_ms` | Needs a probe outside the app that timestamps text arriving in a real window. |
| `delivery_into_real_apps` | Needs real fields: focus moving during processing, a field that rejects paste, a secure field, a slow web field. |
| `bytes_sent_per_dictation` | Needs per-process network counters while dictating. This run denies the network to the runtime instead. |
| `idle_memory_mb` | Needs system tools on the installed app between dictations. |
| `time_to_first_dictation_s` | Needs a stopwatch protocol from a fresh download. |

## The outside probe

`outside_probe.py` measures the one latency comparable across dictation
apps: from letting go of the dictation key to the text arriving in a text
field. It measures from outside the app, so it times any app the same way.

```sh
uv run outside_probe.py run --app whisper-face --go-ahead whisper-face \
    --benchmark <out> --app-revision "$(git -C <app checkout> rev-parse HEAD)"
uv run benchmark_public.py attach-probe --out <out>
```

`<out>` is the folder of a finished `benchmark_public.py run`, made from the
same revision the installed app runs: the probe plays that run's audio, and
`attach-probe` refuses a probe that timed a different revision, Mac, macOS,
corpus or audio. The terminal that runs it needs Accessibility (System
Settings > Privacy & Security), which macOS requires before a program may
post key events; without it the probe says so and stops.

**What it does on the screen.** A small window titled "Outside probe: keep
this window in front", with one empty text field, comes to the front. After
a five-second countdown in the terminal, for each take: the field is emptied,
the capture key (Right Option, or `--key`) is pressed, the take plays from
the speakers, 0.6 s later the key is released, and the app's text appears in
the field. Ctrl-C stops the run and releases the key. With `--repeats 1`
(the default) the whole corpus takes about 15 minutes.

**What it records.** For each take: the release time, the first change to
the field's text and the final one (the last change before the text stays
the same for 1.5 s), on one clock shared by every process on the Mac. The
result, `<out>/outside_probe.json`, also names the machine, macOS, the app
and its revision, the corpus hash, each take's audio hash and the rerun
command. `attach-probe` puts the medians per take length into the
benchmark result under `release_to_text_on_screen_ms` with a third
measurement class, `outside-probe`, and the page gains two latency rows:
release to first text and release to final text.

**How it drives the app, and why that works.** The key is pressed with
synthetic key events posted at the HID level, which every event tap sees as
it sees a real key. Whisper Face hears its key through pynput's listen-only
session event tap; pynput passes posted events on (it marks them as
injected, and the app's callbacks ignore that mark). The app also polls the
key's physical state to catch a lost key-up, but it trusts an "up" reading
only after it has once read "down" during the same hold, so a posted hold
the physical state never shows ends on the posted key-up, not early. This is
read from the code (`dictate.py`, `supervise_hotkey_latch`); it has not yet
been run against the installed app. If an app ignores posted events, every
take comes back with no text; that app then needs a hardware key press,
and its numbers are marked as less reproducible.

**The audio path, and its trade-off.** The take plays through the Mac's
default output and the app hears it through its microphone, as it would
hear a person. No virtual loopback device is installed (installing one
needs the owner's approval), and Whisper Face has no seam that feeds a
recording to its capture: measurement mode covers calibration, keyword
priority and delayed clean-up only, and a seam would measure something a
person never gets. The cost is the room: speaker quality, noise and the
microphone's own processing change what the app hears, so the probe's text
is kept but not scored for accuracy; accuracy comes from the runtime and the
fake Mac. The timing does not depend on the room: it starts at the key
release, which comes after the take and a 0.6 s tail have played.

**What it cannot show.**

- *Pixels.* The time is when the window's text storage changes, before it is
  drawn; the display adds up to one refresh (8 to 17 ms) for every app alike.
- *A release mid-word.* Every take ends in silence plus the tail, as on the
  fake Mac.
- *Every app's own field.* The probe's field is a plain AppKit text view in a
  small program; an app that treats some apps differently may deliver
  differently elsewhere.

**Its own accuracy.** `tests/test_outside_probe.py` runs the real probe
window (hidden, never in front) against a fake dictation app that types each
take's text into it in two parts, 150 ms and 450 ms after the key-up, and
logs when it sent each part. The probe never saw text before it was sent,
always told the first part from the final one, and over five runs on the M4
Pro it was built on it saw each part 0.6 to 1.5 ms after it was sent at the
median and 3.2 ms after at most. That includes the fake's own pipe into the
window, which a real app's paste does not use, so it bounds the probe's
error from above.

**Its posture.** The probe runs against an app only when the command line
names it twice, as `--app` and as the go-ahead `--go-ahead`. It posts only
the capture key, only while its own window is in front with its field
focused, stops the moment that changes, and always releases the key. It
types nothing and touches no pasteboard. It imports no network module, plays
audio only to the speakers, and writes only into `<out>`, which is outside
the checkout. `tests/test_outside_probe.py` breaks each of these on purpose.

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
5. Every number only the outside probe can give is empty, except release
   to text on screen when it was attached from an outside probe result.
6. Its accuracy numbers follow from the per-take texts it carries; an edited
   number is refused.
7. It carries every take once, each with a corpus voice.
8. It is a result for Whisper Face, or for a product the owner has recorded
   in `benchmarks/public_benchmark_rivals.json`.
9. Its rerun command names no local folder: the runtime, model and output
   folders appear as `<runtime>`, `<models>` and `<out>`.
10. An outside probe measurement timed the same app revision, machine, macOS,
    corpus and audio, ran to its last take, and its numbers follow from its
    own per-take times (`tests/test_outside_probe.py`).

## Adding other apps later

This is a design; only the outside probe is built, and it has not been
run against any app.

**The rule.** Nothing is published about another product until the Project
Owner holds an account or trial of it, has read its terms, and has recorded
in `benchmarks/public_benchmark_rivals.json` that those terms allow
publishing a benchmark of it. The renderer refuses any other product
without that entry. An agent never writes an entry. No maker is contacted;
they are free to rerun the benchmark.

**The outside probe.** Other apps cannot be booted on the fake Mac, so they
are measured from outside, and Whisper Face is measured the same way beside
them:

- The same corpus audio (the WAVs `run` writes) is played to each app as
  the outside probe plays it: through the speakers today, or through a
  virtual audio device each app is set to use, once the owner approves
  installing one.
- The outside probe times every app the same way, from its key release to
  the text arriving in its own window. Its `--key` names Whisper Face's
  bindable keys; another app's key would be added to it.
- Each app runs on its defaults, then once on its makers' recommended
  settings with the corpus glossary given through its own dictionary, as
  Whisper Face's `dictionary` configuration does.
- Three runs per app (`--repeats 3`), medians and spreads published, the
  app's version and the date recorded, and for a cloud app the network
  speed.
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
| `benchmark_public.py` | `check`, `run` and `attach-probe` |
| `public_scorecard.py` | `benchmark`: the rules above, and the page |
| `tests/test_benchmark_public.py` | Each rule broken on purpose |
| `outside_probe.py` | The outside probe: `run`, and its own window |
| `tests/test_outside_probe.py` | The probe's rules broken on purpose, and its accuracy against a fake app |
| `tests/fake_dictation_target.py` | The fake app those tests time |
