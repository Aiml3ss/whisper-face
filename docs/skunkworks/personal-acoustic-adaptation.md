# Personal acoustic adaptation: a nightly local fine-tune of the ear

Status: design, not built. Ships off. One of the three paths below
requires a new privacy surface (retained audio) that nothing in the
product has today; that surface is opt-in and consent-gated on its own,
before any training question arises.

## Problem

The recognizers are generic. They mishear the same personal terms the
same way every week — names, project words, a speaker's particular
vowels — and today the product compensates *after* recognition:
`apply_learned_fixes` (dictate.py) rewrites text a correction has proven
wrong `PROMOTE_MIN_COUNT` times, Personal Priors rescore spans in the
Voice Compiler, and keyword prompt priority (gated) biases the Whisper
prompt. None of it changes what the model hears.

The product also already generates, as a side effect of its safety
machinery, exactly the labels a personal fine-tune needs: a **zero-edit
verified dictation** is an utterance whose insertion receipt was
`verified` (insertion_integrity.py `ReceiptState.VERIFIED`) and whose
pasted range was observed unchanged for the full ten-second correction
window — `record_paste_outcome` writes `metrics["zero_edit"] = True`
into `transcripts.jsonl`, and CONTEXT.md is careful about what that
means: "a proxy, not an explicit statement of user acceptance". Those
receipts pair accepted *text* with an utterance. What is missing for
acoustic training is the *audio*, which the product deliberately never
writes to disk anywhere.

## Three candidate paths, honestly ranked

### (c) Distill corrections into the deterministic fix layer — cheapest, half-exists

Not acoustic at all, and that is its virtue. The existing learning path
observes word-shaped respellings on the exact pasted range (similarity
0.4–1.0 exclusive, alphanumeric, 2–30 chars, max 3 per dictation) and
promotes them through the [Personal Regression Lab](../../wiki/personalization.md)
(personal_regression.py: 256 cases, 80-char spans, zero-regression
promotion, demotion on contradicting evidence, quarantine on load).

The distillation step: mine `transcripts.jsonl` — entries carry `raw`,
`clean`, `observed_text`, and `metrics.zero_edit` — for recurring
(heard → preferred) span pairs that the current online learner is too
conservative to catch in the moment: corrections made outside the
ten-second window, multiword respellings, casing-only fixes, app-scoped
patterns that recur across days. A nightly pure job proposes candidates;
every candidate goes through the **existing** promotion machinery —
`PersonalRegressionLab` cases plus the `ShadowCandidateGate` contract
(any error or regression quarantines; zero improvements is insufficient
evidence). No new activation surface: this extends a shipped, always-on,
individually forgettable mechanism within its existing bounds
(MAX_CASES 256, transcripts already trimmed to the recent 500).

What it cannot do: fix a mishearing the user never corrects, or help
before the first correction. It is a floor, not a ceiling.

### (a) LoRA on Whisper Tiny via MLX — feasible on-device

Whisper Tiny (~39M parameters, MLX weights pinned in `WHISPER_REPOS`) is
small enough that LoRA fine-tuning on Apple Silicon is realistic in a
nightly window; MLX has working LoRA training paths for small
transformer models, and audio+transcript pairs in the tens of minutes
are the expected scale for speaker adaptation of a model this size.

Bounded claim about the payoff, because the cascade defines it: with
Parakeet active, **Tiny is never accepted as final text** — it exists
for speculative HUD previews and its disagreement is retained as an
alternative hypothesis ([ASR cascade](../../wiki/asr-cascade.md)). A
personalized Tiny therefore buys: better live captions and stable
prefixes, better speculation reuse at chunk cuts, and a
better-informed alternative hypothesis inside the SpanGraph — not a
direct change to pasted text on the Parakeet path. It *does* directly
change final text on the degraded no-Turbo path and potentially on
Windows (but Windows runs faster-whisper/CTranslate2 weights, where an
MLX-trained LoRA does not port; Mac-only, say so in the UI).

Requirements that do not exist today:

