---
title: "Distribution"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-27
tags: [updates, packaging, signing, releases, operations]
aliases: [self-update, side-by-side-update, release-manifest, packaging]
summary: "Two deliberately separate update paths — a fail-closed local self-update with rollback, and signed-release operator tooling — plus reproducible-tree packaging with notarization and an auditable stdlib-only manifest."
confidence: high
---

# Distribution

## Definition

Updates remain explicit; nothing polls in the background. Path A is the
menu-driven self-update for source checkouts; Path B is the
release-operator tooling for signed disk images. Public Mac releases are
built from one exact Git revision with the same installer used in a
checkout.

## Key Properties

- **Self-update** (`self_update.py`): exactly one network operation
  (git fetch), fail-closed on dirty trees or unresolvable upstreams;
  apply records the previous HEAD, runs the installer, and rolls back
  on failure — the checkout is never left on a broken revision. A
  detached launchd variant exists because the installer reloads the
  service that would otherwise kill the updater; results persist to a
  0600 state file consumed on restart; errors are privacy-scrubbed.
- **Advisor + side-by-side** (`scripts/safe_update_advisor.py`,
  `scripts/side_by_side_update.py`): read-only verdicts
  (up-to-date/upgrade/rollback/refuse) verifying manifests against
  local artifact hashes — stable channels additionally require live
  codesign + stapler validation; side-by-side validates a separately
  prepared clean sibling checkout, only `--apply` executes the
  candidate's own setup, and the current checkout is never altered —
  it *is* the rollback copy.
- **Manifest** (`scripts/release_manifest.py`): stdlib-only so it is
  auditable before dependencies exist; strict SemVer/40-hex/https
  validators; rollback linkage; an installation contract
  (source-bundle-in-place; same-checkout reinstall preserves private
  state; no automatic cross-checkout migration).
- **Packaging** (`scripts/package_macos.sh`): git-archive the exact
  revision, re-init shallow metadata so `/source` can prove it,
  optionally sign + notarize + staple (validating the Apple team
  against the packaged revision's own
  `config/macos-signing-policy.json` — currently null, so signing is
  disabled until the owner records a team id), deterministic tree
  stamping because DMGs are not byte-reproducible, then ZIP + DMG +
  manifest + SHA256SUMS.
- **Release workflow**: the macOS release CI job runs the full gate
  list, asserts the worktree is still pristine afterwards, confines
  Apple credentials to the packaging job, SHA-pins all actions, and
  publishes from an isolated job ([[governance]]).
- The user-facing update guide: back up private files, `git pull
  --ff-only`, rerun the installer in the same checkout; rollback via a
  detached checkout of the known-good revision; never `git reset
  --hard`.

## The pipeline has now shipped

> 📝 **Updated 2026-07-27**: four releases exist on the public
> repository — **v0.1.0**, **v0.2.0**, **v0.2.1** (2026-07-26), and
> **v0.3.0** (2026-07-27). Each carries the same four assets the
> packaging script produces: an Apple-Silicon DMG, a source ZIP,
> `SHA256SUMS`, and `update-manifest.json`. v0.3.0 is the first release
> whose manifest and source offer name the renamed repository (#137) and
> the first whose rollback block links a real previous release (v0.2.1).
> The self-update path also changed underneath it (#132): the check now
> compares upstream against the revision the installer last provisioned
> rather than the checkout's HEAD, refuses to rewind a checkout carrying
> its own commits, and on installer failure targets the last build that
> installed cleanly for recovery. That recovery is best-effort — the
> rollback's own restore and reinstall are not yet verified before
> `rolled_back` is reported, which is a known honesty gap in
> `self_update.apply_update`, not a guarantee.
>
> The [[marketing-site]] offers exactly one of them through a single
> constant, `site/src/data/release.ts` — version, tag, DMG URL, size,
> notes and checksums links, plus an `unsigned: true` flag. That flag is
> what drives the honest Gatekeeper copy on the install section: "This
> build is not notarized yet", with a pointer to `SHA256SUMS` and "A
> signed release is on the way." This is consistent with the signing
> policy above: `config/macos-signing-policy.json` still records no team
> id, so packaging cannot sign, and the site says so rather than hiding
> it.

## Related Concepts

- [[installers-and-services]] — what an update actually runs
- [[governance]] — gates, source offers, licensing of artifacts
- [[marketing-site]] — where the download is offered
- [[privacy-and-security]] — signing and provenance invariants

## References

- self_update.py; scripts/ (advisor, side-by-side, manifest, packaging,
  verify); docs/distribution/
- [[2026-07-26-ops-governance-research]]
