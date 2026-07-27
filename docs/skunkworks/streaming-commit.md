# Streaming commit: insert stable text while the user is still talking

Status: design, not built. Ships off. Nothing in this document is a claim
about current behavior; current behavior is one paste per utterance
([ADR-0003](../adr/0003-transactional-insertion.md)).

## Problem

Today the target application receives text exactly once, at key release,
after the full VoiceIR compiles. The HUD shows the
[stable prefix](../../wiki/stable-prefix.md) live, but a ninety-second
dictation still lands as one block at the end. For long-form writing the
user watches a caption instead of their document.

ADR-0002 anticipated the fix and named its condition: "Target-application
Semantic Commit is permitted only behind an explicit preference and only
while the original focus and insertion receipt remain verifiably safe."
The glossary already defines Semantic Commit as publishing a Stable Prefix
"to the HUD or, when explicitly enabled and focus-safe, the target
application" (CONTEXT.md). This design is that path: commit-as-you-speak,
built as repeated bounded appends under the existing insertion lease,
exactly-once per span instead of exactly-once per utterance.

The reason this is worth doing locally: a cloud dictation product that
streams text into third-party applications must either stream provisional
hypotheses (and destructively retract them when the hypothesis changes) or
wait for its server to declare text final, which costs at least one
network round trip per revision plus jitter, on top of upload of the
audio itself. Retraction into an arbitrary AX field is unsafe at any
latency — the user may have typed meanwhile. The safe alternative,
commit-only-stable-text with per-span verified readback, requires the
recognizer, the stability judgment, and the AX readback loop to sit on
the same machine. That is a structural advantage, not a benchmark claim;
no cross-product measurement exists
([capabilities](../capabilities.md), measurement posture).

## Upstream dependency

FluidAudio (the helper's only dependency, pinned `exact: 0.15.5` in
`native/ParrotASRHelper/Package.swift`) documents a
`SlidingWindowAsrManager` for streaming transcription and a Parakeet EOU
120M model providing streaming recognition with end-of-utterance
detection, English only. Both are upstream claims we have not exercised;
whether they exist at 0.15.5 or require a version bump is the first
verification task (see Open questions). A pin bump is a reviewed change
with its own model-revision pinning, same as the current
`parakeet-unified-en-0.6b` pin in `dictate.py`
(`PARAKEET_MODEL_REPO` / `PARAKEET_MODEL_REVISION`).

## Mechanism

### 1. Helper streaming mode

`parrot-asr-helper` gains a `--stream` subcommand beside `--server`,
`--preload`, and `--verify` (`native/ParrotASRHelper/Sources/parrot-asr-helper/main.swift`).
The batch protocol stays untouched: `--server` remains framed Float32 PCM
in (8-byte little-endian sample count, 10-minute cap), one JSON line out.

`--stream` speaks a session protocol:

- **In**: the same 8-byte-count framing, but each frame is an incremental
  PCM block (one audio-callback block, typically 10–100 ms). A zero-count
  frame closes the utterance session; the next nonzero frame opens a new
  one. No new wire machinery: length-prefixed frames on stdin, JSON lines
  on stdout, audio never touching disk, exactly as today.
- **Out**: one JSON line per hypothesis revision:
  `{"ok": true, "seq": N, "text": "...", "eou": false, "final": false, "processing_s": ...}`.
  `seq` is monotonic per session. `eou: true` reports the end-of-utterance
  detector firing. After the zero-count close frame the helper emits one
  `"final": true` line and resets.

The helper stays a thin adapter: it owns model loading and inference,
nothing else. Stability judgment does not move into Swift.

### 2. Runtime: stable prefix over incremental hypotheses

Today `Recorder._callback` (dictate.py) accumulates PCM, cuts chunks at
pauses, and submits decodes whose results reach the HUD through
`_caption_add`, which publishes `compiled.stable_prefix` — computed by
`VoiceCompiler._stable_prefix` (voice_compiler.py) — into `CAPTION`. That
is the entire live surface: HUD only.

