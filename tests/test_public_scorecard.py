import copy
import io
import json
import contextlib
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capture_app_matrix as matrix  # noqa: E402
from public_scorecard import (  # noqa: E402
    PHYSICAL_EVIDENCE_KINDS,
    SUITE_REPRODUCTION,
    PublicationError,
    assert_evidence_separation,
    build_public_scorecard,
    build_publication,
    classify_physical_artifact,
    main,
    render_json,
    render_publication_markdown,
    validate_environment,
)


REVISION = "343a133ad5a17d654cbf94bbbb5d9cf06ec0c868"


def named_environment(**overrides):
    """A concretely named machine, as a publisher would have to supply."""
    environment = {
        "schema_version": 1,
        "environment_id": "m4-pro-macbook-pro",
        "hardware": "Apple M4 Pro MacBook Pro",
        "os_name": "macOS",
        "os_version": "26.0.1",
        "whisper_face_revision": REVISION,
        "python_version": "3.12.13",
        "software": [{"name": "Whisper Face", "version": "0.2.0"}],
    }
    environment.update(overrides)
    return environment


def real_app_matrix_artifact(*, recorded=1):
    """Build an app-matrix artifact through the real capture producer.

    Using the producer rather than a hand-written dict means a change to the
    harness's honesty fields breaks this test instead of silently widening
    what the publisher will accept.
    """
    records = [
        {
            "case_id": f"case-{index}",
            "category": "native-cocoa",
            "runtime": {
                "insertion_state": "verified",
                "insertion_reason": "commit_verified",
            },
            "operator": {
                "text_verdict": "correct-text-in-intended-target",
                "app_behavior": "accepted-cleanly",
            },
        }
        for index in range(recorded)
    ]
    return matrix.build_artifact(
        matrix.load_apps(None),
        {"records": records, "blocked": [], "plan_digest": "digest"},
    )


class PublicScorecardTests(unittest.TestCase):
    def test_checked_in_synthetic_suites_aggregate_deterministically(self):
        first = build_public_scorecard()
        second = build_public_scorecard()

        self.assertEqual(first, second)
        self.assertEqual(
            [suite["suite_id"] for suite in first["suites"]],
            [
                "voice_compiler",
                "consequence_routing",
                "insertion_reliability",
                "point_and_speak",
                "drop_to_target",
            ],
        )
        self.assertEqual(first["totals"]["suites"], len(first["suites"]))
        self.assertEqual(first["totals"]["failed"], 0)
        self.assertEqual(first["totals"]["critical_failures"], 0)
        self.assertTrue(first["totals"]["all_passed"])

    def test_roadmap_scorecard_total_matches_generated_evidence(self):
        roadmap = (ROOT / "docs" / "development-65.md").read_text(
            encoding="utf-8")
        match = re.search(
            r"\| 44 \|.*?transcript-free (\d+)/(\d+) report", roadmap)
        self.assertIsNotNone(match)

        totals = build_public_scorecard()["totals"]
        self.assertEqual(
            tuple(map(int, match.groups())),
            (totals["passed"], totals["cases"]),
        )

    def test_report_schema_is_closed_and_physical_claims_are_false(self):
        report = build_public_scorecard()

        self.assertEqual(set(report), {
            "schema_version", "report_kind", "privacy", "evidence_scope",
            "physical_validation", "real_apps_exercised",
            "audio_or_model_runs", "suites", "totals",
        })
        self.assertEqual(
            report["evidence_scope"], "checked-in-synthetic-corpora-only")
        self.assertEqual(report["privacy"], "transcript-free-aggregate-only")
        self.assertFalse(report["physical_validation"])
        self.assertEqual(report["real_apps_exercised"], 0)
        self.assertFalse(report["audio_or_model_runs"])
        for suite in report["suites"]:
            self.assertEqual(set(suite), {
                "suite_id", "evidence_scope", "physical_validation", "cases",
                "passed", "failed", "critical_metric", "critical_failures",
            })
            self.assertFalse(suite["physical_validation"])

    def test_json_contains_no_checked_in_transcripts_or_target_identity(self):
        encoded = render_json(build_public_scorecard())

        for private_or_case_content in (
            "2042.76it/s",
            "Alice Smith",
            "Team Inbox",
            "team-inbox",
            "Review Lane",
            "synthetic payload",
        ):
            self.assertNotIn(private_or_case_content, encoded)
        decoded = json.loads(encoded)
        self.assertEqual(decoded, build_public_scorecard())
        self.assertNotIn(str(ROOT), encoded)


