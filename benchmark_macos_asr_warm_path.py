# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Opt-in, content-free warm-path profile for the shipping Mac ASR helper.

Only deterministic synthetic audio is generated. The benchmark verifies the
installed Parakeet revision, starts the existing helper once, compares current
two-write framing with a zero-copy ``writev`` candidate, and emits aggregate
timings and fixed counts. It has no runtime or routing authority.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from benchmark_asr import (
    BoundedJSONLineReader,
    DEFAULT_MODEL_SCORECARD,
    DEFAULT_PARAKEET_MODEL_DIR,
    PARAKEET_HELPER_SAMPLE_TIMEOUT_SECONDS,
    PARAKEET_HELPER_STARTUP_TIMEOUT_SECONDS,
    _cleanup_helper_process,
    load_model_specs,
    verify_installed_parakeet_revision,
)


HERE = Path(__file__).resolve().parent
DEFAULT_HELPER = HERE / ".models" / "bin" / "parrot-asr-helper"
SCHEMA_VERSION = 1
MIN_SAMPLES = 20
MAX_SAMPLES = 100
MEANINGFUL_IMPROVEMENT = 0.10
SAMPLE_RATE = 16_000


def _percentile(samples: Sequence[float], point: float) -> float | None:
    return sorted(samples)[math.ceil((len(samples) - 1) * point)] \
        if samples else None


def _writev_all(
    descriptor: int,
    chunks: Sequence[Any],
    *,
    writer: Callable[[int, Sequence[memoryview]], int] | None = None,
) -> None:
    """Write every byte from bounded buffers, including partial writes."""
    if writer is None:
        writer = getattr(os, "writev", None)
        if writer is None:
            raise RuntimeError("writev is unavailable on this platform")
    pending = [memoryview(chunk).cast("B") for chunk in chunks if len(chunk)]
    while pending:
        written = writer(descriptor, pending)
        if not isinstance(written, int) or written <= 0:
            raise OSError("writev made no progress")
        while pending and written >= len(pending[0]):
            written -= len(pending[0])
            pending.pop(0)
        if pending and written:
            pending[0] = pending[0][written:]


