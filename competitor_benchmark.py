"""Neutral, offline task protocol for reproducible product observations.

The protocol defines user tasks, validates externally collected observations,
and computes descriptive aggregates.  It does not run products, access a
network, automate a UI, rank products, or treat marketing claims as measured
evidence.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
EVIDENCE_SCOPE = "neutral-user-task-protocol"
MEASUREMENT_FIELDS = (
    "completed", "error_count", "latency_ms", "interaction_count",
)
MEASUREMENT_DEFINITIONS = {
    "completed": (
        "True only when the task completion rule is met before the observer "
        "ends the attempt; otherwise false."),
    "error_count": (
        "Count discrete wrong, missing, duplicated, or blocking outcomes "
        "against the task completion rule; an incomplete measured task must "
        "record at least one error."),
    "latency_ms": (
        "Monotonic wall-clock milliseconds from the observer's first user "
        "action in the written procedure until its completion rule is met or "
        "the measured attempt ends."),
    "interaction_count": (
        "Count intentional clicks, taps, or key gestures in the documented "
        "primary flow; permission approvals count, spoken words do not."),
}
MAX_TASKS = 64

_CORPUS_KEYS = frozenset({
    "schema_version", "protocol_id", "evidence_scope",
    "product_results_included", "measurement_fields",
    "measurement_definitions", "tasks",
})
_TASK_KEYS = frozenset({
    "schema_version", "task_id", "category", "title", "procedure",
    "completion_rule",
})
_RUN_KEYS = frozenset({
    "schema_version", "protocol_id", "product_id", "run_id",
    "environment_id", "observations",
})
_OBSERVATION_KEYS = frozenset({
    "task_id", "evidence_state", "completed", "error_count", "latency_ms",
    "interaction_count", "unavailable_reason", "source_reference",
})


class EvidenceState(str, Enum):
    MEASURED = "measured"
    UNAVAILABLE = "unavailable"
    CLAIMED_ONLY = "claimed_only"


class UnavailableReason(str, Enum):
    ENVIRONMENT_UNSUPPORTED = "environment_unsupported"
    NOT_RUN = "not_run"
    OBSERVATION_MISSING = "observation_missing"
    PRODUCT_UNAVAILABLE = "product_unavailable"


class ProtocolError(ValueError):
    """Raised when protocol or observation data violates the closed schema."""


def _closed_mapping(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ProtocolError(f"invalid {label} schema")
    return dict(value)


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 96
        and value[0].isalnum()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _bounded_text(value: Any, *, limit: int = 512) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= limit
        and not any(ord(character) < 32 and character not in "\n\t" for character in value)
    )


def _version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _nonnegative_int(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and 0 <= value <= 1_000_000
    )


def _nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and 0 <= value <= 3_600_000
    )


@dataclass(frozen=True)
class Task:
    task_id: str
    category: str
    title: str
    procedure: str
    completion_rule: str

    @classmethod
    def from_mapping(cls, value: Any) -> "Task":
        task = _closed_mapping(value, _TASK_KEYS, "task")
        if not _version(task["schema_version"]):
            raise ProtocolError("unsupported task schema")
        if not _identifier(task["task_id"]) or not _identifier(task["category"]):
            raise ProtocolError("invalid task identity")
        for field in ("title", "procedure", "completion_rule"):
            if not _bounded_text(task[field]):
                raise ProtocolError(f"invalid task {field}")
        return cls(**{field: task[field] for field in (
            "task_id", "category", "title", "procedure", "completion_rule",
        )})


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    tasks: tuple[Task, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> "Protocol":
        corpus = _closed_mapping(value, _CORPUS_KEYS, "protocol corpus")
        if (not _version(corpus["schema_version"])
                or corpus["evidence_scope"] != EVIDENCE_SCOPE
                or corpus["product_results_included"] is not False):
            raise ProtocolError("unsupported protocol declaration")
        if corpus["measurement_fields"] != list(MEASUREMENT_FIELDS):
            raise ProtocolError("measurement fields must match the closed schema")
        if corpus["measurement_definitions"] != MEASUREMENT_DEFINITIONS:
            raise ProtocolError(
                "measurement definitions must match the closed protocol")
        if not _identifier(corpus["protocol_id"]):
            raise ProtocolError("invalid protocol identifier")
        if (not isinstance(corpus["tasks"], list) or not corpus["tasks"]
                or len(corpus["tasks"]) > MAX_TASKS):
            raise ProtocolError("protocol tasks must be a bounded non-empty list")
        tasks = tuple(Task.from_mapping(task) for task in corpus["tasks"])
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ProtocolError("task identifiers must be unique")
        return cls(corpus["protocol_id"], tasks)


@dataclass(frozen=True)
class Observation:
    task_id: str
    evidence_state: EvidenceState
    completed: bool | None
    error_count: int | None
    latency_ms: float | None
    interaction_count: int | None
    unavailable_reason: UnavailableReason | None
    source_reference: str | None

    @classmethod
    def from_mapping(cls, value: Any) -> "Observation":
        item = _closed_mapping(value, _OBSERVATION_KEYS, "observation")
        if not _identifier(item["task_id"]):
            raise ProtocolError("invalid observation task")
        try:
            state = EvidenceState(item["evidence_state"])
        except (TypeError, ValueError) as error:
            raise ProtocolError("invalid observation evidence state") from error

        metrics = tuple(item[field] for field in MEASUREMENT_FIELDS)
        reason = item["unavailable_reason"]
        reference = item["source_reference"]
        if state == EvidenceState.MEASURED:
            if (not isinstance(item["completed"], bool)
                    or not _nonnegative_int(item["error_count"])
                    or not _nonnegative_number(item["latency_ms"])
                    or not _nonnegative_int(item["interaction_count"])):
                raise ProtocolError("measured observations require all measured fields")
            if item["completed"] is False and item["error_count"] < 1:
                raise ProtocolError(
                    "an incomplete measured task requires at least one error")
            if reason is not None or not _bounded_text(reference, limit=256):
                raise ProtocolError("measured observations require only a source reference")
            parsed_reason = None
        elif state == EvidenceState.UNAVAILABLE:
            if any(metric is not None for metric in metrics) or reference is not None:
                raise ProtocolError("unavailable observations cannot carry results")
            try:
                parsed_reason = UnavailableReason(reason)
            except (TypeError, ValueError) as error:
                raise ProtocolError("unavailable observation needs a closed reason") from error
        else:
            if (any(metric is not None for metric in metrics) or reason is not None
                    or not _bounded_text(reference, limit=256)):
                raise ProtocolError("claimed-only observations cannot carry measured results")
            parsed_reason = None

        return cls(
            task_id=item["task_id"],
            evidence_state=state,
            completed=item["completed"],
            error_count=item["error_count"],
            latency_ms=(float(item["latency_ms"])
                        if item["latency_ms"] is not None else None),
            interaction_count=item["interaction_count"],
            unavailable_reason=parsed_reason,
            source_reference=reference,
        )


def evaluate_product_run(
    corpus: Protocol | Mapping[str, Any], run: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one complete run and compute measured-only aggregates."""
    protocol = corpus if isinstance(corpus, Protocol) else Protocol.from_mapping(corpus)
    envelope = _closed_mapping(run, _RUN_KEYS, "product run")
    if not _version(envelope["schema_version"]):
        raise ProtocolError("unsupported product run schema")
    if envelope["protocol_id"] != protocol.protocol_id:
        raise ProtocolError("product run uses a different protocol")
    for field in ("product_id", "run_id", "environment_id"):
        if not _identifier(envelope[field]):
            raise ProtocolError(f"invalid {field}")
    if not isinstance(envelope["observations"], list):
        raise ProtocolError("product run observations must be a list")
    observations = tuple(
        Observation.from_mapping(item) for item in envelope["observations"]
    )
    observed_ids = [item.task_id for item in observations]
    expected_ids = {task.task_id for task in protocol.tasks}
    if len(set(observed_ids)) != len(observed_ids):
        raise ProtocolError("each task must have exactly one observation")
    if set(observed_ids) != expected_ids:
        raise ProtocolError("product run must explicitly cover every protocol task")

    measured = tuple(
        item for item in observations if item.evidence_state == EvidenceState.MEASURED
    )
    unavailable = tuple(
        item for item in observations
        if item.evidence_state == EvidenceState.UNAVAILABLE
    )
    claimed = tuple(
        item for item in observations
        if item.evidence_state == EvidenceState.CLAIMED_ONLY
    )
    reasons = Counter(item.unavailable_reason.value for item in unavailable)
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": protocol.protocol_id,
        "product_id": envelope["product_id"],
        "run_id": envelope["run_id"],
        "environment_id": envelope["environment_id"],
        "coverage": {
            "tasks": len(protocol.tasks),
            "measured": len(measured),
            "unavailable": len(unavailable),
            "claimed_only": len(claimed),
        },
        "measured": {
            "completed_tasks": sum(item.completed is True for item in measured),
            "completion_rate": (
                round(sum(item.completed is True for item in measured) / len(measured), 4)
                if measured else None
            ),
            "error_count_total": (
                sum(item.error_count for item in measured) if measured else None
            ),
            "per_task": [
                {
                    "task_id": item.task_id,
                    "completed": item.completed,
                    "error_count": item.error_count,
                    "latency_ms": item.latency_ms,
                    "interaction_count": item.interaction_count,
                }
                for item in sorted(measured, key=lambda entry: entry.task_id)
            ],
        },
        "unavailable": {
            "count": len(unavailable),
            "reasons": dict(sorted(reasons.items())),
        },
        "claimed_only": {
            "count": len(claimed),
            "included_in_measured_aggregates": False,
        },
    }


def evaluate_product_runs(
    corpus: Protocol | Mapping[str, Any], runs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate runs in stable order without ranking or declaring a winner."""
    protocol = corpus if isinstance(corpus, Protocol) else Protocol.from_mapping(corpus)
    evaluated = [evaluate_product_run(protocol, run) for run in runs]
    keys = [(item["product_id"], item["run_id"]) for item in evaluated]
    if len(set(keys)) != len(keys):
        raise ProtocolError("product and run identifier pairs must be unique")
    return sorted(evaluated, key=lambda item: (item["product_id"], item["run_id"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate neutral competitor task observation files")
    parser.add_argument("run", nargs="+", type=Path)
    parser.add_argument(
        "--protocol", type=Path,
        default=Path(__file__).with_name("benchmarks") / "competitor_tasks.json")
    args = parser.parse_args(argv)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in args.run]
    print(json.dumps(
        evaluate_product_runs(protocol, runs), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
