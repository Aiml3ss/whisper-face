## Summary

<!-- What changed and why? -->

## Grant of Copyright and Patent Rights

Read the [Whisper Face Contributor License Agreement](https://github.com/Aiml3ss/whispering-parrot/blob/main/CLA.md)
before submitting outside contributions.

- [ ] I own this contribution or have authority to submit it, and I disclosed
      any third-party material and restrictions.
- [ ] I have read and agree to the Whisper Face Contributor License Agreement version 1.0.

> I have read and agree to the Whisper Face Contributor License Agreement
> version 1.0.

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
- [ ] `uv run tests/test_consequence_routing.py`
- [ ] `uv run tests/test_cleanup_circuit_breaker.py`
- [ ] `uv run tests/test_benchmark_voice_compiler.py`
- [ ] `uv run tests/test_benchmark_consequence_routing.py`
- [ ] `uv run tests/test_benchmark_asr.py`
- [ ] `uv run tests/test_performance_lab.py`
- [ ] `uv run tests/test_dictate.py`
- [ ] `uv run tests/test_gui_settings_runtime.py`
- [ ] `uv run tests/test_insertion_integrity.py`
- [ ] `uv run tests/test_benchmark_insertion_reliability.py`
- [ ] `uv run tests/test_compatibility_fingerprint.py`
- [ ] `uv run tests/test_voice_input_protocol.py`
- [ ] `uv run tests/test_acoustic_keyword_memory.py`
- [ ] `uv run tests/test_acoustic_calibration.py`
- [ ] `uv run tests/test_benchmark_acoustic_calibration.py`
- [ ] `uv run tests/test_delayed_cleanup_merge.py`
- [ ] `uv run tests/test_model_wallet.py`
- [ ] `uv run tests/test_model_wallet_shadow.py`
- [ ] `uv run tests/test_point_and_speak_resolver.py`
- [ ] `uv run tests/test_drop_to_target.py`
- [ ] `uv run tests/test_voice_objects.py`
- [ ] `uv run tests/test_voice_inbox.py`
- [ ] `uv run tests/test_demonstration_drafts.py`
- [ ] `uv run tests/test_competitor_benchmark.py`
- [ ] `uv run tests/test_public_scorecard.py`
- [ ] `uv run tests/test_personal_regression.py`
- [ ] `uv run tests/test_whisper_face_gui.py`
- [ ] `uv run --locked --script dictate.py --native-gui-smoke-test`
- [ ] `uv run tests/test_installers.py`
- [ ] `uv run tests/test_repository_governance.py`
- [ ] `uv run tests/test_macos_distribution.py`
- [ ] Live Mac or Windows installer verification was run, or the unavailable
      platform is explicitly disclosed.
