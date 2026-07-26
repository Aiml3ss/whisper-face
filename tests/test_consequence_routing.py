# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Adversarial tests for consequence routing and selective re-listening."""

from dataclasses import asdict
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_compiler import (  # noqa: E402
    ContextCandidate,
    ContextPack,
    MicrospanVerification,
    RecognitionHypothesis,
    TOKEN_RE,
    VoiceIR,
    WordEvidence,
    build_consequence_plan,
    consequence_receipt,
    execute_consequence_plan,
)
from process_verifier import (  # noqa: E402
    RefusalReason,
    VerificationReceipt,
    VerificationResult,
)


def timed_voice(text, *, confidence=0.64, engine="parakeet-unified",
                alternative=None, mode="capture", timing="native",
                context=()):
    words = []
    for index, match in enumerate(TOKEN_RE.finditer(text)):
        token = match.group(0)
        if not any(character.isalnum() for character in token):
            continue
        start = 1.0 + len(words) * 0.34
        words.append(WordEvidence(
            token, start, start + 0.22, confidence, engine, timing))
    hypotheses = [RecognitionHypothesis(
        text, confidence, engine, tuple(words))]
    if alternative is not None:
        hypotheses.append(RecognitionHypothesis(
            alternative, min(1.0, confidence + 0.08), "whisper-turbo"))
    return VoiceIR(
        tuple(hypotheses),
        context=ContextPack(tuple(context)),
        mode=mode,
    )


class SafeVerifier:
    strict_deadline = True
    retains_audio = False

    def __init__(self, outcome="confirmed", engine="whisper-tiny",
                 clock=None):
        self.outcome = outcome
        self.engine = engine
        self.clock = clock
        self.calls = []

    def verify(self, samples, sample_rate, expected, *, deadline_at):
        self.calls.append({
            "samples": len(samples),
            "sample_rate": sample_rate,
            "expected": expected,
            "deadline_at": deadline_at,
        })
        if self.clock is not None:
            self.clock.value = deadline_at + 0.01
        return MicrospanVerification(self.outcome, 0.91, self.engine)


class ProcessSafeVerifier:
    process_isolated = True
    strict_deadline = True
    retains_audio = False

    def __init__(self, outcome="confirmed", engine="mlx-whisper-tiny",
                 refusal=None):
        self.outcome = outcome
        self.engine = engine
        self.refusal = refusal
        self.calls = []

    def verify(self, samples, sample_rate, expected, *, deadline_at):
        self.calls.append({
            "samples": tuple(samples),
            "sample_rate": sample_rate,
            "expected": expected,
            "deadline_at": deadline_at,
        })
        if self.refusal is not None:
            return VerificationReceipt(refusal=self.refusal)
        return VerificationReceipt(result=VerificationResult(
            self.outcome, 0.91, self.engine))


