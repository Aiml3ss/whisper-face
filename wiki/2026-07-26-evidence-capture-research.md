---
title: "Evidence Capture Research Notes"
type: article
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [research, evidence, activation, gates, releases]
summary: "Imported research notes on the four capture harnesses, the two circular-gate defects filed as issues #108 and #110, and the first three public releases — verified at commit 1165335."
source_hash: "687ab3fe5b3ea5b5f57be752ef7b6428adc4ab7e016791b97076b7a66d292a15"
status: published
---

# Evidence Capture Research Notes

## Summary

The other half of 2026-07-26: guided, resumable harnesses that record
real sessions and hand their output to the existing evaluators
(#107, #109); the two circular activation gates those harnesses exposed
before a single case was recorded (issues #108 and #110); and the first
three tagged releases on a public repository.

## Content

The full brief lives at `.raw/evidence-capture-research.md`. Compiled
into: [[evidence-capture]], and updates to [[activation-receipt]],
[[delayed-cleanup]], [[acoustic-personalization]], [[benchmarks]],
[[distribution]], [[governance]], [[privacy-and-security]],
[[consequence-receipts]], [[insertion-transaction]] and
[[whisper-face]].

## Key Takeaways

- The harnesses are structurally incapable of approving anything: tests
  parse their own subject's AST and assert no activation import, no
  process execution, no synthetic audio, and no declaration of the
  approval flags.
- Nothing is defaulted. A case the runtime did not report, reported
  ambiguously, or reported without an integrity receipt is *blocked*
  with a closed reason instead of scored.
- Three of the four activation gates are currently unearnable: the
  calibration and keyword A/Bs cannot produce their candidate arm without
  the receipt that arm is meant to justify (#108), and delayed cleanup
  adds a bootstrap deadlock, a missing `apply_ms` source, and a
  physically unreachable scenario (#110).
- Building the tools was itself the audit: both issues were filed before
  any operator time was spent.
- Three releases (v0.1.0, v0.2.0, v0.2.1) are published from a public
  repository, the site reads a single release constant, and the build is
  honestly labelled not yet notarized.

## Related

- [[whisper-face]] — the hub
- [[activation-receipt]] — the pattern these harnesses feed
- [[2026-07-26-interface-rebuild-research]] — the same day's other half
