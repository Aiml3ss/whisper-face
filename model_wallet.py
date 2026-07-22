"""Provider-neutral, in-process policy for pinned local speech models.

This foundation does not route the live runtime, discover models, perform
network calls, or deliver text.  It selects from caller-supplied local provider
receipts and returns recognition or cleanup text for a later pipeline stage.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable


EVIDENCE_SCOPE = "in-process-policy-conformance-only"
MAX_TEXT_CHARS = 100_000
MAX_LATENCY_BOUND_MS = 600_000
MAX_EVIDENCE_SAMPLES = 1_000_000
_SHA_REVISION = re.compile(r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})\Z")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


class Capability(str, Enum):
    FAST_ASR = "fast_asr"
    FINAL_ASR = "final_asr"
    CLEANUP = "cleanup"


class ReadinessState(str, Enum):
    READY = "ready"
    NOT_INSTALLED = "not_installed"
    LOAD_FAILED = "load_failed"
    REVISION_MISMATCH = "revision_mismatch"


class AttemptState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FailureKind(str, Enum):
    NOT_RUNTIME_WIRED = "not_runtime_wired"
    EXECUTION_FAILED = "execution_failed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    INVALID_OUTPUT = "invalid_output"


class ProviderContractError(RuntimeError):
    """A provider crossed a policy boundary without a valid receipt."""


def _plain_int(value: object, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


@dataclass(frozen=True)
class ModelIdentity:
    """An immutable model repository/tag paired with a content revision."""

    model_id: str
    revision: str

    def __post_init__(self) -> None:
        if (not isinstance(self.model_id, str)
                or not _MODEL_ID.fullmatch(self.model_id)):
            raise ValueError("invalid model_id")
        if not isinstance(self.revision, str) or not _SHA_REVISION.fullmatch(
                self.revision):
            raise ValueError("revision must be an immutable SHA or SHA-256 digest")


@dataclass(frozen=True)
class ProviderProfile:
    """Static identity, capabilities, and deterministic preference."""

    provider_id: str
    identity: ModelIdentity
    capabilities: frozenset[Capability]
    preference_rank: int

    def __post_init__(self) -> None:
        if (not isinstance(self.provider_id, str)
                or not _IDENTIFIER.fullmatch(self.provider_id)):
            raise ValueError("invalid provider_id")
        if not isinstance(self.identity, ModelIdentity):
            raise ValueError("identity must be a ModelIdentity")
        capabilities = frozenset(self.capabilities)
        if (not capabilities
                or any(not isinstance(value, Capability)
                       for value in capabilities)):
            raise ValueError("capabilities must contain Capability values")
        if not _plain_int(self.preference_rank, minimum=0, maximum=10_000):
            raise ValueError("preference_rank must be between 0 and 10000")
        object.__setattr__(self, "capabilities", capabilities)


PARAKEET_PROFILE = ProviderProfile(
    provider_id="local.parakeet-coreml",
    identity=ModelIdentity(
        "FluidInference/parakeet-unified-en-0.6b-coreml",
        "4252711f6f060f9a2f91e5f081a806d7f45eebd8",
    ),
    capabilities=frozenset({Capability.FINAL_ASR}),
    preference_rank=10,
)
WHISPER_TINY_PROFILE = ProviderProfile(
    provider_id="local.whisper-tiny-mlx",
    identity=ModelIdentity(
        "mlx-community/whisper-tiny",
        "78c52ab98ca87f570bc57ad852e15ef7060f9f76",
    ),
    capabilities=frozenset({Capability.FAST_ASR}),
    preference_rank=10,
)
WHISPER_LARGE_TURBO_PROFILE = ProviderProfile(
    provider_id="local.whisper-large-v3-turbo-mlx",
    identity=ModelIdentity(
        "mlx-community/whisper-large-v3-turbo",
        "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
    ),
    capabilities=frozenset({Capability.FINAL_ASR}),
    preference_rank=20,
)
QWEN_CLEANUP_PROFILE = ProviderProfile(
    provider_id="local.qwen3.5-4b-ollama",
    identity=ModelIdentity(
        "qwen3.5:4b",
        "sha256:2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd",
    ),
    capabilities=frozenset({Capability.CLEANUP}),
    preference_rank=10,
)
CURRENT_PROVIDER_PROFILES = (
    PARAKEET_PROFILE,
    WHISPER_TINY_PROFILE,
    WHISPER_LARGE_TURBO_PROFILE,
    QWEN_CLEANUP_PROFILE,
)


@dataclass(frozen=True)
class ReadinessReceipt:
    identity: ModelIdentity
    state: ReadinessState
    revision_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ModelIdentity):
            raise ValueError("readiness identity must be a ModelIdentity")
        if not isinstance(self.state, ReadinessState):
            raise ValueError("invalid readiness state")
        if not isinstance(self.revision_verified, bool):
            raise ValueError("revision_verified must be a boolean")
        if self.state == ReadinessState.READY and not self.revision_verified:
            raise ValueError("ready models must have a verified revision")
        if (self.state == ReadinessState.REVISION_MISMATCH
                and self.revision_verified):
            raise ValueError("revision mismatch cannot be verified")


@dataclass(frozen=True)
class CapabilityEvidence:
    """Finite conservative bounds used only for eligibility decisions."""

    identity: ModelIdentity
    capability: Capability
    latency_upper_bound_ms: int
    quality_lower_bound_bps: int
    sample_count: int
    evidence_scope: str = EVIDENCE_SCOPE

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ModelIdentity):
            raise ValueError("evidence identity must be a ModelIdentity")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid evidence capability")
        if not _plain_int(
                self.latency_upper_bound_ms,
                minimum=1,
                maximum=MAX_LATENCY_BOUND_MS):
            raise ValueError("latency bound is invalid")
        if not _plain_int(
                self.quality_lower_bound_bps, minimum=0, maximum=10_000):
            raise ValueError("quality bound is invalid")
        if not _plain_int(
                self.sample_count, minimum=1, maximum=MAX_EVIDENCE_SAMPLES):
            raise ValueError("sample_count is invalid")
        if self.evidence_scope != EVIDENCE_SCOPE:
            raise ValueError("unsupported evidence scope")


@dataclass(frozen=True)
class ModelRequest:
    """A policy request containing no destination or contextual authority."""

    request_id: str
    capability: Capability
    latency_budget_ms: int
    quality_floor_bps: int

    def __post_init__(self) -> None:
        if (not isinstance(self.request_id, str)
                or not _IDENTIFIER.fullmatch(self.request_id)):
            raise ValueError("invalid request_id")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid request capability")
        if not _plain_int(
                self.latency_budget_ms,
                minimum=1,
                maximum=MAX_LATENCY_BOUND_MS):
            raise ValueError("latency budget is invalid")
        if not _plain_int(
                self.quality_floor_bps, minimum=0, maximum=10_000):
            raise ValueError("quality floor is invalid")


@dataclass(frozen=True)
class NeutralModelResult:
    """Recognition or cleanup text with no insertion or destination powers."""

    request_id: str
    capability: Capability
    text: str
    confidence_bps: int | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.request_id, str)
                or not _IDENTIFIER.fullmatch(self.request_id)):
            raise ValueError("invalid result request_id")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid result capability")
        if not isinstance(self.text, str) or len(self.text) > MAX_TEXT_CHARS:
            raise ValueError("result text is invalid")
        if (self.confidence_bps is not None
                and not _plain_int(
                    self.confidence_bps, minimum=0, maximum=10_000)):
            raise ValueError("confidence is invalid")


@dataclass(frozen=True)
class AttemptReceipt:
    provider_id: str
    identity: ModelIdentity
    request_id: str
    capability: Capability
    state: AttemptState
    attempted: bool
    result: NeutralModelResult | None = None
    failure: FailureKind | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.provider_id, str)
                or not _IDENTIFIER.fullmatch(self.provider_id)):
            raise ValueError("invalid receipt provider_id")
        if (not isinstance(self.identity, ModelIdentity)
                or not isinstance(self.request_id, str)
                or not _IDENTIFIER.fullmatch(self.request_id)
                or not isinstance(self.capability, Capability)
                or not isinstance(self.state, AttemptState)
                or not isinstance(self.attempted, bool)):
            raise ValueError("invalid attempt receipt")
        if self.state == AttemptState.SUCCEEDED:
            if (not self.attempted or self.failure is not None
                    or self.result is None
                    or self.result.request_id != self.request_id
                    or self.result.capability != self.capability):
                raise ValueError("invalid success receipt")
        elif self.result is not None or not isinstance(self.failure, FailureKind):
            raise ValueError("invalid failure receipt")

    @classmethod
    def succeeded(
        cls,
        profile: ProviderProfile,
        request: ModelRequest,
        result: NeutralModelResult,
    ) -> "AttemptReceipt":
        return cls(
            profile.provider_id, profile.identity, request.request_id,
            request.capability, AttemptState.SUCCEEDED, True, result=result,
        )

    @classmethod
    def failed(
        cls,
        profile: ProviderProfile,
        request: ModelRequest,
        failure: FailureKind,
        *,
        attempted: bool,
    ) -> "AttemptReceipt":
        return cls(
            profile.provider_id, profile.identity, request.request_id,
            request.capability, AttemptState.FAILED, attempted,
            failure=failure,
        )


class ModelProvider(ABC):
    """Strict local-provider boundary consumed by :class:`ModelWallet`."""

    @property
    @abstractmethod
    def profile(self) -> ProviderProfile:
        raise NotImplementedError

    @abstractmethod
    def readiness(self) -> ReadinessReceipt:
        raise NotImplementedError

    @abstractmethod
    def evidence(self, capability: Capability) -> CapabilityEvidence | None:
        raise NotImplementedError

    @abstractmethod
    def attempt(self, request: ModelRequest) -> AttemptReceipt:
        raise NotImplementedError


class PinnedLocalProviderAdapter(ModelProvider):
    """Side-effect-free profile adapter around caller-supplied local hooks."""

    def __init__(
        self,
        profile: ProviderProfile,
        readiness: ReadinessReceipt,
        evidence: Iterable[CapabilityEvidence],
        executor: Callable[[ModelRequest], AttemptReceipt] | None = None,
    ):
        if not isinstance(profile, ProviderProfile):
            raise TypeError("profile must be a ProviderProfile")
        if not isinstance(readiness, ReadinessReceipt):
            raise TypeError("readiness must be a ReadinessReceipt")
        self._profile = profile
        self._readiness = readiness
        evidence_items = tuple(evidence)
        if any(not isinstance(item, CapabilityEvidence)
               for item in evidence_items):
            raise TypeError("evidence must contain CapabilityEvidence values")
        self._evidence = {item.capability: item for item in evidence_items}
        if len(self._evidence) != len(evidence_items):
            raise ValueError("duplicate capability evidence")
        self._executor = executor

    @property
    def profile(self) -> ProviderProfile:
        return self._profile

    def readiness(self) -> ReadinessReceipt:
        return self._readiness

    def evidence(self, capability: Capability) -> CapabilityEvidence | None:
        return self._evidence.get(capability)

    def attempt(self, request: ModelRequest) -> AttemptReceipt:
        if self._executor is None:
            return AttemptReceipt.failed(
                self.profile, request, FailureKind.NOT_RUNTIME_WIRED,
                attempted=False,
            )
        return self._executor(request)


@dataclass(frozen=True)
class WalletReceipt:
    request: ModelRequest
    result: NeutralModelResult | None
    attempts: tuple[AttemptReceipt, ...]

    @property
    def succeeded(self) -> bool:
        return self.result is not None


class ModelWallet:
    """Deterministic selection and receipt-gated sequential failover."""

    def __init__(self, providers: Iterable[ModelProvider]):
        self._providers = tuple(providers)
        if any(not isinstance(provider, ModelProvider)
               for provider in self._providers):
            raise TypeError("providers must implement ModelProvider")
        if any(not isinstance(provider.profile, ProviderProfile)
               for provider in self._providers):
            raise ProviderContractError("provider returned an invalid profile")
        profile_ids = [provider.profile.provider_id
                       for provider in self._providers]
        identities = [provider.profile.identity for provider in self._providers]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("provider ids must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("model identities must be unique")

    def _eligible(
        self, request: ModelRequest,
    ) -> tuple[tuple[ModelProvider, CapabilityEvidence], ...]:
        eligible = []
        for provider in self._providers:
            profile = provider.profile
            if request.capability not in profile.capabilities:
                continue
            readiness = provider.readiness()
            if not isinstance(readiness, ReadinessReceipt):
                raise ProviderContractError("provider returned invalid readiness")
            if readiness.identity != profile.identity:
                raise ProviderContractError("readiness identity does not match profile")
            if (readiness.state != ReadinessState.READY
                    or not readiness.revision_verified):
                continue
            evidence = provider.evidence(request.capability)
            if evidence is None:
                continue
            if not isinstance(evidence, CapabilityEvidence):
                raise ProviderContractError("provider returned invalid evidence")
            if (evidence.identity != profile.identity
                    or evidence.capability != request.capability):
                raise ProviderContractError("evidence does not match profile")
            if (evidence.latency_upper_bound_ms <= request.latency_budget_ms
                    and evidence.quality_lower_bound_bps
                    >= request.quality_floor_bps):
                eligible.append((provider, evidence))
        return tuple(sorted(
            eligible,
            key=lambda candidate: (
                candidate[0].profile.preference_rank,
                -candidate[1].quality_lower_bound_bps,
                candidate[1].latency_upper_bound_ms,
                candidate[0].profile.provider_id,
                candidate[0].profile.identity.revision,
            ),
        ))

    def eligible_profiles(
            self, request: ModelRequest,
    ) -> tuple[ProviderProfile, ...]:
        """Return every eligible profile in deterministic, non-executing order."""
        return tuple(provider.profile for provider, _evidence in self._eligible(request))

    def select(self, request: ModelRequest) -> ProviderProfile | None:
        """Select exactly one provider without starting a model attempt."""
        eligible = self.eligible_profiles(request)
        return eligible[0] if eligible else None

    def execute(self, request: ModelRequest) -> WalletReceipt:
        """Run sequentially; only explicit failure receipts permit failover."""
        attempts = []
        for provider, _evidence in self._eligible(request):
            try:
                receipt = provider.attempt(request)
            except Exception as error:
                raise ProviderContractError(
                    "provider raised without an explicit failure receipt"
                ) from error
            if not isinstance(receipt, AttemptReceipt):
                raise ProviderContractError("provider returned no attempt receipt")
            profile = provider.profile
            if (receipt.provider_id != profile.provider_id
                    or receipt.identity != profile.identity
                    or receipt.request_id != request.request_id
                    or receipt.capability != request.capability):
                raise ProviderContractError("attempt receipt does not match selection")
            attempts.append(receipt)
            if receipt.state == AttemptState.SUCCEEDED:
                return WalletReceipt(request, receipt.result, tuple(attempts))
            # The typed failure receipt above is the only failover authority.
        return WalletReceipt(request, None, tuple(attempts))
