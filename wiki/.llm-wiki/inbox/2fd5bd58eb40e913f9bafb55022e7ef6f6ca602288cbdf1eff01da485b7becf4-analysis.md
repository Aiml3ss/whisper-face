# Ingest Analysis — .raw/ research briefs (whole-app wiki build)

**Source hash:** `2fd5bd58eb40e913f9bafb55022e7ef6f6ca602288cbdf1eff01da485b7becf4`
**Language detected:** en
**Analyzed:** 2026-07-26

## Source Summary

Four research briefs distilled from the full Whisper Face codebase at
commit `b49699f`, covering (1) the dictation runtime pipeline, (2) the
personalization/evidence/trust subsystems, (3) the voice-action
foundations, and (4) operations, distribution, site, and governance.
Together they describe the entire application and are the source set for
the initial whole-app wiki.

## Concepts to Extract

| Concept | Action | Reason |
|---------|--------|--------|
| whisper-face | create | Hub page for the product itself |
| dictation-pipeline | create | End-to-end utterance flow + invariants |
| asr-cascade | create | Tiny/Parakeet/Turbo, rolling recognition, speculation |
| voice-compiler | create | VoiceIR, span fusion, prosody, decisions |
| protected-anchor | create | Core safety term (CONTEXT.md glossary) |
| proof-edit | create | Core safety term |
| stable-prefix | create | Core feedback term + semantic commit |
| cleanup-pipeline | create | Deterministic + LLM cleanup, breaker, guards |
| delayed-cleanup | create | Insert-now/clean-later merge machinery |
| insertion-transaction | create | Lease, exactly-once commit, readback, outbox |
| voice-modes | create | Modes, tones, snippets, spoken structure, Flight Recorder |
| consequence-receipts | create | Risk taxonomy + selective re-listen + verifier stack |
| context-firewall | create | Counterfactual shadow comparison |
| personalization | create | Corrections → priors → regression lab → shadow gate |
| activation-receipt | create | The evidence-gating pattern shared by four features |
| acoustic-personalization | create | Keyword memory, calibration, time machine |
| model-wallet | create | Provider policy + readiness evidence + shadow advisory |
| whisper-faces | create | The ten characters, art pipeline, HUD animation |
| voice-objects | create | Objects, inbox, parser, bridge, compose/clipboard |
| point-and-speak | create | Resolver + nonce transaction + AX snapshot |
| inert-foundations | create | Drop-to-target, demonstrations, risky ceremony, protocol trio, networkless worker |
| installers-and-services | create | Installers, services, launcher app, health endpoint |
| distribution | create | Self-update, side-by-side, packaging, signing, manifest |
| benchmarks | create | Lab family + no-runtime-authority philosophy |
| windows-support | create | Platform parity and differences |
| governance | create | Licensing, CLA ledger, CODEOWNERS, CI, release gates |
| privacy-and-security | create | Privacy promise, threat model, support bundle |
| marketing-site | create | Astro site, faces on the web, deploy |

## Persons to Create/Update

None — the sources describe software, not people central to the content.

## Pages to Create

| Filename | Type | Title |
|----------|------|-------|
| 2026-07-26-runtime-pipeline-research.md | article | Runtime pipeline research notes |
| 2026-07-26-trust-personalization-research.md | article | Trust & personalization research notes |
| 2026-07-26-voice-actions-research.md | article | Voice-action foundations research notes |
| 2026-07-26-ops-governance-research.md | article | Ops & governance research notes |
| synth-2026-07-26-what-happens-when-i-dictate.md | synthesis | End-to-end answer to the most common question |
| (28 concept pages listed above) | concept | — |

## Contradictions Detected

None between the four sources. Two documentation-vs-code gaps worth
recording on the relevant pages (not contradictions between wiki pages):

- SpanGraph is a documented concept with no class; implemented as
  `VoiceCompiler._fuse` (noted on [[voice-compiler]]).
- The consequence-routing benchmark's route set omits `verified`, which
  the compiler can emit (noted on [[consequence-receipts]] and
  [[benchmarks]]).

## Proposed Cross-Links

- [[whisper-face]] hubs to every subsystem page.
- [[dictation-pipeline]] ↔ [[asr-cascade]] ↔ [[voice-compiler]] ↔
  [[cleanup-pipeline]] ↔ [[insertion-transaction]] — the utterance path.
- [[protected-anchor]] / [[proof-edit]] / [[stable-prefix]] ↔
  [[voice-compiler]] — glossary terms to their implementation.
- [[personalization]] ↔ [[activation-receipt]] ↔
  [[acoustic-personalization]] — the trust chain.
- [[voice-objects]] ↔ [[inert-foundations]] ↔ [[point-and-speak]] —
  the action layer.
- [[installers-and-services]] ↔ [[distribution]] ↔ [[governance]] —
  the ops chain.

## Items for User Review

- [x] Owner approved a full-app build on 2026-07-26 ("make a wiki of the
  entire app"); `require_review` set to false for this ingest.
