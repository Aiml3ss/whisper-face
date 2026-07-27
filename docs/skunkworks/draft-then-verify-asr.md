# Draft-then-verify ASR: paste Parakeet now, prove corrections in later

Status: design, not built. Ships off. Requires the delayed-cleanup
transaction machinery and inherits its gates.

## Problem

The release path pastes the Parakeet Unified result and stops.
`transcribe_detailed` (dictate.py) returns the helper's text at a fixed
routing prior of 0.84 and never consults Whisper large-v3-turbo unless
the helper fails outright; the confidence-triggered second decode below
`LOW_CONFIDENCE = 0.52` applies only on Whisper paths, so on the normal
Mac path there is no second opinion at all
([ASR cascade](../../wiki/asr-cascade.md)).

That is the right latency call — Parakeet ran at 113× realtime against
Turbo's 4.4× in the 2026-07-21 bakeoff — and on LibriSpeech test-clean it
is also the right accuracy call: Parakeet measured 1.240% WER against
Turbo's 1.717% ([benchmarks](../../wiki/benchmarks.md)). This design is
therefore **not** "a better model checks a worse one". It is an
independent second decode whose value, if any, lives in the disagreement
set: proper nouns, code-shaped tokens, noisy or whispered audio, domains
where two different architectures fail differently. Whether that value
exists is measurable offline before any runtime code is written, and
Stage 0 below is that measurement. If the offline study shows Turbo
deltas do not move disagreement spans toward the reference, this design
stops there and the document records the negative result.

The shape, if it proceeds: paste the Parakeet result at release exactly
as today, blocking on nothing new; run a Turbo re-decode of the same
utterance audio asynchronously; three-way-merge only *proven*
corrections into spans the user has not touched, through the existing
delayed-cleanup transactional apply.

## Mechanism

### 1. Nothing new on the paste path

Release compiles, cleans, and pastes exactly as today
(`finish_and_process`). The draft-verify pass is requested only when the
mode is capture, the destination adapter is readable, and a valid
activation receipt exists — mirroring `delayed_cleanup_scheduling_enabled`.
It is scheduled only after a **verified** insertion receipt, with the
same generation counter and the same suppression of correction learning
for that utterance, because a later programmatic edit would corrupt the
correction observation window (the reason delayed cleanup already
suppresses it).

The utterance audio needed for the re-decode is already in memory at
that point (`full_audio` in `finish_and_process`); the pass holds it in
RAM only for the lifetime of the decode, consistent with the rule that
audio never touches disk.

### 2. Recompile, don't splice: the delta proposal

The inserted text is not raw recognition — it went through
`apply_learned_fixes`, the Voice Compiler, deterministic cleanup, casing
and snippet restoration. Mapping raw-recognition word deltas through all
of that is fragile. So the proposal is built by **recompiling**, the same
trick `build_delayed_cleanup_proposal` (dictate.py) uses for LLM cleanup:

```
build_recognition_delta_proposal(original_inserted, full_audio, voice_ir, ...):
    turbo = transcribe_detailed(full_audio, prompt, verify=False,
                                model_repo=WHISPER_REPO forced to Turbo)   # on ASR_POOL
    agreement = score_agreement(voice_ir.hypotheses[0], turbo)
    if agreement below floor: return None                                  # hallucination guard
    merged_hypotheses = voice_ir.hypotheses + (turbo as Recognition,)
    revised_ir = replace(voice_ir, hypotheses=select_spans(merged_hypotheses))
    proposal = deterministic-only recompile + quick_clean + casing + snippet restore
    if proposal == original_inserted: return None
    return proposal
```

Key properties:

- The Turbo decode runs on `ASR_POOL` (max_workers=1) because any MLX
  call off that thread aborts the process (the Metal single-thread rule
  documented at `decode_audio` in dictate.py). The pass therefore queues
  behind — never preempts — live dictation decodes, and abandons itself
  if a new hold starts (generation check), so a busy user never feels it.
- The recompile is **deterministic-only**: the LLM is excluded so every
  difference between `original_inserted` and `proposal` is attributable
  to recognition evidence, not to a second semantic-cleanup roll.
- Span selection happens where competing spans already live: the
  SpanGraph machinery in the Voice Compiler, which scores acoustic
  confidence, engine agreement, and phonetic/context fit "without freely
  inventing words" (CONTEXT.md). Turbo enters as one more hypothesis with
  word evidence, not as an authority.

### 3. The proof contract for an ASR delta

