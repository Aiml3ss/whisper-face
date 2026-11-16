# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "numpy",
#   "sounddevice",
# ]
# ///
"""Guided capture of the physical voice corpora the activation gates require.

Four features ship off and can only be enabled from a private ``0600`` receipt
built out of manually reviewed physical recordings made on the owner's own Mac.
The evaluators already exist.  What did not exist was a humane way to record the
corpora, so this session runner prompts a human through each case, records what
they actually say at exactly 16 kHz mono, and writes a manifest the existing
benchmark can consume with zero hand-editing.

This tool deliberately has no authority of its own:

* It never generates, synthesizes, or resamples audio.  Every sample comes from
  a live input device.
* It never infers an outcome label.  Every label is typed by the human after
  they have spoken and can play the take back.
* It never writes an activation receipt and never runs a benchmark.  It prints
  the exact command to run and stops.  ``--confirm-manual-review`` stays the
  human's to pass, after listening.

Usage::

    uv run scripts/capture_voice_evidence.py relisten
    uv run scripts/capture_voice_evidence.py calibration --telemetry-log dictate.log
    uv run scripts/capture_voice_evidence.py keywords --keyword Qwen --near-miss Gwen
    uv run scripts/capture_voice_evidence.py accuracy

``accuracy`` is different in kind: it approves nothing. It records the
owner's own voice saying their own names and vocabulary, so every later
recognizer change can be measured on it with benchmark_asr.py and
benchmarks/recognizer_replay.py.

Add ``--plan`` to any subcommand to see the whole session, the current progress,
and the follow-up commands without touching the microphone.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import datetime as _datetime
import io
import json
import math
import os
from pathlib import Path
import secrets
import select
import sys
import tempfile
from typing import Any, Iterator, Mapping, Sequence
import wave


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from measurement_mode import (  # noqa: E402
    CALIBRATION_LABEL,
    EVIDENCE_KEY,
    KEYWORD_LABEL,
    ORDINARY_PATH,
)

SESSION_SCHEMA_VERSION = 1
SESSION_KIND = "whisper-face/voice-evidence-capture-session"
MANIFEST_SCHEMA_VERSION = 1

# The verifier adapter, the runtime, and every consuming benchmark agree on
# exactly one capture format.  Recording at anything else silently produces a
# corpus the gates will reject.
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
DTYPE = "float32"

# whisper_verifier_adapter.MAX_AUDIO_SAMPLES is 2.4 s at 16 kHz and
# benchmark_relisten_activation.read_microspan_wav rejects anything longer.
RELISTEN_MAX_SECONDS = 2.4
# whisper_verifier_adapter.MAX_EXPECTED_CHARACTERS / MAX_EXPECTED_UTF8_BYTES.
MAX_EXPECTED_CHARACTERS = 160
MAX_EXPECTED_UTF8_BYTES = 640

EVIDENCE_TYPE_REAL = "real-recorded"
PHYSICAL_SOURCE = "physical-caller-attested"

CLIPPING_PEAK = 0.99
QUIET_PEAK = 0.05

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


class CaptureError(RuntimeError):
    """The session cannot continue safely and must not fall back to guessing."""


# --------------------------------------------------------------------------
# Prompt banks.  These are scripts for a human to read out loud.  They are not
# evidence and they never become audio: a case only exists once a person has
# spoken it into a live microphone.
# --------------------------------------------------------------------------

# Consequence-bearing spans: the kind of value where a wrong recognition costs
# real money or a real appointment.  ``near_miss`` is what the human says for
# the contradicted half of the pair, while the manifest keeps ``span`` as the
# expected text, so the verifier must disagree with the audio.
RELISTEN_SPANS: tuple[tuple[str, str], ...] = (
    ("invoice 2042", "invoice 2043"),
    ("forty two dollars", "forty three dollars"),
    ("transfer fifteen hundred", "transfer fifty hundred"),
    ("due March third", "due March thirteenth"),
    ("account 8817", "account 8870"),
    ("seven point five milligrams", "seven point nine milligrams"),
    ("ship it to Oakland", "ship it to Auckland"),
    ("delete the archive", "delete the archives"),
    ("approve the refund", "approve the refill"),
    ("total ninety six units", "total ninety five units"),
    ("flight two fourteen", "flight two forty"),
    ("suite three ten", "suite three nineteen"),
    ("twelve percent", "twenty percent"),
    ("call at four p m", "call at five p m"),
    ("order 55031", "order 55013"),
    ("batch seventy eight", "batch seventy nine"),
    ("meet at the north gate", "meet at the north gap"),
    ("paid in full", "paid in fall"),
    ("cancel the order", "cancel the offer"),
    ("two hundred grams", "two hundred grains"),
)

CALIBRATION_CONDITIONS: tuple[str, ...] = ("clean", "quiet", "noisy", "long-pause")

CALIBRATION_CONDITION_SETUP: Mapping[str, str] = {
    "clean": (
        "Normal room, normal speaking voice, nothing playing. This is your "
        "reference condition."
    ),
    "quiet": (
        "Speak softly, or sit further back from the mic than usual. Still "
        "intelligible to a person in the room, just low."
    ),
    "noisy": (
        "Add realistic background noise first: a fan, a kettle, traffic, music "
        "at conversational level. Do not shout over it."
    ),
    "long-pause": (
        "Speak the first half, pause for a slow two-count, then finish. The "
        "pause is the thing being measured, so do not rush it."
    ),
}

CALIBRATION_UTTERANCES: tuple[str, ...] = (
    "Move the Thursday review to nine fifteen and tell the team.",
    "The invoice total came to four hundred and twelve dollars.",
    "Please archive last quarter's notes before the audit starts.",
    "I need the pull request rebased on main before I merge it.",
    "Book the small meeting room for an hour on Wednesday morning.",
    "Send the signed contract back to accounts payable today.",
    "The build failed on the integration step again this afternoon.",
    "Remind me to pick up the prescription after the standup.",
    "Draft a short reply saying we will confirm the numbers tomorrow.",
    "Set the reminder for twenty minutes before the appointment.",
)

KEYWORD_POSITIVE_TEMPLATES: tuple[str, ...] = (
    "Ask {kw} to review the draft before Friday.",
    "The {kw} results landed in the shared folder.",
    "I benchmarked {kw} against the current default.",
    "Add {kw} to the agenda for Monday.",
    "We rolled {kw} out to the staging machine.",
    "Send the {kw} notes to the whole team.",
    "Can you check whether {kw} finished overnight?",
    "The {kw} configuration needs one more pass.",
    "I left a comment on the {kw} thread.",
    "Schedule time to walk through {kw} together.",
    "Nothing about {kw} changed since last week.",
    "Compare {kw} with what we shipped last quarter.",
    "The {kw} numbers look better than expected.",
    "Please pin the {kw} summary at the top.",
    "I will take another look at {kw} tomorrow.",
    "Everyone agreed that {kw} was the right call.",
    "Move the {kw} discussion later in the meeting.",
    "The {kw} report is ready for review.",
    "Forward the {kw} thread to the reviewers.",
    "We should document how {kw} is set up.",
)

KEYWORD_NEGATIVE_TEMPLATES: tuple[str, ...] = (
    "Ask {near} to review the draft before Friday.",
    "The {near} results landed in the shared folder.",
    "I benchmarked {near} against the current default.",
    "Add {near} to the agenda for Monday.",
    "We rolled {near} out to the staging machine.",
    "Send the {near} notes to the whole team.",
    "Can you check whether {near} finished overnight?",
    "The {near} configuration needs one more pass.",
    "I left a comment on the {near} thread.",
    "Schedule time to walk through {near} together.",
    "Nothing about {near} changed since last week.",
    "Compare {near} with what we shipped last quarter.",
    "The {near} numbers look better than expected.",
    "Please pin the {near} summary at the top.",
    "I will take another look at {near} tomorrow.",
    "Everyone agreed that {near} was the right call.",
    "Move the {near} discussion later in the meeting.",
    "The {near} report is ready for review.",
    "Forward the {near} thread to the reviewers.",
    "We should document how {near} is set up.",
)


# --------------------------------------------------------------------------
# Corpus specifications
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str


@dataclass(frozen=True)
class CorpusSpec:
    name: str
    title: str
    manifest_kind: str
    default_cases: int
    min_cases: int
    max_seconds: float
    min_seconds: float
    arms: tuple[str, ...]
    arm_headers: Mapping[str, str]
    questions: tuple[Question, ...]
    benchmark: str
    receipt: str
    extra_arguments: tuple[str, ...] = ()
    # The arm whose behavior the shipped runtime only produces from a receipt,
    # and the label a manifest carries when it was recorded in measurement
    # mode. `None` means the corpus has no such arm (re-listen drives its
    # verifier directly, so its manifest schema stays untouched).
    measurement_arm: str | None = None
    measurement_label: str = ORDINARY_PATH

    @property
    def multi_arm(self) -> bool:
        return len(self.arms) > 1


RELISTEN_SPEC = CorpusSpec(
    name="relisten",
    title="Selective Re-listen (ledger 18/19)",
    manifest_kind="whisper-face/relisten-activation-manifest",
    default_cases=40,
    min_cases=40,
    max_seconds=RELISTEN_MAX_SECONDS,
    min_seconds=0.25,
    arms=("take",),
    arm_headers={
        "take": (
            "Read the SPOKEN line exactly. Short and clean: the verifier only "
            "ever sees a microspan, so takes are capped at 2.4 seconds."
        ),
    },
    questions=(),
    benchmark="benchmark_relisten_activation.py",
    receipt="relisten_activation.json",
    extra_arguments=("--deadline-seconds 10",),
)

CALIBRATION_SPEC = CorpusSpec(
    name="calibration",
    title="Acoustic calibration (ledger 15)",
    manifest_kind="whisper-face/acoustic-calibration-activation-manifest",
    default_cases=40,
    min_cases=40,
    max_seconds=20.0,
    min_seconds=0.5,
    arms=("baseline", "candidate"),
    arm_headers={
        "baseline": (
            "BASELINE PASS. Run Whisper Face with its current, unmodified "
            "settings for every case in this pass. Do not change anything "
            "until the pass is finished."
        ),
        "candidate": (
            "CANDIDATE PASS. Restart Whisper Face with the measurement-mode "
            "command printed below, then repeat the same task set. The "
            "runtime applies those settings through its own calibrated front "
            "end for this session only."
        ),
    },
    questions=(
        Question(
            "recognition_correct",
            "Did Whisper Face recognize the words correctly on this take?",
        ),
        Question(
            "endpoint_correct",
            "Did it end the utterance at the right moment (no early cut, no "
            "long hang)?",
        ),
    ),
    benchmark="benchmark_acoustic_calibration_activation.py",
    receipt="acoustic_calibration_activation.json",
    measurement_arm="candidate",
    measurement_label=CALIBRATION_LABEL,
)

KEYWORDS_SPEC = CorpusSpec(
    name="keywords",
    title="Pronunciation keyword bias (ledger 22)",
    manifest_kind="whisper-face/acoustic-keyword-activation-manifest",
    default_cases=40,
    min_cases=40,
    max_seconds=15.0,
    min_seconds=0.5,
    arms=("unbiased", "biased"),
    arm_headers={
        "unbiased": (
            "UNBIASED PASS. The keyword must NOT be in the Whisper prompt for "
            "this pass. Remove it from dictionary.txt if it is there, and "
            "restart Whisper Face with no --measure argument."
        ),
        "biased": (
            "BIASED PASS. Restart Whisper Face with the measurement-mode "
            "command printed below, then repeat the same sentences. The term "
            "enters the real Whisper prompt for this session only; do not "
            "add it to dictionary.txt, which would measure a different path."
        ),
    },
    questions=(
        Question(
            "keyword_candidate_present",
            "Did the keyword appear anywhere in the recognized candidates?",
        ),
        Question(
            "keyword_selected",
            "Was the keyword the term actually selected into the text?",
        ),
    ),
    benchmark="benchmark_acoustic_keyword_activation.py",
    receipt="acoustic_keyword_activation.json",
    extra_arguments=("--memory acoustic_keyword_memory.json",),
    measurement_arm="biased",
    measurement_label=KEYWORD_LABEL,
)

# The accuracy corpus approves nothing and has no receipt. It exists so every
# later recognizer or vocabulary change can be measured on the owner's own
# voice with benchmark_asr.py and benchmarks/recognizer_replay.py.
ACCURACY_SPEC = CorpusSpec(
    name="accuracy",
    title="Accuracy corpus (your voice, your names)",
    manifest_kind="whisper-face/accuracy-corpus-cases",
    default_cases=100,
    min_cases=0,
    max_seconds=75.0,
    min_seconds=1.0,
    arms=("take",),
    arm_headers={
        "take": (
            "Read each line out loud the way you would dictate it. Return "
            "records, Return stops, Return keeps it."
        ),
    },
    questions=(),
    benchmark="benchmark_asr.py",
    receipt="",
)

CORPORA: Mapping[str, CorpusSpec] = {
    RELISTEN_SPEC.name: RELISTEN_SPEC,
    CALIBRATION_SPEC.name: CALIBRATION_SPEC,
    KEYWORDS_SPEC.name: KEYWORDS_SPEC,
    ACCURACY_SPEC.name: ACCURACY_SPEC,
}


# --------------------------------------------------------------------------
# Private, atomic, owner-only file handling
# --------------------------------------------------------------------------


def utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def gitignore_entries(root: Path) -> set[str]:
    """Return the literal path entries a checkout's .gitignore declares."""
    entries: set[str] = set()
    candidate = root / ".gitignore"
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return entries
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        entries.add(line.rstrip("/").lstrip("/"))
    return entries


