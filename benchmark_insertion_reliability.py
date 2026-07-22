# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic, simulation-only insertion reliability harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from insertion_integrity import (
    DestinationObservation,
    InsertionCoordinator,
    InsertionLease,
    ReadbackResult,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "benchmarks" / "insertion_reliability_cases.json"
DEFAULT_ITERATIONS = 1000
PROFILE_KEYS = frozenset({"id", "target", "paste", "readback"})
CASE_KEYS = frozenset({"id", "profile", "fault", "expect"})
EXPECT_KEYS = frozenset({
    "state", "reason", "paste_attempted", "paste_callbacks", "recoverable",
})
TARGETS = frozenset({"readable", "opaque", "unavailable"})
AVAILABILITY = frozenset({"available", "unavailable"})
FAULTS = frozenset({
    "none", "focus-drift", "duplicate-commit", "selection-drift",
    "surrounding-drift", "relaunch-identity",
    "delayed-readback-duplicate",
})
STATES = frozenset({"verified", "unverifiable", "conflict", "unresolved"})
REASONS = frozenset({
    "commit_verified", "focus_drift", "selection_drift",
    "surrounding_text_drift", "target_unreadable", "readback_unavailable",
    "readback_conflict", "paste_outcome_unknown",
})


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and value[0].isalnum()
            and all(character.islower() or character.isdigit()
                    or character == "-" for character in value))


def load_corpus(path: Path = DEFAULT_CASES) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict)
            or set(payload) != {
                "schema_version", "privacy", "evidence_scope",
                "capability_profiles", "cases",
            }
            or payload.get("schema_version") != 1
            or payload.get("privacy") != "synthetic-payload-only"
            or payload.get("evidence_scope") != "adapter-simulation-only"):
        raise ValueError("unsupported insertion reliability corpus")

    profiles = payload.get("capability_profiles")
    cases = payload.get("cases")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("capability profiles are required")
    if not isinstance(cases, list) or not cases:
        raise ValueError("insertion reliability cases are required")

    profile_ids: set[str] = set()
    for profile in profiles:
        if (not isinstance(profile, dict) or set(profile) != PROFILE_KEYS
                or not _identifier(profile.get("id"))
                or profile.get("target") not in TARGETS
                or profile.get("paste") not in AVAILABILITY
                or profile.get("readback") not in AVAILABILITY
                or profile["id"] in profile_ids):
            raise ValueError("invalid or duplicate capability profile")
        profile_ids.add(profile["id"])

    case_ids: set[str] = set()
    for case in cases:
        expect = case.get("expect") if isinstance(case, dict) else None
        if (not isinstance(case, dict) or set(case) != CASE_KEYS
                or not _identifier(case.get("id"))
                or case.get("id") in case_ids
                or case.get("profile") not in profile_ids
                or case.get("fault") not in FAULTS
                or not isinstance(expect, dict)
                or set(expect) != EXPECT_KEYS
                or expect.get("state") not in STATES
                or expect.get("reason") not in REASONS
                or not isinstance(expect.get("paste_attempted"), bool)
                or isinstance(expect.get("paste_callbacks"), bool)
                or expect.get("paste_callbacks") not in (0, 1)
                or not isinstance(expect.get("recoverable"), bool)):
            raise ValueError("invalid or duplicate insertion reliability case")
        case_ids.add(case["id"])

    return payload


def _lease_and_observation(target: str, fault: str, utterance_id: str):
    destination = "synthetic-app-v1/field"
    surrounding = "synthetic-before|synthetic-after"
    if target == "opaque":
        lease = InsertionLease.capture_opaque(
            utterance_id, destination, "synthetic-composer")
        current = DestinationObservation.capture(
            destination, (0, 0), "synthetic-composer")
    else:
        lease = InsertionLease.capture(
            utterance_id, destination, (1, 0), surrounding)
        current = DestinationObservation.capture(
            destination, (1, 0), surrounding)

    if target == "unavailable":
        current = DestinationObservation.capture(None, None, None)
    elif fault == "focus-drift":
        current = DestinationObservation.capture(
            "synthetic-other-app/field", current.selection, surrounding)
    elif fault == "selection-drift":
        current = DestinationObservation.capture(destination, (2, 0), surrounding)
    elif fault == "surrounding-drift":
        current = DestinationObservation.capture(
            destination, current.selection, "synthetic-user-typed")
    elif fault == "relaunch-identity":
        current = DestinationObservation.capture(
            "synthetic-app-v2/field", current.selection, surrounding)
    return lease, current


