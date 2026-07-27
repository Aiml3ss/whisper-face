# Contributor interfaces

Last reviewed: **27 July 2026**

[Architecture and interoperability](architecture-and-interop.md) describes the
boundaries. This page is its contributor-facing companion: the concrete seams
you can build against, the exact identifiers involved, and — just as
importantly — what is *not* stable and should not be depended on.

> **Read this first.** Whisper Face ships **no public SDK, no public ABI, no
> network service, and no supported cross-process integration.** Everything
> below is a seam *inside* the repository. Some of it is a strict versioned
> contract with conformance tests; none of it is a compatibility promise to a
> third-party binary. If you are looking for a way to drive Whisper Face from
> another application today, the honest answer is that there is not one.

## Stability tiers

Every seam on this page falls into one of three tiers. The tier is the whole
of the promise.

| Tier | Meaning |
|---|---|
| **Conformance contract** | Versioned, closed-schema, covered by a dedicated conformance test. Changing it requires a reviewed schema change. Safe to build against *inside the repository*. |
| **Tracked contract** | Names and shapes are treated as a contract by tests and the wiki, but there is no version number and no deprecation policy. Expect to update in lockstep. |
| **Internal** | May change in any commit without notice. Underscore-prefixed names are always internal, even when they are reachable. |

---

## 1. Voice Input Protocol — conformance contract

Three modules, deliberately layered so validation cannot be bypassed by
encoding or transport.

### `voice_input_protocol.py` — the contract

- `SCHEMA_VERSION = 1`
- `EVIDENCE_SCOPE = "in-process-conformance-only"`
- `MAX_TRANSCRIPT_CHARS = 100_000`
- Error type: `ProtocolError(ValueError)` — the only one.
- `__all__` is declared, and is the surface: `ADAPTER_PROFILES`,
  `EVIDENCE_SCOPE`, `MAX_TRANSCRIPT_CHARS`, `SCHEMA_VERSION`,
  `AdapterProfile`, `MessageKind`, `ProtocolError`, `ProtocolMessage`,
  `VoiceInputProtocolSession`, `validate_transcript`.

Every message envelope carries exactly five fields — `schema_version`,
`utterance_id`, `sequence`, `kind`, `payload` — and unknown fields are
rejected rather than ignored.

| `kind` | payload fields |
|---|---|
| `capture_proposal` | `profile_id`, `target`, `paste`, `readback`, `selection_bound`, `evidence_scope` |
| `stable_prefix` | `text`, `stable_through_ms` |
| `final_text` | `text` |
| `commit_receipt` | `state`, `reason`, `paste_attempted`, `recoverable` |
| `ack_receipt` | `commit_sequence`, `accepted`, `outbox_dismissed` |
| `cancellation` | `reason` |

An utterance is one `capture_proposal`, zero or more monotonically advancing
`stable_prefix` messages, one `final_text` that retains the last stable prefix,
and then either `commit_receipt` + `ack_receipt` or a `cancellation`. The
cancellation vocabulary is closed: `user_cancelled`, `capture_failed`,
`superseded`.

`commit_receipt` state and reason come from `insertion_integrity` and only
these pairs are legal:

| `state` | permitted `reason` |
|---|---|
| `verified` | `commit_verified` |
| `unverifiable` | `target_unreadable`, `readback_unavailable` |
| `conflict` | `focus_drift`, `selection_drift`, `surrounding_text_drift`, `readback_conflict` |
| `unresolved` | `paste_outcome_unknown` |

Note that `insertion_integrity` also defines `pending` and
`commit_verified_edge_whitespace`, and neither is legal in a protocol
`commit_receipt`. The runtime vocabulary is deliberately wider than the wire
vocabulary.

The five capability profiles in `ADAPTER_PROFILES` are **synthetic test
fixtures, not shipped integrations**: `readable-complete`,
`readable-no-readback`, `opaque-reviewed`, `clipboard-unavailable`,
`target-unavailable`. `AdapterProfile.selection_bound` is derived, true only
when `target == "readable"`.

Bounds worth knowing: `utterance_id` is 1–128 characters, alphanumeric-first,
`[alnum-_.]`; integers are bounded to `0..2_147_483_647`; text is bounded by
`MAX_TRANSCRIPT_CHARS`.

### `voice_input_protocol_wire.py` — the codec

