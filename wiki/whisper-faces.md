---
title: "Whisper Faces"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [faces, characters, hud, animation, design]
aliases: [characters, mascots, hud, wave-view]
summary: "The ten animated companion characters — one shared draw-op spec renders the live HUD, the app window, and the site, with mic-driven lip sync, blinks, and a three-frame web flap."
confidence: high
---

# Whisper Faces

## Definition

Ten companion characters (Parrot, Fox, Bear, Owl, Cat, Dog, Wolf, Pig,
Panda, Tiger) give the dictation runtime a face. One shared geometry
spec (`whisper_face_characters.py`) defines each character as an ordered
list of primitive draw ops in a 256×256 box; `dictate.py` replays the
ops through Core Graphics in the live HUD and
`scripts/generate_face_art.py` replays the same ops into SVG for the app
window and the [[marketing-site]] — the renderers cannot drift by
construction.

## Key Properties

- **Animation contract**: `character_ops(face, mouth, level, blink)` —
  mouth sweeps a closed smile through a rounded open jaw with a rising
  tongue; level drives the trailing speech puffs and jelly
  squash/stretch; blink closes the eyes into happy lids (white lids over
  the panda's dark patches). Exported frames keep blink at zero.
- **HUD lip sync**: the mouth envelope maps loudness through a soft knee
  (x^0.7) so quiet speech still moves the lips, opens faster than it
  settles (attack 0.7 / release 0.3), and takes syllable texture from
  two incommensurate sines. Deterministic jittered blinks land every
  2.8-5.0 s; a sub-percent breathing cycle runs at rest. Reduce Motion
  freezes everything.
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
  macOS and must survive 18 points, so they are separate artwork with a
  two-frame flap driven by live level; Windows mirrors face and state in
  its tray icon ([[windows-support]]).
- The HUD (`WaveView`) also draws the sticker card, radial level bars,
  the [[stable-prefix]] caption, and honest status copy (LISTENING /
  HEARD YOU / TIDYING UP), with accessibility values kept in sync.

## Related Concepts

- [[dictation-pipeline]] — the level and caption sources
- [[marketing-site]] — the same art on the web
- [[voice-modes]] — the menu-bar surfaces around the face

## References

- whisper_face_characters.py; dictate.py WaveView/StatusBar;
  scripts/generate_face_art.py; icons/faces/
- [[2026-07-26-runtime-pipeline-research]],
  [[2026-07-26-ops-governance-research]]
