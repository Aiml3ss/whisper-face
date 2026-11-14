# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Record once what the real recognizer writes, then replay it forever.

    uv run benchmarks/recognizer_replay.py record --helper <parrot-asr-helper>
    uv run benchmarks/recognizer_replay.py record --helper <...> --wav-dir <dir>
    uv run benchmarks/recognizer_replay.py check

``record`` turns speech into Parakeet transcripts and stores them in
``benchmarks/recognizer_transcripts.json``. Without ``--wav-dir`` each
sentence's "said" text is spoken by the macOS synthesizer (``say``) into a
temporary file; no sound is played. With ``--wav-dir`` the speech is a folder
of a person's own recordings, each with a sidecar ``<name>.txt`` saying what
was meant. Either way the audio is converted to 16 kHz mono and sent to the
helper given by ``--helper`` over the framed protocol the app uses. Recordings
are never committed: the tool refuses a folder inside the checkout and never
copies audio anywhere.

``record`` only ever writes the recognizer's side of a case. "accept" (every
final text a person would accept), "recognizer_error" and "known_failure" are
written by a person and are carried over untouched; when a transcript differs
from the last recording the tool prints the difference so that person can
review them.

``check`` dictates every stored transcript through the real dictate.py booted
as a fresh install on the fake Mac in tests/fake_platform.py, and prints each
sentence whose typed text is not in "accept". tests/test_whole_program.py runs
the same code in the release gate. It needs no audio and no helper, but the
harness runs the runtime's macOS path, so it needs a macOS host.

Synthetic speech shows how the recognizer punctuates, capitalises and formats
numbers. It says nothing about how accurately it hears a person's voice.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import platform
import re
import select
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPTS = ROOT / "benchmarks" / "recognizer_transcripts.json"
SAMPLE_RATE = 16_000
# Silence around each utterance, as a person leaves before and after speaking
# while holding the key.
PAD_SECONDS = 0.3
RECORDING_SUFFIXES = (".wav", ".aif", ".aiff", ".m4a", ".caf")
SYNTHETIC_CAVEAT = (
    "Synthetic speech shows how the recognizer punctuates, capitalises and "
    "formats numbers. It says nothing about how accurately it hears a "
    "person's voice.")
# Written by a person; record never changes them on an existing case.
PERSON_FIELDS = ("accept", "recognizer_error", "known_failure")


# -- the transcripts file ----------------------------------------------------


