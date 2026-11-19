#!/usr/bin/env python3
"""Update a drag install of Whisper Face from one published release to the next.

A drag install is the copy the Whisper Face app set up in
``~/Library/Application Support/Whisper Face/checkout``. It follows published
releases, never the tip of git, and it updates in one of two ways, both of
which end here:

* **The person drags a newer app over the old one.** On its next open the
  app sees that the copy its receipt binds is an older release, verifies its
  own source archive, unpacks it beside the old copy as ``checkout.incoming``
  and runs ``apply`` from that unpacked copy.
* **The app offers it.** Check for Updates… runs ``check``: one HTTPS read of
  the latest release's ``update-manifest.json``. When a newer release passes
  the existing update rules, the menu offers it, and only the person's click
  runs ``install``. That downloads the release's disk image and source
  archive, verifies them exactly as ``scripts/safe_update_advisor.py`` and
  ``scripts/verify_macos_package.py`` verify a release, puts the new app where
  the old one is, and opens it with ``--update``, which is the drag case.

``apply`` is a switch with a way back. It keeps copies of the login service's
definition and the receipt, stops the service, renames ``checkout`` to
``checkout.previous`` and ``checkout.incoming`` to ``checkout``, and runs the
new copy's own ``setup.sh --app`` with ``--carry-over-from`` naming the old
copy, so setup.sh copies every personal file across from its one list. If
setup fails, the new copy goes back to ``checkout.incoming`` (kept for Try
Again), the old copy back to ``checkout``, the two files back where they were,
and the old service is started and seen running before ``rolled-back`` is
claimed. Models live outside the checkout and are shared by both releases.
A journal written before the switch lets the next ``apply`` finish putting
things back if this one is interrupted.

The versions follow ``safe_update_advisor.update_decision``: only a strictly
newer release on another revision is an update. An older app never replaces a
newer install.

``adopt-folder`` moves a *folder install* into the app: one that
``Install.command`` set up in a folder the person chose, which is every Mac
install before 0.6.0. Such an install is known by what it wrote: a schema 1
receipt naming its folder, the ``com.berg.dictate`` login service run from
that folder, a launcher it built in ``~/Applications`` and, with the cleanup
model, the ``com.berg.ollama`` service and its load receipt. The app has
already unpacked its own release into ``checkout``. The move keeps copies of
those files and of the launcher, stops the folder's service, and runs the
app's ``setup.sh --app`` with ``--carry-over-from`` naming the folder, so the
personal files come across from the same one list an update uses. That setup
is what replaces the service, retires the launcher (only once it proves to
be one this installer built) and rebinds the receipt to the app. If it fails,
the copies go back, the launcher is put back byte for byte, and the folder's
services are started again as they were. The folder itself is only ever
read, so afterwards it can be deleted; models are shared already.

Nothing here runs ``git``. Every external effect goes through an injected
``runner`` (``subprocess.run``-shaped) and ``fetch`` so the tests use fakes,
a temporary home folder and no network.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_manifest  # noqa: E402
import safe_update_advisor  # noqa: E402

SUPPORT = Path("Library/Application Support/Whisper Face")
RECEIPT = "launcher-install.json"
CHECKOUT = "checkout"
PREVIOUS = "checkout.previous"
INCOMING = "checkout.incoming"
BACKUP = "update-backup"
JOURNAL = "update-in-progress.json"
UPDATE_LOG = "update.log"
AGENT = Path("Library/LaunchAgents/com.berg.dictate.plist")
LABEL = "com.berg.dictate"
BUNDLE_ID = "com.berg.whisper-face.launcher"
APP_NAME = "Whisper Face.app"
METADATA = "RELEASE-METADATA.json"
# What a folder install created outside its folder, as setup.sh names it
# (launcher_app, ollama_plist, ollama_service_receipt), in every release
# from 0.2.0 on.
FOLDER_LAUNCHER = Path("Applications") / APP_NAME
OLLAMA_AGENT = Path("Library/LaunchAgents/com.berg.ollama.plist")
OLLAMA_LABEL = "com.berg.ollama"
OLLAMA_RECEIPT = "ollama-service.sha256"
# The move's copies to put back, and its journal. The app reads the journal's
# name too (scripts/macos_launcher_app.py, adoptJournal).
MOVE_BACKUP = "move-backup"
MOVE_JOURNAL = "move-in-progress.json"

# Where releases are published. The only network addresses this module
# names; tests/test_network_egress.py holds it to them.
RELEASES = f"{release_manifest.DEFAULT_REPOSITORY}/releases"
MANIFEST_URL = f"{RELEASES}/latest/download/update-manifest.json"
DOWNLOAD_PREFIX = f"{RELEASES}/download/"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
FETCH_TIMEOUT = 60.0

EXIT_APPLIED = 0
EXIT_REFUSED = 2
EXIT_ROLLED_BACK = 3
EXIT_ROLLBACK_FAILED = 4

# What a person reads when the rule says no. Keys are the advisor's reasons.
REFUSALS = {
    "downgrade-requires-linked-rollback":
        "this Whisper Face is older than the one already set up; open the "
        "newer Whisper Face instead",
    "same-version-revision-conflict":
        "this Whisper Face has the same version as the one set up but "
        "different contents; download it again",
    "equal-precedence-version-conflict":
        "this Whisper Face has the same version as the one set up but "
        "different contents; download it again",
    "new-version-reuses-current-revision":
        "this Whisper Face claims a new version for the same contents; "
        "download it again",
}

Runner = Callable[..., "subprocess.CompletedProcess"]
Fetch = Callable[[str, Path, int], None]


class UpdateError(RuntimeError):
    """The update cannot go ahead; nothing was changed."""


# ------------------------------------------------------------ the install --

class Layout:
    """Every path the drag install's update touches, under one home folder."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.support = self.home / SUPPORT
        self.receipt = self.support / RECEIPT
        self.checkout = self.support / CHECKOUT
        self.previous = self.support / PREVIOUS
        self.incoming = self.support / INCOMING
        self.backup = self.support / BACKUP
        self.journal = self.support / JOURNAL
        self.log = self.support / UPDATE_LOG
        self.agent = self.home / AGENT
        self.launcher = self.home / FOLDER_LAUNCHER
        self.ollama_agent = self.home / OLLAMA_AGENT
        self.ollama_receipt = self.support / OLLAMA_RECEIPT
        self.move_backup = self.support / MOVE_BACKUP
        self.move_journal = self.support / MOVE_JOURNAL


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"could not read {path.name}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(f"{path.name} is not an object")
    return payload


