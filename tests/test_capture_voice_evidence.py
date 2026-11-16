# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Contract tests for the guided voice-evidence capture session.

These run without audio hardware.  The point of most of them is not that the
capture tool is internally consistent but that its output is consumable, with
zero hand-editing, by the benchmarks that already exist: the real loaders and
evaluators are imported and fed the manifests the tool writes.

The last group is the load-bearing one.  The capture tool must have no path
that approves anything.  It may print the approval command; it may never run
it, never import an activation module, and never write a receipt.
"""

import ast
import contextlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
import unittest
import warnings


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capture_voice_evidence as capture  # noqa: E402
import benchmark_acoustic_calibration_activation as calibration_benchmark  # noqa: E402
import benchmark_acoustic_keyword_activation as keyword_benchmark  # noqa: E402
import benchmark_relisten_activation as relisten_benchmark  # noqa: E402
from acoustic_keyword_bias_evaluation import evaluate_keyword_bias  # noqa: E402
from acoustic_keyword_memory import AcousticKeywordMemory  # noqa: E402
from measurement_mode import (  # noqa: E402
    CALIBRATION_LABEL,
    EVIDENCE_KEY,
    ORDINARY_PATH,
    parse_measurement_mode,
)
from whisper_verifier_adapter import (  # noqa: E402
    MAX_AUDIO_SAMPLES,
    MAX_EXPECTED_CHARACTERS,
    WHISPER_SAMPLE_RATE,
)

SOURCE_PATH = ROOT / "scripts" / "capture_voice_evidence.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

TOKEN_PATTERN = re.compile(r"\Acase-[0-9a-f]{16}\Z")

# One real utterance_acoustic trace object, shaped exactly as dictate.py emits
# it.  It is a parser fixture for extract_utterance_telemetry, never evidence:
# nothing in the capture tool can produce one of these.
TRACE_FIELDS = {
    "adaptive_threshold": 0.01,
    "clipped_ratio": 0.0,
    "derived_gain_factor": 2.0,
    "duration_ms": 1000.0,
    "frame_rms_p20": 0.01,
    "frame_rms_p50": 0.05,
    "frame_rms_p95": 0.09,
    "nonfinite_ratio": 0.0,
    "peak_amplitude": 0.5,
    "peak_rms": 0.2,
    "rms": 0.1,
    "sample_count": 16000.0,
    "sample_rate_hz": 16000.0,
    "silence_ratio": 0.3,
    "trailing_silence_ms": 200.0,
    "voiced_fraction": 0.6,
}


def trace_line(**overrides) -> str:
    payload = dict(TRACE_FIELDS)
    payload.update(overrides)
    payload["event"] = "utterance_acoustic"
    payload["schema_version"] = 1
    return "[trace] " + json.dumps(payload, sort_keys=True, separators=(",", ":"))


def tone(seconds: float, *, rate: int = capture.SAMPLE_RATE_HZ) -> list[float]:
    """A deterministic test waveform.

    This lives in the test, not in the tool.  The tool has no code that can
    produce a sample value; every sample it writes comes off an input device.
    """
    count = int(round(seconds * rate))
    return [0.25 * math.sin(2.0 * math.pi * 440.0 * index / rate)
            for index in range(count)]


class CaptureHarness(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def session(self, corpus: str, count: int = 4, **settings) -> capture.Session:
        spec = capture.CORPORA[corpus]
        directory = self.root / corpus
        if corpus == "relisten":
            plan = capture.build_relisten_plan(count)
        elif corpus == "calibration":
            plan = capture.build_calibration_plan(count)
        else:
            settings.setdefault("keyword", "Qwen")
            settings.setdefault("near_miss", "Gwen")
            settings.setdefault("app_scope", None)
            plan = capture.build_keyword_plan(
                count, settings["keyword"], settings["near_miss"])
        capture.secure_directory(directory)
        session = capture.Session.create(spec, directory, plan, settings)
        session.save()
        return session


class ManifestContractTests(CaptureHarness):
    """The manifests must load in the benchmarks that consume them."""

    def test_relisten_manifest_loads_in_the_real_benchmark(self):
        session = self.session("relisten", count=4)
        for case in session.cases:
            session.record_arm(case, "take", samples=tone(0.4))
        self.assertTrue(session.manifest_path.exists())

        manifest = relisten_benchmark.load_manifest(session.manifest_path)
        self.assertEqual(len(manifest.cases), 4)
        self.assertEqual(
            {case.evidence_type for case in manifest.cases},
            {"real-recorded"},
        )
        self.assertEqual(
            sorted(case.expected_outcome for case in manifest.cases),
            ["confirmed", "confirmed", "contradicted", "contradicted"],
        )
        for case in manifest.cases:
            samples = relisten_benchmark.read_microspan_wav(case.wav)
            self.assertEqual(len(samples), int(round(0.4 * WHISPER_SAMPLE_RATE)))

    def test_relisten_manifest_keys_are_exactly_the_closed_set(self):
        session = self.session("relisten", count=2)
        session.record_arm(session.cases[0], "take", samples=tone(0.3))
        manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest), {"schema_version", "kind", "cases"})
        self.assertEqual(manifest["kind"], relisten_benchmark.MANIFEST_KIND)
        self.assertEqual(manifest["schema_version"],
                         relisten_benchmark.SCHEMA_VERSION)
        self.assertEqual(
            set(manifest["cases"][0]),
            {"case_id", "wav", "expected_text", "expected_outcome",
             "evidence_type"},
        )
        self.assertEqual(manifest["cases"][0]["wav"], "confirmed-01.wav")

    def test_calibration_manifest_reaches_manual_review(self):
        session = self.session("calibration", count=40)
        session.state["telemetry"] = capture.extract_utterance_telemetry(
            "\n".join(trace_line() for _ in range(8)))
        session.save()
        self.assertEqual(len(session.state["telemetry"]), 8)

        for index, case in enumerate(session.cases):
            # Three cases where the human observed the candidate fix a failure
            # the baseline had, and nothing anywhere that got worse.
            improved = index < 3
            session.record_arm(case, "baseline", samples=None, labels={
                "recognition_correct": not improved,
                "endpoint_correct": True,
            })
            session.record_arm(case, "candidate", samples=None, labels={
                "recognition_correct": True,
                "endpoint_correct": True,
            })

        loaded = calibration_benchmark.load_manifest(session.manifest_path)
        self.assertEqual(len(loaded["cases"]), 40)
        report = calibration_benchmark.evaluate(loaded)
        self.assertEqual(report["evidence"]["physical_cases"], 40)
        self.assertEqual(report["evidence"]["recognition_improvements"], 3)
        self.assertEqual(report["evidence"]["recognition_regressions"], 0)
        self.assertEqual(report["evidence"]["endpoint_regressions"], 0)
        self.assertEqual(
            report["evidence"]["condition_counts"],
            {"clean": 10, "quiet": 10, "noisy": 10, "long-pause": 10},
        )
        self.assertEqual(report["telemetry_policy_verdict"], "keep")
        self.assertTrue(report["activation_candidate"])
        self.assertEqual(report["verdict"], "manual-review-required")

    def test_calibration_manifest_keys_are_exactly_the_closed_set(self):
        session = self.session("calibration", count=4)
        for case in session.cases:
            for arm in ("baseline", "candidate"):
                session.record_arm(case, arm, samples=None, labels={
                    "recognition_correct": True, "endpoint_correct": True})
        manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {"schema_version", "kind", "measurement_mode", "telemetry",
             "cases"})
        # No answer recorded means the ordinary path, the same fail-closed
        # default a missing receipt gets.
        self.assertEqual(manifest["measurement_mode"], "ordinary-path")
        self.assertEqual(manifest["kind"], calibration_benchmark.MANIFEST_KIND)
        case = manifest["cases"][0]
        self.assertEqual(
            set(case),
            {"case_token", "evidence_source", "condition", "baseline",
             "candidate"})
        self.assertEqual(case["evidence_source"],
                         calibration_benchmark.PHYSICAL_SOURCE)
        self.assertEqual(set(case["baseline"]),
                         {"recognition_correct", "endpoint_correct"})
        self.assertTrue(TOKEN_PATTERN.match(case["case_token"]))

    def test_keyword_manifest_reaches_keep_in_the_real_evaluator(self):
        session = self.session("keywords", count=40)
        for index, case in enumerate(session.cases):
            expected = case["plan"]["keyword_expected"]
            # The first three positives are the ones the human saw the biased
            # prompt recover.  Negatives never gain a candidate.
            recovered = expected and index < 6
            session.record_arm(case, "unbiased", samples=None, labels={
                "keyword_candidate_present": expected and not recovered,
                "keyword_selected": expected and not recovered,
            })
            session.record_arm(case, "biased", samples=None, labels={
                "keyword_candidate_present": expected,
                "keyword_selected": expected,
            })

        memory_path = self.root / "acoustic_keyword_memory.json"
        memory = AcousticKeywordMemory()
        for index in range(3):
            memory.accept_explicit_correction(
                "Qwen", evidence_id=f"physical-case-{index}")
        memory_path.write_text(memory.dumps(), encoding="utf-8")

        candidate, records, measurement = keyword_benchmark.load_inputs(
            session.manifest_path, memory_path)
        self.assertEqual(measurement, "ordinary-path")
        self.assertEqual(candidate.keyword, "Qwen")
        self.assertTrue(candidate.eligible)
        self.assertEqual(len(records), 40)

        report = evaluate_keyword_bias(candidate, records)
        self.assertEqual(report["evidence"]["positive_reference_cases"], 20)
        self.assertEqual(report["evidence"]["negative_reference_cases"], 20)
        self.assertEqual(report["evidence"]["selection_improvements"], 3)
        self.assertEqual(report["evidence"]["selection_regressions"], 0)
        self.assertEqual(report["evidence"]["positive_candidate_losses"], 0)
        self.assertEqual(
            report["evidence"]["negative_candidate_introductions"], 0)
        self.assertEqual(
            report["evidence"]["evidence_scope"],
            "caller-attested-physical-only")
        self.assertEqual(report["verdict"], "keep")

    def test_keyword_manifest_keys_are_exactly_the_closed_set(self):
        session = self.session("keywords", count=2)
        for case in session.cases:
            for arm in ("unbiased", "biased"):
                session.record_arm(case, arm, samples=None, labels={
                    "keyword_candidate_present": False,
                    "keyword_selected": False})
        manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {"schema_version", "kind", "measurement_mode", "keyword",
             "app_scope", "records"})
        self.assertEqual(manifest["measurement_mode"], "ordinary-path")
        self.assertEqual(manifest["kind"], keyword_benchmark.MANIFEST_KIND)
        self.assertIsNone(manifest["app_scope"])
        record = manifest["records"][0]
        self.assertEqual(
            set(record),
            {"case_token", "evidence_source", "reference", "unbiased",
             "biased"})
        self.assertEqual(set(record["reference"]), {"keyword_expected"})
        self.assertEqual(
            set(record["unbiased"]),
            {"keyword_candidate_present", "keyword_selected"})
        self.assertTrue(TOKEN_PATTERN.match(record["case_token"]))

    def test_every_emitted_case_declares_physical_evidence(self):
        for corpus, key, field, value in (
            ("relisten", "cases", "evidence_type", "real-recorded"),
            ("calibration", "cases", "evidence_source",
             "physical-caller-attested"),
            ("keywords", "records", "evidence_source",
             "physical-caller-attested"),
        ):
            with self.subTest(corpus=corpus):
                session = self.session(corpus, count=2)
                for case in session.cases:
                    for arm in session.spec.arms:
                        session.record_arm(
                            case, arm,
                            samples=tone(0.3) if arm == session.spec.arms[0]
                            else None,
                            labels={question.key: False
                                    for question in session.spec.questions}
                            or None,
                        )
                manifest = session.manifest()
                self.assertTrue(manifest[key])
                for item in manifest[key]:
                    self.assertEqual(item[field], value)
                self.assertNotIn("synthetic", json.dumps(manifest))


class WaveformContractTests(CaptureHarness):
    def test_written_wav_matches_the_verifier_format(self):
        session = self.session("relisten", count=2)
        session.record_arm(session.cases[0], "take", samples=tone(0.5))
        path = session.directory / "confirmed-01.wav"
        samples, rate = capture.read_wav(path)
        self.assertEqual(rate, capture.SAMPLE_RATE_HZ)
        self.assertEqual(len(samples), int(round(0.5 * capture.SAMPLE_RATE_HZ)))
        # The benchmark applies its own strict mono/16 kHz/PCM16 check.
        decoded = relisten_benchmark.read_microspan_wav(path)
        self.assertEqual(len(decoded), len(samples))

    def test_relisten_cap_matches_the_adapter_microspan_bound(self):
        spec = capture.CORPORA["relisten"]
        self.assertEqual(
            int(round(spec.max_seconds * capture.SAMPLE_RATE_HZ)),
            MAX_AUDIO_SAMPLES,
        )
        self.assertEqual(capture.SAMPLE_RATE_HZ, WHISPER_SAMPLE_RATE)

    def test_a_take_over_the_microspan_cap_is_rejected_downstream(self):
        session = self.session("relisten", count=2)
        session.record_arm(session.cases[0], "take", samples=tone(2.5))
        with self.assertRaises(relisten_benchmark.BenchmarkError):
            relisten_benchmark.read_microspan_wav(
                session.directory / "confirmed-01.wav")

    def test_prompt_text_stays_inside_the_verifier_bounds(self):
        for span, near_miss in capture.RELISTEN_SPANS:
            for value in (span, near_miss):
                self.assertTrue(capture.valid_expected_text(value), value)
                self.assertLessEqual(len(value), MAX_EXPECTED_CHARACTERS)

    def test_relisten_case_ids_satisfy_the_benchmark_identifier_rule(self):
        for case in capture.build_relisten_plan(40):
            self.assertTrue(
                relisten_benchmark._identifier(case["case_id"]),
                case["case_id"])

    def test_empty_recordings_are_refused(self):
        session = self.session("relisten", count=2)
        with self.assertRaises(capture.CaptureError):
            session.record_arm(session.cases[0], "take", samples=[])


class BalanceAccountingTests(CaptureHarness):
    def test_relisten_balance_counts_only_complete_cases(self):
        session = self.session("relisten", count=40)
        for case in session.cases[:5]:
            session.record_arm(case, "take", samples=tone(0.3))
        self.assertEqual(session.balance(), {"confirmed": 3, "contradicted": 2})
        self.assertIn("5/40", session.progress_line())
        self.assertIn("confirmed 3, contradicted 2", session.progress_line())
        self.assertEqual(
            session.shortfalls(),
            ["confirmed cases needs 17 more",
             "contradicted cases needs 18 more"],
        )

    def test_calibration_case_counts_only_when_both_arms_are_labeled(self):
        session = self.session("calibration", count=8)
        labels = {"recognition_correct": True, "endpoint_correct": True}
        for case in session.cases:
            session.record_arm(case, "baseline", samples=None, labels=labels)
        self.assertEqual(session.balance(), {})
        self.assertEqual(session.active_arm(), "candidate")
        self.assertIn("0/8", session.progress_line())

        for case in session.cases[:4]:
            session.record_arm(case, "candidate", samples=None, labels=labels)
        self.assertEqual(
            session.balance(),
            {"clean": 1, "quiet": 1, "noisy": 1, "long-pause": 1})
        self.assertIn("noisy condition needs 7 more", session.progress_line())

    def test_keyword_balance_tracks_positive_and_negative(self):
        session = self.session("keywords", count=40)
        labels = {"keyword_candidate_present": True, "keyword_selected": True}
        for case in session.cases[:6]:
            for arm in ("unbiased", "biased"):
                session.record_arm(case, arm, samples=None, labels=labels)
        self.assertEqual(session.balance(), {"positive": 3, "negative": 3})
        self.assertIn("positive cases needs 17 more", session.progress_line())

    def test_plans_interleave_so_a_partial_session_stays_balanced(self):
        relisten = [case["expected_outcome"]
                    for case in capture.build_relisten_plan(40)]
        self.assertEqual(relisten[:4],
                         ["confirmed", "contradicted", "confirmed",
                          "contradicted"])
        keywords = [case["keyword_expected"]
                    for case in capture.build_keyword_plan(40, "Qwen", "Gwen")]
        self.assertEqual(keywords[:4], [True, False, True, False])
        conditions = [case["condition"]
                      for case in capture.build_calibration_plan(40)]
        self.assertEqual(conditions[:4], list(capture.CALIBRATION_CONDITIONS))
        for index in range(2, 41, 2):
            self.assertLessEqual(
                abs(relisten[:index].count("confirmed")
                    - relisten[:index].count("contradicted")), 1)
            self.assertLessEqual(
                abs(keywords[:index].count(True)
                    - keywords[:index].count(False)), 1)

    def test_an_odd_case_count_is_refused_where_balance_is_pairwise(self):
        with self.assertRaises(capture.CaptureError):
            capture.build_relisten_plan(41)
        with self.assertRaises(capture.CaptureError):
            capture.build_keyword_plan(41, "Qwen", "Gwen")

    def test_balance_requirements_match_the_evaluator_minimums(self):
        self.assertEqual(
            capture.CORPORA["relisten"].min_cases,
            relisten_benchmark.MIN_REAL_SAMPLES)
        relisten = self.session("relisten", count=2)
        self.assertEqual(
            relisten.balance_requirements(),
            {"confirmed": relisten_benchmark.MIN_REAL_SAMPLES_PER_OUTCOME,
             "contradicted": relisten_benchmark.MIN_REAL_SAMPLES_PER_OUTCOME},
        )
        calibration = self.session("calibration", count=4)
        self.assertEqual(
            set(calibration.balance_requirements()),
            set(calibration_benchmark.CONDITIONS))
        self.assertEqual(
            set(calibration.balance_requirements().values()),
            {calibration_benchmark.MIN_CASES_PER_CONDITION})


class ResumeTests(CaptureHarness):
    def test_completed_cases_are_never_silently_overwritten(self):
        session = self.session("relisten", count=4)
        session.record_arm(session.cases[0], "take", samples=tone(0.4))
        path = session.directory / "confirmed-01.wav"
        original = path.read_bytes()

        with self.assertRaises(capture.CaptureError):
            session.record_arm(session.cases[0], "take", samples=tone(0.9))
        self.assertEqual(path.read_bytes(), original)

    def test_reloading_continues_where_the_session_stopped(self):
        session = self.session("relisten", count=4)
        session.record_arm(session.cases[0], "take", samples=tone(0.4))
        tokens = [session.case_identity(case) for case in session.cases]

        resumed = capture.Session.load(session.spec, session.directory)
        self.assertIsNotNone(resumed)
        self.assertEqual(
            [resumed.case_identity(case) for case in resumed.cases], tokens)
        self.assertEqual(len(resumed.completed_cases()), 1)
        self.assertEqual(
            [resumed.case_identity(case)
             for case in resumed.pending_for_arm("take")],
            tokens[1:],
        )

    def test_case_tokens_survive_a_reload(self):
        session = self.session("keywords", count=4)
        tokens = [case["plan"]["case_token"] for case in session.cases]
        resumed = capture.Session.load(session.spec, session.directory)
        self.assertEqual(
            [case["plan"]["case_token"] for case in resumed.cases], tokens)

    def test_a_missing_wav_reopens_the_case_instead_of_shipping_it(self):
        session = self.session("relisten", count=4)
        session.record_arm(session.cases[0], "take", samples=tone(0.4))
        (session.directory / "confirmed-01.wav").unlink()

        resumed = capture.Session.load(session.spec, session.directory)
        self.assertFalse(resumed.arm_complete(resumed.cases[0], "take"))
        self.assertEqual(resumed.completed_cases(), [])
        self.assertIn(
            resumed.cases[0], resumed.pending_for_arm("take"))

    def test_redo_reopens_exactly_one_case(self):
        session = self.session("relisten", count=4)
        for case in session.cases[:2]:
            session.record_arm(case, "take", samples=tone(0.3))
        session.reopen("confirmed-01")
        self.assertEqual(len(session.completed_cases()), 1)
        with self.assertRaises(capture.CaptureError):
            session.reopen("nonexistent-99")

    def test_labels_are_required_and_never_defaulted(self):
        session = self.session("calibration", count=2)
        with self.assertRaises(capture.CaptureError):
            session.record_arm(session.cases[0], "baseline", samples=None)

    def test_loading_a_foreign_corpus_directory_is_refused(self):
        session = self.session("relisten", count=2)
        with self.assertRaises(capture.CaptureError):
            capture.Session.load(
                capture.CORPORA["calibration"], session.directory)

    def test_manifest_is_not_written_before_the_first_complete_case(self):
        session = self.session("relisten", count=2)
        self.assertFalse(session.write_manifest())
        self.assertFalse(session.manifest_path.exists())


class PrivacyTests(CaptureHarness):
    def test_every_written_file_is_owner_only(self):
        session = self.session("relisten", count=2)
        session.record_arm(session.cases[0], "take", samples=tone(0.3))
        for path in (session.state_path, session.manifest_path,
                     session.directory / "confirmed-01.wav"):
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600, f"{path} is {oct(mode)}")
        directory_mode = stat.S_IMODE(session.directory.stat().st_mode)
        self.assertEqual(directory_mode, 0o700)

    def test_created_parent_directories_are_owner_only(self):
        nested = self.root / "outer" / "inner" / "corpus"
        capture.secure_directory(nested)
        for path in (self.root / "outer", self.root / "outer" / "inner", nested):
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_a_too_permissive_directory_is_tightened(self):
        loose = self.root / "loose"
        loose.mkdir()
        os.chmod(loose, 0o755)
        capture.secure_directory(loose)
        if os.name == "posix":
            self.assertEqual(
                stat.S_IMODE(loose.stat().st_mode), 0o700)

    def test_atomic_write_leaves_no_temporary_behind(self):
        target = self.root / "corpus" / "manifest.json"
        capture.atomic_write_json(target, {"a": 1})
        self.assertEqual(
            sorted(item.name for item in target.parent.iterdir()),
            ["manifest.json"],
        )

    def test_a_tracked_destination_inside_a_checkout_is_refused(self):
        checkout = self.root / "checkout"
        (checkout / ".git").mkdir(parents=True)
        (checkout / ".gitignore").write_text(
            "# comment\n.evidence/\n", encoding="utf-8")

        self.assertIsNone(
            capture.private_destination_error(checkout / ".evidence" / "relisten"))
        error = capture.private_destination_error(checkout / "corpora")
        self.assertIsNotNone(error)
        self.assertIn("never enter Git", error)

    def test_a_destination_outside_every_checkout_is_allowed(self):
        self.assertIsNone(
            capture.private_destination_error(self.root / "anywhere"))

    def test_the_repository_gitignores_the_default_evidence_root(self):
        entries = capture.gitignore_entries(ROOT)
        self.assertIn(".evidence", entries)
        self.assertIsNone(
            capture.private_destination_error(ROOT / ".evidence" / "relisten"))
        self.assertIsNotNone(
            capture.private_destination_error(ROOT / "benchmarks" / "corpus"))

    def test_a_session_produces_only_session_manifest_and_wavs(self):
        session = self.session("relisten", count=4)
        for case in session.cases:
            session.record_arm(case, "take", samples=tone(0.3))
        names = sorted(item.name for item in session.directory.iterdir())
        self.assertEqual(
            names,
            ["confirmed-01.wav", "confirmed-02.wav", "contradicted-01.wav",
             "contradicted-02.wav", "manifest.json", "session.json"],
        )
        for name in names:
            self.assertFalse(name.endswith("_activation.json"), name)


class TelemetryImportTests(CaptureHarness):
    def test_only_complete_utterance_acoustic_records_survive(self):
        log = "\n".join([
            "ordinary console noise",
            trace_line(),
            '[trace] {"event":"warm_path","duration_ms":1.0}',
            "[trace] {not json",
            '[trace] {"event":"utterance_acoustic","rms":0.1}',
            trace_line(rms=0.2),
        ])
        records = capture.extract_utterance_telemetry(log)
        self.assertEqual(len(records), 2)
        self.assertEqual(
            set(records[0]), set(capture.acoustic_telemetry_fields()))
        self.assertNotIn("event", records[0])
        self.assertNotIn("schema_version", records[0])
        self.assertEqual(records[1]["rms"], 0.2)

    def test_nothing_is_invented_for_an_empty_log(self):
        self.assertEqual(capture.extract_utterance_telemetry(""), [])
        session = self.session("calibration", count=2)
        self.assertEqual(session.manifest()["telemetry"], [])

    def test_only_the_most_recent_records_are_kept(self):
        log = "\n".join(trace_line(rms=index / 1000.0) for index in range(10))
        records = capture.extract_utterance_telemetry(log, limit=4)
        self.assertEqual(len(records), 4)
        self.assertEqual(records[-1]["rms"], 0.009)

    def test_imported_telemetry_is_accepted_by_the_real_policy(self):
        records = capture.extract_utterance_telemetry(
            "\n".join(trace_line() for _ in range(8)))
        recommendation = capture.calibration_recommendation(records)
        self.assertIsNotNone(recommendation)
        self.assertEqual(recommendation["verdict"], "keep")


class NextCommandTests(CaptureHarness):
    def test_relisten_next_command_is_the_real_invocation(self):
        session = self.session("relisten", count=2)
        text = capture.render_next_commands(session)
        self.assertIn("uv run benchmark_relisten_activation.py", text)
        self.assertIn("--deadline-seconds 10", text)
        self.assertIn("--approve-runtime relisten_activation.json", text)
        self.assertIn("--confirm-manual-review", text)
        self.assertIn(str(session.manifest_path), text)

    def test_calibration_and_keyword_next_commands_name_their_benchmarks(self):
        calibration = self.session("calibration", count=2)
        text = capture.render_next_commands(calibration)
        self.assertIn(
            "uv run benchmark_acoustic_calibration_activation.py", text)
        self.assertIn(
            "--approve-runtime acoustic_calibration_activation.json", text)

        keywords = self.session("keywords", count=2)
        text = capture.render_next_commands(keywords)
        self.assertIn("uv run benchmark_acoustic_keyword_activation.py", text)
        self.assertIn("--memory acoustic_keyword_memory.json", text)
        self.assertIn(
            "--approve-runtime acoustic_keyword_activation.json", text)

    def test_the_named_benchmarks_and_receipts_actually_exist(self):
        for spec in capture.CORPORA.values():
            self.assertTrue((ROOT / spec.benchmark).is_file(), spec.benchmark)
            if not spec.receipt:
                # The accuracy corpus approves nothing, so it has no receipt.
                self.assertEqual(spec.name, "accuracy")
                continue
            entries = capture.gitignore_entries(ROOT)
            self.assertIn(spec.receipt, entries, spec.receipt)


class MeasurementModeTests(CaptureHarness):
    """The measured arm must be runnable and must label its own evidence."""

    def test_the_candidate_pass_gets_a_literal_runtime_command(self):
        session = self.session("calibration", count=4)
        session.state["telemetry"] = capture.extract_utterance_telemetry(
            "\n".join(trace_line() for _ in range(8)))
        command = capture.measurement_command(session)
        self.assertIsNotNone(command)
        self.assertIn("--measure calibration:", command)
        for field in ("gain=", "noise=", "vad=", "end-silence="):
            self.assertIn(field, command)
        # The runtime must actually accept what the tool prints.
        mode = parse_measurement_mode(["dictate.py"] + command.split()[-2:])
        self.assertIsNotNone(mode.calibration)
        self.assertEqual(mode.arms, ("calibration",))
        printed = capture.render_candidate_settings(session)
        self.assertIn(command, printed)
        self.assertIn("not a receipt", printed)

    def test_the_biased_pass_command_names_the_keyword_arm(self):
        session = self.session("keywords", count=2)
        command = capture.measurement_command(session)
        self.assertEqual(
            command,
            "uv run --locked --script dictate.py --measure keyword:Qwen")
        mode = parse_measurement_mode(["dictate.py", "--measure",
                                       "keyword:Qwen"])
        self.assertEqual(mode.keyword, "Qwen")

    def test_relisten_has_no_measured_arm_and_no_manifest_flag(self):
        session = self.session("relisten", count=2)
        self.assertIsNone(session.spec.measurement_arm)
        self.assertIsNone(capture.measurement_command(session))
        for case in session.cases:
            session.record_arm(case, "take", samples=[0.1, -0.1])
        self.assertNotIn(EVIDENCE_KEY, session.manifest())

    def test_an_answered_candidate_pass_labels_the_manifest(self):
        session = self.session("calibration", count=4)
        session.record_measurement_answer("candidate", True)
        for case in session.cases:
            for arm in ("baseline", "candidate"):
                session.record_arm(case, arm, samples=None, labels={
                    "recognition_correct": True, "endpoint_correct": True})
        manifest = json.loads(
            session.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest[EVIDENCE_KEY], CALIBRATION_LABEL)

    def test_a_declined_answer_leaves_the_ordinary_label(self):
        session = self.session("keywords", count=2)
        session.record_measurement_answer("biased", False)
        for case in session.cases:
            for arm in ("unbiased", "biased"):
                session.record_arm(case, arm, samples=None, labels={
                    "keyword_candidate_present": False,
                    "keyword_selected": False})
        manifest = json.loads(
            session.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest[EVIDENCE_KEY], ORDINARY_PATH)

    def test_the_label_survives_the_real_manifest_loader(self):
        session = self.session("calibration", count=4)
        session.record_measurement_answer("candidate", True)
        session.state["telemetry"] = capture.extract_utterance_telemetry(
            "\n".join(trace_line() for _ in range(8)))
        for case in session.cases:
            for arm in ("baseline", "candidate"):
                session.record_arm(case, arm, samples=None, labels={
                    "recognition_correct": True, "endpoint_correct": True})
        loaded = calibration_benchmark.load_manifest(session.manifest_path)
        self.assertEqual(loaded[EVIDENCE_KEY], CALIBRATION_LABEL)
        report = calibration_benchmark.evaluate(loaded)
        self.assertEqual(report[EVIDENCE_KEY], CALIBRATION_LABEL)


class NoApprovalAuthorityTests(unittest.TestCase):
    """The capture tool must be structurally incapable of approving anything."""

    FORBIDDEN_MODULES = frozenset({
        "relisten_activation",
        "acoustic_calibration_activation",
        "acoustic_keyword_activation",
        "benchmark_relisten_activation",
        "benchmark_acoustic_calibration_activation",
        "benchmark_acoustic_keyword_activation",
        "process_verifier",
        "prewarmed_verifier",
        "whisper_verifier_adapter",
    })

    FORBIDDEN_CALLS = frozenset({
        "eval", "exec", "compile", "__import__",
        "os.system", "os.popen", "os.execv", "os.execvp", "os.execve",
        "os.spawnv", "os.spawnl", "os.posix_spawn", "os.fork",
        "subprocess.run", "subprocess.call", "subprocess.Popen",
        "subprocess.check_call", "subprocess.check_output",
        "runpy.run_path", "runpy.run_module",
    })

    def imported_modules(self):
        names = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    @staticmethod
    def dotted(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
        return None

    def test_no_activation_or_benchmark_module_is_imported(self):
        imported = self.imported_modules()
        self.assertEqual(imported & self.FORBIDDEN_MODULES, set())
        self.assertNotIn("subprocess", imported)
        self.assertNotIn("runpy", imported)

    def test_no_code_path_executes_another_process(self):
        offenders = []
        for node in ast.walk(TREE):
            if not isinstance(node, ast.Call):
                continue
            name = self.dotted(node.func)
            if name in self.FORBIDDEN_CALLS:
                offenders.append((name, node.lineno))
        self.assertEqual(offenders, [])

    def test_no_receipt_builder_is_referenced(self):
        for name in (
            "build_activation_receipt",
            "write_activation_receipt",
            "build_activation_entry",
            "upsert_activation",
            "validate_activation_receipt",
        ):
            self.assertNotIn(name, SOURCE, name)
            self.assertFalse(hasattr(capture, name), name)

    def test_the_approval_flags_are_only_printed_never_declared(self):
        declared = []
        for node in ast.walk(TREE):
            if not isinstance(node, ast.Call):
                continue
            name = self.dotted(node.func)
            if name is None or not name.endswith("add_argument"):
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and argument.value in (
                        "--confirm-manual-review", "--approve-runtime"):
                    declared.append(argument.value)
        self.assertEqual(declared, [])

        # It may appear only inside human-readable strings, i.e. the printed
        # instructions telling the operator what to run themselves.
        literals = [
            node.value for node in ast.walk(TREE)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "--confirm-manual-review" in node.value
        ]
        self.assertTrue(literals)
        self.assertEqual(
            SOURCE.count("--confirm-manual-review"), len(literals))

    def test_no_synthetic_evidence_label_can_be_emitted(self):
        self.assertEqual(capture.EVIDENCE_TYPE_REAL, "real-recorded")
        self.assertIn(
            capture.EVIDENCE_TYPE_REAL, relisten_benchmark.EVIDENCE_TYPES)
        self.assertEqual(capture.PHYSICAL_SOURCE, "physical-caller-attested")
        self.assertNotIn("synthetic-test", SOURCE)

    def test_the_tool_refuses_to_record_off_macos(self):
        source = SOURCE
        self.assertIn('sys.platform != "darwin"', source)
        for name in ("run_capture", "run_review"):
            function = next(
                node for node in ast.walk(TREE)
                if isinstance(node, ast.FunctionDef) and node.name == name)
            calls = {
                self.dotted(node.func)
                for node in ast.walk(function) if isinstance(node, ast.Call)
            }
            self.assertIn("require_macos", calls, name)
            self.assertIn("require_input_device", calls, name)

    def test_audio_is_only_ever_read_from_a_live_device(self):
        # sounddevice is imported lazily in exactly one place, and there is no
        # signal generator anywhere in the tool: no randomness that could fill
        # a buffer and no waveform maths.
        self.assertEqual(SOURCE.count("import sounddevice"), 1)
        self.assertNotIn("random", self.imported_modules())
        calls = {
            self.dotted(node.func)
            for node in ast.walk(TREE) if isinstance(node, ast.Call)
        }
        for generator in (
            "math.sin", "math.cos", "math.tau", "np.sin", "np.cos",
            "np.random.rand", "np.random.normal", "np.linspace", "np.arange",
            "random.random", "random.uniform", "random.gauss",
            "secrets.randbits",
        ):
            self.assertNotIn(generator, calls, generator)
        # The only entry point that produces samples reads a live stream.
        record = next(
            node for node in ast.walk(TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "record_take")
        record_calls = {
            self.dotted(node.func)
            for node in ast.walk(record) if isinstance(node, ast.Call)
        }
        self.assertIn("sounddevice.InputStream", record_calls)


class CommandLineTests(CaptureHarness):
    def test_plan_mode_touches_no_audio_and_reports_the_plan(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = capture.main([
                "relisten", "--session-dir", str(self.root / "relisten"),
                "--cases", "4", "--plan",
            ])
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        self.assertIn("Selective Re-listen", output)
        self.assertIn("0/4", output)
        self.assertIn("confirmed-01", output)
        self.assertIn("--confirm-manual-review", output)
        self.assertTrue((self.root / "relisten" / "session.json").exists())
        self.assertFalse((self.root / "relisten" / "manifest.json").exists())

    def test_status_mode_refuses_when_no_session_exists(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = capture.main([
                "calibration", "--session-dir", str(self.root / "missing"),
                "--status",
            ])
        self.assertEqual(code, 2)
        self.assertIn("no capture session", buffer.getvalue())

    def test_keywords_requires_a_keyword_and_a_near_miss(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = capture.main([
                "keywords", "--session-dir", str(self.root / "kw"), "--plan",
            ])
        self.assertEqual(code, 2)
        self.assertIn("--keyword", buffer.getvalue())

    def test_a_device_may_be_named_or_indexed(self):
        self.assertEqual(capture.parse_device("2"), 2)
        self.assertEqual(capture.parse_device(" MacBook Pro Microphone "),
                         "MacBook Pro Microphone")
        self.assertIsNone(capture.parse_device(None))

    def test_every_arm_carries_an_instruction_header(self):
        for spec in capture.CORPORA.values():
            for arm in spec.arms:
                self.assertIn(arm, spec.arm_headers, f"{spec.name}/{arm}")
                self.assertTrue(spec.arm_headers[arm].strip())

    def test_a_tracked_session_directory_is_refused_by_the_cli(self):
        import contextlib
        import io

        checkout = self.root / "checkout"
        (checkout / ".git").mkdir(parents=True)
        (checkout / ".gitignore").write_text("", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = capture.main([
                "relisten", "--session-dir", str(checkout / "corpus"), "--plan",
            ])
        self.assertEqual(code, 2)
        self.assertIn("never enter Git", buffer.getvalue())


# --------------------------------------------------------------------------
# The accuracy corpus: the owner's own voice, names and vocabulary
# --------------------------------------------------------------------------

# Synthetic term lists only. Nothing here reads the owner's dictionary.txt.
SYNTHETIC_TERMS = "Priya | pre-ah\nOkafor\nKubernetes\n"
FIFTEEN_TERMS = "\n".join(
    f"{name} | {near}" for name, near in (
        ("Priya", "pre-ah"), ("Okafor", "oak offer"), ("Kubernetes", "cube"),
        ("Siobhan", "Shivon"), ("Ngozi", "goes he"), ("Tailscale", "tail"),
        ("Mbeki", "embark"), ("Grafana", "Ghana"), ("Xavier", "savior"),
        ("Zhou", "Joe"), ("Ollama", "llama"), ("Aoife", "Eva"),
        ("Pytorch", "torch"), ("Wojcik", "voice check"), ("Lior", "leer"),
    )) + "\n"


class FakeSounddevice:
    """A fake input device: each opened stream delivers the next prepared take.

    The capture tool itself never makes audio; these samples come from the
    test, through the same callback a real PortAudio stream drives.
    """

    def __init__(self, takes):
        self.takes = list(takes)
        self.played = 0
        self.opened = 0

    def check_input_settings(self, **_settings):
        return None

    def query_devices(self, *_args, **_kwargs):
        return {"name": "Fake input"}

    def InputStream(self, **settings):  # noqa: N802 - sounddevice's name
        device = self

        class Stream:
            def __enter__(self):
                import numpy as np

                device.opened += 1
                samples = np.asarray(device.takes.pop(0), dtype=np.float32)
                for start in range(0, samples.size, 1024):
                    block = samples[start:start + 1024].reshape(-1, 1)
                    settings["callback"](block, len(block), None, None)
                return self

            def __exit__(self, *_exc):
                return False

        return Stream()

    def play(self, *_args, **_kwargs):
        self.played += 1

    def wait(self):
        return None


class AccuracyHarness(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "evidence"
        self.root.mkdir()
        self.directory = self.root / "accuracy"
        self.keys: list[str] = []
        self.device = FakeSounddevice([])
        self.dictionary = Path(self.temporary.name) / "no-dictionary.txt"
        patches = {
            "import_sounddevice": lambda: self.device,
            "poll_key": self.fake_poll_key,
            "read_char": self.fake_read_char,
            "cbreak_terminal": contextlib.nullcontext,
            "supports_interaction": lambda: True,
            "require_macos": lambda: None,
            "DICTIONARY_PATH": self.dictionary,
        }
        for name, value in patches.items():
            original = getattr(capture, name)
            setattr(capture, name, value)
            self.addCleanup(setattr, capture, name, original)

    def fake_poll_key(self, timeout):
        if timeout == 0.0 or not self.keys:
            return None
        return self.keys.pop(0)

    def fake_read_char(self):
        return self.keys.pop(0) if self.keys else ""

    def write_terms(self, text: str = SYNTHETIC_TERMS) -> Path:
        path = self.root / "terms.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def run_cli(self, *extra: str, keys: str = "", takes=()):
        self.keys = list(keys)
        self.device.takes = list(takes)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = capture.main([
                "accuracy", "--evidence-root", str(self.root), *extra])
        return code, out.getvalue(), err.getvalue()

    def make_session(self, text: str = SYNTHETIC_TERMS) -> "capture.AccuracySession":
        terms = capture.parse_terms(text)
        session = capture.AccuracySession.create(
            capture.ACCURACY_SPEC, self.directory,
            capture.build_accuracy_plan(terms),
            capture.accuracy_settings(terms, self.root / "terms.txt"))
        capture.secure_directory(self.directory)
        session.save()
        return session

    def load(self) -> "capture.AccuracySession":
        return capture.AccuracySession.load(capture.ACCURACY_SPEC, self.directory)

    @staticmethod
    def accept(session, case, seconds: float = 1.0):
        plan = case["plan"]
        session.accept(case, tone(seconds),
                       reference=plan["reference"] or "Typed by hand.",
                       edited=plan["reference"] is None, peak=0.25)


def plan_for(text: str):
    return capture.build_accuracy_plan(capture.parse_terms(text))


class AccuracyPlanTests(unittest.TestCase):
    def test_essential_set_of_forty_comes_first_then_the_extended_sixty(self):
        plan = plan_for(FIFTEEN_TERMS)
        self.assertEqual(len(plan), 100)
        parts = [case["part"] for case in plan]
        self.assertEqual(parts, ["essential"] * 40 + ["extended"] * 60)
        kinds = lambda part: sorted(  # noqa: E731
            case["kind"] for case in plan if case["part"] == part)
        self.assertEqual(kinds("essential"),
                         sorted(["term"] * 30 + ["near-miss"] * 10))
        extended = kinds("extended")
        self.assertEqual(extended.count("near-miss"), 10)
        self.assertEqual(extended.count("format"), 20)
        self.assertEqual(extended.count("jargon"), 10)
        self.assertEqual(extended.count("free"), 15)
        self.assertEqual(extended.count("long"), 5)
        self.assertEqual([case["case_id"] for case in plan],
                         [f"acc-{index:03d}" for index in range(1, 101)])

    def test_the_plan_is_deterministic(self):
        self.assertEqual(plan_for(FIFTEEN_TERMS), plan_for(FIFTEEN_TERMS))
        self.assertEqual(plan_for(SYNTHETIC_TERMS), plan_for(SYNTHETIC_TERMS))
        self.assertNotIn("case-", json.dumps(plan_for(SYNTHETIC_TERMS)))

    def test_each_term_twice_in_different_templates_and_positions(self):
        plan = plan_for(FIFTEEN_TERMS)
        names = [term.text for term in capture.parse_terms(FIFTEEN_TERMS).names]
        positions = set()
        for name in names:
            cases = [case for case in plan
                     if case["kind"] == "term" and case["terms"] == [name]]
            self.assertEqual(len(cases), 2, name)
            self.assertNotEqual(cases[0]["template"], cases[1]["template"])
            self.assertNotEqual(cases[0]["position"], cases[1]["position"])
            positions.update(case["position"] for case in cases)
        self.assertEqual(positions, {"start", "middle", "end", "possessive"})

    def test_fewer_terms_fill_thirty_slots_without_repeating_a_template(self):
        plan = plan_for(SYNTHETIC_TERMS)
        terms = [case for case in plan if case["kind"] == "term"]
        self.assertEqual(len(terms), 30)
        for name in ("Priya", "Okafor", "Kubernetes"):
            mine = [case for case in terms if case["terms"] == [name]]
            self.assertEqual(len(mine), 10)
            templates = [case["template"] for case in mine]
            self.assertEqual(len(set(templates)), len(templates))
            for first, second in zip(mine, mine[1:]):
                self.assertNotEqual(first["position"], second["position"])
        essential = [case for case in plan if case["part"] == "essential"]
        for first, second in zip(essential, essential[1:]):
            shared = set(first["terms"]) & set(second["terms"])
            self.assertFalse(shared, "a term runs twice in a row")

    def test_one_term_still_never_repeats_a_template(self):
        plan = plan_for("Priya\n")
        terms = [case for case in plan if case["kind"] == "term"]
        self.assertEqual(len(terms), len(capture.TERM_TEMPLATES))
        self.assertEqual(len({case["template"] for case in terms}), len(terms))

    def test_possessives_follow_the_term(self):
        self.assertEqual(capture.possessive("Priya"), "Priya's")
        self.assertEqual(capture.possessive("Kubernetes"), "Kubernetes'")
        self.assertEqual(capture.fill_term("{t}'s page", "O'Brien"),
                         "O'Brien's page")

    def test_more_than_fifteen_terms_uses_the_first_fifteen(self):
        text = FIFTEEN_TERMS + "Extra One\nExtra Two\nExtra Three\n"
        plan = plan_for(text)
        self.assertEqual(len(plan), 100)
        self.assertNotIn("Extra", json.dumps(plan))

    def test_near_miss_sentences_use_the_near_miss_and_never_the_term(self):
        plan = plan_for(SYNTHETIC_TERMS)
        near = [case for case in plan if case["kind"] == "near-miss"]
        self.assertEqual(len(near), 2)
        self.assertNotEqual(near[0]["template"], near[1]["template"])
        for case in near:
            self.assertIn("pre-ah", case["reference"])
            self.assertNotIn("priya", case["reference"].casefold())
            self.assertEqual(case["terms"], [])
            self.assertEqual(case["must_not_write"], ["Priya"])
            self.assertEqual(case["part"], "essential")
        ordinary = [case for case in plan if case["kind"] == "ordinary"]
        self.assertEqual(len(ordinary), 18)
        for case in ordinary:
            self.assertEqual(case["terms"], [])
            self.assertEqual(case["must_not_write"],
                             ["Priya", "Okafor", "Kubernetes"])

    def test_an_ordinary_sentence_that_holds_a_term_is_left_out(self):
        plan = plan_for("milk\n")
        ordinary = [case for case in plan if case["kind"] == "ordinary"]
        self.assertEqual(len(ordinary), 20)
        self.assertFalse(any("milk" in case["reference"] for case in ordinary))

    def test_jargon_lines_feed_the_jargon_cases(self):
        plan = plan_for("Priya\njargon: kubectl\nJARGON: pytest | pie test\n")
        jargon = [case for case in plan if case["kind"] == "jargon"]
        self.assertEqual(len(jargon), 10)
        templated = [case for case in jargon if case["terms"]]
        self.assertEqual(len(templated), 4)
        for name in ("kubectl", "pytest"):
            mine = [case for case in templated if case["terms"] == [name]]
            self.assertEqual(len({case["template"] for case in mine}), 2)
        near = [case for case in plan if case.get("near_miss") == "pie test"]
        self.assertEqual(len(near), 2)

    def test_format_data_is_fictional(self):
        for _covers, text in capture.FORMAT_SENTENCES:
            for number in re.findall(r"\d{3}-\d{4}", text):
                self.assertRegex(number, r"\A555-01\d\d\Z", text)
            for domain in re.findall(r"[@\w.]*?(\w+\.(?:com|org|net))", text):
                self.assertTrue(domain.startswith("example."), text)
        covers = {covers for covers, _text in capture.FORMAT_SENTENCES}
        for needed in ("number", "date", "time", "money", "phone", "email",
                       "url"):
            self.assertIn(needed, covers)

    def test_long_passages_run_seventy_to_one_hundred_and_ten_words(self):
        self.assertEqual(len(capture.LONG_PASSAGES), 5)
        for passage in capture.LONG_PASSAGES:
            self.assertTrue(70 <= len(passage.split()) <= 110,
                            len(passage.split()))
        long_cases = [case for case in plan_for(SYNTHETIC_TERMS)
                      if case["kind"] == "long"]
        self.assertTrue(all(case["min_seconds"] >= 15 for case in long_cases))

    def test_every_template_takes_a_name_a_product_and_a_technical_term(self):
        for term in ("Priya", "Okafor", "Kubernetes", "José Núñez", "O'Brien",
                     "kubectl"):
            for _position, template in capture.TERM_TEMPLATES:
                text = capture.fill_term(template, term)
                self.assertNotIn("{", text)
                self.assertIn(term, text)
                self.assertRegex(text, r"[.?!]\Z")
                self.assertEqual(capture.found_terms(text, [term]), [term])
            for template in capture.NEAR_MISS_TEMPLATES:
                self.assertNotIn("{", template.replace("{n}", term))
            for template in capture.JARGON_TEMPLATES:
                self.assertNotIn("{", template.replace("{j}", term))

    def test_every_case_carries_what_the_evaluator_needs(self):
        for case in plan_for(FIFTEEN_TERMS):
            for key in ("case_id", "part", "kind", "say_text", "reference",
                        "terms", "must_not_write", "min_seconds",
                        "max_seconds", "estimate_seconds"):
                self.assertIn(key, case)
            self.assertEqual(case["reference"] is None, case["kind"] == "free")

    def test_the_essential_set_takes_about_fifteen_minutes(self):
        plan = plan_for(FIFTEEN_TERMS)
        essential = sum(case["estimate_seconds"] for case in plan
                        if case["part"] == "essential")
        self.assertTrue(13 * 60 <= essential <= 16 * 60, essential)


class AccuracyTermFileTests(AccuracyHarness):
    def test_bare_near_miss_jargon_blank_and_comment_lines(self):
        terms = capture.parse_terms(
            "# my names\n\nPriya | pre-ah\n  Okafor  \n"
            "jargon: kubectl\n   \n# jargon: commented\nKubernetes|\n")
        self.assertEqual(
            [(term.text, term.near_miss) for term in terms.names],
            [("Priya", "pre-ah"), ("Okafor", None), ("Kubernetes", None)])
        self.assertEqual([term.text for term in terms.jargon], ["kubectl"])
        self.assertEqual(terms.notes, ())

    def test_accents_and_apostrophes_survive_and_are_normalised(self):
        decomposed = "José Núñez"
        terms = capture.parse_terms(
            f"{decomposed}\nO'Brien | O Brian\nZoë\nSiân\n")
        texts = [term.text for term in terms.names]
        self.assertEqual(texts, ["José Núñez", "O'Brien", "Zoë", "Siân"])
        text = capture.fill_term("{t}'s name came up.", "O'Brien")
        self.assertEqual(capture.found_terms(text, texts), ["O'Brien"])

    def test_the_dictionary_default_reads_only_typed_terms(self):
        self.dictionary.write_text(
            "# One term per line.\nPriya\n-banned\nOkafor\n\n"
            f"{capture.DICTIONARY_AUTO_MARKER}\nLearnedAutomatically\n",
            encoding="utf-8")
        code, out, _err = self.run_cli("--plan")
        self.assertEqual(code, 0)
        self.assertIn("Priya came up again", out)
        self.assertIn("Okafor", out)
        self.assertNotIn("LearnedAutomatically", out)
        self.assertNotIn("banned", out)
        self.assertIn(str(self.dictionary), out)

    def test_the_auto_marker_matches_the_runtime(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        match = re.search(r'^AUTO_MARKER = "([^"]+)"', source, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), capture.DICTIONARY_AUTO_MARKER)

    def test_terms_txt_in_the_evidence_root_wins_over_the_dictionary(self):
        self.dictionary.write_text("FromDictionary\n", encoding="utf-8")
        self.write_terms()
        code, out, _err = self.run_cli("--plan")
        self.assertEqual(code, 0)
        self.assertIn("Priya", out)
        self.assertNotIn("FromDictionary", out)

    def test_more_than_fifteen_terms_says_which_are_used(self):
        self.write_terms(FIFTEEN_TERMS + "Extra One\nExtra Two\n")
        code, out, _err = self.run_cli("--plan")
        self.assertEqual(code, 0)
        self.assertIn("first 15 of 17", out)
        self.assertNotIn("Extra One", out)

    def test_no_terms_at_all_is_refused_without_naming_anything(self):
        self.write_terms("# nothing yet\n\n-banned\n")
        code, _out, err = self.run_cli("--plan")
        self.assertEqual(code, 2)
        self.assertIn("lists no names or terms", err)
        self.assertFalse(self.directory.exists())
        self.write_terms("")
        self.assertEqual(self.run_cli("--plan")[0], 2)

    def test_a_missing_terms_file_is_refused(self):
        code, _out, err = self.run_cli("--terms", str(self.root / "nope.txt"),
                                       "--plan")
        self.assertEqual(code, 2)
        self.assertIn("no terms file", err)

    def test_notes_name_a_line_never_a_term(self):
        secret = "Ziggurat"
        terms = capture.parse_terms(
            f"{secret}\n{secret}\n{secret} | {secret.lower()}x\n"
            f"{'Q' * 80}\n| {secret}\nOther | other\n")
        self.assertEqual(len(terms.notes), 5)
        for note in terms.notes:
            self.assertNotIn(secret.casefold(), note.casefold())
            self.assertRegex(note, r"line \d+")


class AccuracyCaptureTests(AccuracyHarness):
    def test_three_returns_record_one_untroubled_case(self):
        self.write_terms()
        code, out, _err = self.run_cli(keys="\n\n\n", takes=[tone(3.0)])
        self.assertEqual(code, 0)
        self.assertIn("1 of 40, about 15 minutes left", out)
        self.assertIn("Saved.", out)
        session = self.load()
        self.assertEqual(session.status(session.cases[0]), "complete")
        self.assertEqual(session.status(session.cases[1]), "pending")
        self.assertEqual(
            (self.directory / "acc-001.txt").read_text(encoding="utf-8"),
            session.cases[0]["plan"]["reference"] + "\n")
        self.assertIn("pick up at case 2", out)

    def test_a_later_run_picks_up_at_the_next_case_with_the_same_plan(self):
        self.write_terms()
        self.run_cli(keys="\n\n\n", takes=[tone(3.0)])
        plan = [case["plan"] for case in self.load().cases]
        self.write_terms("Somebody Else\n")
        code, out, _err = self.run_cli(keys="\n\n\n", takes=[tone(3.0)])
        self.assertEqual(code, 0)
        self.assertIn("terms have changed", out)
        self.assertNotIn("Somebody Else", out)
        self.assertIn("2 of 40", out)
        session = self.load()
        self.assertEqual([case["plan"] for case in session.cases], plan)
        self.assertEqual(
            [session.status(case) for case in session.cases[:3]],
            ["complete", "complete", "pending"])

    def test_edit_starts_from_the_prompt_and_saves_what_was_said(self):
        self.write_terms()
        # Return, Return, e, then delete the last word and type a new one.
        code, _out, _err = self.run_cli(
            keys="\n\ne" + "\x17" + "standup." + "\r", takes=[tone(3.0)])
        self.assertEqual(code, 0)
        session = self.load()
        record = session.cases[0]["arms"]["take"]
        self.assertEqual(record["reference"],
                         "Priya came up again in this morning's standup.")
        self.assertTrue(record["reference_edited"])
        labels = json.loads(session.cases_path.read_text(encoding="utf-8"))
        self.assertEqual(labels["cases"][0]["terms"], ["Priya"])
        self.assertEqual(labels["cases"][0]["prompt"],
                         "Priya came up again in this morning's meeting.")

    def test_an_edit_that_drops_the_term_drops_its_label(self):
        session = self.make_session()
        case = session.cases[0]
        session.accept(case, tone(1.0), reference="Maria came up again.",
                       edited=True, peak=0.25)
        line = json.loads(session.manifest_path.read_text().splitlines()[0])
        self.assertEqual(line["terms"], [])

    def test_line_editor_moves_deletes_and_clears(self):
        self.keys = list("\x1b[D\x1b[DXY\x01Z\x05!\r")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.edit_line("p", "abc"), "ZaXYbc!")
        self.keys = list("\x15new\x1b[3~\r")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.edit_line("p", "old"), "new")
        self.keys = list("\x01\x1b[3~\r")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.edit_line("p", "old"), "ld")

    def test_skip_then_redo(self):
        self.write_terms()
        code, out, _err = self.run_cli(keys="sq")
        self.assertEqual(code, 0)
        self.assertIn("Skipped.", out)
        session = self.load()
        self.assertEqual(session.status(session.cases[0]), "skipped")
        code, out, _err = self.run_cli(keys="q")
        self.assertIn("2 of 40", out)
        code, out, _err = self.run_cli("--redo", "acc-001", keys="q")
        self.assertIn("Reopened acc-001", out)
        self.assertIn("1 of 40", out)

    def test_redo_removes_the_old_take_so_the_folder_matches_the_manifest(self):
        session = self.make_session()
        self.accept(session, session.cases[0])
        self.accept(session, session.cases[1])
        session.reopen("acc-001")
        session.save()
        session.write_manifest()
        self.assertFalse((self.directory / "acc-001.wav").exists())
        self.assertFalse((self.directory / "acc-001.txt").exists())
        ids = [json.loads(line)["id"] for line in
               session.manifest_path.read_text().splitlines()]
        self.assertEqual(ids, ["acc-002"])

    def test_free_dictations_and_long_takes_skip_as_groups(self):
        session = self.make_session()
        session.opt_into_extended()
        free = next(case for case in session.cases
                    if case["plan"]["kind"] == "free")
        long = next(case for case in session.cases
                    if case["plan"]["kind"] == "long")
        self.keys = list("F")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.run_accuracy_case(session, free, None),
                             "skipped")
        self.keys = list("L")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.run_accuracy_case(session, long, None),
                             "skipped")
        reloaded = self.load()
        for case in reloaded.cases:
            if case["plan"]["kind"] in ("free", "long"):
                self.assertEqual(reloaded.status(case), "skipped")
        self.assertEqual(
            sum(1 for case in reloaded.cases
                if reloaded.status(case) == "skipped"), 20)

    def test_a_free_dictation_needs_what_was_typed(self):
        session = self.make_session()
        free = next(case for case in session.cases
                    if case["plan"]["kind"] == "free")
        self.device.takes = [tone(3.0)]
        # Return records, Return stops, Return keeps and asks for the text.
        self.keys = list("\n\n\n") + list("Call Okafor at noon.\r")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.run_accuracy_case(session, free, None),
                             "done")
        line = json.loads(session.manifest_path.read_text().splitlines()[0])
        self.assertEqual(line["text"], "Call Okafor at noon.")
        self.assertEqual(line["terms"], ["Okafor"])
        self.assertEqual(line["kind"], "free")

    def test_essential_set_complete_stops_writes_and_prints_commands(self):
        session = self.make_session()
        essential = session.part_cases("essential")
        for case in essential[:-1]:
            self.accept(session, case)
        code, out, _err = self.run_cli(keys="\n\n\nn", takes=[tone(3.0)])
        self.assertEqual(code, 0)
        self.assertIn("40 of 40", out)
        self.assertIn("The essential set is complete", out)
        self.assertIn("Press x to carry on with the extended part (60 more",
                      out)
        self.assertIn("recognizer_replay.py record", out)
        session = self.load()
        self.assertFalse(session.extended_opted_in)
        self.assertEqual(len(session.manifest_path.read_text().splitlines()),
                         40)
        self.assertTrue(all(session.status(case) == "pending"
                            for case in session.part_cases("extended")))
        # A later run without asking does not start the extended part.
        code, out, _err = self.run_cli(keys="n")
        self.assertIn("The essential set is complete", out)
        self.assertNotIn("Extended part, 1 of 60", out)

    def test_x_or_the_flag_carries_on_into_the_extended_part(self):
        session = self.make_session()
        for case in session.part_cases("essential"):
            self.accept(session, case)
        code, out, _err = self.run_cli(keys="x\n\n\nq", takes=[tone(4.0)])
        self.assertEqual(code, 0)
        self.assertIn("Extended part, 1 of 60, about 38 minutes left", out)
        session = self.load()
        self.assertTrue(session.extended_opted_in)
        self.assertEqual(session.status(session.cases[40]), "complete")
        # Resumes in the extended part without asking again.
        code, out, _err = self.run_cli(keys="q")
        self.assertIn("Extended part, 2 of 60", out)

    def test_the_extended_flag_opts_in(self):
        session = self.make_session()
        for case in session.part_cases("essential"):
            self.accept(session, case)
        code, out, _err = self.run_cli("--extended", keys="q")
        self.assertIn("Extended part, 1 of 60", out)

    def test_a_break_is_offered_once_at_the_half_of_the_extended_part(self):
        session = self.make_session()
        session.opt_into_extended()
        for case in session.cases[:69]:
            self.accept(session, case)
        code, out, _err = self.run_cli(keys="sq")
        self.assertEqual(code, 0)
        self.assertIn("Halfway through the extended part", out)
        self.assertIn("next run starts at case 71", out)
        code, out, _err = self.run_cli(keys="sq")
        self.assertNotIn("Halfway", out)

    def test_microphone_zeros_stop_the_session_in_one_line(self):
        self.write_terms()
        code, out, err = self.run_cli(
            keys="\n\n\n\n\n\n", takes=[[0.0] * 32000, tone(2.0)])
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("Privacy & Security > Microphone", err)
        self.assertEqual(self.device.opened, 1)
        self.assertFalse((self.directory / "acc-001.wav").exists())

    def test_a_stream_that_cannot_open_says_what_to_allow(self):
        self.write_terms()

        def refuse(**_settings):
            raise OSError("Internal PortAudio error")

        self.device.InputStream = refuse
        code, _out, err = self.run_cli(keys="\n")
        self.assertEqual(code, 2)
        self.assertIn("Privacy & Security > Microphone", err)

    def test_unusable_takes_get_one_line_and_return_records_again(self):
        self.write_terms()
        quiet = [0.01 * value / 0.25 for value in tone(3.0)]
        clipped = [1.0 if value > 0 else -1.0 for value in tone(3.0)]
        silent = [0.0005 * value / 0.25 for value in tone(3.0)]
        short = tone(0.4)
        code, out, _err = self.run_cli(
            keys="\n" + "\n\n" * 4 + "\n\n",
            takes=[quiet, clipped, silent, short, tone(3.0)])
        self.assertEqual(code, 0)
        self.assertIn("Far too quiet", out)
        self.assertIn("Clipped", out)
        self.assertIn("Nothing was heard", out)
        self.assertIn("far too short for this prompt", out)
        self.assertEqual(self.device.opened, 5)
        record = self.load().cases[0]["arms"]["take"]
        self.assertEqual(record["peak"], 0.25)

    def test_a_flagged_take_can_be_kept_anyway(self):
        self.write_terms()
        quiet = [0.01 * value / 0.25 for value in tone(3.0)]
        code, out, _err = self.run_cli(keys="\n\na", takes=[quiet])
        self.assertIn("Far too quiet", out)
        self.assertEqual(self.load().status(self.load().cases[0]), "complete")

    def test_play_uses_the_default_output(self):
        self.write_terms()
        self.run_cli(keys="\n\np\n", takes=[tone(3.0)])
        self.assertEqual(self.device.played, 1)

    def test_take_checks(self):
        plan = {"kind": "term", "min_seconds": 1.5}
        problem = lambda samples: capture.take_problem(  # noqa: E731
            plan, samples, len(samples) / 16000,
            max(abs(value) for value in samples))
        self.assertIsNone(problem(tone(2.0)))
        with self.assertRaises(capture.CaptureError) as raised:
            problem([0.0] * 8000)
        self.assertIn("System Settings", str(raised.exception))
        # Too short to judge the microphone: a take, not a verdict on it.
        self.assertIn("far too short", capture.take_problem(
            plan, [0.0] * 100, 100 / 16000, 0.0))
        long_plan = {"kind": "long", "min_seconds": 15.0}
        self.assertIn("long take needs at least 15 s", capture.take_problem(
            long_plan, tone(10.0), 10.0, 0.25))


class AccuracyOutputTests(AccuracyHarness):
    def test_formats_and_owner_only_modes(self):
        session = self.make_session()
        for case in session.cases[:3]:
            self.accept(session, case, seconds=1.25)
        session.skip(session.cases[3])
        names = sorted(path.name for path in self.directory.iterdir())
        self.assertEqual(names, [
            "acc-001.txt", "acc-001.wav", "acc-002.txt", "acc-002.wav",
            "acc-003.txt", "acc-003.wav", "cases.json", "manifest.jsonl",
            "session.json"])
        for name in names:
            mode = stat.S_IMODE((self.directory / name).stat().st_mode)
            self.assertEqual(mode, 0o600, name)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        import wave

        with wave.open(str(self.directory / "acc-001.wav"), "rb") as source:
            self.assertEqual(
                (source.getnchannels(), source.getsampwidth(),
                 source.getframerate(), source.getnframes()),
                (1, 2, 16000, 20000))
        lines = session.manifest_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            entry = json.loads(line)
            self.assertEqual(list(entry),
                             ["id", "audio", "text", "kind", "terms"])
            self.assertEqual(
                (self.directory / f"{entry['id']}.txt").read_text().strip(),
                entry["text"])
        labels = json.loads(session.cases_path.read_text(encoding="utf-8"))
        self.assertEqual(labels["kind"], "whisper-face/accuracy-corpus-cases")
        self.assertEqual(labels["planned_cases"], 70 + 30)
        self.assertEqual(labels["skipped"], ["acc-004"])
        self.assertEqual(labels["cases"][0]["position"], "start")

    def test_nothing_recorded_writes_no_manifest_and_no_commands(self):
        session = self.make_session()
        self.assertFalse(session.write_manifest())
        self.assertFalse(session.manifest_path.exists())
        text = capture.render_accuracy_next_commands(session)
        self.assertIn("Nothing is recorded yet", text)
        self.assertNotIn("uv run", text)

    def test_next_commands_name_both_programs_and_a_private_transcripts_file(self):
        session = self.make_session()
        self.accept(session, session.cases[0])
        text = capture.render_accuracy_next_commands(session)
        for helper in (".models/bin/parrot-asr-helper",
                       ".models/bin/parrot-speech-runtime"):
            self.assertEqual(text.count(f"--helper {helper}"), 1)
            self.assertEqual(text.count(f"--parrot-helper {helper}"), 1)
        self.assertIn(f"--transcripts {self.root / 'owner_transcripts.json'}",
                      text)
        self.assertNotIn("recognizer_transcripts.json", text)
        self.assertIn(f"--dataset {session.manifest_path}", text)
        self.assertIn(f"--wav-dir {self.directory}", text)
        self.assertNotIn("--confirm-manual-review", text)

    def printed_commands(self, session):
        text = capture.render_accuracy_next_commands(session)
        commands, current = [], ""
        for line in text.splitlines():
            line = line.strip()
            if current or line.startswith("uv run"):
                current += " " + line.rstrip("\\").strip()
                if not line.endswith("\\"):
                    commands.append(shlex.split(current))
                    current = ""
        return commands

    def fake_helper(self) -> Path:
        helper = Path(self.temporary.name) / "fake-helper"
        helper.write_text(
            f"#!{sys.executable}\n"
            "import json, struct, sys\n"
            "assert sys.argv[1:] == ['--server'], sys.argv\n"
            "out, inp = sys.stdout.buffer, sys.stdin.buffer\n"
            "out.write(b'{\"ready\": true, \"model\": \"fake\"}\\n'); out.flush()\n"
            "while True:\n"
            "    head = inp.read(8)\n"
            "    if len(head) < 8: break\n"
            "    (count,) = struct.unpack('<Q', head)\n"
            "    inp.read(4 * count)\n"
            "    out.write(json.dumps({'ok': True, 'text': f'heard {count}'})"
            ".encode() + b'\\n'); out.flush()\n",
            encoding="utf-8")
        helper.chmod(0o755)
        return helper

    def assert_consumers_accept(self, session):
        sys.path.insert(0, str(ROOT / "benchmarks"))
        import benchmark_asr
        import recognizer_replay

        completed = [session.case_identity(case)
                     for case in session.completed_cases()]
        helper = self.fake_helper()
        commands = self.printed_commands(session)
        self.assertEqual(len(commands), 4)
        replays = [command for command in commands
                   if command[2] == "benchmarks/recognizer_replay.py"]
        benchmarks = [command for command in commands
                      if command[2] == "benchmark_asr.py"]
        self.assertEqual((len(replays), len(benchmarks)), (2, 2))
        for command in replays:
            argv = command[3:]
            argv[argv.index("--helper") + 1] = str(helper)
            with contextlib.redirect_stdout(io.StringIO()), \
                    warnings.catch_warnings():
                # recognizer_replay's Helper leaves its pipe to the collector.
                warnings.simplefilter("ignore", ResourceWarning)
                self.assertEqual(recognizer_replay.main(argv), 0)
            transcripts = Path(argv[argv.index("--transcripts") + 1])
            self.assertTrue(transcripts.is_relative_to(self.root))
            cases = recognizer_replay.load(transcripts)["cases"]
            self.assertEqual(sorted(case["id"] for case in cases),
                             sorted(f"recording-{name}" for name in completed))
            for case in cases:
                identity = case["id"].removeprefix("recording-")
                self.assertEqual(
                    case["said"],
                    (self.directory / f"{identity}.txt").read_text().strip())
                self.assertTrue(case["transcript"].startswith("heard "))
        for command in benchmarks:
            dataset = Path(command[command.index("--dataset") + 1])
            samples = benchmark_asr.select_samples(dataset, 1000)
            self.assertEqual([sample.utterance_id for sample in samples],
                             completed)
            engines = command[command.index("--engines") + 1:
                              command.index("--parrot-helper")]
            self.assertTrue(set(engines) <= set(
                benchmark_asr.MLX_ENGINES + tuple(
                    benchmark_asr.PARAKEET_ENGINES)))
            original_verify = benchmark_asr.verify_installed_parakeet_revision
            original_load = benchmark_asr.load_audio
            benchmark_asr.verify_installed_parakeet_revision = (
                lambda *_args, **_kwargs: None)
            try:
                import soundfile  # noqa: F401
            except ImportError:
                # soundfile is not cached offline here; read the same WAV
                # with the standard library, at the 16 kHz it demands.
                def wave_audio(path):
                    import numpy as np
                    samples, rate = capture.read_wav(path)
                    self.assertEqual(rate, 16000)
                    return np.asarray(samples, dtype=np.float32)

                benchmark_asr.load_audio = wave_audio
            try:
                model = benchmark_asr.load_model_specs(
                    benchmark_asr.DEFAULT_MODEL_SCORECARD)["parakeet-unified"]
                with contextlib.redirect_stdout(io.StringIO()):
                    records = benchmark_asr.run_parrot_helper(
                        model, samples, helper)
            finally:
                benchmark_asr.verify_installed_parakeet_revision = (
                    original_verify)
                benchmark_asr.load_audio = original_load
            self.assertEqual([record["ref"] for record in records],
                             [sample.reference for sample in samples])

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("afconvert"),
                         "recognizer_replay.py converts audio with afconvert")
    def test_an_essential_only_corpus_feeds_both_consumers_unchanged(self):
        session = self.make_session()
        for case in session.part_cases("essential"):
            self.accept(session, case)
        self.assert_consumers_accept(session)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("afconvert"),
                         "recognizer_replay.py converts audio with afconvert")
    def test_a_partial_corpus_with_skips_and_redos_feeds_both_consumers(self):
        session = self.make_session()
        session.opt_into_extended()
        for case in session.cases[:3]:
            self.accept(session, case)
        session.skip(session.cases[3])
        self.accept(session, session.cases[4])
        free = next(case for case in session.cases
                    if case["plan"]["kind"] == "free")
        self.accept(session, free)
        session.reopen("acc-002")
        session.save()
        session.write_manifest()
        self.assert_consumers_accept(session)


class AccuracyPrivacyTests(AccuracyHarness):
    def test_a_destination_inside_a_checkout_is_refused_even_if_ignored(self):
        checkout = Path(self.temporary.name) / "checkout"
        (checkout / ".git").mkdir(parents=True)
        (checkout / ".gitignore").write_text(".evidence/\n", encoding="utf-8")
        self.write_terms()
        self.assertIsNone(capture.private_destination_error(
            checkout / ".evidence" / "accuracy"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = capture.main([
                "accuracy", "--evidence-root", str(checkout / ".evidence"),
                "--terms", str(self.root / "terms.txt"), "--plan"])
        self.assertEqual(code, 2)
        self.assertIn("inside the Git checkout", err.getvalue())
        self.assertFalse((checkout / ".evidence").exists())
        self.assertIsNotNone(capture.checkout_destination_error(
            ROOT / ".evidence" / "accuracy"))

    def test_the_default_root_is_outside_the_checkout(self):
        parser = capture.build_parser()
        arguments = parser.parse_args(["accuracy"])
        self.assertEqual(arguments.evidence_root, "~/whisper-face-evidence")
        self.assertIsNone(arguments.terms)
        self.assertFalse(arguments.extended)

    def test_plan_mode_writes_nothing_before_the_session_starts(self):
        self.write_terms()
        code, out, _err = self.run_cli("--plan")
        self.assertEqual(code, 0)
        self.assertIn("Not started. The plan is fixed when you start", out)
        self.assertIn("Essential set     : 40 cases, about 15 minutes", out)
        self.assertIn("--- extended part ---", out)
        self.assertFalse(self.directory.exists())

    def test_terms_and_speech_land_only_in_the_session_folder(self):
        secret = "Zebulon"
        self.write_terms(f"{secret} | zebra\n")
        self.run_cli(keys="\n\n\n", takes=[tone(3.0)])
        holders = []
        for path in Path(self.temporary.name).resolve().rglob("*"):
            if path.is_file() and secret.encode() in path.read_bytes():
                holders.append(path)
        self.assertTrue(holders)
        for path in holders:
            self.assertTrue(
                path.parent == self.directory or path == self.root / "terms.txt",
                path)

    def test_no_error_message_can_name_a_term(self):
        tree_strings = [
            node for node in ast.walk(TREE)
            if isinstance(node, ast.FunctionDef) and node.name in (
                "parse_terms", "load_terms", "take_problem",
                "microphone_blocked_message", "accept")
        ]
        self.assertEqual(len(tree_strings), 5)
        for function in tree_strings:
            for node in ast.walk(function):
                if isinstance(node, ast.FormattedValue):
                    name = ast.unparse(node.value)
                    self.assertNotIn(name, ("term", "near", "term_text",
                                            "near_text", "reference", "line"),
                                     f"{function.name} formats {name}")


if __name__ == "__main__":
    unittest.main()
