# Repository instructions

## Installer parity is a release gate

Whispering Parrot must remain reproducible on a fresh Mac or Windows machine
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
   uv run tests/test_installers.py
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