def release_of(checkout: Path) -> tuple[str, str] | None:
    """(version, revision) a checkout the app unpacked records, or None."""
    try:
        payload = _read_json(checkout / METADATA)
    except UpdateError:
        return None
    version, revision = payload.get("version"), payload.get("source_revision")
    if isinstance(version, str) and isinstance(revision, str):
        return version, revision
    return None


def installed(layout: Layout) -> dict:
    """The drag install the receipt describes: app, checkout and release."""
    receipt = _read_json(layout.receipt)
    if receipt.get("schema_version") != 2 or receipt.get("bundle_id") != BUNDLE_ID:
        raise UpdateError("this copy was not set up by the Whisper Face app")
    for key in ("app", "checkout", "source_revision", "version"):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise UpdateError(f"the install receipt has no {key}")
    try:
        bound = Path(receipt["checkout"]).resolve()
        managed = layout.checkout.resolve()
    except OSError as exc:
        raise UpdateError("the copy of Whisper Face this install runs cannot be found") from exc
    if bound != managed:
        raise UpdateError("the receipt binds a copy outside the app's own folder")
    return receipt


def app_release(app: Path) -> dict:
    """The release a drag-install app carries, from its Info.plist."""
    try:
        info = plistlib.loads((Path(app) / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError) as exc:
        raise UpdateError("the Whisper Face app's Info.plist cannot be read") from exc
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise UpdateError("that is not the Whisper Face app")
    keys = ("CFBundleShortVersionString", "WFSourceRevision", "WFReleaseChannel")
    if not all(isinstance(info.get(key), str) for key in keys):
        raise UpdateError("that Whisper Face app was not built for drag install")
    return {"version": info["CFBundleShortVersionString"],
            "revision": info["WFSourceRevision"],
            "channel": info["WFReleaseChannel"]}


def decide(current_version: str, current_revision: str,
           candidate_version: str, candidate_revision: str) -> tuple[str, str]:
    """The advisor's update rule, with malformed input as a refusal."""
    try:
        return safe_update_advisor.update_decision(
            current_version, current_revision, candidate_version,
            candidate_revision)
    except safe_update_advisor.AdviceError as exc:
        return "refuse", str(exc)


# ------------------------------------------------------------- the service --

def _target(uid: int, label: str = LABEL) -> str:
    return f"gui/{uid}/{label}"


def _launchctl(runner: Runner, *arguments: str) -> "subprocess.CompletedProcess":
    try:
        return runner(["/bin/launchctl", *arguments], capture_output=True,
                      text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(["/bin/launchctl", *arguments], 1, "", "")


def service_loaded(runner: Runner, uid: int, label: str = LABEL) -> bool:
    return _launchctl(runner, "print", _target(uid, label)).returncode == 0


def stop_service(runner: Runner, uid: int, sleep=time.sleep, wait: int = 15,
                 label: str = LABEL) -> bool:
    """Unload the dictation service and wait until launchd lets it go."""
    _launchctl(runner, "bootout", _target(uid, label))
    for _ in range(wait):
        if _launchctl(runner, "print", _target(uid, label)).returncode != 0:
            return True
        sleep(1)
    return _launchctl(runner, "print", _target(uid, label)).returncode != 0


def start_service(runner: Runner, uid: int, plist: Path, sleep=time.sleep,
                  wait: int = 30, label: str = LABEL) -> bool:
    """Load the service from ``plist`` and see it running."""
    _launchctl(runner, "bootstrap", f"gui/{uid}", str(plist))
    _launchctl(runner, "kickstart", _target(uid, label))
    for _ in range(wait):
        state = _launchctl(runner, "print", _target(uid, label))
        if state.returncode == 0 and "state = running" in (state.stdout or ""):
            return True
        sleep(1)
    return False


# ------------------------------------------------------- the switch itself --

def _private_copy(source: Path, destination: Path) -> None:
    """Copy a file beside its destination, then rename it into place."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.",
                                        dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _discard(path: Path) -> None:
    """Remove a folder this update owns, by renaming it out of the way first
    so a half-removed copy is never mistaken for a whole one."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        raise UpdateError(f"{path.name} is not a folder this update made")
    doomed = path.with_name(f".{path.name}.discard-{os.getpid()}")
    os.replace(path, doomed)
    shutil.rmtree(doomed, ignore_errors=True)


def _write_journal(layout: Layout, payload: dict, path: Path | None = None) -> None:
    path = path or layout.journal
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=layout.support)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
    os.chmod(name, 0o600)
    os.replace(name, path)


def _record(layout: Layout, status: str, start: str, end: str,
            reason: str = "", kind: str = "app-update") -> None:
    """One content-free line per outcome in update.log, as the git updater
    keeps for its own."""
    try:
        with layout.log.open("a", encoding="utf-8") as history:
            history.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {kind} "
                          f"{status} {start[:7]}->{end[:7]} {reason}".rstrip()
                          + "\n")
        os.chmod(layout.log, 0o600)
    except OSError:
        pass


