# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""The outside probe's rules, each broken on purpose, and its accuracy.

outside_probe.py times a dictation app from outside: it presses and releases
the app's capture key with synthetic key events and timestamps the text
arriving in its own window. These tests hold it to its posture:

* it runs against an app only with the go-ahead on the command line;
* it posts only the capture key, only while its own window is in front,
  always releases it, and types nothing anywhere;
* it imports no network module and keeps its result beside the benchmark's;
* its result is consumed by the benchmark only when it timed the same app
  revision, machine, corpus and audio, and its numbers follow from its own
  per-take times.

Nothing here posts a key event or plays a sound. The AppKit tests run the
probe's real window, hidden and never in front, in the locked runtime's
Python (which has PyObjC and pynput); a fake dictation app
(tests/fake_dictation_target.py) types into it after a known delay, and the
probe's timestamps are compared with the moments the fake sent its text.
"""

import ast
import contextlib
import copy
import functools
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import benchmark_public as bench  # noqa: E402
import outside_probe as probe  # noqa: E402
import public_scorecard  # noqa: E402
from public_scorecard import PublicationError  # noqa: E402
from test_benchmark_public import REVISION, valid_result  # noqa: E402

PROBE_SOURCE = ROOT / "outside_probe.py"
FAKE_TARGET = TESTS / "fake_dictation_target.py"
TIMING = {"lead_ms": 300, "tail_ms": 600, "settle_ms": 1500,
          "deadline_ms": 30000, "gap_ms": 1000}
NETWORK_MODULES = {
    "socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx",
    "aiohttp", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
    "socketserver", "xmlrpc", "asyncio", "websocket", "websockets",
    "multiprocessing",
}


class FakeKey:
    """Records presses and releases; posts nothing."""

    def __init__(self, target=None):
        self.target = target
        self.presses: list[int] = []
        self.releases: list[int] = []

    def _tell(self, word):
        if self.target is not None:
            self.target.stdin.write(word + "\n")
            self.target.stdin.flush()

    def press(self):
        self.presses.append(probe.now_ns())
        self._tell("down")
        return self.presses[-1]

    def release(self):
        self.releases.append(probe.now_ns())
        self._tell("up")
        return self.releases[-1]


class FakePlayer:
    """Tells the fake app what it hears; plays no sound."""

    def __init__(self, target=None, fail=False):
        self.target, self.fail, self.played = target, fail, []

    def play(self, take_id, path):
        if self.fail:
            raise RuntimeError("the speakers went away")
        self.played.append(take_id)
        if self.target is not None:
            self.target.stdin.write(f"hear {take_id}\n")
            self.target.stdin.flush()


class FakeWindow:
    """A probe window whose front-ness the test decides, in order."""

    def __init__(self, in_front):
        self.in_front = list(in_front)
        self.asked = 0
        self.seq = 0

    def clear(self):
        self.seq += 1
        return self.seq

    def focused(self):
        self.asked += 1
        return self.in_front.pop(0) if self.in_front else True

    def text_events(self, seq, release_ns, *, settle_ms, deadline_ms):
        return [{"t": release_ns + 120_000_000, "text": "hello"},
                {"t": release_ns + 180_000_000, "text": "hello there"}]


def fast_run(window, key, player, ids=("names-01",), repeats=1):
    takes = [{"id": take_id, "seconds": 3.0, "audio_sha256": "e" * 64}
             for take_id in ids]
    return probe.run_probe(
        takes=takes, audio={take_id: Path("x.wav") for take_id in ids},
        window=window, key=key, player=player, repeats=repeats,
        order=lambda _repeat: list(ids), lead_ms=0, tail_ms=0, settle_ms=0,
        deadline_ms=0, gap_ms=0)


def run_main(arguments):
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        code = probe.main(arguments)
    return code, errors.getvalue()


def make_probe_result(result, ids, *, repeats=2, release_to_first=150.0,
                      release_to_final=420.0):
    takes = {take["id"]: take for take in result["takes"]}
    rows, clock = [], 1_000_000_000
    for repeat in range(1, repeats + 1):
        for take_id in ids:
            clock += 5_000_000_000
            changes = [
                {"t": clock + int(release_to_first * 1e6), "text": "a"},
                {"t": clock + int(release_to_final * 1e6), "text": "a b"}]
            rows.append(probe.take_row(takes[take_id], repeat, clock,
                                       changes, 0))
    outcome = {"takes": rows, "completed": True, "stopped": None}
    return probe.build_probe_result(
        outcome=outcome, app="whisper-face", app_revision=REVISION,
        environment=result["environment"], corpus=bench.load_corpus(),
        key="alt_r", repeats=repeats, timing=TIMING,
        command=("uv run outside_probe.py run --app whisper-face --go-ahead "
                 "whisper-face --benchmark <out> --app-revision <revision>"),
        started_utc="2026-11-17T12:00:00Z")


# -- the go-ahead ----------------------------------------------------------------


class GoAheadTests(unittest.TestCase):
    def setUp(self):
        # Anything that would start a window, a key, a sound or read audio
        # fails the test if it is reached.
        for name in ("prepare_takes", "QuartzKeyDriver", "SpeakerPlayer"):
            patcher = mock.patch.object(
                probe, name, side_effect=AssertionError(f"{name} started"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            probe.ProbeWindow, "spawn",
            side_effect=AssertionError("window started"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out)

    def arguments(self, *extra):
        return ["run", "--app", "whisper-face", "--benchmark", self.out,
                "--app-revision", REVISION, *extra]

    def test_without_the_go_ahead_nothing_starts(self):
        code, errors = run_main(self.arguments())
        self.assertEqual(code, 2)
        self.assertIn("--go-ahead whisper-face", errors)

    def test_a_go_ahead_for_another_app_is_not_one(self):
        code, errors = run_main(self.arguments("--go-ahead", "other-app"))
        self.assertEqual(code, 2)
        self.assertIn("refusing to press keys", errors)

    def test_whisper_face_must_name_its_exact_revision(self):
        code, errors = run_main([
            "run", "--app", "whisper-face", "--go-ahead", "whisper-face",
            "--benchmark", self.out, "--app-revision", "main"])
        self.assertEqual(code, 2)
        self.assertIn("40-character", errors)

    def test_the_result_never_goes_into_the_checkout(self):
        code, errors = run_main([
            "run", "--app", "whisper-face", "--go-ahead", "whisper-face",
            "--benchmark", str(ROOT / "benchmarks"),
            "--app-revision", REVISION])
        self.assertEqual(code, 2)
        self.assertIn("outside the checkout", errors)


# -- only the capture key, only into its own window ---------------------------------


class OwnWindowTests(unittest.TestCase):
    def test_the_keys_are_exactly_the_ones_the_app_can_be_bound_to(self):
        source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        block = source[source.index("HOTKEY_CHOICES = {"):]
        block = block[:block.index("\n}\n")]
        names = set(re.findall(r'^\s+"([a-z_]+)": \(', block, re.M))
        match = re.search(r'f"f\{index\}".*range\((\d+), (\d+)\)', block)
        names |= {f"f{index}" for index in range(
            int(match.group(1)), int(match.group(2)))}
        self.assertEqual(set(probe.CAPTURE_KEYS), names)
        self.assertIn(probe.DEFAULT_KEY, names)

    def test_a_key_that_types_a_character_is_refused(self):
        for name in ("a", "v", "return", "space"):
            with self.subTest(key=name), self.assertRaises(probe.ProbeError):
                probe.QuartzKeyDriver(name)

    def test_the_probe_can_post_only_the_capture_key(self):
        tree = ast.parse(PROBE_SOURCE.read_text(encoding="utf-8"))
        names = [node.attr if isinstance(node, ast.Attribute) else node.id
                 for node in ast.walk(tree)
                 if isinstance(node, (ast.Attribute, ast.Name))]
        for forbidden in ("CGEventKeyboardSetUnicodeString", "CGEventPostToPid",
                          "CGEventPostToPSN", "NSPasteboard",
                          "generalPasteboard", "AXUIElementSetAttributeValue",
                          "AXUIElementCreateApplication"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)
        # One place posts, and it posts the two events built for the key.
        self.assertEqual(names.count("CGEventPost"), 1)
        self.assertEqual(names.count("CGEventCreateKeyboardEvent"), 2)
        poster = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_post")
        self.assertIn("CGEventPost", ast.dump(poster))
        self.assertNotIn("pbcopy", PROBE_SOURCE.read_text(encoding="utf-8"))

    def test_nothing_is_pressed_unless_its_window_is_in_front(self):
        key = FakeKey()
        outcome = fast_run(FakeWindow([False]), key, FakePlayer())
        self.assertEqual(key.presses, [])
        self.assertFalse(outcome["completed"])
        self.assertIn("nothing was pressed", outcome["stopped"])

    def test_losing_the_front_mid_take_releases_and_stops(self):
        key, window = FakeKey(), FakeWindow([True, False])
        outcome = fast_run(window, key, FakePlayer(),
                           ids=("names-01", "names-02"))
        self.assertEqual((len(key.presses), len(key.releases)), (1, 1))
        self.assertFalse(outcome["completed"])
        self.assertIn("lost the front", outcome["stopped"])
        self.assertEqual(outcome["takes"], [])

    def test_the_key_is_released_when_a_take_fails(self):
        key = FakeKey()
        with self.assertRaises(RuntimeError):
            fast_run(FakeWindow([True]), key, FakePlayer(fail=True))
        self.assertEqual((len(key.presses), len(key.releases)), (1, 1))

    def test_a_visible_window_has_no_way_in_for_text(self):
        # The echo pipe a fake app types through exists only for a hidden
        # test window; it is refused before AppKit is even imported.
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            probe.window_main(["--echo-fifo", "/nonexistent"])
        self.assertIn("hidden test window only", errors.getvalue())

    def test_a_take_is_timed_from_release_to_first_and_final_text(self):
        take = {"id": "names-01", "seconds": 3.0, "audio_sha256": "e" * 64}
        row = probe.take_row(take, 1, 2_000_000_000, [
            {"t": 2_150_000_000, "text": "Siobhan"},
            {"t": 2_400_000_000, "text": "Siobhan said"}], 1_000_000_000)
        self.assertEqual((row["release_ms"], row["first_text_ms"],
                          row["final_text_ms"]), (1000.0, 1150.0, 1400.0))
        self.assertEqual((row["release_to_first_text_ms"],
                          row["release_to_final_text_ms"]), (150.0, 400.0))
        self.assertEqual((row["status"], row["text"], row["changes"]),
                         ("text", "Siobhan said", 2))
        empty = probe.take_row(take, 1, 2_000_000_000, [], 1_000_000_000)
        self.assertEqual(empty["status"], "no-text")
        self.assertIsNone(empty["release_to_final_text_ms"])


# -- nothing sent anywhere -----------------------------------------------------------


class NothingSentTests(unittest.TestCase):
    def test_no_network_module_is_imported(self):
        for path in (PROBE_SOURCE, FAKE_TARGET):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {alias.name.split(".")[0]
                                 for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(path=path.name):
                self.assertTrue(imported, "the scan read nothing")
                self.assertEqual(imported & NETWORK_MODULES, set())

    def test_audio_goes_only_to_the_speakers(self):
        tree = ast.parse(PROBE_SOURCE.read_text(encoding="utf-8"))
        programs = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.startswith("/usr/bin/")}
        self.assertEqual(programs, {"/usr/bin/afplay"})


# -- the benchmark consumes the result --------------------------------------------------


class ResultTests(unittest.TestCase):
    IDS = ("names-01", "terms-01", "long-01")

    def setUp(self):
        self.result = valid_result()
        self.probe = make_probe_result(self.result, self.IDS)

    def attached(self, probe_result=None):
        return bench.attach_probe(self.result, probe_result or self.probe)

    def refused(self, attached, message):
        with self.assertRaisesRegex(PublicationError, message):
            public_scorecard.validate_benchmark_result(attached)

    def test_a_probe_result_is_attached_and_rendered(self):
        attached = self.attached()
        public_scorecard.validate_benchmark_result(attached)
        entry = attached["outside_probe"]["release_to_text_on_screen_ms"]
        self.assertEqual(entry["status"], bench.OUTSIDE_PROBE)
        self.assertEqual(entry["value"]["release_to_final_text"]["all"]["p50"],
                         420.0)
        self.assertEqual(entry["value"]["takes"], 3)
        for name, other in attached["outside_probe"].items():
            if name != "release_to_text_on_screen_ms":
                self.assertEqual(other["status"], bench.NEEDS_OUTSIDE_PROBE)
        page = public_scorecard.render_benchmark_markdown(attached)
        self.assertIn("Outside probe: key release to final text in a real "
                      "window | all | 3 | 420 |", page)
        self.assertNotIn("| `release_to_text_on_screen_ms` |", page)
        self.assertIn("| `bytes_sent_per_dictation` |", page)

    def test_an_edited_number_is_refused(self):
        attached = self.attached()
        entry = attached["outside_probe"]["release_to_text_on_screen_ms"]
        entry["value"]["release_to_final_text"]["all"]["p50"] = 90.0
        self.refused(attached, "does not follow")
        edited = copy.deepcopy(self.probe)
        edited["summary"]["release_to_final_text"]["all"]["p50"] = 90.0
        with self.assertRaisesRegex(bench.BenchmarkError, "does not follow"):
            self.attached(edited)
        attached = self.attached()
        row = attached["outside_probe"]["release_to_text_on_screen_ms"][
            "probe"]["takes"][0]
        row["release_to_final_text_ms"] = 10.0
        self.refused(attached, "do not follow from its times")

    def test_a_probe_of_another_revision_machine_or_audio_is_refused(self):
        for path, value, message in (
                (("target", "revision"), "f" * 40, "different app"),
                (("environment", "os_version"), "15.6 (24G84)",
                 "different machine"),
                (("corpus", "sha256"), "0" * 64, "different corpus"),
                (("run", "completed"), False, "stopped"),
                (("run", "command"),
                 "uv run outside_probe.py run --benchmark /Users/x/out",
                 "no local folder")):
            with self.subTest(field=path):
                edited = copy.deepcopy(self.probe)
                edited[path[0]][path[1]] = value
                with self.assertRaisesRegex(bench.BenchmarkError, message):
                    self.attached(edited)
        edited = copy.deepcopy(self.probe)
        edited["takes"][0]["audio_sha256"] = "0" * 64
        edited["summary"] = bench.probe_summary(edited["takes"])
        with self.assertRaisesRegex(bench.BenchmarkError, "different audio"):
            self.attached(edited)

    def test_only_release_to_text_may_carry_the_probe_class(self):
        attached = self.attached()
        attached["outside_probe"]["bytes_sent_per_dictation"] = dict(
            attached["outside_probe"]["release_to_text_on_screen_ms"])
        self.refused(attached, "bytes_sent_per_dictation can only come")
        result = copy.deepcopy(self.result)
        result["outside_probe"]["release_to_text_on_screen_ms"]["value"] = 120
        self.refused(result, "can only come from the outside probe")

    def test_the_command_line_attaches_and_renders(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "result.json").write_text(json.dumps(self.result))
            (out / bench.PROBE_RESULT_FILE).write_text(json.dumps(self.probe))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bench.main(
                    ["attach-probe", "--out", str(out)]), 0)
            self.assertIn("Outside probe: key release to first text",
                          (out / "scorecard.md").read_text())
            broken = copy.deepcopy(self.probe)
            broken["target"]["revision"] = "f" * 40
            (out / "result.json").write_text(json.dumps(self.result))
            (out / bench.PROBE_RESULT_FILE).write_text(json.dumps(broken))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bench.main(
                    ["attach-probe", "--out", str(out)]), 2)

    def test_the_method_document_describes_the_probe(self):
        text = (ROOT / "docs" / "benchmarks" / "public-benchmark.md"
                ).read_text(encoding="utf-8")
        self.assertIn("uv run outside_probe.py run", text)
        self.assertIn("uv run benchmark_public.py attach-probe", text)
        self.assertIn("--go-ahead", text)


# -- the real window, hidden, with a fake app ---------------------------------------------


@functools.lru_cache(maxsize=1)
def locked_python():
    """The locked runtime's Python: it has PyObjC and pynput."""
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        raise AssertionError("uv is required to reach the locked runtime")
    dictate = str(ROOT / "dictate.py")
    environment = {key: value for key, value in os.environ.items()
                   if key != "VIRTUAL_ENV"}
    synced = subprocess.run(
        [uv, "sync", "--script", dictate, "--locked", "--offline", "--quiet"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        timeout=900)
    if synced.returncode != 0:
        raise AssertionError(
            "the locked runtime is not in the uv cache; run `uv sync "
            "--script dictate.py --locked` once, then retry\n" + synced.stderr)
    return subprocess.run(
        [uv, "python", "find", "--script", dictate], cwd=ROOT,
        env=environment, check=True, capture_output=True, text=True,
        timeout=60).stdout.strip()


KEY_EVENT_CHECK = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import Quartz
def refuse(*_args):
    raise AssertionError("an event was posted")
Quartz.CGEventPost = refuse
import outside_probe
from pynput.keyboard import Key
from pynput.keyboard._darwin import Listener
rows = {}
for name in outside_probe.CAPTURE_KEYS:
    down, up = outside_probe.capture_key_events(name, Quartz)
    key = getattr(Key, name)
    mask = Listener._MODIFIER_FLAGS.get(key, 0)
    rows[name] = {
        "vk": key.value.vk,
        "codes": [Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode) for event in (down, up)],
        "types": [Quartz.CGEventGetType(event) for event in (down, up)],
        "pynput_press": [bool(Quartz.CGEventGetFlags(event) & mask)
                         for event in (down, up)],
        "modifier": bool(mask),
    }