def enclosing_checkout(path: Path) -> Path | None:
    """Return the git checkout that would track ``path``, if any."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def private_destination_error(path: Path) -> str | None:
    """Refuse any destination a checkout would happily commit.

    Audio, manifests, and receipts must never enter Git.  A destination outside
    every checkout is fine.  A destination inside one is only fine when the
    checkout's own ``.gitignore`` names it or one of its parent components.
    """
    resolved = path.resolve()
    checkout = enclosing_checkout(resolved)
    if checkout is None:
        return None
    try:
        relative = resolved.relative_to(checkout)
    except ValueError:  # pragma: no cover - enclosing_checkout guarantees this
        return None
    entries = gitignore_entries(checkout)
    parts = relative.parts
    covered = any(
        "/".join(parts[:index]) in entries or parts[index - 1] in entries
        for index in range(1, len(parts) + 1)
    )
    if covered:
        return None
    return (
        f"{resolved} sits inside the checkout at {checkout} and no .gitignore "
        "entry covers it. Recorded audio and manifests must never enter Git. "
        "Add the directory to .gitignore, or pass --evidence-root with a path "
        "outside the checkout."
    )


def secure_directory(path: Path) -> None:
    """Create an owner-only directory chain, tightening only what we create.

    Every directory this tool brings into existence becomes ``0700``, including
    the evidence root, so a corpus is never briefly world-readable.  Existing
    ancestors the operator already owns are left alone.
    """
    missing: list[Path] = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    try:
        for directory in reversed(missing):
            directory.mkdir(exist_ok=True)
            os.chmod(directory, DIRECTORY_MODE)
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, DIRECTORY_MODE)
    except OSError as error:
        raise CaptureError(f"cannot make {path} owner-only: {error}") from error


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write owner-only bytes so a crash never leaves a half-written case."""
    parent = path.resolve().parent
    secure_directory(parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=2,
        allow_nan=False) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))


def encode_pcm16(samples: Sequence[float]) -> bytes:
    """Convert live float32 capture to the PCM16 payload the verifiers read.

    Deliberately stdlib-only: the file format is part of the contract with the
    benchmarks and must stay verifiable without an audio stack installed.
    """
    from array import array

    frame = array("h")
    for value in samples:
        clamped = max(-1.0, min(1.0, float(value)))
        frame.append(max(-32768, min(32767, int(round(clamped * 32767.0)))))
    if sys.byteorder != "little":  # pragma: no cover - little-endian hosts
        frame.byteswap()
    return frame.tobytes()


def write_wav(path: Path, samples: Sequence[float]) -> int:
    """Write one mono 16 kHz PCM16 WAV atomically with mode 0600."""
    payload = encode_pcm16(samples)
    frames = len(payload) // 2
    if frames <= 0:
        raise CaptureError("refusing to write an empty recording")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(CHANNELS)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE_HZ)
        target.setcomptype("NONE", "not compressed")
        target.writeframes(payload)
    atomic_write_bytes(path, buffer.getvalue())
    return frames


def read_wav(path: Path) -> tuple[list[float], int]:
    """Read back one of our own WAVs for playback during review."""
    from array import array

    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.getnframes()
        payload = source.readframes(frames)
    if channels != CHANNELS or width != 2 or rate != SAMPLE_RATE_HZ:
        raise CaptureError(f"{path.name} is not mono 16 kHz PCM16")
    integers = array("h")
    integers.frombytes(payload)
    if sys.byteorder != "little":  # pragma: no cover - little-endian hosts
        integers.byteswap()
    return [value / 32768.0 for value in integers], rate


# --------------------------------------------------------------------------
# Session plan and state
# --------------------------------------------------------------------------


def new_case_token() -> str:
    """Return the ``case-<16 hex>`` shape both physical evaluators demand."""
    return f"case-{secrets.token_hex(8)}"


def valid_expected_text(value: str) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_EXPECTED_CHARACTERS
        and len(value.encode("utf-8")) <= MAX_EXPECTED_UTF8_BYTES
    )


def build_relisten_plan(count: int) -> list[dict[str, Any]]:
    if count % 2:
        raise CaptureError("re-listen needs an even count: half confirmed, half "
                           "contradicted")
    pairs = count // 2
    plan: list[dict[str, Any]] = []
    for index in range(pairs):
        span, near_miss = RELISTEN_SPANS[index % len(RELISTEN_SPANS)]
        suffix = f"{index + 1:02d}"
        for outcome in ("confirmed", "contradicted"):
            spoken = span if outcome == "confirmed" else near_miss
            if not valid_expected_text(span):
                raise CaptureError(f"prompt span is too long for the verifier: {span!r}")
            plan.append({
                "case_id": f"{outcome}-{suffix}",
                "expected_outcome": outcome,
                "say_text": spoken,
                "expected_text": span,
                "setup": (
                    "Say it exactly as written. The manifest expects the same "
                    "text, so the verifier should agree with you."
                    if outcome == "confirmed" else
                    "Say the SPOKEN line, not the expected line. The manifest "
                    "still expects the original span, so the verifier must "
                    "disagree with what it hears."
                ),
            })
    return plan


def build_calibration_plan(count: int) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index in range(count):
        condition = CALIBRATION_CONDITIONS[index % len(CALIBRATION_CONDITIONS)]
        utterance = CALIBRATION_UTTERANCES[
            (index // len(CALIBRATION_CONDITIONS)) % len(CALIBRATION_UTTERANCES)]
        plan.append({
            "case_token": new_case_token(),
            "condition": condition,
            "say_text": utterance,
            "setup": CALIBRATION_CONDITION_SETUP[condition],
        })
    return plan


def build_keyword_plan(count: int, keyword: str, near_miss: str) -> list[dict[str, Any]]:
    if count % 2:
        raise CaptureError("keywords needs an even count: half positive, half "
                           "negative")
    pairs = count // 2
    plan: list[dict[str, Any]] = []
    for index in range(pairs):
        positive = KEYWORD_POSITIVE_TEMPLATES[
            index % len(KEYWORD_POSITIVE_TEMPLATES)].format(kw=keyword)
        negative = KEYWORD_NEGATIVE_TEMPLATES[
            index % len(KEYWORD_NEGATIVE_TEMPLATES)].format(near=near_miss)
        plan.append({
            "case_token": new_case_token(),
            "keyword_expected": True,
            "say_text": positive,
            "setup": (
                f"This sentence contains the hard name {keyword!r}. Say it the "
                "way you normally would."
            ),
        })
        plan.append({
            "case_token": new_case_token(),
            "keyword_expected": False,
            "say_text": negative,
            "setup": (
                f"This sentence deliberately does NOT contain {keyword!r}; it "
                f"contains the near miss {near_miss!r}. If bias makes the "
                "recognizer hear the keyword here, that is a regression."
            ),
        })
    return plan


@dataclass
class Session:
    spec: CorpusSpec
    directory: Path
    state: dict[str, Any]

    # -- persistence -----------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.directory / "session.json"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    @classmethod
    def load(cls, spec: CorpusSpec, directory: Path) -> "Session | None":
        path = directory / "session.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise CaptureError(
                f"{path} exists but is not readable session state: {error}"
            ) from error
        if (not isinstance(state, Mapping)
                or state.get("kind") != SESSION_KIND
                or state.get("corpus") != spec.name):
            raise CaptureError(
                f"{path} is not a {spec.name} capture session. Point "
                "--session-dir somewhere else rather than mixing corpora.")
        return cls(spec, directory, dict(state))

    @classmethod
    def create(
        cls,
        spec: CorpusSpec,
        directory: Path,
        plan: Sequence[Mapping[str, Any]],
        settings: Mapping[str, Any],
    ) -> "Session":
        state = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "kind": SESSION_KIND,
            "corpus": spec.name,
            "created_utc": utc_now(),
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "settings": dict(settings),
            "telemetry": [],
            # How each arm was actually produced. Answered once by the
            # operator, at the point they start the arm, and carried into the
            # manifest so a measured corpus is never mistaken for one recorded
            # on the ordinary path.
            "measurement_mode": {},
            "cases": [
                {"plan": dict(item), "arms": {}}
                for item in plan
            ],
        }
        return cls(spec, directory, state)

    def save(self) -> None:
        self.state["updated_utc"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    # -- case access -----------------------------------------------------
    @property
    def cases(self) -> list[dict[str, Any]]:
        return self.state["cases"]

    def case_identity(self, case: Mapping[str, Any]) -> str:
        plan = case["plan"]
        return plan.get("case_id") or plan["case_token"]

    def wav_name(self, case: Mapping[str, Any], arm: str) -> str:
        identity = self.case_identity(case)
        if self.spec.multi_arm:
            return f"{identity}-{arm}.wav"
        return f"{identity}.wav"

    def arm_complete(self, case: Mapping[str, Any], arm: str) -> bool:
        record = case["arms"].get(arm)
        if not isinstance(record, Mapping) or record.get("status") != "complete":
            return False
        wav = record.get("wav")
        if wav is not None and not (self.directory / wav).exists():
            return False
        labels = record.get("labels")
        if self.spec.questions and not isinstance(labels, Mapping):
            return False
        return True

    def case_complete(self, case: Mapping[str, Any]) -> bool:
        return all(self.arm_complete(case, arm) for arm in self.spec.arms)

    def completed_cases(self) -> list[dict[str, Any]]:
        return [case for case in self.cases if self.case_complete(case)]

    def pending_for_arm(self, arm: str) -> list[dict[str, Any]]:
        return [case for case in self.cases if not self.arm_complete(case, arm)]

    def active_arm(self) -> str | None:
        for arm in self.spec.arms:
            if self.pending_for_arm(arm):
                return arm
        return None

    def record_arm(
        self,
        case: dict[str, Any],
        arm: str,
        *,
        samples: Sequence[float] | None,
        labels: Mapping[str, bool] | None = None,
        peak: float = 0.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """Persist one finished arm, refusing to silently replace a good one.

        Resuming a session must never quietly discard a case a human already
        spoke and reviewed, so overwriting is an explicit act (``--redo``).
        """
        if arm not in self.spec.arms:
            raise CaptureError(f"{arm!r} is not an arm of the {self.spec.name} corpus")
        if self.arm_complete(case, arm) and not force:
            raise CaptureError(
                f"{self.case_identity(case)} [{arm}] is already captured; use "
                "--redo to reopen it deliberately")
        if self.spec.questions and labels is None:
            raise CaptureError(
                f"{self.case_identity(case)} [{arm}] needs its outcome labels; "
                "nothing infers them")
        record: dict[str, Any] = {
            "status": "complete",
            "recorded_utc": utc_now(),
        }
        if samples is not None:
            wav_name = self.wav_name(case, arm)
            frames = write_wav(self.directory / wav_name, samples)
            record["wav"] = wav_name
            record["frames"] = frames
            record["duration_seconds"] = round(frames / SAMPLE_RATE_HZ, 3)
            record["peak"] = round(float(peak), 4)
        if labels is not None:
            record["labels"] = {
                question.key: bool(labels[question.key])
                for question in self.spec.questions
            }
        case["arms"][arm] = record
        self.save()
        self.write_manifest()
        return record

    # -- measurement mode ------------------------------------------------
    def measurement_label(self) -> str:
        """Return the label this corpus's manifest should carry.

        Absent means the ordinary path, exactly as a missing flag does
        everywhere else. Only the arm the runtime cannot otherwise produce is
        ever labelled; a baseline or unbiased pass is by definition ordinary.
        """
        arm = self.spec.measurement_arm
        if arm is None:
            return ORDINARY_PATH
        recorded = self.state.get("measurement_mode") or {}
        return (self.spec.measurement_label
                if recorded.get(arm) is True else ORDINARY_PATH)

    def record_measurement_answer(self, arm: str, used: bool) -> None:
        recorded = dict(self.state.get("measurement_mode") or {})
        recorded[arm] = bool(used)
        self.state["measurement_mode"] = recorded
        self.save()

    def measurement_answered(self, arm: str) -> bool:
        return arm in (self.state.get("measurement_mode") or {})

    def reopen(self, identity: str) -> dict[str, Any]:
        for case in self.cases:
            if self.case_identity(case) == identity:
                case["arms"] = {}
                return case
        raise CaptureError(f"no case named {identity!r} in this session")

    # -- balance ---------------------------------------------------------
    def balance(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in self.completed_cases():
            for key in self.balance_keys(case["plan"]):
                counts[key] = counts.get(key, 0) + 1
        return counts

    def balance_keys(self, plan: Mapping[str, Any]) -> tuple[str, ...]:
        if self.spec.name == "relisten":
            return (plan["expected_outcome"],)
        if self.spec.name == "calibration":
            return (plan["condition"],)
        return ("positive" if plan["keyword_expected"] else "negative",)

    def balance_requirements(self) -> dict[str, int]:
        if self.spec.name == "relisten":
            return {"confirmed": 20, "contradicted": 20}
        if self.spec.name == "calibration":
            return {condition: 8 for condition in CALIBRATION_CONDITIONS}
        return {"positive": 20, "negative": 20}

    def shortfalls(self) -> list[str]:
        counts = self.balance()
        messages = []
        for key, required in self.balance_requirements().items():
            missing = required - counts.get(key, 0)
            if missing > 0:
                label = "condition" if self.spec.name == "calibration" else "cases"
                messages.append(f"{key} {label} needs {missing} more")
        return messages

    def progress_line(self) -> str:
        done = len(self.completed_cases())
        total = len(self.cases)
        counts = self.balance()
        balance = ", ".join(
            f"{key} {counts.get(key, 0)}"
            for key in self.balance_requirements()
        )
        parts = [f"{done}/{total}", balance]
        shortfalls = self.shortfalls()
        if shortfalls:
            parts.append("; ".join(shortfalls))
        elif done == total:
            parts.append("balance requirements met")
        return " · ".join(part for part in parts if part)

    def arm_progress_line(self, arm: str) -> str:
        done = sum(1 for case in self.cases if self.arm_complete(case, arm))
        return f"{arm} pass {done}/{len(self.cases)}"

    # -- manifest --------------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        cases = self.completed_cases()
        if self.spec.name == "relisten":
            return {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "kind": self.spec.manifest_kind,
                "cases": [
                    {
                        "case_id": case["plan"]["case_id"],
                        "wav": case["arms"]["take"]["wav"],
                        "expected_text": case["plan"]["expected_text"],
                        "expected_outcome": case["plan"]["expected_outcome"],
                        "evidence_type": EVIDENCE_TYPE_REAL,
                    }
                    for case in cases
                ],
            }
        if self.spec.name == "calibration":
            return {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "kind": self.spec.manifest_kind,
                EVIDENCE_KEY: self.measurement_label(),
                "telemetry": list(self.state.get("telemetry") or []),
                "cases": [
                    {
                        "case_token": case["plan"]["case_token"],
                        "evidence_source": PHYSICAL_SOURCE,
                        "condition": case["plan"]["condition"],
                        "baseline": dict(case["arms"]["baseline"]["labels"]),
                        "candidate": dict(case["arms"]["candidate"]["labels"]),
                    }
                    for case in cases
                ],
            }
        settings = self.state["settings"]
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "kind": self.spec.manifest_kind,
            EVIDENCE_KEY: self.measurement_label(),
            "keyword": settings["keyword"],
            "app_scope": settings.get("app_scope"),
            "records": [
                {
                    "case_token": case["plan"]["case_token"],
                    "evidence_source": PHYSICAL_SOURCE,
                    "reference": {
                        "keyword_expected": bool(
                            case["plan"]["keyword_expected"]),
                    },
                    "unbiased": dict(case["arms"]["unbiased"]["labels"]),
                    "biased": dict(case["arms"]["biased"]["labels"]),
                }
                for case in cases
            ],
        }

    def write_manifest(self) -> bool:
        manifest = self.manifest()
        payload = manifest.get("cases") or manifest.get("records") or []
        if not payload:
            return False
        atomic_write_json(self.manifest_path, manifest)
        return True


# --------------------------------------------------------------------------
# Telemetry import (real trace lines only, never invented numbers)
# --------------------------------------------------------------------------


def acoustic_telemetry_fields() -> tuple[str, ...]:
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from acoustic_calibration import ACOUSTIC_TELEMETRY_FIELDS
    except Exception:  # pragma: no cover - checkout layout dependent
        return (
            "adaptive_threshold", "clipped_ratio", "derived_gain_factor",
            "duration_ms", "frame_rms_p20", "frame_rms_p50", "frame_rms_p95",
            "nonfinite_ratio", "peak_amplitude", "peak_rms", "rms",
            "sample_count", "sample_rate_hz", "silence_ratio",
            "trailing_silence_ms", "voiced_fraction",
        )
    return tuple(ACOUSTIC_TELEMETRY_FIELDS)


def extract_utterance_telemetry(text: str, *, limit: int = 256) -> list[dict[str, float]]:
    """Pull real ``utterance_acoustic`` trace objects out of a dictate log.

    Nothing is computed or filled in.  A line that is not a complete, closed
    ``utterance_acoustic`` payload is skipped rather than repaired.
    """
    fields = set(acoustic_telemetry_fields())
    records: list[dict[str, float]] = []
    for line in text.splitlines():
        marker = line.find("[trace] ")
        if marker < 0:
            continue
        try:
            payload = json.loads(line[marker + len("[trace] "):])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        if payload.get("event") != "utterance_acoustic":
            continue
        record = {
            key: value for key, value in payload.items()
            if key not in ("event", "schema_version")
        }
        if set(record) != fields:
            continue
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value)) for value in record.values()):
            continue
        records.append({key: float(value) for key, value in record.items()})
    return records[-limit:] if limit and len(records) > limit else records