def _last_reason(lines: list[str]) -> str:
    banners = ("!! Whisper Face setup failed", "!! Full log:", "!! Attach that file")
    reasons = [line[3:].strip() for line in lines
               if line.startswith("!! ") and not line.startswith(banners)]
    return reasons[-1] if reasons else "setup did not finish"


def restore(layout: Layout, from_revision: str, *, runner: Runner, uid: int,
            say: Callable[[str], None], sleep=time.sleep) -> bool:
    """Put the copy at ``from_revision`` back in place and see it running.

    Revision-aware, so it is right wherever an update stopped: before the
    switch (nothing moves), between the two renames, or after them. The
    newer copy is kept as checkout.incoming for Try Again.
    """
    stop_service(runner, uid, sleep=sleep)
    current = release_of(layout.checkout) if layout.checkout.exists() else None
    if current is None or current[1] != from_revision:
        previous = release_of(layout.previous) if layout.previous.exists() else None
        if previous is None or previous[1] != from_revision:
            say("== the copy to go back to is missing")
            return False
        if layout.checkout.exists():
            _discard(layout.incoming)
            os.replace(layout.checkout, layout.incoming)
        os.replace(layout.previous, layout.checkout)
    for name, destination in (("com.berg.dictate.plist", layout.agent),
                              (RECEIPT, layout.receipt)):
        saved = layout.backup / name
        if saved.is_file():
            _private_copy(saved, destination)
    if not layout.agent.is_file():
        say("== the login service's definition is missing")
        return False
    try:
        agent = plistlib.loads(layout.agent.read_bytes())
    except (OSError, ValueError):
        return False
    if agent.get("WorkingDirectory") != str(layout.checkout):
        say("== the login service does not point at the copy put back")
        return False
    return start_service(runner, uid, layout.agent, sleep=sleep)


def recover_interrupted(layout: Layout, *, runner: Runner, uid: int,
                        say: Callable[[str], None], sleep=time.sleep) -> bool | None:
    """Finish putting back an update that stopped part way, if there is one.

    Returns None when there was nothing to do, else whether it worked.
    """
    if not layout.journal.exists():
        return None
    try:
        journal = _read_json(layout.journal)
        from_revision = str(journal["from_revision"])
    except (UpdateError, KeyError):
        return False
    say("== an earlier update did not finish; putting the copy it replaced back first")
    if not restore(layout, from_revision, runner=runner, uid=uid, say=say,
                   sleep=sleep):
        return False
    layout.journal.unlink(missing_ok=True)
    shutil.rmtree(layout.backup, ignore_errors=True)
    _record(layout, "recovered", str(journal.get("to_revision", "")), from_revision)
    return True


