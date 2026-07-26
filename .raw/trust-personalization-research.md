# Research brief: personalization, evidence, and trust subsystems

Codebase research over the Whisper Face repository (2026-07-26), covering
the acoustic personalization stores, activation receipts, the regression
lab, model wallet, and the shared fail-closed design language. Line
references are against commit `b49699f`.

## Per-module briefs

**`acoustic_keyword_memory.py`** — bounded, inspectable evidence store for
hard-name pronunciation keywords. Storage + eligibility only:
`RECOGNITION_EFFECT = "none"`. A candidate keyed by (casefolded keyword,
app scope) becomes eligible after 3 distinct observations AND 2 distinct
confirmations, each deduplicated by a domain-separated SHA-256 digest of
an opaque caller evidence id, never by keyword text. Explicit corrections
record once in each channel idempotently. Exportable and forgettable; the
export omits evidence digests. No filesystem or path API by design (a
test asserts this). Capacity 256 entries with deterministic eviction that
preserves eligible candidates. The parser is closed-mapping: any drift
raises.

**`acoustic_keyword_activation.py`** — the only conversion of keyword
memory into a runtime effect, and only as prompt priority.
`build_activation_entry` requires manual review approval, a keep verdict
with reason caller-attested-physical-gain-without-regression, at least
20 positive and 20 negative physical cases, at least 3 selection
improvements, zero synthetic cases, zero regressions/losses/
introductions. Receipts embed a SHA-256 of the source evaluation.
`validate_state` re-checks every threshold at read time so hand-edited
receipts fail. `active_keywords` returns only globally scoped activations
still backed by a currently eligible memory candidate. Writes are atomic
0600.

**`acoustic_keyword_bias_evaluation.py`** — offline, transcript-free A/B
evaluator. Evidence records carry only a case token, an evidence source
(synthetic or physical-caller-attested), and closed boolean outcomes — no
audio, transcript, app identifier, or keyword text. Mixed
synthetic+physical batches are refused; any regression is a hard kill;
synthetic-only can never reach keep. Receipts always stamp
`activation_claim: False` and `recognition_authority: False`.

**`acoustic_calibration.py`** — conservative policy over the closed
16-field numeric acoustic telemetry schema. Validates internal
consistency so spliced metrics are rejected; kills on nonfinite samples
or clipping; insufficient below 8 records / 8 s or on ambiguous
separation. Emits bounded gain/noise-gate/VAD/end-silence candidates
clamped to module constants. Reverb is permanently unavailable because
the telemetry cannot measure room impulse response. Reports carry counts
and durations only.

**`acoustic_calibration_activation.py`** — the receipt layer for
calibration. Requires manual review + an activation-candidate report.
Validation re-derives everything: exact key sets, the policy block must
byte-equal current constants (40 physical cases minimum, 8 per condition
across clean/quiet/noisy/long-pause, at least 3 improvements, zero
regressions), settings must fall inside bounds imported from the policy
module, noise gate below VAD threshold, reverb None. Any exception
collapses to receipt-invalid; missing is distinguished from invalid;
writes re-validate then write atomically at 0600.

**`acoustic_time_machine.py`** — opt-in, RAM-only microspan buffer; never
records, plays, routes, persists, or transmits audio itself. Bounds: 8
spans, 2.4 s each, 10 s total, exactly 16 kHz. Random content-independent
span ids; wipes zero buffers on delete/clear/disable; receipts are two
closed enums with audio in a separate repr=False field. The runtime adds
a 60-second TTL with locked wipe.

**`personal_regression.py`** — the Personal Regression Lab. Local,
platform-independent regression gate for learned corrections; knows
nothing about audio, document text, engines, or persistence locations.
Holds exact heard-to-preferred cases (span cap 80 chars), promoted
mappings, and quarantined evaluations (caps 256/128/64). Two key
behaviors: new contradicting evidence demotes an already-promoted prior
(re-evaluated and quarantined on record_correction), and apply performs
exactly one non-recursive substitution pass to prevent mapping chains.
Deserialization replays record/propose rather than trusting stored
promotions, so a mapping unsafe under newer rules is re-quarantined on
load; a future schema version yields an empty lab.

**`shadow_candidate_gate.py`** — the single shared promotion contract for
models, prompts, dictionaries, and personal priors. Runs baseline and
candidate transforms over every shadow case; any error or regression
means quarantined and the activation callback never runs; zero
improvements means insufficient evidence; otherwise activate() runs once
and a falsy/raising activation downgrades to quarantined. The receipt
validates itself in __post_init__; case text is repr=False so it cannot
leak through tracebacks. Promotion requires clean material improvement.

**`model_wallet.py`** — provider-neutral in-process routing policy; does
not route the live runtime, discover models, or perform network calls.
Frozen profiles for the four pins (Parakeet, Whisper Tiny, Whisper
large-v3-turbo, Qwen cleanup) with capabilities, readiness, and bounded
evidence. Eligibility requires READY + revision-verified + capability
evidence fitting the request; ordering is deterministic. `execute` is
fail-closed: only an explicit typed failure receipt authorizes failover;
a provider that raises or returns a mismatched receipt raises
ProviderContractError.

**`model_wallet_shadow.py`** — non-executing advisory adapter. Runtime
evidence in is only provider id, readiness state, revision flag, and
optional capability evidence — no paths, exceptions, or model output.
The receipt self-validates: providers must be exactly the pinned set,
attempted must be False, fail_closed must equal not advisory_order, and
tied preference ranks are rejected.

**`model_readiness_evidence.py`** — read-only filesystem evidence for the
four pins; never downloads or executes a provider. A constructor
assertion raises if state is READY: filesystem evidence cannot attest
model readiness, so exact-pin success is capped at RESOLVED. Bounded
traversal, symlink containment, closed error reasons, and receipts carry
no paths.

