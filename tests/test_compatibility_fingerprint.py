import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compatibility_fingerprint import (  # noqa: E402
    MAX_EXPORT_BYTES,
    CompatibilityFingerprintAggregator,
    CompatibilityObservation,
    validate_export_payload,
)


CAPABILITIES = {
    "target": "readable",
    "paste": "available",
    "readback": "available",
}
OUTCOME = {
    "state": "verified",
    "reason": "success",
    "paste_attempted": True,
}


class CompatibilityFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic_and_changes_by_coarse_bucket(self):
        first = CompatibilityObservation.from_buckets(
            dict(reversed(list(CAPABILITIES.items()))),
            dict(reversed(list(OUTCOME.items()))),
        )
        second = CompatibilityObservation.from_buckets(
            CAPABILITIES, OUTCOME)
        changed = CompatibilityObservation.from_buckets(
            CAPABILITIES, {**OUTCOME, "state": "conflict",
                           "reason": "verification-conflict"})

        self.assertEqual(first.fingerprint(), second.fingerprint())
        self.assertNotEqual(first.fingerprint(), changed.fingerprint())
        self.assertRegex(first.fingerprint(), r"^[0-9a-f]{16}$")

    def test_input_schema_rejects_unknown_and_private_fields(self):
        for private_field in (
                "app_name", "bundle_id", "text", "raw_transcript",
                "selection", "surrounding_content", "user_id", "device_id"):
            with self.subTest(private_field=private_field):
                with self.assertRaisesRegex(ValueError, "exactly"):
                    CompatibilityObservation.from_buckets(
                        {**CAPABILITIES, private_field: "private"}, OUTCOME)
                with self.assertRaisesRegex(ValueError, "exactly"):
                    CompatibilityObservation.from_buckets(
                        CAPABILITIES, {**OUTCOME, private_field: "private"})

    def test_input_values_are_closed_buckets(self):
        with self.assertRaisesRegex(ValueError, "target bucket"):
            CompatibilityObservation.from_buckets(
                {**CAPABILITIES, "target": "Mail.app"}, OUTCOME)
        with self.assertRaisesRegex(ValueError, "reason bucket"):
            CompatibilityObservation.from_buckets(
                CAPABILITIES, {**OUTCOME, "reason": "user typed secret"})
        with self.assertRaisesRegex(ValueError, "boolean"):
            CompatibilityObservation.from_buckets(
                CAPABILITIES, {**OUTCOME, "paste_attempted": 1})

    def test_export_defaults_off_even_when_a_bucket_is_eligible(self):
        aggregator = CompatibilityFingerprintAggregator(minimum_count=3)
        for _ in range(3):
            aggregator.record(CAPABILITIES, OUTCOME)

        self.assertIsNone(aggregator.export_payload())
        self.assertIsNone(aggregator.export_json())
        self.assertIsNone(aggregator.export_payload(opt_in=False))

    def test_release_gate_omits_buckets_below_minimum_count(self):
        aggregator = CompatibilityFingerprintAggregator(minimum_count=3)
        aggregator.record(CAPABILITIES, OUTCOME)
        aggregator.record(CAPABILITIES, OUTCOME)

        payload = aggregator.export_payload(opt_in=True)

        self.assertEqual(payload["buckets"], [])
        self.assertEqual(payload["released_observations"], 0)

    def test_opted_in_export_contains_only_aggregate_tokens_and_counts(self):
        aggregator = CompatibilityFingerprintAggregator(minimum_count=3)
        expected = None
        for _ in range(3):
            expected = aggregator.record(CAPABILITIES, OUTCOME)

        payload = aggregator.export_payload(opt_in=True)
        encoded = aggregator.export_json(opt_in=True)

        self.assertEqual(
            payload["buckets"], [{"fingerprint": expected, "count": 3}])
        self.assertLessEqual(len(encoded), MAX_EXPORT_BYTES)
        decoded = json.loads(encoded)
        self.assertEqual(decoded, payload)
        serialized = encoded.decode("ascii")
        for forbidden in (
                "app_name", "bundle_id", "text", "transcript", "selection",
                "surrounding", "user_id", "device_id"):
            self.assertNotIn(forbidden, serialized)

    def test_export_validator_rejects_unknown_private_or_malformed_fields(self):
        aggregator = CompatibilityFingerprintAggregator(minimum_count=2)
        aggregator.record(CAPABILITIES, OUTCOME)
        aggregator.record(CAPABILITIES, OUTCOME)
        payload = aggregator.export_payload(opt_in=True)

        with self.assertRaisesRegex(ValueError, "exactly"):
            validate_export_payload({**payload, "bundle_id": "private"})
        with self.assertRaisesRegex(ValueError, "exactly"):
            validate_export_payload({
                **payload,
                "buckets": [{**payload["buckets"][0], "text": "private"}],
            })
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_export_payload({**payload, "released_observations": 999})

    def test_local_bucket_storage_is_bounded(self):
        aggregator = CompatibilityFingerprintAggregator(
            minimum_count=2, max_local_buckets=1)
        aggregator.record(CAPABILITIES, OUTCOME)
        with self.assertRaisesRegex(OverflowError, "bucket limit"):
            aggregator.record(
                CAPABILITIES,
                {**OUTCOME, "state": "unverifiable",
                 "reason": "verification-unavailable"},
            )


if __name__ == "__main__":
    unittest.main()