def apply_update(*, app: Path, home: Path | None = None, uid: int | None = None,
                 runner: Runner = subprocess.run, sleep=time.sleep,
                 out: TextIO | None = None, dry_run: bool = False) -> int:
    """Move the drag install forward to the release ``app`` carries.

    Run from that release's unpacked copy, ``checkout.incoming``. Prints
    setup.sh's own lines, so the app's window ticks its checklist, and
    returns EXIT_APPLIED, EXIT_REFUSED (nothing changed), EXIT_ROLLED_BACK
    (the old copy is running again) or EXIT_ROLLBACK_FAILED.
    """
    layout = Layout(home or Path.home())
    uid = os.getuid() if uid is None else uid
    stream = out or sys.stdout

    def say(line: str) -> None:
        print(line, file=stream, flush=True)

    def refuse(reason: str) -> int:
        say(f"!! {reason}")
        return EXIT_REFUSED

    app = Path(app).resolve()
    say("@@ phase prepare")
    if not dry_run:
        recovered = recover_interrupted(layout, runner=runner, uid=uid, say=say,
                                        sleep=sleep)
        if recovered is False:
            say("!! an earlier update could not be undone; Show Log, then Try Again")
            return EXIT_ROLLBACK_FAILED
    try:
        receipt = installed(layout)
        carried = app_release(app)
    except UpdateError as exc:
        return refuse(str(exc))
    unpacked = release_of(layout.incoming)
    if unpacked != (carried["version"], carried["revision"]):
        return refuse("the newer copy beside this install is not the release this app carries")
    if release_of(layout.checkout) is None:
        return refuse("the copy this install runs is missing")
    start, end = receipt["source_revision"], carried["revision"]
    decision, reason = decide(receipt["version"], start, carried["version"], end)
    if decision == "up-to-date":
        say("== already up to date")
        return EXIT_APPLIED
    if decision != "upgrade":
        return refuse(REFUSALS.get(reason, f"this update is refused ({reason})"))
    if not layout.agent.is_file():
        return refuse("the login service is missing; open Whisper Face and Set Up Again")
    say(f"== updating Whisper Face {receipt['version']} to {carried['version']}")
    setup = ["/bin/bash", str(layout.checkout / "setup.sh"), "--app", str(app),
             "--carry-over-from", str(layout.previous)]

    if dry_run:
        say(f"== would keep {layout.agent} and {layout.receipt} to put back")
        say(f"== would stop {_target(uid)}")
        say(f"== would move {layout.checkout} to {layout.previous}")
        say(f"== would move {layout.incoming} to {layout.checkout}")
        say(f"== would run: {' '.join(setup)}")
        say("== dry run of that setup, from where the copy is now:")
        status, _lines = _stream(
            ["/bin/bash", str(layout.incoming / "setup.sh"), "--app", str(app),
             "--dry-run", "--carry-over-from", str(layout.checkout)], say)
        return EXIT_APPLIED if status == 0 else EXIT_REFUSED

    # Copies to put back, and a journal, before anything changes.
    layout.backup.mkdir(mode=0o700, exist_ok=True)
    os.chmod(layout.backup, 0o700)
    _private_copy(layout.agent, layout.backup / "com.berg.dictate.plist")
    _private_copy(layout.receipt, layout.backup / RECEIPT)
    _write_journal(layout, {"from_revision": start, "from_version": receipt["version"],
                            "to_revision": end, "to_version": carried["version"]})
    if not stop_service(runner, uid, sleep=sleep):
        say("!! Whisper Face could not be stopped to update it")
        restored = restore(layout, start, runner=runner, uid=uid, say=say, sleep=sleep)
        return _finish_failure(layout, restored, start, end, "could not stop the service")
    try:
        _discard(layout.previous)
        os.replace(layout.checkout, layout.previous)
        os.replace(layout.incoming, layout.checkout)
    except (OSError, UpdateError) as exc:
        say(f"!! the new copy could not be put in place ({type(exc).__name__})")
        restored = restore(layout, start, runner=runner, uid=uid, say=say, sleep=sleep)
        return _finish_failure(layout, restored, start, end, "switch failed")

    status, lines = _stream(setup, say)
    if status == 0:
        layout.journal.unlink(missing_ok=True)
        shutil.rmtree(layout.backup, ignore_errors=True)
        _record(layout, "applied", start, end)
        say(f"== Whisper Face {carried['version']} is set up; "
            f"{receipt['version']} is kept in {layout.previous}")
        return EXIT_APPLIED
    reason = _last_reason(lines)
    say(f"== the update did not finish; putting Whisper Face {receipt['version']} back")
    restored = restore(layout, start, runner=runner, uid=uid, say=say, sleep=sleep)
    if restored:
        # The reason stays the last "!!" line, which is what the window shows.
        say(f"!! {reason}")
    return _finish_failure(layout, restored, start, end, reason)


def _finish_failure(layout: Layout, restored: bool, start: str, end: str,
                    reason: str) -> int:
    if restored:
        layout.journal.unlink(missing_ok=True)
        shutil.rmtree(layout.backup, ignore_errors=True)
        _record(layout, "rolled_back", start, end, reason)
        return EXIT_ROLLED_BACK
    # The journal and the copies stay, so the next attempt finishes this.
    _record(layout, "rollback_failed", start, end, reason)
    return EXIT_ROLLBACK_FAILED


def _stream(command: list[str], say: Callable[[str], None]) -> tuple[int, list[str]]:
    """Run setup.sh, passing each line on as it comes."""
    lines: list[str] = []
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   errors="replace")
    except OSError:
        return 127, ["!! setup.sh could not be started"]
    with process:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            del lines[:-400]
            say(line)
        return process.wait(), lines


# ------------------------------------------------ moving a folder install --

def _move_files(layout: Layout) -> tuple[tuple[str, Path], ...]:
    """The files a folder install keeps outside its folder that the app's
    setup rewrites or retires, each with the name its copy is kept under."""
    return (("com.berg.dictate.plist", layout.agent),
            ("com.berg.ollama.plist", layout.ollama_agent),
            (OLLAMA_RECEIPT, layout.ollama_receipt),
            (RECEIPT, layout.receipt))


