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

A punctuated, cased reference corpus may replace the LibriSpeech directory:
pass a JSONL manifest file as --dataset, one object per line, e.g.

    {"id": "note-001", "audio": "audio/note-001.wav", "text": "Hello, world."}

Audio paths resolve relative to the manifest; "id" defaults to the audio file
stem.  With --formatting-scoring the per-engine summary additionally carries
cased WER, trailing-punctuation precision/recall/F1, and a capitalization
match rate.  Against references that carry no formatting (LibriSpeech is
uppercase and unpunctuated) that block is reported as unavailable instead of
misleading zeros.  Normalized WER stays the primary metric either way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import select
import shutil
import statistics
import struct
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar


HERE = Path(__file__).resolve().parent
DEFAULT_MODEL_SCORECARD = HERE / "benchmarks" / "model_scorecard.json"
DEFAULT_PARAKEET_MODEL_DIR = (
    Path.home() / "Library" / "Application Support" / "FluidAudio" /
    "Models" / "parakeet-unified-en-0.6b"
)
MLX_ENGINES = ("mlx-tiny", "mlx-turbo")
PARAKEET_ENGINES = {"parakeet-unified": "unified"}
PARAKEET_REQUIRED_ASSETS = (
    "parakeet_unified_encoder_int8.mlmodelc",
    "parakeet_unified_decoder.mlmodelc",
    "parakeet_unified_joint_decision_single_step.mlmodelc",
    "vocab.json",
    "metadata.json",
)
PARAKEET_CLI_TIMEOUT_SECONDS = 30 * 60
PARAKEET_HELPER_STARTUP_TIMEOUT_SECONDS = 5 * 60
PARAKEET_HELPER_SAMPLE_TIMEOUT_SECONDS = 2 * 60
PARAKEET_HELPER_CLEANUP_TIMEOUT_SECONDS = 5
PARAKEET_HELPER_MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_DATASET_LABEL = "librispeech-test-clean"
FORMATTING_PUNCTUATION = ".,?!:;"
FORMATTING_WRAPPER_CHARACTERS = "\"'“”‘’()[]{}«»"
FORMATTING_MINIMUM_PUNCTUATED_TOKEN_RATIO = 0.02
FORMATTING_UNAVAILABLE_UNPUNCTUATED = "unavailable — references unpunctuated"
FORMATTING_UNAVAILABLE_SINGLE_CASE = "unavailable — references single-case"
FORMATTING_UNAVAILABLE_EMPTY = "unavailable — no reference text"


@dataclass(frozen=True)
class Sample:
    utterance_id: str
    audio_path: Path
    reference: str


@dataclass(frozen=True)
class ModelSpec:
    engine: str
    model_id: str
    revision: str
    runtime_role: str


