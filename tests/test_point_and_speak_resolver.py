import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from point_and_speak_resolver import (  # noqa: E402
    EVIDENCE_SCOPE,
    ResolutionError,
    ResolutionState,
    TargetSnapshot,
    measure_synthetic_corpus,
    resolve_point_and_speak,
)


def target(
    target_id,
    title,
    *,
    role="button",
    label="",
    x=0,
    y=0,
    visible=True,
    enabled=True,
    focused=False,
    selection="not_applicable",
):
    return {
        "schema_version": 1,
        "target_id": target_id,
        "role": role,
        "title": title,
        "label": label,
        "geometry": {"x": x, "y": y, "width": 100, "height": 30},
        "visible": visible,
        "enabled": enabled,
        "focused": focused,
        "selection": selection,
    }


class PointAndSpeakResolverTests(unittest.TestCase):
    def test_exact_normalized_and_token_evidence_resolve_deterministically(self):
        targets = [
            target("save", "Save Changes", x=10),
            target("submit", "Submit account changes", x=150),
        ]

        exact = resolve_point_and_speak("Save Changes", targets)
        normalized = resolve_point_and_speak("save-changes", targets)
        token_match = resolve_point_and_speak("submit account", targets)

        self.assertEqual((exact.state, exact.target_id),
                         (ResolutionState.RESOLVED, "save"))
        self.assertEqual((normalized.state, normalized.target_id),
                         (ResolutionState.RESOLVED, "save"))
        self.assertEqual((token_match.state, token_match.target_id),
                         (ResolutionState.RESOLVED, "submit"))
        self.assertIn("exact", exact.receipt.evidence)
        self.assertIn("normalized", normalized.receipt.evidence)
        self.assertIn("token", token_match.receipt.evidence)

    def test_ordinal_and_spatial_constraints_select_one_target(self):
        duplicate = [
            target("first", "Delete", y=10),
            target("second", "Delete", y=80),
        ]
        grid = [
            target("top-left", "Microphone", x=10, y=10),
            target("top-right", "Microphone", x=200, y=10),
            target("bottom-left", "Microphone", x=10, y=200),
            target("bottom-right", "Microphone", x=200, y=200),
        ]

        second = resolve_point_and_speak("second delete button", duplicate)
        corner = resolve_point_and_speak("bottom left microphone button", grid)

        self.assertEqual(second.target_id, "second")
        self.assertIn("ordinal", second.receipt.evidence)
        self.assertEqual(corner.target_id, "bottom-left")
        self.assertIn("spatial", corner.receipt.evidence)

    def test_role_selection_and_focus_facts_are_constraints(self):
        targets = [
            target(
                "notifications", "", role="checkbox", label="Notifications",
                selection="selected",
            ),
            target(
                "search", "", role="text_field", label="Search",
                x=150, focused=True,
            ),
            target("docs", "Documentation", role="link", x=300),
            target("preferences", "Preferences", role="menu_item", x=450),
            target(
                "choice-a", "Choice A", role="radio_button", x=600,
                selection="selected",
            ),
        ]

        selected = resolve_point_and_speak(
            "selected notifications checkbox", targets)
        focused = resolve_point_and_speak("focused search field", targets)
        wrong_role = resolve_point_and_speak("documentation button", targets)
        menu_item = resolve_point_and_speak("preferences menu item", targets)
        radio = resolve_point_and_speak(
            "selected choice A radio button", targets)

        self.assertEqual(selected.target_id, "notifications")
        self.assertEqual(focused.target_id, "search")
        self.assertEqual(menu_item.target_id, "preferences")
        self.assertEqual(radio.target_id, "choice-a")
        self.assertEqual(wrong_role.state, ResolutionState.UNAVAILABLE)
        self.assertIsNone(wrong_role.target_id)
        self.assertGreater(wrong_role.receipt.contradiction_count, 0)

    def test_weak_equal_and_partially_spatial_evidence_stays_ambiguous(self):
        duplicate = [
            target("delete-left", "Delete", x=10),
            target("delete-right", "Delete", x=200),
        ]
        same_column = [
            target("mic-top-right", "Microphone", x=200, y=10),
            target("mic-bottom-right", "Microphone", x=200, y=200),
        ]

        lexical = resolve_point_and_speak("delete button", duplicate)
        spatial = resolve_point_and_speak("right microphone button", same_column)

        self.assertEqual(lexical.state, ResolutionState.AMBIGUOUS)
        self.assertIsNone(lexical.target_id)
        self.assertEqual(spatial.state, ResolutionState.AMBIGUOUS)
        self.assertIsNone(spatial.target_id)
        self.assertEqual(spatial.receipt.margin_bucket, "none")

    def test_contradictory_or_unavailable_evidence_fails_closed(self):
        targets = [
            target("left", "Microphone", x=10),
            target("right", "Microphone", x=200),
            target("hidden", "Factory Reset", y=100, visible=False),
            target("disabled", "Export Archive", y=200, enabled=False),
        ]

        contradictory = resolve_point_and_speak(
            "left right microphone button", targets)
        missing_ordinal = resolve_point_and_speak(
            "third microphone button", targets)
        hidden = resolve_point_and_speak("factory reset button", targets)
        disabled = resolve_point_and_speak("export archive button", targets)

        for result in (contradictory, missing_ordinal, hidden, disabled):
            self.assertEqual(result.state, ResolutionState.UNAVAILABLE)
            self.assertIsNone(result.target_id)
        self.assertGreater(contradictory.receipt.contradiction_count, 0)

    def test_snapshot_schema_rejects_document_text_and_automation_fields(self):
        base = target("safe", "Save")
        for prohibited in (
            "value", "document_text", "selected_text", "description",
            "click", "callback", "bundle_id",
        ):
            with self.subTest(prohibited=prohibited):
                with self.assertRaisesRegex(ResolutionError, "snapshot schema"):
                    TargetSnapshot.from_mapping({**base, prohibited: "private"})

        for geometry_field in ("screen_text", "window_id"):
            with self.subTest(geometry_field=geometry_field):
                changed = dict(base)
                changed["geometry"] = {
                    **base["geometry"], geometry_field: "private",
                }
                with self.assertRaisesRegex(ResolutionError, "geometry schema"):
                    TargetSnapshot.from_mapping(changed)

    def test_receipt_is_content_free_and_carries_no_target_identity(self):
        phrase = "Launch secret project"
        snapshot = target("opaque-target-777", "Launch secret project")

        result = resolve_point_and_speak(phrase, [snapshot])
        receipt = result.receipt.to_mapping()
        serialized = json.dumps(receipt, sort_keys=True)

        self.assertEqual(result.target_id, "opaque-target-777")
        self.assertEqual(set(receipt), {
            "schema_version", "state", "observed_targets", "eligible_targets",
            "contradiction_count", "evidence", "confidence_bucket",
            "margin_bucket",
        })
        self.assertNotIn(phrase.casefold(), serialized.casefold())
        self.assertNotIn("opaque-target-777", serialized)
        self.assertNotIn("title", serialized)
        self.assertNotIn("label", serialized)

    def test_snapshot_is_bounded_and_has_no_action_surface(self):
        parsed = TargetSnapshot.from_mapping(target("safe", "Save"))

        self.assertFalse(hasattr(parsed, "click"))
        self.assertFalse(hasattr(parsed, "write"))
        self.assertFalse(hasattr(parsed, "callback"))
        with self.assertRaisesRegex(ResolutionError, "unique"):
            resolve_point_and_speak("save", [parsed, parsed])

    def test_versioned_synthetic_corpus_has_zero_wrong_target_resolutions(self):
        corpus_path = ROOT / "benchmarks" / "point_and_speak_cases.json"
        corpus = json.loads(corpus_path.read_text())

        first = measure_synthetic_corpus(corpus)
        second = measure_synthetic_corpus(corpus)

        self.assertEqual(first, second)
        self.assertEqual(first, {
            "schema_version": 1,
            "evidence_scope": EVIDENCE_SCOPE,
            "physical_validation": False,
            "cases": 17,
            "resolved": 11,
            "ambiguous": 2,
            "unavailable": 4,
            "correct_outcomes": 17,
            "wrong_target_resolutions": 0,
        })

    def test_synthetic_corpus_schema_is_closed_and_cannot_claim_physical_validation(self):
        corpus_path = ROOT / "benchmarks" / "point_and_speak_cases.json"
        corpus = json.loads(corpus_path.read_text())

        with self.assertRaisesRegex(ResolutionError, "corpus schema"):
            measure_synthetic_corpus({**corpus, "accuracy_claim": ">95%"})
        with self.assertRaisesRegex(ResolutionError, "declaration"):
            measure_synthetic_corpus({**corpus, "physical_validation": True})


if __name__ == "__main__":
    unittest.main()
