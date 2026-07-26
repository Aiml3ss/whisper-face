"""Bounded macOS Whisper Tiny adapters for process-isolated verification.

Neither adapter has runtime wiring.  Each accepted request is decoded without
a prompt or application context, and only a closed decision crosses the child
process boundary.  The disposable adapter loads per request; the prewarmed
adapter resolves and loads the pinned local model once in a reusable killable
child.  No latency, accuracy, activation, or sandbox claim is made here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import math
from numbers import Real
from pathlib import Path
import time
from typing import Any
import unicodedata

from process_verifier import (
    ProcessIsolatedVerifier,
    RefusalReason,
    VerificationReceipt,
    VerificationRequest,
    VerificationResult,
)
from prewarmed_verifier import PrewarmedVerifierSupervisor


WHISPER_TINY_REPO = "mlx-community/whisper-tiny"
WHISPER_TINY_REVISION = "78c52ab98ca87f570bc57ad852e15ef7060f9f76"
WHISPER_TINY_ENGINE = "mlx-whisper-tiny"
WHISPER_SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 2.4
MAX_AUDIO_SAMPLES = int(WHISPER_SAMPLE_RATE * MAX_AUDIO_SECONDS)
MAX_EXPECTED_CHARACTERS = 160
MAX_EXPECTED_UTF8_BYTES = 640

_MIN_DECISION_CONFIDENCE = 0.55
_MAX_CONTRADICTION_TOKEN_SIMILARITY = 0.5


def normalize_for_verification(value: str) -> str:
    """Return a deterministic, locale-independent comparison form."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    separated = True
    for character in folded:
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N"}:
            characters.append(character)
            separated = False
        elif not separated:
            characters.append(" ")
            separated = True
    return "".join(characters).strip()


def _valid_expected(expected: Any) -> bool:
    if not isinstance(expected, str):
        return False
    if not 0 < len(expected) <= MAX_EXPECTED_CHARACTERS:
        return False
    try:
        if len(expected.encode("utf-8")) > MAX_EXPECTED_UTF8_BYTES:
            return False
    except UnicodeError:
        return False
    if any(unicodedata.category(character).startswith("C")
           for character in expected):
        return False
    normalized = normalize_for_verification(expected)
    return bool(normalized) and len(normalized) <= MAX_EXPECTED_CHARACTERS


def _bounded_samples(samples: Any) -> tuple[float, ...] | None:
    """Copy a finite, normalized microspan without consuming an unbounded input."""
    try:
        count = len(samples)
    except Exception:
        return None
    if (isinstance(count, bool) or not isinstance(count, int)
            or not 0 < count <= MAX_AUDIO_SAMPLES):
        return None

    copied: list[float] = []
    try:
        for value in samples:
            if isinstance(value, bool) or not isinstance(value, Real):
                return None
            sample = float(value)
            if not math.isfinite(sample) or not -1.0 <= sample <= 1.0:
                return None
            copied.append(sample)
            if len(copied) > count:
                return None
    except Exception:
        return None
    if len(copied) != count:
        return None
    return tuple(copied)


def _bounded_request(request: VerificationRequest) \
        -> VerificationRequest | None:
    try:
        if (not isinstance(request, VerificationRequest)
                or request.sample_rate != WHISPER_SAMPLE_RATE
                or not _valid_expected(request.expected)):
            return None
        samples = _bounded_samples(request.samples)
        if samples is None:
            return None
        if (isinstance(request.deadline_at, bool)
                or not isinstance(request.deadline_at, Real)
                or not math.isfinite(float(request.deadline_at))):
            return None
        return VerificationRequest(
            samples=samples,
            sample_rate=WHISPER_SAMPLE_RATE,
            expected=request.expected,
            deadline_at=float(request.deadline_at),
        )
    except Exception:
        return None


def _token_similarity(left: str, right: str) -> float:
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return 0.0
    previous = list(range(len(right_tokens) + 1))
    for left_index, left_token in enumerate(left_tokens, 1):
        current = [left_index]
        for right_index, right_token in enumerate(right_tokens, 1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_token != right_token),
            ))
        previous = current
    distance = previous[-1]
    return 1.0 - distance / max(len(left_tokens), len(right_tokens))


def _confidence_from_result(result: Any) -> float:
    if not isinstance(result, Mapping):
        return 0.0
    segments = result.get("segments")
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
        return 0.0
    weighted = total_weight = 0.0
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        log_probability = segment.get("avg_logprob")
        if (isinstance(log_probability, bool)
                or not isinstance(log_probability, Real)
                or not math.isfinite(float(log_probability))):
            continue
        start = segment.get("start")
        end = segment.get("end")
        weight = 1.0
        if (isinstance(start, Real) and not isinstance(start, bool)
                and isinstance(end, Real) and not isinstance(end, bool)
                and math.isfinite(float(start)) and math.isfinite(float(end))
                and float(end) > float(start)):
            weight = float(end) - float(start)
        probability = math.exp(min(0.0, float(log_probability)))
        weighted += probability * weight
        total_weight += weight
    if total_weight <= 0.0:
        return 0.0
    return round(max(0.0, min(1.0, weighted / total_weight)), 4)


