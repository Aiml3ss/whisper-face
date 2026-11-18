import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ast
import json

from insertion_integrity import (
    POSITION_CHECKS,
    POSITION_COUNTS,
    READBACK_CONFLICT_SHAPES,
    READBACK_DIFFERENCES,
    READBACK_STAGES,
    SELECTION_DRIFT_DIRECTIONS,
    SELECTION_DRIFT_DISTANCES,
    SELECTION_DRIFT_SHAPES,
    SELECTION_DRIFT_STAGES,
    SELECTION_DRIFT_TEXT,
    USER_INPUT_EVENTS,
    USER_INPUT_SINCE,
    DestinationObservation,
    InsertionCoordinator,
    InsertionLease,
    InsertionReceipt,
    ReadbackResult,
    ReceiptReason,
    ReceiptState,
    fingerprint_surrounding,
    position_check,
    position_count,
    readback_difference,
    readback_stage,
    review_selection_drift,
    same_but_whitespace_form,
    SelectionDrift,
    user_input_since,
    utf16_units,
    whitespace_form,
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

    def test_validation_alone_gives_commits_answer_without_staging(self):
        coordinator = InsertionCoordinator()
        for current, expected in (
                (self.current, None),
                (DestinationObservation.capture(
                    "pid:99/field:1", (12, 0), "before|after"),
                 ReceiptReason.FOCUS_DRIFT),
                (DestinationObservation.capture(
                    "pid:42/field:7", (13, 0), "before|after"),
                 ReceiptReason.SELECTION_DRIFT),
                (DestinationObservation.capture(None, None, None),
                 ReceiptReason.TARGET_UNREADABLE)):
            with self.subTest(expected=expected):
                self.assertEqual(
                    coordinator.validate(self.lease, current), expected)
        self.assertEqual(coordinator.recoverable_count(), 0)
        coordinator.stage(self.lease, "Ship it")

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

    def test_blank_text_is_never_offered_for_recovery(self):
        drifted = DestinationObservation.capture(
            "pid:99/field:1", (12, 0), "before|after")
        for text, current, readback in (
                ("", drifted, ReadbackResult.verified),
                ("  \n", drifted, ReadbackResult.verified),
                ("", self.current, ReadbackResult.unverifiable),
                (" ", self.current, ReadbackResult.conflict)):
            with self.subTest(text=text, readback=readback.__name__):
                coordinator = self.staged(text)
                receipt = coordinator.commit(
                    "utterance-1", current, lambda _text: None, readback)

                self.assertNotEqual(receipt.state, ReceiptState.VERIFIED)
                self.assertEqual(coordinator.recoverable(), ())
                self.assertEqual(coordinator.recoverable_count(), 0)
                # The receipt survives for dedupe; the attempt is still once.
                self.assertEqual(coordinator.receipt("utterance-1"), receipt)
                self.assertIs(coordinator.commit(
                    "utterance-1", self.current, lambda _text: None,
                    ReadbackResult.verified), receipt)

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


class RecoveredDictationTests(unittest.TestCase):
    """What the delivery journal gives back at start-up is kept, not sent."""

    def test_a_recovered_dictation_is_recoverable_and_never_pasted(self):
        coordinator = InsertionCoordinator()
        receipt = coordinator.keep("take-1", "Ship it", paste_attempted=False)
        self.assertEqual(receipt.state, ReceiptState.UNRESOLVED)
        self.assertEqual(receipt.reason, ReceiptReason.INTERRUPTED)
        self.assertFalse(receipt.paste_attempted)
        self.assertFalse(receipt.pasted_unchecked)
        [item] = coordinator.recoverable()
        self.assertEqual(item.text, "Ship it")
        self.assertEqual(coordinator.needs_home_count(), 1)
        # Terminal from the start: a commit cannot paste it.
        pasted = []
        again = coordinator.commit(
            "take-1", DestinationObservation.capture("x", (0, 0), ""),
            pasted.append, ReadbackResult.verified)
        self.assertEqual(pasted, [])
        self.assertEqual(again.reason, ReceiptReason.INTERRUPTED)

    def test_keeping_a_known_id_twice_makes_no_second_copy(self):
        coordinator = InsertionCoordinator()
        coordinator.keep("take-1", "Ship it", paste_attempted=True)
        coordinator.keep("take-1", "Ship it", paste_attempted=True)
        self.assertEqual(coordinator.recoverable_count(), 1)
        self.assertTrue(coordinator.acknowledge("take-1"))
        coordinator.keep("take-1", "Ship it", paste_attempted=True)
        self.assertEqual(coordinator.recoverable_count(), 0)

    def test_blank_text_is_not_kept(self):
        coordinator = InsertionCoordinator()
        coordinator.keep("take-1", "   ", paste_attempted=False)
        self.assertEqual(coordinator.recoverable_count(), 0)


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

    def test_the_receipt_carries_the_shape_of_its_own_readback(self):
        lease = InsertionLease.capture(
            "utterance-1", "pid:42/field:7", (12, 0), "before|after")
        matching = DestinationObservation.capture(
            "pid:42/field:7", (12, 0), "before|after")
        drifted = DestinationObservation.capture(
            "pid:42/field:7", (3, 0), "before|after")
        for current, readback, shape in (
                (matching, lambda: ReadbackResult.conflict(
                    "observed-is-prefix"), "observed-is-prefix"),
                (matching, ReadbackResult.verified_edge_whitespace,
                 "trailing-whitespace"),
                (matching, ReadbackResult.verified, ""),
                (matching, ReadbackResult.unverifiable, ""),
                (drifted, lambda: ReadbackResult.conflict("divergent"), "")):
            with self.subTest(shape=shape, current=current.selection):
                coordinator = InsertionCoordinator()
                coordinator.stage(lease, "Ship it")
                receipt = coordinator.commit(
                    "utterance-1", current, lambda _text: None, readback)

                self.assertEqual(receipt.readback_shape, shape)
                self.assertEqual(
                    coordinator.receipt("utterance-1").readback_shape, shape)


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


class UncheckedPasteTests(unittest.TestCase):
    """A paste sent into a destination that hides its text is its own outcome.

    It is shown calmly and counted apart from text that still needs a home,
    so exactly which receipts qualify is the whole of the trust decision.
    """

    READABLE = ("pid:42/field:7", (12, 0), "before|after")
    OPAQUE = ("pid:42/window:3", (0, 0), "window-context")

    def outcome(self, name, utterance="utterance-1", *, opaque=False):
        """One committed utterance with a named outcome, on a fresh or
        shared coordinator: ``(receipt, pasted)``.

        ``opaque`` leases a destination that hid its text when the
        dictation began; otherwise the destination was read.
        """
        coordinator = getattr(self, "coordinator", None) \
            or InsertionCoordinator()
        home = self.OPAQUE if opaque else self.READABLE
        lease = (InsertionLease.capture_opaque(utterance, home[0], home[2])
                 if opaque else InsertionLease.capture(utterance, *home))
        current = DestinationObservation.capture(*home)
        readback = ReadbackResult.verified
        pasted = []
        paste = pasted.append
        if name == "no-readback":
            # Opaque: no readback is ever run. Readable: every read after
            # the paste answered nothing.
            readback = ReadbackResult.unverifiable
        elif name == "conflict":
            readback = lambda: ReadbackResult.conflict("divergent")
        elif name == "paste-raised":
            def paste(_text):
                raise RuntimeError("synthetic paste failure")
        elif name == "readback-raised":
            def readback():
                raise RuntimeError("synthetic readback failure")
        elif name == "focus-drift":
            current = DestinationObservation.capture(
                "pid:99/field:1", home[1], home[2])
        elif name == "selection-drift":
            current = DestinationObservation.capture(
                "pid:42/field:7", (3, 0), "before|after")
        elif name == "context-drift":
            current = DestinationObservation.capture(
                home[0], home[1], "something else is there now")
        elif name == "target-unreadable":
            current = DestinationObservation.capture(None, None, None)
        elif name != "verified":
            raise AssertionError(name)
        coordinator.stage(lease, "Ship it")
        receipt = coordinator.commit(utterance, current, paste, readback)
        return receipt, pasted

    def test_only_a_paste_sent_where_nothing_can_be_read_is_unchecked(self):
        receipt, pasted = self.outcome("no-readback", opaque=True)

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)
        self.assertEqual(receipt.reason, ReceiptReason.READBACK_UNAVAILABLE)
        self.assertTrue(receipt.destination_opaque)
        self.assertTrue(receipt.pasted_unchecked)

    def test_a_readable_field_that_answers_nothing_after_the_paste_is_not(
            self):
        # The destination was read when the dictation began and at release,
        # and then every read after the paste came back empty. That is a
        # change in an app the runtime normally reads, not an app that
        # hides its text: the person is told to look, as before.
        receipt, pasted = self.outcome("no-readback")

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)
        self.assertEqual(receipt.reason, ReceiptReason.READBACK_UNAVAILABLE)
        self.assertTrue(receipt.paste_attempted)
        self.assertFalse(receipt.destination_opaque)
        self.assertFalse(receipt.pasted_unchecked)
        # The same receipt on an opaque lease is the calm one, so the lease
        # is the only thing that tells the two apart.
        calm, _pasted = self.outcome("no-readback", opaque=True)
        self.assertEqual(
            (calm.state, calm.reason, calm.paste_attempted),
            (receipt.state, receipt.reason, receipt.paste_attempted))
        self.assertTrue(calm.pasted_unchecked)

    def test_every_real_failure_and_every_proof_is_something_else(self):
        expected = {
            # (outcome, opaque lease): (state, paste attempted)
            ("verified", False): (ReceiptState.VERIFIED, True),
            ("no-readback", False): (ReceiptState.UNVERIFIABLE, True),
            ("conflict", False): (ReceiptState.CONFLICT, True),
            ("paste-raised", False): (ReceiptState.UNRESOLVED, True),
            ("readback-raised", False): (ReceiptState.UNRESOLVED, True),
            ("focus-drift", False): (ReceiptState.CONFLICT, False),
            ("selection-drift", False): (ReceiptState.CONFLICT, False),
            ("context-drift", False): (ReceiptState.CONFLICT, False),
            ("target-unreadable", False): (ReceiptState.UNVERIFIABLE, False),
            # An opaque destination is calm for one outcome only. Every
            # other thing that can happen there is still a problem.
            ("conflict", True): (ReceiptState.CONFLICT, True),
            ("paste-raised", True): (ReceiptState.UNRESOLVED, True),
            ("readback-raised", True): (ReceiptState.UNRESOLVED, True),
            ("focus-drift", True): (ReceiptState.CONFLICT, False),
            ("context-drift", True): (ReceiptState.CONFLICT, False),
            ("target-unreadable", True): (ReceiptState.UNVERIFIABLE, False),
        }
        for (name, opaque), (state, attempted) in expected.items():
            with self.subTest(outcome=name, opaque=opaque):
                receipt, _pasted = self.outcome(name, opaque=opaque)
                self.assertEqual(receipt.state, state)
                self.assertEqual(receipt.paste_attempted, attempted)
                self.assertEqual(receipt.destination_opaque, opaque)
                self.assertFalse(receipt.pasted_unchecked)

    def test_a_receipt_that_does_not_say_is_never_taken_for_unchecked(self):
        from insertion_integrity import InsertionReceipt

        bare = InsertionReceipt(
            "utterance-1", ReceiptState.UNVERIFIABLE,
            ReceiptReason.READBACK_UNAVAILABLE, True)
        self.assertFalse(bare.destination_opaque)
        self.assertFalse(bare.pasted_unchecked)
        # Staged and waiting, an opaque lease's receipt already says what
        # it is, and is still not a paste of any kind.
        coordinator = InsertionCoordinator()
        staged = coordinator.stage(InsertionLease.capture_opaque(
            "utterance-2", self.OPAQUE[0], self.OPAQUE[2]), "Ship it")
        self.assertTrue(staged.destination_opaque)
        self.assertFalse(staged.pasted_unchecked)

    def test_unchecked_is_never_verified(self):
        receipt, _pasted = self.outcome("no-readback", opaque=True)
        self.assertNotEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertNotIn(receipt.reason, (
            ReceiptReason.COMMIT_VERIFIED,
            ReceiptReason.COMMIT_VERIFIED_EDGE_WHITESPACE))

    def test_unchecked_copies_are_kept_and_counted_apart(self):
        self.coordinator = InsertionCoordinator()
        for index in range(5):
            self.outcome("no-readback", f"calm-{index}", opaque=True)
        # Kept exactly like any other entry that was not proven delivered.
        self.assertEqual(self.coordinator.recoverable_count(), 5)
        self.assertEqual(
            [item.text for item in self.coordinator.recoverable()],
            ["Ship it"] * 5)
        self.assertEqual(self.coordinator.needs_home_count(), 0)

        problems = (
            ("no-readback", False), ("conflict", False),
            ("paste-raised", False), ("readback-raised", False),
            ("focus-drift", False), ("selection-drift", False),
            ("target-unreadable", False), ("conflict", True),
            ("paste-raised", True), ("focus-drift", True),
            ("target-unreadable", True))
        for index, (name, opaque) in enumerate(problems, start=1):
            self.outcome(name, f"problem-{index}", opaque=opaque)
            self.assertEqual(self.coordinator.needs_home_count(), index)
        self.assertEqual(
            self.coordinator.recoverable_count(), 5 + len(problems))
        # A verified paste is in neither count.
        self.outcome("verified", "verified-1")
        self.assertEqual(
            self.coordinator.recoverable_count(), 5 + len(problems))
        self.assertEqual(
            self.coordinator.needs_home_count(), len(problems))

    def test_the_count_ignores_in_flight_and_dismissed_entries(self):
        self.coordinator = InsertionCoordinator()
        seen = []
        lease = InsertionLease.capture("in-flight", *self.READABLE)
        self.coordinator.stage(lease, "Ship it")

        def readback():
            # The entry is terminal and still in flight here.
            seen.append(self.coordinator.needs_home_count())
            return ReadbackResult.conflict("divergent")

        self.coordinator.commit(
            "in-flight", DestinationObservation.capture(*self.READABLE),
            lambda _text: None, readback)
        self.assertEqual(seen, [0])
        self.assertEqual(self.coordinator.needs_home_count(), 1)
        self.assertTrue(self.coordinator.acknowledge("in-flight"))
        self.assertEqual(self.coordinator.needs_home_count(), 0)


