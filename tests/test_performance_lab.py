# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from performance_lab import (
    DEFAULT_BUDGETS,
    DEFAULT_CORPUS,
    DEFAULT_MODEL_SCORECARD,
    audit_model_sources,
    evaluate_budgets,
    evaluate_observations,
    evaluate_runtime_traces,
    evaluate_startup_traces,
    fetch_hub_json,
    generate_model_scorecard,
    load_budgets,
    load_corpus,
    load_model_scorecard,
    main,
    refresh_model_scorecard,
    RUNTIME_TRACE_SCHEMAS,
    run_compiler_stress,
    run_lifecycle_simulation,
    summarize_corpus,
    summarize_warm_path,
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

    def test_latency_distributions_expose_p90_ordered_within_tail(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        records = [
            {
                "case_id": "name-multilingual",
                "latency_ms": {"end_to_end": value, "compiler": value // 10},
            }
            for value in (100, 200, 300, 400, 500)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            report = evaluate_observations(path, corpus)

        end_to_end = report["latency_ms"]["end_to_end"]
        self.assertEqual(end_to_end["p90"], 460.0)
        for stage, distribution in report["latency_ms"].items():
            self.assertIn("p90", distribution)
            self.assertLessEqual(distribution["p50"], distribution["p90"])
            self.assertLessEqual(distribution["p90"], distribution["p95"])
            self.assertLessEqual(distribution["p95"], distribution["p99"])

    def test_per_dimension_rollup_accumulates_each_case_dimension(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        records = [
            {
                "case_id": "currency-and-decimal",
                "edit_characters": 0,
                "pasted_words": 10,
                "zero_edit": True,
                "selected_route": "turbo",
                "expected_route": "turbo",
            },
            {
                "case_id": "currency-and-decimal",
                "edit_characters": 4,
                "pasted_words": 10,
                "zero_edit": False,
                "selected_route": "tiny",
                "expected_route": "turbo",
            },
            {
                "case_id": "date-time-zone",
                "edit_characters": 2,
                "pasted_words": 10,
                "zero_edit": True,
                "selected_route": "turbo",
                "expected_route": "turbo",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            report = evaluate_observations(path, corpus)

        by_dimension = report["by_dimension"]
        # currency-and-decimal is [numbers, corrections]; date-time-zone is
        # [dates, numbers], so numbers accumulates all three records.
        self.assertEqual(by_dimension["numbers"], {
            "samples": 3,
            "zero_edit_rate": 0.6667,
            "correction_burden_c100w": 20.0,
            "route_quality_rate": 0.6667,
        })
        self.assertEqual(by_dimension["corrections"], {
            "samples": 2,
            "zero_edit_rate": 0.5,
            "correction_burden_c100w": 20.0,
            "route_quality_rate": 0.5,
        })
        self.assertEqual(by_dimension["dates"], {
            "samples": 1,
            "zero_edit_rate": 1.0,
            "correction_burden_c100w": 20.0,
            "route_quality_rate": 1.0,
        })
        # Dimensions with no observed records are omitted, never divided by zero.
        self.assertNotIn("urls", by_dimension)
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

    def test_unhashable_enum_fields_are_rejected_without_crashing(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        records = [
            {"case_id": "name-multilingual", "selected_route": ["tiny"]},
            {"case_id": "name-multilingual", "expected_route": {"tiny": 1}},
            {"case_id": "name-multilingual", "lifecycle": ["warm-path"]},
            {"case_id": "name-multilingual", "receipt": {"value": "verified"}},
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
        self.assertEqual(report["rejected_by_reason"], {
            "invalid-expected-route": 1,
            "invalid-lifecycle": 1,
            "invalid-receipt": 1,
            "invalid-selected-route": 1,
        })

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


class RuntimeTraceAggregationTests(unittest.TestCase):
    @staticmethod
    def _trace(event, **metrics):
        return "[trace] " + json.dumps({
            "schema_version": 1,
            "event": event,
            **metrics,
        })

    def test_runtime_and_lab_trace_schemas_cannot_drift(self):
        tree = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))
        runtime_schema = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == "PERFORMANCE_TRACE_SCHEMAS"
                    for target in node.targets)
        )
        self.assertEqual(runtime_schema, RUNTIME_TRACE_SCHEMAS)

    def test_trace_report_contains_only_aggregates_and_fixed_rejections(self):
        valid = [
            self._trace("warmup_asr_tiny", duration_ms=10, success=1),
            self._trace("warmup_asr_tiny", duration_ms=30, success=0),
            self._trace(
                "utterance_acoustic",
                adaptive_threshold=0.008,
                clipped_ratio=0.01,
                derived_gain_factor=2.0,
                duration_ms=1000,
                frame_rms_p20=0.01,
                frame_rms_p50=0.02,
                frame_rms_p95=0.04,
                nonfinite_ratio=0,
                peak_amplitude=0.8,
                peak_rms=0.05,
                rms=0.03,
                sample_count=16000,
                sample_rate_hz=16000,
                silence_ratio=0.2,
                trailing_silence_ms=200,
                voiced_fraction=0.75,
            ),
        ]
        private_values = (
            "do not expose this transcript",
            "/Users/private/secret.log",
            "secret@example.com",
        )
        invalid = [
            "[trace] {not-json",
            self._trace(
                "warmup_asr_tiny", duration_ms=1, success=1,
                transcript=private_values[0]),
            self._trace(private_values[2], duration_ms=1, success=1),
            self._trace(
                "warmup_asr_tiny", duration_ms=private_values[1], success=1),
            self._trace(
                "warmup_asr_tiny", duration_ms=float("nan"), success=1),
            self._trace(
                "warmup_asr_tiny", duration_ms=10 ** 1000, success=1),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contains-private-name.log"
            path.write_text(
                "ordinary log may contain private speech\n"
                + "\n".join(valid + invalid) + "\n",
                encoding="utf-8",
            )
            report = evaluate_runtime_traces(path)

        self.assertEqual(report["records"], 3)
        self.assertEqual(report["rejected_records"], 6)
        self.assertEqual(report["ignored_non_trace_lines"], 1)
        tiny = report["events"]["warmup_asr_tiny"]
        self.assertEqual(tiny["records"], 2)
        self.assertEqual(tiny["metrics"]["duration_ms"]["p50"], 20.0)
        self.assertEqual(tiny["success_rate"], 0.5)
        serialized = json.dumps(report)
        self.assertTrue(all(value not in serialized for value in private_values))
        self.assertNotIn("path", serialized.casefold())
        self.assertNotIn("transcript", serialized.casefold())
        self.assertEqual(set(report["rejected_by_reason"]), {
            "invalid-json",
            "invalid-numeric-field",
            "unknown-event",
            "unknown-or-private-field",
        })

    def test_trace_cli_reports_json_without_echoing_input_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-runtime-name.log"
            path.write_text(self._trace(
                "warmup_total", duration_ms=42, success=1) + "\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "traces", "--trace-log", str(path), "--format", "json",
                ])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["records"], 1)
        self.assertNotIn("private-runtime-name", output.getvalue())

    def test_trace_cli_does_not_echo_an_unavailable_input_path(self):
        private_path = "/Users/private/secret-runtime.log"
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = main([
                "traces", "--trace-log", private_path, "--format", "json",
            ])
        self.assertEqual(status, 2)
        self.assertNotIn(private_path, error.getvalue())
        self.assertEqual(
            error.getvalue().strip(),
            "performance lab configuration error: runtime trace input unavailable",
        )

    def test_caller_separated_startup_traces_have_cold_and_warm_budgets(self):
        durations = {
            "warmup_audio_pool": 10,
            "warmup_asr_tiny": 20,
            "warmup_asr_final": 30,
            "warmup_ollama": 40,
            "warmup_total": 100,
        }

        def lines(samples):
            return "\n".join(
                self._trace(event, duration_ms=duration, success=1)
                for _ in range(samples)
                for event, duration in durations.items()
            )

        private_values = (
            "/Users/private/cold-start.log",
            "private transcript must not escape",
        )
        with tempfile.TemporaryDirectory() as directory:
            cold = Path(directory) / "private-cold.log"
            warm = Path(directory) / "private-warm.log"
            cold.write_text(
                private_values[0] + "\n" + lines(3) + "\n",
                encoding="utf-8",
            )
            warm.write_text(
                lines(10) + "\n" + self._trace(
                    "warmup_total", duration_ms=1, success=1,
                    transcript=private_values[1]) + "\n",
                encoding="utf-8",
            )
            report = evaluate_startup_traces(cold, warm)

        self.assertEqual(report["records"], 65)
        self.assertEqual(report["phases"]["cold"]["records"], 15)
        self.assertEqual(report["phases"]["warm"]["records"], 50)
        self.assertEqual(report["rejected_records"], 1)
        self.assertFalse(report["physical_conditions_verified"])
        self.assertEqual(
            report["phase_classification"], "caller-separated-trace-logs")
        self.assertTrue(all(
            event in report["phases"][phase]["events"]
            for phase in ("cold", "warm")
            for event in durations
        ))
        serialized = json.dumps(report)
        self.assertTrue(all(value not in serialized for value in private_values))
        self.assertNotIn("private-cold", serialized)
        self.assertNotIn("private-warm", serialized)

        budget = evaluate_budgets(
            report, load_budgets(DEFAULT_BUDGETS), "startup_readiness")
        self.assertTrue(budget["passed"])
        self.assertEqual(len(budget["checks"]), 20)

        report["phases"]["warm"]["events"][
            "warmup_asr_tiny"]["records"] = 9
        budget = evaluate_budgets(
            report, load_budgets(DEFAULT_BUDGETS), "startup_readiness")
        tiny_warm = next(
            check for check in budget["checks"]
            if check["id"] == "warm-asr-tiny-p95")
        self.assertEqual(tiny_warm["reason"], "insufficient-samples")

        report["phases"]["warm"]["events"][
            "warmup_asr_tiny"]["records"] = 10
        report["phases"]["warm"]["events"][
            "warmup_asr_tiny"]["success_rate"] = 0.9
        budget = evaluate_budgets(
            report, load_budgets(DEFAULT_BUDGETS), "startup_readiness")
        tiny_success = next(
            check for check in budget["checks"]
            if check["id"] == "warm-asr-tiny-success")
        self.assertEqual(tiny_success["reason"], "threshold-exceeded")

    def test_startup_cli_never_reflects_an_unavailable_input_path(self):
        private_path = "/Users/private/cold-start.log"
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = main([
                "startup", "--cold-trace-log", private_path,
                "--warm-trace-log", private_path, "--format", "json",
            ])
        self.assertEqual(status, 2)
        self.assertNotIn(private_path, error.getvalue())
        self.assertEqual(
            error.getvalue().strip(),
            "performance lab configuration error: runtime trace input unavailable",
        )


class WarmPathLatencyTests(unittest.TestCase):
    @staticmethod
    def _warm_path(**metrics):
        return "[trace] " + json.dumps({
            "schema_version": 1,
            "event": "warm_path",
            **metrics,
        })

    def test_warm_path_schema_is_shared_and_stage_ordered(self):
        # Slice 2 adds warm_path to the closed schema. The AST parity test keeps
        # the runtime and lab tuples byte-identical; this pins the lab side so a
        # reordered or renamed stage is caught here too.
        self.assertEqual(
            RUNTIME_TRACE_SCHEMAS["warm_path"],
            ("release_ms", "asr_ms", "compiler_ms",
             "cleanup_ms", "context_ms", "insertion_ms"),
        )

    def test_summarize_warm_path_aggregates_all_stages_with_p90(self):
        # insertion_ms sweeps 1..5 so p90 is a known interpolated tail value.
        samples = (1, 2, 3, 4, 5)
        traces = [
            self._warm_path(
                release_ms=100 * value,
                asr_ms=200 * value,
                compiler_ms=10 * value,
                cleanup_ms=50 * value,
                context_ms=5 * value,
                insertion_ms=value,
            )
            for value in samples
        ]
        private_values = (
            "the user's private dictation",
            "/Users/private/warm.log",
        )
        noise = [
            "an ordinary log line may contain private speech",
            self._warm_path(
                release_ms=1, asr_ms=1, compiler_ms=1, cleanup_ms=1,
                context_ms=1, insertion_ms=1, transcript=private_values[0]),
            "[trace] " + json.dumps({
                "schema_version": 1, "event": "warmup_total",
                "duration_ms": 42, "success": 1}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contains-private-warm-name.log"
            path.write_text(
                "\n".join(traces + noise) + "\n", encoding="utf-8")
            report = summarize_warm_path(path)

        self.assertEqual(report["records"], 5)
        self.assertEqual(report["rejected_records"], 1)
        self.assertEqual(
            report["rejected_by_reason"], {"unknown-or-private-field": 1})
        self.assertEqual(report["ignored_non_trace_lines"], 1)
        self.assertEqual(report["ignored_non_warm_path_records"], 1)
        self.assertEqual(
            set(report["latency_ms"]),
            {"release", "asr", "compiler", "cleanup", "context", "insertion"},
        )
        insertion = report["latency_ms"]["insertion"]
        self.assertIn("p90", insertion)
        self.assertEqual(insertion["samples"], 5)
        self.assertEqual(insertion["p50"], 3.0)
        self.assertEqual(insertion["p90"], 4.6)
        self.assertEqual(insertion["p95"], 4.8)
        self.assertEqual(insertion["p99"], 4.96)
        self.assertEqual(insertion["max"], 5.0)
        # Seconds are converted to milliseconds upstream and carried straight
        # through: the release stage sweeps 100..500 ms.
        self.assertEqual(report["latency_ms"]["release"]["max"], 500.0)
        for distribution in report["latency_ms"].values():
            self.assertLessEqual(distribution["p50"], distribution["p90"])
            self.assertLessEqual(distribution["p90"], distribution["p95"])
            self.assertLessEqual(distribution["p95"], distribution["p99"])
        serialized = json.dumps(report)
        self.assertTrue(
            all(value not in serialized for value in private_values))
        self.assertNotIn("transcript", serialized.casefold())

    def test_warm_path_stage_budget_gates_on_samples_and_tail(self):
        budgets = load_budgets(DEFAULT_BUDGETS)
        stages = ("release", "asr", "compiler", "cleanup", "context",
                  "insertion")
        report = {
            "latency_ms": {
                stage: {"samples": 20, "p95": 1.0} for stage in stages
            }
        }
        passing = evaluate_budgets(report, budgets, "warm_path_stage")
        self.assertTrue(passing["passed"])
        self.assertEqual(len(passing["checks"]), 6)

        report["latency_ms"]["insertion"] = {"samples": 5, "p95": 1.0}
        insufficient = evaluate_budgets(report, budgets, "warm_path_stage")
        insertion = next(
            check for check in insufficient["checks"]
            if check["id"] == "insertion-p95")
        self.assertEqual(insertion["reason"], "insufficient-samples")
        self.assertFalse(insufficient["passed"])

        report["latency_ms"]["insertion"] = {"samples": 20, "p95": 9000.0}
        regressed = evaluate_budgets(report, budgets, "warm_path_stage")
        insertion = next(
            check for check in regressed["checks"]
            if check["id"] == "insertion-p95")
        self.assertEqual(insertion["reason"], "threshold-exceeded")

    def test_warm_path_cli_reports_json_without_echoing_input_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-warm-runtime.log"
            path.write_text(
                "\n".join(
                    self._warm_path(
                        release_ms=100, asr_ms=200, compiler_ms=10,
                        cleanup_ms=50, context_ms=5, insertion_ms=2)
                    for _ in range(3)) + "\n",
                encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "warm-path", "--trace-log", str(path), "--format", "json",
                ])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["records"], 3)
        self.assertEqual(
            set(payload["latency_ms"]),
            {"release", "asr", "compiler", "cleanup", "context", "insertion"})
        self.assertNotIn("private-warm-runtime", output.getvalue())

    def test_warm_path_cli_does_not_echo_an_unavailable_input_path(self):
        private_path = "/Users/private/secret-warm-runtime.log"
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = main([
                "warm-path", "--trace-log", private_path, "--format", "json",
            ])
        self.assertEqual(status, 2)
        self.assertNotIn(private_path, error.getvalue())
        self.assertEqual(
            error.getvalue().strip(),
            "performance lab configuration error: runtime trace input unavailable",
        )


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


class MeasurementProvenanceTests(unittest.TestCase):
    """A metric is measured on a named machine, or it is not measured."""

    def setUp(self):
        self.source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)

    def write(self, payload):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "scorecard.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_committed_scorecard_binds_every_measured_metric_to_a_run(self):
        report = generate_model_scorecard(self.source)
        known = {
            record["measurement_id"] for record in self.source["measurements"]}

        for candidate in report["ranked"]:
            self.assertEqual(
                candidate["measured_on_hardware"],
                ["Apple M4 Pro MacBook Pro"])
            self.assertEqual(
                set(candidate["unmeasured_metrics"]),
                {"energy_j_per_audio_minute", "peak_memory_mb", "startup_ms"})
            self.assertTrue(set(candidate["measurement_ids"]).issubset(known))
            # The 2026-07-21 run's artifacts were not preserved, so nothing
            # published today may claim to be recalculable.
            self.assertFalse(candidate["independently_recalculable"])

    def test_a_measured_metric_without_a_value_is_rejected(self):
        payload = json.loads(json.dumps(self.source))
        payload["candidates"][0]["metrics"]["peak_memory_mb"] = None
        payload["candidates"][0]["metric_provenance"]["peak_memory_mb"] = {
            "state": "measured",
            "measurement_id": "librispeech-test-clean-100-m4-pro-2026-07-21",
        }

        with self.assertRaises(ValueError) as caught:
            load_model_scorecard(self.write(payload))
        self.assertIn("provenance disagrees", str(caught.exception))

    def test_a_value_without_a_named_run_is_rejected(self):
        payload = json.loads(json.dumps(self.source))
        payload["candidates"][0]["metrics"]["startup_ms"] = 1234.0

        with self.assertRaises(ValueError) as caught:
            load_model_scorecard(self.write(payload))
        self.assertIn("provenance disagrees", str(caught.exception))

    def test_an_unknown_measurement_reference_is_rejected(self):
        payload = json.loads(json.dumps(self.source))
        payload["candidates"][0]["metric_provenance"]["wer_pct"][
            "measurement_id"] = "a-run-that-never-happened"

        with self.assertRaises(ValueError) as caught:
            load_model_scorecard(self.write(payload))
        self.assertIn("unknown measurement", str(caught.exception))

    def test_a_run_cannot_claim_recalculability_without_an_artifact(self):
        payload = json.loads(json.dumps(self.source))
        payload["measurements"][0]["independently_recalculable"] = True

        with self.assertRaises(ValueError) as caught:
            load_model_scorecard(self.write(payload))
        self.assertIn("independently recalculable", str(caught.exception))

    def test_a_preserved_artifact_must_publish_its_digest(self):
        payload = json.loads(json.dumps(self.source))
        payload["measurements"][0]["artifacts_preserved"] = True

        with self.assertRaises(ValueError) as caught:
            load_model_scorecard(self.write(payload))
        self.assertIn("needs its digest", str(caught.exception))

    def test_a_measurement_must_name_its_hardware(self):
        payload = json.loads(json.dumps(self.source))
        payload["measurements"][0]["hardware"] = "  "

        with self.assertRaises(ValueError) as caught:
            load_model_scorecard(self.write(payload))
        self.assertIn("hardware must be named", str(caught.exception))


class RefreshModelScorecardTests(unittest.TestCase):
    """Refreshing copies from a real run; it never invents a number."""

    def setUp(self):
        self.source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)

    def summary(self, **overrides):
        engines = []
        for candidate in self.source["candidates"]:
            metrics = candidate["metrics"]
            engines.append({
                "engine": candidate["benchmark_engine"],
                "utterances": 100,
                "wer_pct": metrics["wer_pct"],
                "exact_pct": metrics["exact_pct"],
                "utterance_p90_wer_pct": metrics["utterance_p90_wer_pct"],
                "rtfx": metrics["rtfx"],
                "proc_p95_s": metrics["proc_p95_s"],
                "requested_model_id": candidate["model_id"],
                "requested_model_revision": candidate["revision"],
            })
        payload = {
            "schema_version": 1,
            "dataset": "LibriSpeech test-clean",
            "selection": "deterministic-evenly-spaced",
            "samples": 100,
            "engines": engines,
        }
        payload.update(overrides)
        return payload

    def refresh(self, summary, **overrides):
        arguments = {
            "measurement_id": "librispeech-test-clean-100-m4-pro-2026-07-27",
            "hardware": "Apple M4 Pro MacBook Pro",
            "measured_on": "2026-07-27",
            "command": "uv run benchmark_asr.py ...",
            "summary_sha256": "b" * 64,
            "os_version": "26.0.1",
        }
        arguments.update(overrides)
        return refresh_model_scorecard(self.source, summary, **arguments)

    def test_a_real_run_rebinds_metrics_to_a_recalculable_measurement(self):
        refreshed = self.refresh(self.summary())

        record = refreshed["measurements"][-1]
        self.assertTrue(record["artifacts_preserved"])
        self.assertTrue(record["independently_recalculable"])
        self.assertEqual(record["summary_sha256"], "b" * 64)
        self.assertEqual(record["hardware"], "Apple M4 Pro MacBook Pro")
        for candidate in refreshed["candidates"]:
            provenance = candidate["metric_provenance"]
            self.assertEqual(
                provenance["wer_pct"]["measurement_id"],
                "librispeech-test-clean-100-m4-pro-2026-07-27")
            # Resources the harness does not measure stay unmeasured.
            self.assertEqual(provenance["startup_ms"]["state"], "unmeasured")
            self.assertIsNone(candidate["metrics"]["startup_ms"])

    def test_the_refreshed_scorecard_still_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scorecard.json"
            path.write_text(
                json.dumps(self.refresh(self.summary())), encoding="utf-8")
            report = generate_model_scorecard(load_model_scorecard(path))

        for candidate in report["ranked"]:
            self.assertTrue(candidate["independently_recalculable"])

    def test_a_run_against_a_different_model_is_refused(self):
        summary = self.summary()
        summary["engines"][0]["requested_model_revision"] = "c" * 40

        with self.assertRaises(ValueError) as caught:
            self.refresh(summary)
        self.assertIn("different model or revision", str(caught.exception))

    def test_a_partial_run_is_refused_rather_than_half_refreshed(self):
        summary = self.summary()
        summary["engines"] = summary["engines"][:1]

        with self.assertRaises(ValueError) as caught:
            self.refresh(summary)
        self.assertIn("partial refresh", str(caught.exception))

    def test_a_summary_missing_a_metric_is_refused(self):
        summary = self.summary()
        summary["engines"][0]["rtfx"] = None

        with self.assertRaises(ValueError) as caught:
            self.refresh(summary)
        self.assertIn("no usable rtfx", str(caught.exception))

    def test_an_unnamed_machine_or_forged_digest_is_refused(self):
        for overrides, expected in (
            ({"hardware": "   "}, "hardware must be named"),
            ({"summary_sha256": "not-a-digest"}, "SHA-256"),
            ({"measured_on": "27-07-2026"}, "ISO date"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError) as caught:
                    self.refresh(self.summary(), **overrides)
                self.assertIn(expected, str(caught.exception))

    def test_an_unknown_engine_is_refused(self):
        summary = self.summary()
        summary["engines"][0]["engine"] = "some-other-engine"

        with self.assertRaises(ValueError) as caught:
            self.refresh(summary)
        self.assertIn("not a reviewed candidate", str(caught.exception))


class ScheduledModelAuditTests(unittest.TestCase):
    @staticmethod
    def _reviewed_fetcher(source, *, drifting_model=None):
        by_id = {candidate["model_id"]: candidate
                 for candidate in source["candidates"]}

        def fetch(url):
            candidate = next(
                item for model_id, item in by_id.items()
                if f"/api/models/{model_id}" in url)
            if "/revision/" in url:
                return {"sha": candidate["revision"]}
            expected = candidate["expected_hub_metadata"]
            drifting = candidate["model_id"] == drifting_model
            return {
                "sha": "f" * 40 if drifting else candidate["repository_head"],
                "cardData": {
                    "license": "changed-license" if drifting
                    else expected["license"],
                    "base_model": expected["base_models"],
                },
            }
        return fetch

    def test_reviewed_hub_metadata_and_immutable_revisions_pass(self):
        source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)
        report = audit_model_sources(
            source, fetch_json=self._reviewed_fetcher(source),
            checked_at=lambda: "2026-07-21T20:00:00Z")

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"], {
            "candidates": 3,
            "passed": 3,
            "drifted": 0,
            "errors": 0,
        })
        self.assertEqual(report["privacy"], "public-model-metadata-only")
        self.assertNotIn("provenance", json.dumps(report).lower())

    def test_changed_head_or_license_is_drift_not_a_network_error(self):
        source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)
        model_id = "mlx-community/whisper-tiny"
        report = audit_model_sources(
            source,
            fetch_json=self._reviewed_fetcher(
                source, drifting_model=model_id),
            checked_at=lambda: "2026-07-21T20:00:00Z",
        )
        changed = next(
            candidate for candidate in report["candidates"]
            if candidate["model_id"] == model_id)
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["summary"]["drifted"], 1)
        self.assertEqual(changed["status"], "drift")
        self.assertEqual(changed["reasons"], [
            "repository-head-changed",
            "license-metadata-changed",
        ])

    def test_transport_error_is_bounded_to_type_without_exception_text(self):
        source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)

        def unavailable(_url):
            raise TimeoutError("token or private response must not escape")

        report = audit_model_sources(
            source, fetch_json=unavailable,
            checked_at=lambda: "2026-07-21T20:00:00Z")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["summary"]["errors"], 3)
        self.assertTrue(all(
            item["error_type"] == "TimeoutError"
            and item["reasons"] == ["metadata-check-failed"]
            for item in report["candidates"]
        ))
        self.assertNotIn("private response", json.dumps(report))

    def test_hub_fetch_has_bounded_timeout_retries_and_response_size(self):
        attempts = []
        sleeps = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read(_limit):
                return b'{"sha":"abc"}'

            @staticmethod
            def geturl():
                return "https://huggingface.co/api/models/example/model"

        def opener(url, timeout):
            attempts.append((url, timeout))
            if len(attempts) < 3:
                raise TimeoutError("retry")
            return Response()

        result = fetch_hub_json(
            "https://huggingface.co/api/models/example/model",
            timeout=2.0,
            attempts=3,
            opener=opener,
            sleeper=sleeps.append,
        )
        self.assertEqual(result, {"sha": "abc"})
        self.assertEqual([timeout for _url, timeout in attempts], [2.0] * 3)
        self.assertEqual(sleeps, [0.1, 0.2])

    def test_hub_fetch_rejects_cross_origin_redirect_before_reading(self):
        reads = []

        class RedirectedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def geturl():
                return "https://attacker.example/api/models/private"

            @staticmethod
            def read(_limit):
                reads.append(True)
                return b'{"sha":"stolen"}'

        with self.assertRaisesRegex(ValueError, "public Hugging Face API"):
            fetch_hub_json(
                "https://huggingface.co/api/models/example/model",
                attempts=1,
                opener=lambda _url, timeout: RedirectedResponse(),
            )
        self.assertEqual(reads, [])

    def test_cli_always_writes_audit_evidence_and_uses_distinct_exit_codes(self):
        source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "model-audit.json"
            output = io.StringIO()
            fetcher = self._reviewed_fetcher(
                source, drifting_model="mlx-community/whisper-tiny")
            with mock.patch("performance_lab.fetch_hub_json", fetcher), \
                    contextlib.redirect_stdout(output):
                status = main([
                    "audit-models", "--format", "json",
                    "--output", str(artifact),
                ])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "drift")
            self.assertEqual(
                json.loads(artifact.read_text(encoding="utf-8"))["status"],
                "drift",
            )

            with mock.patch(
                    "performance_lab.fetch_hub_json",
                    side_effect=TimeoutError("never include this message")), \
                    contextlib.redirect_stdout(io.StringIO()):
                status = main([
                    "audit-models", "--format", "json",
                    "--output", str(artifact),
                ])
            evidence = artifact.read_text(encoding="utf-8")
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(evidence)["status"], "error")
            self.assertNotIn("never include this message", evidence)

    def test_weekly_workflow_is_read_only_and_uploads_failed_audit_evidence(self):
        workflow = (ROOT / ".github" / "workflows" / "model-audit.yml").read_text(
            encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("performance_lab.py audit-models", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)


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

    def test_adapter_lifecycle_faults_are_deterministic_not_physical_evidence(self):
        corpus = load_corpus(DEFAULT_CORPUS)
        first = run_lifecycle_simulation(corpus, iterations=2)
        second = run_lifecycle_simulation(corpus, iterations=2)

        self.assertEqual(first, second)
        self.assertEqual(first["privacy"], "synthetic-text-only")
        self.assertEqual(first["evidence_scope"], "adapter-simulation-only")
        self.assertFalse(first["physical_evidence"])
        self.assertEqual(first["failures"], 0)
        self.assertEqual(first["nondeterministic_outputs"], 0)
        self.assertEqual(first["faults_injected"], 4)
        self.assertEqual(first["faults_observed"], 4)
        self.assertEqual(first["recoveries"], 4)
        self.assertEqual(set(first["scenarios"]), {
            "back-to-back",
            "long-form",
            "process-restart",
            "sleep-wake",
            "audio-device-switch",
        })
        self.assertEqual(
            first["scenarios"]["sleep-wake"]["blocked_operations"], 2)
        self.assertEqual(
            first["scenarios"]["audio-device-switch"]["blocked_operations"],
            2,
        )
        self.assertIn(
            "physical-operating-system-sleep-wake",
            first["requires_physical_validation"],
        )
        self.assertIn(
            "physical-audio-device-switch",
            first["requires_physical_validation"],
        )
        serialized = json.dumps(first)
        self.assertNotIn("reference", serialized.casefold())
        self.assertNotIn("transcript", serialized.casefold())
        self.assertNotIn("path", serialized.casefold())

    def test_lifecycle_cli_emits_only_simulation_evidence(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([
                "lifecycle", "--iterations", "2", "--format", "json",
            ])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertFalse(report["physical_evidence"])
        self.assertEqual(report["failures"], 0)

    def test_scheduled_lifecycle_workflow_is_read_only_and_preserves_evidence(self):
        workflow = (
            ROOT / ".github" / "workflows" / "performance-lifecycle.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("performance_lab.py lifecycle", workflow)
        self.assertIn("tests/test_performance_lab.py", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)


class PerformanceLabCliTests(unittest.TestCase):
    def test_cli_refreshes_the_scorecard_from_a_preserved_summary(self):
        source = load_model_scorecard(DEFAULT_MODEL_SCORECARD)
        summary = {
            "schema_version": 1,
            "dataset": "LibriSpeech test-clean",
            "selection": "deterministic-evenly-spaced",
            "samples": 100,
            "engines": [
                {
                    "engine": candidate["benchmark_engine"],
                    "requested_model_id": candidate["model_id"],
                    "requested_model_revision": candidate["revision"],
                    **{
                        metric: candidate["metrics"][metric]
                        for metric in (
                            "wer_pct", "exact_pct", "utterance_p90_wer_pct",
                            "rtfx", "proc_p95_s")
                    },
                }
                for candidate in source["candidates"]
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.json"
            destination = Path(directory) / "model_scorecard.json"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "refresh-model-scorecard",
                    "--summary", str(summary_path),
                    "--measurement-id", "cli-refresh-check",
                    "--hardware", "Apple M4 Pro MacBook Pro",
                    "--measured-on", "2026-07-27",
                    "--os-version", "26.0.1",
                    "--output", str(destination),
                ])
            self.assertEqual(status, 0)
            self.assertIn("MEASURED ON NAMED HARDWARE", output.getvalue())
            self.assertIn("cli-refresh-check", output.getvalue())
            self.assertIn(
                "recalculable from a preserved artifact", output.getvalue())

            refreshed = load_model_scorecard(destination)
            record = next(
                item for item in refreshed["measurements"]
                if item["measurement_id"] == "cli-refresh-check")
            self.assertEqual(
                record["summary_sha256"],
                hashlib.sha256(summary_path.read_bytes()).hexdigest())
            # Copying, not inventing: the replayed run must reproduce the
            # committed numbers exactly.
            for candidate, original in zip(
                    refreshed["candidates"], source["candidates"]):
                self.assertEqual(candidate["metrics"], original["metrics"])

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
