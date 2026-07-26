# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "huggingface-hub",
#   "mlx-whisper; sys_platform == 'darwin'",
#   "numpy",
# ]
# ///
"""Offline, transcript-free evidence for Selective Re-listen activation.

The explicit manifest points to local, mono PCM16 WAV microspans and supplies
their private expected text.  Every measured engine receives the same complete
WAV and exact-text case.  Only aggregate decisions, refusals, and monotonic
latencies enter the report; paths, case identifiers, audio, expected text, and
model transcripts never do.
"""

from __future__ import annotations

import argparse
from array import array
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
import wave

from process_verifier import (
    RefusalReason,
    VerificationReceipt,
    VerificationRequest,
    _validated_result,
)
from relisten_activation import (
    MIN_REAL_SAMPLES,
    MIN_REAL_SAMPLES_PER_OUTCOME,
    ActivationError,
    activation_candidate,
    build_activation_receipt,
    write_activation_receipt,
)
from whisper_verifier_adapter import (
    MAX_AUDIO_SAMPLES,
    MAX_EXPECTED_CHARACTERS,
    MAX_EXPECTED_UTF8_BYTES,
    WHISPER_SAMPLE_RATE,
    PrewarmedWhisperTinyVerifier,
    WhisperTinyVerifier,
    WhisperTinyWorker,
)


SCHEMA_VERSION = 1
MANIFEST_KIND = "whisper-face/relisten-activation-manifest"
REPORT_KIND = "whisper-face/relisten-activation-report"
PRIVACY = "transcript-free-aggregate-only"
EVIDENCE_TYPES = frozenset({"real-recorded", "synthetic-test"})
EXPECTED_OUTCOMES = frozenset({"confirmed", "contradicted"})
MAX_CASES = 256
MAX_DEADLINE_SECONDS = 60.0

_MANIFEST_KEYS = frozenset({"schema_version", "kind", "cases"})
_CASE_KEYS = frozenset({
    "case_id", "wav", "expected_text", "expected_outcome", "evidence_type",
})
_ENGINE_IDS = (
    "disposable_whisper_tiny",
    "prewarmed_whisper_tiny",
    "whole_span_local_baseline",
)


class BenchmarkError(ValueError):
    """Manifest or local audio violated the closed benchmark contract."""


class Verifier(Protocol):
    def verify(
        self,
        samples: Sequence[float],
        sample_rate: int,
        expected: str,
        *,
        deadline_at: float,
    ) -> VerificationReceipt: ...


@dataclass(frozen=True, slots=True, repr=False)
class Case:
    case_id: str
    wav: Path
    expected_text: str
    expected_outcome: str
    evidence_type: str


@dataclass(frozen=True, slots=True, repr=False)
class Manifest:
    cases: tuple[Case, ...]


