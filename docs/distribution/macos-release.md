# macOS release and rollback runbook

The public Mac distribution is an exact-source release, not a second frozen
copy of the runtime. The source bundle contains one Git archive, minimal shallow
Git metadata for that exact commit, and the shipped `Install.command`;
installation continues to execute the current checkout as the single source of
truth. The metadata retains only the public GitHub origin, allowing the running
`/source` endpoint to prove its immutable revision without leaking a build path.

The release produces:

- `WhisperFace-VERSION-source.zip`, the architecture-neutral writable source
  bundle (its Mac installer currently requires Apple Silicon);
- `WhisperFace-VERSION-macOS-arm64.dmg`, the signed/notarized transport;
- `PACKAGE-CONTENTS.json` inside both containers, a deterministic logical-tree
  receipt bound to the version, full revision, commit timestamp, entry count,
  executable bits, symlink targets, and file hashes;
- `update-manifest.json`, binding version, full Git revision, source offer,
  artifact sizes and SHA-256 hashes, Apple trust state, and rollback target;
- `SHA256SUMS`, covering both artifacts and the update manifest.

The disk image and ZIP contain the same exact source. A DMG user must copy its
source folder to a writable local folder before clicking `Install.command`.
The ZIP expands to a writable folder directly.

`scripts/package_macos.sh` normalizes the staged tree to the selected commit's
timestamp, stamps its logical-tree digest, then reopens both containers and
verifies their receipts, tracked Git contents, shallow revision, public origin,
and generated release metadata. The DMG's filesystem UUID and compression
metadata are not byte reproducible, so reproducibility claims apply to its
verified logical contents, not to byte-identical DMG files across builds.

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
    https://github.com/Aiml3ss/whispering-parrot/releases/download/v1.2.2/update-manifest.json
```

As an alternative to a keychain profile, notarization reads `APPLE_ID`,
`APPLE_TEAM_ID`, and `APPLE_APP_SPECIFIC_PASSWORD`. Do not place secrets in a
shell history, source file, release artifact, manifest, or log.

The script signs the final disk image, waits for Apple's notarization result,
downloads and checks the notarization log, staples the ticket, validates the
ticket, structurally re-verifies the ZIP and DMG, computes digests only after
stapling, then re-verifies every manifest artifact. Missing or partial
credentials, a non-accepted status, or reported notary issues abort before a
production trust claim can be written. This follows
Apple's current [custom notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow),
which accepts UDIF disk images and uses `notarytool` plus `stapler`. Apple
documents Developer ID Application as the appropriate certificate for signing
disk images in its [notarization troubleshooting guide](https://developer.apple.com/documentation/security/resolving-common-notarization-issues).

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
5. Download the published assets on a different Mac. Verify `SHA256SUMS`, mount
   the DMG, and run `spctl`/`stapler` verification before install.
6. Install from the extracted source and run `./setup.sh --verify`.
7. Confirm Diagnostics -> Exact Source and `GET /source` name the tagged
   revision. Do not announce availability before all checks pass.

## Rollback

An updater or human operator must first verify the current manifest and the
previous manifest named by `rollback.manifest_url`. Download the prior artifact
and verify its digest against that prior manifest. Rerunning `Install.command`
inside the **same checkout** preserves private files because the installer does
not replace an existing destination. A separately extracted rollback folder has
no automatic access to state in the current folder: before running its installer,
manually copy `dictionary.txt`, `snippets.json`, `tones.json`, `preferences.json`,
`learned.json`, and `acoustic_keyword_memory.json` into it with user-only
permissions. Copy `transcripts.jsonl` only if retaining transcript history is
intentional. Review the files rather than copying the entire old checkout or
its logs.

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
