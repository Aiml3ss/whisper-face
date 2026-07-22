#!/usr/bin/env python3
"""Produce a read-only, fail-closed Whisper Face update plan from local files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import release_manifest


SCHEMA_VERSION = 1
REVISION = re.compile(r"^[0-9a-f]{40}$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
NO_EFFECTS = {
    "checkout_mutation": False,
    "download": False,
    "install": False,
    "launchd_change": False,
    "network": False,
    "source_overwrite": False,
}


class AdviceError(ValueError):
    """Inputs cannot support a safe read-only plan."""


def _version(value: str, label: str) -> tuple[int, int, int, tuple[str, ...]]:
    match = SEMVER.fullmatch(value)
    if not match:
        raise AdviceError(f"invalid-{label}")
    prerelease = tuple((match.group(4) or "").split("."))
    if prerelease == ("",):
        prerelease = ()
    if any(item.isdigit() and len(item) > 1 and item.startswith("0") for item in prerelease):
        raise AdviceError(f"invalid-{label}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def _compare(left: tuple, right: tuple) -> int:
    if left[:3] != right[:3]:
        return (left[:3] > right[:3]) - (left[:3] < right[:3])
    left_pre, right_pre = left[3], right[3]
    if not left_pre or not right_pre:
        return (not left_pre) - (not right_pre)
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        left_numeric, right_numeric = left_item.isdigit(), right_item.isdigit()
        if left_numeric and right_numeric:
            return (int(left_item) > int(right_item)) - (int(left_item) < int(right_item))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_item > right_item) - (left_item < right_item)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _revision(value: str, label: str) -> str:
    value = value.casefold()
    if not REVISION.fullmatch(value):
        raise AdviceError(f"invalid-{label}")
    return value


def _verified_manifest(path: Path, artifact_dir: Path, channel: str) -> dict:
    try:
        payload = release_manifest.verify_manifest(path, artifact_dir)
    except (release_manifest.ManifestError, OSError) as exc:
        raise AdviceError("manifest-or-artifact-verification-failed") from exc
    if payload.get("channel") != channel:
        raise AdviceError("channel-mismatch")
    _version(str(payload.get("version", "")), "manifest-version")
    source = payload.get("source_offer", {})
    _revision(str(source.get("revision", "")), "manifest-revision")
    artifacts = payload.get("artifacts", [])
    roles = {item.get("role") for item in artifacts}
    if len(artifacts) != 2 or roles != {"corresponding-source", "installer"}:
        raise AdviceError("required-artifact-set-missing")
    if channel == "stable":
        installers = [item for item in artifacts if item.get("role") == "installer"]
        if len(installers) != 1 or not all(
            item.get("signed") is True and item.get("notarized") is True
            for item in installers
        ):
            raise AdviceError("production-trust-required")
        if shutil.which("codesign") is None or shutil.which("xcrun") is None:
            raise AdviceError("apple-trust-verification-unavailable")
        image = artifact_dir / installers[0]["name"]
        try:
            subprocess.run(
                ["codesign", "--verify", "--strict", "--verbose=2", str(image)],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["xcrun", "stapler", "validate", str(image)],
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise AdviceError("apple-trust-verification-failed") from exc
    return payload


def _base_receipt(current_version: str, current_revision: str, channel: str) -> dict:
    return {
        "channel": channel,
        "current": {"source_revision": current_revision, "version": current_version},
        "decision": "refuse",
        "effects": NO_EFFECTS,
        "execution": "none",
        "reason": "unresolved",
        "schema_version": SCHEMA_VERSION,
    }


def _sanitized_current(version: str, revision: str) -> tuple[str, str]:
    try:
        _version(version, "current-version")
        safe_version = version
    except AdviceError:
        safe_version = "unknown"
    safe_revision = revision.casefold() if REVISION.fullmatch(revision.casefold()) else "unknown"
    return safe_version, safe_revision


def advise(args: argparse.Namespace) -> dict:
    current_version_key = _version(args.current_version, "current-version")
    current_revision = _revision(args.current_revision, "current-revision")
    receipt = _base_receipt(args.current_version, current_revision, args.channel)
    candidate = _verified_manifest(
        Path(args.manifest), Path(args.artifact_dir), args.channel
    )
    candidate_version = str(candidate["version"])
    candidate_revision = str(candidate["source_offer"]["revision"])
    receipt["candidate"] = {
        "artifacts_verified": True,
        "production_trust_verified": args.channel == "stable",
        "source_revision": candidate_revision,
        "version": candidate_version,
    }

    if args.intent == "update":
        comparison = _compare(
            _version(candidate_version, "manifest-version"), current_version_key
        )
        if comparison == 0:
            if candidate_version != args.current_version:
                receipt["reason"] = "equal-precedence-version-conflict"
            elif candidate_revision != current_revision:
                receipt["reason"] = "same-version-revision-conflict"
            else:
                receipt["decision"] = "up-to-date"
                receipt["reason"] = "exact-version-and-revision-match"
        elif comparison > 0:
            if candidate_revision == current_revision:
                receipt["reason"] = "new-version-reuses-current-revision"
            else:
                receipt["decision"] = "upgrade"
                receipt["reason"] = "verified-newer-release"
        else:
            receipt["reason"] = "downgrade-requires-linked-rollback"
        return receipt

    if candidate_version != args.current_version or candidate_revision != current_revision:
        raise AdviceError("rollback-source-is-not-current-release")
    if not args.rollback_manifest or not args.rollback_artifact_dir:
        raise AdviceError("rollback-files-required")
    linkage = candidate.get("rollback")
    if not isinstance(linkage, dict) or linkage.get("supported") is not True:
        raise AdviceError("rollback-linkage-missing")
    if linkage.get("strategy") != "install-previous-release":
        raise AdviceError("rollback-strategy-invalid")
    linked_url = str(linkage.get("manifest_url", ""))
    if urlparse(linked_url).scheme != "https" or not linked_url.endswith(
        "/update-manifest.json"
    ):
        raise AdviceError("rollback-manifest-url-invalid")
    target = _verified_manifest(
        Path(args.rollback_manifest), Path(args.rollback_artifact_dir), args.channel
    )
    target_version = str(target["version"])
    target_revision = str(target["source_offer"]["revision"])
    if (
        linkage.get("version") != target_version
        or linkage.get("source_revision") != target_revision
    ):
        raise AdviceError("rollback-linkage-mismatch")
    if _compare(_version(target_version, "rollback-version"), current_version_key) >= 0:
        raise AdviceError("rollback-target-is-not-older")
    receipt["decision"] = "rollback"
    receipt["reason"] = "verified-linked-rollback"
    receipt["rollback"] = {
        "artifacts_verified": True,
        "production_trust_verified": args.channel == "stable",
        "source_revision": target_revision,
        "version": target_version,
    }
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--current-revision", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--channel", choices=("stable", "preview"), default="stable")
    parser.add_argument("--intent", choices=("update", "rollback"), default="update")
    parser.add_argument("--rollback-manifest")
    parser.add_argument("--rollback-artifact-dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = advise(args)
    except AdviceError as exc:
        safe_version, safe_revision = _sanitized_current(
            args.current_version, args.current_revision
        )
        receipt = _base_receipt(safe_version, safe_revision, args.channel)
        receipt["reason"] = str(exc)
        print(json.dumps(receipt, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
