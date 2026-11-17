# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""The public benchmark's rules, each broken on purpose.

benchmark_public.py measures Whisper Face on a corpus anyone can rerun, and
public_scorecard.py turns its result into the page that is published. These
tests hold the corpus to its own rules (written here, synthetic, no owner
data, every name declared), the scoring to its definitions, the run to a
fixed seed, and the published result to what it may claim: the exact
machine, OS, model and app revision; the corpus checked in now; numbers that
follow from the texts it carries; nothing the outside probe has to measure;
and no other product unless the owner has recorded its terms.
"""

import contextlib
import copy
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_public as bench  # noqa: E402
import public_scorecard  # noqa: E402
from public_scorecard import PublicationError  # noqa: E402


METHOD = ROOT / "docs" / "benchmarks" / "public-benchmark.md"
CORPUS = bench.load_corpus()
REVISION = "a" * 40


def valid_result(corpus=CORPUS):
    """A result as run() builds it, from stand-in measurements."""
    ids = [take["id"] for take in corpus["takes"]]
    texts = {take["id"]: take["text"] for take in corpus["takes"]}
    seconds = {take["id"]: (24.0 if take["category"] == "long" else 3.0)
               for take in corpus["takes"]}
    runtime = {
        "texts": dict(texts),
        "words": {take_id: len(texts[take_id].split()) for take_id in ids},
        "round_trip_ms": {take_id: [40.0, 41.0, 42.0] for take_id in ids},
        "processing_ms": {take_id: [39.0, 40.0, 41.0] for take_id in ids},
    }
    row = {"typed": "", "meant": "", "raw": "", "answered_by": "new",
           "insertion_state": "verified", "insertion_verified": True,
           "delivered_as_meant": True, "release_ms": 80.0,
           "recognition_ms": 1.0}
    app = {"defaults": {take_id: dict(row, typed=texts[take_id],
                                      meant=texts[take_id])
                        for take_id in ids}}
    product = {
        "product_id": "whisper-face", "version": "v0.6.0-1-gb19e18e",
        "revision": REVISION, "tree_clean": True,
        "runtime_source_sha256": "b" * 64, "runtime_binary_sha256": "c" * 64,
    }
    environment = {
        "environment_id": "mac16-8-apple-m4-pro-macos-27-3",
        "hardware": "Mac16,8, Apple M4 Pro, 24 GB",
        "os_name": "macOS", "os_version": "27.3 (26C5035r)",
        "python_version": "3.12.8", "whisper_face_revision": REVISION,
        "software": [{"name": "whisper-face", "version": "v0.6.0"}],
    }
    model = {"model_id": "FluidInference/parakeet-unified-en-0.6b-coreml",
             "revision": "4252711f6f060f9a2f91e5f081a806d7f45eebd8",
             "files_sha256": "d" * 64, "files": 12}
    return bench.build_result(
        corpus, voices=bench.voice_for_takes(corpus),
        audio_sha256={take_id: "e" * 64 for take_id in ids},
        seconds=seconds, runtime=runtime, app=app, product=product,
        model=model, environment=environment, repeats=3, realtime=True,
        command="uv run benchmark_public.py run --runtime r --models m "
                "--out o",
        started_utc="2026-11-17T12:00:00Z")


class CorpusRuleTests(unittest.TestCase):
    def test_the_checked_in_corpus_keeps_every_rule(self):
        self.assertEqual(bench.corpus_problems(CORPUS), [])

    def test_it_covers_names_terms_near_misses_numbers_and_long_takes(self):
        counts = Counter(take["category"] for take in CORPUS["takes"])
        self.assertEqual(set(counts), set(bench.CATEGORIES))
        self.assertGreaterEqual(counts["names"] + counts["terms"], 40)
        self.assertGreaterEqual(counts["near_miss"], 20)
        self.assertGreaterEqual(counts["numbers"], 20)
        self.assertGreaterEqual(counts["long"], 4)

    def test_an_undeclared_name_is_caught(self):
        broken = copy.deepcopy(CORPUS)
        broken["takes"][0]["terms"] = broken["takes"][0]["terms"][:1]
        self.assertTrue(any("does not declare" in line
                            for line in bench.corpus_problems(broken)))

    def test_a_near_miss_that_says_a_glossary_term_is_caught(self):
        broken = copy.deepcopy(CORPUS)
        near = next(take for take in broken["takes"]
                    if take["category"] == "near_miss")
        near["text"] = "Callum will add a column to the spreadsheet."
        self.assertNotEqual(bench.corpus_problems(broken), [])

    def test_the_licence_and_privacy_statements_are_required(self):
        for key, value in (("licence", {"text": "MIT", "audio": ""}),
                           ("privacy", "Recorded by the owner."),
                           ("synthetic_speech", False)):
            broken = copy.deepcopy(CORPUS)
            broken[key] = value
            with self.subTest(key=key):
                self.assertNotEqual(bench.corpus_problems(broken), [])

    def test_the_voices_and_seed_are_fixed(self):
        for key, value in (("voices", ["Samantha"]),
                           ("rate_words_per_minute", 200)):
            broken = copy.deepcopy(CORPUS)
            broken["speech"][key] = value
            with self.subTest(key=key):
                self.assertNotEqual(bench.corpus_problems(broken), [])
        broken = copy.deepcopy(CORPUS)
        broken["seed"] = None
        self.assertNotEqual(bench.corpus_problems(broken), [])

    def test_sentences_taken_from_the_repository_match_their_source(self):
        for take in CORPUS["takes"]:
            if take["source"] == "written-for-this-corpus":
                continue
            path, _, case_id = take["source"].partition("#")
            cases = {case["id"]: case for case in json.loads(
                (ROOT / path).read_text(encoding="utf-8"))["cases"]}
            with self.subTest(take=take["id"]):
                self.assertEqual(cases[case_id]["expected"], take["text"])

    def test_no_audio_is_checked_in(self):
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
            check=True).stdout.splitlines()
        audio = [name for name in tracked if name.startswith("benchmarks/")
                 and name.lower().endswith((".wav", ".aiff", ".aif", ".m4a",
                                            ".caf", ".flac", ".mp3"))]
        self.assertEqual(audio, [])


class ScoringTests(unittest.TestCase):
    def test_words_ignore_case_punctuation_and_number_grouping(self):
        self.assertEqual(bench.normalized_words("It's $1,250.50, at 3:30 p.m."),
                         ["it's", "1250.50", "at", "3:30", "pm"])
        self.assertEqual(bench.normalized_words("25%"), ["25", "percent"])

    def test_a_term_counts_only_as_a_whole_word(self):
        self.assertEqual(bench.term_hits("Ingrid's grid", "Ingrid"), 1)
        self.assertEqual(bench.term_hits("in a grid", "Ingrid"), 0)
        self.assertEqual(bench.term_hits("Callum and callum", "Callum"), 2)

    def test_a_format_target_must_be_written_whole(self):
        self.assertTrue(bench.target_written("Room 214.", ["214"]))
        self.assertFalse(bench.target_written("Room 2140.", ["214"]))
        self.assertTrue(bench.target_written("at 3:30 PM", ["3:30 p.m.",
                                                            "3:30 PM"]))

    def test_recall_false_insertions_and_formatting(self):
        takes = {take["id"]: take for take in CORPUS["takes"]}
        records = [
            {"id": "names-01",
             "hypothesis": "Ask Shivan Kearney to send the draft before lunch."},
            {"id": "near-miss-01",
             "hypothesis": "Add a Callum to the spreadsheet."},
            {"id": "numbers-01", "hypothesis": "The invoice total is $1250.50."},
            {"id": "everyday-01", "hypothesis": takes["everyday-01"]["text"]},
        ]
        score = bench.score_takes(records, CORPUS)
        self.assertEqual(score["terms"]["expected"], 2)
        self.assertEqual(score["terms"]["recalled"], 1)
        self.assertEqual(score["terms"]["recall_pct"], 50.0)
        self.assertEqual(score["terms"]["false_insertions"], 1)
        self.assertEqual(score["terms"]["false_insertions_in_near_misses"], 1)
        self.assertEqual(score["formatting"]["targets"], 1)
        self.assertEqual(score["formatting"]["written_exactly"], 0)
        # "$1250.50" and "$1,250.50" are the same words; "Shivan" and
        # "Callum" are one substitution each.
        reference_words = sum(
            len(bench.normalized_words(takes[record["id"]]["text"]))
            for record in records)
        self.assertEqual(score["wer_pct"],
                         round(100 * 2 / reference_words, 4))

    def test_latency_is_bucketed_by_take_length(self):
        seconds = {"a": 2.0, "b": 9.0, "c": 30.0, "d": 4.0}
        milliseconds = {"a": 40.0, "b": 60.0, "c": 200.0, "d": 50.0}
        summary = bench.latency_summary(seconds, milliseconds)
        self.assertEqual(summary["up-to-5s"]["takes"], 2)
        self.assertEqual(summary["5-to-15s"]["p50"], 60.0)
        self.assertEqual(summary["over-15s"]["max"], 200.0)
        self.assertEqual(summary["all"]["takes"], 4)


class SeedTests(unittest.TestCase):
    def test_voices_are_balanced_and_fixed_by_the_seed(self):
        voices = bench.voice_for_takes(CORPUS)
        self.assertEqual(voices, bench.voice_for_takes(copy.deepcopy(CORPUS)))
        counts = Counter(voices.values())
        self.assertEqual(set(counts), set(bench.VOICES))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        reseeded = dict(CORPUS, seed=CORPUS["seed"] + 1)
        self.assertNotEqual(voices, bench.voice_for_takes(reseeded))

    def test_each_pass_has_its_own_fixed_order(self):
        ids = [take["id"] for take in CORPUS["takes"]]
        first = bench.run_order(ids, CORPUS["seed"], 0)
        self.assertEqual(first, bench.run_order(ids, CORPUS["seed"], 0))
        self.assertNotEqual(first, bench.run_order(ids, CORPUS["seed"], 1))
        self.assertEqual(sorted(first), sorted(ids))


class ResultRuleTests(unittest.TestCase):
    def setUp(self):
        self.result = valid_result()

    def refused(self, result, message):
        with self.assertRaisesRegex(PublicationError, message):
            public_scorecard.validate_benchmark_result(result)

    def test_a_result_built_by_the_run_is_accepted_and_rendered(self):
        public_scorecard.validate_benchmark_result(self.result)
        page = public_scorecard.render_benchmark_markdown(self.result)
        for text in ("Mac16,8, Apple M4 Pro", "macOS 27.3 (26C5035r)",
                     REVISION, "4252711f6f060f9a2f91e5f081a806d7f45eebd8",
                     "Synthetic speech", "needs the outside probe",
                     "release_to_text_on_screen_ms", "Name and term recall",
                     "uv run benchmark_public.py run", "public_benchmark_rivals"):
            self.assertIn(text, page)

    def test_the_machine_must_be_named(self):
        for key, value in (("hardware", "unknown"), ("os_version", "TBD"),
                           ("python_version", "")):
            result = copy.deepcopy(self.result)
            result["environment"][key] = value
            with self.subTest(key=key):
                self.refused(result, key)

    def test_the_app_revision_must_be_exact_clean_and_consistent(self):
        result = copy.deepcopy(self.result)
        result["product"]["revision"] = "b19e18e"
        self.refused(result, "40-character")
        result = copy.deepcopy(self.result)
        result["product"]["tree_clean"] = False
        self.refused(result, "uncommitted")
        result = copy.deepcopy(self.result)
        result["environment"]["whisper_face_revision"] = "f" * 40
        self.refused(result, "different app revision")

    def test_the_model_must_be_named_and_hashed(self):
        result = copy.deepcopy(self.result)
        result["model"]["revision"] = "main"
        self.refused(result, "model revision")
        result = copy.deepcopy(self.result)
        result["model"]["files_sha256"] = None
        self.refused(result, "hash the model")

    def test_a_result_of_another_corpus_or_seed_is_refused(self):
        result = copy.deepcopy(self.result)
        result["corpus"]["sha256"] = "0" * 64
        self.refused(result, "corpus checked in now")
        result = copy.deepcopy(self.result)
        result["run"]["seed"] = 1
        self.refused(result, "seed")
        result = copy.deepcopy(self.result)
        result["corpus"]["synthetic_speech"] = False
        self.refused(result, "synthetic")

    def test_a_number_only_the_probe_can_give_is_refused(self):
        result = copy.deepcopy(self.result)
        result["outside_probe"]["release_to_text_on_screen_ms"]["value"] = 120
        self.refused(result, "outside probe")
        result = copy.deepcopy(self.result)
        del result["outside_probe"]["bytes_sent_per_dictation"]
        self.refused(result, "outside probe")

    def test_numbers_must_keep_how_they_were_measured(self):
        result = copy.deepcopy(self.result)
        result["recognition"]["measured_by"] = "outside-probe"
        self.refused(result, "runtime-in-process")
        result = copy.deepcopy(self.result)
        result["app_path"]["measured_by"] = "real-app"
        self.refused(result, "app-on-fake-mac")

    def test_an_edited_number_that_the_texts_do_not_give_is_refused(self):
        result = copy.deepcopy(self.result)
        result["recognition"]["accuracy"]["wer_pct"] = 1.5
        self.refused(result, "does not follow")
        result = copy.deepcopy(self.result)
        result["app_path"]["configurations"]["defaults"]["accuracy"][
            "terms"]["recall_pct"] = 99.0
        self.refused(result, "does not follow")

    def test_the_rerun_command_names_no_local_folder(self):
        result = copy.deepcopy(self.result)
        result["run"]["command"] = (
            "uv run benchmark_public.py run --runtime /Users/someone/rt "
            "--models <models> --out <out>")
        self.refused(result, "local folder")
        args = bench.argparse.Namespace(
            repeats=5, configurations=["defaults"], pace="real-time")
        command = bench.rerun_command(args)
        self.assertEqual(
            command, "uv run benchmark_public.py run --runtime <runtime> "
            "--models <models> --out <out> --repeats 5 "
            "--configurations defaults")
        self.refused_not(dict(self.result, run=dict(
            self.result["run"], command=command)))

    def refused_not(self, result):
        public_scorecard.validate_benchmark_result(result)

    def test_every_take_appears_once_with_a_corpus_voice(self):
        result = copy.deepcopy(self.result)
        result["takes"].pop()
        self.refused(result, "every take")
        result = copy.deepcopy(self.result)
        result["takes"][0]["voice"] = "Zarvox"
        self.refused(result, "voice")

    def test_another_product_is_refused_until_the_owner_records_its_terms(self):
        result = copy.deepcopy(self.result)
        result["product"]["product_id"] = "wispr-flow"
        self.refused(result, "wispr-flow")
        partial = {"wispr-flow": {
            "product_id": "wispr-flow", "account_held_by": "project-owner",
            "terms_read_on": "2026-12-01", "terms_reference": "terms page",
            "terms_allow_publishing": True, "recorded_by_owner": False}}
        with self.assertRaises(PublicationError):
            public_scorecard.validate_benchmark_result(result, rivals=partial)
        partial["wispr-flow"]["recorded_by_owner"] = True
        public_scorecard.validate_benchmark_result(result, rivals=partial)

    def test_the_checked_in_rivals_registry_allows_no_one(self):
        registry = json.loads(bench.RIVALS.read_text(encoding="utf-8"))
        self.assertEqual(registry["rivals"], [])
        self.assertIn("terms", registry["rule"])

    def test_the_command_line_refuses_and_renders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(self.result), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(public_scorecard.main(
                    ["benchmark", str(path)]), 0)
            self.assertIn("# Whisper Face public benchmark",
                          output.getvalue())
            broken = copy.deepcopy(self.result)
            broken["environment"]["hardware"] = "n/a"
            path.write_text(json.dumps(broken), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(public_scorecard.main(
                    ["benchmark", str(path)]), 1)
            self.assertIn("refusing to publish", output.getvalue())


class RunGuardTests(unittest.TestCase):
    def test_audio_can_never_be_written_into_the_checkout(self):
        inside = ROOT / "benchmark-public-out-test"
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = bench.main(["run", "--runtime", "/bin/sh", "--models",
                               str(ROOT), "--out", str(inside)])
        self.assertEqual(code, 2)
        self.assertFalse(inside.exists())
        if sys.platform == "darwin":
            self.assertIn("outside this checkout", errors.getvalue())

    def test_check_passes_from_a_clone(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(bench.main(["check"]), 0)
        self.assertIn(f"{len(CORPUS['takes'])} takes", output.getvalue())


class MethodDocumentTests(unittest.TestCase):
    def setUp(self):
        self.text = METHOD.read_text(encoding="utf-8")

    def test_it_states_the_corpus_seed_command_and_limits(self):
        counts = Counter(take["category"] for take in CORPUS["takes"])
        self.assertIn(f"{len(CORPUS['takes'])} takes", self.text)
        for category, count in counts.items():
            self.assertIn(f"| `{category}` | {count} |", self.text)
        self.assertIn(str(CORPUS["seed"]), self.text)
        self.assertIn("uv run benchmark_public.py run", self.text)
        self.assertIn("uv run public_scorecard.py benchmark", self.text)
        for name in bench.OUTSIDE_PROBE_METRICS:
            self.assertIn(f"`{name}`", self.text)
        self.assertIn("public_benchmark_rivals.json", self.text)
        self.assertIn("What it cannot show", self.text)

    def test_it_makes_no_comparative_claim(self):
        lowered = self.text.lower()
        self.assertNotIn("faster than", lowered)
        self.assertNotIn("more accurate than", lowered)

    def test_the_reproducibility_manifest_declares_both_files(self):
        manifest = json.loads((ROOT / "benchmarks" / "reproducibility.json")
                              .read_text(encoding="utf-8"))
        declared = {entry["path"]: entry for entry in manifest["corpora"]}
        corpus = declared["benchmarks/public_benchmark_corpus.json"]
        self.assertEqual(corpus["cases"], len(CORPUS["takes"]))
        self.assertTrue(corpus["command"].startswith(
            "uv run benchmark_public.py"))
        self.assertIn("benchmarks/public_benchmark_rivals.json", declared)


@unittest.skipUnless(sys.platform == "darwin",
                     "the app path boots the macOS runtime path of dictate.py")
class AppPathTests(unittest.TestCase):
    """The driver the run uses, on the fake Mac's stand-in runtime."""

    def test_both_configurations_boot_dictate_and_report_delivery(self):
        import numpy as np

        sys.path.append(str(ROOT / "tests"))
        import fake_platform

        audio = fake_platform.speech_audio()
        samples = bench.array("f", audio.astype(np.float32).tolist())
        glossary = [entry["term"] for entry in CORPUS["glossary"]]
        said = "Ask Siobhan Kearney to send the draft before lunch."
        for configuration in bench.CONFIGURATIONS:
            with self.subTest(configuration=configuration):
                app = bench.AppPath(configuration, glossary, realtime=False)
                self.addCleanup(app.close)
                try:
                    dictionary = Path(
                        app.mac.dictate.DICTIONARY_FILE).read_text()
                    self.assertEqual(
                        "Siobhan" in dictionary.splitlines(),
                        configuration == "dictionary")
                    row = app.dictate(samples, heard=said)
                    self.assertEqual(row["answered_by"], "new")
                    self.assertEqual(row["typed"], said)
                    self.assertTrue(row["delivered_as_meant"])
                    self.assertIs(row["insertion_verified"], True)
                    self.assertIsNotNone(row["release_ms"])
                    summary = bench.delivery_summary([row])
                    self.assertEqual(summary["delivered_as_meant"], 1)
                finally:
                    app.close()


if __name__ == "__main__":
    unittest.main()