class WhitespaceFormTests(unittest.TestCase):
    """Only what is the same text by construction may verify."""

    def test_no_break_spaces_and_line_break_forms_are_the_same_text(self):
        for observed, expected in (
                ("Meet\u00a0me there", "Meet me there"),
                ("Meet me there", "Meet\u00a0me there"),
                ("one\r\ntwo", "one\ntwo"),
                ("one\rtwo", "one\ntwo"),
                ("one\u2028two", "one\ntwo"),
                ("one\u2029two", "one\ntwo"),
                ("one\x85two", "one\ntwo"),
                # Forms and one trailing line break together.
                ("Hi\u00a0there\n", "Hi there"),
                ("Hi there", "Hi\u00a0there\r\n")):
            with self.subTest(observed=observed):
                self.assertTrue(same_but_whitespace_form(observed, expected))

    def test_anything_else_is_not(self):
        for observed, expected in (
                ("Meet  me there", "Meet me there"),     # a doubled space
                ("Meetme there", "Meet me there"),       # a dropped space
                ("Meet\tme there", "Meet me there"),     # a tab
                ("Meet\u2009me there", "Meet me there"), # a thin space
                ("Meet\u202fme there", "Meet me there"), # narrow no-break
                ("one\n\ntwo", "one\ntwo"),             # an extra line
                ("one\vtwo", "one\ntwo"),
                ("Meet me ther", "Meet me there"),       # truncated
                ("Meet me there!", "Meet me there"),     # something added
                ("me Meet there", "Meet me there"),      # reordered
                ("\u00a0\n", " \n"),                     # all whitespace
                (None, "Meet me there")):
            with self.subTest(observed=observed):
                self.assertFalse(same_but_whitespace_form(observed, expected))

    def test_the_folding_touches_nothing_but_those_forms(self):
        self.assertEqual(
            whitespace_form("a\u00a0b\r\nc\rd\u2028e\u2029f\x85g\th"),
            "a b\nc\nd\ne\nf\ng\th")

    def test_it_verifies_with_its_own_reason_and_kind(self):
        result = ReadbackResult.verified_whitespace_form()
        self.assertEqual(result.state, ReceiptState.VERIFIED)
        self.assertEqual(
            result.reason, ReceiptReason.COMMIT_VERIFIED_WHITESPACE_FORM)
        self.assertEqual(result.difference, "whitespace-form")
        self.assertEqual(
            ReadbackResult.verified_edge_whitespace().difference,
            "edge-whitespace")
        with self.assertRaises(ValueError):
            ReadbackResult.conflict("divergent", difference="whitespace-form")
        with self.assertRaises(ValueError):
            ReadbackResult.conflict("divergent", difference="edge-whitespace")


