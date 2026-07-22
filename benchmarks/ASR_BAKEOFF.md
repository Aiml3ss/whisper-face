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

## Shipping baseline

On 2026-07-21, the command below was run on an Apple M4 Pro MacBook Pro against
the same deterministic 100-utterance `test-clean` sample for every engine:

| Engine | WER | Exact utterances | p90 utterance WER | Throughput | p95/file |
|---|---:|---:|---:|---:|---:|
| MLX Whisper Tiny | 7.010% | 42% | 23.27% | 120.38x | 0.121s |
| MLX Whisper large-v3-turbo | 1.717% | 79% | 5.90% | 4.41x | 3.218s |
| Parakeet Unified, shipping helper | **1.240%** | **80%** | **3.96%** | **113.28x** | **0.144s** |

This is why the Mac cascade uses Tiny only for speculative UI evidence,
Parakeet for final recognition, and Whisper Turbo as the automatic fallback.
Parakeet was 25.7x the throughput of Turbo in this run while reducing aggregate
WER by 27.8%. Raw hypotheses and corpus text remain in the local output
directory rather than source control.

## Dataset

Download and extract the public LibriSpeech `test-clean` archive from OpenSLR:

```sh
curl -L -o /tmp/test-clean.tar.gz \
  https://www.openslr.org/resources/12/test-clean.tar.gz
tar -xzf /tmp/test-clean.tar.gz -C /tmp
```

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

Generated JSONL may contain public corpus text. Keep it outside the repository
so benchmark results cannot be mistaken for application assets or personal
dictation history.
