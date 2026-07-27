---
title: "Whisper Face"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
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
- **A face, not a dialog** — sixteen animated companion characters live in
  the HUD, the [[menu-bar]] and the [[app-window]]
  ([[whisper-faces]]), all speaking one [[design-language]].
- **Eleven languages, honestly routed** (#135) — English, Spanish,
  French, German, Italian, Portuguese, Dutch, Russian, Japanese, Korean,
  and Chinese. Non-English never touches the English-only Parakeet
  checkpoint, and every English-assuming cleanup rule stands down when
  it cannot apply ([[asr-cascade]], [[cleanup-pipeline]]).

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

## What changed on 2026-07-27

> 📝 Eleven-language dictation landed with the Parakeet routing fix
> behind it (#135), and the update check finally answers about the build
> you are running instead of the checkout on disk (#132). The
> speech-stays-local promise became two layers of tests and the supply
> chain got pinned end to end (#133); publishing a synthetic number as a
> physical one became structurally impossible (#134). Four new
> companions, configurable keys, undo, sound toggles and an uninstaller
> shipped (#127, #128, #125), the Tiny cross-check replaced the fixed
> Parakeet confidence prior (#126), the rename to whisper-face finished
> before it could ship again (#137), and **v0.3.0** went out with the
> site pointing at it ([[distribution]]). After the tag, Pickles and
> Olive — the English cream goldens — made it sixteen companions
> (#139); they ride the next release.

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
