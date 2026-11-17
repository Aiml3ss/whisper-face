# macOS release and rollback runbook

Windows has its own bundle, built from macOS by `scripts/package_windows.sh`
and documented in [the Windows release runbook](windows-release.md). It shares
this document's exact-source discipline and none of its Apple trust: no
Authenticode certificate exists, so the Windows artifact is unsigned and says
so.

The public Mac distribution is an exact-source release, not a second frozen
copy of the runtime. The drag-install app carries the exact published source
archive of its own release, verified against its checksum before use, and
installs from it; installers still execute the checkout as the single source
of truth and never embed runtime source. The source bundle contains one Git archive, minimal shallow
Git metadata for that exact commit, and the shipped `Install.command`;
installation continues to execute the current checkout as the single source of
truth. The metadata retains only the public GitHub origin, allowing the running
`/source` endpoint to prove its immutable revision without leaking a build path.

The release produces:

- `WhisperFace-VERSION-source.zip`, the architecture-neutral writable source
  bundle (its Mac installer currently requires Apple Silicon);
- `WhisperFace-VERSION-macOS-arm64.dmg`, the signed/notarized transport with a
  generic compiled launcher app beside the exact source folder;
- `PACKAGE-CONTENTS.json` inside both containers, a deterministic logical-tree
  receipt bound to the version, full revision, commit timestamp, entry count,
  executable bits, symlink targets, and file hashes;
- `update-manifest.json`, binding version, full Git revision, source offer,
  artifact sizes and SHA-256 hashes, Apple trust state, and rollback target;
- `SHA256SUMS`, covering both artifacts and the update manifest;
- a signed SLSA build-provenance attestation over `SHA256SUMS`, which by
  extension covers everything that file names. It is stored by GitHub rather
  than uploaded as a release asset, and is retrieved by
  `gh attestation verify`.

The disk image holds exactly two entries: `Whisper Face.app` and an
`Applications` link to `/Applications`, on a volume named `Whisper Face`.
The app carries the ZIP, byte for byte, as
`Contents/Resources/WhisperFace-source.zip`, with its SHA-256, the real
version and the source revision sealed into `Info.plist`, plus the packaging
machine's `uv` (`Contents/Helpers/uv`, a thin arm64 binary that runs on
macOS 14) and uv's licences. Opened with nothing installed, the app checks
the archive's digest, unpacks it with `ditto --noqtn` into
`~/Library/Application Support/Whisper Face/checkout`, and runs that
checkout's `setup.sh --app "<the app>"`. The app never executes anything
from the archive; the login service executes the checkout, which stays the
single source of truth.

`setup.sh --app` (app mode) uses the app's `uv` instead of Homebrew, needs no
ffmpeg, never calls `python3`, builds no launcher, and runs
`macos_launcher_app.py adopt`, which verifies the app where it is, retires a
launcher a folder install built only once it proves to be ours, refuses a
checkout of another release, and writes a schema 2 receipt naming the app.
App mode sticks to that checkout through reruns and the git updater, which
refuses it. `setup.sh --app <app> --dry-run` checks all of it and prints the
plan without changing anything. The ZIP still expands to a writable source
folder whose `Install.command` builds an unsigned local launcher, exactly as
before.

