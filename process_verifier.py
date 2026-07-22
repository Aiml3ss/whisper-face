"""Killable, provider-neutral boundary for ephemeral microspan verification.

This module does not choose or load a model.  A caller supplies a worker that
receives one request in a disposable child process.  The parent accepts only a
small transcript-free result and tears the process down before returning.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import multiprocessing
from multiprocessing.connection import Connection
import os
import re
import time
from typing import Any, Callable, Mapping, Protocol, Sequence


_OUTCOMES = frozenset({"confirmed", "contradicted", "inconclusive"})
_RESULT_KEYS = frozenset({"outcome", "confidence", "engine"})
_ENGINE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class RefusalReason(str, Enum):
    """Fixed fail-closed reasons exposed by the process boundary."""

    TIMEOUT = "timeout"
    CRASH = "crash"
    MALFORMED_RESULT = "malformed-result"


@dataclass(frozen=True)
class VerificationRequest:
    """Ephemeral child-only input for one bounded audio span."""

    samples: tuple[float, ...]
    sample_rate: int
    expected: str
    deadline_at: float


@dataclass(frozen=True)
class VerificationResult:
    """Closed, transcript-free evidence returned by a verifier worker."""

    outcome: str
    confidence: float
    engine: str


@dataclass(frozen=True)
class VerificationReceipt:
    """Exactly one accepted result or one fixed refusal reason."""

    result: VerificationResult | None = None
    refusal: RefusalReason | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.refusal is None):
            raise ValueError("receipt requires exactly one result or refusal")

    @property
    def accepted(self) -> bool:
        return self.result is not None


class VerifierWorker(Protocol):
    """Provider adapter invoked only inside the disposable child process."""

    def __call__(self, request: VerificationRequest) -> Mapping[str, Any]: ...


def _silence_child_output() -> None:
    """Prevent provider output from becoming an application or service log."""
    try:
        sink = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(sink, 1)
            os.dup2(sink, 2)
        finally:
            os.close(sink)
    except OSError:
        # Output suppression is defense in depth.  The parent still emits no
        # worker exception, payload, audio, or transcript on any failure path.
        pass


def _run_worker(
        worker: Callable[[VerificationRequest], Mapping[str, Any]],
        request: VerificationRequest,
        sender: Connection,
) -> None:
    """Child entry point; deliberately sends no exception details or logs."""
    _silence_child_output()
    try:
        sender.send(worker(request))
    except BaseException:
        os._exit(70)
    finally:
        sender.close()


def _validated_result(payload: Any) -> VerificationResult | None:
    if not isinstance(payload, Mapping) or set(payload) != _RESULT_KEYS:
        return None
    outcome = payload["outcome"]
    confidence = payload["confidence"]
    engine = payload["engine"]
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        return None
    if (isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0):
        return None
    if not isinstance(engine, str) or _ENGINE_RE.fullmatch(engine) is None:
        return None
    return VerificationResult(outcome, float(confidence), engine)


class ProcessIsolatedVerifier:
    """Run each verification in a fresh process under an absolute deadline.

    The boundary retains only the worker and process context.  It records no
    requests, audio, expected text, results, or worker logs.  Worker adapters
    are likewise required not to write request data to external storage; the
    process boundary guarantees only that their memory is killed.
    """

    process_isolated = True
    strict_deadline = True
    retains_audio = False

    def __init__(
            self,
            worker: Callable[[VerificationRequest], Mapping[str, Any]],
            *,
            context: multiprocessing.context.BaseContext | None = None,
    ) -> None:
        if not callable(worker):
            raise TypeError("worker must be callable")
        self._worker = worker
        self._context = context or multiprocessing.get_context()

    @staticmethod
    def _refused(reason: RefusalReason) -> VerificationReceipt:
        return VerificationReceipt(refusal=reason)

    @staticmethod
    def _stop(
            process: multiprocessing.Process, *, graceful_result: bool = False,
    ) -> None:
        """Ensure no worker can continue using request data after return."""
        if not process.is_alive():
            process.join()
            return
        if graceful_result:
            # A worker that already sent its one result is returning normally.
            # Let provider cleanup run before the hard-stop fallback so ML
            # runtimes do not strand multiprocessing resources.
            process.join(0.25)
            if not process.is_alive():
                return
        process.terminate()
        process.join(0.1)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if kill is not None:
                kill()
            process.join()

    def verify(
            self,
            samples: Sequence[float],
            sample_rate: int,
            expected: str,
            *,
            deadline_at: float,
    ) -> VerificationReceipt:
        """Verify one span, refusing any untrusted or late worker response."""
        if (not isinstance(sample_rate, int) or isinstance(sample_rate, bool)
                or sample_rate <= 0):
            raise ValueError("sample_rate must be a positive integer")
        if not isinstance(expected, str):
            raise TypeError("expected must be a string")
        if (isinstance(deadline_at, bool)
                or not isinstance(deadline_at, (int, float))
                or not math.isfinite(float(deadline_at))):
            raise ValueError("deadline_at must be a finite monotonic timestamp")
        deadline = float(deadline_at)
        if time.monotonic() >= deadline:
            return self._refused(RefusalReason.TIMEOUT)

        request = VerificationRequest(
            tuple(samples), sample_rate, expected, deadline)
        receiver, sender = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_run_worker,
            args=(self._worker, request, sender),
        )
        result_received = False
        try:
            try:
                process.start()
            except BaseException:
                return self._refused(RefusalReason.CRASH)
            finally:
                sender.close()

            remaining = max(0.0, deadline - time.monotonic())
            if not receiver.poll(remaining):
                return self._refused(RefusalReason.TIMEOUT)
            try:
                payload = receiver.recv()
                result_received = True
            except (EOFError, OSError):
                return self._refused(RefusalReason.CRASH)
            except Exception:
                return self._refused(RefusalReason.MALFORMED_RESULT)

            # Crossing the deadline invalidates even a response already queued
            # by the worker: late evidence must never be accepted.
            if time.monotonic() > deadline:
                return self._refused(RefusalReason.TIMEOUT)
            try:
                result = _validated_result(payload)
            except Exception:
                result = None
            if result is None:
                return self._refused(RefusalReason.MALFORMED_RESULT)
            return VerificationReceipt(result=result)
        finally:
            receiver.close()
            if process.pid is not None:
                try:
                    self._stop(process, graceful_result=result_received)
                finally:
                    close_process = getattr(process, "close", None)
                    if close_process is not None:
                        try:
                            close_process()
                        except (OSError, ValueError):
                            pass
