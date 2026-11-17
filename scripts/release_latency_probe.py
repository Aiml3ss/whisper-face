# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyobjc-framework-Cocoa; sys_platform == 'darwin'",
#   "pyobjc-framework-Quartz; sys_platform == 'darwin'",
# ]
# ///
"""Time key release to text on screen, from outside the app. Mac only.

    uv run scripts/release_latency_probe.py --recording take.wav
    uv run scripts/release_latency_probe.py --recording take.wav \\
        --trials 20 --key alt_r --confirm-live

Whisper Face's own release-path trace (``performance_lab.py release-path``)
stops its clock when the paste keystroke is posted. This probe measures what
a person sees instead: it owns a plain text window, presses and holds the
capture key, plays a recording, releases the key, and timestamps the moment
text first appears in its window. Its one number per trial, release to text,
is comparable across dictation apps, which is what the public benchmark
needs.

It is an automated tool and is kept apart from ``benchmark_latency_rig.py``,
which only observes. Without ``--confirm-live`` it prints what it would do and
exits: no key event is posted and nothing is played. Run it only on a Mac
where you mean the running dictation app to receive these dictations, in a
quiet room, with the speakers loud enough for the microphone (or the
recording routed to the input through a loopback device). The terminal needs
Accessibility permission, which macOS requires before one program may post
key events to another.

Whether posted key events drive Whisper Face's hotkey (checked by reading
the code, not by running it):

- The app's listener is pynput's event tap at the session level, which sees
  events posted at the HID level by another process. pynput marks them as
  injected, but the app's ``on_press(key)``/``on_release(key)`` take one
  argument, so pynput drops the mark and treats them like a hand on the key.
  ``tests/test_release_latency_probe.py`` fails if that changes.
- The hold supervisor polls the key's hardware state to catch a lost key-up,
  but trusts an "up" reading only after it has once read "down" during the
  hold. If posted events do not move the hardware state, the supervisor
  never ends a posted hold early; if they do, it agrees with them.
- The default key, Right Option, is a modifier: it is posted as a
  flags-changed event, which is not counted by the key-down counter the app
  reads to seal a lease on a window it cannot inspect, and this probe's own
  window is one the app can read.

The output is timings and counts only: no text from the window is kept or
printed, and the window is emptied before every trial.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence


# The app's bindable keys (HOTKEY_CHOICES in dictate.py): macOS virtual key
# code, and for a modifier the event flags it sets while held (the family
# mask plus the device bit that names the side). A function key is posted as
# key-down and key-up instead.
MODIFIER_KEYS = {
    "alt_r": (0x3D, 0x00080000 | 0x40),
    "alt_l": (0x3A, 0x00080000 | 0x20),
    "cmd_r": (0x36, 0x00100000 | 0x10),
    "cmd_l": (0x37, 0x00100000 | 0x08),
    "ctrl_r": (0x3E, 0x00040000 | 0x2000),
    "ctrl_l": (0x3B, 0x00040000 | 0x01),
    "shift_r": (0x3C, 0x00020000 | 0x04),
    "shift_l": (0x38, 0x00020000 | 0x02),
}
FUNCTION_KEYS = dict(zip(
    (f"f{index}" for index in range(1, 21)),
    (0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D,
     0x67, 0x6F, 0x69, 0x6B, 0x71, 0x6A, 0x40, 0x4F, 0x50, 0x5A)))
KEY_NAMES = (*MODIFIER_KEYS, *FUNCTION_KEYS)

DEFAULT_TRIALS = 20
# Held before the recording starts, so capture is open when speech begins
# (the app's press-to-capture-ready was 42 ms at the median, 109 ms at most).
LEAD_IN_SECONDS = 0.4
# Held after the recording ends, as a person lets go just after speaking.
HOLD_AFTER_SECONDS = 0.2
# A release with no text by then is a timed-out trial, not a number.
TEXT_TIMEOUT_SECONDS = 15.0
# Between trials: the app's readback and feedback finish before the next.
SETTLE_SECONDS = 2.0
POLL_SECONDS = 0.002


class Keys(Protocol):
    def down(self) -> None: ...
    def up(self) -> None: ...


class Playback(Protocol):
    def wait(self) -> None: ...


class Player(Protocol):
    def play(self) -> Playback: ...


class Field(Protocol):
    def reset(self) -> None: ...
    def first_change_after(self, moment: float) -> float | None: ...


@dataclass(frozen=True)
class Trial:
    """One dictation, timings only (milliseconds on the probe's clock)."""
    release_to_text_ms: float | None
    hold_ms: float
    # Text appeared while the key was still held: the app should never do
    # this, so the trial is reported rather than timed.
    early: bool
    timed_out: bool


def run_trial(keys: Keys, player: Player, field: Field, *,
              clock: Callable[[], float] = time.perf_counter,
              sleep: Callable[[float], None] = time.sleep,
              lead_in: float = LEAD_IN_SECONDS,
              hold_after: float = HOLD_AFTER_SECONDS,
              timeout: float = TEXT_TIMEOUT_SECONDS,
              poll: float = POLL_SECONDS) -> Trial:
    """Press, play, release, and wait for the first text after the release.

    The key is always released, even if playback fails, so a probe that
    stops part-way never leaves the app recording.
    """
    field.reset()
    keys.down()
    pressed_at = clock()
    try:
        sleep(lead_in)
        player.play().wait()
        sleep(hold_after)
    finally:
        early = field.first_change_after(pressed_at) is not None
        keys.up()
        released_at = clock()
    hold_ms = round((released_at - pressed_at) * 1000.0, 3)
    if early:
        return Trial(None, hold_ms, early=True, timed_out=False)
    deadline = released_at + timeout
    while True:
        changed_at = field.first_change_after(released_at)
        if changed_at is not None:
            return Trial(round((changed_at - released_at) * 1000.0, 3),
                         hold_ms, early=False, timed_out=False)
        if clock() >= deadline:
            return Trial(None, hold_ms, early=False, timed_out=True)
        sleep(poll)


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower)


