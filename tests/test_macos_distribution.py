# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import hashlib
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TOOL = ROOT / "scripts" / "release_manifest.py"
PACKAGE_SCRIPT = ROOT / "scripts" / "package_macos.sh"
PACKAGE_VERIFIER = ROOT / "scripts" / "verify_macos_package.py"
LAUNCHER_TOOL = ROOT / "scripts" / "macos_launcher_app.py"
LAUNCHER_ICON = ROOT / "icons" / "WhisperFace.icns"
REVISION = "1" * 40
PREVIOUS_REVISION = "2" * 40


def load_launcher_tool():
    """Import the launcher builder for the checks that need no Swift toolchain."""
    specification = importlib.util.spec_from_file_location(
        "whisper_face_launcher_tool", LAUNCHER_TOOL)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ReleaseManifestTests(unittest.TestCase):
    def run_tool(self, *arguments: str, check: bool = True):
        return subprocess.run(
            [sys.executable, str(MANIFEST_TOOL), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=check,
        )

    def test_manifest_binds_artifacts_source_trust_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            source = output / "WhisperFace-1.2.3-source.zip"
            disk_image = output / "WhisperFace-1.2.3-macOS-arm64.dmg"
            source.write_bytes(b"exact source")
            disk_image.write_bytes(b"signed disk image")
            manifest = output / "update-manifest.json"

            self.run_tool(
                "create",
                "--version", "1.2.3",
                "--revision", REVISION,
                "--published-at", "2026-07-21T20:00:00Z",
                "--download-base-url",
                "https://example.invalid/releases/v1.2.3",
                "--artifact", str(source),
                "--artifact", str(disk_image),
                "--signed-artifact", disk_image.name,
                "--notarized-artifact", disk_image.name,
                "--previous-version", "1.2.2",
                "--previous-revision", PREVIOUS_REVISION,
                "--previous-manifest-url",
                "https://example.invalid/releases/v1.2.2/update-manifest.json",
                "--output", str(manifest),
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["source_offer"]["revision"], REVISION)
            self.assertEqual(payload["source_offer"]["license"], "AGPL-3.0-only")
            self.assertEqual(payload["rollback"]["version"], "1.2.2")
            self.assertTrue(payload["rollback"]["supported"])
            installation = payload["installation"]
            self.assertFalse(installation["preserves_private_state"])
            self.assertTrue(
                installation["same_checkout_reinstall_preserves_private_state"]
            )
            self.assertTrue(
                installation[
                    "separate_checkout_requires_manual_private_state_migration"
                ]
            )
            by_name = {item["name"]: item for item in payload["artifacts"]}
            self.assertEqual(
                by_name[source.name]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
            )
            self.assertFalse(by_name[source.name]["signed"])
            self.assertEqual(by_name[source.name]["architectures"], ["any"])
            self.assertTrue(by_name[disk_image.name]["signed"])
            self.assertTrue(by_name[disk_image.name]["notarized"])
            self.assertEqual(by_name[disk_image.name]["architectures"], ["arm64"])
            self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output),
            )

    def test_verifier_fails_closed_after_artifact_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            artifact = output / "WhisperFace-2.0.0-source.zip"
            artifact.write_bytes(b"before")
            manifest = output / "update-manifest.json"
            self.run_tool(
                "create",
                "--version", "2.0.0",
                "--revision", REVISION,
                "--download-base-url", "https://example.invalid/v2.0.0",
                "--artifact", str(artifact),
                "--output", str(manifest),
            )
            artifact.write_bytes(b"after")
            result = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("mismatch", result.stderr)

    def test_verifier_rejects_false_architecture_or_state_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            artifact = output / "WhisperFace-2.0.0-source.zip"
            artifact.write_bytes(b"source")
            manifest = output / "update-manifest.json"
            self.run_tool(
                "create",
                "--version", "2.0.0",
                "--revision", REVISION,
                "--download-base-url", "https://example.invalid/v2.0.0",
                "--artifact", str(artifact),
                "--output", str(manifest),
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["artifacts"][0]["architectures"] = ["arm64"]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            architecture = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False,
            )
            self.assertEqual(architecture.returncode, 2)
            self.assertIn("architecture mismatch", architecture.stderr)

            payload["artifacts"][0]["architectures"] = ["any"]
            payload["installation"]["preserves_private_state"] = True
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            state = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False,
            )
            self.assertEqual(state.returncode, 2)
            self.assertIn("must not be unconditional", state.stderr)

    def test_manifest_rejects_partial_rollback_and_false_notarization(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "WhisperFace-1.0.0-macOS-arm64.dmg"
            artifact.write_bytes(b"image")
            common = (
                "create", "--version", "1.0.0", "--revision", REVISION,
                "--download-base-url", "https://example.invalid/v1.0.0",
                "--artifact", str(artifact), "--output",
                str(Path(directory) / "manifest.json"),
            )
            partial = self.run_tool(
                *common, "--previous-version", "0.9.0", check=False
            )
            self.assertEqual(partial.returncode, 2)
            notarized_only = self.run_tool(
                *common, "--notarized-artifact", artifact.name, check=False
            )
            self.assertEqual(notarized_only.returncode, 2)

    def test_checksum_file_is_deterministic_and_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = output / "b.zip"
            second = output / "a.dmg"
            first.write_bytes(b"b")
            second.write_bytes(b"a")
            sums = output / "SHA256SUMS"
            self.run_tool(
                "checksums", "--file", str(first), "--file", str(second),
                "--output", str(sums),
            )
            lines = sums.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines[0].endswith("  a.dmg"))
            self.assertTrue(lines[1].endswith("  b.zip"))
            for line in lines:
                digest, name = line.split("  ", 1)
                self.assertEqual(digest, hashlib.sha256((output / name).read_bytes()).hexdigest())


class MacDistributionContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def run_package_verifier(self, *arguments: str, check: bool = True):
        return subprocess.run(
            [sys.executable, str(PACKAGE_VERIFIER), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=check,
        )

    @unittest.skipIf(
        os.name == "nt",
        "macOS package timestamp normalization is not a Windows contract",
    )
    def test_package_tree_receipt_is_deterministic_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Whisper Face 1.2.3"
            nested = root / "source"
            nested.mkdir(parents=True)
            executable = nested / "Install.command"
            executable.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            data = nested / "payload.txt"
            data.write_text("exact source\n", encoding="utf-8")
            arguments = (
                "stamp", "--root", str(root), "--version", "1.2.3",
                "--revision", REVISION, "--source-date-epoch", "1700000000",
            )
            self.run_package_verifier(*arguments)
            first = (root / "PACKAGE-CONTENTS.json").read_bytes()
            self.run_package_verifier(*arguments)
            self.assertEqual(first, (root / "PACKAGE-CONTENTS.json").read_bytes())
            receipt = json.loads(first)
            self.assertEqual(receipt["source_revision"], REVISION)
            self.assertEqual(receipt["source_date_epoch"], 1700000000)
            self.assertEqual(int(data.stat().st_mtime), 1700000000)
            self.run_package_verifier(
                "verify-tree", "--root", str(root), "--version", "1.2.3",
                "--revision", REVISION,
            )

            data.write_text("tampered\n", encoding="utf-8")
            tampered = self.run_package_verifier(
                "verify-tree", "--root", str(root), "--version", "1.2.3",
                "--revision", REVISION, check=False,
            )
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("tree digest mismatch", tampered.stderr)

    @unittest.skipUnless(
        sys.platform == "darwin",
        "compiled AppKit launcher bundle requires macOS",
    )
    def test_generic_launcher_is_reproducible_and_uses_external_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "built" / "Whisper Face.app"
            command = [
                sys.executable, str(LAUNCHER_TOOL), "build", "--app", str(app),
            ]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
            first = {
                path.relative_to(app).as_posix(): path.read_bytes()
                for path in app.rglob("*") if path.is_file()
            }
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
            second = {
                path.relative_to(app).as_posix(): path.read_bytes()
                for path in app.rglob("*") if path.is_file()
            }
            self.assertEqual(first, second)
            self.assertNotIn("dictate.py", {path.name for path in app.rglob("*")})
            executable = app / "Contents/MacOS/Whisper Face"
            self.assertEqual(executable.read_bytes()[:4], b"\xcf\xfa\xed\xfe")
            self.assertFalse(executable.read_bytes().startswith(b"#!"))
            plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(plist["CFBundlePackageType"], "APPL")
            # The bundle carries the brand icon, and the plist names it, so
            # macOS shows "Whisper Face" with its own artwork in the privacy
            # panes instead of a blank placeholder.
            self.assertEqual(plist["CFBundleIconFile"], "WhisperFace")
            icon = app / "Contents/Resources/WhisperFace.icns"
            self.assertEqual(icon.read_bytes()[:4], b"icns")
            self.assertEqual(
                icon.read_bytes(), (ROOT / "icons/WhisperFace.icns").read_bytes()
            )
            self.assertEqual(
                {path.relative_to(app).as_posix() for path in app.rglob("*") if path.is_file()},
                {
                    "Contents/Info.plist",
                    "Contents/MacOS/Whisper Face",
                    "Contents/Resources/WhisperFace.icns",
                    "Contents/Resources/launcher-source-sha256",
                    "Contents/_CodeSignature/CodeResources",
                },
            )
            # The built bundle carries a valid ad-hoc signature bound to the
            # launcher identifier so macOS lists it as a grantable app.
            signature = subprocess.run(
                ["codesign", "--verify", "--strict",
                 '-R=identifier "com.berg.whisper-face.launcher"', str(app)],
                capture_output=True,
            )
            self.assertEqual(signature.returncode, 0)
            display = subprocess.run(
                ["codesign", "--display", "--verbose=2", str(app)],
                text=True, capture_output=True,
            )
            self.assertIn("adhoc", display.stderr)
            self.assertIn("com.berg.whisper-face.launcher", display.stderr)
            installed = root / "installed" / "Whisper Face.app"
            receipt = root / "state" / "launcher-install.json"
            subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "install",
                "--app", str(installed), "--source-app", str(app),
                "--checkout", str(ROOT), "--receipt", str(receipt),
            ], cwd=ROOT, check=True, capture_output=True)
            self.assertEqual(first, {
                path.relative_to(installed).as_posix(): path.read_bytes()
                for path in installed.rglob("*") if path.is_file()
            })
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertEqual(receipt.parent.stat().st_mode & 0o777, 0o700)
            self.assertNotIn(str(ROOT), b"".join(first.values()).decode("utf-8", "ignore"))
            verification = subprocess.run(
                [
                    sys.executable, str(LAUNCHER_TOOL), "verify",
                    "--app", str(installed), "--checkout", str(ROOT),
                    "--receipt", str(receipt),
                ],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(verification.returncode, 0, verification.stderr)

            # A substituted binary that is re-signed ad-hoc carries a valid
            # signature yet no longer reproduces byte-for-byte from source.
            resigned = root / "resigned" / "Whisper Face.app"
            subprocess.run(["ditto", str(app), str(resigned)], check=True)
            resigned_executable = resigned / "Contents/MacOS/Whisper Face"
            data = resigned_executable.read_bytes()
            middle = len(data) // 2
            resigned_executable.write_bytes(
                data[:middle] + bytes([data[middle] ^ 1]) + data[middle + 1:])
            subprocess.run(
                ["codesign", "--force", "--sign", "-", "--identifier",
                 "com.berg.whisper-face.launcher", str(resigned)],
                check=True, capture_output=True)
            resigned_rejected = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "install",
                "--app", str(root / "rejected-resigned" / "Whisper Face.app"),
                "--source-app", str(resigned), "--checkout", str(ROOT),
                "--receipt", str(root / "rejected-resigned.json"),
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(resigned_rejected.returncode, 2)
            self.assertIn("compiled binary mismatch", resigned_rejected.stderr)

            # Modifying a signed executable without re-signing invalidates the
            # ad-hoc signature outright.
            corrupt = root / "corrupt" / "Whisper Face.app"
            subprocess.run(["ditto", str(app), str(corrupt)], check=True)
            corrupt_executable = corrupt / "Contents/MacOS/Whisper Face"
            data = corrupt_executable.read_bytes()
            middle = len(data) // 2
            corrupt_executable.write_bytes(
                data[:middle] + bytes([data[middle] ^ 1]) + data[middle + 1:])
            corrupt_rejected = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "install",
                "--app", str(root / "rejected-corrupt" / "Whisper Face.app"),
                "--source-app", str(corrupt), "--checkout", str(ROOT),
                "--receipt", str(root / "rejected-corrupt.json"),
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(corrupt_rejected.returncode, 2)
            self.assertIn("ad-hoc signature is invalid", corrupt_rejected.stderr)

            # The release gate refuses an ad-hoc signed bundle: only a Developer
            # ID signature satisfies --require-signed.
            require_signed = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "verify",
                "--app", str(installed), "--require-signed",
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(require_signed.returncode, 2)
            self.assertIn("release launcher must be signed", require_signed.stderr)

            payload = json.loads(receipt.read_text())
            payload["checkout"] = "/tmp/not-the-checkout"
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            receipt.chmod(0o600)
            rejected = subprocess.run(
                [
                    sys.executable, str(LAUNCHER_TOOL), "verify",
                    "--app", str(installed), "--checkout", str(ROOT),
                    "--receipt", str(receipt),
                ],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("stale or invalid", rejected.stderr)

    def test_packager_exports_one_exact_source_and_applies_apple_trust(self):
        script = self.read("scripts/package_macos.sh")
        policy = json.loads(self.read("config/macos-signing-policy.json"))
        self.assertEqual(policy, {
            "developer_id_team_identifier": None,
            "schema_version": 1,
        })
        launcher = self.read("scripts/macos_launcher_app.py")
        self.assertIn("certificate leaf[subject.OU]", launcher)
        self.assertIn("1.2.840.113635.100.6.1.13", launcher)
        self.assertIn("1.2.840.113635.100.6.2.6", launcher)
        for expected in (
            "git -C \"$REPO_DIR\" archive \"$FULL_REVISION\"",
            "fetch -q --depth 1",
            "config core.logAllRefUpdates false",
            "packaged checkout lost its immutable source revision",
            "https://github.com/Aiml3ss/whispering-parrot.git",
            "RELEASE-METADATA.json",
            "SOURCE_DATE_EPOCH",
            "verify_macos_package.py\" stamp",
            "verify_macos_package.py\" verify-artifacts",
            "macos_launcher_app.py\" build",
            "codesign --force --options runtime --timestamp",
            "--require-signed",
            "config/macos-signing-policy.json",
            "APPLE_TEAM_ID is required with --sign",
            "selected revision's pinned Developer ID policy",
            "hdiutil create",
            "codesign --verify --strict",
            "xcrun notarytool submit",
            "xcrun notarytool log",
            "Apple accepted the submission but reported issues",
            "xcrun stapler staple",
            "xcrun stapler validate",
            "update-manifest.json",
            "SHA256SUMS",
            "APPLE_NOTARY_KEYCHAIN_PROFILE",
            "APPLE_APP_SPECIFIC_PASSWORD",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)
        self.assertIn("PACKAGE-CONTENTS.json", self.read(
            "scripts/verify_macos_package.py"))
        verifier = self.read("scripts/verify_macos_package.py")
        self.assertIn('source_root / "scripts/macos_launcher_app.py"', verifier)
        self.assertIn('source_root / "config/macos-signing-policy.json"', verifier)
        # Native Windows may expose a ``bash.exe`` WSL launcher even when no
        # Linux distribution is installed.  Keep the package-contract checks
        # platform independent, and run the shell parser everywhere Bash is a
        # native POSIX tool (including macOS and Linux CI).
        if os.name == "posix":
            subprocess.run(["bash", "-n", str(PACKAGE_SCRIPT)], check=True)

    def test_launcher_ships_the_brand_icon_named_by_its_info_plist(self):
        data = LAUNCHER_ICON.read_bytes()
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(int.from_bytes(data[4:8], "big"), len(data))
        module = load_launcher_tool()
        self.assertIn("Contents/Resources/WhisperFace.icns", module.BASE_FILES)
        self.assertEqual(module._expected_plist()["CFBundleIconFile"], "WhisperFace")
        self.assertEqual(module._icon_bytes(), data)
        # Every consumer of the bundle contract has to agree, or a release
        # artifact would be rejected as containing unexpected files.
        self.assertIn(
            "Contents/Resources/WhisperFace.icns",
            self.read("scripts/verify_macos_package.py"),
        )
        self.assertIn("icons/WhisperFace.icns", self.read("scripts/package_macos.sh"))
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent.icns"
            with self.assertRaises(module.LauncherError):
                module._icon_bytes(missing)
            truncated = Path(directory) / "truncated.icns"
            truncated.write_bytes(data[:64])
            with self.assertRaises(module.LauncherError):
                module._icon_bytes(truncated)
            foreign = Path(directory) / "foreign.icns"
            foreign.write_bytes(b"\x89PNG\r\n\x1a\n")
            with self.assertRaises(module.LauncherError):
                module._icon_bytes(foreign)

    def test_ownership_check_still_accepts_installs_made_before_the_icon(self):
        module = load_launcher_tool()
        self.assertNotIn("Contents/Resources/WhisperFace.icns", module.PRE_ICON_FILES)
        self.assertNotIn("Contents/Resources/WhisperFace.icns", module.LEGACY_FILES)
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Whisper Face.app"
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/Resources").mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(
                plistlib.dumps(module._expected_plist(), sort_keys=True))
            (app / "Contents/MacOS/Whisper Face").write_bytes(b"\xcf\xfa\xed\xfe")
            (app / "Contents/Resources/launcher-source-sha256").write_text("0\n")
            module.verify_owned_app(app)          # older unsigned install
            (app / "Contents/_CodeSignature").mkdir()
            (app / "Contents/_CodeSignature/CodeResources").write_text("<plist/>")
            module.verify_owned_app(app)          # older signed install
            (app / "Contents/Resources/WhisperFace.icns").write_bytes(b"icns")
            module.verify_owned_app(app)          # current layout
            # A stowaway file is still rejected: tolerance is per known layout,
            # not a relaxation of the file-set contract.
            (app / "Contents/Resources/extra").write_text("x")
            with self.assertRaises(module.LauncherError):
                module.verify_owned_app(app)

    def test_release_workflow_fails_closed_and_publishes_only_tags(self):
        workflow = self.read(".github/workflows/macos-release.yml")
        for expected in (
            "APPLE_DEVELOPER_ID_P12_BASE64",
            "APPLE_DEVELOPER_ID_APPLICATION",
            "APPLE_APP_SPECIFIC_PASSWORD",
            "uv lock --check --script dictate.py",
            "uv run tests/test_macos_distribution.py",
            "verify_macos_package.py verify-artifacts",
            "--sign",
            "--notarize",
            "shasum -a 256 -c SHA256SUMS",
            "startsWith(github.ref, 'refs/tags/v')",
            "gh release create",
            "security delete-keychain",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, workflow)
        resolve = workflow.split("- name: Resolve release inputs", 1)[1].split(
            "- name: Validate trusted signing revision", 1
        )[0]
        self.assertIn("REQUESTED_VERSION", resolve)
        self.assertNotIn("${{ inputs.version }}", resolve.split("run: |", 1)[1])
        before_secrets = workflow.split(
            "- name: Require complete production credentials", 1
        )[0]
        self.assertNotIn("${{ secrets.", before_secrets)
        unsigned = workflow.split(
            "- name: Build unsigned preview artifacts", 1
        )[1].split("- name: Build signed release artifacts", 1)[0]
        self.assertNotIn("${{ secrets.", unsigned)
        self.assertIn("Confirm trusted signing worktree remains pristine", workflow)
        self.assertIn("git diff --quiet", workflow)
        publish = workflow.split("publish-release:", 1)[1]
        self.assertIn("contents: write", publish)
        self.assertNotIn("APPLE_", publish)

    def test_security_and_privacy_contracts_are_public_and_honest(self):
        security = self.read("SECURITY.md")
        privacy = self.read("PRIVACY.md")
        threat_model = self.read("docs/security/threat-model.md")
        self.assertIn("security/advisories/new", security)
        self.assertIn("three business days", " ".join(security.split()))
        self.assertIn("Flight Recorder is off", privacy)
        self.assertIn("port 8787", privacy)
        self.assertIn("unauthenticated", privacy)
        self.assertIn("signed, notarized, stapled", threat_model)
        self.assertIn("AGPL-3.0-only", self.read("scripts/release_manifest.py"))

    def test_release_runbook_preserves_installer_and_source_obligations(self):
        runbook = self.read("docs/distribution/macos-release.md")
        self.assertIn("single source of truth", " ".join(runbook.split()))
        self.assertIn("Install.command", runbook)
        self.assertIn("./setup.sh --verify", runbook)
        self.assertIn("corresponding source", runbook)
        self.assertIn("rollback.manifest_url", runbook)

    def test_release_dependencies_are_pinned_and_model_ambiguity_is_disclosed(self):
        workflow = self.read(".github/workflows/macos-release.yml")
        notices = self.read("THIRD_PARTY_NOTICES.md")
        for revision in (
            "d23441a48e516b6c34aea4fa41551a30e30af803",
            "08807647e7069bb48b6ef5acd8ec9567f424441b",
            "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        ):
            with self.subTest(revision=revision):
                self.assertIn(revision, workflow)
                self.assertIn(revision, notices)
        self.assertIn("conflicting upstream metadata", notices)
        self.assertIn("nvidia/parakeet-tdt-0.6b-v2", notices)
        self.assertIn("nvidia/parakeet-unified-en-0.6b", notices)
        self.assertIn("NVIDIA Open Model License", notices)


if __name__ == "__main__":
    unittest.main()