- **The Training Pantry**: an opt-in, bounded, owner-only (0600) local
  store of (16 kHz audio, accepted text) pairs. Written only when all
  hold: the pantry preference is on; mode is capture or code; receipt
  verified; `zero_edit` true after the full window; not verbatim; no
  snippet expansion in the text. Caps: ≤ 60 minutes of audio and
  ≤ 500 MB, FIFO eviction; one control wipes it entirely; disabling the
  preference wipes it; it never leaves the machine and is excluded from
  the support bundle. This is the first feature in the product that
  writes voice audio to disk. It gets its own consent screen written in
  those words, a PRIVACY.md section, and a distinct file-system location
  the user can inspect. It is not bundled into any other toggle.
- **A nightly trainer as a separate process** (launchd template beside
  `com.berg.ollama.plist.template`). In-process training is ruled out
  twice over: memory, and the documented rule that any MLX call outside
  the single `ASR_POOL` thread aborts the process (see `decode_audio` in
  dictate.py). The trainer runs only on AC power, only when no dictation
  session is active, aborts on user activity, and writes one artifact:
  a LoRA adapter file (≤ 50 MB) plus a candidate report.
- **Adapter loading** in the Tiny decode path: base weights stay the
  pinned `mlx-community/whisper-tiny` revision; the adapter applies only
  if a valid promotion receipt names its hash. Rollback is deleting one
  file; the base model is untouched by construction.

### (b) Bias-embedding / shallow fusion on Parakeet — not currently feasible, tracked honestly

The primary recognizer would be the right place to adapt, and it is the
one place we cannot: the helper drives FluidAudio 0.15.5 over compiled
Core ML artifacts (`parakeet_unified_encoder_int8.mlmodelc` etc., listed
in benchmark_asr.py). The weights are frozen at conversion; the public
API (`UnifiedAsrManager.transcribe(samples) -> text`) exposes no decoder
logits, no hook for shallow fusion with a personal LM, and no biasing
interface. Doing this would mean forking FluidAudio or re-exporting a
modified Core ML graph — a maintenance burden out of proportion to the
product today. Decision: **watch item**. Revisit if upstream ships a
biasing or word-boost API (their streaming work may bring one); the
Training Pantry built for (a) is the fuel either way.

**Recommended order: (c), then (a). (b) waits on upstream.**

## Mechanism (paths c and a)

Nightly job, one entry point, two phases, both pure-testable:

1. **Mine** (path c): scan the bounded transcript window; emit
   `CorrectionCase`-shaped candidates; feed
   `PersonalRegressionLab.record` / evaluate; promotion and quarantine
   exactly as the online path. Runs everywhere, no new consent.
2. **Train** (path a, only if the pantry is on and has ≥ 20 minutes):
   split pantry pairs by day into train/held-out; LoRA fine-tune Tiny;
   evaluate the candidate adapter against the **base** model on (i) the
   held-out pantry days and (ii) a fixed checked-in synthetic corpus, via
   the `benchmark_asr.py` harness with the shared normalizer. Emit a
   content-free candidate report (WER deltas, case counts, hashes). The
   trainer cannot activate anything — same separation the
   [activation receipt](../../wiki/activation-receipt.md) pattern
   enforces everywhere: recording and evaluating are not approving.

Promotion gate for an adapter (the acoustic analog of the Personal
Regression Lab): held-out pantry WER improved by a pinned margin, zero
per-utterance regressions above a pinned tolerance on held-out pairs,
zero regression on the synthetic corpus, adapter file within size bound.
Contradicting later evidence — a week of declining zero-edit rate with
the adapter active versus the trailing baseline — demotes: the adapter
is quarantined and Tiny falls back to base, mirroring how contradicting
evidence demotes a learned prior.

## What exists already vs what is new

Exists:

- Labels: verified receipts + `zero_edit` metric in `transcripts.jsonl`
  (`record_paste_outcome`, `observe_paste_outcome`).
- The whole promotion philosophy and machinery for (c):
  `personal_regression.py`, `shadow_candidate_gate.py`,
  `apply_learned_fixes`, inspectable/forgettable UI rows.
- Pinned base models, `benchmark_asr.py` + normalizer for candidate
  evaluation, launchd service templates, 0600 storage discipline,
  support-bundle exclusion patterns.

New:

