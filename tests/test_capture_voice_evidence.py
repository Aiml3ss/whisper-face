# /// script
# requires-python = ">=3.10"
# dependencies = []
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
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
