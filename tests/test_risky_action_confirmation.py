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


if __name__ == "__main__":
    unittest.main()
