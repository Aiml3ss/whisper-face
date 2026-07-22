# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACTION_ID = "act-00000000000000000000000000000001"
UNKNOWN_ID = "act-00000000000000000000000000000002"

from risky_action_confirmation import (  # noqa: E402
    ConfirmationReason,
    ConfirmationState,
    InertRiskyActionConfirmationRuntime,
    RiskClass,
    RiskyActionConfirmationGate,
    VoiceDecision,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class RiskyActionConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.gate = RiskyActionConfirmationGate(clock=self.clock)
        self.gate.propose(
            ACTION_ID, RiskClass.EXTERNAL_COMMUNICATION,
            window_seconds=10.0)

    def test_voice_then_click_is_the_only_confirmation_path(self):
        voice = self.gate.record_voice(
            ACTION_ID, VoiceDecision.CONFIRM)
        click = self.gate.click_confirm(ACTION_ID)

        self.assertEqual(voice.state, ConfirmationState.AWAITING_CLICK)
        self.assertEqual(click.state, ConfirmationState.CONFIRMED)
        self.assertEqual(
            click.reason, ConfirmationReason.TWO_FACTOR_CONFIRMED)

    def test_early_click_is_not_remembered_as_confirmation(self):
        early = self.gate.click_confirm(ACTION_ID)
        voice = self.gate.record_voice(
            ACTION_ID, VoiceDecision.CONFIRM)

        self.assertEqual(early.state, ConfirmationState.AWAITING_VOICE)
        self.assertEqual(
            early.reason, ConfirmationReason.CLICK_BEFORE_VOICE)
        self.assertEqual(voice.state, ConfirmationState.AWAITING_CLICK)
        self.assertEqual(
            self.gate.status(ACTION_ID).state,
            ConfirmationState.AWAITING_CLICK,
        )

    def test_expiry_is_fail_closed_and_terminal(self):
        self.gate.record_voice(ACTION_ID, VoiceDecision.CONFIRM)
        self.clock.now = 110.0

        expired = self.gate.click_confirm(ACTION_ID)

        self.assertEqual(expired.state, ConfirmationState.EXPIRED)
        self.assertEqual(
            expired.reason, ConfirmationReason.DEADLINE_EXPIRED)
        self.assertEqual(
            self.gate.status(ACTION_ID).reason,
            ConfirmationReason.DEADLINE_EXPIRED,
        )

    def test_voice_or_explicit_cancel_is_terminal_and_idempotent(self):
        cancelled = self.gate.record_voice(
            ACTION_ID, VoiceDecision.CANCEL)
        repeated = self.gate.cancel(ACTION_ID)

        self.assertEqual(cancelled.state, ConfirmationState.CANCELLED)
        self.assertEqual(repeated.state, ConfirmationState.CANCELLED)
        self.assertEqual(
            repeated.reason, ConfirmationReason.ALREADY_TERMINAL)

    def test_receipts_never_contain_action_payload_or_spoken_phrase(self):
        secret = "send the Project Bluebird budget to Ada"
        receipt = self.gate.record_voice(
            ACTION_ID, VoiceDecision.CONFIRM)
        serialized = json.dumps(asdict(receipt), default=str)

        self.assertNotIn(secret, serialized)
        self.assertEqual(set(asdict(receipt)), {
            "action_id", "risk", "state", "reason",
        })

    def test_terminal_proposals_can_be_forgotten_but_pending_cannot(self):
        with self.assertRaisesRegex(ValueError, "pending"):
            self.gate.forget(ACTION_ID)
        self.gate.cancel(ACTION_ID)
        self.gate.forget(ACTION_ID)
        with self.assertRaises(KeyError):
            self.gate.status(ACTION_ID)

    def test_rejects_duplicate_ids_invalid_windows_and_unknown_actions(self):
        with self.assertRaisesRegex(ValueError, "already"):
            self.gate.propose(
                ACTION_ID, RiskClass.FILE_MUTATION)
        with self.assertRaises(ValueError):
            self.gate.propose(
                UNKNOWN_ID, RiskClass.FILE_MUTATION,
                window_seconds=31.0)
        with self.assertRaises(KeyError):
            self.gate.click_confirm(UNKNOWN_ID)

    def test_free_form_payload_shaped_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "opaque"):
            self.gate.propose(
                "send-project-bluebird-to-ada",
                RiskClass.EXTERNAL_COMMUNICATION,
            )


class InertRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.counter = 0

        def tokens(_length: int) -> bytes:
            self.counter += 1
            return self.counter.to_bytes(16, "big")

        self.runtime = InertRiskyActionConfirmationRuntime(
            gate=RiskyActionConfirmationGate(clock=self.clock),
            random_bytes=tokens,
        )

    def test_explicit_start_voice_receipt_then_distinct_click(self):
        started = self.runtime.start(
            RiskClass.EXTERNAL_COMMUNICATION, window_seconds=10.0)
        voice = self.runtime.consume_voice("Confirm risky action.")
        clicked = self.runtime.click_confirm()

        self.assertEqual(started.state, "awaiting_voice")
        self.assertTrue(voice.consumed)
        self.assertEqual(voice.status.state, "awaiting_click")
        self.assertEqual(clicked.state, "confirmed")
        self.assertEqual(clicked.reason, "two_factor_confirmed")

    def test_non_command_is_not_consumed_or_retained(self):
        secret = "send the Project Bluebird budget to Ada"
        self.runtime.start(RiskClass.FILE_MUTATION)

        receipt = self.runtime.consume_voice(secret)

        self.assertFalse(receipt.consumed)
        self.assertEqual(receipt.status.state, "awaiting_voice")
        self.assertNotIn(secret, repr(self.runtime))
        self.assertNotIn(secret, repr(receipt))

    def test_cancel_expiry_early_click_and_terminal_replay_fail_closed(self):
        self.runtime.start(RiskClass.AGENT_EXECUTION, window_seconds=10.0)
        early = self.runtime.click_confirm()
        cancelled = self.runtime.consume_voice("cancel risky action")
        replay = self.runtime.consume_voice("confirm risky action")

        self.assertEqual(early.state, "awaiting_voice")
        self.assertEqual(early.reason, "click_before_voice")
        self.assertEqual(cancelled.status.state, "cancelled")
        self.assertEqual(replay.status.state, "cancelled")
        self.assertEqual(replay.status.reason, "already_terminal")

        self.runtime.start(RiskClass.CALENDAR_COMMIT, window_seconds=10.0)
        self.runtime.consume_voice("confirm risky action")
        self.clock.now = 110.0
        expired = self.runtime.click_confirm()
        self.assertEqual(expired.state, "expired")
        self.assertEqual(expired.reason, "deadline_expired")

    def test_only_one_closed_risk_ceremony_can_be_pending(self):
        self.runtime.start(RiskClass.FILE_MUTATION)
        with self.assertRaisesRegex(RuntimeError, "already pending"):
            self.runtime.start(RiskClass.CALENDAR_COMMIT)
        with self.assertRaisesRegex(ValueError, "closed RiskClass"):
            self.runtime.start("delete_everything")

        status = self.runtime.status()
        self.assertEqual(status.risk, RiskClass.FILE_MUTATION)
        self.assertEqual(set(asdict(status)), {"risk", "state", "reason"})


if __name__ == "__main__":
    unittest.main()
