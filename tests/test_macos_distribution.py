# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TOOL = ROOT / "scripts" / "release_manifest.py"
PACKAGE_SCRIPT = ROOT / "scripts" / "package_macos.sh"
PACKAGE_VERIFIER = ROOT / "scripts" / "verify_macos_package.py"
REVISION = "1" * 40
PREVIOUS_REVISION = "2" * 40


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

    def test_packager_exports_one_exact_source_and_applies_apple_trust(self):
        script = self.read("scripts/package_macos.sh")
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
        # Native Windows may expose a ``bash.exe`` WSL launcher even when no
        # Linux distribution is installed.  Keep the package-contract checks
        # platform independent, and run the shell parser everywhere Bash is a
        # native POSIX tool (including macOS and Linux CI).
        if os.name == "posix":
            subprocess.run(["bash", "-n", str(PACKAGE_SCRIPT)], check=True)

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
