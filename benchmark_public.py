# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""The public benchmark: one command that anyone with a Mac can rerun.

    uv run benchmark_public.py check
    uv run benchmark_public.py run --runtime <parrot-speech-runtime> \\
        --models <parakeet-unified-en-0.6b folder> --out <folder outside the checkout>

``check`` validates ``benchmarks/public_benchmark_corpus.json`` and prints
what it covers. It needs only a clone.

``run`` speaks every take of the corpus with a macOS system voice (``say``,
16 kHz mono, no sound is played), then measures Whisper Face twice:

1. **The runtime alone, in process.** Each take is sent whole to Whisper
   Face's own speech runtime exactly as the app's client frames it, under
   ``sandbox-exec`` with every network operation denied. This gives word
   error rate, name and term recall, false insertions, formatting, and
   recognition latency per take length, with ``--repeats`` timed passes in
   an order fixed by the corpus seed.
2. **The app path, on the fake Mac.** The real ``dictate.py`` is booted in
   this process on ``tests/fake_platform.py`` (the harness the release gate
   uses), with the same runtime binary as its speech program. Each take is
   fed through the fake microphone at real time while the capture key is
   held, so the app cuts, decodes, compiles and pastes it as it would for a
   person, into a fake text field. This is run once on a fresh install
   (``defaults``) and once with the corpus glossary given to the app's own
   dictionary (``dictionary``).

Numbers the outside probe must supply (release to text on screen in a real
app, paste into real apps, bytes sent) are written as ``null`` with the
status ``needs-outside-probe``; nothing here estimates them.

    uv run benchmark_public.py attach-probe --out <folder of a finished run>

fills release to text on screen from ``<out>/outside_probe.json``, which
``outside_probe.py`` writes, after checking that it timed the same app
revision on the same Mac with the same audio.

The result goes to ``<out>/result.json`` and the human page, rendered by
``public_scorecard.py benchmark``, to ``<out>/scorecard.md``. The audio stays
in ``<out>/audio``; ``--out`` may not be inside the checkout, so audio is
never committed. docs/benchmarks/public-benchmark.md is the method.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


HERE = Path(__file__).resolve().parent
CORPUS = HERE / "benchmarks" / "public_benchmark_corpus.json"
RIVALS = HERE / "benchmarks" / "public_benchmark_rivals.json"
RUNTIME_SOURCES = HERE / "native" / "ParrotSpeechRuntime"

SCHEMA_VERSION = 1
RESULT_KIND = "whisper-face/public-benchmark-result"
CORPUS_ID = "whisper-face-public-v1"
PRODUCT_ID = "whisper-face"
CATEGORIES = ("names", "terms", "near_miss", "numbers", "everyday", "long")
# Voices that ship with an English macOS install, one per accent: US, UK,
# Australia, Ireland, India, South Africa.
VOICES = ("Samantha", "Daniel", "Karen", "Moira", "Rishi", "Tessa")
SAMPLE_RATE = 16_000
SANDBOX = Path("/usr/bin/sandbox-exec")
SANDBOX_PROFILE = "(version 1)(allow default)(deny network*)"
DEFAULT_REPEATS = 3
MAX_RESPONSE_BYTES = 1 << 20
# Take lengths the latency is reported for. The runtime decodes in 15 s
# windows, so the last bucket is every take that needs more than one.
LENGTH_BUCKETS = (("up-to-5s", 0.0, 5.0), ("5-to-15s", 5.0, 15.0),
                  ("over-15s", 15.0, math.inf))
CONFIGURATIONS = ("defaults", "dictionary")
# How each block of numbers was measured. Nothing else may appear.
RUNTIME_IN_PROCESS = "runtime-in-process"
APP_ON_FAKE_MAC = "app-on-fake-mac"
NEEDS_OUTSIDE_PROBE = "needs-outside-probe"
# The third class: measured by outside_probe.py, from outside the app, in a
# real window. Only the metrics in OUTSIDE_PROBE_MEASURES may carry it.
OUTSIDE_PROBE = "outside-probe"
PROBE_RESULT_KIND = "whisper-face/outside-probe-result"
PROBE_RESULT_FILE = "outside_probe.json"
PROBE_COMMAND = "uv run outside_probe.py run"
PROBE_TAKE_STATES = ("text", "no-text")
# What only the outside probe can measure. A result carries each as null.
OUTSIDE_PROBE_METRICS = {
    "release_to_text_on_screen_ms": (
        "Key release to the text visible in a real app, timed outside the "
        "app; the fake Mac has no window server and no real paste."),
    "delivery_into_real_apps": (
        "Whether the text lands in real fields (focus moves, fields that "
        "reject paste, secure fields, slow web fields)."),
    "bytes_sent_per_dictation": (
        "Per-process network counters while dictating; this run denies the "
        "network to the runtime instead of counting."),
    "idle_memory_mb": "System tools on the installed app between dictations.",
    "time_to_first_dictation_s": "A stopwatch protocol from a fresh download.",
}
# What the outside probe measures today; the rest stay null until a tool for
# them exists.
OUTSIDE_PROBE_MEASURES = ("release_to_text_on_screen_ms",)


class BenchmarkError(ValueError):
    """The corpus, a result or the run's inputs break a benchmark rule."""


# -- the corpus ---------------------------------------------------------------


