# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build private acoustic-keyword activation from physical A/B evidence.

The manifest names one private keyword and contains only categorical outcomes;
it contains no audio or surrounding transcript.  The JSON report omits the
keyword and case tokens.  Runtime state is written only after balanced
caller-attested physical evidence and explicit manual review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from acoustic_keyword_activation import (
    ActivationError,
    build_activation_entry,
    upsert_activation,
)
from acoustic_keyword_bias_evaluation import evaluate_keyword_bias
from acoustic_keyword_memory import AcousticKeywordMemory


SCHEMA_VERSION = 1
MANIFEST_KIND = "whisper-face/acoustic-keyword-activation-manifest"
_ROOT_KEYS = frozenset({
    "schema_version", "kind", "keyword", "app_scope", "records",
})


class BenchmarkError(ValueError):
    """Private activation input violated the closed contract."""


def load_inputs(
    manifest_path: Path,
    memory_path: Path,
) -> tuple[Any, tuple[Mapping[str, Any], ...]]:
    try:
        root = json.loads(manifest_path.read_text(encoding="utf-8"))
        memory = AcousticKeywordMemory.loads(
            memory_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BenchmarkError("activation inputs are unavailable or invalid") \
            from exc
    if (not isinstance(root, Mapping) or set(root) != _ROOT_KEYS
            or root["schema_version"] != SCHEMA_VERSION
            or root["kind"] != MANIFEST_KIND
            or not isinstance(root["records"], list)):
        raise BenchmarkError("activation manifest is invalid")
    matches = [
        candidate for candidate in memory.candidates
        if candidate.keyword == root["keyword"]
        and candidate.app_scope == root["app_scope"]
    ]
    if len(matches) != 1:
        raise BenchmarkError("eligible keyword memory is unavailable")
    return matches[0], tuple(root["records"])


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--memory", required=True, type=Path)
    parser.add_argument("--approve-runtime", type=Path)
    parser.add_argument("--confirm-manual-review", action="store_true")
    args = parser.parse_args(argv)
    try:
        candidate, records = load_inputs(args.manifest, args.memory)
        report = evaluate_keyword_bias(candidate, records)
        if args.approve_runtime is not None:
            entry = build_activation_entry(
                candidate, report,
                manual_review_approved=args.confirm_manual_review)
            upsert_activation(args.approve_runtime, entry)
    except (ActivationError, BenchmarkError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_json(report))
    return 0 if report["verdict"] == "keep" else 1


if __name__ == "__main__":
    raise SystemExit(main())
