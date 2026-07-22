# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Opt-in Qwen cleanup latency and standalone proof-recovery comparison.

This lab sends only checked-in synthetic cases to local Ollama, never reads
transcript logs, and emits aggregate, content-free reports. It independently
compares the model-provided edit proof with bounded cleanup proof recovery and
has no runtime authority.

Run: uv run benchmark_cleanup_latency.py --run --format json
"""
from __future__ import annotations

import argparse
import ast
import http.client
import json
import math
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from cleanup_proof_recovery import recover_cleanup_proof
from parrot_core import compile_cleanup
from voice_compiler import EditProposal, VoiceCompiler

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "benchmarks" / "cleanup_latency_cases.json"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
REPORT_SCHEMA_VERSION = 5
CURRENT_TOKEN_BUDGET = "max(160, int(words * 4.0) + 64)"
CURRENT_READ_TIMEOUT = 4.0
MEANINGFUL_LATENCY_IMPROVEMENT = 0.10
RISK_LABELS = frozenset({
    "acronyms", "code", "dates", "false_starts", "fillers", "layout",
    "meaningful_filler", "money", "names", "numbers", "punctuation",
    "urls",
})
def _runtime_contract() -> dict[str, Any]:
    """Load only prompt and guard declarations from current runtime source."""
    wanted = {"OLLAMA_MODEL", "REFUSAL_RE", "BASE_PROMPT",
              "STRUCTURED_FEW_SHOT", "TONE", "MODE_INSTRUCTIONS",
              "STRUCTURED_OUTPUT", "_guard_cleaned_output"}
    tree = ast.parse((HERE / "dictate.py").read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        names = {target.id for target in getattr(node, "targets", ())
                 if isinstance(target, ast.Name)}
        if names & wanted or isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)
    namespace: dict[str, Any] = {"json": json, "re": re}
    exec(compile(ast.Module(nodes, type_ignores=[]), "dictate.py", "exec"), namespace)
    if not wanted <= namespace.keys():
        raise RuntimeError("could not load current cleanup prompt contract")
    return namespace


RUNTIME = _runtime_contract()
MODEL = RUNTIME["OLLAMA_MODEL"]
BASE_PROMPT = RUNTIME["BASE_PROMPT"]
STRUCTURED_FEW_SHOT = RUNTIME["STRUCTURED_FEW_SHOT"]
TONE = RUNTIME["TONE"]
MODE_INSTRUCTIONS = RUNTIME["MODE_INSTRUCTIONS"]
STRUCTURED_OUTPUT = RUNTIME["STRUCTURED_OUTPUT"]
_guard_cleaned_output = RUNTIME["_guard_cleaned_output"]


class TransportTimeout(Exception):
    """The local Ollama read deadline elapsed."""


class _UrlResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def local_post(url: str, *, json: dict[str, Any], timeout: tuple[float, float]) -> _UrlResponse:
    request = urllib.request.Request(url, data=__import__("json").dumps(json).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout[1]) as response:
            if response.status >= 400:
                raise urllib.error.HTTPError(url, response.status, "Ollama error", response.headers, None)
            return _UrlResponse(__import__("json").loads(response.read()))
    except (socket.timeout, TimeoutError) as error:
        raise TransportTimeout() from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise TransportTimeout() from error
        raise


def _system(prompt: str) -> str:
    return "\n".join((prompt, TONE["default"], MODE_INSTRUCTIONS["capture"],
                      STRUCTURED_OUTPUT))


VARIANTS = (
    {"id": "current", "system": _system(BASE_PROMPT),
     "few_shot": STRUCTURED_FEW_SHOT,
     "budget": lambda words: max(160, int(words * 4.0) + 64),
     "budget_description": CURRENT_TOKEN_BUDGET,
     "read_timeout": CURRENT_READ_TIMEOUT},
    {"id": "bounded-128-3.5s", "system": _system(BASE_PROMPT),
     "few_shot": STRUCTURED_FEW_SHOT, "budget": lambda _words: 128,
     "budget_description": "128", "read_timeout": 3.5},
    {"id": "three-shot-current-budget", "system": _system(BASE_PROMPT),
     "few_shot": STRUCTURED_FEW_SHOT[:6],
     "budget": lambda words: max(160, int(words * 4.0) + 64),
     "budget_description": CURRENT_TOKEN_BUDGET,
     "read_timeout": CURRENT_READ_TIMEOUT},
)


def load_cases(path: Path = DEFAULT_CASES) -> tuple[dict[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get("schema_version") != 2
            or payload.get("privacy") != "checked-in-synthetic-public-text-only"
            or not isinstance(cases, list) or not cases):
        raise ValueError("unsupported cleanup latency corpus")
    seen: set[str] = set()
    for case in cases:
        risks = case.get("risks") if isinstance(case, dict) else None
        if (not isinstance(case, dict) or set(case) != {"id", "risks", "raw", "candidate", "must_contain", "must_not_contain"}
                or not isinstance(case["id"], str) or not case["id"]
                or not isinstance(risks, list) or not risks or len(risks) > 4
                or len(risks) != len(set(risks))
                or any(not isinstance(item, str) or item not in RISK_LABELS
                       for item in risks)
                or not isinstance(case["raw"], str) or not case["raw"] or len(case["raw"]) > 1000
                or not isinstance(case["candidate"], str) or not case["candidate"]
                or len(case["candidate"]) > 1000
                or not all(isinstance(item, str) for item in case["must_contain"])
                or not all(isinstance(item, str) for item in case["must_not_contain"])
                or case["id"] in seen):
            raise ValueError("invalid or duplicate cleanup latency case")
        seen.add(case["id"])
    return tuple(cases)


def _payload(variant: dict[str, Any], raw: str) -> dict[str, Any]:
    return {"model": MODEL,
            "messages": ([{"role": "system", "content": variant["system"]}]
                         + list(variant["few_shot"]) + [{"role": "user", "content": raw}]),
            "stream": False, "think": False, "format": "json", "keep_alive": -1,
            "options": {"temperature": 0, "repeat_penalty": 1.0,
                        "num_predict": variant["budget"](len(raw.split()))}}


def _semantic_failure(case: dict[str, Any], output: str) -> bool:
    output = output.casefold()
    return (any(item.casefold() not in output for item in case["must_contain"])
            or any(item.casefold() in output for item in case["must_not_contain"]))


def _proof_matches(raw: str, payload: Any, output: str) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("edits"), list):
        return False
    proposals = []
    for edit in payload["edits"][:12]:
        if not isinstance(edit, dict):
            return False
        proposals.append(EditProposal(str(edit.get("kind", "semantic_cleanup")),
                                      str(edit.get("before", ""))[:200], str(edit.get("after", ""))[:200]))
    return VoiceCompiler().verify_edits(raw, proposals).text == output


def _recovery_guard(case: dict[str, Any], done_reason: str) \
        -> Callable[[str, str], str | None]:
    """Reapply both independent candidate gates inside the mediator."""
    def guard(source: str, candidate: str) -> str | None:
        return (_guard_cleaned_output(
            source, candidate, done_reason, "capture")
                or ("semantic-fixture-failed"
                    if _semantic_failure(case, candidate) else None))
    return guard


def _percentile(samples: list[float], point: float) -> float | None:
    return sorted(samples)[math.ceil((len(samples) - 1) * point)] if samples else None


def _empty_risk_counts() -> dict[str, int]:
    return {key: 0 for key in (
        "cases", "baseline_accepted", "recovered_accepted",
        "both_accepted", "baseline_only_accepted",
        "recovered_only_accepted", "neither_accepted",
        "guard_rejected", "semantic_failed", "proof_failed",
        "parse_failed", "timeout", "transport_failed",
    )}


def run_variant(variant: dict[str, Any], cases: tuple[dict[str, Any], ...], *,
                post: Callable[..., Any] = local_post,
                clock: Callable[[], float] = time.perf_counter,
                read_timeout: float | None = None) -> dict[str, Any]:
    deadline = variant["read_timeout"] if read_timeout is None \
        else min(read_timeout, variant["read_timeout"])
    counts = {key: 0 for key in (
        "accepted", "baseline_accepted", "recovered_accepted",
        "both_accepted", "baseline_only_accepted",
        "recovered_only_accepted", "neither_accepted",
        "model_candidates_evaluated", "recovery_attempted",
        "recovery_rejected", "recovery_replay_verified",
        "guard_rejected", "semantic_failed", "proof_failed",
        "parse_failed", "timeout", "transport_failed",
    )}
    recovery_reasons: dict[str, int] = {}
    risk_counts = {
        risk: _empty_risk_counts()
        for risk in sorted({risk for case in cases for risk in case["risks"]})
    }
    latencies: list[float] = []
    recovery_latencies: list[float] = []
    case_outcomes: list[str] = []
    for case in cases:
        started = clock()
        baseline_accepted = recovered_accepted = False
        failure: str | None = None
        proof_failed = False
        try:
            response = post(OLLAMA_CHAT_URL, json=_payload(variant, case["raw"]), timeout=(1, deadline))
            response.raise_for_status()
            wire = response.json()
            answer = re.sub(r"<think>.*?</think>", "", wire["message"]["content"], flags=re.S).strip()
            parsed = json.loads(answer)
            output = str(parsed.get("text", "")).strip().strip('"').strip()
            done_reason = str(wire.get("done_reason", "stop"))
            counts["model_candidates_evaluated"] += 1
            reason = _guard_cleaned_output(
                case["raw"], output, done_reason, "capture")
            if reason:
                counts["guard_rejected"] += 1
                failure = "guard_rejected"
            elif _semantic_failure(case, output):
                counts["semantic_failed"] += 1
                failure = "semantic_failed"
            else:
                baseline_accepted = _proof_matches(
                    case["raw"], parsed, output)
                if baseline_accepted:
                    counts["accepted"] += 1
                    counts["baseline_accepted"] += 1
                else:
                    counts["proof_failed"] += 1
                    proof_failed = True

                recovery_started = clock()
                recovery = recover_cleanup_proof(
                    case["raw"], output,
                    output_guard=_recovery_guard(case, done_reason))
                recovery_latencies.append(
                    (clock() - recovery_started) * 1000.0)
                counts["recovery_attempted"] += 1
                receipt = recovery.receipt
                recovery_reasons[receipt.reason] = (
                    recovery_reasons.get(receipt.reason, 0) + 1)
                recovered_accepted = (
                    receipt.disposition in {"recovered", "no-effect"}
                    and receipt.replay_verified
                    and recovery.text == output)
                if recovered_accepted:
                    counts["recovered_accepted"] += 1
                else:
                    counts["recovery_rejected"] += 1
                counts["recovery_replay_verified"] += int(
                    receipt.replay_verified)
        except TransportTimeout:
            counts["timeout"] += 1
            failure = "timeout"
        except (urllib.error.URLError, urllib.error.HTTPError,
                http.client.HTTPException, ConnectionError):
            counts["transport_failed"] += 1
            failure = "transport_failed"
        except (AttributeError, KeyError, TypeError, ValueError):
            counts["parse_failed"] += 1
            failure = "parse_failed"
        finally:
            if baseline_accepted and recovered_accepted:
                overlap = "both_accepted"
            elif baseline_accepted:
                overlap = "baseline_only_accepted"
            elif recovered_accepted:
                overlap = "recovered_only_accepted"
            else:
                overlap = "neither_accepted"
            counts[overlap] += 1
            case_outcomes.append(failure or overlap)
            for risk in case["risks"]:
                aggregate = risk_counts[risk]
                aggregate["cases"] += 1
                aggregate["baseline_accepted"] += int(baseline_accepted)
                aggregate["recovered_accepted"] += int(recovered_accepted)
                aggregate[overlap] += 1
                aggregate["proof_failed"] += int(proof_failed)
                if failure is not None:
                    aggregate[failure] += 1
            latencies.append((clock() - started) * 1000.0)
    risk_results = []
    for risk, aggregate in risk_counts.items():
        risk_results.append({
            "risk": risk,
            **aggregate,
            "acceptance_delta": (
                aggregate["recovered_accepted"]
                - aggregate["baseline_accepted"]),
        })
    return {"id": variant["id"], "few_shot_pairs": len(variant["few_shot"]) // 2,
            "token_budget": variant["budget_description"], "cases": len(cases), **counts,
            "read_timeout_seconds": deadline,
            "acceptance_delta": (
                counts["recovered_accepted"]
                - counts["baseline_accepted"]),
            "risk_results": risk_results,
            "recovery_reason_counts": dict(sorted(recovery_reasons.items())),
            "recovery_latency_ms": {
                "p50": _percentile(recovery_latencies, .50),
                "p95": _percentile(recovery_latencies, .95),
                "max": max(recovery_latencies) if recovery_latencies else None,
            },
            "latency_ms": {"p50": _percentile(latencies, .50), "p95": _percentile(latencies, .95),
                           "max": max(latencies) if latencies else None},
            "_case_outcomes": tuple(case_outcomes)}


def _variant_comparison(
        baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_outcomes = baseline["_case_outcomes"]
    candidate_outcomes = candidate["_case_outcomes"]
    if len(baseline_outcomes) != len(candidate_outcomes):
        raise ValueError("variant outcome count mismatch")
    baseline_losses = sum(
        before in {"both_accepted", "baseline_only_accepted"}
        and after not in {"both_accepted", "baseline_only_accepted"}
        for before, after in zip(baseline_outcomes, candidate_outcomes))
    new_semantic_failures = sum(
        before != "semantic_failed" and after == "semantic_failed"
        for before, after in zip(baseline_outcomes, candidate_outcomes))
    unavailable = {"guard_rejected", "parse_failed", "timeout",
                   "transport_failed"}
    new_unavailable = sum(
        before not in unavailable and after in unavailable
        for before, after in zip(baseline_outcomes, candidate_outcomes))
    p95_improvement = (
        baseline["latency_ms"]["p95"] - candidate["latency_ms"]["p95"])
    max_improvement = (
        baseline["latency_ms"]["max"] - candidate["latency_ms"]["max"])
    p95_fraction = p95_improvement / baseline["latency_ms"]["p95"]
    max_fraction = max_improvement / baseline["latency_ms"]["max"]
    eligible = (
        baseline_losses == 0
        and new_semantic_failures == 0
        and new_unavailable == 0
        and p95_fraction >= MEANINGFUL_LATENCY_IMPROVEMENT
        and max_fraction >= MEANINGFUL_LATENCY_IMPROVEMENT)
    return {
        "id": candidate["id"],
        "baseline_losses": baseline_losses,
        "new_semantic_failures": new_semantic_failures,
        "new_unavailable_failures": new_unavailable,
        "p95_improvement_ms": p95_improvement,
        "p95_improvement_fraction": p95_fraction,
        "max_improvement_ms": max_improvement,
        "max_improvement_fraction": max_fraction,
        "runtime_change_eligible": eligible,
    }


def build_report(cases: tuple[dict[str, Any], ...], results: list[dict[str, Any]], *, read_timeout: float) -> dict[str, Any]:
    baseline = next(result for result in results if result["id"] == "current")
    safe = lambda result: all(result[key] == 0 for key in (
        "guard_rejected", "semantic_failed", "proof_failed",
        "parse_failed", "timeout", "transport_failed",
    ))
    faster_safe = [result["id"] for result in results if result["id"] != "current" and safe(result) and safe(baseline) and result["latency_ms"]["p95"] < baseline["latency_ms"]["p95"]]
    qwen_routed = [case for case in cases
                   if compile_cleanup(case["raw"]).needs_semantic_cleanup]
    qwen_risks = {
        risk: sum(risk in case["risks"] for case in qwen_routed)
        for risk in sorted({risk for case in qwen_routed
                            for risk in case["risks"]})
    }
    comparisons = [
        _variant_comparison(baseline, result)
        for result in results if result["id"] != "current"]
    public_results = [
        {key: value for key, value in result.items()
         if key != "_case_outcomes"}
        for result in results]
    return {"schema_version": REPORT_SCHEMA_VERSION, "scope": "opt-in-local-synthetic-cleanup-prompt-lab",
            "privacy": "checked-in-synthetic-only-aggregate-report", "runtime_authority": "none", "model": MODEL,
            "cases": len(cases), "read_timeout_cap_seconds": read_timeout,
            "results": public_results, "variant_comparisons": comparisons,
            "deterministic_routing": {
                "fast_path_cases": len(cases) - len(qwen_routed),
                "qwen_routed_cases": len(qwen_routed),
                "qwen_routed_risk_counts": qwen_risks,
            },
            "claim": {"faster_safe_variants": faster_safe, "runtime_change_recommended": False,
                      "reason": "lab evidence only; runtime prompt remains unchanged"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="send synthetic cases to local Ollama")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--read-timeout", type=float, default=4.0)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("refusing local-server activity without --run")
    if not .1 <= args.read_timeout <= 120:
        parser.error("--read-timeout must be between 0.1 and 120 seconds")
    cases = load_cases(args.cases)
    report = build_report(cases, [run_variant(item, cases, read_timeout=args.read_timeout) for item in VARIANTS], read_timeout=args.read_timeout)
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        for result in report["results"]:
            print(f"{result['id']}: baseline={result['baseline_accepted']}/{result['cases']} "
                  f"recovered={result['recovered_accepted']}/{result['cases']} "
                  f"p50={result['latency_ms']['p50']:.0f}ms "
                  f"p95={result['latency_ms']['p95']:.0f}ms")
        print("Runtime prompt unchanged: lab has no runtime authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
