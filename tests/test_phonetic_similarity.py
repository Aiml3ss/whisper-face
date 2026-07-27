# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phonetic_keys import double_metaphone
from voice_compiler import phonetic_similarity


# The span-graph context gate in voice_compiler accepts a candidate at
# 0.84 (heavily weighted context) or 0.88 (everything else).  These
# constants keep the tests honest about which contract they exercise.
GENEROUS_GATE = 0.84
STRICT_GATE = 0.88


class ConfusionPairTests(unittest.TestCase):
    """Pairs where the recognizer hears the left, the screen shows the
    right.  Every one must clear the context-candidate gate."""

    PAIRS = (
        ("Gwen", "Qwen"),
        ("Jason", "JSON"),
        ("sequel", "SQL"),
        ("colonel", "kernel"),
        ("Curser", "Cursor"),
        ("Said", "Zed"),
        ("cloud", "Claude"),
        ("knight", "night"),
    )

    def test_common_asr_confusions_clear_the_generous_gate(self):
        for heard, shown in self.PAIRS:
            with self.subTest(heard=heard, shown=shown):
                self.assertGreaterEqual(
                    phonetic_similarity(heard, shown), GENEROUS_GATE)

    def test_similarity_is_symmetric(self):
        for heard, shown in self.PAIRS:
            with self.subTest(heard=heard, shown=shown):
                self.assertAlmostEqual(
                    phonetic_similarity(heard, shown),
                    phonetic_similarity(shown, heard))

    def test_golden_corpus_pairs_keep_their_historical_score(self):
        # rare-term-visible-context and the Qwen3_5 span-graph test both
        # passed the gate at exactly 0.92 before this algorithm landed;
        # the upgrade must not lower them.
        self.assertAlmostEqual(phonetic_similarity("Gwen", "Qwen"), 0.92)
        self.assertAlmostEqual(
            phonetic_similarity("Gwen", "Qwen3_5"), 0.92)
        self.assertGreaterEqual(
            phonetic_similarity("Gwen", "Qwen3_5"), STRICT_GATE)


class DissimilarPairTests(unittest.TestCase):
    def test_unrelated_words_stay_clearly_below_the_gate(self):
        for left, right in (
            ("cat", "dog"),
            ("ship", "boat"),
            ("paris", "london"),
            ("merge", "lunch"),
            ("deploy", "breakfast"),
        ):
            with self.subTest(left=left, right=right):
                self.assertLess(phonetic_similarity(left, right), 0.80)

    def test_a_near_miss_weekday_still_cannot_pass(self):
        # Spelling ratio 0.80, phonetic codes one consonant apart: the
        # classic context trap (screen says Thursday, speaker said
        # Tuesday) stays under both gates.
        self.assertLess(
            phonetic_similarity("Tuesday", "Thursday"), GENEROUS_GATE)

    def test_symbol_soup_never_fakes_a_match(self):
        # The old key reduced both sides to "" and difflib called two
        # empty keys identical (0.92).  No letters means no phonetic
        # evidence at all now.
        self.assertEqual(phonetic_similarity("!!!", "???"), 0.0)
        self.assertLess(phonetic_similarity("123", "456"), 0.80)


class IdentifierTests(unittest.TestCase):
    def test_digits_and_joiners_pass_through_unchanged(self):
        self.assertEqual(double_metaphone("Qwen3_5"), ("KN3_5", "KN3_5"))
        self.assertEqual(double_metaphone("D55"), ("T55", "T55"))
        self.assertEqual(
            double_metaphone("kubectl2"), ("KPKTL2", "KPKTL2"))

    def test_version_digits_keep_identifiers_phonetically_apart(self):
        self.assertNotEqual(
            double_metaphone("Qwen2_5"), double_metaphone("Qwen3_5"))
        self.assertLess(phonetic_similarity("v2", "v3"), 0.80)

    def test_a_spoken_word_reaches_the_identifier_stem(self):
        # A word carrying no digits may match an identifier by its
        # letters alone: the speaker says "kubectl", the screen shows
        # kubectl2.  Two versioned identifiers get no such grace.
        self.assertGreaterEqual(
            phonetic_similarity("Gwen", "Qwen3_5"), STRICT_GATE)
        self.assertGreaterEqual(
            phonetic_similarity("kubectl", "kubectl2"), STRICT_GATE)
        self.assertNotEqual(
            double_metaphone("kubectl2"), double_metaphone("kubectl3"))

    def test_apostrophes_are_silent(self):
        self.assertEqual(
            double_metaphone("o'clock"), double_metaphone("oclock"))


class DoubleMetaphoneCodeTests(unittest.TestCase):
    def test_canonical_reference_codes(self):
        # Values published with the original algorithm.
        self.assertEqual(double_metaphone("Smith"), ("SM0", "XMT"))
        self.assertEqual(double_metaphone("Schmidt"), ("XMT", "SMT"))
        self.assertEqual(double_metaphone("Xavier"), ("SF", "SFR"))
        self.assertEqual(double_metaphone("Jose"), ("HS", "HS"))

    def test_secondary_code_bridges_germanic_spellings(self):
        # Smith's secondary XMT meets Schmidt's primary XMT; neither
        # primary alone would connect them.
        self.assertGreaterEqual(
            phonetic_similarity("Schmidt", "Smith"), GENEROUS_GATE)

    def test_silent_letters_disappear(self):
        self.assertEqual(double_metaphone("knight")[0], "NT")
        self.assertEqual(double_metaphone("wright")[0], "RT")
        self.assertEqual(double_metaphone("psalm")[0], "SLM")

    def test_case_never_changes_a_code(self):
        for word in ("Qwen", "kubectl", "JSON", "Claude"):
            with self.subTest(word=word):
                self.assertEqual(
                    double_metaphone(word.lower()),
                    double_metaphone(word.upper()))

    def test_empty_and_letterless_input_yield_empty_codes(self):
        self.assertEqual(double_metaphone(""), ("", ""))
        self.assertEqual(double_metaphone("!!!"), ("", ""))


class ScoreScaleTests(unittest.TestCase):
    def test_identical_tokens_score_one(self):
        self.assertEqual(phonetic_similarity("deploy", "deploy"), 1.0)
        self.assertEqual(phonetic_similarity("Qwen3_5", "Qwen3_5"), 1.0)

    def test_scores_stay_inside_the_unit_interval(self):
        tokens = ("Gwen", "Qwen3_5", "kubectl", "colonel", "!!!", "",
                  "https://a.b", "Tuesday")
        for left in tokens:
            for right in tokens:
                score = phonetic_similarity(left, right)
                with self.subTest(left=left, right=right):
                    self.assertGreaterEqual(score, 0.0)
                    self.assertLessEqual(score, 1.0)

    def test_a_pure_phonetic_match_tops_out_below_spelling(self):
        # Homophones cap at 0.92 so a same-spelling candidate always
        # outranks a merely same-sounding one.
        self.assertAlmostEqual(phonetic_similarity("Said", "Zed"), 0.92)
        self.assertLess(
            phonetic_similarity("Said", "Zed"),
            phonetic_similarity("Zed", "Zed"))


if __name__ == "__main__":
    unittest.main()
