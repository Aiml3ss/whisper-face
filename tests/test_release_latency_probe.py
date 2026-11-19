# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""The outside latency probe, run against fakes only.

scripts/release_latency_probe.py posts real key events and plays sound, so
nothing here does either: the keyboard, the player and the text window are
fakes on an injected clock, and every number asserted is one the fakes were
told to produce, never seconds measured on this machine.
"""

import ast
import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_latency_probe as probe  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


class FakeApp:
    """A dictation app: text appears ``latency`` after the key-up, or, with
    ``early``, while the key is still held."""

    def __init__(self, clock, latency=0.137, early=False):
        self.clock = clock
        self.latency = latency
        self.early = early
        self.events = []
        self.changes = []

    # the keyboard
    def down(self):
        self.events.append(("down", self.clock()))
        if self.early:
            self.changes.append(self.clock() + 0.01)

    def up(self):
        self.events.append(("up", self.clock()))
        if self.latency is not None:
            self.changes.append(self.clock() + self.latency)

    # the probe's window
    def reset(self):
        self.changes.clear()

    def first_change_after(self, moment):
        return next((at for at in self.changes
                     if at > moment and at <= self.clock()), None)


class Recording:
    def __init__(self, clock, seconds=2.5, fail=False):
        self.clock = clock
        self.seconds = seconds
        self.fail = fail
        self.plays = 0

    def play(self):
        self.plays += 1
        recording = self

        class Playing:
            def wait(self):
                if recording.fail:
                    raise OSError("afplay failed")
                recording.clock.sleep(recording.seconds)

        return Playing()


class ProbeTrialTests(unittest.TestCase):
    def trial(self, app, recording, clock, **options):
        return probe.run_trial(app, recording, app, clock=clock,
                               sleep=clock.sleep, **options)

    def test_release_to_text_is_the_apps_delay_after_the_key_up(self):
        clock = Clock()
        app = FakeApp(clock, latency=0.137)
        trial = self.trial(app, Recording(clock, 2.5), clock,
                           lead_in=0.4, hold_after=0.2, poll=0.001)
        self.assertFalse(trial.early or trial.timed_out)
        # Polled every millisecond: never more than one poll late.
        self.assertGreaterEqual(trial.release_to_text_ms, 137.0)
        self.assertLess(trial.release_to_text_ms, 138.0 + 1e-6)
        self.assertEqual([event for event, _at in app.events], ["down", "up"])
        self.assertAlmostEqual(trial.hold_ms, 3100.0, places=3)

    def test_no_text_is_a_timed_out_trial_not_a_number(self):
        clock = Clock()
        app = FakeApp(clock, latency=None)
        trial = self.trial(app, Recording(clock), clock, timeout=15.0)
        self.assertTrue(trial.timed_out)
        self.assertIsNone(trial.release_to_text_ms)
        released = app.events[-1][1]
        self.assertGreaterEqual(clock(), released + 15.0)

    def test_text_while_the_key_is_held_is_reported_not_timed(self):
        clock = Clock()
        app = FakeApp(clock, early=True)
        trial = self.trial(app, Recording(clock), clock)
        self.assertTrue(trial.early)
        self.assertIsNone(trial.release_to_text_ms)

    def test_the_key_is_released_even_when_playback_fails(self):
        clock = Clock()
        app = FakeApp(clock)
        with self.assertRaises(OSError):
            self.trial(app, Recording(clock, fail=True), clock)
        self.assertEqual([event for event, _at in app.events], ["down", "up"])

    def test_the_window_is_emptied_before_each_trial(self):
        clock = Clock()
        app = FakeApp(clock, latency=0.1)
        recording = Recording(clock, 1.0)
        trials = probe.run_trials(app, recording, app, 3, clock=clock,
                                  sleep=clock.sleep, settle=2.0)
        self.assertEqual(recording.plays, 3)
        self.assertEqual(len(trials), 3)
        # A previous trial's text never counts as the next one's.
        self.assertTrue(all(abs(trial.release_to_text_ms - 100.0) <= 2.0
                            for trial in trials))


class ProbeSummaryTests(unittest.TestCase):
    def test_summary_drops_the_warm_up_and_holds_timings_only(self):
        trials = [probe.Trial(900.0, 3000.0, False, False)] + [
            probe.Trial(float(ms), 3000.0, False, False)
            for ms in (100, 120, 140, 160, 180)] + [
            probe.Trial(None, 3000.0, False, True),
            probe.Trial(None, 3000.0, True, False)]
        summary = probe.summarize(trials, warmup=1)
        self.assertEqual(
            (summary["trials"], summary["warmup_dropped"], summary["timed"],
             summary["timed_out"], summary["early"]), (7, 1, 5, 1, 1))
        self.assertEqual(summary["release_to_text_ms"],
                         {"p50": 140.0, "p95": 176.0, "max": 180.0})
        self.assertEqual(summary["privacy"], "timings-only")

    def test_no_timed_trial_gives_no_percentiles(self):
        summary = probe.summarize([probe.Trial(None, 1.0, False, True)])
        self.assertIsNone(summary["release_to_text_ms"])


class ProbeCommandTests(unittest.TestCase):
    def test_without_confirmation_it_only_prints_the_plan(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = probe.main(["--recording", "/nonexistent/take.wav"])
        self.assertEqual(status, 0)
        self.assertIn("Nothing was posted or played", output.getvalue())
        # Nothing that can post a key event or open a window was loaded.
        for module in ("Quartz", "AppKit"):
            self.assertNotIn(module, sys.modules)

    def test_bad_counts_are_refused(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = probe.main(["--recording", "x.wav", "--trials", "0",
                                 "--confirm-live"])
        self.assertEqual(status, 2)

    def test_a_missing_recording_is_refused_before_any_event(self):
        if sys.platform != "darwin":
            self.skipTest("the live path is Mac only")
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = probe.main(["--recording", "/nonexistent/take.wav",
                                 "--confirm-live"])
        self.assertEqual(status, 2)
        self.assertNotIn("Quartz", sys.modules)


class ProbeMatchesTheAppTests(unittest.TestCase):
    """What the probe relies on in dictate.py, checked so a change there
    shows up here instead of as a probe that silently stops working."""

    SOURCE = (ROOT / "dictate.py").read_text(encoding="utf-8")
    TREE = ast.parse(SOURCE)

    def test_the_probe_offers_exactly_the_apps_bindable_keys(self):
        node = next(
            node for node in self.TREE.body
            if isinstance(node, ast.Assign)
            and any(getattr(target, "id", None) == "HOTKEY_CHOICES"
                    for target in node.targets))
        block = ast.get_source_segment(self.SOURCE, node)
        named = set(re.findall(r'"([a-z]+_[lr])":', block))
        self.assertIn("range(1, 21)", block)
        self.assertEqual(named, set(probe.MODIFIER_KEYS))
        self.assertEqual(set(probe.FUNCTION_KEYS),
                         {f"f{index}" for index in range(1, 21)})

    def test_posted_events_reach_the_hotkey_callbacks_unfiltered(self):
        # pynput passes (key, injected) to a callback that takes two
        # arguments and drops the flag for one that takes one.
        callbacks = {
            node.name: node for node in ast.walk(self.TREE)
            if isinstance(node, ast.FunctionDef)
            and node.name in {"on_press", "on_release"}}
        self.assertEqual(set(callbacks), {"on_press", "on_release"})
        for name, node in callbacks.items():
            with self.subTest(callback=name):
                self.assertEqual([arg.arg for arg in node.args.args], ["key"])
                self.assertIsNone(node.args.vararg)
                self.assertNotIn("injected", ast.unparse(node))

    def test_the_hold_supervisor_trusts_up_only_after_down(self):
        node = next(
            node for node in self.TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "supervise_hotkey_latch")
        body = ast.unparse(node)
        self.assertIn("physical is False and latch['confirmed']", body)
        self.assertIn("latch['confirmed'] = True", body)


if __name__ == "__main__":
    unittest.main()
