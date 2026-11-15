---
title: "Design Language"
type: concept
language: en
created: 2026-07-26
modified: 2026-11-15
tags: [design, motion, typography, color, accessibility]
aliases: [motion-specs, jelly-springs, whisper-face-theme, type-scale]
summary: "One platform-independent theme module names the palettes, the window's colour tokens, type roles and brand shapes, and the four springs that the Mac window, the recording pill and the website all run — the app through Core Animation, the site through a baked integration of the same ODE."
confidence: high
---

# Design Language

## Definition

`whisper_face_theme.py` (618 lines) is deliberately platform-independent
so colour, motion, type and accessibility contracts can be tested without
loading AppKit. It names the light and dark palettes, the sixteen face chip
colours, the type roles (three for the recording pill, the rest for the
window), four `SURFACE_SPECS` treatments (work, card, playful, control),
four `MOTION_SPECS` springs, and the vocabulary of the window's brand
objects: named colour tokens and the geometry of every ink-outlined shape.
Native views translate the specs into AppKit drawing and Core Animation;
the [[marketing-site]] bakes the same spring numbers into CSS.

The look is **native structure, loud brand moments**. Everything a Mac
user operates — the sidebar, rows, switches, pop-ups, segmented controls,
push buttons, text fields — is a system control. Everything that carries
the brand — the stage, the speech bubble, the primary action, the stat
stickers, key caps, the selected face chip, the recording pill — is
custom, with an ink outline and a hard shadow. The palettes are the
pastel set the chibi-clay rebuild (#120) introduced, kept as they were;
the four springs and their constants have not changed since they were
named.

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

`TYPE_SPECS` gives every window role a size, weight and voice. SF Pro
Rounded is the brand's voice and is used only where the brand speaks:
17pt bold page titles, 13pt bold section headers, the 27pt heavy bubble
headline, 25pt heavy stat numbers with tabular figures, 14pt heavy chunky
buttons (13pt for the small ones) and 12.5pt bold key caps. Rows stay on
SF Pro: 13pt semibold titles and 12pt captions in the secondary colour.

The grid: a 200pt sidebar with 32pt rows; a 52pt title strip; a content
column of up to 720pt (632pt at the window minimum) with a 24pt inset;
rows at least 44pt tall, 50pt with a caption, their text and hairlines
inset 14pt; 7pt from a section header to its group and 18pt from one
group to the next header.

`BRAND_SHAPES` holds the brand objects' geometry: the rounded group of
rows (radius 13, 1pt hairline), the stage (radius 24, 2.5pt outline, hard
shadow 5 right and 6 down), the bubble (22, 2.5, shadow 4/5), the chunky
button (36pt tall, radius 12, 2pt outline, shadow 3/4; 30pt with a 2/3
shadow when small), the stat sticker (16, 2, shadow 3/4, tilted −1.4, +1.0
and −0.6 degrees), the key cap (7, 1.5, shadow 0/2) and the face chip. A
hard shadow has no blur and is drawn in the ink outline colour. Pressed,
a brand object travels onto its shadow; disabled, it fades and loses it.

The app leaves the window's appearance to the system — it reads the
system's `effectiveAppearance` and resolves palettes through
`palette_for_appearance` on each render, and the custom views redraw
themselves when their appearance changes. Every custom colour is a named
token in `COLOR_TOKENS` with a light and a dark value, plus an Increase
Contrast value wherever the colour is an edge or carries text: the ink
outline turns light in the dark appearance (a hard shadow stays dark, so
it is never lighter than the surface it falls on), the hairline rises to
3:1, the switch green darkens, and the brand ink moves further from
its background in both appearances. The stage is amber or emerald,
chosen per face for contrast with it (`STAGE_FOR_FACE`); the stickers
wear three tints.
Because the raw emerald and amber fail WCAG AA as text on light surfaces,
text-bearing uses swap in darkened inks: `BRAND_TEXT_ON_LIGHT ≈ #0B7A57`
(5.3:1 on white) and `AMBER_TEXT_ON_LIGHT ≈ #7A4F00` (6.1:1). The window
draws all of these as sRGB, so the colour on screen is the hex the
palette names and the contrast the tests measure; the recording pill and
the live faces do the same.

**The amber CTA bug (fixed #112).** `_set_cta_title` set an attributed
title in SF Rounded with `LIGHT_PALETTE.ink` on the amber fill. At the
previous revision `LIGHT_PALETTE` was *used* but never imported, so a
`NameError` was raised inside the `try`, swallowed by `except Exception`,
and `setAttributedTitle_` never ran — every amber CTA kept AppKit's
default title colour, which in dark mode is white on amber. Adding the
import restored the intended dark ink. (The commonly quoted "≈1.9:1" and
`controlTextColor` come from the fix's commit message; neither appears in
any revision of the code.) The amber button that carried this fix was
replaced in the November 2026 new look by the chunky button, which
draws its own title in the dark ink on amber.

## Chrome motion, and what it never touches

Page switches crossfade at 150 ms ease-out, and a fresh notice fades in.
When the window opens on Home, its blocks appear in turn about 90 ms
apart and the face pops with the first. The face pops again when a
dictation lands; a setup step's marker and a newly granted permission's
check mark pop as they turn complete; the sidebar's face chip wobbles
when pressed. Sidebar rows have no hover state and do not squash: a
sidebar stays still under the pointer. All of it is Reduce-Motion gated
and all of it is window chrome — the code comment is explicit that none
of it touches the dictation hot path ([[dictation-pipeline]]).

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

- whisper_face_theme.py (palettes :42-64, `TYPE_SPECS` :131,
  `MOTION_SPECS` :162, `SURFACE_SPECS` :182, AA inks :202-203,
  `COLOR_TOKENS` :401, `STAGE_FOR_FACE` :450, `BRAND_SHAPES` :532);
  whisper_face_gui.py (`center_layer_anchor` :5984, `add_jelly_motion`
  :6012, `fade_in` :6051, `_srgb_color` :7115, `_token_color` :7136);
  dictate.py `_center_layer_anchor` (:2846); site/src/data/motion.ts
- [[2026-07-26-interface-rebuild-research]]
