# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_consequence_routing import (  # noqa: E402
    DEFAULT_CASES,
    build_report,
    load_cases,
    main,
)


class ConsequenceBenchmarkTests(unittest.TestCase):
    def test_adversarial_corpus_passes_under_latency_budget(self):
        report = build_report(DEFAULT_CASES, iterations=25)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["passed"], report["cases"])
        self.assertGreaterEqual(report["cases"], 10)
        self.assertTrue(report["latency_ms"]["passed"])
        self.assertEqual(
            report["privacy"], "synthetic-input-transcript-free-results")
        self.assertEqual(report["scope"], "synthetic-selector-only")
        self.assertFalse(report["verifier_exercised"])
        self.assertFalse(report["audio_exercised"])
        self.assertFalse(report["runtime_backend_exercised"])
        self.assertFalse(report["physical_evidence"])
        encoded = json.dumps(report)
        self.assertNotIn("Alice Smith", encoded)
        self.assertNotIn("example.com", encoded)

    def test_loader_rejects_duplicate_ids(self):
        payload = {
            "schema_version": 1,
            "privacy": "synthetic-text-only",
            "cases": [
                {"id": "same", "text": "one", "audio_duration": 1.0,
                 "expect": {"categories": ["number"], "route": "review"}},
                {"id": "same", "text": "two", "audio_duration": 1.0,
                 "expect": {"categories": ["number"], "route": "review"}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_cases(path)

    def test_report_never_reflects_caller_case_ids(self):
        payload = {
            "schema_version": 1,
            "privacy": "synthetic-text-only",
            "cases": [{
                "id": "alice-smith-private",
                "text": "synthetic prose",
                "confidence": 0.95,
                "audio_duration": 2.0,
                "expect": {"categories": [], "route": "standard"},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            report = build_report(path, iterations=1)
        encoded = json.dumps(report)
        self.assertNotIn("alice-smith-private", encoded)
        self.assertIn("case-001", encoded)

    def test_json_cli_is_machine_readable(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([
                "--cases", str(DEFAULT_CASES),
                "--iterations", "2",
                "--format", "json",
            ])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