def summarize(trials: Sequence[Trial], *, warmup: int = 0) -> dict:
    """Content-free summary; the first ``warmup`` trials are not counted."""
    counted = list(trials[warmup:])
    timed = [trial.release_to_text_ms for trial in counted
             if trial.release_to_text_ms is not None]
    summary = {
        "schema_version": 1,
        "privacy": "timings-only",
        "measure": "key release posted to first text change in the probe's "
                   "own window",
        "trials": len(counted),
        "warmup_dropped": min(warmup, len(trials)),
        "timed": len(timed),
        "timed_out": sum(trial.timed_out for trial in counted),
        "early": sum(trial.early for trial in counted),
        "release_to_text_ms": None,
    }
    if timed:
        summary["release_to_text_ms"] = {
            "p50": round(percentile(timed, 0.50), 3),
            "p95": round(percentile(timed, 0.95), 3),
            "max": round(max(timed), 3),
        }
    return summary


def run_trials(keys: Keys, player: Player, field: Field, trials: int, *,
               sleep: Callable[[float], None] = time.sleep,
               settle: float = SETTLE_SECONDS, **trial_options) -> list[Trial]:
    results = []
    for index in range(trials):
        if index:
            sleep(settle)
        results.append(run_trial(
            keys, player, field, sleep=sleep, **trial_options))
    return results


# ------------------------------ the live Mac ---------------------------------


class QuartzKeys:
    """Posts the capture key at the HID level, as a keyboard would."""

    def __init__(self, name: str):
        import Quartz
        self.quartz = Quartz
        if name in MODIFIER_KEYS:
            self.code, self.flags = MODIFIER_KEYS[name]
        else:
            self.code, self.flags = FUNCTION_KEYS[name], None

    def _post(self, down: bool):
        quartz = self.quartz
        event = quartz.CGEventCreateKeyboardEvent(None, self.code, down)
        if self.flags is not None:
            quartz.CGEventSetType(event, quartz.kCGEventFlagsChanged)
            quartz.CGEventSetFlags(event, self.flags if down else 0)
        quartz.CGEventPost(quartz.kCGHIDEventTap, event)

    def down(self):
        self._post(True)

    def up(self):
        self._post(False)


