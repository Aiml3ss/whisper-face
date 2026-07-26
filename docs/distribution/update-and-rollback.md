# Update and rollback

Whisper Face has no automatic updater. Keep the checkout you installed from,
and make a backup of your private files before changing versions.

## Before an update

1. Quit Whisper Face from its menu bar or tray menu.
2. Record the version you are leaving:

   ```sh
   git rev-parse HEAD
   ```

3. Back up these private, gitignored files if they exist:
   `snippets.json`, `tones.json`, `preferences.json`,
   `acoustic_keyword_memory.json`, `acoustic_keyword_activation.json`,
   `acoustic_calibration_activation.json`, `dictionary.txt`, `transcripts.jsonl`,
   `learned.json`, `voice_inbox.json`, `demonstrations.json`, and
   `dictate.log`.

Do not publish that backup: it can contain personal vocabulary, corrections,
transcripts, local drafts, demonstration recipes, and logs.

## Read-only update advisor

Before changing a checkout, a release operator can inspect an already local
manifest and artifact directory:

```sh
python3 scripts/safe_update_advisor.py \
  --current-version 1.2.2 \
  --current-revision "$(git rev-parse HEAD)" \
  --manifest /path/to/release/update-manifest.json \
  --artifact-dir /path/to/release \
  --channel stable
```

The command emits one fixed JSON receipt with `up-to-date`, `upgrade`,
`rollback`, or `refuse`, the current and proposed public version/revision, and
an all-false effects map. It does not access the network, download, fetch,
switch a checkout, overwrite source, install, or change `launchd`. A preview
manifest is accepted only with explicit `--channel preview` and never reports
production trust.

Every plan verifies the manifest against the local artifact sizes and SHA-256
digests, requires exactly one source archive and one disk image, checks strict
SemVer/channel/revision relationships, and refuses ambiguous same-version or
same-revision releases. A stable plan additionally requires the manifest's
signed/notarized claims and live local `codesign --verify` plus
`xcrun stapler validate`; missing Apple tools or invalid trust refuses the plan.
This is advice only, not an updater or authorization to install.

A rollback requires both the current release and its linked older release to
be present and locally verifiable:

```sh
python3 scripts/safe_update_advisor.py \
  --intent rollback \
  --current-version 1.2.3 \
  --current-revision FULL_CURRENT_REVISION \
  --manifest /path/to/current/update-manifest.json \
  --artifact-dir /path/to/current \
  --rollback-manifest /path/to/older/update-manifest.json \
  --rollback-artifact-dir /path/to/older \
  --channel stable
```

The current manifest must describe the installed version and exact revision;
its HTTPS rollback linkage must identify the locally verified older manifest,
whose version must be lower. The advisor never follows the URL or performs the
rollback.

## Explicit side-by-side Mac update

When a newer source checkout has already been prepared beside the current one,
the local helper can validate it without downloading, fetching, switching Git
branches, resetting source, overwriting either checkout, or changing services:

```sh
python3 scripts/side_by_side_update.py \
  --current-checkout /path/to/Whisper-Face-current \
  --candidate-checkout /path/to/Whisper-Face-candidate \
  --current-version 1.2.2 \
  --current-manifest /path/to/current/update-manifest.json \
  --current-artifact-dir /path/to/current \
  --manifest /path/to/candidate/update-manifest.json \
  --artifact-dir /path/to/candidate \
  --channel preview
```

Without all four manifest/artifact arguments, this is a non-authorizing
`review-local-candidate` dry run. With them, it produces an
`apply-side-by-side` dry run. Both directories must be distinct clean sibling
Git checkouts with different full revisions; the candidate must contain the
current installer/runtime contract. The receipt contains only revisions and
closed effects, not paths or private state. If local release metadata is
available, add both the candidate `--manifest`/`--artifact-dir` and the
current `--current-manifest`/`--current-artifact-dir`. Apply requires all four:
it verifies both artifact sets, requires the current manifest to name the
actual current checkout revision and version, and requires the candidate
manifest's supported rollback link to name that exact current release.

After reviewing an `apply-side-by-side` receipt, rerun the same command with
`--apply`. Only then does it execute the candidate checkout's `./setup.sh` and
its `./setup.sh --verify`, using argument arrays rather than a shell. The
current checkout is never altered and remains the source rollback copy. To
roll back, explicitly rerun that old checkout's `./setup.sh` followed by its
`./setup.sh --verify`. This tool does not copy private state, download a
candidate, or authorize an automatic update.

## Update the current checkout

From the Whisper Face folder, first check for local source edits:

```sh
git status --short
```

Continue only when that command prints nothing. If Git reports local changes,
stop and preserve those changes instead of mixing them with an update. Then
download the current published source:

```sh
git pull --ff-only origin main
```

If Git cannot fast-forward, stop instead of forcing the update. Once the source
is current, rerun the same one-click installer. Reinstalling in the same
checkout preserves existing private files.

- **Mac:** double-click `Install.command`, then run `./setup.sh --verify`.
- **Windows:** double-click `Install.cmd`, then run
  `.\setup.ps1 --verify` in PowerShell.

## Roll back to a known-good revision

Use the full commit recorded before the update, or a commit published by a
Whisper Face release. In the same checkout:

```sh
git fetch origin
git switch --detach <known-good-commit>
```

Then rerun `Install.command` on Mac or `Install.cmd` on Windows and use the
matching verification command above. This changes the installed runtime and
login service back to that checkout revision while retaining its private
files.

Restore the current release later with:

```sh
git switch main
git pull --ff-only origin main
```

Rerun the installer and verification command after switching versions. If an
older runtime rejects private state written by a newer version, restore the
backup made before the update. Never use `git reset --hard` as an update or
rollback shortcut.

If you installed from a downloaded archive instead of a Git checkout, keep the
old extracted folder as the rollback copy. A newly extracted folder has its
own private state; migration between folders is currently manual.
