# Parakeet TDT v3: a measured path out of English-only

Status: design, not built. English stays the default and the only
supported language until each additional language earns its way in
through the bakeoff and a physical session.

## Problem

The product is English-only, three times over:

- The primary recognizer is `parakeet-unified-en-0.6b`
  (`PARAKEET_MODEL_REPO`, dictate.py) — an English model.
- Both Whisper decode paths hardcode `language="en"`
  (`transcribe_detailed`, Mac and Windows branches).
- Everything downstream is English-shaped: the glossary biasing prompt
  ("Common terms: …", `refresh_glossary`, 700-char cap), the
  deterministic cleanup lexicon (fillers, "new line", "scratch that",
  list lead-ins in parrot_core.py), the LLM cleanup prompts
  (`BASE_PROMPT`, `MODE_INSTRUCTIONS`), the hallucination screens, and
  the space-joining insertion logic.

Upstream, FluidAudio documents Parakeet TDT v3 as covering 25 European
languages, and current upstream Parakeet TDT work adds Japanese — both
are upstream claims we have not verified locally, and the exact model
repo, FluidAudio minimum version, artifact license, and per-language
quality are all unknowns this plan is structured to resolve rather than
assume. A language is not "supported" because a model card lists it; in
this product a language is supported when wrong-text-pasted risk has
been measured and bounded for it.

## Mechanism

### Stage 0 — bakeoff first, and English regression is the first gate

`benchmark_asr.py` already does engine-agnostic bakeoffs driven by the
reviewed scorecard (`benchmarks/model_scorecard.json`: schema-versioned
candidates with `model_id`, `revision`, `benchmark_engine`,
`runtime_role`, license fields, and metric criteria). The work:

