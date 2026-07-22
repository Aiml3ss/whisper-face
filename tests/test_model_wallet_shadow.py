import inspect
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import model_wallet_shadow  # noqa: E402
from model_wallet import (  # noqa: E402
    PARAKEET_PROFILE,
    WHISPER_LARGE_TURBO_PROFILE,
    Capability,
    ModelRequest,
    ReadinessState,
)
from model_wallet_shadow import (  # noqa: E402
    AdvisoryEligibility,
    ModelWalletShadowReceipt,
    RuntimeCapabilityEvidence,
    RuntimeModelEvidence,
    ShadowProviderReceipt,
    assess_model_wallet,
    readiness_adapters,
)


def observed(profile, *, state=ReadinessState.READY, verified=True,
             capability=None, latency=100, quality=9000, samples=25):
    return RuntimeModelEvidence(
        profile.provider_id,
        state,
        verified,
        RuntimeCapabilityEvidence(
            capability or next(iter(profile.capabilities)), latency, quality,
            samples),
    )


class ModelWalletShadowTests(unittest.TestCase):
    def request(self, capability=Capability.FINAL_ASR):
        return ModelRequest("shadow-001", capability, 1_000, 8_000)

    def test_converts_current_observations_to_existing_typed_contract(self):
        adapters = readiness_adapters([observed(PARAKEET_PROFILE)])

        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].profile, PARAKEET_PROFILE)
        self.assertEqual(adapters[0].readiness().identity,
                         PARAKEET_PROFILE.identity)
        self.assertTrue(adapters[0].readiness().revision_verified)
        self.assertEqual(
            adapters[0].evidence(Capability.FINAL_ASR).sample_count, 25)
        self.assertIsNone(adapters[0]._executor)

    def test_advises_wallet_policy_order_without_attempting_models(self):
        receipt = assess_model_wallet(self.request(), [
            observed(WHISPER_LARGE_TURBO_PROFILE, latency=80, quality=9900),
            observed(PARAKEET_PROFILE, latency=150, quality=9000),
        ])

        self.assertEqual(receipt.advisory_order, (
            PARAKEET_PROFILE.provider_id,
            WHISPER_LARGE_TURBO_PROFILE.provider_id,
        ))
        self.assertEqual(receipt.selected_provider_id,
                         PARAKEET_PROFILE.provider_id)
        self.assertFalse(receipt.fail_closed)
        self.assertFalse(receipt.attempted)

    def test_missing_runtime_evidence_is_honest_and_fails_closed(self):
        receipt = assess_model_wallet(self.request(), [])
        states = {item.provider_id: item.eligibility
                  for item in receipt.providers}

        self.assertTrue(receipt.fail_closed)
        self.assertEqual(receipt.advisory_order, ())
        self.assertIsNone(receipt.selected_provider_id)
        self.assertEqual(states[PARAKEET_PROFILE.provider_id],
                         AdvisoryEligibility.MISSING_RUNTIME_EVIDENCE)
        self.assertEqual(states[WHISPER_LARGE_TURBO_PROFILE.provider_id],
                         AdvisoryEligibility.MISSING_RUNTIME_EVIDENCE)

    def test_not_ready_missing_metrics_and_bounds_are_distinct(self):
        not_ready = observed(PARAKEET_PROFILE, state=ReadinessState.LOAD_FAILED,
                             verified=False)
        no_metrics = RuntimeModelEvidence(
            WHISPER_LARGE_TURBO_PROFILE.provider_id,
            ReadinessState.READY,
            True,
        )
        receipt = assess_model_wallet(self.request(), [not_ready, no_metrics])
        states = {item.provider_id: item.eligibility
                  for item in receipt.providers}

        self.assertEqual(states[PARAKEET_PROFILE.provider_id],
                         AdvisoryEligibility.NOT_READY)
        self.assertEqual(states[WHISPER_LARGE_TURBO_PROFILE.provider_id],
                         AdvisoryEligibility.MISSING_CAPABILITY_EVIDENCE)

        bounded = assess_model_wallet(self.request(), [
            observed(PARAKEET_PROFILE, latency=1001),
        ])
        self.assertEqual(bounded.providers[0].eligibility,
                         AdvisoryEligibility.OUTSIDE_REQUEST_BOUNDS)
        self.assertTrue(bounded.fail_closed)

    def test_rejects_non_current_pins_duplicate_observations_and_cross_role_data(self):
        with self.assertRaisesRegex(ValueError, "current pin"):
            RuntimeModelEvidence("local.unknown", ReadinessState.READY, True)
        with self.assertRaisesRegex(ValueError, "does not match"):
            observed(PARAKEET_PROFILE, capability=Capability.FAST_ASR)
        duplicate = observed(PARAKEET_PROFILE)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            readiness_adapters([duplicate, duplicate])

    def test_receipts_are_closed_transcript_free_and_deterministic(self):
        first = assess_model_wallet(self.request(), [
            observed(PARAKEET_PROFILE),
            observed(WHISPER_LARGE_TURBO_PROFILE),
        ])
        second = assess_model_wallet(self.request(), [
            observed(WHISPER_LARGE_TURBO_PROFILE),
            observed(PARAKEET_PROFILE),
        ])

        self.assertEqual(first, second)
        self.assertEqual(
            {field.name for field in fields(ModelWalletShadowReceipt)},
            {"schema_version", "request_id", "capability", "providers",
             "advisory_order", "selected_provider_id", "fail_closed",
             "attempted"},
        )
        with self.assertRaises(FrozenInstanceError):
            first.attempted = True
        source = inspect.getsource(model_wallet_shadow).lower()
        for forbidden in ("import dictate", "requests", "subprocess", "socket",
                          "urllib", ".execute("):
            self.assertNotIn(forbidden, source)

    def test_receipt_rejects_impossible_eligibility_and_noncanonical_order(self):
        receipt = assess_model_wallet(self.request(), [
            observed(PARAKEET_PROFILE),
            observed(WHISPER_LARGE_TURBO_PROFILE),
        ])
        impossible = tuple(
            ShadowProviderReceipt(
                item.provider_id,
                item.capability,
                AdvisoryEligibility.ELIGIBLE,
            ) if item.eligibility == AdvisoryEligibility.UNSUPPORTED_CAPABILITY
            else item
            for item in receipt.providers
        )
        with self.assertRaisesRegex(ValueError, "pinned capabilities"):
            ModelWalletShadowReceipt(
                receipt.schema_version,
                receipt.request_id,
                receipt.capability,
                impossible,
                tuple(item.provider_id for item in impossible),
                impossible[0].provider_id,
                False,
            )

        reversed_order = tuple(reversed(receipt.advisory_order))
        with self.assertRaisesRegex(ValueError, "not canonical"):
            ModelWalletShadowReceipt(
                receipt.schema_version,
                receipt.request_id,
                receipt.capability,
                receipt.providers,
                reversed_order,
                reversed_order[0],
                False,
            )

    def test_receipt_provider_capability_must_match_request(self):
        receipt = assess_model_wallet(self.request(), [
            observed(PARAKEET_PROFILE),
        ])
        mismatched = (
            ShadowProviderReceipt(
                receipt.providers[0].provider_id,
                Capability.CLEANUP,
                receipt.providers[0].eligibility,
            ),
            *receipt.providers[1:],
        )
        with self.assertRaisesRegex(ValueError, "capability must match"):
            ModelWalletShadowReceipt(
                receipt.schema_version,
                receipt.request_id,
                receipt.capability,
                mismatched,
                receipt.advisory_order,
                receipt.selected_provider_id,
                receipt.fail_closed,
            )


if __name__ == "__main__":
    unittest.main()