def folder_install(layout: Layout) -> tuple[Path, str]:
    """The folder install the receipt describes: its folder and revision.

    Recognised as every release from 0.2.0 on wrote it: a schema 1 receipt
    naming the folder, and a login service, if there is one, run from that
    same folder.
    """
    receipt = _read_json(layout.receipt)
    if receipt.get("schema_version") != 1 or receipt.get("bundle_id") != BUNDLE_ID:
        raise UpdateError("Whisper Face on this Mac was not set up from a folder")
    checkout, revision = receipt.get("checkout"), receipt.get("source_revision")
    if not isinstance(checkout, str) or not checkout.startswith("/") or "\n" in checkout:
        raise UpdateError("the install receipt names no folder")
    folder = Path(checkout)
    try:
        resolved = folder.resolve(strict=True)
        support = layout.support.resolve()
    except OSError as exc:
        raise UpdateError(f"the folder {folder} is no longer there") from exc
    if resolved == support or support in resolved.parents:
        raise UpdateError("the install receipt names a copy inside the app's own folder")
    if not (resolved / "setup.sh").is_file() or not (resolved / "dictate.py").is_file():
        raise UpdateError(f"the folder {folder} no longer holds Whisper Face")
    if layout.agent.exists():
        try:
            agent = plistlib.loads(layout.agent.read_bytes())
        except (OSError, ValueError) as exc:
            raise UpdateError("the login service's definition cannot be read") from exc
        working = agent.get("WorkingDirectory")
        if not isinstance(working, str) or Path(working).resolve() != resolved:
            raise UpdateError("the login service runs another copy of Whisper Face, "
                              "not the folder's")
    return folder, revision if isinstance(revision, str) else ""


def _folder_launcher(layout: Layout, app: Path) -> bool:
    """Whether ~/Applications holds a launcher the folder's installer built.

    The same proof setup.sh's own retirement of it asks for
    (``macos_launcher_app.adopt_app``). Anything else there is not the
    folder install's, and the move leaves it exactly as it is.
    """
    import macos_launcher_app

    launcher = layout.launcher
    if launcher.is_symlink() or not launcher.is_dir():
        return False
    if launcher.resolve() == Path(app).resolve():
        return False
    try:
        macos_launcher_app.verify_owned_app(launcher)
    except (macos_launcher_app.LauncherError, OSError):
        return False
    return True


def _copy_bundle(source: Path, destination: Path, digest: str | None = None) -> str:
    """Copy an app bundle, links as links, and prove the copy the same
    files, modes and bytes. Returns that digest."""
    import macos_launcher_app

    expected = digest or macos_launcher_app._bundle_digest(source)
    shutil.copytree(source, destination, symlinks=True)
    if macos_launcher_app._bundle_digest(destination) != expected:
        raise UpdateError("a copy of the folder's Whisper Face app came out different")
    return expected


def _same_file(saved: Path | None, current: Path) -> bool:
    if saved is None:
        return not current.exists() and not current.is_symlink()
    try:
        return current.is_file() and current.read_bytes() == saved.read_bytes()
    except OSError:
        return False


def _launcher_is(path: Path, digest: str) -> bool:
    import macos_launcher_app

    try:
        return (path.is_dir() and not path.is_symlink()
                and macos_launcher_app._bundle_digest(path) == digest)
    except OSError:
        return False


def restore_folder_install(layout: Layout, journal: dict, *, runner: Runner,
                           uid: int, say: Callable[[str], None],
                           sleep=time.sleep) -> bool:
    """Put the folder install back exactly as the journal recorded it.

    Compares with the copies, so it is right wherever the move stopped:
    the service definitions and their receipts, the launcher (byte for
    byte), and the services that were loaded, seen running again. The
    folder itself was never written to, so nothing in it needs undoing.
    """
    backup = layout.move_backup
    saved = set(journal.get("saved") or ())
    folder = journal.get("folder")
    if not isinstance(folder, str) or RECEIPT not in saved:
        say("== the record of what to put back is incomplete")
        return False
    stop_service(runner, uid, sleep=sleep)
    ollama_copy = backup / "com.berg.ollama.plist"
    ollama_changed = not _same_file(
        ollama_copy if "com.berg.ollama.plist" in saved else None,
        layout.ollama_agent)
    if ollama_changed:
        stop_service(runner, uid, sleep=sleep, label=OLLAMA_LABEL)
    for name, destination in _move_files(layout):
        copy = backup / name
        if name in saved:
            if not copy.is_file():
                say(f"== the copy of {name} kept to put back is missing")
                return False
            _private_copy(copy, destination)
        elif destination.is_file() or destination.is_symlink():
            # The app's setup wrote it where the folder install had none.
            destination.unlink()
    digest = journal.get("launcher_digest")
    if isinstance(digest, str) and not _launcher_is(layout.launcher, digest):
        if layout.launcher.exists() or layout.launcher.is_symlink():
            say("== something else now stands where the folder's Whisper Face app was")
            return False
        layout.launcher.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{APP_NAME}.",
                                        dir=layout.launcher.parent))
        try:
            _copy_bundle(backup / APP_NAME, staging / APP_NAME, digest)
            os.replace(staging / APP_NAME, layout.launcher)
        except (OSError, UpdateError):
            say("== the folder's Whisper Face app could not be put back")
            return False
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    if journal.get("ollama_loaded") and ollama_changed:
        if not start_service(runner, uid, layout.ollama_agent, sleep=sleep,
                             label=OLLAMA_LABEL):
            say("== the folder's Ollama service did not start again")
            return False
    if not journal.get("dictate_loaded"):
        return True
    try:
        agent = plistlib.loads(layout.agent.read_bytes())
        working = Path(str(agent.get("WorkingDirectory", ""))).resolve()
    except (OSError, ValueError):
        say("== the login service's definition is missing")
        return False
    if working != Path(folder).resolve():
        say("== the login service does not point at the folder")
        return False
    return start_service(runner, uid, layout.agent, sleep=sleep)


