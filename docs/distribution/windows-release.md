# Windows release runbook

The Windows distribution is an exact-source release built from macOS. It is the
same idea as [the Mac release](macos-release.md) with one difference that
governs everything else in this document: **there is no Windows code signing.**
The project holds no Authenticode certificate, so no Windows artifact can name a
publisher, and nothing in `scripts/package_windows.sh` reads a signing
credential. Where the Mac runbook says "signed, notarized, stapled", this one
says "unsigned", and the release must be described that way in public.

The release produces:

- `WhisperFace-VERSION-windows-x64.zip`, the writable source bundle containing
  one Git archive of the selected commit, minimal shallow Git metadata for that
  exact commit, and the shipped `Install.cmd`;
- `START HERE.txt`, beside the versioned folder at the ZIP root, so the first
  thing a downloader sees after extracting is what to run next;
- `PACKAGE-CONTENTS.json` inside the bundle, the same deterministic
  logical-tree receipt the Mac packages carry, bound to the version, full
  revision, commit timestamp, entry count, executable bits, symlink targets,
  and file hashes;
- `update-manifest.json`, binding version, full Git revision, source offer,
  artifact size and SHA-256, and rollback target;
- `SHA256SUMS`, covering the bundle and the update manifest.

## What the artifact proves, and what it does not

| Claim | Held by |
|---|---|
| The bundle contains exactly the tracked files of one Git commit | `PACKAGE-CONTENTS.json`, plus `git diff --quiet HEAD` and `git status` inside the extracted checkout |
| That commit is the one the manifest names | the shallow Git metadata, `RELEASE-METADATA.json`, and the manifest's `source_offer.revision` |
| The download was not altered after it was built | `SHA256SUMS`, checked against the manifest |
| **Who built it** | **nothing** |
| **That Windows will trust it** | **nothing** |

There is no Authenticode signature, no timestamp countersignature, and no
SmartScreen reputation. Windows will say the publisher cannot be verified, and
that statement is correct. A user's only defence against a substituted download
is comparing the SHA-256 against `SHA256SUMS` published beside the release, and
`SHA256SUMS` itself is only as trustworthy as the channel it arrives on. Say
this plainly wherever the download is offered; do not imply a trust property the
artifact does not have.

The Mac release covers the gap differently: its DMG is signed and notarized by
Apple, and the runbook still marks a locally built preview as unsigned and not
for publication. On Windows there is no signed path to fall back to yet.

## Build the bundle

Run this from macOS (or any POSIX host with `git` and `python3`). It needs no
Windows machine, no Wine, and no PowerShell:

```sh
scripts/package_windows.sh --version 0.3.1
python3 scripts/windows_bundle.py verify \
  --bundle-zip dist/windows/WhisperFace-0.3.1-windows-x64.zip \
  --version 0.3.1 \
  --revision "$(git rev-parse HEAD)"
python3 scripts/release_manifest.py verify \
  --manifest dist/windows/update-manifest.json \
  --artifact-dir dist/windows
shasum -a 256 -c dist/windows/SHA256SUMS
```

Options mirror `scripts/package_macos.sh`: `--revision`, `--output-dir`,
`--channel`, `--download-base-url`, and the three `--previous-*` rollback
options, which must be supplied together or not at all. The output directory
defaults to `dist/windows` so a Windows build never overwrites the Mac
release's `update-manifest.json` or `SHA256SUMS`.

Like the Mac packager, the script always exports the selected committed
revision with `git archive`. Uncommitted files are deliberately excluded, so
local changes cannot silently enter a release.

`scripts/windows_bundle.py verify` runs as part of packaging and can be rerun
against any published ZIP. It refuses a bundle that:

- has a member Windows cannot create — a reserved device name, a path with
  `< > : " | ? *` or a backslash, a component ending in a space or a dot, or
  two members differing only in case;
- carries macOS resource-fork residue (`__MACOSX/`, `._` sidecars), which is
  what Finder or `ditto` would add;
- has anything at the ZIP root except the versioned folder and `START HERE.txt`;
- fails the logical-tree receipt or the exact-checkout proof;
- is missing `Install.cmd`, `setup.ps1`, `dictate.py`, `dictate.py.lock`, or the
  licence and notice files;
- carries private state — transcripts, dictionary, snippets, tones,
  preferences, learned corrections, logs, `.evidence/`, `.windows/`;
- ships an `Install.cmd` that no longer bypasses the execution policy or no
  longer targets the bundled `setup.ps1` by absolute path.

## Why the bundle ships `Install.cmd` rather than `setup.ps1` alone

Windows will not run a double-clicked `.ps1`. The default execution policy on
Windows client editions is `Restricted`, the file explorer's default verb for
`.ps1` is *Edit*, and a script that arrived inside a downloaded ZIP also carries
the Mark of the Web. Telling a user to "run setup.ps1" therefore fails three
different ways before the installer starts.

`Install.cmd` is the shim that makes it work. It is one file, at the root of the
versioned folder:

```bat
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
```

Every part of that line is load bearing, and `tests/test_windows_distribution.py`
holds each one:

- `cd /d "%~dp0"` runs the installer from the extracted bundle rather than from
  whatever directory Explorer happened to hand `cmd.exe`;
- `%~dp0` expands to the bundle's own path, so `setup.ps1` is resolved
  absolutely and never through `PATH` or the current directory;
