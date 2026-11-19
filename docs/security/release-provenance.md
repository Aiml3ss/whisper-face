# Release provenance

A checksum answers "are these the bytes someone published?". It cannot answer
"what produced them?". This document records what a Whisper Face download
proves today, what the release workflow is designed to add, and what still is
not covered.

## The drag-install disk image (from v0.6.0)

The disk image now holds only `Whisper Face.app` and a link to Applications.
The app carries the release's published source ZIP byte for byte; its SHA-256
is sealed into the app's `Info.plist` (`WFSourceArchiveSHA256`, beside
`WFSourceRevision` and the real version), and the app checks it before it
unpacks anything. `verify_macos_package.py verify-artifacts` proves the ZIP is
the exact source tree and that the app on the disk image carries that same ZIP.
The launcher digest that `RELEASE-METADATA.json` used to record is gone from
this path: the app contains the source, so the source can no longer record the
app. An unsigned app is accepted only from a preview build. A signed build
signs `uv` and then the app with the hardened runtime, a secure timestamp and
the microphone entitlement, then notarizes and staples the app before the disk
image is made, and signs, notarizes and staples the disk image too. v0.7.1 is
the first release built that way, and v0.7.2 is built the same way.

## What published releases carry today

**From v0.7.1**, the Mac disk image carries:

- `Whisper Face.app` signed with `Developer ID Application: Andrew Bergstrom
  (Z6Q832529P)`, the team pinned in `config/macos-signing-policy.json`, with
  the hardened runtime and a secure timestamp;
- Apple's notarization of the app, with the ticket stapled to it;
- a Developer ID signature on the disk image itself, its notarization, and
  its stapled ticket.

So macOS opens it without **Open Anyway**, and because the app's identity is
its bundle identifier plus the team, Input Monitoring and Accessibility
grants carry over from one signed release to the next.

Releases are still built and uploaded **by hand**, on the Project Owner's Mac,
with `scripts/package_macos.sh --sign --notarize`. The release workflow has
not completed on any tag, so **no published release has a build-provenance
attestation**: nothing ties a release to a CI run or proves which machine
built it. The Developer ID signature says who signed it; the exact-source
archive and its sealed digest say which source it carries.

**Up to v0.7.0**, every release was an unsigned preview: the app inside the
disk image is ad-hoc signed, the disk image is not notarized, and macOS
blocks the first open until you choose **Open Anyway** in System Settings →
Privacy & Security. For those, the checksum is the only check.

Every release carries checksums: `SHA256SUMS` for the Mac assets, and, from
v0.4.0, a separate `SHA256SUMS-windows` for the Windows zip (the Mac file is
not built to include it). The Windows zip is not code-signed.

## Verifying a v0.7.1 or later Mac download

```sh
# 1. The bytes are the published bytes.
shasum -a 256 -c SHA256SUMS

# 2. Apple notarized the disk image for this Developer ID, and its ticket is
#    stapled.
spctl --assess --type open --context context:primary-signature -vv \
    WhisperFace-<version>-macOS-arm64.dmg
xcrun stapler validate WhisperFace-<version>-macOS-arm64.dmg

# 3. The same for the app, once it is in Applications.
spctl --assess --type execute -vv "/Applications/Whisper Face.app"
xcrun stapler validate "/Applications/Whisper Face.app"
```

Each `spctl` line should end `accepted`, with `source=Notarized Developer ID`
and `origin=Developer ID Application: Andrew Bergstrom (Z6Q832529P)`; each
`stapler` line should say `The validate action worked!`. A different origin,
`source=Unnotarized Developer ID`, or `rejected` means the file is not the
one this project published. For v0.7.0 and earlier only the checksum line
applies:

```sh
shasum -a 256 -c SHA256SUMS            # Mac assets
shasum -a 256 -c SHA256SUMS-windows    # Windows zip
```

A checksum proves the bytes match the checksum file published beside them,
and nothing about who produced either.

## Verifying a download from the signed workflow

The rest of this document describes what the tagged release workflow adds,
which becomes available only once a tagged release completes in CI. A release
published by that workflow would carry four files. Verify them in this order.

```sh
# 1. The bytes are the published bytes.
shasum -a 256 -c SHA256SUMS

# 2. Apple signed and notarized the disk image, and the ticket is stapled.
spctl --assess --type open --context context:primary-signature -vv \
    WhisperFace-<version>-macOS-arm64.dmg
xcrun stapler validate WhisperFace-<version>-macOS-arm64.dmg

# 3. A specific workflow run in this repository produced this artifact.
gh attestation verify WhisperFace-<version>-macOS-arm64.dmg \
    --repo Aiml3ss/whisper-face
```

