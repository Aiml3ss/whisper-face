# Whisper Face privacy promise

Whisper Face is local-first voice input. Its default product model does not
require an account, upload speech for transcription, sell personal data, or use
dictation to train a shared model.

## What stays on the device

- Microphone samples are processed by local ASR models. Hold-to-talk samples
  and the optional Flight Recorder buffer live in RAM and are not written as
  audio files. The one exception is the explicit `--server-only` mode: audio a
  client uploads there is written to a private temporary file while `ffmpeg`
  decodes it, and deleted afterwards.
- Cleanup is deterministic by default. The optional cleanup model
  (Qwen3.5-4B) runs in a local Ollama service, and Whisper Face reaches it only
  at `127.0.0.1:11434`, through a client that ignores environment and system
  proxy settings. When the model is not installed, no request is sent at all.
  No hosted LLM is used.
- Focused-application, selection, nearby-document, filename, and bounded
  clipboard context is ephemeral recognition evidence. It is not written into
  the transcript history or learned-state files as context. See [What is read
  when you press the key](#what-is-read-when-you-press-the-key) for the
  exceptions in Reply and Edit modes.
- Dictionary terms, snippets, preferences, transcript telemetry, correction
  mappings, and Personal Regression cases are private, gitignored files in the
  installed checkout. On a fresh installation the installer provisions runtime
  logs before starting either local service; reruns repair their protection
  without truncating content and verification checks their current-user-only
  posture. Because these files live in the checkout, install it in a folder
  that is not synced to a cloud drive (not Documents or Desktop when iCloud
  Drive or OneDrive syncs them).

Flight Recorder is off on a fresh installation. When enabled, the microphone
stays open so it can keep the last twenty seconds; its menu state and the
operating system microphone indicator make that continuous capture visible.
The bounded buffer is cleared after use, on pause, when disabled, and on quit.
With Flight Recorder off, the microphone is listening only while you hold the
capture key.

The Mac risk-confirmation ceremony is content-free and RAM-only. It stores one
opaque identifier plus a closed risk class and state for at most the running
process. An exact `confirm risky action` or `cancel risky action` utterance is
consumed before compilation, transcript logging, clipboard access, or text
insertion. The later native click records only an inert receipt: this surface
has no action payload, Accessibility write, app API, subprocess, clipboard, or
execution callback. It currently has no control in the app window.

## Permissions

On macOS, Whisper Face needs three permissions, each granted to
**Whisper Face** under System Settings → Privacy & Security:

- **Input Monitoring** lets it see you hold the capture key anywhere. Its key
  listener receives key events system-wide in order to match its bound keys;
  it does not store or log what you type.
- **Accessibility** lets it read the focused field so it can paste in the
  right place, verify what landed, and gather the recognition context below.
- **Microphone** lets it hear you.

## What is read when you press the key

On a Mac, each time you press the capture key Whisper Face reads, for that one
dictation: the frontmost app's name, the focused field's text and selection,
the window title, the document the window shows (the last 6,000 characters of
a file up to 1 MB) and up to 80 file names beside it, and the clipboard's text
when it is 800 characters or shorter. This biases recognition toward the
words in front of you and is discarded after the dictation.

- Nothing is read from a field macOS reports as a secure text field.
- The clipboard is skipped when its owner marked it concealed or transient
  (the convention password managers use) or, on Windows, excluded it from
  clipboard history or monitoring.
- On Windows only the window title and the clipboard are read.
- App names, window titles, and dictionary terms only bias the recognizer;
  they never replace a recognized word.

Reply and Edit modes are the exception to "discarded": in Reply, up to 2,000
characters of the field go to the local cleanup model with your dictation, and
in Edit, the selection does. The result is pasted and logged like any other
dictation. In Edit without the cleanup model, the unchanged selection is
pasted back and logged.

## Data written locally

`transcripts.jsonl` holds one entry per dictation: the raw and cleaned text,
the app, a timestamp, and timing, engine, confidence, compiler, and
correction-burden telemetry. Text that went to the Voice Outbox instead of
being pasted is logged too, and on a Mac the text read back from the pasted
range ten seconds later can be added to the entry as `observed_text`. The log
holds at most 500 entries and nothing older than 30 days; it is trimmed every
time a dictation is appended, whether or not the cleanup model is installed.
There is no switch that turns the history off: the window's **Recent
dictations** setting only controls whether recent entries are listed.

`delivery_journal.json` holds a dictation only while it is being delivered:
its raw text as soon as it is recognised, then the text about to be pasted,
the app, and a timestamp. It is written owner-only (mode 600 on a Mac) and
atomically before anything is pasted, and the entry is removed once the
dictation's line is in `transcripts.jsonl`, so the file is normally absent.
If Whisper Face stops in between, the next start moves what the journal holds
into `transcripts.jsonl`, marked as not delivered, and into the Voice Outbox.

`learned.json`, `dictionary.txt`, and `snippets.json` hold local
personalization. `learned.json` also holds pending snippet suggestions (an
edit you made to a pasted snippet, which is never applied on its own) and a
short history of learning events. `acoustic_keyword_memory.json` holds keyword
candidates, salted app-scope hashes, and bounded evidence digests; it does not
hold raw audio or transcript history. `voice_inbox.json` can hold inert Voice
Object draft text until the user purges it, and `demonstrations.json` can hold
private, manually authored recipe steps until the user cancels or explicitly
deletes them. Each install run writes a user-only `install.log` in the
checkout. Logs can contain content-free timing, counts, closed event labels,
and diagnostic details; routine logging does not intentionally interpolate
transcript, draft, context, snippet, correction, or focused-app values. In
`--server-only` mode, the endpoint's bearer token is kept in `phone_token.txt`
in the checkout: created on the first start, reused on later starts, written
owner-only (mode 600) on a Mac, and protected on Windows only by the
permissions of the checkout folder. It is never printed and never written to
`dictate.log`, which records only the file's location. Deleting the file and
restarting the service replaces the token. Anyone who can read that file can
send audio to the endpoint; an uninstall removes it only with
`--remove-personal-data`. These files are not anonymous
simply because they remain local. A person or backup product with access to
the installed folder may be able to read them.

Users can disable Flight Recorder, inspect or remove the local files, or
uninstall the checkout. **Forget** on a learned correction removes its rule,
the corrected word's dictionary count, its history entries, and its evidence
in every app scope; deleting a snippet removes its edit record and history.
The history no longer stores snippet text. There is no in-app control that
clears `transcripts.jsonl`; delete the file, or use the uninstaller's
`--remove-personal-data` option. Removing a local file cannot remove copies
already captured by backups or external tools.

On Windows, the installer does not yet give `transcripts.jsonl`,
`delivery_journal.json` and `learned.json` owner-only access control lists;
they inherit whatever the install folder grants.

## The clipboard

Whisper Face pastes by putting the dictated text on the clipboard. On macOS it
is written for this Mac only (kept off Universal Clipboard) and marked with
the `org.nspasteboard.TransientType` and `org.nspasteboard.ConcealedType`
markers, which clipboard managers that follow that convention neither record
nor show. On Windows it carries the formats that exclude it from clipboard
history, cloud clipboard, and clipboard monitors. About a second after a burst
of pastes, your previous clipboard is put back, or the clipboard is cleared if
it was empty. Software that ignores these conventions can still read the text
while it is there.

## Network behavior

The normal speech, cleanup, and learning path talks only to local models.
Model and dependency installation contacts Homebrew, Astral, Ollama (only when
the cleanup model is installed on a Mac), Hugging Face, GitHub, Apple, and
their delivery infrastructure as documented in `THIRD_PARTY_NOTICES.md` and
the installer. On Windows, the runtime loads its Whisper models from local
files only; only the installer's preload downloads them. **Check for
Updates…** on a Mac runs one `git fetch` from the checkout's own remote when
you choose it, and never in the background. For a copy the Whisper Face app
set up from the disk image, it instead reads the latest release's
`update-manifest.json` from the project's GitHub releases, and downloads that
release's disk image and source archive only after you click **Download and
Install**. These requests carry nothing of yours beyond what any download
does (your address, and a fixed `WhisperFace-updater` user agent), run in a
process of their own that the dictation path never loads, and can reach only
the project's releases over HTTPS.

That is a testable claim, so it is tested. `tests/test_network_egress.py` runs
on every pull request. It fails if any module on the dictation path gains the
ability to open a socket, if a new outbound call site appears in the runtime,
or if driving a synthetic utterance through the compile path touches the
network at all, and it runs the shipping cleanup client in the locked runtime
against a live loopback proxy to show the proxy is never used. Its own alarm is
tested too, so a passing run means the check was armed. It is a tripwire, not
a proof: it covers the code a test executes and cannot observe the operating
system, the model processes, or a compromised dependency.

The runtime listens on port 8787. In ordinary desktop mode it binds loopback
and answers only an unauthenticated `GET /health` (the installers' readiness
probe), which returns `ok` and nothing else. The experimental transcription
endpoint, `GET /source`, and `GET /license` exist only in the explicit
`--server-only` mode, which binds reachable network interfaces. There, a
transcription request needs a bearer token that is created once, stored in
`phone_token.txt`, and reused across restarts until that file is deleted;
requests with a browser `Origin` header, or a `Host` that is not an
IP address, `localhost`, or a `.local` name, are refused; snippets are never
expanded; and nothing is written to the transcript history. Use that mode only
on a trusted network with a host firewall and do not forward the port to the
internet. The headless interface is a documented trust boundary and is not
represented as an internet-safe API.

## Our commitments

The community project will not add remote speech processing, analytics,
advertising identifiers, transcript collection, or shared-model training as a
silent update. A future opt-in network feature must identify the operator,
purpose, data categories, retention, security boundary, and deletion path
before data leaves the device. Local operation will remain a first-class path.

This promise covers the app, not the project website.

Security concerns can be reported privately under the
[security policy](SECURITY.md). The technical assumptions behind this promise
are enumerated in the [threat model](docs/security/threat-model.md).