`encode_message` / `decode_message`, `MAX_FRAME_BYTES = 1_048_576`. Encoding is
canonical UTF-8 JSON (`sort_keys`, no spaces, `allow_nan=False`), and decoding
enforces canonicality by re-encoding and comparing. `NaN` and `Infinity` are
rejected. All message validation is delegated to the contract above; the codec
adds no vocabulary of its own.

### `voice_input_protocol_transport.py` — a test-only local transport

`UnixProtocolServer` and `request`, with `MAX_SOCKET_PATH_CHARS = 100`,
`DEFAULT_DEADLINE_SECONDS = 1.0`, `MAX_DEADLINE_SECONDS = 5.0`, and a 4-byte
big-endian length prefix ahead of each canonical frame. Errors are
`ProtocolTransportError` and its subclasses `ProtocolTransportUnavailable`,
`ProtocolTransportTimeout`, `ProtocolTransportClosed`,
`ProtocolTransportPeerRejected`.

**This transport is test-only.** It is a Unix-domain stream socket with a 0600
endpoint, one request and one response per connection, sequential handling, and
same-UID peer checks where the platform exposes peer credentials. It has no
background loop, it is not wired to dictation, and `serve_once` returns `False`
on failure without ever reflecting an error payload onto the wire. Neither
`dictate.py` nor `whisper_face_gui.py` imports it. Its only non-test consumers
are `macos_networkless_worker.py` and `macos_networkless_worker_process.py`.

Do not build a third-party client against it. It is a local foundation for
proving the contract holds across a process boundary, not an IPC API.

**Known rough edge:** `_peer_uid` is underscore-private yet is the default
value of a public keyword argument on both `UnixProtocolServer` and `request`.
Pass your own reader rather than importing it.

Verify with:

```sh
uv run tests/test_voice_input_protocol.py
uv run tests/test_voice_input_protocol_wire.py
uv run tests/test_voice_input_protocol_transport.py
```

---

## 2. `GUIActions` — tracked contract

`GUIActions` (`whisper_face_gui.py`) is the seam between the running runtime
and the native Mac window. It is a **frozen dataclass of callables**, not a
`Protocol` and not an ABC. Every field has a working no-op default, so
`GUIActions()` constructs successfully — that is what makes the window testable
and renderable without a runtime.

The consuming side is small and is the part to build against:

```python
from whisper_face_gui import GUIActions, create_gui

gui = create_gui(GUIActions(status_snapshot=my_status, set_face=my_set_face))
```

- `create_gui(actions, *, locale="en") -> WhisperFaceGUI` builds but does not
  display the window.
- `WhisperFaceViewModel(actions, *, locale="en")` is the pure state/actions
  layer underneath, and is where behaviour is testable without AppKit.
- `scripts/window_render_probe.py` constructs a bare `GUIActions()` and renders
  the window; it is the worked example.

The dataclass currently declares 65 callables covering status and evidence
snapshots, face selection, hotkeys, sound themes, recent dictations and undo,
the gated feature toggles, voice-object and demonstration drafts, risky-action
confirmation, retained spans, tones and snippets and vocabulary, correction and
keyword forgetting, pause/resume, diagnostics, Point-and-Speak, and
Drop-to-Target.

Two rules matter more than the list:

1. **Defaults are part of the contract.** When a runtime does not supply a
   callable, the window must still behave. `describe_hotkey`, `set_hotkey`, and
   `set_undo_hotkey` default to
   `{"accepted": False, "reason": "unsupported_key", "name": "", "label": "", "shared_modes": ()}`;
   `insert_recent_dictation` defaults to
   `{"inserted": False, "reason": "unavailable"}`; `undo_last_dictation`
   defaults to `{"undone": False, "reason": "nothing_to_undo"}`. If you add a
   field, give it a default that fails closed.
2. **Guard logic lives in the runtime, never in the window.** Hotkey conflict
   rules, permission checks, and confirmation nonces are the runtime's job. The
   window asks and renders the answer.

**Known rough edge:** `describe_hotkey` is the only field typed
`Callable[..., ...]`, so its call shape is not pinned by the type, and its
default returns `shared_modes` as a tuple while real implementations may return
another sequence type.

This is a **tracked** contract, not a versioned one. There is no schema
version and no deprecation window; `dictate.py`, the window, and the tests move
together.

---

## 3. Capture-harness artifact schemas — tracked contract

