# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Updating a drag install, against fakes and a temporary home folder.

Nothing here touches the real home folder, launchd, /Applications or the
network: launchctl is a fake that keeps the service's state in memory, the
new release's setup.sh is a small script staged for each test, and every
download is a fake that copies from a folder standing in for the releases.
"""

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
import urllib.request
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "app_update", ROOT / "scripts" / "app_update.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
import release_manifest  # noqa: E402
import macos_launcher_app  # noqa: E402

OLD, NEW = "a" * 40, "b" * 40
UID = 501


class FakeLaunchd:
    """launchctl for one job, in memory. ``starts`` says whether a loaded
    job reaches "running"."""

    def __init__(self, *, loaded=True, starts=True, stops=True):
        self.loaded, self.starts, self.stops = loaded, starts, stops
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command[1:])
        verb = command[1]
        stdout, code = "", 0
        if verb == "bootout":
            if self.stops:
                self.loaded = False
        elif verb == "bootstrap":
            self.loaded = True
        elif verb == "print":
            if not self.loaded:
                code = 113
            elif self.starts:
                stdout = "state = running\npid = 4242\n"
            else:
                stdout = "state = spawn scheduled\n"
        return subprocess.CompletedProcess(command, code, stdout, "")

    def verbs(self):
        return [call[0] for call in self.calls if call[0] != "print"]


def write_release(folder: Path, version: str, revision: str, setup: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "RELEASE-METADATA.json").write_text(json.dumps(
        {"version": version, "source_revision": revision}))
    (folder / "dictate.py").write_text("# stand-in\n")
    (folder / "setup.sh").write_text(setup)
    (folder / "setup.sh").chmod(0o755)


def fake_app(path: Path, version: str, revision: str, channel="preview") -> Path:
    """A drag-install app's layout, every file empty but its Info.plist."""
    for relative in (macos_launcher_app.DRAG_FILES
                     | macos_launcher_app.SIGNATURE_FILES):
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "com.berg.whisper-face.launcher",
        "CFBundleExecutable": "Whisper Face",
        "CFBundleShortVersionString": version, "WFSourceRevision": revision,
        "WFReleaseChannel": channel, "WFSourceArchiveSHA256": "c" * 64}))
    return path


# The new release's setup.sh, standing in for the real one: it records how
# it was run, carries one personal file the way --carry-over-from does, and
# either succeeds as setup.sh --app does (rebinding the receipt) or fails
# with a reason, after rebinding, as a late failure would.
NEW_SETUP = r"""#!/bin/bash
here="$(cd "$(dirname "$0")" && pwd)"
printf '%s\n' "$@" > "$here/setup-arguments"
echo "@@ phase python"
echo "== downloading"
previous="$4"
cp "$previous/dictionary.txt" "$here/dictionary.txt"
receipt="$HOME_FOR_TEST/Library/Application Support/Whisper Face/launcher-install.json"
"$PYTHON_FOR_TEST" - "$receipt" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
payload.update(source_revision="b" * 40, version="0.6.1")
open(sys.argv[1], "w").write(json.dumps(payload))
PY
if [ "${SETUP_SHOULD_FAIL:-0}" = 1 ]; then
    echo "!! error: Failed to fetch: https://example.invalid/wheel"
    echo "!! Whisper Face setup failed (exit status 1)."
    exit 1
fi
echo "@@ phase service"
echo "@@ phase done"
"""


