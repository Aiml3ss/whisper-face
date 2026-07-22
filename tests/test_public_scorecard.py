import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from public_scorecard import build_public_scorecard, render_json  # noqa: E402


class PublicScorecardTests(unittest.TestCase):
    def test_checked_in_synthetic_suites_aggregate_deterministically(self):
        first = build_public_scorecard()
        second = build_public_scorecard()

        self.assertEqual(first, second)
        self.assertEqual(
            [suite["suite_id"] for suite in first["suites"]],
            [
                "voice_compiler",
                "consequence_routing",
                "insertion_reliability",
                "point_and_speak",
                "drop_to_target",
            ],
        )
        self.assertEqual(first["totals"], {
            "suites": 5,
            "cases": 62,
            "passed": 62,
            "failed": 0,
            "critical_failures": 0,
            "all_passed": True,
        })

    def test_report_schema_is_closed_and_physical_claims_are_false(self):
        report = build_public_scorecard()

        self.assertEqual(set(report), {
            "schema_version", "report_kind", "privacy", "evidence_scope",
            "physical_validation", "real_apps_exercised",
            "audio_or_model_runs", "suites", "totals",
        })
        self.assertEqual(
            report["evidence_scope"], "checked-in-synthetic-corpora-only")
        self.assertEqual(report["privacy"], "transcript-free-aggregate-only")
        self.assertFalse(report["physical_validation"])
        self.assertEqual(report["real_apps_exercised"], 0)
        self.assertFalse(report["audio_or_model_runs"])
        for suite in report["suites"]:
            self.assertEqual(set(suite), {
                "suite_id", "evidence_scope", "physical_validation", "cases",
                "passed", "failed", "critical_metric", "critical_failures",
            })
            self.assertFalse(suite["physical_validation"])

    def test_json_contains_no_checked_in_transcripts_or_target_identity(self):
        encoded = render_json(build_public_scorecard())

        for private_or_case_content in (
            "2042.76it/s",
            "Alice Smith",
            "Team Inbox",
            "team-inbox",
            "Review Lane",
            "synthetic payload",
        ):
            self.assertNotIn(private_or_case_content, encoded)
        decoded = json.loads(encoded)
        self.assertEqual(decoded, build_public_scorecard())
        self.assertNotIn(str(ROOT), encoded)


if __name__ == "__main__":
    unittest.main()
