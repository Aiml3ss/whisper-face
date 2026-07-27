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

It reports p50/p90/p95/p99/max latency, Correction Burden in changed characters
per 100 pasted words, the observed zero-edit proxy, route accuracy, corpus
coverage, and verified-delivery rate. Each report also includes a
`by_dimension` block that repeats the zero-edit, Correction Burden, and
route-quality signals for every risk dimension a record touches, so a
regression concentrated in one dimension such as numbers or code stays visible
without reading any transcript. A record with several dimensions contributes to
each of them, and a dimension with no observations is omitted rather than
divided by zero. Add `--format json` for automation. Add
`--budget-profile product_quality` to enforce the versioned minimum sample
counts and targets in `performance_budgets.json`; that profile also carries a
few per-dimension checks gated on `by_dimension.<dim>.samples`, so sparse data
reports `insufficient-samples` instead of a false regression.

Route and lifecycle values are fixed schema identifiers rather than free-form
labels, preventing a user string from being reflected into aggregate keys.
Receipt values exactly match the runtime contract: `verified`, `unverifiable`,
`conflict`, and `unresolved`. Adding a route requires a reviewed schema change.

## Cold versus warm startup traces

The runtime already emits closed numeric traces for audio-pool warmup, Tiny,
final ASR, Ollama, and total readiness. Keep physically collected cold and warm
launches in separate logs, then evaluate both without adding a phase label or
other open-ended data to the runtime schema:

```sh
uv run performance_lab.py startup \
  --cold-trace-log /private/tmp/whisper-face-cold.log \
  --warm-trace-log /private/tmp/whisper-face-warm.log
```

The `startup_readiness` profile requires 100% reported success and applies
independent p95 thresholds and minimum sample counts to all five components:
three caller-labelled cold launches and ten caller-labelled cache-warm
launches. The report never returns either input path or any non-trace log
content. Classification is explicitly
`caller-separated-trace-logs`; the tool does not infer cache state from event
order and sets `physical_conditions_verified` to false. A controlled Mac test
procedure is still required before treating those labels as physical cold and
steady-state evidence.

## Warm-path stage latency traces

Each completed dictation emits one closed numeric `warm_path` trace carrying
only per-stage millisecond timings: release, ASR, compiler, cleanup, context
firewall, and insertion. The trace holds no text, identifiers, or other
open-ended data, and the runtime emits it best-effort, so this telemetry can
never delay or break a paste. The insertion stage times the commit step that
was previously the one un-instrumented stage.

Collect these traces from a runtime log and aggregate them into per-stage
percentile tails:

```sh
uv run performance_lab.py warm-path \
  --trace-log /private/tmp/whisper-face-warm-path.log
```

The report gives p50/p90/p95/p99/mean/max and a sample count for every stage
under `latency_ms.<stage>`. Non-`warm_path` traces are counted as ignored
records, invalid lines fall into fixed rejection categories, and the input path
is never returned. Add `--format json` for automation. Add
`--budget-profile warm_path_stage` to apply the versioned per-stage p95
thresholds in `performance_budgets.json`; each check is gated on
`latency_ms.<stage>.samples`, so a thin log reports `insufficient-samples`
instead of a false pass. The trace schema is duplicated between `dictate.py` and
`performance_lab.py` and held identical by a parity test.
`benchmark_latency_rig.py trace` reads the same log through this parser and
produces the operator-facing p50/p95/max report with a 20-dictation minimum
sample gate and explicit operator-attestation labelling; see
`docs/benchmarks/competitor-evaluation.md` for both rig modes.

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

The separate lifecycle command drives the real Voice Compiler behind a
deterministic adapter, injects unavailable states, verifies blocked operations,
restores service, and compares recovered output with a synthetic baseline:

```sh
uv run performance_lab.py lifecycle --iterations 25
```

