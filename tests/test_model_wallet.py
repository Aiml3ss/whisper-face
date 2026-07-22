import inspect
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import model_wallet  # noqa: E402
from model_wallet import (  # noqa: E402
    CURRENT_PROVIDER_PROFILES,
    PARAKEET_PROFILE,
    QWEN_CLEANUP_PROFILE,
    WHISPER_LARGE_TURBO_PROFILE,
    WHISPER_TINY_PROFILE,
    AttemptReceipt,
    Capability,
    CapabilityEvidence,
    FailureKind,
    ModelIdentity,
    ModelRequest,
    ModelWallet,
    NeutralModelResult,
    PinnedLocalProviderAdapter,
    ProviderContractError,
    ReadinessReceipt,
    ReadinessState,
)


def ready(profile):
    return ReadinessReceipt(
        profile.identity, ReadinessState.READY, revision_verified=True)


def evidence(profile, capability, *, latency=100, quality=9000):
    return CapabilityEvidence(
        profile.identity,
        capability,
        latency_upper_bound_ms=latency,
        quality_lower_bound_bps=quality,
        sample_count=25,
    )


def success(profile, text, calls):
    def execute(request):
        calls.append(profile.provider_id)
        result = NeutralModelResult(
            request.request_id, request.capability, text, 9500)
        return AttemptReceipt.succeeded(profile, request, result)
    return execute


