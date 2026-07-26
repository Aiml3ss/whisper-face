"""Strict physical-evidence gate for macOS delayed cleanup.

The evaluator accepts only content-free caller-attested case records.  It does
not operate applications or claim that synthetic tests are physical evidence.
The runtime may load the resulting closed receipt, but an absent, malformed,
mixed, or failing receipt keeps delayed cleanup disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping


SCHEMA_VERSION = 1
SUITE_ID = "mac-delayed-cleanup-v1"
PHYSICAL_SOURCE = "caller-attested-physical"
MIN_CASES = 50
MIN_SURFACE_CASES = 10
MIN_SCENARIO_CASES = 8
MAX_P95_APPLY_MS = 150.0
SURFACES = frozenset({
    "native-text", "web-text", "electron-editor", "terminal-editor",
})
SCENARIOS = frozenset({
    "unchanged", "edit-elsewhere", "edit-overlap", "focus-drift",
    "duplicate-callback",
})
OUTCOMES = frozenset({
    "applied", "unreadable_target", "focus_drift", "revision_drift",
    "text_drift", "ambiguous_merge", "no_safe_changes",
    "compare_and_swap_rejected", "adapter_exception",
    "proposal_in_flight", "proposal_failed",
})
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_RECORD_KEYS = frozenset({
    "id", "source", "surface", "scenario", "expected_outcome",
    "actual_outcome", "wrong_target_write", "user_edit_overwritten",
    "selection_disrupted", "duplicate_write", "apply_ms",
})
_RECEIPT_KEYS = frozenset({
    "schema_version", "suite_id", "evidence_scope", "manual_reviewed",
    "case_count", "surface_counts", "scenario_counts", "applied_count",
    "rejected_count", "outcome_mismatches", "wrong_target_writes",
    "user_edit_overwrites", "selection_disruptions", "duplicate_writes",
    "p95_apply_ms", "records_sha256", "active", "reason",
})


def _plain_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 10_000.0
    )


def _plain_count(value: object, minimum: int = 0) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= 10_000
    )


def _validated_record(record: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(record, Mapping) or set(record) != _RECORD_KEYS:
        raise ValueError("delayed-cleanup case schema is not closed")
    normalized = dict(record)
    if not isinstance(normalized["id"], str) or not _IDENTIFIER.fullmatch(
            normalized["id"]):
        raise ValueError("invalid delayed-cleanup case identifier")
    if normalized["source"] != PHYSICAL_SOURCE:
        raise ValueError("physical caller-attested evidence is required")
    if normalized["surface"] not in SURFACES:
        raise ValueError("invalid delayed-cleanup surface")
    if normalized["scenario"] not in SCENARIOS:
        raise ValueError("invalid delayed-cleanup scenario")
    if normalized["expected_outcome"] not in OUTCOMES \
            or normalized["actual_outcome"] not in OUTCOMES:
        raise ValueError("invalid delayed-cleanup outcome")
    for key in (
            "wrong_target_write", "user_edit_overwritten",
            "selection_disrupted", "duplicate_write"):
        if not isinstance(normalized[key], bool):
            raise ValueError("invalid delayed-cleanup safety observation")
    if not _plain_number(normalized["apply_ms"]):
        raise ValueError("invalid delayed-cleanup latency")
    normalized["apply_ms"] = round(float(normalized["apply_ms"]), 4)
    return normalized


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 4)


def evaluate_activation(
        records: Iterable[Mapping[str, object]], *,
        manual_reviewed: bool,
) -> dict[str, object]:
    """Return a closed activation receipt from content-free physical records."""
    if not isinstance(manual_reviewed, bool):
        raise ValueError("manual_reviewed must be a boolean")
    items = tuple(_validated_record(record) for record in records)
    ids = tuple(str(item["id"]) for item in items)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate delayed-cleanup case identifier")
    surface_counts = Counter(str(item["surface"]) for item in items)
    scenario_counts = Counter(str(item["scenario"]) for item in items)
    mismatches = sum(
        item["actual_outcome"] != item["expected_outcome"] for item in items)
    wrong_target_writes = sum(
        bool(item["wrong_target_write"]) for item in items)
    user_edit_overwrites = sum(
        bool(item["user_edit_overwritten"]) for item in items)
    selection_disruptions = sum(
        bool(item["selection_disrupted"]) for item in items)
    duplicate_writes = sum(bool(item["duplicate_write"]) for item in items)
    applied_count = sum(
        item["actual_outcome"] == "applied" for item in items)
    rejected_count = len(items) - applied_count
    p95_apply_ms = _p95([
        float(item["apply_ms"]) for item in items
    ]) if items else 0.0
    canonical = json.dumps(
        items, sort_keys=True, separators=(",", ":")).encode("utf-8")

    reason = "physical-evidence-passed"
    active = True
    checks = (
        (manual_reviewed, "manual-review-required"),
        (len(items) >= MIN_CASES, "minimum-physical-evidence-not-met"),
        (all(surface_counts[surface] >= MIN_SURFACE_CASES
             for surface in SURFACES), "surface-coverage-not-met"),
        (all(scenario_counts[scenario] >= MIN_SCENARIO_CASES
             for scenario in SCENARIOS), "scenario-coverage-not-met"),
        (applied_count >= 15 and rejected_count >= 15,
         "apply-reject-balance-not-met"),
        (mismatches == 0, "outcome-mismatch"),
        (wrong_target_writes == 0, "wrong-target-write"),
        (user_edit_overwrites == 0, "user-edit-overwrite"),
        (selection_disruptions == 0, "selection-disruption"),
        (duplicate_writes == 0, "duplicate-write"),
        (p95_apply_ms <= MAX_P95_APPLY_MS, "apply-latency-budget-exceeded"),
    )
    for passed, failure_reason in checks:
        if not passed:
            active = False
            reason = failure_reason
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "evidence_scope": "caller-attested-physical-only",
        "manual_reviewed": manual_reviewed,
        "case_count": len(items),
        "surface_counts": {
            key: surface_counts[key] for key in sorted(SURFACES)},
        "scenario_counts": {
            key: scenario_counts[key] for key in sorted(SCENARIOS)},
        "applied_count": applied_count,
        "rejected_count": rejected_count,
        "outcome_mismatches": mismatches,
        "wrong_target_writes": wrong_target_writes,
        "user_edit_overwrites": user_edit_overwrites,
        "selection_disruptions": selection_disruptions,
        "duplicate_writes": duplicate_writes,
        "p95_apply_ms": p95_apply_ms,
        "records_sha256": hashlib.sha256(canonical).hexdigest(),
        "active": active,
        "reason": reason,
    }


def validate_activation_receipt(value: object) -> bool:
    """Accept only the exact passing receipt shape used by the runtime."""
    if not isinstance(value, Mapping) or set(value) != _RECEIPT_KEYS:
        return False
    try:
        return bool(
            value["schema_version"] == SCHEMA_VERSION
            and value["suite_id"] == SUITE_ID
            and value["evidence_scope"] == "caller-attested-physical-only"
            and value["manual_reviewed"] is True
            and _plain_count(value["case_count"], MIN_CASES)
            and isinstance(value["surface_counts"], Mapping)
            and set(value["surface_counts"]) == SURFACES
            and value["surface_counts"] == {
                key: value["surface_counts"][key]
                for key in sorted(SURFACES)
            }
            and all(
                _plain_count(
                    value["surface_counts"][key], MIN_SURFACE_CASES)
                for key in SURFACES
            )
            and isinstance(value["scenario_counts"], Mapping)
            and set(value["scenario_counts"]) == SCENARIOS
            and value["scenario_counts"] == {
                key: value["scenario_counts"][key]
                for key in sorted(SCENARIOS)
            }
            and all(
                _plain_count(
                    value["scenario_counts"][key], MIN_SCENARIO_CASES)
                for key in SCENARIOS
            )
            and _plain_count(value["applied_count"], 15)
            and _plain_count(value["rejected_count"], 15)
            and value["applied_count"] + value["rejected_count"]
            == value["case_count"]
            and _plain_count(value["outcome_mismatches"])
            and value["outcome_mismatches"] == 0
            and _plain_count(value["wrong_target_writes"])
            and value["wrong_target_writes"] == 0
            and _plain_count(value["user_edit_overwrites"])
            and value["user_edit_overwrites"] == 0
            and _plain_count(value["selection_disruptions"])
            and value["selection_disruptions"] == 0
            and _plain_count(value["duplicate_writes"])
            and value["duplicate_writes"] == 0
            and _plain_number(value["p95_apply_ms"])
            and float(value["p95_apply_ms"]) <= MAX_P95_APPLY_MS
            and isinstance(value["records_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["records_sha256"])
            and value["active"] is True
            and value["reason"] == "physical-evidence-passed"
        )
    except (KeyError, TypeError, ValueError):
        return False


def write_activation_receipt(path: Path, receipt: Mapping[str, object]) -> None:
    """Atomically persist one content-free receipt with owner-only mode."""
    if not validate_activation_receipt(receipt):
        raise ValueError("only a passing activation receipt may be installed")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate content-free physical delayed-cleanup cases.")
    parser.add_argument("records", type=Path)
    parser.add_argument(
        "--write-receipt", type=Path,
        default=Path("delayed_cleanup_activation.json"))
    parser.add_argument("--manual-reviewed", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.records.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"records"} \
                or not isinstance(payload["records"], list):
            raise ValueError("records file must contain only a records list")
        receipt = evaluate_activation(
            payload["records"], manual_reviewed=args.manual_reviewed)
        if not receipt["active"]:
            print(
                "delayed cleanup remains disabled: "
                f"{receipt['reason']} ({receipt['case_count']} cases)")
            return 1
        write_activation_receipt(args.write_receipt, receipt)
        print(
            "delayed cleanup activation receipt installed: "
            f"{receipt['case_count']} cases, "
            f"p95 {receipt['p95_apply_ms']:.1f} ms")
        return 0
    except (OSError, ValueError, json.JSONDecodeError):
        print("delayed cleanup remains disabled: invalid evidence file")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
