import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_input_protocol import (  # noqa: E402
    ADAPTER_PROFILES,
    MessageKind,
    ProtocolError,
    VoiceInputProtocolSession,
)
from voice_input_protocol_wire import (  # noqa: E402
    MAX_FRAME_BYTES,
    decode_message,
    encode_message,
)


class VoiceInputProtocolWireTests(unittest.TestCase):
    def completed_messages(self, profile_id):
        session = VoiceInputProtocolSession("wire-round-trip", profile_id)
        session.capture_proposal()
        session.publish_stable_prefix("Ship", 320)
        session.publish_final_text("Ship the release ✅")
        session.commit()
        session.acknowledge()
        return session.messages

    def test_all_message_kinds_and_profiles_round_trip_deterministically(self):
        seen_kinds = set()

        for profile in ADAPTER_PROFILES:
            with self.subTest(profile=profile.profile_id):
                for message in self.completed_messages(profile.profile_id):
                    first_frame = encode_message(message)
                    decoded = decode_message(first_frame)
                    seen_kinds.add(decoded.kind)

                    self.assertEqual(
                        decoded.to_mapping(), message.to_mapping())
                    self.assertEqual(encode_message(decoded), first_frame)
                    if decoded.kind == MessageKind.FINAL_TEXT:
                        self.assertIn("✅".encode("utf-8"), first_frame)
                    self.assertEqual(
                        first_frame,
                        json.dumps(
                            message.to_mapping(),
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8"),
                    )

                cancelled = VoiceInputProtocolSession(
                    "wire-cancel", profile.profile_id)
                cancelled.capture_proposal()
                cancellation = cancelled.cancel("user_cancelled")
                decoded = decode_message(encode_message(cancellation))
                seen_kinds.add(decoded.kind)
                self.assertEqual(
                    decoded.to_mapping(), cancellation.to_mapping())

        self.assertEqual(seen_kinds, set(MessageKind))

    def test_decode_delegates_unknown_fields_to_protocol_validation(self):
        session = VoiceInputProtocolSession("wire-schema", "readable-complete")
        mapping = session.capture_proposal().to_mapping()
        mapping["transport"] = "socket"
        frame = json.dumps(
            mapping, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        with self.assertRaisesRegex(ProtocolError, "message schema"):
            decode_message(frame)

        mapping.pop("transport")
        mapping["payload"]["destination_id"] = "private-field"
        frame = json.dumps(
            mapping, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with self.assertRaisesRegex(ProtocolError, "payload schema"):
            decode_message(frame)

    def test_decode_rejects_invalid_or_noncanonical_frames(self):
        session = VoiceInputProtocolSession(
            "wire-canonical", "readable-complete")
        message = session.capture_proposal()

        with self.assertRaisesRegex(ProtocolError, "invalid protocol JSON"):
            decode_message(b"\xff")
        with self.assertRaisesRegex(ProtocolError, "not canonical"):
            decode_message(json.dumps(message.to_mapping()).encode("utf-8"))
        with self.assertRaisesRegex(ProtocolError, "invalid protocol JSON"):
            decode_message(b"NaN")

    def test_decode_enforces_the_byte_limit_before_parsing(self):
        oversized = b" " * (MAX_FRAME_BYTES + 1)

        with self.assertRaisesRegex(ProtocolError, "size limit"):
            decode_message(oversized)

    def test_codec_requires_bytes_and_protocol_messages(self):
        with self.assertRaisesRegex(TypeError, "ProtocolMessage"):
            encode_message({})
        with self.assertRaisesRegex(TypeError, "bytes"):
            decode_message("{}")


if __name__ == "__main__":
    unittest.main()
