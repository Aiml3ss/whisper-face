"""Text-free, local compatibility aggregation for insertion outcomes.

This module deliberately has no transport.  It accepts only coarse,
allowlisted capability and outcome buckets, then releases only local groups
that meet a minimum-count threshold and only after explicit opt-in.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = 1
EVIDENCE_SCOPE = "local-minimum-count-aggregate"
DEFAULT_MINIMUM_COUNT = 5
MAX_LOCAL_BUCKETS = 128
MAX_EXPORT_BUCKETS = 32
MAX_BUCKET_COUNT = 1_000_000
MAX_EXPORT_BYTES = 4096

CAPABILITY_KEYS = frozenset({"target", "paste", "readback"})
OUTCOME_KEYS = frozenset({"state", "reason", "paste_attempted"})
TARGET_BUCKETS = frozenset({"readable", "opaque", "unavailable"})
AVAILABILITY_BUCKETS = frozenset({"available", "unavailable"})
STATE_BUCKETS = frozenset({"verified", "unverifiable", "conflict", "unresolved"})
REASON_BUCKETS = frozenset({
    "success",
    "destination-drift",
    "target-unavailable",
    "verification-unavailable",
    "verification-conflict",
    "delivery-unknown",
})

_EXPORT_KEYS = frozenset({
    "schema_version",
    "evidence_scope",
    "minimum_count",
    "released_observations",
    "buckets",
})
_EXPORTED_BUCKET_KEYS = frozenset({"fingerprint", "count"})
_FINGERPRINT_DOMAIN = b"whisper-face/compatibility-fingerprint/v1\0"


def _closed_mapping(value: Mapping[str, Any], expected: frozenset[str],
                    label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} must contain exactly {sorted(expected)!r}")
    return dict(value)


@dataclass(frozen=True)
class CompatibilityObservation:
    """One coarse insertion-capability/outcome observation."""

    target: str
    paste: str
    readback: str
    state: str
    reason: str
    paste_attempted: bool

    def __post_init__(self) -> None:
        if self.target not in TARGET_BUCKETS:
            raise ValueError("unsupported target bucket")
        if self.paste not in AVAILABILITY_BUCKETS:
            raise ValueError("unsupported paste bucket")
        if self.readback not in AVAILABILITY_BUCKETS:
            raise ValueError("unsupported readback bucket")
        if self.state not in STATE_BUCKETS:
            raise ValueError("unsupported state bucket")
        if self.reason not in REASON_BUCKETS:
            raise ValueError("unsupported reason bucket")
        if not isinstance(self.paste_attempted, bool):
            raise ValueError("paste_attempted must be a boolean")

    @classmethod
    def from_buckets(cls, capabilities: Mapping[str, Any],
                     outcome: Mapping[str, Any]) -> "CompatibilityObservation":
        capability = _closed_mapping(
            capabilities, CAPABILITY_KEYS, "capabilities")
        result = _closed_mapping(outcome, OUTCOME_KEYS, "outcome")
        return cls(
            target=capability["target"],
            paste=capability["paste"],
            readback=capability["readback"],
            state=result["state"],
            reason=result["reason"],
            paste_attempted=result["paste_attempted"],
        )

    def fingerprint(self) -> str:
        """Return a stable token for this deliberately small bucket set."""
        canonical = json.dumps(
            {
                "paste": self.paste,
                "paste_attempted": self.paste_attempted,
                "readback": self.readback,
                "reason": self.reason,
                "state": self.state,
                "target": self.target,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(_FINGERPRINT_DOMAIN + canonical).hexdigest()[:16]


class CompatibilityFingerprintAggregator:
    """Bounded local counter with explicit-opt-in, minimum-count release."""

    def __init__(self, *, minimum_count: int = DEFAULT_MINIMUM_COUNT,
                 max_local_buckets: int = MAX_LOCAL_BUCKETS):
        if (isinstance(minimum_count, bool) or not isinstance(minimum_count, int)
                or not 2 <= minimum_count <= 1000):
            raise ValueError("minimum_count must be between 2 and 1000")
        if (isinstance(max_local_buckets, bool)
                or not isinstance(max_local_buckets, int)
                or not 1 <= max_local_buckets <= MAX_LOCAL_BUCKETS):
            raise ValueError(
                f"max_local_buckets must be between 1 and {MAX_LOCAL_BUCKETS}")
        self.minimum_count = minimum_count
        self.max_local_buckets = max_local_buckets
        self._counts: Counter[str] = Counter()

    def record(self, capabilities: Mapping[str, Any],
               outcome: Mapping[str, Any]) -> str:
        observation = CompatibilityObservation.from_buckets(
            capabilities, outcome)
        fingerprint = observation.fingerprint()
        if (fingerprint not in self._counts
                and len(self._counts) >= self.max_local_buckets):
            raise OverflowError("local compatibility bucket limit reached")
        self._counts[fingerprint] = min(
            MAX_BUCKET_COUNT, self._counts[fingerprint] + 1)
        return fingerprint

    def export_payload(self, *, opt_in: bool = False) -> dict[str, Any] | None:
        """Build a text-free aggregate; export remains off by default."""
        if opt_in is not True:
            return None
        eligible = sorted(
            (
                (fingerprint, count)
                for fingerprint, count in self._counts.items()
                if count >= self.minimum_count
            ),
            key=lambda item: (-item[1], item[0]),
        )[:MAX_EXPORT_BUCKETS]
        buckets = [
            {"fingerprint": fingerprint, "count": count}
            for fingerprint, count in eligible
        ]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "evidence_scope": EVIDENCE_SCOPE,
            "minimum_count": self.minimum_count,
            "released_observations": sum(bucket["count"] for bucket in buckets),
            "buckets": buckets,
        }
        validate_export_payload(payload)
        return payload

    def export_json(self, *, opt_in: bool = False) -> bytes | None:
        payload = self.export_payload(opt_in=opt_in)
        if payload is None:
            return None
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > MAX_EXPORT_BYTES:
            raise ValueError("compatibility export exceeds byte limit")
        return encoded


def validate_export_payload(payload: Mapping[str, Any]) -> None:
    """Reject altered, unbounded, or schema-expanding export payloads."""
    envelope = _closed_mapping(payload, _EXPORT_KEYS, "export")
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported compatibility export schema")
    if envelope["evidence_scope"] != EVIDENCE_SCOPE:
        raise ValueError("unsupported compatibility evidence scope")
    minimum_count = envelope["minimum_count"]
    if (isinstance(minimum_count, bool) or not isinstance(minimum_count, int)
            or not 2 <= minimum_count <= 1000):
        raise ValueError("invalid compatibility minimum count")
    buckets = envelope["buckets"]
    if not isinstance(buckets, list) or len(buckets) > MAX_EXPORT_BUCKETS:
        raise ValueError("invalid compatibility bucket list")
    seen: set[str] = set()
    released = 0
    for raw_bucket in buckets:
        bucket = _closed_mapping(
            raw_bucket, _EXPORTED_BUCKET_KEYS, "export bucket")
        fingerprint = bucket["fingerprint"]
        count = bucket["count"]
        if (not isinstance(fingerprint, str) or len(fingerprint) != 16
                or any(character not in "0123456789abcdef"
                       for character in fingerprint)
                or fingerprint in seen):
            raise ValueError("invalid or duplicate compatibility fingerprint")
        if (isinstance(count, bool) or not isinstance(count, int)
                or not minimum_count <= count <= MAX_BUCKET_COUNT):
            raise ValueError("invalid compatibility bucket count")
        seen.add(fingerprint)
        released += count
    if envelope["released_observations"] != released:
        raise ValueError("released observation count does not match buckets")
    encoded = json.dumps(
        envelope, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise ValueError("compatibility export exceeds byte limit")