In streaming mode the callback additionally forwards each block to a
`StreamFeeder` (bounded queue, dedicated thread, writes frames to the
helper's stdin; a full queue drops the stream to batch mode rather than
blocking the audio callback). Incoming hypothesis lines update a
per-utterance `StreamState`. On each update the runtime compiles a
non-finalized VoiceIR from the accumulated hypotheses — the same
`compile_voice_evidence(..., finalized=False)` path `_caption_add` uses —
and reads `compiled.stable_prefix`. The stable-prefix contract is already
what we need: with multiple hypotheses it is the longest
normalized-equal token prefix; with one hypothesis it drops the last two
tokens. The in-process voice input protocol already models this lifecycle
as prefix-monotone with non-decreasing stability timestamps
([stable prefix](../../wiki/stable-prefix.md)); the streaming commit path
must enforce the same invariant at runtime: the committed string is only
ever extended, never rewritten.

### 3. Progressive insertion: exactly-once per span

The unit of delivery changes from utterance to **span**: the delta
between the previously committed stable prefix and the current one, cut
at a token boundary, with a minimum span size (initially: commit only
when the uncommitted stable text reaches ≥ 12 words or an `eou` fires,
whichever is first) so we make few, large, cheap appends rather than
per-word writes.

Each span commit is a bounded transaction under the lease captured at
hotkey press (`capture_insertion_lease`, dictate.py):

1. Revalidate the lease exactly as `commit_insertion` does today: focus,
   selection, surrounding-text fingerprint (`insertion_integrity.py`).
   For spans after the first, the expected surrounding text is the
   original context plus our own previously verified committed text.
2. Mark the span terminal in the coordinator **before** invoking platform
   code — the same reentrancy discipline `insertion_integrity.py` uses
   for whole utterances, extended with a per-utterance monotonic
   `committed_span_seq`. A duplicate or out-of-order span raises.
3. One paste attempt of the span text, appended at the end of the
   previously committed range.
4. Readback (`insertion_readback`) over the committed-so-far range, with
   the existing per-surface windows (0.35 s Electron, 0.02 s native).
   Every span terminates in its own receipt with the existing vocabulary:
   verified, unverifiable, conflict, unresolved.

Only readable AX destinations are eligible. Opaque and terminal targets —
which today get an opaque lease sealed at release — fail closed to the
current single-paste path; streaming commit never runs against a
destination it cannot read back.

### 4. Cleanup interaction

During the stream, **deterministic cleanup only**: `quick_clean`,
`apply_learned_fixes`, vocabulary casing — the pure-compiler path
(`compile_cleanup` in parrot_core.py). The LLM is never on the streaming
path; a (1, 4) s Ollama deadline inside a per-span commit loop would be
absurd, and proof-edit validation is defined over a finished utterance.

At release, the runtime compiles the complete VoiceIR as today. Two
finishing passes follow:

- **Tail commit**: the final compiled text beyond the last committed span
  is committed as the last span (or routed to the Voice Outbox on
  failure, below).
- **LLM pass as delayed cleanup**: if `needs_llm_cleanup` says the
  utterance warranted semantic cleanup, the finished text is improved
  through the existing
  [delayed-cleanup](../../wiki/delayed-cleanup.md) machinery, unchanged:
  `build_delayed_cleanup_proposal` produces a proof-checked proposal, and
  `delayed_cleanup_merge.py` + `macos_delayed_cleanup_destination.py`
  apply only edits whose spans and anchors are untouched, user edits
  winning, overlaps refusing all colliding edits, one single-use
  compare-and-swap. Streaming commit therefore requires the delayed
  cleanup receipt as a prerequisite — a streamed utterance whose LLM pass
  cannot run safely simply keeps its deterministic text, which is the
  same fallback the synchronous path has today.

