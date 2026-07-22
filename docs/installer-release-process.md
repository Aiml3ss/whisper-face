# Installer release process

This is the required distribution process for every Whisper Face change.
Its purpose is to keep the one-click setup identical to the working development
machine without maintaining a second copy of the application.

## Single source of truth

`dictate.py`, `parrot_core.py`, `voice_compiler.py`,
`insertion_integrity.py`, `personal_regression.py`, `whisper_face_gui.py`,
the PEP 723 dependency block, and `dictate.py.lock` are the runtime source of
truth. The Mac LaunchAgent and
Windows scheduled task execute those files from the checkout. Installers may
provision and verify the runtime, but must never embed a frozen copy of it.

## Change classification

| Changed area | Required installer review |
|---|---|
| Python dependency or platform marker | Update PEP 723 metadata, regenerate `dictate.py.lock`, and verify both platform environments. |
| Whisper or Ollama model identifier | Update runtime constants, download/preload checks, verification, disk estimates, README, and installer tests. |
| Model loading or inference options | Confirm preload exercises the new path and service templates still supply required environment options. |
| Runtime file or required asset | Add it to the required-file checks in `setup.sh` and `setup.ps1`; update packaging documentation. |
| Startup argument, port, service, permission, or health behavior | Update both platform service definitions, readiness checks, verification, and user guidance. |
| Private state or default preference | Update its template and preservation/permission logic on both platforms. Never overwrite an existing user's state. |
| Internal runtime-only implementation | Installer edits may be unnecessary because services run the current checkout. Record that conclusion and still run every release gate. |

## Required gates

Run on every change:

```sh
uv lock --check --script dictate.py
uv run tests/test_parrot_core.py
uv run tests/test_voice_compiler.py
uv run tests/test_consequence_routing.py
uv run tests/test_process_verifier.py
uv run tests/test_whisper_verifier_adapter.py
uv run tests/test_benchmark_voice_compiler.py
uv run tests/test_benchmark_consequence_routing.py
uv run tests/test_benchmark_asr.py
uv run tests/test_performance_lab.py
uv run tests/test_dictate.py
uv run tests/test_gui_settings_runtime.py
uv run tests/test_insertion_integrity.py
uv run tests/test_benchmark_insertion_reliability.py
uv run tests/test_compatibility_fingerprint.py
uv run tests/test_voice_input_protocol.py
uv run tests/test_voice_input_protocol_wire.py
uv run tests/test_acoustic_keyword_memory.py
uv run tests/test_delayed_cleanup_merge.py
uv run tests/test_model_wallet.py
uv run tests/test_point_and_speak_resolver.py
uv run tests/test_drop_to_target.py
uv run tests/test_voice_objects.py
uv run tests/test_voice_inbox.py
uv run tests/test_competitor_benchmark.py
uv run tests/test_public_scorecard.py
uv run tests/test_personal_regression.py
uv run tests/test_whisper_face_gui.py
uv run --locked --script dictate.py --native-gui-smoke-test
uv run tests/test_installers.py
uv run tests/test_repository_governance.py
uv run tests/test_macos_distribution.py
```

Run the live verification available on the current platform:

```sh
./setup.sh --verify
```

```powershell
.\setup.ps1 --verify
```

The native GUI smoke command is a macOS-only gate. It constructs and tears
down the AppKit window without showing or activating it, querying permissions,
loading user files, starting runtime services, or persisting defaults.
`tests/test_whisper_face_gui.py` validates the same static contract on Windows;
Windows setup must never execute AppKit.
Headless Mac endpoints use `./setup.sh --server-only --verify`; that mode skips
the AppKit construction check while still verifying the locked runtime,
models, services, and health endpoint. Release CI always runs the separately
bounded native GUI smoke on a windowed macOS runner.

For dependency, service, model, preload, or permission changes, also rerun the
full one-click installer on an appropriate clean or disposable machine before
calling the release fully cross-platform verified.

## Definition of done

A change is distributable only when all of the following are true:

- Mac and Windows impact has been classified.
- Every affected installer, service, template, lockfile, test, and instruction
  is updated in the same commit series.
- Private files remain preserved on a reinstall.
- Static release gates pass.
- Available live platform verification passes and unavailable verification is
  disclosed.
- The service has been restarted when validating a local runtime change.
- The distribution branch contains the commits before another machine is told
  to clone or install them.
- Mac release artifacts pass `tests/test_macos_distribution.py`, identify one
  full source revision, and follow `docs/distribution/macos-release.md`.

## Handoff format

Every implementation handoff should include a short installer-parity line:

> Installer parity: updated `<files>` because `<reason>`; or no installer file
> change required because both installers already execute the changed runtime
> directly. Gates: `<commands/results>`. Distribution: `<local only/published>`.
