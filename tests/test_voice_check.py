# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""An update tried on the person's own voice, against fake recognisers.

Every case builds a temporary home with a small accuracy corpus (16 kHz WAVs
and the ``cases.json`` capture_voice_evidence.py writes), a running checkout
and a candidate checkout. The recognisers are tiny Python programs that speak
the real framed protocol and answer from a table keyed by the recording's
length, so both versions' replays, the candidate's own process and the
scoring all run for real, with no audio model, no network and no real home.
"""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import parrot_core  # noqa: E402
import self_update  # noqa: E402
import voice_check  # noqa: E402
import whisper_face_gui  # noqa: E402

# What the person said in each recording. The names are the corpus's terms.
SAID = {
    "acc-001": ("Send the deck to Priya before lunch.", ["Priya"], []),
    "acc-002": ("Okafor signed off on the budget.", ["Okafor"], []),
    "acc-003": ("Ask Priya and Okafor about the launch.", ["Priya", "Okafor"],
                []),
    "acc-004": ("We should meet after the review.", [], ["Priya"]),
}
# Each recording is a different length, which is how a fake recogniser tells
# them apart: its table maps a sample count to what it "heard".
LENGTHS = {case: 4000 + 1000 * index
           for index, case in enumerate(sorted(SAID))}


def write_corpus(home: Path) -> Path:
    corpus = home / "whisper-face-evidence" / "accuracy"
    corpus.mkdir(parents=True)
    cases = []
    for case, (text, terms, avoid) in sorted(SAID.items()):
        with wave.open(str(corpus / f"{case}.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16_000)
            samples = array("h", [12000 if i % 2 else -12000
                                  for i in range(LENGTHS[case])])
            handle.writeframes(samples.tobytes())
        (corpus / f"{case}.txt").write_text(text + "\n")
        cases.append({"id": case, "audio": f"{case}.wav", "text": text,
                      "kind": "term" if terms else "ordinary",
                      "terms": terms, "must_not_write": avoid})
    (corpus / "cases.json").write_text(json.dumps({
        "schema_version": 1, "kind": "whisper-face/accuracy-corpus-cases",
        "sample_rate_hz": 16000, "planned_cases": len(cases),
        "cases": cases, "skipped": []}))
    return corpus


def heard(**changes) -> dict:
    """A recogniser's table: what it writes for each recording, lowercase
    and unpunctuated as a recogniser might, with ``changes`` swapped in."""
    table = {case: text.lower().rstrip(".") for case, (text, _t, _a)
             in SAID.items()}
    table.update(changes)
    return {str(LENGTHS[case]): text for case, text in table.items()}


def fake_program(path: Path, table: dict, *, ready=True, die_after=None):
    """A speech program speaking the framed protocol, answering from
    ``table``. ``die_after`` requests later, it exits mid-session."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""#!{sys.executable}
import json, struct, sys
table = {json.dumps(table)!r}
table = json.loads(table)
out = sys.stdout.buffer
if not {ready!r}:
    sys.exit(3)
out.write(json.dumps({{"ready": True, "load_s": 0.0}}).encode() + b"\\n")
out.flush()
served = 0
while True:
    head = sys.stdin.buffer.read(8)
    if len(head) < 8:
        break
    count = struct.unpack("<Q", head)[0]
    sys.stdin.buffer.read(count * 4)
    served += 1
    if {die_after!r} is not None and served > {die_after!r}:
        sys.exit(4)
    out.write(json.dumps({{"ok": True, "text": table.get(str(count), "")}}).encode() + b"\\n")
    out.flush()