The third command reads a signed [SLSA](https://slsa.dev/) build-provenance
statement. It reports the repository, the workflow file, the commit SHA, and
the trigger that produced the artifacts, and fails if any of them cannot be
established.

Verification names the artifact you downloaded, not the checksum file. The
release attests over `subject-checksums: dist/SHA256SUMS`, which makes each
file *listed in* `SHA256SUMS` — the disk image, the source archive, and
`update-manifest.json` — an attested subject under its own digest; the
listing file itself is not a subject, so verifying `SHA256SUMS` would find
nothing. Each of the three verifies independently:

```sh
gh attestation verify WhisperFace-<version>-source.zip --repo Aiml3ss/whisper-face
gh attestation verify update-manifest.json --repo Aiml3ss/whisper-face
```

## What each control actually proves

| Control | Proves | Does not prove |
|---|---|---|
| `SHA256SUMS` | The file you have is byte-identical to the one whose digest was published alongside it. | Anything about who produced it. Whoever can replace the artifact can replace the checksum file next to it. |
| `update-manifest.json` | The release names one full Git revision, the source URLs for it, and the previous version for rollback. | That the manifest itself was written by the project. It is not independently signed. |
| Apple Developer ID signature (from v0.7.1) | The disk image was signed by this project's Apple team identifier and has not been altered since. | That the contents match any particular source revision. Apple attests to the signer, not to the build. |
| Apple notarization and stapling (from v0.7.1) | Apple scanned the artifact and issued a ticket that travels with it. | That the artifact was built from reviewed source, or by CI at all. |
| Exact-source archive plus tree digest | The shipped source is a specific Git tree, and the source in the disk image and in the zip are identical. | That the compiled launcher inside the image was built from that tree. |
| **Build provenance attestation** (not yet on any published release) | A named GitHub Actions workflow, in this repository, at a specific commit, produced artifacts with these digests. | That the source at that commit is trustworthy, or that a human reviewed it. Provenance is about origin, not quality. |

## Why attestations rather than a project signing key

The threat model's open work lists independent update-manifest signing
together with a key-rotation and revocation policy, because the two are
inseparable: a long-lived project key is only as good as the plan for the day
it leaks. Adding a second long-lived key to solve a provenance problem would
have taken on that liability before the policy existed.

`actions/attest-build-provenance` avoids it. Signing uses a short-lived
certificate issued to the workflow's own OIDC identity through Sigstore's
Fulcio, and the certificate is recorded in the Rekor transparency log. There
is no project-held private key to store, rotate, or revoke, and the permission
block in the workflow is the entire key-management surface. Verification is a
single `gh attestation verify` with no key distribution.

The trade-offs are real and worth stating:

- verification depends on GitHub's OIDC issuer and the Sigstore public-good
  instance being reachable and honest;
- an attacker who can modify the workflow file on the default branch can
  produce genuine provenance for a malicious build. Provenance says *this
  repository's CI built it*, which is exactly why the workflow already
  refuses to sign anything that is not reachable from `main`;
- only artifacts published by the tagged release job get provenance. A local
  `scripts/package_macos.sh` build has none, and preview builds have none;
- this does not replace independent threshold signing of the update manifest.
  That item stays open.

## What is still open

- No published release has gone through the attested workflow. From v0.7.1 a
  downloader can check the Developer ID signature, notarization and stapled
  ticket as well as `SHA256SUMS`, but not which machine or run built the
  release; up to v0.7.0 a downloader has only `SHA256SUMS`.
- For a folder or clone install, the menu updater (**Check for Updates…**)
  does not use releases at all: it fetches the tip of the checkout's tracked
  branch and runs that revision's installer, with no tag, signature, or
  provenance check. The drag-install app's copy follows published releases
  and, for a stable release, checks `codesign`, `stapler` and the pinned
  Developer ID ([how](../distribution/update-and-rollback.md)).
- The update manifest is not independently signed. A recipient who trusts the
  provenance of a *release* still has no signature over the *update metadata*
  that a future automatic updater would consume.
- There is no key-rotation or revocation policy, because there is no
  project-held key. If one is ever introduced, the policy has to land with it.
- Provenance covers publication, not the earlier packaging job. The packaging
  job holds the Apple credentials and deliberately does not receive an OIDC
  token; the link between the two jobs is the checksum-verified artifact
  bundle, which the publishing job re-checks before attesting.

Report suspected gaps privately through [SECURITY.md](../../SECURITY.md).
