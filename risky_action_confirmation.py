"""Pure voice-plus-click gate for risky action proposals.

The gate never executes an action and never stores action payloads.  Callers
register only an opaque identifier and a closed risk class.  A proposal becomes
confirmed only when an explicit voice confirmation is followed by an explicit
click before the short monotonic deadline.  Receipts contain no spoken text or
action content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import os
import re
import threading
import time
from typing import Callable


MAX_CONFIRMATION_WINDOW_SECONDS = 30.0
_OPAQUE_ID = re.compile(r"act-[0-9a-f]{32}\Z")
_VOICE_COMMAND = re.compile(
    r"(?P<decision>confirm|cancel) risky action[.!?]?\Z", re.IGNORECASE)


class RiskClass(str, Enum):
    EXTERNAL_COMMUNICATION = "external_communication"
    CALENDAR_COMMIT = "calendar_commit"
    FILE_MUTATION = "file_mutation"
    AGENT_EXECUTION = "agent_execution"


class VoiceDecision(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


class ConfirmationState(str, Enum):
    AWAITING_VOICE = "awaiting_voice"
    AWAITING_CLICK = "awaiting_click"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ConfirmationReason(str, Enum):
    PROPOSED = "proposed"
    VOICE_CONFIRMED = "voice_confirmed"
    CLICK_BEFORE_VOICE = "click_before_voice"
    TWO_FACTOR_CONFIRMED = "two_factor_confirmed"
    VOICE_CANCELLED = "voice_cancelled"
    EXPLICITLY_CANCELLED = "explicitly_cancelled"
    DEADLINE_EXPIRED = "deadline_expired"
    ALREADY_TERMINAL = "already_terminal"


@dataclass(frozen=True, slots=True)
class ConfirmationReceipt:
    """Content-free evidence for one gate transition."""

    action_id: str
    risk: RiskClass
    state: ConfirmationState
    reason: ConfirmationReason


@dataclass(frozen=True, slots=True)
class RuntimeConfirmationStatus:
    """Content-free state exposed by the inert runtime and native GUI."""

    risk: RiskClass | None
    state: str
    reason: str


@dataclass(frozen=True, slots=True)
class VoiceCommandReceipt:
    """Whether a closed voice command was consumed, without retaining it."""

    consumed: bool
    status: RuntimeConfirmationStatus


@dataclass(slots=True)
class _Proposal:
    action_id: str
    risk: RiskClass
    deadline_at: float
    state: ConfirmationState = ConfirmationState.AWAITING_VOICE


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
        raise ValueError("action_id must be an opaque act-<32 lowercase hex> token")
    return value


class RiskyActionConfirmationGate:
    """Require voice confirmation and a later click without executing work."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._proposals: dict[str, _Proposal] = {}

    def propose(
        self,
        action_id: str,
        risk: RiskClass,
        *,
        window_seconds: float = MAX_CONFIRMATION_WINDOW_SECONDS,
    ) -> ConfirmationReceipt:
        action_id = _identifier(action_id)
        if not isinstance(risk, RiskClass):
            raise TypeError("risk must be a RiskClass")
        if (isinstance(window_seconds, bool)
                or not isinstance(window_seconds, (int, float))
                or not math.isfinite(float(window_seconds))
                or not 0.0 < float(window_seconds)
                <= MAX_CONFIRMATION_WINDOW_SECONDS):
            raise ValueError("confirmation window must be within 30 seconds")
        if action_id in self._proposals:
            raise ValueError("action_id is already registered")
        self._proposals[action_id] = _Proposal(
            action_id=action_id,
            risk=risk,
            deadline_at=self._clock() + float(window_seconds),
        )
        return self._receipt(
            self._proposals[action_id], ConfirmationReason.PROPOSED)

    def record_voice(
        self,
        action_id: str,
        decision: VoiceDecision,
    ) -> ConfirmationReceipt:
        proposal = self._active(action_id)
        if proposal.state is ConfirmationState.EXPIRED:
            return self._receipt(
                proposal, ConfirmationReason.DEADLINE_EXPIRED)
        if proposal.state in self._terminal_states():
            return self._receipt(
                proposal, ConfirmationReason.ALREADY_TERMINAL)
        if not isinstance(decision, VoiceDecision):
            raise TypeError("decision must be a VoiceDecision")
        if decision is VoiceDecision.CANCEL:
            proposal.state = ConfirmationState.CANCELLED
            return self._receipt(
                proposal, ConfirmationReason.VOICE_CANCELLED)
        proposal.state = ConfirmationState.AWAITING_CLICK
        return self._receipt(
            proposal, ConfirmationReason.VOICE_CONFIRMED)

    def click_confirm(self, action_id: str) -> ConfirmationReceipt:
        proposal = self._active(action_id)
        if proposal.state is ConfirmationState.EXPIRED:
            return self._receipt(
                proposal, ConfirmationReason.DEADLINE_EXPIRED)
        if proposal.state in self._terminal_states():
            return self._receipt(
                proposal, ConfirmationReason.ALREADY_TERMINAL)
        if proposal.state is not ConfirmationState.AWAITING_CLICK:
            return self._receipt(
                proposal, ConfirmationReason.CLICK_BEFORE_VOICE)
        proposal.state = ConfirmationState.CONFIRMED
        return self._receipt(
            proposal, ConfirmationReason.TWO_FACTOR_CONFIRMED)

    def cancel(self, action_id: str) -> ConfirmationReceipt:
        proposal = self._active(action_id)
        if proposal.state is ConfirmationState.EXPIRED:
            return self._receipt(
                proposal, ConfirmationReason.DEADLINE_EXPIRED)
        if proposal.state in self._terminal_states():
            return self._receipt(
                proposal, ConfirmationReason.ALREADY_TERMINAL)
        proposal.state = ConfirmationState.CANCELLED
        return self._receipt(
            proposal, ConfirmationReason.EXPLICITLY_CANCELLED)

    def status(self, action_id: str) -> ConfirmationReceipt:
        proposal = self._active(action_id)
        reason = {
            ConfirmationState.AWAITING_VOICE: ConfirmationReason.PROPOSED,
            ConfirmationState.AWAITING_CLICK:
                ConfirmationReason.VOICE_CONFIRMED,
            ConfirmationState.CONFIRMED:
                ConfirmationReason.TWO_FACTOR_CONFIRMED,
            ConfirmationState.CANCELLED:
                ConfirmationReason.ALREADY_TERMINAL,
            ConfirmationState.EXPIRED:
                ConfirmationReason.DEADLINE_EXPIRED,
        }[proposal.state]
        return self._receipt(proposal, reason)

    def forget(self, action_id: str) -> None:
        """Drop a terminal proposal; pending proposals cannot be hidden."""
        action_id = _identifier(action_id)
        proposal = self._proposals.get(action_id)
        if proposal is None:
            raise KeyError(action_id)
        self._expire(proposal)
        if proposal.state not in self._terminal_states():
            raise ValueError("pending confirmation cannot be forgotten")
        del self._proposals[action_id]

    def _active(self, action_id: str) -> _Proposal:
        action_id = _identifier(action_id)
        proposal = self._proposals.get(action_id)
        if proposal is None:
            raise KeyError(action_id)
        self._expire(proposal)
        return proposal

    def _expire(self, proposal: _Proposal) -> None:
        if (proposal.state not in self._terminal_states()
                and self._clock() >= proposal.deadline_at):
            proposal.state = ConfirmationState.EXPIRED

    @staticmethod
    def _terminal_states() -> frozenset[ConfirmationState]:
        return frozenset({
            ConfirmationState.CONFIRMED,
            ConfirmationState.CANCELLED,
            ConfirmationState.EXPIRED,
        })

    @staticmethod
    def _receipt(
        proposal: _Proposal,
        reason: ConfirmationReason,
    ) -> ConfirmationReceipt:
        return ConfirmationReceipt(
            action_id=proposal.action_id,
            risk=proposal.risk,
            state=proposal.state,
            reason=reason,
        )