def evaluate_case(case: dict[str, Any], profile: dict[str, Any],
                  *, run_number: int = 0) -> dict[str, Any]:
    utterance_id = f"synthetic-{case['id']}-{run_number}"
    lease, current = _lease_and_observation(
        profile["target"], case["fault"], utterance_id)
    coordinator = InsertionCoordinator()
    coordinator.stage(lease, "synthetic payload")
    paste_callbacks = 0
    in_flight_duplicate_blocked = False

    def readback() -> ReadbackResult:
        if profile["readback"] == "unavailable":
            return ReadbackResult.unverifiable()
        return ReadbackResult.verified()

    def paste(_text: str) -> None:
        nonlocal paste_callbacks, in_flight_duplicate_blocked
        paste_callbacks += 1
        if case["fault"] == "delayed-readback-duplicate":
            duplicate = coordinator.commit(
                utterance_id, current,
                lambda _duplicate_text: None,
                ReadbackResult.conflict,
            )
            in_flight_duplicate_blocked = (
                duplicate.paste_attempted
                and duplicate.reason.value == "paste_outcome_unknown")
        if profile["paste"] == "unavailable":
            raise RuntimeError("synthetic clipboard unavailable")

    receipt = coordinator.commit(utterance_id, current, paste, readback)
    if case["fault"] == "duplicate-commit":
        receipt = coordinator.commit(utterance_id, current, paste, readback)

    # Every scenario receives a terminal duplicate callback. This is the core
    # invariant check: a retry must return the receipt without another paste.
    terminal_duplicate = coordinator.commit(
        utterance_id, current, paste, ReadbackResult.conflict)
    recoverable = bool(coordinator.recoverable())
    expected = case["expect"]
    actual = {
        "state": receipt.state.value,
        "reason": receipt.reason.value,
        "paste_attempted": receipt.paste_attempted,
        "paste_callbacks": paste_callbacks,
        "recoverable": recoverable,
    }
    errors = [
        f"{key}: expected {expected[key]!r}, got {actual[key]!r}"
        for key in EXPECT_KEYS if actual[key] != expected[key]
    ]
    terminal_receipt_stable = terminal_duplicate is receipt
    at_most_once = paste_callbacks <= 1
    if not terminal_receipt_stable:
        errors.append("terminal duplicate did not return the same receipt")
    if not at_most_once:
        errors.append("more than one platform paste callback occurred")
    if (case["fault"] == "delayed-readback-duplicate"
            and not in_flight_duplicate_blocked):
        errors.append("in-flight duplicate was not blocked")
    return {
        "id": case["id"],
        "profile": case["profile"],
        "fault": case["fault"],
        "passed": not errors,
        "errors": errors,
        "receipt_state": receipt.state.value,
        "receipt_reason": receipt.reason.value,
        "paste_callbacks": paste_callbacks,
        "terminal_receipt_stable": terminal_receipt_stable,
        "at_most_once": at_most_once,
    }


def build_report(path: Path = DEFAULT_CASES, *, iterations: int = DEFAULT_ITERATIONS
                 ) -> dict[str, Any]:
    if (isinstance(iterations, bool) or not isinstance(iterations, int)
            or not 1 <= iterations <= 100_000):
        raise ValueError("iterations must be between 1 and 100000")
    corpus = load_corpus(path)
    profiles = {
        profile["id"]: profile for profile in corpus["capability_profiles"]}
    public_profiles = {
        profile["id"]: f"profile-{index:03d}"
        for index, profile in enumerate(
            corpus["capability_profiles"], start=1)
    }
    public_cases = {
        case["id"]: f"case-{index:03d}"
        for index, case in enumerate(corpus["cases"], start=1)
    }
    results = [
        evaluate_case(case, profiles[case["profile"]])
        for case in corpus["cases"]
    ]
    for result in results:
        result["id"] = public_cases[result["id"]]
        result["profile"] = public_profiles[result["profile"]]
    invariant_violations = 0
    paste_callbacks = 0
    for run_number in range(iterations):
        for case in corpus["cases"]:
            result = evaluate_case(
                case, profiles[case["profile"]], run_number=run_number + 1)
            invariant_violations += int(
                not result["at_most_once"]
                or not result["terminal_receipt_stable"])
            paste_callbacks += result["paste_callbacks"]
    trials = iterations * len(corpus["cases"])
    return {
        "schema_version": 1,
        "privacy": corpus["privacy"],
        "evidence_scope": corpus["evidence_scope"],
        "physical_evidence": False,
        "real_apps_exercised": 0,
        "fifty_app_claim": False,
        "four_nines_claim": False,
        "capability_profiles": len(profiles),
        "cases": len(results),
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "results": results,
        "attempt_invariant": {
            "simulation_trials": trials,
            "platform_paste_callbacks": paste_callbacks,
            "violations": invariant_violations,
            "passed": invariant_violations == 0,
        },
    }


def render_table(report: dict[str, Any]) -> str:
    invariant = report["attempt_invariant"]
    lines = [
        "INSERTION RELIABILITY SIMULATION",
        f"cases: {report['passed']}/{report['cases']} passed",
        f"synthetic trials: {invariant['simulation_trials']} · "
        f"at-most-once violations: {invariant['violations']}",
        "physical evidence: no · real apps exercised: 0 · four-nines claim: no",
    ]
    for result in report["results"]:
        lines.append(
            f"{'PASS' if result['passed'] else 'FAIL'} {result['id']} · "
            f"{result['receipt_state']} · {result['paste_callbacks']} paste callbacks")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)
    report = build_report(args.cases, iterations=args.iterations)
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        print(render_table(report))
    return 0 if report["failed"] == 0 and report["attempt_invariant"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
