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
            "CANDIDATE PASS. Apply the candidate front-end settings printed "
            "above, restart Whisper Face, then repeat the same task set."
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
            "restart Whisper Face."
        ),
        "biased": (
            "BIASED PASS. Put the keyword in the Whisper prompt for this pass "
            "(add it to dictionary.txt above the managed marker), restart "
            "Whisper Face, then repeat the same sentences."
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
)

CORPORA: Mapping[str, CorpusSpec] = {
    RELISTEN_SPEC.name: RELISTEN_SPEC,
    CALIBRATION_SPEC.name: CALIBRATION_SPEC,
    KEYWORDS_SPEC.name: KEYWORDS_SPEC,
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

    print("  Recording. Press any key to stop.")
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
        print(RULE)
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
            else:
                print(f"  keyword expected : {plan['keyword_expected']}")
                print(f"  labels           : {record.get('labels')}")
            samples, _ = read_wav(path)
            play_samples(samples, device)
    print()
    print(render_next_commands(session))
    return 0


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
        "  Runtime only applies these from an approved receipt, so the "
        "candidate pass",
        "  needs you to set them locally for the duration of the pass and put "
        "them back",
        "  afterwards. See docs/evidence/voice-corpora.md.",
    ])


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
            default=str(REPO_ROOT / ".evidence"),
            help="private root for captured corpora (default: %(default)s)",
        )
        child.add_argument(
            "--session-dir",
            default=None,
            help="exact session directory (default: <evidence-root>/"
                 f"{spec.name})",
        )
        child.add_argument(
            "--cases", type=int, default=spec.default_cases,
            help="number of cases to plan (default: %(default)s)")
        child.add_argument(
            "--device", default=None,
            help="input device index or name for sounddevice")
        child.add_argument(
            "--witness", choices=("all", "first"), default="all",
            help="record witness audio in every pass, or only the first "
                 "(default: %(default)s)")
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
