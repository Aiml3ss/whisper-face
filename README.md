<p align="center">
  <img src="icons/core.svg" width="128" alt="Whisper Face icon"/>
</p>

<h1 align="center">Whisper Face</h1>

<p align="center"><b>The one-click, local-first voice input layer for Mac.</b><br/>
Hold a key, speak, release — trustworthy text appears wherever your cursor is.<br/>
No account, no word cap, no cloud dependency, and no audio leaving your machine.</p>

---

Whisper Face is building a better interface between thought and software—not
just another transcript box. The goal is commercial-quality dictation that is
easy enough for anyone to install, fast enough to disappear into the workflow,
and conservative enough to protect names, numbers, code, commands, and intent.

Mac is the production focus today. Windows shares the core pipeline and keeps
one-click installer parity; the native iPhone product is a future workstream,
not a current setup promise.

## Why it is different

| Product promise | What Whisper Face does |
|---|---|
| **Setup is part of the product** | `Install.command` provisions the app, locked dependencies, models, native helper, login services, and health checks in one repeatable flow. |
| **Speech evidence stays authoritative** | Multiple local recognizers produce hypotheses; the Voice Compiler may improve presentation but cannot invent unsupported meaning. |
| **Fast by architecture** | Models stay warm, long speech is recognized while you talk, installed model paths resolve offline, and routine cleanup stays deterministic unless the words truly require semantic work. |
| **Insertion is a transaction** | The target is leased and revalidated before one paste attempt; destination drift goes to a recoverable Voice Outbox instead of the wrong field. |
| **Personal without surveillance** | Corrections become scoped, inspectable local rules and regression cases—not a cloud transcript dossier. |
| **Failure is recoverable** | Faithful fallbacks, rejected-edit evidence, stable prefixes, exact-once insertion guards, and the outbox prevent silent loss. |

The current local pipeline is:

```text
microphone → warm ASR cascade → VoiceIR → protected anchors + proof edits
           → bounded local cleanup → insertion lease → paste/readback or outbox
           → confirmed correction → personal regression case
```