@unittest.skipIf(os.name == "nt", "the drag install is Mac-only")
class DragUpdateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "home"
        self.layout = MODULE.Layout(self.home)
        self.layout.support.mkdir(parents=True)
        self.layout.support.chmod(0o700)
        write_release(self.layout.checkout, "0.6.0", OLD,
                      "#!/bin/bash\necho old setup\n")
        for name in ("dictionary.txt", "transcripts.jsonl", "preferences.json"):
            (self.layout.checkout / name).write_text(f"mine: {name}\n")
        write_release(self.layout.incoming, "0.6.1", NEW, NEW_SETUP)
        self.app = fake_app(self.root / "Applications" / "Whisper Face.app",
                            "0.6.1", NEW)
        self.layout.agent.parent.mkdir(parents=True)
        self.agent_bytes = plistlib.dumps({
            "Label": "com.berg.dictate",
            "WorkingDirectory": str(self.layout.checkout)})
        self.layout.agent.write_bytes(self.agent_bytes)
        self.receipt_bytes = json.dumps({
            "app": str(self.app), "bundle_id": "com.berg.whisper-face.launcher",
            "checkout": str(self.layout.checkout), "schema_version": 2,
            "source_revision": OLD, "version": "0.6.0"}).encode()
        self.layout.receipt.write_bytes(self.receipt_bytes)
        os.environ["HOME_FOR_TEST"] = str(self.home)
        os.environ["PYTHON_FOR_TEST"] = sys.executable
        self.addCleanup(os.environ.pop, "HOME_FOR_TEST", None)
        self.addCleanup(os.environ.pop, "PYTHON_FOR_TEST", None)
        self.addCleanup(os.environ.pop, "SETUP_SHOULD_FAIL", None)

    def apply(self, launchd, **kwargs):
        out = io.StringIO()
        status = MODULE.apply_update(
            app=self.app, home=self.home, uid=UID, runner=launchd,
            sleep=lambda _seconds: None, out=out, **kwargs)
        return status, out.getvalue().splitlines()

    def test_an_update_switches_to_the_new_copy_and_keeps_the_old_one(self):
        launchd = FakeLaunchd()
        status, lines = self.apply(launchd)
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertEqual(MODULE.release_of(self.layout.checkout), ("0.6.1", NEW))
        self.assertEqual(MODULE.release_of(self.layout.previous), ("0.6.0", OLD))
        self.assertFalse(self.layout.incoming.exists())
        # The new copy's own setup ran, in app mode, carrying the old copy's
        # files, after the service was stopped.
        arguments = (self.layout.checkout / "setup-arguments").read_text().split("\n")
        self.assertEqual(arguments[:4], ["--app", str(self.app),
                                         "--carry-over-from",
                                         str(self.layout.previous)])
        self.assertEqual((self.layout.checkout / "dictionary.txt").read_text(),
                         "mine: dictionary.txt\n")
        self.assertEqual(launchd.verbs(), ["bootout"])
        # The old copy keeps its own files for going back.
        for name in ("dictionary.txt", "transcripts.jsonl", "preferences.json"):
            self.assertEqual((self.layout.previous / name).read_text(),
                             f"mine: {name}\n")
        # setup.sh's checklist lines reach the app's window.
        self.assertIn("@@ phase python", lines)
        self.assertIn("@@ phase done", lines)
        self.assertFalse(self.layout.journal.exists())
        self.assertFalse(self.layout.backup.exists())
        self.assertIn("app-update applied aaaaaaa->bbbbbbb",
                      self.layout.log.read_text())

    def test_an_update_waiting_for_permissions_is_an_update_that_worked(self):
        # An unsigned app's identity changes with every release, so macOS
        # stops honouring its grants: setup.sh then ends on "permissions",
        # and the runtime's own permissions window takes over.
        (self.layout.incoming / "setup.sh").write_text(
            NEW_SETUP.replace('echo "@@ phase done"',
                              'echo "@@ phase permissions"'))
        status, lines = self.apply(FakeLaunchd())
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertIn("@@ phase permissions", lines)
        self.assertEqual(MODULE.release_of(self.layout.checkout), ("0.6.1", NEW))

    def test_a_failed_update_puts_the_old_copy_back_and_starts_it(self):
        os.environ["SETUP_SHOULD_FAIL"] = "1"
        launchd = FakeLaunchd()
        status, lines = self.apply(launchd)
        self.assertEqual(status, MODULE.EXIT_ROLLED_BACK, "\n".join(lines))
        # The old copy is where the service expects it, untouched.
        self.assertEqual(MODULE.release_of(self.layout.checkout), ("0.6.0", OLD))
        for name in ("dictionary.txt", "transcripts.jsonl", "preferences.json"):
            self.assertEqual((self.layout.checkout / name).read_text(),
                             f"mine: {name}\n")
        # The receipt setup rewrote and the service definition are exactly
        # what they were, and the service runs again.
        self.assertEqual(self.layout.receipt.read_bytes(), self.receipt_bytes)
        self.assertEqual(self.layout.agent.read_bytes(), self.agent_bytes)
        self.assertEqual(launchd.verbs(),
                         ["bootout", "bootout", "bootstrap", "kickstart"])
        self.assertTrue(launchd.loaded)
        # The newer copy waits for Try Again, with what it downloaded.
        self.assertEqual(MODULE.release_of(self.layout.incoming), ("0.6.1", NEW))
        self.assertFalse(self.layout.previous.exists())
        # The window's reason is the last "!!" line, and it is setup's.
        reasons = [line for line in lines if line.startswith("!! ")]
        self.assertEqual(reasons[-1],
                         "!! error: Failed to fetch: https://example.invalid/wheel")
        self.assertFalse(self.layout.journal.exists())
        self.assertIn("app-update rolled_back", self.layout.log.read_text())

        # Try Again from there works, and reuses the kept copy.
        os.environ["SETUP_SHOULD_FAIL"] = "0"
        self.layout.receipt.write_bytes(self.receipt_bytes)
        status, lines = self.apply(FakeLaunchd())
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertEqual(MODULE.release_of(self.layout.checkout), ("0.6.1", NEW))

    def test_a_rollback_that_cannot_be_seen_running_is_never_called_one(self):
        os.environ["SETUP_SHOULD_FAIL"] = "1"
        status, lines = self.apply(FakeLaunchd(starts=False))
        self.assertEqual(status, MODULE.EXIT_ROLLBACK_FAILED, "\n".join(lines))
        self.assertTrue(self.layout.journal.exists())
        self.assertIn("app-update rollback_failed", self.layout.log.read_text())
        # The next attempt finishes putting things back first, then updates.
        os.environ["SETUP_SHOULD_FAIL"] = "0"
        status, lines = self.apply(FakeLaunchd())
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertIn("== an earlier update did not finish; putting the copy "
                      "it replaced back first", lines)
        self.assertEqual(MODULE.release_of(self.layout.checkout), ("0.6.1", NEW))
        self.assertEqual(MODULE.release_of(self.layout.previous), ("0.6.0", OLD))
        self.assertIn("app-update recovered", self.layout.log.read_text())

    def test_an_update_interrupted_between_the_two_renames_is_finished_first(self):
        # As if the Mac lost power after checkout became checkout.previous.
        self.layout.backup.mkdir()
        shutil.copy2(self.layout.agent, self.layout.backup / "com.berg.dictate.plist")
        shutil.copy2(self.layout.receipt, self.layout.backup / "launcher-install.json")
        self.layout.journal.write_text(json.dumps(
            {"from_revision": OLD, "to_revision": NEW}))
        os.replace(self.layout.checkout, self.layout.previous)
        status, lines = self.apply(FakeLaunchd(loaded=False))
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertEqual(MODULE.release_of(self.layout.checkout), ("0.6.1", NEW))
        self.assertEqual((self.layout.checkout / "dictionary.txt").read_text(),
                         "mine: dictionary.txt\n")

    def test_an_older_or_conflicting_app_changes_nothing(self):
        before = sorted(str(p) for p in self.layout.support.rglob("*"))
        for version, revision, words in (
                ("0.5.9", NEW, "older than the one already set up"),
                ("0.6.0", NEW, "same version as the one set up"),
                ("0.6.1", OLD, "same contents")):
            with self.subTest(version=version):
                fake_app(self.app, version, revision)
                write_release(self.layout.incoming, version, revision, NEW_SETUP)
                launchd = FakeLaunchd()
                status, lines = self.apply(launchd)
                self.assertEqual(status, MODULE.EXIT_REFUSED, "\n".join(lines))
                self.assertIn(words, lines[-1])
                self.assertEqual(launchd.calls, [])
                self.assertEqual(MODULE.release_of(self.layout.checkout),
                                 ("0.6.0", OLD))
        self.assertEqual(sorted(str(p) for p in self.layout.support.rglob("*")),
                         before)

    def test_only_the_apps_own_release_beside_its_own_install_is_applied(self):
        cases = {
            "unpacked another release": lambda: write_release(
                self.layout.incoming, "0.6.2", "c" * 40, NEW_SETUP),
            "a folder install": lambda: self.layout.receipt.write_text(json.dumps(
                {**json.loads(self.receipt_bytes), "schema_version": 1})),
            "a copy elsewhere": lambda: self.layout.receipt.write_text(json.dumps(
                {**json.loads(self.receipt_bytes), "checkout": str(self.root)})),
        }
        for name, spoil in cases.items():
            with self.subTest(name):
                self.setUp()
                spoil()
                launchd = FakeLaunchd()
                status, lines = self.apply(launchd)
                self.assertEqual(status, MODULE.EXIT_REFUSED, "\n".join(lines))
                self.assertEqual(launchd.calls, [])
                self.assertTrue(self.layout.incoming.exists())

    def test_the_same_release_is_already_up_to_date(self):
        fake_app(self.app, "0.6.0", OLD)
        write_release(self.layout.incoming, "0.6.0", OLD, NEW_SETUP)
        launchd = FakeLaunchd()
        status, lines = self.apply(launchd)
        self.assertEqual(status, MODULE.EXIT_APPLIED)
        self.assertEqual(launchd.calls, [])
        self.assertIn("== already up to date", lines)

    def test_a_dry_run_changes_nothing_and_stops_before_the_service(self):
        (self.layout.incoming / "setup.sh").write_text(
            '#!/bin/bash\necho "dry: $*"\n')
        before = {str(p): p.stat().st_mtime_ns
                  for p in self.layout.support.rglob("*")}
        launchd = FakeLaunchd()
        status, lines = self.apply(launchd, dry_run=True)
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertEqual(launchd.calls, [])
        self.assertIn(
            f"dry: --app {self.app} --dry-run --carry-over-from "
            f"{self.layout.checkout}", lines)
        self.assertIn(f"== would move {self.layout.incoming} to "
                      f"{self.layout.checkout}", lines)
        self.assertEqual({str(p): p.stat().st_mtime_ns
                          for p in self.layout.support.rglob("*")}, before)


