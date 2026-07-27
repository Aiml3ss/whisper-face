# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

"""The quality gate must catch every drift and stay deterministic itself."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import quality_gate  # noqa: E402


def fake_collectors(metrics, *, boom=None):
    def collect():
        if boom is not None:
            raise RuntimeError(boom)
        return dict(metrics)
    return (("fake", collect),)


class CollectMetricsTests(unittest.TestCase):
    def test_collects_and_flattens(self):
        metrics, errors = quality_gate.collect_metrics(
            fake_collectors({"a.passed": 3, "a.total": 3}))
        self.assertEqual(errors, [])
        self.assertEqual(metrics, {"a.passed": 3, "a.total": 3})

    def test_a_failing_collector_fails_closed_with_its_name(self):
        metrics, errors = quality_gate.collect_metrics(
            fake_collectors({}, boom="corpus missing"))
        self.assertEqual(metrics, {})
        self.assertEqual(len(errors), 1)
        self.assertIn("fake: corpus missing", errors[0])

    def test_non_numeric_and_duplicate_metrics_are_refused(self):
        collectors = (
            ("one", lambda: {"m": True}),
            ("two", lambda: {"n": 1}),
            ("three", lambda: {"n": 2}),
        )
        metrics, errors = quality_gate.collect_metrics(collectors)
        self.assertEqual(metrics, {"n": 1})
        self.assertTrue(any("is not a number" in e for e in errors))
        self.assertTrue(any("duplicate metric n" in e for e in errors))


class CompareTests(unittest.TestCase):
    def baseline(self, metrics):
        return {"schema_version": quality_gate.SCHEMA_VERSION,
                "metrics": metrics}

    def test_identical_metrics_pass(self):
        self.assertEqual(
            quality_gate.compare({"a": 1}, self.baseline({"a": 1})), [])

    def test_a_regression_is_named(self):
        problems = quality_gate.compare(
            {"golden.passed": 10}, self.baseline({"golden.passed": 11}))
        self.assertEqual(len(problems), 1)
        self.assertIn("measured 10, baseline 11", problems[0])

    def test_an_unrecorded_improvement_also_fails(self):
        # Better-than-pinned still fails: a baseline nobody moved is a
        # baseline that will silently absorb the next regression.
        problems = quality_gate.compare(
            {"golden.passed": 12}, self.baseline({"golden.passed": 11}))
        self.assertEqual(len(problems), 1)
        self.assertIn("measured 12, baseline 11", problems[0])

    def test_appearing_and_disappearing_metrics_fail(self):
        problems = quality_gate.compare(
            {"new": 1}, self.baseline({"old": 1}))
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("no longer measured" in p for p in problems))
        self.assertTrue(any("not pinned" in p for p in problems))

    def test_a_missing_or_wrong_schema_baseline_fails(self):
        for baseline in ({}, {"schema_version": 99, "metrics": {"a": 1}},
                         {"schema_version": 1, "metrics": {}}):
            with self.subTest(baseline=baseline):
                problems = quality_gate.compare({"a": 1}, baseline)
                self.assertEqual(len(problems), 1)
                self.assertIn("--rebaseline", problems[0])


class BaselineFileTests(unittest.TestCase):
    def test_write_is_deterministic_and_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            quality_gate.write_baseline(path, {"b": 2, "a": 1})
            first = path.read_text(encoding="utf-8")
            quality_gate.write_baseline(path, {"a": 1, "b": 2})
            self.assertEqual(first, path.read_text(encoding="utf-8"))
            payload = json.loads(first)
            self.assertEqual(list(payload["metrics"]), ["a", "b"])


class ReleaseGateTests(unittest.TestCase):
    """The real gate, against the real checked-in baseline.

    This is the property the release process relies on: the deterministic
    collectors reproduce the pinned numbers from a clean checkout. It runs
    the actual CLI, so a drift in any corpus, collector, or the baseline
    itself fails here before it fails a release.
    """

    def test_the_checked_in_baseline_matches_the_collectors(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "quality_gate.py")],
            capture_output=True, text=True, timeout=900)
        self.assertEqual(
            result.returncode, 0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertIn("pinned metrics unchanged", result.stdout)

    def test_a_drifted_baseline_fails_and_names_the_metric(self):
        original = json.loads(
            (ROOT / "benchmarks" / "quality_baseline.json").read_text(
                encoding="utf-8"))
        original["metrics"]["golden.passed"] -= 1
        with tempfile.TemporaryDirectory() as directory:
            drifted = Path(directory) / "baseline.json"
            drifted.write_text(json.dumps(original), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "quality_gate.py"),
                 "--baseline", str(drifted)],
                capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 1)
        self.assertIn("golden.passed", result.stdout)
        self.assertIn("--rebaseline", result.stdout)


if __name__ == "__main__":
    unittest.main()
