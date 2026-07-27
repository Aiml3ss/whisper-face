#!/usr/bin/env python3
"""Shared, transcript-free plumbing for the physical capture sessions.

Three guided session tools (`capture_app_matrix.py`,
`capture_delayed_cleanup_cases.py`, `capture_lifecycle_evidence.py`) share the
same honesty rules, so they share the same primitives:

* Every recorded value comes from the runtime's own report or from a closed
  choice the operator typed. Nothing is defaulted, inferred, or back-filled.
* No free text is ever accepted, so no dictated words or private context can
  reach an artifact. Operators answer with single keys from a printed list.
* Session state is private: owner-only ``0600`` files written atomically, so a
  crash mid-session can neither corrupt nor world-read the evidence.
* Sessions resume. An already-recorded case is never silently overwritten.

This module reads no receipts, writes no receipts, and never sets a
manual-review flag. It is deliberately import-only plumbing.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compatibility_fingerprint import (  # noqa: E402
    AVAILABILITY_BUCKETS,
    REASON_BUCKETS,
    STATE_BUCKETS,
    TARGET_BUCKETS,
)
from insertion_integrity import ReceiptReason, ReceiptState  # noqa: E402


SUPPORT_SCHEMA_VERSION = 1
DEFAULT_EVIDENCE_DIR = ROOT / ".evidence"
DEFAULT_TRANSCRIPTS = ROOT / "transcripts.jsonl"
DEFAULT_RUNTIME_LOG = ROOT / "dictate.log"

# `dictate.py` writes these three keys with the user's own words in them. They
# are named here only so every tool can assert it never touches them.
TEXT_BEARING_TRANSCRIPT_KEYS = ("raw", "clean", "observed_text")

# The runtime writes two sentinels when an utterance produced no integrity
# receipt at all. Neither is a ReceiptState/ReceiptReason value, so a case that
# reports them cannot be recorded as evidence of anything.
NO_RECEIPT_STATE = "legacy"
NO_RECEIPT_REASON = "unsupported_field"

RECEIPT_STATES = frozenset(state.value for state in ReceiptState)
RECEIPT_REASONS = frozenset(reason.value for reason in ReceiptReason)

# `compatibility_fingerprint.REASON_BUCKETS` is a coarser namespace than
# `ReceiptReason`. This is a total translation of the terminal reasons, not an
# inference: each receipt reason names exactly one coarse bucket. `pending` is
# absent on purpose — it is the non-terminal staging reason and describes an
# utterance whose insertion never resolved, which has no outcome bucket.
COMPATIBILITY_REASON_BY_RECEIPT_REASON = {
    ReceiptReason.COMMIT_VERIFIED.value: "success",
    # Delivery proven where only leading/trailing whitespace differed. For
    # compatibility purposes that is a success -- every character arrived in
    # order -- while the receipt keeps its own distinct reason.
    ReceiptReason.COMMIT_VERIFIED_EDGE_WHITESPACE.value: "success",
    ReceiptReason.FOCUS_DRIFT.value: "destination-drift",
    ReceiptReason.SELECTION_DRIFT.value: "destination-drift",
    ReceiptReason.SURROUNDING_TEXT_DRIFT.value: "destination-drift",
    ReceiptReason.TARGET_UNREADABLE.value: "target-unavailable",
    ReceiptReason.READBACK_UNAVAILABLE.value: "verification-unavailable",
    ReceiptReason.READBACK_CONFLICT.value: "verification-conflict",
    ReceiptReason.PASTE_OUTCOME_UNKNOWN.value: "delivery-unknown",
}

# macOS bundle identifiers are safe app identity. On Windows `dictate.py`
# writes `windows:<window title>` into the same field, and a window title can
# carry a document name, so anything that is not bundle-shaped is withheld.
_BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


class SessionAborted(Exception):
    """The operator quit, or the input stream ended, before an answer."""


class CaptureError(Exception):
    """A session cannot honestly continue."""


def utc_now() -> str:
    """Return an ISO-8601 UTC stamp; sessions are ordered, not timed."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


# ----------------------------- private output -----------------------------


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write owner-only JSON through a temporary file in the same directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp",
        dir=destination.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows before 3.13 has no fchmod
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


# --------------------------- resumable sessions ---------------------------


