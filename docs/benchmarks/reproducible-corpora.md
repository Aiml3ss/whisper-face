# Reproducible demonstration corpora

Last audited: **2026-07-27**

Every benchmark corpus in this repository is synthetic text, scenario labels,
protocol definitions, model pins, or what the real recognizer wrote for
synthetic speech. None of them contains a recording or any personal
dictation. That is what makes them safe to publish — and it is also the
boundary of what they can prove.

This page answers one question precisely: **what can somebody who is not us
actually re-run?**

The machine-readable source is
[`benchmarks/reproducibility.json`](../../benchmarks/reproducibility.json).
`uv run tests/test_reproducible_corpora.py` re-reads it and fails if a declared
case count, command, or corpus stops matching the repository, so this page
cannot quietly drift the way a hand-maintained table would.

## Requirement classes

| Class | What it means |
|---|---|
| `clone-only` | Runs with nothing but a git clone and `uv`. No network, no downloads, no personal data, no special hardware. |
| `needs-download` | Needs a public dataset or model download first. |
| `needs-local-service` | Needs a locally running service, such as Ollama with a pulled model. |
| `needs-apple-silicon` | Needs an Apple Silicon Mac and installed runtime assets. |
| `needs-external-observations` | Needs observation files a human must collect outside this repository. |
| `needs-private-runtime-log` | Needs a log produced by actually dictating on an installed machine. |
| `reference-data` | Not a case corpus: thresholds, pins, or protocol definitions consumed by another command. |

## What a third party can run today

Six case corpora and one protocol template are fully reproducible from a bare
clone. Together they are the whole of the public synthetic scorecard.

| Corpus | Cases | Command |
|---|---:|---|
| `voice_compiler_cases.json` | 11 | `uv run benchmark_voice_compiler.py` |
| `consequence_routing_cases.json` | 16 | `uv run benchmark_consequence_routing.py` |
| `insertion_reliability_cases.json` | 11 | `uv run benchmark_insertion_reliability.py` |
| `point_and_speak_cases.json` | 17 | `uv run public_scorecard.py` |
| `drop_to_target_cases.json` | 11 | `uv run public_scorecard.py` |
| `representative_dictation_cases.json` | 22 | `uv run performance_lab.py corpus` |
| `competitor_run_template.json` | — | `uv run competitor_benchmark.py benchmarks/competitor_run_template.json` |

Two further clone-only runs use corpora listed elsewhere in this page:

```sh
uv run benchmark_cleanup_proof_recovery.py       # cleanup_latency_cases.json
uv run performance_lab.py stress --cycles 10 --restart-every 50
uv run performance_lab.py lifecycle --iterations 25
```

`default_path_sentences.json` (128 everyday sentences, each with the exact text
a fresh install must type) is not part of the scorecard. `uv run
tests/test_whole_program.py` dictates every sentence through the real
`dictate.py` booted on a fake Mac, with no models, audio device or network,
and needs a macOS host; `uv run quality_gate.py` also runs each sentence
through the deterministic cleanup.

