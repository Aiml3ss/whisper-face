# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from dataclasses import asdict
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_compiler import (
    ContextCandidate,
    ContextObservation,
    ContextPack,
    ContextRouter,
    EditProposal,
    PersonalPrior,
    ProsodyEvent,
    RecognitionHypothesis,
    VoiceCompiler,
    VoiceIR,
    WordEvidence,
    analyze_prosody,
    context_firewall_receipt,
    protected_anchors,
)


class VoiceCompilerTests(unittest.TestCase):
    def setUp(self):
        self.compiler = VoiceCompiler()

    def test_voice_ir_requires_acoustic_evidence(self):
        with self.assertRaises(ValueError):
            VoiceIR(())

    def test_span_graph_uses_visible_context_for_phonetic_candidate(self):
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "Use Gwen for cleanup", 0.68, "tiny"),),
            context=ContextRouter().collect(ContextObservation(
                app="Codex", bundle="com.openai.codex",
                field_text="OLLAMA_MODEL = Qwen3_5",
            )),
        )
        result = self.compiler.compile(voice)
        self.assertEqual(result.text, "Use Qwen3_5 for cleanup")
        self.assertEqual(result.decisions[0].source, "span-graph")

    def test_visible_context_cannot_corrupt_confident_speech_with_ui_metrics(self):
        raw = (
            "Please make sure all this is updated in the GitHub, please, "
            "with all your ideas."
        )
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(raw, 0.7468, "tiny"),),
            context=ContextRouter().collect(ContextObservation(
                app="Codex", bundle="com.openai.codex",
                field_text=(
                    "Fetching 4 files: 100% 4/4 [00:00<00:00, "
                    "2042.76it/s] release 0.00s And It The Right"
                ),
            )),
        )

        result = self.compiler.compile(voice)

        self.assertEqual(result.text, raw)
        self.assertEqual(result.decisions, ())

    def test_cross_engine_agreement_keeps_the_primary_word(self):
        voice = VoiceIR(hypotheses=(
            RecognitionHypothesis("Ship the installer", 0.72, "tiny"),
            RecognitionHypothesis("Ship the installer", 0.82, "turbo"),
        ))
        result = self.compiler.compile(voice)
        self.assertEqual(result.text, "Ship the installer")

    def test_personal_prior_is_stronger_in_the_observed_app(self):
        prior = PersonalPrior(
            "Gwen", "Qwen", count=1,
            apps=(("com.openai.codex", 2),),
        )
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "Run Gwen locally", 0.72, "tiny"),),
            personal_priors=(prior,),
            app_bundle="com.openai.codex",
        )
        self.assertEqual(self.compiler.compile(voice).text, "Run Qwen locally")

    def test_proof_edits_accept_filler_but_protect_facts(self):
        source = "Um ship API v2 at 15:30 tomorrow"
        result = self.compiler.verify_edits(source, (
            EditProposal("remove_filler", "Um ", ""),
            EditProposal("semantic_cleanup", "API v2", "the API"),
        ))
        self.assertEqual(result.text, "ship API v2 at 15:30 tomorrow")
        self.assertTrue(result.edits[0].accepted)
        self.assertFalse(result.edits[1].accepted)
        self.assertIn("protected anchor", result.edits[1].reason)

    def test_untrusted_edit_kind_cannot_authorize_content_deletion(self):
        result = self.compiler.verify_edits(
            "Please send the customer refund right now",
            (EditProposal(
                "punctuation", "send the customer refund right now", "."),),
        )
        self.assertEqual(
            result.text, "Please send the customer refund right now")
        self.assertFalse(result.edits[0].accepted)
        self.assertEqual(
            result.edits[0].reason, "unproved lexical transformation")

    def test_real_words_are_not_treated_as_individual_fillers(self):
        result = self.compiler.verify_edits(
            "I know you can ship it",
            (EditProposal("remove_filler", "I know you ", ""),),
        )
        self.assertFalse(result.edits[0].accepted)
        self.assertEqual(result.text, "I know you can ship it")

    def test_internal_punctuation_is_semantically_significant(self):
        for before, after in (
            ("re-sign", "resign"),
            ("can't", "cant"),
            ("foo.bar", "foobar"),
        ):
            with self.subTest(before=before, after=after):
                result = self.compiler.verify_edits(
                    f"Please {before} now",
                    (EditProposal("punctuation", before, after),),
                )
                self.assertFalse(result.edits[0].accepted)

    def test_operators_sigils_and_language_suffixes_are_proof_evidence(self):
        for before, after in (
            ("x > y", "x < y"),
            ("enabled = true", "enabled != true"),
            ("a && b", "a || b"),
            ("price + tax", "price - tax"),
            ("C++", "C"),
            ("C#", "C"),
            ("5%", "5"),
            ("$500", "500"),
            ("#general", "general"),
            ("@Andrew", "Andrew"),
            ("A&B", "A B"),
        ):
            with self.subTest(before=before, after=after):
                result = self.compiler.verify_edits(
                    before, (EditProposal("punctuation", before, after),))
                self.assertFalse(result.edits[0].accepted)

    def test_code_mode_preserves_all_punctuation_and_case(self):
        for before, after in (
            ("x: int", "x int"),
            ("call(a, b)", "call(a b)"),
            ("first(); second()", "first() second()"),
            ('{"a": 1, "b": 2}', '{"a" 1 "b" 2}'),
            ("Widget", "widget"),
        ):
            with self.subTest(before=before, after=after):
                result = self.compiler.verify_edits(
                    before,
                    (EditProposal("punctuation", before, after),),
                    mode="code",
                )
                self.assertFalse(result.edits[0].accepted)

    def test_actually_is_not_blanket_permission_to_delete_content(self):
        for before, after in (
            ("i actually know you mean well", "know you mean well"),
            ("This is actually critical", "critical"),
        ):
            with self.subTest(before=before):
                result = self.compiler.verify_edits(
                    before, (EditProposal("self_correction", before, after),))
                self.assertFalse(result.edits[0].accepted)

    def test_counted_enumeration_can_be_proved_as_a_list(self):
        source = ("Two things first ship the installer and second update "
                  "the docs")
        after = "Two things:\n- Ship the installer\n- Update the docs."
        result = self.compiler.verify_edits(
            source, (EditProposal("spoken_enumeration", source, after),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, after)

    def test_counted_ideas_can_be_proved_as_a_list(self):
        source = "Two ideas first improve speed and second preserve privacy"
        after = "Two ideas:\n- Improve speed\n- Preserve privacy."
        result = self.compiler.verify_edits(
            source, (EditProposal("spoken_enumeration", source, after),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, after)

    def test_explicit_list_intent_can_add_bullets_without_dropping_words(self):
        source = ("Here are some feedback items make lists easy to scan and "
                  "also keep every spoken detail")
        after = ("Here are some feedback items:\n- Make lists easy to scan."
                 "\n- And also keep every spoken detail.")
        result = self.compiler.verify_edits(
            source, (EditProposal("spoken_enumeration", source, after),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, after)

    def test_explicit_list_header_can_be_compressed_without_dropping_items(self):
        source = ("Here's a list of ideas that I have improve detection add a "
                  "keyboard shortcut and make settings easier to find")
        after = ("Here's a list of ideas:\n- Improve detection\n- Add a "
                 "keyboard shortcut\n- Make settings easier to find.")
        result = self.compiler.verify_edits(
            source, (EditProposal("spoken_enumeration", source, after),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, after)

    def test_explicit_list_formatting_cannot_drop_an_item(self):
        source = ("Here's a list of ideas that I have improve detection add a "
                  "keyboard shortcut and make settings easier to find")
        after = ("Here's a list of ideas:\n- Improve detection\n- And make "
                 "settings easier to find.")
        result = self.compiler.verify_edits(
            source, (EditProposal("spoken_enumeration", source, after),))
        self.assertFalse(result.edits[0].accepted)

    def test_punctuation_proof_preserves_ordered_lexical_content(self):
        result = self.compiler.verify_edits(
            "ship API v2 tomorrow",
            (EditProposal(
                "anything", "ship API v2 tomorrow", "Ship API v2 tomorrow."),),
        )
        self.assertEqual(result.text, "Ship API v2 tomorrow.")
        self.assertTrue(result.edits[0].accepted)

    def test_unfinalized_result_exposes_only_common_stable_prefix(self):
        voice = VoiceIR(
            hypotheses=(
                RecognitionHypothesis("Ship this Tuesday", 0.7, "tiny"),
                RecognitionHypothesis("Ship this Thursday", 0.8, "turbo"),
            ),
            finalized=False,
        )
        self.assertEqual(self.compiler.compile(voice).stable_prefix, "Ship this")

    def test_prosody_turns_a_long_pause_into_a_paragraph(self):
        words = (
            WordEvidence("First", 0.0, 0.3),
            WordEvidence("point.", 0.35, 0.8),
            WordEvidence("Second", 1.9, 2.2),
            WordEvidence("point.", 2.25, 2.7),
        )
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "First point. Second point.", 0.8, "turbo", words),),
            prosody=(ProsodyEvent("pause", 0.9, 1.0),),
        )
        self.assertEqual(
            self.compiler.compile(voice).text,
            "First point.\n\nSecond point.",
        )

    def test_segment_interpolation_never_places_semantic_punctuation(self):
        words = (
            WordEvidence("First", 0.0, 0.8, timing="segment"),
            WordEvidence("point.", 0.8, 1.6, timing="segment"),
            WordEvidence("Second", 1.6, 2.4, timing="segment"),
        )
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "First point. Second", 0.8, "turbo", words),),
            prosody=(ProsodyEvent("pause", 0.9, 1.0),),
        )
        self.assertEqual(
            self.compiler.compile(voice).text, "First point. Second")

    def test_formatting_decisions_do_not_inflate_acoustic_confidence(self):
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "Can we ship this.", 0.64, "turbo"),),
            prosody=(ProsodyEvent("rising_end", 1.0, strength=0.8),),
        )
        result = self.compiler.compile(voice)
        self.assertEqual(result.text, "Can we ship this?")
        self.assertEqual(result.confidence, 0.64)

    def test_protected_anchors_cover_names_dates_paths_commands_and_flags(self):
        anchors = protected_anchors(
            "Ask Andrew on January 12 to run git status --short in ./repo")
        lowered = {anchor.casefold() for anchor in anchors}
        for expected in ("andrew", "january 12", "git", "--short", "./repo"):
            self.assertIn(expected, lowered)

    def test_dictionary_term_supplied_as_anchor_is_protected(self):
        # A lowercase brand term supplied at the dictionary anchor weight (3.5)
        # becomes a protected anchor once it appears in the recognized text, so
        # cleanup may not delete it. It cannot invent the term: protection only
        # applies because the recognizer already produced it.
        context = (ContextCandidate("qwen", 3.5, "dictionary"),)
        source = "switch the model to qwen please"
        anchors = protected_anchors(source, context)
        self.assertIn("qwen", {anchor.casefold() for anchor in anchors})

        result = self.compiler.verify_edits(
            source,
            (EditProposal("semantic_cleanup", "to qwen please", "please"),),
            context=context,
        )
        self.assertEqual(result.text, source)
        self.assertFalse(result.edits[0].accepted)
        self.assertIn("protected anchor removed", result.edits[0].reason)

    def test_context_does_not_replace_ordinary_english_words(self):
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "The meeting is Tuesday", 0.68, "tiny"),),
            context=ContextRouter().collect(ContextObservation(
                app="Codex", bundle="com.openai.codex",
                field_text="Thursday remains available",
            )),
        )
        self.assertEqual(
            self.compiler.compile(voice).text, "The meeting is Tuesday")

    def test_context_router_has_generic_and_developer_adapters(self):
        pack = ContextRouter().collect(ContextObservation(
            app="Codex", bundle="com.openai.codex",
            selected_text="TranscriptionPipeline",
            document="/repo/parrot_core.py",
            sibling_names=("voice_compiler.py", "dictate.py"),
        ))
        names = {candidate.text for candidate in pack.candidates}
        self.assertIn("TranscriptionPipeline", names)
        self.assertIn("voice_compiler.py", names)
        self.assertEqual(pack.style, "technical")
        self.assertIn("preserve_identifiers", pack.constraints)

    def test_audio_prosody_finds_an_internal_pause(self):
        samples = [0.1] * 4_000 + [0.0] * 8_000 + [0.1] * 4_000
        events = analyze_prosody(samples, sample_rate=16_000)
        self.assertTrue(any(event.kind == "pause" for event in events))