class InertRiskyActionConfirmationRuntime:
    """Own one explicit confirmation ceremony without owning an action.

    Only a risk class and RAM-only opaque identifier reach the pure gate. The
    runtime recognizes two closed voice commands, exposes no action payload,
    and has no execution callback or persistence surface.
    """

    def __init__(
        self,
        *,
        gate: RiskyActionConfirmationGate | None = None,
        random_bytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        if not callable(random_bytes):
            raise TypeError("random_bytes must be callable")
        self._gate = gate or RiskyActionConfirmationGate()
        self._random_bytes = random_bytes
        self._active_id: str | None = None
        self._lock = threading.RLock()

    def start(
        self,
        risk: RiskClass | str,
        *,
        window_seconds: float = MAX_CONFIRMATION_WINDOW_SECONDS,
    ) -> RuntimeConfirmationStatus:
        """Begin one user-requested inert ceremony for a closed risk class."""
        try:
            normalized_risk = risk if isinstance(risk, RiskClass) else RiskClass(risk)
        except (TypeError, ValueError) as error:
            raise ValueError("risk must be a closed RiskClass value") from error
        with self._lock:
            if self._active_id is not None:
                current = self._gate.status(self._active_id)
                if current.state not in {
                    ConfirmationState.CONFIRMED,
                    ConfirmationState.CANCELLED,
                    ConfirmationState.EXPIRED,
                }:
                    raise RuntimeError("a confirmation ceremony is already pending")
                self._gate.forget(self._active_id)
                self._active_id = None
            for _attempt in range(8):
                token = self._random_bytes(16)
                if not isinstance(token, bytes) or len(token) != 16:
                    raise ValueError("random_bytes must return exactly 16 bytes")
                action_id = f"act-{token.hex()}"
                try:
                    receipt = self._gate.propose(
                        action_id,
                        normalized_risk,
                        window_seconds=window_seconds,
                    )
                except ValueError as error:
                    if "already registered" in str(error):
                        continue
                    raise
                self._active_id = action_id
                return self._project(receipt)
            raise RuntimeError("could not allocate an opaque confirmation identifier")

    def consume_voice(self, text: str) -> VoiceCommandReceipt:
        """Consume only an exact confirm/cancel command for the active ceremony."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        command = _VOICE_COMMAND.fullmatch(text.strip())
        with self._lock:
            status = self._status_locked()
            if command is None or self._active_id is None:
                return VoiceCommandReceipt(False, status)
            decision = (
                VoiceDecision.CONFIRM
                if command.group("decision").casefold() == "confirm"
                else VoiceDecision.CANCEL
            )
            receipt = self._gate.record_voice(self._active_id, decision)
            return VoiceCommandReceipt(True, self._project(receipt))

    def click_confirm(self) -> RuntimeConfirmationStatus:
        """Record the distinct native click; this never executes an action."""
        with self._lock:
            if self._active_id is None:
                raise RuntimeError("no confirmation ceremony is active")
            return self._project(self._gate.click_confirm(self._active_id))

    def cancel(self) -> RuntimeConfirmationStatus:
        """Cancel the active ceremony without hiding its terminal state."""
        with self._lock:
            if self._active_id is None:
                raise RuntimeError("no confirmation ceremony is active")
            return self._project(self._gate.cancel(self._active_id))

    def status(self) -> RuntimeConfirmationStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> RuntimeConfirmationStatus:
        if self._active_id is None:
            return RuntimeConfirmationStatus(None, "idle", "idle")
        return self._project(self._gate.status(self._active_id))

    @staticmethod
    def _project(receipt: ConfirmationReceipt) -> RuntimeConfirmationStatus:
        return RuntimeConfirmationStatus(
            risk=receipt.risk,
            state=receipt.state.value,
            reason=receipt.reason.value,
        )
