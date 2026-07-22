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

from benchmark_insertion_reliability import (  # noqa: E402
    DEFAULT_CASES,
    build_report,
    load_corpus,
    main,
)


class InsertionReliabilityBenchmarkTests(unittest.TestCase):
    def test_committed_corpus_covers_requested_adversarial_faults(self):
        corpus = load_corpus(DEFAULT_CASES)
        faults = {case["fault"] for case in corpus["cases"]}
        self.assertTrue({
            "focus-drift", "duplicate-commit", "selection-drift",
            "surrounding-drift", "relaunch-identity",
            "delayed-readback-duplicate",
        } <= faults)
        profile_ids = {
            profile["id"] for profile in corpus["capability_profiles"]}
        self.assertIn("clipboard-unavailable", profile_ids)
        self.assertIn("readable-no-readback", profile_ids)

    def test_simulation_corpus_passes_exactly_once_attempt_invariant(self):
        report = build_report(DEFAULT_CASES, iterations=25)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["passed"], report["cases"])
        self.assertEqual(report["attempt_invariant"]["violations"], 0)
        self.assertTrue(report["attempt_invariant"]["passed"])
        self.assertGreaterEqual(report["cases"], 10)

    def test_report_does_not_overstate_real_app_or_reliability_evidence(self):
        report = build_report(DEFAULT_CASES, iterations=1)
        self.assertEqual(report["evidence_scope"], "adapter-simulation-only")
        self.assertFalse(report["physical_evidence"])
        self.assertEqual(report["real_apps_exercised"], 0)
        self.assertFalse(report["fifty_app_claim"])
        self.assertFalse(report["four_nines_claim"])
        self.assertNotIn("synthetic payload", json.dumps(report))
        self.assertNotIn("readable-complete", json.dumps(report))
        self.assertIn("profile-001", json.dumps(report))

    def test_loader_rejects_duplicate_profile_ids(self):
        payload = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))
        payload["capability_profiles"].append(
            dict(payload["capability_profiles"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_corpus(path)

    def test_json_cli_is_machine_readable(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["--iterations", "2", "--format", "json"])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
