# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import os
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_relisten_activation import (  # noqa: E402
    MANIFEST_KIND,
    MIN_REAL_SAMPLES,
    MIN_REAL_SAMPLES_PER_OUTCOME,
    BenchmarkError,
    evaluate,
    load_manifest,
    main,
    read_microspan_wav,
    render_json,
)
from process_verifier import (  # noqa: E402
    RefusalReason,
    VerificationReceipt,
    VerificationResult,
)
from relisten_activation import (  # noqa: E402
    ActivationError,
    build_activation_receipt,
    load_activation_receipt,
    validate_activation_receipt,
)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 0.001
        return self.now


class SequenceClock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class FakeVerifier:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.closed = False

    def verify(self, samples, sample_rate, expected, *, deadline_at):
        self.calls.append((tuple(samples), sample_rate, expected, deadline_at))
        outcome = next(self.outcomes)
        if isinstance(outcome, RefusalReason):
            return VerificationReceipt(refusal=outcome)
        return VerificationReceipt(result=VerificationResult(
            outcome, 0.9, "fake-local"))

    def close(self):
        self.closed = True


class RelistenActivationBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wav = self.root / "private-audio.wav"
        self._write_wav(self.wav, (-32768, -8192, 0, 8192, 32767))

    @staticmethod
    def _write_wav(path, values):
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16_000)
            target.writeframes(struct.pack(f"<{len(values)}h", *values))

    def _manifest(self, cases):
        path = self.root / "private-manifest.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "kind": MANIFEST_KIND,
            "cases": cases,
        }), encoding="utf-8")
        return path

    def _case(self, index, *, outcome="confirmed", evidence="synthetic-test"):
        return {
            "case_id": f"private-case-{index}",
            "wav": self.wav.name,
            "expected_text": f"Project Bluebird secret {index}",
            "expected_outcome": outcome,
            "evidence_type": evidence,
        }

    @staticmethod
    def _providers(outcomes):
        return {
            "disposable_whisper_tiny": FakeVerifier(outcomes[0]),
            "prewarmed_whisper_tiny": FakeVerifier(outcomes[1]),
            "whole_span_local_baseline": FakeVerifier(outcomes[2]),
        }

    def test_strict_wav_reader_and_manifest_keep_private_fields_internal(self):
        manifest = load_manifest(self._manifest([self._case(1)]))
        samples = read_microspan_wav(manifest.cases[0].wav)

        self.assertEqual(len(samples), 5)
        self.assertEqual(samples[0], -1.0)
        self.assertAlmostEqual(samples[-1], 32767 / 32768)
        self.assertNotIn("Bluebird", repr(manifest))
        self.assertNotIn(str(self.root), repr(manifest))

    def test_all_engines_receive_identical_whole_microspan_and_exact_text(self):
        manifest = load_manifest(self._manifest([
            self._case(1, outcome="confirmed"),
            self._case(2, outcome="contradicted"),
        ]))
        providers = self._providers((
            ["confirmed", "contradicted"],
            ["confirmed", "contradicted"],
            ["confirmed", "contradicted"],
        ))

        report = evaluate(
            manifest, providers, deadline_seconds=1.0, clock=FakeClock())

        first_calls = [provider.calls[0] for provider in providers.values()]
        self.assertEqual(first_calls[0][:3], first_calls[1][:3])
        self.assertEqual(first_calls[1][:3], first_calls[2][:3])
        self.assertEqual(report["cases"], 2)
        for engine in report["engines"][:3]:
            self.assertEqual(engine["correct"], 2)
            self.assertEqual(engine["exact_case_accuracy_pct"], 100.0)

    def test_engine_order_rotates_to_counterbalance_cache_warming(self):
        events = []

        class OrderedVerifier(FakeVerifier):
            def __init__(self, engine_id):
                super().__init__(["confirmed"] * 3)
                self.engine_id = engine_id

            def verify(self, samples, sample_rate, expected, *, deadline_at):
                events.append((self.engine_id, expected.rsplit(" ", 1)[-1]))
                return super().verify(
                    samples, sample_rate, expected, deadline_at=deadline_at)

        manifest = load_manifest(self._manifest([
            self._case(0), self._case(1), self._case(2),
        ]))
        providers = {
            engine_id: OrderedVerifier(engine_id)
            for engine_id in (
                "disposable_whisper_tiny",
                "prewarmed_whisper_tiny",
                "whole_span_local_baseline",
            )
        }

        report = evaluate(
            manifest, providers, deadline_seconds=1.0, clock=FakeClock())

        self.assertEqual([event[0] for event in events], [
            "disposable_whisper_tiny",
            "prewarmed_whisper_tiny",
            "whole_span_local_baseline",
            "prewarmed_whisper_tiny",
            "whole_span_local_baseline",
            "disposable_whisper_tiny",
            "whole_span_local_baseline",
            "disposable_whisper_tiny",
            "prewarmed_whisper_tiny",
        ])
        self.assertEqual(
            report["execution_order"],
            "deterministic-rotation-by-case-index",
        )

    def test_report_aggregates_refusals_latency_and_never_leaks_cases(self):
        manifest = load_manifest(self._manifest([
            self._case(1), self._case(2),
        ]))
        providers = self._providers((
            ["confirmed", RefusalReason.TIMEOUT],
            ["inconclusive", "confirmed"],
            [RefusalReason.CRASH, "contradicted"],
        ))

        report = evaluate(
            manifest, providers, deadline_seconds=1.0, clock=FakeClock())
        encoded = render_json(report)

        disposable = report["engines"][0]
        self.assertEqual(disposable["correct"], 1)
        self.assertEqual(disposable["refusals"]["timeout"], 1)
        self.assertEqual(disposable["latency_ms"], {
            "p50": 1.0, "p95": 1.0, "max": 1.0,
        })
        for private in (
                "Project Bluebird", "private-case", "private-audio",
                str(self.root), "expected_text"):
            self.assertNotIn(private, encoded)
        for forbidden_claim in ("winner", "best", "rank", "recommended"):
            self.assertNotIn(forbidden_claim, encoded.casefold())

    def test_malformed_provider_receipt_is_a_closed_refusal(self):
        class MalformedVerifier:
            def verify(self, *_args, **_kwargs):
                return VerificationReceipt(result=VerificationResult(
                    "invented", float("nan"), "private engine output"))

        manifest = load_manifest(self._manifest([self._case(1)]))
        providers = {
            engine_id: MalformedVerifier()
            for engine_id in (
                "disposable_whisper_tiny",
                "prewarmed_whisper_tiny",
                "whole_span_local_baseline",
            )
        }

        report = evaluate(
            manifest, providers, deadline_seconds=1.0, clock=FakeClock())

        for engine in report["engines"][:3]:
            self.assertEqual(engine["refusals"]["malformed-result"], 1)
            self.assertNotIn("private", render_json(engine))

    def test_late_nominal_success_is_counted_as_timeout(self):
        manifest = load_manifest(self._manifest([self._case(1)]))
        providers = self._providers((["confirmed"],) * 3)
        clock = SequenceClock((
            100.0, 102.0,
            200.0, 200.1,
            300.0, 300.1,
        ))

        report = evaluate(
            manifest, providers, deadline_seconds=1.0, clock=clock)

        self.assertEqual(report["engines"][0]["refusals"]["timeout"], 1)
        self.assertEqual(report["engines"][0]["correct"], 0)
        self.assertEqual(report["engines"][1]["correct"], 1)

    def test_minimum_real_sample_gate_never_makes_activation_claim(self):
        cases = []
        outcomes = []
        for index in range(MIN_REAL_SAMPLES):
            expected = "confirmed" \
                if index < MIN_REAL_SAMPLES_PER_OUTCOME else "contradicted"
            cases.append(self._case(
                index, outcome=expected, evidence="real-recorded"))
            outcomes.append(expected)
        manifest = load_manifest(self._manifest(cases))
        providers = self._providers((outcomes, outcomes, outcomes))

        report = evaluate(
            manifest, providers, deadline_seconds=1.0, clock=FakeClock())

        activation = report["activation_evidence"]
        self.assertEqual(activation["state"], "minimum-sample-count-met")
        self.assertFalse(activation["activation_claim"])
        self.assertEqual(activation["decision"], "manual-review-required")
        self.assertTrue(activation["runtime_candidate"])
        self.assertEqual(
            activation["runtime_candidate_reason"],
            "manual-review-required",
        )

        synthetic = load_manifest(self._manifest([self._case(999)]))
        insufficient = evaluate(
            synthetic,
            self._providers((["confirmed"],) * 3),
            deadline_seconds=1.0,
            clock=FakeClock(),
        )
        self.assertEqual(
            insufficient["activation_evidence"]["state"],
            "insufficient-real-samples",
        )
        self.assertFalse(
            insufficient["activation_evidence"]["runtime_candidate"])

    def test_activation_receipt_requires_explicit_review_and_stays_closed(self):
        cases = []
        outcomes = []
        for index in range(MIN_REAL_SAMPLES):
            expected = "confirmed" \
                if index < MIN_REAL_SAMPLES_PER_OUTCOME else "contradicted"
            cases.append(self._case(
                index, outcome=expected, evidence="real-recorded"))
            outcomes.append(expected)
        report = evaluate(
            load_manifest(self._manifest(cases)),
            self._providers((outcomes, outcomes, outcomes)),
            deadline_seconds=1.0,
            clock=FakeClock(),
        )

        with self.assertRaises(ActivationError):
            build_activation_receipt(
                report, manual_review_approved=False)
        receipt = build_activation_receipt(
            report, manual_review_approved=True)
        self.assertTrue(validate_activation_receipt(receipt).ready)
        self.assertNotIn("Bluebird", json.dumps(receipt))
        self.assertEqual(
            set(receipt["evidence"]),
            {
                "real_samples", "real_confirmed_cases",
                "real_contradicted_cases", "exact_accuracy_pct",
                "p95_latency_ms", "refusals", "source_report_sha256",
            },
        )

        changed = dict(receipt)
        changed["model_revision"] = "0" * 40
        self.assertFalse(validate_activation_receipt(changed).ready)

    def test_cli_writes_private_activation_receipt_after_all_gates(self):
        cases = []
        outcomes = []
        for index in range(MIN_REAL_SAMPLES):
            expected = "confirmed" \
                if index < MIN_REAL_SAMPLES_PER_OUTCOME else "contradicted"
            cases.append(self._case(
                index, outcome=expected, evidence="real-recorded"))
            outcomes.append(expected)
        manifest = self._manifest(cases)
        activation_path = self.root / "relisten_activation.json"

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = main(
                [
                    str(manifest),
                    "--deadline-seconds", "1",
                    "--approve-runtime", str(activation_path),
                    "--confirm-manual-review",
                ],
                providers=self._providers((outcomes, outcomes, outcomes)),
                clock=FakeClock(),
            )

        self.assertEqual(status, 0)
        self.assertTrue(load_activation_receipt(activation_path).ready)
        if os.name == "posix":
            self.assertEqual(
                activation_path.stat().st_mode & 0o777, 0o600)

    def test_general_llm_audio_is_honestly_unavailable(self):
        manifest = load_manifest(self._manifest([self._case(1)]))
        report = evaluate(
            manifest,
            self._providers((["confirmed"],) * 3),
            deadline_seconds=1.0,
            clock=FakeClock(),
        )

        general = report["engines"][-1]
        self.assertEqual(general, {
            "engine_id": "general_llm_audio",
            "availability": "unavailable",
            "reason": "no-valid-local-audio-verifier-contract",
            "metrics": None,
        })

    def test_cli_emits_only_json_report_and_closes_providers(self):
        manifest = self._manifest([self._case(1)])
        providers = self._providers((["confirmed"],) * 3)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                [str(manifest), "--deadline-seconds", "1"],
                providers=providers,
                clock=FakeClock(),
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["privacy"], "transcript-free-aggregate-only")
        self.assertTrue(all(provider.closed for provider in providers.values()))
        self.assertNotIn("Bluebird", stdout.getvalue())

    def test_invalid_manifest_and_wav_fail_without_echoing_private_paths(self):
        outside = self.root.parent / "private-outside.wav"
        manifest = self._manifest([{**self._case(1), "wav": "../private-outside.wav"}])

        with self.assertRaises(BenchmarkError):
            load_manifest(manifest)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = main(
                [str(manifest)],
                providers=self._providers((["confirmed"],) * 3),
            )

        self.assertEqual(status, 2)
        self.assertNotIn(str(outside), stderr.getvalue())
        self.assertNotIn("private", stderr.getvalue().casefold())


if __name__ == "__main__":
    unittest.main()
