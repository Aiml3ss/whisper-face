"""Exactly-once coordinator for an explicit Point-and-Speak action.

The coordinator knows nothing about Accessibility, target names, phrases, or
transcripts. It consumes one short-lived opaque lease, rechecks it once, and
allows at most one execution callback for a session-issued nonce. Only
content-free pending nonces and terminal receipts are retained, both bounded.
Consumed or evicted nonces are never accepted again, even after their receipt
ages out of the cache.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import math
import secrets
import threading
import time
from typing import Callable


SCHEMA_VERSION = 1
MAX_LEASE_AGE_SECONDS = 2.0
MAX_RECEIPTS = 128


class TransactionState(str, Enum):
    EXECUTED = "executed"
    RECHECK_FAILED = "recheck_failed"
    EXPIRED = "expired"
    UNSUPPORTED = "unsupported"
    EXECUTION_FAILED = "execution_failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class TransactionReceipt:
    """Content-free terminal evidence retained only for idempotency."""

    state: TransactionState
    attempted: bool
    recheck: str
    schema_version: int = SCHEMA_VERSION

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "attempted": self.attempted,
            "recheck": self.recheck,
        }


@dataclass(frozen=True, slots=True, repr=False)
class TargetLease:
    """Private, short-lived target identity and its two narrow callbacks."""

    created_at: float
    role: str
    evidence: object
    recheck: Callable[[object], bool]
    execute: Callable[[object], bool]


class PointAndSpeakTransactions:
    """Serialize nonces so concurrent or repeated calls cannot repeat action."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_age_seconds: float = MAX_LEASE_AGE_SECONDS,
        max_receipts: int = MAX_RECEIPTS,
    ) -> None:
        if (not isinstance(max_age_seconds, (int, float))
                or isinstance(max_age_seconds, bool)
                or not math.isfinite(max_age_seconds)
                or max_age_seconds <= 0):
            raise ValueError("max_age_seconds must be positive and finite")
        if (not isinstance(max_receipts, int) or isinstance(max_receipts, bool)
                or max_receipts <= 0):
            raise ValueError("max_receipts must be positive")
        self._clock = clock
        self._max_age_seconds = float(max_age_seconds)
        self._max_receipts = max_receipts
        self._session_prefix = secrets.token_urlsafe(16)
        self._sequence = 0
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._receipts: OrderedDict[str, TransactionReceipt] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _valid_nonce(nonce: object) -> bool:
        return (
            isinstance(nonce, str)
            and 16 <= len(nonce) <= 96
            and all(character.isalnum() or character in "-_" for character in nonce)
        )

    def _remember(
        self, nonce: str, receipt: TransactionReceipt,
    ) -> TransactionReceipt:
        self._receipts[nonce] = receipt
        self._receipts.move_to_end(nonce)
        while len(self._receipts) > self._max_receipts:
            self._receipts.popitem(last=False)
        return receipt

    def issue_nonce(self) -> str:
        """Issue one bounded process-session capability for an explicit action."""

        with self._lock:
            self._sequence += 1
            nonce = f"{self._session_prefix}_{self._sequence:x}"
            self._pending[nonce] = None
            while len(self._pending) > self._max_receipts:
                self._pending.popitem(last=False)
            return nonce

    def execute(self, nonce: str, lease: TargetLease | None) -> TransactionReceipt:
        """Use one session nonce; consumed or evicted nonces fail closed."""

        if not self._valid_nonce(nonce):
            return TransactionReceipt(
                TransactionState.UNAVAILABLE, False, "not_run")
        with self._lock:
            previous = self._receipts.get(nonce)
            if previous is not None:
                return previous
            if nonce not in self._pending:
                return TransactionReceipt(
                    TransactionState.UNAVAILABLE, False, "not_run")
            self._pending.pop(nonce, None)
            if not isinstance(lease, TargetLease):
                return self._remember(nonce, TransactionReceipt(
                    TransactionState.UNAVAILABLE, False, "not_run"))
            if lease.role != "button":
                return self._remember(nonce, TransactionReceipt(
                    TransactionState.UNSUPPORTED, False, "not_run"))
            try:
                age = float(self._clock()) - lease.created_at
            except Exception:
                age = math.inf
            if not math.isfinite(age) or age < 0 or age > self._max_age_seconds:
                return self._remember(nonce, TransactionReceipt(
                    TransactionState.EXPIRED, False, "not_run"))
            try:
                matched = lease.recheck(lease.evidence) is True
            except Exception:
                matched = False
            if not matched:
                return self._remember(nonce, TransactionReceipt(
                    TransactionState.RECHECK_FAILED, False, "mismatched"))

            # The consumed nonce stays absent under the lock through the only
            # action callback, so a concurrent replay cannot enter this window.
            try:
                executed = lease.execute(lease.evidence) is True
            except Exception:
                executed = False
            return self._remember(nonce, TransactionReceipt(
                (TransactionState.EXECUTED if executed
                 else TransactionState.EXECUTION_FAILED),
                True,
                "matched",
            ))
