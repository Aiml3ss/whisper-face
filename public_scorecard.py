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
from typing import Any, Callable, Mapping, Sequence

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
            ("schema_version", 1),
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
            ("schema_version", 1),
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
            ("schema_version", 1),
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
            ("schema_version", 1),
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
            ("schema_version", 1),
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
    artifact_environment_ids: Sequence[str | None] | None = None,
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
    # Explicit pairings come from the publisher's command line, aligned by
    # position. None of the registered producers writes an environment_id
    # into its artifact -- and mutating an artifact to add one would change
    # the digest being published -- so with several environments the pairing
    # has to arrive beside the artifact, not inside it.
    if artifact_environment_ids is None:
        artifact_environment_ids = [None] * len(artifacts)
    if len(artifact_environment_ids) != len(artifacts):
        raise PublicationError(
            "each physical artifact needs exactly one environment pairing")
    sources = []
    for artifact, paired in zip(artifacts, artifact_environment_ids):
        declared = paired
        if declared is None:
            in_artifact = _at(artifact, "environment_id")
            if in_artifact is not _MISSING:
                declared = in_artifact
        if declared is not None:
            if declared not in known:
                raise PublicationError(
                    f"artifact names environment {declared!r}, which is not "
                    "a published environment")
            environment = known[declared]
        elif len(known) == 1:
            environment = next(iter(known.values()))
        else:
            raise PublicationError(
                "with more than one environment, pair each artifact "
                "explicitly: --physical-artifact <environment_id>=<path>")
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


def _assert_publication_header(document: Mapping[str, Any]) -> None:
    """Re-validate the top-level claims, not only the evidence sections.

    The renderers promise that a hand-assembled document is checked by the
    same rules as the builders. Without this, a caller could mutate
    ``published_on`` or ``repository_revision`` after building and both
    renderers would publish it.
    """
    schema = document.get("schema_version")
    if schema != PUBLICATION_SCHEMA_VERSION or isinstance(schema, bool):
        raise PublicationError("unsupported publication schema")
    if document.get("report_kind") != PUBLICATION_KIND:
        raise PublicationError("unsupported report kind")
    revision = document.get("repository_revision")
    if not isinstance(revision, str) or not _REVISION.match(revision):
        raise PublicationError(
            "publication must name the full 40-character repository revision")
    published_on = document.get("published_on")
    if not isinstance(published_on, str) or not _DATE.match(published_on):
        raise PublicationError("published_on must be an ISO YYYY-MM-DD date")
    try:
        datetime.strptime(published_on, "%Y-%m-%d")
    except ValueError as error:
        raise PublicationError("published_on is not a real date") from error


def assert_evidence_separation(document: Mapping[str, Any]) -> None:
    """Fail loudly if any published entry blurs the two evidence classes.

    This runs on the finished document, after every builder, and again inside
    both renderers.  A caller who assembles a document by hand is checked by
    exactly the same rule as the builders.
    """
    if not isinstance(document, Mapping):
        raise PublicationError("publication must be a JSON object")
    _assert_publication_header(document)
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
    artifact_environment_ids: Sequence[str | None] | None = None,
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
            "physical": _physical_section(
                physical_artifacts, validated,
                artifact_environment_ids),
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
                f"| `{source['artifact_sha256']}` |")
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


_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_BENCHMARK_KEYS = frozenset({
    "schema_version", "result_kind", "product", "model", "environment",
    "corpus", "run", "recognition", "app_path", "outside_probe", "takes",
})


def _benchmark_rivals(path: Path | None = None) -> dict[str, Mapping[str, Any]]:
    import benchmark_public

    registry = _read_json_file(path or benchmark_public.RIVALS)
    return {entry.get("product_id"): entry
            for entry in registry.get("rivals", [])
            if isinstance(entry, Mapping)}


def _rival_may_be_published(entry: Mapping[str, Any] | None) -> bool:
    return (isinstance(entry, Mapping)
            and entry.get("account_held_by") == "project-owner"
            and entry.get("terms_allow_publishing") is True
            and entry.get("recorded_by_owner") is True
            and isinstance(entry.get("terms_read_on"), str)
            and bool(_DATE.match(entry["terms_read_on"]))
            and isinstance(entry.get("terms_reference"), str)
            and bool(entry["terms_reference"].strip()))