def make_release(server: Path, version: str, revision: str, channel="preview"):
    """A published release's files and its manifest, in ``server``."""
    server.mkdir(parents=True, exist_ok=True)
    source = server / f"WhisperFace-{version}-source.zip"
    image = server / f"WhisperFace-{version}-macOS-arm64.dmg"
    source.write_bytes(b"source " + version.encode())
    image.write_bytes(b"image " + version.encode())
    manifest = release_manifest.create_manifest(Namespace(
        version=version, revision=revision, artifact=[str(source), str(image)],
        signed_artifact=[], notarized_artifact=[], channel=channel,
        published_at="2026-11-16T00:00:00Z",
        repository=release_manifest.DEFAULT_REPOSITORY,
        download_base_url=f"{MODULE.DOWNLOAD_PREFIX}v{version}",
        previous_version=None, previous_revision=None,
        previous_manifest_url=None))
    (server / "update-manifest.json").write_text(json.dumps(manifest))
    return manifest


class FakeReleases:
    """Downloads, served from a folder; records every address asked for."""

    def __init__(self, server: Path, *, tamper: str | None = None):
        self.server, self.tamper, self.urls = server, tamper, []

    def __call__(self, url, destination, limit):
        self.urls.append(url)
        self.assertion = url.startswith(MODULE.RELEASES + "/")
        if not self.assertion:
            raise AssertionError(f"asked for {url}")
        data = (self.server / url.rsplit("/", 1)[1]).read_bytes()
        if len(data) > limit:
            raise MODULE.UpdateError("too large")
        if self.tamper and url.endswith(self.tamper):
            data += b"!"
        Path(destination).write_bytes(data)


