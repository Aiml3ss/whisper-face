# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Offline benchmark for deterministic cleanup proof recovery.

Only checked-in synthetic public text is read.  The emitted report is
aggregate and content-free, and this lab grants no runtime authority.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from benchmark_cleanup_latency import _guard_cleaned_output, _semantic_failure, load_cases
from cleanup_proof_recovery import recover_cleanup_proof


def _guard(case: dict[str, Any]):
    def check(source: str, candidate: str) -> str | None:
        return (_guard_cleaned_output(source, candidate, "stop", "capture")
                or ("semantic-fixture-failed"
                    if _semantic_failure(case, candidate) else None))
    return check


def run_benchmark() -> dict[str, Any]:
    cases = load_cases()
    reasons: dict[str, int] = {}
    recovered = rejected = replay_verified = 0
    total_edits = total_anchors = abandoned_anchors = 0
    for case in cases:
        result = recover_cleanup_proof(
            case["raw"], case["candidate"], output_guard=_guard(case))
        receipt = result.receipt
        reasons[receipt.reason] = reasons.get(receipt.reason, 0) + 1
        recovered += int(receipt.disposition == "recovered")
        rejected += int(receipt.disposition == "rejected")
        replay_verified += int(receipt.replay_verified)
        total_edits += receipt.edit_count
        total_anchors += receipt.anchor_count
        abandoned_anchors += receipt.abandoned_anchor_count
    all_cases_pass = recovered == len(cases) and replay_verified == len(cases)
    return {
        "schema_version": 1,
        "scope": "checked-in-synthetic-cleanup-proof-recovery",
        "privacy": "aggregate-content-free-no-persistence",
        "runtime_authority": "none",
        "cases": len(cases),
        "recovered": recovered,
        "rejected": rejected,
        "replay_verified": replay_verified,
        "edit_count": total_edits,
        "anchor_count": total_anchors,
        "abandoned_anchor_count": abandoned_anchors,
        "reason_counts": dict(sorted(reasons.items())),
        "claim": {
            "all_cases_pass": all_cases_pass,
            "candidate_demonstrably_no_worse": False,
            "runtime_change_recommended": False,
            "reason": ("standalone evidence only; candidate quality was not "
                       "measured against live model output"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    report = run_benchmark()
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"recovered={report['recovered']}/{report['cases']} "
              f"replay_verified={report['replay_verified']}/{report['cases']}")
        print("Runtime authority: none.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
