"""Aggregate checked-in synthetic evidence into a public JSON scorecard, and
publish a dated report that keeps synthetic and physical evidence apart.

The synthetic report is deliberately aggregate-only.  It contains no
transcripts, case identifiers, target identifiers, paths, timings, or model
output, and it never claims physical validation.  Existing benchmark functions
remain the source of truth for every measured result.

The publication path adds physical evidence only from artifacts that a capture
harness or activation benchmark actually produced, and only when the publisher
names the hardware and software those artifacts were produced on.  Synthetic
suites are built by functions that have no parameter capable of marking them
physical, physical sources are built by a function that cannot run without a
named environment, and a final invariant re-checks the finished document before
any renderer may see it.  Presenting a synthetic number as a physical one is a
structural impossibility rather than a review convention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmark_consequence_routing import (
    DEFAULT_CASES as CONSEQUENCE_CASES,
    evaluate_case as evaluate_consequence_case,
    load_cases as load_consequence_cases,
)
from benchmark_insertion_reliability import (
    DEFAULT_CASES as INSERTION_CASES,
    build_report as build_insertion_report,
)
from benchmark_voice_compiler import (
    DEFAULT_CASES as COMPILER_CASES,
    evaluate_golden_cases,
    load_cases as load_compiler_cases,
)
from drop_to_target import measure_synthetic_corpus as measure_drop_corpus
from point_and_speak_resolver import (
    measure_synthetic_corpus as measure_point_corpus,
)


SCHEMA_VERSION = 1
REPORT_KIND = "whisper-face/public-synthetic-scorecard"
EVIDENCE_SCOPE = "checked-in-synthetic-corpora-only"
PRIVACY = "transcript-free-aggregate-only"
HERE = Path(__file__).resolve().parent
POINT_CASES = HERE / "benchmarks" / "point_and_speak_cases.json"
DROP_CASES = HERE / "benchmarks" / "drop_to_target_cases.json"

PUBLICATION_SCHEMA_VERSION = 1
PUBLICATION_KIND = "whisper-face/public-evidence-report"

# Every synthetic suite must name the exact command and checked-in corpus a
# reader needs to recompute its numbers from a bare clone.  A suite without an
# entry here cannot be published; the test suite enforces total coverage.
SUITE_REPRODUCTION: dict[str, dict[str, str]] = {
    "voice_compiler": {
        "command": "uv run benchmark_voice_compiler.py",
        "corpus": "benchmarks/voice_compiler_cases.json",
    },
    "consequence_routing": {
        "command": "uv run benchmark_consequence_routing.py",
        "corpus": "benchmarks/consequence_routing_cases.json",
    },
    "insertion_reliability": {
        "command": "uv run benchmark_insertion_reliability.py",
        "corpus": "benchmarks/insertion_reliability_cases.json",
    },
    "point_and_speak": {
        "command": "uv run public_scorecard.py",
        "corpus": "benchmarks/point_and_speak_cases.json",
    },
    "drop_to_target": {
        "command": "uv run public_scorecard.py",
        "corpus": "benchmarks/drop_to_target_cases.json",
    },
}


class EvidenceClass(str, Enum):
    """The only two evidence classes this report is allowed to publish."""

    SYNTHETIC = "synthetic"
    PHYSICAL = "physical"


class PublicationError(ValueError):
    """Raised when a document would blur synthetic and physical evidence."""


_MISSING = object()

# A physical environment description must be concrete.  These tokens are the
# usual way an unrecorded machine gets published as if it were a real one.
_PLACEHOLDER_TOKENS = frozenset({
    "", "-", "--", "?", "??", "n/a", "na", "none", "null", "nil", "tbd",
    "todo", "unknown", "unrecorded", "unspecified", "example", "sample",
    "placeholder", "test", "fake", "dummy", "xxx", "foo", "bar", "changeme",
})
_REVISION = re.compile(r"\A[0-9a-f]{40}\Z")
_DATE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_ENVIRONMENT_ID = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,63}\Z")
_ENVIRONMENT_KEYS = frozenset({
    "schema_version", "environment_id", "hardware", "os_name", "os_version",
    "whisper_face_revision", "python_version", "software",
})
_SOFTWARE_KEYS = frozenset({"name", "version"})
_MAX_TEXT = 120
_MAX_SOFTWARE = 32


@dataclass(frozen=True)
class PhysicalEvidenceKind:
    """One artifact shape this repository knows how to publish as physical.

    Every field is a declaration about the *producer*, not about the file in
    hand.  A candidate artifact is published only when it matches all of them,
    so a hand-edited or synthetic file cannot borrow a physical identity.
    """

    kind_id: str
    identity_path: str
    producer_command: str
    scope_path: str | None
    allowed_scopes: frozenset[str]
    required_values: tuple[tuple[str, Any], ...]
    required_zero: tuple[str, ...]
    volume_path: str
    volume_label: str
    published_counters: tuple[tuple[str, str], ...]
    physical_flag_path: str | None


# Artifacts a capture harness or activation benchmark actually writes today.
# Adding a kind is a reviewed data change; nothing here is discovered from the
# candidate file itself.
PHYSICAL_EVIDENCE_KINDS: tuple[PhysicalEvidenceKind, ...] = (
    PhysicalEvidenceKind(
        kind_id="physical-app-insertion-matrix",
        identity_path="artifact",
        producer_command=(
            "uv run scripts/capture_app_matrix.py emit --out <artifact>"),
        scope_path="evidence_scope",
        allowed_scopes=frozenset({
            "operator-attested-physical-session",
            "runtime-observed-passive-use",
            "mixed-operator-attested-and-runtime-observed",
        }),
        required_values=(
            ("privacy", "transcript-free"),
            ("physical_evidence", True),
            ("coverage.extrapolated", False),
            ("claims.four_nines_claim", False),
        ),
        required_zero=(),
        volume_path="claims.real_apps_exercised",
        volume_label="real_apps_exercised",
        published_counters=(
            ("real_apps_exercised", "claims.real_apps_exercised"),
            ("apps_planned", "coverage.apps_planned"),
            ("apps_recorded", "coverage.apps_recorded"),
            ("apps_blocked", "coverage.apps_blocked"),
            ("operator_attested_cases", "operator_observations.attested_cases"),
            ("machine_observed_cases", "machine_observed.cases"),
            ("operator_runtime_agreements", "agreement.both"),
            ("operator_runtime_disagreements", "agreement.disagreements"),
        ),
        physical_flag_path="physical_evidence",
    ),
    PhysicalEvidenceKind(
        kind_id="physical-lifecycle-evidence",
        identity_path="artifact",
        producer_command=(
            "uv run scripts/capture_lifecycle_evidence.py emit "
            "--out <artifact>"),
        scope_path="evidence_scope",
        allowed_scopes=frozenset({"operator-attested-physical-session"}),
        required_values=(
            ("privacy", "transcript-free"),
            ("physical_evidence", True),
            ("coverage.extrapolated", False),
            ("discharges_physical_validation_basis",
             "operator-attested-runs-only"),
        ),
        required_zero=(),
        volume_path="coverage.runs_recorded",
        volume_label="runs_recorded",
        published_counters=(
            ("runs_planned", "coverage.runs_planned"),
            ("runs_recorded", "coverage.runs_recorded"),
            ("runs_blocked", "coverage.runs_blocked"),
        ),
        physical_flag_path="physical_evidence",
    ),
    PhysicalEvidenceKind(
        kind_id="physical-delayed-cleanup-coverage",
        identity_path="artifact",
        producer_command=(
            "uv run scripts/capture_delayed_cleanup_cases.py summary"),
        scope_path="evidence_scope",
        # This artifact carries no physical_evidence boolean of its own, so the
        # scope literal and a non-zero recorded-case count are the only proof
        # available.  Both are required rather than either.
        allowed_scopes=frozenset({"operator-attested-physical-session"}),
        required_values=(
            ("privacy", "transcript-free"),
            ("receipt_written_by_this_tool", False),
            ("manual_review_flag_set_by_this_tool", False),
        ),
        required_zero=(),
        volume_path="cases_recorded",
        volume_label="cases_recorded",
        published_counters=(
            ("cases_planned", "cases_planned"),
            ("cases_recorded", "cases_recorded"),
            ("cases_blocked", "cases_blocked"),
            ("applied", "applied_count"),
            ("rejected", "rejected_count"),
        ),
        physical_flag_path=None,
    ),
    PhysicalEvidenceKind(
        kind_id="whisper-face/relisten-activation-report",
        identity_path="report_kind",
        producer_command=(
            "uv run benchmark_relisten_activation.py <manifest>"),
        scope_path="evidence_scope",
        allowed_scopes=frozenset({"explicit-local-wav-manifest"}),
        required_values=(
            ("activation_evidence.activation_claim", False),
        ),
        # A re-listen report that counted even one synthetic sample is not
        # physical evidence and must never be published as though it were.
        required_zero=("evidence_counts.synthetic-test",),
        volume_path="activation_evidence.real_samples",
        volume_label="real_samples",
        published_counters=(
            ("real_samples", "activation_evidence.real_samples"),
            ("real_confirmed_cases",
             "activation_evidence.real_confirmed_cases"),
            ("real_contradicted_cases",
             "activation_evidence.real_contradicted_cases"),
        ),
        physical_flag_path=None,
    ),
    PhysicalEvidenceKind(
        kind_id="whisper-face/acoustic-calibration-activation-report",
        identity_path="kind",
        producer_command=(
            "uv run benchmark_acoustic_calibration_activation.py <manifest>"),
        scope_path=None,
        allowed_scopes=frozenset(),
        required_values=(
            ("privacy", "aggregate-categorical-and-numeric-only"),
            ("activation_claim", False),
            ("quality_claim", False),
        ),
        required_zero=(
            "evidence.recognition_regressions",
            "evidence.endpoint_regressions",
        ),
        volume_path="evidence.physical_cases",
        volume_label="physical_cases",
        published_counters=(
            ("physical_cases", "evidence.physical_cases"),
            ("recognition_improvements", "evidence.recognition_improvements"),
            ("endpoint_improvements", "evidence.endpoint_improvements"),
        ),
        physical_flag_path=None,
    ),
)

_KINDS_BY_ID = {kind.kind_id: kind for kind in PHYSICAL_EVIDENCE_KINDS}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("synthetic corpus must be a JSON object")
    return value


def _count(value: Any, label: str) -> int:
    if (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _suite(
    suite_id: str,
    evidence_scope: str,
    *,
    cases: Any,
    passed: Any,
    critical_metric: str,
    critical_failures: Any,
) -> dict[str, Any]:
    case_count = _count(cases, f"{suite_id} cases")
    passed_count = _count(passed, f"{suite_id} passed")
    failures = _count(critical_failures, f"{suite_id} critical failures")
    if passed_count > case_count:
        raise ValueError(f"{suite_id} passed count exceeds case count")
    return {
        "suite_id": suite_id,
        "evidence_scope": evidence_scope,
        "physical_validation": False,
        "cases": case_count,
        "passed": passed_count,
        "failed": case_count - passed_count,
        "critical_metric": critical_metric,
        "critical_failures": failures,
    }


def _compiler_suite() -> dict[str, Any]:
    report = evaluate_golden_cases(load_compiler_cases(COMPILER_CASES))
    return _suite(
        "voice_compiler",
        "checked-in-golden-corpus",
        cases=report["total"],
        passed=report["passed"],
        critical_metric="case_expectation_failures",
        critical_failures=report["total"] - report["passed"],
    )


def _consequence_suite() -> dict[str, Any]:
    results = [
        evaluate_consequence_case(case)
        for case in load_consequence_cases(CONSEQUENCE_CASES)
    ]
    passed = sum(result["passed"] is True for result in results)
    return _suite(
        "consequence_routing",
        "synthetic-selector-only",
        cases=len(results),
        passed=passed,
        critical_metric="case_expectation_failures",
        critical_failures=len(results) - passed,
    )


def _insertion_suite() -> dict[str, Any]:
    report = build_insertion_report(INSERTION_CASES, iterations=1)
    if (report.get("evidence_scope") != "adapter-simulation-only"
            or report.get("physical_evidence") is not False
            or report.get("real_apps_exercised") != 0):
        raise ValueError("insertion report exceeded synthetic evidence scope")
    invariant = report.get("attempt_invariant")
    if not isinstance(invariant, dict):
        raise ValueError("insertion report omitted attempt invariant")
    return _suite(
        "insertion_reliability",
        report["evidence_scope"],
        cases=report["cases"],
        passed=report["passed"],
        critical_metric="at_most_once_invariant_violations",
        critical_failures=invariant.get("violations"),
    )


def _target_suite(
    suite_id: str,
    report: dict[str, Any],
    expected_scope: str,
) -> dict[str, Any]:
    if (report.get("evidence_scope") != expected_scope
            or report.get("physical_validation") is not False):
        raise ValueError(f"{suite_id} exceeded synthetic evidence scope")
    return _suite(
        suite_id,
        report["evidence_scope"],
        cases=report["cases"],
        passed=report["correct_outcomes"],
        critical_metric="wrong_target_resolutions",
        critical_failures=report["wrong_target_resolutions"],
    )


def build_public_scorecard() -> dict[str, Any]:
    """Build a deterministic, transcript-free report from fixed local corpora."""
    point = measure_point_corpus(_load_json(POINT_CASES))
    drop = measure_drop_corpus(_load_json(DROP_CASES))
    suites = (
        _compiler_suite(),
        _consequence_suite(),
        _insertion_suite(),
        _target_suite(
            "point_and_speak", point, "synthetic-resolution-only"),
        _target_suite(
            "drop_to_target", drop, "synthetic-decision-only"),
    )
    total_cases = sum(suite["cases"] for suite in suites)
    total_passed = sum(suite["passed"] for suite in suites)
    critical_failures = sum(
        suite["critical_failures"] for suite in suites)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "privacy": PRIVACY,
        "evidence_scope": EVIDENCE_SCOPE,
        "physical_validation": False,
        "real_apps_exercised": 0,
        "audio_or_model_runs": False,
        "suites": list(suites),
        "totals": {
            "suites": len(suites),
            "cases": total_cases,
            "passed": total_passed,
            "failed": total_cases - total_passed,
            "critical_failures": critical_failures,
            "all_passed": (
                total_passed == total_cases and critical_failures == 0),
        },
    }


def render_json(report: dict[str, Any] | None = None) -> str:
    """Render stable machine-readable JSON without adding environment data."""
    return json.dumps(
        report if report is not None else build_public_scorecard(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _at(payload: Any, path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _concrete_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PublicationError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > _MAX_TEXT:
        raise PublicationError(f"{label} must be 1 to {_MAX_TEXT} characters")
    if any(ord(character) < 32 for character in text):
        raise PublicationError(f"{label} must not contain control characters")
    if text.casefold() in _PLACEHOLDER_TOKENS:
        raise PublicationError(
            f"{label} is a placeholder; name the real value or publish nothing")
    return text


def canonical_digest(payload: Any) -> str:
    """Hash a decoded artifact so a reader can confirm they hold the same one."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_environment(value: Any) -> dict[str, Any]:
    """Require a concretely named machine before any physical claim is made."""
    if not isinstance(value, Mapping) or set(value) != _ENVIRONMENT_KEYS:
        raise PublicationError(
            "environment must declare exactly "
            + ", ".join(sorted(_ENVIRONMENT_KEYS)))
    if value["schema_version"] != PUBLICATION_SCHEMA_VERSION or isinstance(
            value["schema_version"], bool):
        raise PublicationError("unsupported environment schema")
    environment_id = value["environment_id"]
    if (not isinstance(environment_id, str)
            or not _ENVIRONMENT_ID.match(environment_id)
            or environment_id.casefold() in _PLACEHOLDER_TOKENS):
        raise PublicationError("invalid environment identifier")
    revision = value["whisper_face_revision"]
    if not isinstance(revision, str) or not _REVISION.match(revision):
        raise PublicationError(
            "whisper_face_revision must be a full 40-character commit id")
    fields = {
        field: _concrete_text(value[field], field)
        for field in ("hardware", "os_name", "os_version", "python_version")
    }
    software = value["software"]
    if not isinstance(software, list) or len(software) > _MAX_SOFTWARE:
        raise PublicationError("software must be a bounded list")
    entries = []
    for item in software:
        if not isinstance(item, Mapping) or set(item) != _SOFTWARE_KEYS:
            raise PublicationError("software entries need name and version")
        entries.append({
            "name": _concrete_text(item["name"], "software name"),
            "version": _concrete_text(item["version"], "software version"),
        })
    names = [entry["name"] for entry in entries]
    if len(set(names)) != len(names):
        raise PublicationError("software names must be unique")
    return {
        "environment_id": environment_id,
        "hardware": fields["hardware"],
        "os_name": fields["os_name"],
        "os_version": fields["os_version"],
        "python_version": fields["python_version"],
        "whisper_face_revision": revision,
        "software": sorted(entries, key=lambda entry: entry["name"]),
    }