@unittest.skipIf(os.name == "nt", "the drag install is Mac-only")
class OfferedUpdateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "home"
        self.layout = MODULE.Layout(self.home)
        self.layout.support.mkdir(parents=True)
        write_release(self.layout.checkout, "0.6.0", OLD, "")
        self.app = fake_app(self.root / "Applications" / "Whisper Face.app",
                            "0.6.0", OLD)
        self.layout.receipt.write_text(json.dumps({
            "app": str(self.app), "bundle_id": "com.berg.whisper-face.launcher",
            "checkout": str(self.layout.checkout), "schema_version": 2,
            "source_revision": OLD, "version": "0.6.0"}))
        self.server = self.root / "releases"

    def test_a_newer_release_on_the_apps_channel_is_offered(self):
        make_release(self.server, "0.6.1", NEW)
        fetch = FakeReleases(self.server)
        offer = MODULE.check(home=self.home, fetch=fetch)
        self.assertEqual(offer["decision"], "upgrade")
        self.assertEqual(offer["version"], "0.6.1")
        self.assertEqual(offer["current_version"], "0.6.0")
        self.assertGreater(offer["size"], 0)
        # Checking reads the manifest and nothing else.
        self.assertEqual(fetch.urls, [MODULE.MANIFEST_URL])

    def test_nothing_older_or_off_channel_is_offered(self):
        make_release(self.server, "0.5.9", NEW)
        self.assertEqual(MODULE.check(home=self.home, fetch=FakeReleases(
            self.server))["decision"], "up-to-date")
        make_release(self.server, "0.6.0", OLD)
        self.assertEqual(MODULE.check(home=self.home, fetch=FakeReleases(
            self.server))["decision"], "up-to-date")
        make_release(self.server, "0.6.1", NEW, channel="stable")
        stable = MODULE.check(home=self.home, fetch=FakeReleases(self.server))
        self.assertEqual(stable["decision"], "refuse")
        self.assertIn("preview channel", stable["reason"])

        def offline(_url, _destination, _limit):
            raise MODULE.UpdateError("couldn't reach the published releases")
        unreachable = MODULE.check(home=self.home, fetch=offline)
        self.assertEqual(unreachable["decision"], "refuse")
        self.assertIn("couldn't reach", unreachable["reason"])

    def test_a_manifest_pointing_elsewhere_is_refused_before_any_download(self):
        manifest = make_release(self.server, "0.6.1", NEW)
        manifest["artifacts"][1]["url"] = "https://example.invalid/x.dmg"
        (self.server / "update-manifest.json").write_text(json.dumps(manifest))
        fetch = FakeReleases(self.server)
        offer = MODULE.check(home=self.home, fetch=fetch)
        self.assertEqual(offer["decision"], "refuse")
        self.assertIn("outside its releases", offer["reason"])

    def staged_image(self, version, revision):
        image = self.root / f"mounted-{version}"
        fake_app(image / "Whisper Face.app", version, revision)
        (image / "Applications").symlink_to("/Applications")

        @contextlib.contextmanager
        def attach(_disk_image):
            yield image
        return attach

    @staticmethod
    def runner(calls):
        def run(command, **_kwargs):
            calls.append(command)
            if command[0] == "/usr/bin/ditto":
                shutil.copytree(command[1], command[2], symlinks=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        return run

    def install(self, fetch, *, verify_app=None, version="0.6.1", revision=NEW):
        calls, verified = [], []

        def default_verify(source_zip, app, v, r):
            verified.append((Path(source_zip).name, app, v, r))
        result = MODULE.install(
            version=version, revision=revision, home=self.home, fetch=fetch,
            runner=self.runner(calls), verify_app=verify_app or default_verify,
            attach=self.staged_image("0.6.1", NEW))
        return result, calls, verified

    def test_accepting_downloads_verifies_replaces_the_app_and_opens_it(self):
        make_release(self.server, "0.6.1", NEW)
        fetch = FakeReleases(self.server)
        result, calls, verified = self.install(fetch)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(
            sorted(url.rsplit("/", 1)[1] for url in fetch.urls),
            ["WhisperFace-0.6.1-macOS-arm64.dmg", "WhisperFace-0.6.1-source.zip",
             "update-manifest.json"])
        # The copy that went in place is the one that was verified, against
        # the published source archive.
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0][0], "WhisperFace-0.6.1-source.zip")
        self.assertEqual(verified[0][2:], ("0.6.1", NEW))
        self.assertEqual(MODULE.app_release(self.app)["version"], "0.6.1")
        # It is opened to finish the update the way a drag does.
        self.assertEqual(calls[-1], ["/usr/bin/open", "-n", str(self.app),
                                     "--args", "--update"])
        # Nothing is left behind: no download, no staging, no old app.
        self.assertEqual(sorted(p.name for p in self.app.parent.iterdir()),
                         ["Whisper Face.app"])
        self.assertFalse((self.layout.support / "update-download").exists())
        self.assertIn("app-update downloaded", self.layout.log.read_text())

    def test_a_download_that_does_not_match_its_manifest_changes_nothing(self):
        make_release(self.server, "0.6.1", NEW)
        with self.assertRaises(MODULE.UpdateError) as caught:
            self.install(FakeReleases(self.server, tamper=".dmg"))
        self.assertIn("did not verify", str(caught.exception))
        self.assertEqual(MODULE.app_release(self.app)["version"], "0.6.0")
        self.assertEqual(sorted(p.name for p in self.app.parent.iterdir()),
                         ["Whisper Face.app"])
        self.assertFalse((self.layout.support / "update-download").exists())

    def test_an_app_that_fails_its_releases_contract_is_not_put_in_place(self):
        make_release(self.server, "0.6.1", NEW)

        def refuse(*_arguments):
            raise macos_launcher_app.LauncherError("launcher source contract mismatch")
        with self.assertRaises(MODULE.UpdateError) as caught:
            self.install(FakeReleases(self.server), verify_app=refuse)
        self.assertIn("did not verify", str(caught.exception))
        self.assertEqual(MODULE.app_release(self.app)["version"], "0.6.0")
        self.assertEqual(sorted(p.name for p in self.app.parent.iterdir()),
                         ["Whisper Face.app"])

    def test_a_release_that_changed_since_the_offer_is_not_installed(self):
        make_release(self.server, "0.6.2", "c" * 40)
        fetch = FakeReleases(self.server)
        with self.assertRaises(MODULE.UpdateError) as caught:
            self.install(fetch)
        self.assertIn("changed since it was offered", str(caught.exception))
        self.assertEqual(fetch.urls, [MODULE.MANIFEST_URL])
        self.assertEqual(MODULE.app_release(self.app)["version"], "0.6.0")

    def test_an_older_release_is_never_installed_even_when_asked(self):
        make_release(self.server, "0.5.9", NEW)
        with self.assertRaises(MODULE.UpdateError) as caught:
            self.install(FakeReleases(self.server), version="0.5.9")
        self.assertIn("not the newer release", str(caught.exception))
        self.assertEqual(MODULE.app_release(self.app)["version"], "0.6.0")

    def test_downloads_stay_on_https_inside_the_published_releases(self):
        opened = []

        def no_network(*_handlers):
            opened.append(_handlers)
            raise AssertionError("a download left the published releases")
        original = MODULE.urllib.request.build_opener
        MODULE.urllib.request.build_opener = no_network
        self.addCleanup(setattr, MODULE.urllib.request, "build_opener", original)
        for url in ("https://example.invalid/update-manifest.json",
                    MODULE.RELEASES.replace("https", "http") + "/x",
                    "https://github.com/someone-else/releases/x"):
            with self.subTest(url=url), self.assertRaises(MODULE.UpdateError):
                MODULE.https_get(url, self.root / "x", 10)
        self.assertEqual(opened, [])
        handler = MODULE._HTTPSOnlyRedirects()
        request = urllib.request.Request(MODULE.MANIFEST_URL)
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(request, None, 302, "Found", {},
                                     "http://objects.example.invalid/x")
        self.assertTrue(MODULE.MANIFEST_URL.startswith(
            "https://github.com/Aiml3ss/whisper-face/releases/"))