`recognizer_transcripts.json` (76 sentences) is not part of the scorecard
either. It holds what the real Parakeet helper wrote for each sentence when
the macOS speech synthesizer spoke it, and every final text a person would
accept. `uv run benchmarks/recognizer_replay.py check` types each transcript
through the real `dictate.py` on the fake Mac and prints every sentence that
does not arrive as accepted; `tests/test_whole_program.py` runs the same
replay in the release gate. Like the default-path sentences it needs a macOS
host but no audio, model or network. Synthetic speech shows how the
recognizer punctuates, capitalises and formats numbers. It says nothing about
how accurately it hears a person's voice. See
[recording the transcripts again](#recording-the-transcripts-again).

The aggregate view is one command:

```sh
uv run public_scorecard.py
```

`public_benchmark_corpus.json` (116 takes) is the public benchmark's corpus:
names, terms, near misses, numbers, everyday sentences and long takes, all
written for it or copied from `default_path_sentences.json`, with no audio
stored. `uv run benchmark_public.py check` validates it from a clone; the run
itself speaks every take with macOS system voices and needs an Apple Silicon
Mac, the built speech runtime and the pinned model. Beside it,
`public_benchmark_rivals.json` holds the rule that nothing is published about
another product until the owner has recorded an account and terms that allow
it. [The public benchmark](public-benchmark.md) is the method and says what
the synthetic speech cannot show.

## What a third party cannot run today, and why

| Corpus | Class | What is missing |
|---|---|---|
| `cleanup_latency_cases.json` | `needs-local-service` | The latency half needs a local Ollama serving the pinned cleanup model, and refuses to start without `--run`. The proof-recovery half of the same 30 cases is clone-only. |
| `public_benchmark_corpus.json` | `needs-apple-silicon` | `check` runs from a clone. The run needs macOS system voices, the built speech runtime and the pinned Parakeet model. |
| `public_benchmark_rivals.json` | `reference-data` | Not a corpus: the publication rule for other products, with no entries. |
| `competitor_tasks.json` | `needs-external-observations` | The six-task protocol is reproducible; the runs are not. A real observation file needs clean machines, the other products, and a human observer. |
| `model_scorecard.json` | `needs-apple-silicon` | Ranking the checked-in evidence is clone-only. Regenerating the metrics needs a LibriSpeech download, an Apple Silicon Mac, the MLX snapshots, and the installed Parakeet helper. |
| `performance_budgets.json` | `reference-data` | Only the `ci_warm_path` profile is reachable from a clone. `product_quality`, `warm_path_stage`, and `startup_readiness` all need private runtime logs from an installed machine. |
| `quality_baseline.json` | `reference-data` | Not a corpus: the pinned deterministic quality metrics that `uv run quality_gate.py` re-measures from the corpora above and fails on any difference. `--rebaseline` rewrites it, and the diff rides the change that moved the numbers. |
| `reproducibility.json` | `reference-data` | The manifest itself. |

The `needs-download` class exists for the LibriSpeech dependency described in
[the ASR bakeoff](../../benchmarks/ASR_BAKEOFF.md); the `needs-private-runtime-log`
class covers the `performance_lab.py evaluate`, `startup`, and `warm-path`
commands, whose inputs are produced by dictating rather than by any corpus in
this directory.

## The competitor run template

`competitor_benchmark.py` used to be unreachable from a clone: it requires at
least one observation file, and producing one requires physically sitting down
with several products. `benchmarks/competitor_run_template.json` closes that
gap without inventing anything. Every one of its six observations is
`unavailable` with reason `not_run`, so the evaluator runs end to end and shows
its real output shape while reporting exactly zero measurements:

```sh
uv run competitor_benchmark.py benchmarks/competitor_run_template.json
```

The template is a schema demonstration, not evidence. A test asserts it can
never carry a measured value.

## Recording the transcripts again

Replaying `recognizer_transcripts.json` needs only a clone. Recording it needs
an Apple Silicon Mac with Whisper Face installed, because the transcripts come
from the installed Parakeet helper:

```sh
uv run benchmarks/recognizer_replay.py record \
  --helper <install>/.models/bin/parrot-asr-helper
uv run benchmarks/recognizer_replay.py check
```

`record` has the synthesizer (`say`) speak each case's `said` text into a
temporary file, so no sound is played, converts it to 16 kHz mono, and sends
it to the helper over the framed protocol the app uses. `--voice` picks a
synthesizer voice. It rewrites only the recognizer's side of each case
(`transcript`, `voice`, `recognizer`) and prints every transcript that
differs from the last recording. It never writes `accept`,
`recognizer_error` or `known_failure`: those are written by a person, from
what the speaker meant, never from what the program types. When a transcript
changes, review those fields before committing. To add a sentence, add a case
with `id`, `covers`, `said` and `accept`, then record.

A person's own speech takes the same path:

```sh
uv run benchmarks/recognizer_replay.py record \
  --helper <install>/.models/bin/parrot-asr-helper --wav-dir <folder>
```

The folder holds recordings (`.wav`, `.aiff`, `.m4a` or `.caf`), each with a
sidecar `<name>.txt` saying what was meant; that text seeds the new case's
`accept`. Recordings are never committed: the tool refuses a folder inside the
checkout and only reads the audio. The transcripts and sidecar text do go into
the transcripts file, so if what was said is private, pass `--transcripts` a
file outside the checkout and replay it with `check --transcripts`.

## What these corpora deliberately cannot prove

- **Nothing here is acoustic.** `representative_dictation_cases.json` declares
  accent, noise, and quiet-speech dimensions, and its coverage check passes on
  those dimensions using scenario labels only. Five of its cases carry explicit
  unfulfilled recording requirements. A text fixture cannot prove acoustic
  performance, and the corpus command says so rather than crediting itself.
  `recognizer_transcripts.json` did come from a real recognizer, but every
  case was spoken by a speech synthesizer: it shows how the recognizer
  punctuates, capitalises and formats numbers, not how accurately it hears a
  person.
- **Nothing here touches a real application.** The insertion suite is an
  adapter simulation and prints zero real apps exercised. Point-and-Speak and
  Drop-to-Target resolve synthetic scenes; there is no drag path in the
  codebase at all.
- **Nothing here is comparative.** No cross-product run has been recorded. See
  the [neutral competitor evaluation](competitor-evaluation.md).
- **Nothing here is physical.** `uv run public_scorecard.py` stamps
  `physical_validation: false`, `real_apps_exercised: 0`, and
  `audio_or_model_runs: false` on every suite it publishes.

## Publishing a dated report

When physical evidence does exist, publish it beside the synthetic evidence
rather than mixed into it:

```sh
uv run public_scorecard.py publish \
  --revision <40-character commit> \
  --published-on 2026-07-27 \
  --physical-artifact <capture or activation artifact> \
  --environment <named hardware and software> \
  --format markdown
```

The publisher refuses any artifact a capture harness did not stamp as physical,
any artifact reporting zero physical work, any re-listen report that counted a
single synthetic sample, and any physical claim without a concretely named
machine. See [architecture and interoperability](../architecture-and-interop.md)
and [contributor interfaces](../contributor-interfaces.md) for the artifact
schemas involved.