`build_delayed_cleanup_proposal` proves an LLM proposal by requiring the
model's declared edits to reconstruct its own output
(`VoiceCompiler.verify_edits`). Recognition deltas need an equivalent
contract, defined here:

A delta is admissible only if **all** hold:

1. **Span-anchored**: it maps to an exact span of `original_inserted`
   with a unique boundary anchor — enforced downstream by
   `delayed_cleanup_merge.py` (`AMBIGUOUS_ANCHOR`,
   `DESTINATION_REORDERED`, `INSUFFICIENT_ANCHOR` all refuse).
2. **Word-evidence backed**: the replacement tokens carry Turbo
   `RecognitionWord` evidence (word text, confidence, audio time range)
   covering the span's audio interval, with per-word confidence ≥ a
   pinned threshold. Honest asymmetry, stated plainly: the Parakeet
   helper returns text only — `{"ok", "text", "processing_s"}` — no word
   timings and no calibrated confidence (the 0.84 is a routing prior).
   The defense of the incumbent text is therefore textual and
   structural, not acoustic. Which is why:
3. **Substitution-shaped**: a delta may substitute at most 3 contiguous
   words, must be phonetically plausible against what it replaces
   (bounded normalized similarity, the same word-shaped constraints the
   correction learner uses: alphanumeric, 2–30 chars), may not insert
   net-new content beyond the substitution, and may not delete anything.
   Turbo can re-spell what Parakeet heard; it cannot add sentences.
4. **Anchor-safe**: deltas overlapping protected anchors (names,
   numbers, dates, URLs, paths, identifiers, commands —
   `protected_anchors` in voice_compiler.py) are admitted only when the
   two engines *agree on the anchor's audio span* and disagree solely on
   its spelling, and the span is not consequence-routed. Everything else
   near an anchor is refused. Cleanup must not invent meaning; neither
   may a verifier.
5. **Globally sane**: whole-transcript agreement between the engines ≥ a
   pinned floor (token-level, after normalization), and the Turbo text
   passes the existing `is_hallucination` / `collapse_repeats` /
   `looks_like_prompt_echo` screens. One bad screen discards the entire
   proposal, not just a span — a decoder that hallucinated anywhere is
   not trusted to correct anything.

### 4. Transactional apply, unchanged

The proposal goes through the identical machinery as delayed cleanup:
`DELAYED_CLEANUP_TRANSACTIONS.apply` with a single-use proposal id,
fresh destination snapshot, pure three-way merge (user edits always win;
colliding edits all refuse), re-read, and one whole-value
compare-and-swap via `macos_delayed_cleanup_destination.py`, including
its disclosed residual read-to-write window
([delayed cleanup](../../wiki/delayed-cleanup.md),
[ADR-0003](../adr/0003-transactional-insertion.md)). `apply_ms` is
measured around the apply, as `_run_delayed_cleanup` does today, because
the gate needs it.

Ordering with delayed LLM cleanup: at most one background mutation
pipeline per utterance. If both are eligible, recognition verification
runs first (it changes *what was heard*; cleanup then polishes it) and
delayed cleanup consumes the verified text as its `original`. Sharing
the generation counter serializes them; two concurrent proposals against
one destination are exactly the collision the single-use-id adapter
exists to refuse.

## What exists already vs what is new

Exists, reused as is:

- Async-after-verified-insertion scheduling shape, generation counter,
  learning suppression (`schedule_delayed_cleanup`).
- The whole merge/apply stack: `delayed_cleanup_merge.py`,
  `macos_delayed_cleanup_destination.py`, single-use proposal ids,
  outcome vocabulary.
- Turbo decode with word evidence and confidence
  (`transcribe_detailed`), hallucination screens, `ASR_POOL` discipline.
- SpanGraph / hypothesis-alternative machinery in the Voice Compiler.
- Activation receipts, measurement mode, capture-harness pattern.

New:

- `build_recognition_delta_proposal` and the delta proof contract above
  (a pure module, testable like `delayed_cleanup_merge.py`).
- Forcing a Turbo decode while Parakeet is enabled —
  `transcribe_detailed` currently short-circuits to Parakeet on Mac; the
  pass needs an explicit engine override parameter.
- Agreement scoring (token-level alignment + phonetic similarity).
- Scheduling/serialization with delayed cleanup.
- The `draft-verify` measurement-mode arm, evaluator, capture harness
  extension, and receipt.

## Evidence gate

Two gates, in order; the second cannot open before the first.

