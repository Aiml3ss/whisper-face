# Contributing to Whisper Face

Whisper Face welcomes focused bug fixes, tests, documentation, accessibility
work, performance improvements, and measured accuracy improvements.

## Before opening a pull request

1. Read [CONTEXT.md](CONTEXT.md) and the relevant records in
   [docs/adr](docs/adr).
2. Keep the Mac experience primary while preserving Windows installer parity.
3. Do not include private transcripts, audio, credentials, personal
   dictionaries, model caches, or generated service files.
4. Keep installers in scope for every runtime change. Audit the Mac and Windows
   setup scripts, clickable launchers, service templates, private-state
   templates, lockfile, installer tests, and install documentation as
   applicable. Both installers must execute the current checkout rather than a
   copied runtime.
5. Disclose all third-party code, assets, model material, and generated output
   included in the contribution.

## Contributor agreement

Outside contributions require the [Whisper Face CLA](CLA.md) because the
Project is offered under both an AGPL community license and separately
negotiated commercial licenses. You keep your copyright; the CLA grants the
Project Owner the rights needed to maintain both paths.

Begin acceptance by checking its box in the pull-request template and leaving
this exact statement in the pull-request description:

> I have read and agree to the Whisper Face Contributor License Agreement version 1.0.

The Project Owner then records Your GitHub login and immutable numeric user ID,
CLA version and checksum, date, and a reference to the affirmative acceptance in the protected
`.github/cla-signatures-v1.json` ledger. The required GitHub check reads only
the ledger from the pull request's base revision, so a contributor cannot add
their own unreviewed acceptance. A first-time contributor's code must not be
merged until the Project Owner lands that record separately.

If You contribute for a company, confirm that You have authority to make the
grant or arrange a separate entity agreement before the pull request is
merged. The Project Owner is not required to sign a CLA with themself.

## Verification

Run the focused test for the area you changed. For example, the Voice Input
Protocol conformance contract uses:

```sh
uv run tests/test_voice_input_protocol.py
```

For a distributable change, run the complete, canonical command list in the
[installer release process](docs/installer-release-process.md). That file is
the single source of truth so release commands do not drift across documents.
Also run the live verification available on your platform:

```sh
./setup.sh --verify
```

```powershell
.\setup.ps1 --verify
```

State unavailable platform verification plainly. Before telling someone on
another machine to install a build, confirm the relevant commit is published
on the distribution branch.

## Pull-request scope

Prefer one independently testable change per pull request. Explain user impact,
installer impact on both platforms (or why no installer file changes were
needed), privacy or security implications, and the commands used for
verification. New runtime behavior should include regression tests. New
dependencies or model assets must include provenance and license information.

By submitting a Contribution, You also agree that the contribution itself is
distributed under `AGPL-3.0-only`, in addition to the rights granted by the
CLA.
