# Whisper Face privacy promise

Whisper Face is local-first voice input. Its default product model does not
require an account, upload speech for transcription, sell personal data, or use
dictation to train a shared model.

## What stays on the device

- Microphone samples are processed by local ASR models. Hold-to-talk samples
  and the optional Flight Recorder buffer live in RAM and are not intentionally
  written as audio files.
- Cleanup runs through the local Ollama service. No hosted LLM is required by
  the shipped configuration.
- Focused-application, selection, nearby-document, filename, and bounded
  clipboard context is ephemeral recognition evidence. It is not intentionally
  written into the transcript history or learned-state files.
- Dictionary terms, snippets, preferences, transcript telemetry, correction
  mappings, and Personal Regression cases are private, gitignored files in the
  installed checkout. On a fresh installation the installer provisions runtime
  logs before starting either local service; reruns repair their protection
  without truncating content and verification checks their current-user-only
  posture.

Flight Recorder is off on a fresh installation. When enabled, its menu state
and the operating system microphone indicator make continuous capture visible;
the bounded buffer is cleared after use, on pause, when disabled, and on quit.

The Mac risk-confirmation ceremony is content-free and RAM-only. It stores one
opaque identifier plus a closed risk class and state for at most the running
process. An exact `confirm risky action` or `cancel risky action` utterance is
consumed before compilation, transcript logging, clipboard access, or text
insertion. The later native click records only an inert receipt: this surface
has no action payload, Accessibility write, app API, subprocess, clipboard, or
execution callback.

## Data written locally

Whisper Face can keep a bounded `transcripts.jsonl` history containing raw and
cleaned text plus timing, engine, confidence, compiler, and correction-burden
telemetry. `learned.json`, `dictionary.txt`, and `snippets.json` hold local
personalization. `acoustic_keyword_memory.json` holds keyword candidates,
salted app-scope hashes, and bounded evidence digests; it does not hold raw
audio or transcript history. `voice_inbox.json` can hold inert Voice Object
draft text, and `demonstrations.json` can hold private, manually authored recipe
steps until the user cancels or explicitly deletes them. Logs can contain
content-free timing, counts, closed event labels, and diagnostic details;
routine logging does not intentionally interpolate transcript, draft, context,
snippet, correction, or focused-app values. These files are not anonymous simply
because they remain local. A person or backup product with access to the installed
folder may be able to read them.

Users can disable Flight Recorder, inspect or remove the local files, forget
learned corrections in the Mac UI, or uninstall the checkout. Removing a local
file cannot remove copies already captured by backups or external tools.

## Network behavior

The normal speech, cleanup, and learning path talks only to local models. Model
and dependency installation contacts Homebrew, Astral, Ollama, Hugging Face,
GitHub, Apple, and their delivery infrastructure as documented in
`THIRD_PARTY_NOTICES.md` and the installer.

The current runtime also exposes an unauthenticated HTTP transcription endpoint
on port 8787 for experimental phone/headless integration. Ordinary desktop mode
binds it to loopback. Only the explicit `--server-only` mode binds reachable
network interfaces; use that mode only on a trusted network with a host firewall
and do not forward the port to the internet. The headless interface is a
documented trust boundary and is not represented as an internet-safe API.

## Our commitments

The community project will not add remote speech processing, analytics,
advertising identifiers, transcript collection, or shared-model training as a
silent update. A future opt-in network feature must identify the operator,
purpose, data categories, retention, security boundary, and deletion path
before data leaves the device. Local operation will remain a first-class path.

Security concerns can be reported privately under the
[security policy](SECURITY.md). The technical assumptions behind this promise
are enumerated in the [threat model](docs/security/threat-model.md).