def calibration_recommendation(telemetry: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Ask the existing read-only policy what the candidate settings would be."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from acoustic_calibration import recommend_calibration
    except Exception:  # pragma: no cover - checkout layout dependent
        return None
    try:
        return recommend_calibration(list(telemetry))
    except Exception:  # pragma: no cover - policy is total, but stay inert
        return None


def keyword_memory_status(
    memory_path: Path,
    keyword: str,
    app_scope: str | None,
) -> str:
    """Report, read-only, whether the keyword is already memory-eligible."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from acoustic_keyword_memory import AcousticKeywordMemory
    except Exception:  # pragma: no cover - checkout layout dependent
        return "eligibility could not be checked (acoustic_keyword_memory.py unavailable)"
    try:
        memory = AcousticKeywordMemory.loads(
            memory_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (
            f"{memory_path} does not exist yet. The keyword becomes eligible "
            "only after three exact runtime corrections and two confirmations; "
            "this tool does not and must not write that file."
        )
    except Exception as error:
        return f"{memory_path} is not readable keyword memory: {error}"
    for candidate in memory.candidates:
        if candidate.keyword == keyword and candidate.app_scope == app_scope:
            return (
                f"memory: observations {candidate.observations}, confirmations "
                f"{candidate.confirmations}, eligible {candidate.eligible} "
                f"({candidate.status})"
            )
    return (
        f"{keyword!r} has no candidate in {memory_path.name} yet. Correct the "
        "term in real dictation until memory reports it eligible; the "
        "benchmark refuses an ineligible candidate."
    )


# --------------------------------------------------------------------------
# Terminal interaction
# --------------------------------------------------------------------------


def supports_interaction() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@contextlib.contextmanager
def cbreak_terminal() -> Iterator[None]:
    """Read single keystrokes without waiting for a newline."""
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    saved = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def poll_key(timeout: float) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    return sys.stdin.read(1)


def ask_choice(choices: Mapping[str, str]) -> str:
    options = "  ".join(f"[{key}] {label}" for key, label in choices.items())
    while True:
        print(f"  {options}")
        answer = input("  > ").strip().lower()
        if answer in choices:
            return answer
        print("  Unrecognized key.")


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = input(f"  {prompt} [y/n] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Answer y or n. This label goes straight into the evidence, so "
              "there is no default and no guess.")


def meter_line(peak: float, rms: float, elapsed: float, cap: float) -> str:
    width = 28
    filled = max(0, min(width, int(round(peak * width))))
    bar = "#" * filled + "." * (width - filled)
    if peak >= CLIPPING_PEAK:
        flag = "  CLIPPING"
    elif peak < QUIET_PEAK:
        flag = "  very quiet"
    else:
        flag = ""
    return (
        f"\r  [{bar}] peak {peak:5.2f}  rms {rms:5.3f}  "
        f"{elapsed:5.2f}s / {cap:.1f}s{flag}        "
    )


# --------------------------------------------------------------------------
# Audio device access
# --------------------------------------------------------------------------


def parse_device(value: str | None) -> Any:
    """Accept either a device index or a device-name substring."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def require_macos() -> None:
    if sys.platform != "darwin":
        raise CaptureError(
            "Recording is macOS-only. The activation receipts are bound to the "
            "owner's own Mac, so a corpus captured anywhere else could never "
            "authorize anything. Use --plan to inspect the session instead."
        )


def import_sounddevice():
    try:
        import sounddevice
    except Exception as error:
        raise CaptureError(
            "sounddevice is unavailable. Run this through uv so the script's "
            f"own dependencies are installed: {error}"
        ) from error
    return sounddevice


def require_input_device(device: Any) -> str:
    sounddevice = import_sounddevice()
    try:
        sounddevice.check_input_settings(
            device=device, channels=CHANNELS, dtype=DTYPE,
            samplerate=SAMPLE_RATE_HZ)
        info = sounddevice.query_devices(device, "input")
    except Exception as error:
        raise CaptureError(
            "No usable input device at 16 kHz mono float32. Check the "
            "microphone selection and Whisper Face's microphone permission, or "
            f"pass --device: {error}"
        ) from error
    return str(info.get("name", "input device"))


def describe_devices() -> str:
    sounddevice = import_sounddevice()
    return str(sounddevice.query_devices())


def record_take(
    *,
    max_seconds: float,
    min_seconds: float,
    device: Any,
    stop_hint: str = "Press any key to stop.",
) -> tuple[list[float], float, float]:
    """Record one live take, showing a level meter, and return the samples."""
    import numpy as np

    sounddevice = import_sounddevice()
    blocks: list[Any] = []
    level = {"peak": 0.0, "rms": 0.0}

    def callback(indata, _frames, _time, _status) -> None:
        block = np.array(indata, dtype=np.float32).reshape(-1)
        blocks.append(block)
        if block.size:
            level["peak"] = float(np.max(np.abs(block)))
            level["rms"] = float(np.sqrt(np.mean(np.square(block))))

    print(f"  Recording. {stop_hint}")
    stream = sounddevice.InputStream(
        samplerate=SAMPLE_RATE_HZ,
        channels=CHANNELS,
        dtype=DTYPE,
        device=device,
        blocksize=1024,
        callback=callback,
    )
    peak_seen = 0.0
    with stream, cbreak_terminal():
        # Discard anything typed ahead while the prompt was up, so a stray
        # keystroke cannot end a take the instant it starts.
        while poll_key(0.0) is not None:
            pass
        while True:
            recorded = sum(block.size for block in blocks) / SAMPLE_RATE_HZ
            peak_seen = max(peak_seen, level["peak"])
            sys.stdout.write(
                meter_line(level["peak"], level["rms"], recorded, max_seconds))
            sys.stdout.flush()
            if recorded >= max_seconds:
                print("\n  Reached the cap for this corpus; stopped.")
                break
            if poll_key(0.05) is not None:
                print()
                break
    samples = (
        np.concatenate(blocks) if blocks
        else np.zeros(0, dtype=np.float32)
    )
    limit = int(round(max_seconds * SAMPLE_RATE_HZ))
    if samples.size > limit:
        samples = samples[:limit]
    duration = samples.size / SAMPLE_RATE_HZ
    if samples.size:
        peak_seen = max(peak_seen, float(np.max(np.abs(samples))))
    if duration < min_seconds:
        print(f"  Only {duration:.2f}s captured; that is shorter than the "
              f"{min_seconds:.2f}s floor. Record again.")
    return [float(value) for value in samples], duration, peak_seen


def play_samples(samples: Sequence[float], device: Any) -> None:
    import numpy as np

    sounddevice = import_sounddevice()
    frame = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
    sounddevice.play(frame, SAMPLE_RATE_HZ, device=device)
    sounddevice.wait()


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

RULE = "-" * 72


def render_case_header(
    session: Session,
    case: Mapping[str, Any],
    arm: str,
    index: int,
) -> str:
    plan = case["plan"]
    spec = session.spec
    lines = [
        RULE,
        f"Case {index}/{len(session.cases)}  ·  {spec.title}",
        f"Progress: {session.progress_line()}",
    ]
    if spec.multi_arm:
        lines.append(f"Pass: {session.arm_progress_line(arm)}")
    lines.append("")
    if spec.name == "relisten":
        lines.append(f"  OUTCOME UNDER TEST : {plan['expected_outcome']}")
        lines.append(f"  SAY THIS OUT LOUD  : {plan['say_text']}")
        lines.append(f"  MANIFEST EXPECTS   : {plan['expected_text']}")
    elif spec.name == "calibration":
        lines.append(f"  CONDITION          : {plan['condition']}")
        lines.append(f"  SAY THIS OUT LOUD  : {plan['say_text']}")
    else:
        expected = "contains the keyword" if plan["keyword_expected"] \
            else "must NOT contain the keyword"
        lines.append(f"  REFERENCE          : {expected}")
        lines.append(f"  SAY THIS OUT LOUD  : {plan['say_text']}")
    lines.append(f"  SETUP              : {plan['setup']}")
    lines.append(
        f"  TAKE CAP           : {spec.max_seconds:.1f}s at "
        f"{SAMPLE_RATE_HZ // 1000} kHz mono")
    return "\n".join(lines)


def render_plan(session: Session) -> str:
    spec = session.spec
    lines = [
        RULE,
        f"{spec.title} capture plan",
        f"Session directory : {session.directory}",
        f"Manifest          : {session.manifest_path}",
        f"Cases             : {len(session.cases)}",
        f"Progress          : {session.progress_line()}",
        RULE,
    ]
    for index, case in enumerate(session.cases, start=1):
        plan = case["plan"]
        marks = "".join(
            "x" if session.arm_complete(case, arm) else "."
            for arm in spec.arms
        )
        identity = session.case_identity(case)
        if spec.name == "relisten":
            detail = (
                f"{plan['expected_outcome']:<13} say {plan['say_text']!r} "
                f"-> expects {plan['expected_text']!r}"
            )
        elif spec.name == "calibration":
            detail = f"{plan['condition']:<13} {plan['say_text']}"
        else:
            reference = "positive" if plan["keyword_expected"] else "negative"
            detail = f"{reference:<13} {plan['say_text']}"
        lines.append(f"[{marks}] {index:>3}. {identity:<22} {detail}")
    return "\n".join(lines)


def render_next_commands(session: Session) -> str:
    if isinstance(session, AccuracySession):
        return render_accuracy_next_commands(session)
    spec = session.spec
    manifest = session.manifest_path
    extra = "".join(f" \\\n     {argument}" for argument in spec.extra_arguments)
    complete = len(session.completed_cases())
    here = Path(__file__).resolve()
    try:
        invocation = str(here.relative_to(REPO_ROOT))
    except ValueError:  # pragma: no cover - script copied out of the checkout
        invocation = str(here)
    lines = [
        RULE,
        "NEXT STEPS - this tool stops here, on purpose.",
        "",
        f"1. Evaluate the corpus (no approval, no runtime change, "
        f"{complete} case(s) captured):",
        "",
        f"   cd {REPO_ROOT}",
        f"   uv run {spec.benchmark} \\",
        f"     {manifest}{extra}",
        "",
        "2. Listen to every take and check it against its label. The review is "
        "the gate;",
        "   nothing here has reviewed anything for you:",
        "",
        f"   uv run {invocation} {spec.name} "
        f"--session-dir {session.directory} --review",
        "",
        "3. Only if step 1 passes and step 2 convinced you, approve by hand:",
        "",
        f"   cd {REPO_ROOT}",
        f"   uv run {spec.benchmark} \\",
        f"     {manifest}{extra} \\",
        f"     --approve-runtime {spec.receipt} \\",
        "     --confirm-manual-review",
        "",
        "The receipt is written mode 0600, is gitignored, and is bound to this",
        "Mac. Never copy one to another machine.",
        RULE,
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Session flows
# --------------------------------------------------------------------------


def label_arm(spec: CorpusSpec, arm: str) -> dict[str, bool]:
    """Collect this arm's outcome labels from the human, one question at a time."""
    labels: dict[str, bool] = {}
    for question in spec.questions:
        labels[question.key] = ask_yes_no(question.prompt)
    if spec.name == "keywords" and labels.get("keyword_selected") \
            and not labels.get("keyword_candidate_present"):
        print("  The evaluator rejects 'selected but not a candidate'. If the "
              "keyword was inserted it was, by definition, a candidate.")
        return label_arm(spec, arm)
    return labels


def capture_case(
    session: Session,
    case: dict[str, Any],
    arm: str,
    index: int,
    device: Any,
    *,
    record_audio: bool,
) -> str:
    """Run one case to completion, or return why the session should stop."""
    spec = session.spec
    samples: list[float] | None = None
    duration = 0.0
    peak = 0.0
    while True:
        print()
        print(render_case_header(session, case, arm, index))
        print()
        if samples is not None:
            print(f"  Take held in memory: {duration:.2f}s, peak {peak:.2f}")
        elif not record_audio:
            print("  Witness audio for this pass is disabled (--witness first).")
        choices: dict[str, str] = {}
        if record_audio:
            choices["r"] = "record" if samples is None else "re-record"
            if samples is not None:
                choices["p"] = "play back"
        choices["a"] = "accept case"
        choices["s"] = "skip for now"
        choices["q"] = "quit and resume later"
        answer = ask_choice(choices)
        if answer == "r":
            samples, duration, peak = record_take(
                max_seconds=spec.max_seconds,
                min_seconds=spec.min_seconds,
                device=device,
            )
            if duration < spec.min_seconds:
                samples = None
                continue
            if peak >= CLIPPING_PEAK:
                print("  That take clipped. Clipping alone makes the "
                      "calibration policy refuse a whole batch; back off the "
                      "input gain and record again.")
            elif peak < QUIET_PEAK:
                print("  That take is very quiet. Fine for the 'quiet' "
                      "condition, otherwise move closer.")
            continue
        if answer == "p":
            if samples is None:
                continue
            play_samples(samples, device)
            continue
        if answer == "s":
            return "skip"
        if answer == "q":
            return "quit"
        if record_audio and samples is None:
            print("  Record the take before accepting it.")
            continue
        labels = None
        if spec.questions:
            print()
            print("  Answer for the take you just made. These labels are the "
                  "evidence; nothing infers them.")
            labels = label_arm(spec, arm)
        session.record_arm(
            case, arm,
            samples=samples if record_audio else None,
            labels=labels,
            peak=peak,
        )
        return "done"


def run_capture(session: Session, arguments: argparse.Namespace) -> int:
    spec = session.spec
    require_macos()
    device = parse_device(arguments.device)
    device_name = require_input_device(device)
    print(f"Input device: {device_name} at {SAMPLE_RATE_HZ} Hz mono float32")
    if not supports_interaction():
        raise CaptureError(
            "This session needs an interactive terminal. Run it directly "
            "rather than through a pipe.")

    while True:
        arm = arguments.arm or session.active_arm()
        if arm is None:
            break
        pending = session.pending_for_arm(arm)
        if not pending:
            print(f"The {arm} pass is already complete.")
            break
        print()
        print(RULE)
        print(spec.arm_headers[arm])
        if spec.name == "calibration" and arm == "candidate":
            print(render_candidate_settings(session))
        elif spec.name == "keywords" and arm == "biased":
            command = measurement_command(session)
            if command:
                print()
                print("  Run the biased pass with the term in the real "
                      "Whisper prompt, for this")
                print("  session only:")
                print()
                print(f"    {command}")
                print()
                print("  That is not an activation: nothing is persisted and "
                      "it ends when you quit")
                print("  the runtime. The manifest records that the biased "
                      "pass used it.")
        print(RULE)
        if arm == spec.measurement_arm and not session.measurement_answered(
                arm):
            print()
            print("  This answer goes into the manifest and is visible to the "
                  "activation gate.")
            session.record_measurement_answer(arm, ask_yes_no(
                f"Is Whisper Face running in measurement mode for the {arm} "
                "pass right now?"))
        stopped = False
        captured = 0
        for case in pending:
            index = session.cases.index(case) + 1
            record_audio = (
                arguments.witness == "all" or arm == spec.arms[0]
            )
            outcome = capture_case(
                session, case, arm, index, device, record_audio=record_audio)
            if outcome == "quit":
                stopped = True
                break
            if outcome == "done":
                captured += 1
        if stopped:
            break
        if not captured:
            print(f"\nEvery remaining {arm} case was skipped; stopping so the "
                  "session does not loop.")
            break
        if arguments.arm:
            break

    session.save()
    session.write_manifest()
    print()
    print(RULE)
    print(f"Session saved: {session.progress_line()}")
    for shortfall in session.shortfalls():
        print(f"  still short: {shortfall}")
    print(render_next_commands(session))
    return 0


def run_review(session: Session, arguments: argparse.Namespace) -> int:
    """Replay completed takes so the human can do the manual review itself."""
    require_macos()
    device = parse_device(arguments.device)
    require_input_device(device)
    completed = session.completed_cases()
    if not completed:
        print("Nothing captured yet.")
        return 0
    print(f"Replaying {len(completed)} completed case(s). Nothing is approved "
          "here; this is only playback.")
    for index, case in enumerate(completed, start=1):
        plan = case["plan"]
        for arm in session.spec.arms:
            record = case["arms"].get(arm) or {}
            wav = record.get("wav")
            if not wav:
                continue
            path = session.directory / wav
            print()
            print(RULE)
            print(f"{index}/{len(completed)}  {session.case_identity(case)}  "
                  f"[{arm}]")
            if session.spec.name == "relisten":
                print(f"  designed outcome : {plan['expected_outcome']}")
                print(f"  spoken           : {plan['say_text']}")
                print(f"  manifest expects : {plan['expected_text']}")
            elif session.spec.name == "calibration":
                print(f"  condition        : {plan['condition']}")
                print(f"  labels           : {record.get('labels')}")
            elif session.spec.name == "accuracy":
                print(f"  kind             : {plan['kind']}")
                print(f"  you said         : {record.get('reference')}")
            else:
                print(f"  keyword expected : {plan['keyword_expected']}")
                print(f"  labels           : {record.get('labels')}")
            samples, _ = read_wav(path)
            play_samples(
                samples, None if session.spec.name == "accuracy" else device)
    print()
    print(render_next_commands(session))
    return 0


def measurement_command(session: Session) -> str | None:
    """Return the exact runtime command for this corpus's measured arm.

    The candidate/biased arm only exists inside the runtime, so the operator
    is given the literal command rather than a description of one. Measurement
    mode applies the real code path for that session and grants no authority.
    """
    spec = session.spec
    if spec.name == "calibration":
        recommendation = calibration_recommendation(
            session.state.get("telemetry") or [])
        if recommendation is None or recommendation["verdict"] != "keep":
            return None
        decisions = recommendation["decisions"]
        return (
            "uv run --locked --script dictate.py --measure "
            f"calibration:gain={decisions['gain_ceiling']['value']},"
            f"noise={decisions['noise_gate']['value']},"
            f"vad={decisions['vad_threshold']['value']},"
            f"end-silence={decisions['end_silence']['value']}")
    if spec.name == "keywords":
        keyword = (session.state.get("settings") or {}).get("keyword")
        if not keyword:
            return None
        return ("uv run --locked --script dictate.py --measure "
                f"keyword:{keyword}")
    return None


def render_candidate_settings(session: Session) -> str:
    telemetry = session.state.get("telemetry") or []
    recommendation = calibration_recommendation(telemetry)
    if recommendation is None:
        return ("\n  Candidate settings unavailable: acoustic_calibration.py "
                "could not be imported.")
    if recommendation["verdict"] != "keep":
        return (
            f"\n  The telemetry policy says {recommendation['verdict']} "
            f"({recommendation['reason']}), so there is no candidate to apply "
            "yet. Import more real utterance_acoustic traces with "
            "--telemetry-log and rerun."
        )
    decisions = recommendation["decisions"]
    return "\n".join([
        "",
        "  Candidate front-end settings derived from your imported telemetry:",
        f"    gain_ceiling  {decisions['gain_ceiling']['value']}",
        f"    noise_gate    {decisions['noise_gate']['value']}",
        f"    vad_threshold {decisions['vad_threshold']['value']}",
        f"    end_silence   {decisions['end_silence']['value']} ms",
        "    reverb        unavailable (no metric exists)",
        "",
        "  Run the candidate pass with the runtime applying exactly these,",
        "  for this session only, through its own calibrated front end:",
        "",
        f"    {measurement_command(session)}",
        "",
        "  That is not a receipt: it is never persisted, it ends when you "
        "quit the",
        "  runtime, and the manifest records that the candidate pass used it.",
    ])


# --------------------------------------------------------------------------
# Accuracy corpus: the owner's own voice, names and vocabulary
#
# Everything the owner says and every term they list is private. Terms and
# references are written only into the session directory (0700, files 0600,
# never inside a Git checkout) and printed to the owner's own terminal. No
# error message names a term; problems in a terms file are reported by line
# number.
# --------------------------------------------------------------------------

DICTIONARY_PATH = REPO_ROOT / "dictionary.txt"
# dictate.py's AUTO_MARKER. Not imported: importing dictate.py starts the app.
# tests/test_capture_voice_evidence.py pins that the two stay equal.
DICTIONARY_AUTO_MARKER = "# --- auto-learned (managed by dictate.py) ---"
ACCURACY_DEFAULT_ROOT = "~/whisper-face-evidence"
ACCURACY_TERMS_FILENAME = "terms.txt"
ACCURACY_HELPERS = (
    ("parrot-asr-helper", ".models/bin/parrot-asr-helper", ""),
    ("parrot-speech-runtime", ".models/bin/parrot-speech-runtime",
     "_speech_runtime"),
)

ESSENTIAL = "essential"
EXTENDED = "extended"

MAX_PLAN_TERMS = 15
MAX_PLAN_JARGON = 5
MAX_PLAN_NEAR_MISSES = 10
MAX_TERM_CHARACTERS = 60
MAX_REFERENCE_CHARACTERS = 2000
TERM_SLOTS = 30
ESSENTIAL_NEAR_MISS_SLOTS = 10
NEAR_MISS_SLOTS = 20
JARGON_SLOTS = 10

# Take checks. A real room never delivers exact digital zero for a quarter
# of a second; a denied or muted microphone does.
ZERO_CHECK_SECONDS = 0.25
SILENT_PEAK = 0.003
FAR_TOO_QUIET_PEAK = 0.02
CLIPPED_RATIO = 0.001
SECONDS_PER_WORD_FLOOR = 0.15
LONG_TAKE_MIN_SECONDS = 15.0

# Owner time per case, scaled from the runbook's 30 minutes for 40 short
# re-listen cases: about 22 s for a prompted sentence, a minute for a free
# dictation that has to be typed, 95 s for a long passage.
ESTIMATE_SECONDS = {"prompted": 22, "free": 60, "long": 95}

# Templates take any name, product or technical term: "Priya", "Okafor",
# "Kubernetes" and "kubectl" all read as something a person would dictate.
# Listed so that index % 4 is the position: start, middle, end, possessive.
TERM_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("start", "{t} came up again in this morning's meeting."),
    ("middle", "Thanks for the update on {t}, that really helps."),
    ("end", "Let me know if you have any questions about {t}."),
    ("possessive", "{t}'s name came up twice in the review."),
    ("start", "{t} is on the agenda for Thursday, right after the budget "
              "review."),
    ("middle", "I added a few notes about {t} to the shared doc this "
               "afternoon."),
    ("end", "The next item on the list is {t}."),
    ("possessive", "I moved {t}'s section to the top of the notes."),
    ("start", "{t} was mentioned twice in the last email thread."),
    ("middle", "I'll bring up {t} at the team meeting tomorrow morning."),
    ("end", "Before the call, I want to read everything we have on {t}."),
    ("possessive", "I bookmarked {t}'s page so I can find it again later."),
    ("start", "{t} needs to be in the summary I send tonight."),
    ("middle", "Could you write two lines about {t} for the newsletter?"),
    ("end", "Quick reminder that tomorrow's review starts with {t}."),
    ("possessive", "Has anyone seen {t}'s latest update?"),
)
# Step between one appearance of a term and its next. Coprime with 16, so a
# term never repeats a template, and 5 % 4 == 1, so it always moves position.
TERM_TEMPLATE_STEP = 5

# A near miss is whatever the owner wrote after "|": a sound-alike name, a
# word, or a phrase. These sentences mention it as words a person said or
# wrote, which reads naturally whatever it is.
NEAR_MISS_TEMPLATES: tuple[str, ...] = (
    "Did you mean {n}? I wasn't sure from your last message.",
    "I wrote {n} on a sticky note so I wouldn't forget it.",
    "She kept saying {n} on the call, so I wrote it down.",
    "Is {n} spelled the way it sounds?",
    "The note on the fridge just says {n}.",
    "I'm fairly sure the word on the sign was {n}.",
)

# No names, no products: negatives for every term in the list.
ORDINARY_SENTENCES: tuple[str, ...] = (
    "I'm running about ten minutes late, so start without me.",
    "Can you pick up milk and bread on the way home?",
    "Thanks again for dinner last night, it was lovely.",
    "I'll have the draft ready by the end of the week.",
    "Let's find a time next week to go over the plan.",
    "Please remember to water the plants while I'm away.",
    "I think we should push the launch back a few days.",
    "Could you send me the notes from this morning?",
    "The heating is broken again, so I'm working from home today.",
    "Happy birthday! I hope you have a wonderful day.",
    "I've attached the signed form, let me know if anything is missing.",
    "We're out of coffee, can someone order more?",
    "Sorry I missed your call, I'll ring you back after lunch.",
    "The kids have a half day on Friday, so I'll leave early.",
    "Just checking that you got my last message.",
    "Let me know what you think when you have a minute.",
    "I'm not sure the new layout works, can we talk it through?",
    "Traffic is terrible, I'll be there as soon as I can.",
    "Remind me to book the car service before the trip.",
    "The meeting room upstairs is booked all afternoon.",
    # Spares, used only when a sentence above holds one of the owner's terms.
    "Can we move dinner to Saturday instead?",
    "I left the spare key under the blue flower pot.",
)

# Written forms, as the owner would want them typed. All data is fictional:
# 555-01xx numbers and the reserved example.com/.org/.net domains.
FORMAT_SENTENCES: tuple[tuple[str, str], ...] = (
    ("money", "The quote came back at $1,240.50, which is under budget."),
    ("phone", "Call the front desk at 555-0142 if the door is locked."),
    ("email", "Please send the invoice to billing@example.com by Friday."),
    ("date", "The lease ends on March 31, 2027."),
    ("time", "Can we push the call to 4:30 p.m.?"),
    ("url", "The sign-up form is at example.com/register."),
    ("percent", "Sales are up 12% compared with last quarter."),
    ("number", "We ordered 250 chairs and 18 tables for the event."),
    ("money", "Can you send me $85 for the concert tickets?"),
    ("phone", "My new number is (415) 555-0187, so update your contacts."),
    ("email", "My work address is sam.lee@example.org, not the old one."),
    ("date", "Let's move the dentist appointment to Tuesday, June 9."),
    ("time", "The train leaves at 7:45 a.m. and gets in at 10:20."),
    ("url", "I put the slides on www.example.net/team/slides for everyone."),
    ("version", "Version 3.2 fixes the crash on startup."),
    ("address", "The package goes to 42 Elm Street, Apartment 5B."),
    ("money", "Rent goes up to $1,850 a month starting in May."),
    ("year", "The report covers every year from 2019 to 2024."),
    ("measurement", "Preheat the oven to 375 degrees and bake it for 25 "
                    "minutes."),
    ("code", "Your confirmation code is 48213, and it expires in 10 "
             "minutes."),
)

JARGON_TEMPLATES: tuple[str, ...] = (
    "I updated the {j} settings and pushed the change.",
    "{j} is failing again on the build machine.",
    "Can you add a short section about {j} to the README?",
    "We need to upgrade {j} before the next release.",
    "The error comes from {j}, not from our own code.",
    "I spent the morning reading through the {j} docs.",
)

DEVELOPER_SENTENCES: tuple[str, ...] = (
    "The pull request is ready, but the CI run timed out on the last step.",
    "Can you rebase the feature branch on main and push it again?",
    "I bumped the Python version to 3.12 in the config file.",
    "The API returns a 404 when the user ID is missing.",
    "Let's add a unit test for the parser before we refactor it.",
    "Run npm install and then restart the dev server.",
    "The Docker image is almost two gigabytes, so let's trim it down.",
    "I left a comment on line 42 of the README.",
    "The JSON response has a null field where we expect a list.",
    "We should cache the result instead of calling the endpoint twice.",
)

FREE_DICTATION_IDEAS: tuple[str, ...] = (
    "a text to a friend about plans for this weekend",
    "a reply to a work email you got this week",
    "a reminder to yourself about something you need to do tomorrow",
    "a message that mentions one or two of the names on your list",
    "a short note about a meeting or call you had recently",
    "a message asking someone for a favour",
    "a shopping or to-do list, said the way you'd dictate it",
    "a quick update for your team or your family",
    "a message that includes a time and a date",
    "something you'd normally type into a chat",
    "a message that mentions a product or tool you use",
    "a thank-you note to someone",
    "a comment you'd leave on a document or a pull request",
    "a message that corrects something you said earlier",
    "anything else you dictate often",
)

# Original passages, 70 to 110 words, for 20 to 40 seconds of continuous
# speech: long enough to cross the runtime's 15-second merge path.
LONG_PASSAGES: tuple[str, ...] = (
    "Hi everyone, a quick update before the long weekend. The new booking "
    "system is live, and so far the feedback has been mostly positive. A few "
    "people mentioned that the confirmation emails arrive late, so we are "
    "looking into that this week. If you notice anything else that seems "
    "off, please write it down with the time it happened and send it my way. "
    "I would rather hear about small problems now than discover them in a "
    "month. Thanks for your patience while we sort out the rough edges, and "
    "enjoy the break.",
    "Notes for the move. The van is booked for Saturday morning, and the "
    "building wants the elevator reserved at least two days ahead, so call "
    "them on Wednesday. Pack the kitchen last, because we will still be "
    "cooking until Friday night. Label every box with the room it goes to, "
    "not what is inside, since that makes unloading much faster. Ask the "
    "neighbours if they want the old bookshelf before it goes out to the "
    "curb. And remember to take photos of the meter readings on the way out.",
    "Here is where the project stands. The first round of interviews is "
    "finished, and the main theme is that people want fewer steps, not more "
    "features. The design team has a simpler version of the sign-up flow "
    "that removes two screens, and we plan to test it with a small group "
    "next week. The biggest risk right now is the data migration, which is "
    "taking longer than expected. I have asked for an extra pair of hands on "
    "that part. Otherwise we are on track for the end of the month.",
    "Hey, I just got back from the trip and wanted to tell you about it "
    "before I forget everything. The first two days it rained nonstop, so we "
    "spent most of the time in museums and little cafes, which honestly "
    "turned out to be the best part. Once the weather cleared up we rented "
    "bikes and rode along the coast for hours. The views were unreal. I took "
    "far too many pictures, so I will send you the good ones this weekend. "
    "We should plan something like that together next year.",
    "Some thoughts after today's workshop. The group agreed that the current "
    "process works, but it depends too much on one or two people remembering "
    "every step. If either of them is away, things stall. The suggestion "
    "that got the most support was to write a short checklist and keep it "
    "next to the shared calendar, so anyone can pick up where someone else "
    "left off. It is not exciting, but it is cheap and we can try it "
    "tomorrow. Let's review how it went at the end of next week.",
)

ACCURACY_HINTS = {
    "near-miss": "Not a name from your list: say it as written.",
    "format": ("Say it the way you normally would; this is how it should "
               "come out written."),
    "free": "Speak first, then type what you said.",
    "long": "Read it straight through at your normal pace, about 30 seconds.",
}


@dataclass(frozen=True)
class Term:
    text: str
    near_miss: str | None = None
    jargon: bool = False


@dataclass(frozen=True)
class TermList:
    names: tuple[Term, ...]
    jargon: tuple[Term, ...]
    notes: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.names) + len(self.jargon)


