"""Try an update on the person's own voice before it replaces what they run.

When a person has recorded an accuracy corpus with
``scripts/capture_voice_evidence.py accuracy`` (by default
``~/whisper-face-evidence/accuracy``), both updaters call
:func:`check_update` at the point where they have verified a candidate and
are about to switch to it: ``self_update.apply_update`` before it checks out
the new revision, ``scripts/app_update.install`` before it puts the new app
in place. The candidate's recogniser and clean-up run on those recordings,
the running version's run on the same recordings, and the two are scored
against what the person said: word mistakes, names and terms written right,
and listed names written where they were not said.

If the candidate is worse by more than :data:`TOLERANCE` allows, the result
is ``held`` and the person is told what got worse, in counts. Nothing is
changed until they choose to update anyway. With no corpus the result is
``no-corpus`` and the update goes ahead exactly as before this module
existed. A check that cannot be run (no recogniser built for this version,
no way to keep it offline, a candidate that predates this module) is
``not-checked``, said once, and does not stop the update: the installer's
own checks still stand behind it.

What it may and may not do:

* It never sends anything anywhere. On a Mac every recogniser, every build
  of the candidate's recogniser and the candidate's own code run inside
  ``sandbox-exec`` with networking denied; without ``sandbox-exec`` nothing
  runs and the check is ``not-checked``.
* It never writes a word. Recordings are read in place and never copied.
  What each version wrote travels between processes through a pipe and is
  scored in memory. The receipt it leaves under
  ``~/whisper-face-evidence/runs/`` (``0600`` in a ``0700`` folder) holds
  counts and recording ids only, and :func:`summary_line` is the only thing
  an update log ever gets from it.
* It never slows dictation. It runs only when the person asks for an
  update, in the updater's own process, with every child at background
  priority (``taskpolicy -b``), and it leaves the running app's speech
  programs and their compiled-model caches alone: an unchanged recogniser is
  the installed program itself, and a changed one is built in a private
  folder under its own name and its cache is removed afterwards.

Run as a program it is one version's half of a check, which is how the
running version asks a candidate to replay itself::

    python3 -I voice_check.py replay --program PATH --corpus DIR \\
        --models DIR [--self-correction] [--keep-repeats]

That reads the recordings, has ``PATH`` recognise each one, finishes the
text with this version's clean-up (``parrot_core.finish_cleanup``) and prints
one JSON document on standard output for the caller to score. It writes no
file.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent

SCHEMA_VERSION = 1
REPLAY_SCHEMA_VERSION = 1
RECEIPT_KIND = "whisper-face/update-voice-check"

EVIDENCE = Path("whisper-face-evidence")
CORPUS = EVIDENCE / "accuracy"
RUNS = EVIDENCE / "runs"
CORPUS_KIND = "whisper-face/accuracy-corpus-cases"
# Where dictate.py's pinned Parakeet snapshot lives; every release shares it.
MODELS = (Path("Library") / "Application Support" / "FluidAudio" / "Models" /
          "parakeet-unified-en-0.6b")

SAMPLE_RATE = 16_000
MAX_RECORDINGS = 200
MAX_RECORDING_SECONDS = 120
MAX_TEXT_CHARACTERS = 4_000
PROTOCOL = 2
MAX_RESPONSE_BYTES = 1024 * 1024

# How long a check may take. A cold compiled-model cache costs about ten
# seconds on an Apple silicon Mac; a candidate's recogniser build a few
# minutes. Past these the side that is late counts as not answering.
START_TIMEOUT = 180.0
REQUEST_TIMEOUT = 60.0
BUILD_TIMEOUT = 1200.0
CANDIDATE_TIMEOUT = START_TIMEOUT + MAX_RECORDINGS * 10.0

# How much worse a candidate may be before the update is held, and why:
#
# * Word mistakes: two words, or half a word in a hundred of what the corpus
#   says, whichever is more (two on the 40-sentence set of about 400 words).
#   The same program on the same recordings gives the same words, so the
#   allowance is not noise. It is room for a clean-up change that trades one
#   spelling for another an update means to make ("OK" for "okay"), which the
#   word count cannot tell from a mishearing.
# * Names and terms: none. Names are what the person recorded the corpus
#   for, and a name lost is the regression they would notice first.
# * Names written where they were not said: none. Writing a name nobody said
#   is the worst mistake a dictation app can make in someone's document.
WORD_ERROR_MINIMUM_ALLOWANCE = 2
WORD_ERROR_ALLOWANCE_RATE = 0.005
TERM_ALLOWANCE = 0
FALSE_INSERTION_ALLOWANCE = 0
TOLERANCE = {
    "word_errors": (f"{WORD_ERROR_MINIMUM_ALLOWANCE} words or "
                    f"{WORD_ERROR_ALLOWANCE_RATE:.1%} of the words said, "
                    "whichever is more"),
    "terms_right": "none",
    "false_insertions": "none",
}

# The two programs that run the Parakeet model, as setup.sh builds them:
# (package, executable, build folder under .models).
PROGRAMS = {
    "fluidaudio": ("native/ParrotASRHelper", "parrot-asr-helper",
                   "swift-build"),
    "own": ("native/ParrotSpeechRuntime", "parrot-speech-runtime",
            "swift-build-runtime"),
}
# A candidate's recogniser runs under this suffix, so Core ML compiles its
# model into a cache of its own, which is removed afterwards, and never into
# the one the running app starts from.
CHECK_SUFFIX = "-voice-check"

NO_NETWORK_PROFILE = "(version 1)(allow default)(deny network*)"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
TASKPOLICY = "/usr/sbin/taskpolicy"

# Why a check could not run. whisper_face_gui's catalog says each one to the
# person as update.voice_check.reason.<code>.
REASON_CODES = (
    "not-mac", "no-sandbox", "no-recogniser", "current-did-not-start",
    "candidate-too-old", "not-built", "candidate-did-not-start",
    "candidate-failed", "unreadable", "failed",
)

Runner = Callable[..., "subprocess.CompletedProcess"]


# ------------------------------------------------------------- the corpus --

@dataclass(frozen=True)
class Case:
    """One recording and what was really said in it; repr-redacted."""

    id: str
    audio: Path = field(repr=False)
    reference: str = field(repr=False)
    terms: tuple[str, ...] = field(default=(), repr=False)
    avoid: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Corpus:
    directory: Path
    cases: tuple[Case, ...] = field(repr=False)
    vocabulary: tuple[str, ...] = field(repr=False)


_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def corpus_directory(home: Path | None = None) -> Path:
    return Path(home or Path.home()) / CORPUS


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value
                 if isinstance(item, str) and 0 < len(item.strip()) <= 80)


def load_corpus(directory: Path) -> Corpus | None:
    """The recordings ``capture_voice_evidence.py accuracy`` wrote, or None.

    Reads only ``cases.json``, which names each recording and what was said;
    a case whose recording is missing, or that names a file outside the
    folder, is left out. None when there is nothing to check against.
    """
    directory = Path(directory)
    try:
        document = json.loads((directory / "cases.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("kind") != CORPUS_KIND:
        return None
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list):
        return None
    cases: list[Case] = []
    seen: set[str] = set()
    for raw in raw_cases[:MAX_RECORDINGS]:
        if not isinstance(raw, dict):
            continue
        identity, audio, text = raw.get("id"), raw.get("audio"), raw.get("text")
        if not (isinstance(identity, str) and _CASE_ID.fullmatch(identity)
                and identity not in seen and isinstance(audio, str)
                and isinstance(text, str) and text.strip()
                and len(text) <= MAX_TEXT_CHARACTERS):
            continue
        name = Path(audio)
        if name.name != audio or not audio.lower().endswith(".wav"):
            continue
        path = directory / name
        if not path.is_file() or path.is_symlink():
            continue
        seen.add(identity)
        terms = _strings(raw.get("terms"))
        avoid = tuple(term for term in _strings(raw.get("must_not_write"))
                      if term not in terms)
        cases.append(Case(identity, path, " ".join(text.split()), terms,
                          avoid))
    if not cases:
        return None
    vocabulary: dict[str, str] = {}
    for case in cases:
        for term in (*case.terms, *case.avoid):
            vocabulary.setdefault(term.casefold(), term)
    return Corpus(directory, tuple(cases), tuple(vocabulary.values()))


def corpus_size(home: Path | None = None) -> int:
    """How many recordings an update would be tried on; 0 for none."""
    if sys.platform != "darwin":
        return 0
    corpus = load_corpus(corpus_directory(home))
    return len(corpus.cases) if corpus else 0


# -------------------------------------------------------------- the audio --

def read_recording(path: Path) -> array:
    """A capture's 16 kHz mono PCM as float32 samples, prepared as
    dictate.py's ``prepare_asr_audio`` prepares a dictation by default: a
    quiet take is raised to a 0.25 peak, by at most 25 times."""
    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate() != SAMPLE_RATE or handle.getnchannels() != 1
                or handle.getsampwidth() != 2):
            raise ValueError("not 16 kHz mono 16-bit")
        frames = handle.getnframes()
        if frames > MAX_RECORDING_SECONDS * SAMPLE_RATE:
            raise ValueError("too long")
        pcm = array("h")
        pcm.frombytes(handle.readframes(frames))
    if sys.byteorder != "little":
        pcm.byteswap()
    samples = array("f", (value / 32768.0 for value in pcm))
    peak = max((abs(value) for value in samples), default=0.0)
    if 0.0 < peak < 0.25:
        gain = min(0.25 / peak, 25.0)
        samples = array("f", (value * gain for value in samples))
    return samples


def _payload(samples: array) -> bytes:
    data = array("f", samples)
    if sys.byteorder != "little":
        data.byteswap()
    return struct.pack("<Q", len(data)) + data.tobytes()


# --------------------------------------------------------- the recogniser --

def isolation(platform: str | None = None) -> list[str] | None:
    """The prefix every program a check starts runs under: no network,
    background priority. None when this Mac cannot deny the network."""
    if (platform or sys.platform) != "darwin":
        return None
    if not os.access(SANDBOX_EXEC, os.X_OK):
        return None
    prefix = [SANDBOX_EXEC, "-p", NO_NETWORK_PROFILE]
    if os.access(TASKPOLICY, os.X_OK):
        prefix += [TASKPOLICY, "-b"]
    return prefix


class Recogniser:
    """A speech program spoken to as dictate.py's ParakeetClient speaks to
    it: a u64 little-endian sample count, that many float32 samples, one
    JSON line back. Its standard error is discarded, never logged."""

    def __init__(self, program: Path, models: Path, *, wrap: Sequence[str] = (),
                 popen=subprocess.Popen, start_timeout: float = START_TIMEOUT,
                 request_timeout: float = REQUEST_TIMEOUT):
        self.request_timeout = request_timeout
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("FLUID_")}
        self.process = popen(
            [*wrap, str(program), "--server", "--protocol", str(PROTOCOL),
             "--models", str(models)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, env=environment)
        try:
            ready = self._line(start_timeout)
        except Exception:
            self.close()
            raise
        if not ready.get("ready"):
            self.close()
            raise RuntimeError("not ready")

    def _line(self, timeout: float) -> dict:
        data = bytearray()
        deadline = time.monotonic() + timeout
        stream = self.process.stdout
        while not data.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([stream], [], [], left)[0]:
                raise TimeoutError("no answer")
            chunk = os.read(stream.fileno(), 65536)
            if not chunk:
                raise RuntimeError("exited")
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError("answer too long")
        answer = json.loads(bytes(data).split(b"\n", 1)[0])
        if not isinstance(answer, dict):
            raise RuntimeError("malformed answer")
        return answer

    def transcribe(self, samples: array) -> str:
        self.process.stdin.write(_payload(samples))
        self.process.stdin.flush()
        reply = self._line(self.request_timeout)
        if not reply.get("ok"):
            raise RuntimeError("request failed")
        return str(reply.get("text", ""))[:MAX_TEXT_CHARACTERS]

    def close(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream and stream.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


def finish(raw: str, *, self_correction: bool, remove_repeats: bool) -> str:
    """This version's clean-up of one recognised text, as dictate.py's
    ``quick_clean`` does it."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from parrot_core import finish_cleanup

    return finish_cleanup(raw, "en", self_correction, remove_repeats)