class ReadbackDifferenceTests(unittest.TestCase):
    """A conflict says how it differed from the paste, never what it read."""

    BEFORE = "Hi. Bye."
    AT = (4, 0)
    PASTE = "Meet me at the station. "

    def kind(self, observed, before=None, selection=None, inserted=None):
        return readback_difference(
            self.BEFORE if before is None else before,
            self.AT if selection is None else selection,
            self.PASTE if inserted is None else inserted, observed)

    def test_each_kind_is_told_apart(self):
        expected = "Hi. Meet me at the station. Bye."
        for observed, kind in (
                ("", "field-empty"),
                ("  \n", "field-empty"),
                (self.BEFORE, "unchanged"),
                ("Hi.  Meet me  at the station. Bye.", "spacing"),
                ("Hi. Meet me at the station.Bye.".replace(".B", ". B"),
                 "spacing"),
                (expected.replace("Meet", "\u201cMeet"),
                 "extra-at-place"),
                ("Hi. Meet me at the station. Bye.".replace(
                    "Hi.", "Hi\uff0e"), "typographic"),
                ("Hi. Meet me at the Bye.", "leading-part"),
                ("Hi. Meet me at the station. \U0001f44d Bye.",
                 "extra-at-place"),
                ("Meet me at the station.", "inserted-alone"),
                ("Hi. Bye.Meet me at the station. ", "elsewhere"),
                ("Hi. Bye. Something else", "absent"),
                ("totally other", "absent")):
            with self.subTest(observed=observed, kind=kind):
                got = self.kind(observed)
                self.assertEqual(got, kind)
                self.assertIn(got, READBACK_DIFFERENCES)

    def test_typographic_substitutions_are_named(self):
        for observed, inserted in (
                ("Hi. It\u2019s here Bye.", "It's here "),
                ("Hi. \u201cyes\u201d Bye.", '"yes" '),
                ("Hi. a \u2014 b Bye.", "a - b "),
                ("Hi. wait\u2026 Bye.", "wait... ")):
            with self.subTest(observed=observed):
                self.assertEqual(
                    self.kind(observed, inserted=inserted), "typographic")

    def test_a_replaced_selection_is_measured_against_the_replacement(self):
        # "old" was selected and replaced by the paste.
        before, at = "Hi old bye", (3, 3)
        self.assertEqual(readback_difference(
            before, at, "new", "Hi ne bye"), "leading-part")
        self.assertEqual(readback_difference(
            before, at, "new", "Hi newold bye"), "extra-at-place")
        self.assertEqual(readback_difference(
            before, at, "new", "Hi  bye"), "absent")

    def test_every_conflict_kind_is_one_the_function_gives(self):
        source = (Path(__file__).resolve().parents[1]
                  / "insertion_integrity.py").read_text(encoding="utf-8")
        function = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "readback_difference")
        given = {
            node.value.value for node in ast.walk(function)
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)}
        # The two proofs are named by the results that verify; every other
        # label is a conflict the comparison can reach.
        self.assertEqual(
            given | {"edge-whitespace", "whitespace-form"},
            READBACK_DIFFERENCES)
        self.assertEqual(READBACK_STAGES, {
            "no-change-seen", "still-changing", "settled"})

    def test_the_stage_says_how_the_field_behaved(self):
        self.assertEqual(readback_stage(False, False), "no-change-seen")
        self.assertEqual(readback_stage(False, True), "no-change-seen")
        self.assertEqual(readback_stage(True, True), "still-changing")
        self.assertEqual(readback_stage(True, False), "settled")

    def test_nothing_read_or_pasted_reaches_the_answer(self):
        secret = "hunter2 Sam Rivera 8pm"
        for observed in (secret, secret[:5], "x" + secret, "", secret * 3):
            for inserted in (secret, "a", secret + " "):
                got = self.kind(observed, before=secret,
                                selection=(len(secret), 0),
                                inserted=inserted)
                self.assertIn(got, READBACK_DIFFERENCES)
                self.assertNotIn("hunter2", got)

    def test_categories_come_from_closed_sets(self):
        with self.assertRaises(ValueError):
            ReadbackResult(ReceiptState.CONFLICT,
                           ReceiptReason.READBACK_CONFLICT, "divergent",
                           difference="hunter2")
        with self.assertRaises(ValueError):
            ReadbackResult.conflict("divergent", stage="12 characters")
        with self.assertRaises(ValueError):
            ReadbackResult.unverifiable("moved-by-7")
        with self.assertRaises(ValueError):
            ReadbackResult.unverifiable("did-not-move", "count-9")
        for kind in READBACK_DIFFERENCES - {
                "edge-whitespace", "whitespace-form"}:
            for stage in READBACK_STAGES:
                result = ReadbackResult.conflict(
                    "divergent", difference=kind, stage=stage)
                self.assertEqual(result.state, ReceiptState.CONFLICT)