class ContextFirewallTests(unittest.TestCase):
    def setUp(self):
        self.compiler = VoiceCompiler()

    def test_protected_context_influence_is_shadow_quarantined(self):
        voice = VoiceIR(
            (RecognitionHypothesis("Use Gwen for cleanup", 0.68, "tiny"),),
            context=ContextRouter().collect(ContextObservation(
                app="Codex", bundle="com.openai.codex",
                selected_text="Qwen",
            )),
        )
        active = self.compiler.compile(voice)
        receipt = context_firewall_receipt(
            voice, compiled=active, compiler=self.compiler)

        self.assertEqual(active.text, "Use Qwen for cleanup")
        self.assertEqual(receipt.disposition, "quarantine")
        self.assertTrue(receipt.counterfactual_changed)
        self.assertEqual(receipt.context_influences, 1)
        self.assertEqual(receipt.protected_influences, 1)
        self.assertEqual(receipt.promotion_candidates, 0)
        self.assertEqual(
            dict(receipt.reason_counts), {"context-protected": 1})

    def test_protected_prior_and_unprotected_prior_are_separated(self):
        protected = VoiceIR(
            (RecognitionHypothesis(
                "Please email Alice Smyth", 0.55, "tiny"),),
            personal_priors=(PersonalPrior("Smyth", "Smith", 6),),
        )
        safe = VoiceIR(
            (RecognitionHypothesis("this is teh draft", 0.55, "tiny"),),
            personal_priors=(PersonalPrior("teh", "the", 6),),
        )

        protected_receipt = context_firewall_receipt(protected)
        safe_receipt = context_firewall_receipt(safe)

        self.assertEqual(protected_receipt.disposition, "quarantine")
        self.assertEqual(protected_receipt.quarantined, 1)
        self.assertEqual(
            dict(protected_receipt.reason_counts),
            {"personal-prior-protected": 1},
        )
        self.assertEqual(safe_receipt.disposition, "promotion-candidate")
        self.assertEqual(safe_receipt.promotion_candidates, 1)
        self.assertEqual(safe_receipt.quarantined, 0)

    def test_shadow_compiler_receives_no_context_or_personal_priors(self):
        voice = VoiceIR(
            (RecognitionHypothesis("this is teh draft", 0.55, "tiny"),),
            context=ContextPack((ContextCandidate(
                "private-context-value", 10.0, "document"),)),
            personal_priors=(PersonalPrior("teh", "the", 6),),
        )
        active = self.compiler.compile(voice)
        seen = []

        class RecordingCompiler:
            def compile(_self, counterfactual):
                seen.append(counterfactual)
                return self.compiler.compile(counterfactual)

        context_firewall_receipt(
            voice, compiled=active, compiler=RecordingCompiler())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].context, ContextPack())
        self.assertEqual(seen[0].personal_priors, ())
        self.assertEqual(voice.context.candidates[0].text,
                         "private-context-value")
        self.assertEqual(voice.personal_priors[0].preferred, "the")

    def test_receipt_never_serializes_adversarial_private_text(self):
        private = "Alice private@example.com /Users/alice/secret"
        voice = VoiceIR(
            (RecognitionHypothesis("this is teh draft", 0.55, "tiny"),),
            context=ContextPack((ContextCandidate(
                private, 10.0, private),)),
            personal_priors=(PersonalPrior("teh", "the", 6),),
            app_bundle=private,
        )
        receipt = context_firewall_receipt(voice)
        encoded = json.dumps(asdict(receipt), sort_keys=True)
        self.assertNotIn("Alice", encoded)
        self.assertNotIn("private@example.com", encoded)
        self.assertNotIn("/Users/alice/secret", encoded)
        self.assertNotIn("teh", encoded)
        self.assertNotIn("the", encoded)

    def test_no_influence_is_reported_without_changing_the_live_result(self):
        voice = VoiceIR((RecognitionHypothesis(
            "ordinary dictated prose", 0.92, "turbo"),))
        active = self.compiler.compile(voice)
        receipt = context_firewall_receipt(voice, compiled=active)
        self.assertEqual(active, self.compiler.compile(voice))
        self.assertEqual(receipt.disposition, "no-effect")
        self.assertFalse(receipt.counterfactual_changed)
        self.assertEqual(dict(receipt.reason_counts), {"no-influence": 1})




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