class AfplayPlayer:
    def __init__(self, recording: Path):
        self.recording = str(recording)

    def play(self):
        return subprocess.Popen(
            ["/usr/bin/afplay", self.recording],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class WindowField:
    """A plain text window that notes when its text changes, never what."""

    def __init__(self, text_view, call_on_main):
        self.text_view = text_view
        self.call_on_main = call_on_main
        self.lock = threading.Lock()
        self.changes: list[float] = []

    def changed(self, moment: float):
        with self.lock:
            self.changes.append(moment)

    def reset(self):
        done = threading.Event()

        def clear():
            self.text_view.setString_("")
            done.set()

        self.call_on_main(clear)
        done.wait(5.0)
        with self.lock:
            self.changes.clear()

    def first_change_after(self, moment: float) -> float | None:
        with self.lock:
            return next((at for at in self.changes if at > moment), None)


def run_live(args) -> int:   # pragma: no cover - needs a Mac session
    import objc
    from AppKit import (
        NSApplication, NSApplicationActivationPolicyRegular,
        NSBackingStoreBuffered, NSTextDidChangeNotification, NSTextView,
        NSWindow, NSWindowStyleMaskTitled)
    from Foundation import NSMakeRect, NSNotificationCenter, NSObject
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(200, 200, 480, 200), NSWindowStyleMaskTitled,
        NSBackingStoreBuffered, False)
    window.setTitle_("Release latency probe")
    text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 480, 200))
    window.setContentView_(text_view)
    field = WindowField(text_view, AppHelper.callAfter)

    class Observer(NSObject):
        @objc.python_method
        def attach(self, target):
            self.target = target
            return self

        def changed_(self, _notification):
            self.target.changed(time.perf_counter())

    observer = Observer.alloc().init().attach(field)
    NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        observer, "changed:", NSTextDidChangeNotification, text_view)
    window.makeKeyAndOrderFront_(None)
    window.makeFirstResponder_(text_view)
    app.activateIgnoringOtherApps_(True)

    outcome = {}

    def trials():
        try:
            time.sleep(1.0)            # let the window take focus
            results = run_trials(
                QuartzKeys(args.key), AfplayPlayer(args.recording), field,
                args.trials + args.warmup)
            outcome["summary"] = summarize(results, warmup=args.warmup)
            outcome["trials"] = [asdict(trial) for trial in results]
        finally:
            AppHelper.callAfter(AppHelper.stopEventLoop)

    threading.Thread(target=trials, daemon=True).start()
    AppHelper.runEventLoop()
    if "summary" not in outcome:
        print("probe stopped before finishing", file=sys.stderr)
        return 1
    print(json.dumps(outcome, indent=2))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--recording", type=Path, required=True,
                        help="a recording of speech to play for each trial")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--warmup", type=int, default=1,
                        help="trials run first and not counted")
    parser.add_argument("--key", choices=KEY_NAMES, default="alt_r",
                        help="the app's capture key (Settings > Hotkey)")
    parser.add_argument(
        "--confirm-live", action="store_true",
        help="post real key events to the running dictation app and play "
             "the recording; without it, only the plan is printed")
    return parser.parse_args(argv)


def plan(args) -> str:
    return (
        f"would run {args.warmup} warm-up and {args.trials} timed trials: "
        f"empty the probe window, hold {args.key}, wait "
        f"{LEAD_IN_SECONDS:.1f} s, play the recording, hold "
        f"{HOLD_AFTER_SECONDS:.1f} s more, release, and time the first "
        f"text in the window (up to {TEXT_TIMEOUT_SECONDS:.0f} s). "
        "Nothing was posted or played; add --confirm-live to run it.")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.trials < 1 or args.warmup < 0:
        print("trials must be at least 1 and warm-up at least 0",
              file=sys.stderr)
        return 2
    if not args.confirm_live:
        print(plan(args))
        return 0
    if sys.platform != "darwin":
        print("the probe runs on a Mac only", file=sys.stderr)
        return 2
    if not args.recording.is_file():
        print("recording not found", file=sys.stderr)
        return 2
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
