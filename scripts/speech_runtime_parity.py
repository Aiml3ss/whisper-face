# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Compare Whisper Face's own speech runtime with the FluidAudio helper.

    uv run scripts/speech_runtime_parity.py --replay
    uv run scripts/speech_runtime_parity.py --corpus DIR [--corpus DIR ...]
    uv run scripts/speech_runtime_parity.py --replay --home SCRATCH_HOME
    uv run scripts/speech_runtime_parity.py --replay --session [--corpus DIR]

Both programs decode every clip: the FluidAudio helper
(``.models/bin/parrot-asr-helper``, the reference) in protocol 1, and the
runtime (``.models/bin/parrot-speech-runtime``, the candidate) in protocol 1
and again in protocol 2. The three are interleaved clip by clip, in rotating
order, so that drift on the Mac falls on all of them alike.

With ``--session`` a fourth process, the runtime in protocol 3, decodes
every clip as dictate.py streams a long take: ``begin``, the audio in
appends of uneven sizes (``SESSION_APPENDS``, which cut every window
boundary at a different place), then ``finish``. Its text and words must
equal the same runtime's protocol 2 answer for the whole clip, for every
clip of every length; it prints how many do, and the time ``finish`` took
(the part of a session decode left for key release) beside the protocol 2
time for the whole clip.

It prints, for clips of 15 seconds or less and for longer ones, how many
transcripts are identical, then every clip whose text differs with both
texts; whether the candidate's protocol 1 answers carry exactly the keys
``ok``, ``processing_s`` and ``text``; whether its protocol 2 text equals its
protocol 1 text; per-request latency; and each process's peak memory. It
exits 1 when a clip of 15 seconds or less differs or a protocol check fails.

Clips:

- ``--replay`` speaks the sentences of ``benchmarks/recognizer_transcripts.json``
  with the macOS synthesizer (``say``, the system voice), as
  ``benchmarks/recognizer_replay.py record`` does. No sound is played.
- ``--corpus DIR`` reads ``<id>.npy`` files (16 kHz mono float32, used as
  they are, in ``manifest.json`` order when there is one) or recordings that
  ``afconvert`` reads (converted to 16 kHz mono and padded with 0.3 s of
  silence each side).

Neither program can reach the network while this runs: each is started under
``sandbox-exec`` with every network operation denied. ``--home DIR`` points
both at a copy of the model files under ``DIR/Library/Application Support/
FluidAudio/Models``; without it they read the installed model, which neither
program changes. Audio stays in memory and in a temporary folder that is
removed afterwards. Nothing is written into the checkout unless ``--out``
names a file there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
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
REFERENCE = ROOT / ".models" / "bin" / "parrot-asr-helper"
CANDIDATE = ROOT / ".models" / "bin" / "parrot-speech-runtime"
TRANSCRIPTS = ROOT / "benchmarks" / "recognizer_transcripts.json"
MODEL_FOLDER = Path("Library") / "Application Support" / "FluidAudio" / \
    "Models" / "parakeet-unified-en-0.6b"
SAMPLE_RATE = 16_000
PAD_SECONDS = 0.3
LONG_SECONDS = 15.0
RECORDING_SUFFIXES = (".wav", ".aif", ".aiff", ".m4a", ".caf")
SANDBOX = Path("/usr/bin/sandbox-exec")
SANDBOX_PROFILE = "(version 1)(allow default)(deny network*)"
TIME = Path("/usr/bin/time")
PROTOCOL_1_KEYS = ["ok", "processing_s", "text"]
# Protocol 3 headers: the operation in the top byte, a count below it
# (native/ParrotASRHelper/PROTOCOL.md, "Protocol 3").
SESSION_BEGIN, SESSION_APPEND, SESSION_FINISH = 1, 2, 3
OPERATION_SHIFT = 56
# Append sizes, used in turn: the app's own (dictate.SPEECH_SESSION_APPEND
# _SAMPLES), one sample, an audio callback's block, and odd sizes that
# land everywhere relative to the 239,360-sample windows.
SESSION_APPENDS = (8_000, 1, 512, 23_999, 160_000, 4_801, 239_361)


