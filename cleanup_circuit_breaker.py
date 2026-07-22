"""Content-free circuit breaker for deadline-bound local cleanup calls.

The breaker owns no transcript, prompt, model output, exception, or provider
handle. After a transport failure it bypasses cleanup during a bounded
cooldown so consecutive dictations can take the deterministic fallback without
repeatedly paying the full local-model timeout. Repeated failures double the
cooldown up to five minutes; one successful probe resets it. Exactly one probe
is admitted after each cooldown.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from threading import Lock
import time
from typing import Callable


MAX_COOLDOWN_SECONDS = 300.0
DEFAULT_COOLDOWN_SECONDS = 60.0


class AdmissionState(str, Enum):
    ALLOWED = "allowed"
    COOLDOWN = "cooldown"
    IN_FLIGHT = "in_flight"


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    """Content-free decision for one attempted cleanup admission."""

    state: AdmissionState
    retry_after_ms: int

    @property
    def allowed(self) -> bool:
        return self.state is AdmissionState.ALLOWED


class CleanupCircuitBreaker:
    """Admit at most one call and cool down after transport failure."""

    def __init__(
        self,
        *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (isinstance(cooldown_seconds, bool)
                or not isinstance(cooldown_seconds, (int, float))
                or not math.isfinite(float(cooldown_seconds))
                or not 0.0 < float(cooldown_seconds)
                <= MAX_COOLDOWN_SECONDS):
            raise ValueError("cooldown must be within 300 seconds")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._lock = Lock()
        self._blocked_until = 0.0
        self._in_flight = False
        self._next_cooldown_seconds = self._cooldown_seconds

    def acquire(self) -> AdmissionReceipt:
        """Admit one call, or return immediately with a bypass reason."""
        with self._lock:
            now = self._clock()
            if self._in_flight:
                return AdmissionReceipt(AdmissionState.IN_FLIGHT, 0)
            if now < self._blocked_until:
                remaining_ms = max(
                    1, math.ceil((self._blocked_until - now) * 1_000.0))
                return AdmissionReceipt(
                    AdmissionState.COOLDOWN, remaining_ms)
            self._in_flight = True
            return AdmissionReceipt(AdmissionState.ALLOWED, 0)

    def record_success(self) -> None:
        """Close the breaker after a responsive, valid transport exchange."""
        with self._lock:
            self._require_in_flight()
            self._in_flight = False
            self._blocked_until = 0.0
            self._next_cooldown_seconds = self._cooldown_seconds

    def record_transport_failure(self) -> None:
        """Open the cooldown after a timeout or local-service failure."""
        with self._lock:
            self._require_in_flight()
            self._in_flight = False
            self._blocked_until = self._clock() + self._next_cooldown_seconds
            self._next_cooldown_seconds = min(
                MAX_COOLDOWN_SECONDS,
                self._next_cooldown_seconds * 2.0,
            )

    def release(self) -> None:
        """Release a call rejected above the transport layer without opening."""
        with self._lock:
            self._require_in_flight()
            self._in_flight = False

    def _require_in_flight(self) -> None:
        if not self._in_flight:
            raise RuntimeError("cleanup circuit has no admitted call")


__all__ = [
    "AdmissionReceipt",
    "AdmissionState",
    "CleanupCircuitBreaker",
    "DEFAULT_COOLDOWN_SECONDS",
    "MAX_COOLDOWN_SECONDS",
]
