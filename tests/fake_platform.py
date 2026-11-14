"""A fake Mac for running the real dictate.py in a test process.

``boot_fresh_install()`` imports the real ``dictate`` module from this
checkout with every platform module replaced, gives it a fresh-install state
directory built from the shipped templates the way ``setup.sh`` builds it, and
runs the real ``main()`` until it hands control to the AppKit event loop.

Fakes sit only at the edge of the process:

  microphone   sounddevice streams whose callback the test feeds
  keyboard     a pynput listener the test presses keys on, and a controller
               whose Cmd+V pastes the pasteboard into the focused field
  field        an Accessibility element with a value and a selection that,
               like macOS, reports its range in UTF-16 code units
  recognizers  the Parakeet helper as a child process speaking the framed
               protocol over real pipes, and mlx_whisper for Whisper Tiny
  models       the default Mac set: Parakeet and Whisper Tiny, no
               large-v3-turbo, no cleanup model
  processes    every other spawn is refused and recorded
  network      requests refuse and record; the phone endpoint server records
               its address instead of binding it

Everything between the hotkey and the paste is the shipping code.

The module replaces entries in ``sys.modules``. Each test file that uses it
runs in its own process, as the release gate runs them; ``FakeMac.close()``
puts back what it replaced for a runner that shares one process.
"""
from __future__ import annotations

import contextlib
import enum
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unicodedata
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16_000

# The private files setup.sh creates from templates on a fresh install.
FRESH_INSTALL_TEMPLATES = (
    ("snippets.template.json", "snippets.json"),
    ("tones.template.json", "tones.json"),
    ("preferences.template.json", "preferences.json"),
    ("acoustic_keyword_memory.template.json", "acoustic_keyword_memory.json"),
    ("dictionary.template.txt", "dictionary.txt"),
)

PLATFORM_MODULES = (
    "AppKit", "Foundation", "Quartz", "PyObjCTools", "PyObjCTools.AppHelper",
    "ApplicationServices", "CoreFoundation", "CoreText", "objc",
    "sounddevice", "pynput", "pynput.keyboard", "requests",
    "mlx_whisper", "tqdm", "huggingface_hub", "mlx", "mlx.core",
)
# First-party modules that import a platform module, directly or through
# dictate. They are imported again under the fakes.
FIRST_PARTY_PREFIXES = (
    "dictate", "whisper_face_", "macos_", "self_update", "support_bundle",
)


# --------------------------------------------------------------- permissive


