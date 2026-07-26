---
title: "Governance"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [licensing, cla, ci, release-gates, policy]
aliases: [licensing, cla-ledger, release-gates, ci-workflows]
summary: "AGPL-3.0-only with a separate commercial path and a preserved MIT history; a base-branch-enforced CLA ledger; owner-gated governance files; and a ~58-command release-gate list that installers must pass."
confidence: high
---

# Governance

## Definition

The project's legal and process backbone: a three-layer license
structure, a cryptographically pinned CLA ledger enforced in CI, owner
review on governance files, and a release process in which installer
parity is a hard gate.

## Key Properties

- **Licensing**: current first-party source is AGPL-3.0-only; a
  separate written commercial license can be signed for proprietary
  distribution, OEM, embedding, or hosted use (the document itself
  grants nothing); snapshots through commit `8f317df7` remain MIT and
  are not revoked. No trademark rights to the name or character
  artwork. The app and the network endpoint both expose the exact
  source offer (`/source`) and notices (`/license`), and a governance
  test pins the license hashes and the MIT boundary commit.
- **CLA**: contributors retain copyright and grant a broad relicensing
  and patent license; acceptance must be an affirmative recorded
  statement. The ledger pins the CLA's SHA-256 and the owner's
  immutable numeric GitHub id; the CI check reads the ledger from the
  PR's *base* SHA so a contributor branch can never supply its own;
  bootstrap is restricted to the owner; CODEOWNERS requires owner
  review on all governance files and workflows.
- **Release gates**: AGENTS.md step 5 lists ~58 `uv run` test commands
  (duplicated in the installer release process doc and the macOS
  release workflow), always ending with the live platform verify.
  Known drift: the characters test appears only in AGENTS.md; the
  release workflow omits two acoustic tests; a few tests appear in no
  gate list.
- **CI workflows**: cla-check (the only *required* status check; runs
  on the self-hosted bergserver runner since commit `8fb7ed5` because
  GitHub-hosted jobs stop starting when the Actions budget is spent),
  windows-smoke (hosted, non-required), macos-release (full gates,
  pristine-worktree assertion, credentials confined to the package job,
  SHA-pinned actions, isolated publish), and two weekly evidence jobs
  (model audit, performance lifecycle) that upload artifacts before
  propagating failure. There is deliberately no site-deploy workflow
  ([[marketing-site]]).
- **Support covenant**: core accuracy, privacy, accessibility,
  correction, export, deletion, and recovery are never
  Supporter-only; the pilot accepts no payment and counts only stated
  interest.

## Related Concepts

- [[privacy-and-security]] — the commitments the process protects
- [[distribution]] — the release pipeline the gates guard
- [[installers-and-services]] — installer parity as a gate

## References

- LICENSE, LICENSE_POLICY.md, COMMERCIAL_LICENSE.md, CLA.md,
  .github/cla-signatures-v1.json, CODEOWNERS, AGENTS.md,
  docs/licensing-release-process.md, .github/workflows/
- [[2026-07-26-ops-governance-research]]