The harnesses in `scripts/capture_*.py` record physical sessions and emit
transcript-free JSON artifacts. They structurally cannot approve anything: an
AST-level test asserts they import no activation module and cannot even declare
`--confirm-manual-review` or `--approve-runtime`.

If you consume one of these artifacts, **consume its honesty fields too**. They
are the difference between evidence and a number.

| Producer | `artifact` | Honesty fields you must check |
|---|---|---|
| `capture_app_matrix.py emit` | `physical-app-insertion-matrix` | `physical_evidence`, `evidence_scope`, `coverage.extrapolated`, `claims.four_nines_claim`, `claims.fifty_app_claim` |
| `capture_lifecycle_evidence.py emit` | `physical-lifecycle-evidence` | `physical_evidence`, `evidence_scope`, `coverage.extrapolated`, `discharges_physical_validation`, `passive_observation.discharges` |
| `capture_delayed_cleanup_cases.py summary` | `physical-delayed-cleanup-coverage` | `evidence_scope`, `receipt_written_by_this_tool`, `manual_review_flag_set_by_this_tool`, `gate_shortfalls` |

All three carry `schema_version`, `privacy: "transcript-free"`, and
`generated_utc`. The app matrix computes its `evidence_scope` from what was
recorded — `operator-attested-physical-session`,
`runtime-observed-passive-use`, or
`mixed-operator-attested-and-runtime-observed` — and never sums operator
verdicts with machine observations into a single figure.

`capture_delayed_cleanup_cases.py emit` writes a *different* shape from its
`summary`: a bare `{"records": [...]}` file consumed by
`delayed_cleanup_activation.py`, where each record carries
`source: "caller-attested-physical"`.

**Known rough edge, and the reason the publisher is strict.** The delayed-
cleanup coverage artifact is the only one of the three with **no
`physical_evidence` boolean**, and its `evidence_scope` is a hard-coded literal
rather than computed from what was recorded. A consumer cannot ask that
artifact whether anything physical happened; it has to infer it from
`cases_recorded`. `public_scorecard.py` therefore requires *both* the scope
literal and a non-zero recorded-case count before it will publish that artifact
as physical. Adding a computed `physical_evidence` field to that harness would
let the guard be uniform.

### Publishing an artifact

```sh
uv run public_scorecard.py publish \
  --revision <40-character commit> \
  --physical-artifact <artifact.json> \
  --environment <environment.json> \
  --format markdown
```

The environment file is a closed schema and every field must be concrete:

```json
{
  "schema_version": 1,
  "environment_id": "m4-pro-macbook-pro",
  "hardware": "Apple M4 Pro MacBook Pro",
  "os_name": "macOS",
  "os_version": "26.0.1",
  "whisper_face_revision": "<40-character commit>",
  "python_version": "3.12.13",
  "software": [{"name": "Whisper Face", "version": "0.2.0"}]
}
```

Placeholder values such as `unknown`, `tbd`, or `n/a` are rejected, as is a
short revision. The publisher will not attach a physical claim to an unnamed
machine.

---

## 4. Activation-receipt formats — conformance contract

Five capabilities ship **off** and unlock only from a private receipt this
machine produced from its own physical evidence, after an explicit manual
review. The formats differ more than they should, so read the specific one.

| Module | Identity | Shape |
|---|---|---|
| `acoustic_calibration_activation.py` | `kind: "whisper-face/acoustic-calibration-activation"` | Single receipt: `schema_version`, `kind`, `settings`, `evidence`, `policy`, `manual_review`, `measurement_mode`, `source_report_sha256` |
| `acoustic_keyword_activation.py` | `kind: "whisper-face/acoustic-keyword-activation"` | **State file with an `entries` list**, not a single receipt: `schema_version`, `kind`, `runtime_effect`, `entries` |
| `relisten_activation.py` | `kind: "whisper-face/relisten-runtime-activation"` | Single receipt, and the only one that pins `engine_id`, `model_repo`, `model_revision` |
| `delayed_cleanup_activation.py` | `suite_id: "mac-delayed-cleanup-v1"` | Single receipt, widest field list, `evidence_scope: "caller-attested-physical-only"` |

`acoustic_time_machine.py` has **no activation receipt and writes no JSON at
all**. Its `BufferReceipt` is an in-memory, content-free record of one
operation and outcome (`Operation` and `Outcome` enums), and nothing is
persisted. Do not look for a file.

### How a receipt is actually verified

