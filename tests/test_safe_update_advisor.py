# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TOOL = ROOT / "scripts" / "release_manifest.py"
ADVISOR = ROOT / "scripts" / "safe_update_advisor.py"
CURRENT = "1" * 40
NEWER = "2" * 40
OLDER = "0" * 40


class SafeUpdateAdvisorTests(unittest.TestCase):
    def make_release(
        self,
        root: Path,
        version: str,
        revision: str,
        *,
        channel: str = "stable",
        trusted: bool = True,
        previous: tuple[str, str] | None = None,
    ) -> Path:
        root.mkdir()
        source = root / f"WhisperFace-{version}-source.zip"
        image = root / f"WhisperFace-{version}-macOS-arm64.dmg"
        source.write_bytes(f"source {version}".encode())
        image.write_bytes(f"image {version}".encode())
        manifest = root / "update-manifest.json"
        command = [
            sys.executable,
            str(MANIFEST_TOOL),
            "create",
            "--version", version,
            "--revision", revision,
            "--channel", channel,
            "--download-base-url", f"https://example.invalid/v{version}",
            "--artifact", str(source),
            "--artifact", str(image),
            "--output", str(manifest),
        ]
        if trusted:
            command.extend([
                "--signed-artifact", image.name,
                "--notarized-artifact", image.name,
            ])
        if previous:
            previous_version, previous_revision = previous
            command.extend([
                "--previous-version", previous_version,
                "--previous-revision", previous_revision,
                "--previous-manifest-url",
                f"https://example.invalid/v{previous_version}/update-manifest.json",
            ])
        subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        return manifest

    def advise(
        self,
        manifest: Path,
        current_version: str,
        current_revision: str,
        *extra: str,
    ) -> tuple[subprocess.CompletedProcess, dict]:
        result = subprocess.run(
            [
                sys.executable,
                str(ADVISOR),
                "--current-version", current_version,
                "--current-revision", current_revision,
                "--manifest", str(manifest),
                "--artifact-dir", str(manifest.parent),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        return result, json.loads(result.stdout)

    def test_upgrade_and_up_to_date_are_verified_read_only_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            newer = self.make_release(
                base / "newer", "1.1.0", NEWER,
                channel="preview", trusted=False,
            )
            result, receipt = self.advise(
                newer, "1.0.0", CURRENT, "--channel", "preview"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["decision"], "upgrade")
            self.assertTrue(receipt["candidate"]["artifacts_verified"])
            self.assertFalse(receipt["candidate"]["production_trust_verified"])
            self.assertEqual(receipt["execution"], "none")
            self.assertFalse(any(receipt["effects"].values()))

            same = self.make_release(
                base / "same", "1.0.0", CURRENT,
                channel="preview", trusted=False,
            )
            result, receipt = self.advise(
                same, "1.0.0", CURRENT, "--channel", "preview"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["decision"], "up-to-date")

    def test_stable_plan_refuses_unsigned_or_tampered_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            unsigned = self.make_release(
                base / "unsigned", "1.1.0", NEWER, trusted=False
            )
            result, receipt = self.advise(unsigned, "1.0.0", CURRENT)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(receipt["decision"], "refuse")
            self.assertEqual(receipt["reason"], "production-trust-required")

            claimed = self.make_release(base / "claimed", "1.1.0", NEWER)
            result, receipt = self.advise(claimed, "1.0.0", CURRENT)
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                receipt["reason"],
                {
                    "apple-trust-verification-failed",
                    "apple-trust-verification-unavailable",
                },
            )

            trusted = self.make_release(base / "tampered", "1.1.0", NEWER)
            (trusted.parent / "WhisperFace-1.1.0-source.zip").write_bytes(b"changed")
            result, receipt = self.advise(trusted, "1.0.0", CURRENT)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                receipt["reason"], "manifest-or-artifact-verification-failed"
            )

    def test_channel_revision_and_semver_ambiguity_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            preview = self.make_release(
                base / "preview", "1.1.0", NEWER,
                channel="preview", trusted=False,
            )
            result, receipt = self.advise(preview, "1.0.0", CURRENT)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(receipt["reason"], "channel-mismatch")

            conflict = self.make_release(
                base / "conflict", "1.0.0", NEWER,
                channel="preview", trusted=False,
            )
            result, receipt = self.advise(
                conflict, "1.0.0", CURRENT, "--channel", "preview"
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(receipt["decision"], "refuse")
            self.assertEqual(receipt["reason"], "same-version-revision-conflict")

            result, receipt = self.advise(
                conflict, "1.0.0-01", CURRENT, "--channel", "preview"
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(receipt["reason"], "invalid-current-version")

            private_sentinel = "PRIVATE-SENTINEL\nnot-a-version"
            result, receipt = self.advise(conflict, private_sentinel, "not-a-revision")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(receipt["current"]["version"], "unknown")
            self.assertEqual(receipt["current"]["source_revision"], "unknown")
            self.assertNotIn("PRIVATE-SENTINEL", result.stdout)

    def test_rollback_requires_verified_local_target_and_exact_linkage(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            current = self.make_release(
                base / "current", "2.0.0", CURRENT,
                channel="preview", trusted=False,
                previous=("1.9.0", OLDER),
            )
            older = self.make_release(
                base / "older", "1.9.0", OLDER,
                channel="preview", trusted=False,
            )
            result, receipt = self.advise(
                current,
                "2.0.0",
                CURRENT,
                "--intent", "rollback",
                "--channel", "preview",
                "--rollback-manifest", str(older),
                "--rollback-artifact-dir", str(older.parent),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["decision"], "rollback")
            self.assertTrue(receipt["rollback"]["artifacts_verified"])
            self.assertEqual(receipt["rollback"]["source_revision"], OLDER)
            self.assertFalse(any(receipt["effects"].values()))

            payload = json.loads(current.read_text())
            payload["rollback"]["source_revision"] = NEWER
            current.write_text(json.dumps(payload), encoding="utf-8")
            result, receipt = self.advise(
                current,
                "2.0.0",
                CURRENT,
                "--intent", "rollback",
                "--channel", "preview",
                "--rollback-manifest", str(older),
                "--rollback-artifact-dir", str(older.parent),
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(receipt["reason"], "rollback-linkage-mismatch")


if __name__ == "__main__":
    unittest.main()
