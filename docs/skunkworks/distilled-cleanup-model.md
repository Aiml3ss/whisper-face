# Distilled cleanup model: sub-second semantic cleanup, guaranteed

Status: design, not built. The current pinned model stays the shipped
default until the existing A/B gate says otherwise on this machine's
own evidence.

## Problem

When an utterance genuinely needs semantic cleanup, the paste path waits
on Qwen3.5-4B through local Ollama under a `(1, 4)` s connect/read
deadline (`LLM_CLEANUP_TIMEOUT`, dictate.py). The
[cleanup pipeline](../../wiki/cleanup-pipeline.md) keeps most utterances
off that path (`needs_llm_cleanup` routes deterministically when it
can), and the guard rails mean a slow or bad model call falls back
rather than blocking — but the honest cost remains: on LLM-routed
dictations, the model dominates the paste latency, and the worst case is
a four-second wait followed by deterministic fallback.
[Delayed cleanup](../../wiki/delayed-cleanup.md) hides that latency
after the fact; this design removes it: a ~0.6B model fine-tuned on this
product's exact contracts, fast enough that **cleanup p95 ≤ 150 ms** —
the same bar the delayed-cleanup apply already answers to — so the LLM
never dominates the paste path again.

The safety story does not change at all, which is what makes a small
model viable: in capture and code modes, output is accepted **only**
through validated proof edits (`llm_clean_with_edits` →
`VoiceCompiler.verify_edits`; byte-exact reconstruction or the whole
result is discarded), `_guard_cleaned_output` screens refusals and
over-deletion, and the circuit breaker
(cleanup_circuit_breaker.py) turns repeated transport failure into
deterministic fallback. A weaker model cannot corrupt text; it can only
fail more often into the same fallback we already trust. The risk
surface is quality-shaped (compose/reply prose), not safety-shaped —
and the gate below is built accordingly.

## Training data: mine our own usage

`transcripts.jsonl` (0600, local-only, trimmed to the recent 500
entries) already stores per-dictation `raw`, `clean`, `path` (which
records whether the LLM route ran, e.g. `llm/casual`), `metrics`
(including `zero_edit` from `record_paste_outcome`), and `observed_text`
when a correction was watched. That yields three label tiers:

1. **Accepted pairs**: `raw → clean` where the path was LLM-routed, the
   insertion receipt verified, and `zero_edit` is true — the user's
   revealed acceptance of that exact transformation (a proxy, per
   CONTEXT.md, not an explicit statement).
2. **Corrected pairs**: `raw → user-corrected text` where
   `observed_text` differs — better than tier 1 where it exists, because
   a human authored the target.
3. **Proof-edit receipts as structure labels**: the runtime keeps
   accepted/rejected proof edits per dictation
   (`last_result_evidence` in `finish_and_process`); persisting a
   bounded, content-safe copy of accepted edit kinds alongside the pair
   teaches the model the *edit-plan* half of the structured output
   (`{"text": …, "edits": [{kind, before, after}]}`,
   `STRUCTURED_OUTPUT` in dictate.py), which is the half small models
   flub.

Volume honesty: 500 retained entries, most deterministic-routed, means
tens to low hundreds of LLM-routed pairs at any moment. Two responses:
raise the retention cap for pairs only (a separate bounded pair store,
consent text updated — the transcript log is already a disclosed local
usage log, but a training store is a new purpose and gets its own
toggle and wipe control, following the pantry pattern in
[personal-acoustic-adaptation](personal-acoustic-adaptation.md)); and
bootstrap with the checked-in synthetic corpus
(`benchmarks/cleanup_latency_cases.json`) plus synthetic expansions of
the five mode contracts. Fine-tuning toward a *format and contract* is
data-efficient; we are not teaching English, we are teaching a strict
JSON dialect and five prompts' worth of behavior
(`MODE_INSTRUCTIONS`: capture, code, compose, reply, edit).

## The model and how it serves

