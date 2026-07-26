# Research brief: voice-action foundations

Codebase research over the Whisper Face repository (2026-07-26), covering
voice objects, the inbox, point-and-speak, drop-to-target, the input
protocol trio, demonstration drafts, the risky-action ceremony, and the
support bundle. Line references are against commit `b49699f`.

## Per-module briefs

**`voice_objects.py`** — pure, side-effect-free projection layer. A
VoiceObject is an opaque id plus typed VoiceFacts from a closed five-role
vocabulary (SUMMARY, DETAILS, CONTACT, WHEN, END). `project` converts
facts into exactly one of four inert drafts (PlainTextDraft, EmailDraft,
TaskDraft, CalendarDraft) with a content-free ProjectionReceipt.
Conflicting values for single-value roles reject as CONTRADICTORY_FACTS;
calendar requires a valid time range with matching tz-awareness. The
module never sends, schedules, saves, copies, types, or executes a draft.

**`voice_inbox.py`** — single-owner durable local queue; a storage
boundary, not an agent boundary. Items are QUEUED, ACKNOWLEDGED, or
CANCELLED; every mutation returns a content-free InboxReceipt. Payload
text is readable only through explicit get/items. Bounds: 128-char ids,
100k-char payloads, 256 items. Closed-schema JSON with atomic 0600
writes; in-memory state commits only after the file replace succeeds.
Idempotent enqueue; conflicting re-enqueue raises; terminal transitions
to a different terminal state raise.

**`voice_object_command_parser.py`** — strict grammar gate. Exactly three
case-sensitive forms: `create task: <title>`, `draft email to <contact>:
<body>`, `create calendar event <ISO start>: <title>`. Anything
malformed returns a rejection, never a best-effort interpretation. No
command form maps to plain text, so the inbox only ever holds
task/email/calendar drafts.

**`voice_object_inbox_bridge.py`** — the serialization seam. Accepts only
a PROJECTED/READY ProjectionResult whose draft type matches its declared
destination; encodes canonical JSON under a fixed payload kind. Decoding
is strict: closed key sets, canonical-bytes equality, and a round-trip
re-projection — a payload that could not have come from a legal
projection is rejected.

**`macos_email_compose.py`** — one in-process compose request per
session nonce. Gates in order: nonce shape, replay cache, pending pop
(one-shot), draft validation (1-64 recipients, bounded lengths, no
NULs), main-thread requirement, then NSSharingService compose with
canPerformWithItems required true. There is no send API on the object
graph at all — performWithItems opens the compose sheet and that is the
entire capability. Recipients/subject/body never enter receipts, argv,
URLs, logs, or adapter state.

**`macos_voice_draft_clipboard.py`** — one clipboard write per nonce
plus a guarded clear. The write records the pasteboard change count; a
clear nonce is issued only while the adapter owns the last write, and
clear re-checks the live change count, doing nothing when it moved. The
adapter never reads clipboard content. macOS provides no atomic
compare-and-clear, so a microscopic race window remains and is
documented.

**`point_and_speak_resolver.py`** — pure target resolution over a
caller-supplied closed accessibility snapshot; no AX API, pointer,
keyboard, clipboard, network, or write capability. Snapshots carry
names/roles/geometry/state, never values or document text. Hard filters
remove candidates contradicting declared role/selection/focus; scoring
blends exact/normalized/token name matches with small bonuses; gates are
MIN_CONFIDENCE 0.82 and MIN_MARGIN 0.12; below either returns AMBIGUOUS
with no target id. Receipts expose confidence/margin buckets, not raw
scores.

**`point_and_speak_transaction.py`** — exactly-once coordinator that
knows nothing about AX, names, or transcripts. Single-use nonces popped
under a lock before any callback; lease age capped at 2.0 s; role must
be in AX_PRESS_SAFE_ROLES (button, checkbox, radio_button, tab,
menu_item, link — text fields deliberately excluded); recheck must
return exactly True; every failure is a closed receipt state.

**`macos_point_and_speak_snapshot.py`** — read-only AX capture + lease
construction (bounds: 2048 elements, 256 targets, depth 12). The
recheck closure re-verifies trusted access, the same focused
application/window elements, and byte-exact equality of the projected
target facts. The system reader's single write verb is AXPress; its
attribute allowlist excludes AXValue and selected text. The runtime gate
is stricter than the resolver: no truncation, exact/normalized evidence,
very-high confidence bucket, wide margin bucket, else the nonce is spent
on UNAVAILABLE.

**`drop_to_target.py`** — pure decision prototype, self-declared
synthetic-decision-only. Operational targets must be visible, enabled,
drop-enabled, and accept the declared source kind and effect. The
conflict logic refuses (UNAVAILABLE, not AMBIGUOUS) when the best
name-match is a target that cannot accept the drop, rather than
redirecting to second-best.