class FakeLaunchctl:
    """launchctl for several jobs, in memory, keyed by label. ``starts``
    says whether a loaded job reaches "running"."""

    def __init__(self, *, loaded=("com.berg.dictate", "com.berg.ollama"),
                 starts=True, stops=True):
        self.loaded = set(loaded)
        self.starts, self.stops = starts, stops
        self.calls = []
        self.bootstrapped = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command[1:])
        verb = command[1]
        stdout, code = "", 0
        if verb == "bootout":
            if self.stops:
                self.loaded.discard(command[2].rsplit("/", 1)[1])
        elif verb == "bootstrap":
            plist = Path(command[3])
            self.loaded.add(plistlib.loads(plist.read_bytes())["Label"])
            self.bootstrapped.append(plist.read_bytes())
        elif verb == "print":
            label = command[2].rsplit("/", 1)[1]
            if label not in self.loaded:
                code = 113
            elif self.starts:
                stdout = "state = running\npid = 4242\n"
            else:
                stdout = "state = spawn scheduled\n"
        return subprocess.CompletedProcess(command, code, stdout, "")

    def verbs(self):
        return [call[0] for call in self.calls if call[0] != "print"]


# The app's setup.sh --app --carry-over-from <folder>, standing in for the
# real one: it carries the personal files across, rewrites the login
# services for its own copy as render_dictate_plist and start_ollama_service
# do, retires the folder's launcher only once it proves ours (the real
# macos_launcher_app.verify_owned_app, as adopt_app does), and rebinds the
# receipt. Then it finishes, or fails late with setup's own kind of reason.
ADOPT_SETUP = r"""#!/bin/bash
here="$(cd "$(dirname "$0")" && pwd)"
printf '%s\n' "$@" > "$here/setup-arguments"
echo "@@ phase python"
folder="$4"
for name in dictionary.txt transcripts.jsonl preferences.json; do
    cp "$folder/$name" "$here/$name"
done
"$PYTHON_FOR_TEST" - "$HOME_FOR_TEST" "$here" "$2" <<'PY'
import json, plistlib, shutil, sys
from pathlib import Path
sys.path.insert(0, __import__("os").environ["SCRIPTS_FOR_TEST"])
import macos_launcher_app
home, here, app = (Path(value) for value in sys.argv[1:4])
agents = home / "Library/LaunchAgents"
(agents / "com.berg.dictate.plist").write_bytes(plistlib.dumps(
    {"Label": "com.berg.dictate", "WorkingDirectory": str(here)}))
if (agents / "com.berg.ollama.plist").exists():
    (agents / "com.berg.ollama.plist").write_bytes(plistlib.dumps(
        {"Label": "com.berg.ollama", "StandardOutPath": str(here / "ollama.log")}))
    (home / "Library/Application Support/Whisper Face/ollama-service.sha256"
     ).write_text("f" * 64 + "\n")
launcher = home / "Applications/Whisper Face.app"
try:
    macos_launcher_app.verify_owned_app(launcher)
except (macos_launcher_app.LauncherError, OSError):
    pass
else:
    shutil.rmtree(launcher)
(home / "Library/Application Support/Whisper Face/launcher-install.json"
 ).write_text(json.dumps({"app": str(app), "schema_version": 2,
                          "bundle_id": "com.berg.whisper-face.launcher",
                          "checkout": str(here), "source_revision": "b" * 40,
                          "version": "0.6.1"}))
PY
if [ "${SETUP_SHOULD_FAIL:-0}" = 1 ]; then
    echo "!! the Xcode Command Line Tools are not installed; run xcode-select --install"
    echo "!! Whisper Face setup failed (exit status 1)."
    exit 1
fi
echo "@@ phase service"
echo "@@ phase permissions"
"""


