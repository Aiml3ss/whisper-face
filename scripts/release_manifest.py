#!/usr/bin/env python3
"""Create and verify immutable Whisper Face release metadata.

The release scripts intentionally use only Python's standard library so the
manifest can be audited before any project dependencies are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


PRODUCT = "Whisper Face"
SCHEMA_VERSION = 1
DEFAULT_REPOSITORY = "https://github.com/Aiml3ss/whisper-face"
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
REVISION = re.compile(r"^[0-9a-f]{40}$")
WINDOWS_BUNDLE_SUFFIX = "-windows-x64.zip"

# Both platforms install by running the shipped source bundle in place; only
# the clickable entry point and the no-change verification command differ.
_SHARED_INSTALLATION = {
    "automatic_cross_checkout_migration": False,
    "preserves_private_state": False,
    "same_checkout_reinstall_preserves_private_state": True,
    "separate_checkout_requires_manual_private_state_migration": True,
    "strategy": "source-bundle-in-place",
}
MACOS_INSTALLATION = {
    **_SHARED_INSTALLATION,
    "entrypoint": "Install.command",
    "verification": "./setup.sh --verify",
}
WINDOWS_INSTALLATION = {
    **_SHARED_INSTALLATION,
    "entrypoint": "Install.cmd",
    "verification": ".\\setup.ps1 --verify",
}
MINIMUM_OS = {
    "Install.command": ("minimum_macos", "14.0"),
    "Install.cmd": ("minimum_windows", "10"),
}


class ManifestError(ValueError):
    """A release manifest is malformed or does not match its artifacts."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_semver(value: str, label: str = "version") -> str:
    if not SEMVER.fullmatch(value):
        raise ManifestError(f"{label} must be a SemVer value: {value!r}")
    return value


def _require_revision(value: str, label: str = "revision") -> str:
    normalized = value.casefold()
    if not REVISION.fullmatch(normalized):
        raise ManifestError(f"{label} must be a full 40-character Git SHA-1")
    return normalized


def _require_https(value: str, label: str) -> str:
    if not value.startswith("https://"):
        raise ManifestError(f"{label} must use https://")
    return value.rstrip("/")