""")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def write_checkout(root: Path, *, runtime_source="let version = 1\n",
                   preferences=None, module=True) -> Path:
    """A checkout as far as a check reads one: the replay code, the speech
    program's sources, and the person's preferences."""
    root.mkdir(parents=True)
    if module:
        for name in ("voice_check.py", "parrot_core.py"):
            shutil.copy2(ROOT / name, root / name)
    for package in ("native/ParrotASRHelper", "native/ParrotSpeechRuntime"):
        sources = root / package / "Sources" / "main"
        sources.mkdir(parents=True)
        (root / package / "Package.swift").write_text("// package\n")
        (sources / "main.swift").write_text(runtime_source)
    if preferences is not None:
        (root / "preferences.json").write_text(json.dumps(preferences))
    return root


class Builds:
    """``run`` for a check: a swift build is faked by putting ``program``
    where SwiftPM would; everything else really runs."""

    def __init__(self, program_table=None, *, build_rc=0, **program):
        self.table = program_table
        self.build_rc = build_rc
        self.program = program
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        if "swift" in command:
            scratch = Path(command[command.index("--scratch-path") + 1])
            if self.build_rc == 0 and self.table is not None:
                package = command[command.index("--package-path") + 1]
                executable = ("parrot-speech-runtime" if
                              package.endswith("ParrotSpeechRuntime")
                              else "parrot-asr-helper")
                fake_program(scratch / "release" / executable, self.table,
                             **self.program)
            return subprocess.CompletedProcess(command, self.build_rc, b"", b"")
        if command[:2] == ["/bin/cp", "-c"]:
            shutil.copytree(command[3], command[4])
            return subprocess.CompletedProcess(command, 0, b"", b"")
        return subprocess.run(command, **kwargs)


class VoiceCheckFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        # Scratch folders the check makes go under the test's own folder.
        scratch = self.root / "tmp"
        scratch.mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(scratch))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scratch = scratch
        self.current = write_checkout(self.root / "current")
        fake_program(self.current / ".models/bin/parrot-asr-helper", heard())
        # The FluidAudio helper's build folder, which holds its dependency.
        (self.current / ".models/swift-build/checkouts").mkdir(parents=True)

    def check(self, candidate, run=None, **kwargs):
        options = {"wrap": [], "platform": "darwin"}
        options.update(kwargs)
        return voice_check.check_update(
            home=self.home, current=self.current, candidate=candidate,
            run=run or Builds(), **options)


class NoCorpusTests(VoiceCheckFixture):
    def test_without_recordings_nothing_runs_and_nothing_is_written(self):
        run = Builds()
        candidate = write_checkout(self.root / "candidate")
        outcome = self.check(candidate, run=run)
        self.assertEqual(outcome, {"status": "no-corpus"})
        self.assertEqual(run.commands, [])
        self.assertFalse((self.home / "whisper-face-evidence").exists())
        self.assertEqual(voice_check.corpus_size(self.home), 0)

    def test_a_folder_that_is_not_a_corpus_is_no_corpus(self):
        corpus = self.home / "whisper-face-evidence" / "accuracy"
        corpus.mkdir(parents=True)
        for content in ("", "[]", json.dumps({"kind": "something-else"}),
                        json.dumps({"kind": voice_check.CORPUS_KIND,
                                    "cases": [{"id": "x", "audio": "../x.wav",
                                               "text": "hi"}]})):
            with self.subTest(content=content):
                (corpus / "cases.json").write_text(content)
                self.assertIsNone(voice_check.load_corpus(corpus))


class ComparisonTests(VoiceCheckFixture):
    def setUp(self):
        super().setUp()
        write_corpus(self.home)

    def test_the_same_update_passes_and_its_receipt_holds_only_counts(self):
        candidate = write_checkout(self.root / "candidate")
        run = Builds()
        outcome = self.check(candidate, run=run)
        self.assertEqual(outcome["status"], "passed", outcome)
        # The same sources make the same program: nothing is built, and the
        # installed program answers for both versions.
        self.assertEqual(outcome["recogniser"], "same")
        self.assertFalse(any("swift" in command for command in run.commands))
        self.assertEqual(outcome["recordings"], 4)
        self.assertEqual(outcome["current"], outcome["candidate"])
        self.assertEqual(outcome["current"]["terms_total"], 4)
        self.assertEqual(outcome["current"]["terms_right"], 4)
        self.assertEqual(outcome["current"]["word_errors"], 0)
        self.assertEqual(outcome["worse"], [])
        receipt = Path(outcome["receipt"])
        self.assertTrue(receipt.is_relative_to(
            self.home / "whisper-face-evidence" / "runs"))
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(receipt.parent.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE(receipt.parent.parent.stat().st_mode), 0o700)
        written = json.loads(receipt.read_text())
        self.assertEqual(written["kind"], voice_check.RECEIPT_KIND)
        self.assertEqual(written["status"], "passed")
        self.assertEqual(sorted(written["per_recording"]), sorted(SAID))
        self.assert_no_words(receipt.read_text())
        self.assert_no_words(voice_check.summary_line(outcome))
        self.assertNotIn("per_recording", outcome)
        # Recordings are read in place: nothing of them is copied anywhere.
        self.assertEqual(list(self.scratch.iterdir()), [])

    def assert_no_words(self, text):
        folded = text.casefold()
        for said, terms, _avoid in SAID.values():
            for word in voice_check.words(said):
                if len(word) > 3:
                    self.assertNotIn(word, folded)
            for term in terms:
                self.assertNotIn(term.casefold(), folded)

    def test_a_candidate_that_loses_a_name_is_held(self):
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        run = Builds(heard(**{"acc-001": "send the deck to korea before lunch"}))
        outcome = self.check(candidate, run=run)
        self.assertEqual(outcome["status"], "held", outcome)
        self.assertEqual(outcome["recogniser"], "built")
        self.assertIn("terms_right", outcome["worse"])
        self.assertEqual(outcome["current"]["terms_right"], 4)
        self.assertEqual(outcome["candidate"]["terms_right"], 3)
        # One word changed: within the word allowance, so it is the name,
        # not the word count, that holds the update.
        self.assertNotIn("word_errors", outcome["worse"])

    def test_a_candidate_that_writes_a_name_nobody_said_is_held(self):
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        run = Builds(heard(**{"acc-004": "we should meet priya the review"}))
        outcome = self.check(candidate, run=run)
        self.assertEqual(outcome["status"], "held", outcome)
        self.assertEqual(outcome["worse"], ["false_insertions"])
        self.assertEqual(outcome["candidate"]["false_insertions"], 1)

    def test_word_mistakes_within_the_stated_allowance_pass(self):
        self.assertEqual(voice_check.allowance(30)["word_errors"], 2)
        self.assertEqual(voice_check.allowance(400)["word_errors"], 2)
        self.assertEqual(voice_check.allowance(1000)["word_errors"], 5)
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        two = Builds(heard(**{"acc-004": "we should meat after a review"}))
        self.assertEqual(self.check(candidate, run=two)["status"], "passed")
        three = Builds(heard(**{"acc-004": "we could meat after a review"}))
        outcome = self.check(candidate, run=three)
        self.assertEqual(outcome["status"], "held", outcome)
        self.assertEqual(outcome["worse"], ["word_errors"])
        self.assertEqual(outcome["candidate"]["word_errors"], 3)

    def test_a_better_candidate_passes(self):
        fake_program(self.current / ".models/bin/parrot-asr-helper",
                     heard(**{"acc-002": "oh cafe signed off on the budget"}))
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        outcome = self.check(candidate, run=Builds(heard()))
        self.assertEqual(outcome["status"], "passed", outcome)
        self.assertLess(outcome["candidate"]["word_errors"],
                        outcome["current"]["word_errors"])

    def test_a_candidate_that_stops_answering_is_held(self):
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        outcome = self.check(candidate, run=Builds(heard(), die_after=2))
        self.assertEqual(outcome["status"], "held", outcome)
        self.assertIn("unanswered", outcome["worse"])
        self.assertEqual(outcome["candidate"]["unanswered"], 2)

    def test_checks_that_cannot_run_say_why_and_do_not_hold(self):
        cases = {
            "candidate-did-not-start": lambda: (
                write_checkout(self.root / "a", runtime_source="2"),
                Builds(heard(), ready=False), {}),
            "not-built": lambda: (
                write_checkout(self.root / "b", runtime_source="2"),
                Builds(heard(), build_rc=1), {}),
            "candidate-too-old": lambda: (
                write_checkout(self.root / "c", module=False), Builds(), {}),
            "no-sandbox": lambda: (
                write_checkout(self.root / "d"), Builds(), {}),
            "not-mac": lambda: (
                write_checkout(self.root / "e"), Builds(), {"platform": "win32"}),
        }
        for reason, make in cases.items():
            with self.subTest(reason=reason):
                candidate, run, options = make()
                if reason == "no-sandbox":
                    with mock.patch.object(voice_check, "isolation",
                                           return_value=None):
                        outcome = voice_check.check_update(
                            home=self.home, current=self.current,
                            candidate=candidate, run=run, platform="darwin")
                else:
                    outcome = self.check(candidate, run=run, **options)
                self.assertEqual(outcome["status"], "not-checked")
                self.assertEqual(outcome["reason"], reason)
                self.assertIn(reason, voice_check.REASON_CODES)

    def test_a_missing_installed_program_is_not_checked(self):
        (self.current / ".models/bin/parrot-asr-helper").unlink()
        outcome = self.check(write_checkout(self.root / "candidate"))
        self.assertEqual(outcome["reason"], "no-recogniser")

    def test_the_persons_own_program_and_clean_up_are_used(self):
        # The new speech runtime chosen in preferences: that program is the
        # one tried, built from its own package.
        self.current = write_checkout(
            self.root / "current-own",
            preferences={"new_speech_runtime": True, "self_correction": True})
        fake_program(self.current / ".models/bin/parrot-speech-runtime",
                     heard())
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        run = Builds(heard())
        with mock.patch.dict(os.environ, {"PARROT_ASR_RUNTIME": ""}):
            outcome = self.check(candidate, run=run)
        self.assertEqual(outcome["program"], "own")
        build = next(command for command in run.commands if "swift" in command)
        self.assertTrue(build[build.index("--package-path") + 1].endswith(
            "native/ParrotSpeechRuntime"))
        replayed = next(command for command in run.commands
                        if str(candidate / "voice_check.py") in command)
        self.assertIn("--self-correction", replayed)
        self.assertNotIn("--keep-repeats", replayed)


    def test_without_the_new_runtime_the_helper_is_tried_as_in_dictation(self):
        (self.current / "preferences.json").write_text(
            json.dumps({"new_speech_runtime": True}))
        with mock.patch.dict(os.environ, {"PARROT_ASR_RUNTIME": ""}):
            outcome = self.check(write_checkout(self.root / "candidate"))
        self.assertEqual(outcome["status"], "passed", outcome)
        self.assertEqual(outcome["program"], "fluidaudio")


class IsolationTests(VoiceCheckFixture):
    def setUp(self):
        super().setUp()
        write_corpus(self.home)

    def test_everything_a_check_starts_runs_offline_at_background_priority(self):
        prefix = ["/usr/bin/sandbox-exec", "-p", "PROFILE"]
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        run = Builds(heard())
        started, ran = [], []

        def popen(command, **kwargs):
            started.append(command)
            return subprocess.Popen(command[len(prefix):], **kwargs)

        def wrapped(command, **kwargs):
            ran.append(command)
            if command[:len(prefix)] == prefix:
                return run(command[len(prefix):], **kwargs)
            return run(command, **kwargs)
        outcome = voice_check.check_update(
            home=self.home, current=self.current, candidate=candidate,
            run=wrapped, popen=popen, wrap=prefix, platform="darwin")
        self.assertEqual(outcome["status"], "passed", outcome)
        # This version's program, the build of the candidate's and the
        # candidate's own process (which starts its program inside the same
        # sandbox, inherited) all ran under the prefix. Only the local clone
        # of the build folder did not, and it opens no connection.
        self.assertTrue(started)
        self.assertTrue(all(command[:3] == prefix for command in started))
        unwrapped = [command for command in ran if command[:3] != prefix]
        self.assertEqual([command[:2] for command in unwrapped],
                         [["/bin/cp", "-c"]])
        build = next(command for command in ran if "swift" in command)
        self.assertIn("--disable-automatic-resolution", build)
        self.assertEqual(voice_check.isolation("darwin")[3:] if
                         voice_check.isolation("darwin") else [],
                         [voice_check.TASKPOLICY, "-b"]
                         if voice_check.isolation("darwin") and os.access(
                             voice_check.TASKPOLICY, os.X_OK) else [])

    def test_the_isolation_denies_the_network(self):
        self.assertIsNone(voice_check.isolation("win32"))
        self.assertIn("(deny network*)", voice_check.NO_NETWORK_PROFILE)
        if sys.platform == "darwin" and os.access(voice_check.SANDBOX_EXEC,
                                                  os.X_OK):
            prefix = voice_check.isolation()
            self.assertEqual(prefix[:3], [voice_check.SANDBOX_EXEC, "-p",
                                          voice_check.NO_NETWORK_PROFILE])
            # And it holds: a program inside cannot open a socket.
            probe = subprocess.run(
                [*prefix, sys.executable, "-c",
                 "import socket; socket.create_connection(('127.0.0.1', 9), 2)"],
                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(probe.returncode, 0)
            self.assertIn("Operation not permitted", probe.stderr)

    def test_a_built_program_has_its_own_name_and_leaves_nothing_behind(self):
        candidate = write_checkout(self.root / "candidate",
                                   runtime_source="let version = 2\n")
        cache = self.home / "Library/Caches/parrot-asr-helper-voice-check"
        seen = []
        original = voice_check.Recogniser.__init__

        def remember(recogniser, program, *args, **kwargs):
            seen.append(Path(program).name)
            cache.mkdir(parents=True, exist_ok=True)
            return original(recogniser, program, *args, **kwargs)
        run = Builds(heard())
        with mock.patch.object(voice_check.Recogniser, "__init__", remember):
            outcome = self.check(candidate, run=run)
        self.assertEqual(outcome["status"], "passed", outcome)
        replayed = next(command for command in run.commands
                        if str(candidate / "voice_check.py") in command)
        program = replayed[replayed.index("--program") + 1]
        self.assertTrue(program.endswith("parrot-asr-helper-voice-check"))
        # The FluidAudio helper's pinned dependency came from this version's
        # build folder, cloned, never downloaded.
        self.assertTrue(any(command[:2] == ["/bin/cp", "-c"]
                            for command in run.commands))
        self.assertFalse(cache.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])


class ScoringTests(unittest.TestCase):
    def test_words_and_mistakes(self):
        self.assertEqual(voice_check.words("Don’t stop, Priya!"),
                         ["don't", "stop", "priya"])
        self.assertEqual(voice_check.word_errors(["a", "b", "c"],
                                                 ["a", "x", "c", "d"]), 2)
        self.assertEqual(voice_check.word_errors([], ["a"]), 1)
        self.assertEqual(voice_check.occurrences("Priya", "priya, PRIYA"), 2)
        self.assertEqual(voice_check.occurrences("Pri", "Priya"), 0)

    def test_the_clean_up_replayed_is_the_one_dictation_uses(self):
        for raw in ("um send it to priya", "so so the plan is fine,",
                    "Tuesday, actually Wednesday"):
            for correction in (False, True):
                self.assertEqual(
                    voice_check.finish(raw, self_correction=correction,
                                       remove_repeats=True),
                    parrot_core.finish_cleanup(raw, "en", correction, True))
        source = (ROOT / "dictate.py").read_text()
        body = source[source.index("def quick_clean("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("return finish_cleanup(", body)

    def test_the_worse_rule_holds_each_measure_to_its_tolerance(self):
        base = voice_check.Score(reference_words=400, word_errors=40,
                                 terms_total=30, terms_right=20)
        cases = {
            (): voice_check.Score(reference_words=400, word_errors=42,
                                  terms_total=30, terms_right=20),
            ("terms_right",): voice_check.Score(
                reference_words=400, word_errors=40, terms_total=30,
                terms_right=19),
            ("word_errors",): voice_check.Score(
                reference_words=400, word_errors=43, terms_total=30,
                terms_right=21),
            ("false_insertions",): voice_check.Score(
                reference_words=400, word_errors=40, terms_total=30,
                terms_right=20, false_insertions=1),
        }
        for expected, candidate in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(tuple(voice_check.worse(base, candidate)),
                                 expected)


class ReplayProgramTests(VoiceCheckFixture):
    def test_the_candidate_half_prints_json_and_writes_no_file(self):
        corpus = write_corpus(self.home)
        before = sorted(p.name for p in corpus.iterdir())
        program = fake_program(self.root / "bin/helper", heard())
        completed = subprocess.run(
            [sys.executable, "-I", str(ROOT / "voice_check.py"), "replay",
             "--program", str(program), "--corpus", str(corpus),
             "--models", str(self.root / "models")],
            capture_output=True, text=True, timeout=120, cwd=self.root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        answer = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(answer["started"])
        self.assertEqual(answer["texts"]["acc-001"],
                         "Send the deck to priya before lunch.")
        self.assertEqual(sorted(p.name for p in corpus.iterdir()), before)


class CopyTests(unittest.TestCase):
    OUTCOME = {
        "status": "held", "recordings": 40,
        "current": {"word_errors": 41, "terms_right": 8, "terms_total": 30,
                    "false_insertions": 0, "unanswered": 0},
        "candidate": {"word_errors": 50, "terms_right": 6, "terms_total": 30,
                      "false_insertions": 0, "unanswered": 0},
        "worse": ["word_errors", "terms_right"],
    }

    def test_a_held_update_says_what_got_worse_in_counts(self):
        title, detail = whisper_face_gui.voice_check_held(
            self.OUTCOME, whisper_face_gui.voice_check_update_label(
                version="0.7.0"))
        self.assertEqual(title, "This update hears you less well")
        self.assertIn("version 0.7.0", detail)
        self.assertIn("40 recordings", detail)
        self.assertIn("Names and terms right: 8 of 30 now, 6 with the update.",
                      detail)
        self.assertIn("Word mistakes: 41 now, 50 with the update.", detail)
        # Names first, and only what got worse.
        self.assertLess(detail.index("Names and terms"),
                        detail.index("Word mistakes"))
        self.assertNotIn("didn’t say", detail)
        self.assertTrue(detail.endswith("or update anyway."))
        self.assertEqual(
            whisper_face_gui.localized_string("update.voice_check.held.wait"),
            "Wait")

    def test_every_reason_and_status_reads_plainly(self):
        for reason in voice_check.REASON_CODES:
            with self.subTest(reason=reason):
                note = whisper_face_gui.voice_check_note(
                    {"status": "not-checked", "reason": reason})
                self.assertTrue(note.startswith("It couldn’t be tried"))
                self.assertEqual(whisper_face_gui.internal_words_in(note), [])
        self.assertIn("(8 before)", whisper_face_gui.voice_check_note(
            {**self.OUTCOME, "status": "passed"}))
        self.assertEqual(whisper_face_gui.voice_check_note(
            {"status": "no-corpus"}), "")
        self.assertEqual(whisper_face_gui.voice_check_ahead(0), "")
        self.assertIn("your recording.", whisper_face_gui.voice_check_ahead(1))
        self.assertIn("your 40 recordings",
                      whisper_face_gui.voice_check_ahead(40))


class GitUpdaterTests(unittest.TestCase):
    """The folder install's updater exports the candidate from its own
    object store and tries it before anything moves."""

    def test_no_recordings_means_no_git_command_at_all(self):
        with tempfile.TemporaryDirectory() as home:
            calls = []
            outcome = self_update.voice_check_candidate(
                Path("/whisper-face/checkout"), "b" * 40,
                runner=lambda *a, **k: calls.append(a), home=home)
        self.assertEqual(outcome, {"status": "no-corpus"})
        self.assertEqual(calls, [])

    def test_the_candidate_is_the_revisions_own_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            write_corpus(home)
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as archive:
                for name, text in (("voice_check.py", "# candidate\n"),
                                   ("../escape.py", "x"),
                                   ("native/x.swift", "y")):
                    data = text.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            commands, seen = [], {}

            def runner(command, **kwargs):
                commands.append(command)
                if "archive" in command:
                    return subprocess.CompletedProcess(
                        command, 0, payload.getvalue(), b"")
                return subprocess.CompletedProcess(command, 0, "a" * 40, "")

            def check(**kwargs):
                candidate = kwargs["candidate"]
                seen["files"] = sorted(
                    str(p.relative_to(candidate))
                    for p in candidate.rglob("*") if p.is_file())
                seen["home"] = kwargs["home"]
                return {"status": "held"}
            outcome = self_update.voice_check_candidate(
                Path("/whisper-face/checkout"), "b" * 40, runner=runner,
                home=home, check=check)
        self.assertEqual(outcome, {"status": "held"})
        self.assertEqual(seen["files"], ["native/x.swift", "voice_check.py"])
        archive_command = next(c for c in commands if "archive" in c)
        self.assertEqual(archive_command[-1], "b" * 40)
        # No fetch, no network: the revision was fetched when it was offered.
        self.assertFalse(any("fetch" in c for c in commands))


if __name__ == "__main__":
    unittest.main()
