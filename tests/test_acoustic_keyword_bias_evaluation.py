# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acoustic_keyword_bias_evaluation import (  # noqa: E402
    PHYSICAL_SOURCE,
    SYNTHETIC_SOURCE,
    evaluate_keyword_bias,
)
from acoustic_keyword_memory import AcousticKeywordMemory  # noqa: E402
from benchmark_acoustic_keyword_bias import (  # noqa: E402
    main,
    run_synthetic_benchmark,
    synthetic_cases,
)
from test_acoustic_keyword_activation import (  # noqa: E402,F401
    AcousticKeywordActivationTests,
)


class AcousticKeywordBiasEvaluationTests(unittest.TestCase):
    @staticmethod
    def _eligible_candidate():
        memory = AcousticKeywordMemory()
        candidate = None
        for index in range(3):
            candidate = memory.accept_explicit_correction(
                "SecretProjectLexeme", evidence_id=f"correction-{index}")
        return candidate

    @staticmethod
    def _case_records(case_id: str):
        return next(
            records for name, _expected, records in synthetic_cases()
            if name == case_id
        )

    def test_balanced_caller_attested_physical_gain_can_only_keep_offline(self):
        receipt = evaluate_keyword_bias(
            self._eligible_candidate(),
            self._case_records("constructed-physical-gain"),
        )

        self.assertEqual(receipt["verdict"], "keep")
        self.assertEqual(
            receipt["reason"],
            "caller-attested-physical-gain-without-regression",
        )
        self.assertEqual(receipt["runtime_effect"], "none")
        self.assertEqual(
            receipt["candidate_evidence"]["recognition_effect"], "none")
        self.assertFalse(receipt["policy"]["synthetic_keep_allowed"])
        self.assertFalse(receipt["activation_claim"])
        self.assertFalse(receipt["recognition_authority"])
        self.assertFalse(receipt["recognition_quality_claim"])
        self.assertEqual(
            receipt["evidence"]["independently_verified_physical_cases"], 0)

    def test_synthetic_gain_stays_insufficient_and_is_separately_counted(self):
        receipt = evaluate_keyword_bias(
            self._eligible_candidate(),
            self._case_records("synthetic-gain"),
        )

        self.assertEqual(receipt["verdict"], "insufficient-evidence")
        self.assertEqual(receipt["reason"], "physical-evidence-required")
        self.assertEqual(receipt["evidence"]["synthetic_cases"], 40)
        self.assertEqual(
            receipt["evidence"]["caller_attested_physical_cases"], 0)
        self.assertEqual(receipt["evidence"]["evidence_scope"], "synthetic-only")

    def test_regression_kills_and_small_or_mixed_batches_stay_insufficient(self):
        candidate = self._eligible_candidate()
        regressed = evaluate_keyword_bias(
            candidate,
            self._case_records("constructed-physical-regression"),
        )
        small = evaluate_keyword_bias(
            candidate,
            self._case_records("too-few-constructed-physical"),
        )
        mixed = evaluate_keyword_bias(
            candidate,
            self._case_records("mixed-sources"),
        )

        self.assertEqual(
            (regressed["verdict"], regressed["reason"]),
            ("kill", "recognition-regression-observed"),
        )
        self.assertEqual(small["verdict"], "insufficient-evidence")
        self.assertEqual(
            mixed["reason"], "evidence-sources-must-be-separated")

    def test_strict_records_reject_private_or_duplicate_fields_without_echoing(self):
        candidate = self._eligible_candidate()
        private_term = "SecretProjectLexeme"
        records = self._case_records("constructed-physical-gain")
        records[0] = {**records[0], "surrounding_transcript": private_term}

        invalid = evaluate_keyword_bias(candidate, records)
        duplicate_records = self._case_records("constructed-physical-gain")
        duplicate_records[1]["case_token"] = duplicate_records[0]["case_token"]
        duplicate = evaluate_keyword_bias(candidate, duplicate_records)

        self.assertEqual(
            (invalid["verdict"], invalid["reason"]),
            ("kill", "invalid-evidence"),
        )
        self.assertEqual(duplicate["reason"], "duplicate-evidence")
        encoded = json.dumps(invalid)
        self.assertNotIn(private_term, encoded)
        self.assertNotIn("surrounding_transcript", encoded)
        self.assertNotIn("case-", encoded)

    def test_memory_eligibility_is_required_but_never_grants_authority(self):
        memory = AcousticKeywordMemory()
        candidate = memory.observe(
            "SecretProjectLexeme", evidence_id="one-observation")
        receipt = evaluate_keyword_bias(
            candidate,
            self._case_records("constructed-physical-gain"),
        )

        self.assertEqual(receipt["verdict"], "insufficient-evidence")
        self.assertEqual(receipt["reason"], "memory-eligibility-not-met")
        self.assertFalse(receipt["candidate_evidence"]["eligible"])

    def test_benchmark_is_deterministic_and_makes_no_physical_claim(self):
        report = run_synthetic_benchmark()
        first = io.StringIO()
        second = io.StringIO()
        with redirect_stdout(first):
            first_status = main([])
        with redirect_stdout(second):
            second_status = main([])

        self.assertEqual(report["matched"], report["cases"])
        self.assertEqual(report["counts"], {
            "keep": 1,
            "kill": 2,
            "insufficient-evidence": 3,
        })
        self.assertFalse(report["physical_evidence"])
        self.assertFalse(report["activation_claim"])
        self.assertFalse(report["recognition_quality_claim"])
        self.assertEqual((first_status, second_status), (0, 0))
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertNotIn("FixtureLexeme", first.getvalue())


if __name__ == "__main__":
    unittest.main()