def load(path: Path = TRANSCRIPTS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(path: Path = TRANSCRIPTS) -> list[dict]:
    return load(path)["cases"]


def dump(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def problems(data: dict) -> list[str]:
    """What makes the file unusable as a gate, one line per defect."""
    found = []
    ids = [case.get("id") for case in data.get("cases", [])]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        found.append(f"{duplicate}: id appears more than once")
    for case in data.get("cases", []):
        name = case.get("id") or "<no id>"
        for field in ("id", "covers", "said", "voice", "recognizer"):
            if not str(case.get(field) or "").strip():
                found.append(f"{name}: no {field}")
        if not isinstance(case.get("transcript"), str):
            found.append(f"{name}: not recorded yet")
        accept = case.get("accept")
        if (not isinstance(accept, list) or not accept
                or not all(isinstance(text, str) for text in accept)):
            found.append(f"{name}: accept must list at least one text")
    return found


def _write(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(dump(data), encoding="utf-8")
    os.replace(temporary, path)


# -- replaying through the program --------------------------------------------


def boot_fake_mac():
    """Boot the real dictate.py as a fresh install on the fake Mac."""
    tests = str(ROOT / "tests")
    if tests not in sys.path:
        sys.path.append(tests)
    import fake_platform

    return fake_platform.boot_fresh_install()


def type_transcript(mac, case: dict) -> str:
    """Dictate one recorded transcript into an empty field; return its text."""
    from fake_platform import TextField

    field = TextField("")
    mac.dictate_sentence(case["transcript"], field=field)
    return field.text


def arrives_as_accepted(case: dict, typed: str) -> bool:
    return typed in case["accept"]


def check(path: Path) -> int:
    data = load(path)
    defects = problems(data)
    if defects:
        print("The transcripts file cannot be replayed:")
        for line in defects:
            print("  " + line)
        return 2
    if sys.platform != "darwin":
        print("check boots the macOS runtime path of dictate.py and needs a "
              "macOS host.")
        return 2
    results = []
    mac = boot_fake_mac()
    try:
        if mac.boot_error is not None:
            raise SystemExit(f"dictate.py did not boot: {mac.boot_error!r}")
        if not mac.wait_until(mac.warmed_up, 15.0):
            raise SystemExit("warm-up did not finish\n" + mac.log_tail())
        for case in data["cases"]:
            results.append((case, type_transcript(mac, case)))
    finally:
        mac.close()

    wrong = fixed = known = 0
    for case, typed in results:
        accepted = arrives_as_accepted(case, typed)
        if case.get("known_failure"):
            if accepted:
                fixed += 1
                print(f"FIXED  {case['id']}: arrives as accepted; remove its "
                      "known_failure")
            else:
                known += 1
            continue
        if accepted:
            continue
        wrong += 1
        print(f"WRONG  {case['id']}")
        print(f"  said:       {case['said']!r}")
        print(f"  recognizer: {case['transcript']!r}")
        print(f"  typed:      {typed!r}")
        for text in case["accept"]:
            print(f"  accept:     {text!r}")
    total = len(results)
    print(f"\n{total - wrong - fixed - known} of {total} arrive as accepted; "
          f"{wrong} wrong, {known} known failures, {fixed} known failures "
          "now fixed.")
    if any(case["voice"] != "recording" for case, _typed in results):
        print(SYNTHETIC_CAVEAT)
    return 1 if wrong or fixed else 0


# -- recording ----------------------------------------------------------------


def to_samples(source: Path, directory: Path) -> np.ndarray:
    """Any audio afconvert reads, as padded 16 kHz mono float32 samples."""
    converted = directory / (source.stem + ".16k.wav")
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1",
         str(source), str(converted)], check=True)
    with wave.open(str(converted), "rb") as handle:
        if handle.getframerate() != SAMPLE_RATE or handle.getnchannels() != 1:
            raise RuntimeError(f"{source.name} did not convert to 16 kHz mono")
        frames = handle.readframes(handle.getnframes())
    converted.unlink()
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    pad = np.zeros(int(PAD_SECONDS * SAMPLE_RATE), dtype=np.float32)
    return np.concatenate([pad, audio, pad])


def synthesize(text: str, directory: Path, index: int,
               voice: str | None) -> np.ndarray:
    aiff = directory / f"say-{index}.aiff"
    command = ["say", "-o", str(aiff)]
    if voice:
        command += ["-v", voice]
    subprocess.run(command + [text], check=True)
    try:
        return to_samples(aiff, directory)
    finally:
        aiff.unlink(missing_ok=True)


class Helper:
    """The Parakeet helper, spoken to the way dictate.py's ParakeetClient does:
    a u64 little-endian sample count, that many float32 samples, one JSON line
    back."""

    def __init__(self, path: Path, timeout: float = 120.0):
        self.timeout = timeout
        self.process = subprocess.Popen(
            [str(path), "--server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.ready = self._line()
        if not self.ready.get("ready"):
            self.close()
            raise RuntimeError(f"the helper did not report ready: {self.ready}")

    def _line(self) -> dict:
        data = bytearray()
        deadline = time.monotonic() + self.timeout
        while not data.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select(
                    [self.process.stdout], [], [], left)[0]:
                raise TimeoutError("the helper did not answer")
            chunk = self.process.stdout.read(1)
            if not chunk:
                raise RuntimeError("the helper exited")
            data.extend(chunk)
        return json.loads(data)

    def transcribe(self, audio: np.ndarray) -> str:
        payload = np.ascontiguousarray(audio, dtype="<f4")
        self.process.stdin.write(
            struct.pack("<Q", len(payload)) + payload.tobytes())
        self.process.stdin.flush()
        reply = self._line()
        if not reply.get("ok"):
            raise RuntimeError(f"the helper failed: {reply.get('error')}")
        return str(reply.get("text", ""))

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def recordings_in(directory: Path) -> list[tuple[Path, str]]:
    """(recording, meant text) for every recording in a folder."""
    pairs = []
    missing = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in RECORDING_SUFFIXES:
            continue
        sidecar = path.with_suffix(".txt")
        if not sidecar.is_file():
            missing.append(sidecar.name)
            continue
        meant = sidecar.read_text(encoding="utf-8").strip()
        if not meant:
            missing.append(sidecar.name + " (empty)")
            continue
        pairs.append((path, meant))
    if missing:
        raise SystemExit(
            "every recording needs a sidecar text file saying what was meant; "
            "missing: " + ", ".join(missing))
    if not pairs:
        raise SystemExit(f"no recordings ({', '.join(RECORDING_SUFFIXES)}) "
                         f"in {directory}")
    return pairs


def recording_id(path: Path) -> str:
    return "recording-" + (
        re.sub(r"[^a-z0-9]+", "-", path.stem.casefold()).strip("-") or "x")


def show_changes(before: dict[str, str | None], cases: list[dict]) -> int:
    changed = 0
    for case in cases:
        old = before.get(case["id"])
        new = case["transcript"]
        if old == new:
            continue
        changed += 1
        if old is None:
            print(f"+ {case['id']}: {new!r} (new; review its accept)")
            continue
        print(f"~ {case['id']} (review accept and recognizer_error)")
        for line in difflib.unified_diff(
                [old], [new], "last recording", "today", lineterm="", n=0):
            if not line.startswith("@@"):
                print("    " + line)
    return changed


def record(path: Path, helper_path: Path, voice: str | None,
           wav_dir: Path | None) -> int:
    helper_path = helper_path.expanduser()
    if not (helper_path.is_file() and os.access(helper_path, os.X_OK)):
        raise SystemExit(f"{helper_path} is not an executable helper")
    if wav_dir is not None:
        wav_dir = wav_dir.expanduser().resolve()
        if wav_dir.is_relative_to(ROOT.resolve()):
            raise SystemExit(
                "recordings are never committed: keep them in a folder "
                f"outside this checkout ({ROOT}), not {wav_dir}")
        if not wav_dir.is_dir():
            raise SystemExit(f"{wav_dir} is not a folder")
    if path.exists():
        try:
            data = load(path)
        except (OSError, ValueError) as error:
            raise SystemExit(
                f"{path} cannot be read ({error}); refusing to replace it")
    else:
        data = {"schema_version": 1, "recordings": {}, "cases": []}
    cases = data["cases"]
    by_id = {case["id"]: case for case in cases}
    kept = {case["id"]: {field: json.dumps(case.get(field))
                         for field in PERSON_FIELDS} for case in cases}
    before = {case["id"]: case.get("transcript") for case in cases}

    with tempfile.TemporaryDirectory(prefix="wf-replay-") as name:
        directory = Path(name)
        if wav_dir is None:
            work = [case for case in cases if case.get("voice") != "recording"]
            voice_name = f"say ({voice or 'system default voice'})"
            audio = [synthesize(case["said"], directory, index, voice)
                     for index, case in enumerate(work)]
        else:
            work = []
            audio = []
            for recording, meant in recordings_in(wav_dir):
                case = by_id.get(recording_id(recording))
                if case is None:
                    case = {"id": recording_id(recording),
                            "covers": "recording", "said": meant,
                            "voice": "recording", "recognizer": "",
                            "transcript": None, "accept": [meant]}
                    cases.append(case)
                    by_id[case["id"]] = case
                    print(f"  {case['id']}: accept seeded from "
                          f"{recording.with_suffix('.txt').name}; review it")
                elif case["said"] != meant:
                    print(f"  {case['id']}: the sidecar now says {meant!r}; "
                          f"the file keeps {case['said']!r} and its accept")
                work.append(case)
                audio.append(to_samples(recording, directory))
            voice_name = "recording"

    started = time.monotonic()
    helper = Helper(helper_path)
    try:
        model = str(helper.ready.get("model") or "unknown")
        print(f"helper ready in {time.monotonic() - started:.1f} s "
              f"({model}); recognising {len(work)} utterances")
        for case, samples in zip(work, audio):
            case["voice"] = voice_name
            case["recognizer"] = model
            case["transcript"] = helper.transcribe(samples)
    finally:
        helper.close()

    for case_id, fields in kept.items():
        for field, value in fields.items():
            if json.dumps(by_id[case_id].get(field)) != value:
                raise AssertionError(f"{case_id}: record changed {field}")
    changed = show_changes(before, work)
    if not changed and len(cases) == len(before):
        print(f"no transcript changed since the last recording; "
              f"{path.name} left as it was")
        return 0
    data.setdefault("recordings", {})["say" if wav_dir is None
                                      else "recording"] = {
        "recognizer": model,
        "helper": helper_path.name + " --server",
        "voice": voice_name,
        "macos": platform.mac_ver()[0],
        "recorded_on": datetime.date.today().isoformat(),
    }
    _write(path, data)
    print(f"{changed} transcript(s) changed; wrote {path}. Run check next.")
    if wav_dir is not None:
        print("The recordings were read, not copied. The transcripts and the "
              "sidecar text are now in the transcripts file; keep that file "
              "out of the repository if what was said is private.")
    else:
        print(SYNTHETIC_CAVEAT)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    record_parser = commands.add_parser(
        "record", help="record transcripts from the real Parakeet helper")
    record_parser.add_argument(
        "--helper", type=Path, required=True,
        help="path to parrot-asr-helper (it is run with --server)")
    record_parser.add_argument(
        "--voice", help="macOS voice for say (default: the system voice)")
    record_parser.add_argument(
        "--wav-dir", type=Path,
        help="a folder of recordings outside the checkout, each with a "
             "sidecar <name>.txt saying what was meant")
    check_parser = commands.add_parser(
        "check", help="replay the transcripts through the program")
    for sub in (record_parser, check_parser):
        sub.add_argument("--transcripts", type=Path, default=TRANSCRIPTS,
                         help="transcripts file (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.transcripts)
    if args.wav_dir is not None and args.voice:
        parser.error("--voice is for synthetic speech, not --wav-dir")
    return record(args.transcripts, args.helper, args.voice, args.wav_dir)


if __name__ == "__main__":
    sys.exit(main())
