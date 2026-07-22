"""Opt-in, RAM-only storage for short audio microspans.

The buffer is a storage foundation only.  It does not record, play, route,
recognize, persist, serialize, log, or transmit audio.  Samples are retained
only while explicitly enabled and are addressed by random, content-independent
identifiers.  Disabling the buffer drops every internally retained sample.

Audio is mono, normalized real samples at the runtime's 16 kHz capture rate.
Slice bounds are sample indexes so reads never depend on time rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real
import re
import secrets
from threading import RLock
from typing import Any, Sequence


SAMPLE_RATE_HZ = 16_000
MAX_SPAN_DURATION_MS = 2_400
MAX_SPAN_SAMPLES = SAMPLE_RATE_HZ * MAX_SPAN_DURATION_MS // 1_000
MAX_SPANS = 8
MAX_TOTAL_DURATION_MS = 10_000
MAX_TOTAL_SAMPLES = SAMPLE_RATE_HZ * MAX_TOTAL_DURATION_MS // 1_000

_SPAN_ID = re.compile(r"atm-[0-9a-f]{32}\Z")


class Operation(str, Enum):
    ENABLE = "enable"
    DISABLE = "disable"
    STORE = "store"
    READ = "read"
    CONSUME = "consume"
    DELETE = "delete"
    CLEAR = "clear"


class Outcome(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    STORED = "stored"
    READ = "read"
    CONSUMED = "consumed"
    DELETED = "deleted"
    CLEARED = "cleared"
    NOT_FOUND = "not_found"
    CAPACITY_EXCEEDED = "capacity_exceeded"


@dataclass(frozen=True, slots=True)
class BufferReceipt:
    """Deterministic operation evidence with no audio or derived metadata."""

    operation: Operation
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class StoreResult:
    """An opaque handle plus its content-free operation receipt."""

    span_id: str | None
    receipt: BufferReceipt


@dataclass(frozen=True, slots=True, repr=False)
class AudioSlice:
    """An explicit caller-owned copy of an exact retained sample range."""

    samples: tuple[float, ...]
    sample_rate_hz: int
    start_sample: int
    end_sample: int


@dataclass(frozen=True, slots=True, repr=False)
class SliceResult:
    """A private audio result kept separate from its content-free receipt."""

    audio: AudioSlice | None
    receipt: BufferReceipt


@dataclass(slots=True, repr=False)
class _StoredSpan:
    samples: list[float]


def _receipt(operation: Operation, outcome: Outcome) -> BufferReceipt:
    return BufferReceipt(operation=operation, outcome=outcome)


def _plain_int(value: Any, *, minimum: int, maximum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _validate_span_id(span_id: Any) -> str:
    if not isinstance(span_id, str) or not _SPAN_ID.fullmatch(span_id):
        raise ValueError("span_id must be an opaque buffer identifier")
    return span_id


def _copy_samples(samples: Sequence[Real]) -> list[float]:
    try:
        count = len(samples)
    except Exception as exc:
        raise ValueError("samples must be a finite sized sequence") from exc
    if not _plain_int(count, minimum=1, maximum=MAX_SPAN_SAMPLES):
        raise ValueError(
            f"samples must contain 1-{MAX_SPAN_SAMPLES} frames")

    copied: list[float] = []
    try:
        for value in samples:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError("samples must contain only real numbers")
            normalized = float(value)
            if not math.isfinite(normalized) or not -1.0 <= normalized <= 1.0:
                raise ValueError("samples must be finite and normalized")
            copied.append(normalized)
            if len(copied) > count:
                raise ValueError("samples changed length while being copied")
    except ValueError:
        _wipe(copied)
        raise
    except Exception as exc:
        _wipe(copied)
        raise ValueError("samples could not be copied exactly once") from exc
    if len(copied) != count:
        _wipe(copied)
        raise ValueError("samples changed length while being copied")
    return copied


def _wipe(samples: list[float]) -> None:
    """Best-effort overwrite before releasing an internal audio allocation."""
    for index in range(len(samples)):
        samples[index] = 0.0


class AcousticTimeMachine:
    """Thread-safe, explicitly enabled buffer for bounded audio microspans."""

    def __init__(self, *, enabled: bool = False) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        self._enabled = enabled
        self._spans: dict[str, _StoredSpan] = {}
        self._total_samples = 0
        self._lock = RLock()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def span_count(self) -> int:
        with self._lock:
            return len(self._spans)

    @property
    def retained_samples(self) -> int:
        with self._lock:
            return self._total_samples

    def enable(self) -> BufferReceipt:
        with self._lock:
            self._enabled = True
            return _receipt(Operation.ENABLE, Outcome.ENABLED)

    def disable(self) -> BufferReceipt:
        with self._lock:
            self._clear_locked()
            self._enabled = False
            return _receipt(Operation.DISABLE, Outcome.DISABLED)

    def store(
        self,
        samples: Sequence[Real],
        *,
        sample_rate_hz: int,
    ) -> StoreResult:
        """Copy one bounded microspan, or retain nothing when disabled/full."""
        with self._lock:
            if not self._enabled:
                return StoreResult(
                    None, _receipt(Operation.STORE, Outcome.DISABLED))
        if not _plain_int(
                sample_rate_hz,
                minimum=SAMPLE_RATE_HZ,
                maximum=SAMPLE_RATE_HZ):
            raise ValueError(f"sample_rate_hz must equal {SAMPLE_RATE_HZ}")
        copied = _copy_samples(samples)

        with self._lock:
            if not self._enabled:
                _wipe(copied)
                return StoreResult(
                    None, _receipt(Operation.STORE, Outcome.DISABLED))
            if (len(self._spans) >= MAX_SPANS
                    or self._total_samples + len(copied) > MAX_TOTAL_SAMPLES):
                _wipe(copied)
                return StoreResult(
                    None,
                    _receipt(Operation.STORE, Outcome.CAPACITY_EXCEEDED),
                )
            span_id = self._new_id_locked()
            self._spans[span_id] = _StoredSpan(copied)
            self._total_samples += len(copied)
            return StoreResult(
                span_id, _receipt(Operation.STORE, Outcome.STORED))

    def read(
        self,
        span_id: str,
        *,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> SliceResult:
        with self._lock:
            if not self._enabled:
                return SliceResult(
                    None, _receipt(Operation.READ, Outcome.DISABLED))
            span = self._spans.get(_validate_span_id(span_id))
            if span is None:
                return SliceResult(
                    None, _receipt(Operation.READ, Outcome.NOT_FOUND))
            start, end = self._slice_bounds(
                len(span.samples), start_sample, end_sample)
            audio = self._audio_slice(span, start, end)
            return SliceResult(audio, _receipt(Operation.READ, Outcome.READ))

    def consume(
        self,
        span_id: str,
        *,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> SliceResult:
        """Return one exact slice and remove the entire backing microspan."""
        with self._lock:
            if not self._enabled:
                return SliceResult(
                    None, _receipt(Operation.CONSUME, Outcome.DISABLED))
            validated_id = _validate_span_id(span_id)
            span = self._spans.get(validated_id)
            if span is None:
                return SliceResult(
                    None, _receipt(Operation.CONSUME, Outcome.NOT_FOUND))
            start, end = self._slice_bounds(
                len(span.samples), start_sample, end_sample)
            audio = self._audio_slice(span, start, end)
            self._remove_locked(validated_id, span)
            return SliceResult(
                audio, _receipt(Operation.CONSUME, Outcome.CONSUMED))

    def delete(self, span_id: str) -> BufferReceipt:
        with self._lock:
            if not self._enabled:
                return _receipt(Operation.DELETE, Outcome.DISABLED)
            validated_id = _validate_span_id(span_id)
            span = self._spans.get(validated_id)
            if span is None:
                return _receipt(Operation.DELETE, Outcome.NOT_FOUND)
            self._remove_locked(validated_id, span)
            return _receipt(Operation.DELETE, Outcome.DELETED)

    def clear(self) -> BufferReceipt:
        with self._lock:
            if not self._enabled:
                return _receipt(Operation.CLEAR, Outcome.DISABLED)
            self._clear_locked()
            return _receipt(Operation.CLEAR, Outcome.CLEARED)

    def _new_id_locked(self) -> str:
        while True:
            span_id = f"atm-{secrets.token_hex(16)}"
            if span_id not in self._spans:
                return span_id

    @staticmethod
    def _slice_bounds(
        length: int,
        start_sample: int,
        end_sample: int | None,
    ) -> tuple[int, int]:
        if not _plain_int(start_sample, minimum=0):
            raise ValueError("start_sample must be a non-negative integer")
        end = length if end_sample is None else end_sample
        if not _plain_int(end, minimum=1, maximum=length):
            raise ValueError("end_sample must be within the stored microspan")
        if start_sample >= end:
            raise ValueError("slice must contain at least one sample")
        return start_sample, end

    @staticmethod
    def _audio_slice(span: _StoredSpan, start: int, end: int) -> AudioSlice:
        return AudioSlice(
            samples=tuple(span.samples[start:end]),
            sample_rate_hz=SAMPLE_RATE_HZ,
            start_sample=start,
            end_sample=end,
        )

    def _remove_locked(self, span_id: str, span: _StoredSpan) -> None:
        self._spans.pop(span_id)
        self._total_samples -= len(span.samples)
        _wipe(span.samples)

    def _clear_locked(self) -> None:
        for span in self._spans.values():
            _wipe(span.samples)
        self._spans.clear()
        self._total_samples = 0
