<p align="center">
  <img src="icons/core.svg" width="128" alt="Whispering Parrot icon"/>
</p>

<h1 align="center">Whispering Parrot</h1>

<p align="center"><b>Free, fully local hold-to-talk dictation for macOS.</b><br/>
Hold a key, speak, release — polished text appears wherever your cursor is.<br/>
No subscription, no cloud, no audio ever leaving your machine.</p>

---

Whispering Parrot is a single-file dictation stack built to match (and in places beat) the
commercial tools: [mlx-whisper](https://github.com/ml-explore/mlx-examples)
for on-device speech recognition and a local LLM via
[Ollama](https://ollama.com) for cleanup. On an M-series Mac, routine
dictations paste in **~0.2–0.5s**.

## Features

- **Hold-to-talk anywhere** — hold Right Option, speak, release; text pastes
  into whatever app has focus, with a frosted HUD waveform while you talk.
- **Rolling recognition** — long dictations are transcribed *while you're
  still talking* (segments cut at natural pauses), so a 60-second ramble
  pastes as fast as a one-liner.
- **LLM cleanup with a safety net** — fillers and false starts removed,
  self-corrections applied ("Tuesday, actually Wednesday" → Wednesday),
  punctuation fixed. An output guard rejects anything that isn't a faithful
  cleanup (refusals, over-deletions, truncations) and falls back to the
  lightly polished raw transcript — the model can never eat your words.
- **Tone awareness** — casual in Slack/Messages (texting style: no trailing
  period), formal prose in Mail, technical in editors and AI chats, verbatim
  in terminals. Pick a tone per app from the menu-bar **App Tones** picker,
  or force one per-dictation: *"Formal tone, …"*, *"casual, …"*.
- **Spoken structure** — "new line" / "new paragraph" / "scratch that" work;
  enumerations ("two things: …") become tidy dash lists.
- **Self-learning dictionary** — new vocabulary is mined from your usage and
  promoted automatically; if you *correct* a word after pasting, the fix is
  learned instantly, and a fix made twice becomes a guaranteed replacement.
- **Snippets** — say "insert my email" and your saved text pastes instead.
- **Whispering works** — quiet speech is gain-normalized before recognition.
- **Menu-bar presence** — the parrot perches in your menu bar (🔴 while
  recording, 🟠 processing, ⏸ paused), with usage stats, a pause toggle,
  and quit in its menu.
- **iPhone keyboard** — an OpenAI-compatible `/v1/audio/transcriptions`
  endpoint (port 8787) plugs straight into the
  [Diction](https://diction.one) iOS keyboard's Self-Hosted mode, with your
  same dictionary, snippets, and cleanup.
- **Always on** — installed as launch agents: starts at login, restarts on
  crash, keeps its models warm so the first dictation after a break is never
  slow.

## Requirements

- Apple Silicon Mac (MLX requires it)
- [Homebrew](https://brew.sh)
- ~6 GB disk for models (Whisper large-v3-turbo + Qwen3.5-4B)

## Install

```sh
git clone https://github.com/Aiml3ss/whispering-parrot.git
cd whispering-parrot
./setup.sh
```

That's it. The script installs `uv`, `ffmpeg`, and `ollama`, pulls the
models, and installs two launch agents (`com.berg.ollama`, tuned with flash
attention, and `com.berg.dictate`). First launch downloads Whisper (~1.6 GB).

macOS will prompt for permissions — enable **"uv"** under System Settings →
Privacy & Security → **Input Monitoring**, **Accessibility**, and
**Microphone**. The app waits and restarts itself automatically once granted.
Allow the firewall prompt if you want the iPhone endpoint.

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
| (hold Right Option, talk, release) | cleaned text at your cursor |
| "…um so basically…" | fillers gone |
| "Tuesday — actually Wednesday" | just Wednesday, in place |
| "two things: … and second …" | a dash list |
| "new paragraph" | a real paragraph break |
| "scratch that" | previous sentence dropped |
| "Formal tone, …" / "casual, …" / "verbatim: …" | that style, this once |
| "insert my email" | your snippet from `snippets.json` |

Everything personal stays in gitignored local files: `dictionary.txt` (your
terms; `-word` bans one), `snippets.json`, `transcripts.jsonl` (your history,
trimmed to recent), `learned.json` (mined vocabulary and fix rules).

## Tuning

The knobs live at the top of `dictate.py` — hotkey, ports, the
quick-path/LLM word threshold, chunking aggressiveness, silence gates, tone
per app-bundle. The `eval_cleanup.py` harness replays adversarial cases and
your own transcripts through candidate Ollama models if you want to test a
different cleanup model.

## License

MIT — see [LICENSE](LICENSE).