def fake_launcher(path: Path, extra: str = "") -> Path:
    """A launcher a folder install built: the generic layout, every file
    some bytes, its executable executable."""
    for relative in (macos_launcher_app.BASE_FILES
                     | macos_launcher_app.SIGNATURE_FILES):
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"{relative} as built\n".encode())
    (path / "Contents/MacOS/Whisper Face").chmod(0o755)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "com.berg.whisper-face.launcher",
        "CFBundleExecutable": "Whisper Face", "CFBundleShortVersionString": "1.0"}))
    if extra:
        (path / extra).write_text("not the installer's\n")
    return path


def snapshot(folder: Path) -> dict:
    """Every entry under a folder: its bytes or link, mode and mtime."""
    taken = {}
    for path in sorted(folder.rglob("*")):
        status = path.lstat()
        content = (os.readlink(path) if path.is_symlink()
                   else path.read_bytes() if path.is_file() else None)
        taken[str(path.relative_to(folder))] = (content, status.st_mode,
                                                status.st_mtime_ns)
    return taken


@unittest.skipIf(os.name == "nt", "the drag install is Mac-only")
class FolderMoveTests(unittest.TestCase):
    """Moving a folder install (every Mac install before 0.6.0) into the
    app, against a temporary home folder and a fake launchctl."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "home"
        self.layout = MODULE.Layout(self.home)
        self.layout.support.mkdir(parents=True)
        self.layout.support.chmod(0o700)
        # The folder install, as setup.sh from 0.2.0 to 0.5.0 left it.
        self.folder = self.home / "Projects" / "whisper-face"
        self.folder.mkdir(parents=True)
        (self.folder / "dictate.py").write_text("# the folder's runtime\n")
        (self.folder / "setup.sh").write_text("#!/bin/bash\necho folder setup\n")
        (self.folder / ".models/bin").mkdir(parents=True)
        (self.folder / ".models/bin/parrot-asr-helper").write_bytes(b"\xcf\xfa\xed\xfe")
        for name in ("dictionary.txt", "transcripts.jsonl", "preferences.json"):
            (self.folder / name).write_text(f"mine: {name}\n")
            (self.folder / name).chmod(0o600)
        self.receipt_bytes = json.dumps({
            "app_bundle_sha256": "d" * 64,
            "bundle_id": "com.berg.whisper-face.launcher",
            "checkout": str(self.folder), "schema_version": 1,
            "source_revision": OLD}, sort_keys=True).encode() + b"\n"
        self.layout.receipt.write_bytes(self.receipt_bytes)
        self.layout.receipt.chmod(0o600)
        self.layout.agent.parent.mkdir(parents=True)
        self.agent_bytes = plistlib.dumps({
            "Label": "com.berg.dictate", "WorkingDirectory": str(self.folder)})
        self.layout.agent.write_bytes(self.agent_bytes)
        self.layout.agent.chmod(0o600)
        self.ollama_bytes = plistlib.dumps({
            "Label": "com.berg.ollama",
            "StandardOutPath": str(self.folder / "ollama.log")})
        self.layout.ollama_agent.write_bytes(self.ollama_bytes)
        self.ollama_receipt_bytes = b"e" * 64 + b"\n"
        self.layout.ollama_receipt.write_bytes(self.ollama_receipt_bytes)
        fake_launcher(self.layout.launcher)
        self.launcher_digest = macos_launcher_app._bundle_digest(self.layout.launcher)
        # The drag app, and the release it unpacked as its own copy.
        self.app = fake_app(self.root / "Applications" / "Whisper Face.app",
                            "0.6.1", NEW)
        write_release(self.layout.checkout, "0.6.1", NEW, ADOPT_SETUP)
        self.folder_before = snapshot(self.folder)
        os.environ["HOME_FOR_TEST"] = str(self.home)
        os.environ["PYTHON_FOR_TEST"] = sys.executable
        os.environ["SCRIPTS_FOR_TEST"] = str(ROOT / "scripts")
        for name in ("HOME_FOR_TEST", "PYTHON_FOR_TEST", "SCRIPTS_FOR_TEST",
                     "SETUP_SHOULD_FAIL"):
            self.addCleanup(os.environ.pop, name, None)

    def move(self, launchctl, **kwargs):
        out = io.StringIO()
        status = MODULE.adopt_folder_install(
            app=self.app, home=self.home, uid=UID, runner=launchctl,
            sleep=lambda _seconds: None, out=out, **kwargs)
        return status, out.getvalue().splitlines()

    def assert_folder_install_is_back(self):
        self.assertEqual(self.layout.receipt.read_bytes(), self.receipt_bytes)
        self.assertEqual(self.layout.agent.read_bytes(), self.agent_bytes)
        self.assertEqual(self.layout.ollama_agent.read_bytes(), self.ollama_bytes)
        self.assertEqual(self.layout.ollama_receipt.read_bytes(),
                         self.ollama_receipt_bytes)
        self.assertEqual(macos_launcher_app._bundle_digest(self.layout.launcher),
                         self.launcher_digest)
        macos_launcher_app.verify_owned_app(self.layout.launcher)
        self.assertEqual(snapshot(self.folder), self.folder_before)

    def test_a_folder_install_moves_into_the_app_and_the_folder_is_only_read(self):
        launchctl = FakeLaunchctl()
        status, lines = self.move(launchctl)
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        # The folder's service was stopped before anything was copied, and
        # the app's own setup ran, carrying the folder's personal files.
        self.assertEqual(launchctl.calls[2], ["bootout", f"gui/{UID}/com.berg.dictate"])
        self.assertEqual(launchctl.verbs(), ["bootout"])
        arguments = (self.layout.checkout / "setup-arguments").read_text().split("\n")
        self.assertEqual(arguments[:4], ["--app", str(self.app),
                                         "--carry-over-from", str(self.folder)])
        for name in ("dictionary.txt", "transcripts.jsonl", "preferences.json"):
            self.assertEqual((self.layout.checkout / name).read_text(),
                             f"mine: {name}\n")
        # Setup retired the launcher the folder install built; the receipt
        # now binds the app.
        self.assertFalse(self.layout.launcher.exists())
        self.assertEqual(json.loads(self.layout.receipt.read_text())["app"],
                         str(self.app))
        # The folder is exactly as it was, and the window can say so.
        self.assertEqual(snapshot(self.folder), self.folder_before)
        self.assertIn(f"== Whisper Face runs from {self.app} now; the folder "
                      f"{self.folder} is no longer used and can be deleted", lines)
        self.assertIn("@@ phase python", lines)
        self.assertFalse(self.layout.move_journal.exists())
        self.assertFalse(self.layout.move_backup.exists())
        self.assertIn("app-move moved aaaaaaa->bbbbbbb", self.layout.log.read_text())

    def test_a_failed_move_puts_the_folder_install_back_exactly(self):
        os.environ["SETUP_SHOULD_FAIL"] = "1"
        launchctl = FakeLaunchctl()
        status, lines = self.move(launchctl)
        self.assertEqual(status, MODULE.EXIT_ROLLED_BACK, "\n".join(lines))
        # Service definitions, receipts and the launcher, byte for byte, and
        # both services loaded again from the folder's own definitions.
        self.assert_folder_install_is_back()
        self.assertEqual(launchctl.loaded, {"com.berg.dictate", "com.berg.ollama"})
        self.assertEqual(sorted(launchctl.bootstrapped),
                         sorted([self.agent_bytes, self.ollama_bytes]))
        # The window's reason is setup's own last "!!" line.
        reasons = [line for line in lines if line.startswith("!! ")]
        self.assertEqual(reasons[-1], "!! the Xcode Command Line Tools are not "
                         "installed; run xcode-select --install")
        self.assertFalse(self.layout.move_journal.exists())
        self.assertFalse(self.layout.move_backup.exists())
        self.assertIn("app-move rolled_back", self.layout.log.read_text())
        # Try Again moves it, from the copy the app already unpacked.
        os.environ["SETUP_SHOULD_FAIL"] = "0"
        status, lines = self.move(FakeLaunchctl())
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertEqual(snapshot(self.folder), self.folder_before)

    def test_a_put_back_that_cannot_be_seen_running_is_never_called_one(self):
        os.environ["SETUP_SHOULD_FAIL"] = "1"
        # Only the dictation service: its own start is what is judged.
        status, lines = self.move(FakeLaunchctl(loaded=("com.berg.dictate",),
                                                starts=False))
        self.assertEqual(status, MODULE.EXIT_ROLLBACK_FAILED, "\n".join(lines))
        self.assertTrue(self.layout.move_journal.exists())
        self.assertIn("app-move rollback_failed", self.layout.log.read_text())
        # The next attempt puts the folder version back first, then moves.
        os.environ["SETUP_SHOULD_FAIL"] = "0"
        status, lines = self.move(FakeLaunchctl())
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertIn("== an earlier move into the app did not finish; putting "
                      "the folder version back first", lines)
        self.assertIn("app-move recovered", self.layout.log.read_text())
        self.assertEqual(snapshot(self.folder), self.folder_before)

    def test_a_move_cut_off_after_setup_rebound_everything_is_put_back_first(self):
        real_stream = MODULE._stream

        def setup_then_power_loss(command, say):
            real_stream(command, say)
            raise SystemExit("power lost")

        with unittest.mock.patch.object(MODULE, "_stream", setup_then_power_loss):
            with self.assertRaises(SystemExit):
                self.move(FakeLaunchctl())
        # What the app finds on its next open: a journal naming the folder.
        self.assertEqual(json.loads(self.layout.move_journal.read_text())["folder"],
                         str(self.folder))
        self.assertFalse(self.layout.launcher.exists())
        launchctl = FakeLaunchctl(loaded=())
        restored = MODULE.recover_interrupted_move(
            self.layout, runner=launchctl, uid=UID, say=lambda _line: None,
            sleep=lambda _seconds: None)
        self.assertTrue(restored)
        self.assert_folder_install_is_back()
        self.assertFalse(self.layout.move_journal.exists())

    def test_services_that_were_not_loaded_stay_unloaded_when_put_back(self):
        os.environ["SETUP_SHOULD_FAIL"] = "1"
        launchctl = FakeLaunchctl(loaded=())
        status, lines = self.move(launchctl)
        self.assertEqual(status, MODULE.EXIT_ROLLED_BACK, "\n".join(lines))
        self.assert_folder_install_is_back()
        self.assertEqual(launchctl.loaded, set())
        self.assertNotIn("bootstrap", launchctl.verbs())

    def test_an_app_in_the_launchers_place_that_is_not_ours_is_left_alone(self):
        shutil.rmtree(self.layout.launcher)
        fake_launcher(self.layout.launcher, extra="Contents/Resources/someone-elses")
        before = snapshot(self.layout.launcher)
        self.assertFalse(MODULE._folder_launcher(self.layout, self.app))
        for fail in ("1", "0"):
            with self.subTest(fail=fail):
                os.environ["SETUP_SHOULD_FAIL"] = fail
                status, lines = self.move(FakeLaunchctl())
                self.assertIn(status, (MODULE.EXIT_ROLLED_BACK, MODULE.EXIT_APPLIED),
                              "\n".join(lines))
                self.assertEqual(snapshot(self.layout.launcher), before)
        # Nor is the drag app taken for the folder's launcher when the
        # person put it there.
        self.assertFalse(MODULE._folder_launcher(self.layout, self.layout.launcher))

    def test_only_a_whole_folder_install_is_moved_and_a_refusal_changes_nothing(self):
        receipt = json.loads(self.receipt_bytes)

        def write_receipt(**changes):
            self.layout.receipt.write_text(json.dumps({**receipt, **changes}))

        cases = {
            "not set up from a folder": lambda: write_receipt(
                schema_version=2, app=str(self.app), version="0.6.0"),
            "no longer holds Whisper Face": lambda: (
                self.folder / "dictate.py").unlink(),
            "no longer there": lambda: write_receipt(
                checkout=str(self.root / "gone")),
            "inside the app's own folder": lambda: write_receipt(
                checkout=str(self.layout.checkout)),
            "runs another copy": lambda: self.layout.agent.write_bytes(
                plistlib.dumps({"Label": "com.berg.dictate",
                                "WorkingDirectory": str(self.root)})),
            "not the release it carries": lambda: write_release(
                self.layout.checkout, "0.6.2", "c" * 40, ADOPT_SETUP),
        }
        for words, spoil in cases.items():
            with self.subTest(words):
                self.setUp()
                spoil()
                before = snapshot(self.home)
                launchctl = FakeLaunchctl()
                status, lines = self.move(launchctl)
                self.assertEqual(status, MODULE.EXIT_REFUSED, "\n".join(lines))
                self.assertIn(words, lines[-1])
                self.assertEqual(launchctl.calls, [])
                self.assertEqual(snapshot(self.home), before)

    def test_a_dry_run_reads_and_plans_and_changes_nothing(self):
        (self.layout.checkout / "setup.sh").write_text('#!/bin/bash\necho "dry: $*"\n')
        before = snapshot(self.home)
        launchctl = FakeLaunchctl()
        status, lines = self.move(launchctl, dry_run=True)
        self.assertEqual(status, MODULE.EXIT_APPLIED, "\n".join(lines))
        self.assertEqual(launchctl.calls, [])
        self.assertIn(f"dry: --app {self.app} --dry-run --carry-over-from "
                      f"{self.folder}", lines)
        self.assertIn(f"== {self.folder} is only read, never changed", lines)
        self.assertEqual(snapshot(self.home), before)


if __name__ == "__main__":
    unittest.main()
