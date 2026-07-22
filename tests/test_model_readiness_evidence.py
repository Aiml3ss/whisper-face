# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import hashlib
import inspect
import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import model_readiness_evidence  # noqa: E402
from model_readiness_evidence import (  # noqa: E402
    MAX_MANIFEST_BYTES,
    AllModelReadinessReceipt,
    EvidenceReason,
    ReadinessPaths,
    collect_model_readiness,
    receipt_dict,
)
from model_wallet import (  # noqa: E402
    CURRENT_PROVIDER_PROFILES,
    PARAKEET_PROFILE,
    QWEN_CLEANUP_PROFILE,
    WHISPER_LARGE_TURBO_PROFILE,
    WHISPER_TINY_PROFILE,
    ModelIdentity,
    ProviderProfile,
    ReadinessState,
)


class ModelReadinessEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.paths = ReadinessPaths(
            root / "hf", root / "parakeet", root / "ollama")

    def _write(self, path, content=b"evidence"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _whisper(self, profile, weights):
        snapshot = (
            self.paths.huggingface_hub /
            f"models--{profile.identity.model_id.replace('/', '--')}" /
            "snapshots" / profile.identity.revision)
        self._write(snapshot / "config.json")
        self._write(snapshot / weights)

    def _parakeet(self, *, revision=None):
        revision = revision or PARAKEET_PROFILE.identity.revision
        required = (
            "parakeet_unified_encoder_int8.mlmodelc/weight.bin",
            "parakeet_unified_decoder.mlmodelc/weight.bin",
            "parakeet_unified_joint_decision_single_step.mlmodelc/weight.bin",
            "vocab.json",
            "metadata.json",
        )
        metadata = (
            self.paths.parakeet_model / ".cache" / "huggingface" /
            "download")
        for relative in required:
            self._write(self.paths.parakeet_model / relative)
            self._write(
                Path(f"{metadata / relative}.metadata"),
                f"{revision}\n".encode())

    def _qwen(self, content=b"exact local manifest"):
        manifest = (
            self.paths.ollama_models / "manifests" /
            "registry.ollama.ai" / "library" / "qwen3.5" / "4b")
        self._write(manifest, content)
        return content

    def test_missing_evidence_covers_every_pin_and_grants_no_authority(self):
        receipt = collect_model_readiness(self.paths)

        self.assertEqual(
            tuple(item.provider_id for item in receipt.providers),
            tuple(item.provider_id for item in CURRENT_PROVIDER_PROFILES))
        self.assertTrue(all(
            item.state == ReadinessState.NOT_INSTALLED
            for item in receipt.providers))
        self.assertFalse(receipt.all_pins_resolved)
        self.assertFalse(receipt.attempted_execution)
        self.assertFalse(receipt.attempted_download)
        self.assertEqual(receipt.runtime_authority, "none")

    def test_exact_whisper_and_parakeet_pins_are_resolved_not_ready(self):
        self._whisper(WHISPER_TINY_PROFILE, "weights.npz")
        self._whisper(
            WHISPER_LARGE_TURBO_PROFILE, "weights.safetensors")
        self._parakeet()

        receipt = collect_model_readiness(self.paths)
        by_id = {item.provider_id: item for item in receipt.providers}
        for profile in (
                PARAKEET_PROFILE, WHISPER_TINY_PROFILE,
                WHISPER_LARGE_TURBO_PROFILE):
            item = by_id[profile.provider_id]
            self.assertEqual(item.state, ReadinessState.RESOLVED)
            self.assertTrue(item.revision_verified)
            self.assertFalse(item.readiness_attested)
            self.assertFalse(item.capability_bounds_attested)
        self.assertEqual(
            by_id[QWEN_CLEANUP_PROFILE.provider_id].state,
            ReadinessState.NOT_INSTALLED)

    def test_alternate_whisper_snapshot_is_revision_mismatch(self):
        alternate = (
            self.paths.huggingface_hub /
            "models--mlx-community--whisper-tiny" / "snapshots" /
            ("0" * 40))
        self._write(alternate / "config.json")

        receipt = collect_model_readiness(self.paths)
        tiny = next(item for item in receipt.providers
                    if item.provider_id == WHISPER_TINY_PROFILE.provider_id)
        self.assertEqual(tiny.state, ReadinessState.REVISION_MISMATCH)
        self.assertEqual(tiny.reason, EvidenceReason.REVISION_MISMATCH)
        self.assertFalse(tiny.revision_verified)

    def test_parakeet_metadata_drift_fails_closed(self):
        self._parakeet(revision="0" * 40)

        receipt = collect_model_readiness(self.paths)
        parakeet = receipt.providers[0]
        self.assertEqual(parakeet.state, ReadinessState.REVISION_MISMATCH)
        self.assertEqual(parakeet.reason, EvidenceReason.REVISION_MISMATCH)
        self.assertFalse(parakeet.revision_verified)

    def test_qwen_manifest_is_bounded_and_must_match_exact_digest(self):
        content = self._qwen()
        receipt = collect_model_readiness(self.paths)
        qwen = receipt.providers[-1]
        expected = "sha256:" + hashlib.sha256(content).hexdigest()
        self.assertNotEqual(expected, QWEN_CLEANUP_PROFILE.identity.revision)
        self.assertEqual(qwen.state, ReadinessState.REVISION_MISMATCH)

        manifest = (
            self.paths.ollama_models / "manifests" /
            "registry.ollama.ai" / "library" / "qwen3.5" / "4b")
        manifest.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1))
        qwen = collect_model_readiness(self.paths).providers[-1]
        self.assertEqual(qwen.reason, EvidenceReason.INSPECTION_OUT_OF_BOUNDS)

    def test_exact_qwen_manifest_is_resolved_without_health_claim(self):
        content = self._qwen()
        identity = ModelIdentity(
            QWEN_CLEANUP_PROFILE.identity.model_id,
            "sha256:" + hashlib.sha256(content).hexdigest())
        fixture_profile = ProviderProfile(
            QWEN_CLEANUP_PROFILE.provider_id,
            identity,
            QWEN_CLEANUP_PROFILE.capabilities,
            QWEN_CLEANUP_PROFILE.preference_rank,
        )

        with patch.object(
                model_readiness_evidence, "QWEN_CLEANUP_PROFILE",
                fixture_profile):
            qwen = collect_model_readiness(self.paths).providers[-1]

        self.assertEqual(qwen.state, ReadinessState.RESOLVED)
        self.assertTrue(qwen.revision_verified)
        self.assertFalse(qwen.readiness_attested)
        self.assertFalse(qwen.capability_bounds_attested)

    def test_receipt_json_is_content_free_and_paths_are_never_disclosed(self):
        self._whisper(WHISPER_TINY_PROFILE, "weights.npz")
        report = receipt_dict(collect_model_readiness(self.paths))
        encoded = json.dumps(report)

        self.assertNotIn(self.temporary.name, encoded)
        self.assertNotIn("revision", encoded.replace(
            '"revision_verified"', ""))
        self.assertNotIn("model_id", encoded)
        self.assertFalse(report["attempted_execution"])
        self.assertFalse(report["attempted_download"])

    def test_contract_rejects_ready_or_mutable_receipts(self):
        item = collect_model_readiness(self.paths).providers[0]
        with self.assertRaises(FrozenInstanceError):
            item.state = ReadinessState.READY
        with self.assertRaisesRegex(ValueError, "cannot attest"):
            type(item)(
                item.provider_id, item.source, ReadinessState.READY,
                EvidenceReason.VERIFIED_EXACT_PIN, True, 1, 1, 1)
        with self.assertRaisesRegex(ValueError, "current pinned set"):
            AllModelReadinessReceipt(1, "wrong", (item,))

    def test_source_has_no_provider_execution_or_network_surface(self):
        source = inspect.getsource(model_readiness_evidence).lower()
        for forbidden in (
                "import subprocess", "import requests", "import urllib",
                "import socket",
                "snapshot_download", "ollama_chat", ".execute(",
                ".read_bytes(", ".read_text("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
