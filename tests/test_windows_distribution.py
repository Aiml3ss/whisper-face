# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TOOL = ROOT / "scripts" / "release_manifest.py"
BUNDLE_TOOL = ROOT / "scripts" / "windows_bundle.py"
PACKAGE_SCRIPT = ROOT / "scripts" / "package_windows.sh"
ENTRY_POINT = ROOT / "Install.cmd"
REVISION = "1" * 40
PREVIOUS_REVISION = "2" * 40


def load_bundle_tool():
    """Import the bundle tool for the checks that need no packaging run."""
    specification = importlib.util.spec_from_file_location(
        "whisper_face_windows_bundle", BUNDLE_TOOL)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class WindowsArtifactKindTests(unittest.TestCase):
    """A Windows installer must not be published as a plain source archive."""

    def run_tool(self, *arguments: str, check: bool = True):
        return subprocess.run(
            [sys.executable, str(MANIFEST_TOOL), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=check,
        )

    def create(self, directory: Path, *names: str, **extra: str):
        artifacts = []
        for name in names:
            artifact = directory / name
            artifact.write_bytes(name.encode("utf-8"))
            artifacts.append(artifact)
        manifest = directory / "update-manifest.json"
        arguments = [
            "create",
            "--version", extra.get("version", "1.2.3"),
            "--revision", REVISION,
            "--download-base-url", "https://example.invalid/releases/v1.2.3",
            "--output", str(manifest),
        ]
        for artifact in artifacts:
            arguments += ["--artifact", str(artifact)]
        return manifest, arguments

    def test_windows_bundle_is_an_installer_for_x64_not_a_source_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest, arguments = self.create(
                output, "WhisperFace-1.2.3-windows-x64.zip")
            self.run_tool(*arguments)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            artifact = payload["artifacts"][0]
            self.assertEqual(artifact["kind"], "windows-source-bundle")
            self.assertEqual(artifact["role"], "installer")
            self.assertEqual(artifact["architectures"], ["x64"])
            # Nothing signs a Windows artifact here, and the manifest says so
            # rather than leaving the question open.
            self.assertFalse(artifact["signed"])
            self.assertFalse(artifact["notarized"])
            self.assertEqual(
                artifact["sha256"],
                hashlib.sha256(
                    (output / artifact["name"]).read_bytes()).hexdigest())
            # The install contract follows the artifacts: a Windows download
            # must never tell its user to double-click a Mac entry point.
            self.assertEqual(payload["installation"]["entrypoint"], "Install.cmd")
            self.assertEqual(
                payload["installation"]["verification"], ".\\setup.ps1 --verify")
            self.assertEqual(payload["minimum_windows"], "10")
            self.assertNotIn("minimum_macos", payload)
            self.run_tool(
                "verify", "--manifest", str(manifest), "--artifact-dir", str(output))

    def test_the_plain_source_archive_contract_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest, arguments = self.create(
                output,
                "WhisperFace-1.2.3-source.zip",
                "WhisperFace-1.2.3-macOS-arm64.dmg",
            )
            self.run_tool(*arguments)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            by_name = {item["name"]: item for item in payload["artifacts"]}
            source = by_name["WhisperFace-1.2.3-source.zip"]
            self.assertEqual(source["kind"], "source-archive")
            self.assertEqual(source["role"], "corresponding-source")
            self.assertEqual(source["architectures"], ["any"])
            self.assertEqual(payload["installation"]["entrypoint"], "Install.command")
            self.assertEqual(payload["minimum_macos"], "14.0")
            self.assertNotIn("minimum_windows", payload)
            self.run_tool(
                "verify", "--manifest", str(manifest), "--artifact-dir", str(output))

    def test_verifier_rejects_a_windows_bundle_labelled_as_source(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest, arguments = self.create(
                output, "WhisperFace-1.2.3-windows-x64.zip")
            self.run_tool(*arguments)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            for field, value in (
                ("kind", "source-archive"),
                ("role", "corresponding-source"),
            ):
                with self.subTest(field=field):
                    tampered = json.loads(json.dumps(payload))
                    tampered["artifacts"][0][field] = value
                    manifest.write_text(json.dumps(tampered), encoding="utf-8")
                    result = self.run_tool(
                        "verify", "--manifest", str(manifest),
                        "--artifact-dir", str(output), check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("kind/role mismatch", result.stderr)
            tampered = json.loads(json.dumps(payload))
            tampered["artifacts"][0]["architectures"] = ["any"]
            manifest.write_text(json.dumps(tampered), encoding="utf-8")
            architecture = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False)
            self.assertEqual(architecture.returncode, 2)
            self.assertIn("architecture mismatch", architecture.stderr)

    def test_verifier_rejects_a_windows_manifest_claiming_the_mac_install_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest, arguments = self.create(
                output, "WhisperFace-1.2.3-windows-x64.zip")
            self.run_tool(*arguments)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["installation"]["entrypoint"] = "Install.command"
            payload["installation"]["verification"] = "./setup.sh --verify"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("installation contract", result.stderr)

            payload["installation"]["entrypoint"] = "Install.cmd"
            payload["installation"]["verification"] = ".\\setup.ps1 --verify"
            del payload["minimum_windows"]
            payload["minimum_macos"] = "14.0"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            minimum = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False)
            self.assertEqual(minimum.returncode, 2)
            self.assertIn("minimum_windows", minimum.stderr)

    def test_one_manifest_never_mixes_two_platforms_install_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            _, arguments = self.create(
                output,
                "WhisperFace-1.2.3-windows-x64.zip",
                "WhisperFace-1.2.3-macOS-arm64.dmg",
            )
            result = self.run_tool(*arguments, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("one platform's install path", result.stderr)

    def test_rollback_metadata_survives_the_windows_artifact_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest, arguments = self.create(
                output, "WhisperFace-1.2.3-windows-x64.zip")
            self.run_tool(
                *arguments,
                "--previous-version", "1.2.2",
                "--previous-revision", PREVIOUS_REVISION,
                "--previous-manifest-url",
                "https://example.invalid/releases/v1.2.2/update-manifest.json",
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertTrue(payload["rollback"]["supported"])
            self.assertEqual(payload["rollback"]["version"], "1.2.2")
            self.assertEqual(
                payload["source_offer"]["license"], "AGPL-3.0-only")


class WindowsEntryPointTests(unittest.TestCase):
    """Windows refuses a double-clicked .ps1; the shipped shim is the fix."""

    @classmethod
    def setUpClass(cls):
        cls.entry = ENTRY_POINT.read_text(encoding="utf-8")

    def test_entry_point_launches_the_bundled_installer(self):
        self.assertTrue(ENTRY_POINT.is_file())
        # The default execution policy is what stops a downloaded installer,
        # so bypassing it for this one invocation is the whole point.
        self.assertIn("-ExecutionPolicy Bypass", self.entry)
        self.assertIn("-File", self.entry)
        self.assertIn("-NoProfile", self.entry)
        # %~dp0 keeps the target inside the extracted bundle, and the quotes
        # keep it working under "C:\Users\Someone\Whisper Face 1.2.3".
        self.assertIn('-File "%~dp0setup.ps1"', self.entry)
        self.assertIn('cd /d "%~dp0"', self.entry)
        # Nothing may resolve setup.ps1 through the current directory or PATH.
        self.assertNotIn("-File setup.ps1", self.entry)
        self.assertNotIn(".\\setup.ps1", self.entry)
        self.assertNotIn("-Command", self.entry)
        # Arguments reach setup.ps1 unchanged, so --verify and --uninstall work.
        self.assertIn("%*", self.entry)
        # A double-click gets a window that stays open long enough to read.
        self.assertIn("pause", self.entry)
        self.assertIn("%ERRORLEVEL%", self.entry)
        self.assertTrue(self.entry.startswith("@echo off"))

    def test_entry_point_uses_the_line_endings_cmd_expects(self):
        raw = ENTRY_POINT.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        # git archive applies this attribute, so the release bundle inherits it.
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("/Install.cmd text eol=crlf", attributes)

    def test_the_installer_it_targets_accepts_what_the_docs_promise(self):
        installer = (ROOT / "setup.ps1").read_text(encoding="utf-8")
        for flag in ("--server-only", "--verify", "--uninstall", "--yes"):
            with self.subTest(flag=flag):
                self.assertIn(f'"{flag}"', installer)
        self.assertIn("Usage: .\\setup.ps1 [--server-only] [--verify]", installer)
        # Safe to rerun is a promise the bundle's instructions repeat.
        self.assertIn("Safe to rerun", installer)

    def test_bundle_instructions_name_the_entry_point_and_its_limits(self):
        module = load_bundle_tool()
        readme = module.render_readme("1.2.3", REVISION)
        for expected in (
            "Install.cmd",
            "Whisper Face 1.2.3",
            REVISION,
            "unsigned",
            "winget",
            "Unblock",
            ".\\setup.ps1 --verify",
            "AGPL-3.0-only",
            # Install.command sits beside Install.cmd in the same folder.
            "Install.command",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, readme)
        with self.assertRaises(module.BundleError):
            module.render_readme("not-a-version", REVISION)
        with self.assertRaises(module.BundleError):
            module.render_readme("1.2.3", "short")


class WindowsBundleToolTests(unittest.TestCase):
    """The ZIP must survive Windows filesystem rules and prove its source."""

    def setUp(self):
        self.module = load_bundle_tool()

    def test_paths_windows_cannot_create_are_rejected(self):
        for name in (
            "Whisper Face 1.2.3/aux.txt",
            "Whisper Face 1.2.3/COM1",
            "Whisper Face 1.2.3/what?.md",
            "Whisper Face 1.2.3/a:b",
            "Whisper Face 1.2.3/trailing.",
            "Whisper Face 1.2.3/trailing ",
            "Whisper Face 1.2.3/../escape",
            "/absolute",
            "Whisper Face 1.2.3\\backslash",
        ):
            with self.subTest(name=name):
                with self.assertRaises(self.module.BundleError):
                    self.module._check_windows_paths([name])
        # Windows folds case, so two members differing only in case collide.
        with self.assertRaises(self.module.BundleError):
            self.module._check_windows_paths(["a/README.md", "a/readme.md"])
        self.module._check_windows_paths(
            ["Whisper Face 1.2.3/", "Whisper Face 1.2.3/Install.cmd"])

    def test_macos_resource_forks_and_stray_roots_are_rejected(self):
        for names in (
            ["Whisper Face 1.2.3/x", "START HERE.txt", "__MACOSX/._x"],
            ["Whisper Face 1.2.3/x", "START HERE.txt", "Whisper Face 1.2.3/._x"],
        ):
            with self.subTest(names=names):
                with self.assertRaises(self.module.BundleError):
                    self.module._check_layout(names, "Whisper Face 1.2.3")
        with self.assertRaises(self.module.BundleError):
            self.module._check_layout(["Whisper Face 1.2.3/x"], "Whisper Face 1.2.3")
        self.module._check_layout(
            ["Whisper Face 1.2.3/x", "START HERE.txt"], "Whisper Face 1.2.3")

    def test_an_edited_entry_point_fails_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Whisper Face 1.2.3"
            root.mkdir()
            (root / "setup.ps1").write_text("# installer\n", encoding="utf-8")
            shutil.copyfile(ENTRY_POINT, root / "Install.cmd")
            self.module._check_entry_point(root)
            # Losing the execution-policy bypass is silent on macOS and fatal
            # on the machine that has to run it.
            (root / "Install.cmd").write_text(
                'cd /d "%~dp0"\npowershell.exe -NoProfile '
                '-File "%~dp0setup.ps1" %*\n',
                encoding="utf-8")
            with self.assertRaises(self.module.BundleError):
                self.module._check_entry_point(root)
            shutil.copyfile(ENTRY_POINT, root / "Install.cmd")
            (root / "setup.ps1").unlink()
            with self.assertRaises(self.module.BundleError):
                self.module._check_entry_point(root)

    def test_private_state_and_missing_essentials_fail_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Whisper Face 1.2.3"
            root.mkdir()
            for name in self.module.REQUIRED_BUNDLE_FILES:
                (root / name).write_text("x", encoding="utf-8")
            self.module._check_bundle_contents(root)
            for name in ("dictate.py", "dictate.py.lock", "setup.ps1"):
                with self.subTest(missing=name):
                    (root / name).rename(root / "moved")
                    with self.assertRaises(self.module.BundleError):
                        self.module._check_bundle_contents(root)
                    (root / "moved").rename(root / name)
            for personal in ("transcripts.jsonl", "dictionary.txt", "preferences.json"):
                with self.subTest(personal=personal):
                    leaked = root / personal
                    leaked.write_text("private", encoding="utf-8")
                    with self.assertRaises(self.module.BundleError):
                        self.module._check_bundle_contents(root)
                    leaked.unlink()
            evidence = root / ".evidence"
            evidence.mkdir()
            with self.assertRaises(self.module.BundleError):
                self.module._check_bundle_contents(root)

    @unittest.skipIf(
        os.name == "nt", "POSIX modes and symlinks are not a Windows contract")
    def test_archive_is_deterministic_and_round_trips_modes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "staged"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "plain.txt").write_text("exact\n", encoding="utf-8")
            script = root / "nested" / "run.sh"
            script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            (root / "link.txt").symlink_to("nested/plain.txt")
            first = base / "first.zip"
            second = base / "second.zip"
            counts = self.module.archive_bundle(root, first, 1700000000)
            self.assertEqual(counts["symlinks"], 1)
            self.assertEqual(counts["directories"], 1)
            self.module.archive_bundle(root, second, 1700000000)
            self.assertEqual(first.read_bytes(), second.read_bytes())

            restored = base / "restored"
            restored.mkdir()
            self.module._extract(first, restored)
            self.assertTrue((restored / "link.txt").is_symlink())
            self.assertEqual(
                os.readlink(restored / "link.txt"), "nested/plain.txt")
            self.assertTrue(
                os.access(restored / "nested" / "run.sh", os.X_OK))
            self.assertFalse(
                os.access(restored / "nested" / "plain.txt", os.X_OK))
            self.assertEqual(
                (restored / "nested" / "plain.txt").read_text(encoding="utf-8"),
                "exact\n")
            with zipfile.ZipFile(first) as handle:
                for member in handle.infolist():
                    with self.subTest(member=member.filename):
                        self.assertNotIn("\\", member.filename)
                        self.assertEqual(member.date_time[0], 2023)
            # A pre-1980 stamp cannot be stored in a ZIP at all; say so rather
            # than silently shipping a wrong date.
            with self.assertRaises(self.module.BundleError):
                self.module.archive_bundle(root, base / "old.zip", 0)


@unittest.skipIf(
    os.name == "nt", "the packaging script is a POSIX build-host contract")
class WindowsPackagerTests(unittest.TestCase):
    """Build the real bundle and prove what a downloader would find in it."""

    bundle = None
    output = None
    revision = None
    temporary = None

    @classmethod
    def setUpClass(cls):
        if shutil.which("bash") is None or shutil.which("git") is None:
            raise unittest.SkipTest("bash and git are required to package")
        cls.revision = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True).stdout.strip()
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="whisper-face-windows-package.")
        cls.output = Path(cls.temporary.name) / "dist"
        subprocess.run(
            [
                "bash", str(PACKAGE_SCRIPT),
                "--version", "1.2.3",
                "--revision", cls.revision,
                "--channel", "preview",
                "--output-dir", str(cls.output),
            ],
            cwd=ROOT, text=True, capture_output=True, check=True)
        cls.bundle = cls.output / "WhisperFace-1.2.3-windows-x64.zip"

    @classmethod
    def tearDownClass(cls):
        if cls.temporary is not None:
            cls.temporary.cleanup()

    def test_the_packaged_bundle_verifies_and_detects_tampering(self):
        verified = subprocess.run(
            [
                sys.executable, str(BUNDLE_TOOL), "verify",
                "--bundle-zip", str(self.bundle),
                "--version", "1.2.3", "--revision", self.revision,
            ],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("verified Windows bundle tree", verified.stdout)
        for version, revision in (("1.2.4", self.revision), ("1.2.3", "0" * 40)):
            with self.subTest(version=version, revision=revision):
                mismatched = subprocess.run(
                    [
                        sys.executable, str(BUNDLE_TOOL), "verify",
                        "--bundle-zip", str(self.bundle),
                        "--version", version, "--revision", revision,
                    ],
                    cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(mismatched.returncode, 2)

    def test_the_bundle_holds_the_exact_unmodified_source_and_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory)
            load_bundle_tool()._extract(self.bundle, extracted)
            self.assertEqual(
                sorted(item.name for item in extracted.iterdir()),
                ["START HERE.txt", "Whisper Face 1.2.3"])
            root = extracted / "Whisper Face 1.2.3"
            for name in ("Install.cmd", "setup.ps1", "dictate.py", "dictate.py.lock"):
                with self.subTest(name=name):
                    self.assertTrue((root / name).is_file())
            # Every shipped file is the committed one, byte for byte.
            for name in (
                "setup.ps1", "dictate.py", "dictate.py.lock", "Install.cmd"
            ):
                with self.subTest(unmodified=name):
                    self.assertEqual(
                        (root / name).read_bytes(), (ROOT / name).read_bytes())
            # git archive applies the CRLF attribute on the way into the ZIP.
            self.assertIn(b"\r\n", (root / "Install.cmd").read_bytes())
            self.assertIn(
                'powershell.exe -NoProfile -ExecutionPolicy Bypass '
                '-File "%~dp0setup.ps1"',
                (root / "Install.cmd").read_text(encoding="utf-8"))
            # The revision proof the runtime's /source endpoint depends on.
            self.assertTrue((root / ".git").is_dir())
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                text=True, capture_output=True, check=True).stdout.strip()
            self.assertEqual(head, self.revision)
            metadata = json.loads(
                (root / "RELEASE-METADATA.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_revision"], self.revision)
            self.assertEqual(metadata["license"], "AGPL-3.0-only")
            for expected in (
                "LICENSE", "LICENSE_POLICY.md", "NOTICE",
                "THIRD_PARTY_NOTICES.md", "PACKAGE-CONTENTS.json",
            ):
                with self.subTest(expected=expected):
                    self.assertTrue((root / expected).is_file())
            names = {path.name for path in root.rglob("*")}
            for forbidden in (
                ".evidence", "transcripts.jsonl", "dictionary.txt",
                "preferences.json", "learned.json", "dictate.log",
                "__pycache__", ".DS_Store", ".windows",
            ):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_the_tree_digest_is_deterministic_across_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            second = Path(directory) / "dist"
            subprocess.run(
                [
                    "bash", str(PACKAGE_SCRIPT),
                    "--version", "1.2.3",
                    "--revision", self.revision,
                    "--channel", "preview",
                    "--output-dir", str(second),
                ],
                cwd=ROOT, text=True, capture_output=True, check=True)
            receipts = []
            for bundle in (self.bundle, second / self.bundle.name):
                with zipfile.ZipFile(bundle) as handle:
                    receipts.append(json.loads(handle.read(
                        "Whisper Face 1.2.3/PACKAGE-CONTENTS.json")))
            self.assertEqual(receipts[0], receipts[1])
            self.assertEqual(receipts[0]["source_revision"], self.revision)
            self.assertEqual(receipts[0]["version"], "1.2.3")
            self.assertEqual(receipts[0]["root_name"], "Whisper Face 1.2.3")

    def test_the_manifest_and_checksums_cover_the_published_bundle(self):
        manifest = json.loads(
            (self.output / "update-manifest.json").read_text(encoding="utf-8"))
        artifact = manifest["artifacts"][0]
        self.assertEqual(artifact["kind"], "windows-source-bundle")
        self.assertEqual(artifact["name"], self.bundle.name)
        self.assertEqual(
            artifact["sha256"], hashlib.sha256(self.bundle.read_bytes()).hexdigest())
        self.assertEqual(artifact["size"], self.bundle.stat().st_size)
        self.assertEqual(manifest["source_offer"]["revision"], self.revision)
        sums = (self.output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        recorded = {name: digest for digest, name in
                    (line.split("  ", 1) for line in sums)}
        self.assertEqual(
            recorded[self.bundle.name],
            hashlib.sha256(self.bundle.read_bytes()).hexdigest())
        self.assertIn("update-manifest.json", recorded)


class WindowsReleaseContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_packager_exports_one_exact_source_and_signs_nothing(self):
        script = self.read("scripts/package_windows.sh")
        for expected in (
            'git -C "$REPO_DIR" archive "$FULL_REVISION"',
            "fetch -q --depth 1",
            "config core.logAllRefUpdates false",
            "packaged checkout lost its immutable source revision",
            "https://github.com/Aiml3ss/whisper-face.git",
            "RELEASE-METADATA.json",
            "SOURCE_DATE_EPOCH",
            'verify_macos_package.py" stamp',
            'windows_bundle.py" archive',
            'windows_bundle.py" verify',
            "WhisperFace-$VERSION-windows-x64.zip",
            "update-manifest.json",
            "SHA256SUMS",
            "release revision does not contain Install.cmd",
            "--previous-manifest-url",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)
        # Comments may name what the script deliberately does not do; the
        # executable body may not contain it.
        body = "\n".join(
            line for line in script.splitlines()
            if not line.lstrip().startswith("#"))
        # No signing story exists, so no half-finished signing path may either.
        for absent in (
            "codesign", "notarytool", "stapler", "signtool", "APPLE_",
            "SIGNING_CERTIFICATE", "--sign", "--notarize",
        ):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, body)
        # Nothing here may need a Windows host or an emulator to run.
        for absent in ("wine", "powershell", "pwsh", "ditto ", "hdiutil"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, body.casefold())
        for option in (
            "--version", "--revision", "--output-dir", "--channel",
            "--previous-version", "--previous-revision", "--previous-manifest-url",
        ):
            with self.subTest(option=option):
                self.assertIn(option, script)
        if os.name == "posix":
            subprocess.run(["bash", "-n", str(PACKAGE_SCRIPT)], check=True)

    def test_windows_runbook_is_honest_about_what_the_bundle_proves(self):
        runbook = self.read("docs/distribution/windows-release.md")
        for expected in (
            "scripts/package_windows.sh",
            "WhisperFace-VERSION-windows-x64.zip",
            "Install.cmd",
            ".\\setup.ps1 --verify",
            "Authenticode",
            "SHA256SUMS",
            "PACKAGE-CONTENTS.json",
            "corresponding source",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, runbook)
        # The Mac runbook must point at its Windows sibling, or nobody finds it.
        self.assertIn(
            "windows-release.md", self.read("docs/distribution/macos-release.md"))

    def test_the_new_gate_is_in_every_mirrored_list(self):
        gate = "uv run tests/test_windows_distribution.py"
        for relative in (
            "AGENTS.md",
            "docs/installer-release-process.md",
            ".github/workflows/macos-release.yml",
            ".github/workflows/windows-smoke.yml",
            ".github/pull_request_template.md",
        ):
            with self.subTest(gate_list=relative):
                self.assertIn(gate, self.read(relative))


if __name__ == "__main__":
    unittest.main()
