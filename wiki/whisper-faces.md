---
title: "Whisper Faces"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
tags: [faces, characters, hud, animation, design]
aliases: [characters, mascots, hud, wave-view, chibi-clay]
summary: "The fourteen chibi-clay companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap."
confidence: high
---

# Whisper Faces

## Definition

Fourteen companion characters (Parrot, Fox, Bear, Owl, Cat, Dog, Wolf,
Pig, Panda, Tiger, Frog, Rabbit, Hedgehog, Penguin) give the dictation
runtime a face — since the chibi-clay revamp (#120), drawn pastel with
clay shading, blush and porcelain catchlights. One shared geometry
spec (`whisper_face_characters.py`)
defines each character as an ordered list of primitive draw ops in a
256×256 box; the Core Graphics replay in `whisper_face_render.py` draws
them in the live HUD and the window, and
`scripts/generate_face_art.py` replays the same ops into SVG for the
[[marketing-site]] — the renderers cannot drift by construction.
`whisper_face_render.py` also owns how a character behaves over time:
the blink, breath, gaze and mouth schedules (`IdleLifeDriver`) are
frame-counted, deterministic, AppKit-free at the top half, and shared by
the HUD and the window's `LiveFaceView` so the two cannot drift apart.

## Key Properties

- **Animation contract**: `character_ops(face, mouth, level, blink,
  gaze)` — mouth sweeps a closed smile through a rounded open jaw with a
  rising tongue; level drives the trailing speech puffs and jelly
  squash/stretch; blink closes the eyes into happy lids (white lids over
  the panda's dark patches); gaze (#120) drifts the pupils a few clamped
  points, with idle saccades every 5-9 s that glide rather than
  teleport. Exported frames keep blink and gaze at rest.
- **Lip sync and idle life** (shared schedules, `whisper_face_render`):
  the mouth envelope maps loudness through a soft knee (x^0.7) so quiet
  speech still moves the lips, opens faster than it settles (attack
  0.7 / release 0.3), and takes syllable texture from two incommensurate
  sines. Deterministic jittered blinks land every 2.8-5.0 s; a
  sub-percent breathing cycle runs at rest (the bigger window face
  breathes a touch deeper); mood overrides give non-recording phases a
  face — processing concentrates behind happy closed lids, error looks
  down. Reduce Motion freezes everything.
- **Three-frame web flap**: static surfaces sample named frames — idle,
  a mid-syllable half, and talk. The site hero syncs the mouth to the
  letters landing in the ticker (vowels wide, consonants half, pauses
  closed); gallery hover runs a varied-timing babble loop.
- **Species identity**: fox ear-tips and cheek fluff, cat fangs and
  curved whiskers, teardrop dog ears with a brow patch, wolf stern
  brows, pig snout, tilted panda patches on solid black ears, tapered
  tiger stripes, gold owl irises with chest chevrons, three-feather
  parrot crest with a hooked beak that visibly opens.
- **Menu bar stays hand-authored**: template silhouettes are tinted by
  macOS and must survive 18 points, so they are separate artwork —
  since #120 three cached frames per character (idle, talk, blink): the
  open-mouth frame follows the live mic level and the blink frame
  flashes on a rare deterministic timer every 4-7 s, frozen under
  Reduce Motion. Windows mirrors face and state in its tray icon, now
  drawn in the same pastel chibi palettes ([[windows-support]]).
- The HUD (`WaveView`) also draws the sticker card, radial level bars,
  the [[stable-prefix]] caption, and honest status copy (LISTENING /
  HEARD YOU / TIDYING UP), with accessibility values kept in sync.

> 📝 **Updated for the chibi-clay rebuild (#120)**: the face anchors the
> rebuilt window surfaces. A 36pt chip heads [[app-window]]'s top bar
> (the sidebar is gone), Home leads with a live `LiveFaceView` character
> that breathes, blinks, glances and lip-syncs while recording, a 208pt
> tilted chip carries first run, and [[menu-bar]]'s **Choose Face** is
> the only submenu left in the menu. The HUD pop and the menu-bar mouth
> wobble run the shared [[design-language]] springs, and both were
> squashing from the bottom-left corner until #112 centred the layer
> anchor.

## Related Concepts

- [[dictation-pipeline]] — the level and caption sources
- [[marketing-site]] — the same art on the web
- [[menu-bar]], [[app-window]] — the two surfaces the face heads
- [[design-language]] — the springs the squash comes from

## References

- whisper_face_characters.py; whisper_face_render.py;
  dictate.py WaveView/StatusBar; scripts/generate_face_art.py;
  icons/faces/
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-ops-governance-research]]
