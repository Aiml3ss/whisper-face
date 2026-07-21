<p align="center">
  <img src="icons/core.svg" width="128" alt="Whispering Parrot icon"/>
</p>

<h1 align="center">Whispering Parrot</h1>

<p align="center"><b>Free, fully local hold-to-talk dictation for macOS and Windows.</b><br/>
Hold a key, speak, release — polished text appears wherever your cursor is.<br/>
No subscription, no cloud, no audio ever leaving your machine.</p>

---

Whispering Parrot is a single-file dictation stack built to match (and in places beat) the
commercial tools. It uses [mlx-whisper](https://github.com/ml-explore/mlx-examples)
on Apple Silicon and [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
on Windows, plus a local LLM through [Ollama](https://ollama.com) for cleanup.
Routine dictations use a low-latency fast path, while longer speech is
recognized in the background before you release the key.

## Features

- **Flight Recorder (experimental)** — enable its menu-bar toggle, speak
  naturally, then tap Right Option afterward. Parrot finds and pastes the
  latest utterance from a 20-second RAM-only buffer. Holding Right Option still
  performs normal push-to-talk.
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
- **Pre-resolved local models** — MLX model repositories are resolved once at
  launch and every decode uses the cached snapshot path directly, avoiding a
  repeated Hugging Face metadata walk on the release-critical path.
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
- **Six explicit voice modes** — modifier keys turn the same Right Option
  gesture into faithful capture, polished composition, context-aware reply,
  selected-text editing, spoken-code compilation, or a small allowlist of
  reversible editing commands.
- **Tone awareness** — casual in Slack/Messages (texting style: no trailing
  period), formal prose in Mail, technical in editors and AI chats, verbatim
  in terminals. Pick a tone per app from the menu-bar **App Tones** picker,
  or force one per-dictation: *"Formal tone, …"*, *"casual, …"*.
- **Spoken structure** — "new line" / "new paragraph" / "scratch that" work;
  enumerations ("two things: …") become tidy dash lists.
- **Reversible personalization** — vocabulary is mined from your usage, while
  corrections are learned only when the exact pasted range is changed. A
  correction activates after two confirmations in the same app or three
  globally, and every learned mapping can be inspected or forgotten from the
  menu.
- **Self-editing snippets (Mac)** — say "insert my email" and your saved text
  pastes instead. Edit that exact insertion within ten seconds and the revised
  value is saved for next time and listed under **Learned Corrections**.
- **Whispering works** — quiet speech is gain-normalized before recognition.
- **Menu-bar/tray presence** — the parrot perches in your menu bar or Windows
  notification area (🔴 while recording, 🟠 processing, ⏸ paused). The Mac
  menu includes usage, tones, recognition alternatives, and learned-correction
  controls; the Windows tray provides pause, Flight Recorder, logs, and quit.
- **iPhone keyboard** — an OpenAI-compatible `/v1/audio/transcriptions`
  endpoint (port 8787) plugs straight into the
  [Diction](https://diction.one) iOS keyboard's Self-Hosted mode, with your
  same dictionary, snippets, and cleanup.
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

### Matching another Mac

The installer reproduces the application, models, performance settings, and
default behavior. Personal state is intentionally gitignored. To carry your
MacBook vocabulary and preferences to a Mac mini, securely copy any desired
`dictionary.txt`, `snippets.json`, `tones.json`, `preferences.json`, and
`learned.json` files into the cloned folder **before** running `./setup.sh`.
Existing copies are preserved and locked to user-only permissions. You do not
need `transcripts.jsonl` unless you also want the old transcript history.

**Headless / server Mac** (e.g. a Mac mini serving only your iPhone):

```sh
./setup.sh --server-only
```

No hotkey, no HUD, no permission prompts — just the endpoint and the
learning loop. Then in the Diction app on iOS: *Self-Hosted* →
`http://<that-mac's-ip>:8787/v1/audio/transcriptions`.

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
| "two things: … and second …" | a dash list |
| "new paragraph" | a real paragraph break |
| "scratch that" | previous sentence dropped |
| "Formal tone, …" / "casual, …" / "verbatim: …" | that style, this once |
| "insert my email" | your snippet from `snippets.json` |

The bundled `EDIT ME` values are setup placeholders. On Mac, insert one and
replace the pasted placeholder in place within ten seconds; Parrot saves that
exact replacement to `snippets.json`. You can also edit the private JSON file
directly. Learned snippet edits appear by name under **Learned Corrections**;
forgetting one restores its previous value when the file has not since changed.

On Windows, use **Right Alt** wherever the table says Right Option and the
**Windows key** wherever it says Command. Shift and Control are unchanged.

Everything personal stays in private, gitignored local files: `dictionary.txt`
(your terms; `-word` bans one), `snippets.json`, `tones.json`, `preferences.json`,
`transcripts.jsonl` (your history, trimmed to recent), and `learned.json`
(mined vocabulary and fix rules).

Command mode intentionally recognizes only undo, redo, select all, copy, cut,
paste, delete selection, new line, and escape. It cannot launch arbitrary shell
commands. Edit mode requires a selection, so ordinary dictation cannot rewrite
an unseen document by accident.

Flight Recorder is disabled by default and must be enabled from the parrot
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

## Verify

Run the fast regression suite after changing the pipeline:

```sh
uv run tests/test_parrot_core.py
uv run tests/test_voice_compiler.py
uv run tests/test_benchmark_voice_compiler.py
uv run tests/test_benchmark_asr.py
uv run tests/test_dictate.py
```

## License

MIT — see [LICENSE](LICENSE).
