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

from benchmark_voice_compiler import (
    DEFAULT_CASES,
    build_report,
    character_edit_distance,
    evaluate_case,
    evaluate_transcripts,
    load_cases,
    main,
    render_table,
)


class GoldenCorpusTests(unittest.TestCase):
    def test_committed_corpus_covers_every_quality_dimension_and_passes(self):
        report = build_report(DEFAULT_CASES, None)
        golden = report["golden"]
        self.assertEqual(golden["passed"], golden["total"])
        self.assertGreaterEqual(golden["total"], 8)
        self.assertTrue(golden["compiler_latency"]["passed"])
        self.assertLessEqual(
            golden["compiler_latency"]["max"],
            golden["compiler_latency"]["budget_ms"],
        )
        self.assertEqual(
            set(golden["categories"]),
            {
                "anchor_preservation",
                "context_firewall",
                "corrections",
                "personal_priors",
                "prosody",
                "rare_terms",
                "stable_prefixes",
            },
        )

    def test_context_firewall_benchmark_reports_aggregate_evidence_only(self):
        report = build_report(DEFAULT_CASES, None)
        cases = [case for case in report["golden"]["cases"]
                 if case["category"] == "context_firewall"]
        self.assertEqual(len(cases), 3)
        self.assertTrue(all(case["passed"] for case in cases))
        encoded = json.dumps(cases, sort_keys=True)
        for private in (
                "Use Gwen", "Qwen", "teh", "ordinary dictated prose",
                "2042.76it/s"):
            self.assertNotIn(private, encoded)

    def test_context_firewall_rejects_unknown_expectation_keys(self):
        result = evaluate_case({
            "id": "misspelled-expectation",
            "category": "context_firewall",
            "operation": "context_firewall",
            "voice": {
                "hypotheses": [{"text": "Ship it", "confidence": 0.9}],
            },
            "expect": {"dispositoin": "no_effect"},
        })
        self.assertFalse(result["passed"])
        self.assertIn(
            "unsupported context firewall expectations: ['dispositoin']",
            result["errors"],
        )

    def test_a_regression_is_explained_instead_of_hidden(self):
        case = {
            "id": "deliberate-failure",
            "category": "test",
            "operation": "compile",
            "voice": {
                "hypotheses": [{"text": "actual", "confidence": 0.8}]
            },
            "expect": {"text": "expected"},
        }
        result = evaluate_case(case)
        self.assertFalse(result["passed"])
        self.assertIn("expected 'expected', got 'actual'", result["errors"][0])

    def test_case_loader_rejects_duplicate_identifiers(self):
        payload = {
            "schema_version": 1,
            "cases": [
                {"id": "same", "category": "one"},
                {"id": "same", "category": "two"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique id"):
                load_cases(path)


class TranscriptMetricTests(unittest.TestCase):
    def _write_jsonl(self, entries):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "transcripts.jsonl"
        path.write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8",
        )
        self.addCleanup(directory.cleanup)
        return path

    def test_runtime_only_log_does_not_claim_user_quality_metrics(self):
        path = self._write_jsonl([
            {
                "raw": "Use Gwen",
                "clean": "Use Qwen",
                "metrics": {
                    "release_s": 0.2,
                    "asr_s": 0.1,
                    "compiler_s": 0.004,
                    "cleanup_s": 0.01,
                    "confidence": 0.8,
                    "verified": True,
                },
            }
        ])
        result = evaluate_transcripts(path)
        self.assertFalse(result["zero_edit_proxy"]["available"])
        self.assertFalse(result["correction_burden"]["available"])
        self.assertEqual(result["performance"]["release_s"]["p50"], 0.2)
        self.assertEqual(result["performance"]["compiler_s"]["p95"], 0.004)
        self.assertEqual(result["performance"]["verified_rate"]["rate"], 1.0)

    def test_explicit_accepted_text_enables_quality_metrics(self):
        path = self._write_jsonl([
            {"clean": "Use Gwen", "accepted_text": "Use Qwen"},
            {"clean": "Ship now", "accepted_text": "Ship now"},
        ])
        result = evaluate_transcripts(path)
        self.assertEqual(result["zero_edit_proxy"]["rate"], 0.5)
        burden = result["correction_burden"]
        self.assertEqual(burden["edit_characters"], 1)
        self.assertEqual(burden["pasted_words"], 4)
        self.assertEqual(burden["characters_per_100_words"], 25.0)

    def test_explicit_zero_edit_without_text_supports_rate_not_burden(self):
        path = self._write_jsonl([
            {"clean": "One", "metrics": {"zero_edit": True}},
            {"clean": "Two", "zero_edit": False},
        ])
        result = evaluate_transcripts(path)
        self.assertEqual(result["zero_edit_proxy"]["rate"], 0.5)
        self.assertFalse(result["correction_burden"]["available"])

    def test_malformed_records_are_counted_and_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcripts.jsonl"
            path.write_text(
                '{"clean":"ok","metrics":{"release_s":0.1}}\n'
                "not json\n[]\n",
                encoding="utf-8",
            )
            result = evaluate_transcripts(path)
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["malformed_records"], 2)

    def test_character_burden_uses_edit_distance(self):
        self.assertEqual(character_edit_distance("kitten", "sitting"), 3)
        self.assertEqual(character_edit_distance("", "three"), 5)


class OutputTests(unittest.TestCase):
    def test_table_is_human_readable_and_honest_about_missing_log(self):
        report = build_report(DEFAULT_CASES, None)
        table = render_table(report)
        self.assertIn("VOICE COMPILER GOLDEN CORPUS", table)
        self.assertIn("TRANSCRIPT TELEMETRY", table)
        self.assertIn("zero-edit proxy: unavailable", table)

    def test_json_cli_is_machine_readable(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([
                "--cases", str(DEFAULT_CASES),
                "--transcripts", "/definitely/missing/transcripts.jsonl",
                "--format", "json",
            ])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["golden"]["pass_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
