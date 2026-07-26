"""Content-free promotion gate for private regression-suite candidates.

Callers keep case text and candidate outputs in memory.  Only bounded counts
and a caller-chosen opaque candidate identifier cross this boundary.  The gate
can evaluate models, prompts, dictionaries, and Personal Priors through the
same contract, but it never installs a candidate unless it improves at least
one case and changes no case to an incorrect result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Callable, Iterable


SHADOW_GATE_SCHEMA_VERSION = 1
MAX_SHADOW_CASES = 512
MAX_SHADOW_TEXT_CHARS = 100_000
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class CandidateKind(str, Enum):
    MODEL = "model"
    PROMPT = "prompt"
    DICTIONARY = "dictionary"
    PERSONAL_PRIOR = "personal_prior"


class CandidateDisposition(str, Enum):
    PROMOTED = "promoted"
    QUARANTINED = "quarantined"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True, repr=False)
class ShadowRegressionCase:
    """One private case; text and application scope are repr-redacted."""

    case_id: str
    source: str = field(repr=False)
    expected: str = field(repr=False)
    app: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not _IDENTIFIER.fullmatch(
                self.case_id):
            raise ValueError("invalid shadow case identifier")
        for value in (self.source, self.expected):
            if (not isinstance(value, str)
                    or not 0 < len(value) <= MAX_SHADOW_TEXT_CHARS
                    or "\x00" in value):
                raise ValueError("invalid shadow case text")
        if self.app is not None and (
                not isinstance(self.app, str) or len(self.app) > 255):
            raise ValueError("invalid shadow case application scope")


@dataclass(frozen=True, slots=True)
class CandidateShadowReceipt:
    """Closed, content-free result for one candidate activation attempt."""

    schema_version: int
    candidate_id: str
    kind: CandidateKind
    disposition: CandidateDisposition
    case_count: int
    improvement_count: int
    regression_count: int
    unchanged_count: int
    error_count: int
    activation_attempted: bool
    activated: bool

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_GATE_SCHEMA_VERSION:
            raise ValueError("unsupported shadow receipt schema")
        if not isinstance(self.candidate_id, str) or not _IDENTIFIER.fullmatch(
                self.candidate_id):
            raise ValueError("invalid candidate identifier")
        if not isinstance(self.kind, CandidateKind):
            raise ValueError("invalid candidate kind")
        if not isinstance(self.disposition, CandidateDisposition):
            raise ValueError("invalid candidate disposition")
        counts = (
            self.case_count, self.improvement_count, self.regression_count,
            self.unchanged_count, self.error_count,
        )
        if any(
                not isinstance(value, int) or isinstance(value, bool)
                or not 0 <= value <= MAX_SHADOW_CASES
                for value in counts):
            raise ValueError("invalid shadow receipt count")
        if (self.improvement_count + self.regression_count
                + self.unchanged_count + self.error_count != self.case_count):
            raise ValueError("shadow receipt counts do not balance")
        if not isinstance(self.activation_attempted, bool) \
                or not isinstance(self.activated, bool):
            raise ValueError("invalid activation state")
        if self.activated and (
                self.disposition != CandidateDisposition.PROMOTED
                or not self.activation_attempted):
            raise ValueError("only an attempted promotion may activate")
        if self.disposition == CandidateDisposition.PROMOTED and (
                not self.activated or self.regression_count
                or self.error_count or not self.improvement_count):
            raise ValueError("promotion requires clean material improvement")


CandidateTransform = Callable[[str, str | None], str]
Activation = Callable[[], bool]


class ShadowCandidateGate:
    """Evaluate and activate each opaque candidate at most once."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._receipts: dict[str, CandidateShadowReceipt] = {}

    @staticmethod
    def _output(transform: CandidateTransform,
                case: ShadowRegressionCase) -> str:
        value = transform(case.source, case.app)
        if (not isinstance(value, str)
                or len(value) > MAX_SHADOW_TEXT_CHARS or "\x00" in value):
            raise ValueError("candidate returned invalid text")
        return value

    def attempt(
            self,
            candidate_id: str,
            kind: CandidateKind,
            cases: Iterable[ShadowRegressionCase],
            baseline: CandidateTransform,
            candidate: CandidateTransform,
            activate: Activation,
    ) -> CandidateShadowReceipt:
        if not isinstance(candidate_id, str) or not _IDENTIFIER.fullmatch(
                candidate_id):
            raise ValueError("invalid candidate identifier")
        if not isinstance(kind, CandidateKind):
            raise ValueError("invalid candidate kind")
        if not callable(baseline) or not callable(candidate) \
                or not callable(activate):
            raise TypeError("shadow candidate callbacks must be callable")
        items = tuple(cases)
        if (not items or len(items) > MAX_SHADOW_CASES
                or any(not isinstance(item, ShadowRegressionCase)
                       for item in items)):
            raise ValueError("shadow suite must contain 1-512 cases")
        ids = tuple(item.case_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate shadow case identifier")

        with self._lock:
            completed = self._receipts.get(candidate_id)
            if completed is not None:
                return completed

            improved = regressed = unchanged = errors = 0
            for case in items:
                try:
                    baseline_text = self._output(baseline, case)
                    candidate_text = self._output(candidate, case)
                except Exception:
                    errors += 1
                    continue
                if candidate_text == baseline_text:
                    unchanged += 1
                elif candidate_text == case.expected:
                    improved += 1
                else:
                    regressed += 1

            activation_attempted = False
            activated = False
            if errors or regressed:
                disposition = CandidateDisposition.QUARANTINED
            elif not improved:
                disposition = CandidateDisposition.INSUFFICIENT_EVIDENCE
            else:
                activation_attempted = True
                try:
                    activated = activate() is True
                except Exception:
                    activated = False
                disposition = (
                    CandidateDisposition.PROMOTED if activated
                    else CandidateDisposition.QUARANTINED
                )
            receipt = CandidateShadowReceipt(
                SHADOW_GATE_SCHEMA_VERSION,
                candidate_id,
                kind,
                disposition,
                len(items),
                improved,
                regressed,
                unchanged,
                errors,
                activation_attempted,
                activated,
            )
            self._receipts[candidate_id] = receipt
            return receipt