It covers back-to-back compilation, the synthetic long-form case, compiler
re-instantiation, simulated sleep/wake, and simulated audio-device loss and
restoration. Its artifact says `adapter-simulation-only` and
`physical_evidence: false`; it cannot prove operating-system sleep, driver
recovery, microphone behavior, or long-audio memory and thermal stability. A
read-only scheduled/manual workflow runs the deterministic harness and its test
suite, then preserves only the synthetic aggregate artifact.

The consequence-routing benchmark is narrower: it exercises only synthetic
classification and selector policy. Its artifact explicitly reports that no
audio, verifier, runtime ASR backend, or physical device was exercised. Custom
case identifiers are replaced by deterministic ordinal labels in every result
and latency row so caller strings cannot enter an artifact described as
transcript-free.

## Model scorecard

Generate the current evidence-based ranking:

```sh
uv run performance_lab.py scorecard
```

The ASR bakeoff consumes the same scorecard as its source of truth. Every MLX
snapshot download supplies both the repository ID and immutable revision, and
the raw records and summary separate the requested model identity from the
identity actually resolved by the executor. Before launching Whisper Face's
shipping Parakeet helper, the harness requires every required Core ML asset to
have a Hugging Face sidecar naming the reviewed scorecard SHA. A mismatch or
missing asset aborts the run before the helper starts. Sidecars do not attest
the asset bytes, executable, or model actually loaded by FluidAudio, so helper
runs set both `resolved_model_*` fields to `null` and use
`unverified-helper-runtime-unattested`. The matching sidecar preflight is
recorded separately. The summary also records SHA-256 hashes of
`benchmark_asr.py` and `model_scorecard.json`, the Git revision when available,
and the concrete executor.

The optional independent `macparakeet-cli` accepts only its own named model
selector, not a repository SHA. Its records therefore preserve the scorecard
target as `requested_model_*` but set `resolved_model_id` and
`resolved_model_revision` to `null`, with `model_revision_status` equal to
`unverified-external-executor`. Such a run is useful for research comparison,
but is not evidence for the scorecard revision.

The external CLI has a 30-minute process deadline. The shipping helper has
separate startup and per-sample response deadlines, a 64 KiB maximum JSON
response, and bounded close/terminate/kill cleanup. A timeout aborts the run;
partial results are never presented as a completed bakeoff.

The scorecard normalizes quality, latency, and throughput within the compared
cohort. Licensing is an independent eligibility gate. Missing memory, energy,
and startup measurements remain visibly unmeasured and are excluded from the
score rather than guessed. Update `model_scorecard.json` only from a preserved,
documented benchmark run.

### Scheduled public-source audit

The read-only `Model source audit` workflow runs weekly and can be started
manually. It compares each reviewed repository head, immutable revision,
declared license, and declared base-model metadata with the public Hugging Face
API:

```sh
uv run performance_lab.py audit-models \
  --format json \
  --output /tmp/whisper-face-model-audit.json
```

Exit status `0` means the reviewed public metadata is unchanged, `1` means
upstream drift was detected, and `2` means the check could not complete. The
workflow uploads the JSON evidence before propagating either failure. Reports
contain public model IDs, revisions, license/base-model metadata, status codes,
and exception types only; exception messages and model-card prose are excluded.

`review-required` is the current reviewed licensing state and does not itself
fail this audit. A failure means the reviewed facts changed or could not be
checked. The job does not download model weights, benchmark quality or speed,
change a runtime pin, update the scorecard, or make a release recommendation.
Those remain explicit review and bakeoff decisions.

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
`dictate.py --verify-parakeet-model` confirmed that the installed Parakeet
sidecars name the reviewed revision; it did not attest asset contents or the
runtime-loaded model. Quality and latency numbers exactly match
`ASR_BAKEOFF.md`, but raw
JSONL/summary artifacts from that run are not committed and were not present in
`/tmp/parrot-asr-results` during the audit. The scorecard therefore records
those metrics as documented-run evidence, not independently recalculated raw
results.
