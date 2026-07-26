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