A drag install updates to a newer release when that release's app is dragged
over the old one, or from **Check for Updates…**
([how](update-and-rollback.md#updating-the-app-from-the-disk-image)). For the
menu to offer a release, publish its `update-manifest.json`, disk image and
source ZIP as assets of a GitHub release that is not marked as a prerelease:
the app reads `releases/latest/download/update-manifest.json` and follows
only its own channel, so a preview app is offered a preview manifest and a
stable app a stable one. The release gates exercise this path only against
fakes; nothing has yet been updated from a real published release.

On a full Mac install, `setup.sh` installs `~/Applications/Whisper Face.app` as
a generic Mac launcher app bundle. It contains only `Info.plist`, a minimal
compiled Swift/AppKit launcher, and its source-contract hash;
it does not copy `dictate.py`, its lockfile, models, private state, or any second
source tree. `setup.sh` builds the arm64 Mach-O executable with the system
`swiftc` supplied by the already-required Xcode Command Line Tools. Machine
binding lives outside the immutable bundle in the same-user, strict `0600`
`~/Library/Application Support/Whisper Face/launcher-install.json`, whose
parent is strict `0700` and which binds the exact app digest, checkout, and full
Git revision. Opening the app validates that receipt's owner and modes, the
bound full Git revision, and LaunchAgent working directory, then asks
`launchd` to start that existing runtime entrypoint, and uses AppKit to activate
a runtime-owned window. The running service exposes one same-user `0600` Unix
socket whose name binds the launchd PID and full source revision. After
validating the same revision and exact LaunchAgent working directory, the
launcher retries that endpoint for at most five seconds and sends one fixed byte;
the service schedules its existing `show()` method on the AppKit main thread.
There is no transcript, text, path, command, or general message payload, and a
missing endpoint safely becomes a no-op rather than starting another Python
runtime; reopening the app is the explicit retry. Each service start removes
only owned socket files matching the exact
launcher pattern whose recorded PID no longer exists; regular files, foreign
owners, live PIDs, and unrelated paths are untouched. Accepted peers have a
short read timeout so an idle same-user client cannot block later launches. It
never constructs a second settings GUI; the checkout-backed menu-bar service
continues to own its UI.
Rerunning the installer replaces only an app owned by this launcher contract.
For a DMG install it copies and digest-checks the packaged app without rewriting
it, preserving any Developer ID signature; for a source checkout it builds an
unsigned (ad-hoc) app locally. A working ad-hoc app already in place is kept
rather than swapped for another ad-hoc app, because its code hash is its
privacy identity and swapping it would cost the user every permission grant. `./setup.sh --verify` checks the app structure and
signature when present, exact receipt digest and checkout/revision binding, and
then executes no-action `--verify` mode without kickstarting `launchd`.

App acceptance is fail closed, but a rejected sibling no longer stops the
install. An ad-hoc signed sibling is accepted only when its bundle digest
equals the `launcher_bundle_sha256` recorded in the release's
`RELEASE-METADATA.json`; it is never recompiled for a byte comparison, because
`swiftc` output carries the builder's toolchain and SDK versions and no other
Mac reproduces it. A sibling that is unparseable, unsigned, carries no
recorded digest, or does not match is set aside with a message, and the
launcher is built locally instead. A Developer ID signed sibling must satisfy an Apple Developer ID Application
designated requirement for bundle ID `com.berg.whisper-face.launcher` and the
exact ten-character Team ID pinned in
`config/macos-signing-policy.json`; an arbitrary valid or ad-hoc signature is
not accepted. The policy is intentionally unconfigured until the Project Owner
records the production Team ID, so the signed release path currently aborts
rather than trusting an unknown signer.

`scripts/package_macos.sh` normalizes the staged tree to the selected commit's
timestamp, stamps its logical-tree digest, then reopens both containers and
verifies their receipts, tracked Git contents, shallow revision, public origin,
and generated release metadata. The DMG's filesystem UUID and compression
metadata are not byte reproducible, so reproducibility claims apply to its
verified logical contents, not to byte-identical DMG files across builds.

The logical-tree digest covers the bundle's `.git` directory too. The packager
therefore receives the commit's objects as loose objects, not as the pack Git
would otherwise keep: a fetched pack's bytes depend on the build machine's own
repository state and on a threaded delta search, so two builds of one revision
used to ship different pack files and different digests. Loose objects make
every covered file byte-identical between builds of one revision on one build
host. Their bytes still depend on the host's zlib, so a build on a different
machine may record a different digest for the same revision. The packager
fails if anything other than a loose object lands in `.git/objects`.

## Build an unsigned local preview

Run this on Apple Silicon macOS from any worktree state:

```sh
scripts/package_macos.sh --version 0.1.0 --channel preview
python3 scripts/release_manifest.py verify \
  --manifest dist/update-manifest.json --artifact-dir dist
python3 scripts/verify_macos_package.py verify-artifacts \
  --source-zip dist/WhisperFace-0.1.0-source.zip \
  --disk-image dist/WhisperFace-0.1.0-macOS-arm64.dmg \
  --version 0.1.0 \
  --revision "$(git rev-parse HEAD)"
shasum -a 256 -c dist/SHA256SUMS
```

The package always exports the selected committed revision with `git archive`.
Uncommitted files are deliberately excluded, so local changes cannot silently
enter a release. The structural verifier requires no Apple account or signing
credentials. Unsigned previews are useful for pipeline testing but must not be
published as production builds.

## Sign and notarize locally

Use a **Developer ID Application** certificate installed in the login keychain:

```sh
export APPLE_DEVELOPER_ID_APPLICATION='Developer ID Application: Example (TEAMID)'
xcrun notarytool store-credentials whisper-face-notary \
  --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PASSWORD
export APPLE_NOTARY_KEYCHAIN_PROFILE=whisper-face-notary

scripts/package_macos.sh --version 1.2.3 --sign --notarize \
  --previous-version 1.2.2 \
  --previous-revision FULL_40_CHARACTER_GIT_SHA \
  --previous-manifest-url \
    https://github.com/Aiml3ss/whisper-face/releases/download/v1.2.2/update-manifest.json
```

As an alternative to a keychain profile, notarization reads `APPLE_ID`,
`APPLE_TEAM_ID`, and `APPLE_APP_SPECIFIC_PASSWORD`. Do not place secrets in a
shell history, source file, release artifact, manifest, or log.

The script compiles the generic launcher from the selected exported revision,
signs it with hardened runtime before creating the disk image, signs the final
disk image, waits for Apple's notarization result,
downloads and checks the notarization log, staples the ticket, validates the
ticket, structurally re-verifies the ZIP and DMG, computes digests only after
stapling, then re-verifies every manifest artifact. Missing or partial
credentials, a non-accepted status, or reported notary issues abort before a
production trust claim can be written. This follows
Apple's current [custom notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow),
which accepts UDIF disk images and uses `notarytool` plus `stapler`. Apple
documents Developer ID Application as the appropriate certificate for signing
disk images in its [notarization troubleshooting guide](https://developer.apple.com/documentation/security/resolving-common-notarization-issues).
Before the first signed release, replace the `null` Team ID in
`config/macos-signing-policy.json` with the production certificate's Team ID in
an owner-reviewed commit. Packaging requires `APPLE_TEAM_ID` and the Team ID in
`APPLE_DEVELOPER_ID_APPLICATION` to match that selected revision's policy before
using the credential. Artifact verification reuses the launcher verifier from
the exact packaged source: unsigned previews are rebuilt and byte-compared;
signed apps must satisfy that source revision's pinned designated requirement.

## GitHub release automation

`.github/workflows/macos-release.yml` builds an unsigned preview when manually
dispatched. Setting **Signed preview** exercises the production signing path
without publishing. A `vX.Y.Z` tag requires signing and notarization and creates
the GitHub Release only after verification.

Configure these GitHub Actions secrets:

| Secret | Purpose |
|---|---|
| `APPLE_DEVELOPER_ID_P12_BASE64` | Base64-encoded Developer ID Application certificate and private key. |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | Password protecting the P12. |
| `APPLE_DEVELOPER_ID_APPLICATION` | Exact certificate identity reported by `security find-identity -v -p codesigning`. |
| `APPLE_ID` | Apple Account used by `notarytool`. |
| `APPLE_TEAM_ID` | Apple Developer team identifier. |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password for notarization. |

The workflow creates an ephemeral keychain, never uploads it, and deletes it in
an `always()` cleanup step. GitHub should restrict release workflow changes and
environment secrets to the Project Owner.

Manual dispatch may package any ref only as an unsigned, read-only preview.
Before any Apple credential is exposed, the workflow requires a signed preview
to equal the current `origin/main`, or a release tag to point to a commit
reachable from `main`; it then runs the release gates and proves the tracked
worktree is still pristine. Dispatch inputs enter shell steps through environment
variables rather than expression interpolation. Apple values are scoped to the
three credential/signing steps, and the job has a read-only repository token.
A separate job receives only verified artifacts and a write token for tagged
publication; it has no Apple credentials.

## Release procedure

1. Run every repository release gate and `./setup.sh --verify` on the candidate.
2. Confirm `git status` is understood; only committed files enter the artifact.
3. Create an annotated `vX.Y.Z` tag on the approved full revision and push it.
4. Require the macOS release workflow to pass signing, notarization, stapling,
   logical package verification, manifest verification, and checksum generation.
5. Download the published assets on a different Mac. Verify `SHA256SUMS`, run
   `gh attestation verify WhisperFace-<version>-macOS-arm64.dmg --repo
   Aiml3ss/whisper-face` (the attested subjects are the files *listed in*
   `SHA256SUMS`, not the listing itself) to
   confirm which workflow run and commit produced them, mount the DMG, and run
   `spctl`/`stapler` verification before install. What each of those checks
   does and does not prove is set out in
   [release provenance](../security/release-provenance.md).
6. Install from the extracted source and run `./setup.sh --verify`.
7. Confirm Advanced -> Exact Source (and, for a `--server-only` install,
   `GET /source`) name the tagged
   revision. Do not announce availability before all checks pass.

## Rollback

An updater or human operator must first verify the current manifest and the
previous manifest named by `rollback.manifest_url`. Download the prior artifact
and verify its digest against that prior manifest. Rerunning `Install.command`
inside the **same checkout** preserves private files because the installer does
not replace an existing destination. A separately extracted rollback folder has
no automatic access to state in the current folder: before running its installer,
manually copy `dictionary.txt`, `snippets.json`, `tones.json`, `preferences.json`,
`learned.json`, `acoustic_keyword_memory.json`,
`acoustic_keyword_activation.json`, `acoustic_calibration_activation.json`,
`relisten_activation.json`, and `delayed_cleanup_activation.json` into it with
user-only permissions. Copy
`transcripts.jsonl` only if retaining transcript history is intentional.
Review the files rather than copying the entire old checkout or its logs.

Never overwrite the only working checkout before the prior artifact has been
verified. Keep the failed release folder until diagnostics are collected. If a
release is known unsafe, remove it from the update channel, publish a security
advisory where appropriate, and ship a new signed release; do not retarget or
rewrite its Git tag or manifest.

## AGPL source-offer gate

Every artifact includes `LICENSE`, `LICENSE_POLICY.md`, `NOTICE`,
`THIRD_PARTY_NOTICES.md`, and `RELEASE-METADATA.json`. The manifest links the
same immutable tree and corresponding source archive. Modified distributors
must build from and publish their own full revision, retain notices, and provide
the corresponding source required by the AGPL. Packaging must never substitute
a binary-only source offer.
