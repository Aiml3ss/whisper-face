import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insertion_integrity import (
    READBACK_CONFLICT_SHAPES,
    DestinationObservation,
    InsertionCoordinator,
    InsertionLease,
    ReadbackResult,
    ReceiptReason,
    ReceiptState,
    fingerprint_surrounding,
)


class InsertionIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.lease = InsertionLease.capture(
            "utterance-1", "pid:42/field:7", (12, 0), "before|after")
        self.current = DestinationObservation.capture(
            "pid:42/field:7", (12, 0), "before|after")

    def staged(self, text="Ship it"):
        coordinator = InsertionCoordinator()
        coordinator.stage(self.lease, text)
        return coordinator

    def test_a_matching_destination_is_pasted_once_and_verified(self):
        pasted = []
        coordinator = self.staged()

        receipt = coordinator.commit(
            "utterance-1",
            self.current,
            pasted.append,
            lambda: ReadbackResult.verified(),
        )

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertTrue(receipt.paste_attempted)
        self.assertEqual(coordinator.recoverable(), ())

    def test_focus_drift_conflicts_without_attempting_a_paste(self):
        current = DestinationObservation.capture(
            "pid:99/field:1", (12, 0), "before|after")
        pasted = []
        coordinator = self.staged()

        receipt = coordinator.commit(
            "utterance-1", current, pasted.append,
            lambda: ReadbackResult.verified())

        self.assertEqual(pasted, [])
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertEqual(receipt.reason, ReceiptReason.FOCUS_DRIFT)
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(coordinator.recoverable_count(), 1)
        self.assertEqual(coordinator.recoverable()[0].text, "Ship it")

    def test_selection_drift_conflicts_without_attempting_a_paste(self):
        current = DestinationObservation.capture(
            "pid:42/field:7", (13, 0), "before|after")
        pasted = []
        coordinator = self.staged()

        receipt = coordinator.commit(
            "utterance-1", current, pasted.append,
            lambda: ReadbackResult.verified())

        self.assertEqual(pasted, [])
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertEqual(receipt.reason, ReceiptReason.SELECTION_DRIFT)

    def test_surrounding_text_drift_conflicts_without_storing_that_text(self):
        current = DestinationObservation.capture(
            "pid:42/field:7", (12, 0), "changed nearby text")
        pasted = []
        coordinator = self.staged()

        receipt = coordinator.commit(
            "utterance-1", current, pasted.append,
            lambda: ReadbackResult.verified())

        self.assertEqual(pasted, [])
        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertEqual(receipt.reason, ReceiptReason.SURROUNDING_TEXT_DRIFT)
        self.assertNotIn("before|after", repr(self.lease))
        self.assertNotIn("changed nearby text", repr(current))
        self.assertEqual(
            self.lease.surrounding_fingerprint,
            fingerprint_surrounding("before|after"),
        )

    def test_an_unreadable_target_is_unverifiable_and_never_pasted(self):
        current = DestinationObservation.capture(None, None, None)
        pasted = []
        coordinator = self.staged()

        receipt = coordinator.commit(
            "utterance-1", current, pasted.append,
            lambda: ReadbackResult.verified())

        self.assertEqual(pasted, [])
        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)
        self.assertEqual(receipt.reason, ReceiptReason.TARGET_UNREADABLE)
        self.assertFalse(receipt.paste_attempted)

    def test_duplicate_commit_callback_returns_the_same_receipt_without_repaste(self):
        pasted = []
        coordinator = self.staged()
        first = coordinator.commit(
            "utterance-1", self.current, pasted.append,
            lambda: ReadbackResult.verified())
        second = coordinator.commit(
            "utterance-1", self.current, pasted.append,
            lambda: ReadbackResult.conflict())

        self.assertIs(second, first)
        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(second.state, ReceiptState.VERIFIED)

    def test_an_ambiguous_paste_exception_stays_unresolved_and_never_retries(self):
        attempts = []
        coordinator = self.staged()

        def ambiguous_paste(text):
            attempts.append(text)
            raise RuntimeError("OS callback failed after delivery was possible")

        first = coordinator.commit(
            "utterance-1", self.current, ambiguous_paste,
            lambda: ReadbackResult.verified())
        second = coordinator.commit(
            "utterance-1", self.current, ambiguous_paste,
            lambda: ReadbackResult.verified())

        self.assertEqual(attempts, ["Ship it"])
        self.assertIs(second, first)
        self.assertEqual(first.state, ReceiptState.UNRESOLVED)
        self.assertEqual(first.reason, ReceiptReason.PASTE_OUTCOME_UNKNOWN)
        self.assertTrue(first.paste_attempted)
        recoverable = coordinator.recoverable()
        self.assertEqual(recoverable[0].lease, self.lease)
        self.assertEqual(recoverable[0].text, "Ship it")
        self.assertEqual(recoverable[0].receipt, first)

    def test_unavailable_readback_is_recoverable_but_not_retried(self):
        pasted = []
        coordinator = self.staged()
        receipt = coordinator.commit(
            "utterance-1", self.current, pasted.append,
            lambda: ReadbackResult.unverifiable())

        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)
        self.assertEqual(receipt.reason, ReceiptReason.READBACK_UNAVAILABLE)
        coordinator.commit(
            "utterance-1", self.current, pasted.append,
            lambda: ReadbackResult.verified())
        self.assertEqual(pasted, ["Ship it"])

    def test_conflicting_readback_is_recoverable_but_not_retried(self):
        pasted = []
        coordinator = self.staged()
        receipt = coordinator.commit(
            "utterance-1", self.current, pasted.append,
            lambda: ReadbackResult.conflict())

        self.assertEqual(receipt.state, ReceiptState.CONFLICT)
        self.assertEqual(receipt.reason, ReceiptReason.READBACK_CONFLICT)
        self.assertTrue(receipt.paste_attempted)
        self.assertEqual(coordinator.recoverable()[0].receipt, receipt)

    def test_an_ambiguous_readback_exception_stays_unresolved(self):
        pasted = []
        coordinator = self.staged()

        def failed_readback():
            raise RuntimeError("target became unreadable after paste")

        receipt = coordinator.commit(
            "utterance-1", self.current, pasted.append, failed_readback)

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.UNRESOLVED)
        self.assertEqual(receipt.reason, ReceiptReason.PASTE_OUTCOME_UNKNOWN)
        self.assertEqual(coordinator.recoverable()[0].receipt, receipt)

    def test_reentrant_duplicate_callback_cannot_paste_twice(self):
        pasted = []
        coordinator = self.staged()

        def paste(text):
            pasted.append(text)
            duplicate = coordinator.commit(
                "utterance-1", self.current, pasted.append,
                lambda: ReadbackResult.verified())
            self.assertEqual(duplicate.state, ReceiptState.UNRESOLVED)

        receipt = coordinator.commit(
            "utterance-1", self.current, paste,
            lambda: ReadbackResult.verified())

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)

    def test_concurrent_duplicate_callbacks_share_one_paste_attempt(self):
        pasted = []
        paste_started = threading.Event()
        allow_readback = threading.Event()
        coordinator = self.staged()
        receipts = []

        def paste(text):
            pasted.append(text)
            paste_started.set()
            self.assertTrue(allow_readback.wait(timeout=1))

        def commit():
            receipts.append(coordinator.commit(
                "utterance-1", self.current, paste,
                lambda: ReadbackResult.verified()))

        first = threading.Thread(target=commit)
        second = threading.Thread(target=commit)
        first.start()
        self.assertTrue(paste_started.wait(timeout=1))
        second.start()
        allow_readback.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(pasted, ["Ship it"])
        self.assertCountEqual(
            [receipt.state for receipt in receipts],
            [ReceiptState.VERIFIED, ReceiptState.UNRESOLVED],
        )
        final = coordinator.commit(
            "utterance-1", self.current, pasted.append,
            lambda: ReadbackResult.conflict())
        self.assertEqual(final.state, ReceiptState.VERIFIED)
        self.assertEqual(pasted, ["Ship it"])

    def test_verified_payload_is_erased_but_dedupe_receipt_remains(self):
        coordinator = self.staged("private dictated text")
        receipt = coordinator.commit(
            "utterance-1", self.current, lambda _text: None,
            lambda: ReadbackResult.verified())

        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(coordinator.recoverable(), ())
        self.assertNotIn("private dictated text", repr(coordinator._entries))
        self.assertEqual(
            coordinator.receipt("utterance-1").state, ReceiptState.VERIFIED)

    def test_in_flight_payload_is_not_recoverable_or_acknowledgeable(self):
        coordinator = self.staged("private dictated text")

        def paste(_text):
            self.assertEqual(coordinator.recoverable(), ())
            self.assertFalse(coordinator.acknowledge("utterance-1"))

        receipt = coordinator.commit(
            "utterance-1", self.current, paste,
            lambda: ReadbackResult.verified())

        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(coordinator.recoverable(), ())

    def test_recoverable_payload_can_be_acknowledged_and_erased(self):
        coordinator = self.staged("recover me")
        coordinator.commit(
            "utterance-1",
            DestinationObservation.capture("other", (12, 0), "before|after"),
            lambda _text: None,
            lambda: ReadbackResult.verified(),
        )

        self.assertTrue(coordinator.acknowledge("utterance-1"))
        self.assertFalse(coordinator.acknowledge("utterance-1"))
        self.assertEqual(coordinator.recoverable(), ())

    def test_opaque_lease_checks_destination_without_readable_text(self):
        lease = InsertionLease.capture_opaque(
            "opaque-1", "com.example:field", "Composer")
        matching = DestinationObservation.capture(
            "com.example:field", (0, 0), "Composer")
        coordinator = InsertionCoordinator()
        coordinator.stage(lease, "terminal command")
        pasted = []

        receipt = coordinator.commit(
            "opaque-1", matching, pasted.append,
            lambda: ReadbackResult.unverifiable())

        self.assertEqual(pasted, ["terminal command"])
        self.assertTrue(receipt.paste_attempted)
        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)


