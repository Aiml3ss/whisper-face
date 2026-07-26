---
title: "Interface Rebuild Research Notes"
type: article
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [research, interface, gui, menu-bar, motion]
summary: "Imported research notes on the menu-bar simplification, the Home/Settings/Advanced window, the design-language rebuild, first run, and site/app motion parity — verified at commit 1165335."
source_hash: "db3392d4430fa92e865e501d4b38273a5c6be412eb64f1b2afa1f844f6aef7f6"
status: published
---

# Interface Rebuild Research Notes

## Summary

The delta between the app the wiki was built from (`b49699f`, morning of
2026-07-26) and the app as it shipped that evening (`1165335`). Five
changes: the menu bar became a quick-glance surface (#101), the window
collapsed from five sections to three (#104), the window was rebuilt
around the shared design language (#105), first run and every state were
rewritten in the product's voice (#112), and the site adopted the app's
springs (#111).

## Content

The full brief lives at `.raw/interface-rebuild-research.md`. Compiled
into: [[menu-bar]], [[app-window]], [[design-language]], and updates to
[[whisper-faces]], [[voice-modes]], [[personalization]],
[[model-wallet]], [[inert-foundations]], [[point-and-speak]],
[[voice-objects]], [[insertion-transaction]], [[consequence-receipts]],
[[marketing-site]], [[windows-support]], [[dictation-pipeline]] and
[[whisper-face]].

## Key Takeaways

- Nothing was deleted from the product, only relocated: every menu row
  that disappeared has a home in the window, reachable through the
  menu's first item.
- Four experimental surfaces left the *window* while their runtime
  modules, view-model passthroughs, `GUIActions` fields and tests all
  stayed — a removal of chrome, not of capability.
- The trust surface survived the Results page: `result_evidence_text`
  gained a `result` parameter so the evidence inspector now carries the
  whole thing.
- One shared `MOTION_SPECS` table now drives Core Animation springs in
  the window and the HUD *and* baked CSS keyframes on the site, because
  Jelly UI exposes no physics API to share instead.
- Two live bugs were fixed on the way: a missing import made every amber
  CTA fall back to AppKit's default title colour, and AppKit's `(0, 0)`
  layer anchor made every spring scale from the bottom-left corner.

## Related

- [[whisper-face]] — the hub
- [[design-language]] — the vocabulary the rebuild is written in
- [[2026-07-26-evidence-capture-research]] — the same day's other half