def load_model_specs(
        path: Path = DEFAULT_MODEL_SCORECARD) -> dict[str, ModelSpec]:
    """Load exact benchmark targets from the reviewed model scorecard."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict)
            or payload.get("schema_version") not in (1, 2)
            or not isinstance(candidates, list)):
        raise ValueError("unsupported model scorecard")
    specs: dict[str, ModelSpec] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("invalid model scorecard candidate")
        values = [
            candidate.get("benchmark_engine"), candidate.get("model_id"),
            candidate.get("revision"), candidate.get("runtime_role"),
        ]
        if any(not isinstance(value, str) or not value.strip()
               for value in values):
            raise ValueError("model benchmark target is incomplete")
        engine, model_id, revision, runtime_role = values
        if engine in specs:
            raise ValueError(f"duplicate model benchmark engine: {engine}")
        if len(revision) != 40 or any(
                character not in "0123456789abcdef" for character in revision):
            raise ValueError(f"model revision must be an immutable SHA: {engine}")
        specs[engine] = ModelSpec(
            engine=engine, model_id=model_id, revision=revision,
            runtime_role=runtime_role)
    if not specs:
        raise ValueError("model scorecard has no benchmark targets")
    return specs


def resolve_mlx_snapshot(spec: ModelSpec, downloader=None) -> str:
    """Resolve an MLX artifact by immutable reviewed repository revision."""
    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download
    return str(downloader(repo_id=spec.model_id, revision=spec.revision))


def execution_model_provenance(
        spec: ModelSpec, *, executor: str,
        revision_status: str,
        preflight_status: str) -> dict[str, str | bool | None]:
    """Separate the reviewed target from what an executor actually proved."""
    allowed_statuses = {
        "verified-immutable-snapshot",
        "unverified-external-executor",
        "unverified-helper-runtime-unattested",
    }
    if revision_status not in allowed_statuses:
        raise ValueError(f"unsupported revision status: {revision_status}")
    resolved = revision_status == "verified-immutable-snapshot"
    return {
        "requested_model_id": spec.model_id,
        "requested_model_revision": spec.revision,
        "resolved_model_id": spec.model_id if resolved else None,
        "resolved_model_revision": spec.revision if resolved else None,
        "model_revision_status": revision_status,
        "model_preflight_status": preflight_status,
        "runtime_role": spec.runtime_role,
        "executor": executor,
    }


class BoundedJSONLineReader:
    """Read newline-delimited helper JSON with strict time and size bounds."""

    def __init__(self, stream, *, maximum_bytes: int =
                 PARAKEET_HELPER_MAX_RESPONSE_BYTES):
        if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        self._fd = stream.fileno()
        self._maximum_bytes = maximum_bytes
        self._buffer = bytearray()

    def read(self, *, timeout: float) -> dict:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < float(timeout) <= 30 * 60):
            raise ValueError("protocol timeout must be between 0 and 1800 seconds")
        deadline = time.monotonic() + float(timeout)
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                if newline > self._maximum_bytes:
                    raise RuntimeError("helper response exceeded size limit")
                raw = bytes(self._buffer[:newline])
                del self._buffer[:newline + 1]
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RuntimeError("helper returned invalid JSON") from error
                if not isinstance(value, dict):
                    raise RuntimeError("helper response must be a JSON object")
                return value
            if len(self._buffer) > self._maximum_bytes:
                raise RuntimeError("helper response exceeded size limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("helper protocol response timed out")
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                raise TimeoutError("helper protocol response timed out")
            capacity = max(
                1, min(4096, self._maximum_bytes + 1 - len(self._buffer)))
            chunk = os.read(self._fd, capacity)
            if not chunk:
                raise RuntimeError("helper closed before returning a response")
            self._buffer.extend(chunk)


def _cleanup_helper_process(
        process, *, timeout: float =
        PARAKEET_HELPER_CLEANUP_TIMEOUT_SECONDS) -> None:
    """Close, terminate, then kill a helper without allowing cleanup to hang."""
    try:
        process.stdin.close()
    except (AttributeError, OSError, ValueError):
        pass

    def wait_once() -> bool:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        except (AttributeError, OSError, ValueError):
            return True
        return True

    try:
        if wait_once():
            return
        try:
            process.terminate()
        except (AttributeError, OSError):
            pass
        if not wait_once():
            try:
                process.kill()
            except (AttributeError, OSError):
                pass
            wait_once()
    finally:
        try:
            process.stdout.close()
        except (AttributeError, OSError, ValueError):
            pass


def verify_installed_parakeet_revision(
        spec: ModelSpec,
        model_dir: Path = DEFAULT_PARAKEET_MODEL_DIR) -> str:
    """Require every helper asset sidecar to name the reviewed revision."""
    metadata_root = model_dir / ".cache" / "huggingface" / "download"
    for relative in PARAKEET_REQUIRED_ASSETS:
        target = model_dir / relative
        paths = [target] if target.is_file() else (
            list(target.rglob("*")) if target.is_dir() else [])
        files = [path for path in paths if path.is_file()]
        if not files:
            raise RuntimeError(f"Parakeet model asset is missing: {relative}")
        for path in files:
            metadata = metadata_root / path.relative_to(model_dir)
            metadata = Path(f"{metadata}.metadata")
            try:
                revision = metadata.read_text(encoding="utf-8").splitlines()[0]
            except (OSError, IndexError) as error:
                raise RuntimeError(
                    f"Parakeet revision metadata is missing: {path.name}") \
                    from error
            if revision != spec.revision:
                raise RuntimeError(
                    f"Parakeet model revision drift: {path.name} is {revision}")
    return spec.revision


def harness_provenance(
        scorecard: Path = DEFAULT_MODEL_SCORECARD) -> dict[str, str | None]:
    """Describe the exact benchmark harness and evidence inputs."""
    script = Path(__file__).resolve()
    scorecard = scorecard.expanduser().resolve()
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=HERE, check=True,
            capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    try:
        scorecard_name = scorecard.relative_to(HERE).as_posix()
    except ValueError:
        scorecard_name = scorecard.name
    return {
        "script": script.name,
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "model_scorecard": scorecard_name,
        "model_scorecard_sha256": hashlib.sha256(
            scorecard.read_bytes()).hexdigest(),
        "git_revision": revision,
        "python": platform.python_version(),
    }


def load_references(dataset: Path) -> dict[str, str]:
    references: dict[str, str] = {}
    for transcript in sorted(dataset.glob("*/*/*.trans.txt")):
        for line in transcript.read_text(encoding="utf-8").splitlines():
            utterance_id, separator, text = line.strip().partition(" ")
            if separator and text:
                references[utterance_id] = text
    return references


def load_manifest_samples(manifest: Path) -> list[Sample]:
    """Load a JSONL manifest of punctuated, cased references.

    Each line is one object: {"id": ..., "audio": ..., "text": ...}.  The
    "audio" path resolves relative to the manifest file; "id" defaults to the
    audio file stem.  Reference text keeps its punctuation and casing so the
    opt-in formatting scoring has something honest to compare against.
    """
    samples: list[Sample] = []
    seen: set[str] = set()
    for number, line in enumerate(
            manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid manifest JSON on line {number}") from error
        if not isinstance(entry, dict):
            raise ValueError(f"manifest line {number} must be a JSON object")
        audio = entry.get("audio")
        text = entry.get("text")
        if not isinstance(audio, str) or not audio.strip():
            raise ValueError(f"manifest line {number} needs an audio path")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"manifest line {number} needs reference text")
        audio_path = Path(audio.strip())
        if not audio_path.is_absolute():
            audio_path = manifest.parent / audio_path
        if not audio_path.is_file():
            raise ValueError(
                f"manifest line {number} audio file is missing: {audio_path}")
        utterance_id = entry.get("id", audio_path.stem)
        if not isinstance(utterance_id, str) or not utterance_id.strip():
            raise ValueError(f"manifest line {number} has an invalid id")
        utterance_id = utterance_id.strip()
        if utterance_id in seen:
            raise ValueError(f"duplicate manifest utterance id: {utterance_id}")
        seen.add(utterance_id)
        samples.append(Sample(utterance_id, audio_path, text.strip()))
    if not samples:
        raise ValueError(f"manifest has no samples: {manifest}")
    return samples


Item = TypeVar("Item")


def evenly_spaced(items: Sequence[Item], limit: int | None) -> list[Item]:
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
    if dataset.is_file():
        return evenly_spaced(load_manifest_samples(dataset), limit)
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


def formatting_tokens(text: str) -> list[str]:
    """Whitespace tokens with curly quotes mapped, formatting preserved."""
    text = (text.replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"'))
    return text.split()


def split_formatting_token(token: str) -> tuple[str, str]:
    """Split one token into (case-preserved core, trailing scored marks).

    Wrapper characters (quotes, brackets) never score; trailing marks are the
    scored punctuation attached after the word, so '"Stop."' yields
    ("Stop", ".") and 'said,"' yields ("said", ",").
    """
    stripped = token.strip(FORMATTING_WRAPPER_CHARACTERS)
    index = len(stripped)
    trailing: list[str] = []
    while index > 0:
        character = stripped[index - 1]
        if character in FORMATTING_PUNCTUATION:
            trailing.append(character)
        elif character not in FORMATTING_WRAPPER_CHARACTERS:
            break
        index -= 1
    core = stripped[:index].strip(
        FORMATTING_WRAPPER_CHARACTERS + FORMATTING_PUNCTUATION)
    return core, "".join(reversed(trailing))


def align_tokens(
        reference: Sequence[str],
        hypothesis: Sequence[str]) -> list[tuple[int | None, int | None]]:
    """Return a Levenshtein alignment path as (ref_index, hyp_index) pairs.

    None marks the missing side of an insertion or deletion.  Ties prefer the
    diagonal so equal tokens stay paired; the backtrace is deterministic.
    Memory is O(n*m), which is fine at utterance scale.
    """
    rows, columns = len(reference), len(hypothesis)
    cost = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        cost[row][0] = row
    for column in range(1, columns + 1):
        cost[0][column] = column
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            cost[row][column] = min(
                cost[row - 1][column - 1]
                + (reference[row - 1] != hypothesis[column - 1]),
                cost[row - 1][column] + 1,
                cost[row][column - 1] + 1,
            )
    pairs: list[tuple[int | None, int | None]] = []
    row, column = rows, columns
    while row > 0 or column > 0:
        if (row > 0 and column > 0
                and cost[row][column] == cost[row - 1][column - 1]
                + (reference[row - 1] != hypothesis[column - 1])):
            pairs.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif row > 0 and cost[row][column] == cost[row - 1][column] + 1:
            pairs.append((row - 1, None))
            row -= 1
        else:
            pairs.append((None, column - 1))
            column -= 1
    pairs.reverse()
    return pairs


def score_formatting_records(records: Iterable[dict]) -> dict | str:
    """Score punctuation and casing, or say plainly why that is impossible.

    Cased WER compares raw whitespace tokens exactly, punctuation attached.
    Punctuation precision/recall/F1 and the capitalization match rate are
    conditioned on aligned equal words (same token after stripping case and
    punctuation), so recognition errors are not double-counted as formatting
    errors.  References that carry no formatting produce an explicit
    unavailable string instead of misleading zeros.
    """
    prepared: list[tuple[list[str], list[str]]] = []
    reference_tokens_total = 0
    reference_tokens_punctuated = 0
    has_upper = False
    has_lower = False
    for record in records:
        reference_text = str(record["ref"])
        reference_tokens = formatting_tokens(reference_text)
        if not reference_tokens:
            continue
        hypothesis_tokens = formatting_tokens(str(record.get("hyp", "")))
        reference_tokens_total += len(reference_tokens)
        reference_tokens_punctuated += sum(
            1 for token in reference_tokens
            if split_formatting_token(token)[1])
        has_upper = has_upper or any(
            character.isupper() for character in reference_text)
        has_lower = has_lower or any(
            character.islower() for character in reference_text)
        prepared.append((reference_tokens, hypothesis_tokens))
    if not prepared:
        return FORMATTING_UNAVAILABLE_EMPTY
    if (reference_tokens_punctuated / reference_tokens_total
            < FORMATTING_MINIMUM_PUNCTUATED_TOKEN_RATIO):
        return FORMATTING_UNAVAILABLE_UNPUNCTUATED
    if not (has_upper and has_lower):
        return FORMATTING_UNAVAILABLE_SINGLE_CASE

    cased_edits = 0
    cased_reference_tokens = 0
    aligned_equal_tokens = 0
    case_matches = 0
    reference_marks = 0
    hypothesis_marks = 0
    matched_marks = 0
    for reference_tokens, hypothesis_tokens in prepared:
        cased_edits += edit_distance(reference_tokens, hypothesis_tokens)
        cased_reference_tokens += len(reference_tokens)
        reference_split = [
            split_formatting_token(token) for token in reference_tokens]
        hypothesis_split = [
            split_formatting_token(token) for token in hypothesis_tokens]
        reference_keys = [core.casefold() for core, _ in reference_split]
        hypothesis_keys = [core.casefold() for core, _ in hypothesis_split]
        for ref_index, hyp_index in align_tokens(
                reference_keys, hypothesis_keys):
            if ref_index is None or hyp_index is None:
                continue
            key = reference_keys[ref_index]
            if not key or key != hypothesis_keys[hyp_index]:
                continue
            reference_core, reference_trailing = reference_split[ref_index]
            hypothesis_core, hypothesis_trailing = hypothesis_split[hyp_index]
            aligned_equal_tokens += 1
            case_matches += int(reference_core == hypothesis_core)
            reference_counter = Counter(reference_trailing)
            hypothesis_counter = Counter(hypothesis_trailing)
            reference_marks += sum(reference_counter.values())
            hypothesis_marks += sum(hypothesis_counter.values())
            matched_marks += sum(
                (reference_counter & hypothesis_counter).values())

    precision = (100 * matched_marks / hypothesis_marks
                 if hypothesis_marks else None)
    recall = (100 * matched_marks / reference_marks
              if reference_marks else None)
    if precision is not None and recall is not None and precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    elif reference_marks or hypothesis_marks:
        f1 = 0.0
    else:
        f1 = None
    return {
        "cased_wer_pct": round(
            100 * cased_edits / cased_reference_tokens, 4),
        "punctuation_precision_pct": round(precision, 2)
        if precision is not None else None,
        "punctuation_recall_pct": round(recall, 2)
        if recall is not None else None,
        "punctuation_f1_pct": round(f1, 2) if f1 is not None else None,
        "capitalization_match_pct": round(
            100 * case_matches / aligned_equal_tokens, 2)
        if aligned_equal_tokens else None,
        "aligned_equal_tokens": aligned_equal_tokens,
        "reference_punctuation_marks": reference_marks,
        "hypothesis_punctuation_marks": hypothesis_marks,
        "matched_punctuation_marks": matched_marks,
        "reference_punctuated_token_pct": round(
            100 * reference_tokens_punctuated / reference_tokens_total, 2),
    }


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


def summarize_model_run(
        records: Iterable[dict], spec: ModelSpec, *, executor: str,
        revision_status: str, preflight_status: str, tokenize=None,
        formatting: bool = False) -> dict:
    records = list(records)
    summary = score_records(records, tokenize=tokenize)
    if formatting:
        summary["formatting_scoring"] = score_formatting_records(records)
    summary.update(execution_model_provenance(
        spec, executor=executor, revision_status=revision_status,
        preflight_status=preflight_status))
    return summary


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


def run_mlx(spec: ModelSpec, samples: Sequence[Sample], *,
            dataset_label: str = DEFAULT_DATASET_LABEL) -> list[dict]:
    if platform.system() != "Darwin":
        raise RuntimeError("MLX benchmark engines require macOS")
    import mlx_whisper

    engine = spec.engine
    model = resolve_mlx_snapshot(spec)
    provenance = execution_model_provenance(
        spec, executor="mlx-whisper",
        revision_status="verified-immutable-snapshot",
        preflight_status="not-applicable")
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
            "dataset": dataset_label,
            "engine": engine,
            **provenance,
            "audio_s": round(len(audio) / 16_000, 4),
            "proc_s": round(elapsed, 4),
        })
        print(f"[{engine}] {index}/{len(samples)}", flush=True)
    return records


def run_parakeet(
        spec: ModelSpec, samples: Sequence[Sample], cli: Path, *,
        dataset_label: str = DEFAULT_DATASET_LABEL) -> list[dict]:
    engine = spec.engine
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
    provenance = execution_model_provenance(
        spec, executor="macparakeet-cli",
        revision_status="unverified-external-executor",
        preflight_status="not-supported")
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, env=environment,
            timeout=PARAKEET_CLI_TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started
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
                "dataset": dataset_label,
                "engine": engine,
                **provenance,
                "audio_s": round(audio_s, 4),
                # The CLI loads once for the batch. Attribute batch wall time
                # proportionally so aggregate RTFx remains exact.
                "proc_s": round(elapsed * audio_s / total_audio, 4),
            })
        return records
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_parrot_helper(
    spec: ModelSpec, samples: Sequence[Sample], helper: Path,
    *, model_dir: Path = DEFAULT_PARAKEET_MODEL_DIR,
    reader_factory=BoundedJSONLineReader,
    dataset_label: str = DEFAULT_DATASET_LABEL,
) -> list[dict]:
    """Drive Whisper Face's shipping RAM-only helper protocol."""
    engine = spec.engine
    verify_installed_parakeet_revision(spec, model_dir)
    provenance = execution_model_provenance(
        spec, executor="whisper-face-parakeet-helper",
        revision_status="unverified-helper-runtime-unattested",
        preflight_status="installed-sidecar-revision-matched")
    process = subprocess.Popen(
        [str(helper), "--server"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=None, bufsize=0)
    try:
        reader = reader_factory(process.stdout)
        ready = reader.read(timeout=PARAKEET_HELPER_STARTUP_TIMEOUT_SECONDS)
        if not ready.get("ready"):
            raise RuntimeError("Parrot helper did not become ready")
        records = []
        for index, sample in enumerate(samples, 1):
            audio = load_audio(sample.audio_path)
            started = time.monotonic()
            process.stdin.write(struct.pack("<Q", len(audio)))
            process.stdin.write(memoryview(audio).cast("B"))
            process.stdin.flush()
            response = reader.read(timeout=PARAKEET_HELPER_SAMPLE_TIMEOUT_SECONDS)
            elapsed = time.monotonic() - started
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "helper error")))
            records.append({
                "id": sample.utterance_id,
                "ref": sample.reference,
                "hyp": str(response.get("text", "")).strip(),
                "dataset": dataset_label,
                "engine": engine,
                **provenance,
                "audio_s": round(len(audio) / 16_000, 4),
                "proc_s": round(elapsed, 4),
            })
            print(f"[{engine}] {index}/{len(samples)}", flush=True)
        return records
    finally:
        _cleanup_helper_process(process)