class ConsequenceClassifierTests(unittest.TestCase):
    def test_all_required_consequence_families_are_classified(self):
        text = (
            "Send to Alice Smith, charge $1,250 on July 21 at 3:30 PM, "
            "email alice@example.com, open https://example.com/pay, and save "
            "to /tmp/invoice-2042."
        )
        voice = timed_voice(text)
        plan = build_consequence_plan(voice, audio_duration=20.0)
        categories = {risk.category for risk in plan.risks}
        self.assertTrue({
            "action", "name", "recipient", "currency", "date", "time",
            "contact", "url", "path",
        }.issubset(categories))
        self.assertTrue(all(risk.char_end > risk.char_start
                            for risk in plan.risks))
        self.assertFalse(any(hasattr(risk, "text") for risk in plan.risks))

    def test_generic_and_spoken_numbers_and_command_mode_are_covered(self):
        number_plan = build_consequence_plan(
            timed_voice("Order 2042 needs two hundred units"),
            audio_duration=12.0,
        )
        self.assertGreaterEqual(
            sum(risk.category == "number" for risk in number_plan.risks), 2)
        command_plan = build_consequence_plan(
            timed_voice("git status", mode="command"), audio_duration=4.0)
        self.assertIn("command", {
            risk.category for risk in command_plan.risks})

    def test_context_name_is_detected_without_treating_plain_prose_as_name(self):
        context = (ContextCandidate("Qwen", 4.0, "document"),)
        plan = build_consequence_plan(
            timed_voice("Use Qwen here", context=context),
            audio_duration=6.0,
        )
        self.assertIn("name", {risk.category for risk in plan.risks})
        self.assertEqual(
            sum(risk.category == "name" for risk in plan.risks), 1)
        benign = build_consequence_plan(
            timed_voice("This is ordinary prose with no risky payload",
                        confidence=0.95),
            audio_duration=8.0,
        )
        self.assertEqual(benign.risks, ())
        month_name = build_consequence_plan(
            timed_voice("Email April Jones", confidence=0.95),
            audio_duration=8.0,
        )
        self.assertIn("name", {risk.category for risk in month_name.risks})

    def test_alternative_disagreement_marks_only_the_risky_name_uncertain(self):
        plan = build_consequence_plan(timed_voice(
            "Please email Alice Smith tomorrow",
            confidence=0.93,
            alternative="Please email Alice Smyth tomorrow",
        ), audio_duration=8.0)
        names = [risk for risk in plan.risks if risk.category == "name"]
        self.assertEqual(len(names), 1)
        self.assertIn("hypothesis-disagreement", names[0].uncertainty)

    def test_spoken_ordinal_is_one_risky_span_and_captures_disagreement(self):
        plan = build_consequence_plan(timed_voice(
            "Schedule July twenty first",
            confidence=0.93,
            alternative="Schedule July twenty fifth",
        ), audio_duration=8.0)
        numbers = [risk for risk in plan.risks if risk.category == "number"]
        self.assertEqual(len(numbers), 1)
        self.assertEqual(numbers[0].word_end - numbers[0].word_start, 2)
        self.assertIn("hypothesis-disagreement", numbers[0].uncertainty)

    def test_number_and_common_unit_are_one_consequential_claim(self):
        text = "Set the dose to 5 milligrams"
        plan = build_consequence_plan(timed_voice(
            text,
            confidence=0.93,
            alternative="Set the dose to 5 milliliters",
        ), audio_duration=8.0)

        numbers = [risk for risk in plan.risks if risk.category == "number"]
        self.assertEqual(len(numbers), 1)
        self.assertEqual(
            text[numbers[0].char_start:numbers[0].char_end],
            "5 milligrams",
        )
        self.assertIn("hypothesis-disagreement", numbers[0].uncertainty)

    def test_spoken_decimal_and_fraction_with_units_are_one_claim(self):
        cases = (
            (
                "Set the dose to two point five milligrams",
                "Set the dose to two point five milliliters",
                "two point five milligrams",
            ),
            (
                "Wait one and a half hours",
                "Wait one and a half minutes",
                "one and a half hours",
            ),
        )
        for text, alternative, expected in cases:
            with self.subTest(text=text):
                plan = build_consequence_plan(timed_voice(
                    text,
                    confidence=0.93,
                    alternative=alternative,
                ), audio_duration=8.0)

                numbers = [risk for risk in plan.risks
                           if risk.category == "number"]
                self.assertEqual(len(numbers), 1)
                self.assertEqual(
                    text[numbers[0].char_start:numbers[0].char_end],
                    expected,
                )
                self.assertIn(
                    "hypothesis-disagreement", numbers[0].uncertainty)
                self.assertEqual(len(plan.relisten_requests), 1)

    def test_spoken_decimal_and_fraction_grammar_is_closed(self):
        cases = (
            ("I have one point to make", "one"),
            ("Follow two point five guidelines", None),
            ("Wait one and a third hours", None),
            ("Wait one and a halfhearted hours", None),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                plan = build_consequence_plan(
                    timed_voice(text, confidence=0.95),
                    audio_duration=8.0,
                )
                spans = {
                    text[risk.char_start:risk.char_end]
                    for risk in plan.risks if risk.category == "number"
                }
                if expected is not None:
                    self.assertIn(expected, spans)
                self.assertNotIn("one point", spans)
                self.assertNotIn("two point five guidelines", spans)
                self.assertNotIn("one and a third hours", spans)
                self.assertNotIn("one and a halfhearted hours", spans)

    def test_number_and_common_abbreviated_unit_are_one_claim(self):
        cases = (
            ("Set the dose to 5 mg", "Set the dose to 5 mL", "5 mg"),
            ("Set the dose to 5mg", "Set the dose to 5mL", "5mg"),
            ("Set the dose to five mg", "Set the dose to five mL", "five mg"),
        )
        for text, alternative, expected in cases:
            with self.subTest(text=text):
                plan = build_consequence_plan(timed_voice(
                    text,
                    confidence=0.93,
                    alternative=alternative,
                ), audio_duration=8.0)

                numbers = [risk for risk in plan.risks
                           if risk.category == "number"]
                self.assertEqual(len(numbers), 1)
                self.assertEqual(
                    text[numbers[0].char_start:numbers[0].char_end],
                    expected,
                )
                self.assertIn(
                    "hypothesis-disagreement", numbers[0].uncertainty)
                self.assertEqual(len(plan.relisten_requests), 1)

    def test_abbreviated_unit_matching_is_closed_and_word_bounded(self):
        cases = (
            ("Choose 5 methods", "5"),
            ("Choose 5 in the list", "5"),
            ("Use version 5mlpack", None),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                plan = build_consequence_plan(
                    timed_voice(text, confidence=0.95),
                    audio_duration=8.0,
                )
                numbers = [risk for risk in plan.risks
                           if risk.category == "number"]
                if expected is None:
                    self.assertEqual(numbers, [])
                else:
                    self.assertEqual(len(numbers), 1)
                    self.assertEqual(
                        text[numbers[0].char_start:numbers[0].char_end],
                        expected,
                    )

    def test_consequential_punctuation_disagreements_are_not_collapsed(self):
        pairs = (
            ("Charge $1.20", "Charge $120", "currency"),
            ("Use 2.0", "Use 20", "number"),
            ("Open /tmp/v1.2", "Open /tmp/v12", "path"),
            ("Email a.b@example.com", "Email ab@example.com", "contact"),
        )
        for primary, alternative, category in pairs:
            with self.subTest(primary=primary):
                plan = build_consequence_plan(timed_voice(
                    primary, confidence=0.94, alternative=alternative,
                ), audio_duration=8.0)
                matching = [risk for risk in plan.risks
                            if risk.category == category]
                self.assertTrue(matching)
                self.assertTrue(any(
                    "hypothesis-disagreement" in risk.uncertainty
                    for risk in matching))

    def test_statement_is_not_misrouted_as_an_imperative_action(self):
        plan = build_consequence_plan(
            timed_voice("I will delete the draft tomorrow", confidence=0.95),
            audio_duration=8.0,
        )
        self.assertNotIn("action", {risk.category for risk in plan.risks})

    def test_recipient_requires_an_explicit_target(self):
        for text in (
                "Email the report tomorrow",
                "Message is clear",
                "Call it a day"):
            with self.subTest(text=text):
                plan = build_consequence_plan(
                    timed_voice(text, confidence=0.95), audio_duration=8.0)
                self.assertNotIn(
                    "recipient", {risk.category for risk in plan.risks})
                if text == "Message is clear":
                    self.assertNotIn(
                        "action", {risk.category for risk in plan.risks})

    def test_bare_domain_and_path_prefixes_are_preserved(self):
        cases = (
            ("Open github.com/openai/codex", "url", "github.com/openai/codex"),
            ("Open ~/project/file", "path", "~/project/file"),
            ("Open ../project/file", "path", "../project/file"),
            ("Open /project/file", "path", "/project/file"),
        )
        for text, category, expected in cases:
            with self.subTest(text=text):
                voice = timed_voice(text, confidence=0.95)
                plan = build_consequence_plan(voice, audio_duration=8.0)
                risks = [risk for risk in plan.risks
                         if risk.category == category]
                self.assertEqual(len(risks), 1)
                self.assertEqual(
                    text[risks[0].char_start:risks[0].char_end], expected)
        filename = build_consequence_plan(
            timed_voice("Open report.txt", confidence=0.95),
            audio_duration=8.0)
        self.assertNotIn("url", {risk.category for risk in filename.risks})

    def test_sign_and_path_prefix_disagreements_are_protected(self):
        pairs = (
            ("Pay -$500", "Pay $500", "currency"),
            ("Charge $500", "Charge 500", "currency"),
            ("Use +12%", "Use 12%", "number"),
            ("Use minus five hundred", "Use five hundred", "number"),
            ("Open /tmp/file", "Open tmp/file", "path"),
            ("Open ~/file", "Open /file", "path"),
            ("Open ../file", "Open file", "path"),
        )
        for primary, alternative, category in pairs:
            with self.subTest(primary=primary):
                plan = build_consequence_plan(timed_voice(
                    primary, confidence=0.94, alternative=alternative,
                ), audio_duration=8.0)
                risks = [risk for risk in plan.risks
                         if risk.category == category]
                self.assertTrue(risks)
                self.assertTrue(any(
                    "hypothesis-disagreement" in risk.uncertainty
                    for risk in risks))


class MicrospanSelectionTests(unittest.TestCase):
    def test_only_native_precise_microspans_are_selected(self):
        voice = timed_voice("Please send invoice 2042 to accounting")
        plan = build_consequence_plan(voice, audio_duration=10.0)
        self.assertGreaterEqual(len(plan.relisten_requests), 1)
        self.assertLessEqual(len(plan.relisten_requests), 2)
        for request in plan.relisten_requests:
            self.assertGreater(request.end, request.start)
            self.assertLess(request.end - request.start, 2.4)
            self.assertLess(request.end - request.start, 7.5)

        imprecise = build_consequence_plan(
            timed_voice("Please send invoice 2042", timing="segment"),
            audio_duration=6.0,
        )
        self.assertEqual(imprecise.relisten_requests, ())
        self.assertIn(("timing-unavailable", 2), imprecise.relisten_skipped)

    def test_full_utterance_relisten_is_forbidden(self):
        voice = VoiceIR((RecognitionHypothesis(
            "https://example.com/pay",
            0.6,
            "parakeet-unified",
            (
                WordEvidence("https://example.com/pay", 0.0, 1.0, 0.6),
            ),
        ),))
        plan = build_consequence_plan(voice, audio_duration=1.0)
        self.assertEqual(plan.relisten_requests, ())
        self.assertTrue(dict(plan.relisten_skipped).get("span-not-micro"))

    def test_action_and_payload_share_one_prioritized_microspan(self):
        cases = (
            ("Pay $500", "currency"),
            ("Send 2042", "number"),
            ("Call +1 415 555 0199", "contact"),
            ("Open /tmp/file", "path"),
        )
        for text, payload_category in cases:
            with self.subTest(text=text):
                plan = build_consequence_plan(
                    timed_voice(text), audio_duration=8.0)
                payload_index = next(
                    index for index, risk in enumerate(plan.risks)
                    if risk.category == payload_category)
                self.assertTrue(any(
                    payload_index in request.risk_indexes
                    for request in plan.relisten_requests))
                action_indexes = {
                    index for index, risk in enumerate(plan.risks)
                    if risk.category in {"action", "command"}
                }
                if action_indexes:
                    covering = next(
                        request for request in plan.relisten_requests
                        if payload_index in request.risk_indexes)
                    self.assertTrue(
                        action_indexes.intersection(covering.risk_indexes))


class ReceiptExecutionTests(unittest.TestCase):
    def test_no_verifier_never_reads_or_copies_full_utterance_audio(self):
        class UnreadableAudio:
            def __len__(self):
                return 960_000

            def __getitem__(self, _key):
                raise AssertionError("full utterance audio was accessed")

        voice = timed_voice("Please send invoice 2042 to accounting")
        receipt = consequence_receipt(
            voice, audio=UnreadableAudio(), audio_duration=60.0)
        self.assertEqual(receipt.route, "review")
        self.assertEqual(receipt.relisten_status, "skipped")

    def test_every_in_process_verifier_is_refused_without_audio_access(self):
        class UnreadableAudio:
            def __len__(self):
                return 1000

            def __getitem__(self, _key):
                raise AssertionError("audio was accessed")

        voice = timed_voice("Please send invoice 2042 to accounting")
        verifier = SafeVerifier()
        receipt = consequence_receipt(
            voice,
            audio=UnreadableAudio(),
            sample_rate=100,
            verifier=verifier,
        )
        self.assertEqual(receipt.route, "review")
        self.assertEqual(receipt.relisten_status, "skipped")
        self.assertEqual(receipt.relisten_attempted, 0)
        self.assertFalse(verifier.calls)
        self.assertTrue(dict(receipt.relisten_skipped).get(
            "unsafe-verifier-contract"))
        serialized = json.dumps(asdict(receipt), sort_keys=True)
        self.assertNotIn("2042", serialized)
        self.assertNotIn("invoice", serialized.casefold())

    def test_missing_or_unsafe_verifier_is_skipped_without_audio_access(self):
        voice = timed_voice("Please transfer $500 tomorrow")
        no_verifier = consequence_receipt(
            voice, audio=[0.0] * 1000, sample_rate=100)
        self.assertEqual(no_verifier.route, "review")
        self.assertEqual(no_verifier.relisten_status, "skipped")
        self.assertTrue(dict(no_verifier.relisten_skipped).get(
            "verifier-unavailable"))

        verifier = SafeVerifier()
        verifier.strict_deadline = False
        unsafe = consequence_receipt(
            voice, audio=[0.0] * 1000, sample_rate=100, verifier=verifier)
        self.assertFalse(verifier.calls)
        self.assertTrue(dict(unsafe.relisten_skipped).get(
            "unsafe-verifier-contract"))

    def test_process_isolated_verifier_confirms_only_selected_microspan(self):
        class SliceAuditAudio:
            def __init__(self, count):
                self.count = count
                self.slices = []

            def __len__(self):
                return self.count

            def __getitem__(self, key):
                if not isinstance(key, slice):
                    raise AssertionError("audio must be sliced")
                self.slices.append(key)
                return [0.1] * (key.stop - key.start)

        audio = SliceAuditAudio(160_000)
        voice = timed_voice("Send 2042")
        verifier = ProcessSafeVerifier()

        receipt = consequence_receipt(
            voice,
            audio=audio,
            sample_rate=16_000,
            audio_duration=10.0,
            verifier=verifier,
        )

        self.assertEqual(receipt.route, "verified")
        self.assertEqual(receipt.relisten_status, "confirmed")
        self.assertEqual(receipt.relisten_attempted, 1)
        self.assertEqual(receipt.relisten_confirmed, 1)
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(verifier.calls[0]["expected"], "Send 2042")
        self.assertEqual(len(audio.slices), 1)
        self.assertLess(
            audio.slices[0].stop - audio.slices[0].start,
            len(audio),
        )

    def test_contradiction_and_timeout_remain_review_routes(self):
        voice = timed_voice("Send 2042")
        audio = [0.1] * 160_000
        contradicted = consequence_receipt(
            voice,
            audio=audio,
            sample_rate=16_000,
            audio_duration=10.0,
            verifier=ProcessSafeVerifier(outcome="contradicted"),
        )
        self.assertEqual(contradicted.route, "review")
        self.assertEqual(contradicted.relisten_status, "contradicted")

        timed_out = consequence_receipt(
            voice,
            audio=audio,
            sample_rate=16_000,
            audio_duration=10.0,
            verifier=ProcessSafeVerifier(refusal=RefusalReason.TIMEOUT),
        )
        self.assertEqual(timed_out.route, "review")
        self.assertEqual(timed_out.relisten_status, "timed-out")
        self.assertEqual(
            dict(timed_out.relisten_skipped)["deadline-expired"], 1)

    def test_same_engine_result_cannot_verify_itself(self):
        voice = timed_voice("Send 2042", engine="mlx-whisper-tiny")
        verifier = ProcessSafeVerifier(engine="mlx-whisper-tiny")
        receipt = consequence_receipt(
            voice,
            audio=[0.1] * 160_000,
            sample_rate=16_000,
            audio_duration=10.0,
            verifier=verifier,
        )
        self.assertEqual(receipt.route, "review")
        self.assertEqual(receipt.relisten_confirmed, 0)
        self.assertEqual(
            dict(receipt.relisten_skipped)["verifier-not-independent"], 1)

if __name__ == "__main__":
    unittest.main()
