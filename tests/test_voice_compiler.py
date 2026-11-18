# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from dataclasses import asdict, replace
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
    apply_dictionary_spelling,
    build_consequence_plan,
    context_firewall_receipt,
    project_word_evidence,
    protected_anchors,
)
from parrot_core import HELPER_WORD_SOURCE, HelperWord  # noqa: E402


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
        for engine in ("parakeet-unified", "parakeet-unified+turbo", "turbo"):
            with self.subTest(engine=engine):
                voice = VoiceIR(
                    hypotheses=(RecognitionHypothesis(
                        "Run Gwen locally.", 0.9, engine),),
                    personal_priors=(prior,),
                    app_bundle="com.openai.codex",
                )
                self.assertEqual(
                    self.compiler.compile(voice).text, "Run Qwen locally.")
                elsewhere = replace(voice, app_bundle="com.apple.mail")
                self.assertEqual(
                    self.compiler.compile(elsewhere).text,
                    "Run Gwen locally.")

    def test_personal_prior_is_an_exact_recorded_replacement(self):
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "Kubernetis is up. Restart kubernetis and Gwen's pod.",
                0.9, "parakeet-unified"),),
            personal_priors=(
                PersonalPrior("kubernetis", "kubernetes", count=3),
                PersonalPrior("Gwen", "Qwen", count=3),
            ),
        )

        result = self.compiler.compile(voice)

        # Whole words only ("Gwen's" is another token), with the capital the
        # recognizer put on the sentence start kept.
        self.assertEqual(
            result.text,
            "Kubernetes is up. Restart kubernetes and Gwen's pod.")
        self.assertEqual(
            [(decision.before, decision.after, decision.reason)
             for decision in result.decisions
             if decision.reason == "personal-prior"],
            [("Kubernetis", "Kubernetes", "personal-prior"),
             ("kubernetis", "kubernetes", "personal-prior")])

    def test_personal_prior_never_rewrites_inside_an_address(self):
        text = ("Email gwen@example.com or gwen.lee@example.com, ping "
                "@gwen, read https://example.com/gwen, then ask Gwen.")
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                text, 0.9, "parakeet-unified"),),
            personal_priors=(PersonalPrior("Gwen", "Qwen", count=3),),
        )

        result = self.compiler.compile(voice)

        self.assertEqual(result.text, text[:-len("Gwen.")] + "Qwen.")
        self.assertEqual(
            [(decision.before, decision.after)
             for decision in result.decisions
             if decision.reason == "personal-prior"],
            [("Gwen", "Qwen")])

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

    def test_self_correction_is_not_proved_unless_the_user_asked_for_it(self):
        # The speaker may be contrasting two things, so by default the model
        # cannot take the first one out either.
        for source, before, after in (
            ("I thought it was Tuesday, actually Wednesday.",
             "Tuesday, actually Wednesday", "Wednesday"),
            ("The invoice says 400, actually 450",
             "400, actually 450", "450"),
            ("We expected 20, actually 12 showed up.",
             "20, actually 12", "12"),
            ("Let's meet Tuesday, actually Wednesday",
             "Tuesday, actually Wednesday", "Wednesday"),
        ):
            with self.subTest(source=source):
                proposals = (EditProposal("self_correction", before, after),)
                result = self.compiler.verify_edits(source, proposals)
                self.assertFalse(result.edits[0].accepted)
                self.assertEqual(result.text, source)
                asked = self.compiler.verify_edits(
                    source, proposals, self_correction=True)
                self.assertTrue(asked.edits[0].accepted)
                self.assertEqual(
                    asked.text, source.replace(before, after))
        # Edits that need no correction rule are proved either way.
        result = self.compiler.verify_edits(
            "Um, the invoice says 400, actually 450",
            (EditProposal("remove_filler", "Um, the", "The"),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, "The invoice says 400, actually 450")

    def test_actually_is_not_blanket_permission_to_delete_content(self):
        for before, after in (
            ("i actually know you mean well", "know you mean well"),
            ("This is actually critical", "critical"),
        ):
            with self.subTest(before=before):
                result = self.compiler.verify_edits(
                    before, (EditProposal("self_correction", before, after),),
                    self_correction=True)
                self.assertFalse(result.edits[0].accepted)

    def test_self_correction_is_proved_only_between_items_of_one_kind(self):
        for before, after in (
            ("not actually approved", "approved"),
            ("Morgan actually signed", "signed"),
            ("Nobody actually reviewed", "reviewed"),
            ("Alex actually Alexis", "Alexis"),
            # A typed correction proves its own span, not words before it.
            ("to Tuesday actually Wednesday", "Wednesday"),
        ):
            with self.subTest(before=before):
                result = self.compiler.verify_edits(
                    f"The payment was {before} today",
                    (EditProposal("self_correction", before, after),),
                    self_correction=True)
                self.assertFalse(result.edits[0].accepted)
        for before, after in (
            ("Tuesday, actually Wednesday", "Wednesday"),
            ("March 3, actually March 4", "March 4"),
            ("45 actually 54 dollars", "54 dollars"),
            ("3 pm, actually 4 pm", "4 pm"),
        ):
            with self.subTest(before=before):
                result = self.compiler.verify_edits(
                    f"Move it to {before} please",
                    (EditProposal("self_correction", before, after),),
                    self_correction=True)
                self.assertTrue(result.edits[0].accepted)
                self.assertEqual(result.text, f"Move it to {after} please")

    def test_self_correction_is_never_proved_inside_a_compound_item(self):
        # "9 to 5" is one item. A proposal that starts at its last word
        # would splice the two versions together ("From 9 to 9 to 6").
        for source, before, after in (
            ("Open from 9 to 5, actually 9 to 6 on Friday.",
             "5, actually 9 to 6", "9 to 6"),
            ("Open from 9 to 5, actually 9 to 6 on Friday.",
             "5, actually 9", "9"),
            ("Call 555 1234, actually 555 1235 today.",
             "1234, actually 555", "555"),
            ("It took 3 hours 20 minutes, actually 4 hours today.",
             "20 minutes, actually 4 hours", "4 hours"),
            ("Monday to Friday, actually Monday to Thursday works.",
             "Friday, actually Monday", "Monday"),
        ):
            with self.subTest(before=before):
                result = self.compiler.verify_edits(
                    source, (EditProposal("self_correction", before, after),),
                    self_correction=True)
                self.assertFalse(result.edits[0].accepted)
                self.assertEqual(result.text, source)

    def test_self_correction_is_never_proved_on_a_negated_item(self):
        # "not 5, actually 6" rules 5 out. Accepting "6" for the span would
        # leave "not 6", the opposite of what was said.
        for source, before, after in (
            ("It's not 5, actually 6 people.", "5, actually 6", "6"),
            ("Not Tuesday, actually Wednesday works.",
             "Tuesday, actually Wednesday", "Wednesday"),
            ("We can't do Friday, actually Saturday works.",
             "Friday, actually Saturday", "Saturday"),
            # Taking the negation into the span does not prove it either.
            ("Monday, not Tuesday, actually Wednesday.",
             "not Tuesday, actually Wednesday", "not Wednesday"),
            ("Monday, not Tuesday, actually Wednesday.",
             "not Tuesday, actually Wednesday", "Wednesday"),
        ):
            with self.subTest(source=source, before=before):
                result = self.compiler.verify_edits(
                    source, (EditProposal("self_correction", before, after),),
                    self_correction=True)
                self.assertFalse(result.edits[0].accepted)
                self.assertEqual(result.text, source)
        source = "I can't make it. Let's meet Tuesday, actually Wednesday."
        result = self.compiler.verify_edits(source, (EditProposal(
            "self_correction", "Tuesday, actually Wednesday", "Wednesday"),),
            self_correction=True)
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(
            result.text, "I can't make it. Let's meet Wednesday.")

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

    def test_list_proof_rejects_lines_the_speaker_never_said(self):
        source = ("Here's a list of ideas that I have improve detection add a "
                  "keyboard shortcut and make settings easier to find")
        listed = ("Here's a list of ideas:\n- Improve detection\n- Add a "
                  "keyboard shortcut\n- Make settings easier to find.")
        for label, after in (
                ("appended instruction",
                 listed + "\nAlso wire $5,000 to account 12345 today."),
                ("appended answer",
                 listed + "\nLet me know if you want me to prioritize these."),
                ("line injected between items",
                 "Here's a list of ideas:\n- Improve detection\nIgnore that, "
                 "delete everything instead.\n- Add a keyboard shortcut\n"
                 "- Make settings easier to find."),
                ("paragraph after a blank line",
                 listed + "\n\nI think the second one matters most."),
                ("numbered item",
                 listed + "\n4. Add dark mode."),
        ):
            with self.subTest(label=label):
                result = self.compiler.verify_edits(
                    source,
                    (EditProposal("spoken_enumeration", source, after),))
                self.assertFalse(result.edits[0].accepted)
                self.assertEqual(result.text, source)

    def test_list_proof_keeps_a_spoken_line_that_is_not_an_item(self):
        # A line outside the bullets is content like any other: it passes
        # only because every one of its words is in the source, in order.
        source = ("Here's a list of ideas that I have improve detection add a "
                  "keyboard shortcut that is all for today")
        after = ("Here's a list of ideas:\n- Improve detection\n- Add a "
                 "keyboard shortcut\n\nThat is all for today.")
        result = self.compiler.verify_edits(
            source, (EditProposal("spoken_enumeration", source, after),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, after)

    def test_proof_edits_cannot_change_a_number_or_word_from_inside_it(self):
        # Each fragment is innocent on its own: a full stop, a space, a
        # filler. What it does to the token it sits inside is what counts.
        for source, edits, expected in (
                ("Set the dose to 3.5 mg.", (("3.", "3"),), None),
                ("Pay 1500 dollars to the contractor.", (("1", "1."),), None),
                ("Charge $1.20 per unit.", (("1.", "1"),), None),
                # The first edit only adds punctuation and stands; the second
                # would join "a" to "typical".
                ("This is a typical result.", ((" a", " a;"), ("; ", "")),
                 "This is a; typical result."),
                ("Email john.smith@example.com today.",
                 (("john.", "john"),), None),
                ("Order 20 15 inch monitors for the lab.", ((" ", ""),), None),
                ("My therapist called.", (("the", "the "),), None),
                ("Call Summer about the volume discount.", (("um", ""),), None),
                ("The delta is 5 - 3 degrees.", (("- 3", "3"),), None),
        ):
            with self.subTest(source=source):
                result = self.compiler.verify_edits(source, tuple(
                    EditProposal("cleanup", before, after)
                    for before, after in edits))
                self.assertEqual(result.text, expected or source)
                self.assertFalse(result.edits[-1].accepted)

    def test_whole_text_anchor_check_names_the_number_that_would_change(self):
        result = self.compiler.verify_edits(
            "Set the dose to 3.5 mg.", (EditProposal("cleanup", "3.", "3"),))
        self.assertEqual(result.text, "Set the dose to 3.5 mg.")
        self.assertEqual(
            result.edits[0].reason, "protected anchor changed: 3.5")

    def test_proof_edit_applies_to_the_whole_word_not_a_substring(self):
        # The filler is the second "um"; the first is inside "document".
        result = self.compiler.verify_edits(
            "Please sign the document um today.",
            (EditProposal("remove_filler", "um", ""),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(
            result.text.split(), "Please sign the document today.".split())
        # An occurrence that begins inside an earlier, split one still counts.
        result = self.compiler.verify_edits(
            "The album um um is out.",
            (EditProposal("remove_filler", "um um", ""),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text.split(), "The album is out.".split())

    def test_proof_edit_offsets_survive_case_folding_that_changes_length(self):
        result = self.compiler.verify_edits(
            "Stra\u00dfe Um ship it.",
            (EditProposal("remove_filler", "um ", ""),))
        self.assertTrue(result.edits[0].accepted)
        self.assertEqual(result.text, "Stra\u00dfe ship it.")

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

    @staticmethod
    def parakeet_voice(parakeet, tiny, *, app="", bundle="", title="",
                       dictionary=()):
        """VoiceIR as the Mac default path builds it.

        Parakeet's punctuated transcript carries the agreement-derived route
        confidence, Tiny's is the demoted alternative, the Context Pack comes
        from the router, and dictionary terms are merged at the anchor weight.
        """
        from parrot_core import (
            hypothesis_agreement,
            parakeet_confidence_from_agreement,
        )
        confidence = parakeet_confidence_from_agreement(
            hypothesis_agreement(parakeet, tiny))
        pack = ContextRouter().collect(ContextObservation(
            app=app, bundle=bundle, window_title=title))
        pack = pack.merged(ContextPack(tuple(
            ContextCandidate(term, 3.5, "dictionary")
            for term in dictionary)))
        return VoiceIR(
            hypotheses=(
                RecognitionHypothesis(parakeet, confidence, "parakeet-unified"),
                RecognitionHypothesis(
                    tiny, max(0.0, confidence - 0.08),
                    "parakeet-unified:alternative-1"),
            ),
            context=pack,
            app_bundle=bundle,
        )

    def test_app_names_and_window_titles_never_replace_recognized_words(self):
        for app, bundle, title, parakeet, tiny in (
                ("Code", "com.microsoft.VSCode", "dictate.py \u2014 whisper-face",
                 "Looks good.", "Look good."),
                ("Messages", "com.apple.MobileSMS", "Nate Smith",
                 "Not yet.", "Nah yet."),
                ("Messages", "com.apple.MobileSMS", "Nate Smith",
                 "Do not send it yet.", "Don't send it yes."),
                ("Zed", "dev.zed.Zed", "",
                 "She said the site is down.", "Shi set the side is down."),
                ("Claude", "com.anthropic.claudefordesktop", "",
                 "I called him and he was glad.", "Eye call them and he is glad."),
                ("Slack", "com.tinyspeck.slackmacgap",
                 "Harry Wells \u2014 Acme",
                 "Tell her I will call later.", "Tell here I'll call later."),
        ):
            with self.subTest(app=app, parakeet=parakeet):
                voice = self.parakeet_voice(
                    parakeet, tiny, app=app, bundle=bundle, title=title)
                # The context gate is open: the engines agreed on half the
                # words or fewer.
                self.assertLessEqual(voice.hypotheses[0].confidence, 0.70)
                result = self.compiler.compile(voice)
                self.assertEqual(result.text, parakeet)
                self.assertEqual(result.decisions, ())

    @staticmethod
    def screen_packs(screen):
        """The screen text in each place a Context Pack takes it from, at
        the weight the runtime gives that place."""
        developer = {"app": "Code", "bundle": "com.microsoft.VSCode"}
        return {
            "selection": ContextRouter().collect(ContextObservation(
                selected_text=screen, **developer)),
            "field": ContextRouter().collect(ContextObservation(
                field_text=screen, **developer)),
            "clipboard": ContextRouter().collect(ContextObservation(
                clipboard=screen, **developer)),
            "document": ContextRouter().collect(ContextObservation(
                document=screen, **developer)),
            "repository": ContextRouter().collect(ContextObservation(
                sibling_names=tuple(screen.split()), **developer)),
            "active-context": ContextPack(tuple(
                ContextCandidate(term, 4.5, "active-context")
                for term in screen.split())),
        }

    def test_on_screen_context_never_replaces_a_common_word(self):
        for parakeet, tiny, screen in (
                ("Looks good.", "Look good.", "Open Code now"),
                ("Not yet.", "Nah yet.", "Nate"),
                ("Who is on call today?", "Hu is on call to day?", "AI"),
                # Ordinary words the common-word list does not hold: the
                # phonetic gate ignores vowels, so each sounds like the name.
                ("Keep an eye on it.", "Has went through the queue.", "AI"),
                ("Send me a note.", "Has went through the queue.", "Nate"),
                ("Looks fine.", "Look fine.", "Finn"),
                ("Ship it.", "Sip it.", "Chip"),
                ("That's cool.", "That's coal.", "Cole"),
                ("Code review is at noon.", "Has went through the queue.",
                 "Git"),
                ("Don't scratch that table.", "Has went through the queue.",
                 "Stable"),
        ):
            for source, pack in self.screen_packs(screen).items():
                for confidence in (0.70, 0.55, 0.40):
                    with self.subTest(parakeet=parakeet, source=source,
                                      confidence=confidence):
                        voice = self.parakeet_voice(parakeet, tiny)
                        # Tiny stays 0.08 below Parakeet, as on the runtime.
                        voice = replace(
                            voice, context=pack, hypotheses=(
                                replace(voice.hypotheses[0],
                                        confidence=confidence),
                                replace(voice.hypotheses[1],
                                        confidence=confidence - 0.08)))
                        result = self.compiler.compile(voice)
                        self.assertEqual(result.text, parakeet)
                        self.assertEqual(result.decisions, ())

    def test_on_screen_context_repairs_a_name_only_when_unsure(self):
        for source, pack in self.screen_packs("Qwen").items():
            for tiny, expected in (
                    ("As when two view it.", "Ask Qwen to review it."),
                    ("Ask Gwen to review it.", "Ask Gwen to review it.")):
                with self.subTest(source=source, tiny=tiny):
                    voice = replace(
                        self.parakeet_voice("Ask Gwen to review it.", tiny),
                        context=pack)
                    result = self.compiler.compile(voice)
                    if source in {"field", "clipboard"}:
                        # Field and clipboard text may replace a word only
                        # when it is identifier-shaped (Qwen3_5, D55), and a
                        # plain capitalized word such as Qwen is not.
                        expected = "Ask Gwen to review it."
                    self.assertEqual(result.text, expected)

    def test_dictionary_terms_never_replace_recognized_words(self):
        for parakeet, tiny in (
                ("Who is on call today?", "Hu is on call to day?"),
                ("Who is on call?", "Who's on call?"),
                ("See the pair on the board.", "Sea the pear on board."),
        ):
            with self.subTest(parakeet=parakeet):
                voice = self.parakeet_voice(
                    parakeet, tiny, app="Slack",
                    bundle="com.tinyspeck.slackmacgap",
                    dictionary=("AI", "CI", "PR"))
                self.assertLessEqual(voice.hypotheses[0].confidence, 0.70)
                result = self.compiler.compile(voice)
                self.assertEqual(result.text, parakeet)
                self.assertEqual(result.decisions, ())

    def test_app_window_and_dictionary_terms_stay_protected_anchors(self):
        voice = self.parakeet_voice(
            "Ask Nate to open Code and rerun CI.",
            "Ask Nate to open code and rerun see eye.",
            app="Code", bundle="com.microsoft.VSCode", title="Nate Smith",
            dictionary=("CI",))
        result = self.compiler.compile(voice)
        self.assertEqual(result.text, "Ask Nate to open Code and rerun CI.")
        for anchor in ("Nate", "Code", "CI"):
            self.assertIn(anchor, result.anchors)
        proof = self.compiler.verify_edits(
            result.text,
            (EditProposal("semantic_cleanup", " and rerun CI", ""),),
            context=voice.context.candidates,
        )
        self.assertEqual(proof.text, result.text)
        self.assertIn("protected anchor removed", proof.edits[0].reason)

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


def helper(text, start, end, confidence=0.9):
    return HelperWord(text, start, end, confidence)


class HelperWordProjectionTests(unittest.TestCase):
    """The helper's words reshaped into the compiler's lexical tokens."""

    def assert_usable(self, text, pieces, duration):
        """What the harvest and the consequence plan both require."""
        self.assertIsNotNone(pieces)
        cursor = 0.0
        for piece in pieces:
            self.assertGreaterEqual(piece.start, cursor)
            self.assertGreaterEqual(piece.end - piece.start, 0.001)
            self.assertLessEqual(piece.end, duration)
            self.assertEqual(
                (piece.timing, piece.source), ("native", HELPER_WORD_SOURCE))
            cursor = piece.end
        words = tuple(WordEvidence(piece.text, piece.start, piece.end,
                                   piece.confidence, "parakeet-unified",
                                   piece.timing) for piece in pieces)
        voice = VoiceIR((RecognitionHypothesis(
            text, 0.9, "parakeet-unified", words),))
        plan = build_consequence_plan(voice, audio_duration=duration)
        for risk in plan.risks:
            self.assertNotIn("word-evidence-unavailable", risk.uncertainty)
            self.assertIsNotNone(risk.audio_start)

    def test_attached_punctuation_is_left_out_of_the_word(self):
        text = "Hello world, this is a test."
        pieces = project_word_evidence(text, (
            helper("Hello", 0.88, 0.96), helper("world,", 1.04, 1.52, 0.8),
            helper("this", 1.6, 1.7), helper("is", 1.76, 1.84),
            helper("a", 1.84, 1.92), helper("test.", 1.92, 2.4)), 2.5)
        self.assert_usable(text, pieces, 2.5)
        self.assertEqual([piece.text for piece in pieces],
                         ["Hello", "world", "this", "is", "a", "test"])
        self.assertEqual((pieces[1].start, pieces[1].end, pieces[1].confidence),
                         (1.04, 1.52, 0.8))

    def test_money_splits_into_its_lexical_tokens(self):
        text = "Pay $1,250.50 to Dana Lee on 3:15 p.m. Friday."
        pieces = project_word_evidence(text, (
            helper("Pay", 0.1, 0.3), helper("$1,250.50", 0.4, 1.3, 0.7),
            helper("to", 1.3, 1.4), helper("Dana", 1.5, 1.8),
            helper("Lee", 1.8, 2.0), helper("on", 2.0, 2.1),
            helper("3:15", 2.2, 2.6), helper("p.m.", 2.6, 2.9),
            helper("Friday.", 3.0, 3.4)), 3.5)
        self.assert_usable(text, pieces, 3.5)
        self.assertEqual(
            [piece.text for piece in pieces],
            ["Pay", "1", "250.50", "to", "Dana", "Lee", "on", "3", "15",
             "p.m", "Friday"])
        one, amount = pieces[1], pieces[2]
        self.assertEqual((one.start, amount.end), (0.4, 1.3))
        self.assertEqual(one.end, amount.start)
        self.assertEqual({one.confidence, amount.confidence}, {0.7})

    def test_words_emitted_on_one_frame_share_out_its_span(self):
        # Every token after the first on one 80 ms frame has no length.
        text = "OK, so I go."
        pieces = project_word_evidence(text, (
            helper("OK,", 0.32, 0.32), helper("so", 0.32, 0.32),
            helper("I", 0.32, 0.4), helper("go.", 0.48, 0.56)), 1.0)
        self.assert_usable(text, pieces, 1.0)
        self.assertEqual(pieces[0].start, 0.32)
        self.assertEqual(pieces[2].end, 0.4)
        # Shared by character count: "OK," 3, "so" 2, "I" 1.
        self.assertAlmostEqual(pieces[0].end - pieces[0].start, 0.04)
        self.assertAlmostEqual(pieces[1].end - pieces[1].start, 0.08 * 2 / 6)

    def test_an_end_rounded_past_the_clip_is_clamped(self):
        text = "Done."
        pieces = project_word_evidence(text, (helper("Done.", 0.5, 1.0015),),
                                       1.0)
        self.assert_usable(text, pieces, 1.0)
        self.assertEqual(pieces[-1].end, 1.0)

    def test_a_lone_mark_is_carried_by_no_token(self):
        text = "Wait - what?"
        pieces = project_word_evidence(text, (
            helper("Wait", 0.1, 0.3), helper("-", 0.3, 0.4),
            helper("what?", 0.4, 0.7)), 1.0)
        self.assert_usable(text, pieces, 1.0)
        self.assertEqual([piece.text for piece in pieces], ["Wait", "what"])

    def test_words_that_do_not_spell_the_text_are_no_evidence(self):
        words = (helper("Hello", 0.1, 0.3), helper("world.", 0.4, 0.8))
        for text in ("Hello there.", "Hello world again.", "Hello.", "",
                     "Hello, world."[:5]):
            with self.subTest(text=text):
                self.assertIsNone(project_word_evidence(text, words, 1.0))
        self.assertIsNotNone(
            project_word_evidence("HELLO World!", words, 1.0))

    def test_an_empty_or_overlapping_span_is_no_evidence(self):
        text = "one two"
        for words in (
                (helper("one", 0.1, 0.1), helper("two", 0.3, 0.5)),
                (helper("one", 0.1, 0.6), helper("two", 0.3, 0.5)),
                (helper("one", 0.1, 0.10005), helper("two", 0.2, 0.5)),
                (helper("one", 0.1, 0.3), helper("two", 1.0, 1.2))):
            with self.subTest(words=words):
                self.assertIsNone(project_word_evidence(text, words, 1.0))

    def test_no_words_or_no_clip_is_no_evidence(self):
        for words, duration in (((), 1.0),
                                ((helper("one", 0.1, 0.2),), 0.0),
                                ((helper("one", 0.1, 0.2),), float("nan")),
                                ((helper("one", 0.1, 0.2),), True)):
            with self.subTest(duration=duration):
                self.assertIsNone(
                    project_word_evidence("one", words, duration))


class PausePunctuationSwitchTests(unittest.TestCase):
    """Uncalibrated timed words never place commas or paragraphs."""

    def voice(self, **switches):
        words = (
            WordEvidence("First", 0.0, 0.3), WordEvidence("point", 0.35, 0.8),
            WordEvidence("Second", 1.9, 2.2), WordEvidence("point", 2.25, 2.7))
        return VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "First point. Second point.", 0.8, "parakeet-unified",
                words),),
            prosody=(ProsodyEvent("pause", 0.9, 1.0),),
            **switches)

    def test_pauses_place_punctuation_only_when_allowed(self):
        compiler = VoiceCompiler()
        self.assertEqual(compiler.compile(self.voice()).text,
                         "First point.\n\nSecond point.")
        self.assertEqual(
            compiler.compile(self.voice(pause_punctuation=False)).text,
            "First point. Second point.")

    def test_a_rising_question_is_not_a_pause(self):
        voice = VoiceIR(
            hypotheses=(RecognitionHypothesis(
                "Can we ship this.", 0.64, "parakeet-unified", (
                    WordEvidence("Can", 0.0, 0.2),
                    WordEvidence("we", 0.25, 0.4),
                    WordEvidence("ship", 1.6, 1.8),
                    WordEvidence("this", 1.85, 2.0))),),
            prosody=(ProsodyEvent("pause", 0.5, 1.0),
                     ProsodyEvent("rising_end", 2.0, strength=0.8)),
            pause_punctuation=False)
        self.assertEqual(VoiceCompiler().compile(voice).text,
                         "Can we ship this?")


class DictionarySpellingTests(unittest.TestCase):
    """The clean-up writes a dictionary term in its own form where the words
    heard equal it, ignoring only spaces, hyphens and case."""

    TERMS = ("GitHub", "iPhone", "kubectl", "Whisper Face", "Copilot",
             "Wi-Fi", "Priyanka", "Qwen", "Mark", "Grace", "US", "Notable",
             "voice_compiler.py", "Tailscale", "Chidinma")

    def spell(self, text):
        return apply_dictionary_spelling(text, self.TERMS)[0]

    def test_must_fix(self):
        for heard, written in (
                ("push it to git hub", "push it to GitHub"),
                ("push it to Git-Hub", "push it to GitHub"),
                ("push it to github", "push it to GitHub"),
                ("my iphone died", "my iPhone died"),
                ("run kube ctl now", "run kubectl now"),
                ("Kube ctl is installed.", "Kubectl is installed."),
                ("open whisperface", "open Whisper Face"),
                ("open whisper face", "open Whisper Face"),
                ("open whisper-face", "open Whisper Face"),
                ("ask co-pilot", "ask Copilot"),
                ("ask co pilot", "ask Copilot"),
                ("the wifi is down", "the Wi-Fi is down"),
                ("the wi fi is down", "the Wi-Fi is down"),
                ("call priya nka", "call Priyanka"),
                ("try qwen.", "try Qwen."),
                ("(git hub)", "(GitHub)"),
                # No part of the join is an ordinary word.
                ("Chi Din Ma approved it.", "Chidinma approved it."),
        ):
            with self.subTest(heard=heard):
                self.assertEqual(self.spell(heard), written)

    def test_must_keep(self):
        for heard in (
                # A case change alone never touches an ordinary word.
                "mark the date", "say grace", "tell us now",
                # Only raises letters: capitals the recognizer wrote stay.
                "GITHUB is down", "Iphone sales",
                # Punctuation or a line break between words is not a join.
                "git, hub", "git. Hub", "git\nhub",
                # No join takes away a negation.
                "not able to come",
                # A larger word that merely contains a term.
                "githubbed", "qwens",
                # Addresses, paths and handles.
                "mail git@hub.com", "see git/hub", "ping @github",
                "edit Voice_compiler.py",
                # Already the term's own form.
                "GitHub and iPhone and Mark",
                # The near miss: ordinary words that happen to spell a term
                # when joined. Without the term pass finding the term
                # there, they are what was said.
                "The fish has a shiny tail scale.",
                "I phone her every day.",
        ):
            with self.subTest(heard=heard):
                self.assertEqual(self.spell(heard), heard)

    def test_a_join_or_split_never_writes_or_splits_an_ordinary_word(self):
        self.assertEqual(apply_dictionary_spelling(
            "every one came", ("Everyone",))[0], "every one came")
        self.assertEqual(apply_dictionary_spelling(
            "it was today", ("To Day",))[0], "it was today")
        # Ordinary heard words may still join into a term that is not one.
        self.assertEqual(apply_dictionary_spelling(
            "log in to sales force", ("Salesforce",))[0],
            "log in to Salesforce")

    @staticmethod
    def candidate(term, heard, debt=2.0):
        from parrot_core import VocabularyCandidate, VocabularyStep
        return VocabularyCandidate(
            term, term, heard, (1.0, 1.4), 1.7, debt, 1.0, 0.4,
            (VocabularyStep(1, "x", 0, 0.5),))

    def test_a_join_over_an_ordinary_word_needs_the_term_pass(self):
        said = "Connect to the server over tail scale."
        # Alone, the words are left as heard.
        self.assertEqual(self.spell(said), said)
        # The term pass found the term over those same words, within its
        # debt budget: it is written.
        found = [self.candidate("Tailscale", "tail scale.")]
        self.assertEqual(
            apply_dictionary_spelling(said, self.TERMS, found)[0],
            "Connect to the server over Tailscale.")
        # Over budget, for another term, or over other words: no support.
        from voice_compiler import VOCABULARY_DEBT_BUDGET
        for evidence in (
                [self.candidate("Tailscale", "tail scale",
                                VOCABULARY_DEBT_BUDGET + 0.1)],
                [self.candidate("Priyanka", "tail scale")],
                [self.candidate("Tailscale", "scale")]):
            with self.subTest(evidence=evidence[0].term):
                self.assertEqual(apply_dictionary_spelling(
                    said, self.TERMS, evidence)[0], said)
        # "i phone" takes in the word "I" too.
        self.assertEqual(apply_dictionary_spelling(
            "my i phone died", self.TERMS,
            [self.candidate("iPhone", "i phone")])[0], "my iPhone died")

    def test_reports_what_it_replaced(self):
        self.assertEqual(
            apply_dictionary_spelling("git hub and iphone", self.TERMS),
            ("GitHub and iPhone", ["git hub", "iphone"]))
        self.assertEqual(apply_dictionary_spelling("mark", self.TERMS),
                         ("mark", []))
        self.assertEqual(apply_dictionary_spelling("github", ()),
                         ("github", []))


class VocabularyPolicyTests(unittest.TestCase):
    """The term pass's decision policy, in shadow."""

    def setUp(self):
        from voice_compiler import VocabularyTerm
        self.terms = (VocabularyTerm("Priyanka"),
                      VocabularyTerm("Kubernetes"),
                      VocabularyTerm("Qwen", "corrected", ("Gwen",)),
                      VocabularyTerm("Screenname", "screen"))

    @staticmethod
    def words(text, step=0.4):
        return [HelperWord(word, index * step, index * step + 0.3, 0.6)
                for index, word in enumerate(text.split())]

    @staticmethod
    def candidate(term, heard_indices, *, heard, debt=2.0, prob=0.6,
                  step=0.4):
        from parrot_core import VocabularyCandidate, VocabularyStep
        return VocabularyCandidate(
            term, term, heard, tuple(index * step for index in heard_indices),
            heard_indices[-1] * step + 0.3, debt, 1.0, prob,
            (VocabularyStep(1, "x", 0, 0.5),))

    def decide(self, text, candidates, **options):
        from voice_compiler import apply_vocabulary_evidence
        return apply_vocabulary_evidence(
            text, self.words(text), candidates, self.terms, **options)

    def test_a_sound_alike_unsure_cheap_detour_would_be_written(self):
        from voice_compiler import vocabulary_alternative
        text = "Ask Prea ka about it."
        decisions = self.decide(text, [self.candidate(
            "Priyanka", [1, 2], heard="Prea ka")])
        self.assertEqual([(d.accepted, d.reason) for d in decisions],
                         [(True, "would-write")])
        # In shadow nothing is applied: the alternative is computed apart.
        self.assertEqual(vocabulary_alternative(text, decisions),
                         "Ask Priyanka about it.")

    def test_punctuation_and_a_possessive_are_kept(self):
        from voice_compiler import vocabulary_alternative
        text = "That was Prea ka's."
        decisions = self.decide(text, [self.candidate(
            "Priyanka", [2, 3], heard="Prea ka's.")])
        self.assertEqual(vocabulary_alternative(text, decisions),
                         "That was Priyanka's.")

    def test_each_guard_refuses_with_its_reason(self):
        cases = (
            ("Ask Prea ka now.", self.candidate(
                "Priyanka", [1, 2], heard="Prea ka", debt=5.5),
             "over-budget"),
            ("Ask Prea ka now.", self.candidate(
                "Priyanka", [1, 2], heard="Prea ka", prob=0.995), "sure"),
            ("Ask Dexter now.", self.candidate(
                "Priyanka", [1], heard="Dexter"), "not-sound-alike"),
            ("Ask the now.", self.candidate(
                "Priyanka", [1], heard="the"), "common-word"),
            ("Ask Prea 2 now.", self.candidate(
                "Priyanka", [1, 2], heard="Prea 2"), "protected"),
            ("Mail prea@ka.com now.", self.candidate(
                "Priyanka", [1], heard="prea@ka.com"), "protected"),
            ("Ask Screen name now.", self.candidate(
                "Screenname", [1, 2], heard="Screen name"), "not-listed"),
            ("Ask Kubernetes now.", self.candidate(
                "Kubernetes", [1], heard="Kubernetes"), "spelling-rule"),
            ("Ask pre a kay a now.", self.candidate(
                "Priyanka", [1, 2, 3, 4], heard="pre a kay a"), "too-long"),
            ("Ask Prea ka now.", self.candidate(
                "Priyanka", [3, 4], heard="Prea ka"), "not-placed"),
        )
        for text, candidate, reason in cases:
            with self.subTest(reason=reason):
                decisions = self.decide(text, [candidate])
                self.assertEqual([(d.accepted, d.reason) for d in decisions],
                                 [(False, reason)])

    def test_an_alias_from_a_proved_correction_passes_the_sound_check(self):
        decisions = self.decide("Run Gwen locally.", [self.candidate(
            "Qwen", [1], heard="Gwen")])
        self.assertEqual([d.reason for d in decisions], ["would-write"])

    def test_command_mode_and_the_blast_radius(self):
        text = "Ask Prea ka and Cooper Netties and Prea ka again."
        candidates = [
            self.candidate("Priyanka", [1, 2], heard="Prea ka", debt=1.0),
            self.candidate("Kubernetes", [4, 5], heard="Cooper Netties",
                           debt=2.0),
            self.candidate("Priyanka", [7, 8], heard="Prea ka", debt=3.0),
            self.candidate("Kubernetes", [2, 3], heard="ka and", debt=4.0),
        ]
        reasons = sorted(d.reason for d in self.decide(text, candidates))
        self.assertEqual(reasons.count("would-write"), 2)
        self.assertIn("limit", reasons)
        self.assertEqual({d.reason for d in self.decide(
            text, candidates, mode="command")}, {"mode"})

    def test_the_term_list_is_scoped(self):
        from voice_compiler import vocabulary_terms
        terms = vocabulary_terms(
            ["GitHub", "AI", "Mark", "Qwen3", "Priyanka", "Re-Debrid",
             "priyanka"], [("Gwen", "Qwen"), ("pre a car", "Priyanka")])
        self.assertEqual(
            [(t.text, t.source, t.aliases) for t in terms],
            [("GitHub", "typed", ()), ("Priyanka", "typed", ("pre a car",)),
             ("Re-Debrid", "typed", ()), ("Qwen", "corrected", ("Gwen",))])

    def test_thresholds_are_the_probes(self):
        import voice_compiler
        self.assertEqual(voice_compiler.VOCABULARY_DEBT_BUDGET, 5.0)
        self.assertEqual(voice_compiler.VOCABULARY_REPORT_DEBT, 5.0)
        self.assertEqual(voice_compiler.VOCABULARY_SOUND_ALIKE_MIN, 0.7)
        self.assertEqual(voice_compiler.VOCABULARY_UNSURE_BELOW, 0.99)
        self.assertEqual(voice_compiler.VOCABULARY_MAX_PER_DICTATION, 2)

    def test_a_piece_the_model_did_not_offer_is_never_written(self):
        from parrot_core import VocabularyCandidate, VocabularyStep
        good = self.candidate("Priyanka", [1, 2], heard="Prea ka")
        for steps in ((VocabularyStep(1, "x", 64, 0.5),),
                      (VocabularyStep(1, "x", 0, 0.5),
                       VocabularyStep(2, "y", -1, 0.5)),
                      ()):
            with self.subTest(steps=steps):
                candidate = VocabularyCandidate(
                    good.term, good.written, good.heard,
                    good.heard_word_starts, good.end, good.debt,
                    good.max_step_gap, good.heard_min_prob, steps)
                decisions = self.decide("Ask Prea ka now.", [candidate])
                self.assertEqual([(d.accepted, d.reason) for d in decisions],
                                 [(False, "not-a-candidate")])


class VocabularyApplicationTests(unittest.TestCase):
    """"Favor my words": the accepted decisions written, behind rails."""

    # The policy tests' terms, words and reports, without their tests.
    setUp = VocabularyPolicyTests.setUp
    words = staticmethod(VocabularyPolicyTests.words)
    candidate = staticmethod(VocabularyPolicyTests.candidate)
    decide = VocabularyPolicyTests.decide

    def apply(self, text, candidates, **options):
        from voice_compiler import apply_vocabulary_decisions
        return apply_vocabulary_decisions(
            text, self.words(text), self.decide(text, candidates), **options)

    def test_an_accepted_decision_is_written_into_text_and_words(self):
        text = "Ask Prea ka about it."
        application = self.apply(text, [self.candidate(
            "Priyanka", [1, 2], heard="Prea ka")])
        self.assertEqual(application.refused, "")
        self.assertEqual(application.text, "Ask Priyanka about it.")
        self.assertEqual(application.changes, (("Prea ka", "Priyanka"),))
        self.assertEqual([word.text for word in application.words],
                         ["Ask", "Priyanka", "about", "it."])
        written = application.words[1]
        # The written word spans the heard words' time.
        self.assertAlmostEqual(written.start, 0.4)
        self.assertAlmostEqual(written.end, 1.1)
        # Nothing accepted: nothing changes and nothing is refused.
        same = self.apply(text, [self.candidate(
            "Priyanka", [1, 2], heard="Prea ka", debt=9.0)])
        self.assertEqual((same.text, same.applied, same.refused),
                         (text, (), ""))

    def test_each_rail_applies_nothing(self):
        text = "Ask Prea ka about it."
        found = [self.candidate("Priyanka", [1, 2], heard="Prea ka")]
        for options, reason in (
                ({"mode": "code"}, "mode"),
                ({"mode": "command"}, "mode"),
                ({"language": "de"}, "language"),
                ({"language": ""}, "language"),
                ({"verbatim": True}, "verbatim"),
                ({"limit": 0}, "limit")):
            with self.subTest(reason=reason):
                application = self.apply(text, found, **options)
                self.assertEqual(application.refused, reason)
                self.assertEqual(application.text, text)
                self.assertEqual(application.applied, ())
                self.assertEqual(application.changes, ())

    def test_never_more_than_the_limit_per_dictation(self):
        from voice_compiler import (VOCABULARY_MAX_PER_DICTATION,
                                    VocabularyDecision,
                                    apply_vocabulary_decisions)
        text = "Prea ka and Prea ka and Prea ka."
        words = self.words(text)
        decisions = [VocabularyDecision(
            "Priyanka", "Prea ka", start, start + 7, 1.0, 0.8, True,
            "would-write", "Priyanka") for start in (0, 12, 24)]
        # Three accepted is past the limit: the whole set is refused.
        application = apply_vocabulary_decisions(text, words, decisions)
        self.assertEqual(application.refused, "limit")
        self.assertEqual(application.text, text)
        # The policy's limit, written, is exactly what is applied.
        allowed = apply_vocabulary_decisions(
            text, words, decisions[:VOCABULARY_MAX_PER_DICTATION])
        self.assertEqual(len(allowed.applied), VOCABULARY_MAX_PER_DICTATION)
        self.assertEqual(allowed.text, "Priyanka and Priyanka and Prea ka.")
        # And the policy never accepts more than the limit.
        many = [self.candidate("Priyanka", [index, index + 1],
                               heard="Prea ka", debt=1.0 + index)
                for index in (0, 3, 6)]
        self.assertLessEqual(
            sum(d.accepted for d in self.decide(text, many)),
            VOCABULARY_MAX_PER_DICTATION)

    def test_a_decision_that_no_longer_fits_its_text_applies_nothing(self):
        from voice_compiler import VocabularyDecision, \
            apply_vocabulary_decisions
        text = "Ask Prea ka about it."
        words = self.words(text)
        moved = VocabularyDecision("Priyanka", "Prea ka", 3, 10, 1.0, 0.8,
                                   True, "would-write", "Priyanka")
        self.assertEqual(apply_vocabulary_decisions(
            text, words, [moved]).refused, "span")
        crossed = [VocabularyDecision(
            "Priyanka", "Prea ka", 4, 11, 1.0, 0.8, True, "would-write",
            "Priyanka"), VocabularyDecision(
            "Kubernetes", "ka about", 9, 17, 1.0, 0.8, True, "would-write",
            "Kubernetes")]
        self.assertEqual(apply_vocabulary_decisions(
            text, words, crossed).refused, "overlap")
        other = VocabularyDecision("Priyanka", "Prea ka", 4, 11, 1.0, 0.8,
                                   True, "would-write", "Someone else")
        self.assertEqual(apply_vocabulary_decisions(
            text, words, [other]).refused, "written")


if __name__ == "__main__":
    unittest.main()