- the quotes survive a path like `C:\Users\Someone\Whisper Face 0.3.1`;
- `-ExecutionPolicy Bypass` is what lets an unsigned, freshly downloaded script
  run at all;
- `-NoProfile` keeps whatever the machine's PowerShell profile does out of the
  install;
- `-File` (not `-Command`) passes the script path as a path, and `%*` forwards
  `--verify`, `--server-only`, and `--uninstall` through unchanged;
- the trailing `pause` on an argument-free run keeps the window open long
  enough to read the result of a double-click.

`Install.cmd` is checked in with CRLF line endings via `.gitattributes`
(`/Install.cmd text eol=crlf`). `git archive` applies that attribute, so the
release bundle inherits it. `cmd.exe` is the only interpreter in this project
that still cares.

`-ExecutionPolicy Bypass` does **not** override an execution policy set by
Group Policy (`MachinePolicy` or `UserPolicy` scope). On a managed machine the
install can still be blocked, and the failure will come from PowerShell rather
than from Whisper Face.

## What a first-time Windows user will actually see

1. The browser may warn about the download itself. The ZIP is unsigned.
2. Right-clicking the ZIP, choosing Properties, and ticking **Unblock** before
   extracting avoids the next warning. `START HERE.txt` says so.
3. Without that, double-clicking `Install.cmd` shows *Open File - Security
   Warning* ("The publisher could not be verified"). This is expected and
   correct; there is no publisher to verify.
4. `setup.ps1` requires **winget** (App Installer, from the Microsoft Store) and
   fails closed with a clear message if it is absent. It is not installed for
   the user.
5. The installer needs a writable checkout. Running it from inside the ZIP
   viewer, from `C:\Program Files`, or from a read-only share fails with
   "checkout is not writable".
6. `setup.ps1` installs `uv`, `ffmpeg`, and Ollama through winget, then
   registers a `Whisper Face` Task Scheduler entry that starts dictation at
   login. Rerunning replaces the login task and keeps private files.

## Verification on the machine that installs it

Static packaging checks cannot stand in for running the installer. On the
Windows machine, after installing:

```powershell
.\setup.ps1 --verify
```

That checks the installed stack without changing it. To see what an uninstall
would remove without removing anything:

```powershell
.\setup.ps1 --uninstall
```

**`git` is not part of the Windows install.** `setup.ps1` installs `uv`,
`ffmpeg`, and Ollama, and nothing else. The runtime's `/source` endpoint and
Diagnostics -> Exact Source read the revision by running `git rev-parse HEAD`
in the checkout, falling back to the `WHISPER_FACE_SOURCE_REVISION`
environment variable. The bundle carries the shallow Git metadata that makes
that possible, but a machine with no `git.exe` on `PATH` still cannot run the
command. On such a machine the source proof raises rather than answering, and
either `git` must be installed or `WHISPER_FACE_SOURCE_REVISION` must be set to
the full revision printed at the end of packaging. Every other part of
dictation is unaffected.

## Release procedure

1. Run every repository release gate, including
   `uv run tests/test_windows_distribution.py`.
2. Confirm `git status` is understood; only committed files enter the artifact.
3. Build the bundle from the approved full revision and verify it with the four
   commands above.
4. Publish `WhisperFace-VERSION-windows-x64.zip`, `update-manifest.json`, and
   `SHA256SUMS` alongside the Mac assets. Keep the Windows `update-manifest.json`
   distinct from the Mac one; `scripts/release_manifest.py` refuses to describe
   both platforms' install paths in a single manifest, because a manifest names
   exactly one entry point.
5. On a Windows machine, download the published ZIP, compare its SHA-256 against
   `SHA256SUMS`, extract it, and run `Install.cmd`.
6. Run `.\setup.ps1 --verify`, then confirm dictation works after a login.
7. Do not announce availability before a real Windows install has succeeded.
   Nothing in this repository can verify the Windows install path from macOS.

## Rollback

The Windows manifest carries the same `rollback` block as the Mac one. There is
no automatic updater for Windows: `scripts/safe_update_advisor.py` and
`self_update.py` verify Apple trust and refuse anything else, so a Windows
rollback is a manual download of the previous bundle after checking its digest
against that release's manifest.

Rerunning `Install.cmd` inside the **same** extracted folder preserves private
files, because the installer does not replace an existing destination. A
separately extracted rollback folder has no automatic access to state in the
current folder; copy `dictionary.txt`, `snippets.json`, `tones.json`,
`preferences.json`, `learned.json`, `acoustic_keyword_memory.json`,
`acoustic_keyword_activation.json`, `acoustic_calibration_activation.json`,
`relisten_activation.json`, and `delayed_cleanup_activation.json` across by
hand first, and `transcripts.jsonl` only if retaining transcript history is
intentional. The full list and the reasoning are in
[update and rollback](update-and-rollback.md).

## AGPL source-offer gate

The bundle is the corresponding source. It contains `LICENSE`,
`LICENSE_POLICY.md`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, and
`RELEASE-METADATA.json`, and the manifest links the same immutable tree and
corresponding-source archive. The manifest classifies the bundle as
`windows-source-bundle` with role `installer` because that is what a user runs;
the AGPL obligation is carried by `source_offer` and by the bundle's own
contents, both of which name the same revision. Modified distributors must
build from and publish their own full revision, retain notices, and provide the
corresponding source the AGPL requires.
