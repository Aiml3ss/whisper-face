# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from performance_lab import (
    DEFAULT_BUDGETS,
    DEFAULT_CORPUS,
    DEFAULT_MODEL_SCORECARD,
    evaluate_budgets,
    evaluate_observations,
    generate_model_scorecard,
    load_budgets,
    load_corpus,
    load_model_scorecard,
    main,
    run_compiler_stress,
    summarize_corpus,
)


class RepresentativeCorpusTests(unittest.TestCase):
    def test_committed_corpus_is_synthetic_and_covers_dictation_risks(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        summary = summarize_corpus(corpus)

        self.assertGreaterEqual(summary["cases"], 16)
        self.assertEqual(summary["privacy"], "synthetic-text-only")
        self.assertEqual(summary["missing_dimensions"], [])
        self.assertTrue({
            "names",
            "numbers",
            "dates",
            "urls",
            "code",
            "commands",
            "corrections",
            "accent",
            "noise",
            "quiet_speech",
        }.issubset(summary["dimension_counts"]))


class PrivacySafeDashboardTests(unittest.TestCase):
    def test_outcome_dashboard_has_tail_latency_and_quality_metrics_no_text(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        records = [
            {
                "case_id": "name-multilingual",
                "latency_ms": {"asr": 70, "compiler": 4, "end_to_end": 100},
                "edit_characters": 0,
                "pasted_words": 5,
                "zero_edit": True,
                "selected_route": "parakeet",
                "expected_route": "parakeet",
                "receipt": "verified"
            },
            {
                "case_id": "currency-and-decimal",
                "latency_ms": {"asr": 120, "compiler": 8, "end_to_end": 200},
                "edit_characters": 2,
                "pasted_words": 5,
                "zero_edit": False,
                "selected_route": "tiny",
                "expected_route": "turbo",
                "receipt": "unverifiable"
            },
            {
                "case_id": "quiet-speaker",
                "latency_ms": {"asr": 180, "compiler": 12, "end_to_end": 300},
                "edit_characters": 0,
                "pasted_words": 10,
                "zero_edit": True,
                "selected_route": "turbo",
                "expected_route": "turbo",
                "receipt": "verified"
            },
            {
                "case_id": "quiet-speaker",
                "clean": "this transcript-shaped field must be rejected"
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            report = evaluate_observations(path, corpus)

        self.assertEqual(report["records"], 3)
        self.assertEqual(report["rejected_records"], 1)
        self.assertEqual(report["latency_ms"]["end_to_end"]["p50"], 200.0)
        self.assertEqual(report["latency_ms"]["end_to_end"]["p95"], 290.0)
        self.assertEqual(report["latency_ms"]["end_to_end"]["p99"], 298.0)
        self.assertEqual(report["zero_edit"]["rate"], 0.6667)
        self.assertEqual(
            report["correction_burden"]["characters_per_100_words"], 10.0)
        self.assertEqual(report["route_quality"]["rate"], 0.6667)
        self.assertEqual(report["verified_delivery"]["rate"], 0.6667)
        self.assertNotIn("observations", report)
        self.assertNotIn("reference", json.dumps(report))

    def test_malformed_identifiers_and_free_text_labels_are_rejected(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        records = [
            {"case_id": ["not", "hashable"]},
            {
                "case_id": "name-multilingual",
                "latency_ms": {"the user's private sentence": 1},
            },
            {
                "case_id": "name-multilingual",
                "selected_route": "route plus private free text",
                "expected_route": "parakeet",
            },
            {
                "case_id": "name-multilingual",
                "selected_route": "secret@example.com",
                "expected_route": "parakeet",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            report = evaluate_observations(path, corpus)
        self.assertEqual(report["records"], 0)
        self.assertEqual(report["rejected_records"], 4)
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("secret", json.dumps(report))

    def test_runtime_conflict_receipt_is_counted_as_unverified_delivery(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            path.write_text(json.dumps({
                "case_id": "name-multilingual",
                "receipt": "conflict",
            }) + "\n", encoding="utf-8")
            report = evaluate_observations(path, corpus)
        self.assertEqual(report["records"], 1)
        self.assertEqual(report["rejected_records"], 0)
        self.assertEqual(report["verified_delivery"], {
            "available": True,
            "samples": 1,
            "rate": 0.0,
        })


class PerformanceBudgetTests(unittest.TestCase):
    def test_budget_gate_fails_tail_regression_and_insufficient_samples(self):
        budgets = load_budgets(DEFAULT_BUDGETS)
        report = {
            "latency_ms": {
                "compiler": {"samples": 30, "p95": 51.0},
            }
        }
        result = evaluate_budgets(report, budgets, "ci_warm_path")

        self.assertFalse(result["passed"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["checks"][0]["id"], "compiler-p95")
        self.assertEqual(result["checks"][0]["reason"], "threshold-exceeded")

        report["latency_ms"]["compiler"] = {"samples": 10, "p95": 1.0}
        result = evaluate_budgets(report, budgets, "ci_warm_path")
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"][0]["reason"], "insufficient-samples")

        report["latency_ms"]["compiler"] = {"samples": 30, "p95": 4.0}
        self.assertTrue(
            evaluate_budgets(report, budgets, "ci_warm_path")["passed"])


class ModelScorecardTests(unittest.TestCase):
    def test_committed_scorecard_ranks_evidence_and_exposes_missing_metrics(self):
        source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)
        report = generate_model_scorecard(source)

        self.assertEqual(
            {criterion["dimension"] for criterion in source["criteria"]},
            {"quality", "latency", "throughput", "memory", "energy", "startup"},
        )
        self.assertEqual(
            report["ranked"][0]["model_id"],
            "FluidInference/parakeet-unified-en-0.6b-coreml",
        )
        self.assertFalse(report["ranked"][0]["eligible"])
        self.assertIn(
            "license-review-required",
            report["ranked"][0]["ineligibility_reasons"],
        )
        self.assertEqual(report["ranked"][0]["upstream_model_id"], None)
        self.assertIn("conflicting", report["ranked"][0]["upstream_license"])
        whisper = next(
            item for item in report["ranked"]
            if item["model_id"] == "mlx-community/whisper-tiny")
        self.assertFalse(whisper["eligible"])
        self.assertIn("license-review-required", whisper["ineligibility_reasons"])
        self.assertEqual(whisper["artifact_license"], "not-declared")
        self.assertEqual(whisper["upstream_license"], "MIT")
        self.assertEqual(whisper["currentness"], "current-head")
        self.assertTrue(all(
            candidate["revision"] == candidate["repository_head"]
            and candidate["pinned_is_current_head"] is True
            and candidate["model_card_url"].startswith("https://huggingface.co/")
            for candidate in report["ranked"]
        ))
        self.assertEqual(source["evidence"]["provenance_verified_on"], "2026-07-21")
        self.assertIn(
            "could not be independently recalculated",
            source["evidence"]["metric_verification"],
        )
        self.assertIsNone(report["recommendation"])
        self.assertEqual(
            set(report["missing_measurements"]),
            {"energy_j_per_audio_minute", "peak_memory_mb", "startup_ms"},
        )
        self.assertLess(report["measurement_coverage"], 1.0)


class CompilerLifecycleStressTests(unittest.TestCase):
    def test_repeated_compiles_are_deterministic_and_fit_warm_path_budget(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        report = run_compiler_stress(corpus, cycles=2, restart_every=7)

        self.assertEqual(report["operations"], len(corpus["cases"]) * 2)
        self.assertEqual(report["failures"], 0)
        self.assertEqual(report["nondeterministic_outputs"], 0)
        self.assertGreater(report["compiler_restarts"], 1)
        self.assertGreater(report["peak_python_bytes"], 0)
        self.assertIn("long-form", report["exercised"])
        self.assertIn("audio-device-switch", report["requires_physical_validation"])
        budgets = load_budgets(DEFAULT_BUDGETS)
        self.assertTrue(
            evaluate_budgets(report, budgets, "ci_warm_path")["passed"])


class PerformanceLabCliTests(unittest.TestCase):
    def test_cli_exposes_corpus_stress_dashboard_and_model_scorecard(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["corpus", "--format", "json"])
        corpus = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(corpus["missing_dimensions"], [])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([
                "stress", "--cycles", "2", "--restart-every", "7",
                "--format", "json",
            ])
        stress = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(stress["budget"]["passed"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["scorecard", "--format", "table"])
        self.assertEqual(status, 1)
        self.assertIn("MODEL SCORECARD", output.getvalue())
        self.assertIn("UNMEASURED", output.getvalue())
        self.assertIn("recommendation: none", output.getvalue())
        self.assertIn("could not be independently recalculated", output.getvalue())

    def test_evaluate_cli_emits_machine_readable_transcript_free_report(self):
        with tempfile.TemporaryDirectory() as directory:
            observations = Path(directory) / "observations.jsonl"
            observations.write_text(json.dumps({
                "case_id": "name-multilingual",
                "latency_ms": {"end_to_end": 250},
                "edit_characters": 0,
                "pasted_words": 8,
                "zero_edit": True,
                "selected_route": "parakeet",
                "expected_route": "parakeet",
                "receipt": "verified",
            }) + "\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "evaluate", "--observations", str(observations),
                    "--format", "json",
                ])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["privacy"], "transcript-free-outcomes")
        self.assertEqual(report["records"], 1)


if __name__ == "__main__":
    unittest.main()