def write_records(path: Path, records: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _formatting_report_line(value: dict | str) -> str:
    if isinstance(value, str):
        return value

    def shown(number) -> str:
        return "n/a" if number is None else f"{number:.2f}"

    return (
        f"cased WER {shown(value['cased_wer_pct'])}%  "
        f"punctuation P/R/F1 {shown(value['punctuation_precision_pct'])}/"
        f"{shown(value['punctuation_recall_pct'])}/"
        f"{shown(value['punctuation_f1_pct'])}%  "
        f"case match {shown(value['capitalization_match_pct'])}%")


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
    formatted = [
        summary for summary in summaries if "formatting_scoring" in summary]
    if formatted:
        print("\nFORMATTING SCORING (opt-in; normalized WER stays primary)")
        for summary in formatted:
            print(f"{summary['engine']:20s} "
                  f"{_formatting_report_line(summary['formatting_scoring'])}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--engines", nargs="+", required=True,
        choices=MLX_ENGINES + tuple(PARAKEET_ENGINES))
    parser.add_argument("--macparakeet-cli", type=Path)
    parser.add_argument("--parrot-helper", type=Path)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--scorecard", type=Path, default=DEFAULT_MODEL_SCORECARD,
        help="reviewed exact model repositories and revisions")
    parser.add_argument(
        "--formatting-scoring", action="store_true",
        help="additionally score punctuation and casing (cased WER, "
             "punctuation F1, capitalization match) against punctuated, "
             "cased references; reports unavailable instead of zeros when "
             "references carry no formatting")
    args = parser.parse_args()

    scorecard = args.scorecard.expanduser().resolve()
    model_specs = load_model_specs(scorecard)
    missing = [engine for engine in args.engines if engine not in model_specs]
    if missing:
        raise SystemExit(
            "selected engine is not defined by the reviewed model scorecard: "
            + ", ".join(missing))
    dataset = args.dataset.expanduser().resolve()
    if dataset.is_file():
        dataset_label = f"manifest:{dataset.name}"
        dataset_description = f"JSONL manifest {dataset.name}"
    else:
        dataset_label = DEFAULT_DATASET_LABEL
        dataset_description = "LibriSpeech test-clean"
    try:
        samples = select_samples(dataset, args.limit)
    except ValueError as error:
        raise SystemExit(f"dataset error: {error}")
    if not samples:
        raise SystemExit(f"no referenced FLAC files under {args.dataset}")
    print(f"dataset={args.dataset} samples={len(samples)}")

    summaries = []
    for engine in args.engines:
        spec = model_specs[engine]
        if engine in MLX_ENGINES:
            records = run_mlx(spec, samples, dataset_label=dataset_label)
            executor = "mlx-whisper"
            revision_status = "verified-immutable-snapshot"
            preflight_status = "not-applicable"
        else:
            if args.parrot_helper is not None:
                records = run_parrot_helper(
                    spec, samples, args.parrot_helper.expanduser().resolve(),
                    dataset_label=dataset_label)
                executor = "whisper-face-parakeet-helper"
                revision_status = "unverified-helper-runtime-unattested"
                preflight_status = "installed-sidecar-revision-matched"
            elif args.macparakeet_cli is not None:
                records = run_parakeet(
                    spec, samples,
                    args.macparakeet_cli.expanduser().resolve(),
                    dataset_label=dataset_label)
                executor = "macparakeet-cli"
                revision_status = "unverified-external-executor"
                preflight_status = "not-supported"
            else:
                raise SystemExit(
                    f"--parrot-helper or --macparakeet-cli is required for {engine}")
        records_path = args.output_dir / f"{engine}.jsonl"
        write_records(records_path, records)
        summaries.append(summarize_model_run(
            records, spec, executor=executor,
            revision_status=revision_status,
            preflight_status=preflight_status,
            formatting=args.formatting_scoring))

    report = {
        "schema_version": 1,
        "hardware": {
            "machine": platform.machine(),
            "macos": platform.mac_ver()[0],
        },
        "dataset": dataset_description,
        "selection": "deterministic-evenly-spaced",
        "samples": len(samples),
        "formatting_scoring_requested": bool(args.formatting_scoring),
        "harness": harness_provenance(scorecard),
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
