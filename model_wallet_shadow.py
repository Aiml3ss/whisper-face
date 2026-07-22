"""Transcript-free, non-executing readiness projection for ``ModelWallet``.

This module is intentionally unwired.  A caller supplies only its current
model readiness and bounded capability evidence; the adapter binds that
evidence to the exact pinned profiles in :mod:`model_wallet` and emits an
advisory receipt.  It never imports the runtime, discovers models, starts a
provider, routes text, or performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from model_wallet import (
    CURRENT_PROVIDER_PROFILES,
    Capability,
    CapabilityEvidence,
    ModelRequest,
    ModelWallet,
    PinnedLocalProviderAdapter,
    ProviderProfile,
    ReadinessReceipt,
    ReadinessState,
)


SHADOW_RECEIPT_SCHEMA_VERSION = 1
_PROFILES_BY_ID = {
    profile.provider_id: profile for profile in CURRENT_PROVIDER_PROFILES
}


class AdvisoryEligibility(str, Enum):
    """Closed, content-free reason for a shadow eligibility outcome."""

    ELIGIBLE = "eligible"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MISSING_RUNTIME_EVIDENCE = "missing_runtime_evidence"
    NOT_READY = "not_ready"
    MISSING_CAPABILITY_EVIDENCE = "missing_capability_evidence"
    OUTSIDE_REQUEST_BOUNDS = "outside_request_bounds"


@dataclass(frozen=True)
class RuntimeCapabilityEvidence:
    """Caller-observed bounded evidence for exactly one model capability."""

    capability: Capability
    latency_upper_bound_ms: int
    quality_lower_bound_bps: int
    sample_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid runtime evidence capability")
        # Delegate all numeric range checks to the wallet's typed contract.
        CapabilityEvidence(
            CURRENT_PROVIDER_PROFILES[0].identity,
            self.capability,
            self.latency_upper_bound_ms,
            self.quality_lower_bound_bps,
            self.sample_count,
        )


@dataclass(frozen=True)
class RuntimeModelEvidence:
    """Current, caller-supplied evidence for one exact pinned provider.

    No path, exception, transcript, or model output is accepted at this
    boundary.  An absent provider record is deliberately represented by its
    absence from the input iterable, not by a fabricated readiness state.
    """

    provider_id: str
    state: ReadinessState
    revision_verified: bool
    capability_evidence: RuntimeCapabilityEvidence | None = None

    def __post_init__(self) -> None:
        if self.provider_id not in _PROFILES_BY_ID:
            raise ValueError("runtime evidence provider is not a current pin")
        if not isinstance(self.state, ReadinessState):
            raise ValueError("invalid runtime readiness state")
        if not isinstance(self.revision_verified, bool):
            raise ValueError("revision_verified must be a boolean")
        if (self.capability_evidence is not None
                and not isinstance(
                    self.capability_evidence, RuntimeCapabilityEvidence)):
            raise ValueError("invalid runtime capability evidence")
        profile = _PROFILES_BY_ID[self.provider_id]
        if (self.capability_evidence is not None
                and self.capability_evidence.capability not in
                profile.capabilities):
            raise ValueError("runtime evidence capability does not match pin")
        # This also preserves the wallet's READY/revision invariants.
        ReadinessReceipt(profile.identity, self.state, self.revision_verified)


@dataclass(frozen=True)
class ShadowProviderReceipt:
    """One content-free advisory outcome for a current pinned provider."""

    provider_id: str
    capability: Capability
    eligibility: AdvisoryEligibility

    def __post_init__(self) -> None:
        if self.provider_id not in _PROFILES_BY_ID:
            raise ValueError("receipt provider is not a current pin")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid receipt capability")
        if not isinstance(self.eligibility, AdvisoryEligibility):
            raise ValueError("invalid advisory eligibility")


@dataclass(frozen=True)
class ModelWalletShadowReceipt:
    """Closed receipt for a non-executing provider-ordering advisory."""

    schema_version: int
    request_id: str
    capability: Capability
    providers: tuple[ShadowProviderReceipt, ...]
    advisory_order: tuple[str, ...]
    selected_provider_id: str | None
    fail_closed: bool
    attempted: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported shadow receipt schema")
        # ModelRequest keeps the same strict identifier and capability checks.
        ModelRequest(self.request_id, self.capability, 1, 0)
        expected_ids = tuple(profile.provider_id
                             for profile in CURRENT_PROVIDER_PROFILES)
        if (not isinstance(self.providers, tuple)
                or any(not isinstance(item, ShadowProviderReceipt)
                       for item in self.providers)
                or tuple(item.provider_id for item in self.providers)
                != expected_ids):
            raise ValueError("receipt providers must be the current pinned set")
        for item in self.providers:
            profile = _PROFILES_BY_ID[item.provider_id]
            if item.capability != self.capability:
                raise ValueError("provider receipt capability must match request")
            supported = self.capability in profile.capabilities
            if ((item.eligibility == AdvisoryEligibility.UNSUPPORTED_CAPABILITY)
                    != (not supported)):
                raise ValueError(
                    "provider eligibility must match pinned capabilities")
        if (not isinstance(self.advisory_order, tuple)
                or len(set(self.advisory_order)) != len(self.advisory_order)
                or any(provider_id not in _PROFILES_BY_ID
                       for provider_id in self.advisory_order)):
            raise ValueError("invalid advisory provider order")
        eligible_ids = tuple(
            item.provider_id for item in self.providers
            if item.eligibility == AdvisoryEligibility.ELIGIBLE)
        if set(self.advisory_order) != set(eligible_ids):
            raise ValueError("advisory order must contain exactly eligible providers")
        eligible_profiles = tuple(
            _PROFILES_BY_ID[provider_id] for provider_id in eligible_ids)
        preference_ranks = tuple(
            profile.preference_rank for profile in eligible_profiles)
        if len(preference_ranks) != len(set(preference_ranks)):
            # The receipt intentionally omits numeric capability evidence. A
            # future same-capability rank tie would therefore be impossible to
            # validate against ModelWallet's evidence-based tie breakers.
            raise ValueError("shadow receipt cannot validate tied provider ranks")
        canonical_order = tuple(
            profile.provider_id for profile in sorted(
                eligible_profiles,
                key=lambda profile: (
                    profile.preference_rank,
                    profile.provider_id,
                    profile.identity.revision,
                ),
            )
        )
        if self.advisory_order != canonical_order:
            raise ValueError("advisory provider order is not canonical")
        if self.selected_provider_id != (
                self.advisory_order[0] if self.advisory_order else None):
            raise ValueError("selected provider must lead advisory order")
        if (not isinstance(self.fail_closed, bool)
                or not isinstance(self.attempted, bool)
                or self.attempted
                or self.fail_closed != (not self.advisory_order)):
            raise ValueError("shadow receipt must be non-executing and fail closed")


def readiness_adapters(
        observations: Iterable[RuntimeModelEvidence],
) -> tuple[PinnedLocalProviderAdapter, ...]:
    """Convert supplied evidence into typed, executor-free wallet adapters."""
    items = tuple(observations)
    if any(not isinstance(item, RuntimeModelEvidence) for item in items):
        raise TypeError("observations must contain RuntimeModelEvidence values")
    observed_ids = [item.provider_id for item in items]
    if len(observed_ids) != len(set(observed_ids)):
        raise ValueError("duplicate runtime provider evidence")
    by_id = {item.provider_id: item for item in items}
    adapters = []
    for profile in CURRENT_PROVIDER_PROFILES:
        observation = by_id.get(profile.provider_id)
        if observation is None:
            continue
        typed_evidence = () if observation.capability_evidence is None else (
            CapabilityEvidence(
                profile.identity,
                observation.capability_evidence.capability,
                observation.capability_evidence.latency_upper_bound_ms,
                observation.capability_evidence.quality_lower_bound_bps,
                observation.capability_evidence.sample_count,
            ),
        )
        adapters.append(PinnedLocalProviderAdapter(
            profile,
            ReadinessReceipt(
                profile.identity, observation.state,
                observation.revision_verified),
            typed_evidence,
        ))
    return tuple(adapters)


def assess_model_wallet(
        request: ModelRequest,
        observations: Iterable[RuntimeModelEvidence],
) -> ModelWalletShadowReceipt:
    """Return advisory eligibility and order without a model attempt."""
    if not isinstance(request, ModelRequest):
        raise TypeError("request must be a ModelRequest")
    items = tuple(observations)
    adapters = readiness_adapters(items)
    by_id = {item.provider_id: item for item in items}
    wallet = ModelWallet(adapters)
    # The public eligibility API only reads typed receipts and evidence; it
    # has no attempt path and keeps advisory ordering identical to the policy.
    ordered_ids = tuple(
        profile.provider_id for profile in wallet.eligible_profiles(request))
    provider_receipts = []
    for profile in CURRENT_PROVIDER_PROFILES:
        observation = by_id.get(profile.provider_id)
        if request.capability not in profile.capabilities:
            eligibility = AdvisoryEligibility.UNSUPPORTED_CAPABILITY
        elif observation is None:
            eligibility = AdvisoryEligibility.MISSING_RUNTIME_EVIDENCE
        elif (observation.state != ReadinessState.READY
              or not observation.revision_verified):
            eligibility = AdvisoryEligibility.NOT_READY
        elif observation.capability_evidence is None:
            eligibility = AdvisoryEligibility.MISSING_CAPABILITY_EVIDENCE
        elif profile.provider_id in ordered_ids:
            eligibility = AdvisoryEligibility.ELIGIBLE
        else:
            eligibility = AdvisoryEligibility.OUTSIDE_REQUEST_BOUNDS
        provider_receipts.append(ShadowProviderReceipt(
            profile.provider_id, request.capability, eligibility))
    return ModelWalletShadowReceipt(
        SHADOW_RECEIPT_SCHEMA_VERSION,
        request.request_id,
        request.capability,
        tuple(provider_receipts),
        ordered_ids,
        ordered_ids[0] if ordered_ids else None,
        fail_closed=not ordered_ids,
    )
