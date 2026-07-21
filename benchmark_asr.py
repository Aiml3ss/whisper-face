# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "huggingface-hub",
#   "mlx-whisper; sys_platform == 'darwin'",
#   "numpy",
#   "soundfile",
#   "whisper-normalizer",
# ]
# ///
"""Reproducible Apple-Silicon ASR bakeoff for dictation engine decisions.

The harness deliberately keeps public research audio outside the repository.
It selects a deterministic, evenly distributed LibriSpeech subset, runs each
engine over the exact same files, and scores every hypothesis through the same
Whisper English normalizer.  Local JSONL records contain public references and
hypotheses only; no personal dictation data is consumed.

Example:

    uv run benchmark_asr.py \
      --dataset /tmp/LibriSpeech/test-clean \
      --engines mlx-tiny mlx-turbo parakeet-unified \
      --macparakeet-cli /path/to/macparakeet-cli \
      --limit 100 --output-dir /tmp/parrot-asr-results
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


MLX_MODELS = {
    "mlx-tiny": "mlx-community/whisper-tiny",
    "mlx-turbo": "mlx-community/whisper-large-v3-turbo",
}
PARAKEET_ENGINES = {
    "parakeet-unified": "unified",
    "parakeet-v3": "v3",
}


@dataclass(frozen=True)
class Sample:
    utterance_id: str
    audio_path: Path
    reference: str


def load_references(dataset: Path) -> dict[str, str]:
    references: dict[str, str] = {}
    for transcript in sorted(dataset.glob("*/*/*.trans.txt")):
        for line in transcript.read_text(encoding="utf-8").splitlines():
            utterance_id, separator, text = line.strip().partition(" ")
            if separator and text:
                references[utterance_id] = text
    return references


def evenly_spaced(items: Sequence[Path], limit: int | None) -> list[Path]:
    """Select stable coverage across a sorted corpus without random state."""
    if limit is None or limit >= len(items):
        return list(items)
    if limit <= 0:
        return []
    if limit == 1:
        return [items[0]]
    last = len(items) - 1
    indices = [round(index * last / (limit - 1)) for index in range(limit)]
    # round() can theoretically collide for unusual ratios; preserve order.
    return [items[index] for index in dict.fromkeys(indices)]


def select_samples(dataset: Path, limit: int | None) -> list[Sample]:
    references = load_references(dataset)
    files = [
        path for path in sorted(dataset.glob("*/*/*.flac"))
        if path.stem in references
    ]
    return [
        Sample(path.stem, path, references[path.stem])
        for path in evenly_spaced(files, limit)
    ]


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower)


def edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Return token Levenshtein distance using O(min(n, m)) memory."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_token in enumerate(reference, 1):
        current = [row]
        for column, hypothesis_token in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1]
                + (reference_token != hypothesis_token),
            ))
        previous = current
    return previous[-1]


def canonical_tokenizer() -> Callable[[str], list[str]]:
    from whisper_normalizer.english import EnglishTextNormalizer

    normalize = EnglishTextNormalizer()

    def tokens(text: str) -> list[str]:
        text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"'))
        return normalize(text).split()

    return tokens


def score_records(records: Iterable[dict], tokenize=None) -> dict:
    tokenize = tokenize or canonical_tokenizer()
    total_edits = 0
    total_reference_words = 0
    exact = 0
    durations: list[float] = []
    processing: list[float] = []
    utterance_error_rates: list[float] = []
    count = 0
    engine = "unknown"
    for record in records:
        engine = str(record.get("engine", engine))
        reference = tokenize(str(record["ref"]))
        hypothesis = tokenize(str(record.get("hyp", "")))
        if not reference:
            continue
        edits = edit_distance(reference, hypothesis)
        total_edits += edits
        total_reference_words += len(reference)
        utterance_error_rates.append(edits / len(reference))
        exact += int(reference == hypothesis)
        count += 1
        if record.get("audio_s") is not None:
            durations.append(float(record["audio_s"]))
        if record.get("proc_s") is not None:
            processing.append(float(record["proc_s"]))
    total_processing = sum(processing)
    return {
        "engine": engine,
        "utterances": count,
        "wer_pct": round(
            100 * total_edits / total_reference_words, 4)
            if total_reference_words else None,
        "exact_pct": round(100 * exact / count, 2) if count else None,
        "utterance_p90_wer_pct": round(
            100 * percentile(utterance_error_rates, 0.90), 4),
        "rtfx": round(sum(durations) / total_processing, 2)
        if durations and total_processing else None,
        "proc_p50_s": round(statistics.median(processing), 4)
        if processing else None,
        "proc_p95_s": round(percentile(processing, 0.95), 4)
        if processing else None,
    }


def audio_info(path: Path) -> tuple[float, int]:
    import soundfile

    info = soundfile.info(str(path))
    return float(info.duration), int(info.samplerate)


def load_audio(path: Path):
    import numpy as np
    import soundfile

    audio, sample_rate = soundfile.read(str(path), dtype="float32")
    if sample_rate != 16_000:
        raise ValueError(f"expected 16 kHz audio, got {sample_rate}: {path}")
    if getattr(audio, "ndim", 1) > 1:
        audio = np.mean(audio, axis=1)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if 0.0 < peak < 0.25:
        audio = audio * min(0.25 / peak, 25.0)
    return np.asarray(audio, dtype=np.float32)


def run_mlx(engine: str, samples: Sequence[Sample]) -> list[dict]:
    if platform.system() != "Darwin":
        raise RuntimeError("MLX benchmark engines require macOS")
    import mlx_whisper
    from huggingface_hub import snapshot_download

    model = str(snapshot_download(repo_id=MLX_MODELS[engine]))
    records = []
    for index, sample in enumerate(samples, 1):
        audio = load_audio(sample.audio_path)
        started = time.monotonic()
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=model,
            language="en",
            temperature=(0.0, 0.2),
            condition_on_previous_text=False,
        )
        elapsed = time.monotonic() - started
        records.append({
            "id": sample.utterance_id,
            "ref": sample.reference,
            "hyp": str(result.get("text", "")).strip(),
            "dataset": "librispeech-test-clean",
            "engine": engine,
            "audio_s": round(len(audio) / 16_000, 4),
            "proc_s": round(elapsed, 4),
        })
        print(f"[{engine}] {index}/{len(samples)}", flush=True)
    return records


def run_parakeet(engine: str, samples: Sequence[Sample], cli: Path) -> list[dict]:
    if not cli.exists():
        raise FileNotFoundError(cli)
    work = Path(tempfile.mkdtemp(prefix="parrot-parakeet-benchmark-"))
    transcripts = work / "transcripts"
    transcripts.mkdir()
    command = [
        str(cli), "transcribe", *(str(sample.audio_path) for sample in samples),
        "--format", "transcript", "--output-dir", str(transcripts),
        "--engine", "parakeet", "--parakeet-model", PARAKEET_ENGINES[engine],
        "--speaker-detection", "off", "--no-history", "--mode", "raw",
    ]
    environment = dict(os.environ)
    environment["MACPARAKEET_TELEMETRY"] = "0"
    started = time.monotonic()
    result = subprocess.run(
        command, capture_output=True, text=True, env=environment)
    elapsed = time.monotonic() - started
    try:
        if result.returncode:
            raise RuntimeError(
                f"macparakeet-cli exited {result.returncode}: "
                f"{result.stderr[-1000:]}")
        durations = {
            sample.utterance_id: audio_info(sample.audio_path)[0]
            for sample in samples
        }
        total_audio = sum(durations.values())
        records = []
        for sample in samples:
            transcript = transcripts / f"{sample.utterance_id}.txt"
            if not transcript.exists():
                raise RuntimeError(f"missing competitor transcript: {transcript}")
            audio_s = durations[sample.utterance_id]
            records.append({
                "id": sample.utterance_id,
                "ref": sample.reference,
                "hyp": transcript.read_text(encoding="utf-8").strip(),
                "dataset": "librispeech-test-clean",
                "engine": engine,
                "audio_s": round(audio_s, 4),
                # The CLI loads once for the batch. Attribute batch wall time
                # proportionally so aggregate RTFx remains exact.
                "proc_s": round(elapsed * audio_s / total_audio, 4),
            })
        return records
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_parrot_helper(
    engine: str, samples: Sequence[Sample], helper: Path
) -> list[dict]:
    """Drive Whisper Face's shipping RAM-only helper protocol."""
    process = subprocess.Popen(
        [str(helper), "--server"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=None, bufsize=0)
    try:
        ready = json.loads(process.stdout.readline().decode("utf-8"))
        if not ready.get("ready"):
            raise RuntimeError("Parrot helper did not become ready")
        records = []
        for index, sample in enumerate(samples, 1):
            audio = load_audio(sample.audio_path)
            started = time.monotonic()
            process.stdin.write(struct.pack("<Q", len(audio)))
            process.stdin.write(memoryview(audio).cast("B"))
            process.stdin.flush()
            response = json.loads(process.stdout.readline().decode("utf-8"))
            elapsed = time.monotonic() - started
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "helper error")))
            records.append({
                "id": sample.utterance_id,
                "ref": sample.reference,
                "hyp": str(response.get("text", "")).strip(),
                "dataset": "librispeech-test-clean",
                "engine": engine,
                "audio_s": round(len(audio) / 16_000, 4),
                "proc_s": round(elapsed, 4),
            })
            print(f"[{engine}] {index}/{len(samples)}", flush=True)
        return records
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=5)
        except Exception:
            process.terminate()


