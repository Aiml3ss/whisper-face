"""Local, platform-independent regression gate for learned corrections.

The lab retains only the exact corrected span and its optional application
scope. It deliberately knows nothing about audio, surrounding document text,
recognition engines, UI frameworks, or persistence locations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_CASES = 256
MAX_MAPPINGS = 128
MAX_QUARANTINED = 64
MAX_SPAN_CHARS = 80


def _valid_span(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_SPAN_CHARS


def _valid_app(value: Any) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= 255)


def _case_id(heard: str, preferred: str, app: str | None) -> str:
    payload = json.dumps(
        [heard.casefold(), preferred, app], ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


@dataclass(frozen=True)
class CorrectionCase:
    """One exact changed span; no audio or surrounding document content."""

    id: str
    heard: str
    preferred: str
    app: str | None = None


@dataclass(frozen=True)
class LearnedMapping:
    """A Personal Prior mapping that passed every applicable case."""

    heard: str
    preferred: str
    app: str | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    """Inspectable outcome of evaluating a proposed Personal Prior mapping."""

    heard: str
    preferred: str
    app: str | None
    passed: bool
    promoted: bool
    reasons: tuple[str, ...] = ()
    case_ids: tuple[str, ...] = ()


class PersonalRegressionLab:
    """Record correction cases and gate learned mappings against them."""

    def __init__(self) -> None:
        self._cases: dict[str, CorrectionCase] = {}
        self._promoted: dict[tuple[str, str | None], LearnedMapping] = {}
        self._quarantined: list[CandidateEvaluation] = []

    @property
    def cases(self) -> tuple[CorrectionCase, ...]:
        return tuple(sorted(self._cases.values(), key=lambda case: case.id))

    @property
    def promoted(self) -> tuple[LearnedMapping, ...]:
        return tuple(sorted(
            self._promoted.values(),
            key=lambda item: ((item.app or ""), item.heard.casefold(),
                              item.preferred),
        ))

    @property
    def quarantined(self) -> tuple[CandidateEvaluation, ...]:
        return tuple(self._quarantined)

    def record_correction(
        self, heard: str, preferred: str, *, app: str | None = None
    ) -> CorrectionCase:
        if (not _valid_span(heard) or not _valid_span(preferred)
                or not _valid_app(app)):
            raise ValueError("correction spans must contain 1-80 characters")
        case = CorrectionCase(
            _case_id(heard, preferred, app), heard, preferred, app)
        self._cases[case.id] = case
        while len(self._cases) > MAX_CASES:
            self._cases.pop(next(iter(self._cases)))
        # New evidence can invalidate a formerly safe Personal Prior. Demote
        # it immediately; the next proposal must pass the expanded suite.
        for key, mapping in tuple(self._promoted.items()):
            if mapping.heard.casefold() != heard.casefold():
                continue
            if mapping.app is not None and mapping.app != app:
                continue
            evaluation = self.evaluate(
                mapping.heard, mapping.preferred, app=mapping.app)
            if not evaluation.passed:
                self._promoted.pop(key, None)
                self._remember_quarantine(evaluation)
        return case

    def propose(
        self, heard: str, preferred: str, *, app: str | None = None
    ) -> CandidateEvaluation:
        if (not _valid_span(heard) or not _valid_span(preferred)
                or not _valid_app(app)):
            raise ValueError("mapping spans must contain 1-80 characters")
        result = self.evaluate(heard, preferred, app=app)
        if not result.passed:
            self._remember_quarantine(result)
            return result
        self._quarantined = [
            item for item in self._quarantined
            if not (item.heard.casefold() == heard.casefold()
                    and item.app == app)
        ]
        self._promoted[(heard.casefold(), app)] = LearnedMapping(
            heard, preferred, app)
        while len(self._promoted) > MAX_MAPPINGS:
            self._promoted.pop(next(iter(self._promoted)))
        return replace(result, promoted=True)

    def _remember_quarantine(self, result: CandidateEvaluation) -> None:
        if result not in self._quarantined:
            self._quarantined.append(result)
        self._quarantined = self._quarantined[-MAX_QUARANTINED:]

    def evaluate(
        self, heard: str, preferred: str, *, app: str | None = None
    ) -> CandidateEvaluation:
        """Evaluate a candidate without promoting or quarantining it."""
        applicable = [
            case for case in self._cases.values()
            if case.heard.casefold() == heard.casefold()
            and (app is None or case.app == app)
        ]
        applicable.sort(key=lambda case: case.id)
        reasons = tuple(
            f"{case.id} ({case.app or 'global'}): expected "
            f"{case.preferred!r}, got {preferred!r}"
            for case in applicable
            if case.preferred != preferred
        )
        if not applicable:
            reasons = ("no applicable correction cases",)
        return CandidateEvaluation(
            heard=heard,
            preferred=preferred,
            app=app,
            passed=not reasons,
            promoted=False,
            reasons=reasons,
            case_ids=tuple(case.id for case in applicable),
        )

    def apply(self, text: str, *, app: str | None = None) -> str:
        chosen: dict[str, LearnedMapping] = {}
        mappings = sorted(
            self._promoted.values(),
            key=lambda mapping: (
                0 if mapping.app == app and app is not None else 1,
                -len(mapping.heard), mapping.heard.casefold()),
        )
        for mapping in mappings:
            if mapping.app is None or mapping.app == app:
                chosen.setdefault(mapping.heard.casefold(), mapping)
        if not chosen:
            return text
        alternatives = sorted(
            (mapping.heard for mapping in chosen.values()),
            key=lambda heard: (-len(heard), heard.casefold()),
        )
        pattern = re.compile(
            rf"(?<!\w)(?:{'|'.join(re.escape(v) for v in alternatives)})(?!\w)",
            flags=re.IGNORECASE,
        )
        # One substitution pass over the original text prevents mapping chains
        # such as Gwen->Qwen and Qwen->When from recursively changing Gwen.
        return pattern.sub(
            lambda match: chosen[match.group(0).casefold()].preferred,
            text,
        )

    def forget(self, heard: str, *, app: str | None = None) -> int:
        """Forget all evidence and decisions for one mapping scope."""
        key = heard.casefold()
        kept_cases = {
            case_id: case for case_id, case in self._cases.items()
            if not (case.heard.casefold() == key and case.app == app)
        }
        removed = len(self._cases) - len(kept_cases)
        self._cases = kept_cases
        self._promoted.pop((key, app), None)
        self._quarantined = [
            item for item in self._quarantined
            if not (item.heard.casefold() == key and item.app == app)
        ]
        return removed

    def to_dict(self) -> dict[str, Any]:
        cases = [
            {
                "id": case.id,
                "heard": case.heard,
                "preferred": case.preferred,
                "app": case.app,
            }
            for case in self.cases
        ]
        promoted = [
            {
                "heard": mapping.heard,
                "preferred": mapping.preferred,
                "app": mapping.app,
            }
            for mapping in self.promoted
        ]
        quarantined = [
            {
                "heard": item.heard,
                "preferred": item.preferred,
                "app": item.app,
                "reasons": list(item.reasons),
                "case_ids": list(item.case_ids),
            }
            for item in self._quarantined
        ]
        quarantined.sort(
            key=lambda item: ((item["app"] or ""),
                              item["heard"].casefold(), item["preferred"],
                              item["reasons"]))
        return {
            "version": SCHEMA_VERSION,
            "cases": cases,
            "promoted": promoted,
            "quarantined": quarantined,
        }

    def dumps(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def loads(cls, payload: str) -> "PersonalRegressionLab":
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("personal regression state must be an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PersonalRegressionLab":
        lab = cls()
        version = payload.get("version", 0)
        if isinstance(version, int) and version > SCHEMA_VERSION:
            return lab
        raw_cases = payload.get("cases", [])
        if isinstance(raw_cases, list):
            for raw in raw_cases:
                if not isinstance(raw, dict):
                    continue
                heard = raw.get("heard", raw.get("from"))
                preferred = raw.get("preferred", raw.get("to"))
                app = raw.get("app", raw.get("bundle"))
                if _valid_span(heard) and _valid_span(preferred) \
                        and _valid_app(app):
                    try:
                        lab.record_correction(heard, preferred, app=app)
                    except ValueError:
                        pass
        raw_promoted = payload.get("promoted", [])
        if isinstance(raw_promoted, list):
            for raw in raw_promoted:
                if not isinstance(raw, dict):
                    continue
                heard = raw.get("heard", raw.get("from"))
                preferred = raw.get("preferred", raw.get("to"))
                app = raw.get("app", raw.get("bundle"))
                if _valid_span(heard) and _valid_span(preferred) \
                        and _valid_app(app):
                    try:
                        lab.propose(heard, preferred, app=app)
                    except ValueError:
                        pass
        raw_quarantined = payload.get("quarantined", [])
        if isinstance(raw_quarantined, list):
            for raw in raw_quarantined:
                if not isinstance(raw, dict):
                    continue
                heard = raw.get("heard")
                preferred = raw.get("preferred")
                app = raw.get("app")
                reasons = raw.get("reasons")
                case_ids = raw.get("case_ids", [])
                if _valid_span(heard) and _valid_span(preferred) \
                        and _valid_app(app) \
                        and isinstance(reasons, list) \
                        and len(reasons) <= 32 \
                        and all(isinstance(reason, str) for reason in reasons) \
                        and all(len(reason) <= 512 for reason in reasons) \
                        and isinstance(case_ids, list) \
                        and len(case_ids) <= MAX_CASES \
                        and all(isinstance(case_id, str)
                                and len(case_id) <= 64
                                for case_id in case_ids):
                    lab._remember_quarantine(CandidateEvaluation(
                        heard=heard,
                        preferred=preferred,
                        app=app,
                        passed=False,
                        promoted=False,
                        reasons=tuple(reasons),
                        case_ids=tuple(case_ids),
                    ))
        return lab
