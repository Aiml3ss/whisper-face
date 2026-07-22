# Update and rollback

Whisper Face currently updates from its source checkout; it does not have an
automatic updater. Keep the checkout you installed from, and make a backup of
your private files before changing versions.

## Before an update

1. Quit Whisper Face from its menu bar or tray menu.
2. Record the version you are leaving:

   ```sh
   git rev-parse HEAD
   ```

3. Back up these private, gitignored files if they exist:
   `snippets.json`, `tones.json`, `preferences.json`,
   `acoustic_keyword_memory.json`, `dictionary.txt`, `transcripts.jsonl`,
   `learned.json`, `voice_inbox.json`, `demonstrations.json`, and
   `dictate.log`.

Do not publish that backup: it can contain personal vocabulary, corrections,
transcripts, local drafts, demonstration recipes, and logs.

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
