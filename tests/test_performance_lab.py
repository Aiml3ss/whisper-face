# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import contextlib
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
    fetch_hub_json,
    generate_model_scorecard,
    load_budgets,
    load_corpus,
    load_model_scorecard,
    main,
    RUNTIME_TRACE_SCHEMAS,
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