The public [product roadmap](https://github.com/Aiml3ss/whispering-parrot/issues/1)
tracks the Mac trust/performance work, future native iPhone architecture, and a
sustainable business that never sells speech, transcripts, or behavioral
profiles.

The dated [neutral competitor evaluation](docs/benchmarks/competitor-evaluation.md)
separates official product claims from reproducible measured results. It does
not declare a winner before the same physical tasks have been run.

The [Support and setup pilot](SUPPORT.md) makes the free-local covenant
explicit and collects only public, nonbinding interest in voluntary Supporter
recognition or a limited Mac setup session. No payment account or paid service
is active, and core accuracy, privacy, accessibility, installers, and local use
are never Supporter-only features.

## Features

- **A real native Mac app window** — choose **Open Whisper Face…** from the
  menu-bar face for focused Overview, Results, Settings, Models, and
  Diagnostics sections. It exposes live latency and usage—including
  Parakeet's own processing time when available—inspectable decision evidence,
  unified personalization/privacy controls, local model health, and one-click
  installer verification without adding a browser runtime.
- **Transactional insertion + Voice Outbox (Mac)** — readable text fields are
  leased at key-down and revalidated before the single paste attempt. If focus,
  selection, or nearby text changed while recognition ran, Whisper Face does
  not guess or paste into the wrong place; it keeps the result in a bounded,
  RAM-only recoverable outbox. Apps that hide their text still bind the
  insertion to the original application/focused element and surface delivery
  as unverified instead of training from an assumption; if macOS cannot identify any focused
  element at all, the insertion fails closed into the outbox.
- **Flight Recorder (experimental)** — enable its menu-bar toggle, speak
  naturally, then tap Right Option afterward. Whisper Face finds and pastes the
  latest utterance from a 20-second RAM-only buffer. Holding Right Option still
  performs normal push-to-talk.
- **Acoustic Time Machine (experimental, Mac)** — opt in under Privacy to keep
  only the latest result's short, consequence-selected audio spans in RAM after
  insertion. Results can play those spans directly from memory or clear them
  immediately; they self-delete after one minute, a new usable result replaces
  them, disabling wipes them, and no replay file is written.
- **Hold-to-talk anywhere** — hold Right Option, speak, release; text pastes
  into whatever app has focus, with a frosted HUD waveform while you talk.
  The microphone is pre-warmed at login and the start cue sounds only once
  capture is ready.
- **Rolling recognition** — long dictations are transcribed *while you're
  still talking* (segments cut at natural pauses), so a 60-second ramble
  pastes as fast as a one-liner.
- **Context-aware recognition** — the active selection, nearby field text,
  window title, local document, sibling filenames, app name, and a short
  clipboard sample temporarily bias recognition toward the words already in
  front of you. This context is neither logged nor learned.
- **Three-engine confidence cascade on Mac** — Whisper Tiny speculates during
  an end pause. Clear speech can paste immediately; uncertain English is
  verified by a warm native Parakeet Unified helper, with Whisper
  large-v3-turbo retained as the independent fallback. Audio reaches the
  helper through a RAM-only pipe. Windows retains the Tiny → Turbo cascade.
- **Offline runtime model resolution** — the one-click installer downloads both
  pinned MLX snapshots up front. Dictation then resolves only the installed
  local paths and memoizes them, so a release never waits on Hugging Face or
  prints a model-fetch progress bar.
- **Structured cleanup with a safety net** — fillers and false starts removed,
  self-corrections applied ("Tuesday, actually Wednesday" → Wednesday),
  punctuation fixed. Safe edits are compiled deterministically; the local LLM
  is used only for semantic cleanup and explicit writing modes. Its strict
  JSON edit plan is guarded against refusals, over-deletion, and truncation;
  nearby context is explicitly marked as untrusted, with a faithful local
  fallback.
- **Evidence-driven Voice Compiler** — acoustic hypotheses, ephemeral context,
  app-scoped personal priors, and native timing evidence meet in a local
  `VoiceIR`. A conservative span graph chooses terms; protected names,
  numbers, dates, URLs, paths, identifiers, commands, and flags survive
  cleanup; LLM edits apply only when bounded proof edits reconstruct the
  claimed output exactly.
- **Stable semantic feedback** — rolling recognition publishes only a stable
  prefix to the HUD, never provisional text into the target app. Native word
  timing may add conservative pause/paragraph formatting without enabling the
  expensive Whisper alignment pass.
- **Consequence receipts before consequence automation** — names, numbers,
  currency, dates, times, recipients, contacts, URLs, paths, commands, and
  actions receive transcript-free risk/uncertainty receipts in Last
  Recognition and Results. The selective re-listen selector is bounded to two
  native-timed microspans and never a full utterance. Re-listen execution is
  intentionally disabled until a killable process-isolated verifier beats the
  current stack in a public accuracy/latency comparison. Rolling and
  speculative decodes now retain exact capture-sample bounds, including real
  silence gaps; malformed or overlapping timing evidence still fails closed.
  A provider-neutral disposable-process boundary and pinned local Whisper Tiny
  adapter now supply hard timeouts and transcript-free results. The adapter
  stays unwired until its cold-start latency and accuracy justify activation.
  After a successful ordinary Mac dictation, a Ping advises you to review
  consequence-sensitive text; it does not block insertion or verify the words.
  The Results window repeats that guidance without exposing transcript text.
- **Counterfactual Context Firewall** — every finalized, insertion-bound
  contextual/personalized compile is compared with a context-free shadow
  compile. Protected influence is quarantined in a transcript-free receipt;
  benign influence is only a shadow promotion candidate. Results explains the
  bounded receipt without exposing context or transcript text. The comparison
  cannot change the text, cleanup, insertion, or model route.
- **Six explicit voice modes** — modifier keys turn the same Right Option
  gesture into faithful capture, polished composition, context-aware reply,
  selected-text editing, spoken-code compilation, or a small allowlist of
  reversible editing commands.
- **Tone awareness** — casual in Slack/Messages (texting style: no trailing
  period), formal prose in Mail, technical in editors and AI chats, verbatim
  in terminals. Pick a tone per app from the menu-bar **App Tones** picker,
  or force one per-dictation: *"Formal tone, …"*, *"casual, …"*.
- **Spoken structure** — "new line" / "new paragraph" / "scratch that" work;
  explicit list lead-ins ("two things", "here's a list", "here are some
  feedback items") become tidy dash lists when you state multiple items. A
  lone ordinal in ordinary prose (for example, "the second thing regarding
  audio") stays on the deterministic fast path instead of waking the LLM.
- **Reversible personalization** — vocabulary is mined from your usage, while
  corrections are learned only when the exact pasted range is changed. A
  correction activates after two confirmations in the same app or three
  globally, and every learned mapping can be inspected or forgotten from the
  menu.
- **Acoustic keyword memory foundation** — a strict local store can count
  unique observations and explicit confirmations for hard names without raw
  audio, transcript history, or unhashed app identifiers. It is exportable and
  forgettable from the on-demand Pronunciation Keywords inspector. Only exact,
  verified user corrections add idempotent global evidence; routine status does
  not load keyword text. It deliberately has no recognition authority yet.
- **Point-and-Speak resolver foundation** — a non-writing resolver combines
  accessible names, roles, ordinal and spatial language, visibility, focus,
  and selection facts behind strict confidence/margin gates. Its 17-case
  synthetic corpus has zero wrong-target resolutions, but it has no live
  Accessibility automation and makes no physical-app accuracy claim.
- **Provider-neutral model wallet foundation** — immutable Parakeet, Whisper
  Tiny, Whisper large-v3-turbo, and Qwen profiles expose capabilities,
  readiness, and bounded evidence through one in-process policy. Failover is
  sequential and requires an explicit typed failure receipt; live routing is
  intentionally not wired yet.
- **Voice action foundations without surprise execution** — typed Voice
  Objects can project closed facts into inert text, email, task, and calendar
  drafts; a private Voice Inbox can durably queue exact payloads with source
  provenance; and Drop-to-Target can resolve or refuse a synthetic target
  behind strict capability and ambiguity gates. None of these foundations can
  send, schedule, click, type, drag, or run an agent yet.
- **Personal Regression Lab** — confirmed correction mappings are evaluated
  against a private, deterministic suite of the user's exact corrected spans.
  Conflicting candidates are quarantined instead of silently becoming a bad
  global rule; no audio or surrounding document text enters the suite.
- **Self-editing snippets (Mac)** — say "insert my email" and your saved text
  pastes instead. Edit that exact insertion within ten seconds and the revised
  value is saved for next time and listed under **Learned Corrections**.
- **Whispering works** — quiet speech is gain-normalized before recognition.
- **Choose your Whisper Face** — Parrot, Fox, Owl, Cat, and Bear share the
  original friendly vector style. Pick one from the menu bar and it persists
  locally. On Mac, both the floating character and tiny menu-bar face open and
  close their mouths with your live speech; Windows mirrors the selected face
  and recording state in its tray icon.
- **Menu-bar/tray presence** — the selected face remains visible in the menu
  bar or Windows notification area, with processing and paused states. The Mac
  menu includes character, usage, tones, recognition alternatives, and learned
  corrections; Windows adds character, pause, Flight Recorder, logs, and quit.
- **Experimental phone compatibility endpoint** — an OpenAI-compatible
  `/v1/audio/transcriptions` endpoint (port 8787) can support self-hosted
  clients such as [Diction](https://diction.one). Native iPhone work is
  deliberately deferred while the Mac experience is brought to product
  quality.
- **Always on** — installed as launch agents: starts at login, restarts on
  crash, keeps its models warm so the first dictation after a break is never
  slow.

## Requirements

| Platform | Requirements |
|---|---|
| macOS | Apple Silicon, macOS 14 or newer recommended, ~8 GB free |
| Windows | Windows 10/11 x64, ~8 GB free; NVIDIA GPU preferred, CPU fallback supported |

Both platforms install Whisper Tiny, Whisper large-v3-turbo, and Qwen3.5-4B.
Mac also builds a pinned FluidAudio helper and downloads Parakeet Unified;
Whisper remains installed for fallback and broader language support.
Whisper and Parakeet preload immutable audited revisions so a later upstream
model update cannot silently change a fresh install. Qwen's current Ollama tag
manifest is recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
must be re-audited if it moves.

## Install

Clone or download the repository, then use the one-click launcher for your OS:

- **Mac:** double-click `Install.command`.
- **Windows:** double-click `Install.cmd`.

From a terminal, the same installers are:

```sh
# macOS
./setup.sh

# Windows PowerShell
.\setup.ps1
```

The launcher detects the OS. A Windows Git Bash or WSL invocation of
`./setup.sh` automatically hands off to `setup.ps1`. On a fresh machine it:

1. Installs the native package prerequisites (`Homebrew` on Mac or `winget`
   packages on Windows), then installs `uv`, `ffmpeg`, and Ollama.
2. Reproduces the locked Python dependency environment from
   `dictate.py.lock`.
3. Downloads Qwen3.5-4B and both Whisper models before launching the app. On
   Mac it also builds and preloads the native Parakeet Unified helper.
4. Creates private configuration files without overwriting existing ones.
5. Installs and validates the tuned Ollama and dictation login services
   (`launchd` on Mac, Task Scheduler on Windows).

Homebrew's official installer may pause once to explain its changes and ask
for your macOS password. Model downloads are several gigabytes, so the first
run can take a while. Subsequent runs are idempotent and reuse the caches.

Every runtime change is governed by the repository's
[installer release process](docs/installer-release-process.md). It requires
Mac/Windows impact review, locked-environment and installer-contract tests,
live platform verification where available, and publication of the commit
before a fresh machine is told the build is available.

For an existing installation, follow the concise
[update and rollback guide](docs/distribution/update-and-rollback.md). Updates
currently come from the installed source checkout; there is no automatic
updater yet.

macOS will prompt for permissions — enable **"uv"** under System Settings →
Privacy & Security → **Input Monitoring**, **Accessibility**, and
**Microphone**. The app waits and restarts itself automatically once granted.
Allow the firewall prompt if you want the iPhone endpoint.

Windows may show standard package-install prompts. Enable **Microphone access**
under Settings → Privacy & security if it is disabled. The Windows backend
automatically uses an NVIDIA GPU when its runtime is available and otherwise
uses optimized int8 CPU inference. Its tray icon exposes pause, Flight
Recorder, logs, and quit. The core capture, confidence cascade, cleanup,
snippets, tones, and learning pipeline is shared; macOS additionally provides
the floating Cocoa HUD and richer Accessibility-based selection/document
context.

After granting permissions, verify the complete installation at any time:

```sh
./setup.sh --verify                 # macOS
.\setup.ps1 --verify               # Windows
```

This checks the dependency lock, platform login service, Qwen model, both
Whisper caches, and the process health endpoint without changing the
installation.

### Native Mac window

Click the selected animal in the menu bar and choose **Open Whisper Face…**.
The menu bar remains the fastest everyday control, while the window provides a
clear home for setup health, privacy controls, model status, character choice,
and Voice Outbox recovery. Closing the window does not stop dictation.
Diagnostics can copy a fixed support snapshot containing only categorized
health/model state and numeric result aggregates; it excludes dictation text,
context, paths, logs, personal language data, and machine identifiers.

### Matching another Mac

The installer reproduces the application, models, performance settings, and
default behavior. Personal state is intentionally gitignored. To carry your
MacBook vocabulary and preferences to a Mac mini, securely copy any desired
`dictionary.txt`, `snippets.json`, `tones.json`, `preferences.json`, and
`learned.json` files into the cloned folder **before** running `./setup.sh`.
Copy `acoustic_keyword_memory.json` as well if you want to preserve its
inspectable candidate evidence. Existing copies are preserved and locked to
user-only permissions. You do not need `transcripts.jsonl` unless you also want
the old transcript history.

**Headless / server Mac** (for experimental self-hosted clients):

```sh
./setup.sh --server-only
```

No hotkey, no HUD, no permission prompts—just the compatibility endpoint and
the learning loop. This is not the planned native iPhone experience.
Verify that headless installation later with `./setup.sh --server-only --verify`;
the runtime/model/service checks still run while the AppKit construction gate
is correctly skipped.

## Daily use

| Say | Get |
|---|---|
| (hold Right Option on Mac or Right Alt on Windows, talk, release) | cleaned text at your cursor |
| (Flight Recorder enabled: talk, then tap Right Option) | your latest utterance |
| Shift + Right Option | compose polished prose |
| Control + Right Option | reply using the current selection/context |
| Command + Right Option | edit the selected text |
| Shift + Control + Right Option | compile spoken code punctuation |
| Command + Control + Right Option | run an allowlisted Mac command |
| "…um so basically…" | fillers gone |
| "Tuesday — actually Wednesday" | just Wednesday, in place |
| "two things: … and second …" / "here's a list of ideas …" | a dash list |
| "new paragraph" | a real paragraph break |
| "scratch that" | previous sentence dropped |
| "Formal tone, …" / "casual, …" / "verbatim: …" | that style, this once |
| "insert my email" | your snippet from `snippets.json` |

On macOS, **Voice Object Commands** is an off-by-default Privacy setting. When
you explicitly enable it, only these exact lowercase forms bypass paste and
queue an inert local draft in `voice_inbox.json`: `create task: <title>`,
`draft email to <contact>: <body>`, and
`create calendar event <ISO start>: <title>`. No draft is sent, scheduled, or
opened in another app; all other speech follows the normal paste path.
Turning the setting off stops new diversion but leaves already queued local
drafts intact.

Choose **Inspect** beside Voice Object Commands to open the local Voice Inbox.
The first view lists only bounded draft number, type, and state. Draft text is
read only after you explicitly choose **Reveal**. You can acknowledge or cancel
a selected draft and purge acknowledged/cancelled drafts; queued drafts are not
purged. The inspector never sends, schedules, launches, executes, or copies a
draft, and it remains available when command diversion is turned off.

The bundled `EDIT ME` values are setup placeholders. On Mac, insert one and
replace the pasted placeholder in place within ten seconds; Whisper Face saves
that exact replacement to `snippets.json`. You can also edit the private JSON file
directly. Learned snippet edits appear by name under **Learned Corrections**;
forgetting one restores its previous value when the file has not since changed.

On Windows, use **Right Alt** wherever the table says Right Option and the
**Windows key** wherever it says Command. Shift and Control are unchanged.

Everything personal stays in private, gitignored local files: `dictionary.txt`
(your terms; `-word` bans one), `snippets.json`, `tones.json`, `preferences.json`,
`transcripts.jsonl` (your history, trimmed to recent), `learned.json` (mined
vocabulary and fix rules), and `voice_inbox.json` (only opt-in inert Voice
Object drafts). `acoustic_keyword_memory.json` stores bounded
keyword candidates, hashed app scopes, and evidence digests without raw audio,
surrounding context, or transcript history.

Command mode intentionally recognizes only undo, redo, select all, copy, cut,
paste, delete selection, new line, and escape. It cannot launch arbitrary shell
commands. Edit mode requires a selection, so ordinary dictation cannot rewrite
an unseen document by accident.

Flight Recorder is disabled by default and must be enabled from the menu-bar
menu. Its audio is never written to disk: the bounded buffer is cleared after
use, on pause, when disabled, and on quit. The menu-bar dot and macOS microphone
indicator remain visible while it is active.

Each local transcript entry includes latency, ASR engine, confidence,
verification, compiler decisions, and proof-edit metrics. When macOS can
safely re-read the exact pasted range, the same private record is updated with
the text observed in that range after ten seconds; unsafe observations are
skipped. This enables a clearly labeled zero-edit proxy and observed
correction-burden measurement.

## Tuning

The knobs live at the top of `dictate.py` — hotkey, ports, the
LLM cleanup deadline, chunking aggressiveness, silence gates, tone per
app-bundle. The `eval_cleanup.py` harness replays adversarial cases and your
own transcripts through candidate Ollama models if you want to test a
different cleanup model.

Run `uv run benchmark_voice_compiler.py` for the platform-independent golden
corpus plus p50/p95 telemetry from the private local transcript log. JSON
output is available with `--format json`. Quality metrics remain explicitly
unavailable until safe post-paste observations exist.

Run `uv run benchmark_asr.py` with a local LibriSpeech `test-clean` directory
for an apples-to-apples Mac engine comparison. Downloaded audio and generated
hypotheses stay outside the repository; see `benchmarks/ASR_BAKEOFF.md`.

Runtime startup and acoustic-health traces can be reduced to numeric
aggregates with `uv run performance_lab.py traces --trace-log dictate.log`.
The trace command ignores ordinary log lines, rejects any non-allowlisted or
non-numeric trace field, and never includes raw lines, input paths, app
identifiers, or transcript text in its table or JSON output.

Run `uv run benchmark_consequence_routing.py` for the synthetic selector-only
consequence corpus. Its closed artifact explicitly says that no audio,
verifier, runtime ASR backend, or physical device was exercised, and its 5 ms
gate uses the worst per-case p95 rather than corpus-average throughput.

Run `uv run benchmark_insertion_reliability.py` for deterministic focus,
typing, duplicate-callback, destination-relaunch, clipboard, readback, and
delay fault injection. It checks at-most-once platform paste attempts and
stable terminal receipts, while explicitly reporting zero real apps tested and
making no 50-app or four-nines claim.

Run `uv run public_scorecard.py` for the transcript-free aggregate of five
checked-in synthetic suites. Use `uv run competitor_benchmark.py` with the
neutral task corpus and externally collected observation files for product
comparisons; vendor claims and unavailable tasks never enter measured totals.

`voice_input_protocol.py` defines the strict versioned proposal, stable-prefix,
final-text, commit, acknowledgement, and cancellation contract used by five
synthetic capability profiles. `compatibility_fingerprint.py` can build a
bounded, text-free minimum-count aggregate only after explicit opt-in. Both are
local foundations: neither claims a shipped cross-process SDK, physical app
adapter, anonymous user population, telemetry backend, or network service.
See [architecture and interoperability](docs/architecture-and-interop.md) for
the current process boundaries and the precise in-process protocol surface.
A canonical, size-bounded JSON codec can round-trip those messages, but no
socket, server, discovery layer, or public transport is shipped.

`delayed_cleanup_merge.py` provides a pure three-way merge for cleanup that
finishes after insertion. It proposes changes only where the original span and
unique boundary anchors remain untouched, rejects ambiguous or reordered text,
and returns an explainable candidate. Its callback boundary requires two exact
destination snapshots and a final atomic compare-and-swap, with content-free
idempotent receipts. A real platform destination adapter is still required
before runtime activation.

`voice_objects.py`, `voice_inbox.py`, and `drop_to_target.py` define inert,
local foundations for the next interaction layer. Typed drafts can now enter
the local inbox through canonical closed-schema JSON and explicit-read decoding,
but there is still no live destination, automatic dispatch, or agent execution.

`acoustic_time_machine.py` is a default-off, RAM-only buffer foundation for at
most eight 2.4-second microspans and ten seconds total. It has explicit
read/consume/delete/clear operations and retains nothing while disabled; no
runtime capture or replay UI is enabled yet.

## Verify

Run the focused test for the area you changed. The complete release checklist
lives in one place—[the installer release process](docs/installer-release-process.md)—so
the required Mac and Windows commands do not drift across documents. Typical
focused commands are:

```sh
uv run tests/test_dictate.py
uv run tests/test_whisper_face_gui.py
uv run tests/test_installers.py
```

## Distribution, privacy, and security

Public Mac releases are built from one exact Git revision with the same
`Install.command` used in a checkout. The release pipeline emits an unsigned
local preview or, when Apple credentials are supplied, a signed, notarized, and
stapled disk image together with a source ZIP, update/rollback manifest, and
SHA-256 checksums. See the [Mac release runbook](docs/distribution/macos-release.md).

Whisper Face's user-facing data commitments are in the
[privacy promise](PRIVACY.md). Security boundaries and private vulnerability
reporting are documented in the [threat model](docs/security/threat-model.md)
and [security policy](SECURITY.md).

## License

Current first-party source is available under
[`AGPL-3.0-only`](LICENSE). [Alternative commercial terms](COMMERCIAL_LICENSE.md)
may be made available through a separately signed agreement for proprietary
distribution, OEM use, embedding, or hosted use that needs different terms.
Outside contributions require the
[Whisper Face CLA](CLA.md) so both licensing paths remain viable.

Earlier published commits remain MIT-licensed; those grants are not revoked.
The exact transition boundary and third-party scope are documented in
[LICENSE_POLICY.md](LICENSE_POLICY.md). This licensing structure keeps the
complete community edition open while allowing commercial work to fund the
free local product.

The Mac window exposes offline **License Notices** and the immutable
**Exact Source** under Diagnostics. Network-facing installs publish the same
commit-specific source offer at `GET /source` and the shipped notices at
`GET /license`. Modified deployments should set `WHISPER_FACE_SOURCE_URL` and,
for packaged builds without Git metadata, `WHISPER_FACE_SOURCE_REVISION` to
their corresponding source.
