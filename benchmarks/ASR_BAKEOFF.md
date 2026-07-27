# Mac ASR bakeoff

`benchmark_asr.py` compares candidate Apple-Silicon recognition engines over
the exact same deterministic LibriSpeech sample. It measures normalized word
error rate, exact-utterance accuracy, tail errors, throughput, and per-file
processing latency. Public research audio and generated hypotheses remain
outside the repository.

The benchmark complements rather than replaces the private product regression
suite. LibriSpeech measures read-English recognition; it does not measure
dictation cleanup, names, numbers, code, context safety, insertion integrity,
or correction burden.

## Single source of truth

[`benchmarks/model_scorecard.json`](model_scorecard.json) holds every published
number. The table below is a rendering of that file, not a second copy of the
data: a test parses these rows and fails if any value drifts from the
scorecard. Change the scorecard, never the table.

Each metric in the scorecard is bound either to a named measurement record or
to the explicit state `unmeasured`. A metric is published as measured only when
a record names the hardware and date it was measured on.

## Shipping baseline

Measurement `librispeech-test-clean-100-m4-pro-2026-07-21`: an Apple M4 Pro
MacBook Pro on 2026-07-21, running the same deterministic 100-utterance
`test-clean` sample for every engine. The operating-system version of that run
was not recorded and is published as null rather than guessed.

| Engine | Runtime role | WER | Exact utterances | p90 utterance WER | Throughput | p95/file |
|---|---|---:|---:|---:|---:|---:|
| `mlx-tiny` | Mac speculative ASR | 7.01% | 42% | 23.27% | 120.38x | 0.121s |
| `mlx-turbo` | Mac fallback ASR | 1.717% | 79% | 5.9% | 4.41x | 3.218s |
| `parakeet-unified` | Mac primary final ASR | 1.24% | 80% | 3.96% | 113.28x | 0.144s |

This is why the Mac cascade uses Tiny only for speculative UI evidence,
Parakeet for final recognition, and Whisper Turbo as the automatic fallback.
Parakeet was 25.7x the throughput of Turbo in this run while reducing aggregate
WER by 27.8%. Raw hypotheses and corpus text remain in the local output
directory rather than source control.

**This run's artifacts were not preserved.** The raw JSONL and `summary.json`
were not present in the repository or in `/tmp/parrot-asr-results` when the
scorecard was audited, so these numbers are documented-run evidence rather than
independently recalculated results. The scorecard records that honestly:
`artifacts_preserved` and `independently_recalculable` are both `false` for this
measurement.

Memory, energy, and startup remain **unmeasured** for all three candidates.
They are excluded from the score rather than guessed, and
`uv run performance_lab.py scorecard` prints them under `UNMEASURED`.

## Dataset

Download and extract the public LibriSpeech `test-clean` archive from OpenSLR:

```sh
curl -L -o /tmp/test-clean.tar.gz \
  https://www.openslr.org/resources/12/test-clean.tar.gz
tar -xzf /tmp/test-clean.tar.gz -C /tmp
```

This is the one benchmark in the repository that a third party cannot re-run
from a bare clone: it needs the dataset above, an Apple Silicon Mac, the MLX
snapshots, and the installed Parakeet helper. See
[reproducible corpora](../docs/benchmarks/reproducible-corpora.md) for what can
be re-run without any of that.

## Current MLX models

```sh
uv run benchmark_asr.py \
  --dataset /tmp/LibriSpeech/test-clean \
  --engines mlx-tiny mlx-turbo \
  --limit 100 \
  --output-dir /tmp/parrot-asr-results
```

The installed Mac helper exercises Whisper Face's shipping Parakeet path:

```sh
uv run benchmark_asr.py \
  --dataset /tmp/LibriSpeech/test-clean \
  --engines mlx-tiny mlx-turbo parakeet-unified \
  --parrot-helper .models/bin/parrot-asr-helper \
  --limit 100 \
  --output-dir /tmp/parrot-asr-results
```

## Refreshing the scorecard from a real run

Never hand-edit a metric. Preserve the run's `summary.json` and refresh from
it, so the published numbers stay bound to an artifact a reader can hash:

```sh
uv run performance_lab.py refresh-model-scorecard \
  --summary /tmp/parrot-asr-results/summary.json \
  --measurement-id librispeech-test-clean-100-<machine>-<date> \
  --hardware "Apple M4 Pro MacBook Pro" \
  --os-version 26.0.1 \
  --measured-on 2026-07-21
```

The command copies only the metrics the summary actually contains. It refuses a
run whose engine targeted a different model or revision than the reviewed pin,
refuses a partial run that does not cover every reviewed candidate, and refuses
to mark a measurement independently recalculable unless a preserved artifact
digest backs the claim. Memory, energy, and startup stay `unmeasured` because
the harness does not measure them.

## Executor honesty

An independently downloaded `macparakeet-cli` may be supplied with
`--macparakeet-cli` for research comparison when the Parrot helper is omitted.
It is not a Whisper Face dependency and must never be bundled by either
installer. That external CLI does not accept the scorecard repository revision,
so its result is explicitly marked `unverified-external-executor`; it cannot be
used as proof of measurements for the pinned shipping model. The shipping
helper path fails before inference unless every installed Core ML asset has
local Hugging Face sidecar metadata matching the scorecard revision. Those
sidecars do not attest the asset contents, helper executable, or model loaded
at runtime, so shipping-helper results remain
`unverified-helper-runtime-unattested` with null resolved model fields. CLI,
helper startup, and per-sample operations have bounded deadlines; helper JSON
responses are size-limited and timeout cleanup escalates from close to
terminate to kill.

## Formatting scoring (opt-in)

Normalized WER strips punctuation and casing before comparison, so it says
nothing about the formatting a user reads in every dictation. Parakeet emits
its own punctuation; that part of its output is not covered by the table
above.

`--formatting-scoring` adds a `formatting_scoring` block to each engine
summary next to, never replacing, the primary normalized WER:

- **Cased WER** (`cased_wer_pct`): word error rate over raw whitespace
  tokens compared exactly, case-sensitive and with punctuation still
  attached.
- **Punctuation precision/recall/F1**: reference and hypothesis words are
  aligned after stripping case and punctuation; on aligned equal words the
  trailing marks from `. , ? ! : ;` are compared. Marks on misrecognized
  words are excluded so a recognition error is not also counted as a
  punctuation error.
- **Capitalization match** (`capitalization_match_pct`): the share of
  aligned equal words whose exact casing matches the reference. There is no
  proper-noun modelling; sentence-initial and proper-noun capitalization
  count the same way.

The mode refuses to score references that carry no formatting. LibriSpeech
transcripts are uppercase without punctuation, so a LibriSpeech run reports
`"formatting_scoring": "unavailable — references unpunctuated"` instead of
misleading zeros. References with under 2% punctuated tokens, or with only
one letter case, are both treated as unavailable.

To score formatting, point `--dataset` at a JSONL manifest file instead of a
LibriSpeech directory. One object per line:

```json
{"id": "note-001", "audio": "audio/note-001.wav", "text": "Punctuated, cased reference."}
```

`audio` resolves relative to the manifest, `id` defaults to the audio file
stem, and the audio requirements are unchanged (16 kHz for the MLX and
helper engines). Keep manifests and their audio outside the repository like
every other corpus here.

No punctuated-corpus run has been recorded yet. Parakeet's formatting
quality remains unmeasured until one is.

## Output hygiene

Generated JSONL may contain public corpus text. Keep it outside the repository
so benchmark results cannot be mistaken for application assets or personal
dictation history.