class Session:
    """A resumable, append-only capture session.

    Recorded and blocked cases are keyed by case id. A key that already exists
    is never replaced: resuming a session can only add cases the operator has
    not yet answered.
    """

    def __init__(self, path: Path, tool: str, *, plan_digest: str,
                 blocked_reasons: frozenset[str] | None = None):
        self.path = Path(path)
        self.tool = tool
        self.plan_digest = plan_digest
        self.blocked_reasons = blocked_reasons
        self.started_utc = utc_now()
        self.records: dict[str, dict[str, Any]] = {}
        self.blocked: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, path: Path, tool: str, *, plan_digest: str,
             blocked_reasons: frozenset[str] | None = None) -> "Session":
        session = cls(path, tool, plan_digest=plan_digest,
                      blocked_reasons=blocked_reasons)
        if not session.path.exists():
            return session
        try:
            payload = json.loads(session.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CaptureError(
                f"session file is unreadable: {session.path}") from error
        if not isinstance(payload, Mapping):
            raise CaptureError(f"session file is not an object: {session.path}")
        if payload.get("tool") != tool:
            raise CaptureError(
                f"session file belongs to {payload.get('tool')!r}, not {tool!r}")
        if payload.get("plan_digest") != plan_digest:
            raise CaptureError(
                "session file was recorded against a different case plan; "
                "start a new session file rather than mixing plans")
        session.started_utc = str(payload.get("started_utc") or utc_now())
        for entry in payload.get("records") or ():
            session.records[str(entry["case_id"])] = dict(entry)
        for entry in payload.get("blocked") or ():
            session.blocked[str(entry["case_id"])] = dict(entry)
        return session

    def answered(self, case_id: str) -> bool:
        return case_id in self.records or case_id in self.blocked

    def record(self, case_id: str, payload: Mapping[str, Any]) -> None:
        if self.answered(case_id):
            raise CaptureError(f"case already answered: {case_id}")
        self.records[case_id] = {"case_id": case_id, **dict(payload)}
        self.save()

    def block(self, case_id: str, reason: str,
              payload: Mapping[str, Any] | None = None) -> None:
        if self.answered(case_id):
            raise CaptureError(f"case already answered: {case_id}")
        if (self.blocked_reasons is not None
                and reason not in self.blocked_reasons):
            raise CaptureError(f"blocked reason is not in the closed set: {reason}")
        self.blocked[case_id] = {
            "case_id": case_id,
            "blocked_utc": utc_now(),
            "reason": reason,
            **dict(payload or {}),
        }
        self.save()

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SUPPORT_SCHEMA_VERSION,
            "tool": self.tool,
            "plan_digest": self.plan_digest,
            "privacy": "transcript-free",
            "started_utc": self.started_utc,
            "updated_utc": utc_now(),
            "records": [self.records[key] for key in sorted(self.records)],
            "blocked": [self.blocked[key] for key in sorted(self.blocked)],
        }

    def save(self) -> None:
        atomic_write_json(self.path, self.payload())


# ------------------------------ closed input ------------------------------


@dataclass(frozen=True)
class Choice:
    key: str
    value: str
    label: str


def ask_choice(question: str, choices: Sequence[Choice], *,
               reader: TextIO, writer: TextIO) -> str:
    """Return the value behind a listed key. Free text is never accepted.

    The operator may type `q` to abort the session. There is no default and no
    empty-input shortcut: an unanswered question ends the session instead of
    silently producing an observation.
    """
    if not choices:
        raise CaptureError("a closed question needs at least one choice")
    keys = {choice.key: choice.value for choice in choices}
    if "q" in keys:
        raise CaptureError("`q` is reserved for aborting the session")
    while True:
        writer.write(f"\n{question}\n")
        for choice in choices:
            writer.write(f"  [{choice.key}] {choice.label}\n")
        writer.write("  [q] quit and keep what is already recorded\n")
        writer.write("> ")
        writer.flush()
        answer = reader.readline()
        if answer == "":
            raise SessionAborted("input ended before the question was answered")
        answer = answer.strip()
        if answer == "q":
            raise SessionAborted("operator quit")
        if answer in keys:
            return keys[answer]
        writer.write(f"  '{answer}' is not one of the listed keys.\n")


def wait_for_enter(message: str, *, reader: TextIO, writer: TextIO) -> None:
    """Block until the operator confirms they performed a physical action."""
    writer.write(f"\n{message}\n> ")
    writer.flush()
    answer = reader.readline()
    if answer == "":
        raise SessionAborted("input ended before the step was confirmed")
    if answer.strip() == "q":
        raise SessionAborted("operator quit")


