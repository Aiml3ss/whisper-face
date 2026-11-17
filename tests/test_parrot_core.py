# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parrot_core import (  # noqa: E402
    HELPER_MAX_WORDS,
    HelperWord,
    ParakeetResult,
    Recognition,
    RecognitionWord,
    WORD_EVIDENCE_STATUSES,
    combine_word_evidence_status,
    helper_protocol,
    parakeet_result,
    parse_helper_words,
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
    words_settle_route,
    CONFIDENT_WORD,
    normalize_spacing,
    confidence_from_segments,
    recognition_words_from_segments,
    correction_similarity,
    find_restart,
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
        # A count in front makes the phrase a noun, whatever the noun is:
        # "two new line cooks" are cooks and "two new line staff" are
        # staff. The first is what the real recognizer wrote.
        for spoken in (
            "We hired two new line cooks.",
            "We hired 2 new line cooks.",
            "We hired twenty-five new line cooks.",
            "Two new line cooks start Monday.",
            "I drafted three new paragraph styles.",
            "The kitchen has 12 new line cooks this year.",
            "Order 1,500 new line cards.",
            # Not a regular plural: a mass noun, an irregular plural, a
            # possessive, the first word of a compound.
            "We hired two new line staff.",
            "We hired 12 new line staff.",
            "We hired twenty new line people.",
            "I need 2 new line cooks' aprons.",
            "We posted two new line cook positions.",
            "Two new line cook jobs opened today.",
            "We opened two new line manager roles.",
            "We need three new paragraph style options.",
            # Counts that are not numbers.
            "We hired a dozen new line cooks.",
            "We hired a couple new line cooks.",
            "We hired a couple of new line cooks.",
            "Hundreds of new line cooks applied.",
            "We hired several new line cooks.",
            "All new line cooks must sign in.",
            "Most new line cooks quit in a month.",
            "Do we have any new line cooks?",
            "There is no new line at the end of the file.",
            "Those new line cooks are fast.",
            "Add one more new line after the title.",
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken)
                self.assertEqual(plan.edits, [])

    def test_an_unpaused_command_needs_no_punctuation(self):
        # The real recognizer writes an unpaused command without marks.
        for spoken, expected in (
            ("Milk new line eggs new line bread", "Milk\neggs\nbread"),
            ("First item new line second item", "First item\nsecond item"),
            ("Dear team new paragraph the launch is on Friday.",
             "Dear team\n\nthe launch is on Friday."),
            # A pause puts a mark after a count, which then ends its line.
            ("We need 2. New line. Cooks start Monday.",
             "We need 2.\nCooks start Monday."),
            ("Step 1, new line, step 2, new line, step 3",
             "Step 1\nstep 2\nstep 3"),
            ("We need 2, new line, cooks start Monday.",
             "We need 2\ncooks start Monday."),
            ("The total is twenty. New paragraph. Thanks for the update.",
             "The total is twenty.\n\nThanks for the update."),
            # A price, a clock time and a phone number count nothing.
            ("The total is $40 new line thanks for the update",
             "The total is $40\nthanks for the update"),
            ("Call me at 3:30 new line see you then",
             "Call me at 3:30\nsee you then"),
            ("Call me at 5 pm new line see you then",
             "Call me at 5 pm\nsee you then"),
            ("Call me at 555-0100 new line see you then",
             "Call me at 555-0100\nsee you then"),
        ):
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, expected)
                self.assertEqual(plan.edit_kinds, ["spoken_structure"])

    def test_a_count_directly_before_an_unpaused_command_keeps_the_words(self):
        # "Room 4 new line guests arrive" and "12 new line staff arrive"
        # have the same shape, so nothing is removed: the command is typed
        # as words, where the speaker sees it. "new line item" is a noun
        # wherever it appears.
        for spoken in (
            "Step 1 new line step 2 new line step 3",
            "Step one new line step two new line step three",
            "Call me at 5 new line see you then",
            "Eggs 12 new line milk 2 new line bread",
            "Eggs 12 new line apples 6",
            "Room 4 new line this is the big one",
            "The total is twenty new paragraph thanks for the update",
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


# A phrase said twice by accident, and what is typed. The owner's own
# message comes first.
MUST_REPAIR = (
    ("Of course I can. I have nothing I have nothing going on",
     "Of course I can. I have nothing going on"),
    ("I have nothing I have nothing going on", "I have nothing going on"),
    ("I have I have to go", "I have to go"),
    ("we can we can do it", "we can do it"),
    ("it's a it's a problem", "it's a problem"),
    ("in the in the morning", "in the morning"),
    ("to the to the store", "to the store"),
    ("I I think", "I think"),
    # Twenty more, the harder ones.
    ("In the in the morning we left.", "In the morning we left."),
    ("Go to the, to the store.", "Go to the store."),
    ("I have nothing... I have nothing going on.",
     "I have nothing going on."),
    ("I have nothing — I have nothing going on.", "I have nothing going on."),
    ("I want to- I want to ask you something.",
     "I want to ask you something."),
    ("I want to - I want to ask you something.",
     "I want to ask you something."),
    ("Do you want do you want to come?", "Do you want to come?"),
    ("We need to talk... we need to talk about the budget.",
     "We need to talk about the budget."),
    ("I'm not sure I'm not sure what you mean.",
     "I'm not sure what you mean."),
    ("They're going they're going to love it.", "They're going to love it."),
    ("he's a he's a good guy", "he's a good guy"),
    ("Pass me the the salt.", "Pass me the salt."),
    ("The the report is late.", "The report is late."),
    ("Send it to to Dana.", "Send it to Dana."),
    ("It's one of of the best.", "It's one of the best."),
    ("I lost my my keys.", "I lost my keys."),
    ("That took an an hour.", "That took an hour."),
    ("We we need more time.", "We need more time."),
    ("And and then we left.", "And then we left."),
    ("I think that, I think that we should go.",
     "I think that we should go."),
    ("I said I said it was fine.", "I said it was fine."),
    ("the cat sat the cat sat on the mat", "the cat sat on the mat"),
    # A filler between the two copies goes first, then the repeat.
    ("I, um, I think so.", "I think so."),
    # Two separate restarts in one dictation.
    ("I have I have to go to the to the store.", "I have to go to the store."),
    # Plain restarts the first review found left as they were.
    ("Can you can you hear me?", "Can you hear me?"),
    ("Is it is it worth it?", "Is it worth it?"),
    ("Did you did you get my message?", "Did you get my message?"),
    ("Have you have you eaten yet?", "Have you eaten yet?"),
    ("I think I think I should go.", "I think I should go."),
    ("Can we can we talk later?", "Can we talk later?"),
    ("My mom my mom is coming over.", "My mom is coming over."),
    ("Let me let me check.", "Let me check."),
    ("So I so I told him.", "So I told him."),
    ("If you if you want, we can meet.", "If you want, we can meet."),
    ("There's a there's a problem with the order.",
     "There's a problem with the order."),
    ("That's what that's what I said.", "That's what I said."),
    ("It's it's fine.", "It's fine."),
    ("I'm I'm on my way.", "I'm on my way."),
    ("This is this is the best part.", "This is the best part."),
    ("Where's the where's the remote?", "Where's the remote?"),
    ("Because because it's late.", "Because it's late."),
    # A comma at the restart point after a word no phrase ends on.
    ("We can, we can do it on Friday.", "We can do it on Friday."),
    ("I want to, I want to ask you something.",
     "I want to ask you something."),
)

# Repeats a person meant, and anything the repair must not read.
MUST_KEEP = (
    # Three or more times is emphasis.
    "no way no way no way",
    "No way, no way, no way, I said.",
    "I I I think so.",
    "the the the end",
    "ha ha ha ha ha ha, very funny",
    # A sentence end between the copies.
    "Of course I can. I have nothing. I have nothing going on.",
    "I have nothing. I have nothing going on.",
    "Is it? Is it really?",
    # A single word outside the closed list.
    "no no, not that one",
    "That was very very good.",
    "Okay, bye bye now.",
    "He had had enough.",
    "He said that that was fine.",
    "Walla Walla is in Washington.",
    "I logged in in the morning.",
    "Check in in five minutes.",
    # Pairs that do not open like a restart.
    "New York New York is my favorite song.",
    "again and again and again",
    "on and on and on",
    "day after day after day",
    "tick tock tick tock goes the clock",
    "and so on and so on and so forth",
    "You know you know what I mean.",
    "He said he said it.",
    "She thinks she thinks too much.",
    # Numbers.
    "one two three one two three go",
    "Call 555 1234 555 1234 now.",
    "My PIN is 4 4 4 4.",
    # Quotation marks, a line break, a spoken command.
    'He wrote "I have I have to go" on the board.',
    "She typed “the the” on purpose.",
    "I have nothing\nI have nothing going on",
    "Tuesday scratch that scratch that Wednesday",
    # Said twice at the end of a sentence, or set off by a comma: on purpose.
    "I love you, I love you.",
    "I know, I know, I should have called.",
    "Of course, of course, come in.",
    "we will see we will see.",
    "Pass me the the.",
    # A capital in mid-sentence is a name or a letter.
    "I got an A a week ago.",
    "The grade was A A A plus.",
    "Plan A a day keeps things simple.",
    # Spelled-out letters.
    "spell it a a r o n",
    # A comma alone at the restart point, after a copy that is a whole
    # phrase, is how a repeat on purpose is written. The restart written the
    # same way is kept too: the words cannot tell the two apart.
    "I have nothing, I have nothing going on.",
    "And I mean it, I mean it this time.",
    "I'm serious, I'm serious about this.",
    "We won, we won the whole thing!",
    "It's mine, it's mine and you can't have it.",
    "I'm sorry, I'm sorry I didn't mean it.",
    "I did it for you, for you and nobody else.",
    "Without you, without you I'm nothing.",
    "In the end, in the end it doesn't even matter is the line.",
    "You and me, you and me against the world.",
    "The chorus goes one more time, one more time and then the drop.",
    "He goes I know, I know and then leaves.",
    "The kids yelled we want cake, we want cake all afternoon.",
    "Hold on, hold on a second.",
    # Both copies set off by commas.
    "We sang happy birthday to you, happy birthday to you, and then cut "
    "the cake.",
    "Don't stop me now, don't stop me now, I'm having such a good time.",
    "Come in, come in, sit down.",
    "Yes we can, yes we can, and we will.",
    # A quotation after a verb of saying, singing or chanting.
    "The crowd chanted we will, we will rock you.",
    "She whispered I love you, I love you over and over.",
    "She sang let it go let it go all day.",
    # A word being named, not used, and "oh my my".
    "Oh my my what a mess.",
    "Type and and or in the search box.",
    "The words and and or are conjunctions.",
    "Use and and or to join them.",
    "The letter a, a vowel, comes first.",
    "Write I I for the roman numeral two.",
    "The regex is a a star b.",
    # Patterns, codes and lists: no word that opens a phrase, half numbers,
    # or only letters.
    "The pattern is red blue green red blue green and so on.",
    "Knit one purl one knit one purl one to the end of the row.",
    "The code is X Y Z X Y Z then press enter.",
    "The plate is K L M K L M and it is red.",
    "Call foo bar baz foo bar baz in the loop.",
    "Clap on one and two on one and two then stop.",
    "The rhyme scheme is a b a b and so on.",
    # Said over and over, with the sentence going on after.
    "on and on and on we went",
    "over and over and over again",
    "you know you know you know what I mean",
    "This way this way please.",
    # A sentence end after the second copy, with more text after it.
    "I love you I love you. See you soon.",
    # A full stop inside each copy.
    "No. I have no. I have nothing going on",
    # An opening mark on a later word of the second copy.
    "I have nothing (I have nothing going on",
)


class RestartRepairTests(unittest.TestCase):
    def test_a_restart_is_typed_once(self):
        for spoken, expected in MUST_REPAIR:
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, expected)
                self.assertIn("remove_repeat", plan.edit_kinds)

    def test_a_repeat_someone_meant_is_kept(self):
        for spoken in MUST_KEEP:
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken)
                self.assertEqual(plan.text, spoken.strip())
                self.assertNotIn("remove_repeat", plan.edit_kinds)
                self.assertIsNone(find_restart(spoken))

    def test_the_lists_hold_enough_hard_cases(self):
        # The brief's own examples plus at least twenty more of each.
        self.assertGreaterEqual(len(MUST_REPAIR), 8 + 20)
        self.assertGreaterEqual(len(MUST_KEEP), 13 + 20)

    def test_the_repair_runs_once_on_already_compiled_text(self):
        # quick_clean compiles text the compiler has already compiled; that
        # second pass must find nothing new, or a removal would go
        # unrecorded.
        for spoken in [spoken for spoken, _ in MUST_REPAIR] + list(MUST_KEEP):
            with self.subTest(spoken=spoken):
                once = compile_cleanup(spoken).text
                self.assertNotIn("remove_repeat",
                                 compile_cleanup(once).edit_kinds)

    def test_a_spoken_command_is_never_part_of_a_run(self):
        # The cleanup expands or resolves a command before the repair runs;
        # what reaches it is a phrase the command rules kept as words, or a
        # command they could not place. Either way it is not the repair's.
        for spoken in ("Start a new line a new line here.",
                       "we need a new paragraph a new paragraph here",
                       "let's scratch that let's scratch that plan"):
            with self.subTest(spoken=spoken):
                self.assertEqual(compile_cleanup(spoken).text, spoken)
        for text in ("Then new line then new line goes here",
                     "so scratch that so scratch that now"):
            with self.subTest(text=text):
                self.assertIsNone(find_restart(text))

    def test_three_repeats_are_kept_whatever_the_length(self):
        for phrase in ("the", "I have", "I have nothing",
                       "we can do it", "it's a long way"):
            for joiner in (" ", ", "):
                spoken = joiner.join([phrase] * 3) + " going on"
                with self.subTest(spoken=spoken):
                    self.assertEqual(compile_cleanup(spoken).text, spoken)
                spoken = joiner.join([phrase] * 4) + " going on"
                with self.subTest(spoken=spoken):
                    self.assertEqual(compile_cleanup(spoken).text, spoken)

    def test_the_owner_sentence_is_one_recorded_edit(self):
        spoken = "Of course I can. I have nothing I have nothing going on"
        plan = compile_cleanup(spoken)
        self.assertEqual(plan.edit_kinds, ["remove_repeat"])
        self.assertEqual(len(plan.edits), 1)
        edit = plan.edits[0]
        self.assertEqual(edit.before, spoken)
        self.assertEqual(edit.after, "Of course I can. I have nothing going on")
        self.assertFalse(plan.needs_semantic_cleanup)

    def test_the_repair_can_be_turned_off(self):
        for spoken, _expected in MUST_REPAIR:
            with self.subTest(spoken=spoken):
                plan = compile_cleanup(spoken, remove_repeats=False)
                self.assertNotIn("remove_repeat", plan.edit_kinds)
                self.assertNotEqual(plan.text, _expected)

    def test_the_repair_runs_again_to_a_fixed_point(self):
        for spoken, expected in MUST_REPAIR:
            with self.subTest(spoken=spoken):
                once = compile_cleanup(spoken).text
                self.assertEqual(compile_cleanup(once).text, once)

    def test_the_kept_copy_takes_the_first_copy_capital(self):
        self.assertEqual(compile_cleanup("In the in the end it worked.").text,
                         "In the end it worked.")
        # A capital the recognizer gave the second copy in mid-sentence
        # does not survive.
        self.assertEqual(
            compile_cleanup("so we can go We can go now.").text,
            "so we can go now.")
        self.assertEqual(
            compile_cleanup("We met I have nothing I have nothing going on.")
            .text, "We met I have nothing going on.")

    def test_other_languages_are_left_exactly_as_they_are(self):
        for language, spoken in (
            ("de", "Ich habe ich habe nichts vor"),
            ("fr", "je je pense que oui"),
            ("es", "en la en la mañana"),
            ("nl", "the the cat"),
            ("ja", "私は 私は 行きます"),
        ):
            with self.subTest(language=language):
                plan = compile_cleanup(spoken, language)
                self.assertNotIn("remove_repeat", plan.edit_kinds)
                self.assertIn(spoken.split()[0], plan.text)
                self.assertEqual(plan.text.split()[:2], spoken.split()[:2])

    def test_code_dictation_never_repairs_a_repeat(self):
        for spoken in ("the the cat", "I have nothing I have nothing going on"):
            with self.subTest(spoken=spoken):
                plan = compile_code_dictation(spoken)
                self.assertNotIn("remove_repeat", plan.edit_kinds)
                self.assertEqual(plan.text, spoken)

    def test_a_snippet_placeholder_is_never_part_of_a_run(self):
        token = ""
        for spoken in (f"I want {token} I want {token} today",
                       f"the {token} the {token} report",
                       f"{token} {token} and then"):
            with self.subTest(spoken=spoken):
                self.assertEqual(compile_cleanup(spoken).text, spoken)

    def test_a_long_dictation_stays_fast(self):
        import time
        sentence = "I have nothing I have nothing going on. "
        started = time.perf_counter()
        plan = compile_cleanup(sentence * 60)
        elapsed = time.perf_counter() - started
        self.assertEqual(plan.text.count("I have nothing going on."), 60)
        # Compared with plain cleanup of the same text in the same run, not
        # with a number of seconds measured on one machine.
        started = time.perf_counter()
        compile_cleanup(sentence * 60, remove_repeats=False)
        baseline = time.perf_counter() - started
        self.assertLess(elapsed, max(baseline * 200, 0.5))


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
    def test_only_native_words_all_at_the_floor_settle_the_route(self):
        sure = (RecognitionWord("ship", 0, 0.2, 0.99),
                RecognitionWord("it", 0.2, 0.4, CONFIDENT_WORD))
        unsure = sure + (RecognitionWord("now", 0.4, 0.6, 0.77),)
        self.assertTrue(words_settle_route(sure, "native"))
        self.assertFalse(words_settle_route(unsure, "native"))
        self.assertFalse(words_settle_route((), "native"))
        for status in ("misaligned", "rejected", "v1-helper", "absent"):
            self.assertFalse(words_settle_route(sure, status))
        self.assertFalse(words_settle_route(
            (RecognitionWord("x", 0, 0.1, None),), "native"))

    def test_the_floor_is_the_compilers_word_confidence_line(self):
        source = (ROOT / "voice_compiler.py").read_text(encoding="utf-8")
        self.assertIn("< CONFIDENT_WORD:", source)
        self.assertNotIn("< 0.78", source)


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