**`cleanup_circuit_breaker.py`** — content-free breaker in front of the
local Ollama cleanup call. One call admitted at a time; a transport
failure opens a cooldown that doubles from 60 s up to 300 s; one
successful probe resets it. `release()` exists so an output-guard
rejection above the transport layer does not open the breaker. Bypasses
fall through to deterministic cleanup.

**`cleanup_proof_recovery.py`** — bounded, fail-closed recovery of an
exact edit script for an LLM cleanup candidate; no runtime authority
(benchmark-only today). Requires the caller's output guard; enforces
symbol/punctuation eligibility and lexical proofs (compiler proofs first,
then a memoized dynamic proof limited to fillers, structure markers, the
one-word actually-correction, and a bounded contiguous scratch-that
abandonment); derived hunks must replay to exactly the candidate.
Rejection returns the source unchanged. Receipts are counts and fixed
categories, never text or content-derived digests.

**`insertion_integrity.py`** — pure exactly-once insertion contracts.
Leases record destination id, selection range, and a SHA-256 fingerprint
of surrounding text (the text itself is never retained). The coordinator
marks entries terminal before invoking platform code so reentrancy cannot
double-paste; exceptions leave entries unresolved and recoverable because
delivery may precede failure and retrying risks duplication. Verified
entries are removed and tombstoned; others stay recoverable up to 20 with
a 256-entry tombstone ring. `recoverable_count()` exists so a count can
be shown without constructing payloads.

**`risky_action_confirmation.py`** — two-factor (voice then click)
ceremony that never executes anything. Four closed risk classes; a
30-second monotonic window; a click before voice does not advance; expiry
is evaluated lazily; a pending proposal cannot be forgotten. The runtime
consumes the exact phrase before compilation, captions, logging,
clipboard, or insertion, and even a confirmed terminal has no payload or
callback attached.

**`compatibility_fingerprint.py`** — text-free local aggregation of
insertion capability/outcome buckets, all closed-allowlist. Export
returns None unless opted in, requires a minimum count, and is
size-capped; a validator re-checks emitted payloads. Deliberately has no
transport and is not wired into the runtime.

## The activation-receipt pattern

Four features ship off and can only turn on via a receipt this machine
produced from its own physical evidence:

- Producers are `uv run` benchmarks taking a private manifest plus
  `--approve-runtime` and `--confirm-manual-review`
  (calibration, keyword priority, selective re-listen, delayed cleanup).
- Consumers in the runtime load once and fall back to defaults when the
  receipt is missing or invalid — the two are indistinguishable.
- Policy pinning: receipts embed the thresholds they were approved under
  and validation compares them to current module constants; bumping a
  threshold invalidates every existing receipt.
- Model pinning: the re-listen receipt pins engine, repo, and revision.
- Evidence pinning: receipts carry a SHA-256 of the source report.
- Manual review is a separate non-defaultable input checked before any
  evidence.
- Synthetic evidence can never activate.
- Backing state must persist: keyword activations re-join against
  currently eligible memory; forgetting a keyword removes its activation.
- Writes are atomic 0600 and every receipt path is gitignored.

## Learned corrections to Personal Priors

Corrections are captured only after a VERIFIED insertion receipt, by
observing the exact pasted range for 10 seconds. Only word-shaped
respellings qualify (similarity 0.4-1.0 exclusive, alnum, 2-30 chars,
at most 3 per dictation). Each accepted pair records into the lab and
proposes at 2 same-app or 3 global observations (ADR-0004). Proposal
runs the cheap evaluation first, then the shadow gate over the whole
suite with an opaque candidate id; anything but PROMOTED quarantines.
A transient scope-counterfactual case makes global candidates
measurable without persisting synthetic evidence. Promoted priors reach
recognition through `compiler_personal_priors`, which also suppresses
legacy fixes whose heard-term is gated by promotion or quarantine.

## The Counterfactual Context Firewall

`context_firewall_receipt` recompiles the same VoiceIR with context
candidates and personal priors stripped and reports aggregate-only
differences. Influences are protected when the changed token overlaps a
risk span, is a protected anchor, or when the replacement introduces a
new factual/code-shaped anchor. Any protected influence quarantines the
receipt disposition; otherwise promotion-candidate or no-effect. Mode is
hardcoded shadow-only: it cannot change text, cleanup, insertion, or
routing. The runtime allowlists every mode/disposition/reason string on
read and coerces unknowns to unavailable.

## Privacy invariants

- Never stored or logged: raw audio, transcripts, surrounding document
  text, keyword text in routine status, case tokens in reports, paths in
  readiness receipts, content-derived digests in recovery receipts,
  nearby destination text (only a fingerprint), spoken ceremony text.
- RAM-only: the time machine, the Voice Outbox coordinator, the risky
  ceremony, shadow-gate receipts.
- Hashed app scopes: `hash_app_scope` uses a per-installation random
  salt (currently a designed-in capability; the live path records global
  scope). The regression lab stores plain bundle ids deliberately so
  mappings stay inspectable and forgettable by name, compensating with
  span caps and no audio/timestamps/surrounding text.
- Storage posture: atomic 0600 writes everywhere; all private files
  gitignored.

## Synthesis: the house style

1. Fail closed, and make broken indistinguishable from absent.
2. Content-free receipts as the universal boundary object, often
   self-validating in __post_init__.
3. Bounded evidence in both directions, with deterministic eviction.
4. Layered authority: evidence, eligibility, and activation live in
   separate modules that each refuse the next one's power.
5. Shadow-first: measure the counterfactual before letting anything act.
   Several modules are deliberately not yet wired
   (compatibility_fingerprint, cleanup_proof_recovery,
   model_wallet.execute) and the docs state that plainly.
