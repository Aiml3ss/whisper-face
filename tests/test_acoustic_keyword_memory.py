import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acoustic_keyword_memory import (  # noqa: E402
    MIN_CONFIRMATIONS,
    MIN_OBSERVATIONS,
    RECOGNITION_EFFECT,
    AcousticKeywordMemory,
    hash_app_scope,
)


class AcousticKeywordMemoryTests(unittest.TestCase):
    def test_one_off_and_repeated_observation_cannot_activate_without_confirmation(self):
        memory = AcousticKeywordMemory()

        first = memory.observe("Qwen", evidence_id="utterance-1")
        duplicate = memory.observe("Qwen", evidence_id="utterance-1")
        for index in range(2, MIN_OBSERVATIONS + 1):
            candidate = memory.observe(
                "Qwen", evidence_id=f"utterance-{index}")

        self.assertEqual(first.observations, 1)
        self.assertFalse(first.eligible)
        self.assertEqual(duplicate.observations, 1)
        self.assertEqual(candidate.observations, MIN_OBSERVATIONS)
        self.assertEqual(candidate.confirmations, 0)
        self.assertFalse(candidate.eligible)

    def test_eligibility_requires_distinct_observations_and_confirmations(self):
        memory = AcousticKeywordMemory()
        for index in range(MIN_OBSERVATIONS):
            memory.observe("GraphRAG", evidence_id=f"heard-{index}")

        first = memory.confirm("GraphRAG", evidence_id="confirm-1")
        duplicate = memory.confirm("GraphRAG", evidence_id="confirm-1")
        second = memory.confirm("GraphRAG", evidence_id="confirm-2")

        self.assertFalse(first.eligible)
        self.assertEqual(duplicate.confirmations, 1)
        self.assertTrue(second.eligible)
        self.assertEqual(second.confirmations, MIN_CONFIRMATIONS)
        self.assertEqual(
            second.status, "eligible-not-connected-to-recognition")

    def test_explicit_correction_counts_once_in_each_channel_and_is_idempotent(self):
        memory = AcousticKeywordMemory()

        first = memory.accept_explicit_correction(
            "Qwen", evidence_id="opaque-correction-1")
        duplicate = memory.accept_explicit_correction(
            "Qwen", evidence_id="opaque-correction-1")
        second = memory.accept_explicit_correction(
            "Qwen", evidence_id="opaque-correction-2")
        eligible = memory.accept_explicit_correction(
            "Qwen", evidence_id="opaque-correction-3")

        self.assertEqual((first.observations, first.confirmations), (1, 1))
        self.assertEqual(
            (duplicate.observations, duplicate.confirmations), (1, 1))
        self.assertEqual((second.observations, second.confirmations), (2, 2))
        self.assertEqual((eligible.observations, eligible.confirmations), (3, 2))
        self.assertTrue(eligible.eligible)
        encoded = memory.dumps()
        self.assertNotIn("opaque-correction", encoded)

    def test_hashed_app_scopes_are_salted_and_isolate_evidence(self):
        raw_app = "com.example.SecretProject"
        first_scope = hash_app_scope(raw_app, salt=b"a" * 16)
        second_scope = hash_app_scope(raw_app, salt=b"b" * 16)
        memory = AcousticKeywordMemory()

        memory.observe(
            "Codex", evidence_id="event-1", app_scope=first_scope)
        memory.observe(
            "Codex", evidence_id="event-2", app_scope=second_scope)

        self.assertNotEqual(first_scope, second_scope)
        self.assertRegex(first_scope, r"^app-[0-9a-f]{16}$")
        self.assertEqual(len(memory.candidates), 2)
        self.assertNotIn(raw_app, memory.dumps())
        with self.assertRaisesRegex(ValueError, "salted"):
            memory.observe(
                "Codex", evidence_id="event-3", app_scope=raw_app)

    def test_persistence_round_trip_is_strict_deterministic_and_text_history_free(self):
        memory = AcousticKeywordMemory(max_entries=4)
        scope = hash_app_scope("com.openai.codex", salt=b"private-salt-123")
        memory.observe(
            "Whisper Face", evidence_id="private-event-id", app_scope=scope)
        memory.confirm(
            "Whisper Face", evidence_id="private-confirm-id", app_scope=scope)

        encoded = memory.dumps()
        restored = AcousticKeywordMemory.loads(encoded)

        self.assertEqual(restored.dumps(), encoded)
        self.assertNotIn("private-event-id", encoded)
        self.assertNotIn("private-confirm-id", encoded)
        lowered = encoded.casefold()
        for forbidden in (
                "raw_audio", "audio_path", "transcript_history",
                "surrounding_context", "document_text"):
            self.assertNotIn(forbidden, lowered)
        decoded = json.loads(encoded)
        self.assertEqual(
            set(decoded["entries"][0]),
            {
                "keyword", "app_scope", "observation_tokens",
                "confirmation_tokens", "sequence",
            },
        )

    def test_unknown_versions_fields_and_malformed_evidence_fail_closed(self):
        memory = AcousticKeywordMemory()
        memory.observe("Qwen", evidence_id="event-1")
        payload = memory.to_dict()

        with self.assertRaisesRegex(ValueError, "unsupported.*schema"):
            AcousticKeywordMemory.from_dict({
                **payload, "schema_version": 999,
            })
        with self.assertRaisesRegex(ValueError, "unsupported.*schema"):
            AcousticKeywordMemory.from_dict({
                **payload, "schema_version": True,
            })
        with self.assertRaisesRegex(ValueError, "exactly"):
            AcousticKeywordMemory.from_dict({
                **payload, "transcript": "private text",
            })
        malformed = json.loads(memory.dumps())
        malformed["entries"][0]["observation_tokens"] = ["event-raw"]
        with self.assertRaisesRegex(ValueError, "observation token"):
            AcousticKeywordMemory.from_dict(malformed)
        malformed["entries"][0]["observation_tokens"] = [{}]
        with self.assertRaisesRegex(ValueError, "observation token"):
            AcousticKeywordMemory.from_dict(malformed)

    def test_explicit_export_is_inspectable_but_omits_evidence_tokens(self):
        memory = AcousticKeywordMemory()
        memory.observe("Qwen", evidence_id="event-1")

        exported = memory.export_dict()
        encoded = memory.export_json()

        self.assertEqual(
            exported["policy"]["recognition_effect"], RECOGNITION_EFFECT)
        self.assertEqual(exported["candidates"][0]["keyword"], "Qwen")
        self.assertEqual(exported["candidates"][0]["observations"], 1)
        self.assertNotIn("observation_tokens", encoded)
        self.assertNotIn("confirmation_tokens", encoded)
        self.assertNotIn("event-1", encoded)

    def test_forget_one_is_scope_exact_and_forget_all_clears_everything(self):
        memory = AcousticKeywordMemory()
        scope = hash_app_scope("com.openai.codex", salt=b"private-salt-123")
        memory.observe("Qwen", evidence_id="global", app_scope=None)
        memory.observe("Qwen", evidence_id="scoped", app_scope=scope)

        self.assertTrue(memory.forget("Qwen", app_scope=scope))
        self.assertFalse(memory.forget("Qwen", app_scope=scope))
        self.assertEqual(len(memory.candidates), 1)
        self.assertIsNone(memory.candidates[0].app_scope)
        self.assertEqual(memory.forget_all(), 1)
        self.assertEqual(memory.candidates, ())

    def test_cardinality_evicts_weak_oldest_deterministically(self):
        memory = AcousticKeywordMemory(max_entries=2)
        memory.observe("old", evidence_id="event-old")
        memory.observe("middle", evidence_id="event-middle")
        memory.observe("new", evidence_id="event-new")

        self.assertEqual(
            [candidate.keyword for candidate in memory.candidates],
            ["middle", "new"],
        )

        stronger = AcousticKeywordMemory(max_entries=2)
        for index in range(MIN_OBSERVATIONS):
            stronger.observe("keep", evidence_id=f"keep-o-{index}")
        for index in range(MIN_CONFIRMATIONS):
            stronger.confirm("keep", evidence_id=f"keep-c-{index}")
        stronger.observe("weak-old", evidence_id="weak-old")
        stronger.observe("weak-new", evidence_id="weak-new")

        candidates = {candidate.keyword: candidate
                      for candidate in stronger.candidates}
        self.assertEqual(set(candidates), {"keep", "weak-new"})
        self.assertTrue(candidates["keep"].eligible)

    def test_module_has_no_path_or_automatic_recognition_api(self):
        memory = AcousticKeywordMemory()

        self.assertFalse(hasattr(memory, "apply"))
        self.assertFalse(hasattr(memory, "bias"))
        self.assertFalse(hasattr(memory, "insert"))
        self.assertEqual(RECOGNITION_EFFECT, "none")