def load_corpus(path: Path = CORPUS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_digest(payload: Any) -> str:
    """The SHA-256 of a JSON value, independent of its file's formatting."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def term_hits(text: str, term: str) -> int:
    """Whole-word occurrences of a term, ignoring case."""
    pattern = r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)"
    return len(re.findall(pattern, text.casefold()))


def target_written(text: str, alternatives: Sequence[str]) -> bool:
    """Whether one acceptable written form appears exactly, not inside a
    longer number or word."""
    for form in alternatives:
        start = text.find(form)
        while start >= 0:
            before = text[start - 1] if start else " "
            end = start + len(form)
            after = text[end] if end < len(text) else " "
            if not before.isalnum() and not after.isalnum():
                return True
            start = text.find(form, start + 1)
    return False


def corpus_problems(corpus: Mapping[str, Any]) -> list[str]:
    """Every way the corpus breaks its own rules, one line each."""
    found: list[str] = []
    expected_keys = {
        "schema_version", "corpus_id", "description", "licence",
        "synthetic_speech", "privacy", "seed", "speech", "glossary", "takes"}
    if set(corpus) != expected_keys:
        return [f"corpus keys must be exactly {sorted(expected_keys)}"]
    if corpus["schema_version"] != 1 or corpus["corpus_id"] != CORPUS_ID:
        found.append("unsupported corpus schema or identity")
    licence = corpus["licence"]
    if (not isinstance(licence, Mapping)
            or not str(licence.get("text", "")).startswith("AGPL-3.0-only")
            or "Not distributed" not in str(licence.get("audio", ""))):
        found.append("the licence must name the text licence and say the "
                     "audio is not distributed")
    if corpus["synthetic_speech"] is not True:
        found.append("the corpus must declare its speech synthetic")
    if "no owner data" not in str(corpus["privacy"]).casefold():
        found.append("the privacy statement must say there is no owner data")
    if (isinstance(corpus["seed"], bool) or not isinstance(corpus["seed"], int)
            or corpus["seed"] <= 0):
        found.append("the seed must be a fixed positive integer")
    speech = corpus["speech"]
    if (not isinstance(speech, Mapping)
            or speech.get("synthesizer") != "say"
            or tuple(speech.get("voices") or ()) != VOICES
            or speech.get("sample_rate") != SAMPLE_RATE
            or speech.get("rate_words_per_minute") != 180
            or speech.get("pad_seconds") != 0.3):
        found.append("speech settings must be the fixed say voices, rate "
                     "180, 16 kHz and 0.3 s of padding")
    glossary = corpus["glossary"]
    terms = [entry.get("term") for entry in glossary
             if isinstance(entry, Mapping)]
    if (len(terms) != len(glossary) or len(set(map(str.casefold, terms)))
            != len(terms)):
        found.append("glossary terms must be unique objects")
    takes = corpus["takes"]
    ids = [take.get("id") for take in takes]
    if len(set(ids)) != len(ids):
        found.append("take ids must be unique")
    used_terms: set[str] = set()
    for take in takes:
        name = take.get("id") or "<no id>"
        allowed = {"id", "category", "text", "said", "terms",
                   "format_targets", "near_miss_of", "source"}
        if not set(take) <= allowed or not {
                "id", "category", "text", "terms", "format_targets",
                "source"} <= set(take):
            found.append(f"{name}: unexpected or missing keys")
            continue
        if take["category"] not in CATEGORIES:
            found.append(f"{name}: unknown category {take['category']!r}")
        text = take["text"]
        if not isinstance(text, str) or not text.strip():
            found.append(f"{name}: no text")
            continue
        for term in take["terms"]:
            if term not in terms:
                found.append(f"{name}: {term!r} is not in the glossary")
            elif not term_hits(text, term):
                found.append(f"{name}: {term!r} is not in its text")
            used_terms.add(term)
        undeclared = [term for term in terms
                      if term_hits(text, term) and term not in take["terms"]]
        if undeclared:
            found.append(f"{name}: names glossary terms it does not declare: "
                         f"{undeclared}")
        if take["category"] == "near_miss":
            if take["terms"] or take.get("near_miss_of") not in terms:
                found.append(f"{name}: a near miss names no glossary term "
                             "and says which one it sounds like")
        elif "near_miss_of" in take:
            found.append(f"{name}: only a near miss names a look-alike")
        for alternatives in take["format_targets"]:
            if (not alternatives
                    or not target_written(text, alternatives[:1])):
                found.append(f"{name}: format target {alternatives!r} must "
                             "list the written form in its text first")
        if take["category"] == "numbers" and not take["format_targets"]:
            found.append(f"{name}: a numbers take needs format targets")
        if take["category"] == "long" and len(text.split()) < 60:
            found.append(f"{name}: a long take needs at least 60 words")
        said = take.get("said")
        if said is not None and (not isinstance(said, str) or not said.strip()
                                 or said == text):
            found.append(f"{name}: said must differ from text when given")
        source = take["source"]
        if source != "written-for-this-corpus" and not re.fullmatch(
                r"benchmarks/[a-z_]+\.json#[a-z0-9-]+", str(source)):
            found.append(f"{name}: unknown source {source!r}")
    unused = [term for term in terms if term not in used_terms]
    if unused:
        found.append(f"glossary terms no take says: {unused}")
    return found


def voice_for_takes(corpus: Mapping[str, Any]) -> dict[str, str]:
    """Each take's voice: a balanced assignment, shuffled by the seed."""
    takes = corpus["takes"]
    voices = list(corpus["speech"]["voices"])
    pool = (voices * (len(takes) // len(voices) + 1))[:len(takes)]
    random.Random(corpus["seed"]).shuffle(pool)
    return {take["id"]: voice for take, voice in zip(takes, pool)}


def run_order(ids: Sequence[str], seed: int, repeat: int | str) -> list[str]:
    """The order of one pass, fixed by the seed and the pass's name."""
    order = list(ids)
    random.Random(f"{seed}:{repeat}").shuffle(order)
    return order


# -- scoring --------------------------------------------------------------------


def normalized_words(text: str) -> list[str]:
    """Words for the word error rate: case, punctuation and number grouping
    do not count; formatting accuracy scores those separately."""
    text = (text.replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"').casefold())
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    text = text.replace("%", " percent ")
    text = re.sub(r"(?<=[a-z])\.(?=[a-z])", "", text)
    return re.findall(r"[a-z0-9]+(?:['.:][a-z0-9]+)*", text)


def _rate(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 2) if whole else None


def score_takes(records: Sequence[Mapping[str, Any]],
                corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Score hypotheses against the corpus. Each record has ``id`` and
    ``hypothesis``; the corpus supplies the reference."""
    from benchmark_asr import score_formatting_records, score_records

    by_id = {take["id"]: take for take in corpus["takes"]}
    glossary = [entry["term"] for entry in corpus["glossary"]]
    rows = []
    expected_terms = recalled_terms = 0
    false_insertions = near_miss_insertions = 0
    targets = written = 0
    misses: list[dict[str, Any]] = []
    insertions: list[dict[str, Any]] = []
    for record in records:
        take = by_id[record["id"]]
        reference = take["text"]
        hypothesis = str(record.get("hypothesis") or "")
        rows.append({"ref": reference, "hyp": hypothesis,
                     "category": take["category"]})
        for term in take["terms"]:
            wanted = term_hits(reference, term)
            got = min(term_hits(hypothesis, term), wanted)
            expected_terms += wanted
            recalled_terms += got
            if got < wanted:
                misses.append({"id": take["id"], "term": term,
                               "hypothesis": hypothesis})
        for term in glossary:
            extra = term_hits(hypothesis, term) - term_hits(reference, term)
            if extra > 0:
                false_insertions += extra
                if take["category"] == "near_miss":
                    near_miss_insertions += extra
                insertions.append({"id": take["id"], "term": term,
                                   "hypothesis": hypothesis})
        for alternatives in take["format_targets"]:
            targets += 1
            written += target_written(hypothesis, alternatives)
    words = score_records(rows, tokenize=normalized_words)
    by_category = {}
    for category in CATEGORIES:
        subset = [row for row in rows if row["category"] == category]
        if subset:
            scored = score_records(subset, tokenize=normalized_words)
            by_category[category] = {
                "takes": len(subset), "wer_pct": scored["wer_pct"],
                "exact_pct": scored["exact_pct"]}
    formatting = score_formatting_records(rows)
    near_miss_takes = sum(by_id[r["id"]]["category"] == "near_miss"
                          for r in records)
    return {
        "takes": len(rows),
        "wer_pct": words["wer_pct"],
        "exact_pct": words["exact_pct"],
        "utterance_p90_wer_pct": words["utterance_p90_wer_pct"],
        "by_category": by_category,
        "terms": {
            "expected": expected_terms,
            "recalled": recalled_terms,
            "recall_pct": _rate(recalled_terms, expected_terms),
            "false_insertions": false_insertions,
            "false_insertions_in_near_misses": near_miss_insertions,
            "near_miss_takes": near_miss_takes,
            "missed": misses,
            "inserted": insertions,
        },
        "formatting": {
            "targets": targets,
            "written_exactly": written,
            "accuracy_pct": _rate(written, targets),
            "punctuation_and_case": formatting,
        },
    }


def latency_summary(seconds_by_take: Mapping[str, float],
                    milliseconds_by_take: Mapping[str, float]) -> dict:
    """p50, p95 and max per take-length bucket and over all takes."""
    from benchmark_asr import percentile

    def summary(values: list[float]) -> dict[str, Any]:
        return {
            "takes": len(values),
            "p50": round(percentile(values, 0.5), 2) if values else None,
            "p95": round(percentile(values, 0.95), 2) if values else None,
            "max": round(max(values), 2) if values else None,
        }

    buckets = {}
    for name, low, high in LENGTH_BUCKETS:
        buckets[name] = summary([
            milliseconds_by_take[take_id]
            for take_id, seconds in seconds_by_take.items()
            if take_id in milliseconds_by_take and low < seconds <= high])
    buckets["all"] = summary([
        milliseconds_by_take[take_id] for take_id in seconds_by_take
        if take_id in milliseconds_by_take])
    return buckets


# -- audio ----------------------------------------------------------------------


def synthesize(take: Mapping[str, Any], voice: str, speech: Mapping[str, Any],
               directory: Path) -> tuple[Path, str]:
    """Speak one take into a 16 kHz mono WAV; return it and its PCM hash."""
    path = directory / f"{take['id']}.wav"
    subprocess.run(
        ["say", "-v", voice, "-r", str(speech["rate_words_per_minute"]),
         "--file-format=WAVE",
         f"--data-format=LEI16@{speech['sample_rate']}",
         "-o", str(path), take.get("said", take["text"])],
        check=True)
    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate() != SAMPLE_RATE or handle.getnchannels() != 1
                or handle.getsampwidth() != 2):
            raise BenchmarkError(f"{path.name} is not 16 kHz mono 16-bit")
        frames = handle.readframes(handle.getnframes())
    return path, hashlib.sha256(frames).hexdigest()


def padded_samples(path: Path, pad_seconds: float) -> array:
    """The take as float32 samples with silence either side, as a person
    leaves before and after speaking while holding the key."""
    with wave.open(str(path), "rb") as handle:
        pcm = array("h", handle.readframes(handle.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    pad = [0.0] * int(round(pad_seconds * SAMPLE_RATE))
    return array("f", pad + [value / 32768.0 for value in pcm] + pad)


# -- the runtime alone ------------------------------------------------------------


class RuntimeProcess:
    """parrot-speech-runtime in protocol 2, spoken to as the app's client
    speaks to it, with the network denied."""

    def __init__(self, runtime: Path, models: Path):
        from benchmark_asr import BoundedJSONLineReader

        argv = [str(runtime), "--server", "--protocol", "2",
                "--models", str(models)]
        if SANDBOX.is_file():
            argv = [str(SANDBOX), "-p", SANDBOX_PROFILE, *argv]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("FLUID_")}
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, env=environment)
        self.reader = BoundedJSONLineReader(
            self.process.stdout, maximum_bytes=MAX_RESPONSE_BYTES)
        self.ready = self.reader.read(timeout=300)
        if not self.ready.get("ready"):
            self.close()
            raise BenchmarkError(f"the runtime did not start: {self.ready}")

    def transcribe(self, samples: array) -> tuple[dict[str, Any], float]:
        payload = samples.tobytes() if sys.byteorder == "little" else (
            _swapped(samples))
        started = time.perf_counter()
        self.process.stdin.write(len(samples).to_bytes(8, "little"))
        self.process.stdin.write(payload)
        self.process.stdin.flush()
        reply = self.reader.read(timeout=120)
        elapsed = time.perf_counter() - started
        if not reply.get("ok"):
            raise BenchmarkError(f"the runtime failed: {reply.get('error')}")
        return reply, elapsed

    def close(self) -> None:
        from benchmark_asr import _cleanup_helper_process

        _cleanup_helper_process(self.process)


def _swapped(samples: array) -> bytes:
    copy = array("f", samples)
    copy.byteswap()
    return copy.tobytes()


def measure_runtime(corpus: Mapping[str, Any], audio: Mapping[str, array],
                    runtime: Path, models: Path, repeats: int,
                    log: Callable[[str], None]) -> dict[str, Any]:
    ids = [take["id"] for take in corpus["takes"]]
    program = RuntimeProcess(runtime, models)
    texts: dict[str, str] = {}
    words: dict[str, int] = {}
    timings: dict[str, list[float]] = {take_id: [] for take_id in ids}
    processing: dict[str, list[float]] = {take_id: [] for take_id in ids}
    try:
        # One untimed decode warms the model before anything is timed.
        program.transcribe(audio[ids[0]])
        for repeat in range(repeats):
            for take_id in run_order(ids, corpus["seed"], repeat):
                reply, elapsed = program.transcribe(audio[take_id])
                text = str(reply.get("text", "")).strip()
                if take_id in texts and texts[take_id] != text:
                    raise BenchmarkError(
                        f"{take_id}: the runtime wrote different text on "
                        "a repeat; the result would not be reproducible")
                texts[take_id] = text
                words[take_id] = len(reply.get("words") or [])
                timings[take_id].append(1000 * elapsed)
                processing[take_id].append(
                    1000 * float(reply.get("processing_s") or 0.0))
            log(f"runtime pass {repeat + 1} of {repeats} done")
    finally:
        program.close()
    return {"ready": program.ready, "texts": texts, "words": words,
            "round_trip_ms": timings, "processing_ms": processing}


# -- the app path on the fake Mac ------------------------------------------------


class AppPath:
    """The real dictate.py booted on tests/fake_platform.py.

    ``process_factory`` starts the speech program the app asks for. The run
    passes one that starts the real runtime under the network sandbox;
    without one, the fake Mac's own stand-in answers (the gate's tests).
    """

    def __init__(self, configuration: str, glossary: Sequence[str], *,
                 models: Path | None = None,
                 process_factory: Callable[..., Any] | None = None,
                 realtime: bool = True):
        if configuration not in CONFIGURATIONS:
            raise BenchmarkError(f"unknown configuration {configuration!r}")
        tests = str(HERE / "tests")
        if tests not in sys.path:
            sys.path.append(tests)
        import fake_platform

        self.fake_platform = fake_platform
        self.configuration = configuration
        self.realtime = realtime
        self.mac = fake_platform.FakeMac().boot()
        if self.mac.boot_error is not None:
            error = self.mac.boot_error
            self.mac.close()
            raise BenchmarkError(f"dictate.py did not boot: {error!r}")
        dictate = self.mac.dictate
        if models is not None:
            dictate.OWN_PARAKEET.models = Path(models)
        if process_factory is not None:
            dictate.OWN_PARAKEET.process_factory = process_factory
        dictate.set_new_speech_runtime(True)
        if configuration == "dictionary":
            dictate.save_gui_vocabulary(list(glossary), [])
        if not self.mac.wait_until(self.mac.warmed_up, 120.0):
            self.close()
            raise BenchmarkError("the app did not finish warming up")
        self.desktop = self.mac.desktop
        if realtime:
            self.desktop.speak = self._speak_in_real_time

    def _speak_in_real_time(self, audio, block: int = 512):
        """The fake microphone, delivering blocks as a device does."""
        import numpy as np

        audio = np.asarray(audio, dtype=np.float32)
        self.desktop.spoken_into = [
            stream.device for stream in self.desktop.live_streams()]
        started = time.perf_counter()
        for start in range(0, len(audio), block):
            delay = started + start / SAMPLE_RATE - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            frames = audio[start:start + block].reshape(-1, 1).copy()
            for stream in self.desktop.live_streams():
                stream.callback(frames, len(frames), None, None)

    def _entries(self) -> list[str]:
        path = Path(self.mac.dictate.TRANSCRIPTS_FILE)
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def dictate(self, samples: array, *, heard: str = "") -> dict[str, Any]:
        """Hold the key, speak the take, release; what the app did.

        ``heard`` is what the fake Mac's stand-in recognizers hear. With the
        real runtime it stays empty: the runtime hears the audio, and the
        Whisper Tiny cross-check hears nothing.
        """
        import numpy as np

        dictate = self.mac.dictate
        before = len(self._entries())
        field = self.fake_platform.TextField("")
        seconds = len(samples) / SAMPLE_RATE
        self.mac.dictate_sentence(
            heard, field=field,
            audio=np.frombuffer(samples.tobytes(), dtype=np.float32)
            if sys.byteorder == "little" else np.array(samples, np.float32),
            timeout=max(30.0, 4 * seconds))
        entries = self._entries()[before:]
        entry = json.loads(entries[-1]) if entries else {}
        metrics = entry.get("metrics") or {}
        clean = entry.get("clean")
        return {
            "typed": field.text,
            "meant": clean,
            "raw": entry.get("raw"),
            "answered_by": dictate.speech_program_that_answered(),
            "insertion_state": metrics.get("insertion_state"),
            "insertion_verified": metrics.get("insertion_verified"),
            "delivered_as_meant": bool(clean) and field.text == clean,
            "release_ms": _ms(metrics.get("release_s")),
            "recognition_ms": _ms(metrics.get("asr_s")),
        }

    def close(self) -> None:
        """Put back what the fake Mac replaced; safe to call twice."""
        if not getattr(self, "closed", False):
            self.closed = True
            self.mac.close()


def _ms(seconds: Any) -> float | None:
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return round(1000 * float(seconds), 3)
    return None


def sandboxed_runtime_factory(runtime: Path) -> Callable[..., Any]:
    """Start the real runtime for the app instead of the installed path."""
    tests = str(HERE / "tests")
    if tests not in sys.path:
        sys.path.append(tests)
    import fake_platform

    def factory(argv, **kwargs):
        command = [str(runtime), *list(argv)[1:]]
        if SANDBOX.is_file():
            command = [str(SANDBOX), "-p", SANDBOX_PROFILE, *command]
        return fake_platform.REAL_POPEN(command, **kwargs)

    return factory


def delivery_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    answered = sum(row["answered_by"] == "new" for row in rows)
    delivered = sum(bool(row["delivered_as_meant"]) for row in rows)
    verified = sum(row["insertion_verified"] is True for row in rows)
    return {
        "takes": len(rows),
        "answered_by_runtime": answered,
        "delivered_as_meant": delivered,
        "delivered_pct": _rate(delivered, len(rows)),
        "insertion_verified": verified,
        "nothing_typed": sum(not row["typed"] for row in rows),
    }


def measure_app_path(corpus: Mapping[str, Any], audio: Mapping[str, array],
                     configuration: str, factory: Callable[..., Any] | None,
                     models: Path | None, realtime: bool,
                     log: Callable[[str], None]) -> dict[str, Any]:
    glossary = [entry["term"] for entry in corpus["glossary"]]
    app = AppPath(configuration, glossary, models=models,
                  process_factory=factory, realtime=realtime)
    rows: dict[str, dict[str, Any]] = {}
    try:
        ids = [take["id"] for take in corpus["takes"]]
        # A first, unscored dictation lets the runtime finish loading.
        app.dictate(audio[ids[0]])
        for index, take_id in enumerate(
                run_order(ids, corpus["seed"], f"app-{configuration}"), 1):
            rows[take_id] = app.dictate(audio[take_id])
            if index % 20 == 0:
                log(f"app path ({configuration}): {index} of {len(ids)}")
    finally:
        app.close()
    return rows


# -- identity of what was measured ---------------------------------------------


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=HERE, check=True, capture_output=True,
        text=True).stdout.strip()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def product_identity(runtime: Path) -> dict[str, Any]:
    sys.path.insert(0, str(HERE / "scripts"))
    try:
        from speech_runtime_parity import source_digest
    finally:
        sys.path.pop(0)
    return {
        "product_id": PRODUCT_ID,
        "version": _git("describe", "--tags", "--always"),
        "revision": _git("rev-parse", "HEAD"),
        "tree_clean": not _git("status", "--porcelain", "--untracked-files=no"),
        "runtime_source_sha256": source_digest(RUNTIME_SOURCES),
        "runtime_binary_sha256": _file_digest(runtime),
    }


def model_identity(models: Path) -> dict[str, Any]:
    from benchmark_asr import (
        PARAKEET_REQUIRED_ASSETS, load_model_specs,
        verify_installed_parakeet_revision)

    spec = load_model_specs()["parakeet-unified"]
    try:
        revision = verify_installed_parakeet_revision(spec, models)
    except RuntimeError as error:
        raise BenchmarkError(
            f"the model folder is not the pinned model: {error}") from error
    files = {}
    for relative in PARAKEET_REQUIRED_ASSETS:
        target = models / relative
        paths = [target] if target.is_file() else sorted(
            path for path in target.rglob("*") if path.is_file())
        for path in paths:
            files[path.relative_to(models).as_posix()] = _file_digest(path)
    return {
        "model_id": spec.model_id,
        "revision": revision,
        "files_sha256": canonical_digest(files),
        "files": len(files),
    }


def host_machine() -> dict[str, str]:
    """The Mac this process runs on, as every result names it. The outside
    probe uses the same words, so a probe result can be matched to the
    benchmark result of the same machine."""
    def sysctl(name: str) -> str:
        return subprocess.run(["sysctl", "-n", name], capture_output=True,
                              text=True, check=True).stdout.strip()

    def sw_vers(flag: str) -> str:
        return subprocess.run(["sw_vers", flag], capture_output=True,
                              text=True, check=True).stdout.strip()

    model = sysctl("hw.model")
    chip = sysctl("machdep.cpu.brand_string")
    memory = round(int(sysctl("hw.memsize")) / 2**30)
    version, build = sw_vers("-productVersion"), sw_vers("-buildVersion")
    environment_id = re.sub(r"[^a-z0-9]+", "-", (
        f"{model} {chip} macos {version}").casefold()).strip("-")[:64]
    return {
        "environment_id": environment_id,
        "hardware": f"{model}, {chip}, {memory} GB",
        "os_name": "macOS",
        "os_version": f"{version} ({build})",
    }


def machine_environment(product: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **host_machine(),
        "python_version": platform.python_version(),
        "whisper_face_revision": product["revision"],
        "software": [
            {"name": "whisper-face", "version": product["version"]},
            {"name": "parrot-speech-runtime",
             "version": "sha256:" + product["runtime_binary_sha256"][:16]},
        ],
    }


# -- the result -------------------------------------------------------------------


def build_result(corpus: Mapping[str, Any], *, voices: Mapping[str, str],
                 audio_sha256: Mapping[str, str],
                 seconds: Mapping[str, float], runtime: Mapping[str, Any],
                 app: Mapping[str, Mapping[str, Mapping[str, Any]]],
                 product: Mapping[str, Any], model: Mapping[str, Any],
                 environment: Mapping[str, Any], repeats: int,
                 realtime: bool, command: str,
                 started_utc: str) -> dict[str, Any]:
    ids = [take["id"] for take in corpus["takes"]]
    median_trip = {take_id: statistics.median(values)
                   for take_id, values in runtime["round_trip_ms"].items()}
    recognition = score_takes(
        [{"id": take_id, "hypothesis": runtime["texts"][take_id]}
         for take_id in ids], corpus)
    audio_total = sum(seconds.values())
    configurations = {}
    for name, rows in app.items():
        configurations[name] = {
            "accuracy": score_takes(
                [{"id": take_id, "hypothesis": rows[take_id]["typed"]}
                 for take_id in ids], corpus),
            "delivery": delivery_summary([rows[take_id] for take_id in ids]),
            "latency_ms": {
                "release_to_pasted_in_fake_field": latency_summary(
                    seconds, {take_id: rows[take_id]["release_ms"]
                              for take_id in ids
                              if rows[take_id]["release_ms"] is not None}),
                "recognition_after_release": latency_summary(
                    seconds, {take_id: rows[take_id]["recognition_ms"]
                              for take_id in ids
                              if rows[take_id]["recognition_ms"] is not None}),
            },
        }
    takes = []
    for take in corpus["takes"]:
        take_id = take["id"]
        entry = {
            "id": take_id,
            "category": take["category"],
            "voice": voices[take_id],
            "seconds": round(seconds[take_id], 3),
            "audio_sha256": audio_sha256[take_id],
            "runtime": {
                "text": runtime["texts"][take_id],
                "words": runtime["words"][take_id],
                "round_trip_ms": [round(value, 3) for value in
                                  runtime["round_trip_ms"][take_id]],
            },
            "app": {name: {key: rows[take_id][key] for key in (
                "typed", "answered_by", "insertion_state",
                "insertion_verified", "release_ms")}
                for name, rows in app.items()},
        }
        takes.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "result_kind": RESULT_KIND,
        "product": dict(product),
        "model": dict(model),
        "environment": dict(environment),
        "corpus": {
            "corpus_id": corpus["corpus_id"],
            "sha256": canonical_digest(corpus),
            "takes": len(ids),
            "audio_seconds": round(audio_total, 1),
            "synthetic_speech": True,
            "voices": list(corpus["speech"]["voices"]),
            "audio_sha256": canonical_digest(dict(audio_sha256)),
        },
        "run": {
            "started_utc": started_utc,
            "seed": corpus["seed"],
            "repeats": repeats,
            "app_path_pace": "real-time" if realtime else "instant",
            "network": "denied to the runtime by sandbox-exec",
            "command": command,
        },
        "recognition": {
            "measured_by": RUNTIME_IN_PROCESS,
            "what": (
                "Whisper Face's own speech runtime alone: each take sent "
                "whole, framed as the app frames it, and timed from the "
                "request to the answer. Latency is each take's median over "
                "the repeats."),
            "accuracy": recognition,
            "latency_ms": latency_summary(seconds, median_trip),
            "real_time_factor": round(
                audio_total / (sum(median_trip.values()) / 1000), 1)
            if median_trip else None,
        },
        "app_path": {
            "measured_by": APP_ON_FAKE_MAC,
            "what": (
                "The real dictate.py on the fake Mac of tests/fake_platform.py "
                "with the same runtime: each take spoken into the fake "
                "microphone while the key is held, cut, decoded, compiled "
                "and pasted into a fake text field. Release time is the "
                "app's own measure, from key release to the paste done; the "
                "fake field answers instantly, a real app does not."),
            "configurations": configurations,
        },
        "outside_probe": {
            name: {"status": NEEDS_OUTSIDE_PROBE, "value": None, "why": why}
            for name, why in OUTSIDE_PROBE_METRICS.items()},
        "takes": takes,
    }


# -- the outside probe's numbers ---------------------------------------------------


def probe_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Release to first and to final text, from the probe's per-take rows.

    Each take's time is its median over the probe's repeats, bucketed by the
    take's length as every other latency in a result is.
    """
    seconds: dict[str, float] = {}
    first: dict[str, list[float]] = {}
    final: dict[str, list[float]] = {}
    without_text = 0
    for row in rows:
        seconds[row["id"]] = row["seconds"]
        if row["status"] != "text":
            without_text += 1
            continue
        first.setdefault(row["id"], []).append(row["release_to_first_text_ms"])
        final.setdefault(row["id"], []).append(row["release_to_final_text_ms"])
    return {
        "release_to_first_text": latency_summary(seconds, {
            take_id: statistics.median(values)
            for take_id, values in first.items()}),
        "release_to_final_text": latency_summary(seconds, {
            take_id: statistics.median(values)
            for take_id, values in final.items()}),
        "takes": len(seconds),
        "repeats_without_text": without_text,
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def outside_probe_problems(result: Mapping[str, Any],
                           entry: Mapping[str, Any]) -> list[str]:
    """Why a probe measurement may not stand in this result, if it may not.

    The probe must have timed the same app revision, on the same machine and
    OS, with the same corpus and audio, and run to the end; every per-take
    time must be consistent; and the published value must follow from those
    times, so an edited number is refused.
    """
    found = []
    probe = entry.get("probe")
    if not isinstance(probe, Mapping) or probe.get(
            "result_kind") != PROBE_RESULT_KIND:
        return ["it does not carry the outside probe's own result"]
    if probe.get("measured_by") != OUTSIDE_PROBE:
        found.append(f"the probe's numbers must be {OUTSIDE_PROBE!r}")
    product = result.get("product", {})
    target = probe.get("target")
    if not isinstance(target, Mapping) or (
            target.get("product_id"), target.get("revision")) != (
            product.get("product_id"), product.get("revision")):
        found.append("the probe timed a different app or revision")
    environment = result.get("environment", {})
    machine = probe.get("environment")
    if not isinstance(machine, Mapping) or any(
            machine.get(key) != environment.get(key)
            for key in ("hardware", "os_name", "os_version")):
        found.append("the probe ran on a different machine or OS")
    corpus = probe.get("corpus")
    if not isinstance(corpus, Mapping) or corpus.get("sha256") != result.get(
            "corpus", {}).get("sha256"):
        found.append("the probe played a different corpus")
    run = probe.get("run")
    if not isinstance(run, Mapping) or run.get("completed") is not True:
        found.append("the probe run stopped before its last take")
    elif not str(run.get("command", "")).startswith(PROBE_COMMAND) or any(
            word.startswith(("/", "~")) or ":\\" in word
            for word in str(run["command"]).split()):
        found.append("the probe's rerun command must start with "
                     f"{PROBE_COMMAND!r} and name no local folder")
    takes = {take.get("id"): take for take in result.get("takes", [])
             if isinstance(take, Mapping)}
    rows = probe.get("takes")
    if not isinstance(rows, list) or not rows:
        return found + ["the probe carries no takes"]
    for row in rows:
        take = takes.get(row.get("id")) if isinstance(row, Mapping) else None
        if take is None:
            found.append(f"{row!r:.60}: not a take of this result")
            continue
        label = row["id"]
        if row.get("audio_sha256") != take.get("audio_sha256") or row.get(
                "seconds") != take.get("seconds"):
            found.append(f"{label}: the probe played different audio")
        if row.get("status") not in PROBE_TAKE_STATES:
            found.append(f"{label}: unknown status {row.get('status')!r}")
            continue
        times = [row.get(key) for key in (
            "release_ms", "first_text_ms", "final_text_ms",
            "release_to_first_text_ms", "release_to_final_text_ms")]
        if row["status"] == "no-text":
            if not _is_number(times[0]) or any(
                    value is not None for value in times[1:]):
                found.append(f"{label}: a take with no text has no text times")
            continue
        if not all(_is_number(value) for value in times):
            found.append(f"{label}: a take with text needs every time")
            continue
        release, first_at, final_at, to_first, to_final = times
        if not first_at <= final_at or final_at < release:
            found.append(f"{label}: its times are out of order")
        if (abs(to_first - (first_at - release)) > 0.002
                or abs(to_final - (final_at - release)) > 0.002):
            found.append(f"{label}: its intervals do not follow from its times")
    if not found and entry.get("value") != probe_summary(rows):
        found.append("the value does not follow from the probe's own "
                     "per-take times")
    return found


def attach_probe(result: Mapping[str, Any],
                 probe: Mapping[str, Any]) -> dict[str, Any]:
    """The result with the outside probe's release-to-text filled in."""
    if not isinstance(probe, Mapping) or probe.get(
            "result_kind") != PROBE_RESULT_KIND:
        raise BenchmarkError("that is not an outside probe result")
    rows = probe.get("takes")
    if not isinstance(rows, list) or not rows:
        raise BenchmarkError("the outside probe result carries no takes")
    if probe.get("summary") != probe_summary(rows):
        raise BenchmarkError("the outside probe's summary does not follow "
                             "from its own per-take times")
    attached = json.loads(json.dumps(result))
    entry = {
        "status": OUTSIDE_PROBE,
        "value": probe_summary(rows),
        "why": ("Key release to the text arriving in the probe's own "
                "window, timed by outside_probe.py from outside the app."),
        "probe": {key: probe[key] for key in (
            "result_kind", "measured_by", "target", "environment", "corpus",
            "method", "run", "takes")},
    }
    problems = outside_probe_problems(attached, entry)
    if problems:
        raise BenchmarkError("the outside probe result does not belong to "
                             "this benchmark result: " + "; ".join(problems))
    attached["outside_probe"]["release_to_text_on_screen_ms"] = entry
    return attached


def rerun_command(args: argparse.Namespace) -> str:
    """The command that reproduces this run, with the machine's own paths
    left as placeholders: a published page names no local folder."""
    words = ["uv", "run", "benchmark_public.py", "run",
             "--runtime", "<runtime>", "--models", "<models>",
             "--out", "<out>"]
    if args.repeats != DEFAULT_REPEATS:
        words += ["--repeats", str(args.repeats)]
    if list(args.configurations) != list(CONFIGURATIONS):
        words += ["--configurations", *args.configurations]
    if args.pace != "real-time":
        words += ["--pace", args.pace]
    return " ".join(words)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def run(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise BenchmarkError("the benchmark speaks with macOS voices and "
                             "runs a macOS program; run it on a Mac")
    out = args.out.expanduser().resolve()
    if _inside(out, HERE):
        raise BenchmarkError(
            f"--out must be outside this checkout ({HERE}): the audio it "
            "keeps is never committed")
    runtime = args.runtime.expanduser().resolve()
    models = args.models.expanduser().resolve()
    if not (runtime.is_file() and os.access(runtime, os.X_OK)):
        raise BenchmarkError(f"{runtime} is not an executable runtime")
    if not models.is_dir():
        raise BenchmarkError(f"{models} is not a model folder")
    corpus = load_corpus()
    problems = corpus_problems(corpus)
    if problems:
        raise BenchmarkError("the corpus is invalid: " + "; ".join(problems))

    def log(message: str) -> None:
        print(message, file=sys.__stderr__, flush=True)

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    product = product_identity(runtime)
    if not product["tree_clean"]:
        raise BenchmarkError(
            "the checkout has uncommitted changes, so its revision does not "
            "describe the code being measured; commit or stash them first")
    model = model_identity(models)
    environment = machine_environment(product)
    voices = voice_for_takes(corpus)
    audio_dir = out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio: dict[str, array] = {}
    hashes: dict[str, str] = {}
    seconds: dict[str, float] = {}
    for take in corpus["takes"]:
        path, digest = synthesize(take, voices[take["id"]], corpus["speech"],
                                  audio_dir)
        samples = padded_samples(path, corpus["speech"]["pad_seconds"])
        audio[take["id"]] = samples
        hashes[take["id"]] = digest
        seconds[take["id"]] = len(samples) / SAMPLE_RATE
    log(f"spoke {len(audio)} takes, {sum(seconds.values()):.0f} s of audio")
    runtime_result = measure_runtime(
        corpus, audio, runtime, models, args.repeats, log)
    app: dict[str, dict[str, dict[str, Any]]] = {}
    factory = sandboxed_runtime_factory(runtime)
    for configuration in args.configurations:
        app[configuration] = measure_app_path(
            corpus, audio, configuration, factory, models,
            args.pace == "real-time", log)
    result = build_result(
        corpus, voices=voices, audio_sha256=hashes, seconds=seconds,
        runtime=runtime_result, app=app, product=product, model=model,
        environment=environment, repeats=args.repeats,
        realtime=args.pace == "real-time",
        command=rerun_command(args),
        started_utc=started)

    import public_scorecard

    public_scorecard.validate_benchmark_result(result)
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    (out / "scorecard.md").write_text(
        public_scorecard.render_benchmark_markdown(result), encoding="utf-8")
    recognition = result["recognition"]["accuracy"]
    log(f"runtime alone: WER {recognition['wer_pct']}%, term recall "
        f"{recognition['terms']['recall_pct']}%, false insertions "
        f"{recognition['terms']['false_insertions']}, formatting "
        f"{recognition['formatting']['accuracy_pct']}%")
    log(f"wrote {out / 'result.json'} and {out / 'scorecard.md'}")
    return 0


def check() -> int:
    corpus = load_corpus()
    problems = corpus_problems(corpus)
    for line in problems:
        print(line)
    if problems:
        return 1
    from collections import Counter

    counts = Counter(take["category"] for take in corpus["takes"])
    print(f"{corpus['corpus_id']}: {len(corpus['takes'])} takes "
          + ", ".join(f"{counts[name]} {name}" for name in CATEGORIES)
          + f"; {len(corpus['glossary'])} glossary terms; seed "
          f"{corpus['seed']}; sha256 {canonical_digest(corpus)}")
    return 0


def attach(args: argparse.Namespace) -> int:
    """Fill a result's release-to-text from the outside probe's result."""
    import public_scorecard

    out = args.out.expanduser().resolve()
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    probe = json.loads((out / PROBE_RESULT_FILE).read_text(encoding="utf-8"))
    attached = attach_probe(result, probe)
    try:
        public_scorecard.validate_benchmark_result(attached)
    except public_scorecard.PublicationError as error:
        raise BenchmarkError(str(error)) from error
    (out / "result.json").write_text(
        json.dumps(attached, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    (out / "scorecard.md").write_text(
        public_scorecard.render_benchmark_markdown(attached), encoding="utf-8")
    final = attached["outside_probe"]["release_to_text_on_screen_ms"][
        "value"]["release_to_final_text"]["all"]
    print(f"release to text on screen: p50 {final['p50']} ms, p95 "
          f"{final['p95']} ms over {final['takes']} takes; wrote "
          f"{out / 'result.json'} and {out / 'scorecard.md'}",
          file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate the corpus (clone only)")
    run_parser = commands.add_parser("run", help="run the benchmark on a Mac")
    run_parser.add_argument("--runtime", type=Path, required=True,
                            help="a built parrot-speech-runtime")
    run_parser.add_argument("--models", type=Path, required=True,
                            help="the parakeet-unified-en-0.6b model folder")
    run_parser.add_argument("--out", type=Path, required=True,
                            help="a folder outside the checkout")
    run_parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                            choices=range(1, 11), metavar="1-10")
    run_parser.add_argument("--configurations", nargs="+",
                            default=list(CONFIGURATIONS),
                            choices=CONFIGURATIONS)
    run_parser.add_argument("--pace", choices=("real-time", "instant"),
                            default="real-time",
                            help="how the fake microphone delivers audio")
    attach_parser = commands.add_parser(
        "attach-probe", help="fill release to text on screen from "
        f"<out>/{PROBE_RESULT_FILE}, written by outside_probe.py")
    attach_parser.add_argument("--out", type=Path, required=True,
                               help="the folder of a finished run")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return check()
        if args.command == "attach-probe":
            return attach(args)
        return run(args)
    except BenchmarkError as error:
        print(f"benchmark_public: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
