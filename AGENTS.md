# Repository instructions

## Installer parity is a release gate

Whisper Face must remain reproducible on a fresh Mac or Windows machine
through `Install.command` or `Install.cmd`. Treat installer parity as part of
every runtime change, regardless of which agent or contributor makes it.

For any change to runtime behavior, Python dependencies, models, model options,
assets, templates, permissions, services, platform support, or startup
arguments:

1. Audit `setup.sh`, `setup.ps1`, both clickable launchers, service templates,
   `dictate.py.lock`, private-state templates, installer tests, and install
   documentation for corresponding changes.
2. Update every affected Mac and Windows surface in the same change. Do not
   embed or copy runtime source into an installer; both installers must execute
   the current checkout as their single source of truth.
3. If the runtime change requires no installer edit, state why in the handoff.
   Valid examples include an internal implementation change when both
   installers already launch the current `dictate.py`, or a model-loading
   optimization that preserves model identifiers and dependencies.
4. Regenerate `dictate.py.lock` whenever the PEP 723 dependency block changes.
5. Run the repository release gates before declaring completion:

   ```sh
   uv lock --check --script dictate.py
   uv run tests/test_parrot_core.py
   uv run tests/test_voice_compiler.py
   uv run tests/test_benchmark_voice_compiler.py
   uv run tests/test_benchmark_asr.py
   uv run tests/test_dictate.py
   uv run tests/test_insertion_integrity.py
   uv run tests/test_personal_regression.py
   uv run tests/test_whisper_face_gui.py
   uv run tests/test_installers.py
   uv run tests/test_repository_governance.py
   ```

6. On an installed Mac, also run `./setup.sh --verify`. On Windows, run
   `.\setup.ps1 --verify`. If the current environment cannot run a platform
   verification, say so explicitly; static tests are not a substitute for
   claiming that platform was verified live.
7. Before claiming another machine can install the latest build, confirm the
   relevant commit is available from the distribution branch. Never describe
   an unpushed local commit as available to a fresh clone.

The complete decision table and definition of done are in
`docs/installer-release-process.md`.

## Licensing and contribution rights are a merge gate

Current first-party work is `AGPL-3.0-only` with a separately negotiated
commercial/OEM path. Historical repository snapshots through
`8f317df7ac5bb687ac8fbbfcd23abc1385be396d` remain under their original MIT
grant. Do not rewrite, remove, or describe that grant as revoked.

Before merging an outside contribution, require a repository-controlled ledger
entry with the contributor's immutable GitHub user ID accepting `CLA.md`; a
mutable pull-request body or commit metadata alone is not enough. New
dependencies, models, copied or generated assets, and
third-party code must have their provenance and license recorded in
`THIRD_PARTY_NOTICES.md`. Changes to `LICENSE`, `LICENSE_POLICY.md`, `CLA.md`,
the CLA acceptance ledger, commercial-license policy, or historical transition
boundary require explicit Project Owner approval.

The complete decision table is in `docs/licensing-release-process.md`.

## Agent skills

### Issue tracker

Issues and PRDs live in this repository's GitHub Issues. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the standard `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, and `wontfix` labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository: read root `CONTEXT.md` and relevant ADRs
under `docs/adr/` before architecture work. See `docs/agents/domain.md`.