Candidate: the smallest current Qwen tier (~0.6B) fine-tuned with
LoRA/QLoRA — small enough to train on this Mac (MLX-LM or llama.cpp
tooling; training runs as a separate nightly-class process, never
in-process, for the same Metal single-thread reason documented at
`decode_audio`). Base choice is not load-bearing for the design; the
gate is.

Serving, two options:

- **Ollama, exactly like today (preferred first)**: package the tuned
  model locally, pin its manifest digest the way `OLLAMA_MODEL` /
  `OLLAMA_MODEL_MANIFEST_SHA256` pin qwen3.5:4b today, and the runtime
  change is two constants plus the receipt check. Every existing guard,
  timeout, breaker, and benchmark speaks Ollama already.
- **MLX serving**: lower floor latency, but it puts LLM inference into
  the Metal single-thread regime that ASR owns — so it would have to be
  its own process with its own lifecycle, which is… a local model
  server, i.e. what Ollama is. Revisit only if Ollama overhead itself
  (HTTP + scheduling, measurable in the lab) blocks the 150 ms target.

Mode routing stays open as a fallback position: if 0.6B proves solid on
capture/code (where proof edits catch everything) but weak on
compose/reply prose, route capture/code to the distilled model and keep
4B for compose/reply. The lab measures per-case outcomes, so this
decision falls out of the data rather than taste.

## Promotion: the gate already exists — extend it to model variants

`benchmark_cleanup_latency.py` is the opt-in, local-Ollama-only,
transcript-free lab, and `_variant_comparison`
(benchmark_cleanup_latency.py, near line 339) is the promotion rule this
product already trusts for cleanup-path changes:
`runtime_change_eligible` requires **zero baseline losses, zero new
semantic failures, zero new unavailable failures, and ≥ 10% improvement
in both p95 and max latency** (`MEANINGFUL_LATENCY_IMPROVEMENT = 0.10`),
computed per-case against the current runtime contract, which the lab
loads from dictate.py's own AST so it cannot drift.

One honest gap, which is real work: today's variants differ in few-shot
count and token budget against a **fixed** `MODEL` loaded from the
runtime contract. The lab needs a model dimension — a variant carrying
`model_id` + manifest digest, a report schema bump, and per-variant
model warm-up so cold-load doesn't pollute latency. The comparison
logic itself needs no change; that is the point of reusing it.

Target restated against the gate: baseline qwen3.5:4b p95 on this
machine's cases is whatever the lab measures; the candidate must land
**≤ 150 ms p95 absolute** *and* clear the relative gate. The absolute
target is this design's addition (the gate alone would accept 10% off a
slow baseline), pinned in the receipt policy.

## What exists already vs what is new

Exists:

- The entire acceptance chain that makes a small model safe: proof
  edits, output guard, circuit breaker, deterministic fallback, snippet
  masking.
- The A/B gate and its case corpus; `eval_cleanup.py` and the
  proof-recovery lab as secondary checks.
- Digest pinning for Ollama models; `verify_ollama_model_manifest` at
  startup.
- Raw material for mining (transcripts.jsonl fields above); zero-edit
  and verified-receipt semantics.
- Measurement mode as the pattern for running a candidate arm live
  without granting it anything.

New:

- The miner (pure module: transcripts window → tiered pairs,
  content-safe, bounded) and the opt-in pair store + consent + wipe.
- The training pipeline (separate process; artifacts: model + report).
- Model-variant support in `benchmark_cleanup_latency.py` (schema bump).
- A `--measure cleanup-model:<digest>` arm so the candidate can serve
  real sessions for physical evidence without touching defaults.
- The activation receipt and the runtime check that lets a receipted
  digest override `OLLAMA_MODEL` (off absent a receipt; the pinned 4B
  remains the compiled-in default).

## Evidence gate

Two stages, both required:

**Offline (the lab)**: candidate passes `_variant_comparison` against
the current model on (a) the checked-in synthetic corpus and (b) a
private mined case set with train/eval split **by day**, so the model is
never evaluated on pairs it trained on. Zero losses is already the
rule; the receipt additionally pins the ≤ 150 ms absolute p95 and the
per-mode outcome table (a compose/reply quality collapse must be
visible as semantic failures or guard rejections in per-mode counts,
not averaged away).

