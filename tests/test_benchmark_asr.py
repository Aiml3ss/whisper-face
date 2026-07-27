# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from array import array
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_asr
from benchmark_asr import (
    BoundedJSONLineReader,
    DEFAULT_MODEL_SCORECARD,
    FORMATTING_PUNCTUATION,
    FORMATTING_UNAVAILABLE_EMPTY,
    FORMATTING_UNAVAILABLE_SINGLE_CASE,
    FORMATTING_UNAVAILABLE_UNPUNCTUATED,
    PARAKEET_CLI_TIMEOUT_SECONDS,
    PARAKEET_HELPER_SAMPLE_TIMEOUT_SECONDS,
    PARAKEET_HELPER_STARTUP_TIMEOUT_SECONDS,
    PARAKEET_ENGINES,
    Sample,
    _cleanup_helper_process,
    _formatting_report_line,
    align_tokens,
    edit_distance,
    execution_model_provenance,
    evenly_spaced,
    harness_provenance,
    load_manifest_samples,
    load_model_specs,
    load_references,
    resolve_mlx_snapshot,
    run_parakeet,
    run_parrot_helper,
    score_formatting_records,
    score_records,
    select_samples,
    split_formatting_token,
    summarize_model_run,
    verify_installed_parakeet_revision,
)