def _clean_term(value: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFC", value).split())


def parse_terms(text: str, *, source: str = "terms file") -> TermList:
    """Parse ``Name``, ``Name | near-miss`` and ``jargon: term`` lines.

    Blank lines and ``#`` comments are ignored, ``-term`` lines are
    dictionary bans and ignored, and reading stops at dictate.py's
    auto-learned marker so only typed terms count. Notes name a line number,
    never a term.
    """
    names: list[Term] = []
    jargon: list[Term] = []
    notes: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line == DICTIONARY_AUTO_MARKER:
            break
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        is_jargon = line.casefold().startswith("jargon:")
        if is_jargon:
            line = line[len("jargon:"):]
        term_text, _bar, near_text = line.partition("|")
        term = _clean_term(term_text)
        near = _clean_term(near_text) or None
        if not term:
            notes.append(f"{source} line {number}: no term before '|'; "
                         "skipped.")
            continue
        if len(term) > MAX_TERM_CHARACTERS or (
                near and len(near) > MAX_TERM_CHARACTERS):
            notes.append(f"{source} line {number}: longer than "
                         f"{MAX_TERM_CHARACTERS} characters; skipped.")
            continue
        if near and near.casefold() == term.casefold():
            notes.append(f"{source} line {number}: the near miss is the "
                         "term itself; the near miss is ignored.")
            near = None
        key = term.casefold()
        if key in seen:
            notes.append(f"{source} line {number}: repeats an earlier term; "
                         "skipped.")
            continue
        seen.add(key)
        entry = Term(term, near, is_jargon)
        (jargon if is_jargon else names).append(entry)
    return TermList(tuple(names), tuple(jargon), tuple(notes))


def resolve_terms_path(arguments: argparse.Namespace, root: Path) -> Path:
    """--terms, else <evidence root>/terms.txt, else the checkout's dictionary."""
    if getattr(arguments, "terms", None):
        return Path(arguments.terms).expanduser().resolve()
    written = root / ACCURACY_TERMS_FILENAME
    if written.is_file():
        return written
    return DICTIONARY_PATH


def load_terms(path: Path) -> TermList:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as error:
        raise CaptureError(
            f"no terms file at {path}. Write your names and terms one per "
            "line (Name, or Name | sound-alike, or jargon: term) and pass "
            "--terms, or put them in ~/whisper-face-evidence/terms.txt."
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise CaptureError(
            f"cannot read {path} as UTF-8 text ({type(error).__name__})"
        ) from error
    terms = parse_terms(text, source=path.name)
    if not terms.total:
        raise CaptureError(
            f"{path} lists no names or terms to test. Put one per line "
            "(Name, or Name | sound-alike, or jargon: term), above the "
            "auto-learned marker in a dictionary.")
    return terms


def terms_fingerprint(terms: TermList) -> str:
    import hashlib

    payload = json.dumps(
        [[term.text, term.near_miss, term.jargon]
         for term in (*terms.names, *terms.jargon)],
        ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def possessive(term: str) -> str:
    return term + ("'" if term[-1:].casefold() == "s" else "'s")


def fill_term(template: str, term: str) -> str:
    return template.replace("{t}'s", possessive(term)).replace("{t}", term)


def found_terms(text: str, vocabulary: Sequence[str]) -> list[str]:
    """The listed terms a reference contains, as whole words, any case."""
    import re

    folded = text.casefold()
    return [
        term for term in vocabulary
        if re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)",
                     folded)
    ]


def _accuracy_case(kind: str, say_text: str, reference: str | None,
                   vocabulary: Sequence[str], **labels: Any) -> dict[str, Any]:
    if kind == "free":
        minimum, maximum, estimate = 1.0, 60.0, ESTIMATE_SECONDS["free"]
    elif kind == "long":
        minimum, maximum, estimate = (
            LONG_TAKE_MIN_SECONDS, 75.0, ESTIMATE_SECONDS["long"])
    else:
        words = len(say_text.split())
        minimum = round(max(1.0, SECONDS_PER_WORD_FLOOR * words), 2)
        maximum, estimate = 30.0, ESTIMATE_SECONDS["prompted"]
    terms = found_terms(reference, vocabulary) if reference else []
    must_not = [
        term for term in labels.pop("must_not_write", ()) if term not in terms]
    case = {
        "kind": kind,
        "say_text": say_text,
        "reference": reference,
        "hint": ACCURACY_HINTS.get(kind, ""),
        "terms": terms,
        "must_not_write": must_not,
        "min_seconds": minimum,
        "max_seconds": maximum,
        "estimate_seconds": estimate,
    }
    case.update(labels)
    return case


def _spread(groups: Sequence[Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Interleave groups evenly so any stretch of the plan mixes every kind."""
    keyed = []
    for group_index, group in enumerate(groups):
        for item_index, item in enumerate(group):
            keyed.append(((item_index + 0.5) / len(group), group_index,
                          item_index, item))
    return [item for *_key, item in sorted(keyed, key=lambda entry: entry[:3])]


def build_accuracy_plan(terms: TermList) -> list[dict[str, Any]]:
    """The fixed plan: an essential set of 40, then an optional extended 60.

    Deterministic: the same term list always gives the same plan, case ids
    included. Each term appears at least twice, never twice in one template,
    and its next appearance always moves position in the sentence.
    """
    names = list(terms.names[:MAX_PLAN_TERMS])
    jargon = list(terms.jargon[:MAX_PLAN_JARGON])
    vocabulary = [term.text for term in (*names, *jargon)]

    term_cases: list[dict[str, Any]] = []
    for round_index in range(len(TERM_TEMPLATES)):
        for term_index, term in enumerate(names):
            if len(term_cases) >= TERM_SLOTS:
                break
            template_index = (
                term_index + TERM_TEMPLATE_STEP * round_index
            ) % len(TERM_TEMPLATES)
            position, template = TERM_TEMPLATES[template_index]
            text = fill_term(template, term.text)
            term_cases.append(_accuracy_case(
                "term", text, text, vocabulary,
                template=f"term-{template_index + 1:02d}", position=position))
        if len(term_cases) >= TERM_SLOTS:
            break

    near_entries = [term for term in (*names, *jargon) if term.near_miss]
    near_entries = near_entries[:MAX_PLAN_NEAR_MISSES]
    near_cases: list[dict[str, Any]] = []
    for round_index in range(2):
        for index, term in enumerate(near_entries):
            template_index = (index + 3 * round_index) % len(
                NEAR_MISS_TEMPLATES)
            text = NEAR_MISS_TEMPLATES[template_index].replace(
                "{n}", term.near_miss or "")
            near_cases.append(_accuracy_case(
                "near-miss", text, text, vocabulary,
                template=f"near-miss-{template_index + 1:02d}",
                near_miss=term.near_miss, must_not_write=[term.text]))
    for sentence in ORDINARY_SENTENCES:
        if len(near_cases) >= NEAR_MISS_SLOTS:
            break
        if found_terms(sentence, vocabulary):
            continue
        near_cases.append(_accuracy_case(
            "ordinary", sentence, sentence, vocabulary,
            must_not_write=vocabulary))
    near_cases = near_cases[:NEAR_MISS_SLOTS]

    format_cases = [
        _accuracy_case("format", text, text, vocabulary, covers=covers)
        for covers, text in FORMAT_SENTENCES
    ]
    jargon_cases: list[dict[str, Any]] = []
    for round_index in range(2):
        for index, term in enumerate(jargon):
            template_index = (index + 3 * round_index) % len(JARGON_TEMPLATES)
            text = JARGON_TEMPLATES[template_index].replace("{j}", term.text)
            jargon_cases.append(_accuracy_case(
                "jargon", text, text, vocabulary,
                template=f"jargon-{template_index + 1:02d}"))
    for sentence in DEVELOPER_SENTENCES:
        if len(jargon_cases) >= JARGON_SLOTS:
            break
        jargon_cases.append(_accuracy_case(
            "jargon", sentence, sentence, vocabulary, template="developer"))
    free_cases = [
        _accuracy_case(
            "free", f"Say something you'd really dictate. Idea: {idea}.",
            None, vocabulary)
        for idea in FREE_DICTATION_IDEAS
    ]
    long_cases = [
        _accuracy_case("long", passage, passage, vocabulary)
        for passage in LONG_PASSAGES
    ]

    essential = _spread([term_cases, near_cases[:ESSENTIAL_NEAR_MISS_SLOTS]])
    extended = _spread([
        near_cases[ESSENTIAL_NEAR_MISS_SLOTS:], format_cases, jargon_cases,
        free_cases, long_cases,
    ])
    plan: list[dict[str, Any]] = []
    for part, items in ((ESSENTIAL, essential), (EXTENDED, extended)):
        for item in items:
            plan.append({
                "case_id": f"acc-{len(plan) + 1:03d}", "part": part, **item})
    return plan


def accuracy_settings(terms: TermList, source: Path) -> dict[str, Any]:
    return {
        "terms_source": str(source),
        "terms_sha256": terms_fingerprint(terms),
        "terms_listed": terms.total,
        "terms": [
            {"text": term.text, "near_miss": term.near_miss,
             "jargon": term.jargon}
            for term in (*terms.names[:MAX_PLAN_TERMS],
                         *terms.jargon[:MAX_PLAN_JARGON])
        ],
    }


def checkout_destination_error(path: Path) -> str | None:
    """The accuracy corpus must sit outside every Git checkout.

    Stricter than private_destination_error: recognizer_replay.py refuses a
    recording folder inside the checkout even when .gitignore covers it.
    """
    resolved = path.resolve()
    checkout = enclosing_checkout(resolved)
    if checkout is None:
        return None
    return (
        f"{resolved} sits inside the Git checkout at {checkout}. Your voice "
        "and your terms must never be where Git could commit them; pass "
        f"--evidence-root with a folder outside it, such as "
        f"{ACCURACY_DEFAULT_ROOT}.")


class AccuracySession(Session):
    """A fixed plan of prompted takes, each a WAV plus what was really said."""

    ARM = "take"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.jsonl"

    @property
    def cases_path(self) -> Path:
        return self.directory / "cases.json"

    def status(self, case: Mapping[str, Any]) -> str:
        if self.arm_complete(case, self.ARM):
            return "complete"
        record = case["arms"].get(self.ARM) or {}
        return "skipped" if record.get("status") == "skipped" else "pending"

    def part_cases(self, part: str) -> list[dict[str, Any]]:
        return [case for case in self.cases if case["plan"]["part"] == part]

    def pending(self, part: str) -> list[dict[str, Any]]:
        return [case for case in self.part_cases(part)
                if self.status(case) == "pending"]

    @property
    def extended_opted_in(self) -> bool:
        return bool(self.state.get("extended_opted_in"))

    def opt_into_extended(self) -> None:
        self.state["extended_opted_in"] = True
        self.save()

    def next_case(self) -> dict[str, Any] | None:
        essential = self.pending(ESSENTIAL)
        if essential:
            return essential[0]
        if self.extended_opted_in:
            extended = self.pending(EXTENDED)
            if extended:
                return extended[0]
        return None

    def minutes_left(self, part: str) -> int:
        seconds = sum(case["plan"]["estimate_seconds"]
                      for case in self.pending(part))
        return math.ceil(seconds / 60) if seconds else 0

    def progress_text(self, case: Mapping[str, Any]) -> str:
        part = case["plan"]["part"]
        cases = self.part_cases(part)
        position = next(index for index, item in enumerate(cases, 1)
                        if item is case)
        minutes = max(1, self.minutes_left(part))
        label = "" if part == ESSENTIAL else "Extended part, "
        unit = "minute" if minutes == 1 else "minutes"
        return (f"{label}{position} of {len(cases)}, about {minutes} {unit} "
                "left")

    def summary(self) -> str:
        parts = []
        for part, name in ((ESSENTIAL, "Essential set"),
                           (EXTENDED, "Extended part")):
            cases = self.part_cases(part)
            done = sum(1 for case in cases if self.status(case) == "complete")
            skipped = sum(1 for case in cases if self.status(case) == "skipped")
            line = f"{name}: {done} of {len(cases)} recorded"
            if skipped:
                line += f", {skipped} skipped"
            pending = self.pending(part)
            if pending:
                line += f", about {self.minutes_left(part)} minutes to go"
            parts.append(line)
        return "; ".join(parts)

    def accept(
        self,
        case: dict[str, Any],
        samples: Sequence[float],
        *,
        reference: str,
        edited: bool,
        peak: float,
    ) -> dict[str, Any]:
        reference = " ".join(str(reference).split())
        if not reference:
            raise CaptureError(
                f"{self.case_identity(case)} needs what you said")
        if len(reference) > MAX_REFERENCE_CHARACTERS:
            raise CaptureError(
                f"{self.case_identity(case)}: what you said is longer than "
                f"{MAX_REFERENCE_CHARACTERS} characters")
        identity = self.case_identity(case)
        # The sidecar first: a crash between the two files leaves a text with
        # no recording, which recognizer_replay ignores, never a recording
        # with no text, which it refuses.
        atomic_write_bytes(self.directory / f"{identity}.txt",
                           (reference + "\n").encode("utf-8"))
        frames = write_wav(self.directory / f"{identity}.wav", samples)
        case["arms"][self.ARM] = {
            "status": "complete",
            "recorded_utc": utc_now(),
            "wav": f"{identity}.wav",
            "frames": frames,
            "duration_seconds": round(frames / SAMPLE_RATE_HZ, 3),
            "peak": round(float(peak), 4),
            "reference": reference,
            "reference_edited": bool(edited),
        }
        self.save()
        self.write_manifest()
        return case["arms"][self.ARM]

    def skip(self, case: dict[str, Any], *, group: str | None = None) -> None:
        case["arms"][self.ARM] = {
            "status": "skipped", "skipped_utc": utc_now(), "group": group}
        self.save()
        self.write_manifest()

    def skip_group(self, kind: str) -> int:
        skipped = 0
        for case in self.pending(EXTENDED):
            if case["plan"]["kind"] == kind:
                case["arms"][self.ARM] = {
                    "status": "skipped", "skipped_utc": utc_now(),
                    "group": kind}
                skipped += 1
        self.save()
        self.write_manifest()
        return skipped

    def reopen(self, identity: str) -> dict[str, Any]:
        case = super().reopen(identity)
        for suffix in (".wav", ".txt"):
            with contextlib.suppress(FileNotFoundError):
                (self.directory / f"{identity}{suffix}").unlink()
        return case

    def case_labels(self, case: Mapping[str, Any]) -> dict[str, Any]:
        plan = case["plan"]
        record = case["arms"][self.ARM]
        vocabulary = [term["text"] for term in
                      (self.state.get("settings") or {}).get("terms", [])]
        reference = record["reference"]
        terms = found_terms(reference, vocabulary)
        return {
            "id": self.case_identity(case),
            "audio": record["wav"],
            "text": reference,
            "kind": plan["kind"],
            "part": plan["part"],
            "prompt": plan["say_text"],
            "reference_edited": record.get("reference_edited", False),
            "terms": terms,
            "near_miss": plan.get("near_miss"),
            "must_not_write": [
                term for term in plan.get("must_not_write", [])
                if term not in terms],
            "template": plan.get("template"),
            "position": plan.get("position"),
            "covers": plan.get("covers"),
            "duration_seconds": record.get("duration_seconds"),
            "peak": record.get("peak"),
            "recorded_utc": record.get("recorded_utc"),
        }

    def manifest(self) -> dict[str, Any]:
        labelled = [self.case_labels(case) for case in self.completed_cases()]
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "kind": self.spec.manifest_kind,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "planned_cases": len(self.cases),
            "cases": labelled,
            "skipped": [self.case_identity(case) for case in self.cases
                        if self.status(case) == "skipped"],
        }

    def write_manifest(self) -> bool:
        document = self.manifest()
        if not document["cases"]:
            return False
        lines = [
            json.dumps({key: case[key] for key in
                        ("id", "audio", "text", "kind", "terms")},
                       ensure_ascii=False)
            for case in document["cases"]
        ]
        atomic_write_bytes(
            self.manifest_path, ("\n".join(lines) + "\n").encode("utf-8"))
        atomic_write_json(self.cases_path, document)
        return True


def terminal_app_name() -> str:
    names = {
        "Apple_Terminal": "Terminal",
        "iTerm.app": "iTerm",
        "vscode": "Visual Studio Code",
        "WezTerm": "WezTerm",
        "ghostty": "Ghostty",
        "WarpTerminal": "Warp",
    }
    return names.get(os.environ.get("TERM_PROGRAM", ""), "your terminal app")


def microphone_blocked_message() -> str:
    return (
        "The microphone is sending only silence, so nothing can be recorded: "
        f"allow {terminal_app_name()} in System Settings > Privacy & "
        "Security > Microphone, then run this again.")


def take_problem(
    plan: Mapping[str, Any],
    samples: Sequence[float],
    duration: float,
    peak: float,
) -> str | None:
    """One plain line when a take will not be usable, else None.

    Raises when the input is exact digital silence: that is a denied or muted
    microphone, and every later take would be silent too.
    """
    if (len(samples) >= ZERO_CHECK_SECONDS * SAMPLE_RATE_HZ
            and not any(samples)):
        raise CaptureError(microphone_blocked_message())
    if duration < plan["min_seconds"]:
        if plan["kind"] == "long":
            return (f"Only {duration:.1f} s; a long take needs at least "
                    f"{plan['min_seconds']:.0f} s. Read it at your normal "
                    "pace.")
        return (f"Only {duration:.1f} s, far too short for this prompt. Did "
                "it stop early?")
    if peak < SILENT_PEAK:
        return ("Nothing was heard. Check the microphone is the one you "
                "dictate with (--list-devices), then record again.")
    if peak < FAR_TOO_QUIET_PEAK:
        return ("Far too quiet to use. Move closer or turn the input level "
                "up, then record again.")
    clipped = sum(1 for value in samples if abs(value) >= CLIPPING_PEAK)
    if samples and clipped / len(samples) >= CLIPPED_RATIO:
        return ("Clipped: too loud for the microphone. Move back a little or "
                "turn the input level down.")
    return None


def read_char() -> str:
    return sys.stdin.read(1)


def read_key() -> str:
    """One keystroke without Return; Return itself comes back as newline."""
    with cbreak_terminal():
        key = read_char()
    if key == "":
        return "q"
    return "\n" if key in ("\r", "\n") else key


def edit_line(prompt: str, prefill: str = "") -> str:
    """Edit one line that starts out as ``prefill``, so only changes are typed.

    Written here because macOS's libedit ignores readline's pre-filled text.
    Left/right arrows, Ctrl-A/Ctrl-E, Backspace, forward Delete, Ctrl-W
    (word) and Ctrl-U (all before the cursor) edit; Return finishes.
    """
    import shutil

    columns = max(20, shutil.get_terminal_size((80, 24)).columns)
    prefix = "  > "
    text = list(prefill)
    cursor = len(text)
    shown_row = 0

    def render() -> None:
        nonlocal shown_row
        out = "\r" + (f"\x1b[{shown_row}A" if shown_row else "") + "\x1b[J"
        out += prefix + "".join(text)
        total = len(prefix) + len(text)
        end_row = (total - 1) // columns if total else 0
        target = len(prefix) + cursor
        target_row = min(target // columns, end_row)
        target_column = min(target - target_row * columns, columns - 1)
        up = end_row - target_row
        out += "\r" + (f"\x1b[{up}A" if up else "")
        out += f"\x1b[{target_column}C" if target_column else ""
        shown_row = target_row
        sys.stdout.write(out)
        sys.stdout.flush()

    print(prompt)
    with cbreak_terminal():
        render()
        while True:
            char = read_char()
            if char in ("", "\r", "\n"):
                break
            if char in ("\x7f", "\x08"):
                if cursor:
                    del text[cursor - 1]
                    cursor -= 1
            elif char == "\x15":
                del text[:cursor]
                cursor = 0
            elif char == "\x17":
                start = cursor
                while start and text[start - 1] == " ":
                    start -= 1
                while start and text[start - 1] != " ":
                    start -= 1
                del text[start:cursor]
                cursor = start
            elif char == "\x01":
                cursor = 0
            elif char == "\x05":
                cursor = len(text)
            elif char == "\x1b":
                lead = read_char()
                if lead in ("[", "O"):
                    sequence = ""
                    final = read_char()
                    while final and final in "0123456789;":
                        sequence += final
                        final = read_char()
                    if final == "D":
                        cursor = max(0, cursor - 1)
                    elif final == "C":
                        cursor = min(len(text), cursor + 1)
                    elif final == "H":
                        cursor = 0
                    elif final == "F":
                        cursor = len(text)
                    elif final == "~" and sequence == "3" and cursor < len(text):
                        del text[cursor]
            elif char.isprintable():
                text.insert(cursor, char)
                cursor += 1
            render()
    cursor = len(text)
    render()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(text)


def render_accuracy_case(session: AccuracySession,
                         case: Mapping[str, Any]) -> str:
    import textwrap

    plan = case["plan"]
    lines = ["", session.progress_text(case), ""]
    lines.append(textwrap.fill(plan["say_text"], width=76,
                               initial_indent="  ", subsequent_indent="  "))
    if plan.get("hint"):
        lines.append(f"  ({plan['hint']})")
    return "\n".join(lines)


def before_take_options(plan: Mapping[str, Any]) -> str:
    options = ["Return record", "s skip"]
    if plan["kind"] == "free":
        options.append("F skip all free dictations")
    if plan["kind"] == "long":
        options.append("L skip all long takes")
    options.append("q quit")
    return "  " + "   ".join(options)


def after_take_options(problem: str | None) -> str:
    if problem:
        return ("  Return record again   a keep it anyway   p play   "
                "s skip   q quit")
    return ("  Return keep   r redo   p play   e edit what you said   "
            "s skip   q quit")


def run_accuracy_case(
    session: AccuracySession,
    case: dict[str, Any],
    device: Any,
) -> str:
    """Prompt, record, check, and keep one case. Returns what happened."""
    plan = case["plan"]
    print(render_accuracy_case(session, case))
    print()
    print(before_take_options(plan))
    while True:
        key = read_key()
        if key in ("\n", "r"):
            break
        if key == "s":
            session.skip(case)
            print("  Skipped.")
            return "skipped"
        if key == "q":
            return "quit"
        if key == "F" and plan["kind"] == "free":
            count = session.skip_group("free")
            print(f"  Skipped all {count} remaining free dictations.")
            return "skipped"
        if key == "L" and plan["kind"] == "long":
            count = session.skip_group("long")
            print(f"  Skipped all {count} remaining long takes.")
            return "skipped"

    while True:  # one pass per take
        try:
            samples, duration, peak = record_take(
                max_seconds=plan["max_seconds"], min_seconds=0.0,
                device=device, stop_hint="Return to stop.")
        except CaptureError:
            raise
        except Exception as error:
            raise CaptureError(
                f"the microphone could not be opened ({type(error).__name__}"
                f"). Allow {terminal_app_name()} in System Settings > Privacy "
                "& Security > Microphone, then run this again.") from error
        problem = take_problem(plan, samples, duration, peak)
        reference = plan["reference"]
        while True:  # the menu for this take
            print(f"  {problem}" if problem else f"  {duration:.1f} s.")
            print(after_take_options(problem))
            key = read_key()
            if (key == "\n" and problem) or key == "r":
                break  # record again
            if key == "p":
                play_samples(samples, None)
                continue
            if key == "s":
                session.skip(case)
                print("  Skipped.")
                return "skipped"
            if key == "q":
                return "quit"
            edited = False
            if key == "e" or (key in ("\n", "a") and reference is None):
                prompt = ("  Type what you said, then Return:"
                          if reference is None else
                          "  Change it to what you said, then Return "
                          "(arrows move, Ctrl-U clears):")
                typed = " ".join(edit_line(prompt, reference or "").split())
                if not typed:
                    print("  Nothing typed; the take is still here.")
                    continue
                edited = reference is None or typed != reference
                reference = typed
            elif key not in ("\n", "a"):
                continue
            session.accept(case, samples, reference=reference, edited=edited,
                           peak=peak)
            print("  Saved.")
            return "done"


def render_accuracy_plan(session: AccuracySession, *, started: bool) -> str:
    settings = session.state.get("settings") or {}
    terms = settings.get("terms") or []
    near = sum(1 for term in terms if term.get("near_miss"))
    jargon = sum(1 for term in terms if term.get("jargon"))
    essential = session.part_cases(ESSENTIAL)
    extended = session.part_cases(EXTENDED)
    minutes = {part: math.ceil(sum(case["plan"]["estimate_seconds"]
                                   for case in session.part_cases(part)) / 60)
               for part in (ESSENTIAL, EXTENDED)}
    lines = [
        RULE,
        f"{session.spec.title}: plan",
        f"Session directory : {session.directory}",
        f"Terms             : {len(terms) - jargon} names and terms, "
        f"{jargon} jargon, {near} with a near miss "
        f"(from {settings.get('terms_source')})",
        f"Essential set     : {len(essential)} cases, about "
        f"{minutes[ESSENTIAL]} minutes",
        f"Extended part     : {len(extended)} more, about "
        f"{minutes[EXTENDED]} minutes, only if you ask for it (--extended)",
        f"Progress          : {session.summary()}",
    ]
    if not started:
        lines.append("                    Not started. The plan is fixed when "
                     "you start recording.")
    lines.append(RULE)
    marks = {"complete": "x", "skipped": "-", "pending": "."}
    for index, case in enumerate(session.cases, start=1):
        plan = case["plan"]
        if index == len(essential) + 1:
            lines.append("--- extended part ---")
        text = plan["say_text"]
        if plan["kind"] == "long":
            text = f"{text[:60].rstrip()}... ({len(text.split())} words)"
        lines.append(
            f"[{marks[session.status(case)]}] {index:>3}. "
            f"{session.case_identity(case)}  {plan['kind']:<9} {text}")
    return "\n".join(lines)


def render_accuracy_next_commands(session: AccuracySession) -> str:
    import shlex

    root = session.directory.parent
    complete = len(session.completed_cases())
    lines = [RULE, "NEXT STEPS - this tool stops here, on purpose.", ""]
    if not complete:
        lines += ["Nothing is recorded yet, so there is nothing to measure.",
                  RULE]
        return "\n".join(lines)
    today = _datetime.date.today().isoformat()
    quoted = shlex.quote
    lines += [
        f"{complete} take(s) and what you said are in {session.directory}.",
        "Nothing has left this Mac, and nothing below sends anything "
        "anywhere.",
        "",
        "1. Transcribe every take with each speech program. Keep --transcripts",
        "   private: the default transcripts file is committed to the "
        "repository.",
        "",
        f"   cd {quoted(str(REPO_ROOT))}",
    ]
    for _name, helper, suffix in ACCURACY_HELPERS:
        transcripts = root / f"owner_transcripts{suffix}.json"
        lines += [
            f"   uv run benchmarks/recognizer_replay.py record --helper "
            f"{helper} \\",
            f"     --wav-dir {quoted(str(session.directory))} \\",
            f"     --transcripts {quoted(str(transcripts))}",
        ]
    lines += ["", "2. Score accuracy against what you said:", ""]
    for name, helper, suffix in ACCURACY_HELPERS:
        engines = "parakeet-unified mlx-tiny" if not suffix else \
            "parakeet-unified"
        output = root / "runs" / f"{today}-{name}"
        lines += [
            f"   uv run benchmark_asr.py --dataset "
            f"{quoted(str(session.manifest_path))} \\",
            f"     --engines {engines} --parrot-helper {helper} \\",
            f"     --limit 1000 --formatting-scoring --output-dir "
            f"{quoted(str(output))}",
        ]
    lines += [
        "",
        "cases.json beside the recordings carries the labels (which term, "
        "which near",
        "miss, which position) for the evaluator. Every result stays under",
        f"{root}.",
        RULE,
    ]
    return "\n".join(lines)


def run_accuracy_capture(
    session: AccuracySession,
    arguments: argparse.Namespace,
) -> int:
    require_macos()
    device = parse_device(arguments.device)
    device_name = require_input_device(device)
    if not supports_interaction():
        raise CaptureError(
            "This session needs an interactive terminal. Run it directly "
            "rather than through a pipe.")
    if arguments.extended and not session.extended_opted_in:
        session.opt_into_extended()
    print(f"Input device: {device_name}. {session.spec.arm_headers['take']}")

    essential = session.part_cases(ESSENTIAL)
    extended = session.part_cases(EXTENDED)
    half = math.ceil(len(extended) / 2)
    while True:
        case = session.next_case()
        if case is None:
            if (not session.extended_opted_in
                    and session.pending(EXTENDED)):
                recorded = sum(1 for item in essential
                               if session.status(item) == "complete")
                print()
                print(f"The essential set is complete: {recorded} of "
                      f"{len(essential)} recorded, saved in "
                      f"{session.directory}.")
                print(f"Press x to carry on with the extended part "
                      f"({len(session.pending(EXTENDED))} more, about "
                      f"{session.minutes_left(EXTENDED)} minutes); any other "
                      "key stops here.")
                if read_key() in ("x", "X"):
                    session.opt_into_extended()
                    continue
            break
        outcome = run_accuracy_case(session, case, device)
        if outcome == "quit":
            following = session.next_case()
            print()
            print(f"Stopped. {session.summary()}.")
            if following is not None:
                print("Run the same command to pick up at case "
                      f"{session.cases.index(following) + 1}.")
            return 0
        if (case["plan"]["part"] == EXTENDED
                and not session.state.get("break_offered")
                and len(extended) - len(session.pending(EXTENDED)) >= half
                and session.pending(EXTENDED)):
            session.state["break_offered"] = True
            session.save()
            following = session.next_case()
            print()
            print("Halfway through the extended part: a good moment for a "
                  "break. q stops here and the")
            print(f"next run starts at case "
                  f"{session.cases.index(following) + 1}; Return keeps "
                  "going.")
            if read_key() == "q":
                print(f"Stopped. {session.summary()}.")
                return 0

    session.save()
    session.write_manifest()
    print()
    print(f"Session saved. {session.summary()}.")
    print(render_accuracy_next_commands(session))
    return 0


def accuracy_main(
    spec: CorpusSpec,
    directory: Path,
    arguments: argparse.Namespace,
) -> int:
    error = checkout_destination_error(directory)
    if error is not None:
        raise CaptureError(error)
    session = AccuracySession.load(spec, directory)
    started = session is not None
    if session is None:
        if arguments.status or arguments.review:
            raise CaptureError(
                f"no capture session in {directory}. Run the subcommand "
                "without --status first.")
        terms_path = resolve_terms_path(arguments, directory.parent)
        terms = load_terms(terms_path)
        for note in terms.notes:
            print(f"note: {note}")
        if len(terms.names) > MAX_PLAN_TERMS:
            print(f"note: using the first {MAX_PLAN_TERMS} of "
                  f"{len(terms.names)} names and terms, in file order; move "
                  "the ones that matter most to the top to change that.")
        session = AccuracySession.create(
            spec, directory, build_accuracy_plan(terms),
            accuracy_settings(terms, terms_path))
        session.state["extended_opted_in"] = False
        session.state["break_offered"] = False
        if not arguments.plan:
            secure_directory(directory)
            session.save()
            started = True
    else:
        settings = session.state.get("settings") or {}
        with contextlib.suppress(CaptureError):
            current = load_terms(resolve_terms_path(
                arguments, directory.parent))
            if terms_fingerprint(current) != settings.get("terms_sha256"):
                print("note: your terms have changed since this session "
                      "started. It keeps its original plan; to record a new "
                      "one, pass --session-dir with a new folder.")

    for identity in arguments.redo:
        session.reopen(identity)
        print(f"Reopened {identity}; it will be asked again.")
    if arguments.redo:
        session.save()
        session.write_manifest()

    if arguments.plan:
        print(render_accuracy_plan(session, started=started))
        print(render_accuracy_next_commands(session))
        return 0
    if arguments.status:
        print(f"{spec.title}: {session.summary()}")
        print(render_accuracy_next_commands(session))
        return 0
    if arguments.review:
        return run_review(session, arguments)
    return run_accuracy_capture(session, arguments)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def resolve_session_directory(
    spec: CorpusSpec,
    arguments: argparse.Namespace,
) -> Path:
    if arguments.session_dir is not None:
        directory = Path(arguments.session_dir).expanduser()
    else:
        root = Path(arguments.evidence_root).expanduser()
        directory = root / spec.name
    directory = directory.resolve()
    error = private_destination_error(directory)
    if error is not None:
        raise CaptureError(error)
    return directory


def load_or_create_session(
    spec: CorpusSpec,
    directory: Path,
    arguments: argparse.Namespace,
    *,
    create: bool,
) -> Session:
    session = Session.load(spec, directory)
    if session is not None:
        return session
    if not create:
        raise CaptureError(
            f"no capture session in {directory}. Run the subcommand without "
            "--status first.")
    count = arguments.cases
    if spec.name == "relisten":
        plan = build_relisten_plan(count)
        settings: dict[str, Any] = {}
    elif spec.name == "calibration":
        plan = build_calibration_plan(count)
        settings = {}
    else:
        keyword = (arguments.keyword or "").strip()
        near_miss = (arguments.near_miss or "").strip()
        if not keyword or not near_miss:
            raise CaptureError(
                "keywords needs --keyword (the hard name) and --near-miss (an "
                "acoustically close word that is NOT the keyword).")
        if keyword.casefold() == near_miss.casefold():
            raise CaptureError("--near-miss must differ from --keyword")
        plan = build_keyword_plan(count, keyword, near_miss)
        settings = {
            "keyword": keyword,
            "near_miss": near_miss,
            "app_scope": arguments.app_scope,
        }
    secure_directory(directory)
    session = Session.create(spec, directory, plan, settings)
    session.save()
    return session


def apply_telemetry(session: Session, arguments: argparse.Namespace) -> None:
    if session.spec.name != "calibration" or not arguments.telemetry_log:
        return
    path = Path(arguments.telemetry_log).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise CaptureError(f"cannot read {path}: {error}") from error
    records = extract_utterance_telemetry(text, limit=arguments.telemetry_limit)
    session.state["telemetry"] = records
    session.save()
    print(f"Imported {len(records)} real utterance_acoustic record(s) from "
          f"{path}.")
    if len(records) < 8:
        print("  The policy needs at least eight valid records and eight "
              "seconds of audio. Keep dictating and import again; nothing here "
              "will invent the missing ones.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture_voice_evidence.py",
        description=(
            "Guided capture of the physical voice corpora the Whisper Face "
            "activation gates require. Records real speech, writes a manifest "
            "the existing benchmarks consume, and never approves anything."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "This tool never synthesizes audio, never infers an outcome label, "
            "and never writes an activation receipt. It prints the exact "
            "benchmark command and stops; --confirm-manual-review stays yours "
            "to pass, after you have listened to every case."
        ),
    )
    subparsers = parser.add_subparsers(dest="corpus", required=True)
    for spec in CORPORA.values():
        child = subparsers.add_parser(
            spec.name,
            help=spec.title,
            description=f"Capture the {spec.title} corpus.",
        )
        child.add_argument(
            "--evidence-root",
            default=(ACCURACY_DEFAULT_ROOT if spec.name == "accuracy"
                     else str(REPO_ROOT / ".evidence")),
            help="private root for captured corpora (default: %(default)s)",
        )
        child.add_argument(
            "--session-dir",
            default=None,
            help="exact session directory (default: <evidence-root>/"
                 f"{spec.name})",
        )
        if spec.name == "accuracy":
            # The plan follows from the term list, and there is one pass.
            child.set_defaults(cases=spec.default_cases, witness="all")
            child.add_argument(
                "--terms", default=None,
                help="your names and terms, one per line: Name, Name | "
                     "sound-alike, or jargon: term (default: <evidence-root>/"
                     "terms.txt if it exists, else the typed terms in the "
                     "checkout's dictionary.txt)")
            child.add_argument(
                "--extended", action="store_true",
                help="carry on into the extended part after the essential "
                     "set")
        else:
            child.add_argument(
                "--cases", type=int, default=spec.default_cases,
                help="number of cases to plan (default: %(default)s)")
            child.add_argument(
                "--witness", choices=("all", "first"), default="all",
                help="record witness audio in every pass, or only the first "
                     "(default: %(default)s)")
        child.add_argument(
            "--device", default=None,
            help="input device index or name for sounddevice")
        child.add_argument(
            "--plan", action="store_true",
            help="open or create the session, print the whole plan and "
                 "progress, then exit without touching audio")
        child.add_argument(
            "--status", action="store_true",
            help="print progress and the next command for an existing session")
        child.add_argument(
            "--review", action="store_true",
            help="replay completed takes so you can do the manual review")
        child.add_argument(
            "--list-devices", action="store_true",
            help="print the available audio devices and exit")
        child.add_argument(
            "--redo", action="append", default=[],
            help="reopen a completed case by identifier (repeatable)")
        if spec.multi_arm:
            child.add_argument(
                "--arm", choices=spec.arms, default=None,
                help="run only one pass (default: whichever is incomplete)")
        else:
            child.set_defaults(arm=None)
        if spec.name == "calibration":
            child.add_argument(
                "--telemetry-log", default=None,
                help="dictate.log to import real utterance_acoustic traces from")
            child.add_argument(
                "--telemetry-limit", type=int, default=256,
                help="keep at most this many most-recent records "
                     "(default: %(default)s)")
        else:
            child.set_defaults(telemetry_log=None, telemetry_limit=256)
        if spec.name == "keywords":
            child.add_argument("--keyword", default=None,
                               help="the hard name being biased")
            child.add_argument("--near-miss", default=None,
                               help="an acoustically close word that is NOT it")
            child.add_argument("--app-scope", default=None,
                               help="salted app-<16 hex> scope, or omit for the "
                                    "global candidate")
            child.add_argument(
                "--memory", default=str(REPO_ROOT / "acoustic_keyword_memory.json"),
                help="keyword memory to check eligibility against, read-only "
                     "(default: %(default)s)")
        else:
            child.set_defaults(
                keyword=None, near_miss=None, app_scope=None, memory=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    spec = CORPORA[arguments.corpus]
    try:
        if arguments.list_devices:
            print(describe_devices())
            return 0
        if arguments.cases < 2 or arguments.cases > 256:
            raise CaptureError("--cases must be between 2 and 256")
        if arguments.cases < spec.min_cases:
            print(
                f"WARNING: {spec.title} needs at least {spec.min_cases} cases "
                f"to reach its gate; planning {arguments.cases}.",
                file=sys.stderr)
        directory = resolve_session_directory(spec, arguments)
        if spec.name == "accuracy":
            return accuracy_main(spec, directory, arguments)
        read_only = arguments.status or arguments.review
        session = load_or_create_session(
            spec, directory, arguments, create=not read_only)
        apply_telemetry(session, arguments)

        for identity in arguments.redo:
            session.reopen(identity)
            print(f"Reopened {identity}; its take will be recorded again.")
        if arguments.redo:
            session.save()
            session.write_manifest()

        if spec.name == "keywords" and arguments.memory:
            print(keyword_memory_status(
                Path(arguments.memory).expanduser(),
                session.state["settings"]["keyword"],
                session.state["settings"].get("app_scope"),
            ))
        if arguments.plan:
            print(render_plan(session))
            if spec.name == "calibration":
                print(render_candidate_settings(session))
            print(render_next_commands(session))
            return 0
        if arguments.status:
            print(f"{spec.title}: {session.progress_line()}")
            for shortfall in session.shortfalls():
                print(f"  still short: {shortfall}")
            if spec.name == "calibration":
                print(render_candidate_settings(session))
            print(render_next_commands(session))
            return 0
        if arguments.review:
            return run_review(session, arguments)
        return run_capture(session, arguments)
    except CaptureError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; completed cases are already saved",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