def write_records(path: Path, records: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def print_report(summaries: Sequence[dict]) -> None:
    print("\nASR BAKEOFF")
    print(f"{'engine':20s} {'n':>5s} {'WER%':>8s} {'exact%':>8s} "
          f"{'p90 WER%':>10s} {'RTFx':>8s} {'p95/file':>10s}")
    for summary in summaries:
        print(
            f"{summary['engine']:20s} {summary['utterances']:5d} "
            f"{summary['wer_pct']:8.3f} {summary['exact_pct']:8.2f} "
            f"{summary['utterance_p90_wer_pct']:10.2f} "
            f"{summary['rtfx']:8.2f} {summary['proc_p95_s']:10.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--engines", nargs="+", required=True,
        choices=tuple(MLX_MODELS) + tuple(PARAKEET_ENGINES))
    parser.add_argument("--macparakeet-cli", type=Path)
    parser.add_argument("--parrot-helper", type=Path)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    samples = select_samples(args.dataset.expanduser().resolve(), args.limit)
    if not samples:
        raise SystemExit(f"no referenced FLAC files under {args.dataset}")
    print(f"dataset={args.dataset} samples={len(samples)}")

    summaries = []
    for engine in args.engines:
        if engine in MLX_MODELS:
            records = run_mlx(engine, samples)
        else:
            if args.parrot_helper is not None:
                records = run_parrot_helper(
                    engine, samples, args.parrot_helper.expanduser().resolve())
            elif args.macparakeet_cli is not None:
                records = run_parakeet(
                    engine, samples,
                    args.macparakeet_cli.expanduser().resolve())
            else:
                raise SystemExit(
                    f"--parrot-helper or --macparakeet-cli is required for {engine}")
        records_path = args.output_dir / f"{engine}.jsonl"
        write_records(records_path, records)
        summaries.append(score_records(records))

    report = {
        "schema_version": 1,
        "hardware": {
            "machine": platform.machine(),
            "macos": platform.mac_ver()[0],
        },
        "dataset": "LibriSpeech test-clean",
        "selection": "deterministic-evenly-spaced",
        "samples": len(samples),
        "engines": summaries,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print_report(summaries)
    print(f"\nreport: {args.output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