print(json.dumps({"rows": rows, "types": {
    "flags": Quartz.kCGEventFlagsChanged, "down": Quartz.kCGEventKeyDown,
    "up": Quartz.kCGEventKeyUp}}))
'''


class InFrontForTest(probe.ProbeWindow):
    """The hidden test window cannot come to the front without taking the
    owner's screen, so this test, and only this one, says it is there."""

    def focused(self):
        return True


@unittest.skipUnless(sys.platform == "darwin", "the probe window is AppKit")
class AppKitWindowTests(unittest.TestCase):
    def spawn(self, window_class, **options):
        window = window_class.spawn(hidden=True, python=locked_python(),
                                    **options)
        self.addCleanup(window.close)
        return window

    def test_key_events_are_what_the_app_listens_for(self):
        # Built, never posted: the app's listener is pynput's session tap,
        # which reads a modifier's press or release from its family flag.
        completed = subprocess.run(
            [locked_python(), "-c", KEY_EVENT_CHECK, str(ROOT)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        types = report["types"]
        self.assertEqual(len(report["rows"]), len(probe.CAPTURE_KEYS))
        for name, row in report["rows"].items():
            with self.subTest(key=name):
                self.assertEqual(row["codes"], [row["vk"], row["vk"]])
                if row["modifier"]:
                    self.assertEqual(row["types"], [types["flags"]] * 2)
                    self.assertEqual(row["pynput_press"], [True, False])
                else:
                    self.assertEqual(row["types"], [types["down"],
                                                    types["up"]])

    def test_the_hidden_window_is_never_in_front_so_nothing_is_pressed(self):
        window = self.spawn(probe.ProbeWindow)
        state = window.focus()
        self.assertEqual(state["pid"], window.pid)
        self.assertFalse(window.focused())
        key = FakeKey()
        outcome = fast_run(window, key, FakePlayer())
        self.assertEqual(key.presses, [])
        self.assertFalse(outcome["completed"])
        # A paste (Command-V) reaches the field through its Edit menu.
        self.assertEqual(window.menu().get("v"), "paste:")

    def test_a_fake_app_is_timed_against_the_moments_it_typed(self):
        corpus = bench.load_corpus()
        texts = {take["id"]: take["text"] for take in corpus["takes"]}
        ids = ("names-01", "numbers-01", "everyday-01")
        first_delay, final_delay = 150, 450
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        (out / "audio").mkdir()
        result = valid_result()
        for take in result["takes"]:
            if take["id"] in ids:
                path = out / "audio" / f"{take['id']}.wav"
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(bytes(range(256)) * 32)
                take["audio_sha256"] = probe.pcm_sha256(path)
        (out / "result.json").write_text(json.dumps(result))
        corpus_, takes, audio = probe.prepare_takes(out, list(ids))
        self.assertEqual([take["id"] for take in takes], list(ids))
        (out / "texts.json").write_text(json.dumps(texts))
        fifo = out / "echo"
        os.mkfifo(fifo)
        window = self.spawn(InFrontForTest, echo_fifo=fifo)
        fake = subprocess.Popen(
            [sys.executable, str(FAKE_TARGET), "--fifo", str(fifo),
             "--texts", str(out / "texts.json"),
             "--first-delay-ms", str(first_delay),
             "--final-delay-ms", str(final_delay)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

        def stop_fake():
            if fake.poll() is None:
                fake.kill()
            fake.wait()
            fake.stdout.close()
        self.addCleanup(stop_fake)
        self.assertEqual(json.loads(fake.stdout.readline())["event"], "ready")

        key = FakeKey(fake)
        outcome = probe.run_probe(
            takes=takes, audio=audio, window=window, key=key,
            player=FakePlayer(fake), repeats=2,
            order=lambda repeat: bench.run_order(list(ids), 7, repeat),
            lead_ms=20, tail_ms=20, settle_ms=900, deadline_ms=10000,
            gap_ms=50)
        fake.stdin.close()
        log = [json.loads(line) for line in fake.stdout]
        self.assertTrue(outcome["completed"], outcome["stopped"])
        rows = outcome["takes"]
        self.assertEqual(len(rows), 6)
        ups = [entry["t"] for entry in log if entry["event"] == "up"]
        sent = [entry for entry in log if entry["event"] == "sent"]
        self.assertEqual((len(ups), len(sent)), (6, 12))

        first_errors, final_errors = [], []
        for index, row in enumerate(rows):
            with self.subTest(take=row["id"], repeat=row["repeat"]):
                part_one, part_two = sent[2 * index], sent[2 * index + 1]
                self.assertEqual(part_one["take"], row["id"])
                self.assertEqual(row["status"], "text")
                self.assertEqual(row["text"], texts[row["id"]])
                release = key.releases[index]
                first = release + row["release_to_first_text_ms"] * 1e6
                final = release + row["release_to_final_text_ms"] * 1e6
                # One clock across processes: the release is stamped before
                # the fake hears it, and no text is seen before it was sent.
                self.assertLessEqual(release, ups[index])
                self.assertGreaterEqual(first, part_one["t"])
                self.assertGreaterEqual(final, part_two["t"])
                # The first text is told apart from the final text.
                self.assertLess(first, part_two["t"])
                first_errors.append((first - part_one["t"]) / 1e6)
                final_errors.append((final - part_two["t"]) / 1e6)

        # The benchmark consumes what the probe wrote.
        written = probe.build_probe_result(
            outcome=outcome, app="whisper-face", app_revision=REVISION,
            environment=result["environment"], corpus=corpus_, key="alt_r",
            repeats=2, timing=TIMING,
            command="uv run outside_probe.py run --app whisper-face "
                    "--go-ahead whisper-face --benchmark <out> "
                    "--app-revision <revision>",
            started_utc="2026-11-17T12:00:00Z")
        (out / bench.PROBE_RESULT_FILE).write_text(json.dumps(written))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bench.main(["attach-probe", "--out", str(out)]),
                             0)
        attached = json.loads((out / "result.json").read_text())
        public_scorecard.validate_benchmark_result(attached)
        print(f"\nprobe error against the fake app, ms: first text p50 "
              f"{statistics.median(first_errors):.2f} max "
              f"{max(first_errors):.2f}; final text p50 "
              f"{statistics.median(final_errors):.2f} max "
              f"{max(final_errors):.2f}; release to final p50 "
              f"{written['summary']['release_to_final_text']['all']['p50']} "
              f"(fake delay {final_delay})", file=sys.stderr)
        self.assertTrue(all(math.isfinite(value) for value in first_errors))


if __name__ == "__main__":
    unittest.main()
