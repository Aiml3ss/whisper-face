import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from delayed_cleanup_merge import (
    DelayedApplyOutcome,
    DelayedCleanupTransactionAdapter,
    DelayedMergeReason,
    DestinationSnapshot,
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


class DelayedCleanupTransactionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = DelayedCleanupTransactionAdapter()
        self.snapshot = DestinationSnapshot(
            "window-1/field-1", "revision-7", "We should uh ship Friday.")

    def apply(self, *, proposal_id="proposal-1", snapshots=None, cas=None):
        snapshots = iter(snapshots or (self.snapshot, self.snapshot))
        return self.adapter.apply(
            proposal_id,
            "We should uh ship Friday.",
            "We should ship Friday.",
            lambda: next(snapshots),
            cas or (lambda _expected, _replacement: True),
        )

    def test_applies_only_through_revision_checked_callback(self):
        calls = []

        receipt = self.apply(cas=lambda expected, replacement: (
            calls.append((expected, replacement)) or True))

        self.assertEqual(
            receipt,
            receipt.__class__(DelayedApplyOutcome.APPLIED, True, 1, 0),
        )
        self.assertEqual(
            calls,
            [(self.snapshot, "We should ship Friday.")],
        )

    def test_completed_proposal_is_idempotent_without_callbacks(self):
        first = self.apply()

        second = self.adapter.apply(
            "proposal-1",
            "different original",
            "different proposal",
            lambda: self.fail("duplicate must not read"),
            lambda *_args: self.fail("duplicate must not apply"),
        )

        self.assertIs(second, first)

    def test_reentrant_duplicate_fails_closed_while_first_is_in_flight(self):
        duplicate_receipts = []
        reads = iter((self.snapshot, self.snapshot))

        def read():
            if not duplicate_receipts:
                duplicate_receipts.append(self.adapter.apply(
                    "proposal-1",
                    "raw",
                    "clean",
                    lambda: self.snapshot,
                    lambda *_args: self.fail("duplicate must not apply"),
                ))
            return next(reads)

        first = self.adapter.apply(
            "proposal-1",
            "We should uh ship Friday.",
            "We should ship Friday.",
            read,
            lambda _expected, _replacement: True,
        )

        self.assertTrue(first.applied)
        self.assertEqual(
            duplicate_receipts[0].outcome,
            DelayedApplyOutcome.PROPOSAL_IN_FLIGHT,
        )
        self.assertFalse(duplicate_receipts[0].applied)

    def test_unreadable_initial_or_recheck_fails_closed_before_apply(self):
        for index, snapshots in enumerate(
                ((None,), (self.snapshot, None))):
            with self.subTest(read=index + 1):
                adapter = DelayedCleanupTransactionAdapter()
                applied = []
                receipt = adapter.apply(
                    f"proposal-{index}",
                    "We should uh ship Friday.",
                    "We should ship Friday.",
                    iter(snapshots).__next__,
                    lambda *_args: applied.append(True) or True,
                )
                self.assertEqual(
                    receipt.outcome,
                    DelayedApplyOutcome.UNREADABLE_TARGET,
                )
                self.assertFalse(applied)

    def test_recheck_rejects_focus_revision_and_text_drift(self):
        changed = (
            (DestinationSnapshot(
                "window-2/field-1", "revision-7", self.snapshot.text),
             DelayedApplyOutcome.FOCUS_DRIFT),
            (DestinationSnapshot(
                self.snapshot.destination_id,
                self.snapshot.revision,
                self.snapshot.text,
                focused=False),
             DelayedApplyOutcome.FOCUS_DRIFT),
            (DestinationSnapshot(
                self.snapshot.destination_id, "revision-8", self.snapshot.text),
             DelayedApplyOutcome.REVISION_DRIFT),
            (DestinationSnapshot(
                self.snapshot.destination_id,
                self.snapshot.revision,
                "User changed this."),
             DelayedApplyOutcome.TEXT_DRIFT),
        )
        for index, (current, expected) in enumerate(changed):
            with self.subTest(outcome=expected):
                adapter = DelayedCleanupTransactionAdapter()
                applied = []
                receipt = adapter.apply(
                    f"proposal-{index}",
                    "We should uh ship Friday.",
                    "We should ship Friday.",
                    iter((self.snapshot, current)).__next__,
                    lambda *_args: applied.append(True) or True,
                )
                self.assertEqual(receipt.outcome, expected)
                self.assertFalse(applied)

    def test_ambiguous_merge_and_zero_safe_changes_never_apply(self):
        applied = []
        ambiguous = DestinationSnapshot(
            self.snapshot.destination_id,
            self.snapshot.revision,
            "left target right / left target right",
        )
        ambiguous_receipt = self.adapter.apply(
            "ambiguous",
            "left target right",
            "left TARGET right",
            lambda: ambiguous,
            lambda *_args: applied.append(True) or True,
        )
        noop_receipt = self.adapter.apply(
            "noop",
            "already clean",
            "already clean",
            lambda: DestinationSnapshot("target", "revision", "user text"),
            lambda *_args: applied.append(True) or True,
        )

        self.assertEqual(
            ambiguous_receipt.outcome, DelayedApplyOutcome.AMBIGUOUS_MERGE)
        self.assertEqual(
            noop_receipt.outcome, DelayedApplyOutcome.NO_SAFE_CHANGES)
        self.assertFalse(applied)

    def test_compare_and_swap_rejection_and_adapter_exceptions_are_fixed(self):
        rejected = self.apply(cas=lambda _expected, _replacement: False)
        read_error = DelayedCleanupTransactionAdapter().apply(
            "read-error",
            "raw",
            "clean",
            lambda: (_ for _ in ()).throw(RuntimeError("private details")),
            lambda *_args: True,
        )
        apply_error = DelayedCleanupTransactionAdapter().apply(
            "apply-error",
            "We should uh ship Friday.",
            "We should ship Friday.",
            lambda: self.snapshot,
            lambda *_args: (_ for _ in ()).throw(
                RuntimeError("private details")),
        )

        self.assertEqual(
            rejected.outcome,
            DelayedApplyOutcome.COMPARE_AND_SWAP_REJECTED,
        )
        self.assertEqual(
            read_error,
            read_error.__class__(DelayedApplyOutcome.ADAPTER_EXCEPTION, False),
        )
        self.assertEqual(
            apply_error.outcome, DelayedApplyOutcome.ADAPTER_EXCEPTION)
        self.assertFalse(apply_error.applied)


if __name__ == "__main__":
    unittest.main()
