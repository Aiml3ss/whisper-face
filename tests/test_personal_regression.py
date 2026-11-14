# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import sys
import json
import inspect
import unittest
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_regression import (  # noqa: E402
    MAX_CASES,
    MAX_MAPPINGS,
    MAX_QUARANTINED,
    PersonalRegressionLab,
)
from shadow_candidate_gate import (  # noqa: E402
    CandidateDisposition,
    CandidateKind,
    CandidateShadowReceipt,
    ShadowCandidateGate,
    ShadowRegressionCase,
)


class PersonalRegressionLabTests(unittest.TestCase):
    def test_matching_app_correction_is_promoted_and_applied(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")

        result = lab.propose("Gwen", "Qwen", app="com.openai.codex")

        self.assertTrue(result.promoted)
        self.assertEqual(result.reasons, ())
        self.assertEqual(
            lab.apply("Use Gwen here", app="com.openai.codex"),
            "Use Qwen here",
        )

    def test_conflicting_candidate_is_quarantined_with_reasons(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")

        result = lab.propose("Gwen", "Guin", app="com.openai.codex")

        self.assertFalse(result.promoted)
        self.assertIn("Qwen", result.reasons[0])
        self.assertIn("Guin", result.reasons[0])
        self.assertEqual(lab.quarantined[0], result)
        self.assertEqual(
            lab.apply("Gwen", app="com.openai.codex"), "Gwen")

    def test_app_scoped_mapping_does_not_escape_its_application(self):
        lab = PersonalRegressionLab()
        lab.record_correction(
            "Graph write", "GraphRAG", app="com.microsoft.VSCode")
        result = lab.propose(
            "Graph write", "GraphRAG", app="com.microsoft.VSCode")

        self.assertTrue(result.promoted)
        self.assertEqual(
            lab.apply("Use Graph write", app="com.microsoft.VSCode"),
            "Use GraphRAG",
        )
        self.assertEqual(
            lab.apply("Use Graph write", app="com.apple.Notes"),
            "Use Graph write",
        )

    def test_global_mapping_applies_in_every_application(self):
        lab = PersonalRegressionLab()
        lab.record_correction("pie torch", "PyTorch")
        result = lab.propose("pie torch", "PyTorch")

        self.assertTrue(result.promoted)
        self.assertEqual(
            lab.apply("Use pie torch", app="com.apple.Notes"),
            "Use PyTorch",
        )
        self.assertEqual(
            lab.apply("Use pie torch", app="com.microsoft.VSCode"),
            "Use PyTorch",
        )

    def test_evaluation_is_deterministic_and_has_no_side_effects(self):
        first = PersonalRegressionLab()
        second = PersonalRegressionLab()
        corrections = (("Gwen", "Quinn"), ("Gwen", "Kwen"))
        for heard, preferred in corrections:
            first.record_correction(
                heard, preferred, app="com.openai.codex")
        for heard, preferred in reversed(corrections):
            second.record_correction(
                heard, preferred, app="com.openai.codex")

        first_result = first.evaluate(
            "Gwen", "Qwen", app="com.openai.codex")
        second_result = second.evaluate(
            "Gwen", "Qwen", app="com.openai.codex")

        self.assertEqual(first_result, second_result)
        self.assertEqual(first.quarantined, ())
        self.assertEqual(
            first.evaluate("Gwen", "Qwen", app="com.openai.codex"),
            first_result,
        )

    def test_state_round_trips_without_audio_or_surrounding_text(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")
        lab.propose("Gwen", "Qwen", app="com.openai.codex")
        lab.propose("Gwen", "Guin", app="com.openai.codex")

        encoded = lab.dumps()
        restored = PersonalRegressionLab.loads(encoded)

        self.assertEqual(restored.dumps(), encoded)
        self.assertEqual(
            restored.apply("Gwen", app="com.openai.codex"), "Qwen")
        self.assertEqual(restored.quarantined, lab.quarantined)
        state = json.loads(encoded)
        self.assertEqual(
            set(state["cases"][0]), {"id", "heard", "preferred", "app"})
        self.assertNotIn("audio", encoded.casefold())
        self.assertNotIn("surrounding", encoded.casefold())

    def test_versionless_legacy_aliases_load_and_discard_extra_context(self):
        restored = PersonalRegressionLab.from_dict({
            "cases": [{
                "from": "Gwen",
                "to": "Qwen",
                "bundle": "com.openai.codex",
                "context": "unrelated private document text",
            }],
            "promoted": [{
                "from": "Gwen",
                "to": "Qwen",
                "bundle": "com.openai.codex",
            }],
        })

        self.assertEqual(
            restored.apply("Gwen", app="com.openai.codex"), "Qwen")
        self.assertNotIn("private document", restored.dumps())

    def test_forgetting_removes_cases_mappings_and_quarantine_for_scope(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")
        lab.propose("Gwen", "Qwen", app="com.openai.codex")
        lab.propose("Gwen", "Guin", app="com.openai.codex")

        removed = lab.forget("Gwen", app="com.openai.codex")

        self.assertEqual(removed, 1)
        self.assertEqual(
            lab.apply("Gwen", app="com.openai.codex"), "Gwen")
        self.assertEqual(lab.quarantined, ())
        state = json.loads(lab.dumps())
        self.assertEqual(state["cases"], [])
        self.assertEqual(state["promoted"], [])

    def test_new_conflicting_case_demotes_a_previously_promoted_mapping(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen", app="com.openai.codex")
        self.assertTrue(
            lab.propose("Gwen", "Qwen", app="com.openai.codex").promoted)

        lab.record_correction("Gwen", "Quinn", app="com.openai.codex")

        self.assertEqual(lab.promoted, ())
        self.assertEqual(
            lab.apply("Gwen", app="com.openai.codex"), "Gwen")
        self.assertEqual(len(lab.quarantined), 1)

    def test_reload_keeps_an_app_mapping_beside_its_global_twin(self):
        lab = PersonalRegressionLab()
        for app in ("com.openai.codex", "com.openai.codex", "com.apple.mail"):
            lab.record_correction("Gwen", "Qwen", app=app)
        self.assertTrue(
            lab.propose("Gwen", "Qwen", app="com.openai.codex").promoted)
        self.assertTrue(lab.propose("Gwen", "Qwen").promoted)
        encoded = lab.dumps()

        restored = PersonalRegressionLab.loads(encoded)

        self.assertEqual(restored.promoted, lab.promoted)
        self.assertEqual(restored.quarantined, ())
        self.assertEqual(restored.dumps(), encoded)
        # The global mapping is then held back by one conflicting correction
        # elsewhere; the app that taught the mapping must keep it.
        restored.record_correction("Gwen", "Gwyn", app="com.apple.mail")
        reloaded = PersonalRegressionLab.loads(restored.dumps())
        self.assertEqual(
            reloaded.apply("Ask Gwen.", app="com.openai.codex"), "Ask Qwen.")
        self.assertEqual(
            reloaded.apply("Ask Gwen.", app="com.apple.Notes"), "Ask Gwen.")

    def test_one_common_word_is_never_mapped_to_another(self):
        for heard, preferred in (
                ("there", "their"), ("their", "there"), ("can", "can't"),
                ("can\u2019t", "can"), ("to", "too"), ("Tuesday", "Thursday"),
                ("15", "50"),
                # Contractions typed without their apostrophe, everyday
                # verbs, and a negation against any word at all.
                ("can", "cant"), ("want", "wont"), ("want", "won't"),
                ("got", "get"), ("site", "sight"), ("its", "it's"),
                ("Kant", "can't"), ("knot", "not"), ("isn't", "Izzy")):
            with self.subTest(heard=heard, preferred=preferred):
                lab = PersonalRegressionLab()
                lab.record_correction(heard, preferred, app="com.slack")
                lab.record_correction(heard, preferred, app="com.slack")

                result = lab.propose(heard, preferred, app="com.slack")

                self.assertFalse(result.promoted)
                self.assertEqual(
                    result.reasons, ("common words are not remapped",))
                self.assertEqual(lab.promoted, ())
                self.assertEqual(lab.quarantined, (result,))
        # A mishearing of a common word, and a name, are still learnable.
        for heard, preferred in (("teh", "the"), ("Gwen", "Qwen")):
            with self.subTest(heard=heard, preferred=preferred):
                lab = PersonalRegressionLab()
                lab.record_correction(heard, preferred)
                self.assertTrue(lab.propose(heard, preferred).promoted)

    def test_a_saved_common_word_mapping_is_held_back_on_load(self):
        restored = PersonalRegressionLab.from_dict({
            "version": 1,
            "cases": [{"heard": "there", "preferred": "their",
                       "app": "com.slack"}],
            "promoted": [{"heard": "there", "preferred": "their",
                          "app": "com.slack"}],
        })

        self.assertEqual(restored.promoted, ())
        self.assertEqual(
            restored.quarantined[0].reasons,
            ("common words are not remapped",))
        self.assertEqual(
            restored.apply("is there a problem over there", app="com.slack"),
            "is there a problem over there")

    def test_reverting_a_mapping_demotes_it_and_keeps_it_demoted(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Mark", "Mary", app="com.slack")
        lab.record_correction("Mark", "Mary", app="com.slack")
        self.assertTrue(lab.propose("Mark", "Mary", app="com.slack").promoted)
        # A correction that is not the inverse of an active mapping is not a
        # revert, in this app or any other.
        self.assertFalse(lab.record_revert("Mark", "Marc", app="com.slack"))
        self.assertFalse(lab.record_revert("Mark", "Mary", app="com.mail"))

        self.assertTrue(lab.record_revert("Mark", "Mary", app="com.slack"))

        self.assertEqual(lab.promoted, ())
        self.assertEqual(
            lab.apply("Call Mark.", app="com.slack"), "Call Mark.")
        # Making the original correction again does not bring it back: the
        # user has shown that "Mark" is sometimes right here.
        lab.record_correction("Mark", "Mary", app="com.slack")
        self.assertFalse(
            lab.propose("Mark", "Mary", app="com.slack").promoted)
        reloaded = PersonalRegressionLab.loads(lab.dumps())
        self.assertEqual(reloaded.promoted, ())
        # Forgetting the scope clears the veto along with the evidence.
        lab.forget("Mark", app="com.slack")
        lab.record_correction("Mark", "Mary", app="com.slack")
        self.assertTrue(lab.propose("Mark", "Mary", app="com.slack").promoted)

    def test_unedited_pastes_of_the_heard_word_count_against_promotion(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Mark", "Mary", app="com.slack")

        held = lab.propose(
            "Mark", "Mary", app="com.slack", corrected=2, kept=2)

        self.assertFalse(held.promoted)
        self.assertEqual(
            held.reasons, ("left unchanged 2 times against 2 corrections",))
        self.assertEqual(lab.promoted, ())
        self.assertEqual(lab.quarantined, (held,))
        self.assertTrue(lab.propose(
            "Mark", "Mary", app="com.slack", corrected=3, kept=2).promoted)
        # Evidence that catches up with an active mapping takes it down.
        self.assertFalse(lab.propose(
            "Mark", "Mary", app="com.slack", corrected=3, kept=3).promoted)
        self.assertEqual(lab.promoted, ())
        self.assertEqual(
            lab.apply("Call Mark.", app="com.slack"), "Call Mark.")

    def test_replacement_keeps_a_capital_the_recognizer_placed(self):
        lab = PersonalRegressionLab()
        lab.record_correction("kubernetis", "kubernetes")
        lab.propose("kubernetis", "kubernetes")
        lab.record_correction("Gwen", "qwen")
        lab.propose("Gwen", "qwen")
        lab.record_correction("iphone", "iPhone")
        lab.propose("iphone", "iPhone")

        self.assertEqual(
            lab.apply("Kubernetis is up. Restart kubernetis."),
            "Kubernetes is up. Restart kubernetes.")
        # A correction that lowered the capital itself is applied as made,
        # and a mixed-case spelling is never recapitalised.
        self.assertEqual(lab.apply("Run Gwen."), "Run qwen.")
        self.assertEqual(lab.apply("Iphone and iphone"), "iPhone and iPhone")
        self.assertEqual(len(lab.active()), 3)

    def test_candidate_is_shadowed_against_unrelated_private_cases(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen")
        lab.record_correction("Keep Gwen here", "Keep Gwen here")

        result = lab.propose("Gwen", "Qwen")

        self.assertFalse(result.promoted)
        self.assertIn("1 regressions", result.reasons[0])
        self.assertEqual(lab.apply("Gwen"), "Gwen")

    def test_promoted_mappings_apply_in_one_non_recursive_pass(self):
        lab = PersonalRegressionLab()
        lab.record_correction("Gwen", "Qwen")
        lab.propose("Gwen", "Qwen")
        lab.record_correction("Qwen", "When")
        lab.propose("Qwen", "When")

        self.assertEqual(lab.apply("Gwen and Qwen"), "Qwen and When")

    def test_stale_serialized_promotion_is_rechecked_and_quarantined(self):
        restored = PersonalRegressionLab.from_dict({
            "version": 1,
            "cases": [{"heard": "Gwen", "preferred": "Qwen", "app": None}],
            "promoted": [{"heard": "Gwen", "preferred": "Guin", "app": None}],
        })

        self.assertEqual(restored.promoted, ())
        self.assertEqual(len(restored.quarantined), 1)
        self.assertEqual(restored.apply("Gwen"), "Gwen")

    def test_unknown_future_schema_is_ignored_safely(self):
        restored = PersonalRegressionLab.from_dict({
            "version": 999,
            "cases": [{"heard": "Gwen", "preferred": "Qwen", "app": None}],
            "promoted": [{"heard": "Gwen", "preferred": "Qwen", "app": None}],
        })

        self.assertEqual(restored.cases, ())
        self.assertEqual(restored.promoted, ())

    def test_oversized_persistent_spans_are_rejected(self):
        lab = PersonalRegressionLab()
        with self.assertRaises(ValueError):
            lab.record_correction("x" * 81, "safe")

        restored = PersonalRegressionLab.from_dict({
            "cases": [{"heard": "x" * 1000, "preferred": "safe"}],
            "promoted": [{"heard": "x" * 1000, "preferred": "safe"}],
        })
        self.assertEqual(restored.cases, ())
        self.assertEqual(restored.promoted, ())

    def test_state_caps_evict_oldest_entries_deterministically(self):
        cases = PersonalRegressionLab()
        first = cases.record_correction("heard-0", "term-0")
        for index in range(1, MAX_CASES + 1):
            cases.record_correction(f"heard-{index}", f"term-{index}")
        self.assertEqual(len(cases.cases), MAX_CASES)
        self.assertNotIn(first.id, {case.id for case in cases.cases})

        mappings = PersonalRegressionLab()
        for index in range(MAX_MAPPINGS + 1):
            heard, preferred = f"h-{index}", f"p-{index}"
            mappings.record_correction(heard, preferred)
            mappings.propose(heard, preferred)
        self.assertEqual(len(mappings.promoted), MAX_MAPPINGS)
        self.assertFalse(any(item.heard == "h-0" for item in mappings.promoted))

        quarantine = PersonalRegressionLab()
        for index in range(MAX_QUARANTINED + 1):
            heard = f"q-{index}"
            quarantine.record_correction(heard, "expected")
            quarantine.propose(heard, "wrong")
        self.assertEqual(len(quarantine.quarantined), MAX_QUARANTINED)
        self.assertFalse(any(
            item.heard == "q-0" for item in quarantine.quarantined))


class ShadowCandidateGateTests(unittest.TestCase):
    def cases(self):
        return (
            ShadowRegressionCase(
                "case-one", "Gwen", "Qwen", "com.openai.codex"),
            ShadowRegressionCase(
                "case-two", "Keep this", "Keep this", None),
        )

    def test_each_candidate_kind_promotes_only_after_clean_improvement(self):
        for kind in CandidateKind:
            with self.subTest(kind=kind):
                activated = []
                receipt = ShadowCandidateGate().attempt(
                    f"candidate-{kind.value.replace('_', '-')}",
                    kind,
                    self.cases(),
                    lambda text, _app: text,
                    lambda text, _app: "Qwen" if text == "Gwen" else text,
                    lambda: activated.append(True) or True,
                )
                self.assertEqual(
                    receipt.disposition, CandidateDisposition.PROMOTED)
                self.assertTrue(receipt.activated)
                self.assertEqual(receipt.improvement_count, 1)
                self.assertEqual(receipt.regression_count, 0)
                self.assertEqual(activated, [True])

    def test_regression_error_and_no_gain_never_call_activation(self):
        activated = []
        regressed = ShadowCandidateGate().attempt(
            "candidate-regression",
            CandidateKind.PROMPT,
            self.cases(),
            lambda text, _app: text,
            lambda text, _app: "wrong",
            lambda: activated.append(True) or True,
        )
        no_gain = ShadowCandidateGate().attempt(
            "candidate-no-gain",
            CandidateKind.MODEL,
            self.cases(),
            lambda text, _app: text,
            lambda text, _app: text,
            lambda: activated.append(True) or True,
        )

        self.assertEqual(
            regressed.disposition, CandidateDisposition.QUARANTINED)
        self.assertGreater(regressed.regression_count, 0)
        self.assertEqual(
            no_gain.disposition,
            CandidateDisposition.INSUFFICIENT_EVIDENCE)
        self.assertEqual(activated, [])

    def test_receipt_is_content_free_closed_and_candidate_is_idempotent(self):
        gate = ShadowCandidateGate()
        activated = []
        first = gate.attempt(
            "candidate-private",
            CandidateKind.DICTIONARY,
            self.cases(),
            lambda text, _app: text,
            lambda text, _app: "Qwen" if text == "Gwen" else text,
            lambda: activated.append(True) or True,
        )
        second = gate.attempt(
            "candidate-private",
            CandidateKind.DICTIONARY,
            self.cases(),
            lambda *_args: self.fail("duplicate must not evaluate"),
            lambda *_args: self.fail("duplicate must not evaluate"),
            lambda: self.fail("duplicate must not activate"),
        )

        self.assertIs(first, second)
        self.assertEqual(activated, [True])
        self.assertEqual(
            {field.name for field in fields(CandidateShadowReceipt)},
            {
                "schema_version", "candidate_id", "kind", "disposition",
                "case_count", "improvement_count", "regression_count",
                "unchanged_count", "error_count", "activation_attempted",
                "activated",
            },
        )
        encoded = json.dumps(first.__dict__ if hasattr(first, "__dict__")
                             else {
                                 field.name: getattr(first, field.name).value
                                 if hasattr(getattr(first, field.name), "value")
                                 else getattr(first, field.name)
                                 for field in fields(first)
                             })
        self.assertNotIn("Gwen", encoded)
        self.assertNotIn("Qwen", encoded)
        self.assertNotIn("Gwen", repr(self.cases()[0]))
        source = inspect.getsource(__import__("shadow_candidate_gate")).lower()
        for forbidden in ("open(", "requests", "subprocess", "socket",
                          "urllib", "import dictate"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
