"""Killable, prewarmed process supervisor for bounded verification requests.

One lazy child initializes a caller-supplied provider once and handles requests
sequentially.  The supervisor retains no request or result history, emits no
child output, and discards the complete child process after a timeout, crash,
or malformed response.  This is only a process-lifecycle boundary: it neither
selects a model nor claims network, filesystem, or sandbox isolation.
"""

from __future__ import annotations

import math
import multiprocessing
from multiprocessing.connection import Connection
from numbers import Real
import os
from threading import RLock
import time
from typing import Any, Callable, Mapping, Sequence

from process_verifier import (
    RefusalReason,
    VerificationReceipt,
    VerificationRequest,
    _silence_child_output,
    _validated_result,
)


SAMPLE_RATE_HZ = 16_000
MAX_REQUEST_DURATION_MS = 2_400
MAX_REQUEST_SAMPLES = (
    SAMPLE_RATE_HZ * MAX_REQUEST_DURATION_MS // 1_000
)
MAX_EXPECTED_CHARACTERS = 160
MAX_EXPECTED_UTF8_BYTES = 640

_READY = ("ready",)
_VERIFY = "verify"
_RESULT = "result"
_CLOSE = ("close",)


def _child_main(
    provider_factory: Callable[[], Callable[
        [VerificationRequest], Mapping[str, Any]
    ]],
    connection: Connection,
) -> None:
    """Initialize once and exchange only closed protocol messages."""
    _silence_child_output()
    try:
        provider = provider_factory()
        if not callable(provider):
            os._exit(70)
        connection.send(_READY)
        while True:
            message = connection.recv()
            if message == _CLOSE:
                return
            if (not isinstance(message, tuple) or len(message) != 2
                    or message[0] != _VERIFY
                    or not isinstance(message[1], VerificationRequest)):
                os._exit(70)
            request = message[1]
            payload = provider(request)
            connection.send((_RESULT, payload))
            request = None
            payload = None
    except BaseException:
        os._exit(70)
    finally:
        connection.close()