Correction learning is suppressed for streamed utterances, exactly as it
is for delayed-cleanup-scheduled ones (`learn_correction and verified and
not delayed_cleanup_scheduled` in `finish_and_process`). ADR-0002 said it
plainly: live typing stays experimental "until correction receipts can
span multiple commits reliably". Extending `PasteReceipt` observation
across a multi-span range is future work, not part of this design.

### 5. Failure modes fail closed to the Voice Outbox

- **Focus drift mid-stream**: the next span's lease revalidation fails →
  no further paste attempts, stream feeding continues (recognition is
  unaffected), and at release the entire *uncommitted tail* lands in the
  Voice Outbox with a conflict receipt. Committed spans are already
  verified text in the right destination; they need no recovery.
- **Readback conflict on a span**: same as drift — stop committing, tail
  to outbox. Never retry a span; delivery may have preceded the failure
  (ADR-0003's rule, applied per span).
- **User types mid-stream inside our range**: surrounding-text
  fingerprint mismatch at the next span → stop committing, tail to
  outbox. The user's edit is never overwritten.
- **Helper dies mid-stream**: fall back to the batch cascade for the
  whole utterance (the audio is all still in `Recorder.frames`); the
  existing lazy-restart pattern applies. Committed spans stay; the final
  compile-and-tail-commit reconciles against the readback of what landed.
- **EOU false positive**: harmless; `eou` only accelerates a span cut. It
  never finalizes the utterance — the hotkey does.

## What exists already vs what is new

Exists, reused as is:

- `VoiceCompiler._stable_prefix` and the finalized/non-finalized compile
  split (voice_compiler.py).
- The lease / exactly-once / readback / receipt machinery
  (insertion_integrity.py; `capture_insertion_lease`,
  `commit_insertion`, `insertion_readback` in dictate.py).
- Voice Outbox recovery semantics
  ([insertion transaction](../../wiki/insertion-transaction.md)).
- The delayed-cleanup merge and transactional apply
  (delayed_cleanup_merge.py, macos_delayed_cleanup_destination.py) for
  the end-of-utterance LLM pass.
- Prefix-monotone lifecycle modeling in the voice input protocol.
- The framed-PCM/JSON-lines helper wire format, the activation-receipt
  pattern, and measurement mode (`measurement_mode.py`) for recording
  candidate-arm evidence before a receipt exists.

New:

- `--stream` subcommand in the helper; FluidAudio pin bump; EOU model
  pin and verification.
- `StreamFeeder` + `StreamState` in dictate.py; span cutting policy.
- Span-sequence extension to the insertion-integrity coordinator and
  per-span receipts.
- Append-positioned paste (insert at end of committed range rather than
  replace-selection).
- The `stream-commit` measurement-mode arm, capture-harness scenario
  set, activation evaluator, and the explicit preference (off by
  default) that ADR-0002 requires even after activation.

## Evidence gate

Default off, twice over: the feature needs both a valid activation
receipt and the explicit user preference. The receipt follows the house
pattern ([activation receipt](../../wiki/activation-receipt.md)): 0600
owner-only, policy-pinned (thresholds embedded), model-pinned (FluidAudio
version, streaming model repo and revision), evidence-pinned (SHA-256 of
the canonical report), manual review as a separate non-defaultable flag,
synthetic evidence structurally refused.

Physical suite `stream-commit-v1`, recorded under
`--measure stream-commit` with every artifact carrying that label:

- ≥ 50 caller-attested streamed dictations across native-text, web-text,
  and electron-editor surfaces (≥ 12 each). Terminal editors are out of
  scope by construction (opaque targets are ineligible).
- Scenario mix, ≥ 8 each: `long-uninterrupted` (≥ 60 s dictations),
  `mid-stream-focus-drift` (must stop committing, tail to outbox),
  `mid-stream-user-edit` (must stop committing, user text intact),
  `eou-burst` (short sentences with pauses).
- Hard zeros: wrong-target writes, duplicate spans, out-of-order spans,
  user-edit overwrites, and any committed text that the final compile
  contradicts (a stable-prefix invalidation — one occurrence fails the
  suite, because it falsifies the stability contract itself).
- Timing: per-span commit-to-verified p95 ≤ 150 ms on native, ≤ 500 ms
  on Electron (readback window dominates), measured by the runtime and
  logged per span; a case with no runtime timing blocks, never guesses.
- Operator attests per case, in closed choices, that the visible text
  matched what was spoken and nothing flickered or retracted.

The capture harness follows `scripts/capture_delayed_cleanup_cases.py`'s
shape: it imports the evaluator's own vocabulary, reads the runtime's
per-span log lines, asks closed questions, and structurally cannot
approve anything ([evidence capture](../../wiki/evidence-capture.md)).

## Risks and failure modes

- **Stability contract is load-bearing.** If `_stable_prefix` ever emits
  a prefix that a later hypothesis contradicts, streaming commit turns
  that bug from a cosmetic HUD flicker into wrong text in a document. The
  suite's hard zero on invalidation, plus the two-token holdback and
  cross-hypothesis agreement rule, are the mitigations; the open question
  on measuring invalidation rates offline comes first.
- **Appends interleaved with user edits elsewhere in the field** (before
  our range): position arithmetic must anchor to the committed-range end
  found by readback, not to absolute offsets captured at press.
- **Per-span receipts multiply AX traffic** in slow apps; the span
  minimum size bounds this, and Electron's 0.35 s readback window caps
  span cadence naturally.
- **English-only EOU model** conflicts with the multilingual direction
  ([parakeet-tdt-v3](parakeet-tdt-v3.md)); streaming commit ships
  English-only until an equivalent signal exists elsewhere, and must say
  so in the preference UI.
- **Battery/thermals**: continuous decoding during the hold is more
  inference than pause-cut chunks. Measure energy in the bakeoff harness
  before deciding defaults.

## Staged rollout

1. **Stage 0 — verify upstream.** Confirm `SlidingWindowAsrManager` and
   the EOU model at a concrete FluidAudio tag; measure stable-prefix
   invalidation rate offline by replaying the existing bakeoff audio
   through the streaming API and diffing successive prefixes against the
   final transcript. No runtime changes.
2. **Stage 1 — helper `--stream` + HUD only.** Streaming hypotheses feed
   the existing CAPTION path (better live feedback, zero insertion
   change). This stage is shippable on its own merits.
3. **Stage 2 — streaming commit behind measurement mode**, spans logged,
   nothing user-visible without `--measure stream-commit`; record the
   physical suite.
4. **Stage 3 — activation receipt + explicit preference.** Off by
   default even when earned, per ADR-0002.
5. **Stage 4 (separate design) — correction receipts spanning multiple
   commits**, re-enabling personalization learning for streamed
   utterances.

## Open questions

- Does FluidAudio 0.15.5 already ship `SlidingWindowAsrManager`, or what
  is the minimum tag; and what is the EOU model's actual repo, size, and
  license under our LICENSES process?
- Measured stable-prefix invalidation rate on real streaming hypotheses —
  the two-token holdback was tuned for pause-cut chunks, not sliding
  windows. Does the holdback need to grow in streaming mode?
- Span placement when the user's caret is not at the end of our committed
  range at commit time (they clicked elsewhere but the field is
  unchanged): commit at range end (proposed) or treat as drift?
- Should `eou` from the 120M model also drive `release_should_wait_for_tail`
  (overlap with [silero-vad](silero-vad.md) — two end-of-speech signals
  need one owner)?
- Windows: no AX readback exists there at all; streaming commit is
  Mac-only for the foreseeable future. Is HUD-only streaming (Stage 1)
  worth porting?