class PublicationSeparationTests(unittest.TestCase):
    """A synthetic number must be structurally unable to become a physical one."""

    def test_publication_without_physical_evidence_says_so_plainly(self):
        document = build_publication(
            revision=REVISION, published_on="2026-07-27")

        synthetic = document["evidence"]["synthetic"]
        physical = document["evidence"]["physical"]
        self.assertEqual(document["published_on"], "2026-07-27")
        self.assertEqual(document["repository_revision"], REVISION)
        self.assertFalse(synthetic["physical_validation"])
        self.assertEqual(synthetic["totals"]["cases"], 66)
        self.assertFalse(physical["physical_validation"])
        self.assertEqual(physical["sources"], [])
        self.assertIn(
            "No physical evidence has been published",
            physical["interpretation"])
        markdown = render_publication_markdown(document)
        self.assertIn("Physical validation: **no**", markdown)
        self.assertIn("Every number in this report is synthetic", markdown)

    def test_every_synthetic_suite_publishes_a_reproduction_command(self):
        document = build_publication(revision=REVISION)

        published = {
            suite["suite_id"] for suite in document["evidence"]["synthetic"]
            ["suites"]}
        self.assertEqual(published, set(SUITE_REPRODUCTION))
        for suite in document["evidence"]["synthetic"]["suites"]:
            command = suite["reproduction"]["command"]
            self.assertTrue(command.startswith("uv run "))
            script = ROOT / command.split()[2]
            self.assertTrue(script.is_file(), command)
            corpus = ROOT / suite["reproduction"]["corpus"]
            self.assertTrue(corpus.is_file(), corpus)
            self.assertFalse(suite["reproduction"]["requires_named_hardware"])

    def test_a_real_zero_evidence_capture_artifact_cannot_be_published(self):
        empty = real_app_matrix_artifact(recorded=0)

        self.assertFalse(empty["physical_evidence"])
        # The harness still stamps a physical-sounding scope on an empty
        # session, so scope alone must never be enough.
        self.assertEqual(
            empty["evidence_scope"], "operator-attested-physical-session")
        with self.assertRaises(PublicationError) as caught:
            build_publication(
                revision=REVISION, physical_artifacts=[empty],
                environments=[named_environment()])
        self.assertIn("physical_evidence must be True", str(caught.exception))

    def test_a_recorded_capture_artifact_publishes_with_named_hardware(self):
        artifact = real_app_matrix_artifact(recorded=2)

        document = build_publication(
            revision=REVISION, published_on="2026-07-27",
            physical_artifacts=[artifact],
            environments=[named_environment()])

        physical = document["evidence"]["physical"]
        self.assertTrue(physical["physical_validation"])
        source, = physical["sources"]
        self.assertEqual(source["kind_id"], "physical-app-insertion-matrix")
        self.assertEqual(source["evidence_class"], "physical")
        self.assertTrue(source["physical_validation"])
        self.assertEqual(source["volume_metric"], "real_apps_exercised")
        self.assertEqual(source["volume"], 2)
        self.assertEqual(source["environment_id"], "m4-pro-macbook-pro")
        self.assertEqual(len(source["artifact_sha256"]), 64)
        self.assertTrue(source["reproduction"]["requires_named_hardware"])
        markdown = render_publication_markdown(document)
        self.assertIn("Apple M4 Pro MacBook Pro", markdown)
        self.assertIn("physical-app-insertion-matrix", markdown)

    def test_physical_evidence_without_a_named_machine_is_refused(self):
        artifact = real_app_matrix_artifact(recorded=2)

        with self.assertRaises(PublicationError) as caught:
            build_publication(
                revision=REVISION, physical_artifacts=[artifact])
        self.assertIn("named environment", str(caught.exception))

    def test_placeholder_hardware_and_revisions_are_refused(self):
        for overrides, expected in (
            ({"hardware": "unknown"}, "placeholder"),
            ({"hardware": "  "}, "1 to"),
            ({"os_version": "TBD"}, "placeholder"),
            ({"whisper_face_revision": "343a133"}, "40-character"),
            ({"environment_id": "Example"}, "invalid environment"),
            ({"python_version": "n/a"}, "placeholder"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(PublicationError) as caught:
                    validate_environment(named_environment(**overrides))
                self.assertIn(expected, str(caught.exception))

    def test_the_synthetic_scorecard_itself_is_not_a_physical_artifact(self):
        with self.assertRaises(PublicationError) as caught:
            classify_physical_artifact(build_public_scorecard())
        self.assertIn("registered physical", str(caught.exception))

    def test_an_unregistered_or_renamed_artifact_is_refused(self):
        artifact = real_app_matrix_artifact(recorded=2)
        artifact["artifact"] = "physical-app-insertion-matrix-v2"

        with self.assertRaises(PublicationError):
            classify_physical_artifact(artifact)

    def test_extrapolated_or_four_nines_coverage_is_refused(self):
        for path, key in (
            ("coverage", "extrapolated"),
            ("claims", "four_nines_claim"),
        ):
            with self.subTest(key=key):
                artifact = real_app_matrix_artifact(recorded=2)
                artifact[path][key] = True
                with self.assertRaises(PublicationError):
                    classify_physical_artifact(artifact)

    def test_a_relisten_report_carrying_synthetic_samples_is_refused(self):
        report = {
            "report_kind": "whisper-face/relisten-activation-report",
            "evidence_scope": "explicit-local-wav-manifest",
            "evidence_counts": {"real-recorded": 40, "synthetic-test": 1},
            "activation_evidence": {
                "activation_claim": False,
                "real_samples": 40,
                "real_confirmed_cases": 20,
                "real_contradicted_cases": 20,
            },
        }

        with self.assertRaises(PublicationError) as caught:
            classify_physical_artifact(report)
        self.assertIn("must be exactly 0", str(caught.exception))

        report["evidence_counts"]["synthetic-test"] = 0
        self.assertEqual(
            classify_physical_artifact(report).kind_id,
            "whisper-face/relisten-activation-report")

    def test_zero_volume_physical_work_is_refused(self):
        report = {
            "report_kind": "whisper-face/relisten-activation-report",
            "evidence_scope": "explicit-local-wav-manifest",
            "evidence_counts": {"real-recorded": 0, "synthetic-test": 0},
            "activation_evidence": {
                "activation_claim": False,
                "real_samples": 0,
                "real_confirmed_cases": 0,
                "real_contradicted_cases": 0,
            },
        }

        with self.assertRaises(PublicationError) as caught:
            classify_physical_artifact(report)
        self.assertIn("nothing physical happened", str(caught.exception))

    def test_the_same_artifact_cannot_be_published_twice(self):
        artifact = real_app_matrix_artifact(recorded=2)

        with self.assertRaises(PublicationError) as caught:
            build_publication(
                revision=REVISION,
                physical_artifacts=[artifact, copy.deepcopy(artifact)],
                environments=[named_environment()])
        self.assertIn("published twice", str(caught.exception))

    def test_the_guard_rejects_a_hand_built_document_that_blurs_classes(self):
        document = build_publication(
            revision=REVISION, published_on="2026-07-27",
            physical_artifacts=[real_app_matrix_artifact(recorded=2)],
            environments=[named_environment()])
        assert_evidence_separation(document)

        promoted = copy.deepcopy(document)
        promoted["evidence"]["synthetic"]["suites"][0][
            "physical_validation"] = True
        with self.assertRaises(PublicationError) as caught:
            assert_evidence_separation(promoted)
        self.assertIn("claimed physical validation", str(caught.exception))

        relabelled = copy.deepcopy(document)
        relabelled["evidence"]["synthetic"]["suites"][0][
            "evidence_class"] = "physical"
        with self.assertRaises(PublicationError):
            assert_evidence_separation(relabelled)

        smuggled = copy.deepcopy(document)
        smuggled["evidence"]["synthetic"]["suites"][0][
            "environment_id"] = "m4-pro-macbook-pro"
        with self.assertRaises(PublicationError) as caught:
            assert_evidence_separation(smuggled)
        self.assertIn("only physical evidence may name", str(caught.exception))

        section = copy.deepcopy(document)
        section["evidence"]["synthetic"]["physical_validation"] = True
        with self.assertRaises(PublicationError):
            assert_evidence_separation(section)

        merged = copy.deepcopy(document)
        merged["totals"] = {"cases": 999}
        with self.assertRaises(PublicationError) as caught:
            assert_evidence_separation(merged)
        self.assertIn("must not merge", str(caught.exception))

    def test_a_physical_source_must_name_a_published_environment(self):
        document = build_publication(
            revision=REVISION,
            physical_artifacts=[real_app_matrix_artifact(recorded=2)],
            environments=[named_environment()])

        orphaned = copy.deepcopy(document)
        orphaned["evidence"]["physical"]["environments"] = []
        with self.assertRaises(PublicationError) as caught:
            assert_evidence_separation(orphaned)
        self.assertIn("no published environment", str(caught.exception))

        invented = copy.deepcopy(document)
        invented["evidence"]["physical"]["environments"][0][
            "hardware"] = "unknown"
        with self.assertRaises(PublicationError):
            assert_evidence_separation(invented)

    def test_renderers_refuse_a_document_that_failed_the_guard(self):
        document = build_publication(revision=REVISION)
        document["evidence"]["synthetic"]["suites"][0][
            "physical_validation"] = True

        with self.assertRaises(PublicationError):
            render_publication_markdown(document)

    def test_publication_requires_a_full_revision_and_a_real_date(self):
        for kwargs, expected in (
            ({"revision": "not-a-revision"}, "40-character"),
            ({"revision": REVISION, "published_on": "27-07-2026"}, "ISO"),
            ({"revision": REVISION, "published_on": "2026-02-31"},
             "not a real date"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PublicationError) as caught:
                    build_publication(**kwargs)
                self.assertIn(expected, str(caught.exception))

    def test_every_registered_kind_declares_its_volume_among_its_counters(self):
        for kind in PHYSICAL_EVIDENCE_KINDS:
            with self.subTest(kind=kind.kind_id):
                labels = {label for label, _ in kind.published_counters}
                self.assertIn(kind.volume_label, labels)
                self.assertTrue(kind.producer_command.startswith("uv run "))

    def test_markdown_never_lists_a_synthetic_suite_as_a_physical_source(self):
        document = build_publication(
            revision=REVISION, published_on="2026-07-27",
            physical_artifacts=[real_app_matrix_artifact(recorded=2)],
            environments=[named_environment()])

        markdown = render_publication_markdown(document)
        physical_half = markdown.split("## Physical evidence", 1)[1]
        for suite_id in SUITE_REPRODUCTION:
            self.assertNotIn(suite_id, physical_half)

    def test_cli_default_is_unchanged_and_publish_refuses_bad_evidence(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([])
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue()), build_public_scorecard())

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "empty.json"
            environment = Path(directory) / "environment.json"
            report = Path(directory) / "report.md"
            artifact.write_text(
                json.dumps(real_app_matrix_artifact(recorded=0)),
                encoding="utf-8")
            environment.write_text(
                json.dumps(named_environment()), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "publish", "--revision", REVISION,
                    "--physical-artifact", str(artifact),
                    "--environment", str(environment),
                ])
            self.assertEqual(status, 1)
            self.assertIn("refusing to publish", output.getvalue())

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "publish", "--revision", REVISION,
                    "--published-on", "2026-07-27",
                    "--format", "markdown", "--output", str(report),
                ])
            self.assertEqual(status, 0)
            self.assertIn(
                "# Whisper Face public evidence report",
                report.read_text(encoding="utf-8"))

    def test_publication_carries_no_paths_or_case_content(self):
        document = build_publication(
            revision=REVISION, published_on="2026-07-27",
            physical_artifacts=[real_app_matrix_artifact(recorded=2)],
            environments=[named_environment()])

        encoded = json.dumps(document)
        self.assertNotIn(str(ROOT), encoded)
        for content in (
            "Alice Smith", "Team Inbox", "Review Lane", "case-0", "case-1",
            "native-cocoa", "synthetic payload",
        ):
            self.assertNotIn(content, encoded)


if __name__ == "__main__":
    unittest.main()
