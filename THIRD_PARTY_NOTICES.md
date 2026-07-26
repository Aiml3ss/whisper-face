# Third-party notices and provenance

Whisper Face depends on third-party software and model assets. The project's
`AGPL-3.0-only` and commercial licensing options cover only first-party rights;
they do not replace, override, or relicense the items below.

This source repository does not vendor the Python wheels, Ollama model, Whisper
weights, Parakeet weights, FFmpeg, or Ollama binary. The one-click installer
downloads them from their named upstream package managers. The exact Python
dependency graph is locked in `dictate.py.lock`; installed packages retain
their own license metadata and files.

## Direct Python runtime dependencies

| Component | Locked version | License | Upstream |
|---|---:|---|---|
| mlx-whisper | 0.4.3 | MIT | [ml-explore/mlx-examples](https://github.com/ml-explore/mlx-examples) |
| faster-whisper | 1.2.1 | MIT | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| sounddevice | 0.5.5 | MIT | [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) |
| pynput | 1.8.2 | LGPL-3.0 | [moses-palmer/pynput](https://github.com/moses-palmer/pynput) |
| PyObjC frameworks | 12.2.1 | MIT | [pyobjc](https://github.com/ronaldoussoren/pyobjc) |
| pyperclip | 1.11.0 | BSD-3-Clause | [asweigart/pyperclip](https://github.com/asweigart/pyperclip) |
| pywin32 | 312 | PSF-2.0 | [mhammond/pywin32](https://github.com/mhammond/pywin32) |
| pystray | 0.19.5 | LGPL-3.0 | [moses-palmer/pystray](https://github.com/moses-palmer/pystray) |
| Pillow | 12.3.0 | MIT-CMU | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) |
| NumPy | 2.2.6 or 2.4.6 by Python version | BSD-3-Clause plus bundled-component notices | [numpy/numpy](https://github.com/numpy/numpy) |
| Requests | 2.34.2 | Apache-2.0 | [psf/requests](https://github.com/psf/requests) |

Transitive packages and platform markers are authoritative in
`dictate.py.lock`. Regenerate the lock and this table together whenever the
PEP 723 dependency block changes.

## Native Mac recognition helper

| Component | Pinned version/revision | License | How used |
|---|---|---|---|
| FluidAudio | 0.15.5 / `19600a485baa4998812e4654b70d2bab8f2c9949` | Apache-2.0 | Swift package compiled locally into the Mac helper. [Source and license](https://github.com/FluidInference/FluidAudio) |
| Parakeet Unified 0.6B Core ML | `4252711f6f060f9a2f91e5f081a806d7f45eebd8` | **Review required: conflicting upstream metadata** | Model downloaded at install time from [`FluidInference/parakeet-unified-en-0.6b-coreml`](https://huggingface.co/FluidInference/parakeet-unified-en-0.6b-coreml). At this exact revision, the repository card/API declares CC-BY-4.0 and names `nvidia/parakeet-tdt-0.6b-v2` as its base; the shipped `metadata.json` and `config.json` instead identify `nvidia/parakeet-unified-en-0.6b`, whose upstream repository declares the NVIDIA Open Model License. Do not treat the conversion as unambiguously CC-BY-4.0 or redistribute it in a binary/commercial package until FluidInference or NVIDIA reconciles the artifact provenance and governing terms. |

The helper receives audio through a RAM-only local pipe. Neither model weights
nor FluidAudio source are committed to this repository.

## Speech and cleanup models

| Runtime role | Repository/tag | Pinned revision or audited manifest | License/provenance |
|---|---|---|---|
| Mac speculative ASR | `mlx-community/whisper-tiny` | `78c52ab98ca87f570bc57ad852e15ef7060f9f76` | Conversion repository metadata does not declare a license. The upstream [OpenAI Whisper](https://github.com/openai/whisper) code and weights are MIT; review the conversion provenance before bundling. |
| Mac fallback ASR | `mlx-community/whisper-large-v3-turbo` | `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` | Same provenance qualification as the Mac Tiny conversion. |
| Windows speculative ASR | `Systran/faster-whisper-tiny` | `d90ca5fe260221311c53c58e660288d3deb8d356` | MIT as declared by the model repository. |
| Windows fallback ASR | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | MIT as declared by the model repository (currently redirects to `dropbox-dash`). |
| Selective cleanup | Ollama `qwen3.5:4b` | enforced local manifest `sha256:2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd` | Apache-2.0 text is embedded in the Ollama model manifest. The installer fails closed until a changed public tag is re-audited. |

Whisper and Parakeet revisions are enforced by the runtime preload path so a
fresh install does not silently move to different weights. The Ollama tag is
not content-addressable through the current installer, so install and verify
hash the local manifest and reject any unreviewed change.

## System tools downloaded by the installer

| Component | Installation source | License note |
|---|---|---|
| Ollama | Homebrew or `Ollama.Ollama` via winget | MIT; installed separately, not linked into Whisper Face. |
| FFmpeg | Homebrew or `Gyan.FFmpeg` via winget | The current Homebrew formula is `GPL-3.0-or-later`; Gyan's full Windows builds are GPLv3 distributions. Installed and invoked as a separate executable for compatibility-endpoint audio decoding. |
| uv | Official installer or `astral-sh.uv` via winget | Apache-2.0 OR MIT; used to create the locked environment. |
| Swift toolchain | Apple Command Line Tools | Apple toolchain terms; used locally to compile the helper. |

## Release automation actions

The macOS release workflow executes these actions on GitHub-hosted runners.
They are pinned to full commit IDs so a moving major-version tag cannot silently
change code that handles release artifacts or precedes signing steps.

| Component | Pinned revision | License | How used |
|---|---|---|---|
| `actions/checkout` v6 | `d23441a48e516b6c34aea4fa41551a30e30af803` | MIT | Checks out the selected exact source revision with a read-only token. |
| `astral-sh/setup-uv` v8.1.0 | `08807647e7069bb48b6ef5acd8ec9567f424441b` | MIT | Installs uv for repository release gates. |
| `actions/upload-artifact` v7 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | MIT | Transfers verified assets out of the read-only packaging job. |
| `actions/download-artifact` v8 | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | MIT | Retrieves verified assets in the isolated release-publishing job. |

## Marketing-site interface library

| Component | Version/revision | License | How used |
|---|---|---|---|
| Jelly UI | 1.1.0 / upstream revision `8e39a8e61b5a43a562ae85e4b01191d333d5b121` | MIT, Copyright 2026 bmson | The public site loads the official bundled Web Components module from `jelly-ui.com` for its theme provider and theme control. Subresource Integrity pins the audited bundle bytes; branded surfaces use first-party CSS motion derived from the same interaction language. [Source and license](https://github.com/jelly-org/ui) |

## Binary distribution warning

Before distributing a signed, notarized, self-contained binary or hardware
image, generate an SBOM from the exact artifact, include all required license
texts and attribution, and have counsel review the LGPL/GPL, Apache notice,
Parakeet license/provenance conflict, other model attribution, and FFmpeg
configuration. A
source checkout plus download orchestrator is not the same compliance surface
as a bundled application.
