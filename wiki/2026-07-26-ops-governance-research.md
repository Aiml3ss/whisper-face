---
title: "Ops & Governance Research Notes"
type: article
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [research, operations, installers, governance]
summary: "Imported research notes on installers, services, updates, packaging, the native helper, the site, the benchmark family, tests, and governance."
source_hash: "2fd5bd58eb40e913f9bafb55022e7ef6f6ca602288cbdf1eff01da485b7becf4"
status: published
---

# Ops & Governance Research Notes

## Summary

The operational half of the product: idempotent installers whose
invariant is "generated services are replaced, private user files
survive"; a launcher app that gives macOS one grantable permission
identity; two deliberately separate update paths; reproducible-tree
packaging; a benchmark family with no runtime authority; and a
governance stack of AGPL + commercial + preserved MIT, a
base-branch-enforced CLA ledger, and a ~58-command release-gate list.

## Content

The full brief lives at `.raw/ops-governance-research.md`. Compiled
into: [[installers-and-services]], [[distribution]], [[benchmarks]],
[[governance]], [[privacy-and-security]], [[marketing-site]],
[[windows-support]], [[asr-cascade]].

## Key Takeaways

- The CLA check reads the ledger from the PR's base SHA — a contributor
  branch can never supply its own acceptance.
- Signing is disabled until the owner records a real Team ID in the
  pinned signing policy.
- The CLA check runs on the self-hosted bergserver runner because
  hosted jobs stop when the Actions budget is spent.
- Known gate-list drift exists between AGENTS.md, the installer doc,
  and the release workflow.

## Related

- [[whisper-face]] — the hub
- [[activation-receipt]] — evidence discipline, ops edition