class ModelWalletTests(unittest.TestCase):
    def request(self, capability=Capability.FINAL_ASR):
        return ModelRequest("utterance-001", capability, 1000, 8000)

    def adapter(self, profile, capability, executor=None, **bounds):
        return PinnedLocalProviderAdapter(
            profile, ready(profile),
            [evidence(profile, capability, **bounds)], executor)

    def test_current_profiles_use_committed_immutable_pins_and_roles(self):
        actual = {
            profile.provider_id: (
                profile.identity.model_id,
                profile.identity.revision,
                profile.capabilities,
            )
            for profile in CURRENT_PROVIDER_PROFILES
        }
        self.assertEqual(actual, {
            "local.parakeet-coreml": (
                "FluidInference/parakeet-unified-en-0.6b-coreml",
                "4252711f6f060f9a2f91e5f081a806d7f45eebd8",
                frozenset({Capability.FINAL_ASR}),
            ),
            "local.whisper-tiny-mlx": (
                "mlx-community/whisper-tiny",
                "78c52ab98ca87f570bc57ad852e15ef7060f9f76",
                frozenset({Capability.FAST_ASR}),
            ),
            "local.whisper-large-v3-turbo-mlx": (
                "mlx-community/whisper-large-v3-turbo",
                "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
                frozenset({Capability.FINAL_ASR}),
            ),
            "local.qwen3.5-4b-ollama": (
                "qwen3.5:4b",
                "sha256:2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd",
                frozenset({Capability.CLEANUP}),
            ),
        })
        with self.assertRaises(FrozenInstanceError):
            PARAKEET_PROFILE.identity.revision = "0" * 40
        with self.assertRaisesRegex(ValueError, "immutable"):
            ModelIdentity("moving/model", "latest")

    def test_policy_selects_one_eligible_provider_deterministically(self):
        calls = []
        fallback = self.adapter(
            WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR,
            success(WHISPER_LARGE_TURBO_PROFILE, "fallback", calls),
            latency=80, quality=9900,
        )
        primary = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR,
            success(PARAKEET_PROFILE, "primary", calls),
            latency=150, quality=9000,
        )
        wallet = ModelWallet([fallback, primary])

        self.assertEqual(wallet.select(self.request()), PARAKEET_PROFILE)
        receipt = wallet.execute(self.request())

        self.assertEqual(receipt.result.text, "primary")
        self.assertEqual(calls, [PARAKEET_PROFILE.provider_id])
        self.assertEqual(len(receipt.attempts), 1)

    def test_ineligible_providers_are_skipped_before_any_attempt(self):
        calls = []
        too_slow = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR,
            success(PARAKEET_PROFILE, "slow", calls),
            latency=1001, quality=9900,
        )
        eligible = self.adapter(
            WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR,
            success(WHISPER_LARGE_TURBO_PROFILE, "eligible", calls),
            latency=500, quality=9000,
        )

        receipt = ModelWallet([too_slow, eligible]).execute(self.request())

        self.assertEqual(receipt.result.text, "eligible")
        self.assertEqual(calls, [WHISPER_LARGE_TURBO_PROFILE.provider_id])

    def test_not_ready_and_missing_evidence_are_skipped_pre_attempt(self):
        calls = []
        not_ready = PinnedLocalProviderAdapter(
            PARAKEET_PROFILE,
            ReadinessReceipt(
                PARAKEET_PROFILE.identity,
                ReadinessState.NOT_INSTALLED,
                revision_verified=True,
            ),
            [evidence(PARAKEET_PROFILE, Capability.FINAL_ASR)],
            success(PARAKEET_PROFILE, "not called", calls),
        )
        no_evidence = PinnedLocalProviderAdapter(
            WHISPER_LARGE_TURBO_PROFILE,
            ready(WHISPER_LARGE_TURBO_PROFILE),
            [],
            success(WHISPER_LARGE_TURBO_PROFILE, "not called", calls),
        )

        receipt = ModelWallet([not_ready, no_evidence]).execute(self.request())

        self.assertFalse(receipt.succeeded)
        self.assertEqual(receipt.attempts, ())
        self.assertEqual(calls, [])

    def test_explicit_failure_receipt_allows_one_sequential_failover(self):
        calls = []

        def fail(request):
            calls.append(PARAKEET_PROFILE.provider_id)
            return AttemptReceipt.failed(
                PARAKEET_PROFILE, request, FailureKind.EXECUTION_FAILED,
                attempted=True)

        primary = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR, fail)
        fallback = self.adapter(
            WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR,
            success(WHISPER_LARGE_TURBO_PROFILE, "recovered", calls))

        receipt = ModelWallet([fallback, primary]).execute(self.request())

        self.assertEqual(receipt.result.text, "recovered")
        self.assertEqual(calls, [
            PARAKEET_PROFILE.provider_id,
            WHISPER_LARGE_TURBO_PROFILE.provider_id,
        ])
        self.assertEqual(len(receipt.attempts), 2)

    def test_unwired_profile_receipt_can_fail_over_before_an_attempt(self):
        calls = []
        profile_only = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR)
        fallback = self.adapter(
            WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR,
            success(WHISPER_LARGE_TURBO_PROFILE, "fallback", calls))

        receipt = ModelWallet([profile_only, fallback]).execute(self.request())

        self.assertFalse(receipt.attempts[0].attempted)
        self.assertEqual(
            receipt.attempts[0].failure, FailureKind.NOT_RUNTIME_WIRED)
        self.assertEqual(calls, [WHISPER_LARGE_TURBO_PROFILE.provider_id])

    def test_exception_without_failure_receipt_never_launches_fallback(self):
        calls = []

        def explode(_request):
            calls.append(PARAKEET_PROFILE.provider_id)
            raise RuntimeError("private provider detail")

        primary = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR, explode)
        fallback = self.adapter(
            WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR,
            success(WHISPER_LARGE_TURBO_PROFILE, "must not run", calls))

        with self.assertRaisesRegex(ProviderContractError, "explicit"):
            ModelWallet([primary, fallback]).execute(self.request())
        self.assertEqual(calls, [PARAKEET_PROFILE.provider_id])

    def test_mismatched_receipt_never_launches_fallback(self):
        calls = []

        def wrong_receipt(request):
            calls.append(PARAKEET_PROFILE.provider_id)
            result = NeutralModelResult(
                request.request_id, request.capability, "wrong", 9000)
            return AttemptReceipt.succeeded(
                WHISPER_LARGE_TURBO_PROFILE, request, result)

        primary = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR, wrong_receipt)
        fallback = self.adapter(
            WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR,
            success(WHISPER_LARGE_TURBO_PROFILE, "must not run", calls))

        with self.assertRaisesRegex(ProviderContractError, "selection"):
            ModelWallet([primary, fallback]).execute(self.request())
        self.assertEqual(calls, [PARAKEET_PROFILE.provider_id])

    def test_capability_bounds_and_identity_evidence_are_strict(self):
        with self.assertRaisesRegex(ValueError, "latency"):
            evidence(PARAKEET_PROFILE, Capability.FINAL_ASR, latency=0)
        with self.assertRaisesRegex(ValueError, "quality"):
            evidence(PARAKEET_PROFILE, Capability.FINAL_ASR, quality=10001)
        wrong = PinnedLocalProviderAdapter(
            PARAKEET_PROFILE,
            ready(PARAKEET_PROFILE),
            [evidence(
                WHISPER_LARGE_TURBO_PROFILE, Capability.FINAL_ASR)],
        )
        with self.assertRaisesRegex(ProviderContractError, "evidence"):
            ModelWallet([wrong]).select(self.request())

    def test_cleanup_result_is_neutral_and_has_no_delivery_authority(self):
        calls = []
        provider = self.adapter(
            QWEN_CLEANUP_PROFILE, Capability.CLEANUP,
            success(QWEN_CLEANUP_PROFILE, "Clean text", calls))
        receipt = ModelWallet([provider]).execute(
            self.request(Capability.CLEANUP))

        self.assertEqual(receipt.result.text, "Clean text")
        self.assertEqual(
            {field.name for field in fields(NeutralModelResult)},
            {"request_id", "capability", "text", "confidence_bps"},
        )
        source = inspect.getsource(model_wallet).lower()
        self.assertNotIn("import voice_compiler", source)
        self.assertFalse(hasattr(receipt.result, "paste"))
        self.assertFalse(hasattr(receipt.result, "app"))
        self.assertFalse(hasattr(receipt.result, "context"))

    def test_profiles_cover_fast_final_and_cleanup_without_cross_role_use(self):
        calls = []
        tiny = self.adapter(
            WHISPER_TINY_PROFILE, Capability.FAST_ASR,
            success(WHISPER_TINY_PROFILE, "preview", calls))
        final = self.adapter(
            PARAKEET_PROFILE, Capability.FINAL_ASR,
            success(PARAKEET_PROFILE, "final", calls))
        cleanup = self.adapter(
            QWEN_CLEANUP_PROFILE, Capability.CLEANUP,
            success(QWEN_CLEANUP_PROFILE, "clean", calls))
        wallet = ModelWallet([cleanup, final, tiny])

        self.assertEqual(
            wallet.execute(self.request(Capability.FAST_ASR)).result.text,
            "preview")
        self.assertEqual(
            wallet.execute(self.request(Capability.FINAL_ASR)).result.text,
            "final")
        self.assertEqual(
            wallet.execute(self.request(Capability.CLEANUP)).result.text,
            "clean")


if __name__ == "__main__":
    unittest.main()
