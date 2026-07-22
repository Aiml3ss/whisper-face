# Whisper Face 1-65 execution ledger

This ledger turns the product roadmap into a completion audit. A row may move
to **complete** only when its behavior, automated coverage, installer impact,
and live-platform evidence are linked. `active` means a current implementation
wave owns it; `queued` means it remains in scope; `deferred-phone` preserves the
explicit Mac-first sequencing rather than dropping the item.

| # | Deliverable | State | Completion evidence required |
|---:|---|---|---|
| 1 | Signed/notarized Mac app, package, checksums, rollback, safe updater | active | Reproducible package; unsigned local path; signed CI release; notarization and update verification |
| 2 | Permission/model/hotkey/first-dictation onboarding | active | Native flow plus state and UI tests; fresh-Mac walkthrough |
| 3 | Confidence/Stable Prefix/processing/recovery HUD | complete | Native Overview states consume runtime capture/confidence/prefix/outbox evidence; 15 GUI tests and an AppKit construction/render pass cover all phases |
| 4 | Unified settings for modes, tones, snippets, vocabulary, corrections, privacy, diagnostics | active | Editable controls, persistence, and regression tests |
| 5 | Result inspector for alternatives, anchors, edits, context, timing | complete | Transcript-free runtime projection and Results UI expose counts, anchors, canonical edit categories, context influence, confidence, and timing with privacy regressions |
| 6 | VoiceOver, keyboard, contrast, reduced motion, localization readiness | active | Accessibility metadata, keyboard QA, and reduced-motion behavior |
| 7 | Clear non-blocking degraded-mode UX | complete | Blocking microphone/permission/service failures route to Action Needed while a failed fallback remains a non-blocking warning; GUI and AudioPool failure tests cover both |
| 8 | Competitor task-based UX evaluation | queued | Reproducible task protocol and published measured results |
| 9 | Representative speech/semantic-safety benchmark corpus | complete | `benchmarks/representative_dictation_cases.json` has 19 synthetic cases covering every declared risk dimension; acoustic labels remain explicit physical-recording requirements |
| 10 | Correction Burden, zero-edit, routing, and latency dashboard | complete | Transcript-free `performance_lab.py evaluate` reports p50/p95/p99, burden, zero-edit, routing, coverage, and delivery with strict schema tests |
| 11 | Apple Silicon and Windows hardware matrix | queued | Reproducible runs on representative real hardware |
| 12 | Scheduled model scorecard and reevaluation cadence | active | Machine-readable scorecard, generator, and scheduled workflow |
| 13 | Non-flaky warm-path latency budgets in CI | complete | Versioned minimum-sample/p95 compiler budget runs on Windows CI; physical latency claims are explicitly excluded |
| 14 | End-to-end component prewarming/cold-start elimination | queued | Startup trace and warm/cold budgets for every hot-path component |
| 15 | Automatic gain/noise/reverb/VAD/end-of-speech calibration | queued | Calibrator, audio fixtures, and device validation |
| 16 | Long/back-to-back/sleep-wake/device-switch/restart stress coverage | active | Automated harness plus live Mac evidence |
| 17 | Explainable Last Recognition disagreements and rejections | queued | Decision receipt rendered in menu and window |
| 18 | Selective Re-listen of uncertain spans | queued | Timed-span rerun path with accuracy/latency comparison |
| 19 | Specialized ASR/error verifier comparison | queued | Local verifier and benchmark against general cleanup |
| 20 | Consequence-aware risk routing | queued | Risk policy, protected categories, tests, latency bounds |
| 21 | Acoustic Time Machine microspan replay | queued | Opt-in bounded audio buffer, UI, deletion proof, tests |
| 22 | Inspectable personal acoustic keyword memory | queued | Local storage, false-insertion threshold, export/forget |
| 23 | Counterfactual Context Firewall | queued | With/without-context comparison for risky spans |
| 24 | Context influence receipts and shadow personalization/model evaluation | queued | Receipt UI, shadow runner, quarantine/promotion tests |
| 25 | Delayed cleanup merges only untouched VoiceIR spans | queued | Concurrent-edit merge algorithm and adversarial tests |
| 26 | Public 50-app insertion/capability corpus | queued | Versioned app matrix and reproducible protocol |
| 27 | Adversarial focus/duplicate/typing/relaunch/clipboard/delay tests | queued | Automated and manual corpus covering every failure stage |
| 28 | Four-nines exactly-once insertion measurement | queued | Long-run fault-injection report meeting target |
| 29 | Privacy-safe compatibility fingerprint network | queued | Text-free schema, opt-in transport, threat model |
| 30 | Voice Input Protocol and initial adapters | queued | Versioned protocol, conformance tests, five adapters |
| 31 | Networkless sandboxed XPC speech worker | queued | Narrow IPC, sandbox profile, network-denial proof |
| 32 | Provider-neutral model wallet | queued | Provider interface, local providers, failover, compiler independence |
| 33 | Personal Regression cardinality-boundary tests | complete | `tests/test_personal_regression.py` covers deterministic `MAX_CASES + 1`, `MAX_MAPPINGS + 1`, and `MAX_QUARANTINED + 1` eviction |
| 34 | Full installed-Windows verification | queued | Real Windows `setup.ps1 --verify` and first dictation |
| 35 | Point-and-Speak | queued | >95% target resolution and zero wrong-target writes in test corpus |
| 36 | Drop-to-Target | queued | Prototype plus measured kill/continue decision |
| 37 | Voice Objects projected across destinations | queued | Typed object and contradiction-safe projections |
| 38 | Agent Voice Inbox | queued | Provenance-preserving queue and local-agent adapters |
| 39 | Voice-plus-click confirmation for risky agent actions | queued | Risk policy, explicit confirmation, audit receipt |
| 40 | Teach-by-demonstration workflows | queued | Finder/Mail/Notes/menu vertical slices with preview/rollback |
| 41 | Risk sonification/haptic feedback | queued | Isolated prototype and usability/error-rate evidence |
| 42 | Privacy promise, threat model, security policy, disclosure route | complete | `PRIVACY.md`, `SECURITY.md`, and threat model document current local/LAN/release boundaries; GitHub private vulnerability reporting is enabled and tested live |
| 43 | Trademark clearance and optional patent/prior-art decision | queued-external | Owner/counsel decision recorded without claiming legal advice |
| 44 | Public reproducible benchmarks and model scorecard | active | CI artifacts and published methodology/results |
| 45 | Architecture/interoperability/SDK/contributor/upgrade/rollback docs | active | Versioned docs tested against release paths |
| 46 | Reproducible demonstration corpora | complete | Public synthetic corpus, deterministic compiler stress replay, privacy-safe observation schema, CLI instructions, and CI regressions are committed |
| 47 | Honest competitor comparison pages | queued | Dated, sourced, reproducible task results |
| 48 | Independently assignable GitHub implementation issues | complete | Vertical-slice issues [#6](https://github.com/Aiml3ss/whispering-parrot/issues/6) through [#19](https://github.com/Aiml3ss/whispering-parrot/issues/19) link the roadmap to acceptance criteria and dependencies |
| 49 | Sponsors and voluntary Supporter experiment | queued | Public tiers/benefits with no core feature lock |
| 50 | Concierge setup/migration offering | queued | Documented scope, privacy process, fulfillment path |
| 51 | Pricing/willingness-to-pay experiment | queued | Landing/waitlist experiment before cloud build |
| 52 | Optional E2EE preference/vocabulary/correction/recovery sync | queued | Threat model, protocol, clients, recovery, delete/export |
| 53 | Away-from-home encrypted relay to owned Mac | queued | Ciphertext-only fallback relay and direct-path preference |
| 54 | Team vocabulary/deployment/policy/diagnostics/roles/updates | queued | Self-hosted vertical slice with transcript-free metrics |
| 55 | Enterprise SSO/SCIM/compliance foundations | queued | Demand gate followed by auditable controls and documents |
| 56 | OEM, funded-open-work, Accuracy Bond, certification experiments | queued | One measured pilot before scaling each model |
| 57 | iPhone physical-device capture/keyboard feasibility | deferred-phone | Host/keyboard prototype on supported physical devices |
| 58 | Standalone offline Whisper Face Pocket | deferred-phone | Account-free offline first dictation and recovery |
| 59 | Stable-Prefix voice keyboard with exactly-once insertion | deferred-phone | Marked-text keyboard and lifecycle fault tests |
| 60 | Mobile Voice Compiler and adaptive acoustic front end | deferred-phone | On-device compiler, routes, energy/thermal benchmarks |
| 61 | Durable mobile Dictation Ledger and Voice Outbox | deferred-phone | Crash-safe proposals/commit/acknowledgement fault tests |
| 62 | Encrypted one-scan Mac pairing/Nearby Compute Auction | deferred-phone | Secure pairing, selective cooperation, automatic fallback |
| 63 | Transcript-free correction sync and expiring Context Capsules | deferred-phone | E2EE compact state, provenance, expiry, export/forget |
| 64 | iPhone lifecycle/thermal/battery/accessibility/App Review torture suite | deferred-phone | Physical-device matrix and review/privacy preflight |
| 65 | iPhone control mesh, Watch, AirPods, Siri, Shortcut experiments | deferred-phone | One state machine and measured keep/kill decisions |

## Release-wave rule

Each wave must update this ledger and the GitHub roadmap, audit both installers,
run the repository release gates, report live-platform limitations honestly,
and merge through protected `main`. External credentials or physical hardware
may delay final evidence, but do not remove the deliverable from scope.
