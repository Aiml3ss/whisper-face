"""Local-only, content-free support bundle writer for the native Mac UI."""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping


BUNDLE_KIND = "whisper-face/support-bundle"
BUNDLE_SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 64 * 1024
MAX_MODELS = 4
MAX_COUNT = 1_000_000
MAX_LATENCY_MS = 3_600_000.0

_STATUS_VALUES = frozenset({
    "running", "ready", "granted", "installed", "starting", "checking",
    "unavailable", "unknown",
})
_MODEL_FAMILIES = frozenset({"parakeet", "whisper", "qwen", "unknown"})
_MODES = frozenset({
    "capture", "compose", "edit", "reply", "command", "code", "unknown",
})
_BUILDS = frozenset({"local-checkout", "unknown"})


class SupportBundleError(ValueError):
    """The selected destination or diagnostic projection is unsafe."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enum(value: Any, allowed: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = float(value)
    if not math.isfinite(number):
        return 0
    return min(MAX_COUNT, max(0, int(number)))


def _optional_number(value: Any, *, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= maximum:
        return None
    return round(number, 3)


def build_support_bundle(snapshot_text: str) -> dict[str, Any]:
    """Rebuild a closed support schema from the GUI's diagnostic projection."""
    if not isinstance(snapshot_text, str) or len(snapshot_text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise SupportBundleError("support snapshot is invalid")
    try:
        snapshot = json.loads(snapshot_text)
    except (TypeError, ValueError) as error:
        raise SupportBundleError("support snapshot is invalid") from error
    if not isinstance(snapshot, Mapping):
        raise SupportBundleError("support snapshot is invalid")

    health = _mapping(snapshot.get("health"))
    permissions = _mapping(snapshot.get("permissions"))
    last_result = _mapping(snapshot.get("last_result"))
    models: list[dict[str, str]] = []
    raw_models = snapshot.get("models")
    if isinstance(raw_models, list):
        for model in raw_models[:MAX_MODELS]:
            value = _mapping(model)
            models.append({
                "family": _enum(value.get("family"), _MODEL_FAMILIES),
                "status": _enum(value.get("status"), _STATUS_VALUES),
            })
    models.sort(key=lambda item: (item["family"], item["status"]))

    return {
        "kind": BUNDLE_KIND,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "app": {"build": _enum(snapshot.get("build"), _BUILDS)},
        "runtime": {
            "health": {
                "service_status": _enum(
                    health.get("service_status"), _STATUS_VALUES),
                "microphone_status": _enum(
                    health.get("microphone_status"), _STATUS_VALUES),
            },
            "permissions": {
                "accessibility_status": _enum(
                    permissions.get("accessibility_status"), _STATUS_VALUES),
            },
        },
        "models": models,
        "last_result": {
            "available": last_result.get("available") is True,
            "engine": _enum(last_result.get("engine"), _MODEL_FAMILIES),
            "mode": _enum(last_result.get("mode"), _MODES),
            "latency_ms": _optional_number(
                last_result.get("latency_ms"), maximum=MAX_LATENCY_MS),
            "word_count": _count(last_result.get("word_count")),
            "confidence": _optional_number(
                last_result.get("confidence"), maximum=1.0),
            "stable_prefix_words": _count(
                last_result.get("stable_prefix_words")),
            "compiler_decisions": _count(
                last_result.get("compiler_decisions")),
            "protected_anchor_count": _count(
                last_result.get("protected_anchor_count")),
            "alternatives_considered": _count(
                last_result.get("alternatives_considered")),
            "cleanup_edits_count": _count(
                last_result.get("cleanup_edits_count")),
            "proof_edits_accepted": _count(
                last_result.get("proof_edits_accepted")),
            "proof_edits_rejected": _count(
                last_result.get("proof_edits_rejected")),
        },
    }


def write_support_bundle(destination: str | Path, snapshot_text: str) -> Path:
    """Atomically save the closed diagnostic bundle at a user-selected path."""
    try:
        path = Path(destination)
    except (TypeError, ValueError) as error:
        raise SupportBundleError("support bundle destination is invalid") from error
    try:
        if (not path.name or path.name in {".", ".."}
                or path.is_symlink() or not path.parent.is_dir()):
            raise SupportBundleError("support bundle destination is invalid")
    except OSError as error:
        raise SupportBundleError("support bundle destination is invalid") from error
    encoded = (json.dumps(build_support_bundle(snapshot_text), indent=2,
                          sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = -1
    temporary_path: Path | None = None
    replaced = False
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.",
                                                dir=path.parent)
        temporary_path = Path(temporary)
        os.fchmod(descriptor, 0o600)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise SupportBundleError("support bundle permissions are unsafe")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        replaced = True
        return path
    except OSError as error:
        raise SupportBundleError("could not save support bundle") from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None and not replaced:
            try:
                temporary_path.unlink()
            except OSError:
                pass