class ReadbackConflictShapeTests(unittest.TestCase):
    """A conflict has to say how it differed, without ever saying what."""

    def test_conflict_shape_must_come_from_the_closed_vocabulary(self):
        with self.assertRaises(ValueError):
            ReadbackResult.conflict("something-invented")

    def test_conflict_defaults_to_unclassified_for_older_callers(self):
        result = ReadbackResult.conflict()
        self.assertEqual(result.detail, "unclassified")
        self.assertEqual(result.state, ReceiptState.CONFLICT)

    def test_non_conflict_results_carry_no_detail(self):
        self.assertEqual(ReadbackResult.verified().detail, "")
        self.assertEqual(ReadbackResult.unverifiable().detail, "")

    def test_every_shape_is_constructible(self):
        for shape in READBACK_CONFLICT_SHAPES:
            with self.subTest(shape=shape):
                self.assertEqual(ReadbackResult.conflict(shape).detail, shape)


class EdgeWhitespaceVerificationTests(unittest.TestCase):
    """Edge whitespace proves delivery; nothing weaker may reach VERIFIED."""

    def test_edge_whitespace_is_verified_with_its_own_reason(self):
        result = ReadbackResult.verified_edge_whitespace()
        self.assertEqual(result.state, ReceiptState.VERIFIED)
        self.assertEqual(result.reason,
                         ReceiptReason.COMMIT_VERIFIED_EDGE_WHITESPACE)
        self.assertEqual(result.detail, "trailing-whitespace")

    def test_it_is_distinguishable_from_a_byte_exact_match(self):
        self.assertNotEqual(ReadbackResult.verified().reason,
                            ReadbackResult.verified_edge_whitespace().reason)
        self.assertEqual(ReadbackResult.verified().detail, "")

    def test_correction_learning_still_sees_a_verified_state(self):
        # Learning is gated on state, not reason: an app that only trims a
        # newline must not silently disable the Personal Prior.
        self.assertEqual(ReadbackResult.verified_edge_whitespace().state,
                         ReadbackResult.verified().state)



if __name__ == "__main__":
    unittest.main()