def _end_move(layout: Layout) -> None:
    layout.move_journal.unlink(missing_ok=True)
    shutil.rmtree(layout.move_backup, ignore_errors=True)


def recover_interrupted_move(layout: Layout, *, runner: Runner, uid: int,
                             say: Callable[[str], None],
                             sleep=time.sleep) -> bool | None:
    """Put back a move that stopped part way, if there is one. Returns None
    when there was nothing to do, else whether it worked."""
    if not layout.move_journal.exists():
        # Copies made before anything changed; the next move makes its own.
        shutil.rmtree(layout.move_backup, ignore_errors=True)
        return None
    try:
        journal = _read_json(layout.move_journal)
    except UpdateError:
        return False
    say("== an earlier move into the app did not finish; putting the folder version back first")
    if not restore_folder_install(layout, journal, runner=runner, uid=uid,
                                  say=say, sleep=sleep):
        return False
    _end_move(layout)
    _record(layout, "recovered", str(journal.get("from_revision", "")),
            str(journal.get("to_revision", "")), kind="app-move")
    return True


def _finish_move_failure(layout: Layout, restored: bool, start: str, end: str,
                         reason: str) -> int:
    if restored:
        _end_move(layout)
        _record(layout, "rolled_back", start, end, reason, kind="app-move")
        return EXIT_ROLLED_BACK
    # The journal and the copies stay, so the next attempt finishes this.
    _record(layout, "rollback_failed", start, end, reason, kind="app-move")
    return EXIT_ROLLBACK_FAILED


def adopt_folder_install(*, app: Path, home: Path | None = None,
                         uid: int | None = None, runner: Runner = subprocess.run,
                         sleep=time.sleep, out: TextIO | None = None,
                         dry_run: bool = False) -> int:
    """Move a folder install into the app at ``app``.

    Run from the release the app unpacked into ``checkout``. Prints
    setup.sh's own lines, so the app's window ticks its checklist, and
    returns EXIT_APPLIED, EXIT_REFUSED (nothing changed), EXIT_ROLLED_BACK
    (the folder version is running again) or EXIT_ROLLBACK_FAILED.
    """
    layout = Layout(home or Path.home())
    uid = os.getuid() if uid is None else uid
    stream = out or sys.stdout

    def say(line: str) -> None:
        print(line, file=stream, flush=True)

    def refuse(reason: str) -> int:
        say(f"!! {reason}")
        return EXIT_REFUSED

    app = Path(app).resolve()
    say("@@ phase prepare")
    if not dry_run:
        recovered = recover_interrupted_move(layout, runner=runner, uid=uid,
                                             say=say, sleep=sleep)
        if recovered is False:
            say("!! an earlier move could not be undone; Show Log, then Try Again")
            return EXIT_ROLLBACK_FAILED
    try:
        folder, start = folder_install(layout)
        carried = app_release(app)
    except UpdateError as exc:
        return refuse(str(exc))
    if release_of(layout.checkout) != (carried["version"], carried["revision"]):
        return refuse("the copy this app unpacked is not the release it carries")
    end = carried["revision"]
    launcher = _folder_launcher(layout, app)
    setup = ["/bin/bash", str(layout.checkout / "setup.sh"), "--app", str(app),
             "--carry-over-from", str(folder)]
    say(f"== moving Whisper Face from {folder} into {app}")

    if dry_run:
        kept = [str(path) for _name, path in _move_files(layout) if path.is_file()]
        if launcher:
            kept.append(str(layout.launcher))
        say(f"== would keep {', '.join(kept)} to put back")
        say(f"== would stop {_target(uid)}")
        say(f"== would run: {' '.join(setup)}")
        say(f"== {folder} is only read, never changed")
        say("== dry run of that setup:")
        status, _lines = _stream(
            ["/bin/bash", str(layout.checkout / "setup.sh"), "--app", str(app),
             "--dry-run", "--carry-over-from", str(folder)], say)
        return EXIT_APPLIED if status == 0 else EXIT_REFUSED

    # Copies to put back, then the journal, before anything changes.
    shutil.rmtree(layout.move_backup, ignore_errors=True)
    layout.move_backup.mkdir(mode=0o700)
    os.chmod(layout.move_backup, 0o700)
    saved = []
    for name, source in _move_files(layout):
        if source.is_file():
            _private_copy(source, layout.move_backup / name)
            saved.append(name)
    digest = None
    if launcher:
        try:
            digest = _copy_bundle(layout.launcher, layout.move_backup / APP_NAME)
        except (OSError, UpdateError):
            shutil.rmtree(layout.move_backup, ignore_errors=True)
            return refuse("the folder's Whisper Face app could not be copied to put back")
    _write_journal(layout, {
        "folder": str(folder), "from_revision": start, "to_revision": end,
        "saved": saved, "launcher_digest": digest,
        "dictate_loaded": service_loaded(runner, uid),
        "ollama_loaded": service_loaded(runner, uid, OLLAMA_LABEL),
    }, layout.move_journal)
    journal = _read_json(layout.move_journal)

    def put_back() -> bool:
        return restore_folder_install(layout, journal, runner=runner, uid=uid,
                                      say=say, sleep=sleep)

    # Nothing in the folder may change while its files are copied.
    if not stop_service(runner, uid, sleep=sleep):
        say("!! Whisper Face could not be stopped to move it")
        return _finish_move_failure(layout, put_back(), start, end,
                                    "could not stop the service")
    status, lines = _stream(setup, say)
    if status == 0:
        _end_move(layout)
        _record(layout, "moved", start, end, kind="app-move")
        say(f"== Whisper Face runs from {app} now; the folder {folder} "
            "is no longer used and can be deleted")
        return EXIT_APPLIED
    reason = _last_reason(lines)
    say("== the move did not finish; putting the folder version back")
    restored = put_back()
    if restored:
        # The reason stays the last "!!" line, which is what the window shows.
        say(f"!! {reason}")
    return _finish_move_failure(layout, restored, start, end, reason)


