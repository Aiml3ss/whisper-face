---
title: "Distribution"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
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

## Related Concepts

- [[installers-and-services]] — what an update actually runs
- [[governance]] — gates, source offers, licensing of artifacts
- [[privacy-and-security]] — signing and provenance invariants

## References

- self_update.py; scripts/ (advisor, side-by-side, manifest, packaging,
  verify); docs/distribution/
- [[2026-07-26-ops-governance-research]]