1. **Add the candidate**: a `parakeet-tdt-v3` scorecard entry with a
   concrete pinned revision, provenance and license review (the
   scorecard's `license_status` discipline exists for exactly this),
   and a new `benchmark_engine` id wired to the helper.
2. **Helper compatibility**: bump FluidAudio to the minimum version
   exposing the v3 model; verify the required `.mlmodelc` asset list
   (the v3 analog of `PARAKEET_REQUIRED_ASSETS`); keep the wire
   protocol byte-identical.
3. **English regression run**: the existing 100-utterance LibriSpeech
   protocol, v3 vs the shipping `parakeet-unified-en` baseline
   (1.240% WER, 113× realtime on M4 Pro, 2026-07-21 —
   [benchmarks](../../wiki/benchmarks.md)). Gate: v3 English WER and
   p95 latency within agreed tolerance of the baseline (proposed:
   WER within +0.15 absolute, rtfx ≥ 60×). If v3 regresses English,
   the runtime plan changes shape: **two models routed by language**
   rather than a swap, at the cost of disk and load time. Decide from
   the numbers, not the release notes.
4. **Multilingual corpora**: LibriSpeech is English; per-language runs
   need public corpora with per-language references (MLS, FLEURS, or
   Common Voice subsets — chosen per language for license and
   availability, kept outside the repo like all research audio). This
   requires the harness's one real gap to be fixed: scoring currently
   uses the Whisper **English** normalizer for every hypothesis
   (`whisper-normalizer` in benchmark_asr.py's dependency block).
   Per-language normalization (or the basic multilingual normalizer) is
   a scoring-correctness prerequisite; numbers produced without it are
   not evidence.

Deliverable: a per-language scorecard table — WER, exact-match, p90
utterance WER, latency, memory — checked in as bakeoff results, each
language marked `candidate` / `not-viable` against a pinned per-language
WER bar (proposed: ≤ 6% on the chosen corpus, roughly "Tiny-on-English
quality is not good enough to paste", tightened per feedback).

### Stage 1 — a gated runtime language setting

A `language` preference (preferences.json / Settings), default `"en"`,
plus per-app override in the tones table only if demand appears. No
auto language identification in this stage: LID mistakes convert to
wrong-language pastes, a new failure class, and an explicit setting is
inspectable. Non-`en` values are selectable only for languages whose
bakeoff row is `candidate` **and** whose physical session (Evidence
gate below) produced a receipt; everything else renders disabled with
the reason.

Runtime changes when `language != "en"`:

- **ASR routing**: helper loads the v3 model (or the dual-model route
  if Stage 0 chose that); Whisper paths pass the configured language
  instead of the literal `"en"` — Tiny speculation and Turbo fallback
  are already multilingual-capable models, only the parameter is
  pinned. The `initial_prompt` glossary biasing is suppressed for
  languages where the English-shaped "Common terms:" scaffold is wrong,
  until per-language prompt templates exist; a biased-wrong prompt is
  worse than none.
- **Deterministic cleanup**: `compile_cleanup` / `quick_clean` consult
  per-language token tables for fillers, spoken structure ("nueva
  línea", "点、丸" style conventions), and list lead-ins. Absent a
  table, deterministic cleanup degrades to punctuation-safe minimal
  mode for that language — never English rules applied to non-English
  text.
- **LLM cleanup**: `BASE_PROMPT` gains an explicit output-language
  instruction; few-shot examples per language (start with the target
  language's own examples for capture mode only; compose/reply stay
  English-gated longer since their contract is broad rewrite). The
  proof-edit acceptance chain is language-neutral by construction
  (byte-exact reconstruction), which is comforting: the safety property
  survives translation even where quality lags.
- **Insertion joining**: the `continuing` logic inserts a leading space
  when joining to prior text; CJK (Japanese) needs a no-space joining
  rule keyed on script. Small, but wrong-by-default without it.
- **Hallucination screens**: `is_hallucination` phrase lists get
  per-language entries (Whisper's notorious per-language loops are
  documented in the field; collect during bakeoff transcripts).

### Stage 2 — defaults, per language, by receipt

A language moves from "selectable, labeled experimental" to plain
selectable when its receipt exists (below). English remains the global
default; there is no plan to change that.

## What exists already vs what is new

Exists:

- The bakeoff harness, scorecard schema with license review fields, and
  the pinning discipline for models and helper dependency.
- A helper whose protocol is language-agnostic (PCM in, text out) and
  whose model loading is already revision-verified at install.
- Multilingual-capable Whisper weights already shipped (Tiny, Turbo) —
  only the `language` parameter is pinned.
- Language-neutral safety spine: proof edits, protected anchors
  (regex-shaped anchors like URLs/paths/numbers carry over), insertion
  transaction, outbox, receipts.
- The activation-receipt pattern and capture-harness style for the
  physical sessions.

New:

- v3 scorecard entry, FluidAudio bump, helper asset list, possible
  dual-model routing.
- Per-language scoring normalization in benchmark_asr.py (prerequisite).
- The `language` preference and its plumbing; suppression/templating of
  the glossary prompt; per-language deterministic token tables; prompt
  language instructions; CJK joining; per-language hallucination
  entries.
- Per-language physical session harness arm and receipts.
- UI: language row in Settings, experimental labeling, receipt-aware
  enablement.

## Evidence gate

Per language, two artifacts:

1. **Bakeoff row** (offline, reproducible): the Stage 0 scorecard entry
   for that language with correct normalization, meeting the pinned WER
   bar. This is model evidence, not product evidence.
2. **Physical session receipt** `language-<code>-v1`, house pattern
   ([activation receipt](../../wiki/activation-receipt.md)): ≥ 20
   caller-attested real dictations in that language by a speaker of it,
   across at least native-text and web-text surfaces, mixing short
   commands, long-form, and number/name/URL-bearing content
   (consequence anchors are where wrong-language damage concentrates).
   Closed-choice attestation per case: text faithful / minor errors /
   unusable; anchors intact yes/no. Hard zeros: unusable cases, anchor
   corruption, English-rule cleanup artifacts (e.g. an English filler
   table deleting a real word — the specific failure the degraded
   cleanup mode exists to prevent). Recorded under
   `--measure language:<code>`; policy-pinned (WER bar, session
   thresholds), model-pinned (v3 revision + helper version),
   evidence-pinned, manual review non-defaultable. A receipt for
   Spanish says nothing about Portuguese; there is no cross-language
   inheritance.

## Risks and failure modes

- **English regression via model swap** is the biggest product risk —
  every current user is an English user. The Stage 0 regression gate
  and the dual-model fallback shape exist for it.
- **Wrong-language decode** (setting says Spanish, speaker uses
  English): produces confident garbage. Mitigation: the setting is
  explicit and visible in the HUD when non-default; per-app overrides
  deferred; auto-LID deliberately excluded until a design treats its
  failure modes as first-class.
- **Cleanup quality cliff**: deterministic English rules are years of
  accumulated fixes; other languages start from the minimal mode.
  Expectation-setting in the UI ("experimental") and the physical
  session's cleanup-artifact zero are the honest bounds.
- **Glossary/personalization mismatch**: learned fixes, priors, and
  vocabulary casing were mined from English dictations; applying them
  cross-language is wrong. Personalization stores gain a language key;
  entries apply only within their language. Until then, non-English
  sessions run with personalization read paths disabled.
- **Japanese specifically** stacks three unknowns: upstream support
  status, CJK segmentation/joining, and normalizer choice. It is last
  in the order for that reason, not first, despite being the
  headline-grabbing addition.
- **Helper memory/thermals**: a multilingual 0.6B+ model and possible
  dual-load; measure `peak_memory_mb` in the bakeoff (the scorecard has
  the column, currently null even for shipped models — fill it this
  time).
- **License**: NVIDIA Parakeet artifacts and their conversions must
  clear the repository's license review before any pin lands; the
  scorecard's `license_status: review-required` state is the tracked
  vehicle.

## Staged rollout

1. **Stage 0a** — normalization fix in benchmark_asr.py (standalone,
   useful regardless).
2. **Stage 0b** — v3 scorecard entry + helper bump behind a build flag;
   English regression run; go/no-go and swap-vs-dual decision.
3. **Stage 0c** — per-language bakeoffs for an initial shortlist
   (proposed: the languages the operator can actually attest or
   recruit attestation for — physical evidence needs a real speaker,
   which bounds the honest shortlist more than model cards do).
4. **Stage 1** — `language` preference, experimental-gated; runtime
   plumbing; degraded-mode cleanup; `--measure language:<code>`
   sessions per language.
5. **Stage 2** — receipts flip languages to plainly selectable.
   English default unchanged.

## Open questions

- Exact upstream artifacts: which FluidAudio version, which model repo,
  streaming support for v3 (interacts with
  [streaming-commit](streaming-commit.md), whose EOU model is
  English-only — multilingual streaming commit is out of scope until
  upstream signals exist), and whether the Japanese-capable variant is
  the same artifact or a separate model.
- Does v3 expose anything Whisper-prompt-like for biasing? If not,
  keyword prompt priority and glossary biasing remain
  Whisper-path-only in every language, and the per-language value of
  the Whisper fallback grows accordingly.
- Who attests non-English physical sessions? The gate requires a real
  speaker; for languages without one available, the honest state is
  "bakeoff-candidate, no receipt, not selectable" — is that acceptable
  as a long-lived state in the Settings UI?
- Per-language deterministic token tables: hand-written per language,
  or mined from that language's bakeoff transcripts plus review? Start
  hand-written for the shortlist; mining is a later economy.
- Windows parity: faster-whisper is multilingual, so Stage 1 plumbing
  could reach Windows cheaply — but with no Parakeet there, quality
  rides entirely on Turbo latency. Separate decision, not bundled.