# ------------------------------------------------- offering a new release --

class _HTTPSOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow GitHub's redirect to its download host, and never to plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise urllib.error.URLError("refused a redirect away from HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_get(url: str, destination: Path, limit: int) -> None:
    """Download one release file over HTTPS into ``destination``, no larger
    than ``limit`` bytes. The only network access in this module."""
    if not url.startswith(RELEASES + "/"):
        raise UpdateError("refused a download from outside the published releases")
    opener = urllib.request.build_opener(_HTTPSOnlyRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "WhisperFace-updater"})
    received = 0
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT) as response, \
                destination.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > limit:
                    raise UpdateError("a release file is larger than it may be")
                handle.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError("couldn't reach the published releases; check that this Mac is online") from exc


def _offer(manifest: dict, channel: str) -> dict:
    """What a manifest offers, checked before anything large is downloaded."""
    if manifest.get("schema_version") != release_manifest.SCHEMA_VERSION \
            or manifest.get("product") != release_manifest.PRODUCT:
        raise UpdateError("the published update manifest is not Whisper Face's")
    if manifest.get("channel") != channel:
        raise UpdateError(f"the latest release is not on this app's {channel} channel")
    source = manifest.get("source_offer")
    version = manifest.get("version")
    revision = source.get("revision") if isinstance(source, dict) else None
    if not isinstance(version, str) or not isinstance(revision, str):
        raise UpdateError("the published update manifest names no release")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise UpdateError("the published update manifest lists no files")
    files = {}
    for artifact in artifacts:
        name = artifact.get("name") if isinstance(artifact, dict) else None
        if not isinstance(name, str) or name != Path(name).name or name.startswith("."):
            raise UpdateError("the published update manifest names an unsafe file")
        url, size = artifact.get("url"), artifact.get("size")
        if not isinstance(url, str) or not url.startswith(DOWNLOAD_PREFIX):
            raise UpdateError("the published update manifest points outside its releases")
        if not isinstance(size, int) or isinstance(size, bool) \
                or not 0 < size <= MAX_ARTIFACT_BYTES:
            raise UpdateError("the published update manifest gives a file an impossible size")
        files[artifact.get("role")] = {"name": name, "url": url, "size": size}
    if len(artifacts) != 2 or set(files) != {"corresponding-source", "installer"}:
        raise UpdateError("the latest release does not carry a Mac disk image and its source")
    if not files["installer"]["name"].endswith(".dmg"):
        raise UpdateError("the latest release does not carry a Mac disk image")
    return {"version": version, "revision": revision, "files": files}


def _current(layout: Layout) -> tuple[dict, dict, Path]:
    receipt = installed(layout)
    app = Path(receipt["app"])
    carried = app_release(app)
    if carried["revision"] != receipt["source_revision"]:
        raise UpdateError("open Whisper Face from Applications to finish its last update first")
    return receipt, carried, app


def check(*, home: Path | None = None, fetch: Fetch = https_get) -> dict:
    """Is a newer published release on this app's channel? Reads only the
    latest release's manifest."""
    layout = Layout(home or Path.home())
    result = {"decision": "refuse", "reason": ""}
    try:
        receipt, carried, _app = _current(layout)
        result["current_version"] = receipt["version"]
        with tempfile.TemporaryDirectory(prefix="whisper-face-update.") as scratch:
            manifest_path = Path(scratch) / "update-manifest.json"
            fetch(MANIFEST_URL, manifest_path, MAX_MANIFEST_BYTES)
            offer = _offer(_read_json(manifest_path), carried["channel"])
    except UpdateError as exc:
        result["reason"] = str(exc)
        return result
    decision, reason = decide(receipt["version"], receipt["source_revision"],
                              offer["version"], offer["revision"])
    result.update({"decision": decision, "reason": reason,
                   "version": offer["version"], "revision": offer["revision"],
                   "size": offer["files"]["installer"]["size"]})
    if reason == "downgrade-requires-linked-rollback":
        # What is installed is newer than the latest release: nothing to
        # offer, and nothing wrong.
        result.update({"decision": "up-to-date", "reason": "installed-is-newer"})
    elif decision == "refuse":
        result["reason"] = REFUSALS.get(reason, reason)
    return result


