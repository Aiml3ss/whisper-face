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
    EDIT_COMMAND_UNDO,
    EDIT_COMMAND_DELETE_WORD,
    EDIT_COMMAND_DELETE_SENTENCE,
    EDIT_COMMAND_NEWLINE,
    EDIT_COMMAND_NEWPARAGRAPH,
    EDIT_COMMAND_UPPERCASE_LAST,
    EDIT_COMMAND_CAPITALIZE_LAST,
    EDIT_COMMAND_LOWERCASE_LAST,
    classify_edit_command,
    compile_cleanup,
    compile_code_dictation,
    hypothesis_agreement,
    parakeet_confidence_from_agreement,
    should_escalate_uncertain,
    normalize_spacing,
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

    def test_scratch_that_preserves_a_repeated_boundary_and_factual_prefix(self):
        plan = compile_cleanup(
            "tell Morgan the migration starts at six scratch that at seven")
        self.assertEqual(
            plan.text, "tell Morgan the migration starts at seven")
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_ambiguous_scratch_that_preserves_source_and_fails_closed(self):
        raw = "move the meeting Tuesday scratch that Wednesday"
        plan = compile_cleanup(raw)
        self.assertEqual(plan.text, raw)
        self.assertNotIn("scratch_that", plan.edit_kinds)
        self.assertTrue(plan.needs_semantic_cleanup)

    def test_classify_edit_command_matches_each_closed_phrase(self):
        cases = {
            "scratch that": EDIT_COMMAND_UNDO,
            "undo that": EDIT_COMMAND_UNDO,
            "undo": EDIT_COMMAND_UNDO,
            "delete last word": EDIT_COMMAND_DELETE_WORD,
            "delete last sentence": EDIT_COMMAND_DELETE_SENTENCE,
            "delete that": EDIT_COMMAND_DELETE_SENTENCE,
            "new line": EDIT_COMMAND_NEWLINE,
            "new paragraph": EDIT_COMMAND_NEWPARAGRAPH,
            "all caps": EDIT_COMMAND_UPPERCASE_LAST,
            "uppercase that": EDIT_COMMAND_UPPERCASE_LAST,
            "capitalize that": EDIT_COMMAND_CAPITALIZE_LAST,
            "lowercase that": EDIT_COMMAND_LOWERCASE_LAST,
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(classify_edit_command(phrase), expected)

    def test_classify_edit_command_normalizes_like_execute_voice_command(self):
        # Casefold, strip everything but a-z and spaces, then trim the ends.
        self.assertEqual(
            classify_edit_command("Scratch that."), EDIT_COMMAND_UNDO)
        self.assertEqual(
            classify_edit_command("  NEW LINE  "), EDIT_COMMAND_NEWLINE)

    def test_classify_edit_command_only_fires_on_the_whole_utterance(self):
        for sentence in (
            "lets scratch that plan",
            "please undo the migration",
            "start a new line of thinking",
            "delete last word from the report",
            "capitalize that first heading",
        ):
            with self.subTest(sentence=sentence):
                self.assertIsNone(classify_edit_command(sentence))

    def test_classify_edit_command_rejects_empty_and_garbage(self):
        for junk in ("", "   ", "!!!", "12345", "hello world", None):
            with self.subTest(junk=junk):
                self.assertIsNone(classify_edit_command(junk))

    def test_enumeration_requests_semantic_cleanup(self):
        self.assertTrue(compile_cleanup(
            "three things first speed second trust"
        ).needs_semantic_cleanup)

    def test_single_ordinal_in_ordinary_prose_stays_deterministic(self):
        raw = (
            "The second thing regarding the audio is that the microphone "
            "should remain warm between dictations."
        )
        plan = compile_cleanup(raw)

        self.assertEqual(plan.text, raw)
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_explicit_list_introductions_request_semantic_cleanup(self):
        for spoken in (
            "here's a list of ideas that I have improve speed and keep trust",
            "here are some feedback items make it scannable and keep it short",
            "okay here are a few ideas improve speed and preserve privacy",
            "I have a few points reliability matters and privacy matters",
            "let me list out some things improve speed and preserve privacy",
        ):
            with self.subTest(spoken=spoken):
                self.assertTrue(
                    compile_cleanup(spoken).needs_semantic_cleanup)

    def test_repeated_numbered_markers_format_without_semantic_cleanup(self):
        spoken = (
            "The two things and feedback still does not list out items here, "
            "right? So listing, here's one as a test, and here's two as a "
            "test."
        )

        plan = compile_cleanup(spoken)

        self.assertEqual(
            plan.text,
            "The two things and feedback still does not list out items here, "
            "right? So listing:\n"
            "- Here's one as a test.\n"
            "- Here's two as a test.",
        )
        self.assertIn("spoken_enumeration", plan.edit_kinds)
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_exact_counted_inline_list_formats_without_semantic_cleanup(self):
        plan = compile_cleanup(
            "three items first freeze API v4 second cap spend at $900 "
            "and third email QA")
        self.assertEqual(
            plan.text,
            "three items:\n"
            "- Freeze API v4.\n"
            "- Cap spend at $900.\n"
            "- Email QA.",
        )
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_counted_inline_list_requires_exact_count_and_sequence(self):
        for spoken in (
            "three items first preserve names and second preserve dates",
            "two things second skip the first marker",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertTrue(plan.needs_semantic_cleanup)

    def test_counted_inline_list_preserves_names_urls_code_and_numbers(self):
        plan = compile_cleanup(
            "two things first notify Morgan at https://example.com/api "
            "and second set RETRY_COUNT to 3")
        self.assertEqual(
            plan.text,
            "two things:\n"
            "- Notify Morgan at https://example.com/api.\n"
            "- Set RETRY_COUNT to 3.",
        )
        for anchor in (
                "Morgan", "https://example.com/api", "RETRY_COUNT", "3"):
            self.assertIn(anchor, plan.text)
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_spoken_structure_does_not_become_an_ordinal_list_request(self):
        plan = compile_cleanup(
            "first line is approved new line second line needs revision")
        self.assertEqual(
            plan.text, "first line is approved\nsecond line needs revision")
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_literal_ambiguous_filler_phrases_are_preserved_without_qwen(self):
        for spoken in (
            "the phrase you know appears in the customer quote",
            "the phrase I mean should remain in the quotation",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertFalse(plan.needs_semantic_cleanup)
        self.assertTrue(compile_cleanup(
            "check the logs you know before deploy").needs_semantic_cleanup)

    def test_plain_numbered_feedback_items_become_bullets(self):
        plan = compile_cleanup(
            "Here's some feedback items. One, this is great. Two, this is "
            "not so great."
        )

        self.assertEqual(
            plan.text,
            "Here's some feedback items:\n"
            "- This is great.\n"
            "- This is not so great.",
        )
        self.assertIn("spoken_enumeration", plan.edit_kinds)
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_plain_numbered_list_accepts_comma_item_boundaries(self):
        plan = compile_cleanup("Let's make a list. One high, two by.")

        self.assertEqual(
            plan.text,
            "Let's make a list:\n"
            "- High.\n"
            "- By.",
        )
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_plain_numbered_list_accepts_mixed_cardinal_ordinals(self):
        plan = compile_cleanup("Two things. One test. Second, test.")

        self.assertEqual(
            plan.text,
            "Two things:\n"
            "- Test.\n"
            "- Test.",
        )
        self.assertIn("spoken_enumeration", plan.edit_kinds)
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_repeated_number_words_without_list_intent_stay_as_prose(self):
        for spoken in (
            "Here's one reason I stayed, and here's two tickets tomorrow",
            "I put the list on the shelf. One day, two people arrived.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertNotIn("spoken_enumeration", plan.edit_kinds)

    def test_ordinary_reference_to_a_list_stays_on_the_fast_path(self):
        self.assertFalse(compile_cleanup(
            "I sent the contractor a list of ideas yesterday"
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
        recognition = Recognition(
            "hello", engine="parakeet-unified", native_processing_s=0.02)

        self.assertEqual(recognition.engine, "parakeet-unified")
        self.assertEqual(recognition.native_processing_s, 0.02)
        self.assertIsNone(Recognition("hello").native_processing_s)

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


class AgreementConfidenceTests(unittest.TestCase):
    def test_agreement_counts_every_token_case_insensitively(self):
        self.assertEqual(hypothesis_agreement(
            "Ship it to Berg", "ship it to berg"), 1.0)
        self.assertEqual(hypothesis_agreement("", ""), 1.0)
        self.assertEqual(hypothesis_agreement("something", ""), 0.0)
        self.assertEqual(hypothesis_agreement(
            "alpha beta gamma delta", "epsilon zeta eta theta"), 0.0)

    def test_agreement_penalizes_insertions_not_just_substitutions(self):
        # Same matched words, but the longer hypothesis added two tokens.
        partial = hypothesis_agreement(
            "send the deposit", "send the full deposit today")
        self.assertAlmostEqual(partial, 3 / 5)

    def test_short_words_and_numbers_count_toward_disagreement(self):
        heard_two = hypothesis_agreement(
            "pay 2 dollars now", "pay 10 dollars now")
        self.assertLess(heard_two, 1.0)

    def test_confidence_map_crosses_the_runtime_gates_where_documented(self):
        self.assertAlmostEqual(parakeet_confidence_from_agreement(1.0), 0.93)
        self.assertAlmostEqual(parakeet_confidence_from_agreement(0.0), 0.45)
        # Context-candidate repair unlocks below 0.70.
        self.assertGreater(parakeet_confidence_from_agreement(0.6), 0.70)
        self.assertLess(parakeet_confidence_from_agreement(0.4), 0.70)
        # The low-confidence region below 0.52 needs severe disagreement.
        self.assertLess(parakeet_confidence_from_agreement(0.1), 0.52)
        self.assertGreater(parakeet_confidence_from_agreement(0.2), 0.52)
        # Out-of-range inputs stay bounded.
        self.assertAlmostEqual(parakeet_confidence_from_agreement(1.7), 0.93)
        self.assertAlmostEqual(parakeet_confidence_from_agreement(-0.5), 0.45)

    def test_escalation_needs_bad_agreement_and_bounded_audio(self):
        self.assertTrue(should_escalate_uncertain(0.2, 5.0))
        self.assertFalse(should_escalate_uncertain(0.5, 5.0))
        self.assertFalse(should_escalate_uncertain(0.2, 30.0))
        self.assertFalse(should_escalate_uncertain(0.2, 0.0))
class NonEnglishCleanupTests(unittest.TestCase):
    """Every rule in compile_cleanup is English by construction."""

    def test_english_rules_still_apply_to_english(self):
        plan = compile_cleanup("um, ship it Tuesday actually Wednesday")
        self.assertEqual(plan.text, "ship it Wednesday")
        self.assertIn("remove_filler", [edit.kind for edit in plan.edits])

    def test_the_same_rules_never_run_on_another_language(self):
        # "um" is a real word in several languages, "actually" only marks a
        # self-correction in English, and the scratch-that offset arithmetic
        # is the literal length of an English phrase. Applied elsewhere these
        # rules cannot help and can silently delete real words.
        for language, text in (
            ("de", "um acht Uhr, actually um neun Uhr"),
            ("es", "el informe   estara listo"),
            ("nl", "um dat is prima"),
        ):
            with self.subTest(language=language):
                plan = compile_cleanup(text, language)
                self.assertEqual(plan.edits, [])
                self.assertFalse(plan.needs_semantic_cleanup)
                # Whitespace normalization is the one rule that holds
                # everywhere, so it is all that runs.
                self.assertEqual(plan.text, " ".join(text.split()))

    def test_spaceless_scripts_keep_their_own_punctuation_spacing(self):
        japanese = "\u56db\u534a\u671f\u5831\u544a\u66f8\u3002"
        self.assertEqual(compile_cleanup(japanese, "ja").text, japanese)
        self.assertEqual(compile_cleanup(japanese, "zh").text, japanese)

    def test_spoken_code_punctuation_is_english_only(self):
        english = compile_code_dictation("open paren close paren")
        self.assertEqual(english.text, "()")
        # The same words are ordinary prose in another language, and the
        # glyphs they would insert are ASCII regardless of the script.
        other = compile_code_dictation("open paren close paren", "nl")
        self.assertEqual(other.text, "open paren close paren")

    def test_normalize_spacing_is_the_shared_language_safe_rule(self):
        self.assertEqual(
            normalize_spacing("a  b \n  c", spaced=True), "a b\nc")
        # A script that does not write ASCII sentence punctuation keeps its
        # own spacing around it.
        self.assertEqual(
            normalize_spacing("a  b", spaced=False), "a b")


if __name__ == "__main__":
    unittest.main()
