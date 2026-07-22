"""Bounded, inspectable evidence for personal acoustic keywords.

This module is deliberately a storage and eligibility foundation only.  It
does not bias a recognizer, rewrite a transcript, or insert text.  A future
integration must make that separate behavior explicit and preserve the Voice
Compiler's acoustic-evidence rules.

Only the candidate keyword, coarse hashed application scope, and bounded
digests used to de-duplicate evidence are retained.  Raw audio, surrounding
context, and transcript history are never accepted or serialized.  The caller
chooses where state lives and may atomically persist :meth:`dumps`; this module
has no filesystem or home-directory assumptions.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = 1
STATE_KIND = "whisper-face/acoustic-keyword-memory"
EXPORT_KIND = "whisper-face/acoustic-keyword-memory-export"
MIN_OBSERVATIONS = 3
MIN_CONFIRMATIONS = 2
MAX_ENTRIES = 256
MAX_KEYWORD_CHARS = 80
MAX_APP_IDENTIFIER_CHARS = 255
APP_SCOPE_SALT_BYTES = 16
RECOGNITION_EFFECT = "none"

_STATE_KEYS = frozenset({
    "schema_version", "kind", "policy", "next_sequence", "entries",
})
_POLICY_KEYS = frozenset({
    "minimum_observations", "minimum_confirmations", "max_entries",
    "recognition_effect",
})
_ENTRY_KEYS = frozenset({
    "keyword", "app_scope", "observation_tokens", "confirmation_tokens",
    "sequence",
})
_TOKEN_DOMAIN = b"whisper-face/acoustic-keyword-evidence/v1\0"
_APP_DOMAIN = b"whisper-face/acoustic-keyword-app-scope/v1\0"


def _plain_int(value: Any, *, minimum: int = 0,
               maximum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _closed_mapping(value: Any, expected: frozenset[str],
                    label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} must contain exactly {sorted(expected)!r}")
    return dict(value)


def _normalize_keyword(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("keyword must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized) > MAX_KEYWORD_CHARS:
        raise ValueError(
            f"keyword must contain 1-{MAX_KEYWORD_CHARS} characters")
    if any(unicodedata.category(character) == "Cc"
           for character in normalized):
        raise ValueError("keyword must not contain control characters")
    return normalized


def _valid_scope(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and len(value) == 20
        and value.startswith("app-")
        and all(character in "0123456789abcdef" for character in value[4:])
    )


def _valid_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 20
        and value.startswith("ev-")
        and all(character in "0123456789abcdef" for character in value[3:])
    )


def hash_app_scope(app_identifier: str, *, salt: bytes) -> str:
    """Return the only application identifier format accepted by the store.

    A per-installation random salt prevents a stored token from being a plain,
    enumerable hash of a bundle identifier.  Neither input nor salt is kept.
    """
    if (not isinstance(app_identifier, str) or not app_identifier
            or len(app_identifier) > MAX_APP_IDENTIFIER_CHARS):
        raise ValueError("application identifier must contain 1-255 characters")
    if not isinstance(salt, bytes) or len(salt) < APP_SCOPE_SALT_BYTES:
        raise ValueError(
            f"application scope salt must contain at least "
            f"{APP_SCOPE_SALT_BYTES} bytes")
    digest = hashlib.sha256(
        _APP_DOMAIN + salt + b"\0" + app_identifier.encode("utf-8")
    ).hexdigest()[:16]
    return f"app-{digest}"


def _evidence_token(evidence_id: str) -> str:
    if (not isinstance(evidence_id, str) or not evidence_id
            or len(evidence_id) > 255):
        raise ValueError("evidence_id must be an opaque 1-255 character value")
    digest = hashlib.sha256(
        _TOKEN_DOMAIN + evidence_id.encode("utf-8")
    ).hexdigest()[:17]
    return f"ev-{digest}"


@dataclass(frozen=True)
class KeywordCandidate:
    """Inspectable aggregate state, never an instruction to recognition."""

    keyword: str
    app_scope: str | None
    observations: int
    confirmations: int
    eligible: bool
    status: str


@dataclass
class _Entry:
    keyword: str
    app_scope: str | None
    observation_tokens: list[str]
    confirmation_tokens: list[str]
    sequence: int

    @property
    def eligible(self) -> bool:
        return (
            len(self.observation_tokens) >= MIN_OBSERVATIONS
            and len(self.confirmation_tokens) >= MIN_CONFIRMATIONS
        )


class AcousticKeywordMemory:
    """Keep bounded keyword evidence without changing recognized output."""

    def __init__(self, *, max_entries: int = MAX_ENTRIES) -> None:
        if (not _plain_int(max_entries, minimum=1, maximum=MAX_ENTRIES)):
            raise ValueError(f"max_entries must be between 1 and {MAX_ENTRIES}")
        self.max_entries = max_entries
        self._entries: dict[tuple[str, str | None], _Entry] = {}
        self._next_sequence = 1

    @staticmethod
    def _key(keyword: str, app_scope: str | None) -> tuple[str, str | None]:
        return keyword.casefold(), app_scope

    @staticmethod
    def _validate_scope(app_scope: str | None) -> None:
        if not _valid_scope(app_scope):
            raise ValueError(
                "app_scope must be None or a salted app-<16 hex> token")

    def _advance(self) -> int:
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def _entry(self, keyword: str, app_scope: str | None) -> _Entry:
        key = self._key(keyword, app_scope)
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(keyword, app_scope, [], [], self._advance())
            self._entries[key] = entry
        return entry

    @staticmethod
    def _eviction_rank(entry: _Entry) -> tuple[Any, ...]:
        # Preserve eligible and better-supported candidates ahead of weak,
        # older candidates.  Text/scope make ties independent of dict order.
        return (
            entry.eligible,
            len(entry.confirmation_tokens),
            len(entry.observation_tokens),
            entry.sequence,
            entry.app_scope or "",
            entry.keyword.casefold(),
            entry.keyword,
        )

    def _enforce_bound(self) -> None:
        while len(self._entries) > self.max_entries:
            victim = min(self._entries.values(), key=self._eviction_rank)
            self._entries.pop(self._key(victim.keyword, victim.app_scope))

    def observe(self, keyword: str, *, evidence_id: str,
                app_scope: str | None = None) -> KeywordCandidate:
        """Record one unique candidate occurrence, capped at the threshold."""
        normalized = _normalize_keyword(keyword)
        self._validate_scope(app_scope)
        token = _evidence_token(evidence_id)
        entry = self._entry(normalized, app_scope)
        if (token not in entry.observation_tokens
                and len(entry.observation_tokens) < MIN_OBSERVATIONS):
            entry.observation_tokens.append(token)
            entry.sequence = self._advance()
        self._enforce_bound()
        retained = self._entries.get(self._key(normalized, app_scope))
        if retained is None:
            return self._snapshot(entry)
        return self._snapshot(retained)

    def confirm(self, keyword: str, *, evidence_id: str,
                app_scope: str | None = None) -> KeywordCandidate:
        """Record independent explicit confirmation; it cannot replace reads."""
        normalized = _normalize_keyword(keyword)
        self._validate_scope(app_scope)
        token = _evidence_token(evidence_id)
        entry = self._entry(normalized, app_scope)
        if (token not in entry.confirmation_tokens
                and len(entry.confirmation_tokens) < MIN_CONFIRMATIONS):
            entry.confirmation_tokens.append(token)
            entry.sequence = self._advance()
        self._enforce_bound()
        retained = self._entries.get(self._key(normalized, app_scope))
        if retained is None:
            return self._snapshot(entry)
        return self._snapshot(retained)

    def accept_explicit_correction(
        self,
        keyword: str,
        *,
        evidence_id: str,
        app_scope: str | None = None,
    ) -> KeywordCandidate:
        """Count one exact user correction once in each evidence channel.

        The opaque event identifier is domain-separated before storage. The
        corrected keyword is never folded into the identifier, so repeated
        delivery of the same correction stays idempotent without retaining
        the original transcript or the rejected alternative.
        """
        self.observe(
            keyword,
            evidence_id=f"{evidence_id}:observation",
            app_scope=app_scope,
        )
        return self.confirm(
            keyword,
            evidence_id=f"{evidence_id}:confirmation",
            app_scope=app_scope,
        )

    @staticmethod
    def _snapshot(entry: _Entry) -> KeywordCandidate:
        observations = len(entry.observation_tokens)
        confirmations = len(entry.confirmation_tokens)
        missing_observations = max(0, MIN_OBSERVATIONS - observations)
        missing_confirmations = max(0, MIN_CONFIRMATIONS - confirmations)
        if not missing_observations and not missing_confirmations:
            status = "eligible-not-connected-to-recognition"
        else:
            status = (
                f"needs-{missing_observations}-observations-and-"
                f"{missing_confirmations}-confirmations"
            )
        return KeywordCandidate(
            keyword=entry.keyword,
            app_scope=entry.app_scope,
            observations=observations,
            confirmations=confirmations,
            eligible=entry.eligible,
            status=status,
        )

    @property
    def candidates(self) -> tuple[KeywordCandidate, ...]:
        return tuple(
            self._snapshot(entry)
            for entry in sorted(
                self._entries.values(),
                key=lambda item: (
                    item.app_scope or "", item.keyword.casefold(),
                    item.keyword),
            )
        )

    def forget(self, keyword: str, *,
               app_scope: str | None = None) -> bool:
        """Forget one keyword in exactly one global or hashed app scope."""
        normalized = _normalize_keyword(keyword)
        self._validate_scope(app_scope)
        return self._entries.pop(self._key(normalized, app_scope), None) is not None

    def forget_all(self) -> int:
        """Forget every candidate and return the number removed."""
        removed = len(self._entries)
        self._entries.clear()
        return removed

    def to_dict(self) -> dict[str, Any]:
        """Return strict persistence state for caller-controlled storage."""
        entries = [
            {
                "keyword": entry.keyword,
                "app_scope": entry.app_scope,
                "observation_tokens": sorted(entry.observation_tokens),
                "confirmation_tokens": sorted(entry.confirmation_tokens),
                "sequence": entry.sequence,
            }
            for entry in sorted(
                self._entries.values(),
                key=lambda item: (
                    item.sequence, item.app_scope or "",
                    item.keyword.casefold(), item.keyword),
            )
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": STATE_KIND,
            "policy": {
                "minimum_observations": MIN_OBSERVATIONS,
                "minimum_confirmations": MIN_CONFIRMATIONS,
                "max_entries": self.max_entries,
                "recognition_effect": RECOGNITION_EFFECT,
            },
            "next_sequence": self._next_sequence,
            "entries": entries,
        }

    def dumps(self) -> str:
        """Serialize state; callers may atomically write the returned text."""
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    def export_dict(self) -> dict[str, Any]:
        """Explicitly export inspectable counts without evidence digests."""
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": EXPORT_KIND,
            "policy": {
                "minimum_observations": MIN_OBSERVATIONS,
                "minimum_confirmations": MIN_CONFIRMATIONS,
                "max_entries": self.max_entries,
                "recognition_effect": RECOGNITION_EFFECT,
            },
            "candidates": [
                {
                    "keyword": candidate.keyword,
                    "app_scope": candidate.app_scope,
                    "observations": candidate.observations,
                    "confirmations": candidate.confirmations,
                    "eligible": candidate.eligible,
                    "status": candidate.status,
                }
                for candidate in self.candidates
            ],
        }

    def export_json(self) -> str:
        return json.dumps(
            self.export_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def loads(cls, payload: str) -> "AcousticKeywordMemory":
        if not isinstance(payload, str):
            raise ValueError("acoustic keyword memory state must be JSON text")
        try:
            decoded = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid acoustic keyword memory JSON") from exc
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AcousticKeywordMemory":
        state = _closed_mapping(payload, _STATE_KEYS, "state")
        if (not _plain_int(
                state["schema_version"], minimum=SCHEMA_VERSION,
                maximum=SCHEMA_VERSION)):
            raise ValueError("unsupported acoustic keyword memory schema")
        if state["kind"] != STATE_KIND:
            raise ValueError("unsupported acoustic keyword memory kind")
        policy = _closed_mapping(state["policy"], _POLICY_KEYS, "policy")
        if (policy["minimum_observations"] != MIN_OBSERVATIONS
                or policy["minimum_confirmations"] != MIN_CONFIRMATIONS
                or policy["recognition_effect"] != RECOGNITION_EFFECT):
            raise ValueError("unsupported acoustic keyword memory policy")
        max_entries = policy["max_entries"]
        memory = cls(max_entries=max_entries)
        next_sequence = state["next_sequence"]
        if not _plain_int(next_sequence, minimum=1):
            raise ValueError("next_sequence must be a positive integer")
        raw_entries = state["entries"]
        if (not isinstance(raw_entries, list)
                or len(raw_entries) > max_entries):
            raise ValueError("entries exceed the configured cardinality bound")
        seen_keys: set[tuple[str, str | None]] = set()
        seen_sequences: set[int] = set()
        for raw_entry in raw_entries:
            item = _closed_mapping(raw_entry, _ENTRY_KEYS, "entry")
            keyword = _normalize_keyword(item["keyword"])
            if keyword != item["keyword"]:
                raise ValueError("stored keyword must already be normalized")
            app_scope = item["app_scope"]
            memory._validate_scope(app_scope)
            observations = item["observation_tokens"]
            confirmations = item["confirmation_tokens"]
            if (not isinstance(observations, list)
                    or len(observations) > MIN_OBSERVATIONS
                    or not all(_valid_token(token) for token in observations)
                    or len(set(observations)) != len(observations)):
                raise ValueError("invalid observation token set")
            if (not isinstance(confirmations, list)
                    or len(confirmations) > MIN_CONFIRMATIONS
                    or not all(_valid_token(token) for token in confirmations)
                    or len(set(confirmations)) != len(confirmations)):
                raise ValueError("invalid confirmation token set")
            sequence = item["sequence"]
            if (not _plain_int(sequence, minimum=1)
                    or sequence in seen_sequences
                    or sequence >= next_sequence):
                raise ValueError("invalid or duplicate entry sequence")
            key = memory._key(keyword, app_scope)
            if key in seen_keys:
                raise ValueError("duplicate keyword and application scope")
            seen_keys.add(key)
            seen_sequences.add(sequence)
            memory._entries[key] = _Entry(
                keyword=keyword,
                app_scope=app_scope,
                observation_tokens=list(observations),
                confirmation_tokens=list(confirmations),
                sequence=sequence,
            )
        memory._next_sequence = next_sequence
        return memory
