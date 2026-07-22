# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_cleanup_latency import load_cases  # noqa: E402
from benchmark_cleanup_proof_recovery import run_benchmark  # noqa: E402


class CleanupProofRecoveryBenchmarkTests(unittest.TestCase):
    def test_report_is_aggregate_content_free_and_denies_runtime_authority(self):
        cases = load_cases()
        report = run_benchmark()
        self.assertEqual(report["cases"], 6)
        self.assertEqual(report["recovered"], 5)
        self.assertEqual(report["rejected"], 1)
        self.assertEqual(report["replay_verified"], 5)
        self.assertEqual(report["runtime_authority"], "none")
        self.assertFalse(report["claim"]["all_cases_pass"])
        self.assertFalse(report["claim"]["candidate_demonstrably_no_worse"])
        self.assertFalse(report["claim"]["runtime_change_recommended"])
        encoded = json.dumps(report)
        for case in cases:
            self.assertNotIn(case["id"], encoded)
            self.assertNotIn(case["raw"], encoded)
            self.assertNotIn(case["candidate"], encoded)


if __name__ == "__main__":
    unittest.main()