- (c): the offline miner (pure module + nightly entry point).
- (a): Training Pantry store + consent surface + wipe controls;
  the separate-process trainer; adapter loading + promotion receipt in
  the Tiny path; the acoustic promotion gate; PRIVACY.md changes.
- (b): nothing — deliberately.

## Evidence gate

Path (c) inherits the shipped gate: the Personal Regression Lab with
zero regressions is already the activation bar for every mapping, and
each mapping stays individually inspectable and forgettable. No new
receipt.

Path (a) is receipt-gated per the house pattern before the adapter ever
loads: receipt `acoustic-adaptation-v1`, 0600, policy-pinned (margins,
size bounds, minimum pantry minutes and distinct days — proposed: ≥ 20
minutes across ≥ 5 days so one bad session cannot dominate),
model-pinned (base Tiny revision + adapter hash; either changing
invalidates), evidence-pinned (candidate report SHA-256), manual review
non-defaultable. The review step is physical: the operator replays a
sample of pantry takes next to base-vs-adapter transcripts (the
`--review` playback pattern from
[evidence capture](../../wiki/evidence-capture.md) — "nothing is
approved here; this is only playback") and attests the adapter did not
degrade any reviewed take. Synthetic audio can never enter the pantry:
the store accepts only utterances that arrived through the live capture
path with a verified receipt id, and the evaluator refuses mixed
provenance, same as every other gate.

## Risks and failure modes

- **Self-training feedback loop**: labels are the model pipeline's own
  accepted outputs, so the fine-tune can entrench errors the user
  tolerates rather than corrects. Mitigations: zero-edit + verified is
  the *floor* filter, held-out-by-day evaluation, the demotion trigger
  on declining live zero-edit rate, and (c)'s correction mining pulling
  in the opposite direction (things the user actively fixed).
- **Audio at rest** is a genuine posture change; the design treats it as
  the headline risk, not a footnote: separate consent, hard caps, wipe
  on disable, never in bundles, never synced. If that surface is judged
  unacceptable, path (a) is simply off the table and (c) still stands.
- **Guest speakers / shared Macs**: an adapter tuned to one voice can
  degrade another's dictation with no visible cause. The preference copy
  must say the adaptation is speaker-specific, and demotion-on-decline
  is the backstop.
- **Nightly Metal contention** with the warm keep-alive tick and an
  active user: separate process, AC-only, activity-abort, and the
  trainer defers to any live dictation.
- **Quiet drift**: Tiny's calibrated confidence (`FAST_ACCEPT_CONFIDENCE
  = 0.70` reasoning in dictate.py) was tuned for base Tiny; an adapter
  shifts the confidence distribution. The candidate report must include
  the confidence histogram delta, and the routing constants are part of
  the pinned policy so a receipt predates any constant change.

## Staged rollout

1. **Stage 0** — ship (c): miner + lab promotion, on by default within
   the existing personalization bounds (it is the same shipped feature,
   fed better).
2. **Stage 1** — Training Pantry behind explicit consent, storing and
   wiping only; no trainer. Soak the storage bounds and the consent UX.
3. **Stage 2** — nightly trainer producing candidate reports only;
   nothing loads them. Measure real training time, adapter sizes, WER
   deltas on this machine's data.
4. **Stage 3** — receipt-gated adapter loading in the Tiny path, off by
   default, demotion telemetry active.
5. **Stage 4** — revisit (b) against the then-current FluidAudio API.

## Open questions

- Is 60 minutes the right pantry cap, and should takes be
  quality-filtered at write time (clipping, SNR) with the meter logic
  the capture harness already has?
- Does an adapter improve *stable-prefix agreement* (Tiny vs Parakeet
  prefix match rate) measurably? That is the metric closest to the felt
  benefit on the main path, and it is computable from existing runtime
  state — worth adding to the candidate report.
- Word-level timestamps for pantry pairs come from Whisper decodes, but
  final text comes from Parakeet; pairs store utterance-level alignment
  only. Is that enough for LoRA on Tiny (whole-utterance loss), or do we
  need forced alignment? (Expected: whole-utterance is fine at this
  scale.)
- Cross-link: a confirmed Turbo correction from
  [draft-then-verify](draft-then-verify-asr.md) Stage 0 corpora is a
  high-value labeled pair; should the pantry accept those explicitly?