def helper_word(word="Hello", start=0.1, end=0.3, confidence=0.9, **extra):
    return {"word": word, "start": start, "end": end,
            "confidence": confidence, **extra}


class HelperWordParsingTests(unittest.TestCase):
    """Protocol 2 words are untrusted input: anything wrong is no evidence."""

    def test_well_formed_words_are_kept_in_order(self):
        words = parse_helper_words([
            helper_word("Hello", 0.88, 0.96, 0.998),
            helper_word(" world, ", 1.04, 1.52, 0.991, mark_confidence=0.61),
        ], 2.0)
        self.assertEqual(words, (
            HelperWord("Hello", 0.88, 0.96, 0.998),
            HelperWord("world,", 1.04, 1.52, 0.991, 0.61)))

    def test_same_frame_words_and_rounding_past_the_end_are_allowed(self):
        words = parse_helper_words([
            helper_word("a", 0.4, 0.4), helper_word("b", 0.4, 0.4),
            helper_word("c", 0.4, 1.0015)], 1.0)
        self.assertEqual(len(words), 3)

    def test_anything_malformed_discards_every_word(self):
        good = helper_word()
        cases = {
            "not a list": {"word": "Hello"},
            "a string": "Hello world",
            "empty": [],
            "too many": [helper_word("a", 0.0, 0.0)] * (HELPER_MAX_WORDS + 1),
            "not an object": [good, ["Hello", 0.1, 0.3, 0.9]],
            "no word": [{"start": 0.1, "end": 0.3, "confidence": 0.9}],
            "word not text": [helper_word(7)],
            "blank word": [helper_word("   ")],
            "word too long": [helper_word("x" * 65)],
            "no start": [{"word": "a", "end": 0.3, "confidence": 0.9}],
            "text start": [helper_word(start="0.1")],
            "boolean end": [helper_word(end=True)],
            "not a number": [helper_word(start=float("nan"))],
            "infinite": [helper_word(end=float("inf"))],
            "negative start": [helper_word(start=-0.1)],
            "ends before it starts": [helper_word(start=0.5, end=0.4)],
            "past the clip": [helper_word(end=2.01)],
            "starts out of order": [helper_word("a", 0.5, 0.6),
                                    helper_word("b", 0.4, 0.6)],
            "no confidence": [{"word": "a", "start": 0.1, "end": 0.3}],
            "confidence above one": [helper_word(confidence=1.5)],
            "confidence below zero": [helper_word(confidence=-0.1)],
            "boolean confidence": [helper_word(confidence=True)],
            "bad mark confidence": [helper_word(mark_confidence=2)],
            "null mark confidence": [helper_word(mark_confidence=None)],
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(parse_helper_words(raw, 2.0))
        # One bad word anywhere drops the good ones too.
        self.assertIsNone(parse_helper_words(
            [good, helper_word("b", 0.4, 0.5, confidence="high")], 2.0))

    def test_the_word_cap_is_the_helpers_own_4096(self):
        # The runtime leaves `words` out above 4,096 (PROTOCOL.md); a larger
        # list can only come from a broken or hostile helper.
        self.assertEqual(HELPER_MAX_WORDS, 4_096)
        protocol = (ROOT / "native" / "ParrotASRHelper"
                    / "PROTOCOL.md").read_text(encoding="utf-8")
        self.assertIn("more than 4,096 entries", protocol)
        runtime = (ROOT / "native" / "ParrotSpeechRuntime" / "Sources"
                   / "parrot-speech-runtime"
                   / "WordEvidence.swift").read_text(encoding="utf-8")
        self.assertIn("static let maximumWords = 4_096", runtime)
        self.assertIn("words.count <= maximumWords", runtime)
        at_cap = [helper_word("a", 0.0, 0.0)] * 4_096
        self.assertEqual(len(parse_helper_words(at_cap, 2.0)), 4_096)
        self.assertIsNone(parse_helper_words(at_cap + at_cap[:1], 2.0))

    def test_an_integer_too_large_for_a_float_is_no_evidence(self):
        # JSON reads 1 followed by 400 zeros as an int, and float() of it
        # raises OverflowError rather than returning infinity.
        huge = 10 ** 400
        for field in ("start", "end", "confidence", "mark_confidence"):
            for value in (huge, -huge):
                with self.subTest(field=field, value=value > 0):
                    self.assertIsNone(parse_helper_words(
                        [helper_word(**{field: value})], 2.0))
        parsed = json.loads(
            '[{"word": "Hello", "start": 1' + "0" * 400
            + ', "end": 0.3, "confidence": 0.9}]')
        self.assertIsInstance(parsed[0]["start"], int)
        self.assertIsNone(parse_helper_words(parsed, 2.0))
        self.assertIsNone(parse_helper_words([helper_word()], huge))

    def test_an_unusable_clip_length_discards_every_word(self):
        for duration in (0, -1.0, float("nan"), None, True, "2.0"):
            with self.subTest(duration=duration):
                self.assertIsNone(parse_helper_words([helper_word()], duration))

    def test_unknown_word_fields_are_ignored(self):
        self.assertEqual(
            parse_helper_words([helper_word(speaker=2)], 1.0),
            (HelperWord("Hello", 0.1, 0.3, 0.9),))


class HelperProtocolTests(unittest.TestCase):
    def test_only_an_exact_protocol_2_with_words_is_protocol_2(self):
        self.assertEqual(helper_protocol(
            {"ready": True, "protocol": 2, "capabilities": ["words"]}), 2)
        for status in (
                {"ready": True},                              # protocol 1
                {"ready": True, "protocol": 1},
                {"ready": True, "protocol": 3, "capabilities": ["words"]},
                {"ready": True, "protocol": "2", "capabilities": ["words"]},
                {"ready": True, "protocol": 2.0, "capabilities": ["words"]},
                {"ready": True, "protocol": True, "capabilities": ["words"]},
                {"ready": True, "protocol": 2},
                {"ready": True, "protocol": 2, "capabilities": "words"},
                {"ready": True, "protocol": 2, "capabilities": ["vocabulary"]},
                None, "ready", []):
            with self.subTest(status=status):
                self.assertEqual(helper_protocol(status), 1)

    def test_a_result_is_the_protocol_1_pair_with_evidence_attached(self):
        result = parakeet_result(
            "Hello world.", 0.15,
            {"ok": True, "text": "Hello world.", "processing_s": 0.15,
             "avg_confidence": 0.97,
             "words": [helper_word("Hello", 0.1, 0.4),
                       helper_word("world.", 0.5, 0.9)]},
            protocol=2, duration=1.0)
        self.assertIsInstance(result, ParakeetResult)
        self.assertEqual(result, ("Hello world.", 0.15))
        text, seconds = result
        self.assertEqual((text, seconds), (result.text, result.processing_s))
        self.assertEqual(result.word_evidence_status, "native")
        self.assertEqual(result.avg_confidence, 0.97)
        self.assertEqual([word.text for word in result.words],
                         ["Hello", "world."])

    def test_a_protocol_1_helper_has_no_evidence_whatever_it_sends(self):
        result = parakeet_result(
            "Hello.", 0.1, {"ok": True, "text": "Hello.",
                            "words": [helper_word("Hello.")],
                            "avg_confidence": 0.9},
            protocol=1, duration=1.0)
        self.assertEqual(
            (result.words, result.avg_confidence, result.word_evidence_status),
            (None, None, "v1-helper"))

    def test_missing_words_are_absent_and_malformed_words_are_rejected(self):
        absent = parakeet_result("", 0.1, {"ok": True, "text": ""},
                                 protocol=2, duration=1.0)
        self.assertEqual(absent.word_evidence_status, "absent")
        rejected = parakeet_result(
            "Hello.", 0.1,
            {"ok": True, "text": "Hello.", "avg_confidence": "high",
             "words": [helper_word("Hello.", 0.5, 0.1)]},
            protocol=2, duration=1.0)
        self.assertEqual(rejected, ("Hello.", 0.1))
        self.assertEqual(
            (rejected.words, rejected.avg_confidence,
             rejected.word_evidence_status), (None, None, "rejected"))

    def test_a_huge_integer_anywhere_in_a_response_costs_no_text(self):
        huge = 10 ** 400
        words = [helper_word("Hello.", 0.1, 0.4)]
        average = parakeet_result(
            "Hello.", 0.1, {"ok": True, "text": "Hello.",
                            "avg_confidence": huge, "words": words},
            protocol=2, duration=1.0)
        self.assertEqual(average, ("Hello.", 0.1))
        self.assertEqual(
            (average.avg_confidence, average.word_evidence_status),
            (None, "native"))
        response = json.loads(
            '{"ok": true, "text": "Hello.", "processing_s": 0.1, '
            '"avg_confidence": -1' + "0" * 400 + ', "words": [{"word": '
            '"Hello.", "start": 0.1, "end": 1' + "0" * 400
            + ', "confidence": 0.9}]}')
        result = parakeet_result("Hello.", 0.1, response,
                                 protocol=2, duration=1.0)
        self.assertEqual(result, ("Hello.", 0.1))
        self.assertEqual(
            (result.words, result.avg_confidence,
             result.word_evidence_status), (None, None, "rejected"))

    def test_a_dictation_reports_the_worst_status_of_its_parts(self):
        self.assertEqual(WORD_EVIDENCE_STATUSES, (
            "rejected", "misaligned", "v1-helper", "native", "absent"))
        self.assertEqual(combine_word_evidence_status([]), "absent")
        self.assertEqual(
            combine_word_evidence_status(["native", "absent"]), "native")
        self.assertEqual(combine_word_evidence_status(
            ["native", "misaligned", "native"]), "misaligned")
        self.assertEqual(combine_word_evidence_status(
            ["misaligned", "rejected"]), "rejected")
        self.assertEqual(
            combine_word_evidence_status(["unknown"]), "absent")
        self.assertEqual(Recognition("x").word_evidence_status, "absent")
        self.assertEqual(RecognitionWord("x").source, "")


if __name__ == "__main__":
    unittest.main()
