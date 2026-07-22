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
    _guard_cleaned_output, _semantic_failure, load_cases,
)
from cleanup_proof_recovery import (  # noqa: E402
    RecoveredEdit, recover_cleanup_proof, replay_edits,
)


def _runtime_guard(source, candidate):
    return _guard_cleaned_output(source, candidate, "stop", "capture")


class CleanupProofRecoveryTests(unittest.TestCase):
    def test_checked_in_candidates_recover_only_conservative_transformations(self):
        outcomes = {}
        for case in load_cases():
            result = recover_cleanup_proof(
                case["raw"], case["candidate"],
                output_guard=lambda source, candidate, case=case: (
                    _runtime_guard(source, candidate)
                    or ("semantic-fixture-failed"
                        if _semantic_failure(case, candidate) else None)),
            )
            outcomes[case["id"]] = result.receipt.disposition
            if result.receipt.disposition == "recovered":
                self.assertEqual(replay_edits(case["raw"], result.edits),
                                 case["candidate"])

        self.assertEqual(outcomes, {
            "correction-in-place": "recovered",
            "question-not-answer": "recovered",
            "layout-command": "recovered",
            "spoken-list": "recovered",
            # Real words do not become disposable merely because a model calls
            # the phrase filler or an exact diff can remove it.
            "filler-and-anchor": "rejected",
            "scratch-that": "recovered",
        })

    def test_exact_diff_does_not_make_arbitrary_rewrite_eligible(self):
        source = "send the customer refund tomorrow"
        candidate = "deny the customer refund tomorrow."
        result = recover_cleanup_proof(
            source, candidate, output_guard=_runtime_guard)
        self.assertEqual(result.text, source)
        self.assertEqual(result.edits, ())
        self.assertEqual(result.receipt.reason, "unproved-transformation")
        self.assertFalse(result.receipt.replay_verified)

    def test_composite_proof_cannot_smuggle_symbols_or_punctuation(self):
        source = "um please ship Tuesday actually Wednesday tomorrow"
        for candidate in (
            "Please ship Wednesday tomorrow!!!",
            "Please ship Wednesday tomorrow 😀.",
            "Please ship Wednesday tomorrow $.",
            "Please ship\n- Wednesday tomorrow.",
            "Please ship\tWednesday tomorrow.",
        ):
            with self.subTest(candidate=candidate):
                result = recover_cleanup_proof(
                    source, candidate, output_guard=_runtime_guard)
                self.assertEqual(
                    result.receipt.reason, "candidate-symbol-policy")
                self.assertEqual(result.text, source)
                self.assertFalse(result.receipt.replay_verified)

    def test_protected_anchor_loss_fails_closed_even_with_scratch_marker(self):
        source = "Tell Morgan API v2 is ready scratch that send it tomorrow"
        candidate = "Tell Morgan send it tomorrow."
        result = recover_cleanup_proof(
            source, candidate, output_guard=_runtime_guard)
        self.assertEqual(result.receipt.reason, "protected-anchor-removed")
        self.assertEqual(result.text, source)

    def test_explicit_correction_can_abandon_only_its_old_anchor(self):
        result = recover_cleanup_proof(
            "Ship Tuesday actually Wednesday with API v2",
            "Ship Wednesday with API v2.", output_guard=_runtime_guard)
        self.assertEqual(result.receipt.disposition, "recovered")
        self.assertEqual(result.receipt.anchor_count, 5)
        self.assertEqual(result.receipt.abandoned_anchor_count, 1)

    def test_output_guard_is_mandatory_and_runs_before_eligibility(self):
        missing = recover_cleanup_proof(
            "hello", "Hello.", output_guard=None)
        blocked = recover_cleanup_proof(
            "hello", "Hello.", output_guard=lambda *_args: "blocked")
        errored = recover_cleanup_proof(
            "hello", "Hello.",
            output_guard=lambda *_args: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(missing.receipt.reason, "missing-required-evidence")
        self.assertFalse(missing.receipt.output_guard_passed)
        self.assertEqual(blocked.receipt.reason, "output-guard-rejected")
        self.assertFalse(blocked.receipt.output_guard_passed)
        self.assertEqual(errored.receipt.reason, "output-guard-error")
        self.assertFalse(errored.receipt.output_guard_passed)

    def test_content_free_receipt_does_not_echo_or_hash_text(self):
        source = "um ship ProjectCerulean tomorrow"
        candidate = "Ship ProjectCerulean tomorrow."
        result = recover_cleanup_proof(
            source, candidate, output_guard=_runtime_guard)
        encoded = json.dumps(result.receipt.__dict__)
        self.assertNotIn("ProjectCerulean", encoded)
        self.assertNotIn("ship", encoded.casefold())
        self.assertNotIn("digest", encoded)
        self.assertTrue(result.receipt.replay_verified)

    def test_replay_rejects_tampered_or_overlapping_edits(self):
        source = "abcdef"
        self.assertIsNone(replay_edits(source, (
            RecoveredEdit(1, 3, "wrong", "x"),
        )))
        self.assertIsNone(replay_edits(source, (
            RecoveredEdit(1, 4, "bcd", "x"),
            RecoveredEdit(3, 5, "de", "y"),
        )))


if __name__ == "__main__":
    unittest.main()
