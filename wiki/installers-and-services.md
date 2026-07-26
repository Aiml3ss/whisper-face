---
title: "Installers and Services"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [install, launchd, services, operations]
aliases: [setup-sh, setup-ps1, launcher-app, health-endpoint]
summary: "One-click, idempotent installers provision dependencies, pinned models, the native helper, login services, and health checks; generated services are replaced while private user files always survive."
confidence: high
---

# Installers and Services

## Definition

`Install.command` / `Install.cmd` are thin shims over `setup.sh` /
`setup.ps1`. Setup is part of the product: a fresh Mac or Windows
machine reaches a verified, always-on dictation service in one run, and
reruns are idempotent.

## Key Properties

- **Router and gates**: `setup.sh` re-execs PowerShell on
  MinGW/WSL; bare Linux hard-fails. Manifest (~140 required files),
  arm64 (Mac) / x64 (Windows), free-space, and a writable-checkout
  proof that fails on read-only DMGs *before* any download.
- **Mac flow**: private 0600 logs provisioned before services can
  create them; Homebrew + uv/ffmpeg/ollama; model inventory probed so
  announcements reflect actual work; Parakeet Core ML preload +
  Swift helper build + verify; Ollama LaunchAgent with a warm fast path
  (byte-identical plist + digest receipt + launchd-PID-equals-listener
  identity skips reload); locked env from `dictate.py.lock`; templates
  copied only when absent; the dictation LaunchAgent; the launcher app;
  a readiness wait; health check and full verify.
- **Default small**: Parakeet + Whisper Tiny (~650 MB);
  large-v3-turbo and Qwen are opt-in (`--with-all-models` / `--models`).
  Windows always installs Tiny + Turbo + Qwen.
- **Services**: `com.berg.dictate` (RunAtLoad, umask 077, KeepAlive
  except successful exit so the single-instance lock's exit 0 stops
  respawn) and `com.berg.ollama` (flash attention on, one parallel slot
  for KV-cache hits). Windows registers a current-user Task Scheduler
  task over a generated, digest-receipted launcher shim.
- **The launcher app** exists so macOS attributes Input Monitoring /
  Accessibility / Microphone to one grantable "Whisper Face" identity
  that survives reinstalls. The bundle contains no runtime or paths;
  machine binding lives in an outside 0600 receipt; local builds get a
  deterministic ad-hoc signature; at runtime it starts the existing
  service and writes exactly one byte to a PID+revision-bound 0600
  socket to show the GUI.
- **Verify modes** are read-only and never repair: tools, helper,
  plists, logs, launcher binding, locks, model pins, agents, health —
  plus a bounded native GUI smoke test on Mac. The documented platform
  asymmetry: Mac proves launchd-PID-to-listener identity; Windows
  cannot (Ollama is independently managed).
- **Health endpoint** (port 8787): `/health` ok, `/source` the AGPL
  source offer, `/license` the notices, and the OpenAI-compatible
  transcription POST. Loopback unless an explicit `--server-only`
  install (trusted-LAN, unauthenticated, documented as a trust
  boundary).
- **Idempotency invariant**: generated services are replaced; private
  user files survive; satisfied steps skip; verification never skips.

## Related Concepts

- [[distribution]] — updates, packaging, releases
- [[asr-cascade]] — what the models serve
- [[governance]] — installer parity as a release gate
- [[windows-support]] — the PowerShell twin

## References

- setup.sh, setup.ps1, scripts/macos_launcher_app.py, plist templates;
  docs/installer-release-process.md
- [[2026-07-26-ops-governance-research]]
