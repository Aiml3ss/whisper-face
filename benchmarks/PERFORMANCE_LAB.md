# Whisper Face performance lab

`performance_lab.py` is the transcript-free evaluation surface for accuracy,
tail latency, routing, model selection, and offline lifecycle stress. It uses
only the Python standard library and the platform-independent Voice Compiler.

## Representative corpus

The committed corpus contains synthetic text and scenario labels. It does not
contain recordings or personal dictation. Validate that every required risk
dimension still has coverage:

```sh
uv run performance_lab.py corpus
```

The manifest covers names, numbers, dates, URLs, code, commands, corrections,
false starts, acronyms, paths, and email addresses. Accent, noise, and quiet
speech are recording requirements in scenario metadata; a text fixture cannot
prove acoustic performance.

## Privacy-safe outcome dashboard

Create JSONL with one numeric outcome per line. Unknown fields are rejected so
raw transcripts, audio, clipboard contents, and surrounding application text
cannot accidentally enter the aggregate report:

```json
{"case_id":"currency-and-decimal","latency_ms":{"asr":120,"compiler":8,"cleanup":40,"end_to_end":350},"edit_characters":0,"pasted_words":9,"zero_edit":true,"selected_route":"parakeet","expected_route":"parakeet","receipt":"verified"}
```

Render a local dashboard:

```sh
uv run performance_lab.py evaluate \
  --observations /private/tmp/whisper-face-outcomes.jsonl
```

It reports p50/p95/p99/max latency, Correction Burden in changed characters
per 100 pasted words, the observed zero-edit proxy, route accuracy, corpus
coverage, and verified-delivery rate. Add `--format json` for automation. Add
`--budget-profile product_quality` to enforce the versioned minimum sample
counts and targets in `performance_budgets.json`.

Route and lifecycle values are fixed schema identifiers rather than free-form
labels, preventing a user string from being reflected into aggregate keys.
Receipt values exactly match the runtime contract: `verified`, `unverifiable`,
`conflict`, and `unresolved`. Adding a route requires a reviewed schema change.

## Deterministic warm-path gate

The stress command warms every synthetic case, compiles the full corpus
back-to-back, periodically creates a new compiler, checks output determinism,
tracks peak Python allocations, and applies the CI tail-latency budget:

```sh
uv run performance_lab.py stress --cycles 10 --restart-every 50
```

The workload and percentile calculation are deterministic. Wall-clock timing
still depends on the runner, so the 50 ms p95 budget is intentionally wide: it
catches hangs and order-of-magnitude regressions without turning normal shared
runner variance into failures. The cross-platform GitHub workflow runs the
corpus, metrics, scorecard, and lifecycle tests on every push and pull request.

This harness does not claim to exercise microphone gain, background audio,
device switching, OS sleep/wake, energy, or thermal behavior. Those require a
licensed physical-audio corpus and the hardware matrix.

## Model scorecard

Generate the current evidence-based ranking:

```sh
uv run performance_lab.py scorecard
```

The scorecard normalizes quality, latency, and throughput within the compared
cohort. Licensing is an independent eligibility gate. Missing memory, energy,
and startup measurements remain visibly unmeasured and are excluded from the
score rather than guessed. Update `model_scorecard.json` only from a preserved,
documented benchmark run.

### Provenance and currentness audit

The Mac ASR candidates were rechecked against the runtime constants, installed
model metadata, and primary model repositories on 2026-07-21:

| Runtime role | Exact repository | Pinned revision | Repository `main` at audit | Artifact license evidence |
|---|---|---|---|---|
| Speculative | [`mlx-community/whisper-tiny`](https://huggingface.co/mlx-community/whisper-tiny) | `78c52ab98ca87f570bc57ad852e15ef7060f9f76` | Same revision | Not declared by conversion repository |
| Fallback | [`mlx-community/whisper-large-v3-turbo`](https://huggingface.co/mlx-community/whisper-large-v3-turbo) | `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` | Same revision | Not declared by conversion repository |
| Primary final | [`FluidInference/parakeet-unified-en-0.6b-coreml`](https://huggingface.co/FluidInference/parakeet-unified-en-0.6b-coreml) | `4252711f6f060f9a2f91e5f081a806d7f45eebd8` | Same revision | Card declares CC-BY-4.0; upstream provenance conflicts |

The two MLX cards identify their source only as `tiny` and
`large-v3-turbo`; they do not declare a license or content-addressed upstream
artifact. OpenAI's [upstream Whisper repository](https://github.com/openai/whisper)
states that its code and model weights are MIT, but that does not fill the
conversion repositories' missing artifact metadata. They therefore remain
`review-required` rather than silently inheriting an assumed license.

The FluidInference artifact card declares CC-BY-4.0, but its provenance is
internally inconsistent at the exact pinned revision. README front matter names
[`nvidia/parakeet-tdt-0.6b-v2`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)
as the base model; that NVIDIA card declares CC-BY-4.0. The pinned
`metadata.json` instead names
[`nvidia/parakeet-unified-en-0.6b`](https://huggingface.co/nvidia/parakeet-unified-en-0.6b),
whose card uses the NVIDIA Open Model License, and the conversion card's
architecture and offline/streaming description align with this newer unified
model. The scorecard therefore marks the artifact `review-required`, leaves
`upstream_model_id` unknown, and makes no license-eligible recommendation until
the publisher clarifies which upstream model and terms apply.

Current-head checks used each repository's Hugging Face model API and immutable
revision endpoint. The two installed MLX cache refs matched the pins, and
`dictate.py --verify-parakeet-model` verified the installed Parakeet asset
metadata. Quality and latency numbers exactly match `ASR_BAKEOFF.md`, but raw
JSONL/summary artifacts from that run are not committed and were not present in
`/tmp/parrot-asr-results` during the audit. The scorecard therefore records
those metrics as documented-run evidence, not independently recalculated raw
results.
