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

The public [product roadmap](https://github.com/Aiml3ss/whisper-face/issues/1)
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
  menu-bar face for focused Home, Settings, and Advanced sections. It exposes
  live latency and usage—including
  Parakeet's own processing time when available—inspectable decision evidence,
  unified personalization/privacy controls, local model health, and one-click
  installer verification without adding a browser runtime. Advanced can
  also save a private `0600`, transcript-free support bundle to a destination
  you choose; it is never uploaded and contains only allowlisted health,
  permission, model, and aggregate result metadata. First run shows
  Permissions, Hotkey, Models, and Dictate together with live evidence-backed
  status; hotkey practice completes only after capture is actually observed.
- **Transactional insertion + Voice Outbox (Mac)** — readable text fields are
  leased at key-down and revalidated before the single paste attempt. If focus,
  selection, or nearby text changed while recognition ran, Whisper Face does
  not guess or paste into the wrong place; it keeps the result in a bounded,
  RAM-only recoverable outbox. Apps that hide their text still bind the
  insertion to the original application/focused element and surface delivery
  as unverified instead of training from an assumption; if macOS cannot identify any focused
  element at all, the insertion fails closed into the outbox. The count-only
  **Voice Outbox** menu entry routes directly to Home; only the existing
  explicit **Copy & Dismiss** control can recover content. When the field can
  be read and the character before the caret is neither whitespace nor opening
  punctuation, a single space is added so a new dictation does not jam against
  the text before it; text that starts with its own space, or with closing
  punctuation, is left exactly as recognized.
- **Flight Recorder (experimental)** — enable it under Settings →
  Privacy, speak
  naturally, then tap Right Option afterward. Whisper Face finds and pastes the
  latest utterance from a 20-second RAM-only buffer. Holding Right Option still
  performs normal push-to-talk.
- **Acoustic Time Machine (experimental, Mac)** — opt in under Privacy to keep
  only the latest result's short, consequence-selected audio spans in RAM after
  insertion. Home's Last dictation card can play those spans directly from
  memory or clear them
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
  helper through a RAM-only pipe. Startup and duration-aware request deadlines
  terminate a wedged helper, fall back to Turbo, and allow a lazy restart on
  the next utterance. Windows retains the Tiny → Turbo cascade.
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
  Recognition and the window's evidence inspector. The selective re-listen
  selector is bounded to two
  native-timed microspans and never a full utterance. A prewarmed,
  process-isolated pinned Whisper Tiny verifier can execute those spans under
  one hard deadline, returning only confirmed, contradicted, or inconclusive.
  It stays off unless a private local benchmark has at least 40 balanced real
  recordings, meets the closed accuracy/latency/refusal thresholds, receives
  explicit manual review, writes a content-free activation receipt, and the
  user opts in. Missing, stale, cold, malformed, timed-out, or same-engine
  evidence fails closed to review without delaying on model warmup. Rolling
  and speculative decodes retain exact capture-sample bounds, including real
  silence gaps; malformed or overlapping timing evidence still fails closed.
  After a successful ordinary Mac dictation, a Ping advises you to review
  consequence-sensitive text; it does not block insertion or verify the words.
  The window's evidence inspector repeats that guidance without exposing
  transcript text.
- **Counterfactual Context Firewall** — every finalized, insertion-bound
  contextual/personalized compile is compared with a context-free shadow
  compile. Protected influence is quarantined in a transcript-free receipt;
  benign influence is only a shadow promotion candidate. The evidence
  inspector explains the
  bounded receipt without exposing context or transcript text. The comparison
  cannot change the text, cleanup, insertion, or model route.
- **Six explicit voice modes** — modifier keys turn the same Right Option
  gesture into faithful capture, polished composition, context-aware reply,
  selected-text editing, spoken-code compilation, or a small allowlist of
  reversible editing commands.
- **Tone awareness** — casual in Slack/Messages (texting style: no trailing
  period), formal prose in Mail, technical in editors and AI chats, verbatim
  in terminals. Pick a tone per app under Settings → Personalize →
  **App tones**,
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
  not load keyword text. A separate offline evaluator can compare caller-
  supplied unbiased and keyword-biased candidate outcomes using categorical,
  transcript-free evidence. Synthetic evidence can never earn a keep receipt;
  at least 20 positive and 20 negative caller-attested physical cases must show
  at least three selection gains with no selection or candidate regressions.
  Receipts contain aggregate counts only and distinguish synthetic from caller-
  attested physical evidence. `benchmark_acoustic_keyword_activation.py`
  can grant one eligible term bounded local-ASR prompt priority only after
  explicit manual review; missing, malformed, forgotten, or regressing
  evidence has no recognition effect.
- **Evidence-gated acoustic calibration** — an offline policy consumes only
  the existing closed numeric capture telemetry and emits bounded candidate
  gain, noise-gate, VAD, and end-silence settings. Nonfinite/clipped evidence
  is killed, ambiguous silence/noise/quiet speech or near-saturation headroom
  stays insufficient, and reverb is explicitly unavailable. The runtime
  applies a candidate only from a private receipt built by
  `benchmark_acoustic_calibration_activation.py` after 40 balanced physical
  A/B cases, at least three improvements, zero regressions, and manual review.
  See [Acoustic accuracy activation](docs/acoustic-accuracy-activation.md).
- **Explicit Point-and-Speak action (Mac)** — a developer-invokable harness
  (no longer surfaced in the app window) takes a
  bounded target phrase for a read-only preview of the focused app. Whisper Face
  reads only bounded Accessibility names, roles, geometry, visibility,
  enablement, focus, and selection metadata; strict confidence/margin gates
  either show one selected name and role or fail closed. Only after a separate
  **Press once** confirmation does it take a fresh snapshot and allow one
  `AXPress` on a strongly named button, checkbox, radio button, tab, menu item,
  or link, with a session-issued nonce and exact app/window/element/role recheck
  immediately before the action. Text fields and every unlisted role remain
  inert. Drift, replay, expiry, weak evidence, unsupported roles, and action
  failure all do nothing.
  Phrases, names, target identifiers, and native identities remain transient;
  routine status and support snapshots receive only content-free evidence. The
  17-case resolver corpus has zero synthetic wrong-target resolutions, but no
  physical-app accuracy claim is made.
- **Provider-neutral model wallet foundation** — immutable Parakeet, Whisper
  Tiny, Whisper large-v3-turbo, and Qwen profiles expose capabilities,
  readiness, and bounded evidence through one in-process policy. Failover is
  sequential and requires an explicit typed failure receipt. A transcript-free
  shadow adapter now reports current-pin eligibility and deterministic advisory
  order without executing a model. Warmup caches bounded exact-pin evidence for
  all four providers and separately records process-local warm-path observations;
  neither is treated as current runtime readiness. The native Advanced section
  labels
  both as a shadow advisory with no execution or routing. Because the runtime exposes
  no conservative capability bounds, the current advisory stays fail-closed as
  missing evidence; live routing is intentionally not wired.
  `uv run model_readiness_evidence.py --format json` can also inspect all four
  local pin locations without downloading or executing a provider. On this Mac
  every pin resolves exactly, but filesystem evidence is deliberately capped
  at `resolved`: readiness, capability bounds, and routing authority stay false.
- **Networkless worker experiment (Mac)** — an opt-in, one-shot local worker
  proves OS-enforced denial of IP bind and outbound connection while retaining
  one private, bounded Unix-socket exchange. It accepts no transcript-bearing
  protocol message and is not wired to recognition, the app runtime, XPC, or a
  public SDK.
- **Voice action foundations without surprise execution** — typed Voice
  Objects can project closed facts into inert text, email, task, and calendar
  drafts; a private Voice Inbox can durably queue exact payloads with source
  provenance. After revealing a queued email, a separate confirmation can hand
  its recipients, subject, and body directly to macOS's in-process compose
  service exactly once. That action can only request a compose window: it has no
  send API, leaves the inbox item queued, and puts no private field in a URL,
  process argument, log, status, or receipt. Drop-to-Target can resolve or refuse a synthetic target
  behind strict capability and ambiguity gates. A developer-invokable Mac
  harness (no longer surfaced in the app window) exposes
  that resolver as an explicit, transient read-only preview: the caller declares
  a hypothetical role, source kind, and effect before bounded Accessibility
  role/name/geometry/state and `AXDropEnabled` evidence is inspected. The
  preview returns a no-execution receipt, cannot infer those declared
  semantics, and cannot initiate a drag/drop or make a physical accuracy claim. Inert
  demonstration drafts can
  also record, preview, approve, or roll back a bounded Finder, Mail, Notes, or
  menu recipe without replaying it. On Mac, the Privacy pane provides an
  explicit **Demonstrations** editor; its routine list returns and renders only
  draft number, domain, state, and step count, and private step text is returned
  to the editor only after **Reveal/Edit**. The same pane also offers an inert
  four-class risk-confirmation ceremony: explicitly start one class, say the
  exact phrase “confirm risky action,” then use the separately enabled click
  within 30 seconds. Its content-free RAM-only receipt cannot invoke an action.
  Outside the explicit Point-and-Speak button press and compose-window request,
  these foundations cannot send, schedule, click, type, drag, automate an app,
  or run an agent yet.
- **Personal Regression Lab** — confirmed correction mappings are evaluated
  against a private, deterministic suite of the user's exact corrected spans.
  Every model, prompt, dictionary, or Personal Prior candidate can use the same
  content-free shadow gate: it must materially improve the whole suite with
  zero regressions or evaluation errors before its activation callback runs.
  Personal Priors use that gate in the live learning path; conflicting or
  collateral-changing candidates are quarantined instead of silently becoming
  a bad rule. No audio or surrounding document text enters receipts.
- **Self-editing snippets (Mac)** — say "insert my email" and your saved text
  pastes instead. Edit that exact insertion within ten seconds and the revised
  value is saved for next time and listed under Settings → Personalize →
  **Learned corrections**.
- **Whispering works** — quiet speech is gain-normalized before recognition.
- **Choose your Whisper Face** — fourteen characters (Parrot, Fox, Owl, Cat,
  Bear, Dog, Wolf, Pig, Panda, Tiger, Frog, Rabbit, Hedgehog, and Penguin)
  share one pastel chibi-clay style. Pick one from the menu bar and it
  persists locally. On Mac, both the floating
  character and tiny menu-bar face open and close their mouths with your live
  speech; Windows mirrors the selected face and recording state in its tray
  icon.
- **Menu-bar/tray presence** — the selected face remains visible in the menu
  bar or Windows notification area, with processing and paused states. The Mac
  menu stays a quick-glance surface: **Open Whisper Face…**, usage, pause,
  character choice, **Check for Updates…**, and quit are always there, and the
  Last Recognition, Voice Outbox, Voice Inbox, and Selective Re-listen rows
  each appear only once they have something to show. Tones, learned
  corrections, mode reference, and logs live in the app window; Windows adds
  character, pause, Flight Recorder, logs, and quit to its tray.
- **Experimental phone compatibility endpoint** — an OpenAI-compatible
  `/v1/audio/transcriptions` endpoint (port 8787) can support self-hosted
  clients such as [Diction](https://diction.one). Ordinary desktop mode binds
  it to loopback; only an explicit `--server-only` install binds the trusted
  LAN. Native iPhone work is deliberately deferred while the Mac experience
  is brought to product quality.
- **Always on** — installed as launch agents: starts at login, restarts on
  crash, keeps its models warm so the first dictation after a break is never
  slow.

## Requirements

| Platform | Requirements |
|---|---|
| macOS | Apple Silicon, macOS 14 or newer recommended, ~5 GB free (~8 GB with `--with-all-models`) |
| Windows | Windows 10/11 x64, ~8 GB free; NVIDIA GPU preferred, CPU fallback supported |

A default Mac install downloads only what dictation needs: Parakeet Unified
(~565 MB, primary recognition) and Whisper Tiny (~75 MB, the fast preview
pass). It also builds a pinned FluidAudio helper. Two models are optional
quality upgrades and are skipped unless you ask for them:

| Optional model | Size | What you lose without it |
|---|---|---|
| Whisper large-v3-turbo | ~1.6 GB | The accurate fallback for audio Parakeet declines; the cascade uses Whisper Tiny instead |
| Qwen3.5-4B | ~3.4 GB | Semantic cleanup; the deterministic compiler still punctuates and cleans every dictation |

Install them up front with `./setup.sh --with-all-models`, or add them later
with `./setup.sh --models`. Windows installs Whisper Tiny, Whisper
large-v3-turbo, and Qwen3.5-4B.
Whisper and Parakeet preload immutable audited revisions so a later upstream
model update cannot silently change a fresh install. Qwen's current Ollama tag
manifest is recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
must be re-audited if it moves.

## Install

Clone or download the repository, then use the one-click launcher for your OS:

Copy or extract the complete source folder to a writable local directory first;
do not run the installer from a read-only mounted DMG or protected folder. Both
installers check this before downloads or service changes.

- **Mac:** double-click `Install.command`.
- **Windows:** double-click `Install.cmd`.

From a terminal, the same installers are:

```sh
# macOS
./setup.sh

# macOS, including the optional large-v3-turbo and Qwen3.5-4B models
./setup.sh --with-all-models

# macOS, add those optional models to an existing installation
./setup.sh --models

# Windows PowerShell
.\setup.ps1
```

The launcher detects the OS. A Windows Git Bash or WSL invocation of
`./setup.sh` automatically hands off to `setup.ps1`. On a fresh machine it:

1. Installs the native package prerequisites (`Homebrew` on Mac or `winget`
   packages on Windows), then installs `uv`, `ffmpeg`, and Ollama.
2. Reproduces the locked Python dependency environment from
   `dictate.py.lock`.
3. Downloads the models it needs before launching the app: Parakeet Unified
   and Whisper Tiny on Mac (plus large-v3-turbo and Qwen3.5-4B when you pass
   `--with-all-models`), Qwen3.5-4B and both Whisper models on Windows. Each
   model is probed first, so a rerun reports what is already cached instead of
   announcing a download it will skip. On Mac it also builds and preloads the
   native Parakeet Unified helper.
4. Creates private configuration files without overwriting existing ones.
5. Installs and validates the tuned Ollama and dictation login services
   (`launchd` on Mac, Task Scheduler on Windows).
6. On Mac, installs and verifies `~/Applications/Whisper Face.app`, a tiny
   generic arm64 AppKit launcher. A DMG install preserves its packaged app byte
   for byte, including its Developer ID signature when present; a source-only
   install builds it unsigned locally. The exact checkout/revision and app
   digest live in a separate user-only `0600` receipt, so no machine state
   invalidates the app's signature. The app contains no copied runtime, models,
   or private state; it validates and starts the existing `launchd` service,
   then asks that one process to open its main GUI over a private, content-free
   local endpoint.

Homebrew's official installer may pause once to explain its changes and ask
for your macOS password. A default Mac install downloads about 650 MB of
models; `--with-all-models` adds roughly 5 GB, so that first run takes a
while. Subsequent runs are idempotent, reuse the caches, and say which models
were already present.

Every runtime change is governed by the repository's
[installer release process](docs/installer-release-process.md). It requires
Mac/Windows impact review, locked-environment and installer-contract tests,
live platform verification where available, and publication of the commit
before a fresh machine is told the build is available.

For an existing installation, follow the concise
[update and rollback guide](docs/distribution/update-and-rollback.md). Updates
remain explicit: a local helper can validate and install an already-prepared
clean sibling checkout, with linked verified current/candidate release
metadata required before apply, while leaving the current checkout intact as
the rollback copy. It does not download a candidate or run in the background.

macOS will prompt for permissions — enable **"uv"** under System Settings →
Privacy & Security → **Input Monitoring**, **Accessibility**, and
**Microphone**. The app waits and restarts itself automatically once granted.
While either evidenced permission is incomplete, Home and Advanced show
an accessible **Open System Settings** recovery action. It opens the generic
macOS settings app, never changes a permission, and refreshes the displayed
evidence while the Whisper Face window remains open.
Only an explicit headless `--server-only` install needs trusted-LAN/firewall
access for the experimental compatibility endpoint.

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
installation. On Mac it also freshly reconstructs the expected Ollama service
definition and requires its exact digest receipt plus an exact match between
the launchd PID and the endpoint's unique listener PID. Windows verifies its
scheduled task's current-user principal, sole action, private launcher receipt,
and exact current-checkout launcher before checking endpoint health and model
presence. Both platforms also verify that runtime logs exist with the expected
user-private posture. Windows' independently managed Ollama process does not
currently provide the same task-to-listener identity binding.

### Native Mac window

Click the selected animal in the menu bar and choose **Open Whisper Face…**.
The menu bar remains the fastest everyday control, while the window provides a
clear home for setup health, privacy controls, model status, character choice,
and Voice Outbox recovery. Home leads with your character, drawn live: it
breathes and blinks while idle and mouths along with the mic while you
dictate (System Settings Reduce Motion keeps it still). Closing the window
does not stop dictation.
A single **Last Recognition…** menu row appears once there is a result to look
at, and is a direct route to the transcript-free evidence inspector.
Advanced can copy a fixed support snapshot containing only categorized
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
the learning loop. This explicit mode binds port 8787 to reachable interfaces;
use only a trusted LAN and firewall, and never forward it to the internet. This
is not the planned native iPhone experience.
Verify that headless installation later with `./setup.sh --server-only --verify`;
the runtime/model/service checks still run while the AppKit construction gate
is correctly skipped.

## Uninstall

You can leave, and see exactly what leaving costs before it happens. Both
installers take `--uninstall`, which by default only prints a plan:

```sh
./setup.sh --uninstall              # macOS: list everything, change nothing
.\setup.ps1 --uninstall             # Windows: the same
```

The listing names every service, file, and directory the installer created,
with its full path, and separates them into three tiers. Nothing is removed
until you add `--yes`:

```sh
./setup.sh --uninstall --yes                          # the software
./setup.sh --uninstall --yes --remove-models          # and the models
./setup.sh --uninstall --yes --remove-personal-data   # and your words
```

- **The software** goes without ceremony: both login services, the Ollama and
  dictation LaunchAgents (or the Windows scheduled task, under its current and
  legacy names), `~/Applications/Whisper Face.app`, the Application Support
  receipts, the built native helper, and the Swift build scratch.
- **The models** are a separate choice because they are large (650 MB to 5 GB)
  and can be downloaded again: the Parakeet Unified Core ML model, the two
  pinned Whisper snapshots in the Hugging Face cache, and `qwen3.5:4b`, which
  Ollama itself is asked to drop.
- **Your personal files are kept unless you ask for them to go.** Your
  dictionary, snippets, tones, preferences, learned corrections, transcripts,
  Voice Inbox drafts, demonstrations, activation receipts, and logs are your
  words. No download restores them, so `--remove-personal-data` is the only
  thing that removes them, and the run tells you where they were left.

The uninstaller works from an explicit inventory, never a wildcard. It never
touches Homebrew, `uv`, `ffmpeg`, Ollama itself, the Swift toolchain, or any
other model in the shared Hugging Face cache — those are shared tools that
something else on your machine may need, and the output says so. It is safe to
run on a partial or already-clean installation: a missing service or app
bundle is reported and the run still succeeds.

Two things it deliberately leaves to you. The checkout itself stays where it
is — delete that folder by hand when you are done with it. And macOS privacy
grants live in a system database that no installer should edit, so remove
**Whisper Face** yourself under System Settings → Privacy & Security →
**Input Monitoring**, **Accessibility**, and **Microphone**.

Full detail, including a per-platform table of exactly what each tier removes,
is in the [update and rollback guide](docs/distribution/update-and-rollback.md).

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

A **Voice Inbox** menu-bar entry appears whenever the queue holds a draft, and
is the shortest route to the local inspector; its title adds only the bounded
queued count. An empty queue earns no menu row.
You can also choose **Inspect** beside Voice Object Commands in Privacy. The
first view lists only bounded draft number, type, and state. Draft text is read
only after you explicitly choose **Reveal**. A revealed queued task or
calendar draft can be copied to the Mac clipboard only after a second explicit
confirmation; a revealed queued email can similarly request the native Mail
compose sheet, but neither path sends or schedules anything. You can acknowledge
or cancel a selected draft and purge acknowledged/cancelled drafts; queued drafts
are not purged. After a successful task/calendar copy, a separate **Clear
Clipboard** action makes a best-effort change-count check and does nothing when
an intervening clipboard change is already visible. macOS provides no atomic
compare-and-clear, so an external change in the microscopic interval between
that check and clear can still race. The
inspector never reads clipboard content, pastes, sends, schedules, or executes a
draft, and it remains available when command diversion is turned off.

The Mac Privacy pane's **Risk confirmation (inert)** row is a safety ceremony,
not an agent-action launcher. Choose one of four closed risk classes and select
**Start**. While it is awaiting voice, an exact capture-mode dictation of
`confirm risky action` is consumed before compilation, transcript logging,
clipboard access, or insertion. Only then is **Confirm click** enabled, and the
ceremony expires after at most 30 monotonic seconds. `cancel risky action`, the
Cancel button, an early click, expiry, or replay all fail closed. The runtime
keeps only a RAM-only opaque ID plus risk/state/reason; it has no action payload
or execution callback.

Choose **Author** beside **Demonstrations** in the Mac Privacy pane to create an
inert Finder, Mail, Notes, or menu recipe. Whisper Face generates an opaque
local ID, lists only content-free metadata, and reveals private described steps
only when you explicitly select **Reveal/Edit**. From that revealed editor you
can record one bounded, domain-valid description or explicitly approve a
non-empty recipe. **Cancel Draft** atomically rolls an unapproved recipe and its
step text out of storage; **Delete Approved** explicitly removes a selected
approved recipe and its private text. Approval remains a local inert state:
this editor has no replay, automation, click, type, paste, Accessibility action,
subprocess, network, or application API path.

The bundled `EDIT ME` values are setup placeholders. On Mac, insert one and
replace the pasted placeholder in place within ten seconds; Whisper Face saves
that exact replacement to `snippets.json`. You can also edit the private JSON file
directly. Learned snippet edits appear by name under Settings →
Personalize → **Learned corrections**;
forgetting one restores its previous value when the file has not since changed.

On Windows, use **Right Alt** wherever the table says Right Option and the
**Windows key** wherever it says Command. Shift and Control are unchanged.

Everything personal stays in private, gitignored local files: `dictionary.txt`
(your terms; `-word` bans one), `snippets.json`, `tones.json`, `preferences.json`,
`transcripts.jsonl` (your history, trimmed to recent), `learned.json` (mined
vocabulary and fix rules), `voice_inbox.json` (only opt-in inert Voice Object
drafts), and `demonstrations.json` (manually authored inert recipe steps).
`acoustic_keyword_memory.json` stores bounded
keyword candidates, hashed app scopes, and evidence digests without raw audio,
surrounding context, or transcript history.
`acoustic_keyword_activation.json` and
`acoustic_calibration_activation.json` are private, content-minimized physical
evidence receipts. They must travel with the matching machine state if the
user intentionally migrates those activations.
`benchmark_acoustic_keyword_bias.py` exercises the keyword-bias evaluator with
constructed categorical fixtures only. Its physical-shaped fixtures test policy
branches and are explicitly not physical recognition evidence or an activation
claim.

Command mode intentionally recognizes only undo, redo, select all, copy, cut,
paste, delete selection, new line, and escape. It cannot launch arbitrary shell
commands. Edit mode requires a selection, so ordinary dictation cannot rewrite
an unseen document by accident.

Flight Recorder is disabled by default and must be enabled under Settings →
Privacy. Its audio is never written to disk: the bounded buffer is cleared after
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

Run `uv run benchmark_macos_asr_warm_path.py --run --format json` to opt into a
synthetic, content-free profile of the installed warm Parakeet helper. One
24-pair Mac run rejected replacing the current two writes with `writev`: output
matched, but wall p95 improved only 3.11% (35.558 to 34.451 ms), max improved
2.30% (35.632 to 34.814 ms), and client-overhead p95 worsened from 0.104 to
0.128 ms. The 10% p95-and-max gate was not met, so the benchmark has no runtime
authority and the shipping path remains unchanged.

Runtime startup and acoustic-health traces can be reduced to numeric
aggregates with `uv run performance_lab.py traces --trace-log dictate.log`.
The trace command ignores ordinary log lines, rejects any non-allowlisted or
non-numeric trace field, and never includes raw lines, input paths, app
identifiers, or transcript text in its table or JSON output.

Run `uv run benchmark_consequence_routing.py` for the synthetic selector-only
consequence corpus. Its closed artifact explicitly says that no audio,
verifier, runtime ASR backend, or physical device was exercised, and its 5 ms
gate uses the worst per-case p95 rather than corpus-average throughput.
The separate real-recording activation workflow is documented in
`docs/selective-relisten-activation.md`.

Run `uv run benchmark_cleanup_latency.py --run --format json` to compare the
current pinned Qwen3.5-4B structured-cleanup prompt with smaller prompt,
few-shot, and token-budget variants against thirty checked-in synthetic cases.
It contacts only local Ollama after the explicit `--run` opt-in, never reads
transcript logs, and independently compares each eligible model-provided edit
proof with the bounded standalone proof mediator. Its content-free report
includes baseline/recovered overlap, acceptance delta, fixed rejection reasons,
and recovery latency. One local run improved the current prompt from 2/6 to
4/6 proof-accepted cases with zero baseline-only losses and about 1.3 ms
recovery p95, and a three-shot variant reached 5/6 — but that was recorded
when the corpus held six cases, and it has not been re-run since the corpus
grew to thirty. Synthetic cases on one machine are not activation evidence
either way, so the benchmark has no runtime authority.

Run `uv run benchmark_cleanup_proof_recovery.py --format json` to exercise a
standalone exact-proof mediator over the same public synthetic cases. It
requires the existing output and semantic guards, independently limits lexical
changes and protected-anchor abandonment, emits only content-free aggregate
receipts, and currently recovers 27 of its 30 cases — the other three are
rejected as an unproved transformation or a removed protected anchor, which is
the mediator refusing rather than failing. The meaningful phrase `you know`
remains deliberately ineligible, so the mediator has no runtime authority and
the benchmark makes no no-worse quality claim.

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
and returns an explainable candidate. The Mac capture-mode runtime can now
insert the deterministic result immediately, finish Voice Compiler-proofed
cleanup in a daemon thread, and apply only safe edits after two exact
destination snapshots and one final exact Accessibility recheck. User edits
win, proposal IDs are single-use, correction learning is skipped for a
scheduled delayed pass, and receipts/logs contain only fixed outcomes and
counts. macOS Accessibility has no native atomic compare-and-swap, so a small
read-to-write scheduling window remains.

Delayed cleanup stays disabled unless a local, owner-only activation receipt
proves a manually reviewed caller-attested physical suite: at least 50 cases,
coverage across native, web, Electron, and terminal editors, balanced applied
and rejected outcomes, zero wrong-target/user-overwrite/duplicate failures,
no unexpected selection disruption, and p95 final-apply latency no greater
than 150 ms. Evidence files contain no transcript or application names.
Evaluate and install a passing receipt with:

```sh
uv run delayed_cleanup_activation.py delayed_cleanup_physical_cases.json \
  --manual-reviewed --write-receipt delayed_cleanup_activation.json
```

Missing, synthetic, malformed, mixed, or failing evidence leaves the feature
off. The physical suite has not been run in this repository, so no live safety
or application-compatibility claim is made yet.

The guided sessions that would produce that suite, the physical 50-app
insertion matrix, and the physical lifecycle/stress evidence live in
[docs/evidence/physical-sessions.md](docs/evidence/physical-sessions.md) with
`scripts/capture_app_matrix.py`, `scripts/capture_delayed_cleanup_cases.py`,
and `scripts/capture_lifecycle_evidence.py`. Each session records only what the
runtime reported or the operator chose from a closed list, writes transcript-free
artifacts to a private gitignored directory, and reports coverage as measured
rather than extrapolated. None of them writes an activation receipt.

`voice_objects.py`, `voice_inbox.py`, and `drop_to_target.py` define inert,
local foundations for the next interaction layer. Typed drafts can now enter
the local inbox through canonical closed-schema JSON and explicit-read decoding,
but there is still no live destination, automatic dispatch, or agent execution.
`macos_drop_to_target_snapshot.py` is likewise unwired: an explicit caller can
request bounded Accessibility evidence for a future decision, and accessible
titles/descriptions are treated as transient private labels. The adapter has no
logging, persistence, drag/drop, or AX action surface.

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
local preview or, when Apple credentials are supplied, a generic signed app
inside a signed, notarized, and stapled disk image, together with a source ZIP,
update/rollback manifest, and SHA-256 checksums. See the
[Mac release runbook](docs/distribution/macos-release.md).
The signed path additionally requires the production Apple Team ID to match the
repo-pinned `config/macos-signing-policy.json`; its current `null` value safely
disables signing until the Project Owner records the real Team ID. Unsigned
local builds and source installs remain available.

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
**Exact Source** under Advanced. Network-facing installs publish the same
commit-specific source offer at `GET /source` and the shipped notices at
`GET /license`. Modified deployments should set `WHISPER_FACE_SOURCE_URL` and,
for packaged builds without Git metadata, `WHISPER_FACE_SOURCE_REVISION` to
their corresponding source.
