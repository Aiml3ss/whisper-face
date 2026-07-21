# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parrot_core import (  # noqa: E402
    Recognition,
    RecognitionWord,
    compile_cleanup,
    compile_code_dictation,
    confidence_from_segments,
    recognition_words_from_segments,
    correction_similarity,
    infer_revised_insertion,
    mode_from_modifiers,
    phonetic_key,
    rank_context_terms,
    recognition_prompt,
    should_start_speculation,
    can_reuse_speculation,
)


class ContextTests(unittest.TestCase):
    def test_ranks_identifiers_and_names_from_weighted_context(self):
        terms = rank_context_terms([
            ("Editing TranscriptionPipeline in Sipario", 3.0),
            ("the ordinary words should not dominate TranscriptionPipeline", 1.0),
            ("selected Qwen3_5 adapter", 4.0),
        ])
        self.assertEqual(terms[0], "Qwen3_5")
        self.assertIn("TranscriptionPipeline", terms[:3])
        self.assertIn("Sipario", terms)
        self.assertNotIn("ordinary", terms[:4])

    def test_prompt_deduplicates_global_and_ephemeral_terms(self):
        prompt = recognition_prompt(
            ["Qwen", "Whisper"], ["qwen", "Sipario"], max_terms=3)
        self.assertEqual(prompt, "Common terms: Qwen, Sipario, Whisper.")


class CleanupCompilerTests(unittest.TestCase):
    def test_compiles_fillers_structure_and_correction(self):
        plan = compile_cleanup(
            "um Tuesday actually Wednesday new paragraph I mean, ship it")
        self.assertEqual(plan.text, "Wednesday\n\nship it")
        self.assertEqual(
            plan.edit_kinds,
            ["spoken_structure", "remove_filler",
             "remove_discourse_filler", "self_correction"],
        )

    def test_scratch_that_removes_only_the_latest_clause(self):
        plan = compile_cleanup(
            "Keep this sentence. remove this part scratch that use this instead")
        self.assertEqual(plan.text, "Keep this sentence. use this instead")

    def test_enumeration_requests_semantic_cleanup(self):
        self.assertTrue(compile_cleanup(
            "three things first speed second trust third privacy"
        ).needs_semantic_cleanup)

    def test_code_mode_compiles_spoken_tokens(self):
        plan = compile_code_dictation(
            "result equals parse open paren payload close paren semicolon")
        self.assertEqual(plan.text, "result = parse(payload);")
        self.assertIn("spoken_code_token", plan.edit_kinds)


class CorrectionTests(unittest.TestCase):
    def test_isolates_revision_inside_exact_inserted_range(self):
        revised = infer_revised_insertion(
            "Hello  world", (6, 0), "Gwen is fast", "Hello Qwen is fast world")
        self.assertEqual(revised, "Qwen is fast")

    def test_ignores_typing_appended_after_an_unchanged_paste(self):
        revised = infer_revised_insertion(
            "Draft: ", (7, 0), "ship today", "Draft: ship today and notify me")
        self.assertIsNone(revised)

    def test_rejects_changes_outside_the_inserted_range(self):
        revised = infer_revised_insertion(
            "Hello  world", (6, 0), "Gwen", "Hallo Qwen world")
        self.assertIsNone(revised)

    def test_phonetic_similarity_catches_common_asr_confusion(self):
        self.assertTrue(phonetic_key("Qwen"))
        self.assertGreater(correction_similarity("Gwen", "Qwen"), 0.6)


class RecognitionTests(unittest.TestCase):
    def test_recognition_retains_its_engine(self):
        self.assertEqual(Recognition("hello", engine="tiny").engine, "tiny")

    def test_recognition_retains_word_evidence(self):
        words = recognition_words_from_segments([{
            "text": "hello world",
            "start": 1.0,
            "end": 2.0,
            "avg_logprob": -0.1,
        }])
        self.assertEqual([word.text for word in words], ["hello", "world"])
        self.assertEqual((words[0].start, words[1].end), (1.0, 2.0))
        self.assertIsInstance(words[0], RecognitionWord)
        self.assertTrue(all(word.timing == "segment" for word in words))

    def test_sdk_word_confidence_wins_over_segment_interpolation(self):
        words = recognition_words_from_segments([{
            "text": "Qwen",
            "avg_logprob": -1.0,
            "words": [{
                "word": "Qwen", "start": 0.1, "end": 0.4,
                "probability": 0.97,
            }],
        }])
        self.assertEqual(words[0].text, "Qwen")
        self.assertAlmostEqual(words[0].confidence, 0.97)
        self.assertEqual(words[0].timing, "native")

    def test_confidence_is_weighted_and_bounded(self):
        score = confidence_from_segments([
            {"text": "high confidence words", "avg_logprob": -0.1},
            {"text": "uncertain", "avg_logprob": -1.5},
        ])
        self.assertGreater(score, 0.5)
        self.assertLessEqual(score, 1.0)

    def test_modifier_contracts_are_unambiguous(self):
        self.assertEqual(mode_from_modifiers(False, False, False), "capture")
        self.assertEqual(mode_from_modifiers(True, False, False), "compose")
        self.assertEqual(mode_from_modifiers(False, True, False), "edit")
        self.assertEqual(mode_from_modifiers(False, False, True), "reply")
        self.assertEqual(mode_from_modifiers(False, True, True), "command")
        self.assertEqual(mode_from_modifiers(True, False, True), "code")

    def test_speculation_starts_on_a_real_pause_and_is_invalidated_by_growth(self):
        self.assertTrue(should_start_speculation(
            True, 16_000, 4_000, 16_000, False))
        self.assertFalse(should_start_speculation(
            True, 16_000, 2_000, 16_000, False))
        self.assertTrue(can_reuse_speculation(True, False, 8_000, 8_000))
        self.assertFalse(can_reuse_speculation(True, True, 8_000, 8_000))
        self.assertFalse(can_reuse_speculation(True, False, 8_000, 9_000))


if __name__ == "__main__":
    unittest.main()
