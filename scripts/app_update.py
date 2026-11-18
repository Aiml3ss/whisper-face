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
# The checkout's root, for voice_check.py, after scripts/ so a module there
# can never be shadowed by one of the same name at the root.
sys.path.append(str(Path(__file__).resolve().parents[1]))

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
        raise UpdateError("the install's checkout cannot be found") from exc
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

def _target(uid: int) -> str:
    return f"gui/{uid}/{LABEL}"


def _launchctl(runner: Runner, *arguments: str) -> "subprocess.CompletedProcess":
    try:
        return runner(["/bin/launchctl", *arguments], capture_output=True,
                      text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(["/bin/launchctl", *arguments], 1, "", "")


def stop_service(runner: Runner, uid: int, sleep=time.sleep, wait: int = 15) -> bool:
    """Unload the dictation service and wait until launchd lets it go."""
    _launchctl(runner, "bootout", _target(uid))
    for _ in range(wait):
        if _launchctl(runner, "print", _target(uid)).returncode != 0:
            return True
        sleep(1)
    return _launchctl(runner, "print", _target(uid)).returncode != 0


def start_service(runner: Runner, uid: int, plist: Path, sleep=time.sleep,
                  wait: int = 30) -> bool:
    """Load the service from ``plist`` and see it running."""
    _launchctl(runner, "bootstrap", f"gui/{uid}", str(plist))
    _launchctl(runner, "kickstart", _target(uid))
    for _ in range(wait):
        state = _launchctl(runner, "print", _target(uid))
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


def _write_journal(layout: Layout, payload: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{JOURNAL}.", dir=layout.support)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
    os.chmod(name, 0o600)
    os.replace(name, layout.journal)


def _record(layout: Layout, status: str, start: str, end: str,
            reason: str = "") -> None:
    """One content-free line per outcome in update.log, as the git updater
    keeps for its own."""
    try:
        with layout.log.open("a", encoding="utf-8") as history:
            history.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} app-update "
                          f"{status} {start[:7]}->{end[:7]} {reason}".rstrip()
                          + "\n")
        os.chmod(layout.log, 0o600)
    except OSError:
        pass


def _voice_line(checked: dict) -> str:
    """The voice check's content-free counts, for update.log."""
    import voice_check

    return voice_check.summary_line(checked)


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


def voice_check_release(layout: Layout, source_zip: Path, version: str,
                        current_version: str, *, check=None) -> dict:
    """Try a verified release on the person's recordings before its app
    replaces this one.

    With no recordings nothing is unpacked. Otherwise the release's source
    archive, already proven to be that release, is unpacked into the
    update's own private folder and ``voice_check.check_update`` compares it
    with the copy this install runs.
    """
    import voice_check
    import verify_macos_package

    if voice_check.corpus_size(layout.home) == 0:
        return {"status": "no-corpus"}
    unpacked = Path(tempfile.mkdtemp(prefix="voice-check.", dir=source_zip.parent))
    try:
        os.chmod(unpacked, 0o700)
        root = f"Whisper Face {version}"
        verify_macos_package._check_zip_members(source_zip, root)
        verify_macos_package._extract_zip(source_zip, unpacked)
        return (check or voice_check.check_update)(
            home=layout.home, current=layout.checkout,
            candidate=unpacked / root, current_label=current_version,
            candidate_label=version)
    except (OSError, verify_macos_package.PackageError):
        return {"status": "not-checked", "reason": "candidate-failed"}
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)


def install(*, version: str, revision: str, home: Path | None = None,
            fetch: Fetch = https_get, runner: Runner = subprocess.run,
            verify_app: Callable[[Path, Path, str, str], None] | None = None,
            attach=None, policy: Path | None = None,
            voice_check=None) -> dict:
    """Download the release the person accepted, verify it, put its app
    where this one is and open it to finish the update.

    ``version`` and ``revision`` are what the menu offered: if the latest
    release has changed since, nothing is installed.

    ``voice_check``, when given, is called as ``voice_check(layout,
    source_zip, version, current_version)`` once the download has verified
    and before the app is replaced (``main`` passes
    :func:`voice_check_release` unless the person chose to update anyway).
    A ``held`` outcome returns ``{"status": "held", ...}`` with nothing
    replaced; any other outcome goes on and is carried in the result.
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
        # Verified: now, before it replaces anything, on the person's voice.
        checked = None
        if voice_check is not None:
            try:
                checked = voice_check(layout, source_zip, version,
                                      receipt["version"])
            except Exception:
                checked = {"status": "not-checked", "reason": "failed"}
            if not isinstance(checked, dict):
                checked = {"status": "not-checked", "reason": "failed"}
            if checked.get("status") == "held":
                _record(layout, "held", receipt["source_revision"], revision,
                        _voice_line(checked))
                return {"status": "held", "version": version,
                        "revision": revision, "voice_check": checked}
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
    _record(layout, "downloaded", receipt["source_revision"], revision,
            _voice_line(checked) if checked else "")
    # The new app finishes the update the way it does after a drag.
    opened = runner(["/usr/bin/open", "-n", str(app), "--args", "--update"],
                    capture_output=True, text=True, timeout=30)
    outcome = {"status": "installed", "version": version}
    if checked:
        outcome["voice_check"] = checked
    if opened.returncode != 0:
        outcome["note"] = "open Whisper Face from Applications to finish"
    return outcome


# ------------------------------------------------------------------- main --

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply", help="switch the install to this app's release")
    apply.add_argument("--app", required=True)
    apply.add_argument("--dry-run", action="store_true")
    commands.add_parser("check", help="is a newer published release offered?")
    accept = commands.add_parser("install", help="download and install the offered release")
    accept.add_argument("--version", required=True)
    accept.add_argument("--revision", required=True)
    # The person saw a held update's counts and chose to update anyway.
    accept.add_argument("--update-anyway", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "apply":
        return apply_update(app=Path(args.app), dry_run=args.dry_run)
    if args.command == "check":
        print(json.dumps(check(), sort_keys=True))
        return 0
    try:
        outcome = install(
            version=args.version, revision=args.revision,
            voice_check=None if args.update_anyway else voice_check_release)
    except (UpdateError, OSError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
