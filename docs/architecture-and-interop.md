# Architecture and interoperability

Whisper Face is a local-first voice-input application. Its architecture keeps
speech evidence, text transformation, and delivery decisions separate so that
an improvement in one layer cannot silently weaken another layer's safety
contract.

## Current architecture boundaries

The capture path is local and evidence-first:

```text
audio capture -> recognition hypotheses -> VoiceIR -> Voice Compiler
           -> protected-anchor/proof-edit validation -> insertion transaction
           -> insertion receipt or Voice Outbox
```

- Recognition engines and context adapters contribute evidence to `VoiceIR`.
  Context is ephemeral. The Voice Compiler selects supported spans, preserves
  protected anchors, and accepts capture-mode cleanup only as validated proof
  edits; it is not a free-form source of transcript content.
- Stable prefixes are feedback for the HUD. They are not provisional typing
  into another application. Final text is compiled from the complete `VoiceIR`.
- The insertion layer owns the destination boundary. It captures and
  revalidates an insertion lease, permits at most one paste attempt, and emits
  a receipt. Unverified or conflicting delivery is recoverable through the
  Voice Outbox rather than retried blindly.
- Correction learning is downstream of a verified receipt. Personal priors are
  local, scoped, inspectable, and regression-tested; surrounding document text
  and audio are not retained for that purpose.

The accepted decisions and their consequences are recorded in
[ADR-0001](adr/0001-voice-compiler.md),
[ADR-0002](adr/0002-semantic-commit.md),
[ADR-0003](adr/0003-transactional-insertion.md), and
[ADR-0004](adr/0004-personal-regression-lab.md). Install and release behavior
is a separate boundary governed by the
[installer release process](installer-release-process.md): the installers
provision the current checkout and do not contain a separate runtime copy.

## Voice Input Protocol: current surface

[`voice_input_protocol.py`](../voice_input_protocol.py) is a strict,
versioned, **in-process conformance contract**. It has schema version 1 and
models one utterance as a closed sequence of messages:

1. `capture_proposal` declares one fixed synthetic destination-capability
   profile.
2. Zero or more `stable_prefix` messages must advance monotonically in text and
   audio time.
3. One `final_text` must retain the last stable prefix.
4. A `commit_receipt` reports an at-most-once delivery result, followed by an
   `ack_receipt`; alternatively, `cancellation` ends an uncommitted utterance.

The contract rejects unknown message and payload fields, non-contiguous
sequences, lifecycle violations, unsupported capability declarations, and
receipt combinations that do not match insertion-integrity semantics. It
contains no destination identity, document context, socket, IPC, platform
automation, or transport specification.

The available profiles are synthetic test fixtures, not shipped integrations:
`readable-complete`, `readable-no-readback`, `opaque-reviewed`,
`clipboard-unavailable`, and `target-unavailable`. They exercise capability
and receipt behavior through the pure insertion contracts. The focused
conformance command is:

```sh
uv run tests/test_voice_input_protocol.py
```

`voice_input_protocol_wire.py` supplies deterministic, canonical UTF-8 JSON
encoding for one validated message with a strict frame-size limit. It delegates
all message and payload validation to the protocol contract.

[`voice_input_protocol_transport.py`](../voice_input_protocol_transport.py)
adds a deliberately narrow POSIX local transport around that existing codec.
It uses a Unix-domain stream socket with a 0600 filesystem endpoint, one
length-bounded canonical request and one response per connection, explicit
deadlines, sequential handling, and same-UID peer checks when the platform
exposes peer credentials. Malformed frames, deadline expiry, peer mismatch,
and handler failure close the connection without a payload-bearing error or
logging. The server has no background loop and removes only the socket path it
created on explicit shutdown. It is not wired to dictation.

[`macos_networkless_worker.py`](../macos_networkless_worker.py) is an
experimental, one-shot macOS boundary around that transport. It launches a
private child through Apple's `sandbox-exec` with a deny-default profile,
denies IP networking, permits only path-filtered Unix-socket IPC in a private
0700 temporary directory, and proves that loopback bind and outbound connect
are denied before serving one request. The boundary accepts only a
non-transcript `capture_proposal` and returns a content-free cancellation. It
is not imported by the runtime, is not XPC, and is not a speech worker yet.

## What remains outstanding

This repository does **not** currently ship a public cross-process SDK, public ABI,
network service, XPC endpoint, active sandboxed speech runtime, Windows
transport, or physical-app adapter suite. The bounded POSIX transport and
one-shot network-denial worker are local test foundations, not a stability or
compatibility promise to external clients. Any such surface needs its own
versioning, security/privacy model, adapter evidence, and release commitment
before it can be described as public.
