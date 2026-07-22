import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_input_protocol import (  # noqa: E402
    ADAPTER_PROFILES,
    EVIDENCE_SCOPE,
    MessageKind,
    ProtocolError,
    ProtocolMessage,
    VoiceInputProtocolSession,
    validate_transcript,
)


class VoiceInputProtocolTests(unittest.TestCase):
    def full_session(self, profile_id):
        session = VoiceInputProtocolSession("utterance-001", profile_id)
        capture = session.capture_proposal()
        session.publish_stable_prefix("Ship", 320)
        session.publish_stable_prefix("Ship the", 610)
        session.publish_final_text("Ship the release")
        commit = session.commit()
        acknowledgement = session.acknowledge()
        validated = validate_transcript(
            [message.to_mapping() for message in session.messages])
        return session, capture, commit, acknowledgement, validated

    def test_five_profiles_conform_to_insertion_integrity_receipts(self):
        expected = {
            "readable-complete": (
                "verified", "commit_verified", True, False, 1),
            "readable-no-readback": (
                "unverifiable", "readback_unavailable", True, True, 1),
            "opaque-reviewed": (
                "unverifiable", "readback_unavailable", True, True, 1),
            "clipboard-unavailable": (
                "unresolved", "paste_outcome_unknown", True, True, 1),
            "target-unavailable": (
                "unverifiable", "target_unreadable", False, True, 0),
        }
        self.assertEqual(
            {profile.profile_id for profile in ADAPTER_PROFILES}, set(expected))

        for profile_id, receipt in expected.items():
            with self.subTest(profile=profile_id):
                session, capture, commit, ack, validated = self.full_session(
                    profile_id)
                self.assertEqual(
                    (
                        commit.payload["state"], commit.payload["reason"],
                        commit.payload["paste_attempted"],
                        commit.payload["recoverable"], session.paste_attempts,
                    ),
                    receipt,
                )
                self.assertEqual(
                    capture.payload["evidence_scope"], EVIDENCE_SCOPE)
                self.assertTrue(ack.payload["accepted"])
                self.assertEqual(
                    ack.payload["outbox_dismissed"], receipt[3])
                self.assertEqual(validated, session.messages)

    def test_duplicate_commit_and_ack_return_receipts_without_repaste(self):
        session = VoiceInputProtocolSession(
            "utterance-duplicate", "readable-complete")
        session.capture_proposal()
        session.publish_final_text("Only once")

        commit = session.commit()
        acknowledgement = session.acknowledge()

        self.assertIs(session.commit(), commit)
        self.assertIs(session.acknowledge(), acknowledgement)
        self.assertEqual(session.paste_attempts, 1)
        self.assertEqual(session.pasted_text, ("Only once",))

    def test_cancel_is_a_terminal_transcript_without_staging_text(self):
        session = VoiceInputProtocolSession(
            "utterance-cancel", "readable-complete")
        session.capture_proposal()
        session.publish_stable_prefix("Never inserted", 100)
        cancellation = session.cancel("superseded")

        self.assertEqual(cancellation.kind, MessageKind.CANCELLATION)
        self.assertEqual(session.paste_attempts, 0)
        self.assertEqual(validate_transcript(session.messages), session.messages)
        with self.assertRaises(ProtocolError):
            session.publish_final_text("Never inserted")

    def test_message_and_payload_schemas_reject_unknown_fields(self):
        session = VoiceInputProtocolSession(
            "utterance-schema", "readable-complete")
        mapping = session.capture_proposal().to_mapping()
        mapping["transport"] = "network"
        with self.assertRaisesRegex(ProtocolError, "message schema"):
            ProtocolMessage.from_mapping(mapping)

        mapping.pop("transport")
        mapping["payload"]["destination_id"] = "private-field"
        with self.assertRaisesRegex(ProtocolError, "payload schema"):
            ProtocolMessage.from_mapping(mapping)

    def test_capture_capability_must_match_a_fixed_profile(self):
        session = VoiceInputProtocolSession(
            "utterance-capability", "readable-complete")
        mapping = session.capture_proposal().to_mapping()
        mapping["payload"]["evidence_scope"] = "shipped-network-sdk"
        with self.assertRaisesRegex(ProtocolError, "destination capability"):
            ProtocolMessage.from_mapping(mapping)

        mapping = session.capture_proposal().to_mapping()
        mapping["payload"]["profile_id"] = []
        with self.assertRaisesRegex(ProtocolError, "destination capability"):
            ProtocolMessage.from_mapping(mapping)

    def test_scalar_types_and_receipt_semantics_are_strict(self):
        session = VoiceInputProtocolSession(
            "utterance-types", "readable-complete")
        mapping = session.capture_proposal().to_mapping()
        mapping["schema_version"] = True
        with self.assertRaisesRegex(ProtocolError, "schema version"):
            ProtocolMessage.from_mapping(mapping)

        mapping = {
            "schema_version": 1,
            "utterance_id": "utterance-types",
            "sequence": 1,
            "kind": "commit_receipt",
            "payload": {
                "state": "verified",
                "reason": "commit_verified",
                "paste_attempted": False,
                "recoverable": False,
            },
        }
        with self.assertRaisesRegex(ProtocolError, "commit receipt"):
            ProtocolMessage.from_mapping(mapping)

    def test_stable_prefix_and_timing_must_only_advance(self):
        session = VoiceInputProtocolSession(
            "utterance-prefix", "readable-complete")
        session.capture_proposal()
        session.publish_stable_prefix("The stable words", 500)
        with self.assertRaisesRegex(ProtocolError, "prefix regressed"):
            session.publish_stable_prefix("The changed words", 600)
        with self.assertRaisesRegex(ProtocolError, "prefix regressed"):
            session.publish_stable_prefix("The stable words continue", 499)
        with self.assertRaisesRegex(ProtocolError, "stable prefix"):
            session.publish_final_text("Different final")

    def test_transcript_validator_rejects_sequence_and_lifecycle_drift(self):
        session, _capture, _commit, _ack, _validated = self.full_session(
            "readable-complete")
        mappings = [message.to_mapping() for message in session.messages]
        mappings[1]["sequence"] = 5
        with self.assertRaisesRegex(ProtocolError, "not contiguous"):
            validate_transcript(mappings)

        session = VoiceInputProtocolSession(
            "utterance-open", "readable-complete")
        session.capture_proposal()
        with self.assertRaisesRegex(ProtocolError, "no terminal"):
            validate_transcript(session.messages)
        self.assertEqual(
            validate_transcript(session.messages, require_terminal=False),
            session.messages,
        )

    def test_messages_expose_no_destination_identity_or_context(self):
        session, _capture, _commit, _ack, _validated = self.full_session(
            "opaque-reviewed")
        encoded = repr([message.to_mapping() for message in session.messages])
        self.assertNotIn("vip-local", encoded)
        self.assertNotIn("synthetic-context", encoded)
        self.assertNotIn("synthetic-composer", encoded)
        self.assertNotIn("destination_id", encoded)


if __name__ == "__main__":
    unittest.main()