class PositionCheckTests(unittest.TestCase):
    """A field that hides its text is checked by where its cursor went."""

    def test_only_an_exact_move_with_the_selection_replaced_counts(self):
        for before, after, units, check in (
                ((4, 0), (9, 0), 5, "moved-exactly"),
                # The old selection replaced: the cursor lands past its
                # start, not past its end.
                ((4, 3), (9, 0), 5, "moved-exactly"),
                ((4, 3), (12, 0), 5, "moved-other"),
                # Truncated, too far, or left as a selection.
                ((4, 0), (8, 0), 5, "moved-other"),
                ((4, 0), (10, 0), 5, "moved-other"),
                ((4, 0), (4, 5), 5, "moved-other"),
                # Somewhere else entirely.
                ((4, 0), (0, 0), 5, "moved-other"),
                ((4, 0), (4, 0), 5, "did-not-move"),
                ((4, 3), (4, 3), 5, "did-not-move"),
                ((4, 0), None, 5, "unreadable-after"),
                # Nothing to measure.
                ((4, 0), (4, 0), 0, "did-not-move")):
            with self.subTest(before=before, after=after, units=units):
                got = position_check(before, after, units)
                self.assertEqual(got, check)
                self.assertIn(got, POSITION_CHECKS)

    def test_lengths_are_counted_as_accessibility_counts_them(self):
        # Outside the Basic Multilingual Plane a character is two units.
        self.assertEqual(utf16_units("ok"), 2)
        self.assertEqual(utf16_units("ok \U0001f44d"), 5)
        self.assertEqual(utf16_units("\U0001f468\u200d\U0001f469"), 5)
        self.assertEqual(
            position_check((0, 0), (5, 0), utf16_units("ok \U0001f44d")),
            "moved-exactly")
        # Counting code points would be one short: no proof.
        self.assertEqual(
            position_check((0, 0), (4, 0), utf16_units("ok \U0001f44d")),
            "moved-other")

    def test_a_count_agrees_only_with_exactly_what_the_paste_adds(self):
        for before, after, selected, units, count in (
                (10, 15, 0, 5, "agrees"),
                (10, 12, 3, 5, "agrees"),
                (10, 15, 3, 5, "disagrees"),
                (10, 14, 0, 5, "disagrees"),
                (10, 10, 0, 5, "disagrees"),
                (None, None, 0, 5, "not-offered"),
                (10, None, 0, 5, "disagrees"),
                (None, 15, 0, 5, "disagrees")):
            with self.subTest(before=before, after=after):
                got = position_count(before, after, selected, units)
                self.assertEqual(got, count)
                self.assertIn(got, POSITION_COUNTS)

    def test_proof_needs_an_exact_move_and_no_disagreeing_count(self):
        for count in ("agrees", "not-offered"):
            result = ReadbackResult.verified_by_position(count)
            self.assertEqual(result.state, ReceiptState.VERIFIED)
            self.assertEqual(
                result.reason, ReceiptReason.COMMIT_VERIFIED_POSITION)
            self.assertEqual(result.position, "moved-exactly")
        with self.assertRaises(ValueError):
            ReadbackResult.verified_by_position("disagrees")
        # Anything that is not proof is the unverifiable result it was.
        for check in POSITION_CHECKS:
            for count in POSITION_COUNTS:
                if check == "moved-exactly" and count != "disagrees":
                    with self.assertRaises(ValueError):
                        ReadbackResult.unverifiable(check, count)
                    continue
                result = ReadbackResult.unverifiable(check, count)
                self.assertEqual(result.state, ReceiptState.UNVERIFIABLE)
                self.assertEqual(
                    result.reason, ReceiptReason.READBACK_UNAVAILABLE)

    def commit(self, readback, coordinator=None):
        coordinator = coordinator or InsertionCoordinator()
        lease = InsertionLease.capture_opaque(
            "utterance-1", "pid:42/field:7", "Messages")
        coordinator.stage(lease, "Ship it")
        receipt = coordinator.commit(
            "utterance-1",
            DestinationObservation.capture("pid:42/field:7", (0, 0),
                                           "Messages"),
            lambda _text: None, readback)
        return coordinator, receipt

    def test_a_paste_checked_by_position_is_kept_like_any_checked_paste(self):
        coordinator, receipt = self.commit(
            lambda: ReadbackResult.verified_by_position("not-offered"))

        self.assertTrue(receipt.checked_by_position)
        self.assertFalse(receipt.pasted_unchecked)
        self.assertEqual(receipt.position_check, "moved-exactly")
        self.assertEqual(receipt.position_count, "not-offered")
        # Nothing kept for it, nothing waiting for a home.
        self.assertEqual(coordinator.recoverable(), ())
        self.assertEqual(coordinator.recoverable_counts(), (0, 0))

    def test_anything_short_of_proof_is_an_unchecked_paste_as_before(self):
        for check, count in (
                ("did-not-move", "not-offered"),
                ("moved-other", "agrees"),
                ("unreadable-after", "not-offered"),
                ("moved-exactly", "disagrees")):
            with self.subTest(check=check, count=count):
                coordinator, receipt = self.commit(
                    lambda: ReadbackResult.unverifiable(check, count))
                self.assertFalse(receipt.checked_by_position)
                self.assertTrue(receipt.pasted_unchecked)
                self.assertEqual(receipt.state, ReceiptState.UNVERIFIABLE)
                self.assertEqual(
                    receipt.reason, ReceiptReason.READBACK_UNAVAILABLE)
                self.assertEqual(receipt.position_check, check)
                self.assertEqual(
                    [item.text for item in coordinator.recoverable()],
                    ["Ship it"])
                self.assertEqual(coordinator.needs_home_count(), 0)

    def test_only_its_own_reason_is_checked_by_position(self):
        for result in (ReadbackResult.verified(),
                       ReadbackResult.verified_edge_whitespace(),
                       ReadbackResult.verified_whitespace_form(),
                       ReadbackResult.unverifiable(),
                       ReadbackResult.conflict("divergent")):
            receipt = InsertionReceipt(
                "u", result.state, result.reason, True)
            self.assertFalse(receipt.checked_by_position)

    def test_a_log_reason_carries_categories_and_nothing_else(self):
        _coordinator, receipt = self.commit(
            lambda: ReadbackResult.unverifiable("did-not-move", "agrees"))
        self.assertEqual(
            receipt.log_reason,
            "readback_unavailable; position did-not-move; count agrees")
        lease = InsertionLease.capture(
            "u2", "pid:42/field:7", (12, 0), "before|after")
        coordinator = InsertionCoordinator()
        coordinator.stage(lease, "hunter2")
        conflict = coordinator.commit(
            "u2", DestinationObservation.capture(
                "pid:42/field:7", (12, 0), "before|after"),
            lambda _text: None,
            lambda: ReadbackResult.conflict(
                "divergent", difference="absent", stage="settled"))
        self.assertEqual(
            conflict.log_reason,
            "readback_conflict; difference absent; stage settled")
        self.assertEqual(InsertionReceipt(
            "u", ReceiptState.UNVERIFIABLE,
            ReceiptReason.READBACK_UNAVAILABLE, True).log_reason,
            "readback_unavailable")
        self.assertNotIn("hunter2", json.dumps(conflict.log_reason))


