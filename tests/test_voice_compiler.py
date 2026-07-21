# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_compiler import (
    ContextCandidate,
    ContextObservation,
    ContextRouter,
    EditProposal,
    PersonalPrior,
    ProsodyEvent,
    RecognitionHypothesis,
    VoiceCompiler,
    VoiceIR,
    WordEvidence,
    analyze_prosody,
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


if __name__ == "__main__":
    unittest.main()