def replay(corpus: Corpus, program: Path, *, models: Path,
           self_correction: bool, remove_repeats: bool,
           wrap: Sequence[str] = (), popen=subprocess.Popen,
           start_timeout: float = START_TIMEOUT,
           request_timeout: float = REQUEST_TIMEOUT) -> dict:
    """What this version writes for every recording: ``{"started", "texts"}``
    with None for a recording the program did not answer. A recording that
    cannot be read is left out, so both sides skip it alike."""
    texts: dict[str, str | None] = {}
    unreadable: list[str] = []
    try:
        recogniser = Recogniser(program, models, wrap=wrap, popen=popen,
                                start_timeout=start_timeout,
                                request_timeout=request_timeout)
    except Exception:
        return {"schema_version": REPLAY_SCHEMA_VERSION, "started": False,
                "texts": {}, "unreadable": []}
    try:
        alive = True
        for case in corpus.cases:
            try:
                samples = read_recording(case.audio)
            except (OSError, EOFError, ValueError, wave.Error):
                unreadable.append(case.id)
                continue
            if not alive:
                texts[case.id] = None
                continue
            try:
                raw = recogniser.transcribe(samples)
            except Exception:
                # One stuck or failed request ends the program's turn: the
                # rest count as unanswered rather than waiting again.
                texts[case.id] = None
                alive = False
                continue
            texts[case.id] = finish(raw, self_correction=self_correction,
                                    remove_repeats=remove_repeats)
    finally:
        recogniser.close()
    return {"schema_version": REPLAY_SCHEMA_VERSION, "started": True,
            "texts": texts, "unreadable": unreadable}