**Stage 0, offline (no receipt, no runtime change)**: a paired-decode
study over the existing `benchmark_asr.py` protocol plus a
disagreement-rich private corpus recorded with the voice-evidence
harness (proper nouns, code tokens, quiet speech — the conditions the
[calibration corpus](../../wiki/activation-receipt.md) already names).
For every utterance, decode both engines, synthesize the deltas the
contract would admit, and score against the reference:
`delta_fired`, `delta_correct`, `delta_harmful`. Publication bar to
proceed: `delta_harmful = 0` under the contract thresholds, and
`delta_correct` strictly positive at a rate worth the machinery
(proposed floor: corrects ≥ 2% of utterances in the disagreement corpus).
If the bar fails, tighten thresholds or stop; the study is the deliverable.

**Stage 2, physical**: activation receipt `draft-verify-v1` following
the house pattern (0600, policy-pinned thresholds, model-pinned Turbo
repo/revision — a Turbo model change invalidates the receipt —
evidence-pinned report hash, separate manual review). Suite recorded
under `--measure draft-verify`:

- ≥ 50 caller-attested cases across native-text, web-text,
  electron-editor, terminal-editor (≥ 10 each), the delayed-cleanup
  surface set.
- Scenario mix, ≥ 8 each: `agreement-noop` (engines agree, nothing
  applied), `true-correction` (operator attests the applied delta fixed
  a real mishearing), `disagreement-held` (proposal correctly refused),
  `user-edit-overlap` (user touched the span; delta refused).
- Hard zeros: user-edit overwrites, protected-anchor overwrites, wrong
  target writes, duplicate writes, and any applied delta the operator
  judges made the text *worse* than what Parakeet delivered.
- p95 `apply_ms` ≤ 150 (the existing `MAX_P95_APPLY_MS` bar), measured
  by the runtime; cases without runtime timing block.

## Risks and failure modes

- **Turbo hallucination overwriting verified text** is the headline
  abuse case; the layered answer is the global-agreement floor, the
  whole-proposal discard on any screen failure, the
  substitution-only/3-word cap, and the anchor rule. The residual risk —
  a fluent, confident, wrong 3-word substitution that passes phonetic
  similarity — is why `delta_harmful = 0` is the Stage 0 bar and
  operator review is per-case in Stage 2.
- **The visible-revision problem**: text the user already read changes
  under them. Bounded by the same properties delayed cleanup relies on
  (untouched spans only, small edits), but real; the Stage 2 operator
  attestation asks about it explicitly, and the result inspector must
  list applied recognition deltas just as it lists proof edits.
- **Double background mutation** (this pass plus delayed cleanup)
  multiplies the residual CAS window; serialization reduces occurrences
  to one apply at a time but two windows per utterance remain. The
  receipt's zero-tolerance counters cover both passes.
- **Latency and thermals**: Turbo at 4.4× realtime means a 60 s
  dictation costs ~14 s of decode queued on `ASR_POOL` behind live work.
  The pass must yield (generation check before starting the decode, not
  only after) and should skip utterances above a length ceiling.
- **Windows**: no transactional destination adapter exists there; out of
  scope, same as delayed cleanup.

## Staged rollout

1. **Stage 0 — offline paired-decode study**; go/no-go on measured
   delta value. No runtime change.
2. **Stage 1 — pure modules**: delta contract + agreement scoring with
   the same test discipline as `delayed_cleanup_merge.py`; engine
   override in `transcribe_detailed`.
3. **Stage 2 — runtime behind `--measure draft-verify`**, physical
   suite recorded via an extended
   `scripts/capture_delayed_cleanup_cases.py` sibling.
4. **Stage 3 — receipt-gated activation**, off by default, delayed
   cleanup receipt also required (shared apply machinery must have
   earned its own bar first).

## Open questions

- Can FluidAudio expose Parakeet word timings or per-token scores in a
  future version? Real acoustic evidence on the incumbent side would
  replace the purely textual defense in contract rule 2 and materially
  strengthen the design.
- Should the pass reuse the low-confidence path's temperature-0.4 retry
  as a third opinion when the two engines disagree hard, or is one
  verifier enough? (Cost says one; measure in Stage 0.)
- Interaction with [streaming commit](streaming-commit.md): per-span
  verification during the stream is tempting and much harder (spans are
  committed continuously); this design deliberately verifies only
  finished utterances.
- Does the disagreement corpus double as training signal for
  [personal acoustic adaptation](personal-acoustic-adaptation.md)?
  A confirmed Turbo correction is exactly the kind of labeled pair that
  design mines.
