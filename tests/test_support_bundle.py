"""Regression coverage for the local, content-free Diagnostics bundle."""

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support_bundle import (
    BUNDLE_KIND,
    SupportBundleError,
    build_support_bundle,
    write_support_bundle,
)
import support_bundle as support_bundle_module


def snapshot(**overrides):
    payload = {
        "kind": "whisper-face/support-snapshot",
        "schema_version": 1,
        "health": {
            "service_status": "running",
            "microphone_status": "ready",
        },
        "permissions": {"accessibility_status": "granted"},
        "build": "local-checkout",
        "models": [{"family": "parakeet", "status": "running"}],
        "last_result": {
            "available": True,
            "engine": "parakeet",
            "mode": "capture",
            "latency_ms": 42.125,
            "word_count": 12,
            "confidence": 0.98,
            "stable_prefix_words": 12,
            "compiler_decisions": 3,
            "protected_anchor_count": 2,
            "alternatives_considered": 1,
            "cleanup_edits_count": 1,
            "proof_edits_accepted": 1,
            "proof_edits_rejected": 0,
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


class SupportBundleTests(unittest.TestCase):
    def test_rebuilds_a_closed_allowlist_and_drops_private_values(self):
        raw = json.loads(snapshot())
        raw.update({
            "transcript": "private dictation",
            "draft": "private draft",
            "snippet": "private snippet",
            "vocabulary": ["private term"],
            "correction": "private correction",
            "clipboard": "private clipboard",
            "focused_app": "private app",
            "log_body": "private log",
            "path": "/Users/private/path",
        })
        raw["last_result"]["transcript"] = "private result"
        raw["models"].append({
            "family": "private model", "status": "private status"})

        bundle = build_support_bundle(json.dumps(raw))
        encoded = json.dumps(bundle, sort_keys=True)

        self.assertEqual(bundle["kind"], BUNDLE_KIND)
        self.assertEqual(set(bundle), {
            "kind", "schema_version", "app", "runtime", "models",
            "last_result",
        })
        self.assertEqual(bundle["models"][-1], {
            "family": "unknown", "status": "unknown"})
        for private_value in (
                "private dictation", "private draft", "private snippet",
                "private term", "private correction", "private clipboard",
                "private app", "private log", "/Users/private/path",
                "private result"):
            self.assertNotIn(private_value, encoded)

    def test_writes_a_local_private_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "support.json"
            saved = write_support_bundle(destination, snapshot())

            self.assertEqual(saved, destination)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(json.loads(destination.read_text())["kind"],
                             BUNDLE_KIND)

    def test_refuses_invalid_inputs_and_symlink_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SupportBundleError):
                build_support_bundle("not json")
            target = root / "target.json"
            target.write_text("{}")
            link = root / "support.json"
            link.symlink_to(target)
            with self.assertRaises(SupportBundleError):
                write_support_bundle(link, snapshot())

    def test_wraps_preflight_and_temporary_file_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "support.json"
            with patch.object(Path, "is_symlink", side_effect=OSError("denied")):
                with self.assertRaisesRegex(SupportBundleError, "destination"):
                    write_support_bundle(destination, snapshot())
            with patch.object(
                    support_bundle_module.tempfile, "mkstemp",
                    side_effect=OSError("read only")):
                with self.assertRaisesRegex(SupportBundleError, "could not save"):
                    write_support_bundle(destination, snapshot())

    def test_replacement_has_no_post_rename_path_following_operations(self):
        source = (Path(__file__).resolve().parents[1] /
                  "support_bundle.py").read_text(encoding="utf-8")
        writer = source[source.index("def write_support_bundle"):]
        after_replace = writer[writer.index("os.replace("):writer.index(
            "return path", writer.index("os.replace("))]
        self.assertNotIn("os.chmod", after_replace)
        self.assertNotIn("path.stat", after_replace)
        self.assertIn("os.fstat(descriptor)", writer)
        self.assertIn("replaced = True", after_replace)

    def test_writer_has_no_network_or_runtime_authority(self):
        source = (Path(__file__).resolve().parents[1] /
                  "support_bundle.py").read_text(encoding="utf-8")
        for forbidden in (
                "socket", "requests", "subprocess", "urllib", "http",
                "NSPasteboard", "VoiceInbox", "InsertionCoordinator"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