There is **no HMAC and no cryptographic machine binding anywhere in this
system**. Say so plainly rather than implying otherwise. A receipt is unsigned,
content-free JSON defended by four mechanisms:

1. **Closed schema.** A key set that is not exactly the expected set is
   rejected outright.
2. **Policy pinning.** The receipt embeds the thresholds it was approved under,
   and validation compares them against the current module constants. Raising a
   threshold invalidates every existing receipt.
3. **Evidence pinning.** A SHA-256 of the canonical source report or records.
4. **Filesystem binding.** Atomic `mkstemp` + `chmod 0600` + `os.replace`.

Machine binding is *procedural only*: the runbook forbids copying a receipt
between machines. Nothing in the code enforces it. Treat that as a known
limitation, not a guarantee.

Every gate also fails closed on failure: reasons collapse to closed strings
such as `receipt-invalid` and `receipt-missing`, so a malformed receipt is
indistinguishable from no receipt.

### What makes evidence physical rather than synthetic

Each gate has an explicit synthetic-rejection rule, and these are the exact
rules `public_scorecard.py` re-enforces before publishing:

- **Re-listen** rejects any report where `evidence_counts["synthetic-test"]` is
  non-zero, with reason `synthetic-evidence-present`. It further requires at
  least 40 real samples, at least 20 per outcome, and exactly one engine with
  `availability == "measured"`.
- **Calibration** requires 40 caller-attested physical cases balanced across
  `clean`, `quiet`, `noisy`, `long-pause` with at least 8 each, at least 3
  improvements, and **zero** recognition or endpoint regressions.
- **Acoustic keywords** require `evidence["synthetic_cases"] == 0` and hard
  zeros for `selection_regressions`, `positive_candidate_losses`, and
  `negative_candidate_introductions`.
- **Delayed cleanup** rejects any record whose `source` is not
  `caller-attested-physical`, and requires 50 cases, per-surface and
  per-scenario minimums, and at least 15 applied *and* 15 rejected.

### Inconsistencies to be aware of (and ideally fix)

These are real and will bite anyone writing a generic receipt reader:

- `manual_review` has three shapes: a bare `True` (calibration, keyword
  entries), a nested `{"approved": True}` (re-listen), and a differently named
  `manual_reviewed` boolean (delayed cleanup).
- The evidence digest lives in three places: top-level `source_report_sha256`
  (calibration), per-entry (keyword state), nested under `evidence`
  (re-listen), and named `records_sha256` (delayed cleanup).
- `ActivationStatus` is a **different type in each module**. Calibration's
  carries `settings`/`reason`/`measurement_mode` with `ready` as a computed
  property; re-listen's carries `ready`/`reason` as plain fields. They are not
  interchangeable.
- Only calibration and keyword activation carry `measurement_mode`. Re-listen
  has no circular measurement arm, so it has no such field.

---

## What is explicitly not stable

- **Anything underscore-prefixed**, in any module, including `_peer_uid`.
- **`dictate.py` and `whisper_face_gui.py` internals.** Only `GUIActions` and
  `create_gui` are the seam; everything else in those files is internal.
- **The bounded POSIX transport and the networkless worker.** Local test
  foundations. Not IPC, not XPC, not a speech runtime.
- **Every `ADAPTER_PROFILES` entry.** Synthetic fixtures, not integrations.
- **Case corpora contents.** Cases are added and rewritten freely; the *schema*
  is what tests hold stable. See
  [reproducible corpora](benchmarks/reproducible-corpora.md).
- **Scores in the model scorecard.** Cohort-relative and re-derived on every
  run; only the pins and the measurement records are durable facts.

## How to verify a change to any of this

```sh
uv run tests/test_voice_input_protocol.py
uv run tests/test_voice_input_protocol_wire.py
uv run tests/test_voice_input_protocol_transport.py
uv run tests/test_whisper_face_gui.py
uv run tests/test_capture_app_matrix.py
uv run tests/test_capture_lifecycle_evidence.py
uv run tests/test_capture_delayed_cleanup_cases.py
uv run tests/test_public_scorecard.py
uv run tests/test_reproducible_corpora.py
uv run tests/test_performance_lab.py
uv run tests/test_repository_governance.py
```

Installer parity is a separate gate and is audited for every runtime, model,
dependency, asset, service, permission, or startup change. See
[CONTRIBUTING.md](../CONTRIBUTING.md).