def _synthetic_audio(duration_seconds: float) -> np.ndarray:
    count = int(round(duration_seconds * SAMPLE_RATE))
    at = np.arange(count, dtype=np.float32) / np.float32(SAMPLE_RATE)
    # Non-speech, deterministic, bounded waveform with silence gaps.
    carrier = (np.sin(2 * np.pi * 220 * at)
               + 0.35 * np.sin(2 * np.pi * 440 * at))
    envelope = ((np.arange(count) // (SAMPLE_RATE // 5)) % 2).astype(
        np.float32)
    return np.ascontiguousarray(0.04 * carrier * envelope, dtype="<f4")


def _send(
    process: Any,
    reader: BoundedJSONLineReader,
    audio: np.ndarray,
    *,
    mode: str,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[dict[str, float], str]:
    prep_started = clock()
    payload = np.ascontiguousarray(audio, dtype="<f4")
    header = struct.pack("<Q", len(payload))
    view = memoryview(payload).cast("B")
    prep_ms = (clock() - prep_started) * 1000.0

    started = clock()
    if mode == "current-two-write":
        process.stdin.write(header)
        process.stdin.write(view)
        process.stdin.flush()
    elif mode == "candidate-writev":
        _writev_all(process.stdin.fileno(), (header, view))
    else:
        raise ValueError("unknown framing mode")
    response = reader.read(timeout=PARAKEET_HELPER_SAMPLE_TIMEOUT_SECONDS)
    wall_ms = (clock() - started) * 1000.0
    if not response.get("ok"):
        raise RuntimeError("helper rejected synthetic audio")
    native_ms = float(response.get("processing_s", -1.0)) * 1000.0
    if not math.isfinite(native_ms) or native_ms < 0.0:
        raise RuntimeError("helper returned invalid native timing")
    return ({
        "preparation_ms": prep_ms,
        "wall_ms": wall_ms,
        "native_ms": native_ms,
        "client_overhead_ms": max(0.0, wall_ms - native_ms),
    }, str(response.get("text", "")))


def _timing_summary(samples: Sequence[dict[str, float]]) -> dict[str, Any]:
    return {
        key: {
            "p50": _percentile([sample[key] for sample in samples], .50),
            "p95": _percentile([sample[key] for sample in samples], .95),
            "max": max(sample[key] for sample in samples),
        }
        for key in (
            "preparation_ms", "wall_ms", "native_ms", "client_overhead_ms")
    }


def build_report(
    current: Sequence[dict[str, float]],
    candidate: Sequence[dict[str, float]],
    *,
    output_mismatches: int,
    duration_seconds: float,
) -> dict[str, Any]:
    if (len(current) != len(candidate) or len(current) < MIN_SAMPLES
            or not isinstance(output_mismatches, int)
            or output_mismatches < 0 or output_mismatches > len(current)):
        raise ValueError("insufficient or invalid warm-path evidence")
    current_summary = _timing_summary(current)
    candidate_summary = _timing_summary(candidate)
    p95_improvement = (
        current_summary["wall_ms"]["p95"]
        - candidate_summary["wall_ms"]["p95"])
    max_improvement = (
        current_summary["wall_ms"]["max"]
        - candidate_summary["wall_ms"]["max"])
    p95_fraction = p95_improvement / current_summary["wall_ms"]["p95"]
    max_fraction = max_improvement / current_summary["wall_ms"]["max"]
    overhead_p95_improvement = (
        current_summary["client_overhead_ms"]["p95"]
        - candidate_summary["client_overhead_ms"]["p95"])
    overhead_max_improvement = (
        current_summary["client_overhead_ms"]["max"]
        - candidate_summary["client_overhead_ms"]["max"])
    overhead_p95_baseline = current_summary["client_overhead_ms"]["p95"]
    overhead_max_baseline = current_summary["client_overhead_ms"]["max"]
    overhead_p95_fraction = (
        overhead_p95_improvement / overhead_p95_baseline
        if overhead_p95_baseline > 0.0 else None)
    overhead_max_fraction = (
        overhead_max_improvement / overhead_max_baseline
        if overhead_max_baseline > 0.0 else None)
    runtime_change_eligible = (
        output_mismatches == 0
        and p95_fraction >= MEANINGFUL_IMPROVEMENT
        and max_fraction >= MEANINGFUL_IMPROVEMENT
        and overhead_p95_fraction is not None
        and overhead_p95_fraction >= MEANINGFUL_IMPROVEMENT
        and overhead_max_fraction is not None
        and overhead_max_fraction >= MEANINGFUL_IMPROVEMENT)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "opt-in-local-synthetic-warm-mac-asr",
        "privacy": "synthetic-audio-content-free-aggregate-only",
        "runtime_authority": "none",
        "samples_per_variant": len(current),
        "synthetic_duration_seconds": duration_seconds,
        "results": [
            {"id": "current-two-write", "timing_ms": current_summary},
            {"id": "candidate-writev", "timing_ms": candidate_summary},
        ],
        "comparison": {
            "output_mismatches": output_mismatches,
            "p95_improvement_ms": p95_improvement,
            "p95_improvement_fraction": p95_fraction,
            "max_improvement_ms": max_improvement,
            "max_improvement_fraction": max_fraction,
            "client_overhead_p95_improvement_ms": overhead_p95_improvement,
            "client_overhead_p95_improvement_fraction": (
                overhead_p95_fraction),
            "client_overhead_max_improvement_ms": overhead_max_improvement,
            "client_overhead_max_improvement_fraction": (
                overhead_max_fraction),
            "runtime_change_eligible": runtime_change_eligible,
        },
        "claim": {
            "runtime_change_recommended": False,
            "reason": "synthetic warm-path evidence only",
        },
    }


def run_benchmark(
    helper: Path = DEFAULT_HELPER,
    model_dir: Path = DEFAULT_PARAKEET_MODEL_DIR,
    *,
    samples: int = 24,
    duration_seconds: float = 1.0,
) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("warm Mac ASR benchmark requires macOS")
    if not MIN_SAMPLES <= samples <= MAX_SAMPLES:
        raise ValueError("samples must be between 20 and 100")
    if not 0.25 <= duration_seconds <= 10.0:
        raise ValueError("duration must be between 0.25 and 10 seconds")
    if not helper.is_file():
        raise RuntimeError("shipping Parakeet helper is unavailable")
    spec = load_model_specs(DEFAULT_MODEL_SCORECARD)["parakeet-unified"]
    verify_installed_parakeet_revision(spec, model_dir)
    audio = _synthetic_audio(duration_seconds)
    process = subprocess.Popen(
        [str(helper), "--server"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=None, bufsize=0)
    try:
        reader = BoundedJSONLineReader(process.stdout)
        ready = reader.read(timeout=PARAKEET_HELPER_STARTUP_TIMEOUT_SECONDS)
        if not ready.get("ready"):
            raise RuntimeError("shipping Parakeet helper did not become ready")
        _send(process, reader, audio, mode="current-two-write")
        current = []
        candidate = []
        mismatches = 0
        for index in range(samples):
            order = ("current-two-write", "candidate-writev") \
                if index % 2 == 0 else (
                    "candidate-writev", "current-two-write")
            outputs = {}
            for mode in order:
                timing, output = _send(process, reader, audio, mode=mode)
                (current if mode == "current-two-write" else candidate).append(
                    timing)
                outputs[mode] = output
            mismatches += int(
                outputs["current-two-write"] != outputs["candidate-writev"])
        return build_report(
            current, candidate, output_mismatches=mismatches,
            duration_seconds=duration_seconds)
    finally:
        _cleanup_helper_process(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    parser.add_argument(
        "--model-dir", type=Path, default=DEFAULT_PARAKEET_MODEL_DIR)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("refusing helper execution without --run")
    report = run_benchmark(
        args.helper.expanduser().resolve(),
        args.model_dir.expanduser().resolve(),
        samples=args.samples, duration_seconds=args.duration)
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        for result in report["results"]:
            timing = result["timing_ms"]["wall_ms"]
            print(f"{result['id']}: p50={timing['p50']:.2f}ms "
                  f"p95={timing['p95']:.2f}ms max={timing['max']:.2f}ms")
        print("Runtime authority: none.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
