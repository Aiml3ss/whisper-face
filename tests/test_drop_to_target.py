import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drop_to_target import (  # noqa: E402
    DecisionState,
    DropDecisionError,
    DropProposal,
    DropTargetFact,
    EVIDENCE_SCOPE,
    decide_drop_to_target,
    measure_synthetic_corpus,
)


def target(
    target_id,
    title,
    *,
    label="",
    kinds=None,
    effects=None,
    visible=True,
    enabled=True,
    drop_enabled=True,
):
    return {
        "schema_version": 1,
        "target_id": target_id,
        "title": title,
        "label": label,
        "accepted_kinds": kinds or ["file_reference"],
        "accepted_effects": effects or ["copy"],
        "visible": visible,
        "enabled": enabled,
        "drop_enabled": drop_enabled,
    }


def proposal(hint, *, kind="file_reference", effect="copy"):
    return {
        "schema_version": 1,
        "target_hint": hint,
        "source_kind": kind,
        "effect": effect,
    }


class DropToTargetTests(unittest.TestCase):
    def test_exact_normalized_and_token_names_resolve_deterministically(self):
        targets = [
            target("team-inbox", "Team Inbox"),
            target("cold-archive", "Cold Archive", effects=["move"]),
        ]

        exact = decide_drop_to_target(proposal("Team Inbox"), targets)
        normalized = decide_drop_to_target(proposal("team-inbox"), targets)
        token = decide_drop_to_target(proposal("inbox"), targets)

        for result in (exact, normalized, token):
            self.assertEqual(
                (result.state, result.target_id),
                (DecisionState.RESOLVED, "team-inbox"),
            )
        self.assertIn("exact_name", exact.receipt.evidence)
        self.assertIn("normalized_name", normalized.receipt.evidence)
        self.assertIn("token_name", token.receipt.evidence)

    def test_source_and_effect_conflicts_fail_closed(self):
        targets = [
            target(
                "archive", "Archive", kinds=["file_reference"], effects=["move"]
            )
        ]

        wrong_kind = decide_drop_to_target(
            proposal("Archive", kind="image_reference", effect="move"), targets
        )
        wrong_effect = decide_drop_to_target(proposal("Archive"), targets)

        for result in (wrong_kind, wrong_effect):
            self.assertEqual(result.state, DecisionState.UNAVAILABLE)
            self.assertIsNone(result.target_id)
            self.assertGreater(result.receipt.contradiction_count, 0)
            self.assertIn("constraint_conflict", result.receipt.evidence)

    def test_visibility_enablement_and_drop_capability_are_hard_gates(self):
        blocked = [
            target("hidden", "Hidden", visible=False),
            target("disabled", "Disabled", enabled=False),
            target("no-drop", "No Drop", drop_enabled=False),
        ]

        for snapshot in blocked:
            with self.subTest(target_id=snapshot["target_id"]):
                result = decide_drop_to_target(
                    proposal(snapshot["title"]), blocked
                )
                self.assertEqual(result.state, DecisionState.UNAVAILABLE)
                self.assertIsNone(result.target_id)

    def test_equal_candidates_and_weak_evidence_never_choose_a_target(self):
        duplicate = [
            target("lane-a", "Review Lane"),
            target("lane-b", "Review Lane"),
        ]
        ambiguous = decide_drop_to_target(proposal("Review Lane"), duplicate)
        weak = decide_drop_to_target(
            proposal("put quarterly report in the team destination"),
            [target("team", "Team Inbox")],
        )

        self.assertEqual(ambiguous.state, DecisionState.AMBIGUOUS)
        self.assertIsNone(ambiguous.target_id)
        self.assertEqual(ambiguous.receipt.margin_bucket, "none")
        self.assertEqual(weak.state, DecisionState.UNAVAILABLE)
        self.assertIsNone(weak.target_id)
        self.assertEqual(weak.receipt.confidence_bucket, "below_threshold")

    def test_stronger_contradictory_target_blocks_a_weaker_compatible_match(self):
        targets = [
            target(
                "archive", "Archive", kinds=["file_reference"], effects=["move"]
            ),
            target("archive-copy", "Archive Copy Destination"),
        ]

        result = decide_drop_to_target(proposal("Archive"), targets)

        self.assertEqual(result.state, DecisionState.UNAVAILABLE)
        self.assertIsNone(result.target_id)
        self.assertGreater(result.receipt.contradiction_count, 0)

    def test_nearby_contradictory_target_counts_against_resolution_margin(self):
        targets = [
            target("archive-copy", "Archive Copy"),
            target(
                "archive-copy-disabled", "Archive-Copy",
                effects=["move"],
            ),
        ]

        result = decide_drop_to_target(proposal("Archive Copy"), targets)

        self.assertEqual(result.state, DecisionState.UNAVAILABLE)
        self.assertIsNone(result.target_id)
        self.assertIn("constraint_conflict", result.receipt.evidence)
        self.assertEqual(result.receipt.margin_bucket, "narrow")

    def test_closed_schemas_reject_content_paths_and_action_surfaces(self):
        safe_target = target("safe", "Safe")
        safe_proposal = proposal("Safe")
        for prohibited in (
            "payload", "data", "file_path", "selected_text", "callback", "drop",
            "clipboard", "coordinates",
        ):
            with self.subTest(prohibited=prohibited):
                with self.assertRaisesRegex(DropDecisionError, "proposal schema"):
                    DropProposal.from_mapping({**safe_proposal, prohibited: "private"})
                with self.assertRaisesRegex(DropDecisionError, "target fact schema"):
                    DropTargetFact.from_mapping({**safe_target, prohibited: "private"})

        parsed = DropTargetFact.from_mapping(safe_target)
        self.assertFalse(hasattr(parsed, "drop"))
        self.assertFalse(hasattr(parsed, "write"))
        self.assertFalse(hasattr(parsed, "callback"))

    def test_receipt_is_content_free_and_has_no_target_identity(self):
        hint = "Secret Project Destination"
        result = decide_drop_to_target(
            proposal(hint), [target("opaque-target-777", hint)]
        )
        receipt = result.receipt.to_mapping()
        serialized = json.dumps(receipt, sort_keys=True)

        self.assertEqual(result.target_id, "opaque-target-777")
        self.assertEqual(set(receipt), {
            "schema_version", "state", "observed_targets", "eligible_targets",
            "contradiction_count", "evidence", "confidence_bucket",
            "margin_bucket",
        })
        self.assertNotIn(hint.casefold(), serialized.casefold())
        self.assertNotIn("opaque-target-777", serialized)
        self.assertNotIn("file_reference", serialized)

    def test_inputs_are_bounded_and_target_ids_are_unique(self):
        parsed = DropTargetFact.from_mapping(target("safe", "Safe"))
        with self.assertRaisesRegex(DropDecisionError, "unique"):
            decide_drop_to_target(proposal("Safe"), [parsed, parsed])
        with self.assertRaisesRegex(DropDecisionError, "bounded"):
            DropProposal.from_mapping(proposal("x" * 129))

    def test_synthetic_corpus_is_deterministic_and_has_no_wrong_resolutions(self):
        corpus = json.loads(
            (ROOT / "benchmarks" / "drop_to_target_cases.json").read_text()
        )

        first = measure_synthetic_corpus(corpus)
        second = measure_synthetic_corpus(corpus)

        self.assertEqual(first, second)
        self.assertEqual(first, {
            "schema_version": 1,
            "evidence_scope": EVIDENCE_SCOPE,
            "physical_validation": False,
            "cases": 11,
            "resolved": 5,
            "ambiguous": 1,
            "unavailable": 5,
            "correct_outcomes": 11,
            "wrong_target_resolutions": 0,
        })

    def test_corpus_cannot_claim_physical_validation_or_accuracy(self):
        corpus = json.loads(
            (ROOT / "benchmarks" / "drop_to_target_cases.json").read_text()
        )

        with self.assertRaisesRegex(DropDecisionError, "corpus schema"):
            measure_synthetic_corpus({**corpus, "accuracy_claim": "perfect"})
        with self.assertRaisesRegex(DropDecisionError, "declaration"):
            measure_synthetic_corpus({**corpus, "physical_validation": True})


if __name__ == "__main__":
    unittest.main()
