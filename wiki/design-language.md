---
title: "Design Language"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [design, motion, typography, color, accessibility]
aliases: [motion-specs, jelly-springs, whisper-face-theme, type-scale]
summary: "One platform-independent theme module names the palettes, surfaces and four springs that the Mac window, the HUD and the website all render — the app through Core Animation, the site through a baked integration of the same ODE."
confidence: high
---

# Design Language

## Definition

`whisper_face_theme.py` (216 lines) is deliberately platform-independent
so colour, motion, type and accessibility contracts can be tested without
loading AppKit. It names the light and dark palettes, the ten face chip
colours, three HUD type tokens, four `SURFACE_SPECS` treatments (work,
card, playful, control) and four `MOTION_SPECS` springs. Native views
translate the specs into Core Animation; the [[marketing-site]] bakes the
same numbers into CSS.

## The four springs

`MOTION_SPECS` — fields `mass, stiffness, damping, initial_velocity,
duration, squash_x, squash_y`:

| motion | mass | stiffness | damping | v₀ | duration | squash |
|---------|------|-----------|---------|-----|----------|--------|
| press | 1.0 | 420 | 28 | 0.0 | 0.24 s | 1.05 / 0.94 |
| release | 1.0 | 360 | 22 | 0.5 | 0.34 s | 0.96 / 1.05 |
| wobble | 1.0 | 330 | 16 | 0.8 | 0.46 s | 1.07 / 0.93 |
| pop | 1.0 | 390 | 20 | 0.4 | 0.38 s | 0.90 / 0.90 |

In the app, `add_jelly_motion` looks the motion up and attaches two
`CASpringAnimation`s on `transform.scale.x` / `.y`. Its first line is the
Reduce Motion gate: it returns `False` without touching the view, "so
callers can verify the gate". The flag is
`NSWorkspace…accessibilityDisplayShouldReduceMotion()`, mirrored into a
module global on every render so every jelly control reads it at press
time.

> ⚠️ **Known gap**: no
> `NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification` observer
> exists. Reduce Motion is polled through `runtime_status_snapshot()` on
> the refresh timer, so a mid-session toggle takes effect at the next
> refresh rather than immediately.

**The anchor-point bug (fixed #112).** AppKit hands view-backed layers an
anchor of `(0, 0)`, so every scale spring was growing out of the
bottom-left corner and the squash read as a slide. `center_layer_anchor`
re-anchors to `(0.5, 0.5)` and shifts the position by exactly the
distance the anchor moved, leaving layer frames unchanged. A
byte-identical twin fixes the HUD and menu-bar port in `dictate.py`, so
the HUD pop and the [[menu-bar]] mouth wobble squash around their centres
too.

## Type, grid and colour in the window

22pt semibold rounded page titles, 17pt rounded headings, 13pt body, 11pt
captions — the old 8/9/10pt strays are gone — plus a 30pt bold rounded
onboarding poster title. 44pt control rows, 36pt dense model rows, a
720pt content column, 24pt above it. *Two honest caveats about the "8pt
grid" summary*: the 24pt inset is the column's **top** only (horizontally
the column is centred with a 12pt minimum leading), and card gaps are
16pt on Home and Personalize but 12 / 6 / 2 on Advanced.

The app never calls `setAppearance_` — it reads the system's
`effectiveAppearance` and resolves palettes through
`palette_for_appearance`, repainting every card, hairline, ink role, CTA
fill, chip and rail row on each render. Because the raw emerald and amber
fail WCAG AA as text on light surfaces, text-bearing uses swap in
darkened inks: `BRAND_TEXT_ON_LIGHT ≈ #0B7A57` (5.3:1 on white) and
`AMBER_TEXT_ON_LIGHT ≈ #7A4F00` (6.1:1). Dark mode gets more than a
palette swap — different rail alphas, stronger pill fills, and a neutral
ink wash on the current onboarding chip instead of amber-over-pine, which
used to read olive.

**The amber CTA bug (fixed #112).** `_set_cta_title` sets an attributed
title in SF Rounded with `LIGHT_PALETTE.ink` on the amber fill. At the
previous revision `LIGHT_PALETTE` was *used* but never imported, so a
`NameError` was raised inside the `try`, swallowed by `except Exception`,
and `setAttributedTitle_` never ran — every amber CTA kept AppKit's
default title colour, which in dark mode is white on amber. Adding the
import restored the intended dark ink. (The commonly quoted "≈1.9:1" and
`controlTextColor` come from the fix's commit message; neither appears in
any revision of the code.)

## Chrome motion, and what it never touches

Section switches crossfade at 150 ms ease-out and pop the newly selected
sidebar row; a fresh notice fades in; sidebar rows hover through an
`NSTrackingArea` with `NSTrackingInVisibleRect`, repainting only the row
whose state flipped. All of it is Reduce-Motion gated and all of it is
window chrome — the code comment is explicit that none of it touches the
dictation hot path ([[dictation-pipeline]]).

## The site runs the same numbers

`site/src/data/motion.ts` declares the same four names and carries all 28
constants identically, then solves the same second-order ODE
(`m·x'' + c·x' + k·x = 0`, all three damping regimes) that Core Animation
integrates and bakes the solution into `@keyframes wf-{name}` at one
frame per 10 ms. The animations drive the standalone `scale` property so
the sticker push and face tilts keep `transform` free.

The reason parity is implemented this way rather than by adopting the
Jelly UI library: **Jelly exposes no physics API** — its springs are
baked per component and its soft body is painted into a canvas in its
shadow root — so there is nothing to share, and the Mac app could not use
it in any case. That finding is recorded in the tree, in
`THIRD_PARTY_NOTICES.md` and in `motion.ts` itself.

## Related Concepts

- [[app-window]] — the surface this rebuilt
- [[whisper-faces]] — the characters that carry the personality
- [[marketing-site]] — the other renderer of the same springs
- [[privacy-and-security]] — accessibility and honesty as constraints

## References

- whisper_face_theme.py (`MOTION_SPECS` :105-110, `SURFACE_SPECS`
  :124-129, palettes :38-60); whisper_face_gui.py (`add_jelly_motion`
  :4446, `center_layer_anchor` :4418, `_set_cta_title` :4823, AA inks
  :4332-4336); dictate.py `_center_layer_anchor` (:2093-2117);
  site/src/data/motion.ts
- [[2026-07-26-interface-rebuild-research]]