# ------------------------------------------------------------- the score --

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def words(text: str) -> list[str]:
    """Casefolded words, punctuation and casing aside, as the accuracy
    benchmarks align them."""
    return _WORD.findall(str(text).casefold().replace("’", "'"))


def word_errors(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Substitutions, deletions and insertions between two word lists."""
    previous = list(range(len(hypothesis) + 1))
    for row, said in enumerate(reference, 1):
        current = [row]
        for column, written in enumerate(hypothesis, 1):
            current.append(min(previous[column] + 1, current[column - 1] + 1,
                               previous[column - 1] + (said != written)))
        previous = current
    return previous[-1]


def occurrences(term: str, text: str) -> int:
    """Whole-word, any-case occurrences of a listed name or term."""
    pattern = r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)"
    return len(re.findall(pattern, str(text).casefold().replace("’", "'")))


@dataclass
class Score:
    recordings: int = 0
    reference_words: int = 0
    word_errors: int = 0
    terms_total: int = 0
    terms_right: int = 0
    false_insertions: int = 0
    unanswered: int = 0
    cases: dict[str, dict[str, int]] = field(default_factory=dict, repr=False)

    def totals(self) -> dict[str, int]:
        return {"recordings": self.recordings,
                "reference_words": self.reference_words,
                "word_errors": self.word_errors,
                "terms_total": self.terms_total,
                "terms_right": self.terms_right,
                "false_insertions": self.false_insertions,
                "unanswered": self.unanswered}


def score(corpus: Corpus, texts: Mapping[str, str | None],
          only: Iterable[str] | None = None) -> Score:
    """Count, never keep, what a version got right on each recording.

    ``only`` limits the score to the recordings both sides could read.
    A recording a version did not answer counts every word and every term
    in it as wrong.
    """
    keep = set(only) if only is not None else None
    result = Score()
    for case in corpus.cases:
        if keep is not None and case.id not in keep:
            continue
        said = words(case.reference)
        text = texts.get(case.id)
        unanswered = not isinstance(text, str)
        text = "" if unanswered else text
        errors = word_errors(said, words(text))
        right = sum(1 for term in case.terms if occurrences(term, text) > 0)
        inserted = sum(
            max(0, occurrences(term, text) - occurrences(term, case.reference))
            for term in corpus.vocabulary)
        result.recordings += 1
        result.reference_words += len(said)
        result.word_errors += errors
        result.terms_total += len(case.terms)
        result.terms_right += right
        result.false_insertions += inserted
        result.unanswered += int(unanswered)
        result.cases[case.id] = {"word_errors": errors, "terms_right": right,
                                 "terms": len(case.terms),
                                 "false_insertions": inserted,
                                 "unanswered": int(unanswered)}
    return result


def allowance(reference_words: int) -> dict[str, int]:
    """The stated tolerance, in counts, for a corpus of this many words."""
    return {"word_errors": max(WORD_ERROR_MINIMUM_ALLOWANCE,
                               int(reference_words * WORD_ERROR_ALLOWANCE_RATE)),
            "terms_right": TERM_ALLOWANCE,
            "false_insertions": FALSE_INSERTION_ALLOWANCE}


def worse(current: Score, candidate: Score) -> list[str]:
    """Which measures the candidate is worse on beyond the tolerance."""
    allowed = allowance(current.reference_words)
    found = []
    if candidate.terms_right < current.terms_right - allowed["terms_right"]:
        found.append("terms_right")
    if candidate.word_errors > current.word_errors + allowed["word_errors"]:
        found.append("word_errors")
    if candidate.false_insertions > (current.false_insertions
                                     + allowed["false_insertions"]):
        found.append("false_insertions")
    if candidate.unanswered > current.unanswered:
        found.append("unanswered")
    return found


# ------------------------------------------------------------ the receipt --

def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_receipt(home: Path, payload: Mapping[str, Any], *,
                  now: _datetime.datetime | None = None) -> Path:
    """Counts only, ``0600`` in a ``0700`` folder of its own under runs/."""
    now = now or _datetime.datetime.now()
    runs = Path(home) / RUNS
    _private_directory(runs.parent)
    _private_directory(runs)
    stem = f"{now:%Y-%m-%d-%H%M%S}-update-check"
    folder = runs / stem
    suffix = 1
    while folder.exists():
        suffix += 1
        folder = runs / f"{stem}-{suffix}"
    _private_directory(folder)
    path = folder / "receipt.json"
    descriptor, name = tempfile.mkstemp(prefix=".receipt.", dir=folder)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.chmod(name, 0o600)
    os.replace(name, path)
    return path


def summary_line(outcome: Mapping[str, Any]) -> str:
    """One content-free line for an update log: a status and counts."""
    status = str(outcome.get("status") or "")
    if status not in {"passed", "held"}:
        reason = str(outcome.get("reason") or "")
        reason = reason if reason in REASON_CODES else ""
        return f"voice-check {status} {reason}".rstrip()

    def count(side: str, key: str) -> int:
        value = (outcome.get(side) or {}).get(key)
        return value if isinstance(value, int) else 0
    recordings = outcome.get("recordings")
    recordings = recordings if isinstance(recordings, int) else 0
    return (f"voice-check {status} recordings={recordings} "
            f"words {count('current', 'word_errors')}->"
            f"{count('candidate', 'word_errors')} "
            f"terms {count('current', 'terms_right')}->"
            f"{count('candidate', 'terms_right')}"
            f"/{count('candidate', 'terms_total')} "
            f"inserted {count('current', 'false_insertions')}->"
            f"{count('candidate', 'false_insertions')}")


# ---------------------------------------------------------- the candidate --

def _package_digest(root: Path, package: str) -> str | None:
    """What a speech program is built from: its manifest, its pinned
    dependencies and every source file."""
    base = Path(root) / package
    if not (base / "Package.swift").is_file():
        return None
    files = [base / "Package.swift", base / "Package.resolved"]
    files += sorted((base / "Sources").rglob("*"))
    digest = hashlib.sha256()
    for path in files:
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(base)).encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def program_kind(checkout: Path) -> str:
    """Which program this person's dictation asks first, as dictate.py
    decides it: PARROT_ASR_RUNTIME when set, else the preference."""
    pinned = os.environ.get("PARROT_ASR_RUNTIME", "").strip()
    if pinned:
        return "own" if pinned.lower() == "own" else "fluidaudio"
    return "own" if _preference(checkout, "new_speech_runtime", False) \
        else "fluidaudio"


def _preference(checkout: Path, key: str, default: bool) -> bool:
    try:
        loaded = json.loads((Path(checkout) / "preferences.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return default
    if not isinstance(loaded, dict) or key not in loaded:
        return default
    return loaded.get(key) is True


def candidate_program(current: Path, candidate: Path, kind: str, work: Path,
                      *, run: Runner, wrap: Sequence[str]) -> tuple[Path | None, str]:
    """The candidate's recogniser, and how it was had.

    The same sources as the running version's make the same program, so the
    installed one is used and nothing is built. Otherwise the candidate's
    package is built here, offline, into ``work``: for the FluidAudio helper
    its pinned dependency is taken from the running version's own build
    folder (an APFS clone), because the network is denied.
    """
    package, executable, build = PROGRAMS[kind]
    installed = Path(current) / ".models" / "bin" / executable
    before = _package_digest(current, package)
    after = _package_digest(candidate, package)
    if after is None:
        return None, "not-built"
    if before == after:
        return installed, "same"
    scratch = work / "build"
    seed = Path(current) / ".models" / build
    if kind == "fluidaudio":
        if not seed.is_dir():
            return None, "not-built"
        cloned = run(["/bin/cp", "-c", "-R", str(seed), str(scratch)],
                     capture_output=True, timeout=600)
        if cloned.returncode != 0:
            return None, "not-built"
    try:
        built = run([*wrap, "swift", "build", "-c", "release",
                     "--disable-sandbox", "--disable-automatic-resolution",
                     "--package-path", str(Path(candidate) / package),
                     "--scratch-path", str(scratch)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=BUILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None, "not-built"
    product = scratch / "release" / executable
    if built.returncode != 0 or not product.is_file():
        return None, "not-built"
    program = work / "bin" / (executable + CHECK_SUFFIX)
    program.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(product, program)
    return program, "built"


def candidate_replay(candidate: Path, corpus: Corpus, program: Path, *,
                     models: Path, python: str, self_correction: bool,
                     remove_repeats: bool, wrap: Sequence[str],
                     run: Runner) -> dict | None:
    """The candidate replays itself with its own code, in its own process."""
    script = Path(candidate) / "voice_check.py"
    if not script.is_file():
        return None
    command = [*wrap, python, "-I", str(script), "replay",
               "--program", str(program), "--corpus", str(corpus.directory),
               "--models", str(models)]
    if self_correction:
        command.append("--self-correction")
    if not remove_repeats:
        command.append("--keep-repeats")
    try:
        completed = run(command, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True,
                        timeout=CANDIDATE_TIMEOUT, cwd=str(candidate))
    except (OSError, subprocess.SubprocessError):
        return {"failed": True}
    lines = (completed.stdout or "").strip().splitlines()
    try:
        answer = json.loads(lines[-1]) if lines else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not isinstance(answer.get("texts"), dict):
        return {"failed": True}
    known = {case.id for case in corpus.cases}
    texts = {key: (value[:MAX_TEXT_CHARACTERS] if isinstance(value, str)
                   else None)
             for key, value in answer["texts"].items() if key in known}
    unreadable = [key for key in answer.get("unreadable") or []
                  if key in known]
    return {"started": answer.get("started") is True, "texts": texts,
            "unreadable": unreadable}


def check_update(*, home: Path, current: Path, candidate: Path,
                 current_label: str = "", candidate_label: str = "",
                 run: Runner = subprocess.run, popen=subprocess.Popen,
                 python: str | None = None, wrap: Sequence[str] | None = None,
                 platform: str | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 record: bool = True) -> dict:
    """Replay the corpus with both versions and decide.

    Returns ``{"status": "no-corpus"}`` when there is nothing to check
    against, ``{"status": "not-checked", "reason"}`` when a check could not
    run, and otherwise ``{"status": "passed" | "held", "current",
    "candidate", "allowance", "worse", "recordings", "seconds", "receipt"}``
    with counts only. Never raises.
    """
    home = Path(home)
    corpus = load_corpus(corpus_directory(home))
    if corpus is None:
        return {"status": "no-corpus"}
    started = clock()
    try:
        outcome = _check(home, Path(current), Path(candidate), corpus,
                         run=run, popen=popen,
                         python=python or sys.executable,
                         wrap=wrap if wrap is not None else isolation(platform),
                         platform=platform or sys.platform)
    except Exception:
        outcome = {"status": "not-checked", "reason": "failed"}
    outcome["seconds"] = round(clock() - started, 1)
    outcome["recordings"] = outcome.get("recordings", len(corpus.cases))
    if record:
        receipt = {
            "schema_version": SCHEMA_VERSION, "kind": RECEIPT_KIND,
            "created": _datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"),
            "current_version": current_label,
            "candidate_version": candidate_label,
            "tolerance": TOLERANCE,
            **{key: value for key, value in outcome.items()
               if key != "per_recording"},
            "per_recording": outcome.get("per_recording", {}),
        }
        try:
            outcome["receipt"] = str(write_receipt(home, receipt))
        except OSError:
            outcome["receipt"] = ""
    outcome.pop("per_recording", None)
    return outcome


def _check(home: Path, current: Path, candidate: Path, corpus: Corpus, *,
           run: Runner, popen, python: str, wrap: Sequence[str] | None,
           platform: str) -> dict:
    if platform != "darwin":
        return {"status": "not-checked", "reason": "not-mac"}
    if wrap is None:
        return {"status": "not-checked", "reason": "no-sandbox"}
    if not (candidate / "voice_check.py").is_file():
        return {"status": "not-checked", "reason": "candidate-too-old"}
    kind = program_kind(current)
    if kind == "own" and not (current / ".models" / "bin" /
                              PROGRAMS["own"][1]).is_file():
        # As in dictation: without the runtime, the helper answers.
        kind = "fluidaudio"
    executable = PROGRAMS[kind][1]
    installed = current / ".models" / "bin" / executable
    if not installed.is_file():
        return {"status": "not-checked", "reason": "no-recogniser"}
    models = home / MODELS
    self_correction = _preference(current, "self_correction", False)
    remove_repeats = _preference(current, "remove_repeats", True)
    work = Path(tempfile.mkdtemp(prefix="whisper-face-voice-check."))
    try:
        os.chmod(work, 0o700)
        program, how = candidate_program(current, candidate, kind, work,
                                         run=run, wrap=wrap)
        if program is None:
            return {"status": "not-checked", "reason": how, "program": kind}
        mine = replay(corpus, installed, models=models,
                      self_correction=self_correction,
                      remove_repeats=remove_repeats, wrap=wrap, popen=popen)
        if not mine["started"]:
            return {"status": "not-checked",
                    "reason": "current-did-not-start", "program": kind}
        theirs = candidate_replay(
            candidate, corpus, program, models=models, python=python,
            self_correction=self_correction, remove_repeats=remove_repeats,
            wrap=wrap, run=run)
        if theirs is None:
            return {"status": "not-checked", "reason": "candidate-too-old"}
        if theirs.get("failed"):
            return {"status": "not-checked", "reason": "candidate-failed",
                    "program": kind}
        if not theirs["started"]:
            return {"status": "not-checked",
                    "reason": "candidate-did-not-start", "program": kind}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(home / "Library" / "Caches" / (executable + CHECK_SUFFIX),
                      ignore_errors=True)
    skipped = set(mine["unreadable"]) | set(theirs["unreadable"])
    both = [case.id for case in corpus.cases if case.id not in skipped]
    if not both:
        return {"status": "not-checked", "reason": "unreadable"}
    before = score(corpus, mine["texts"], both)
    after = score(corpus, theirs["texts"], both)
    found = worse(before, after)
    per_recording = {
        case_id: {"current": before.cases[case_id],
                  "candidate": after.cases[case_id]}
        for case_id in both}
    return {"status": "held" if found else "passed", "program": kind,
            "recogniser": how, "recordings": len(both),
            "unreadable": len(skipped),
            "current": before.totals(), "candidate": after.totals(),
            "allowance": allowance(before.reference_words), "worse": found,
            "per_recording": per_recording}


# ------------------------------------------------------------------ main --

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    replaying = commands.add_parser(
        "replay", help="recognise and finish every recording; print JSON")
    replaying.add_argument("--program", type=Path, required=True)
    replaying.add_argument("--corpus", type=Path, required=True)
    replaying.add_argument("--models", type=Path, required=True)
    replaying.add_argument("--self-correction", action="store_true")
    replaying.add_argument("--keep-repeats", action="store_true")
    args = parser.parse_args(argv)
    corpus = load_corpus(args.corpus)
    if corpus is None:
        print(json.dumps({"schema_version": REPLAY_SCHEMA_VERSION,
                          "started": False, "texts": {}, "unreadable": []}))
        return 1
    answer = replay(corpus, args.program, models=args.models,
                    self_correction=args.self_correction,
                    remove_repeats=not args.keep_repeats)
    print(json.dumps(answer, ensure_ascii=False, separators=(",", ":")))
    return 0 if answer["started"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