# ------------------------------- progress ---------------------------------


def progress_line(done: int, total: int,
                  groups: Mapping[str, tuple[int, int]]) -> str:
    """Render `31/50 · electron-chromium 4/8 · terminal 2/5`."""
    parts = [f"{done}/{total}"]
    for name in sorted(groups):
        recorded, planned = groups[name]
        parts.append(f"{name} {recorded}/{planned}")
    return " · ".join(parts)


# --------------------------- transcript reading ---------------------------


@dataclass(frozen=True)
class TranscriptReceipt:
    """The transcript-free projection of one utterance record.

    Only allowlisted keys are read. `raw`, `clean`, and `observed_text` are
    never touched, and the routing `path` string is reduced to one boolean
    because it embeds the operator's own tone-preset names.
    """

    event_id: str | None
    ts: float | None
    has_metrics: bool
    insertion_state: str | None
    insertion_reason: str | None
    paste_attempted: bool | None
    insertion_verified: bool | None
    delayed_cleanup_scheduled: bool | None
    insertion_ms: float | None
    route_outbox: bool | None
    app_bundle: str | None
    app_identity_withheld: bool
    capabilities: dict[str, str] | None = None

    @property
    def has_receipt(self) -> bool:
        """True only for a real terminal ReceiptState/ReceiptReason pair."""
        return (self.insertion_state in RECEIPT_STATES
                and self.insertion_reason in RECEIPT_REASONS)

    def as_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "insertion_state": self.insertion_state,
            "insertion_reason": self.insertion_reason,
            "paste_attempted": self.paste_attempted,
            "insertion_verified": self.insertion_verified,
            "delayed_cleanup_scheduled": self.delayed_cleanup_scheduled,
            "insertion_ms": self.insertion_ms,
            "route_outbox": self.route_outbox,
            "app_bundle": self.app_bundle,
            "app_identity_withheld": self.app_identity_withheld,
        }

    def compatibility_outcome(self) -> dict[str, Any] | None:
        """Translate a terminal receipt into closed compatibility buckets."""
        if not self.has_receipt or not isinstance(self.paste_attempted, bool):
            return None
        reason = COMPATIBILITY_REASON_BY_RECEIPT_REASON.get(
            str(self.insertion_reason))
        if reason is None or self.insertion_state not in STATE_BUCKETS:
            return None
        return {
            "state": self.insertion_state,
            "reason": reason,
            "paste_attempted": self.paste_attempted,
        }


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def project_transcript_record(entry: object) -> TranscriptReceipt | None:
    """Project one parsed transcript record onto the allowlist."""
    if not isinstance(entry, Mapping):
        return None
    metrics = entry.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else None
    raw_app = entry.get("app")
    bundle = (raw_app if isinstance(raw_app, str)
              and _BUNDLE_ID.fullmatch(raw_app)
              and not raw_app.startswith("windows:") else None)
    route = entry.get("path")
    return TranscriptReceipt(
        event_id=entry.get("id") if isinstance(entry.get("id"), str) else None,
        ts=_optional_number(entry.get("ts")),
        has_metrics=metrics is not None,
        insertion_state=(metrics.get("insertion_state")
                         if metrics and isinstance(
                             metrics.get("insertion_state"), str) else None),
        insertion_reason=(metrics.get("insertion_reason")
                          if metrics and isinstance(
                              metrics.get("insertion_reason"), str) else None),
        paste_attempted=(_optional_bool(metrics.get("paste_attempted"))
                         if metrics else None),
        insertion_verified=(_optional_bool(metrics.get("insertion_verified"))
                            if metrics else None),
        delayed_cleanup_scheduled=(
            _optional_bool(metrics.get("delayed_cleanup_scheduled"))
            if metrics else None),
        insertion_ms=(
            None if not metrics
            or _optional_number(metrics.get("insertion_s")) is None
            else round(_optional_number(metrics.get("insertion_s")) * 1000, 4)),
        route_outbox=(route.startswith("outbox/")
                      if isinstance(route, str) else None),
        app_bundle=bundle,
        app_identity_withheld=isinstance(raw_app, str) and bundle is None,
        capabilities=project_capability_buckets(metrics),
    )


