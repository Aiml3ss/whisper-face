# Installer release process

This is the required distribution process for every Whispering Parrot change.
Its purpose is to keep the one-click setup identical to the working development
machine without maintaining a second copy of the application.

## Single source of truth

`dictate.py`, `parrot_core.py`, the PEP 723 dependency block, and
`dictate.py.lock` are the runtime source of truth. The Mac LaunchAgent and
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
uv run tests/test_dictate.py
uv run tests/test_installers.py
```

Run the live verification available on the current platform:

```sh
./setup.sh --verify
```

```powershell
.\setup.ps1 --verify
```

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

## Handoff format

Every implementation handoff should include a short installer-parity line:

> Installer parity: updated `<files>` because `<reason>`; or no installer file
> change required because both installers already execute the changed runtime
> directly. Gates: `<commands/results>`. Distribution: `<local only/published>`.