class Program:
    """One helper process, spoken to as dictate.py's ParakeetClient does."""

    def __init__(self, label: str, argv: list[str], env: dict,
                 timeout: float = 180.0):
        self.label = label
        self.timeout = timeout
        self.stderr = tempfile.TemporaryFile()
        self.buffer = bytearray()
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, bufsize=0, env=env)
        self.ready = self._line()
        if not self.ready.get("ready"):
            self.close()
            raise SystemExit(f"{label} did not report ready: {self.ready}")

    def _line(self) -> dict:
        """One JSON line, read in blocks as dictate.py's client reads."""
        deadline = time.monotonic() + self.timeout
        output = self.process.stdout.fileno()
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([output], [], [], left)[0]:
                raise SystemExit(f"{self.label} did not answer in time")
            chunk = os.read(output, 65536)
            if not chunk:
                raise SystemExit(f"{self.label} exited "
                                 f"({self.process.poll()})")
            self.buffer.extend(chunk)
        end = self.buffer.index(b"\n")
        line = bytes(self.buffer[:end])
        del self.buffer[:end + 1]
        return json.loads(line)

    def request(self, audio: np.ndarray) -> tuple[dict, float]:
        payload = np.ascontiguousarray(audio, dtype="<f4")
        started = time.perf_counter()
        self.process.stdin.write(struct.pack("<Q", len(payload)))
        self.process.stdin.write(payload.tobytes())
        self.process.stdin.flush()
        reply = self._line()
        return reply, time.perf_counter() - started

    def _operation(self, operation: int, payload: bytes = b"",
                   count: int = 0) -> None:
        self.process.stdin.write(struct.pack(
            "<Q", (operation << OPERATION_SHIFT) | count))
        if payload:
            self.process.stdin.write(payload)
        self.process.stdin.flush()

    def session(self, audio: np.ndarray) -> tuple[dict, float]:
        """Decode ``audio`` as a protocol 3 session; the answer to finish
        and the round trip of the finish alone."""
        payload = np.ascontiguousarray(audio, dtype="<f4")
        self._operation(SESSION_BEGIN)
        offset, turn = 0, 0
        while offset < len(payload):
            size = SESSION_APPENDS[turn % len(SESSION_APPENDS)]
            part = payload[offset:offset + size]
            self._operation(SESSION_APPEND, part.tobytes(), len(part))
            offset += len(part)
            turn += 1
        started = time.perf_counter()
        self._operation(SESSION_FINISH)
        reply = self._line()
        return reply, time.perf_counter() - started

    def close(self) -> dict:
        """End the process; its peak memory in bytes, when measured."""
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.stderr.seek(0)
        text = self.stderr.read().decode("utf-8", "replace")
        self.stderr.close()
        memory = {}
        for key, label in (("max_rss", "maximum resident set size"),
                           ("peak_footprint", "peak memory footprint")):
            match = re.search(rf"^\s*(\d+)\s+{label}\s*$", text, re.MULTILINE)
            if match:
                memory[key] = int(match.group(1))
        return memory


def launch(label: str, program: Path, arguments: list[str], home: Path | None,
           sandbox: bool) -> Program:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FLUID_")}
    if home is not None:
        env["CFFIXED_USER_HOME"] = str(home)
    argv = [str(program), *arguments]
    if sandbox:
        argv = [str(SANDBOX), "-p", SANDBOX_PROFILE, *argv]
    if TIME.is_file():
        argv = [str(TIME), "-l", *argv]
    return Program(label, argv, env)


