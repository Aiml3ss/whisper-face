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
    |              |               |           (optional)          |
    + RAM audio    + model files   + context   + 127.0.0.1 only    + target app

release producer -> Git commit -> unsigned (ad-hoc) artifact -> local installer
Check for Updates -> git fetch of tracked branch -> checkout -> installer run
launchd -> Whisper Face.app (holds the grants) -> runtime in the checkout
local client ----------------------> desktop loopback port 8787 (GET /health)
phone/LAN client ------------------> explicit server-only port 8787 (token)
```

The operating system permission system, installed user account, local model
processes, source checkout, and target applications are separate principals.
Loopback reduces exposure but does not make a process trustworthy. Ordinary
desktop mode keeps port 8787 on loopback and serves only `GET /health`, which
returns `ok`; it accepts no audio and serves no source, license, or snippet
data. Only explicit `--server-only` mode widens that boundary to reachable
network interfaces and serves the transcription endpoint, behind a bearer
token.

## In-scope adversaries and failures

- an untrusted document or clipboard value trying to prompt-inject cleanup;
- focus or selection changing while recognition is in flight;
- delayed callbacks, retries, or crashes causing duplicate or wrong-target
  insertion;
- another local process reading private files, impersonating Ollama, changing a
  model, calling the transcription endpoint, borrowing the launcher's privacy
  grants, or editing the user-writable runtime;
- in explicit `--server-only` mode, another reachable LAN device sending audio
  or exhausting the endpoint;
- a web page reaching the loopback listener (cross-origin form posts, DNS
  rebinding);
- a compromised or mistaken commit reaching users through the menu updater;
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
| Wrong or duplicate paste | Mac Insertion Lease, one attempt, bounded readback (one adaptive window of up to 0.4 s), terminal receipt, and an in-memory Voice Outbox. Reviewed OpenAI opaque editors also require a stable window and unchanged input counters from hotkey press through commit. | Opaque compatibility cannot detect a programmatic same-window focus change; its result is unverified, excluded from learning, and is not generalized to other apps. Outbox text is also written to the local transcript history. |
| Clipboard exposure | Dictated text is written to the macOS pasteboard for this Mac only with the transient and concealed markers (on Windows, with the history, cloud, and monitor exclusions), and the previous clipboard is restored about one second after a burst of pastes. Context capture skips concealed or transient clipboards and secure text fields. | Clipboard software that ignores those conventions can read the text while it is on the clipboard. |
| Persistent audio | Hold-to-talk and Flight Recorder keep audio in RAM; recorder is opt-in and visibly indicated. | OS/audio drivers and unrelated software are outside the process boundary. |
| Private-state disclosure | Gitignored files, installer-created user-only permissions on Mac, a transcript log bounded to 500 entries and 30 days. | The logged-in user, backups, cloud-synced folders, and privileged software can read the files. Private state lives inside the checkout. On Windows `transcripts.jsonl` and `learned.json` get no owner-only ACL. |
| Model or dependency drift | Whisper, Parakeet, FluidAudio, and Qwen artifacts/revisions are pinned and verified; every `uv` call runs on CPython 3.12; `dictate.py.lock` pins Python packages and is verified on every pull request; `tests/test_supply_chain_integrity.py` fails closed when the runtime, model wallet, benchmark scorecard, and third-party notices disagree about a revision or a locked version, or when CI runs an action that is not pinned to a full commit. | Upstream package installers and first download remain supply-chain dependencies. The weekly `model-audit.yml` check is the only thing that notices an upstream repository changing underneath a pinned revision. |
| Release substitution | Published releases carry an exact Git archive and SHA256SUMS. The release workflow is designed to add a Developer ID signature, notarization, stapling, and a signed SLSA build-provenance attestation, but every release published so far was packaged by hand as an unsigned preview with none of those. See [release-provenance.md](release-provenance.md). | Whoever can replace a release asset can replace the checksum file beside it. The ad-hoc signed launcher and unsigned disk image give a downloader no proof of origin. The update manifest is not independently signed. |
| Update path | **Check for Updates…** runs only when the user chooses it, refuses a checkout with local changes or local commits, and rolls back to the last revision that installed cleanly. | **Open.** It fetches the tip of the tracked branch and runs that revision's installer with no release-tag, signature, or provenance check. Its trust anchor is the same as a `git clone` install, but users receive unreleased commits. |
| Borrowed privacy grants | launchd starts the runtime through the Whisper Face launcher, so macOS attributes Input Monitoring, Accessibility, and Microphone to Whisper Face. | **Open.** Those grants are not confined to the reviewed runtime. The launcher, and the user-writable checkout it starts, sit inside the same trust boundary as everything else running as the logged-in user, so software already running as that user can act with all three grants without a new prompt. |
| Speech leaving the machine | `tests/test_network_egress.py` checks that no first-party module on the dictation path imports a network client, enumerates every socket call site in the runtime, drives a synthetic utterance through the compile path against an instrumented socket, `urllib`, and `http.client`, and runs the shipping cleanup client against a live loopback proxy. The cleanup client reaches only `127.0.0.1:11434`, ignores environment and system proxies, and sends nothing when the model is not installed. Windows loads models from local files only at runtime. | A dynamic check only covers the code a test executes; a structural check cannot see through `getattr`. Neither observes the operating system, the installed model processes, or a compromised dependency's own traffic. It is a tripwire, not a proof. |
| Source/license mismatch | Artifact includes exact corresponding source and notices; manifest binds its full revision and source URLs. | A distributor can violate policy; recipients can compare the digest and source. |
| Endpoint abuse | Desktop mode is loopback-only and answers only `GET /health`; any request with a browser `Origin` header or a `Host` that is not an IP address, `localhost`, or a `.local` name is refused. Explicit `--server-only` adds the transcription, source, and license routes; transcription needs a 64-hex bearer token created at each start, bodies are size-limited, snippets are never expanded, and nothing is logged to the transcript history. | The token is printed once and, under the login service, sits in `dictate.log`; it changes on every restart. Uploaded audio is written to a temporary file for `ffmpeg`, which has no format whitelist, and phone requests share the single recognition worker with live dictation. The endpoint is not safe to expose to the internet. |

## Security invariants

1. Cleanup may format evidence but may not become acoustic truth.
2. Final insertion is at most one attempt and must honor the captured target.
3. Ambiguous insertion is not retried and does not train personalization.
4. Audio is not persisted unless a future, explicit product decision changes
   the privacy contract.
5. A public release identifies one full Git commit, carries corresponding
   source and notices, and is verifiable before installation.
6. A production Mac artifact is signed, notarized, stapled, checksummed, and
   paired with rollback metadata. Unsigned artifacts are previews only. Every
   Mac release published so far is such a preview.
7. New outbound data flows require explicit consent and an updated privacy
   promise and threat model.

## Open security work

- make the menu updater install only verified release tags, not the tip of a
  branch;
- confine the launcher's privacy grants to the runtime it is meant to start,
  and longer term ship the runtime inside a signed bundle so editing the
  checkout cannot borrow them;
- publish a release through the signed, notarized, attested workflow; none has
  been yet;
- decode `--server-only` uploads without a temporary file, and give the phone
  token a stored, rotatable form instead of a per-start value in the log;
- sandbox model and capture work into least-privilege processes. The
  `macos_networkless_worker` foundation exists and proves the OS denies it IP
  networking, but no runtime path uses it yet;
- add independent update-manifest signing and key-rotation/revocation policy.
  Build provenance now identifies what produced a release artifact, but the
  update metadata itself still carries no independent signature;
- commission an external review of capture lifecycle and insertion adapters.

## Closed since the last revision

- **Repeatable dependency and model provenance.**
  `tests/test_supply_chain_integrity.py` gates every pull request on the lock,
  the model revisions agreeing across the runtime, wallet, scorecard, and
  notices, and every CI action being pinned to a full commit and recorded.
  The release workflow attests SLSA build provenance, although no published
  release has gone through it yet;
  [release-provenance.md](release-provenance.md) states what that would and
  would not prove.
- **Desktop loopback endpoint.** Desktop mode now serves only `GET /health`;
  the transcription route needs `--server-only` and a bearer token, and
  browser-origin and DNS-rebinding requests are refused.
- **Automated privacy regression tests for network egress.**
  `tests/test_network_egress.py` enforces the structural and dynamic contracts
  described in the controls table above, and its own tripwire is tested so a
  green run is evidence rather than an unarmed alarm.

Report possible invariant violations privately through [SECURITY.md](../../SECURITY.md).
