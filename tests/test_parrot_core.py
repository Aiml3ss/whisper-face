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
    find_self_correction,
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
            "um Tuesday actually Wednesday new paragraph I mean, ship it",
            self_correction=True)
        self.assertEqual(plan.text, "Wednesday\n\nship it")
        self.assertEqual(
            plan.edit_kinds,
            ["spoken_structure", "remove_filler",
             "remove_discourse_filler", "self_correction"],
        )

    def test_punctuated_fillers_structure_and_correction_compile_the_same(self):
        plan = compile_cleanup(
            "Um, Tuesday, actually Wednesday. New paragraph. I mean, ship it.",
            self_correction=True)
        self.assertEqual(plan.text, "Wednesday.\n\nship it.")
        self.assertEqual(
            plan.edit_kinds,
            ["spoken_structure", "remove_filler",
             "remove_discourse_filler", "self_correction"],
        )

    def test_self_correction_is_off_unless_asked_for(self):
        # What the real recognizer wrote for a speaker contrasting two
        # things. The words are the same as a correction's, so by default
        # both are typed as spoken.
        for spoken in (
            "I thought it was Tuesday, actually Wednesday.",
            "The invoice says 400, actually 450",
            "We expected 20, actually 12 showed up.",
            "I guessed March, actually April.",
            "Everyone assumed Monday, actually Tuesday.",
            "She said five, actually six",
            "Out of ten, actually four past.",
            "Let's meet Tuesday, actually Wednesday",
            "It starts at 3, actually 4.",
            "Send five actually six copies.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])
                self.assertFalse(plan.needs_semantic_cleanup)
                self.assertEqual(compile_code_dictation(spoken).text, spoken)
                asked = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(asked.edit_kinds, ["self_correction"])
                self.assertEqual(
                    compile_code_dictation(
                        spoken, self_correction=True).text, asked.text)

    def test_actually_as_an_adverb_changes_nothing(self):
        for spoken in (
            "I didn't actually finish the report",
            "I don't actually know the answer.",
            "she is not actually coming tomorrow",
            "The payment was not actually approved.",
            "We never actually shipped version 2.",
            "Nobody actually reviewed the contract.",
            "Siobhan actually approved the budget.",
            "The price is actually 40 dollars.",
            "The total is actually 500 dollars",
            "that is actually a good idea",
            "I actually think we should wait.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, spoken)
                self.assertNotIn("self_correction", plan.edit_kinds)
                self.assertFalse(plan.needs_semantic_cleanup)

    def test_self_correction_resolves_two_items_of_the_same_kind(self):
        for spoken, expected in (
            # Weekdays, bare and in the forms a recognizer punctuates.
            ("Move the meeting to Tuesday actually Wednesday.",
             "Move the meeting to Wednesday."),
            ("Move the meeting to Tuesday, actually Wednesday.",
             "Move the meeting to Wednesday."),
            ("Move the meeting to Tuesday — actually, Wednesday.",
             "Move the meeting to Wednesday."),
            # Months, with and without a day.
            ("move the meeting from March 3 actually March 4",
             "move the meeting from March 4"),
            ("Move the meeting from March 3, actually March 4.",
             "Move the meeting from March 4."),
            ("Move the meeting from March 3, actually 4.",
             "Move the meeting from March 4."),
            ("It ships in March, actually April.", "It ships in April."),
            # Numbers, as digits or words, with a unit or a currency.
            ("charge 45 actually 54 dollars to the travel budget",
             "charge 54 dollars to the travel budget"),
            ("Charge 45 dollars, actually 54 dollars.",
             "Charge 54 dollars."),
            ("Charge 45 dollars, actually 54.", "Charge 54 dollars."),
            ("Pay $1,500, actually $1,600.", "Pay $1,600."),
            ("Raise it by 4.5%, actually 5.5%.", "Raise it by 5.5%."),
            ("We need three, actually four, reviewers.",
             "We need four, reviewers."),
            ("Order twenty five, actually thirty.", "Order thirty."),
            ("I have 3 apples, actually 4 apples.", "I have 4 apples."),
            # Clock times.
            ("Meet at 3 pm, actually 4 pm.", "Meet at 4 pm."),
            ("Meet at 3 p.m., actually 4 p.m.", "Meet at 4 p.m."),
            ("Meet at 3, actually 4 pm.", "Meet at 4 pm."),
            ("Meet at 3 pm actually 4", "Meet at 4 pm"),
            ("Meet at 10:30, actually 11:30.", "Meet at 11:30."),
            ("Call at noon, actually 1 pm.", "Call at 1 pm."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["self_correction"])

    def test_self_correction_needs_both_sides_to_be_the_same_kind(self):
        for spoken in (
            "send the draft to Alex actually Alexis Chen by Friday",
            "use HTTP actually HTTPS for the callback URL",
            "See you Tuesday, actually 4 works better.",
            "We may actually march tomorrow.",
            "Pay $45, actually 4 pm is too late.",
            "Meet at 10:30, actually 45 dollars is fine.",
            "I counted 30 and, actually 31 is right.",
            "We shipped version 2, actually version 3.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, spoken)
                self.assertNotIn("self_correction", plan.edit_kinds)

    def test_a_number_is_one_item_inside_a_self_correction(self):
        # "30" is the minutes of a clock time, not a number of its own.
        raw = "Meet at 3:30, actually 4 dollars is the fee."
        self.assertEqual(compile_cleanup(raw, self_correction=True).text, raw)
        self.assertEqual(find_self_correction("at 3:30 actually 4:30"),
                         (3, 21, "4:30"))
        self.assertIsNone(find_self_correction("it is actually fine"))

    def test_self_correction_leaves_a_compound_item_as_spoken(self):
        # A range, score, size or grouped number is one item written as
        # several. Correcting its last word alone splices the two versions
        # ("From 9 to 9 to 6"), so neither version is touched.
        for spoken in (
            "From 9 to 5, actually 9 to 6.",
            "Monday to Friday, actually Monday to Thursday.",
            "The meeting runs from 2 to 3, actually 2 to 4.",
            "Call 555 1234, actually 555 1235.",
            "Score was 2 to 1, actually 3 to 1.",
            "It is 3 by 5, actually 4 by 6 inches.",
            "I was 1 of 10, actually 2 of 10.",
            "March to May, actually March to June.",
            "May 3 to May 5, actually May 3 to May 6.",
            "From March 3 to 5, actually 3 to 6.",
            "Open 9 - 5, actually 9 - 6.",
            "Open 9–5, actually 9–6.",
            "from nine to five, actually nine to six",
            "It took 3 hours 20 minutes, actually 3 hours 30 minutes.",
            "It took 3 hours 20 minutes, actually 4 hours.",
            "It took 3 hours and 20 minutes, actually 4 hours.",
            "He is 5 feet 10, actually 5 feet 11.",
            "It takes 3 days, actually 4 to 5 days.",
            "Open 9 am to 5 pm, actually 10 am to 6 pm.",
            "It is 2 point 5, actually 2 point 6.",
            # The restatement starts at an item the sentence already has.
            "Seat 5 people in room 3, actually 5 people in room 4.",
            "Open from 9 in the morning to 5, actually 9 in the morning to 6.",
            "Book Monday at noon for Friday, actually Monday at noon for "
            "Thursday.",
            # Which end of a range a lone item corrects is not knowable.
            "From 9 to 5, actually 6.",
            "Monday through Wednesday, actually Thursday.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, spoken)
                self.assertNotIn("self_correction", plan.edit_kinds)
                self.assertIsNone(find_self_correction(spoken))

    def test_self_correction_leaves_a_negated_item_as_spoken(self):
        # "Not Tuesday, actually Wednesday" names Wednesday as the answer.
        # Replacing Tuesday would leave "Not Wednesday", the opposite.
        for spoken in (
            "Not Tuesday, actually Wednesday.",
            "It's not 5, actually 6.",
            "The meeting isn't at 3, actually 4.",
            "The meeting isn’t at 3, actually 4.",
            "Monday, not Tuesday, actually Wednesday.",
            "We can't do Friday, actually Saturday works.",
            "No one, actually two.",
            "We cannot ship in March, actually April.",
            "Never on Monday, actually Tuesday.",
            "I have none on Friday, actually Saturday.",
            "Neither 3 nor 4, actually 5.",
            "Nothing ships Tuesday, actually Wednesday works.",
            "Nobody is free at 3, actually 4.",
            "Come without 45 dollars, actually 54.",
            "NOT TUESDAY, ACTUALLY WEDNESDAY.",
            "Don't call at 3 pm — actually, 4 pm.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, spoken)
                self.assertNotIn("self_correction", plan.edit_kinds)
                self.assertIsNone(find_self_correction(spoken))

    def test_self_correction_reads_a_negation_in_its_own_clause_only(self):
        for spoken, expected in (
            ("Let's meet Tuesday, actually Wednesday.",
             "Let's meet Wednesday."),
            # The negation belongs to an earlier clause or sentence.
            ("No, Tuesday, actually Wednesday.", "No, Wednesday."),
            ("I can't make it. Let's meet Tuesday, actually Wednesday.",
             "I can't make it. Let's meet Wednesday."),
            ("Don't come at 3, come at 4, actually 5.",
             "Don't come at 3, come at 5."),
            # "No." the abbreviation, and words that only contain one.
            ("Send No. 5, actually 6.", "Send No. 6."),
            ("I know it is Tuesday, actually Wednesday.",
             "I know it is Wednesday."),
            ("Another note is due Monday, actually Tuesday.",
             "Another note is due Tuesday."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["self_correction"])

    def test_self_correction_still_resolves_an_item_beside_other_items(self):
        for spoken, expected in (
            # A date and a time are two items, not one.
            ("Let's meet March 5 at 3, actually 4 pm.",
             "Let's meet March 5 at 4 pm."),
            ("Let's meet on the 5th at 3, actually 4.",
             "Let's meet on the 5th at 4."),
            ("I ordered 2 pizzas for 45 dollars, actually 54.",
             "I ordered 2 pizzas for 54 dollars."),
            ("Charge 45, actually 54 dollars for 2 tickets.",
             "Charge 54 dollars for 2 tickets."),
            ("Send 3 copies on Tuesday, actually Wednesday.",
             "Send 3 copies on Wednesday."),
            ("In 2024, about 45, actually 54 people came.",
             "In 2024, about 54 people came."),
            # "may" the verb and the "ten" in "often" are not items.
            ("It may be May 5, actually May 6.", "It may be May 6."),
            ("We often meet at 3, actually 4.", "We often meet at 4."),
            ("Tuesday, actually Wednesday night or Thursday.",
             "Wednesday night or Thursday."),
            # An equal item in an earlier sentence is not being restated.
            ("Room 4 is free. Seat 3, actually 4 people there.",
             "Room 4 is free. Seat 4 people there."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, self_correction=True)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["self_correction"])

    def test_self_correction_reads_the_words_around_a_bounded_span(self):
        # The proof validator checks a span of a longer source; the words
        # outside the span still decide whether the item is a compound.
        self.assertEqual(
            find_self_correction("5, actually 6"), (0, 13, "6"))
        self.assertIsNone(find_self_correction(
            "5, actually 6", context_before="From 9 to "))
        self.assertIsNone(find_self_correction(
            "20 minutes, actually 4", context_before="It took 3 hours ",
            context_after=" hours."))
        self.assertIsNone(find_self_correction(
            "3, actually 4", context_after=" to 5 days."))
        self.assertEqual(
            find_self_correction(
                "3, actually 4", context_before="Meet March 5 at ",
                context_after=" pm."),
            (0, 13, "4"))

    def test_spoken_structure_takes_its_own_punctuation(self):
        for spoken, expected in (
            ("First line is approved. New line. Second line needs revision.",
             "First line is approved.\nSecond line needs revision."),
            ("First line is approved, new line, second line needs revision.",
             "First line is approved\nsecond line needs revision."),
            ("First line is approved. New line second line needs revision.",
             "First line is approved.\nsecond line needs revision."),
            ("Thanks for the update. New paragraph. I will review tonight.",
             "Thanks for the update.\n\nI will review tonight."),
            ("Thanks for the update — new paragraph — I will review tonight.",
             "Thanks for the update\n\nI will review tonight."),
            # A break before any text has nothing to separate.
            ("New paragraph. Thanks for the update.",
             "Thanks for the update."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["spoken_structure"])
                self.assertFalse(plan.needs_semantic_cleanup)

    def test_new_line_as_a_noun_phrase_is_not_a_command(self):
        for spoken in (
            "We are launching a new line of products next quarter",
            "We are launching a new line of products next quarter.",
            "Let me start a new line of thinking.",
            "Add new line items to the invoice.",
            "The new paragraph reads better.",
            "It is a whole new line for us.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])

    def test_a_counted_new_line_noun_phrase_is_not_a_command(self):
        # A count in front and a plural after make the phrase a noun:
        # "two new line cooks" are cooks. The first is what the real
        # recognizer wrote.
        for spoken in (
            "We hired two new line cooks.",
            "We hired 2 new line cooks.",
            "We hired twenty-five new line cooks.",
            "Two new line cooks start Monday.",
            "I drafted three new paragraph styles.",
            "The kitchen has 12 new line cooks this year.",
            "Order 1,500 new line cards.",
            "We hired several new line cooks.",
            "Do we have any new line cooks?",
            "There is no new line at the end of the file.",
            "Those new line cooks are fast.",
            "Add one more new line after the title.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])

    def test_a_number_before_an_unpaused_command_does_not_block_it(self):
        # The real recognizer writes an unpaused command without marks, so
        # a count before it cannot make it prose on its own.
        for spoken, expected in (
            ("Milk new line eggs new line bread", "Milk\neggs\nbread"),
            ("First item new line second item", "First item\nsecond item"),
            ("Step 1 new line step 2 new line step 3",
             "Step 1\nstep 2\nstep 3"),
            ("Call me at 5 new line see you then",
             "Call me at 5\nsee you then"),
            ("Eggs 12 new line milk 2 new line bread",
             "Eggs 12\nmilk 2\nbread"),
            ("The total is twenty new paragraph thanks for the update",
             "The total is twenty\n\nthanks for the update"),
            ("Room 4 new line this is the big one",
             "Room 4\nthis is the big one"),
            # A pause puts a mark between the count and the command.
            ("We need 2. New line. Cooks start Monday.",
             "We need 2.\nCooks start Monday."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["spoken_structure"])

    def test_a_count_and_a_plural_around_a_command_keep_the_words(self):
        # Command or noun phrase cannot be told apart here, so nothing is
        # removed. "new line item" is a noun wherever it appears.
        for spoken in (
            "Eggs 12 new line apples 6",
            "Item 2 new line item 3",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])

    def test_scratch_that_takes_its_own_punctuation(self):
        for spoken, expected in (
            ("Move the meeting to Tuesday. Scratch that. Move it to "
             "Wednesday.", "Move it to Wednesday."),
            ("Keep this sentence. Remove this part. Scratch that. Use this "
             "instead.", "Keep this sentence. Use this instead."),
            ("Keep this sentence. Remove this part, scratch that, use this "
             "instead.", "Keep this sentence. use this instead."),
            ("Tell Morgan the migration starts at six, scratch that, at "
             "seven.", "Tell Morgan the migration starts at seven."),
            ("Tell Morgan the migration starts at six. Scratch that, at "
             "seven.", "Tell Morgan the migration starts at seven."),
            ("Transfer 1500 dollars to savings, scratch that, to checking.",
             "Transfer 1500 dollars to checking."),
            # The dropped sentence leaves an earlier spoken line break alone.
            ("Line one. New line. Line two. Scratch that. Line three.",
             "Line one.\nLine three."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, expected)
                self.assertIn("scratch_that", plan.edit_kinds)
                self.assertFalse(plan.needs_semantic_cleanup)

    def test_scratch_that_never_cuts_inside_a_number_or_an_amount(self):
        for spoken, expected in (
            ("transfer 1,500 dollars to savings scratch that to checking",
             "transfer 1,500 dollars to checking"),
            ("Transfer 1,500 dollars to savings, scratch that, to checking.",
             "Transfer 1,500 dollars to checking."),
            ("pay $3.50 to Sam scratch that to Alex", "pay $3.50 to Alex"),
            ("Pay $3.50 to Sam, scratch that, to Alex.",
             "Pay $3.50 to Alex."),
            ("Version 2.5 ships to beta scratch that to production",
             "Version 2.5 ships to production"),
        ):
            with self.subTest(spoken=spoken):
                self.assertEqual(compile_cleanup(spoken).text, expected)

    def test_punctuated_ambiguous_scratch_that_still_fails_closed(self):
        raw = "Move the meeting Tuesday, scratch that, Wednesday."
        plan = compile_cleanup(raw)
        self.assertEqual(plan.text, raw)
        self.assertNotIn("scratch_that", plan.edit_kinds)
        self.assertTrue(plan.needs_semantic_cleanup)

    def test_a_hesitation_before_scratch_that_is_not_a_sentence_end(self):
        # Recognizers write a trailing-off pause as an ellipsis. The command
        # then interrupts its own sentence and must not drop all of it.
        for raw in (
            "Send it Tuesday... scratch that... Monday.",
            "Send it Tuesday… scratch that… Monday.",
            "Send it Tuesday... Scratch that. Monday.",
        ):
            with self.subTest(raw=raw):
                plan = compile_cleanup(raw)
                self.assertEqual(plan.text, raw)
                self.assertNotIn("scratch_that", plan.edit_kinds)
                self.assertTrue(plan.needs_semantic_cleanup)
        self.assertEqual(
            compile_cleanup(
                "Tell Morgan it starts at six... scratch that... at seven."
            ).text,
            "Tell Morgan it starts at seven.")
        # After a real sentence end the command still drops that sentence.
        self.assertEqual(
            compile_cleanup("Send it Tuesday. Scratch that... Monday.").text,
            "Monday.")

    def test_scratch_that_as_a_verb_phrase_is_not_a_command(self):
        for spoken in (
            "We need to rethink it. Let's scratch that idea and start over.",
            "I think we should scratch that plan entirely.",
            "We'll scratch that from the list.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])
                self.assertFalse(plan.needs_semantic_cleanup)

    def test_capitalized_um_and_uh_are_names_not_fillers(self):
        for spoken in (
            "I studied at UM for four years",
            "The UH campus is closed on Friday.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])

    def test_a_filler_spoken_as_its_own_sentence_leaves_no_punctuation(self):
        self.assertEqual(compile_cleanup("Um.").text, "")
        self.assertEqual(compile_cleanup("Hmm...").text, "")
        self.assertEqual(
            compile_cleanup("It works. Um. Anyway, next.").text,
            "It works. Anyway, next.")
        self.assertEqual(
            compile_cleanup("Um, so we ship Friday.").text,
            "so we ship Friday.")

    def test_a_filler_sound_inside_an_interjection_is_part_of_the_word(self):
        # "Uh-oh" and "uh huh" are words that mean something. Dropping the
        # filler-shaped half pasted "-oh, the build broke." and "Huh."
        for spoken in (
            "Uh-oh, the build broke.",
            "Uh oh, the build broke.",
            "Mm-hmm, sure.",
            "Mm hmm, sure.",
            "Uh-huh.",
            "Uh huh.",
            "She said uh-huh and left.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])
        for spoken, expected in (
            ("It is, uh, ready.", "It is ready."),
            ("Uh, oh well, ship it.", "oh well, ship it."),
            ("Hmm, let me think.", "let me think."),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["remove_filler"])

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
        punctuated = compile_cleanup(
            "First line is approved. New line. Second line needs revision.")
        self.assertEqual(
            punctuated.text,
            "First line is approved.\nSecond line needs revision.")
        self.assertFalse(punctuated.needs_semantic_cleanup)

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

    def test_a_number_past_the_announced_count_stays_inside_its_item(self):
        plan = compile_cleanup(
            "Here are two things. One, buy milk. Two, eggs, three dozen.")

        self.assertEqual(
            plan.text,
            "Here are two things:\n"
            "- Buy milk.\n"
            "- Eggs, three dozen.",
        )

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
        plan = compile_cleanup(
            "um, ship it Tuesday actually Wednesday", self_correction=True)
        self.assertEqual(plan.text, "ship it Wednesday")
        self.assertIn("remove_filler", [edit.kind for edit in plan.edits])
        punctuated = compile_cleanup(
            "Um, ship it Tuesday, actually Wednesday.", self_correction=True)
        self.assertEqual(punctuated.text, "ship it Wednesday.")

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
                plan = compile_cleanup(text, language, self_correction=True)
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
