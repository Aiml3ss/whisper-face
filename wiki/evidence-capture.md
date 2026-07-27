---
title: "Evidence Capture"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
tags: [evidence, activation, harness, operator, safety]
aliases: [capture-harness, capture-voice-evidence, capture-app-matrix, physical-sessions, voice-corpora]
summary: "Four guided, resumable terminal sessions that record real physical evidence and hand it to the existing evaluators — structurally incapable of approving anything, and blocking rather than guessing when the runtime stays silent."
confidence: high
---

# Evidence Capture

## Definition

The [[activation-receipt]] pattern always had evaluators; what it lacked
was a humane way to produce what they eat — dozens of 16 kHz WAVs with
hand-authored manifests, or fifty app-by-app insertion observations. Four
scripts added on 2026-07-26 (#107, #109) run those campaigns as guided,
resumable terminal sessions. They record; they never approve. Everything
they write lands in the gitignored, owner-only `.evidence/`.

## The four harnesses

- **`scripts/capture_voice_evidence.py`** runs three corpora as guided
  sessions: selective re-listen (ledger 18/19), acoustic calibration
  (ledger 15) and pronunciation keyword bias (ledger 22). It records live
  at exactly 16 kHz mono — enforced at device open, stream creation, WAV
  write and readback — draws a 28-cell level meter flagging clipping and
  very-quiet takes, writes 0600 WAVs and a manifest atomically, tracks
  balance in-session, and caps re-listen takes at the verifier adapter's
  own 2.4 s microspan bound so a corpus cannot be rejected after the
  operator's time is spent ([[consequence-receipts]]). Calibration and
  keywords run as two passes over one task set, so settings change once
  per pass rather than once per case. Resuming never overwrites a
  completed case; `--redo` reopens one deliberately.
- **`scripts/capture_app_matrix.py`** walks a curated fifty-app plan
  across ten surface classes (native-cocoa, electron-chromium, browser,
  web-text-area, terminal, ide, office, messaging, notes, mail), reads
  the runtime's own insertion receipt from the transcript-free keys of
  `transcripts.jsonl` ([[insertion-transaction]]), and asks the operator
  the one thing the machine cannot know — did the intended text land in
  the intended place — as closed choices. Coverage is reported as
  measured: `extrapolated` is hardcoded false and `four_nines_claim` is
  unconditionally false.
- **`scripts/capture_delayed_cleanup_cases.py`** walks the four-surface
  by four-scenario grid the [[delayed-cleanup]] gate demands (50 cases,
  13/13/12/12 per surface and per scenario, a 25/25 applied/rejected
  split), importing the gate's own vocabulary and thresholds so it
  cannot drift, reads the runtime's `[delayed-cleanup]` line — including
  its apply duration and measurement-mode marker — asks four closed
  safety questions per case, and prints the activation command only when
  no shortfall remains — "This tool does not run it, does not write the
  receipt, and does not set --manual-reviewed on your behalf."
- **`scripts/capture_lifecycle_evidence.py`** guides exactly the five
  scenarios `performance_lab.run_lifecycle_simulation` names — long-form,
  back-to-back, process-restart, sleep-wake, audio-device-switch — and
  reports which of its three `requires_physical_validation` ids a session
  discharges and which still stand ([[benchmarks]]).

`scripts/capture_session_support.py` is the shared, import-only plumbing
for the last three: atomic 0600 JSON, a resumable session that refuses to
replace an answered case or to load another tool's file, closed-choice
prompts, the strict transcript projection, and a vocabulary-integrity
assertion that runs at *import* time so drift fails all three tools at
once.

## Key Properties

- **No authority, enforced structurally.** Tests parse each script's own
  AST and assert it imports no activation module (a nine-name
  forbidden set), executes no other process (a twenty-name forbidden call
  set), generates no synthetic audio (thirteen signal generators banned;
  `record_take` must call `sounddevice.InputStream`), and cannot even
  *declare* `--confirm-manual-review` or `--approve-runtime` — every
  occurrence of those strings must be inside a string literal. The three
  session tools additionally assert `manual_reviewed`,
  `activation_receipt` and `write_receipt` never appear as a bound name,
  keyword, argument or function name in either the script or the shared
  library. *Precision*: two further properties — "never writes a receipt"
  and "never infers an outcome label" — are name and constant checks
  rather than AST walks, backed by behavioural tests.
- **Authority stays with the operator.** The capture tools print the
  exact benchmark invocation and stop. `--confirm-manual-review` is
  declared only by the three activation benchmarks; `--review` is a
  capture flag that replays every completed take next to its designed
  label and announces "Nothing is approved here; this is only playback."
- **Nothing is defaulted.** A case the runtime did not report, reported
  ambiguously, or reported without an integrity receipt is recorded as
  *blocked* with a reason from a closed set — `no-runtime-record`,
  `no-runtime-line`, `no-runtime-timing`, `runtime-reported-no-receipt`,
  `delayed-cleanup-inactive`, `operator-could-not-judge` and siblings.
  The runtime's no-receipt sentinels sit deliberately outside the receipt
  enums, so a case reporting them cannot be evidence of anything.
- **No dictated words reach an artifact** in the three session tools:
  every answer is a listed key, the fixed practice phrases live in source
  and only their ids are written, and poison-string tests prove raw,
  clean and tone text never survive into an artifact.
  (`capture_voice_evidence.py` predates the shared library: its prompts
  are closed too, but `--keyword` / `--near-miss` are free-text arguments
  that reach the keyword manifest, and its corpus *is* recorded speech by
  design.)
- **Private by construction.** `.evidence/` is the only ignore rule these
  commits added, and the voice tool parses the real `.gitignore` and
  refuses any session directory inside a checkout that no entry covers.

## Two issues the build surfaced, and their fix

Building the tools was the audit. Issues #108 and #110 were filed before
any operator time was spent: three of the four gates demanded evidence
whose candidate arm the runtime only produced after the receipt that
evidence would authorize, and delayed cleanup added an unmeasured
`apply_ms` and a physically unreachable `duplicate-callback` scenario.
#118 closed both on 2026-07-27:

- A session-scoped `--measure` override (`measurement_mode.py`) applies
  the real candidate path — calibrated front end, biased prompt, or the
  delayed-cleanup transaction — with no receipt and no authority. Every
  artifact recorded under it is labelled, and the validators carry the
  label into the receipt ([[activation-receipt]]).
- The runtime now times the transactional apply and prints it on the
  `[delayed-cleanup]` line this tool parses.
- `duplicate-callback` left the gate's scenario set rather than being
  made reachable by synthetic injection; the plan is now a 4×4 grid
  ([[delayed-cleanup]]).

Selective re-listen never had the circularity, which is why that corpus
was straightforwardly recordable all along. None of the physical
sessions has been run yet, so every gated feature still ships off.

## Documentation

`docs/evidence/` holds two operator guides: `voice-corpora.md` (the three
corpora with realistic time budgets, the `.evidence/` privacy model, the
review-then-approve three-step, and why receipts do not travel between
machines) and `physical-sessions.md` (the three sessions plus four
checklists for what no script can drive: fresh-Mac onboarding, the
hardware matrix, live Windows verification, and the six-task competitor
run).

## Related Concepts

- [[activation-receipt]] — what the evidence is for, and who may write it
- [[benchmarks]] — the evaluators the harnesses stop in front of
- [[delayed-cleanup]], [[acoustic-personalization]] — the gates the
  sessions feed
- [[privacy-and-security]] — 0600, gitignored, content-free artifacts

## References

- scripts/capture_voice_evidence.py, capture_app_matrix.py,
  capture_delayed_cleanup_cases.py, capture_lifecycle_evidence.py,
  capture_session_support.py; tests/test_capture_*.py;
  docs/evidence/voice-corpora.md, docs/evidence/physical-sessions.md
- [[2026-07-26-evidence-capture-research]]
