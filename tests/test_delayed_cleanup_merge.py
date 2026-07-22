import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from delayed_cleanup_merge import (
    DelayedMergeReason,
    merge_delayed_cleanup,
)


class DelayedCleanupMergeTests(unittest.TestCase):
    def test_applies_cleanup_when_destination_is_unchanged(self):
        receipt = merge_delayed_cleanup(
            "We should uh ship Friday.",
            "We should ship Friday.",
            "We should uh ship Friday.",
        )

        self.assertEqual(receipt.merged_text, "We should ship Friday.")
        self.assertEqual(receipt.applied_count, 1)
        self.assertEqual(receipt.rejected_count, 0)
        self.assertTrue(receipt.changed)
        self.assertEqual(
            receipt.decisions[0].reason, DelayedMergeReason.APPLIED)

    def test_preserves_user_edit_elsewhere_and_maps_shifted_cleanup(self):
        receipt = merge_delayed_cleanup(
            "We should uh ship Friday.",
            "We should ship Friday.",
            "Honestly, we should uh ship Friday.",
        )

        self.assertEqual(
            receipt.merged_text, "Honestly, we should ship Friday.")
        self.assertEqual(receipt.applied_count, 1)

    def test_user_edit_to_proposed_span_wins(self):
        receipt = merge_delayed_cleanup(
            "We should uh ship Friday.",
            "We should ship Friday.",
            "We should definitely ship Friday.",
        )

        self.assertEqual(
            receipt.merged_text, "We should definitely ship Friday.")
        self.assertEqual(receipt.applied_count, 0)
        self.assertEqual(receipt.rejected_count, 1)
        self.assertEqual(
            receipt.decisions[0].reason,
            DelayedMergeReason.CURRENT_SPAN_TOUCHED,
        )

    def test_independent_proposal_edits_can_apply_around_user_edit(self):
        receipt = merge_delayed_cleanup(
            "Keep teh value and uh continue",
            "Keep the value and continue.",
            "Keep TEAM value and uh continue",
        )

        self.assertEqual(receipt.merged_text, "Keep TEAM value and continue.")
        self.assertEqual(receipt.applied_count, 2)
        # SequenceMatcher represents the transposition in ``teh`` as two
        # disjoint character edits.  Both are rejected because the user
        # replaced that source span; the other two cleanup edits still apply.
        self.assertEqual(receipt.rejected_count, 2)
        self.assertEqual(
            [decision.applied for decision in receipt.decisions],
            [False, False, True, True],
        )

    def test_user_insertion_at_proposal_boundary_is_not_overwritten(self):
        receipt = merge_delayed_cleanup("hello", "!hello", "Xhello")

        self.assertEqual(receipt.merged_text, "Xhello")
        self.assertEqual(
            receipt.decisions[0].reason,
            DelayedMergeReason.CURRENT_SPAN_TOUCHED,
        )

    def test_duplicate_destination_anchor_is_ambiguous(self):
        receipt = merge_delayed_cleanup(
            "left target right",
            "left TARGET right",
            "left target right / left target right",
        )

        self.assertEqual(
            receipt.merged_text, "left target right / left target right")
        self.assertEqual(
            receipt.decisions[0].reason,
            DelayedMergeReason.AMBIGUOUS_ANCHOR,
        )

    def test_reordered_destination_fails_closed(self):
        receipt = merge_delayed_cleanup(
            "AA start target end ZZ",
            "AA start TARGET end ZZ",
            "target end AA start ZZ",
        )

        self.assertEqual(receipt.merged_text, "target end AA start ZZ")
        self.assertEqual(
            receipt.decisions[0].reason,
            DelayedMergeReason.DESTINATION_REORDERED,
        )

    def test_full_replacement_has_no_safe_unchanged_boundary(self):
        receipt = merge_delayed_cleanup("abc", "XYZ", "abc")

        self.assertEqual(receipt.merged_text, "abc")
        self.assertEqual(
            receipt.decisions[0].reason,
            DelayedMergeReason.INSUFFICIENT_ANCHOR,
        )

    def test_noop_proposal_returns_current_user_text(self):
        receipt = merge_delayed_cleanup(
            "already clean", "already clean", "user changed it")

        self.assertEqual(receipt.merged_text, "user changed it")
        self.assertEqual(receipt.applied_count, 0)
        self.assertEqual(receipt.rejected_count, 0)
        self.assertEqual(receipt.decisions, ())

    def test_receipt_is_deterministic_and_contains_mapped_span(self):
        inputs = (
            "Please uh send this",
            "Please send this.",
            "Note: Please uh send this",
        )

        first = merge_delayed_cleanup(*inputs)
        second = merge_delayed_cleanup(*inputs)

        self.assertEqual(first, second)
        self.assertEqual(first.merged_text, "Note: Please send this.")
        self.assertEqual(
            [(decision.current_start, decision.current_end)
             for decision in first.decisions],
            [(12, 15), (25, 25)],
        )

    def test_requires_text_inputs(self):
        with self.assertRaisesRegex(TypeError, "must be strings"):
            merge_delayed_cleanup("raw", "clean", None)


if __name__ == "__main__":
    unittest.main()
