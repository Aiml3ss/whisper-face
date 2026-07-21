# Third-party notices

Whispering Parrot depends on third-party software and model assets. This file
is an operational inventory, not a replacement for the corresponding license
texts.

## FluidAudio

- Project: `FluidInference/FluidAudio`
- Pinned version: `0.15.5`
- License: Apache License 2.0
- Purpose: Apple-Silicon Core ML inference for the optional Parakeet Unified
  recognition helper.

## Parakeet Unified

- Model family: NVIDIA Parakeet Unified 0.6B, converted for Core ML by
  FluidInference.
- Runtime repository: `FluidInference/parakeet-unified-en-0.6b`
- License reported by the model distributor: CC-BY-4.0.
- Purpose: English-only, punctuation-aware Mac recognition candidate.

The existing Whisper, MLX Whisper, faster-whisper, Ollama, Qwen, FFmpeg, Python,
Swift, and platform integration dependencies retain their upstream licenses.
Before distributing a self-contained binary bundle, regenerate a complete SBOM
and review every bundled component and model license.
