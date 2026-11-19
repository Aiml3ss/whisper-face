# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyobjc-framework-Cocoa; sys_platform == 'darwin'",
#   "pyobjc-framework-Quartz; sys_platform == 'darwin'",
# ]
# ///
"""The outside probe: release to text on screen, timed from outside the app.

    uv run outside_probe.py run --app whisper-face --go-ahead whisper-face \\
        --benchmark <out of a benchmark run> --app-revision <40-character revision>

The one latency comparable across dictation apps is the time from letting go
of the dictation key to the text appearing in a text field. No app can be
trusted to time that itself, so this probe does it from outside:

1. It opens its own small window with one plain text field, brings it to the
   front and keeps it there. It never types anything, into that window or
   any other; it only presses and releases the app's capture key.
2. For each take of a finished public-benchmark run (``<out>/audio``, the
   same audio the benchmark's result hashes), it presses the capture key
   with a synthetic key event, plays the take through the Mac's speakers
   (``afplay``) so the app hears it through the built-in microphone, waits a
   short tail, and releases the key.
3. It timestamps every change to its window's text, and records per take
   the release time, the first text and the final text (the last change
   before the text stays still for ``--settle-ms``).

It writes ``<out>/outside_probe.json``; ``uv run benchmark_public.py
attach-probe --out <out>`` checks it against the benchmark result (same app
revision, machine, OS, corpus and audio) and fills the result's
``release_to_text_on_screen_ms``.

Posture, enforced by tests/test_outside_probe.py:

* It runs against an app only when the command line names the app twice,
  as ``--app`` and as the go-ahead ``--go-ahead``.
* It posts only the capture key, only while its own window is frontmost and
  key, and stops if that changes. It types no text and touches no pasteboard.
* It sends nothing anywhere: no network module is imported, the audio goes
  to the speakers, and the result stays in ``<out>``.

docs/benchmarks/public-benchmark.md ("The outside probe") is the method and
its caveats.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


HERE = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
WINDOW_TITLE = "Outside probe: keep this window in front"

# The keys Whisper Face can be bound to (dictate.py HOTKEY_CHOICES), as macOS
# virtual key codes (Carbon Events.h). A modifier is posted as a
# flags-changed event carrying its family's flag and its side's device bit,
# exactly as the keyboard sends it; a function key as key down and key up.
FLAG_SHIFT, FLAG_CONTROL = 0x00020000, 0x00040000
FLAG_OPTION, FLAG_COMMAND = 0x00080000, 0x00100000
FLAG_NONCOALESCED = 0x00000100
CAPTURE_KEYS: dict[str, tuple[int, int, int]] = {
    # name: (virtual key code, family flag, device-side bit)
    "alt_r": (61, FLAG_OPTION, 0x40),
    "alt_l": (58, FLAG_OPTION, 0x20),
    "cmd_r": (54, FLAG_COMMAND, 0x10),
    "cmd_l": (55, FLAG_COMMAND, 0x08),
    "ctrl_r": (62, FLAG_CONTROL, 0x2000),
    "ctrl_l": (59, FLAG_CONTROL, 0x01),
    "shift_r": (60, FLAG_SHIFT, 0x04),
    "shift_l": (56, FLAG_SHIFT, 0x02),
    **{f"f{index}": (code, 0, 0) for index, code in enumerate(
        (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107,
         113, 106, 64, 79, 80, 90), start=1)},
}

DEFAULT_KEY = "alt_r"
LEAD_MS = 300       # key down to the first sample: the app's start cue
TAIL_MS = 600       # last sample to key up: speaker and microphone buffers
SETTLE_MS = 1500    # text unchanged this long after a change is final
DEADLINE_MS = 30000 # no text this long after release is "no-text"
GAP_MS = 1000       # between takes, so a late paste lands in its own take
AUDIO_PATH = "the Mac's default output, heard by the app's microphone"


class ProbeError(RuntimeError):
    """The probe refuses to start or to go on."""


def now_ns() -> int:
    # One clock for every process on this Mac: the window process stamps
    # text with it, this process stamps the key release with it.
    return time.monotonic_ns()


def ms(ns: int) -> float:
    return round(ns / 1e6, 3)


# -- the capture key ----------------------------------------------------------


def capture_key_events(name: str, quartz: Any) -> tuple[Any, Any]:
    """The key-down and key-up events for one capture key; nothing is posted."""
    if name not in CAPTURE_KEYS:
        raise ProbeError(f"{name!r} is not a capture key the app can be "
                         f"bound to: {', '.join(CAPTURE_KEYS)}")
    code, family, side = CAPTURE_KEYS[name]
    source = quartz.CGEventSourceCreate(
        quartz.kCGEventSourceStateHIDSystemState)
    down = quartz.CGEventCreateKeyboardEvent(source, code, True)
    up = quartz.CGEventCreateKeyboardEvent(source, code, False)
    if family:
        for event, flags in ((down, family | side | FLAG_NONCOALESCED),
                             (up, FLAG_NONCOALESCED)):
            quartz.CGEventSetType(event, quartz.kCGEventFlagsChanged)
            quartz.CGEventSetFlags(event, flags)
    return down, up


class KeyDriver(Protocol):
    def press(self) -> int: ...
    def release(self) -> int: ...


class QuartzKeyDriver:
    """Presses and releases one capture key, as the keyboard would.

    The events go in at the HID level, so every event tap sees them as it
    sees a real key, including the app's listen-only session tap. macOS
    allows this only to a process the owner has trusted under Privacy &
    Security > Accessibility; without it the events are silently dropped,
    so the driver refuses to start instead.
    """

    def __init__(self, name: str):
        if name not in CAPTURE_KEYS:
            raise ProbeError(f"{name!r} is not a capture key")
        import Quartz

        if not Quartz.CGPreflightPostEventAccess():
            raise ProbeError(
                "macOS does not let this terminal post key events. Add it "
                "under System Settings > Privacy & Security > Accessibility, "
                "then run the probe again.")
        self._quartz = Quartz
        self._down, self._up = capture_key_events(name, Quartz)
        self.held = False

    def _post(self, event: Any) -> int:
        stamp = now_ns()
        self._quartz.CGEventPost(self._quartz.kCGHIDEventTap, event)
        return stamp

    def press(self) -> int:
        self.held = True
        return self._post(self._down)

    def release(self) -> int:
        stamp = self._post(self._up)
        self.held = False
        return stamp


# -- the audio ---------------------------------------------------------------


class Player(Protocol):
    def play(self, take_id: str, path: Path) -> None: ...


class SpeakerPlayer:
    """Plays a take through the default output and returns when it ends."""

    def play(self, take_id: str, path: Path) -> None:
        subprocess.run(["/usr/bin/afplay", str(path)], check=True)


def pcm_sha256(path: Path) -> str:
    """The hash the benchmark records for a take: its PCM frames."""
    with wave.open(str(path), "rb") as handle:
        return hashlib.sha256(handle.readframes(handle.getnframes())).hexdigest()


def write_padded(source: Path, target: Path, pad_seconds: float) -> None:
    """The take with the benchmark's silence either side, as 16-bit PCM:
    what the fake Mac's microphone was given, made playable."""
    with wave.open(str(source), "rb") as handle:
        rate, width = handle.getframerate(), handle.getsampwidth()
        channels = handle.getnchannels()
        frames = handle.readframes(handle.getnframes())
    pad = bytes(int(round(pad_seconds * rate)) * width * channels)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(pad + frames + pad)


