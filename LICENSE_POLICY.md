# Whisper Face licensing policy

Whisper Face uses a dual-licensing model for first-party code. The current
source tree is available under either:

1. **GNU Affero General Public License v3.0 only (`AGPL-3.0-only`)** under the
   terms in [LICENSE](LICENSE); or
2. **a separate written commercial license** from the Project Owner for uses
   that need different terms, including proprietary redistribution, embedding,
   OEM distribution, or a network service that cannot meet the AGPL's source
   obligations.

The commercial path is an alternative license, not an exception silently
attached to the AGPL. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for
the scope and contact path.

## Transition from MIT

Repository snapshots through and including commit
`8f317df7ac5bb687ac8fbbfcd23abc1385be396d` were distributed under the MIT
License found in those snapshots. Those grants remain valid and are not
revoked. A copy of that historical license and its scope note are preserved in
the [LICENSES directory](LICENSES/README.md).

The transition release—the commit that introduces this policy—and later
release snapshots are offered under `AGPL-3.0-only` or a separately signed
commercial license. Code received from a historical snapshot retains its MIT
rights; later snapshots do not require line-by-line license archaeology.
Checking out an earlier commit gives you the license and code that applied to
that historical snapshot, not later improvements.

Some foundations, including the first published Voice Compiler version, are
present in historical MIT snapshots. The transition protects later
improvements and newly released components; it does not reclaim exclusive
rights in code already distributed under MIT.

## What the policy covers

Unless a file or directory carries a different notice, `AGPL-3.0-only` applies
to first-party source code, tests, documentation, templates, and artwork in
the current tree. This policy does not relicense third-party software, model
weights, fonts, or other material. Those items remain governed by their own
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

A commercial license can grant only rights controlled by its licensor. It
does not override third-party obligations.

## Contributions and relicensing

Contributors keep copyright in their work. To preserve both the public AGPL
edition and the commercial/OEM option, outside contributions require
acceptance of the [Whisper Face CLA](CLA.md). The CLA grants the Project Owner
the copyright and patent permissions needed to distribute contributions under
the project's open-source and commercial licenses. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Trademarks

The software licenses do not grant rights to use the Whisper Face name, logos,
or character artwork as trademarks or to imply endorsement. A separate
trademark policy can be added before broad binary distribution.

This file documents the repository's licensing choices and is not legal
advice. Organizations relying on these choices should have counsel review
their intended use.