def validate_benchmark_result(
    result: Any,
    *,
    corpus: Mapping[str, Any] | None = None,
    rivals: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Refuse a public benchmark result that breaks a benchmark rule.

    It must name the machine, the OS, the model and the app revision it
    measured; be a result of the corpus checked in now, with its seed; keep
    every number under the way it was measured; leave every number only the
    outside probe can give empty; and carry accuracy numbers that its own
    per-take texts reproduce. A product other than Whisper Face is refused
    unless the owner has recorded its terms in the rivals registry.
    """
    import benchmark_public as bench

    if not isinstance(result, Mapping) or set(result) != _BENCHMARK_KEYS:
        raise PublicationError(
            "a benchmark result must carry exactly "
            + ", ".join(sorted(_BENCHMARK_KEYS)))
    if (result["schema_version"] != bench.SCHEMA_VERSION
            or isinstance(result["schema_version"], bool)
            or result["result_kind"] != bench.RESULT_KIND):
        raise PublicationError("unsupported benchmark result")

    product = result["product"]
    if not isinstance(product, Mapping):
        raise PublicationError("the result must name the product")
    product_id = product.get("product_id")
    if product_id != bench.PRODUCT_ID:
        registry = _benchmark_rivals() if rivals is None else rivals
        if not _rival_may_be_published(registry.get(product_id)):
            raise PublicationError(
                f"refusing to publish a result for {product_id!r}: the "
                "owner has not recorded an account and terms that allow it "
                "in benchmarks/public_benchmark_rivals.json")
    revision = product.get("revision")
    if not isinstance(revision, str) or not _REVISION.match(revision):
        raise PublicationError(
            "the result must name the full 40-character app revision")
    if product.get("tree_clean") is not True:
        raise PublicationError(
            "the result was measured on a checkout with uncommitted changes")
    _concrete_text(product.get("version"), "product version")
    for key in ("runtime_source_sha256", "runtime_binary_sha256"):
        if not isinstance(product.get(key), str) or not _SHA256.match(
                product[key]):
            raise PublicationError(f"the result must record {key}")

    environment = result["environment"]
    if not isinstance(environment, Mapping):
        raise PublicationError("the result must name the machine")
    validated = validate_environment(
        {"schema_version": PUBLICATION_SCHEMA_VERSION, **dict(environment)})
    if validated["whisper_face_revision"] != revision:
        raise PublicationError(
            "the machine description names a different app revision")

    model = result["model"]
    if not isinstance(model, Mapping):
        raise PublicationError("the result must name the model")
    _concrete_text(model.get("model_id"), "model id")
    if not isinstance(model.get("revision"), str) or not _REVISION.match(
            model["revision"]):
        raise PublicationError("the result must name the model revision")
    if (not isinstance(model.get("files_sha256"), str)
            or not _SHA256.match(model["files_sha256"])
            or not _integer(model.get("files"))):
        raise PublicationError("the result must hash the model files")

    corpus = bench.load_corpus() if corpus is None else corpus
    summary = result["corpus"]
    if (not isinstance(summary, Mapping)
            or summary.get("corpus_id") != corpus["corpus_id"]
            or summary.get("sha256") != bench.canonical_digest(corpus)
            or summary.get("takes") != len(corpus["takes"])):
        raise PublicationError(
            "the result is not of the corpus checked in now; rerun it")
    if summary.get("synthetic_speech") is not True:
        raise PublicationError(
            "the result must say its speech is synthetic")
    run = result["run"]
    if (not isinstance(run, Mapping) or run.get("seed") != corpus["seed"]
            or isinstance(run.get("seed"), bool)):
        raise PublicationError("the result must use the corpus seed")
    if (not _integer(run.get("repeats")) or not 1 <= run["repeats"] <= 10
            or "denied" not in str(run.get("network"))
            or not str(run.get("command", "")).startswith(
                "uv run benchmark_public.py run")):
        raise PublicationError(
            "the result must record its repeats, the denied network and "
            "the command that produced it")
    if any(word.startswith(("/", "~")) or ":\\" in word
           for word in str(run["command"]).split()):
        raise PublicationError(
            "the rerun command names a local folder; a published page "
            "shows placeholders such as <runtime>, <models> and <out>")

    recognition = result["recognition"]
    app_path = result["app_path"]
    if (not isinstance(recognition, Mapping)
            or recognition.get("measured_by") != bench.RUNTIME_IN_PROCESS):
        raise PublicationError(
            f"recognition numbers must be {bench.RUNTIME_IN_PROCESS!r}")
    if (not isinstance(app_path, Mapping)
            or app_path.get("measured_by") != bench.APP_ON_FAKE_MAC):
        raise PublicationError(
            f"app-path numbers must be {bench.APP_ON_FAKE_MAC!r}")
    configurations = app_path.get("configurations")
    if (not isinstance(configurations, Mapping) or not configurations
            or not set(configurations) <= set(bench.CONFIGURATIONS)):
        raise PublicationError("unknown app-path configurations")
    probe = result["outside_probe"]
    if (not isinstance(probe, Mapping)
            or set(probe) != set(bench.OUTSIDE_PROBE_METRICS)):
        raise PublicationError(
            "the result must list every number only the outside probe gives")
    for name, entry in probe.items():
        if (isinstance(entry, Mapping)
                and entry.get("status") == bench.OUTSIDE_PROBE
                and name in bench.OUTSIDE_PROBE_MEASURES):
            continue                 # checked once the takes are known
        if (not isinstance(entry, Mapping)
                or entry.get("status") != bench.NEEDS_OUTSIDE_PROBE
                or entry.get("value") is not None):
            raise PublicationError(
                f"{name} can only come from the outside probe; this run "
                "cannot carry a value for it")

    takes = result["takes"]
    if (not isinstance(takes, list)
            or [take.get("id") for take in takes if isinstance(take, Mapping)]
            != [take["id"] for take in corpus["takes"]]):
        raise PublicationError("the result must carry every take once, in "
                               "corpus order")
    for take in takes:
        if take.get("voice") not in corpus["speech"]["voices"]:
            raise PublicationError(f"{take['id']}: not a corpus voice")
    for name, entry in probe.items():
        if entry.get("status") == bench.OUTSIDE_PROBE:
            problems = bench.outside_probe_problems(result, entry)
            if problems:
                raise PublicationError(
                    f"{name} from the outside probe is refused: "
                    + "; ".join(problems))
    expected = bench.score_takes(
        [{"id": take["id"], "hypothesis": take["runtime"]["text"]}
         for take in takes], corpus)
    if recognition.get("accuracy") != expected:
        raise PublicationError(
            "the recognition accuracy does not follow from the per-take "
            "texts in the same result")
    for name, block in configurations.items():
        rescored = bench.score_takes(
            [{"id": take["id"], "hypothesis": take["app"][name]["typed"]}
             for take in takes], corpus)
        if not isinstance(block, Mapping) or block.get("accuracy") != rescored:
            raise PublicationError(
                f"the {name} app-path accuracy does not follow from the "
                "per-take texts in the same result")


def _shown(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value}{suffix}"


def _latency_rows(label: str, buckets: Mapping[str, Any]) -> list[str]:
    import benchmark_public as bench

    rows = []
    for name, _low, _high in (*bench.LENGTH_BUCKETS, ("all", 0, 0)):
        bucket = buckets[name]
        rows.append(
            f"| {label} | {name} | {bucket['takes']} | "
            f"{_shown(bucket['p50'])} | {_shown(bucket['p95'])} | "
            f"{_shown(bucket['max'])} |")
    return rows


def render_benchmark_markdown(result: Mapping[str, Any]) -> str:
    """The human page for one benchmark result; the rules run first."""
    import benchmark_public as bench

    validate_benchmark_result(result)
    product = result["product"]
    environment = result["environment"]
    model = result["model"]
    corpus = result["corpus"]
    run = result["run"]
    recognition = result["recognition"]
    configurations = result["app_path"]["configurations"]
    accuracy = recognition["accuracy"]
    columns = [("Runtime alone", accuracy)] + [
        (f"App path, {name}", block["accuracy"])
        for name, block in configurations.items()]
    lines = [
        "# Whisper Face public benchmark",
        "",
        f"Run {run['started_utc']} by Whisper Face's own makers. Rerun it "
        "with the command at the end; docs/benchmarks/public-benchmark.md "
        "says what is measured, how, and what it cannot show.",
        "",
        f"- **Machine:** {environment['hardware']}; "
        f"{environment['os_name']} {environment['os_version']}; Python "
        f"{environment['python_version']}.",
        f"- **App:** Whisper Face {product['version']}, revision "
        f"`{product['revision']}`; speech runtime sources "
        f"`sha256:{product['runtime_source_sha256'][:16]}`, binary "
        f"`sha256:{product['runtime_binary_sha256'][:16]}`.",
        f"- **Model:** `{model['model_id']}` at revision "
        f"`{model['revision']}` ({model['files']} files, "
        f"`sha256:{model['files_sha256'][:16]}`).",
        f"- **Corpus:** `{corpus['corpus_id']}`, {corpus['takes']} takes, "
        f"{corpus['audio_seconds']} s of audio, corpus "
        f"`sha256:{corpus['sha256'][:16]}`, audio "
        f"`sha256:{corpus['audio_sha256'][:16]}`.",
        f"- **Run:** seed {run['seed']}, {run['repeats']} timed passes of "
        f"the runtime, app path paced {run['app_path_pace']}; "
        f"network {run['network']}.",
        "",
        "**Synthetic speech.** Every take was spoken by a macOS system voice "
        f"({', '.join(corpus['voices'])}). That shows how the app handles "
        "names, terms, numbers and long takes from clean speech. It says "
        "nothing about how well it hears a real person, in a real room.",
        "",
        "## Accuracy",
        "",
        "| Measure | " + " | ".join(label for label, _ in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]

    def row(label: str, pick: Callable[[Mapping[str, Any]], Any],
            suffix: str = "") -> str:
        return (f"| {label} | "
                + " | ".join(_shown(pick(block), suffix)
                             for _, block in columns) + " |")

    lines += [
        row("Word error rate", lambda a: a["wer_pct"], "%"),
        row("Takes exactly right (words)", lambda a: a["exact_pct"], "%"),
        row("Name and term recall", lambda a: a["terms"]["recall_pct"], "%"),
        row("Names and terms recalled", lambda a: (
            f"{a['terms']['recalled']} of {a['terms']['expected']}")),
        row("False insertions of a glossary term",
            lambda a: a["terms"]["false_insertions"]),
        row("... of them in the near misses", lambda a: (
            f"{a['terms']['false_insertions_in_near_misses']} in "
            f"{a['terms']['near_miss_takes']} takes")),
        row("Formatting written exactly", lambda a: (
            f"{a['formatting']['written_exactly']} of "
            f"{a['formatting']['targets']} "
            f"({_shown(a['formatting']['accuracy_pct'], '%')})")),
    ]
    for category in bench.CATEGORIES:
        lines.append(row(
            f"Word error rate, {category.replace('_', ' ')}",
            lambda a, c=category: a["by_category"].get(c, {}).get("wer_pct"),
            "%"))
    lines += [
        "",
        "*Runtime alone* is Whisper Face's own speech runtime given each "
        "take whole. *App path* is the real `dictate.py` on the fake Mac "
        "the release gate uses, with the same runtime: the take is spoken "
        "into the fake microphone at real time, then cut, decoded, "
        "compiled and pasted as for a person. *defaults* is a fresh "
        "install; *dictionary* is the same with the corpus glossary in the "
        "app's own dictionary. A false insertion is a glossary term written "
        "more often than it was said.",
        "",
        "## Latency, milliseconds",
        "",
        "| Measured | Take length | Takes | p50 | p95 | max |",
        "|---|---|---:|---:|---:|---:|",
    ]
    lines += _latency_rows(
        "Runtime alone, request to answer (in process)",
        recognition["latency_ms"])
    for name, block in configurations.items():
        lines += _latency_rows(
            f"App path, {name}: key release to pasted (fake field)",
            block["latency_ms"]["release_to_pasted_in_fake_field"])
    measured = result["outside_probe"]["release_to_text_on_screen_ms"]
    if measured["status"] == bench.OUTSIDE_PROBE:
        value = measured["value"]
        lines += _latency_rows(
            "Outside probe: key release to first text in a real window",
            value["release_to_first_text"])
        lines += _latency_rows(
            "Outside probe: key release to final text in a real window",
            value["release_to_final_text"])
    lines += [
        "",
        f"The runtime decodes {recognition['real_time_factor']}x faster "
        "than real time over the corpus. App-path times are the app's own "
        "measure on the fake Mac, whose field takes a paste instantly; they "
        "leave out what a real app adds, so they are not a release-to-screen "
        "number.",
    ]
    if measured["status"] == bench.OUTSIDE_PROBE:
        method = measured["probe"]["method"]
        lines += [
            "",
            "*Outside probe* times are the only ones here comparable across "
            "apps: outside_probe.py played each take "
            f"({method['audio']}), pressed and released the app's capture "
            "key with synthetic key events, and timed the text arriving in "
            "its own window "
            f"({measured['value']['takes']} takes, "
            f"{measured['probe']['run']['repeats']} repeats each, "
            f"{measured['value']['repeats_without_text']} repeats with no "
            "text). Final text is the last change before the window stayed "
            f"unchanged for {method['settle_ms']} ms.",
        ]
    lines += [
        "",
        "## Delivery on the fake Mac",
        "",
        "| Configuration | Takes | Answered by the runtime | Typed as the "
        "app meant | Paste verified | Nothing typed |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, block in configurations.items():
        delivery = block["delivery"]
        lines.append(
            f"| {name} | {delivery['takes']} | "
            f"{delivery['answered_by_runtime']} | "
            f"{delivery['delivered_as_meant']} | "
            f"{delivery['insertion_verified']} | "
            f"{delivery['nothing_typed']} |")
    lines += [
        "",
        "This counts the app's own paste and readback against a fake text "
        "field. It does not show delivery into real apps.",
        "",
        "## Not measured here: needs the outside probe",
        "",
        "| Measure | Why this run cannot give it |",
        "|---|---|",
    ]
    for name, entry in result["outside_probe"].items():
        if entry["status"] == bench.NEEDS_OUTSIDE_PROBE:
            lines.append(f"| `{name}` | {entry['why']} |")
    lines += ["", "## Where it went wrong", ""]
    wrong = [("Runtime alone", accuracy)] + [
        (f"App path, {name}", block["accuracy"])
        for name, block in configurations.items()]
    for label, block in wrong:
        missed = block["terms"]["missed"]
        inserted = block["terms"]["inserted"]
        lines.append(f"**{label}:** {len(missed)} names or terms missed, "
                     f"{len(inserted)} inserted.")
        lines.append("")
        for item in missed:
            lines.append(f"- missed `{item['term']}` in `{item['id']}`: "
                         f"{item['hypothesis']!r}")
        for item in inserted:
            lines.append(f"- inserted `{item['term']}` in `{item['id']}`: "
                         f"{item['hypothesis']!r}")
        if missed or inserted:
            lines.append("")
    lines += [
        "## Rerun it",
        "",
        "```sh",
        run["command"],
        "uv run public_scorecard.py benchmark <out>/result.json",
        "```",
        "",
        "Rival apps are not in this table. Nothing is published about "
        "another product until the owner holds an account, has read its "
        "terms, and has recorded that they allow it "
        "(`benchmarks/public_benchmark_rivals.json`).",
        "",
    ]
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
        "--physical-artifact", action="append", default=[],
        metavar="[ENVIRONMENT_ID=]PATH",
        help="artifact from a capture harness or activation benchmark; "
             "with more than one --environment, prefix the id of the "
             "environment that produced it")
    publish.add_argument(
        "--environment", type=Path, action="append", default=[],
        metavar="PATH",
        help="named hardware and software the physical artifacts came from")
    publish.add_argument(
        "--format", choices=("json", "markdown"), default="json")
    publish.add_argument("--output", type=Path)
    benchmark = commands.add_parser(
        "benchmark",
        help="check a benchmark_public.py result and render its page")
    benchmark.add_argument("result", type=Path)
    benchmark.add_argument(
        "--format", choices=("markdown", "json"), default="markdown")
    benchmark.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.command in (None, "synthetic"):
        print(render_json())
        return 0

    if args.command == "benchmark":
        try:
            result = _read_json_file(args.result)
            if args.format == "markdown":
                rendered = render_benchmark_markdown(result)
            else:
                validate_benchmark_result(result)
                rendered = json.dumps(
                    result, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        except PublicationError as error:
            print(f"refusing to publish: {error}")
            return 1
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            print(f"wrote {args.format} page ({len(rendered)} characters)")
        else:
            print(rendered, end="")
        return 0

    try:
        artifact_paths: list[Path] = []
        artifact_environment_ids: list[str | None] = []
        for value in args.physical_artifact:
            paired, separator, path = value.partition("=")
            if separator and paired and not paired.startswith((".", "/", "~")):
                artifact_environment_ids.append(paired)
                artifact_paths.append(Path(path))
            else:
                artifact_environment_ids.append(None)
                artifact_paths.append(Path(value))
        document = build_publication(
            revision=args.revision,
            published_on=args.published_on,
            physical_artifacts=[
                _read_json_file(path) for path in artifact_paths],
            environments=[
                _read_json_file(path) for path in args.environment],
            artifact_environment_ids=artifact_environment_ids,
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