def _plain_int(value: Any, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _bounded_samples(
    samples: Sequence[float],
    sample_rate: int,
) -> tuple[float, ...]:
    try:
        count = len(samples)
    except Exception as exc:
        raise ValueError("samples must be a finite sized sequence") from exc
    duration_limit = sample_rate * MAX_REQUEST_DURATION_MS // 1_000
    limit = min(MAX_REQUEST_SAMPLES, duration_limit)
    if not _plain_int(count, minimum=1, maximum=limit):
        raise ValueError("samples exceed the bounded microspan duration")

    copied: list[float] = []
    try:
        for value in samples:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError("samples must contain only real numbers")
            sample = float(value)
            if not math.isfinite(sample) or not -1.0 <= sample <= 1.0:
                raise ValueError("samples must be finite and normalized")
            copied.append(sample)
            if len(copied) > count:
                raise ValueError("samples changed length while being copied")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("samples could not be copied exactly once") from exc
    if len(copied) != count:
        raise ValueError("samples changed length while being copied")
    return tuple(copied)


def _validate_expected(expected: Any) -> str:
    if not isinstance(expected, str):
        raise TypeError("expected must be a string")
    try:
        encoded_size = len(expected.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("expected must be valid UTF-8 text") from exc
    if (not expected or len(expected) > MAX_EXPECTED_CHARACTERS
            or encoded_size > MAX_EXPECTED_UTF8_BYTES):
        raise ValueError("expected exceeds the bounded text limit")
    return expected


class PrewarmedVerifierSupervisor:
    """Reuse one initialized child while preserving fail-closed deadlines."""

    process_isolated = True
    strict_deadline = True
    retains_audio = False
    prewarmed = True

    def __init__(
        self,
        provider_factory: Callable[[], Callable[
            [VerificationRequest], Mapping[str, Any]
        ]],
        *,
        context: multiprocessing.context.BaseContext | None = None,
    ) -> None:
        if not callable(provider_factory):
            raise TypeError("provider_factory must be callable")
        self._provider_factory = provider_factory
        self._context = context or multiprocessing.get_context()
        self._process: multiprocessing.Process | None = None
        self._connection: Connection | None = None
        self._closed = False
        self._lock = RLock()

    def __enter__(self) -> PrewarmedVerifierSupervisor:
        with self._lock:
            if self._closed:
                raise RuntimeError("verifier supervisor is closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _refused(reason: RefusalReason) -> VerificationReceipt:
        return VerificationReceipt(refusal=reason)

    @staticmethod
    def _stop(process: multiprocessing.Process) -> None:
        if not process.is_alive():
            process.join()
            return
        process.terminate()
        process.join(0.1)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if kill is not None:
                kill()
            process.join()

    def close(self) -> None:
        """Permanently close the supervisor and destroy provider state."""
        with self._lock:
            if self._closed:
                return
            connection = self._connection
            process = self._process
            if (connection is not None and process is not None
                    and process.is_alive()):
                try:
                    connection.send(_CLOSE)
                except Exception:
                    pass
            self._discard_locked()
            self._closed = True

    def verify(
        self,
        samples: Sequence[float],
        sample_rate: int,
        expected: str,
        *,
        deadline_at: float,
    ) -> VerificationReceipt:
        """Verify one bounded span and accept no response after its deadline."""
        if (isinstance(deadline_at, bool)
                or not isinstance(deadline_at, (int, float))
                or not math.isfinite(float(deadline_at))):
            raise ValueError("deadline_at must be a finite monotonic timestamp")
        deadline = float(deadline_at)
        if time.monotonic() >= deadline:
            return self._refused(RefusalReason.TIMEOUT)
        if not _plain_int(
                sample_rate, minimum=SAMPLE_RATE_HZ,
                maximum=SAMPLE_RATE_HZ):
            raise ValueError(f"sample_rate must equal {SAMPLE_RATE_HZ}")
        validated_expected = _validate_expected(expected)
        copied_samples = _bounded_samples(samples, sample_rate)
        if time.monotonic() >= deadline:
            return self._refused(RefusalReason.TIMEOUT)
        request = VerificationRequest(
            copied_samples, sample_rate, validated_expected, deadline)

        with self._lock:
            if self._closed:
                raise RuntimeError("verifier supervisor is closed")
            if time.monotonic() >= deadline:
                return self._refused(RefusalReason.TIMEOUT)
            refusal = self._ensure_child_locked(deadline)
            if refusal is not None:
                return self._refused(refusal)
            connection = self._connection
            process = self._process
            assert connection is not None and process is not None
            try:
                connection.send((_VERIFY, request))
            except Exception:
                self._discard_locked()
                return self._refused(RefusalReason.CRASH)

            remaining = max(0.0, deadline - time.monotonic())
            if not connection.poll(remaining):
                reason = RefusalReason.TIMEOUT if process.is_alive() \
                    else RefusalReason.CRASH
                self._discard_locked()
                return self._refused(reason)
            try:
                message = connection.recv()
            except (EOFError, OSError):
                self._discard_locked()
                return self._refused(RefusalReason.CRASH)
            except Exception:
                self._discard_locked()
                return self._refused(RefusalReason.MALFORMED_RESULT)
            if time.monotonic() > deadline:
                self._discard_locked()
                return self._refused(RefusalReason.TIMEOUT)
            if (not isinstance(message, tuple) or len(message) != 2
                    or message[0] != _RESULT):
                self._discard_locked()
                return self._refused(RefusalReason.MALFORMED_RESULT)
            try:
                result = _validated_result(message[1])
            except Exception:
                result = None
            if result is None:
                self._discard_locked()
                return self._refused(RefusalReason.MALFORMED_RESULT)
            return VerificationReceipt(result=result)

    def _ensure_child_locked(
        self,
        deadline: float,
    ) -> RefusalReason | None:
        if self._process is not None:
            if (self._connection is not None and self._process.is_alive()):
                return None
            self._discard_locked()

        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_child_main,
            args=(self._provider_factory, child),
        )
        self._connection = parent
        self._process = process
        try:
            try:
                process.start()
            except BaseException:
                self._discard_locked()
                return RefusalReason.CRASH
            finally:
                child.close()

            remaining = max(0.0, deadline - time.monotonic())
            if not parent.poll(remaining):
                reason = RefusalReason.TIMEOUT if process.is_alive() \
                    else RefusalReason.CRASH
                self._discard_locked()
                return reason
            try:
                ready = parent.recv()
            except Exception:
                self._discard_locked()
                return RefusalReason.CRASH
            if time.monotonic() > deadline:
                self._discard_locked()
                return RefusalReason.TIMEOUT
            if ready != _READY:
                self._discard_locked()
                return RefusalReason.CRASH
            return None
        except BaseException:
            self._discard_locked()
            return RefusalReason.CRASH

    def _discard_locked(self) -> None:
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None
        if connection is not None:
            connection.close()
        if process is not None:
            if process.pid is not None:
                self._stop(process)
            close_process = getattr(process, "close", None)
            if close_process is not None:
                try:
                    close_process()
                except ValueError:
                    pass
