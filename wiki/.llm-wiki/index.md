# Wiki Index
<!-- AUTO-GENERATED — DO NOT EDIT BY HAND -->
**Last generated:** 2026-07-26T22:57:34Z (hand-refreshed 2026-07-27 for #115-#121)
**Source hash:** 9b9260c4fb83273c32f9cf5143eece6273911d2ae6261109c0182e16a3ff5460
**Total pages:** 39

## All Pages

| Slug | Title | Type | Lang | Tags | Summary | Modified |
|------|-------|------|------|------|---------|----------|
| 2026-07-26-evidence-capture-research | Evidence Capture Research Notes | article | en | research, evidence, activation, gates, releases | Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335. | 2026-07-26 |
| 2026-07-26-interface-rebuild-research | Interface Rebuild Research Notes | article | en | research, interface, gui, menu-bar, motion | Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335. | 2026-07-26 |
| 2026-07-26-ops-governance-research | Ops & Governance Research Notes | article | en | research, operations, installers, governance | Imported research notes on installers, services, updates, packaging, the native helper, the site, the benchmark family, tests, and governance. | 2026-07-26 |
| 2026-07-26-runtime-pipeline-research | Runtime Pipeline Research Notes | article | en | research, runtime, pipeline, dictate-py | Imported research notes mapping dictate.py, the pure core modules, the verifier stack, delayed cleanup, and the end-to-end utterance flow at commit b49699f. | 2026-07-26 |
| 2026-07-26-trust-personalization-research | Trust & Personalization Research Notes | article | en | research, trust, personalization, evidence | Imported research notes on the acoustic stores, activation receipts, regression lab, shadow gate, model wallet, and the shared fail-closed design language. | 2026-07-26 |
| 2026-07-26-voice-actions-research | Voice-Action Foundations Research Notes | article | en | research, actions, inert, foundations | Imported research notes on voice objects, point-and-speak, drop-to-target, the protocol trio, demonstration drafts, the risky ceremony, and why none of it can act by surprise. | 2026-07-26 |
| acoustic-personalization | Acoustic Personalization | concept | en | acoustics, keywords, calibration, privacy, evidence | Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer. | 2026-07-27 |
| activation-receipt | Activation Receipt | concept | en | evidence, safety, pattern, activation | The house pattern for risky capabilities: features ship off and unlock only via a 0600 receipt this machine produced from manually reviewed physical evidence — with policy, model, and evidence pinning. | 2026-07-27 |
| app-window | App Window | concept | en | gui, macos, ux, surfaces, onboarding | The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector. | 2026-07-27 |
| asr-cascade | ASR Cascade | concept | en | asr, recognition, parakeet, whisper, performance | The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold. | 2026-07-26 |
| benchmarks | Benchmarks | concept | en | benchmarks, evidence, performance, labs | A family of offline, transcript-free labs whose shared rule is no runtime authority: evidence can reject changes and build activation receipts, but nothing in the runtime moves on a benchmark's say-so. | 2026-07-27 |
| cleanup-pipeline | Cleanup Pipeline | concept | en | cleanup, llm, qwen, deterministic, safety | Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits. | 2026-07-26 |
| consequence-receipts | Consequence Receipts | concept | en | risk, verification, relisten, safety, evidence | Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text. | 2026-07-27 |
| context-firewall | Context Firewall | concept | en | context, shadow, safety, evidence | Every finalized, insertion-bound contextual compile is compared with a context-free shadow compile; protected influence is quarantined in a transcript-free receipt that can change nothing. | 2026-07-26 |
| delayed-cleanup | Delayed Cleanup | concept | en | cleanup, insertion, merge, activation, macos | Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt. | 2026-07-27 |
| design-language | Design Language | concept | en | design, motion, typography, color, accessibility | One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE. | 2026-07-27 |
| dictation-pipeline | Dictation Pipeline | concept | en | pipeline, runtime, dictate-py, architecture | The end-to-end path one utterance takes from key-down to pasted text, and the invariants that hold along it. | 2026-07-26 |
| distribution | Distribution | concept | en | updates, packaging, signing, releases, operations | Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest. | 2026-07-26 |
| evidence-capture | Evidence Capture | concept | en | evidence, activation, harness, operator, safety | Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent. | 2026-07-27 |
| governance | Governance | concept | en | licensing, cla, ci, release-gates, policy | AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass. | 2026-07-26 |
| inert-foundations | Inert Foundations | concept | en | foundations, inert, safety, protocol, macos | The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution. | 2026-07-26 |
| insertion-transaction | Insertion Transaction | concept | en | insertion, safety, outbox, core, macos | Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox. | 2026-07-27 |
| installers-and-services | Installers and Services | concept | en | install, launchd, services, operations | One-click, idempotent installers provision dependencies, pinned models, the native helper, login services, and health checks; generated services are replaced while private user files always survive. | 2026-07-26 |
| marketing-site | Marketing Site | concept | en | site, astro, web, cloudflare | A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the ten characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions. | 2026-07-26 |
| menu-bar | Menu Bar | concept | en | menu-bar, macos, ux, surfaces | Six choices by default and five rows that appear only when they have something to offer — the everyday control surface, with everything else moved into the window. | 2026-07-27 |
| model-wallet | Model Wallet | concept | en | models, routing, evidence, foundation | A provider-neutral routing policy over the four pinned models — deliberately not wired to live routing, with filesystem evidence capped below readiness and a fail-closed shadow advisory. | 2026-07-26 |
| personalization | Personalization | concept | en | learning, corrections, priors, regression, safety | Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate. | 2026-07-26 |
| point-and-speak | Point-and-Speak | concept | en | actions, accessibility, transaction, macos | Say a target's name, preview the single confident match, then explicitly confirm one AXPress on a strongly named control — with nonces, leases, exact rechecks, and text fields excluded by construction. | 2026-07-26 |
| privacy-and-security | Privacy and Security | concept | en | privacy, security, threat-model, receipts | No account, no upload, no sale, no shared-model training; RAM-only audio; content-free receipts everywhere; seven security invariants; and a support bundle that is a double allowlist. | 2026-07-26 |
| proof-edit | Proof Edit | concept | en | safety, cleanup, glossary, core | A bounded transformation with an exact source span, before/after text, kind, and validation outcome — capture-mode cleanup applies only validated proof edits. | 2026-07-26 |
| protected-anchor | Protected Anchor | concept | en | safety, cleanup, glossary, core | Factual or code-shaped content — names, numbers, dates, URLs, paths, identifiers, commands — that cleanup must not delete or change without explicit spoken instruction. | 2026-07-26 |
| stable-prefix | Stable Prefix | concept | en | feedback, hud, glossary, core | The leading text supported by enough completed audio and cross-hypothesis agreement that later speech cannot invalidate it — the only text the HUD ever shows live. | 2026-07-26 |
| synth-2026-07-26-what-happens-when-i-dictate | What Happens When I Dictate? | synthesis | en | pipeline, narrative, overview | From key-down to pasted text: the warm capture, rolling recognition, evidence compilation, guarded cleanup, and the single transactional paste. | 2026-07-26 |
| voice-compiler | Voice Compiler | concept | en | compiler, voiceir, span-graph, evidence, core | The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning. | 2026-07-26 |
| voice-modes | Voice Modes | concept | en | modes, tones, snippets, hotkey, ux | Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use. | 2026-07-26 |
| voice-objects | Voice Objects | concept | en | actions, drafts, inbox, inert, macos | Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate. | 2026-07-26 |
| whisper-face | Whisper Face | concept | en | product, overview, dictation, local-first | Local-first voice input for Mac: hold a key, speak, and trustworthy text appears at the cursor — no account, no cloud, no audio leaving the machine. | 2026-07-26 |
| whisper-faces | Whisper Faces | concept | en | faces, characters, hud, animation, design | The ten chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap. | 2026-07-27 |
| windows-support | Windows Support | concept | en | windows, platform, parity | Windows shares the core capture, cascade, cleanup, snippets, tones, and learning pipeline with one-click installer parity — while the Cocoa HUD, Accessibility context, and several macOS-only trust features stay Mac. | 2026-07-27 |

## By Tag

### accessibility (2 pages)
- [[design-language]] — One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE.
- [[point-and-speak]] — Say a target's name, preview the single confident match, then explicitly confirm one AXPress on a strongly named control — with nonces, leases, exact rechecks, and text fields excluded by construction.

### acoustics (1 pages)
- [[acoustic-personalization]] — Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer.

### actions (3 pages)
- [[2026-07-26-voice-actions-research]] — Imported research notes on voice objects, point-and-speak, drop-to-target, the protocol trio, demonstration drafts, the risky ceremony, and why none of it can act by surprise.
- [[point-and-speak]] — Say a target's name, preview the single confident match, then explicitly confirm one AXPress on a strongly named control — with nonces, leases, exact rechecks, and text fields excluded by construction.
- [[voice-objects]] — Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate.

### activation (4 pages)
- [[2026-07-26-evidence-capture-research]] — Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335.
- [[activation-receipt]] — The house pattern for risky capabilities: features ship off and unlock only via a 0600 receipt this machine produced from manually reviewed physical evidence — with policy, model, and evidence pinning.
- [[delayed-cleanup]] — Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt.
- [[evidence-capture]] — Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent.

### animation (1 pages)
- [[whisper-faces]] — The ten chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap.

### architecture (1 pages)
- [[dictation-pipeline]] — The end-to-end path one utterance takes from key-down to pasted text, and the invariants that hold along it.

### asr (1 pages)
- [[asr-cascade]] — The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold.

### astro (1 pages)
- [[marketing-site]] — A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the ten characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions.

### benchmarks (1 pages)
- [[benchmarks]] — A family of offline, transcript-free labs whose shared rule is no runtime authority: evidence can reject changes and build activation receipts, but nothing in the runtime moves on a benchmark's say-so.

### calibration (1 pages)
- [[acoustic-personalization]] — Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer.

### characters (1 pages)
- [[whisper-faces]] — The ten chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap.

### ci (1 pages)
- [[governance]] — AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass.

### cla (1 pages)
- [[governance]] — AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass.

### cleanup (4 pages)
- [[cleanup-pipeline]] — Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits.
- [[delayed-cleanup]] — Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt.
- [[proof-edit]] — A bounded transformation with an exact source span, before/after text, kind, and validation outcome — capture-mode cleanup applies only validated proof edits.
- [[protected-anchor]] — Factual or code-shaped content — names, numbers, dates, URLs, paths, identifiers, commands — that cleanup must not delete or change without explicit spoken instruction.

### cloudflare (1 pages)
- [[marketing-site]] — A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the ten characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions.

### color (1 pages)
- [[design-language]] — One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE.

### compiler (1 pages)
- [[voice-compiler]] — The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning.

### context (1 pages)
- [[context-firewall]] — Every finalized, insertion-bound contextual compile is compared with a context-free shadow compile; protected influence is quarantined in a transcript-free receipt that can change nothing.

### core (5 pages)
- [[insertion-transaction]] — Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox.
- [[proof-edit]] — A bounded transformation with an exact source span, before/after text, kind, and validation outcome — capture-mode cleanup applies only validated proof edits.
- [[protected-anchor]] — Factual or code-shaped content — names, numbers, dates, URLs, paths, identifiers, commands — that cleanup must not delete or change without explicit spoken instruction.
- [[stable-prefix]] — The leading text supported by enough completed audio and cross-hypothesis agreement that later speech cannot invalidate it — the only text the HUD ever shows live.
- [[voice-compiler]] — The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning.

### corrections (1 pages)
- [[personalization]] — Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate.

### design (2 pages)
- [[design-language]] — One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE.
- [[whisper-faces]] — The ten chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap.

### deterministic (1 pages)
- [[cleanup-pipeline]] — Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits.

### dictate-py (2 pages)
- [[2026-07-26-runtime-pipeline-research]] — Imported research notes mapping dictate.py, the pure core modules, the verifier stack, delayed cleanup, and the end-to-end utterance flow at commit b49699f.
- [[dictation-pipeline]] — The end-to-end path one utterance takes from key-down to pasted text, and the invariants that hold along it.

### dictation (1 pages)
- [[whisper-face]] — Local-first voice input for Mac: hold a key, speak, and trustworthy text appears at the cursor — no account, no cloud, no audio leaving the machine.

### drafts (1 pages)
- [[voice-objects]] — Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate.

### evidence (10 pages)
- [[2026-07-26-evidence-capture-research]] — Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335.
- [[2026-07-26-trust-personalization-research]] — Imported research notes on the acoustic stores, activation receipts, regression lab, shadow gate, model wallet, and the shared fail-closed design language.
- [[acoustic-personalization]] — Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer.
- [[activation-receipt]] — The house pattern for risky capabilities: features ship off and unlock only via a 0600 receipt this machine produced from manually reviewed physical evidence — with policy, model, and evidence pinning.
- [[benchmarks]] — A family of offline, transcript-free labs whose shared rule is no runtime authority: evidence can reject changes and build activation receipts, but nothing in the runtime moves on a benchmark's say-so.
- [[consequence-receipts]] — Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text.
- [[context-firewall]] — Every finalized, insertion-bound contextual compile is compared with a context-free shadow compile; protected influence is quarantined in a transcript-free receipt that can change nothing.
- [[evidence-capture]] — Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent.
- [[model-wallet]] — A provider-neutral routing policy over the four pinned models — deliberately not wired to live routing, with filesystem evidence capped below readiness and a fail-closed shadow advisory.
- [[voice-compiler]] — The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning.

### faces (1 pages)
- [[whisper-faces]] — The ten chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap.

### feedback (1 pages)
- [[stable-prefix]] — The leading text supported by enough completed audio and cross-hypothesis agreement that later speech cannot invalidate it — the only text the HUD ever shows live.

### foundation (1 pages)
- [[model-wallet]] — A provider-neutral routing policy over the four pinned models — deliberately not wired to live routing, with filesystem evidence capped below readiness and a fail-closed shadow advisory.

### foundations (2 pages)
- [[2026-07-26-voice-actions-research]] — Imported research notes on voice objects, point-and-speak, drop-to-target, the protocol trio, demonstration drafts, the risky ceremony, and why none of it can act by surprise.
- [[inert-foundations]] — The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution.

### gates (1 pages)
- [[2026-07-26-evidence-capture-research]] — Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335.

### glossary (3 pages)
- [[proof-edit]] — A bounded transformation with an exact source span, before/after text, kind, and validation outcome — capture-mode cleanup applies only validated proof edits.
- [[protected-anchor]] — Factual or code-shaped content — names, numbers, dates, URLs, paths, identifiers, commands — that cleanup must not delete or change without explicit spoken instruction.
- [[stable-prefix]] — The leading text supported by enough completed audio and cross-hypothesis agreement that later speech cannot invalidate it — the only text the HUD ever shows live.

### governance (1 pages)
- [[2026-07-26-ops-governance-research]] — Imported research notes on installers, services, updates, packaging, the native helper, the site, the benchmark family, tests, and governance.

### gui (2 pages)
- [[2026-07-26-interface-rebuild-research]] — Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335.
- [[app-window]] — The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector.

### harness (1 pages)
- [[evidence-capture]] — Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent.

### hotkey (1 pages)
- [[voice-modes]] — Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use.

### hud (2 pages)
- [[stable-prefix]] — The leading text supported by enough completed audio and cross-hypothesis agreement that later speech cannot invalidate it — the only text the HUD ever shows live.
- [[whisper-faces]] — The ten chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap.

### inbox (1 pages)
- [[voice-objects]] — Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate.

### inert (3 pages)
- [[2026-07-26-voice-actions-research]] — Imported research notes on voice objects, point-and-speak, drop-to-target, the protocol trio, demonstration drafts, the risky ceremony, and why none of it can act by surprise.
- [[inert-foundations]] — The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution.
- [[voice-objects]] — Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate.

### insertion (2 pages)
- [[delayed-cleanup]] — Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt.
- [[insertion-transaction]] — Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox.

### install (1 pages)
- [[installers-and-services]] — One-click, idempotent installers provision dependencies, pinned models, the native helper, login services, and health checks; generated services are replaced while private user files always survive.

### installers (1 pages)
- [[2026-07-26-ops-governance-research]] — Imported research notes on installers, services, updates, packaging, the native helper, the site, the benchmark family, tests, and governance.

### interface (1 pages)
- [[2026-07-26-interface-rebuild-research]] — Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335.

### keywords (1 pages)
- [[acoustic-personalization]] — Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer.

### labs (1 pages)
- [[benchmarks]] — A family of offline, transcript-free labs whose shared rule is no runtime authority: evidence can reject changes and build activation receipts, but nothing in the runtime moves on a benchmark's say-so.

### launchd (1 pages)
- [[installers-and-services]] — One-click, idempotent installers provision dependencies, pinned models, the native helper, login services, and health checks; generated services are replaced while private user files always survive.

### learning (1 pages)
- [[personalization]] — Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate.

### licensing (1 pages)
- [[governance]] — AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass.

### llm (1 pages)
- [[cleanup-pipeline]] — Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits.

### local-first (1 pages)
- [[whisper-face]] — Local-first voice input for Mac: hold a key, speak, and trustworthy text appears at the cursor — no account, no cloud, no audio leaving the machine.

### macos (7 pages)
- [[app-window]] — The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector.
- [[delayed-cleanup]] — Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt.
- [[inert-foundations]] — The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution.
- [[insertion-transaction]] — Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox.
- [[menu-bar]] — Six choices by default and five rows that appear only when they have something to offer — the everyday control surface, with everything else moved into the window.
- [[point-and-speak]] — Say a target's name, preview the single confident match, then explicitly confirm one AXPress on a strongly named control — with nonces, leases, exact rechecks, and text fields excluded by construction.
- [[voice-objects]] — Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate.

### menu-bar (2 pages)
- [[2026-07-26-interface-rebuild-research]] — Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335.
- [[menu-bar]] — Six choices by default and five rows that appear only when they have something to offer — the everyday control surface, with everything else moved into the window.

### merge (1 pages)
- [[delayed-cleanup]] — Insert the deterministic result immediately, finish LLM cleanup in the background, and apply only safe merged edits after exact destination rechecks — gated by a physical-evidence activation receipt.

### models (1 pages)
- [[model-wallet]] — A provider-neutral routing policy over the four pinned models — deliberately not wired to live routing, with filesystem evidence capped below readiness and a fail-closed shadow advisory.

### modes (1 pages)
- [[voice-modes]] — Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use.

### motion (2 pages)
- [[2026-07-26-interface-rebuild-research]] — Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335.
- [[design-language]] — One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE.

### narrative (1 pages)
- [[synth-2026-07-26-what-happens-when-i-dictate]] — From key-down to pasted text: the warm capture, rolling recognition, evidence compilation, guarded cleanup, and the single transactional paste.

### onboarding (1 pages)
- [[app-window]] — The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector.

### operations (3 pages)
- [[2026-07-26-ops-governance-research]] — Imported research notes on installers, services, updates, packaging, the native helper, the site, the benchmark family, tests, and governance.
- [[distribution]] — Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest.
- [[installers-and-services]] — One-click, idempotent installers provision dependencies, pinned models, the native helper, login services, and health checks; generated services are replaced while private user files always survive.

### operator (1 pages)
- [[evidence-capture]] — Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent.

### outbox (1 pages)
- [[insertion-transaction]] — Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox.

### overview (2 pages)
- [[synth-2026-07-26-what-happens-when-i-dictate]] — From key-down to pasted text: the warm capture, rolling recognition, evidence compilation, guarded cleanup, and the single transactional paste.
- [[whisper-face]] — Local-first voice input for Mac: hold a key, speak, and trustworthy text appears at the cursor — no account, no cloud, no audio leaving the machine.

### packaging (1 pages)
- [[distribution]] — Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest.

### parakeet (1 pages)
- [[asr-cascade]] — The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold.

### parity (1 pages)
- [[windows-support]] — Windows shares the core capture, cascade, cleanup, snippets, tones, and learning pipeline with one-click installer parity — while the Cocoa HUD, Accessibility context, and several macOS-only trust features stay Mac.

### pattern (1 pages)
- [[activation-receipt]] — The house pattern for risky capabilities: features ship off and unlock only via a 0600 receipt this machine produced from manually reviewed physical evidence — with policy, model, and evidence pinning.

### performance (2 pages)
- [[asr-cascade]] — The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold.
- [[benchmarks]] — A family of offline, transcript-free labs whose shared rule is no runtime authority: evidence can reject changes and build activation receipts, but nothing in the runtime moves on a benchmark's say-so.

### personalization (1 pages)
- [[2026-07-26-trust-personalization-research]] — Imported research notes on the acoustic stores, activation receipts, regression lab, shadow gate, model wallet, and the shared fail-closed design language.

### pipeline (3 pages)
- [[2026-07-26-runtime-pipeline-research]] — Imported research notes mapping dictate.py, the pure core modules, the verifier stack, delayed cleanup, and the end-to-end utterance flow at commit b49699f.
- [[dictation-pipeline]] — The end-to-end path one utterance takes from key-down to pasted text, and the invariants that hold along it.
- [[synth-2026-07-26-what-happens-when-i-dictate]] — From key-down to pasted text: the warm capture, rolling recognition, evidence compilation, guarded cleanup, and the single transactional paste.

### platform (1 pages)
- [[windows-support]] — Windows shares the core capture, cascade, cleanup, snippets, tones, and learning pipeline with one-click installer parity — while the Cocoa HUD, Accessibility context, and several macOS-only trust features stay Mac.

### policy (1 pages)
- [[governance]] — AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass.

### priors (1 pages)
- [[personalization]] — Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate.

### privacy (2 pages)
- [[acoustic-personalization]] — Three bounded acoustic subsystems: a keyword evidence store with no recognition effect of its own, an offline calibration policy, and a RAM-only microspan replay buffer.
- [[privacy-and-security]] — No account, no upload, no sale, no shared-model training; RAM-only audio; content-free receipts everywhere; seven security invariants; and a support bundle that is a double allowlist.

### product (1 pages)
- [[whisper-face]] — Local-first voice input for Mac: hold a key, speak, and trustworthy text appears at the cursor — no account, no cloud, no audio leaving the machine.

### protocol (1 pages)
- [[inert-foundations]] — The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution.

### qwen (1 pages)
- [[cleanup-pipeline]] — Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits.

### receipts (1 pages)
- [[privacy-and-security]] — No account, no upload, no sale, no shared-model training; RAM-only audio; content-free receipts everywhere; seven security invariants; and a support bundle that is a double allowlist.

### recognition (1 pages)
- [[asr-cascade]] — The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold.

### regression (1 pages)
- [[personalization]] — Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate.

### release-gates (1 pages)
- [[governance]] — AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass.

### releases (2 pages)
- [[2026-07-26-evidence-capture-research]] — Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335.
- [[distribution]] — Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest.

### relisten (1 pages)
- [[consequence-receipts]] — Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text.

### research (6 pages)
- [[2026-07-26-evidence-capture-research]] — Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335.
- [[2026-07-26-interface-rebuild-research]] — Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335.
- [[2026-07-26-ops-governance-research]] — Imported research notes on installers, services, updates, packaging, the native helper, the site, the benchmark family, tests, and governance.
- [[2026-07-26-runtime-pipeline-research]] — Imported research notes mapping dictate.py, the pure core modules, the verifier stack, delayed cleanup, and the end-to-end utterance flow at commit b49699f.
- [[2026-07-26-trust-personalization-research]] — Imported research notes on the acoustic stores, activation receipts, regression lab, shadow gate, model wallet, and the shared fail-closed design language.
- [[2026-07-26-voice-actions-research]] — Imported research notes on voice objects, point-and-speak, drop-to-target, the protocol trio, demonstration drafts, the risky ceremony, and why none of it can act by surprise.

### risk (1 pages)
- [[consequence-receipts]] — Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text.

### routing (1 pages)
- [[model-wallet]] — A provider-neutral routing policy over the four pinned models — deliberately not wired to live routing, with filesystem evidence capped below readiness and a fail-closed shadow advisory.

### runtime (2 pages)
- [[2026-07-26-runtime-pipeline-research]] — Imported research notes mapping dictate.py, the pure core modules, the verifier stack, delayed cleanup, and the end-to-end utterance flow at commit b49699f.
- [[dictation-pipeline]] — The end-to-end path one utterance takes from key-down to pasted text, and the invariants that hold along it.

### safety (10 pages)
- [[activation-receipt]] — The house pattern for risky capabilities: features ship off and unlock only via a 0600 receipt this machine produced from manually reviewed physical evidence — with policy, model, and evidence pinning.
- [[cleanup-pipeline]] — Deterministic cleanup always; the local LLM only when words truly require semantic work — guarded, circuit-broken, and accepted only through validated proof edits.
- [[consequence-receipts]] — Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text.
- [[context-firewall]] — Every finalized, insertion-bound contextual compile is compared with a context-free shadow compile; protected influence is quarantined in a transcript-free receipt that can change nothing.
- [[evidence-capture]] — Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent.
- [[inert-foundations]] — The action substrate that cannot act: drop-to-target decisions, demonstration recipes, the two-factor risk ceremony, the versioned input protocol, and the sandboxed networkless worker — all deliberately unwired from execution.
- [[insertion-transaction]] — Final insertion is an exactly-once local transaction: lease the target at key-down, revalidate before one paste attempt, and route anything unproven to a recoverable RAM-only outbox.
- [[personalization]] — Corrections observed on the exact pasted range become scoped, inspectable, forgettable Personal Priors — but only after passing a private regression suite through a shared shadow gate.
- [[proof-edit]] — A bounded transformation with an exact source span, before/after text, kind, and validation outcome — capture-mode cleanup applies only validated proof edits.
- [[protected-anchor]] — Factual or code-shaped content — names, numbers, dates, URLs, paths, identifiers, commands — that cleanup must not delete or change without explicit spoken instruction.

### security (1 pages)
- [[privacy-and-security]] — No account, no upload, no sale, no shared-model training; RAM-only audio; content-free receipts everywhere; seven security invariants; and a support bundle that is a double allowlist.

### services (1 pages)
- [[installers-and-services]] — One-click, idempotent installers provision dependencies, pinned models, the native helper, login services, and health checks; generated services are replaced while private user files always survive.

### shadow (1 pages)
- [[context-firewall]] — Every finalized, insertion-bound contextual compile is compared with a context-free shadow compile; protected influence is quarantined in a transcript-free receipt that can change nothing.

### signing (1 pages)
- [[distribution]] — Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest.

### site (1 pages)
- [[marketing-site]] — A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the ten characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions.

### snippets (1 pages)
- [[voice-modes]] — Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use.

### span-graph (1 pages)
- [[voice-compiler]] — The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning.

### surfaces (2 pages)
- [[app-window]] — The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector.
- [[menu-bar]] — Six choices by default and five rows that appear only when they have something to offer — the everyday control surface, with everything else moved into the window.

### threat-model (1 pages)
- [[privacy-and-security]] — No account, no upload, no sale, no shared-model training; RAM-only audio; content-free receipts everywhere; seven security invariants; and a support bundle that is a double allowlist.

### tones (1 pages)
- [[voice-modes]] — Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use.

### transaction (1 pages)
- [[point-and-speak]] — Say a target's name, preview the single confident match, then explicitly confirm one AXPress on a strongly named control — with nonces, leases, exact rechecks, and text fields excluded by construction.

### trust (1 pages)
- [[2026-07-26-trust-personalization-research]] — Imported research notes on the acoustic stores, activation receipts, regression lab, shadow gate, model wallet, and the shared fail-closed design language.

### typography (1 pages)
- [[design-language]] — One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE.

### updates (1 pages)
- [[distribution]] — Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest.

### ux (3 pages)
- [[app-window]] — The native Mac window is three sections — Home, Settings (Personalize + Privacy), Advanced — with the whole trust surface behind one explicit evidence inspector.
- [[menu-bar]] — Six choices by default and five rows that appear only when they have something to offer — the everyday control surface, with everything else moved into the window.
- [[voice-modes]] — Modifier keys turn the same Right Option gesture into six explicit modes; per-app tones, self-editing snippets, spoken structure, and the Flight Recorder round out daily use.

### verification (1 pages)
- [[consequence-receipts]] — Names, numbers, dates, recipients, commands and other consequence-sensitive spans get transcript-free risk receipts, optionally verified by a process-isolated microspan re-listen — evidence that never changes the text.

### voiceir (1 pages)
- [[voice-compiler]] — The deep module that fuses recognition hypotheses, context, priors, and prosody into compiled text with protected anchors, proof edits, and a stable prefix — without inventing meaning.

### web (1 pages)
- [[marketing-site]] — A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the ten characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions.

### whisper (1 pages)
- [[asr-cascade]] — The three-engine recognition strategy: Whisper Tiny speculates, native Parakeet verifies, Whisper large-v3-turbo is the independent fallback — with rolling chunk decoding during the hold.

### windows (1 pages)
- [[windows-support]] — Windows shares the core capture, cascade, cleanup, snippets, tones, and learning pipeline with one-click installer parity — while the Cocoa HUD, Accessibility context, and several macOS-only trust features stay Mac.

## Orphan Pages
(none)

## Review Queue
(empty)