def _closed_result(outcome: str, confidence: float) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "engine": WHISPER_TINY_ENGINE,
    }


def _compare(transcript: str, expected: str, confidence: float) \
        -> dict[str, Any]:
    heard = normalize_for_verification(transcript)
    wanted = normalize_for_verification(expected)
    if not heard or confidence < _MIN_DECISION_CONFIDENCE:
        return _closed_result("inconclusive", confidence)
    if heard == wanted:
        return _closed_result("confirmed", confidence)
    if _token_similarity(heard, wanted) \
            <= _MAX_CONTRADICTION_TOKEN_SIMILARITY:
        return _closed_result("contradicted", confidence)
    return _closed_result("inconclusive", confidence)


def _resolve_local_snapshot(downloader=None) -> str:
    if downloader is None:
        from huggingface_hub import snapshot_download
        downloader = snapshot_download

    return str(downloader(
        repo_id=WHISPER_TINY_REPO,
        revision=WHISPER_TINY_REVISION,
        local_files_only=True,
    ))


def _transcribe(samples: tuple[float, ...], model_path: str) \
        -> Mapping[str, Any]:
    import mlx_whisper
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if 0.0 < peak < 0.25:
        audio = audio * min(0.25 / peak, 25.0)
    return mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model_path,
        language="en",
        temperature=0.0,
        condition_on_previous_text=False,
        initial_prompt=None,
    )


def _load_whisper_tiny_model(model_path: str) -> Any:
    """Load the pinned local snapshot once inside the prewarmed child."""
    import mlx.core as mx
    from mlx_whisper.load_models import load_model

    return load_model(model_path, dtype=mx.float16)


@dataclass(frozen=True, repr=False)
class LoadedWhisperTiny:
    """Child-only model state; never sent through the parent connection."""

    model_path: str
    model: Any


def _transcribe_loaded(
        samples: tuple[float, ...],
        loaded: LoadedWhisperTiny) -> Mapping[str, Any]:
    """Decode through mlx-whisper's holder without reloading the model."""
    import numpy as np
    from mlx_whisper.transcribe import ModelHolder, transcribe

    ModelHolder.model = loaded.model
    ModelHolder.model_path = loaded.model_path
    audio = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if 0.0 < peak < 0.25:
        audio = audio * min(0.25 / peak, 25.0)
    return transcribe(
        audio,
        path_or_hf_repo=loaded.model_path,
        language="en",
        temperature=0.0,
        condition_on_previous_text=False,
        initial_prompt=None,
    )


@dataclass(frozen=True)
class PrewarmedWhisperTinyProvider:
    """Child-only request handler backed by one already loaded model."""

    loaded: LoadedWhisperTiny
    transcriber: Callable[
        [tuple[float, ...], LoadedWhisperTiny], Mapping[str, Any]
    ] = _transcribe_loaded

    def __call__(self, request: VerificationRequest) -> Mapping[str, Any]:
        bounded = _bounded_request(request)
        if bounded is None or time.monotonic() >= bounded.deadline_at:
            return _closed_result("inconclusive", 0.0)
        if not any(bounded.samples):
            return _closed_result("inconclusive", 0.0)
        result = self.transcriber(bounded.samples, self.loaded)
        if not isinstance(result, Mapping):
            raise ValueError("transcriber returned a malformed result")
        transcript = result.get("text")
        if not isinstance(transcript, str):
            raise ValueError("transcriber result omitted text")
        confidence = _confidence_from_result(result)
        return _compare(transcript, bounded.expected, confidence)


@dataclass(frozen=True)
class PrewarmedWhisperTinyProviderFactory:
    """Resolve and load exactly once when the supervisor starts its child."""

    resolver: Callable[[], str] = _resolve_local_snapshot
    loader: Callable[[str], Any] = _load_whisper_tiny_model
    transcriber: Callable[
        [tuple[float, ...], LoadedWhisperTiny], Mapping[str, Any]
    ] = _transcribe_loaded

    def __call__(self) -> PrewarmedWhisperTinyProvider:
        model_path = self.resolver()
        if not isinstance(model_path, str) or not Path(model_path).is_dir():
            raise ValueError("local Whisper Tiny snapshot is unavailable")
        model = self.loader(model_path)
        if model is None:
            raise ValueError("Whisper Tiny loader returned no model")
        return PrewarmedWhisperTinyProvider(
            loaded=LoadedWhisperTiny(model_path, model),
            transcriber=self.transcriber,
        )