**`macos_drop_to_target_snapshot.py`** — read-only AX evidence with a
caller-supplied role-keyed capability policy, because macOS AX has no
generic way to prove drop semantics and the module never guesses them.
Reads AXDropEnabled as its only capability fact. There is no drop
transaction module — no nonce issuer, lease, or execute path; nothing in
the repo can initiate a drag or write a pasteboard on this path. The
only runtime consumer is a Diagnostics preview restricted to four
declarable roles, stamped capability_basis caller_declared_role_policy
and execution none.

**`voice_input_protocol.py`** — versioned in-process conformance
contract. Message kinds: capture_proposal, stable_prefix, final_text,
commit_receipt, ack_receipt, cancellation, with closed payload key sets
and lifecycle rules (contiguous sequence, prefix-monotone stable
prefixes, exactly-once final/commit, terminal ack/cancel).
Receipt-state/reason pairs derive from insertion_integrity so only real
combinations validate. Five adapter profiles are synthetic fixtures, not
shipped integrations.

**`voice_input_protocol_wire.py`** — canonical JSON codec, 1 MiB frame
cap both directions, NaN/Infinity rejected, and decode requires the
frame to byte-equal its own re-encode.

**`voice_input_protocol_transport.py`** — bounded local Unix-socket
transport, POSIX-only. Refuses existing/symlink paths, chmods 0600,
verifies same-UID peers, one request/response per connection under an
explicit deadline (1.0 s default, 5.0 s max), closes on any failure with
no error reflection, and unlinks only the exact socket inode it created.
Documented as not wired to dictation; its only consumer is the
networkless-worker experiment.

**`macos_delayed_cleanup_destination.py`** — the one module here with a
real destination write, fenced accordingly. Captures one focused
editable element (AXTextField/AXTextArea only) into a snapshot whose
identity and revision are HMAC-SHA256 tokens over
pid/window/element/role/selection/text — raw text and AX identifiers
never leave. Apply pops the raw observation one-shot, requires exact
text match and trusted access, re-resolves the focused element, requires
the whole closed observation to match, then performs a single
whole-value AXValue set. Documents its own residual race: AX has no
native compare-and-swap.

**`support_bundle.py`** — rebuilds a fixed schema from a bounded snapshot
string rather than copying it. Contains only: build kind,
service/microphone/accessibility status enums, up to four model
family/status pairs, and last-result aggregates (latency, word count,
confidence, decision/anchor/edit counts). Excludes transcripts, edit
text, dictionaries, paths, usernames, hostnames, OS details, timestamps,
bundle ids, and logs. Write path rejects symlinks, verifies 0600 by
re-reading fstat, and is user-chosen via a save panel.

## The Voice Objects flow, end to end

1. Opt-in: a macOS-only, default-off Privacy setting.
2. Diversion: parsed before cleanup, LLM routing, snippets, logging, and
   insertion, only in capture mode; parser rejection and store failure
   are deliberately indistinguishable so ordinary paste is the uniform
   fallback.
3. Listing returns only item id, sequence, destination, state; the menu
   shows only a bounded count.
4. Reveal is the only content-decoding path.
5. Confirm branches mint fresh single-use nonces and re-read the item:
   email drafts can request the Mail compose sheet; task/calendar drafts
   can copy to the clipboard. Email is not copyable; task/calendar are
   not composable. Rejections burn the nonce.
6. An optional guarded clipboard clear follows a successful copy.
7. Acknowledge/cancel/purge manage lifecycle; neither compose nor copy
   acknowledges the item.

Deliberately impossible: sending, scheduling, automation, content in
receipts/logs/argv, silent retry, unexplicit reads.

## Why these are foundations without surprise execution

Every module separates a pure decision from a narrow single-verb
boundary and puts an explicit human gesture between them. Where a side
effect exists it is exactly one verb (compose sheet, one clipboard
write, AXPress on six roles, one AXValue compare-and-swap) reached
through a single-use nonce popped under a lock and re-validated against
frozen evidence. Everything else fails closed to content-free receipts.
Corpora and benchmarks stamp themselves physical_validation false. The
interop layer is an in-process conformance contract with a test-only
transport. Sending, scheduling, dragging, replaying, or agent execution
could later be built on this substrate, but none can happen today by
accident, drift, replay, or a plausible-sounding phrase.

## Gaps to flag

- `benchmark_consequence_routing.py` declares routes standard/protected/
  review but the compiler can also emit verified; no corpus case asserts
  that route.
- The delayed-cleanup destination adapter and the clipboard clear both
  document unavoidable microscopic race windows on macOS.