class _AnyMeta(type):
    """Unknown class attributes (NSColor.labelColor) are callables too."""

    def __getattr__(cls, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        value = Anything()
        type.__setattr__(cls, name, value)
        return value


class Anything(metaclass=_AnyMeta):
    """An Objective-C class, instance, constant or function, as needed."""

    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        value = Anything()
        object.__setattr__(self, name, value)
        return value

    def __call__(self, *args, **kwargs):
        return Anything()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True

    def __or__(self, other):
        return self

    __ror__ = __and__ = __rand__ = __add__ = __radd__ = __or__
    __sub__ = __rsub__ = __mul__ = __rmul__ = __or__

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        # NS*/CA*/AX* names may be subclassed, so hand back a class.
        value = type(name, (Anything,), {})
        setattr(self, name, value)
        return value


class ObjCSuper:
    """objc.super: an Objective-C superclass answers every message, and an
    initializer answers with the receiver."""

    def __init__(self, cls, instance):
        self._super = super(cls, instance)
        self._instance = instance

    def __getattr__(self, name):
        try:
            return getattr(self._super, name)
        except AttributeError:
            if name.startswith("init"):
                return lambda *args, **kwargs: self._instance
            return lambda *args, **kwargs: Anything()


# ------------------------------------------------------------------ pynput


class KeyCode:
    """pynput's KeyCode: a virtual-key code or a character."""

    def __init__(self, vk=None, char=None):
        self.vk = vk
        self.char = char

    @classmethod
    def from_vk(cls, vk, **_kwargs):
        return cls(vk=vk)

    @classmethod
    def from_char(cls, char, **_kwargs):
        return cls(char=char)

    def __eq__(self, other):
        return (isinstance(other, KeyCode)
                and (self.vk, self.char) == (other.vk, other.char))

    def __hash__(self):
        return hash((self.vk, self.char))

    def __repr__(self):
        return f"KeyCode(vk={self.vk!r}, char={self.char!r})"


class Key(enum.Enum):
    """pynput's macOS Key table: aliases share one virtual-key code."""

    alt = KeyCode(vk=58)
    alt_l = KeyCode(vk=58)
    alt_r = KeyCode(vk=61)
    alt_gr = KeyCode(vk=61)
    cmd = KeyCode(vk=55)
    cmd_l = KeyCode(vk=55)
    cmd_r = KeyCode(vk=54)
    ctrl = KeyCode(vk=59)
    ctrl_l = KeyCode(vk=59)
    ctrl_r = KeyCode(vk=62)
    shift = KeyCode(vk=56)
    shift_l = KeyCode(vk=56)
    shift_r = KeyCode(vk=60)
    caps_lock = KeyCode(vk=57)
    backspace = KeyCode(vk=51)
    delete = KeyCode(vk=117)
    enter = KeyCode(vk=36)
    esc = KeyCode(vk=53)
    space = KeyCode(vk=49)
    tab = KeyCode(vk=48)
    left = KeyCode(vk=123)
    right = KeyCode(vk=124)
    down = KeyCode(vk=125)
    up = KeyCode(vk=126)
    f1 = KeyCode(vk=122)
    f2 = KeyCode(vk=120)
    f3 = KeyCode(vk=99)
    f4 = KeyCode(vk=118)
    f5 = KeyCode(vk=96)
    f6 = KeyCode(vk=97)
    f7 = KeyCode(vk=98)
    f8 = KeyCode(vk=100)
    f9 = KeyCode(vk=101)
    f10 = KeyCode(vk=109)
    f11 = KeyCode(vk=103)
    f12 = KeyCode(vk=111)
    f13 = KeyCode(vk=105)
    f14 = KeyCode(vk=107)
    f15 = KeyCode(vk=113)
    f16 = KeyCode(vk=106)
    f17 = KeyCode(vk=64)
    f18 = KeyCode(vk=79)
    f19 = KeyCode(vk=80)
    f20 = KeyCode(vk=90)


# ------------------------------------------------------------- the desktop


class TextField:
    """A focused text field as Accessibility exposes it.

    ``selection`` is in code points; ``reported_range`` is what macOS hands
    an Accessibility client, in UTF-16 code units, as NSString counts.
    """

    def __init__(self, text: str = "", bundle: str = "com.apple.TextEdit",
                 caret: int | None = None, rewrite=None):
        self.text = text
        self.selection = (len(text) if caret is None else caret, 0)
        self.bundle = bundle
        self.secure = False
        # An app that changes what is pasted into it (smart quotes, an added
        # newline) is modelled by a function from pasted to typed text.
        self.rewrite = rewrite

    def reported_range(self):
        start, length = self.selection

        def units(value: str) -> int:
            return len(value.encode("utf-16-le", "surrogatepass")) // 2

        return (units(self.text[:start]),
                units(self.text[start:start + length]))

    def type_text(self, value: str):
        if self.rewrite is not None:
            value = self.rewrite(value)
        start, length = self.selection
        self.text = self.text[:start] + value + self.text[start + length:]
        self.selection = (start + len(value), 0)

    def delete_backward(self):
        start, length = self.selection
        if length == 0:
            start, length = cluster_start(self.text, start), 0
            length = self.selection[0] - start
        self.text = self.text[:start] + self.text[start + length:]
        self.selection = (start, 0)

    def __repr__(self):
        return f"TextField({self.text!r}, selection={self.selection})"


def _attaches(char: str) -> bool:
    """Whether a code point joins the character before it on screen."""
    code = ord(char)
    return (unicodedata.category(char) in ("Mn", "Me")
            or 0x1F3FB <= code <= 0x1F3FF      # skin tone
            or code in (0x200D, 0xFE0E, 0xFE0F)
            or 0xE0020 <= code <= 0xE007F)     # tag characters


def cluster_start(text: str, end: int) -> int:
    """Where the user-perceived character ending at ``end`` starts, the way
    Shift+Left and Delete step on macOS."""
    start = max(0, end - 1)
    while start > 0 and (_attaches(text[start])
                         or text[start - 1] == "\u200d"):
        start -= 1
    regional = 0
    while start - regional > 0 and 0x1F1E6 <= ord(
            text[start - regional - 1]) <= 0x1F1FF:
        regional += 1
    if 0x1F1E6 <= ord(text[start]) <= 0x1F1FF and regional % 2:
        start -= 1
    return start


class App:
    """NSRunningApplication for the frontmost app."""

    def __init__(self, bundle: str, pid: int = 4242):
        self._bundle = bundle
        self._pid = pid

    def bundleIdentifier(self):
        return self._bundle

    def processIdentifier(self):
        return self._pid

    def localizedName(self):
        return self._bundle.rsplit(".", 1)[-1]

    def bundleURL(self):
        return None

    def isActive(self):
        return True


class PasteboardItem:
    def __init__(self):
        self.data = {}

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setString_forType_(self, value, kind):
        self.data[kind] = value
        return True

    def setData_forType_(self, value, kind):
        self.data[kind] = value
        return True

    def stringForType_(self, kind):
        value = self.data.get(kind)
        return value if isinstance(value, str) else None

    def dataForType_(self, kind):
        return self.data.get(kind)

    def types(self):
        return list(self.data)


class Pasteboard:
    """NSPasteboard.generalPasteboard()."""

    STRING = "public.utf8-plain-text"

    def __init__(self):
        self.items = []
        self.change_count = 0
        self.options = []

    def clearContents(self):
        self.items = []
        self.change_count += 1
        return self.change_count

    def prepareForNewContentsWithOptions_(self, options):
        self.options.append(options)
        return self.clearContents()

    def writeObjects_(self, items):
        self.items.extend(items)
        self.change_count += 1
        return True

    def setString_forType_(self, value, kind):
        item = PasteboardItem()
        item.setString_forType_(value, kind)
        self.items = [item]
        self.change_count += 1
        return True

    def changeCount(self):
        return self.change_count

    def pasteboardItems(self):
        return list(self.items)

    def types(self):
        return [kind for item in self.items for kind in item.types()]

    def stringForType_(self, kind):
        for item in self.items:
            value = item.stringForType_(kind)
            if value is not None:
                return value
        return None

    def copy_text(self, value: str):
        """Something the user copied: a plain item with no privacy markers."""
        self.setString_forType_(value, self.STRING)


class Keyboard:
    """pynput.keyboard.Controller: what synthetic keystrokes do on the Mac."""

    def __init__(self, desktop):
        self.desktop = desktop
        self.held = []

    def pressed(self, *keys):
        keyboard = self

        @contextlib.contextmanager
        def chord():
            for key in keys:
                keyboard.press(key)
            try:
                yield
            finally:
                for key in reversed(keys):
                    keyboard.release(key)

        return chord()

    def press(self, key):
        self.desktop.keystrokes.append(("press", key, tuple(self.held)))
        if key == "v" and Key.cmd in self.held:
            self.desktop.paste_from_pasteboard()
        elif key == Key.left and Key.shift in self.held:
            self.desktop.extend_selection_left()
        elif key == Key.backspace and self.desktop.field is not None:
            self.desktop.field.delete_backward()
        self.held.append(key)

    def release(self, key):
        self.desktop.keystrokes.append(("release", key, tuple(self.held)))
        with contextlib.suppress(ValueError):
            self.held.remove(key)

    def tap(self, key):
        self.press(key)
        self.release(key)

    def type(self, text):
        self.desktop.keystrokes.append(("type", text, tuple(self.held)))


class Listener:
    """pynput.keyboard.Listener: the event tap the test presses keys on."""

    def __init__(self, desktop, on_press=None, on_release=None, **kwargs):
        self.desktop = desktop
        self.on_press = on_press
        self.on_release = on_release
        self.options = kwargs
        self.running = False
        self.alive = False

    def start(self):
        self.running = self.alive = True
        self.desktop.listeners.append(self)

    def stop(self):
        self.running = self.alive = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        return None


class InputStream:
    """A sounddevice.InputStream on PortAudio's default input device.

    PortAudio reads the device list once, when it is initialized, so a stream
    opens the device that was the default at the last initialization.
    """

    def __init__(self, desktop, **options):
        if not desktop.portaudio_initialized:
            raise RuntimeError("PortAudio not initialized")
        self.desktop = desktop
        self.options = options
        self.callback = options.get("callback")
        self.device = desktop.portaudio_default
        self.active = False
        self.closed = False
        desktop.streams.append(self)

    def start(self):
        if self.closed:
            raise RuntimeError("stream closed")
        if self.desktop.microphone_fails:
            raise RuntimeError("PortAudio: no default input device")
        self.active = True

    def stop(self):
        self.active = False

    def abort(self):
        self.active = False

    def close(self):
        self.active = False
        self.closed = True


class HelperProcess:
    """The Parakeet helper: a child process speaking the framed protocol.

    Real pipes carry the request, so the client's 8-byte length framing and
    its bounded, non-blocking exchange are what is being exercised. The
    "process" is a thread that reads a little-endian u64 sample count and that
    many float32 samples, and answers with one JSON line.
    """

    def __init__(self, desktop, argv):
        self.desktop = desktop
        self.argv = list(argv)
        request_read, request_write = os.pipe()
        response_read, response_write = os.pipe()
        self.stdin = os.fdopen(request_write, "wb", buffering=0)
        self.stdout = os.fdopen(response_read, "rb", buffering=0)
        self._input = os.fdopen(request_read, "rb", buffering=0)
        self._output = os.fdopen(response_write, "wb", buffering=0)
        self.returncode = None
        self.thread = threading.Thread(
            target=self._serve, name="fake-parakeet-helper", daemon=True)
        self.thread.start()

    def _read_exactly(self, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = self._input.read(count - len(data))
            if not chunk:
                raise EOFError
            data.extend(chunk)
        return bytes(data)

    def _reply(self, value: dict):
        self._output.write(json.dumps(value).encode("utf-8") + b"\n")

    def _serve(self):
        try:
            self._reply({"ready": True, "load_s": 0.01,
                         "model": "parakeet-unified"})
            while True:
                header = self._read_exactly(8)
                (count,) = struct.unpack("<Q", header)
                payload = self._read_exactly(4 * count)
                audio = np.frombuffer(payload, dtype="<f4")
                self.desktop.parakeet_requests.append(len(audio))
                self._reply({"ok": True,
                             "text": self.desktop.recognize(audio, "parakeet"),
                             "processing_s": 0.01})
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.returncode = 0
            for stream in (self._input, self._output):
                with contextlib.suppress(OSError):
                    stream.close()

    def poll(self):
        return self.returncode

    def terminate(self):
        with contextlib.suppress(OSError):
            self.stdin.close()

    kill = terminate

    def wait(self, timeout=None):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.returncode


class CoreAudioListener:
    """CoreAudio's default-input property listener."""

    def __init__(self):
        self.notify = None

    def start(self, notify):
        self.notify = notify

    def close(self):
        self.notify = None


class PhoneServer:
    """ThreadingHTTPServer for the loopback endpoint: records, never binds."""

    def __init__(self, desktop, address, handler):
        self.server_address = address
        self.handler = handler
        self.stopped = threading.Event()
        desktop.servers.append(self)

    def serve_forever(self, *_args):
        self.stopped.wait()

    def shutdown(self):
        self.stopped.set()

    def server_close(self):
        self.stopped.set()


class DictationStuck(AssertionError):
    """A dictation never started or never finished: the program is broken
    in a way that makes every later dictation pointless to wait for."""


class RefusedConnection(Exception):
    """Raised for every outbound request: a fresh install has no network."""


class Desktop:
    """What the program can see and touch of the Mac it runs on."""

    def __init__(self):
        self.app = App("com.apple.TextEdit")
        self.field = TextField()
        self.pasteboard = Pasteboard()
        self.keyboard = Keyboard(self)
        self.trusted = True
        self.keys_down = set()
        self.keystrokes = []
        self.pastes = []
        self.listeners = []
        self.streams = []
        self.microphone_fails = False
        self.default_input = "Built-in Microphone"
        self.portaudio_default = self.default_input
        self.portaudio_initialized = True
        self.portaudio_calls = []
        self.spoken_into = []
        self.servers = []
        self.spawns = []
        self.helper_spawns = []
        self.network = []
        self.parakeet_requests = []
        self.whisper_requests = []
        self.main_thread_calls = []
        self.installed_models = set()
        self.core_audio = CoreAudioListener()
        # What the speaker is saying right now, as each recognizer hears it.
        self.heard = {"parakeet": "", "whisper": ""}

    # -- focus and the pasteboard ------------------------------------------

    def focus(self, field: TextField):
        self.field = field
        self.app = App(field.bundle)

    def paste_from_pasteboard(self):
        value = self.pasteboard.stringForType_(Pasteboard.STRING)
        self.pastes.append({"text": value, "types": self.pasteboard.types(),
                            "options": list(self.pasteboard.options)})
        if value is not None and self.field is not None:
            self.field.type_text(value)

    def extend_selection_left(self):
        start, length = self.field.selection
        if start > 0:
            begin = cluster_start(self.field.text, start)
            self.field.selection = (begin, length + start - begin)

    # -- recognizers -------------------------------------------------------

    def recognize(self, audio, engine: str) -> str:
        if len(audio) == 0 or float(np.max(np.abs(audio))) < 1e-3:
            return ""
        return self.heard[engine]

    def whisper_transcribe(self, audio, **options):
        self.whisper_requests.append(options)
        text = self.recognize(np.asarray(audio), "whisper")
        seconds = len(audio) / SAMPLE_RATE
        segments = [{
            "text": text, "start": 0.0, "end": seconds,
            "avg_logprob": -0.05, "no_speech_prob": 0.0,
            "compression_ratio": 1.3,
        }] if text else []
        return {"text": text, "segments": segments}

    def snapshot_download(self, repo_id=None, **_options):
        if repo_id not in self.installed_models:
            raise FileNotFoundError(f"{repo_id} is not in the local cache")
        return f"/fake-models/{repo_id}"

    # -- processes and the network -----------------------------------------

    def refuse_spawn(self, args=None, *more, **kwargs):
        argv = args if args is not None else kwargs.get("args")
        self.spawns.append(list(argv) if isinstance(argv, (list, tuple))
                           else [str(argv)])
        raise OSError("spawning a process is refused in this test")

    def spawn_helper(self, argv, **_kwargs):
        self.helper_spawns.append(list(argv))
        return HelperProcess(self, argv)

    def refuse_request(self, method):
        def request(url, *args, **kwargs):
            self.network.append((method, str(url)))
            raise RefusedConnection(f"{method} {url} refused in this test")
        return request

    def sounds(self) -> list[str]:
        """Feedback cues, in order, from the afplay spawns play() asked for."""
        return [Path(argv[-1]).stem for argv in self.spawns
                if argv and argv[0] == "afplay"]

    # -- keys and the microphone ---------------------------------------------

    def press(self, key):
        self.keys_down.add(key.value.vk)
        for listener in list(self.listeners):
            if listener.running and listener.on_press:
                listener.on_press(key)

    def release(self, key):
        self.keys_down.discard(key.value.vk)
        for listener in list(self.listeners):
            if listener.running and listener.on_release:
                listener.on_release(key)

    def key_state(self, _source_state, vk) -> bool:
        return vk in self.keys_down

    def live_streams(self):
        return [stream for stream in self.streams if stream.active]

    def portaudio_terminate(self):
        self.portaudio_calls.append("terminate")
        self.portaudio_initialized = False

    def portaudio_initialize(self):
        self.portaudio_calls.append("initialize")
        self.portaudio_default = self.default_input
        self.portaudio_initialized = True

    def change_default_input(self, name: str):
        """The user plugs in a headset: macOS switches the default input and
        CoreAudio notifies its listeners."""
        self.default_input = name
        if self.core_audio.notify is not None:
            self.core_audio.notify()

    def speak(self, audio, block: int = 512):
        """Deliver audio to every running input stream, in device blocks."""
        audio = np.asarray(audio, dtype=np.float32)
        self.spoken_into = [stream.device for stream in self.live_streams()]
        for start in range(0, len(audio), block):
            frames = audio[start:start + block].reshape(-1, 1).copy()
            for stream in self.live_streams():
                stream.callback(frames, len(frames), None, None)


def speech_audio(seconds: float = 1.2, silence: float = 0.3, seed: int = 7):
    """Voiced noise followed by trailing silence, at the capture rate."""
    rng = np.random.default_rng(seed)
    voiced = (0.08 * rng.standard_normal(int(seconds * SAMPLE_RATE)))
    quiet = np.zeros(int(silence * SAMPLE_RATE))
    return np.concatenate([voiced, quiet]).astype(np.float32)


# --------------------------------------------------------- module install


def _platform_modules(desktop: Desktop) -> dict:
    modules = {name: FakeModule(name) for name in PLATFORM_MODULES}
    modules["pynput"].keyboard = modules["pynput.keyboard"]
    modules["PyObjCTools"].AppHelper = modules["PyObjCTools.AppHelper"]
    modules["mlx"].core = modules["mlx.core"]

    keyboard = modules["pynput.keyboard"]
    keyboard.Key = Key
    keyboard.KeyCode = KeyCode
    keyboard.Controller = lambda: desktop.keyboard
    keyboard.Listener = lambda **kwargs: Listener(desktop, **kwargs)

    objc = modules["objc"]
    objc.python_method = lambda function: function
    objc.IBAction = lambda function: function
    objc.autorelease_pool = contextlib.nullcontext
    objc.super = ObjCSuper
    objc.selector = lambda function, **_kw: function

    helper = modules["PyObjCTools.AppHelper"]
    helper.callAfter = (
        lambda function, *args: desktop.main_thread_calls.append(
            (function, args)))
    helper.runEventLoop = (
        lambda *args, **kwargs: desktop.main_thread_calls.append(
            ("runEventLoop", kwargs)))

    class Workspace(Anything):
        def frontmostApplication(self):
            return desktop.app

    workspace = Workspace()
    appkit = modules["AppKit"]
    appkit.NSWorkspace = type("NSWorkspace", (Anything,), {
        "sharedWorkspace": staticmethod(lambda: workspace)})
    appkit.NSPasteboard = type("NSPasteboard", (Anything,), {
        "generalPasteboard": staticmethod(lambda: desktop.pasteboard)})
    appkit.NSPasteboardItem = PasteboardItem
    appkit.NSPasteboardTypeString = Pasteboard.STRING
    modules["Foundation"].NSData = type("NSData", (Anything,), {
        "data": staticmethod(lambda: b"")})

    quartz = modules["Quartz"]
    quartz.CGPreflightListenEventAccess = lambda: desktop.trusted
    quartz.CGPreflightPostEventAccess = lambda: desktop.trusted
    quartz.CGRequestListenEventAccess = lambda: desktop.trusted
    quartz.CGRequestPostEventAccess = lambda: desktop.trusted
    quartz.CGEventSourceKeyState = desktop.key_state
    quartz.kCGEventSourceStateHIDSystemState = 1

    modules["ApplicationServices"].__dict__.update(
        _accessibility(desktop))
    modules["CoreFoundation"].CFEqual = lambda left, right: left is right

    sounddevice = modules["sounddevice"]
    sounddevice.InputStream = (
        lambda **options: InputStream(desktop, **options))
    sounddevice._terminate = desktop.portaudio_terminate
    sounddevice._initialize = desktop.portaudio_initialize

    requests = modules["requests"]
    requests.RequestException = RefusedConnection
    requests.ConnectionError = RefusedConnection
    requests.Timeout = RefusedConnection
    requests.HTTPError = RefusedConnection
    for method in ("get", "post", "put", "delete", "head", "request"):
        setattr(requests, method, desktop.refuse_request(method))

    class Session:
        trust_env = True

        def __init__(self):
            for name in ("get", "post", "put", "delete", "head", "request"):
                setattr(self, name, desktop.refuse_request(name))

        def mount(self, *_args):
            return None

        def close(self):
            return None

    requests.Session = Session

    modules["mlx_whisper"].transcribe = desktop.whisper_transcribe
    modules["huggingface_hub"].snapshot_download = desktop.snapshot_download
    modules["tqdm"].tqdm = type("tqdm", (Anything,), {
        "set_lock": classmethod(lambda cls, lock: None)})
    return modules


def _accessibility(desktop: Desktop) -> dict:
    """ApplicationServices, enough to read and write the focused field."""
    system_wide = object()
    names = {
        "kAXFocusedUIElementAttribute": "AXFocusedUIElement",
        "kAXFocusedWindowAttribute": "AXFocusedWindow",
        "kAXValueAttribute": "AXValue",
        "kAXSelectedTextAttribute": "AXSelectedText",
        "kAXSelectedTextRangeAttribute": "AXSelectedTextRange",
        "kAXTitleAttribute": "AXTitle",
        "kAXDocumentAttribute": "AXDocument",
        "kAXRoleAttribute": "AXRole",
        "kAXSubroleAttribute": "AXSubrole",
        "kAXValueCFRangeType": 4,
        "kAXTrustedCheckOptionPrompt": "AXTrustedCheckOptionPrompt",
    }
    no_value = -25212        # kAXErrorNoValue

    def copy_attribute(element, attribute, _out):
        field = desktop.field
        if element is system_wide:
            if attribute == "AXFocusedUIElement" and field is not None:
                return 0, field
            return no_value, None
        if isinstance(element, TextField):
            if attribute == "AXValue":
                return 0, element.text
            if attribute == "AXSelectedTextRange":
                return 0, element.reported_range()
            if attribute == "AXSelectedText":
                start, length = element.selection
                return 0, element.text[start:start + length]
            if attribute == "AXRole":
                return 0, "AXTextField"
            if attribute == "AXSubrole":
                return (0, "AXSecureTextField") if element.secure \
                    else (no_value, None)
        return no_value, None

    def set_attribute(element, attribute, value):
        if isinstance(element, TextField) and attribute == "AXSelectedTextRange":
            location, length = value
            encoded = element.text.encode("utf-16-le", "surrogatepass")
            start = len(encoded[:2 * location].decode("utf-16-le"))
            stop = len(encoded[:2 * (location + length)].decode("utf-16-le"))
            element.selection = (start, stop - start)
            return 0
        return no_value

    def settable(element, attribute, _out):
        return 0, (isinstance(element, TextField)
                   and attribute == "AXSelectedTextRange")

    table = dict(names)
    table.update(
        AXUIElementIsAttributeSettable=settable,
        AXUIElementCreateSystemWide=lambda: system_wide,
        AXUIElementCreateApplication=lambda _pid: object(),
        AXUIElementCopyAttributeValue=copy_attribute,
        AXUIElementSetAttributeValue=set_attribute,
        AXIsProcessTrusted=lambda: desktop.trusted,
        AXIsProcessTrustedWithOptions=lambda _options: desktop.trusted,
        AXValueCreate=lambda _kind, value: value,
        AXValueGetType=lambda _value: 0,
        AXValueGetValue=lambda value, _kind, _out: (True, value),
    )
    return table


class FakeMac:
    """The real dictate module, booted on a fake Mac as a fresh install."""

    def __init__(self):
        self.desktop = Desktop()
        self.directory = Path(tempfile.mkdtemp(prefix="whisper-face-e2e-"))
        self.state = self.directory / "install"
        self.log = io.StringIO()
        self.dictate = None
        self.boot_error = None
        self.boot_seconds = None
        self._saved_modules = {}
        self._saved_environ = {}
        self._saved_spawn = None
        self._saved_stdout = None
        self._lock = None
        self._finished = threading.Condition()
        self._finished_count = 0

    # -- fresh-install state -------------------------------------------------

    def _create_fresh_install(self):
        """Create the private files exactly as setup.sh does: from templates,
        owner-only, never over an existing file."""
        self.state.mkdir(parents=True)
        for template, name in FRESH_INSTALL_TEMPLATES:
            destination = self.state / name
            if not destination.exists():
                shutil.copyfile(ROOT / template, destination)
            destination.chmod(0o600)
        helper = self.state / ".models" / "bin" / "parrot-asr-helper"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b"")
        helper.chmod(0o755)
        (self.directory / "ollama-models").mkdir()

    # -- install and import --------------------------------------------------

    def _install_modules(self):
        for name in list(sys.modules):
            if (name in PLATFORM_MODULES
                    or name.startswith(FIRST_PARTY_PREFIXES)):
                self._saved_modules[name] = sys.modules.pop(name)
        for name in PLATFORM_MODULES:
            self._saved_modules.setdefault(name, None)
        sys.modules.update(_platform_modules(self.desktop))
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

    def _set_environ(self, **values):
        for key, value in values.items():
            self._saved_environ.setdefault(key, os.environ.get(key))
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _import(self):
        saved_argv = sys.argv
        sys.argv = [str(ROOT / "dictate.py")]
        try:
            import dictate
        finally:
            sys.argv = saved_argv
        if Path(dictate.__file__).resolve() != (ROOT / "dictate.py").resolve():
            raise RuntimeError(f"imported {dictate.__file__}, not this checkout")
        return dictate

    def _redirect_state(self, dictate):
        """Point every private file at the fresh install, so the program can
        never read or write the checkout's own state."""
        here = Path(dictate.HERE)
        moved = {}
        for name, value in list(vars(dictate).items()):
            if (isinstance(value, Path) and name.endswith("_FILE")
                    and value.parent == here):
                moved[value] = self.state / value.name
                setattr(dictate, name, moved[value])
        for value in list(vars(dictate).values()):
            function = getattr(value, "__func__", value)
            defaults = getattr(function, "__defaults__", None)
            if isinstance(function, types.FunctionType) and defaults:
                function.__defaults__ = tuple(
                    moved.get(item, item) if isinstance(item, Path) else item
                    for item in defaults)
        helper = self.state / ".models" / "bin" / "parrot-asr-helper"
        dictate.PARAKEET_HELPER = helper
        dictate.PARAKEET.helper = helper
        dictate.PARAKEET.process_factory = self.desktop.spawn_helper
        dictate.AUDIO_RECOVERY.core_audio = self.desktop.core_audio
        dictate.ThreadingHTTPServer = (
            lambda address, handler: PhoneServer(
                self.desktop, address, handler))
        real_activation = dictate.start_gui_activation_server

        def activation_server(gui, **kwargs):
            kwargs.setdefault("root", str(self.directory))
            return real_activation(gui, **kwargs)

        dictate.start_gui_activation_server = activation_server
        real_finish = dictate.finish_in_release_order

        def finish_in_release_order(*args, **kwargs):
            try:
                return real_finish(*args, **kwargs)
            finally:
                with self._finished:
                    self._finished_count += 1
                    self._finished.notify_all()

        dictate.finish_in_release_order = finish_in_release_order
        real_single_instance = dictate.ensure_single_instance

        def ensure_single_instance():
            self._lock = real_single_instance()
            return self._lock

        dictate.ensure_single_instance = ensure_single_instance

    def boot(self):
        """Import the real module and run main() up to the event loop."""
        self._create_fresh_install()
        self._set_environ(
            OLLAMA_MODELS=str(self.directory / "ollama-models"),
            PARROT_ASR_BACKEND=None,
            PARROT_ASR_CROSSCHECK=None,
            WHISPER_FACE_SOURCE_REVISION="0" * 40,
            WHISPER_FACE_PERMISSION_ATTEMPT=None,
        )
        self._saved_spawn = subprocess.Popen
        subprocess.Popen = self.desktop.refuse_spawn
        # The runtime prints its log from many threads for as long as the
        # process lives; keep it for failure messages instead.
        self._saved_stdout = sys.stdout
        sys.stdout = self.log
        self._install_modules()
        dictate = self._import()
        self.dictate = dictate
        self.desktop.installed_models = {dictate.FAST_WHISPER_REPO}
        self._redirect_state(dictate)
        started = time.perf_counter()
        try:
            dictate.main()
        except BaseException as error:  # noqa: BLE001 - reported by a test
            self.boot_error = error
        self.boot_seconds = time.perf_counter() - started
        return self

    # -- driving dictation -------------------------------------------------

    def wait_until(self, predicate, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def warmed_up(self) -> bool:
        return self.dictate.PIPELINE_STATE.get("cleanup_status") not in (
            None, "", "Checking", "Warming up")

    def dictate_sentence(self, spoken: str, *, whisper: str | None = None,
                         field: TextField | None = None, key=None,
                         modifiers=(), audio=None, timeout: float = 10.0):
        """Hold the capture key, speak, release; return the focused field.

        ``spoken`` is what Parakeet hears, in the punctuated, sentence-cased
        form it writes; ``whisper`` is what Whisper Tiny hears (the same by
        default).
        """
        desktop = self.desktop
        dictate = self.dictate
        if field is not None:
            desktop.focus(field)
        desktop.heard = {"parakeet": spoken,
                         "whisper": spoken if whisper is None else whisper}
        key = key or dictate.HOTKEY
        with self._finished:
            finished_before = self._finished_count
        for modifier in modifiers:
            desktop.press(modifier)
        desktop.press(key)
        if not self.wait_until(lambda: desktop.live_streams(), 5.0):
            raise DictationStuck(
                "pressing the capture key opened no microphone stream\n"
                + self.log_tail())
        desktop.speak(speech_audio() if audio is None else audio)
        desktop.release(key)
        for modifier in reversed(modifiers):
            desktop.release(modifier)
        with self._finished:
            done = self._finished.wait_for(
                lambda: self._finished_count > finished_before, timeout)
        if not done:
            raise DictationStuck(
                f"dictation of {spoken!r} did not finish\n" + self.log_tail())
        return desktop.field

    def log_tail(self, lines: int = 40) -> str:
        return "\n".join(self.log.getvalue().splitlines()[-lines:])

    # -- teardown ------------------------------------------------------------

    def close(self):
        if self._saved_spawn is not None:
            subprocess.Popen = self._saved_spawn
        if self._saved_stdout is not None:
            sys.stdout = self._saved_stdout
        for listener in self.desktop.listeners:
            listener.stop()
        for server in self.desktop.servers:
            server.shutdown()
        if self.dictate is not None:
            with contextlib.suppress(Exception):
                self.dictate.PARAKEET._close()
        if self._lock is not None:
            self._lock.close()
        for name, module in self._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name in list(sys.modules):
            if (name.startswith(FIRST_PARTY_PREFIXES)
                    and name not in self._saved_modules):
                sys.modules.pop(name, None)
        for key, value in self._saved_environ.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.directory, ignore_errors=True)


def boot_fresh_install() -> FakeMac:
    return FakeMac().boot()