class BenchmarkASRTests(unittest.TestCase):
    def test_evenly_spaced_selection_is_stable_and_includes_endpoints(self):
        items = [Path(str(index)) for index in range(10)]
        self.assertEqual(
            evenly_spaced(items, 4),
            [Path("0"), Path("3"), Path("6"), Path("9")],
        )
        self.assertEqual(evenly_spaced(items, 1), [Path("0")])
        self.assertEqual(evenly_spaced(items, 0), [])

    def test_load_references_uses_librispeech_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            chapter = Path(directory) / "1" / "2"
            chapter.mkdir(parents=True)
            (chapter / "1-2.trans.txt").write_text(
                "1-2-0000 HELLO WORLD\n1-2-0001 SECOND TEST\n",
                encoding="utf-8",
            )
            self.assertEqual(load_references(Path(directory)), {
                "1-2-0000": "HELLO WORLD",
                "1-2-0001": "SECOND TEST",
            })

    def test_edit_distance_handles_insert_delete_and_substitute(self):
        self.assertEqual(edit_distance("a b c".split(), "a x c".split()), 1)
        self.assertEqual(edit_distance("a b".split(), "a b c".split()), 1)
        self.assertEqual(edit_distance("a b c".split(), "a c".split()), 1)

    def test_score_records_uses_one_tokenizer_for_every_engine(self):
        tokenize = lambda text: text.lower().replace("!", "").split()
        result = score_records([
            {
                "engine": "candidate",
                "ref": "Hello world!",
                "hyp": "hello world",
                "audio_s": 2.0,
                "proc_s": 0.5,
            },
            {
                "engine": "candidate",
                "ref": "one two",
                "hyp": "one too",
                "audio_s": 1.0,
                "proc_s": 0.25,
            },
        ], tokenize=tokenize)
        self.assertEqual(result["utterances"], 2)
        self.assertEqual(result["wer_pct"], 25.0)
        self.assertEqual(result["exact_pct"], 50.0)
        self.assertEqual(result["rtfx"], 4.0)

    def test_model_specs_use_exact_reviewed_repositories_and_revisions(self):
        specs = load_model_specs(DEFAULT_MODEL_SCORECARD)
        self.assertEqual(
            (specs["mlx-tiny"].model_id, specs["mlx-tiny"].revision),
            (
                "mlx-community/whisper-tiny",
                "78c52ab98ca87f570bc57ad852e15ef7060f9f76",
            ),
        )
        self.assertEqual(
            specs["parakeet-unified"].model_id,
            "FluidInference/parakeet-unified-en-0.6b-coreml",
        )
        self.assertEqual(set(specs), {
            "mlx-tiny", "mlx-turbo", "parakeet-unified",
        })

    def test_mlx_snapshot_download_is_bound_to_exact_revision(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["mlx-turbo"]
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return "/tmp/exact-model"

        self.assertEqual(
            resolve_mlx_snapshot(spec, downloader=downloader),
            "/tmp/exact-model",
        )
        self.assertEqual(calls, [{
            "repo_id": spec.model_id,
            "revision": spec.revision,
        }])

    def test_harness_provenance_hashes_script_and_scorecard(self):
        provenance = harness_provenance(DEFAULT_MODEL_SCORECARD)
        self.assertEqual(provenance["script"], "benchmark_asr.py")
        self.assertEqual(provenance["model_scorecard"], "benchmarks/model_scorecard.json")
        self.assertEqual(len(provenance["script_sha256"]), 64)
        self.assertEqual(len(provenance["model_scorecard_sha256"]), 64)

    def test_run_summary_carries_model_and_executor_provenance(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["mlx-tiny"]
        summary = summarize_model_run([{
            "engine": spec.engine,
            "ref": "hello world",
            "hyp": "hello world",
            "audio_s": 1.0,
            "proc_s": 0.1,
        }], spec, executor="mlx-whisper",
            revision_status="verified-immutable-snapshot",
            preflight_status="not-applicable",
            tokenize=str.split)
        self.assertEqual(summary["requested_model_id"], spec.model_id)
        self.assertEqual(summary["requested_model_revision"], spec.revision)
        self.assertEqual(summary["resolved_model_id"], spec.model_id)
        self.assertEqual(summary["resolved_model_revision"], spec.revision)
        self.assertEqual(
            summary["model_revision_status"], "verified-immutable-snapshot")
        self.assertEqual(summary["runtime_role"], spec.runtime_role)
        self.assertEqual(summary["executor"], "mlx-whisper")

    def test_external_parakeet_executor_never_asserts_an_unproved_revision(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["parakeet-unified"]
        provenance = execution_model_provenance(
            spec, executor="macparakeet-cli",
            revision_status="unverified-external-executor",
            preflight_status="not-supported")
        self.assertEqual(provenance["requested_model_revision"], spec.revision)
        self.assertIsNone(provenance["resolved_model_id"])
        self.assertIsNone(provenance["resolved_model_revision"])
        self.assertEqual(
            provenance["model_revision_status"],
            "unverified-external-executor",
        )
        self.assertEqual(set(PARAKEET_ENGINES), {"parakeet-unified"})

        helper = execution_model_provenance(
            spec, executor="whisper-face-parakeet-helper",
            revision_status="unverified-helper-runtime-unattested",
            preflight_status="installed-sidecar-revision-matched")
        self.assertIsNone(helper["resolved_model_id"])
        self.assertIsNone(helper["resolved_model_revision"])
        self.assertEqual(
            helper["model_revision_status"],
            "unverified-helper-runtime-unattested")
        self.assertEqual(
            helper["model_preflight_status"],
            "installed-sidecar-revision-matched")

    def test_shipping_helper_sidecar_preflight_is_fail_closed_not_attestation(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["parakeet-unified"]
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            assets = (
                "parakeet_unified_encoder_int8.mlmodelc/weights.bin",
                "parakeet_unified_decoder.mlmodelc/weights.bin",
                "parakeet_unified_joint_decision_single_step.mlmodelc/weights.bin",
                "vocab.json",
                "metadata.json",
            )
            for relative in assets:
                target = model_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
                metadata = (
                    model_dir / ".cache" / "huggingface" / "download" /
                    relative)
                metadata.parent.mkdir(parents=True, exist_ok=True)
                Path(f"{metadata}.metadata").write_text(
                    spec.revision + "\n", encoding="utf-8")

            self.assertEqual(
                verify_installed_parakeet_revision(spec, model_dir),
                spec.revision,
            )
            self.assertEqual((model_dir / "vocab.json").read_bytes(), b"fixture")
            provenance = execution_model_provenance(
                spec, executor="whisper-face-parakeet-helper",
                revision_status="unverified-helper-runtime-unattested",
                preflight_status="installed-sidecar-revision-matched")
            self.assertIsNone(provenance["resolved_model_id"])
            self.assertIsNone(provenance["resolved_model_revision"])
            bad_metadata = (
                model_dir / ".cache" / "huggingface" / "download" /
                "vocab.json.metadata")
            bad_metadata.write_text("0" * 40 + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "revision drift"):
                verify_installed_parakeet_revision(spec, model_dir)

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("benchmark_asr.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "asset is missing"):
                run_parrot_helper(
                    spec, [], Path("/unused/helper"),
                    model_dir=Path(directory))
            popen.assert_not_called()

    @unittest.skipUnless(
        os.name == "posix",
        "macOS helper reader uses POSIX pipe descriptors with select",
    )
    def test_protocol_reader_bounds_startup_and_sample_responses(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'{"ready":true}\n')
            with os.fdopen(read_fd, "rb", buffering=0) as stream:
                reader = BoundedJSONLineReader(stream, maximum_bytes=32)
                self.assertEqual(reader.read(timeout=0.1), {"ready": True})
            read_fd = -1
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            os.close(write_fd)

        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(read_fd, "rb", buffering=0) as stream:
                reader = BoundedJSONLineReader(stream, maximum_bytes=16)
                with self.assertRaisesRegex(TimeoutError, "timed out"):
                    reader.read(timeout=0.01)
                os.write(write_fd, b'x' * 17 + b'\n')
                with self.assertRaisesRegex(RuntimeError, "size limit"):
                    reader.read(timeout=0.1)
            read_fd = -1
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            os.close(write_fd)

    def test_external_cli_timeout_still_removes_temporary_transcripts(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["parakeet-unified"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "macparakeet-cli"
            cli.write_text("fixture", encoding="utf-8")
            work = root / "work"
            work.mkdir()
            with mock.patch(
                    "benchmark_asr.tempfile.mkdtemp", return_value=str(work)), \
                    mock.patch(
                        "benchmark_asr.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("cli", 1)) as run:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_parakeet(spec, [], cli)
            self.assertEqual(
                run.call_args.kwargs["timeout"], PARAKEET_CLI_TIMEOUT_SECONDS)
            self.assertFalse(work.exists())

    def test_helper_sample_timeout_runs_terminate_wait_kill_cleanup(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["parakeet-unified"]

        class Input:
            def write(self, _value):
                return None

            def flush(self):
                return None

            def close(self):
                return None

        class Output:
            def close(self):
                return None

        class Process:
            def __init__(self):
                self.stdin = Input()
                self.stdout = Output()
                self.waits = 0
                self.terminated = False
                self.killed = False

            def wait(self, timeout):
                self.waits += 1
                if self.waits < 3:
                    raise subprocess.TimeoutExpired("helper", timeout)
                return 0

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        class Reader:
            def __init__(self):
                self.timeouts = []

            def read(self, *, timeout):
                self.timeouts.append(timeout)
                if len(self.timeouts) == 1:
                    return {"ready": True}
                raise TimeoutError("sample response timed out")

        process = Process()
        reader = Reader()
        with mock.patch(
                "benchmark_asr.verify_installed_parakeet_revision"), \
                mock.patch("benchmark_asr.subprocess.Popen", return_value=process), \
                mock.patch(
                    "benchmark_asr.load_audio",
                    return_value=array("f", [0.1, 0.2])):
            with self.assertRaisesRegex(TimeoutError, "sample response"):
                run_parrot_helper(
                    spec, [Sample("one", Path("one.flac"), "ONE")],
                    Path("helper"), reader_factory=lambda _stream: reader)
        self.assertEqual(reader.timeouts, [
            PARAKEET_HELPER_STARTUP_TIMEOUT_SECONDS,
            PARAKEET_HELPER_SAMPLE_TIMEOUT_SECONDS,
        ])
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.waits, 3)

        process = Process()
        _cleanup_helper_process(process, timeout=0.01)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_helper_reader_initialization_failure_still_cleans_up_process(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["parakeet-unified"]

        class Stream:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class Process:
            def __init__(self):
                self.stdin = Stream()
                self.stdout = Stream()
                self.waits = 0
                self.terminated = False
                self.killed = False

            def wait(self, timeout):
                self.waits += 1
                if self.waits < 3:
                    raise subprocess.TimeoutExpired("helper", timeout)
                return 0

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        process = Process()

        def fail_reader(_stream):
            raise RuntimeError("reader initialization failed")

        with mock.patch(
                "benchmark_asr.verify_installed_parakeet_revision"), \
                mock.patch("benchmark_asr.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "reader initialization"):
                run_parrot_helper(
                    spec, [], Path("helper"), reader_factory=fail_reader)

        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.waits, 3)


class FormattingScoringTests(unittest.TestCase):
    def test_split_formatting_token_extracts_core_and_trailing_marks(self):
        self.assertEqual(set(FORMATTING_PUNCTUATION), set(".,?!:;"))
        self.assertEqual(split_formatting_token("world."), ("world", "."))
        self.assertEqual(split_formatting_token('"Stop."'), ("Stop", "."))
        self.assertEqual(split_formatting_token('said,"'), ("said", ","))
        self.assertEqual(split_formatting_token("wait..."), ("wait", "..."))
        self.assertEqual(split_formatting_token("(yes),"), ("yes", ","))
        self.assertEqual(split_formatting_token("don't"), ("don't", ""))
        self.assertEqual(split_formatting_token("plain"), ("plain", ""))

    def test_align_tokens_returns_match_delete_and_insert_positions(self):
        self.assertEqual(
            align_tokens("a b c".split(), "a x c".split()),
            [(0, 0), (1, 1), (2, 2)])
        self.assertEqual(
            align_tokens("a b c".split(), "a c".split()),
            [(0, 0), (1, None), (2, 1)])
        self.assertEqual(
            align_tokens("a c".split(), "a b c".split()),
            [(0, 0), (None, 1), (1, 2)])
        self.assertEqual(align_tokens([], ["a"]), [(None, 0)])
        self.assertEqual(align_tokens(["a"], []), [(0, None)])

    def test_punctuation_f1_matches_hand_computed_counts(self):
        result = score_formatting_records([{
            "engine": "candidate",
            "ref": "Hello, world. This is fine.",
            "hyp": "Hello world. This is fine.",
        }])
        self.assertEqual(result["reference_punctuation_marks"], 3)
        self.assertEqual(result["hypothesis_punctuation_marks"], 2)
        self.assertEqual(result["matched_punctuation_marks"], 2)
        self.assertEqual(result["punctuation_precision_pct"], 100.0)
        self.assertEqual(result["punctuation_recall_pct"], 66.67)
        self.assertEqual(result["punctuation_f1_pct"], 80.0)
        self.assertEqual(result["aligned_equal_tokens"], 5)
        self.assertEqual(result["capitalization_match_pct"], 100.0)
        self.assertEqual(result["cased_wer_pct"], 20.0)
        self.assertEqual(result["reference_punctuated_token_pct"], 60.0)

    def test_punctuation_scoring_ignores_marks_on_misrecognized_words(self):
        result = score_formatting_records([{
            "engine": "candidate",
            "ref": "Good morning, Sam",
            "hyp": "Good evening, Sam.",
        }])
        self.assertEqual(result["aligned_equal_tokens"], 2)
        self.assertEqual(result["reference_punctuation_marks"], 0)
        self.assertEqual(result["hypothesis_punctuation_marks"], 1)
        self.assertEqual(result["punctuation_precision_pct"], 0.0)
        self.assertIsNone(result["punctuation_recall_pct"])
        self.assertEqual(result["punctuation_f1_pct"], 0.0)
        self.assertEqual(result["capitalization_match_pct"], 100.0)
        self.assertEqual(result["cased_wer_pct"], 66.6667)

    def test_capitalization_match_rate_over_aligned_equal_tokens(self):
        result = score_formatting_records([{
            "engine": "candidate",
            "ref": "Hello world. Sam left.",
            "hyp": "hello world. sam left.",
        }])
        self.assertEqual(result["capitalization_match_pct"], 50.0)
        self.assertEqual(result["punctuation_f1_pct"], 100.0)
        self.assertEqual(result["cased_wer_pct"], 50.0)

    def test_librispeech_style_references_report_unavailable_not_zeros(self):
        result = score_formatting_records([
            {"engine": "candidate", "ref": "HELLO WORLD",
             "hyp": "Hello, world."},
            {"engine": "candidate", "ref": "SECOND TEST",
             "hyp": "Second test."},
        ])
        self.assertEqual(result, FORMATTING_UNAVAILABLE_UNPUNCTUATED)
        self.assertEqual(result, "unavailable — references unpunctuated")

    def test_single_case_punctuated_references_are_unavailable(self):
        for reference in ("hello, world.", "HELLO, WORLD."):
            result = score_formatting_records([{
                "engine": "candidate", "ref": reference, "hyp": reference}])
            self.assertEqual(result, FORMATTING_UNAVAILABLE_SINGLE_CASE)
        self.assertEqual(
            score_formatting_records([]), FORMATTING_UNAVAILABLE_EMPTY)

    def test_detection_uses_two_percent_punctuated_token_floor(self):
        sparse = " ".join(["word"] * 99 + ["End."])
        self.assertEqual(
            score_formatting_records([
                {"engine": "candidate", "ref": sparse, "hyp": sparse}]),
            FORMATTING_UNAVAILABLE_UNPUNCTUATED)
        boundary = " ".join(["word"] * 49 + ["End."])
        result = score_formatting_records([
            {"engine": "candidate", "ref": boundary, "hyp": boundary}])
        self.assertIsInstance(result, dict)
        self.assertEqual(result["reference_punctuated_token_pct"], 2.0)
        self.assertEqual(result["cased_wer_pct"], 0.0)
        self.assertEqual(result["punctuation_f1_pct"], 100.0)

    def test_summary_carries_formatting_only_when_requested(self):
        spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["mlx-tiny"]
        records = [{
            "engine": spec.engine,
            "ref": "Hello, world.",
            "hyp": "Hello world.",
            "audio_s": 1.0,
            "proc_s": 0.1,
        }]
        keywords = dict(
            executor="mlx-whisper",
            revision_status="verified-immutable-snapshot",
            preflight_status="not-applicable",
            tokenize=str.split)
        plain = summarize_model_run(records, spec, **keywords)
        self.assertNotIn("formatting_scoring", plain)
        scored = summarize_model_run(
            records, spec, formatting=True, **keywords)
        self.assertEqual(scored["wer_pct"], 50.0)
        self.assertEqual(set(scored["formatting_scoring"]), {
            "cased_wer_pct",
            "punctuation_precision_pct",
            "punctuation_recall_pct",
            "punctuation_f1_pct",
            "capitalization_match_pct",
            "aligned_equal_tokens",
            "reference_punctuation_marks",
            "hypothesis_punctuation_marks",
            "matched_punctuation_marks",
            "reference_punctuated_token_pct",
        })
        librispeech = summarize_model_run([{
            "engine": spec.engine,
            "ref": "HELLO WORLD",
            "hyp": "hello world",
            "audio_s": 1.0,
            "proc_s": 0.1,
        }], spec, formatting=True, **keywords)
        self.assertEqual(
            librispeech["formatting_scoring"],
            FORMATTING_UNAVAILABLE_UNPUNCTUATED)

    def test_formatting_report_line_shows_unavailable_and_metrics(self):
        self.assertEqual(
            _formatting_report_line(FORMATTING_UNAVAILABLE_UNPUNCTUATED),
            "unavailable — references unpunctuated")
        line = _formatting_report_line({
            "cased_wer_pct": 20.0,
            "punctuation_precision_pct": 100.0,
            "punctuation_recall_pct": 66.67,
            "punctuation_f1_pct": 80.0,
            "capitalization_match_pct": None,
        })
        self.assertIn("cased WER 20.00%", line)
        self.assertIn("100.00/66.67/80.00%", line)
        self.assertIn("case match n/a%", line)

    def test_main_accepts_formatting_scoring_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "benchmark_asr.py", "--dataset", directory,
                "--engines", "mlx-tiny",
                "--output-dir", str(Path(directory) / "out"),
                "--formatting-scoring",
            ]
            with mock.patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(SystemExit, "no referenced"):
                    benchmark_asr.main()


class ManifestCorpusTests(unittest.TestCase):
    def test_manifest_dataset_keeps_punctuated_cased_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "audio").mkdir()
            for name in ("one.wav", "two.wav"):
                (root / "audio" / name).write_bytes(b"fixture")
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                '{"id": "one", "audio": "audio/one.wav",'
                ' "text": "Hello, world."}\n'
                "\n"
                '{"audio": "audio/two.wav", "text": "Second test?"}\n',
                encoding="utf-8")
            samples = select_samples(manifest, None)
            self.assertEqual(
                [sample.utterance_id for sample in samples], ["one", "two"])
            self.assertEqual(samples[0].reference, "Hello, world.")
            self.assertEqual(samples[1].reference, "Second test?")
            self.assertEqual(samples[0].audio_path, root / "audio" / "one.wav")
            limited = select_samples(manifest, 1)
            self.assertEqual(
                [sample.utterance_id for sample in limited], ["one"])

    def test_manifest_rejects_bad_lines_duplicates_and_missing_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.wav").write_bytes(b"fixture")
            manifest = root / "manifest.jsonl"

            manifest.write_text("not json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid manifest JSON"):
                load_manifest_samples(manifest)

            manifest.write_text(
                '{"audio": "one.wav", "text": "First."}\n'
                '{"id": "one", "audio": "one.wav", "text": "Again."}\n',
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_manifest_samples(manifest)

            manifest.write_text(
                '{"audio": "gone.wav", "text": "Missing."}\n',
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "audio file is missing"):
                load_manifest_samples(manifest)

            manifest.write_text(
                '{"audio": "one.wav", "text": "  "}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reference text"):
                load_manifest_samples(manifest)

            manifest.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no samples"):
                load_manifest_samples(manifest)

    def test_directory_dataset_still_uses_librispeech_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            chapter = Path(directory) / "1" / "2"
            chapter.mkdir(parents=True)
            (chapter / "1-2.trans.txt").write_text(
                "1-2-0000 HELLO WORLD\n", encoding="utf-8")
            (chapter / "1-2-0000.flac").write_bytes(b"fixture")
            samples = select_samples(Path(directory), None)
            self.assertEqual(
                [sample.utterance_id for sample in samples], ["1-2-0000"])
            self.assertEqual(samples[0].reference, "HELLO WORLD")


if __name__ == "__main__":
    unittest.main()