def _published_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ManifestError("published-at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ManifestError("published-at must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _artifact_kind(path: Path) -> tuple[str, str, list[str]]:
    """Classify a release artifact by its published name, not its extension.

    The Windows bundle and the corresponding-source archive are both ZIPs, so
    the suffix alone cannot tell them apart. Keying off the release filename
    keeps a Windows installer from being published, and trusted, as a plain
    source archive.
    """
    lower = path.name.casefold()
    if lower.endswith(".dmg"):
        return "macos-disk-image", "installer", ["arm64"]
    if lower.endswith(WINDOWS_BUNDLE_SUFFIX):
        return "windows-source-bundle", "installer", ["x64"]
    if lower.endswith(".zip"):
        return "source-archive", "corresponding-source", ["any"]
    raise ManifestError(f"unsupported release artifact type: {path.name}")


def _installation(kinds: set[str]) -> dict:
    """Return the install contract the artifact set actually describes."""
    windows = "windows-source-bundle" in kinds
    macos = "macos-disk-image" in kinds
    if windows and macos:
        raise ManifestError(
            "a release manifest describes one platform's install path; "
            "package Windows and macOS artifacts into separate manifests"
        )
    return dict(WINDOWS_INSTALLATION if windows else MACOS_INSTALLATION)


def create_manifest(args: argparse.Namespace) -> dict:
    version = _require_semver(args.version)
    revision = _require_revision(args.revision)
    repository = _require_https(args.repository, "repository")
    download_base = _require_https(args.download_base_url, "download-base-url")

    artifacts: list[dict] = []
    seen_names: set[str] = set()
    signed_names = set(args.signed_artifact)
    notarized_names = set(args.notarized_artifact)
    supplied_names = {Path(value).name for value in args.artifact}
    unknown_trust = (signed_names | notarized_names) - supplied_names
    if unknown_trust:
        raise ManifestError(
            "trust metadata names an unknown artifact: "
            + ", ".join(sorted(unknown_trust))
        )
    if not notarized_names.issubset(signed_names):
        raise ManifestError("a notarized artifact must also be marked signed")

    for raw_path in args.artifact:
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise ManifestError(f"artifact is not a file: {path}")
        if path.name in seen_names:
            raise ManifestError(f"duplicate artifact name: {path.name}")
        seen_names.add(path.name)
        kind, role, architectures = _artifact_kind(path)
        artifacts.append(
            {
                "architectures": architectures,
                "kind": kind,
                "name": path.name,
                "notarized": path.name in notarized_names,
                "role": role,
                "sha256": _sha256(path),
                "signed": path.name in signed_names,
                "size": path.stat().st_size,
                "url": f"{download_base}/{quote(path.name)}",
            }
        )
    if not artifacts:
        raise ManifestError("at least one artifact is required")

    previous_values = (
        args.previous_version,
        args.previous_revision,
        args.previous_manifest_url,
    )
    if any(previous_values) and not all(previous_values):
        raise ManifestError(
            "previous-version, previous-revision, and previous-manifest-url "
            "must be supplied together"
        )
    if all(previous_values):
        rollback = {
            "manifest_url": _require_https(
                args.previous_manifest_url, "previous-manifest-url"
            ),
            "source_revision": _require_revision(
                args.previous_revision, "previous-revision"
            ),
            "strategy": "install-previous-release",
            "supported": True,
            "version": _require_semver(args.previous_version, "previous-version"),
        }
        if rollback["version"] == version:
            raise ManifestError("rollback version must differ from current version")
        if rollback["source_revision"] == revision:
            raise ManifestError("rollback revision must differ from current revision")
    else:
        rollback = {
            "reason": "No previous signed release was supplied.",
            "supported": False,
        }

    installation = _installation({item["kind"] for item in artifacts})
    minimum_key, minimum_value = MINIMUM_OS[installation["entrypoint"]]

    return {
        "artifacts": artifacts,
        "channel": args.channel,
        "installation": installation,
        minimum_key: minimum_value,
        "product": PRODUCT,
        "published_at": _published_at(args.published_at),
        "rollback": rollback,
        "schema_version": SCHEMA_VERSION,
        "source_offer": {
            "archive": f"{repository}/archive/{revision}.tar.gz",
            "license": "AGPL-3.0-only",
            "license_policy": f"{repository}/blob/{revision}/LICENSE_POLICY.md",
            "repository": repository,
            "revision": revision,
            "tree": f"{repository}/tree/{revision}",
        },
        "version": version,
    }


def _load_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"could not read manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    return payload


def verify_manifest(path: Path, artifact_dir: Path) -> dict:
    payload = _load_manifest(path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("unsupported manifest schema")
    if payload.get("product") != PRODUCT:
        raise ManifestError("manifest product does not match Whisper Face")
    _require_semver(str(payload.get("version", "")))
    source_offer = payload.get("source_offer")
    if not isinstance(source_offer, dict):
        raise ManifestError("source_offer must be an object")
    _require_revision(str(source_offer.get("revision", "")), "source revision")
    if source_offer.get("license") != "AGPL-3.0-only":
        raise ManifestError("manifest must offer the current AGPL source")
    for key in ("archive", "license_policy", "repository", "tree"):
        _require_https(str(source_offer.get(key, "")), f"source_offer.{key}")

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ManifestError("manifest must list at least one artifact")
    seen_names: set[str] = set()
    kinds: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ManifestError("each artifact must be an object")
        name = artifact.get("name")
        if not isinstance(name, str) or name != Path(name).name or name in seen_names:
            raise ManifestError("artifact names must be unique safe basenames")
        seen_names.add(name)
        expected_kind, expected_role, expected_architectures = _artifact_kind(
            Path(name)
        )
        if artifact.get("kind") != expected_kind or artifact.get("role") != expected_role:
            raise ManifestError(f"artifact kind/role mismatch: {name}")
        kinds.add(expected_kind)
        if artifact.get("architectures") != expected_architectures:
            raise ManifestError(f"artifact architecture mismatch: {name}")
        local = artifact_dir / name
        if not local.is_file():
            raise ManifestError(f"missing artifact: {name}")
        if local.stat().st_size != artifact.get("size"):
            raise ManifestError(f"artifact size mismatch: {name}")
        if _sha256(local) != artifact.get("sha256"):
            raise ManifestError(f"artifact digest mismatch: {name}")
        if artifact.get("notarized") and not artifact.get("signed"):
            raise ManifestError(f"notarized artifact is not signed: {name}")
        _require_https(str(artifact.get("url", "")), f"artifact URL for {name}")

    installation = payload.get("installation")
    if not isinstance(installation, dict):
        raise ManifestError("installation must be an object")
    if installation.get("preserves_private_state") is not False:
        raise ManifestError("private-state preservation must not be unconditional")
    if installation.get("same_checkout_reinstall_preserves_private_state") is not True:
        raise ManifestError("same-checkout state preservation must be explicit")
    if (
        installation.get("separate_checkout_requires_manual_private_state_migration")
        is not True
    ):
        raise ManifestError("separate-checkout migration requirement is missing")
    # A Windows bundle published under the Mac install contract would tell the
    # person who downloaded it to double-click a file it does not contain.
    expected_installation = _installation(kinds)
    if installation != expected_installation:
        raise ManifestError(
            "installation contract does not match the artifacts: expected "
            f"entrypoint {expected_installation['entrypoint']!r} and "
            f"verification {expected_installation['verification']!r}"
        )
    minimum_key, minimum_value = MINIMUM_OS[expected_installation["entrypoint"]]
    if payload.get(minimum_key) != minimum_value:
        raise ManifestError(f"manifest must declare {minimum_key} {minimum_value}")
    return payload


def create_source_metadata(args: argparse.Namespace) -> dict:
    revision = _require_revision(args.revision)
    version = _require_semver(args.version)
    repository = _require_https(args.repository, "repository")
    return {
        "license": "AGPL-3.0-only",
        "product": PRODUCT,
        "schema_version": SCHEMA_VERSION,
        "source_archive": f"{repository}/archive/{revision}.tar.gz",
        "source_revision": revision,
        "source_tree": f"{repository}/tree/{revision}",
        "version": version,
    }


def create_checksums(args: argparse.Namespace) -> None:
    paths = [Path(value).resolve() for value in args.file]
    if not paths:
        raise ManifestError("at least one file is required")
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise ManifestError("checksum file names must be unique")
    for path in paths:
        if not path.is_file():
            raise ManifestError(f"checksum input is not a file: {path}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{_sha256(path)}  {path.name}\n" for path in sorted(paths)]
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an update manifest")
    create.add_argument("--version", required=True)
    create.add_argument("--revision", required=True)
    create.add_argument("--artifact", action="append", default=[], required=True)
    create.add_argument("--signed-artifact", action="append", default=[])
    create.add_argument("--notarized-artifact", action="append", default=[])
    create.add_argument("--output", required=True)
    create.add_argument("--channel", default="stable", choices=("stable", "preview"))
    create.add_argument("--published-at")
    create.add_argument("--repository", default=DEFAULT_REPOSITORY)
    create.add_argument("--download-base-url", required=True)
    create.add_argument("--previous-version")
    create.add_argument("--previous-revision")
    create.add_argument("--previous-manifest-url")

    verify = commands.add_parser("verify", help="verify a manifest and artifacts")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--artifact-dir", required=True)

    source = commands.add_parser(
        "source-metadata", help="write metadata inside the source bundle"
    )
    source.add_argument("--version", required=True)
    source.add_argument("--revision", required=True)
    source.add_argument("--repository", default=DEFAULT_REPOSITORY)
    source.add_argument("--output", required=True)

    checksums = commands.add_parser("checksums", help="write SHA256SUMS")
    checksums.add_argument("--file", action="append", default=[], required=True)
    checksums.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            _atomic_json(Path(args.output), create_manifest(args))
        elif args.command == "verify":
            verify_manifest(Path(args.manifest), Path(args.artifact_dir))
            print(f"verified {args.manifest}")
        elif args.command == "source-metadata":
            _atomic_json(Path(args.output), create_source_metadata(args))
        elif args.command == "checksums":
            create_checksums(args)
        else:  # pragma: no cover - argparse prevents this path
            raise AssertionError(args.command)
    except ManifestError as exc:
        print(f"release metadata error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