def classify_physical_artifact(artifact: Any) -> PhysicalEvidenceKind:
    """Resolve a candidate artifact to a registered physical producer.

    The artifact never chooses its own class: it must match a registered
    identity, declare the scope that producer stamps, satisfy every honesty
    field that producer sets, and prove a non-zero amount of physical work.
    """
    if not isinstance(artifact, Mapping):
        raise PublicationError("physical artifact must be a JSON object")
    matches = [
        kind for kind in PHYSICAL_EVIDENCE_KINDS
        if _at(artifact, kind.identity_path) == kind.kind_id
    ]
    if len(matches) != 1:
        raise PublicationError(
            "artifact does not match exactly one registered physical "
            "evidence kind")
    kind = matches[0]
    if kind.scope_path is not None:
        scope = _at(artifact, kind.scope_path)
        if scope not in kind.allowed_scopes:
            raise PublicationError(
                f"{kind.kind_id}: evidence scope is not a physical scope")
    for path, expected in kind.required_values:
        observed = _at(artifact, path)
        if observed is _MISSING or observed != expected or (
                isinstance(expected, bool) != isinstance(observed, bool)):
            raise PublicationError(
                f"{kind.kind_id}: {path} must be {expected!r}")
    for path in kind.required_zero:
        observed = _integer(_at(artifact, path))
        if observed != 0:
            raise PublicationError(
                f"{kind.kind_id}: {path} must be exactly 0 to publish this "
                "artifact as physical evidence")
    if kind.physical_flag_path is not None:
        flag = _at(artifact, kind.physical_flag_path)
        if flag is not True:
            raise PublicationError(
                f"{kind.kind_id}: producer did not stamp physical evidence")
    volume = _integer(_at(artifact, kind.volume_path))
    if not volume:
        raise PublicationError(
            f"{kind.kind_id}: {kind.volume_label} is zero, so nothing "
            "physical happened")
    return kind


