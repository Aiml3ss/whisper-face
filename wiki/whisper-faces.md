---
title: "Whisper Faces"
type: concept
language: en
created: 2026-07-26
modified: 2026-11-15
tags: [faces, characters, hud, animation, design]
aliases: [characters, mascots, hud, wave-view, chibi-clay, sticker-faces]
summary: "The sixteen sticker-style companion characters — one shared draw-op spec renders the recording pill, the app window, and the site, with shared schedules for lip sync, blinks, breath, and gaze, and a three-frame web flap."
confidence: high
---

# Whisper Faces

## Definition

Sixteen companion characters (Parrot, Fox, Bear, Owl, Cat, Dog, Wolf,
Pig, Panda, Tiger, Frog, Rabbit, Hedgehog, Penguin, Pickles, Olive) give
the dictation runtime a face, drawn as stickers: flat saturated colour,
blush and porcelain catchlights, inside one thick ink outline. One shared geometry
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

- **Sticker style**: every opaque body shape is first drawn in ink
  (`#2A211C`, the eye colour) grown by 6.5 design units, so the union
  reads as one outline around the whole character and never around an
  inner feature. Eyes are 14% larger about their centres and thin strokes
  (whiskers, brows, the closed smile, blink lids) 35% heavier, so they
  hold up beside the outline. `character_ops(..., shadow=True)` adds the
  same ink silhouette offset by (7, 9) under it: the hard shadow belongs
  to the surface, not the art. The Home and setup stages ask for it; the
  sidebar chip, the picker chips, the recording pill and the exported
  colour SVGs do not. `whispers=False` drops the speech puffs where a
  surface shows the level itself (the stages, the pill). Each character
  is scaled about its centre so the outline and shadow stay inside the
  256 box.
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
  Reduce Motion. Windows mirrors face and state in its tray icon, drawn
  in the pastel colours of the face chips ([[windows-support]]).
- The HUD (`WaveView`) is the recording pill: an ink-outlined capsule
  with a hard shadow, porcelain in both appearances (coral for an
  error), with the face
  leaning out of its left end. It also draws a short row of level bars,
  the [[stable-prefix]] caption, and honest status copy as one headline
  (Listening… / Heard you / Tidying up… / Pasted / Uh-oh), with
  accessibility values kept in sync. "Pasted" is the porcelain pill with
  a calm face: the paste was sent into an app that hides its text and
  could not be read back ([[insertion-transaction]]).

> 📝 **New look (November 2026)**: the faces became stickers. In
> [[app-window]] the chosen face sits on a chip at the top of the
> sidebar, stands 200pt tall and tilted on Home's stage with its hard
> shadow, does the same at 150pt in first-run setup, and appears with all
> the others in Personalize's grid of chips. The recording pill draws it
> without a shadow, leaning out of the pill's left end. [[menu-bar]]
> silhouettes kept their hand-drawn shapes.

> 📝 **Earlier, the chibi-clay rebuild (#120)**: the face anchors the
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