def to_samples(source: Path, directory: Path) -> np.ndarray:
    converted = directory / (source.stem + ".16k.wav")
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1",
         str(source), str(converted)], check=True)
    with wave.open(str(converted), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    converted.unlink()
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    pad = np.zeros(int(PAD_SECONDS * SAMPLE_RATE), dtype=np.float32)
    return np.concatenate([pad, audio, pad])


def replay_clips(directory: Path) -> list[tuple[str, np.ndarray]]:
    data = json.loads(TRANSCRIPTS.read_text(encoding="utf-8"))
    clips = []
    for index, case in enumerate(data["cases"]):
        if "said" not in case:
            continue
        aiff = directory / f"say-{index}.aiff"
        subprocess.run(["say", "-o", str(aiff), case["said"]], check=True)
        try:
            clips.append((f"replay:{case['id']}", to_samples(aiff, directory)))
        finally:
            aiff.unlink(missing_ok=True)
    return clips


def corpus_clips(folder: Path, directory: Path) -> list[tuple[str, np.ndarray]]:
    manifest = folder / "manifest.json"
    if manifest.is_file():
        names = [f"{item['id']}.npy"
                 for item in json.loads(manifest.read_text(encoding="utf-8"))]
    else:
        names = sorted(path.name for path in folder.iterdir())
    clips = []
    for name in names:
        path = folder / name
        if path.suffix == ".npy":
            audio = np.load(path).astype(np.float32)
        elif path.suffix.lower() in RECORDING_SUFFIXES:
            audio = to_samples(path, directory)
        else:
            continue
        clips.append((f"{folder.name}:{path.stem}", audio))
    return clips


def source_digest(package: Path = ROOT / "native" / "ParrotSpeechRuntime") -> str:
    """The SHA-256 of everything compiled into the runtime.

    Package.swift and every Swift source, by path, with line endings made
    LF so a Windows checkout hashes the same. native/ParrotSpeechRuntime/
    README.md records the digest its parity run built, and
    tests/test_dictate.py fails when the sources no longer match it.
    """
    files = [package / "Package.swift", *(package / "Sources").rglob("*.swift")]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(package).as_posix()):
        digest.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def percentile(values: list[float], point: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil((len(ordered) - 1) * point)] if ordered else math.nan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=Path, default=REFERENCE,
                        help="the FluidAudio helper (default: %(default)s)")
    parser.add_argument("--candidate", type=Path, default=CANDIDATE,
                        help="the speech runtime (default: %(default)s)")
    parser.add_argument("--home", type=Path,
                        help="a home folder holding a copy of the model files")
    parser.add_argument("--models", type=Path,
                        help="the model folder given to the candidate "
                             "(default: the one under --home, or the installed one)")
    parser.add_argument("--replay", action="store_true",
                        help="speak the recognizer replay sentences with say")
    parser.add_argument("--corpus", type=Path, action="append", default=[],
                        help="a folder of .npy clips or recordings")
    parser.add_argument("--out", type=Path,
                        help="write one JSON line per clip here")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="run the programs without sandbox-exec")
    parser.add_argument("--session", action="store_true",
                        help="also decode every clip as a protocol 3 session")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("this compares macOS programs; run it on a Mac")
    if not args.replay and not args.corpus:
        parser.error("give --replay, --corpus DIR, or both")
    for program in (args.reference, args.candidate):
        if not program.is_file():
            parser.error(f"{program} is not built")
    home = args.home.expanduser().resolve() if args.home else None
    models = args.models or ((home or Path.home()) / MODEL_FOLDER)
    if not models.is_dir():
        parser.error(f"no model folder at {models}")
    sandbox = not args.no_sandbox and SANDBOX.is_file()
    if not sandbox:
        print("! running without a network sandbox")

    with tempfile.TemporaryDirectory(prefix="speech-runtime-parity-") as tmp:
        directory = Path(tmp)
        clips: list[tuple[str, np.ndarray]] = []
        if args.replay:
            clips += replay_clips(directory)
        for folder in args.corpus:
            clips += corpus_clips(folder.expanduser().resolve(), directory)
    if not clips:
        parser.error("no clips found")

    model_args = ["--models", str(models)]
    programs = {
        "reference": launch("reference", args.reference, ["--server"],
                            home, sandbox),
        "candidate-1": launch("candidate-1", args.candidate,
                              ["--server", *model_args], home, sandbox),
        "candidate-2": launch("candidate-2", args.candidate,
                              ["--server", "--protocol", "2", *model_args],
                              home, sandbox),
    }
    session = (launch("candidate-3", args.candidate,
                      ["--server", "--protocol", "3", *model_args],
                      home, sandbox) if args.session else None)
    # The candidate must be built from this checkout (setup.sh builds it
    # from here): this is the digest the README records beside the result.
    print(f"sources: sha256:{source_digest()}")
    for name, program in programs.items():
        print(f"{name}: {program.ready}")
    order = list(programs)
    # Warm every program on the first clip before anything is timed.
    for program in programs.values():
        for _ in range(3):
            program.request(clips[0][1])
    if session is not None:
        print(f"candidate-3: {session.ready}")
        for _ in range(3):
            session.session(clips[0][1])

    records = []
    for index, (clip_id, audio) in enumerate(clips):
        record = {"id": clip_id, "seconds": round(len(audio) / SAMPLE_RATE, 3)}
        for name in order[index % 3:] + order[:index % 3]:
            reply, seconds = programs[name].request(audio)
            record[name] = {
                "ok": reply.get("ok"), "text": reply.get("text"),
                "keys": sorted(reply), "processing_s": reply.get("processing_s"),
                "round_trip_s": round(seconds, 6),
                "words": len(reply.get("words") or []),
            }
            if name == "candidate-2":
                batch = reply
        if session is not None:
            reply, seconds = session.session(audio)
            record["candidate-3"] = {
                "ok": reply.get("ok"), "text": reply.get("text"),
                "processing_s": reply.get("processing_s"),
                "round_trip_s": round(seconds, 6),
                "samples": reply.get("samples"),
                # Text, words and mean confidence, against protocol 2.
                "same": (reply.get("ok") is True and batch.get("ok") is True
                         and reply.get("samples") == len(audio)
                         and all(reply.get(key) == batch.get(key)
                                 for key in ("text", "words",
                                             "avg_confidence"))),
            }
        records.append(record)
    memory = {name: program.close() for name, program in programs.items()}
    if session is not None:
        memory["candidate-3"] = session.close()

    if args.out:
        with args.out.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def same(record: dict) -> bool:
        return (record["reference"]["ok"] is True
                and record["candidate-1"]["ok"] is True
                and record["reference"]["text"] == record["candidate-1"]["text"])

    short = [r for r in records if r["seconds"] <= LONG_SECONDS]
    long = [r for r in records if r["seconds"] > LONG_SECONDS]
    total_seconds = sum(r["seconds"] for r in records)
    keys_ok = sum(r["candidate-1"]["keys"] == PROTOCOL_1_KEYS for r in records)
    protocols_agree = sum(
        r["candidate-2"]["ok"] is True
        and r["candidate-2"]["text"] == r["candidate-1"]["text"] for r in records)
    with_words = sum(r["candidate-2"]["words"] > 0 for r in records)
    print(f"clips: {len(records)} ({len(short)} of {LONG_SECONDS:g} s or less, "
          f"{len(long)} longer), {total_seconds:.0f} s of audio")
    print(f"{LONG_SECONDS:g} s or less: identical {sum(map(same, short))} of {len(short)}")
    print(f"longer than {LONG_SECONDS:g} s: identical {sum(map(same, long))} of {len(long)}")
    print(f"candidate protocol 1 answers with exactly {', '.join(PROTOCOL_1_KEYS)}: "
          f"{keys_ok} of {len(records)}")
    print(f"candidate protocol 2 text equal to its protocol 1 text: "
          f"{protocols_agree} of {len(records)}; with words: {with_words}")
    differences = [r for r in records if not same(r)]
    print(f"differences: {len(differences)}")
    for record in differences:
        print(f"  {record['id']} ({record['seconds']} s)")
        print(f"    reference: {record['reference']['text']!r}")
        print(f"    candidate: {record['candidate-1']['text']!r}")

    sessions_same = None
    if session is not None:
        sessions_same = sum(r["candidate-3"]["same"] for r in records)
        print(f"candidate protocol 3 session text and words equal to its "
              f"protocol 2 answer: {sessions_same} of {len(records)} "
              f"({sum(r['candidate-3']['same'] for r in short)} of {len(short)} "
              f"of {LONG_SECONDS:g} s or less, "
              f"{sum(r['candidate-3']['same'] for r in long)} of {len(long)} "
              f"longer)")
        for record in records:
            if not record["candidate-3"]["same"]:
                print(f"  session differs: {record['id']} ({record['seconds']} s)")
        print("session finish against the whole clip in protocol 2, "
              "processing_s, ms:")
        for label, lo, hi in (("15 s or less", 0, LONG_SECONDS),
                              ("15 to 30 s", LONG_SECONDS, 30),
                              ("30 to 120 s", 30, 120),
                              ("over 120 s", 120, math.inf)):
            group = [r for r in records if lo < r["seconds"] <= hi
                     or (lo == 0 and r["seconds"] == 0)]
            if not group:
                continue
            finish = [1000 * float(r["candidate-3"]["processing_s"] or 0)
                      for r in group]
            whole = [1000 * float(r["candidate-2"]["processing_s"] or 0)
                     for r in group]
            print(f"  {label:13s} ({len(group):3d} clips) finish p50 "
                  f"{percentile(finish, .5):7.2f} p95 {percentile(finish, .95):7.2f} "
                  f"max {max(finish):7.2f}   whole p50 {percentile(whole, .5):7.2f} "
                  f"p95 {percentile(whole, .95):7.2f} max {max(whole):7.2f}")

    print("latency, ms (processing_s / round trip):")
    for name in order:
        processing = [1000 * float(r[name]["processing_s"] or 0) for r in records]
        trip = [1000 * r[name]["round_trip_s"] for r in records]
        print(f"  {name:12s} p50 {percentile(processing, .5):7.2f} / "
              f"{percentile(trip, .5):7.2f}   p95 {percentile(processing, .95):7.2f} / "
              f"{percentile(trip, .95):7.2f}")
    for name in ("candidate-1", "candidate-2"):
        paired = [1000 * (r[name]["round_trip_s"] - r["reference"]["round_trip_s"])
                  for r in records]
        print(f"  paired {name} minus reference, round trip: "
              f"p50 {percentile(paired, .5):+.2f}  p95 {percentile(paired, .95):+.2f}")
    print("peak memory, MiB (maximum resident set size / peak footprint):")
    for name in [*order, *(["candidate-3"] if session is not None else [])]:
        values = memory.get(name) or {}
        rss = values.get("max_rss")
        footprint = values.get("peak_footprint")
        print(f"  {name:12s} "
              f"{rss / 2**20 if rss else math.nan:7.1f} / "
              f"{footprint / 2**20 if footprint else math.nan:7.1f}")

    failed = (sum(map(same, short)) != len(short)
              or keys_ok != len(records) or protocols_agree != len(records)
              or (sessions_same is not None and sessions_same != len(records)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