@dataclass(frozen=True)
class WhisperTinyWorker:
    """Picklable child worker with injectable local resolver and transcriber."""

    resolver: Callable[[], str] = _resolve_local_snapshot
    transcriber: Callable[[tuple[float, ...], str], Mapping[str, Any]] = \
        _transcribe

    def __call__(self, request: VerificationRequest) -> Mapping[str, Any]:
        bounded = _bounded_request(request)
        if bounded is None or time.monotonic() >= bounded.deadline_at:
            return _closed_result("inconclusive", 0.0)
        if not any(bounded.samples):
            return _closed_result("inconclusive", 0.0)
        try:
            model_path = self.resolver()
            if (not isinstance(model_path, str)
                    or not Path(model_path).is_dir()
                    or time.monotonic() >= bounded.deadline_at):
                return _closed_result("inconclusive", 0.0)
            result = self.transcriber(bounded.samples, model_path)
            if time.monotonic() >= bounded.deadline_at \
                    or not isinstance(result, Mapping):
                return _closed_result("inconclusive", 0.0)
            transcript = result.get("text")
            if not isinstance(transcript, str):
                return _closed_result("inconclusive", 0.0)
            confidence = _confidence_from_result(result)
            return _compare(transcript, bounded.expected, confidence)
        except Exception:
            return _closed_result("inconclusive", 0.0)


class WhisperTinyVerifier:
    """Parent-side bounds plus the disposable-process Whisper worker."""

    process_isolated = True
    strict_deadline = True
    retains_audio = False

    def __init__(
            self,
            *,
            worker: WhisperTinyWorker | None = None,
            process_verifier: ProcessIsolatedVerifier | None = None,
    ) -> None:
        if worker is not None and process_verifier is not None:
            raise ValueError("provide worker or process_verifier, not both")
        self._process_verifier = process_verifier or ProcessIsolatedVerifier(
            worker or WhisperTinyWorker())

    @staticmethod
    def _inconclusive() -> VerificationReceipt:
        return VerificationReceipt(result=VerificationResult(
            "inconclusive", 0.0, WHISPER_TINY_ENGINE))

    def verify(
            self,
            samples: Sequence[float],
            sample_rate: int,
            expected: str,
            *,
            deadline_at: float,
    ) -> VerificationReceipt:
        if (isinstance(deadline_at, bool) or not isinstance(deadline_at, Real)
                or not math.isfinite(float(deadline_at))):
            raise ValueError("deadline_at must be a finite monotonic timestamp")
        deadline = float(deadline_at)
        if time.monotonic() >= deadline:
            return VerificationReceipt(refusal=RefusalReason.TIMEOUT)
        bounded = _bounded_request(VerificationRequest(
            samples=samples, sample_rate=sample_rate, expected=expected,
            deadline_at=deadline))
        if bounded is None:
            return self._inconclusive()
        return self._process_verifier.verify(
            bounded.samples,
            bounded.sample_rate,
            bounded.expected,
            deadline_at=bounded.deadline_at,
        )


class PrewarmedWhisperTinyVerifier:
    """Parent-side Whisper bounds composed with the prewarmed supervisor."""

    process_isolated = True
    strict_deadline = True
    retains_audio = False
    prewarmed = True

    def __init__(
            self,
            *,
            provider_factory: PrewarmedWhisperTinyProviderFactory | None = None,
            supervisor: PrewarmedVerifierSupervisor | None = None,
    ) -> None:
        if provider_factory is not None and supervisor is not None:
            raise ValueError("provide provider_factory or supervisor, not both")
        self._supervisor = supervisor or PrewarmedVerifierSupervisor(
            provider_factory or PrewarmedWhisperTinyProviderFactory())

    def __enter__(self) -> PrewarmedWhisperTinyVerifier:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._supervisor.close()

    @property
    def ready(self) -> bool:
        return self._supervisor.ready

    def prewarm(self, *, deadline_at: float) -> bool:
        return self._supervisor.prewarm(deadline_at=deadline_at)

    def verify(
            self,
            samples: Sequence[float],
            sample_rate: int,
            expected: str,
            *,
            deadline_at: float,
    ) -> VerificationReceipt:
        if (isinstance(deadline_at, bool) or not isinstance(deadline_at, Real)
                or not math.isfinite(float(deadline_at))):
            raise ValueError("deadline_at must be a finite monotonic timestamp")
        deadline = float(deadline_at)
        if time.monotonic() >= deadline:
            return VerificationReceipt(refusal=RefusalReason.TIMEOUT)
        bounded = _bounded_request(VerificationRequest(
            samples=samples, sample_rate=sample_rate, expected=expected,
            deadline_at=deadline))
        if time.monotonic() >= deadline:
            return VerificationReceipt(refusal=RefusalReason.TIMEOUT)
        if bounded is None:
            return WhisperTinyVerifier._inconclusive()
        return self._supervisor.verify(
            bounded.samples,
            bounded.sample_rate,
            bounded.expected,
            deadline_at=bounded.deadline_at,
        )
