## Summary

<!-- What changed and why? -->

## Installer parity

- [ ] I classified the Mac and Windows installer impact using
      `docs/installer-release-process.md`.
- [ ] I updated every affected installer, service template, lockfile, asset,
      default, test, and instruction—or explained why no installer edit is
      required.
- [ ] I did not duplicate runtime source inside an installer.
- [ ] Private state remains preserved on reinstall.

## Verification

- [ ] `uv lock --check --script dictate.py`
- [ ] `uv run tests/test_parrot_core.py`
- [ ] `uv run tests/test_voice_compiler.py`
- [ ] `uv run tests/test_benchmark_voice_compiler.py`
- [ ] `uv run tests/test_benchmark_asr.py`
- [ ] `uv run tests/test_dictate.py`
- [ ] `uv run tests/test_installers.py`
- [ ] Live Mac or Windows installer verification was run, or the unavailable
      platform is explicitly disclosed.
