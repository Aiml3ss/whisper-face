"""Bounded, read-only evidence for the current immutable local model pins.

The collector inspects only caller-selected local cache roots.  It never
downloads, imports a provider runtime, opens a socket, starts a model, or
creates capability bounds.  Exact local-pin resolution is deliberately weaker
than model readiness: every successful observation remains ``RESOLVED``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from model_wallet import (
    CURRENT_PROVIDER_PROFILES,
    PARAKEET_PROFILE,
    QWEN_CLEANUP_PROFILE,
    WHISPER_LARGE_TURBO_PROFILE,
    WHISPER_TINY_PROFILE,
    ReadinessState,
)


RECEIPT_SCHEMA_VERSION = 1
EVIDENCE_SCOPE = "local-pin-resolution-only"
RUNTIME_AUTHORITY = "none"
MAX_MANIFEST_BYTES = 1_000_000
MAX_METADATA_BYTES = 256
MAX_FILES_PER_ASSET = 128
MAX_ENTRIES_PER_ASSET = 512

_WHISPER_REQUIRED = {
    WHISPER_TINY_PROFILE.provider_id: ("config.json", "weights.npz"),
    WHISPER_LARGE_TURBO_PROFILE.provider_id: (
        "config.json", "weights.safetensors"),
}
_PARAKEET_REQUIRED = (
    "parakeet_unified_encoder_int8.mlmodelc",
    "parakeet_unified_decoder.mlmodelc",
    "parakeet_unified_joint_decision_single_step.mlmodelc",
    "vocab.json",
    "metadata.json",
)


class EvidenceSource(str, Enum):
    HUGGING_FACE_SNAPSHOT = "hugging_face_snapshot"
    PARAKEET_METADATA = "parakeet_metadata"
    OLLAMA_MANIFEST = "ollama_manifest"


class EvidenceReason(str, Enum):
    VERIFIED_EXACT_PIN = "verified_exact_pin"
    EVIDENCE_MISSING = "evidence_missing"
    REQUIRED_ASSETS_MISSING = "required_assets_missing"
    REVISION_MISMATCH = "revision_mismatch"
    INSPECTION_OUT_OF_BOUNDS = "inspection_out_of_bounds"
    INSPECTION_ERROR = "inspection_error"


@dataclass(frozen=True)
class ReadinessPaths:
    """Explicit roots; none of these paths are emitted in receipts."""

    huggingface_hub: Path
    parakeet_model: Path
    ollama_models: Path

    def __post_init__(self) -> None:
        for value in (
                self.huggingface_hub, self.parakeet_model,
                self.ollama_models):
            if not isinstance(value, Path):
                raise TypeError("readiness roots must be Path values")


@dataclass(frozen=True)
class ProviderReadinessEvidence:
    """Content-free local-pin evidence for one current provider."""

    provider_id: str
    source: EvidenceSource
    state: ReadinessState
    reason: EvidenceReason
    revision_verified: bool
    required_asset_count: int
    observed_asset_count: int
    metadata_records_checked: int
    readiness_attested: bool = False
    capability_bounds_attested: bool = False

    def __post_init__(self) -> None:
        current_ids = {item.provider_id for item in CURRENT_PROVIDER_PROFILES}
        if self.provider_id not in current_ids:
            raise ValueError("evidence provider is not a current pin")
        if (not isinstance(self.source, EvidenceSource)
                or not isinstance(self.state, ReadinessState)
                or not isinstance(self.reason, EvidenceReason)):
            raise ValueError("invalid readiness evidence category")
        if self.state == ReadinessState.READY:
            raise ValueError("filesystem evidence cannot attest model readiness")
        expected_verified = (
            self.state == ReadinessState.RESOLVED
            and self.reason == EvidenceReason.VERIFIED_EXACT_PIN)
        if self.revision_verified != expected_verified:
            raise ValueError("revision verification does not match evidence")
        for value in (
                self.required_asset_count, self.observed_asset_count,
                self.metadata_records_checked):
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0 or value > 100_000):
                raise ValueError("invalid evidence count")
        if self.observed_asset_count > self.required_asset_count:
            raise ValueError("observed assets exceed required assets")
        if self.readiness_attested or self.capability_bounds_attested:
            raise ValueError("local pin evidence grants no readiness authority")


@dataclass(frozen=True)
class AllModelReadinessReceipt:
    """Closed, non-executing receipt over every current provider pin."""

    schema_version: int
    scope: str
    providers: tuple[ProviderReadinessEvidence, ...]
    attempted_execution: bool = False
    attempted_download: bool = False
    runtime_authority: str = RUNTIME_AUTHORITY

    def __post_init__(self) -> None:
        expected_ids = tuple(
            profile.provider_id for profile in CURRENT_PROVIDER_PROFILES)
        if (self.schema_version != RECEIPT_SCHEMA_VERSION
                or self.scope != EVIDENCE_SCOPE
                or not isinstance(self.providers, tuple)
                or any(not isinstance(item, ProviderReadinessEvidence)
                       for item in self.providers)
                or tuple(item.provider_id for item in self.providers)
                != expected_ids):
            raise ValueError("receipt must cover the current pinned set")
        if (self.attempted_execution or self.attempted_download
                or self.runtime_authority != RUNTIME_AUTHORITY):
            raise ValueError("readiness receipt must be read-only and advisory")

    @property
    def all_pins_resolved(self) -> bool:
        return all(item.revision_verified for item in self.providers)


def default_paths() -> ReadinessPaths:
    home = Path.home()
    hf_root = Path(os.environ.get(
        "HF_HUB_CACHE",
        str(Path(os.environ.get("HF_HOME", str(home / ".cache" /
                                               "huggingface"))) / "hub"),
    ))
    ollama_root = Path(os.environ.get(
        "OLLAMA_MODELS", str(home / ".ollama" / "models")))
    return ReadinessPaths(
        hf_root,
        home / "Library" / "Application Support" / "FluidAudio" /
        "Models" / "parakeet-unified-en-0.6b",
        ollama_root,
    )


def _evidence(
    provider_id: str,
    source: EvidenceSource,
    state: ReadinessState,
    reason: EvidenceReason,
    *,
    required: int,
    observed: int = 0,
    metadata: int = 0,
) -> ProviderReadinessEvidence:
    return ProviderReadinessEvidence(
        provider_id, source, state, reason,
        state == ReadinessState.RESOLVED
        and reason == EvidenceReason.VERIFIED_EXACT_PIN,
        required, observed, metadata,
    )


def _bounded_file(path: Path, *, root: Path, allow_symlink: bool) -> bool:
    if path.is_symlink():
        if not allow_symlink:
            return False
        try:
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, ValueError):
            return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


class _InspectionBoundsError(Exception):
    pass


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise _InspectionBoundsError()
    if not content:
        raise OSError("empty evidence")
    return content


def _asset_files(target: Path) -> tuple[Path, ...]:
    """Enumerate one required asset with fixed traversal and file bounds."""
    if target.is_symlink():
        raise OSError("symbolic asset")
    if target.is_file():
        return (target,)
    if not target.is_dir():
        return ()
    files = []
    for entries, item in enumerate(target.rglob("*"), start=1):
        if entries > MAX_ENTRIES_PER_ASSET:
            raise _InspectionBoundsError()
        if item.is_symlink():
            raise OSError("symbolic asset member")
        if item.is_file():
            files.append(item)
            if len(files) > MAX_FILES_PER_ASSET:
                raise _InspectionBoundsError()
    return tuple(files)


def _whisper_evidence(
    profile_id: str,
    model_id: str,
    revision: str,
    hub_root: Path,
) -> ProviderReadinessEvidence:
    source = EvidenceSource.HUGGING_FACE_SNAPSHOT
    required = _WHISPER_REQUIRED[profile_id]
    model_root = hub_root / f"models--{model_id.replace('/', '--')}"
    snapshot_root = model_root / "snapshots"
    exact = snapshot_root / revision
    try:
        if not exact.is_dir():
            alternate = snapshot_root.is_dir() and any(
                item.is_dir() for item in snapshot_root.iterdir())
            return _evidence(
                profile_id, source,
                ReadinessState.REVISION_MISMATCH if alternate
                else ReadinessState.NOT_INSTALLED,
                EvidenceReason.REVISION_MISMATCH if alternate
                else EvidenceReason.EVIDENCE_MISSING,
                required=len(required),
            )
        if (model_root.is_symlink() or snapshot_root.is_symlink()
                or exact.is_symlink()):
            return _evidence(
                profile_id, source, ReadinessState.LOAD_FAILED,
                EvidenceReason.INSPECTION_ERROR, required=len(required))
        observed = sum(
            _bounded_file(exact / name, root=model_root, allow_symlink=True)
            for name in required)
    except OSError:
        return _evidence(
            profile_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.INSPECTION_ERROR, required=len(required))
    if observed != len(required):
        return _evidence(
            profile_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.REQUIRED_ASSETS_MISSING,
            required=len(required), observed=observed)
    return _evidence(
        profile_id, source, ReadinessState.RESOLVED,
        EvidenceReason.VERIFIED_EXACT_PIN,
        required=len(required), observed=observed)


def _parakeet_evidence(model_root: Path) -> ProviderReadinessEvidence:
    profile = PARAKEET_PROFILE
    source = EvidenceSource.PARAKEET_METADATA
    required_count = len(_PARAKEET_REQUIRED)
    if not model_root.is_dir():
        return _evidence(
            profile.provider_id, source, ReadinessState.NOT_INSTALLED,
            EvidenceReason.EVIDENCE_MISSING, required=required_count)
    metadata_root = model_root / ".cache" / "huggingface" / "download"
    observed = checked = 0
    try:
        for relative in _PARAKEET_REQUIRED:
            target = model_root / relative
            files = _asset_files(target)
            if not files or any(item.stat().st_size <= 0 for item in files):
                continue
            observed += 1
            for asset in files:
                record = Path(f"{metadata_root / asset.relative_to(model_root)}.metadata")
                if not record.is_file() or record.is_symlink():
                    return _evidence(
                        profile.provider_id, source, ReadinessState.LOAD_FAILED,
                        EvidenceReason.REQUIRED_ASSETS_MISSING,
                        required=required_count, observed=observed,
                        metadata=checked)
                revision = _read_bounded(
                    record, MAX_METADATA_BYTES).decode("utf-8").splitlines()[0]
                checked += 1
                if revision != profile.identity.revision:
                    return _evidence(
                        profile.provider_id, source,
                        ReadinessState.REVISION_MISMATCH,
                        EvidenceReason.REVISION_MISMATCH,
                        required=required_count, observed=observed,
                        metadata=checked)
    except _InspectionBoundsError:
        return _evidence(
            profile.provider_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.INSPECTION_OUT_OF_BOUNDS,
            required=required_count, observed=observed, metadata=checked)
    except (OSError, UnicodeError, IndexError, ValueError):
        return _evidence(
            profile.provider_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.INSPECTION_ERROR,
            required=required_count, observed=observed, metadata=checked)
    if observed != required_count:
        return _evidence(
            profile.provider_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.REQUIRED_ASSETS_MISSING,
            required=required_count, observed=observed, metadata=checked)
    return _evidence(
        profile.provider_id, source, ReadinessState.RESOLVED,
        EvidenceReason.VERIFIED_EXACT_PIN,
        required=required_count, observed=observed, metadata=checked)


def _qwen_evidence(models_root: Path) -> ProviderReadinessEvidence:
    profile = QWEN_CLEANUP_PROFILE
    source = EvidenceSource.OLLAMA_MANIFEST
    manifest = (
        models_root / "manifests" / "registry.ollama.ai" / "library" /
        "qwen3.5" / "4b")
    if not manifest.exists():
        return _evidence(
            profile.provider_id, source, ReadinessState.NOT_INSTALLED,
            EvidenceReason.EVIDENCE_MISSING, required=1)
    try:
        if manifest.is_symlink() or not manifest.is_file():
            return _evidence(
                profile.provider_id, source, ReadinessState.LOAD_FAILED,
                EvidenceReason.INSPECTION_ERROR, required=1)
        digest = "sha256:" + hashlib.sha256(
            _read_bounded(manifest, MAX_MANIFEST_BYTES)).hexdigest()
    except _InspectionBoundsError:
        return _evidence(
            profile.provider_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.INSPECTION_OUT_OF_BOUNDS, required=1)
    except OSError:
        return _evidence(
            profile.provider_id, source, ReadinessState.LOAD_FAILED,
            EvidenceReason.INSPECTION_ERROR, required=1)
    if digest != profile.identity.revision:
        return _evidence(
            profile.provider_id, source, ReadinessState.REVISION_MISMATCH,
            EvidenceReason.REVISION_MISMATCH, required=1, observed=1,
            metadata=1)
    return _evidence(
        profile.provider_id, source, ReadinessState.RESOLVED,
        EvidenceReason.VERIFIED_EXACT_PIN,
        required=1, observed=1, metadata=1)


def collect_model_readiness(
        paths: ReadinessPaths | None = None) -> AllModelReadinessReceipt:
    """Inspect every current pin without provider execution or downloads."""
    roots = paths or default_paths()
    by_id = {
        PARAKEET_PROFILE.provider_id: _parakeet_evidence(
            roots.parakeet_model),
        WHISPER_TINY_PROFILE.provider_id: _whisper_evidence(
            WHISPER_TINY_PROFILE.provider_id,
            WHISPER_TINY_PROFILE.identity.model_id,
            WHISPER_TINY_PROFILE.identity.revision,
            roots.huggingface_hub),
        WHISPER_LARGE_TURBO_PROFILE.provider_id: _whisper_evidence(
            WHISPER_LARGE_TURBO_PROFILE.provider_id,
            WHISPER_LARGE_TURBO_PROFILE.identity.model_id,
            WHISPER_LARGE_TURBO_PROFILE.identity.revision,
            roots.huggingface_hub),
        QWEN_CLEANUP_PROFILE.provider_id: _qwen_evidence(
            roots.ollama_models),
    }
    return AllModelReadinessReceipt(
        RECEIPT_SCHEMA_VERSION,
        EVIDENCE_SCOPE,
        tuple(by_id[profile.provider_id]
              for profile in CURRENT_PROVIDER_PROFILES),
    )


def receipt_dict(receipt: AllModelReadinessReceipt) -> dict[str, object]:
    """Return the closed JSON shape without paths, revisions, or model text."""
    return {
        "schema_version": receipt.schema_version,
        "scope": receipt.scope,
        "providers": [{
            "provider_id": item.provider_id,
            "source": item.source.value,
            "state": item.state.value,
            "reason": item.reason.value,
            "revision_verified": item.revision_verified,
            "required_asset_count": item.required_asset_count,
            "observed_asset_count": item.observed_asset_count,
            "metadata_records_checked": item.metadata_records_checked,
            "readiness_attested": item.readiness_attested,
            "capability_bounds_attested": item.capability_bounds_attested,
        } for item in receipt.providers],
        "all_pins_resolved": receipt.all_pins_resolved,
        "attempted_execution": receipt.attempted_execution,
        "attempted_download": receipt.attempted_download,
        "runtime_authority": receipt.runtime_authority,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    report = receipt_dict(collect_model_readiness())
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        for item in report["providers"]:
            print(f"{item['provider_id']}: {item['state']} ({item['reason']})")
        print("Runtime authority: none. Models were not executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
