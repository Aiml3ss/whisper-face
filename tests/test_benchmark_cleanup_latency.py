# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import http.client
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_cleanup_latency import (  # noqa: E402
    MODEL, RISK_LABELS, TransportTimeout, VARIANTS, _semantic_failure,
    _variant_comparison, build_report, load_cases, run_variant,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class CleanupLatencyBenchmarkTests(unittest.TestCase):
    @staticmethod
    def _result(cases, **changes):
        result = {
            "id": "current", "few_shot_pairs": 4, "token_budget": "x",
            "read_timeout_seconds": 4.0,
            "cases": len(cases), "accepted": len(cases),
            "baseline_accepted": len(cases),
            "recovered_accepted": len(cases),
            "both_accepted": len(cases), "baseline_only_accepted": 0,
            "recovered_only_accepted": 0, "neither_accepted": 0,
            "acceptance_delta": 0,
            "risk_results": [],
            "model_candidates_evaluated": len(cases),
            "recovery_attempted": len(cases), "recovery_rejected": 0,
            "recovery_replay_verified": len(cases),
            "guard_rejected": 0, "semantic_failed": 0,
            "proof_failed": 0, "parse_failed": 0, "timeout": 0,
            "transport_failed": 0, "recovery_reason_counts": {
                "eligible": len(cases),
            },
            "recovery_latency_ms": {
                "p50": 0.1, "p95": 0.2, "max": 0.3,
            },
            "latency_ms": {"p50": 100.0, "p95": 120.0, "max": 130.0},
            "_case_outcomes": tuple("both_accepted" for _case in cases),
        }
        result.update(changes)
        return result

    def test_report_is_aggregate_only_and_never_claims_runtime_authority(self):
        cases = load_cases()
        current = self._result(cases)
        report = build_report(cases, [current, {**current, "id": "lean-three-shot",
                                                "latency_ms": {"p50": 70.0, "p95": 80.0, "max": 90.0}}], read_timeout=4.0)
        self.assertEqual(report["schema_version"], 5)
        self.assertEqual(report["model"], MODEL)
        self.assertEqual(report["runtime_authority"], "none")
        self.assertFalse(report["claim"]["runtime_change_recommended"])
        self.assertNotIn("_case_outcomes", json.dumps(report))
        self.assertEqual(report["deterministic_routing"], {
            "fast_path_cases": 29,
            "qwen_routed_cases": 1,
            "qwen_routed_risk_counts": {
                "dates": 1,
                "fillers": 1,
                "meaningful_filler": 1,
                "numbers": 1,
            },
        })
        encoded = json.dumps(report)
        for case in cases:
            self.assertNotIn(case["raw"], encoded)
            self.assertNotIn(case["candidate"], encoded)
            self.assertNotIn(case["id"], encoded)

    def test_paired_variant_comparison_requires_quality_and_latency(self):
        cases = load_cases()
        baseline = self._result(cases)
        faster = self._result(
            cases, id="candidate",
            latency_ms={"p50": 70.0, "p95": 90.0, "max": 95.0})
        comparison = _variant_comparison(baseline, faster)
        self.assertEqual(comparison["baseline_losses"], 0)
        self.assertEqual(comparison["new_semantic_failures"], 0)
        self.assertEqual(comparison["new_unavailable_failures"], 0)
        self.assertTrue(comparison["runtime_change_eligible"])

        regressed = self._result(
            cases, id="regressed",
            latency_ms={"p50": 70.0, "p95": 90.0, "max": 95.0},
            _case_outcomes=(
                "semantic_failed", *baseline["_case_outcomes"][1:]))
        comparison = _variant_comparison(baseline, regressed)
        self.assertEqual(comparison["baseline_losses"], 1)
        self.assertEqual(comparison["new_semantic_failures"], 1)
        self.assertFalse(comparison["runtime_change_eligible"])

    def test_variant_deadlines_are_bounded_by_the_cli_cap(self):
        seen = []

        def timeout(*_args, **kwargs):
            seen.append(kwargs["timeout"])
            raise TransportTimeout()

        case = load_cases()[:1]
        run_variant(VARIANTS[1], case, post=timeout)
        run_variant(VARIANTS[1], case, post=timeout, read_timeout=3.0)
        self.assertEqual(seen, [(1, 3.5), (1, 3.0)])

    def test_corpus_has_explicit_golden_constraints_and_risk_coverage(self):
        cases = load_cases()
        covered = {risk for case in cases for risk in case["risks"]}

        self.assertGreaterEqual(len(cases), 24)
        self.assertEqual(covered, RISK_LABELS)
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["candidate"])
                self.assertFalse(
                    _semantic_failure(case, case["candidate"]))

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

    def test_disconnected_local_response_is_counted_as_transport(self):
        def disconnected(*_args, **_kwargs):
            raise http.client.RemoteDisconnected()

        result = run_variant(
            VARIANTS[0], load_cases()[:1], post=disconnected)
        self.assertEqual(result["transport_failed"], 1)
        self.assertEqual(result["parse_failed"], 0)

    def test_guard_rejection_is_never_offered_to_recovery(self):
        case = load_cases()[0]

        def truncated(*_args, **_kwargs):
            return _Response({"done_reason": "length", "message": {
                "content": json.dumps({
                    "text": case["candidate"], "edits": [],
                }),
            }})

        result = run_variant(VARIANTS[0], (case,), post=truncated)
        self.assertEqual(result["guard_rejected"], 1)
        self.assertEqual(result["recovery_attempted"], 0)
        self.assertEqual(result["recovery_reason_counts"], {})

    def test_proof_mismatch_is_not_accepted(self):
        case = load_cases()[0]

        def response(*_args, **_kwargs):
            return _Response({"done_reason": "stop", "message": {"content": json.dumps({
                "text": case["candidate"], "edits": [],
            })}})

        result = run_variant(VARIANTS[0], (case,), post=response)
        self.assertEqual(result["proof_failed"], 1)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["baseline_accepted"], 0)
        self.assertEqual(result["recovered_accepted"], 1)
        self.assertEqual(result["acceptance_delta"], 1)
        self.assertEqual(result["recovered_only_accepted"], 1)
        self.assertEqual(result["recovery_attempted"], 1)
        self.assertEqual(result["recovery_reason_counts"], {"eligible": 1})
        by_risk = {item["risk"]: item for item in result["risk_results"]}
        for risk in case["risks"]:
            self.assertEqual(by_risk[risk]["cases"], 1)
            self.assertEqual(by_risk[risk]["baseline_accepted"], 0)
            self.assertEqual(by_risk[risk]["recovered_accepted"], 1)
            self.assertEqual(by_risk[risk]["acceptance_delta"], 1)

    def test_independent_comparison_exposes_a_recovery_regression(self):
        case = {
            "id": "punctuation-policy",
            "risks": ["punctuation"],
            "raw": "hello team",
            "candidate": "Hello\nteam.",
            "must_contain": ["hello", "team"],
            "must_not_contain": [],
        }

        def response(*_args, **_kwargs):
            return _Response({"done_reason": "stop", "message": {
                "content": json.dumps({
                    "text": case["candidate"],
                    "edits": [{
                        "kind": "formatting",
                        "before": case["raw"],
                        "after": case["candidate"],
                    }],
                }),
            }})

        result = run_variant(VARIANTS[0], (case,), post=response)
        self.assertEqual(result["baseline_accepted"], 1)
        self.assertEqual(result["recovered_accepted"], 0)
        self.assertEqual(result["acceptance_delta"], -1)
        self.assertEqual(result["baseline_only_accepted"], 1)
        self.assertEqual(result["recovery_rejected"], 1)
        self.assertEqual(
            result["recovery_reason_counts"], {"candidate-symbol-policy": 1})
        risk = result["risk_results"][0]
        self.assertEqual(risk["risk"], "punctuation")
        self.assertEqual(risk["baseline_only_accepted"], 1)
        self.assertEqual(risk["acceptance_delta"], -1)

    def test_model_candidate_and_recovery_measurements_remain_content_free(self):
        case = load_cases()[1]

        def response(*_args, **_kwargs):
            return _Response({"done_reason": "stop", "message": {
                "content": json.dumps({
                    "text": case["candidate"],
                    "edits": [{
                        "kind": "formatting",
                        "before": case["raw"],
                        "after": case["candidate"],
                    }],
                }),
            }})

        result = run_variant(VARIANTS[0], (case,), post=response)
        self.assertEqual(result["both_accepted"], 1)
        self.assertEqual(result["baseline_only_accepted"], 0)
        encoded = json.dumps(result)
        self.assertNotIn(case["id"], encoded)
        self.assertNotIn(case["raw"], encoded)
        self.assertNotIn(case["candidate"], encoded)


if __name__ == "__main__":
    unittest.main()
