# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acoustic_keyword_activation import (  # noqa: E402
    ActivationError,
    active_keywords,
    build_activation_entry,
    clear_activations,
    empty_state,
    load_state,
    remove_activation,
    upsert_activation,
    validate_state,
)
from acoustic_keyword_bias_evaluation import (  # noqa: E402
    evaluate_keyword_bias,
)
from acoustic_keyword_memory import AcousticKeywordMemory  # noqa: E402
from benchmark_acoustic_keyword_activation import (  # noqa: E402
    MANIFEST_KIND,
    main,
)
from benchmark_acoustic_keyword_bias import synthetic_cases  # noqa: E402


class AcousticKeywordActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "acoustic_keyword_activation.json"
        self.memory_path = self.root / "acoustic_keyword_memory.json"
        self.memory = AcousticKeywordMemory()
        self.candidate = None
        for index in range(3):
            self.candidate = self.memory.accept_explicit_correction(
                "PrivateProjectName", evidence_id=f"correction-{index}")
        self.memory_path.write_text(
            self.memory.dumps(), encoding="utf-8")
        records = next(
            rows for name, _expected, rows in synthetic_cases()
            if name == "constructed-physical-gain")
        self.records = records
        self.evaluation = evaluate_keyword_bias(
            self.candidate, self.records)

    def test_passing_evidence_builds_strict_private_activation(self):
        entry = build_activation_entry(
            self.candidate, self.evaluation,
            manual_review_approved=True)
        upsert_activation(self.state_path, entry)

        active, status = active_keywords(self.state_path, self.memory)

        self.assertEqual(status, "ready")
        self.assertEqual(active, ("PrivateProjectName",))
        self.assertEqual(os.stat(self.state_path).st_mode & 0o777, 0o600)
        encoded = self.state_path.read_text(encoding="utf-8")
        self.assertNotIn("case-", encoded)
        self.assertNotIn("observation_tokens", encoded)

    def test_memory_eligibility_and_manual_review_are_both_required(self):
        with self.assertRaisesRegex(ActivationError, "manual review"):
            build_activation_entry(
                self.candidate, self.evaluation,
                manual_review_approved=False)
        weak_memory = AcousticKeywordMemory()
        weak = weak_memory.observe("PrivateProjectName", evidence_id="one")
        with self.assertRaisesRegex(ActivationError, "eligible"):
            build_activation_entry(
                weak, self.evaluation, manual_review_approved=True)

    def test_regression_or_synthetic_evidence_cannot_activate(self):
        for case_name in (
                "constructed-physical-regression", "synthetic-gain"):
            records = next(
                rows for name, _expected, rows in synthetic_cases()
                if name == case_name)
            report = evaluate_keyword_bias(self.candidate, records)
            with self.subTest(case=case_name), self.assertRaises(
                    ActivationError):
                build_activation_entry(
                    self.candidate, report, manual_review_approved=True)

    def test_activation_fails_closed_when_memory_is_forgotten(self):
        entry = build_activation_entry(
            self.candidate, self.evaluation,
            manual_review_approved=True)
        upsert_activation(self.state_path, entry)
        forgotten = AcousticKeywordMemory()

        active, status = active_keywords(self.state_path, forgotten)

        self.assertEqual(status, "ready")
        self.assertEqual(active, ())

    def test_remove_clear_and_malformed_state_are_bounded(self):
        entry = build_activation_entry(
            self.candidate, self.evaluation,
            manual_review_approved=True)
        upsert_activation(self.state_path, entry)
        self.assertTrue(remove_activation(
            self.state_path, "PrivateProjectName"))
        self.assertFalse(remove_activation(
            self.state_path, "PrivateProjectName"))
        upsert_activation(self.state_path, entry)
        self.assertEqual(clear_activations(self.state_path), 1)
        self.assertEqual(validate_state(
            json.loads(self.state_path.read_text()))["entries"], [])

        self.state_path.write_text(
            '{"keyword":"private","transcript":"must not load"}',
            encoding="utf-8")
        state, status = load_state(self.state_path)
        self.assertEqual(status, "invalid")
        self.assertEqual(state, empty_state())
        self.assertEqual(active_keywords(
            self.state_path, self.memory)[0], ())

    def test_cli_omits_keyword_and_requires_explicit_review_to_write(self):
        manifest = self.root / "private-manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "kind": MANIFEST_KIND,
            "keyword": "PrivateProjectName",
            "app_scope": None,
            "records": self.records,
        }), encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            refused = main([
                str(manifest), "--memory", str(self.memory_path),
                "--approve-runtime", str(self.state_path),
            ])
        self.assertEqual(refused, 2)
        self.assertFalse(self.state_path.exists())
        self.assertNotIn("PrivateProjectName", stdout.getvalue())
        self.assertNotIn("PrivateProjectName", stderr.getvalue())

        with redirect_stdout(stdout), redirect_stderr(stderr):
            accepted = main([
                str(manifest), "--memory", str(self.memory_path),
                "--approve-runtime", str(self.state_path),
                "--confirm-manual-review",
            ])
        self.assertEqual(accepted, 0)
        self.assertTrue(self.state_path.exists())
        self.assertNotIn("PrivateProjectName", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