**Physical (the receipt)**: `cleanup-model-v1`, house pattern — 0600,
policy-pinned (thresholds above), model-pinned (base digest + tuned
digest; retraining invalidates), evidence-pinned (lab report SHA-256),
manual review non-defaultable. The physical suite: ≥ 50 live dictations
under `--measure cleanup-model:<digest>` across the five modes (≥ 8
each for capture/code/compose/reply, ≥ 4 for edit), operator-attested
per case in closed choices: output acceptable / fell back
deterministically / worse than expected; hard zeros on guard-rejection
rate exceeding the baseline session rate and on any case where the
operator judges meaning changed. Runtime-measured cleanup latency per
case, blocking if absent. Synthetic evidence structurally refused, as
everywhere ([activation receipt](../../wiki/activation-receipt.md)).

## Risks and failure modes

- **Compose/reply quality** is the likely failure: those modes carry
  the broad-rewrite contract with no proof-edit reconstruction
  ([cleanup pipeline](../../wiki/cleanup-pipeline.md)), so a weak model
  degrades prose without tripping a guard. Mitigations: per-mode gate
  counts, the mode-routing fallback (small model for proofed modes
  only), and per-case operator attestation in the physical suite.
- **Overfit to the author's voice**: a model tuned on one user's
  dictations is a personal artifact, mirroring personal priors — it
  must never ship as anyone else's default. This design is explicitly a
  personal, on-device fine-tune; distribution of tuned weights is out
  of scope.
- **Eval leakage** via near-duplicate dictations across days (people
  repeat themselves): day-split plus near-dup filtering in the miner;
  the report states both.
- **Digest churn**: every retrain invalidates the receipt by design;
  nightly retraining is therefore pointless under this gate. Retrain
  deliberately, occasionally, with a fresh suite each time.
- **Latency regressions under load**: Ollama shares the machine with
  ASR and the app; the lab measures in isolation. The physical suite
  measures in real sessions, which is why it exists.
- **A worse model failing more often into fallback** silently erodes
  the feature's value (everything pastes, but deterministically). The
  breaker and the per-session fallback counters make it visible; the
  gate's zero-new-unavailable term makes it disqualifying.

## Staged rollout

1. **Stage 0** — model-variant support in the latency lab; baseline the
   current 4B thoroughly on this machine (p50/p95/max per mode).
2. **Stage 1** — miner + opt-in pair store (consent, bounds, wipe).
3. **Stage 2** — first tuned candidate; offline gate only. Iterate
   here; most candidates should die here, cheaply.
4. **Stage 3** — `--measure cleanup-model` live arm; physical suite;
   receipt. Off by default; receipted digest serves this machine.
5. **Stage 4** — only with a receipt held and weeks of clean breaker
   telemetry: consider mode-routing or default-flip in a reviewed
   release, as a code change with the receipt as its cited evidence.

## Open questions

- Does 0.6B reliably emit the strict structured-output JSON at all, or
  is the floor 1–2B? Stage 2 answers this cheaply; the design should
  not anchor on 0.6B if the format failure rate is high.
- Is Ollama's fixed per-call overhead (HTTP, scheduling, tokenizer
  startup) already a double-digit-ms floor on this machine? Measure
  before blaming the model for missing 150 ms.
- Should tier-2 corrected pairs outweigh tier-1 accepted pairs in the
  loss (they are better labels but far scarcer)?
- The `(1, 4)` s timeout was sized for a 4B model; with a fast model a
  tighter read deadline (say 800 ms) would convert rare stalls into
  fast fallbacks. Retune only after the physical suite, and note the
  timeout is part of the lab's runtime contract, so it re-baselines the
  gate.
- Cross-link: if [draft-then-verify](draft-then-verify-asr.md) and this
  both hold background passes after paste, the shared
  one-mutation-at-a-time rule lives in delayed-cleanup scheduling;
  three consumers of that serialization deserve a small shared
  coordinator rather than three copies.
