# Whisper Face threat model

Status: baseline for the Mac-first local product. Update this document whenever
a new network service, updater, context source, model provider, or insertion
adapter changes a trust boundary.

## Assets to protect

- live microphone audio and the Flight Recorder RAM buffer;
- raw and cleaned transcripts, snippets, dictionary entries, correction rules,
  and regression cases;
- focused application identity, selection, nearby document content, filenames,
  and clipboard samples;
- recovery text in the Voice Outbox;
- the authority to paste, invoke a bounded command, or alter a snippet;
- release signing credentials and the integrity of installers, source, models,
  dependencies, and update metadata.

## Trust boundaries

```text
microphone -> local ASR -> VoiceIR/Compiler -> local Ollama -> insertion lease
    |              |               |                |              |
    + RAM audio    + model files   + context        + localhost    + target app

release producer -> Git commit -> signed/notarized artifact -> local installer
phone/LAN client ---------------------------------> port 8787 endpoint
```

The operating system permission system, installed user account, local model
processes, source checkout, and target applications are separate principals.
Loopback reduces exposure but does not make a process trustworthy. The current
port 8787 endpoint is a larger boundary because it binds to network interfaces.

## In-scope adversaries and failures

- an untrusted document or clipboard value trying to prompt-inject cleanup;
- focus or selection changing while recognition is in flight;
- delayed callbacks, retries, or crashes causing duplicate or wrong-target
  insertion;
- another local process reading private files, impersonating Ollama, changing a
  model, or calling the transcription endpoint;
- another reachable LAN device sending audio or exhausting the endpoint;
- a compromised dependency, model host, release artifact, mirror, or update
  manifest;
- accidental developer release from a dirty tree or an ambiguous source tag;
- logs, backups, crash reports, or screen sharing exposing local text.

Physical compromise of an unlocked account, malicious kernel/firmware, and a
target application intentionally recording pasted text are outside what the
application alone can prevent. They remain important deployment risks.

## Existing controls

| Risk | Current control | Residual risk |
|---|---|---|
| Context invents speech | Context is untrusted evidence; protected anchors and Proof Edits constrain cleanup. | A local model can still produce a bad candidate; users must be able to inspect results. |
| Wrong or duplicate paste | Mac Insertion Lease, one attempt, bounded readback, terminal receipt, and RAM-only Voice Outbox. Reviewed OpenAI opaque editors also require a stable window and unchanged input counters from hotkey press through commit. | Opaque compatibility cannot detect a programmatic same-window focus change; its result is unverified, excluded from learning, and is not generalized to other apps. |
| Persistent audio | Hold-to-talk and Flight Recorder keep audio in RAM; recorder is opt-in and visibly indicated. | OS/audio drivers and unrelated software are outside the process boundary. |
| Private-state disclosure | Gitignored files, installer-created user-only permissions, bounded state. | The logged-in user, backups, and privileged software can read the files. |
| Model drift | Whisper, Parakeet, FluidAudio, and Qwen artifacts/revisions are pinned and verified. | Upstream package installers and first download remain supply-chain dependencies. |
| Release substitution | Exact Git archive, SHA-256 manifest, SHA256SUMS, Apple Developer ID signature, notarization, and stapling for public Mac releases. | The update manifest is not yet independently threshold-signed. |
| Source/license mismatch | Artifact includes exact corresponding source and notices; manifest binds its full revision and source URLs. | A distributor can violate policy; recipients can compare the digest and source. |
| LAN endpoint abuse | Intended for a trusted local network; source and license endpoints disclose the running revision. | Port 8787 is unauthenticated and not safe to expose to the internet. |

## Security invariants

1. Cleanup may format evidence but may not become acoustic truth.
2. Final insertion is at most one attempt and must honor the captured target.
3. Ambiguous insertion is not retried and does not train personalization.
4. Audio is not persisted unless a future, explicit product decision changes
   the privacy contract.
5. A public release identifies one full Git commit, carries corresponding
   source and notices, and is verifiable before installation.
6. A production Mac artifact is signed, notarized, stapled, checksummed, and
   paired with rollback metadata. Unsigned artifacts are previews only.
7. New outbound data flows require explicit consent and an updated privacy
   promise and threat model.

## Open security work

- bind or authenticate the experimental network endpoint before presenting it
  as safe outside a trusted LAN;
- sandbox model and capture work into least-privilege processes;
- add independent update-manifest signing and key-rotation/revocation policy;
- publish repeatable dependency/model provenance attestations;
- add automated privacy regression tests for log redaction and network egress;
- commission an external review of capture lifecycle and insertion adapters.

Report possible invariant violations privately through [SECURITY.md](../../SECURITY.md).