def signature(**changed):
    """An input signature in USER_INPUT_EVENTS order, every counter 10
    except the ones named (with ``-`` written ``_``)."""
    counts = {name: 10 for name in USER_INPUT_EVENTS}
    for name, value in changed.items():
        counts[name.replace("_", "-")] = value
    return ":".join(str(counts[name]) for name in USER_INPUT_EVENTS)


class SelectionDriftTests(unittest.TestCase):
    """A selection that moved since key-down: who moved it, and what then.

    The lease is "Hi.|" at the end of the field; at commit the same field
    reads with its caret somewhere else.
    """

    DESTINATION = "pid:42/field:7"

    def setUp(self):
        self.lease = InsertionLease.capture(
            "utterance-1", self.DESTINATION, (3, 0), "Hi.")

    def at(self, selection, nearby="Hi. ", destination=None):
        return DestinationObservation.capture(
            destination or self.DESTINATION, selection, nearby)

    def test_input_is_counted_by_kind_and_never_guessed(self):
        press = signature()
        for now, expected in (
                (signature(), "none"),
                # Moving the pointer, scrolling, or a button coming up that
                # went down before key-down cannot move a caret.
                (signature(moved=90, scroll=40, left_up=11), "none"),
                (signature(key_down=11), "keys"),
                (signature(left_down=11), "clicks"),
                (signature(right_down=11), "clicks"),
                (signature(other_down=11), "clicks"),
                (signature(left_dragged=12), "clicks"),
                (signature(key_down=11, left_down=11), "keys-and-clicks")):
            with self.subTest(expected=expected):
                self.assertEqual(user_input_since(press, now), expected)
        for before, after in (
                (None, press), (press, None), ("", press),
                ("1:2:3", "1:2:3"),                 # not this layout
                (press, press.replace("10", "x", 1)),
                (signature(key_down=11), signature())):   # went backwards
            with self.subTest(before=before, after=after):
                self.assertEqual(
                    user_input_since(before, after), "unavailable")

    def test_a_drift_nobody_caused_renews_the_lease_at_the_field_as_it_is(self):
        current = self.at((4, 0))
        staged, drift = review_selection_drift(
            self.lease, current, text_changed=True, input_since="none")

        self.assertEqual(drift, SelectionDrift(
            "forward", "1", "cursor-to-cursor", "changed", "none", "renewed"))
        self.assertEqual(staged.selection, (4, 0))
        self.assertEqual(staged.utterance_id, self.lease.utterance_id)
        self.assertEqual(staged.destination_id, self.lease.destination_id)
        self.assertFalse(staged.opaque)
        # The renewed lease is exactly what commit will now validate.
        self.assertIsNone(InsertionCoordinator.validate(staged, current))

    def test_any_input_or_none_countable_keeps_the_refusal(self):
        current = self.at((0, 4))
        for since in ("keys", "clicks", "keys-and-clicks", "unavailable"):
            with self.subTest(since=since):
                staged, drift = review_selection_drift(
                    self.lease, current, text_changed=False,
                    input_since=since)
                self.assertIs(staged, self.lease)
                self.assertEqual(drift.stage, "refused")
                self.assertEqual(drift.input, since)
                self.assertEqual(
                    (drift.direction, drift.distance, drift.selection),
                    ("backward", "3-8", "cursor-to-range"))
                self.assertEqual(
                    InsertionCoordinator.validate(staged, current),
                    ReceiptReason.SELECTION_DRIFT)

    def test_only_a_drift_on_the_same_readable_destination_is_reviewed(self):
        for current, reason in (
                (self.at((3, 0), nearby="Hi."), None),
                (self.at((4, 0), destination="pid:42/field:8"),
                 ReceiptReason.FOCUS_DRIFT),
                (self.at((3, 0), nearby="Hello."),
                 ReceiptReason.SURROUNDING_TEXT_DRIFT),
                (DestinationObservation.capture(None, None, None),
                 ReceiptReason.TARGET_UNREADABLE)):
            with self.subTest(reason=reason):
                staged, drift = review_selection_drift(
                    self.lease, current, text_changed=True,
                    input_since="none")
                self.assertIs(staged, self.lease)
                self.assertIsNone(drift)
                self.assertEqual(
                    InsertionCoordinator.validate(staged, current), reason)
        opaque = InsertionLease.capture_opaque(
            "utterance-2", self.DESTINATION, "Messages")
        staged, drift = review_selection_drift(
            opaque, self.at((4, 0)), text_changed=True, input_since="none")
        self.assertIs(staged, opaque)
        self.assertIsNone(drift)
        with self.assertRaises(ValueError):
            opaque.renewed_for(self.at((4, 0)))
        with self.assertRaises(ValueError):
            self.lease.renewed_for(self.at((4, 0), destination="other"))

    def test_how_far_and_which_way_are_buckets_never_positions(self):
        for selection, direction, distance, shape in (
                ((3, 2), "same-start", "0", "cursor-to-range"),
                ((2, 0), "backward", "1", "cursor-to-cursor"),
                ((5, 0), "forward", "2", "cursor-to-cursor"),
                ((11, 0), "forward", "3-8", "cursor-to-cursor"),
                ((12, 0), "forward", "9+", "cursor-to-cursor"),
                ((900, 3), "forward", "9+", "cursor-to-range")):
            with self.subTest(selection=selection):
                _staged, drift = review_selection_drift(
                    self.lease, self.at(selection), text_changed=None,
                    input_since="keys")
                self.assertEqual(drift.direction, direction)
                self.assertEqual(drift.distance, distance)
                self.assertEqual(drift.selection, shape)
                self.assertEqual(drift.text, "unknown")
                self.assertIn(drift.direction, SELECTION_DRIFT_DIRECTIONS)
                self.assertIn(drift.distance, SELECTION_DRIFT_DISTANCES)
                self.assertIn(drift.selection, SELECTION_DRIFT_SHAPES)
        ranged = InsertionLease.capture(
            "utterance-3", self.DESTINATION, (1, 2), "Hi.")
        _staged, drift = review_selection_drift(
            ranged, self.at((1, 0)), text_changed=False, input_since="none")
        self.assertEqual(drift.selection, "range-to-cursor")
        self.assertIn(drift.text, SELECTION_DRIFT_TEXT)
        self.assertIn(drift.stage, SELECTION_DRIFT_STAGES)
        self.assertIn(drift.input, USER_INPUT_SINCE)
        with self.assertRaises(ValueError):
            SelectionDrift("forward", "37", "cursor-to-cursor", "same",
                           "none", "renewed")
        with self.assertRaises(ValueError):
            review_selection_drift(self.lease, self.at((4, 0)),
                                   text_changed=True, input_since="maybe")

    def commit(self, current, since, text="Ship it", readback=None):
        coordinator = InsertionCoordinator()
        staged, drift = review_selection_drift(
            self.lease, current, text_changed=True, input_since=since)
        coordinator.stage(staged, text)
        pasted = []
        receipt = coordinator.commit(
            "utterance-1", current, pasted.append,
            readback or ReadbackResult.verified, drift=drift)
        return coordinator, receipt, pasted

    def test_a_renewed_lease_pastes_once_and_the_receipt_says_so(self):
        _coordinator, receipt, pasted = self.commit(self.at((4, 0)), "none")

        self.assertEqual(pasted, ["Ship it"])
        self.assertEqual(receipt.state, ReceiptState.VERIFIED)
        self.assertEqual(receipt.selection_drift.stage, "renewed")
        self.assertEqual(
            receipt.log_reason,
            "commit_verified; drift forward 1; selection cursor-to-cursor; "
            "text changed; input none; renewed")

    def test_a_renewed_lease_still_fails_a_short_or_misplaced_paste(self):
        for difference in ("leading-part", "elsewhere", "absent"):
            with self.subTest(difference=difference):
                coordinator, receipt, pasted = self.commit(
                    self.at((4, 0)), "none",
                    readback=lambda: ReadbackResult.conflict(
                        "divergent", difference=difference,
                        stage="settled"))
                self.assertEqual(pasted, ["Ship it"])
                self.assertEqual(receipt.state, ReceiptState.CONFLICT)
                self.assertEqual(
                    receipt.reason, ReceiptReason.READBACK_CONFLICT)
                self.assertEqual(receipt.selection_drift.stage, "renewed")
                self.assertEqual(coordinator.needs_home_count(), 1)

    def test_a_refused_drift_never_pastes_and_says_how_it_drifted(self):
        coordinator, receipt, pasted = self.commit(
            self.at((4, 0)), "keys", text="hunter2")

        self.assertEqual(pasted, [])
        self.assertEqual(receipt.reason, ReceiptReason.SELECTION_DRIFT)
        self.assertFalse(receipt.paste_attempted)
        self.assertEqual(
            receipt.log_reason,
            "selection_drift; drift forward 1; selection cursor-to-cursor; "
            "text changed; input keys; refused")
        self.assertEqual(coordinator.recoverable()[0].text, "hunter2")
        self.assertEqual(
            coordinator.recoverable()[0].receipt.selection_drift.stage,
            "refused")
        written = json.dumps([receipt.log_reason,
                              repr(receipt.selection_drift)])
        for word in ("hunter2", "Hi."):
            self.assertNotIn(word, written)


if __name__ == "__main__":
    unittest.main()
