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
            # The disk image is the drag-install app, which manages one
            # checkout of its own and carries private files into the next
            # release's checkout itself.
            self.assertFalse(
                installation[
                    "separate_checkout_requires_manual_private_state_migration"
                ]
            )
            self.assertTrue(installation["automatic_cross_checkout_migration"])
            self.assertEqual(installation["entrypoint"], "Whisper Face.app")
            self.assertEqual(installation["strategy"], "app-managed")
            self.assertIn("Application Support/Whisper Face/checkout/setup.sh",
                          installation["verification"])
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

            # A source bundle installed in place never claims to move private
            # state between folders; only the drag-install app does.
            payload["installation"]["preserves_private_state"] = False
            payload["installation"]["automatic_cross_checkout_migration"] = True
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            migration = self.run_tool(
                "verify", "--manifest", str(manifest),
                "--artifact-dir", str(output), check=False,
            )
            self.assertEqual(migration.returncode, 2)
            self.assertIn("migration requirement", migration.stderr)

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

            # An install made before the icon changed is stale, like one built
            # from older launcher source: verify refuses it, and the installer
            # builds the current app and binds its receipt to that one.
            module = load_launcher_tool()
            earlier = root / "earlier" / "Whisper Face.app"
            earlier_receipt = root / "earlier-state" / "launcher-install.json"
            subprocess.run(["ditto", str(app), str(earlier)], check=True)
            (earlier / "Contents/Resources/WhisperFace.icns").write_bytes(
                b"icns" + (8).to_bytes(4, "big"))
            subprocess.run(
                ["codesign", "--force", "--sign", "-", "--identifier",
                 "com.berg.whisper-face.launcher", str(earlier)],
                check=True, capture_output=True)
            module._write_receipt(earlier_receipt, ROOT, earlier)
            stale = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "verify",
                "--app", str(earlier), "--checkout", str(ROOT),
                "--receipt", str(earlier_receipt),
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(stale.returncode, 2, stale.stderr)
            self.assertIn("icon differs", stale.stderr)
            reinstall = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "install",
                "--app", str(earlier), "--checkout", str(ROOT),
                "--receipt", str(earlier_receipt),
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(reinstall.returncode, 0, reinstall.stderr)
            self.assertEqual(first, {
                path.relative_to(earlier).as_posix(): path.read_bytes()
                for path in earlier.rglob("*") if path.is_file()
            })
            renewed = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "verify",
                "--app", str(earlier), "--checkout", str(ROOT),
                "--receipt", str(earlier_receipt),
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(renewed.returncode, 0, renewed.stderr)

            # A substituted binary that is re-signed ad-hoc carries a valid
            # signature. Without a recorded digest it is not trusted, and the
            # install builds its own launcher instead of stopping.
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

            def install(source, checkout, name):
                target = root / name / "Whisper Face.app"
                result = subprocess.run([
                    sys.executable, str(LAUNCHER_TOOL), "install",
                    "--app", str(target), "--source-app", str(source),
                    "--checkout", str(checkout),
                    "--receipt", str(root / f"{name}.json"),
                ], cwd=ROOT, text=True, capture_output=True)
                return target, result

            def contents(bundle):
                return {
                    path.relative_to(bundle).as_posix(): path.read_bytes()
                    for path in bundle.rglob("*") if path.is_file()
                }

            unrecorded, result = install(resigned, ROOT, "unrecorded")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not using the app beside this folder",
                          result.stderr)
            self.assertEqual(contents(unrecorded), first)

            # A release bundle records the digest of the app it ships, and
            # that record, not a local recompile, is what accepts it. The
            # recompile could only ever succeed on the release builder's
            # exact toolchain.
            release = root / "release-checkout"
            release.mkdir()
            subprocess.run(["git", "init", "-q", str(release)], check=True)
            subprocess.run(
                ["git", "-C", str(release), "-c", "user.name=t",
                 "-c", "user.email=t@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-q",
                 "--allow-empty", "-m", "release"], check=True)
            recorded_digest = subprocess.run(
                [sys.executable, str(LAUNCHER_TOOL), "digest",
                 "--app", str(resigned)],
                text=True, capture_output=True, check=True).stdout.strip()
            (release / "RELEASE-METADATA.json").write_text(
                json.dumps({"launcher_bundle_sha256": recorded_digest}),
                encoding="utf-8")
            recorded, result = install(resigned, release, "recorded")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(contents(recorded), contents(resigned))
            verification = subprocess.run([
                sys.executable, str(LAUNCHER_TOOL), "verify",
                "--app", str(recorded), "--checkout", str(release),
                "--receipt", str(root / "recorded.json"),
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(verification.returncode, 0, verification.stderr)

            # The same app is refused against a record of a different one.
            (release / "RELEASE-METADATA.json").write_text(
                json.dumps({"launcher_bundle_sha256": "0" * 64}),
                encoding="utf-8")
            mismatched, result = install(resigned, release, "mismatched")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("differs from the one its release recorded",
                          result.stderr)
            self.assertEqual(contents(mismatched), first)

            # A working installed app is kept rather than swapped for another
            # ad-hoc one, even a recorded one, because its code hash is its
            # privacy identity.
            built_digest = subprocess.run(
                [sys.executable, str(LAUNCHER_TOOL), "digest", "--app", str(app)],
                text=True, capture_output=True, check=True).stdout.strip()
            (release / "RELEASE-METADATA.json").write_text(
                json.dumps({"launcher_bundle_sha256": built_digest}),
                encoding="utf-8")
            kept, result = install(app, release, "recorded")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("not using the app", result.stderr)
            self.assertEqual(contents(kept), contents(resigned))

            # Modifying a signed executable without re-signing invalidates the
            # ad-hoc signature outright; the install still completes.
            corrupt = root / "corrupt" / "Whisper Face.app"
            subprocess.run(["ditto", str(app), str(corrupt)], check=True)
            corrupt_executable = corrupt / "Contents/MacOS/Whisper Face"
            data = corrupt_executable.read_bytes()
            middle = len(data) // 2
            corrupt_executable.write_bytes(
                data[:middle] + bytes([data[middle] ^ 1]) + data[middle + 1:])
            corrupted, result = install(corrupt, ROOT, "rejected-corrupt")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ad-hoc signature is invalid", result.stderr)
            self.assertEqual(contents(corrupted), first)

            # A damaged or partial bundle with the right layout but files that
            # do not even parse is judged, not trusted: it is set aside and a
            # launcher is built here, and the verify command refuses it with
            # its usual exit code instead of a traceback.
            damages = {
                "Info.plist": ("Contents/Info.plist", b"garbage"),
                "source digest": ("Contents/Resources/launcher-source-sha256",
                                  b"\xff\xfe\xfa"),
            }
            for damage, (relative, data) in damages.items():
                with self.subTest(damage=damage):
                    damaged = root / f"damaged-{len(relative)}" / "Whisper Face.app"
                    subprocess.run(["ditto", str(app), str(damaged)], check=True)
                    (damaged / relative).write_bytes(data)
                    rebuilt, result = install(
                        damaged, ROOT, f"rejected-damaged-{len(relative)}")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("not using the app beside this folder",
                                  result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(contents(rebuilt), first)
                    verify = subprocess.run([
                        sys.executable, str(LAUNCHER_TOOL), "verify",
                        "--app", str(damaged),
                    ], cwd=ROOT, text=True, capture_output=True)
                    self.assertEqual(verify.returncode, 2, verify.stderr)
                    self.assertNotIn("Traceback", verify.stderr)

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
            # Loose objects keep the digest of .git stable across builds.
            "-c fetch.unpackLimit=2147483647",
            "-c maintenance.auto=false",
            "holds objects that are not loose",
            "config core.logAllRefUpdates false",
            "packaged checkout lost its immutable source revision",
            "https://github.com/Aiml3ss/whisper-face.git",
            "RELEASE-METADATA.json",
            "SOURCE_DATE_EPOCH",
            "verify_macos_package.py\" stamp",
            "verify_macos_package.py\" verify-artifacts",
            "macos_launcher_app.py\" package",
            "macos_launcher_app.py\" verify-drag",
            "-volname \"$VOLUME_NAME\"",
            "ln -s /Applications \"$DMG_ROOT/Applications\"",
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
        # scripts/make_app_icon.py packs every size from 16 to 512 points at
        # 1x and 2x; a missing one makes macOS scale another, blurred.
        entries, offset = {}, 8
        while offset < len(data):
            length = int.from_bytes(data[offset + 4:offset + 8], "big")
            self.assertGreater(length, 8)
            entries[data[offset:offset + 4]] = data[offset + 8:offset + length]
            offset += length
        self.assertEqual(offset, len(data))
        self.assertLessEqual({b"ic04", b"ic05"}, set(entries))   # 16, 32 px
        for kind, pixels in ((b"ic11", 32), (b"ic12", 64), (b"ic07", 128),
                             (b"ic13", 256), (b"ic08", 256), (b"ic14", 512),
                             (b"ic09", 512), (b"ic10", 1024)):
            with self.subTest(kind=kind):
                png = entries[kind]
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(int.from_bytes(png[16:20], "big"), pixels)
                self.assertEqual(int.from_bytes(png[20:24], "big"), pixels)
        # Every consumer of the bundle contract has to agree, or a release
        # artifact would be rejected as containing unexpected files.
        self.assertIn("Contents/Resources/WhisperFace.icns", module.DRAG_FILES)
        # The disk image verifier checks the app with this same contract.
        self.assertIn('"verify-drag"', self.read("scripts/verify_macos_package.py"))
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


class DiskImageLayoutTests(unittest.TestCase):
    """The disk image opens to the app and Applications, and nothing else."""

    def layout(self, root: Path):
        verifier = importlib.util.spec_from_file_location(
            "whisper_face_package_verifier", PACKAGE_VERIFIER)
        module = importlib.util.module_from_spec(verifier)
        verifier.loader.exec_module(module)
        return module

    def test_only_the_app_and_an_applications_link_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            module = self.layout(Path(directory))
            root = Path(directory) / "volume"
            (root / "Whisper Face.app" / "Contents").mkdir(parents=True)
            (root / "Applications").symlink_to("/Applications")
            (root / ".fseventsd").mkdir()
            module.verify_volume_layout(root)

            cases = {
                "a stray folder": lambda: (root / "Whisper Face 1.2.3").mkdir(),
                "a stray file": lambda: (root / "README.txt").write_text("x"),
            }
            for name, damage in cases.items():
                with self.subTest(case=name):
                    damage()
                    with self.assertRaisesRegex(module.PackageError, "unexpected"):
                        module.verify_volume_layout(root)
                    for extra in ("Whisper Face 1.2.3", "README.txt"):
                        target = root / extra
                        if target.is_dir():
                            target.rmdir()
                        elif target.exists():
                            target.unlink()
            module.verify_volume_layout(root)

            (root / "Applications").unlink()
            with self.assertRaisesRegex(module.PackageError, "missing: Applications"):
                module.verify_volume_layout(root)
            (root / "Applications").symlink_to("/tmp")
            with self.assertRaisesRegex(module.PackageError, "link to /Applications"):
                module.verify_volume_layout(root)
            (root / "Applications").unlink()
            (root / "Applications").mkdir()
            with self.assertRaisesRegex(module.PackageError, "link to /Applications"):
                module.verify_volume_layout(root)


class SetupWindowContractTests(unittest.TestCase):
    """The drag-install app's setup window, without a Swift toolchain."""

    def test_the_window_reads_setup_progress_and_failures_from_stable_lines(self):
        module = load_launcher_tool()
        self.assertEqual(module.plain_reason([]), "installer.reason.generic")
        # The reason is the last "!!" line that is not the log wrapper's
        # banner, in setup.sh's own words.
        tail = [
            "!! at least 5 GB of free disk space is required",
            "!! Whisper Face setup failed (exit status 1).",
            "!! Full log: /x/install.log",
            "!! Attach that file to a bug report; it holds every step.",
            "!! at least 5 GB of free disk space is required",
        ]
        self.assertEqual(module.plain_reason(tail), "installer.reason.space")
        self.assertEqual(module.plain_reason(
            ["!! this stack requires an Apple Silicon Mac (MLX)"]),
            "installer.reason.mac")
        self.assertEqual(module.plain_reason(
            ["!! the Xcode Command Line Tools are not installed; run x"]),
            "installer.reason.tools")
        self.assertEqual(module.plain_reason(
            ["!! macOS launcher error: this app installs a different "
             "release than the checkout holds"]),
            "installer.reason.version")
        # A download tool's error carries no "!!" line of its own; it is
        # found in the output, and only network words are looked for there.
        self.assertEqual(module.plain_reason([
            "error: Failed to fetch: `https://example.invalid/x.whl`",
            "!! Whisper Face setup failed (exit status 1).",
        ]), "installer.reason.network")
        self.assertEqual(module.plain_reason([
            "free disk space is low somewhere in a log line",
            "!! dictation service did not become ready; inspect x",
        ]), "installer.reason.generic")

    def test_the_swift_source_compiles_in_the_catalog_and_the_theme(self):
        module = load_launcher_tool()
        source = module.swift_source()
        for marker in ("/*COPY_TABLE*/", "/*THEME_TABLE*/", "/*TYPE_TABLE*/",
                       "/*FAILURE_REASONS*/", "/*FACE_SVG*/"):
            self.assertIn(marker, module.SWIFT_TEMPLATE)
            self.assertNotIn(marker, source)
        copy = module.setup_copy()
        for key, value in copy.items():
            self.assertIn(module._swift_string(key), source)
            self.assertIn(module._swift_string(value), source)
        # No visible string is written into the Swift by hand: every
        # localized() call names a catalog key.
        import re as _re
        for key in _re.findall(r'localized\("([^"\\]+)"', module.SWIFT_TEMPLATE):
            self.assertIn(key, copy)
        for phase in module.SETUP_PHASES:
            self.assertIn(f"installer.phase.{phase}", copy)
        # The update runs the release's own updater, from where the app
        # unpacks it, on the Python setup.sh uses.
        setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn(f'PYTHON_VERSION="{module.SETUP_PYTHON}"', setup)
        self.assertIn(f'let pythonVersion = "{module.SETUP_PYTHON}"', source)
        self.assertIn('let updateFolder = "checkout.incoming"', source)
        self.assertIn('let updateScript = "scripts/app_update.py"', source)
        self.assertTrue((ROOT / "scripts/app_update.py").is_file())
        # A finished update hands over to the runtime, which asks for any
        # permission the app's new identity needs; nothing else does.
        finished = module.SWIFT_TEMPLATE[
            module.SWIFT_TEMPLATE.index("func updateFinished("):]
        finished = finished[:finished.index("\n    func ", 10)]
        self.assertIn("case 0:\n            // The runtime", finished)
        self.assertIn("try? launchExistingRuntime(start: true)", finished)
        for name in ("window", "ink", "ink_soft", "brand_ink", "error_ink"):
            self.assertIn(f'themeColor("{name}")', module.SWIFT_TEMPLATE)
            self.assertEqual(len(module.setup_theme()[name]), 16)
        self.assertIn("<svg", source)
        self.assertEqual(module._source_digest(),
                         hashlib.sha256(source.encode()).hexdigest())

    def test_the_drag_app_offers_to_move_a_folder_install_into_itself(self):
        module = load_launcher_tool()
        template = module.SWIFT_TEMPLATE

        def body(start):
            # Up to the closing brace at the declaration's own indent.
            text = template[template.index(start):]
            indent = start[:len(start) - len(start.lstrip())]
            end = f"\n{indent}}}\n"
            return text[:text.index(end) + len(end)]

        decide = body("func initialScreen()")
        # A move that stopped part way comes first, then a drag install's
        # own release, then a folder install, which is offered the move
        # before this app would ever start it as it is.
        order = [decide.index(marker) for marker in (
            "interruptedAdoption()", "installMatch(", "receipt.app == nil",
            "launchExistingRuntime(start: false)")]
        self.assertEqual(order, sorted(order))
        self.assertIn(
            "    if drag, let receipt = try? binding(), receipt.app == nil {\n"
            "        return folderHoldsInstall(receipt.checkout) ? "
            ".adopt(receipt.checkout) : firstSetupScreen()\n    }", decide)
        self.assertIn("    if drag, let folder = interruptedAdoption() { return "
                      ".adoptStuck(", decide)
        self.assertIn('.adoptStuck("installer.adopt.interrupted", folder)', decide)
        # The app reads the journal app_update.py writes, and runs that
        # module's move from the copy it unpacked, with its own uv.
        app_update = (ROOT / "scripts/app_update.py").read_text(encoding="utf-8")
        self.assertIn('MOVE_JOURNAL = "move-in-progress.json"', app_update)
        self.assertIn('let adoptJournal = "move-in-progress.json"', template)
        start = body("    func startAdopt(")
        self.assertIn('"adopt-folder", "--app", ownBundlePath()', start)
        self.assertIn("checkout.appendingPathComponent(updateScript)", start)
        # Moved: the window stays to say the folder can go, and only then
        # hands over to the runtime and its permissions window. Put back,
        # or not: the window says which.
        finished = body("    func adoptFinished(")
        self.assertIn("case 0: show(.adopted(folder))", finished)
        self.assertIn("case 4: show(.adoptStuck(", finished)
        self.assertIn("default: show(.adoptFailed(", finished)
        self.assertNotIn("terminate", finished)
        pressed = body("    @objc func primaryPressed(")
        handover = pressed[pressed.index("case .adopted:"):]
        handover = handover[:handover.index("case .tools")]
        self.assertIn("launchExistingRuntime(start: true)", handover)
        # Keeping the folder version is never a dead end: one that starts
        # is started, and one that doesn't says how to set it up, with the
        # move still on offer.
        keep = body("    func keepFolder(")
        self.assertIn("launchExistingRuntime(start: true)", keep)
        self.assertIn("show(.folder(folder))", keep)
        secondary = body("    @objc func secondaryPressed(")
        self.assertIn("case .adopt(let folder): keepFolder(folder)", secondary)
        self.assertIn("case .folder(let folder): show(.adopt(folder))", secondary)
        copy = module.setup_copy()
        for key in ("installer.adopt.action", "installer.adopt.keep",
                    "installer.folder.adopt", "installer.adopted.show"):
            self.assertIn(key, copy)
        for key in ("installer.adopt.body", "installer.adopt.note",
                    "installer.adopted.body"):
            self.assertIn("delete" if key != "installer.adopt.body" else
                          "dictionary, settings, history and downloaded models",
                          copy[key])
        self.assertIn("Install.command", copy["installer.folder.body"])

    def test_drag_install_info_plist_names_the_real_release(self):
        module = load_launcher_tool()
        plist = module._drag_plist("0.6.0-drag.1", REVISION, "a" * 64, "preview")
        self.assertEqual(plist["CFBundleShortVersionString"], "0.6.0-drag.1")
        self.assertEqual(plist["CFBundleVersion"], "0.6.0")
        self.assertEqual(plist["WFSourceRevision"], REVISION)
        self.assertEqual(plist["WFSourceArchiveSHA256"], "a" * 64)
        self.assertEqual(plist["CFBundleIdentifier"],
                         "com.berg.whisper-face.launcher")
        for bad in (("1.0", REVISION, "a" * 64, "preview"),
                    ("1.0.0", "x" * 40, "a" * 64, "preview"),
                    ("1.0.0", REVISION, "A" * 64, "preview"),
                    ("1.0.0", REVISION, "a" * 64, "nightly")):
            with self.subTest(bad=bad), self.assertRaises(module.LauncherError):
                module._drag_plist(*bad)

    def test_signing_carries_the_microphone_entitlement(self):
        entitlements = plistlib.loads(
            (ROOT / "config/macos-launcher-entitlements.plist").read_bytes())
        self.assertEqual(entitlements, {
            "com.apple.security.device.audio-input": True})
        script = (ROOT / "scripts/package_macos.sh").read_text(encoding="utf-8")
        self.assertIn(
            '--entitlements "$BUNDLE_DIR/config/macos-launcher-entitlements.plist"',
            script)
        # uv is signed before the app that seals it, and both with the
        # hardened runtime and a timestamp.
        uv_sign = script.index('"$LAUNCHER_APP/Contents/Helpers/uv"')
        app_sign = script.index("--entitlements")
        self.assertLess(uv_sign, app_sign)
        # The app is notarized and stapled before the disk image is made.
        self.assertLess(script.index('xcrun stapler staple "$LAUNCHER_APP"'),
                        script.index("hdiutil create"))


UV_STAND_IN = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        puts("uv 0.0.0 (test stand-in)");
        return 0;
    }
    const char *python = getenv("UV_STAND_IN_PYTHON");
    for (int i = 1; python && i < argc; i++) {
        size_t n = strlen(argv[i]);
        if (n > 3 && strcmp(argv[i] + n - 3, ".py") == 0) {
            argv[i - 1] = (char *)python;
            execv(python, argv + i - 1);
            return 127;
        }
    }
    fputs("uv stand-in: nothing to run\n", stderr);
    return 2;
}
"""


@unittest.skipUnless(
    sys.platform == "darwin", "compiled AppKit launcher bundle requires macOS")
class DragInstallAppTests(unittest.TestCase):
    """Package, verify and adopt the drag-install app against fakes.

    Nothing here touches the real home folder, launchd or /Applications.
    """

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.root = root
        cls.generic = root / "generic" / "Whisper Face.app"
        subprocess.run([sys.executable, str(LAUNCHER_TOOL), "build",
                        "--app", str(cls.generic)],
                       cwd=ROOT, check=True, capture_output=True)
        # A one-commit checkout stands in for the release, and a tiny ZIP
        # for its published source archive.
        cls.checkout = root / "checkout"
        cls.checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(cls.checkout)], check=True)
        subprocess.run(
            ["git", "-C", str(cls.checkout), "-c", "user.name=t",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "--allow-empty", "-m", "release"], check=True)
        cls.revision = subprocess.run(
            ["git", "-C", str(cls.checkout), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True).stdout.strip()
        cls.source_zip = root / "WhisperFace-0.6.0-drag.1-source.zip"
        import zipfile
        with zipfile.ZipFile(cls.source_zip, "w") as archive:
            archive.writestr("Whisper Face 0.6.0-drag.1/README.md", "source\n")
        # A thin arm64 program for macOS 14 stands in for uv. It answers
        # --version and, for "run ... script.py args", runs the script with
        # the Python these tests run on, so a dry run can check the app.
        cls.uv_dir = root / "uv" / "bin"
        cls.uv_dir.mkdir(parents=True)
        cls.uv = cls.uv_dir / "uv"
        stand_in = root / "uv-stand-in.c"
        stand_in.write_text(UV_STAND_IN, encoding="utf-8")
        subprocess.run(["xcrun", "clang", "-arch", "arm64",
                        "-mmacosx-version-min=14.0", "-o", str(cls.uv),
                        str(stand_in)], check=True, capture_output=True)
        for name in ("LICENSE-MIT", "LICENSE-APACHE"):
            (cls.uv_dir.parent / name).write_text(f"{name}\n")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def tool(self, *arguments):
        return subprocess.run([sys.executable, str(LAUNCHER_TOOL), *arguments],
                              cwd=ROOT, text=True, capture_output=True)

    def package(self, name, *, channel="preview", revision=None):
        app = self.root / name / "Whisper Face.app"
        result = self.tool(
            "package", "--app", str(app), "--launcher", str(self.generic),
            "--source-zip", str(self.source_zip), "--uv", str(self.uv),
            "--version", "0.6.0-drag.1", "--revision", revision or self.revision,
            "--channel", channel)
        return app, result

    def test_the_drag_app_carries_its_archive_uv_and_licences_and_nothing_else(self):
        app, result = self.package("packaged")
        self.assertEqual(result.returncode, 0, result.stderr)
        module = load_launcher_tool()
        files = {path.relative_to(app).as_posix()
                 for path in app.rglob("*") if path.is_file()}
        self.assertEqual(files, module.DRAG_FILES | module.SIGNATURE_FILES)
        self.assertEqual(
            (app / "Contents/Resources/WhisperFace-source.zip").read_bytes(),
            self.source_zip.read_bytes())
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["WFSourceArchiveSHA256"],
                         hashlib.sha256(self.source_zip.read_bytes()).hexdigest())
        self.assertEqual(info["WFSourceRevision"], self.revision)
        self.assertEqual(info["CFBundleIdentifier"],
                         "com.berg.whisper-face.launcher")
        signature = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict",
             '-R=identifier "com.berg.whisper-face.launcher"', str(app)],
            capture_output=True)
        self.assertEqual(signature.returncode, 0)
        verified = self.tool("verify-drag", "--app", str(app),
                             "--source-zip", str(self.source_zip),
                             "--version", "0.6.0-drag.1",
                             "--revision", self.revision)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        # The release gate wants a Developer ID; an ad-hoc preview fails it.
        signed = self.tool("verify-drag", "--app", str(app), "--require-signed")
        self.assertEqual(signed.returncode, 2)
        self.assertIn("release launcher must be signed", signed.stderr)
        # The packaged app is still an owned launcher, so a later install
        # can recognise it.
        module.verify_owned_app(app)
        # The download carries this checkout's icon, and one that carries an
        # earlier icon is refused like one built from earlier launcher source.
        self.assertEqual(
            (app / "Contents/Resources/WhisperFace.icns").read_bytes(),
            (ROOT / "icons/WhisperFace.icns").read_bytes())
        earlier = self.root / "earlier-icon" / "Whisper Face.app"
        subprocess.run(["ditto", str(app), str(earlier)], check=True)
        (earlier / "Contents/Resources/WhisperFace.icns").write_bytes(
            b"icns" + (8).to_bytes(4, "big"))
        stale = self.tool("verify-drag", "--app", str(earlier))
        self.assertEqual(stale.returncode, 2)
        self.assertIn("icon differs", stale.stderr)

    def test_an_unsigned_stable_build_or_a_swapped_archive_is_refused(self):
        _app, result = self.package("stable", channel="stable")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsigned drag-install app must be a preview build",
                      result.stderr)
        app, result = self.package("swapped")
        self.assertEqual(result.returncode, 0, result.stderr)
        swapped = self.root / "swapped-copy" / "Whisper Face.app"
        subprocess.run(["ditto", str(app), str(swapped)], check=True)
        archive = swapped / "Contents/Resources/WhisperFace-source.zip"
        archive.write_bytes(archive.read_bytes() + b"\0")
        refused = self.tool("verify-drag", "--app", str(swapped))
        self.assertEqual(refused.returncode, 2)
        self.assertIn("does not match its recorded digest", refused.stderr)
        other = self.root / "other.zip"
        other.write_bytes(b"PK\5\6" + b"\0" * 18)
        refused = self.tool("verify-drag", "--app", str(app),
                            "--source-zip", str(other))
        self.assertEqual(refused.returncode, 2)
        self.assertIn("does not carry the published source archive",
                      refused.stderr)

    def test_adopting_binds_the_receipt_to_the_app_where_it_is(self):
        app, result = self.package("adopted")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.root / "adopt-state"
        receipt = state / "launcher-install.json"
        folder_launcher = self.root / "home-apps" / "Whisper Face.app"
        subprocess.run(["ditto", str(self.generic), str(folder_launcher)],
                       check=True)
        # A dry run reports the plan and changes nothing.
        dry = self.tool("adopt", "--app", str(app), "--checkout",
                        str(self.checkout), "--receipt", str(receipt),
                        "--folder-launcher", str(folder_launcher), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("would remove the locally built launcher", dry.stdout)
        self.assertIn("would write receipt", dry.stdout)
        self.assertTrue(folder_launcher.exists())
        self.assertFalse(receipt.exists())
        # For real: the second, locally built launcher goes, and the
        # receipt names the app and pins its release.
        adopted = self.tool("adopt", "--app", str(app), "--checkout",
                            str(self.checkout), "--receipt", str(receipt),
                            "--folder-launcher", str(folder_launcher))
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertFalse(folder_launcher.exists())
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["app"], str(app.resolve()))
        self.assertEqual(payload["checkout"], str(self.checkout.resolve()))
        self.assertEqual(payload["source_revision"], self.revision)
        self.assertFalse(payload["signed"])
        self.assertEqual(payload["version"], "0.6.0-drag.1")
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        verified = self.tool("verify", "--app", str(app), "--checkout",
                             str(self.checkout), "--receipt", str(receipt))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        # Something that is not a launcher this installer built is left
        # alone, whatever its name.
        stranger = self.root / "stranger" / "Whisper Face.app"
        (stranger / "Contents").mkdir(parents=True)
        (stranger / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "com.example.other"}))
        kept = self.tool("adopt", "--app", str(app), "--checkout",
                         str(self.checkout), "--receipt", str(receipt),
                         "--folder-launcher", str(stranger))
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertTrue(stranger.exists())
        self.assertIn("not a launcher this installer built", kept.stdout)

    def test_an_app_never_adopts_a_checkout_of_another_release(self):
        app, result = self.package("mismatched", revision="3" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = self.root / "mismatch-state" / "launcher-install.json"
        refused = self.tool("adopt", "--app", str(app), "--checkout",
                            str(self.checkout), "--receipt", str(receipt))
        self.assertEqual(refused.returncode, 2)
        self.assertIn("installs a different release", refused.stderr)
        self.assertFalse(receipt.exists())

    def dry_run(self, home, *arguments):
        """setup.sh in app mode's dry run, against a home folder of its own.

        Anything that would change this Mac or go to the network is
        shadowed by a stub that refuses, so a dry run that reached one fails.
        """
        bin_dir = home / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        # Apple's toolchain leaves scratch folders in TMPDIR; keep them out
        # of the home folder this test inspects.
        scratch = self.root / "dry-run-tmp"
        scratch.mkdir(exist_ok=True)
        for tool in ("brew", "uv", "curl", "launchctl", "ollama", "open",
                     "ffmpeg", "swift"):
            stub = bin_dir / tool
            stub.write_text('#!/bin/bash\necho "refused: $0 $*" >&2\nexit 97\n')
            stub.chmod(0o755)
        return subprocess.run(
            ["/bin/bash", str(ROOT / "setup.sh"), *arguments],
            env={"HOME": str(home), "LANG": "C", "TMPDIR": str(scratch),
                 "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
                 "UV_STAND_IN_PYTHON": sys.executable},
            text=True, capture_output=True, timeout=120,
            stdin=subprocess.DEVNULL)

    def test_app_modes_dry_run_checks_everything_and_changes_nothing(self):
        if os.uname().machine != "arm64":
            self.skipTest("setup.sh supports Apple silicon only")
        revision = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True).stdout.strip()
        app, result = self.package("dry-run-app", revision=revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        home = self.root / "dry-run-home"
        home.mkdir()
        # An update's copy to carry private files from; a dry run only
        # names what it would bring across.
        replaced = self.root / "dry-run-replaced"
        replaced.mkdir()
        for name in ("setup.sh", "dictate.py", "dictionary.txt",
                     "transcripts.jsonl"):
            (replaced / name).write_text(f"{name}\n")
        before = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--ignored"],
            text=True, capture_output=True, check=True).stdout
        run = self.dry_run(home, "--app", str(app), "--dry-run",
                           "--carry-over-from", str(replaced))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("refused:", run.stderr)
        executable = f"{app.resolve()}/Contents/MacOS/Whisper Face"
        for line in (
                "== dry run of app mode in",
                "Homebrew is not used",
                f"== private files: would carry 2 over from "
                f"{replaced.resolve()}: dictionary.txt transcripts.jsonl",
                "would write receipt",
                f"== login service: would run {executable} --run",
                "== dry run complete: nothing was changed"):
            with self.subTest(line=line):
                self.assertIn(line, run.stdout)
        # Nothing landed in the home folder or the checkout.
        self.assertEqual(
            sorted(path.name for path in home.iterdir()), ["bin"])
        after = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--ignored"],
            text=True, capture_output=True, check=True).stdout
        self.assertEqual(before, after)

        # Sticky: a schema 2 receipt for this checkout is enough on its own,
        # so a rerun of Install.command keeps to the app.
        support = home / "Library/Application Support/Whisper Face"
        support.mkdir(parents=True)
        support.chmod(0o700)
        receipt = support / "launcher-install.json"
        receipt.write_text(json.dumps({
            "app": str(app.resolve()), "bundle_id":
            "com.berg.whisper-face.launcher", "checkout": str(ROOT.resolve()),
            "schema_version": 2, "source_revision": revision}))
        receipt.chmod(0o600)
        sticky = self.dry_run(home, "--dry-run")
        self.assertEqual(sticky.returncode, 0, sticky.stdout + sticky.stderr)
        self.assertIn(f"== app: {app.resolve()}", sticky.stdout)
        self.assertIn(f"would run {executable} --run", sticky.stdout)

        # An app of another release cannot take this checkout over.
        other, result = self.package("dry-run-other", revision="4" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        refused = self.dry_run(home, "--app", str(other), "--dry-run")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("installs a different release", refused.stderr)
        # A dry run is for app mode only.
        receipt.unlink()
        folder = self.dry_run(home, "--dry-run")
        self.assertEqual(folder.returncode, 1)
        self.assertIn("--dry-run checks app mode", folder.stderr)

    def test_the_app_tells_an_update_from_an_older_copy_as_the_advisor_does(self):
        module = load_launcher_tool()
        app, result = self.package("match")
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = "1" * 40
        base = {"app": "/Applications/Whisper Face.app",
                "bundle_id": "com.berg.whisper-face.launcher",
                "checkout": "/Users/you/Library/Application Support/Whisper Face/checkout",
                "schema_version": 2, "source_revision": installed}
        cases = (
            # (this app's version, the installed version, the answer)
            ("0.6.1", "0.6.0", "update 0.6.0 0.6.1"),
            ("0.6.0", "0.6.1", "older 0.6.1 0.6.0"),
            ("0.6.0", "0.6.0-drag.3", "update 0.6.0-drag.3 0.6.0"),
            ("0.6.0-drag.3", "0.6.0-drag.10", "older 0.6.0-drag.10 0.6.0-drag.3"),
            ("0.6.0-drag.a", "0.6.0-drag.3", "update 0.6.0-drag.3 0.6.0-drag.a"),
            ("0.6.0-rc.1", "0.6.0-drag.1", "update 0.6.0-drag.1 0.6.0-rc.1"),
            ("0.6.0-drag.1", "0.6.0-drag.1.1", "older 0.6.0-drag.1.1 0.6.0-drag.1"),
            ("0.6.0", "0.6.0", "conflict 0.6.0"),
            ("0.6.0+b", "0.6.0+a", "conflict 0.6.0+a"),
            ("0.6.0", "1.0", "conflict 1.0"),
            ("0.6.0", "0.6.0-drag.01", "conflict 0.6.0-drag.01"),
        )
        receipt_path = self.root / "match-receipt.json"
        info_path = app / "Contents/Info.plist"
        for own, other, expected in cases:
            with self.subTest(own=own, installed=other):
                info = plistlib.loads(info_path.read_bytes())
                info["CFBundleShortVersionString"] = own
                info_path.write_bytes(plistlib.dumps(info, sort_keys=True))
                subprocess.run(["codesign", "--force", "--sign", "-",
                                "--identifier", "com.berg.whisper-face.launcher",
                                str(app)], check=True, capture_output=True)
                receipt = {**base, "version": other}
                receipt_path.write_text(json.dumps(receipt))
                swift = subprocess.run(
                    [str(app / "Contents/MacOS/Whisper Face"), "--install-match",
                     str(receipt_path)], text=True, capture_output=True,
                    timeout=30).stdout.strip()
                self.assertEqual(swift, expected)
                self.assertEqual(module.install_match(receipt, info), expected)
        # The same release is current whatever the version says; a folder
        # install's receipt is no drag install at all.
        for receipt, expected in (
                ({**base, "source_revision": self.revision, "version": "9.9.9"},
                 "current"),
                ({**base, "schema_version": 1}, "none")):
            with self.subTest(expected=expected):
                receipt_path.write_text(json.dumps(receipt))
                swift = subprocess.run(
                    [str(app / "Contents/MacOS/Whisper Face"), "--install-match",
                     str(receipt_path)], text=True, capture_output=True,
                    timeout=30).stdout.strip()
                self.assertEqual(swift, expected)
                self.assertEqual(module.install_match(
                    receipt, plistlib.loads(info_path.read_bytes())), expected)

    def test_the_setup_window_renders_every_screen_in_both_appearances(self):
        out = self.root / "renders"
        out.mkdir()
        rendered = subprocess.run(
            [str(self.generic / "Contents/MacOS/Whisper Face"),
             "--render-setup-window", str(out)],
            capture_output=True, timeout=120)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        names = {path.name for path in out.iterdir()}
        for screen in ("welcome", "move", "tools", "running", "failed",
                       "repair", "folder", "unset", "update", "updating",
                       "update-failed", "update-stuck", "older", "conflict",
                       "adopt", "adopting", "adopt-failed", "adopt-stuck",
                       "adopted", "folder-keep"):
            for appearance in ("light", "dark"):
                with self.subTest(screen=screen, appearance=appearance):
                    picture = out / f"installer-{screen}-{appearance}.png"
                    self.assertIn(picture.name, names)
                    self.assertEqual(picture.read_bytes()[:8],
                                     b"\x89PNG\r\n\x1a\n")
        # The two appearances are drawn differently, so the theme's dynamic
        # colours really resolved.
        self.assertNotEqual(
            (out / "installer-welcome-light.png").read_bytes(),
            (out / "installer-welcome-dark.png").read_bytes())
        # Keeping the folder version from this app offers the move back.
        self.assertNotEqual(
            (out / "installer-folder-light.png").read_bytes(),
            (out / "installer-folder-keep-light.png").read_bytes())


if __name__ == "__main__":
    unittest.main()
