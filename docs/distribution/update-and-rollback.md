# Update and rollback

Whisper Face never updates itself in the background. On a Mac, the menu's
**Check for Updates…** is the update most people use; the rest of this guide
covers updating by hand and the release-operator tools. Keep the checkout you
installed from, and make a backup of your private files before changing
versions.

## Updating the app from the disk image

A copy set up by the Whisper Face app follows published releases, never git:
`self_update.py` refuses its checkout
(`~/Library/Application Support/Whisper Face/checkout`) and any checkout its
receipt binds to the app, and rerunning `Install.command` there stays in app
mode. It updates in one of two ways, and both end the same way.

- **Drag the newer app over the old one**, then open it. It sees that the
  install is an older release and offers **Update**.
- **Check for Updates…** reads the latest published release's
  `update-manifest.json` (one HTTPS request to the project's GitHub releases)
  and offers **Download and Install** only for a strictly newer release on
  the app's own channel (a preview app follows preview releases, a stable app
  stable ones). Nothing is downloaded until you click. Then
  `scripts/app_update.py` downloads that release's disk image and source
  archive, verifies them as `safe_update_advisor.py` verifies any release
  (sizes and SHA-256 against the manifest, channel, versions, and for a
  stable release `codesign` and `stapler`), copies the app off the read-only
  image, proves the copy with that release's own launcher contract and its
  sealed source archive (and for a stable release, the Developer ID this
  install pins), puts it where the old app was and opens it, which starts
  the update at once.

The update itself: the app checks its own archive against the digest sealed
into it, unpacks it beside the install as `checkout.incoming`, and runs that
copy's `scripts/app_update.py apply`. That applies the advisor's rule again
(only a strictly newer version on another revision), keeps copies of the
login service's definition and the receipt, stops the service, renames
`checkout` to `checkout.previous` and the new copy to `checkout`, and runs
the new copy's `setup.sh --app … --carry-over-from checkout.previous`. Before
anything else, setup copies every personal file across from the one list
`setup.sh` keeps (`personal_files`, the same list as the backup list below
and the uninstaller's personal tier): dictionary, snippets, tones,
preferences, transcripts, learned data, the voice outbox, demonstrations,
the acoustic and activation files, the phone token, and the logs. Models
live outside the checkout (the Parakeet model in
`~/Library/Application Support/FluidAudio`, Whisper in the Hugging Face
cache) and are shared, so nothing is downloaded again for them.

If setup fails, the new copy goes back to `checkout.incoming` (kept, with
what it downloaded, for **Try Again**), the old copy back to `checkout`
with its own files untouched, the two saved files back in place, and the old
service is started; the window says the update did not finish and that the
old version is running again only once launchd shows it running. If that
cannot be shown, the window says so, and the next **Try Again** finishes
putting things back before it updates; a journal in
`~/Library/Application Support/Whisper Face/update-in-progress.json` makes
that work after a crash or power loss too. Each outcome is one line in
`update.log`. After a successful update the replaced copy stays in
`checkout.previous` until the next update; uninstalling lists it, and the
newer copy a failed update keeps, with the personal files they hold copies
of.

An app older than the install refuses ("A newer Whisper Face is set up") and
says to download the latest and drag it into Applications; one with the same
version but other contents refuses too. Neither changes anything. To go back
a version, the previous copy is in `checkout.previous`; no button does it
yet.

**Permissions after an update.** Until the app is signed with a Developer ID,
every release has a new code identity, so macOS keeps Whisper Face listed
under Accessibility and Input Monitoring but stops honouring the grant.
After the update the runtime waits for permissions and its permission window
opens by itself, with buttons for the exact Settings pages and **Still Not
Working…** to clear Whisper Face's two entries. With a Developer ID build the
identity is the bundle identifier plus the Team ID, which stay the same from
release to release, so the grants are expected to survive an update; that is
unverified until a signed build exists.

## Check for Updates… for a folder or clone install

For a folder or clone install, when you choose it, `self_update.py`:

1. refuses if the checkout has local changes or commits its upstream lacks
   (a disk-image install built from the November 2026 fixes onward passes
   this check; earlier disk-image installs always report local changes);
2. runs one `git fetch` of the checkout's tracked branch (normally `main` on
   GitHub);
3. if that branch has moved, checks out its tip and reruns the installer;
4. if the installer fails, restores the last revision the installer
   provisioned cleanly, reinstalls it, and reports `rolled_back` only once
   that is verified. The reason it reports is the installer's last `!!` line,
   and outcomes are appended to
   `~/Library/Application Support/Whisper Face/update.log`.