def _physical_source(
    artifact: Mapping[str, Any],
    environment: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the only kind of entry allowed under the physical section."""
    kind = classify_physical_artifact(artifact)
    counters: dict[str, int] = {}
    for label, path in kind.published_counters:
        observed = _integer(_at(artifact, path))
        if observed is None:
            raise PublicationError(
                f"{kind.kind_id}: {label} is missing or not a count")
        counters[label] = observed
    if kind.volume_label not in counters:
        raise PublicationError(
            f"{kind.kind_id}: the volume metric must also be published")
    generated = _at(artifact, "generated_utc")
    return {
        "evidence_class": EvidenceClass.PHYSICAL.value,
        "physical_validation": True,
        "kind_id": kind.kind_id,
        "environment_id": environment["environment_id"],
        "artifact_sha256": canonical_digest(artifact),
        "generated_utc": generated if isinstance(generated, str) else None,
        "volume_metric": kind.volume_label,
        "volume": counters[kind.volume_label],
        "counters": dict(sorted(counters.items())),
        "reproduction": {
            "producer_command": kind.producer_command,
            "requires_named_hardware": True,
            "hardware": environment["hardware"],
        },
    }


def _synthetic_section() -> dict[str, Any]:
    """Build the synthetic section; nothing here can be marked physical."""
    report = build_public_scorecard()
    suites = []
    for suite in report["suites"]:
        reproduction = SUITE_REPRODUCTION.get(suite["suite_id"])
        if reproduction is None:
            raise PublicationError(
                f"{suite['suite_id']}: no reproduction command is published")
        suites.append({
            "evidence_class": EvidenceClass.SYNTHETIC.value,
            "physical_validation": False,
            "suite_id": suite["suite_id"],
            "evidence_scope": suite["evidence_scope"],
            "cases": suite["cases"],
            "passed": suite["passed"],
            "failed": suite["failed"],
            "critical_metric": suite["critical_metric"],
            "critical_failures": suite["critical_failures"],
            "reproduction": {
                "command": reproduction["command"],
                "corpus": reproduction["corpus"],
                "requires_named_hardware": False,
            },
        })
    return {
        "evidence_class": EvidenceClass.SYNTHETIC.value,
        "physical_validation": False,
        "evidence_scope": report["evidence_scope"],
        "real_apps_exercised": 0,
        "audio_or_model_runs": False,
        "interpretation": (
            "These suites are deterministic checks on checked-in synthetic "
            "corpora. They catch regressions. They are not evidence about "
            "real-world accuracy, latency, or reliability, and a passing "
            "case here is never a physical result."),
        "suites": suites,
        "totals": dict(report["totals"]),
    }


def _physical_section(
    artifacts: Sequence[Mapping[str, Any]],
    environments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    known = {
        environment["environment_id"]: environment
        for environment in environments
    }
    if len(known) != len(environments):
        raise PublicationError("environment identifiers must be unique")
    if artifacts and not known:
        raise PublicationError(
            "physical artifacts require at least one named environment")
    sources = []
    for artifact in artifacts:
        if len(known) == 1:
            environment = next(iter(known.values()))
        else:
            declared = _at(artifact, "environment_id")
            if declared not in known:
                raise PublicationError(
                    "each physical artifact needs a resolvable environment_id "
                    "when more than one environment is published")
            environment = known[declared]
        sources.append(_physical_source(artifact, environment))
    digests = [source["artifact_sha256"] for source in sources]
    if len(set(digests)) != len(digests):
        raise PublicationError("the same physical artifact was published twice")
    sources.sort(key=lambda source: (source["kind_id"],
                                     source["artifact_sha256"]))
    return {
        "evidence_class": EvidenceClass.PHYSICAL.value,
        "physical_validation": bool(sources),
        "interpretation": (
            "Each source below was produced by the named command on the named "
            "machine. Reproducing it requires that hardware and software; it "
            "cannot be recomputed from the repository alone."
            if sources else
            "No physical evidence has been published. Every number in this "
            "report is synthetic."),
        "sources": sources,
        "environments": sorted(
            (dict(environment) for environment in known.values()),
            key=lambda environment: environment["environment_id"]),
        "totals": {
            "sources": len(sources),
            "environments": len(known),
            "kinds": len({source["kind_id"] for source in sources}),
        },
    }


def assert_evidence_separation(document: Mapping[str, Any]) -> None:
    """Fail loudly if any published entry blurs the two evidence classes.

    This runs on the finished document, after every builder, and again inside
    both renderers.  A caller who assembles a document by hand is checked by
    exactly the same rule as the builders.
    """
    if not isinstance(document, Mapping):
        raise PublicationError("publication must be a JSON object")
    evidence = document.get("evidence")
    if not isinstance(evidence, Mapping) or set(evidence) != {
            "synthetic", "physical"}:
        raise PublicationError(
            "publication must carry exactly one synthetic and one physical "
            "section")
    synthetic = evidence["synthetic"]
    physical = evidence["physical"]
    if not isinstance(synthetic, Mapping) or not isinstance(physical, Mapping):
        raise PublicationError("evidence sections must be JSON objects")

    if (synthetic.get("evidence_class") != EvidenceClass.SYNTHETIC.value
            or synthetic.get("physical_validation") is not False
            or synthetic.get("real_apps_exercised") != 0
            or synthetic.get("audio_or_model_runs") is not False):
        raise PublicationError(
            "the synthetic section must declare itself synthetic and claim no "
            "physical validation")
    suites = synthetic.get("suites")
    if not isinstance(suites, list) or not suites:
        raise PublicationError("the synthetic section must publish suites")
    suite_ids = []
    for suite in suites:
        if not isinstance(suite, Mapping):
            raise PublicationError("synthetic suites must be JSON objects")
        if (suite.get("evidence_class") != EvidenceClass.SYNTHETIC.value
                or suite.get("physical_validation") is not False):
            raise PublicationError(
                f"synthetic suite {suite.get('suite_id')!r} claimed physical "
                "validation")
        for forbidden in ("environment_id", "hardware", "kind_id"):
            if forbidden in suite:
                raise PublicationError(
                    f"synthetic suite {suite.get('suite_id')!r} carries "
                    f"{forbidden}, which only physical evidence may name")
        reproduction = suite.get("reproduction")
        if (not isinstance(reproduction, Mapping)
                or reproduction.get("requires_named_hardware") is not False
                or not isinstance(reproduction.get("command"), str)):
            raise PublicationError(
                f"synthetic suite {suite.get('suite_id')!r} must publish a "
                "hardware-free reproduction command")
        suite_ids.append(suite.get("suite_id"))
    if len(set(suite_ids)) != len(suite_ids):
        raise PublicationError("synthetic suite identifiers must be unique")

    if physical.get("evidence_class") != EvidenceClass.PHYSICAL.value:
        raise PublicationError("the physical section must declare itself")
    sources = physical.get("sources")
    environments = physical.get("environments")
    if not isinstance(sources, list) or not isinstance(environments, list):
        raise PublicationError("physical sources and environments are lists")
    if physical.get("physical_validation") is not bool(sources):
        raise PublicationError(
            "the physical section must claim validation only when it has "
            "sources")
    named = {
        environment.get("environment_id")
        for environment in environments
        if isinstance(environment, Mapping)
    }
    for source in sources:
        if not isinstance(source, Mapping):
            raise PublicationError("physical sources must be JSON objects")
        if (source.get("evidence_class") != EvidenceClass.PHYSICAL.value
                or source.get("physical_validation") is not True):
            raise PublicationError(
                "a physical source must declare itself physical")
        if source.get("kind_id") not in _KINDS_BY_ID:
            raise PublicationError(
                "a physical source named an unregistered producer")
        if source.get("environment_id") not in named:
            raise PublicationError(
                "a physical source named no published environment")
        if source.get("suite_id") is not None:
            raise PublicationError(
                "a physical source reused a synthetic suite identifier")
        if not _integer(source.get("volume")):
            raise PublicationError(
                "a physical source published zero physical work")
    for environment in environments:
        validate_environment({
            "schema_version": PUBLICATION_SCHEMA_VERSION, **dict(environment)})
    if not environments and sources:
        raise PublicationError("physical sources need a named environment")

    overlap = set(suite_ids) & {
        source.get("kind_id") for source in sources
        if isinstance(source, Mapping)
    }
    if overlap:
        raise PublicationError(
            "synthetic and physical identifiers must stay disjoint")
    for forbidden in ("totals", "combined_totals", "all_evidence", "suites"):
        if forbidden in document:
            raise PublicationError(
                "the report must not merge synthetic and physical evidence "
                f"into a top-level {forbidden!r} block")


def build_publication(
    *,
    revision: str,
    published_on: str | None = None,
    physical_artifacts: Sequence[Mapping[str, Any]] = (),
    environments: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Assemble the dated public report and verify it before returning it."""
    if not isinstance(revision, str) or not _REVISION.match(revision):
        raise PublicationError(
            "publishing requires the full 40-character repository revision")
    if published_on is None:
        published_on = datetime.now(timezone.utc).date().isoformat()
    if not isinstance(published_on, str) or not _DATE.match(published_on):
        raise PublicationError("published_on must be an ISO YYYY-MM-DD date")
    try:
        datetime.strptime(published_on, "%Y-%m-%d")
    except ValueError as error:
        raise PublicationError("published_on is not a real date") from error
    validated = [
        validate_environment(environment) for environment in environments]
    document = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "report_kind": PUBLICATION_KIND,
        "privacy": PRIVACY,
        "published_on": published_on,
        "repository_revision": revision,
        "evidence_classes": [
            EvidenceClass.SYNTHETIC.value, EvidenceClass.PHYSICAL.value],
        "separation_rule": (
            "Synthetic and physical evidence are built by different functions, "
            "reported in different sections, and never summed together. A "
            "synthetic number cannot be published as a physical one."),
        "evidence": {
            "synthetic": _synthetic_section(),
            "physical": _physical_section(physical_artifacts, validated),
        },
    }
    assert_evidence_separation(document)
    return document