def _closed_mapping(
    value: Any,
    expected: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise BenchmarkError(f"invalid {label} schema")
    return dict(value)


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 96
        and value[0].isalnum()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _expected_text(value: Any) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_EXPECTED_CHARACTERS:
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_EXPECTED_UTF8_BYTES
    except UnicodeError:
        return False


def load_manifest(path: Path) -> Manifest:
    """Load a closed manifest without placing private fields in errors."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        root = _closed_mapping(raw, _MANIFEST_KEYS, "manifest")
    except BenchmarkError:
        raise
    except Exception as exc:
        raise BenchmarkError("manifest is unavailable or invalid") from exc
    if (root["schema_version"] != SCHEMA_VERSION
            or isinstance(root["schema_version"], bool)
            or root["kind"] != MANIFEST_KIND):
        raise BenchmarkError("unsupported manifest declaration")
    if (not isinstance(root["cases"], list) or not root["cases"]
            or len(root["cases"]) > MAX_CASES):
        raise BenchmarkError("manifest requires a bounded non-empty case list")

    manifest_root = path.resolve().parent
    cases: list[Case] = []
    case_ids: set[str] = set()
    for index, raw_case in enumerate(root["cases"]):
        item = _closed_mapping(raw_case, _CASE_KEYS, f"case {index}")
        case_id = item["case_id"]
        if not _identifier(case_id) or case_id in case_ids:
            raise BenchmarkError("case identifiers must be valid and unique")
        relative = item["wav"]
        if (not isinstance(relative, str) or not relative
                or len(relative) > 512 or Path(relative).is_absolute()):
            raise BenchmarkError("case WAV must be a bounded relative path")
        wav_path = (manifest_root / relative).resolve()
        try:
            wav_path.relative_to(manifest_root)
        except ValueError as exc:
            raise BenchmarkError("case WAV must stay under the manifest root") \
                from exc
        if not _expected_text(item["expected_text"]):
            raise BenchmarkError("case expected text is invalid")
        if item["expected_outcome"] not in EXPECTED_OUTCOMES:
            raise BenchmarkError("case expected outcome is unsupported")
        if item["evidence_type"] not in EVIDENCE_TYPES:
            raise BenchmarkError("case evidence type is unsupported")
        cases.append(Case(
            case_id=case_id,
            wav=wav_path,
            expected_text=item["expected_text"],
            expected_outcome=item["expected_outcome"],
            evidence_type=item["evidence_type"],
        ))
        case_ids.add(case_id)
    return Manifest(tuple(cases))


def read_microspan_wav(path: Path) -> tuple[float, ...]:
    """Read one strict 16 kHz mono PCM16 WAV without exposing its path."""
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != WHISPER_SAMPLE_RATE
                    or source.getcomptype() != "NONE"):
                raise BenchmarkError("WAV must be mono 16 kHz PCM16")
            frames = source.getnframes()
            if (isinstance(frames, bool) or not isinstance(frames, int)
                    or not 0 < frames <= MAX_AUDIO_SAMPLES):
                raise BenchmarkError("WAV exceeds the microspan frame bound")
            payload = source.readframes(frames)
    except BenchmarkError:
        raise
    except Exception as exc:
        raise BenchmarkError("WAV is unavailable or invalid") from exc
    if len(payload) != frames * 2:
        raise BenchmarkError("WAV sample payload is truncated")
    integers = array("h")
    integers.frombytes(payload)
    if sys.byteorder != "little":
        integers.byteswap()
    return tuple(value / 32768.0 for value in integers)


class WholeSpanLocalBaseline:
    """Run the existing pinned local worker over the entire manifest WAV."""

    def __init__(self, worker: WhisperTinyWorker | None = None) -> None:
        self._worker = worker or WhisperTinyWorker()

    def verify(
        self,
        samples: Sequence[float],
        sample_rate: int,
        expected: str,
        *,
        deadline_at: float,
    ) -> VerificationReceipt:
        try:
            payload = self._worker(VerificationRequest(
                tuple(samples), sample_rate, expected, deadline_at))
            result = _validated_result(payload)
        except Exception:
            result = None
        if result is None:
            return VerificationReceipt(refusal=RefusalReason.MALFORMED_RESULT)
        return VerificationReceipt(result=result)


def default_providers() -> dict[str, Verifier]:
    return {
        "disposable_whisper_tiny": WhisperTinyVerifier(),
        "prewarmed_whisper_tiny": PrewarmedWhisperTinyVerifier(),
        "whole_span_local_baseline": WholeSpanLocalBaseline(),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _empty_engine() -> dict[str, Any]:
    return {
        "cases": 0,
        "correct": 0,
        "outcomes": Counter(),
        "refusals": Counter(),
        "latencies": [],
    }


def _closed_receipt(value: Any) -> VerificationReceipt:
    if not isinstance(value, VerificationReceipt):
        return VerificationReceipt(refusal=RefusalReason.MALFORMED_RESULT)
    if value.refusal is not None:
        if not isinstance(value.refusal, RefusalReason):
            return VerificationReceipt(
                refusal=RefusalReason.MALFORMED_RESULT)
        return value
    result = value.result
    validated = _validated_result({
        "outcome": getattr(result, "outcome", None),
        "confidence": getattr(result, "confidence", None),
        "engine": getattr(result, "engine", None),
    })
    if validated is None:
        return VerificationReceipt(refusal=RefusalReason.MALFORMED_RESULT)
    return VerificationReceipt(result=validated)


def _engine_report(engine_id: str, state: dict[str, Any]) -> dict[str, Any]:
    cases = state["cases"]
    correct = state["correct"]
    latencies = state["latencies"]
    outcomes = state["outcomes"]
    refusals = state["refusals"]
    return {
        "engine_id": engine_id,
        "availability": "measured",
        "cases": cases,
        "correct": correct,
        "incorrect": cases - correct,
        "exact_case_accuracy_pct": round(100.0 * correct / cases, 3),
        "outcomes": {
            outcome: int(outcomes[outcome])
            for outcome in ("confirmed", "contradicted", "inconclusive")
        },
        "refusals": {
            reason.value: int(refusals[reason.value])
            for reason in RefusalReason
        },
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
        },
    }


def evaluate(
    manifest: Manifest,
    providers: Mapping[str, Verifier],
    *,
    deadline_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run identical private cases and return aggregate-only evidence."""
    if (isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(float(deadline_seconds))
            or not 0.0 < float(deadline_seconds) <= MAX_DEADLINE_SECONDS):
        raise BenchmarkError("deadline must be within the supported bound")
    if set(providers) != set(_ENGINE_IDS):
        raise BenchmarkError("providers must match the closed engine set")
    if any(not hasattr(provider, "verify") for provider in providers.values()):
        raise BenchmarkError("every measured provider must implement verify")

    states = {engine_id: _empty_engine() for engine_id in _ENGINE_IDS}
    evidence_counts: Counter[str] = Counter()
    real_outcomes: Counter[str] = Counter()
    for case_index, case in enumerate(manifest.cases):
        samples = read_microspan_wav(case.wav)
        evidence_counts[case.evidence_type] += 1
        if case.evidence_type == "real-recorded":
            real_outcomes[case.expected_outcome] += 1
        offset = case_index % len(_ENGINE_IDS)
        engine_order = (*_ENGINE_IDS[offset:], *_ENGINE_IDS[:offset])
        for engine_id in engine_order:
            state = states[engine_id]
            started = clock()
            deadline_at = started + float(deadline_seconds)
            try:
                receipt = providers[engine_id].verify(
                    samples,
                    WHISPER_SAMPLE_RATE,
                    case.expected_text,
                    deadline_at=deadline_at,
                )
            except Exception:
                receipt = VerificationReceipt(refusal=RefusalReason.CRASH)
            finished = clock()
            if finished >= deadline_at:
                receipt = VerificationReceipt(refusal=RefusalReason.TIMEOUT)
            state["cases"] += 1
            state["latencies"].append(
                max(0.0, (finished - started) * 1_000.0))
            receipt = _closed_receipt(receipt)
            if receipt.refusal is not None:
                state["refusals"][receipt.refusal.value] += 1
                continue
            outcome = receipt.result.outcome
            state["outcomes"][outcome] += 1
            if outcome == case.expected_outcome:
                state["correct"] += 1
        samples = ()

    real_samples = evidence_counts["real-recorded"]
    minimum_met = (
        real_samples >= MIN_REAL_SAMPLES
        and all(real_outcomes[outcome] >= MIN_REAL_SAMPLES_PER_OUTCOME
                for outcome in EXPECTED_OUTCOMES)
    )
    engines = [_engine_report(engine_id, states[engine_id])
               for engine_id in _ENGINE_IDS]
    engines.append({
        "engine_id": "general_llm_audio",
        "availability": "unavailable",
        "reason": "no-valid-local-audio-verifier-contract",
        "metrics": None,
    })
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "privacy": PRIVACY,
        "evidence_scope": "explicit-local-wav-manifest",
        "execution_order": "deterministic-rotation-by-case-index",
        "cases": len(manifest.cases),
        "evidence_counts": {
            evidence_type: int(evidence_counts[evidence_type])
            for evidence_type in sorted(EVIDENCE_TYPES)
        },
        "engines": engines,
        "activation_evidence": {
            "minimum_real_samples": MIN_REAL_SAMPLES,
            "minimum_real_samples_per_outcome": MIN_REAL_SAMPLES_PER_OUTCOME,
            "real_samples": real_samples,
            "real_confirmed_cases": int(real_outcomes["confirmed"]),
            "real_contradicted_cases": int(real_outcomes["contradicted"]),
            "state": (
                "minimum-sample-count-met" if minimum_met
                else "insufficient-real-samples"
            ),
            "activation_claim": False,
            "decision": "evidence-required",
        },
    }
    candidate = activation_candidate(report)
    report["activation_evidence"]["runtime_candidate"] = candidate.ready
    report["activation_evidence"]["runtime_candidate_reason"] = \
        candidate.reason
    report["activation_evidence"]["decision"] = (
        "manual-review-required" if candidate.ready
        else "evidence-required"
    )
    return report


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def close_providers(providers: Mapping[str, Verifier]) -> None:
    for provider in providers.values():
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def main(
    argv: Sequence[str] | None = None,
    *,
    providers: Mapping[str, Verifier] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run transcript-free Selective Re-listen evidence")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--deadline-seconds", type=float, default=10.0)
    parser.add_argument(
        "--approve-runtime",
        type=Path,
        help="write a content-free activation receipt after all gates pass",
    )
    parser.add_argument(
        "--confirm-manual-review",
        action="store_true",
        help="confirm private real-recording cases were manually reviewed",
    )
    arguments = parser.parse_args(argv)
    if arguments.confirm_manual_review and arguments.approve_runtime is None:
        print("re-listen activation approval requires an output path",
              file=sys.stderr)
        return 2
    actual_providers = dict(providers) if providers is not None \
        else default_providers()
    try:
        manifest = load_manifest(arguments.manifest)
        report = evaluate(
            manifest,
            actual_providers,
            deadline_seconds=arguments.deadline_seconds,
            clock=clock,
        )
    except BenchmarkError:
        print("re-listen benchmark configuration error", file=sys.stderr)
        return 2
    finally:
        close_providers(actual_providers)
    if arguments.approve_runtime is not None:
        try:
            receipt = build_activation_receipt(
                report,
                manual_review_approved=arguments.confirm_manual_review,
            )
            write_activation_receipt(arguments.approve_runtime, receipt)
        except (ActivationError, OSError):
            print("re-listen activation evidence not approved",
                  file=sys.stderr)
            return 2
    print(render_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