It installs whatever is at the tip of the branch. There is no release-tag,
signature, or provenance check, so you receive unreleased commits exactly as
a fresh `git clone` would. Rollback restores code, not private state. See the
[threat model](../security/threat-model.md) for this as an open risk.

## Before an update

1. Quit Whisper Face from its menu bar or tray menu.
2. Record the version you are leaving:

   ```sh
   git rev-parse HEAD
   ```

3. Back up these private, gitignored files if they exist:
   `snippets.json`, `tones.json`, `preferences.json`,
   `acoustic_keyword_memory.json`, `acoustic_keyword_activation.json`,
   `acoustic_calibration_activation.json`, `relisten_activation.json`,
   `delayed_cleanup_activation.json`, `dictionary.txt`, `transcripts.jsonl`,
   `learned.json`,
   `voice_inbox.json`, `demonstrations.json`, `dictate.log`, and
   `install.log`. A `--server-only` install also has `phone_token.txt`, the
   bearer token its endpoint checks.

Do not publish that backup: it can contain personal vocabulary, corrections,
transcripts, local drafts, demonstration recipes, logs, and the endpoint
token, which is a password.

An in-place update leaves `phone_token.txt` where it is, so a paired phone
client keeps working. Moving to a different folder does not carry it: copy it
across, or give the client the new token the first start creates there.

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
is current, rerun the same installer. Reinstalling in the same
checkout preserves existing private files.

- **Mac:** double-click `Install.command`, then run `./setup.sh --verify`.
- **Windows:** double-click `Install.cmd`, then run
  `.\setup.ps1 --verify` in PowerShell (or `Install.cmd --verify` from a
  Command Prompt).

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

## Uninstall

Leaving is the same shape as rolling back: look first, then decide. Both
installers take `--uninstall`, which prints a plan and changes nothing:

```sh
./setup.sh --uninstall              # macOS
.\setup.ps1 --uninstall             # Windows
```

The plan lists, with full paths, every service, file, and directory the
installer created. Removal happens only when you add `--yes`, and each tier
beyond the software itself needs its own flag:

```sh
./setup.sh --uninstall --yes                          # services, launcher, receipts
./setup.sh --uninstall --yes --remove-models          # and the downloaded models
./setup.sh --uninstall --yes --remove-personal-data   # and your private files
```

```powershell
.\setup.ps1 --uninstall --yes
.\setup.ps1 --uninstall --yes --remove-models
.\setup.ps1 --uninstall --yes --remove-personal-data
```

What each tier covers:

| Tier | macOS | Windows |
|---|---|---|
| Software (with `--yes`) | `launchctl bootout` of `com.berg.dictate` and `com.berg.ollama`, both LaunchAgent plists, `~/Applications/Whisper Face.app`, the `Application Support/Whisper Face` receipts, `.models/bin`, `.models/swift-build`, `.models/swift-build-runtime`, `~/Library/Caches/parrot-speech-runtime`, `.dictate.lock` | the `Whisper Face` scheduled task and its legacy `Whispering Parrot` name, `.windows/launch.ps1`, `.windows/launch.sha256`, the `.windows` directory, `.dictate.lock` |
| Models (`--remove-models`) | the Parakeet Unified Core ML model, both pinned `mlx-community/whisper-*` snapshots in the Hugging Face cache, and `ollama rm qwen3.5:4b` | the `.models` faster-Whisper cache and `ollama rm qwen3.5:4b` |
| Personal (`--remove-personal-data`) | `snippets.json`, `tones.json`, `preferences.json`, `acoustic_keyword_memory.json`, `acoustic_keyword_activation.json`, `acoustic_calibration_activation.json`, `relisten_activation.json`, `delayed_cleanup_activation.json`, `dictionary.txt`, `transcripts.jsonl`, `learned.json`, `voice_inbox.json`, `demonstrations.json`, `phone_token.txt`, `dictate.log`, `ollama.log`, `install.log` | the same, plus `ollama-error.log` |

The personal tier is exactly the backup list at the top of this document, and
for the same reason: no download restores those files. They are kept unless
you ask for them by name, and the closing summary says where they were left.

The uninstaller removes only paths it can name, never a pattern. Homebrew,
`uv`, `ffmpeg`, Ollama itself, Ollama's own model store, the `uv` cache, the
Swift toolchain, and every other model in the shared Hugging Face cache are
left alone; the run prints that list too. Running it twice is safe — the
second run reports everything as already gone and still succeeds.

Two steps stay manual on purpose. The checkout is not deleted, so remove that
folder yourself once you no longer want the source. And macOS privacy grants
live in a system database an installer must not edit, so remove **Whisper
Face** by hand under System Settings → Privacy & Security → **Input
Monitoring**, **Accessibility**, and **Microphone**.