def render_publication_json(document: Mapping[str, Any]) -> str:
    assert_evidence_separation(document)
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def render_publication_markdown(document: Mapping[str, Any]) -> str:
    """Render the dated public report; the guard runs again before any text."""
    assert_evidence_separation(document)
    synthetic = document["evidence"]["synthetic"]
    physical = document["evidence"]["physical"]
    lines = [
        "# Whisper Face public evidence report",
        "",
        f"Published: **{document['published_on']}**",
        "",
        f"Repository revision: `{document['repository_revision']}`",
        "",
        document["separation_rule"],
        "",
        "## Synthetic evidence",
        "",
        synthetic["interpretation"],
        "",
        "| Suite | Cases | Passed | Critical metric | Critical failures "
        "| Reproduce |",
        "|---|---:|---:|---|---:|---|",
    ]
    for suite in synthetic["suites"]:
        lines.append(
            f"| `{suite['suite_id']}` | {suite['cases']} | {suite['passed']} "
            f"| {suite['critical_metric']} | {suite['critical_failures']} "
            f"| `{suite['reproduction']['command']}` |")
    totals = synthetic["totals"]
    lines += [
        "",
        f"Totals: {totals['passed']}/{totals['cases']} cases passed across "
        f"{totals['suites']} suites, {totals['critical_failures']} critical "
        "failures.",
        "",
        "Physical validation: **no**. Real applications exercised: "
        f"**{synthetic['real_apps_exercised']}**. Audio or model runs: "
        f"**{'yes' if synthetic['audio_or_model_runs'] else 'no'}**.",
        "",
        "## Physical evidence",
        "",
        physical["interpretation"],
        "",
    ]
    if physical["sources"]:
        lines += [
            "| Producer | Environment | Volume metric | Volume | Artifact "
            "SHA-256 |",
            "|---|---|---|---:|---|",
        ]
        for source in physical["sources"]:
            lines.append(
                f"| `{source['kind_id']}` | `{source['environment_id']}` "
                f"| {source['volume_metric']} | {source['volume']} "
                f"| `{source['artifact_sha256'][:16]}…` |")
        lines += ["", "### Environments", ""]
        for environment in physical["environments"]:
            software = ", ".join(
                f"{entry['name']} {entry['version']}"
                for entry in environment["software"]) or "none recorded"
            lines += [
                f"- **`{environment['environment_id']}`** — "
                f"{environment['hardware']}, {environment['os_name']} "
                f"{environment['os_version']}, Python "
                f"{environment['python_version']}, Whisper Face "
                f"`{environment['whisper_face_revision']}`; {software}.",
            ]
        lines += ["", "### Reproduction", ""]
        for source in physical["sources"]:
            lines.append(
                f"- `{source['kind_id']}`: "
                f"`{source['reproduction']['producer_command']}` on "
                f"{source['reproduction']['hardware']}.")
    lines.append("")
    return "\n".join(lines)