class AcousticKeywordMemoryInstallerContractTests(unittest.TestCase):
    def test_private_template_is_strict_empty_state(self):
        template = ROOT / "acoustic_keyword_memory.template.json"
        restored = AcousticKeywordMemory.loads(
            template.read_text(encoding="utf-8"))

        self.assertEqual(restored.candidates, ())
        self.assertEqual(restored.to_dict()["next_sequence"], 1)
        self.assertEqual(
            restored.to_dict()["policy"]["recognition_effect"], "none")

    def test_mac_and_windows_installers_create_preserve_and_restrict_state(self):
        shell = (ROOT / "setup.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "setup.ps1").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for installer in (shell, powershell):
            with self.subTest(installer="setup"):
                self.assertIn("acoustic_keyword_memory.py", installer)
                self.assertIn(
                    "acoustic_keyword_memory.template.json", installer)
        self.assertIn(
            "for name in snippets tones preferences acoustic_keyword_memory",
            shell,
        )
        self.assertIn('"acoustic_keyword_memory.json"', powershell)
        self.assertIn('[ -f "$destination" ] || install -m 600', shell)
        self.assertIn('chmod 600 "$destination"', shell)
        self.assertIn('if (-not (Test-Path $Destination))', powershell)
        self.assertIn("icacls $Destination /inheritance:r /grant:r", powershell)
        self.assertIn("acoustic_keyword_memory.json", gitignore.splitlines())


if __name__ == "__main__":
    unittest.main()
