---
title: "Whisper Face"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [product, overview, dictation, local-first]
aliases: [whispering-parrot, the-app]
summary: "Local-first voice input for Mac: hold a key, speak, and trustworthy text appears at the cursor — no account, no cloud, no audio leaving the machine."
confidence: high
---

# Whisper Face

## Definition

Whisper Face is a local-first voice-input layer for macOS (with Windows
installer parity) that turns an utterance into text suitable for the
focused application while preserving the speaker's meaning, factual
anchors, privacy, and control. Hold Right Option, speak, release —
cleaned text pastes wherever the cursor is. All recognition, cleanup,
and learning run on-device.

## Key Properties

- **Setup is part of the product** — one-click installers provision the
  app, locked dependencies, pinned models, the native helper, login
  services, and health checks ([[installers-and-services]]).
- **Speech evidence stays authoritative** — recognizers produce
  hypotheses; the [[voice-compiler]] may improve presentation but cannot
  invent unsupported meaning ([[protected-anchor]], [[proof-edit]]).
- **Insertion is a transaction** — the target is leased and revalidated
  before exactly one paste attempt; drift goes to a recoverable outbox
  instead of the wrong field ([[insertion-transaction]]).
- **Personal without surveillance** — corrections become scoped,
  inspectable local rules and regression cases ([[personalization]]),
  and risky capabilities unlock only through physical-evidence receipts
  ([[activation-receipt]]).
- **A face, not a dialog** — fourteen animated companion characters live in
  the HUD, the [[menu-bar]] and the [[app-window]]
  ([[whisper-faces]]), all speaking one [[design-language]].

## The pipeline at a glance

microphone → warm [[asr-cascade]] → VoiceIR → [[protected-anchor]]s +
[[proof-edit]]s → bounded [[cleanup-pipeline]] →
[[insertion-transaction]] → paste/readback or outbox → confirmed
correction → [[personalization]].

The full walk-through lives on [[dictation-pipeline]] and in
[[synth-2026-07-26-what-happens-when-i-dictate]].

## Subsystem map

- Core path: [[dictation-pipeline]], [[asr-cascade]],
  [[voice-compiler]], [[cleanup-pipeline]], [[delayed-cleanup]],
  [[insertion-transaction]], [[stable-prefix]], [[voice-modes]]
- Trust: [[consequence-receipts]], [[context-firewall]],
  [[personalization]], [[activation-receipt]],
  [[acoustic-personalization]], [[model-wallet]]
- Actions (inert today): [[voice-objects]], [[point-and-speak]],
  [[inert-foundations]]
- Presentation: [[whisper-faces]], [[menu-bar]], [[app-window]],
  [[design-language]], [[marketing-site]]
- Operations: [[installers-and-services]], [[distribution]],
  [[benchmarks]], [[evidence-capture]], [[windows-support]]
- Policy: [[governance]], [[privacy-and-security]]

## What changed on 2026-07-26

> 📝 The wiki's first build described the app at `b49699f`. Later the
> same day the menu shrank to six choices, the window collapsed to
> Home/Settings/Advanced and was rebuilt around a shared visual language,
> the site adopted the app's springs, four evidence-capture harnesses
> landed, two circular activation gates were filed as blockers, three
> releases shipped, and the repository is public. See
> [[2026-07-26-interface-rebuild-research]] and
> [[2026-07-26-evidence-capture-research]].

## References

- README.md, CONTEXT.md, docs/architecture-and-interop.md
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-trust-personalization-research]],
  [[2026-07-26-voice-actions-research]],
  [[2026-07-26-ops-governance-research]],
  [[2026-07-26-interface-rebuild-research]],
  [[2026-07-26-evidence-capture-research]]
