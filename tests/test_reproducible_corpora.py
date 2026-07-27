# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Reproducibility claims must be checked, not asserted in prose.

Documentation about what a third party can re-run rots silently. These tests
read the machine-readable manifest instead, and fail when a declared case
count, command, or corpus stops matching the repository.
"""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from competitor_benchmark import (  # noqa: E402
    EvidenceState,
    evaluate_product_run,
)


BENCHMARKS = ROOT / "benchmarks"
MANIFEST = BENCHMARKS / "reproducibility.json"
BAKEOFF = BENCHMARKS / "ASR_BAKEOFF.md"
SCORECARD = BENCHMARKS / "model_scorecard.json"
CORPORA_DOC = ROOT / "docs" / "benchmarks" / "reproducible-corpora.md"

# Column order of the published bakeoff table, after engine and runtime role.
BAKEOFF_METRICS = (
    "wer_pct", "exact_pct", "utterance_p90_wer_pct", "rtfx", "proc_p95_s",
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class ReproducibilityManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load(MANIFEST)
        self.corpora = self.manifest["corpora"]

    def test_every_checked_in_corpus_is_declared_exactly_once(self):
        declared = [entry["path"] for entry in self.corpora]
        self.assertEqual(len(declared), len(set(declared)))

        on_disk = {
            str(path.relative_to(ROOT))
            for path in BENCHMARKS.glob("*.json")
        }
        self.assertEqual(
            set(declared), on_disk,
            "a corpus was added or removed without updating "
            "benchmarks/reproducibility.json")

    def test_declared_case_counts_match_the_files(self):
        for entry in self.corpora:
            with self.subTest(path=entry["path"]):
                path = ROOT / entry["path"]
                self.assertTrue(path.is_file())
                key = entry["case_key"]
                if key is None:
                    self.assertIsNone(entry["cases"])
                    continue
                payload = load(path)
                self.assertEqual(
                    len(payload[key]), entry["cases"],
                    f"{entry['path']} declares {entry['cases']} but holds "
                    f"{len(payload[key])}")

    def test_every_requirement_class_is_declared_and_used_honestly(self):
        classes = self.manifest["requirement_classes"]
        used = {entry["requirement"] for entry in self.corpora}
        self.assertTrue(used.issubset(set(classes)))
        for name, description in classes.items():
            self.assertTrue(description.strip())
        self.assertIn("clone-only", used)

    def test_every_named_command_points_at_a_script_that_exists(self):
        for entry in self.corpora:
            with self.subTest(path=entry["path"]):
                parts = entry["command"].split()
                self.assertEqual(parts[:2], ["uv", "run"], entry["command"])
                script = ROOT / parts[2]
                self.assertTrue(script.is_file(), parts[2])

    def test_clone_only_commands_reference_nothing_outside_the_repository(self):
        for entry in self.corpora:
            if entry["requirement"] != "clone-only":
                continue
            with self.subTest(path=entry["path"]):
                command = entry["command"]
                for outside in ("/tmp", "~", "<", "http://", "https://"):
                    self.assertNotIn(
                        outside, command,
                        f"{command} is declared clone-only but reaches "
                        f"outside the repository")
                # Anything that looks like a path must resolve inside the
                # clone; bare words are subcommands.
                for token in command.split()[3:]:
                    if "/" not in token and not token.endswith(".json"):
                        continue
                    self.assertTrue(
                        (ROOT / token).is_file(), f"{token} does not exist")


class CompetitorTemplateTests(unittest.TestCase):
    def test_the_run_template_contains_no_measurement_by_construction(self):
        template = load(BENCHMARKS / "competitor_run_template.json")

        self.assertEqual(
            template["protocol_id"],
            load(BENCHMARKS / "competitor_tasks.json")["protocol_id"])
        for observation in template["observations"]:
            self.assertEqual(
                observation["evidence_state"], EvidenceState.UNAVAILABLE.value)
            self.assertEqual(observation["unavailable_reason"], "not_run")
            for field in (
                "completed", "error_count", "latency_ms", "interaction_count",
                "source_reference",
            ):
                self.assertIsNone(observation[field])

    def test_a_third_party_can_evaluate_the_template_from_a_clone(self):
        report = evaluate_product_run(
            load(BENCHMARKS / "competitor_tasks.json"),
            load(BENCHMARKS / "competitor_run_template.json"))

        self.assertEqual(report["coverage"]["measured"], 0)
        self.assertEqual(report["coverage"]["unavailable"], 6)
        self.assertIsNone(report["measured"]["completion_rate"])
        self.assertEqual(report["unavailable"]["reasons"], {"not_run": 6})


class BakeoffTableTests(unittest.TestCase):
    """The published table is a rendering of the scorecard, not a second copy."""

    def parse_rows(self):
        rows = {}
        for line in BAKEOFF.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| `"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            engine = cells[0].strip("`")
            values = []
            for cell in cells[2:]:
                values.append(float(cell.rstrip("%xs")))
            rows[engine] = (cells[1], values)
        return rows

    def test_every_published_number_matches_the_scorecard(self):
        rows = self.parse_rows()
        candidates = {
            candidate["benchmark_engine"]: candidate
            for candidate in load(SCORECARD)["candidates"]
        }
        self.assertEqual(set(rows), set(candidates))

        for engine, (runtime_role, values) in rows.items():
            with self.subTest(engine=engine):
                candidate = candidates[engine]
                self.assertEqual(runtime_role, candidate["runtime_role"])
                self.assertEqual(len(values), len(BAKEOFF_METRICS))
                for metric, published in zip(BAKEOFF_METRICS, values):
                    self.assertEqual(
                        published, candidate["metrics"][metric],
                        f"{engine} {metric} drifted from the scorecard")

    def test_the_document_states_the_scorecard_is_the_single_source(self):
        text = BAKEOFF.read_text(encoding="utf-8")

        self.assertIn("Single source of truth", text)
        self.assertIn("model_scorecard.json", text)
        self.assertIn("refresh-model-scorecard", text)
        self.assertIn("artifacts were not preserved", text)

    def test_unmeasured_resources_stay_unmeasured_in_the_scorecard(self):
        scorecard = load(SCORECARD)

        for candidate in scorecard["candidates"]:
            provenance = candidate["metric_provenance"]
            for metric in (
                "peak_memory_mb", "energy_j_per_audio_minute", "startup_ms",
            ):
                self.assertEqual(provenance[metric]["state"], "unmeasured")
                self.assertIsNone(provenance[metric]["measurement_id"])
                self.assertIsNone(candidate["metrics"][metric])

    def test_no_measurement_claims_to_be_recalculable_without_an_artifact(self):
        for record in load(SCORECARD)["measurements"]:
            with self.subTest(record=record["measurement_id"]):
                if not record["artifacts_preserved"]:
                    self.assertFalse(record["independently_recalculable"])
                    self.assertIsNone(record["summary_sha256"])
                self.assertTrue(record["hardware"].strip())


class CorporaDocumentationTests(unittest.TestCase):
    def test_the_page_covers_every_corpus_and_requirement_class(self):
        text = CORPORA_DOC.read_text(encoding="utf-8")
        manifest = load(MANIFEST)

        for entry in manifest["corpora"]:
            name = Path(entry["path"]).name
            self.assertIn(name, text, f"{name} is undocumented")
        for name in manifest["requirement_classes"]:
            self.assertIn(name, text, f"{name} is undocumented")

    def test_the_page_does_not_claim_physical_or_comparative_evidence(self):
        text = CORPORA_DOC.read_text(encoding="utf-8").lower()

        self.assertIn("synthetic", text)
        self.assertNotIn("faster than", text)
        self.assertNotIn("more accurate than", text)


if __name__ == "__main__":
    unittest.main()