def read_transcript_receipts(path: Path) -> list[TranscriptReceipt]:
    """Read the whole transcript log and project every parsable record.

    `dictate.py` atomically replaces this file when it trims history or
    back-fills a paste outcome, so the file is re-read by path every time
    rather than followed through a held descriptor.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as error:
        raise CaptureError(f"cannot read {path}: {error}") from error
    receipts: list[TranscriptReceipt] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        projected = project_transcript_record(entry)
        if projected is not None:
            receipts.append(projected)
    return receipts


def new_transcript_receipts(
        path: Path, seen_event_ids: Iterable[str],
        baseline_count: int) -> list[TranscriptReceipt]:
    """Return receipts that appeared after a baseline was taken.

    De-duplication is by event id because history trimming renumbers lines.
    Records without an id (the phone endpoint writes none) fall back to the
    positional tail, which is why the baseline count is also carried.
    """
    seen = set(seen_event_ids)
    receipts = read_transcript_receipts(path)
    identified = [item for item in receipts
                  if item.event_id is not None and item.event_id not in seen]
    if identified:
        return identified
    if len(receipts) > baseline_count:
        return receipts[baseline_count:]
    return []


def transcript_baseline(path: Path) -> tuple[list[str], int]:
    """Capture the event ids and record count present before an attempt."""
    receipts = read_transcript_receipts(path)
    return ([item.event_id for item in receipts if item.event_id is not None],
            len(receipts))


# --------------------------- capability buckets ---------------------------

# `compatibility_fingerprint.CompatibilityObservation` needs a capability
# triple: `target`, `paste`, and `readback`. `dictate.commit_insertion`
# computes it per utterance and writes it into these three `transcripts.jsonl`
# metric keys, so the capability half of a compatibility observation is
# sourceable from an ordinary physical session. A record without them (an
# older log, or an utterance the runtime had no lease for) still yields the
# outcome half only, and says so rather than guessing a bucket.
CAPABILITY_METRIC_KEYS = (
    "insertion_target", "insertion_paste", "insertion_readback")
CAPABILITY_UNAVAILABLE_REASON = (
    "runtime-reported-no-target-paste-readback-buckets-for-these-utterances")


def project_capability_buckets(metrics: Mapping[str, Any] | None
                               ) -> dict[str, str] | None:
    """Return a closed capability triple only if the runtime reported one."""
    if not isinstance(metrics, Mapping):
        return None
    target = metrics.get("insertion_target")
    paste = metrics.get("insertion_paste")
    readback = metrics.get("insertion_readback")
    if (target in TARGET_BUCKETS and paste in AVAILABILITY_BUCKETS
            and readback in AVAILABILITY_BUCKETS):
        return {"target": target, "paste": paste, "readback": readback}
    return None


def assert_compatibility_vocabulary() -> None:
    """Fail loudly if the shared vocabulary drifts from its owners."""
    unknown = set(COMPATIBILITY_REASON_BY_RECEIPT_REASON.values()) - set(
        REASON_BUCKETS)
    if unknown:
        raise CaptureError(
            f"reason translation leaves the closed bucket set: {sorted(unknown)}")
    missing = (RECEIPT_REASONS - {ReceiptReason.PENDING.value}
               - set(COMPATIBILITY_REASON_BY_RECEIPT_REASON))
    if missing:
        raise CaptureError(
            f"terminal receipt reasons without a bucket: {sorted(missing)}")


assert_compatibility_vocabulary()


__all__ = [
    "CAPABILITY_METRIC_KEYS",
    "CAPABILITY_UNAVAILABLE_REASON",
    "COMPATIBILITY_REASON_BY_RECEIPT_REASON",
    "DEFAULT_EVIDENCE_DIR",
    "DEFAULT_RUNTIME_LOG",
    "DEFAULT_TRANSCRIPTS",
    "NO_RECEIPT_REASON",
    "NO_RECEIPT_STATE",
    "RECEIPT_REASONS",
    "RECEIPT_STATES",
    "ROOT",
    "TEXT_BEARING_TRANSCRIPT_KEYS",
    "CaptureError",
    "Choice",
    "Session",
    "SessionAborted",
    "TranscriptReceipt",
    "ask_choice",
    "atomic_write_json",
    "identifier",
    "new_transcript_receipts",
    "progress_line",
    "project_capability_buckets",
    "project_transcript_record",
    "read_transcript_receipts",
    "transcript_baseline",
    "utc_now",
    "wait_for_enter",
]
