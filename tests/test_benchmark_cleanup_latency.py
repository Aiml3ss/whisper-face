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

from benchmark_cleanup_latency import (  # noqa: E402
    MODEL, TransportTimeout, VARIANTS, build_report, load_cases, run_variant,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class CleanupLatencyBenchmarkTests(unittest.TestCase):
    def test_report_is_aggregate_only_and_never_claims_runtime_authority(self):
        cases = load_cases()
        current = {"id": "current", "few_shot_pairs": 4, "token_budget": "x",
                   "cases": len(cases), "accepted": len(cases), "guard_rejected": 0,
                   "semantic_failed": 0, "proof_failed": 0, "parse_failed": 0,
                   "timeout": 0,
                   "transport_failed": 0,
                   "latency_ms": {"p50": 100.0, "p95": 120.0, "max": 130.0}}
        report = build_report(cases, [current, {**current, "id": "lean-three-shot",
                                                "latency_ms": {"p50": 70.0, "p95": 80.0, "max": 90.0}}], read_timeout=4.0)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["model"], MODEL)
        self.assertEqual(report["runtime_authority"], "none")
        self.assertFalse(report["claim"]["runtime_change_recommended"])
        encoded = json.dumps(report)
        self.assertNotIn(cases[0]["raw"], encoded)
        self.assertNotIn(cases[0]["id"], encoded)

    def test_timeout_is_counted_and_uses_current_runtime_deadline(self):
        def timeout(*_args, **kwargs):
            self.assertEqual(kwargs["timeout"], (1, 4.0))
            raise TransportTimeout("synthetic timeout")

        result = run_variant(VARIANTS[0], load_cases()[:1], post=timeout, read_timeout=4.0)
        self.assertEqual(result["timeout"], 1)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["transport_failed"], 0)

    def test_malformed_local_response_is_not_mislabeled_as_transport(self):
        def malformed(*_args, **_kwargs):
            return _Response({"done_reason": "stop", "message": {
                "content": "not-json",
            }})

        result = run_variant(VARIANTS[0], load_cases()[:1], post=malformed)
        self.assertEqual(result["parse_failed"], 1)
        self.assertEqual(result["transport_failed"], 0)

    def test_proof_mismatch_is_not_accepted(self):
        def response(*_args, **_kwargs):
            return _Response({"done_reason": "stop", "message": {"content": json.dumps({
                "text": "Schedule the review for Wednesday with the API team.", "edits": [],
            })}})

        result = run_variant(VARIANTS[0], load_cases()[:1], post=response)
        self.assertEqual(result["proof_failed"], 1)
        self.assertEqual(result["accepted"], 0)


if __name__ == "__main__":
    unittest.main()
