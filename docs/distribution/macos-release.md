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
- `WhisperFace-VERSION-macOS-arm64.dmg`, the disk image people install
  from: the drag-install app and a link to Applications (signed and notarized
  once a Developer ID exists; an unsigned preview until then);
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
plan without changing anything. Opened on a Mac with a folder install, the
app offers to move it into itself, carrying the personal files over and
putting the folder install back if setup fails
([how](update-and-rollback.md#moving-a-folder-install-into-the-app)). The ZIP still expands to a writable source
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
it, preserving any Developer ID signature; for a source checkout it builds the app locally, signed with the pinned
team's Developer ID when this Mac's keychain holds one and ad hoc otherwise
(see [Folder installs](#folder-installs)). A working ad-hoc app already in place is kept
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
not accepted. The policy pins the Project Owner's Team ID, `Z6Q832529P`.
Signing is ready, not yet published: a local test build has been signed with
that Developer ID, but no signed or notarized release has been published, so
every published release is still the unsigned preview described in
[release provenance](../security/release-provenance.md).

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

Use the **Developer ID Application** certificate installed in the login
keychain. Notarization reads a `notarytool` keychain profile named
`whisper-face-notary`. Only the Project Owner creates it, once per Mac, by
running this and typing an app-specific password (made at account.apple.com)
when it asks; the password goes straight into the keychain and is never put in
a file, an environment variable or a command line:

```sh
xcrun notarytool store-credentials whisper-face-notary \
  --apple-id OWNER_APPLE_ID --team-id Z6Q832529P
```

Then:

```sh
export APPLE_DEVELOPER_ID_APPLICATION='Developer ID Application: Andrew Bergstrom (Z6Q832529P)'
export APPLE_TEAM_ID=Z6Q832529P
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
`config/macos-signing-policy.json` records the production Team ID; changing
it is an owner-reviewed commit. Packaging requires `APPLE_TEAM_ID` and the
Team ID in `APPLE_DEVELOPER_ID_APPLICATION` to match that selected revision's
policy before using the credential, so a build of a revision from before the
Team ID was recorded (0.6.0 and earlier) cannot be signed. Artifact verification reuses the launcher verifier from
the exact packaged source: unsigned previews are rebuilt and byte-compared;
signed apps must satisfy that source revision's pinned designated requirement.

### What a signed app carries, and why

Notarization refuses any program that lacks a Developer ID signature, the
hardened runtime or a secure timestamp, and it opens archives it finds and
checks the programs inside them too. The app holds exactly two programs and
`scripts/package_macos.sh --sign` signs both, inside out:

| Program | Identifier | Entitlements |
|---|---|---|
| `Contents/Helpers/uv` | `com.berg.whisper-face.uv` | none |
| `Contents/MacOS/Whisper Face` (the app) | `com.berg.whisper-face.launcher` | `com.apple.security.device.audio-input` |

- **Microphone (`audio-input`).** The launcher is the program launchd runs for
  the login service, and it starts the Python runtime as its child, so macOS
  treats the launcher as the responsible process when that runtime opens the
  microphone. A hardened responsible process without this entitlement is
  denied the microphone without a prompt.
- **Nothing else.** The hardened runtime and its library validation apply only
  to a process whose own program was signed with them. The launcher links only
  system frameworks and starts `bash`, `uv` and Python as separate processes;
  uv links only system libraries and starts Python as a separate process. The
  Python that uv installs, and the libraries it loads, keep their own
  signatures and are not hardened, so neither
  `com.apple.security.cs.disable-library-validation` nor
  `allow-dyld-environment-variables`, `allow-jit` or
  `allow-unsigned-executable-memory` is needed. A hardened, Developer ID
  signed uv was run to start a uv-installed Python 3.12 that loaded `ssl`,
  `ctypes` and `sqlite3`. The runtime sends no Apple events, so
  `automation.apple-events` is not needed either; Accessibility, Input
  Monitoring and Screen Recording have no hardened-runtime entitlement.

`verify-drag --require-signed`, and every check of a signed app after it,
refuses a program that is not signed by the pinned team, lacks the hardened
runtime or a secure timestamp, carries any other entitlement, or is not one of
those two. It also refuses, signed or not, a source archive that holds a
program or a nested archive: notarization would refuse the program, and
signing it would change the published source. Today's source holds neither;
its `.git` objects are zlib streams, which notarization does not open.

With `--notarize`, after stapling, `spctl --assess` must accept the app as
something to execute and the disk image as something to open.

### Folder installs

A folder install (`Install.command` in a source checkout or a git clone)
builds its own launcher with `swiftc`. When the login keychain holds a valid
`Developer ID Application: <name> (<team>)` identity whose team is the one
pinned in `config/macos-signing-policy.json`, `macos_launcher_app.py install`
signs that launcher with it, as `package_macos.sh --sign` signs a release's:
the hardened runtime, a secure timestamp and only the microphone entitlement.
Its designated requirement is then the pinned team plus the bundle ID, which
stays the same across rebuilds, so macOS keeps its Accessibility, Input
Monitoring and microphone permissions. Otherwise the launcher is signed ad
hoc, as before, and its privacy identity is its code hash, so a rebuild after
the launcher source changes costs every permission.

- **Choosing.** `security find-identity -v -p codesigning` lists the valid
  identities without prompting; the first Developer ID Application identity of
  the pinned team is used, by its SHA-1 hash. Setting
  `WHISPER_FACE_LAUNCHER_SIGNING=adhoc` keeps the ad-hoc build.
- **Falling back.** `setup.sh` also runs from the in-app updater and other
  unattended paths, where a keychain prompt would wait forever. codesign runs
  with no input and is stopped after 60 seconds; if it fails, is stopped, or
  produces a launcher that does not pass the signed-launcher checks, the
  launcher is signed ad hoc with one plain line saying why, and the install
  goes on. No password is ever asked for or handled.
- **Once.** A working ad-hoc launcher on a Mac that can sign is replaced by a
  signed one, which costs its permissions that one time. If that signing
  fails, the working ad-hoc launcher is kept rather than swapped for another
  ad-hoc build, and the next run tries again. A signed launcher whose source
  and icon match the checkout is current and is never rebuilt, on this Mac or
  one without the identity. An ad-hoc launcher packaged beside a release
  folder is not used on a Mac that can sign; the launcher is built and signed
  there instead.
- **Checking.** Every check of an installed or packaged launcher that is not
  ad hoc requires the pinned team's designated requirement, the hardened
  runtime, a secure timestamp and exactly the microphone entitlement.
  `./setup.sh --verify` prints `== launcher signature:` with the signer and
  team, or `ad hoc` and whether this Mac could sign it.

The drag-install app is never rebuilt or re-signed on the Mac it runs on: app
mode verifies and adopts it as released.

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
6. Install the way a person will: drag the app from the disk image to
   Applications, open it (an unsigned build needs **Open Anyway** in System
   Settings → Privacy & Security first), let the setup window finish, then run
   `"$HOME/Library/Application Support/Whisper Face/checkout/setup.sh" --verify`.
   On another Mac or account, also install from the extracted source ZIP and
   run `./setup.sh --verify` there.
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