def install(*, version: str, revision: str, home: Path | None = None,
            fetch: Fetch = https_get, runner: Runner = subprocess.run,
            verify_app: Callable[[Path, Path, str, str], None] | None = None,
            attach=None, policy: Path | None = None) -> dict:
    """Download the release the person accepted, verify it, put its app
    where this one is and open it to finish the update.

    ``version`` and ``revision`` are what the menu offered: if the latest
    release has changed since, nothing is installed.
    """
    import macos_launcher_app
    import verify_macos_package

    layout = Layout(home or Path.home())
    receipt, carried, app = _current(layout)
    if not os.access(app.parent, os.W_OK):
        raise UpdateError(f"Whisper Face can't replace itself in {app.parent}; "
                          "download the new version and drag it there yourself")
    verify_app = verify_app or (lambda zip_path, candidate, v, r:
                                verify_macos_package.verify_release_app(
                                    zip_path, candidate, v, r))
    attach = attach or verify_macos_package.attached_disk_image
    work = layout.support / "update-download"
    _discard(work)
    work.mkdir(mode=0o700)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{APP_NAME}.", dir=app.parent))
    staged = staging_root / APP_NAME
    try:
        manifest_path = work / "update-manifest.json"
        fetch(MANIFEST_URL, manifest_path, MAX_MANIFEST_BYTES)
        offer = _offer(_read_json(manifest_path), carried["channel"])
        if (offer["version"], offer["revision"]) != (version, revision):
            raise UpdateError("the latest release changed since it was offered; check again")
        for item in offer["files"].values():
            fetch(item["url"], work / item["name"], item["size"])
        # The release rules, exactly as the advisor applies them: digests and
        # sizes against the manifest, channel, versions, and for a stable
        # release its signature and notarization.
        try:
            advice = safe_update_advisor.advise(SimpleNamespace(
                current_version=receipt["version"],
                current_revision=receipt["source_revision"],
                manifest=str(manifest_path), artifact_dir=str(work),
                channel=carried["channel"], intent="update",
                rollback_manifest=None, rollback_artifact_dir=None))
        except safe_update_advisor.AdviceError as exc:
            raise UpdateError(f"the download did not verify ({exc})") from exc
        if advice.get("decision") != "upgrade" \
                or advice.get("candidate", {}).get("source_revision") != revision:
            raise UpdateError("the download is not the newer release that was offered")
        source_zip = work / offer["files"]["corresponding-source"]["name"]
        disk_image = work / offer["files"]["installer"]["name"]
        try:
            with attach(disk_image) as mounted:
                copied = runner(["/usr/bin/ditto", str(mounted / APP_NAME), str(staged)],
                                capture_output=True, text=True, timeout=300)
                if copied.returncode != 0:
                    raise UpdateError("the new app could not be copied off its disk image")
            # The copy is what goes in place, so the copy is what is proven:
            # the release's own launcher contract, with the published source
            # archive sealed inside it.
            verify_app(source_zip, staged, version, revision)
            if carried["channel"] == "stable":
                # And signed by the Developer ID this install already trusts.
                macos_launcher_app._verify_pinned_signature(
                    staged, policy or macos_launcher_app.DEFAULT_POLICY)
        except (verify_macos_package.PackageError,
                macos_launcher_app.LauncherError, OSError) as exc:
            raise UpdateError(f"the new app did not verify ({exc})") from exc
        try:
            macos_launcher_app.verify_owned_app(app)
        except macos_launcher_app.LauncherError as exc:
            raise UpdateError("the app being replaced is not Whisper Face") from exc
        retired = app.with_name(f".{APP_NAME}.previous-{os.getpid()}")
        os.replace(app, retired)
        try:
            os.replace(staged, app)
        except OSError:
            os.replace(retired, app)
            raise
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    _record(layout, "downloaded", receipt["source_revision"], revision)
    # The new app finishes the update the way it does after a drag.
    opened = runner(["/usr/bin/open", "-n", str(app), "--args", "--update"],
                    capture_output=True, text=True, timeout=30)
    if opened.returncode != 0:
        return {"status": "installed", "version": version,
                "note": "open Whisper Face from Applications to finish"}
    return {"status": "installed", "version": version}


# ------------------------------------------------------------------- main --

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply", help="switch the install to this app's release")
    apply.add_argument("--app", required=True)
    apply.add_argument("--dry-run", action="store_true")
    adopt = commands.add_parser(
        "adopt-folder", help="move a folder install into this app's release")
    adopt.add_argument("--app", required=True)
    adopt.add_argument("--dry-run", action="store_true")
    commands.add_parser("check", help="is a newer published release offered?")
    accept = commands.add_parser("install", help="download and install the offered release")
    accept.add_argument("--version", required=True)
    accept.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    if args.command == "apply":
        return apply_update(app=Path(args.app), dry_run=args.dry_run)
    if args.command == "adopt-folder":
        return adopt_folder_install(app=Path(args.app), dry_run=args.dry_run)
    if args.command == "check":
        print(json.dumps(check(), sort_keys=True))
        return 0
    try:
        outcome = install(version=args.version, revision=args.revision)
    except (UpdateError, OSError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
