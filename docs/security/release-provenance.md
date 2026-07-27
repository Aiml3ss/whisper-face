# Release provenance

A checksum answers "are these the bytes someone published?". It cannot answer
"what produced them?". This document records what a Whisper Face download
already proves, what build provenance adds, and what still is not covered.

## Verifying a download

Every published Mac release carries four files. Verify them in this order.

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
| Apple Developer ID signature | The disk image was signed by this project's Apple team identifier and has not been altered since. | That the contents match any particular source revision. Apple attests to the signer, not to the build. |
| Apple notarization and stapling | Apple scanned the artifact and issued a ticket that travels with it. | That the artifact was built from reviewed source, or by CI at all. |
| Exact-source archive plus tree digest | The shipped source is a specific Git tree, and the source in the disk image and in the zip are identical. | That the compiled launcher inside the image was built from that tree. |
| **Build provenance attestation** | A named GitHub Actions workflow, in this repository, at a specific commit, produced artifacts with these digests. | That the source at that commit is trustworthy, or that a human reviewed it. Provenance is about origin, not quality. |

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