# -- the probe's own window ----------------------------------------------------


class ProbeWindow:
    """The probe's text window, in its own AppKit process.

    That process reports every change to its text with the time it saw it,
    and answers whether it is frontmost with its text field focused.
    """

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.events: queue.Queue = queue.Queue()
        # Text that arrives while waiting for an answer is kept for the take.
        self._stash: deque = deque()
        self.pid = None
        self._seq = 0
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        ready = self._wait("ready", timeout=30)
        self.pid = ready["pid"]

    @classmethod
    def spawn(cls, *, hidden: bool = False, echo_fifo: Path | None = None,
              python: str | None = None) -> "ProbeWindow":
        command = [python or sys.executable, str(Path(__file__).resolve()),
                   "window"]
        if hidden:
            command.append("--hidden")
        if echo_fifo is not None:
            command += ["--echo-fifo", str(echo_fifo)]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1)
        try:
            return cls(process)
        except BaseException:
            process.kill()
            process.wait()
            process.stdin.close()
            process.stdout.close()
            raise

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                continue
        self.events.put({"event": "closed"})

    def _send(self, message: Mapping[str, Any]) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _wait(self, kind: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ProbeError(f"the probe window did not answer {kind!r}")
            try:
                event = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if event.get("event") == kind:
                return event
            if event.get("event") == "closed":
                raise ProbeError("the probe window closed")
            if event.get("event") == "text":
                self._stash.append(event)

    def clear(self) -> int:
        """Empty the field; changes after this belong to a new take."""
        self._seq += 1
        self._send({"op": "clear", "seq": self._seq})
        self._wait("cleared", timeout=10)
        self._stash.clear()
        return self._seq

    def focus(self) -> dict[str, Any]:
        self._send({"op": "focus"})
        return self._wait("focus", timeout=10)

    def focused(self) -> bool:
        state = self.focus()
        return bool(state["active"] and state["key"] and state["responder"]
                    and state["frontmost_pid"] == state["pid"])

    def activate(self) -> None:
        self._send({"op": "activate"})

    def menu(self) -> dict[str, str]:
        self._send({"op": "menu"})
        return self._wait("menu", timeout=10)["key_equivalents"]

    def text_events(self, seq: int, release_ns: int, *, settle_ms: int,
                    deadline_ms: int) -> list[dict[str, Any]]:
        """This take's text changes, until the text settles or the deadline."""
        seen: list[dict[str, Any]] = []
        while True:
            last = seen[-1]["t"] if seen else None
            limit = (last + settle_ms * 1_000_000 if last is not None
                     else release_ns + deadline_ms * 1_000_000)
            limit = min(limit, release_ns + deadline_ms * 1_000_000)
            wait = (limit - now_ns()) / 1e9
            if self._stash:
                event = self._stash.popleft()
            elif wait <= 0:
                return seen
            else:
                try:
                    event = self.events.get(timeout=wait)
                except queue.Empty:
                    continue
            if event.get("event") == "closed":
                raise ProbeError("the probe window closed during a take")
            if event.get("event") == "text" and event.get("seq") == seq:
                seen.append(event)

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self._send({"op": "quit"})
                self.process.stdin.close()
            except (BrokenPipeError, ValueError, OSError):
                pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (BrokenPipeError, OSError):
                pass


def window_main(argv: Sequence[str]) -> int:
    """The probe window's own process. It talks JSON lines on stdio."""
    parser = argparse.ArgumentParser(prog="outside_probe.py window")
    parser.add_argument("--hidden", action="store_true",
                        help="never show the window (tests)")
    parser.add_argument("--echo-fifo", type=Path,
                        help="tests only: a fake app's text arrives here")
    args = parser.parse_args(argv)
    if args.echo_fifo is not None and not args.hidden:
        # The seam a fake app types through exists only for a window nobody
        # sees, so a real run can never be fed text by anything but the app.
        parser.error("--echo-fifo is for a hidden test window only")

    import AppKit
    from Foundation import NSMakeRect, NSObject
    from PyObjCTools import AppHelper

    def emit(message: Mapping[str, Any]) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    state = {"seq": 0, "clearing": False}

    class StorageWatcher(NSObject):
        def textStorage_didProcessEditing_range_changeInLength_(
                self, storage, mask, _range, _delta):
            stamp = now_ns()
            if mask & AppKit.NSTextStorageEditedCharacters and not state[
                    "clearing"]:
                text = str(storage.string())
                emit({"event": "text", "seq": state["seq"], "t": stamp,
                      "length": len(text), "text": text})

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyProhibited if args.hidden
        else AppKit.NSApplicationActivationPolicyRegular)

    # An Edit menu, so a paste (Command-V) reaches the field as it would in
    # any Mac app. Without one, a bare process ignores the key equivalent.
    menubar = AppKit.NSMenu.alloc().init()
    app_item = AppKit.NSMenuItem.alloc().init()
    menubar.addItem_(app_item)
    app_menu = AppKit.NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_(
        "Quit Outside Probe", "terminate:", "q")
    app_item.setSubmenu_(app_menu)
    edit_item = AppKit.NSMenuItem.alloc().init()
    menubar.addItem_(edit_item)
    edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Cut", "cut:", "x"),
                               ("Copy", "copy:", "c"),
                               ("Paste", "paste:", "v"),
                               ("Select All", "selectAll:", "a")):
        edit_menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
    edit_item.setSubmenu_(edit_menu)
    app.setMainMenu_(menubar)

    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 560, 220),
        AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
        AppKit.NSBackingStoreBuffered, False)
    window.setTitle_(WINDOW_TITLE)
    window.setReleasedWhenClosed_(False)
    scroll = AppKit.NSScrollView.alloc().initWithFrame_(
        window.contentView().bounds())
    scroll.setHasVerticalScroller_(True)
    scroll.setAutoresizingMask_(
        AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    field = AppKit.NSTextView.alloc().initWithFrame_(scroll.bounds())
    field.setRichText_(False)
    field.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    field.setFont_(AppKit.NSFont.systemFontOfSize_(15))
    field.setAutomaticSpellingCorrectionEnabled_(False)
    field.setAutomaticTextReplacementEnabled_(False)
    field.setAutomaticQuoteSubstitutionEnabled_(False)
    field.setAutomaticDashSubstitutionEnabled_(False)
    scroll.setDocumentView_(field)
    window.setContentView_(scroll)
    window.makeFirstResponder_(field)
    watcher = StorageWatcher.alloc().init()
    field.textStorage().setDelegate_(watcher)

    def activate() -> None:
        window.center()
        window.makeKeyAndOrderFront_(None)
        window.makeFirstResponder_(field)
        app.activateIgnoringOtherApps_(True)

    def focus_state() -> dict[str, Any]:
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return {"event": "focus", "pid": os.getpid(),
                "active": bool(app.isActive()),
                "key": bool(window.isKeyWindow()),
                "responder": window.firstResponder() is field,
                "frontmost_pid": (int(front.processIdentifier())
                                  if front is not None else None)}

    def menu_state() -> dict[str, Any]:
        found = {}
        for item in app.mainMenu().itemArray():
            for entry in (item.submenu().itemArray()
                          if item.submenu() is not None else ()):
                if entry.keyEquivalent():
                    found[str(entry.keyEquivalent())] = str(entry.action())
        return {"event": "menu", "key_equivalents": found}

    def handle(message: Mapping[str, Any]) -> None:
        op = message.get("op")
        if op == "clear":
            state["clearing"] = True
            field.setString_("")
            state["clearing"] = False
            state["seq"] = int(message["seq"])
            emit({"event": "cleared", "seq": state["seq"], "t": now_ns()})
        elif op == "focus":
            emit(focus_state())
        elif op == "activate":
            activate()
        elif op == "menu":
            emit(menu_state())
        elif op == "quit":
            app.terminate_(None)

    def commands() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            AppHelper.callAfter(handle, message)
        # The probe is gone: never outlive it.
        AppHelper.callAfter(app.terminate_, None)

    def type_into_field(text: str) -> None:
        # A fake app's text, delivered as a key event to this window's own
        # field, the way typed text reaches it from the window server.
        event = AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(  # noqa: E501
            AppKit.NSEventTypeKeyDown, (0, 0), 0, 0, window.windowNumber(),
            None, text, text, False, 0)
        window.sendEvent_(event)

    def echoes(path: Path) -> None:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    text = json.loads(line)["type"]
                except (ValueError, KeyError, TypeError):
                    continue
                AppHelper.callAfter(type_into_field, str(text))

    threading.Thread(target=commands, daemon=True).start()
    if args.echo_fifo is not None:
        threading.Thread(target=echoes, args=(args.echo_fifo,),
                         daemon=True).start()
    if not args.hidden:
        activate()
    emit({"event": "ready", "pid": os.getpid()})
    AppHelper.runEventLoop(installInterrupt=True)
    return 0


# -- one run ---------------------------------------------------------------------


def take_row(take: Mapping[str, Any], repeat: int, release_ns: int,
             changes: Sequence[Mapping[str, Any]], epoch_ns: int) -> dict:
    """One take's times, in milliseconds since the run began."""
    row = {"id": take["id"], "repeat": repeat, "seconds": take["seconds"],
           "audio_sha256": take["audio_sha256"],
           "release_ms": ms(release_ns - epoch_ns)}
    if not changes:
        return {**row, "status": "no-text", "first_text_ms": None,
                "final_text_ms": None, "release_to_first_text_ms": None,
                "release_to_final_text_ms": None, "changes": 0, "text": ""}
    first, final = changes[0]["t"], changes[-1]["t"]
    return {**row, "status": "text",
            "first_text_ms": ms(first - epoch_ns),
            "final_text_ms": ms(final - epoch_ns),
            "release_to_first_text_ms": ms(first - release_ns),
            "release_to_final_text_ms": ms(final - release_ns),
            "changes": len(changes), "text": changes[-1]["text"]}


def run_probe(*, takes: Sequence[Mapping[str, Any]], audio: Mapping[str, Path],
              window: ProbeWindow, key: KeyDriver, player: Player,
              repeats: int, order: Callable[[int], Sequence[str]],
              lead_ms: int = LEAD_MS, tail_ms: int = TAIL_MS,
              settle_ms: int = SETTLE_MS, deadline_ms: int = DEADLINE_MS,
              gap_ms: int = GAP_MS,
              log: Callable[[str], None] = lambda _message: None) -> dict:
    """Time every take; stop at once if the probe window loses the front.

    The key is pressed only while the window is frontmost with its field
    focused, and is always released, even when the run stops.
    """
    by_id = {take["id"]: take for take in takes}
    rows: list[dict[str, Any]] = []
    epoch = now_ns()
    held = False
    stopped = None
    try:
        for repeat in range(1, repeats + 1):
            for take_id in order(repeat):
                take = by_id[take_id]
                seq = window.clear()
                if not window.focused():
                    stopped = (f"before {take_id}: the probe window is not "
                               "in front; nothing was pressed")
                    break
                key.press()
                held = True
                time.sleep(lead_ms / 1000)
                player.play(take_id, audio[take_id])
                time.sleep(tail_ms / 1000)
                in_front = window.focused()
                release = key.release()
                held = False
                if not in_front:
                    stopped = (f"during {take_id}: the probe window lost the "
                               "front, so the app may have typed elsewhere")
                    break
                changes = window.text_events(
                    seq, release, settle_ms=settle_ms, deadline_ms=deadline_ms)
                row = take_row(take, repeat, release, changes, epoch)
                rows.append(row)
                log(f"{repeat}/{repeats} {take_id}: " + (
                    f"first text {row['release_to_first_text_ms']:.0f} ms, "
                    f"final {row['release_to_final_text_ms']:.0f} ms"
                    if row["status"] == "text" else "no text"))
                time.sleep(gap_ms / 1000)
            if stopped:
                break
    finally:
        if held:
            key.release()
    return {"takes": rows, "completed": stopped is None, "stopped": stopped}


# -- the result ------------------------------------------------------------------


def build_probe_result(*, outcome: Mapping[str, Any], app: str,
                       app_revision: str, environment: Mapping[str, Any],
                       corpus: Mapping[str, Any], key: str, repeats: int,
                       timing: Mapping[str, int], command: str,
                       started_utc: str) -> dict[str, Any]:
    import benchmark_public as bench

    return {
        "schema_version": SCHEMA_VERSION,
        "result_kind": bench.PROBE_RESULT_KIND,
        "measured_by": bench.OUTSIDE_PROBE,
        "target": {"product_id": app, "revision": app_revision},
        "environment": {key_: environment[key_] for key_ in (
            "environment_id", "hardware", "os_name", "os_version")},
        "corpus": {"corpus_id": corpus["corpus_id"],
                   "sha256": bench.canonical_digest(corpus)},
        "method": {
            "capture_key": key,
            "key_events": "synthetic, posted at the HID level",
            "audio": AUDIO_PATH,
            "timestamp": ("the probe window's text storage edited, before "
                          "it is drawn"),
            **{name: timing[name] for name in (
                "lead_ms", "tail_ms", "settle_ms", "deadline_ms", "gap_ms")},
        },
        "run": {"started_utc": started_utc, "repeats": repeats,
                "seed": corpus["seed"], "command": command,
                "completed": outcome["completed"],
                "stopped": outcome["stopped"]},
        "takes": list(outcome["takes"]),
        "summary": bench.probe_summary(outcome["takes"]),
    }


def rerun_command(args: argparse.Namespace) -> str:
    words = ["uv", "run", "outside_probe.py", "run", "--app", args.app,
             "--go-ahead", args.app, "--benchmark", "<out>",
             "--app-revision", "<revision>"]
    if args.key != DEFAULT_KEY:
        words += ["--key", args.key]
    if args.repeats != 1:
        words += ["--repeats", str(args.repeats)]
    if args.take:
        words += ["--take", *args.take]
    return " ".join(words)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def prepare_takes(out: Path, wanted: Sequence[str] | None
                  ) -> tuple[dict, list[dict], dict[str, Path]]:
    """The benchmark result's takes, their audio checked against its hashes,
    and the padded files the probe plays."""
    import benchmark_public as bench

    result_path = out / "result.json"
    if not result_path.is_file():
        raise ProbeError(f"{result_path} does not exist: run "
                         "benchmark_public.py run first")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    corpus = bench.load_corpus()
    if result.get("corpus", {}).get("sha256") != bench.canonical_digest(corpus):
        raise ProbeError("that benchmark result is of a different corpus")
    takes = [take for take in result["takes"]
             if not wanted or take["id"] in wanted]
    unknown = sorted(set(wanted or ()) - {take["id"] for take in takes})
    if unknown:
        raise ProbeError(f"no such takes: {', '.join(unknown)}")
    audio = {}
    for take in takes:
        source = out / "audio" / f"{take['id']}.wav"
        if not source.is_file() or pcm_sha256(source) != take["audio_sha256"]:
            raise ProbeError(f"{source} is not the audio the benchmark "
                             "measured; rerun the benchmark")
        target = out / "probe-audio" / f"{take['id']}.wav"
        write_padded(source, target, corpus["speech"]["pad_seconds"])
        audio[take["id"]] = target
    return corpus, takes, audio


def refuse_without_go_ahead(args: argparse.Namespace) -> None:
    """Nothing runs against an app the command line did not name twice."""
    if not args.go_ahead or args.go_ahead != args.app:
        raise ProbeError(
            f"refusing to press keys for {args.app!r}: repeat its name as "
            f"--go-ahead {args.app} to say you want this run. The probe "
            "will bring its own window to the front, press and release the "
            "app's capture key, and play every take through the speakers.")
    if args.app == "whisper-face" and not (
            len(args.app_revision) == 40 and all(
                ch in "0123456789abcdef" for ch in args.app_revision)):
        raise ProbeError("--app-revision must be the full 40-character "
                         "revision the installed Whisper Face runs")
    if not args.app_revision.strip():
        raise ProbeError("--app-revision must name what was measured")


def run(args: argparse.Namespace,
        log: Callable[[str], None] = lambda message: print(
            message, file=sys.stderr, flush=True)) -> int:
    refuse_without_go_ahead(args)
    out = args.benchmark.expanduser().resolve()
    if _inside(out, HERE):
        raise ProbeError("--benchmark must be a run folder outside the "
                         "checkout, as benchmark_public.py requires")
    if sys.platform != "darwin":
        raise ProbeError("the probe drives a Mac app; run it on a Mac")
    corpus, takes, audio = prepare_takes(out, args.take)
    import benchmark_public as bench

    environment = bench.host_machine()
    key = QuartzKeyDriver(args.key)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = [take["id"] for take in takes]
    log(f"{len(ids)} takes x {args.repeats}. Set the speaker volume so the "
        "microphone hears it, keep the room quiet, and do not touch the "
        "keyboard or mouse. Ctrl-C stops the run and releases the key.")
    window = ProbeWindow.spawn()
    try:
        window.activate()
        for _ in range(50):
            if window.focused():
                break
            time.sleep(0.1)
        else:
            raise ProbeError("the probe window could not come to the front; "
                             "click it and run again")
        for remaining in range(args.countdown, 0, -1):
            log(f"starting in {remaining} s")
            time.sleep(1)
        timing = {"lead_ms": args.lead_ms, "tail_ms": args.tail_ms,
                  "settle_ms": args.settle_ms,
                  "deadline_ms": args.deadline_ms, "gap_ms": GAP_MS}
        outcome = run_probe(
            takes=takes, audio=audio, window=window, key=key,
            player=SpeakerPlayer(), repeats=args.repeats,
            order=lambda repeat: bench.run_order(
                ids, corpus["seed"], f"probe-{repeat}"),
            log=log, **timing)
    finally:
        window.close()
    probe = build_probe_result(
        outcome=outcome, app=args.app, app_revision=args.app_revision,
        environment=environment, corpus=corpus, key=args.key,
        repeats=args.repeats, timing=timing, command=rerun_command(args),
        started_utc=started)
    (out / bench.PROBE_RESULT_FILE).write_text(
        json.dumps(probe, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    final = probe["summary"]["release_to_final_text"]["all"]
    log(f"release to final text: p50 {final['p50']} ms, p95 {final['p95']} "
        f"ms over {final['takes']} takes; wrote "
        f"{out / bench.PROBE_RESULT_FILE}")
    if not outcome["completed"]:
        log(f"stopped {outcome['stopped']}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["window"]:
        return window_main(argv[1:])
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("run", help="time an app from outside it")
    probe.add_argument("--app", required=True,
                       help="the app being measured, e.g. whisper-face")
    probe.add_argument("--go-ahead", default="",
                       help="the same name again: your go-ahead to run")
    probe.add_argument("--benchmark", type=Path, required=True,
                       help="the --out folder of a benchmark_public.py run")
    probe.add_argument("--app-revision", required=True,
                       help="the revision (or version) the app runs")
    probe.add_argument("--key", default=DEFAULT_KEY, choices=CAPTURE_KEYS,
                       help="the app's capture key (default Right Option)")
    probe.add_argument("--repeats", type=int, default=1,
                       choices=range(1, 11), metavar="1-10")
    probe.add_argument("--take", nargs="+", help="only these take ids")
    probe.add_argument("--lead-ms", type=int, default=LEAD_MS)
    probe.add_argument("--tail-ms", type=int, default=TAIL_MS)
    probe.add_argument("--settle-ms", type=int, default=SETTLE_MS)
    probe.add_argument("--deadline-ms", type=int, default=DEADLINE_MS)
    probe.add_argument("--countdown", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except ProbeError as error:
        print(f"outside_probe: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