def _read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish Whisper Face evidence without blurring classes")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "synthetic",
        help="print the deterministic synthetic scorecard (default)")
    publish = commands.add_parser(
        "publish", help="publish a dated synthetic and physical report")
    publish.add_argument(
        "--revision", required=True,
        help="full 40-character repository revision the report describes")
    publish.add_argument(
        "--published-on", help="ISO date; defaults to today in UTC")
    publish.add_argument(
        "--physical-artifact", type=Path, action="append", default=[],
        metavar="PATH",
        help="artifact from a capture harness or activation benchmark")
    publish.add_argument(
        "--environment", type=Path, action="append", default=[],
        metavar="PATH",
        help="named hardware and software the physical artifacts came from")
    publish.add_argument(
        "--format", choices=("json", "markdown"), default="json")
    publish.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.command in (None, "synthetic"):
        print(render_json())
        return 0

    try:
        document = build_publication(
            revision=args.revision,
            published_on=args.published_on,
            physical_artifacts=[
                _read_json_file(path) for path in args.physical_artifact],
            environments=[
                _read_json_file(path) for path in args.environment],
        )
        rendered = (
            render_publication_json(document) if args.format == "json"
            else render_publication_markdown(document))
    except PublicationError as error:
        print(f"refusing to publish: {error}")
        return 1
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.format} report ({len(rendered)} characters)")
    else:
        print(rendered, end="" if rendered.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
