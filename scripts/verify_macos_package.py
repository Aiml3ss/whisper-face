#!/usr/bin/env python3
"""Stamp and verify the logical contents of Whisper Face Mac packages.

DMG filesystem identifiers and compression metadata are not byte reproducible.
This tool instead makes the staged source tree reproducible, proves the ZIP
holds exactly that tree, and proves the disk image holds only the drag-install
app and a link to Applications, with the app carrying that same ZIP byte for
byte. It uses only the Python standard library so it can run before project
dependencies or Apple credentials are available.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


PRODUCT = "Whisper Face"
RECEIPT_NAME = "PACKAGE-CONTENTS.json"
RELEASE_METADATA_NAME = "RELEASE-METADATA.json"
REPOSITORY = "https://github.com/Aiml3ss/whisper-face.git"
SCHEMA_VERSION = 1
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
REVISION = re.compile(r"^[0-9a-f]{40}$")
LAUNCHER_NAME = "Whisper Face.app"
APPLICATIONS_LINK = "Applications"
VOLUME_BOOKKEEPING = {".fseventsd", ".Spotlight-V100", ".Trashes"}


class PackageError(ValueError):
    """The staged package or one of its containers is malformed."""


def _require_version(value: str) -> str:
    if not SEMVER.fullmatch(value):
        raise PackageError(f"version must be SemVer: {value!r}")
    return value


def _require_revision(value: str) -> str:
    value = value.casefold()
    if not REVISION.fullmatch(value):
        raise PackageError("revision must be a full 40-character Git SHA-1")
    return value


def _run(*arguments: str, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise PackageError(
            f"command failed: {' '.join(arguments)}: {detail.strip()}"
        ) from exc
    return completed.stdout.strip()


def _atomic_json(path: Path, payload: dict) -> None:
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


def _tree_entries(root: Path) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == RECEIPT_NAME:
            continue
        info = path.lstat()
        common = {
            "executable": bool(info.st_mode & stat.S_IXUSR),
            "path": relative,
        }
        if stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            entries.append({**common, "sha256": digest.hexdigest(), "type": "file"})
        elif stat.S_ISDIR(info.st_mode):
            entries.append({**common, "type": "directory"})
        elif stat.S_ISLNK(info.st_mode):
            entries.append({**common, "target": os.readlink(path), "type": "symlink"})
        else:
            raise PackageError(f"unsupported staged file type: {relative}")
    return entries


def _tree_digest(entries: list[dict]) -> str:
    encoded = json.dumps(
        entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_mtimes(root: Path, epoch: int) -> None:
    paths = [root, *root.rglob("*")]
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        try:
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        except (NotImplementedError, OSError) as exc:
            raise PackageError(f"could not normalize timestamp: {path}") from exc


def stamp_tree(root: Path, version: str, revision: str, source_date_epoch: int) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise PackageError(f"staging root is not a directory: {root}")
    version = _require_version(version)
    revision = _require_revision(revision)
    if source_date_epoch < 0:
        raise PackageError("source-date-epoch must be non-negative")
    expected_name = f"Whisper Face {version}"
    if root.name != expected_name:
        raise PackageError(f"staging root must be named {expected_name!r}")
    (root / RECEIPT_NAME).unlink(missing_ok=True)
    entries = _tree_entries(root)
    payload = {
        "entry_count": len(entries),
        "product": PRODUCT,
        "root_name": expected_name,
        "schema_version": SCHEMA_VERSION,
        "source_date_epoch": source_date_epoch,
        "source_revision": revision,
        "tree_sha256": _tree_digest(entries),
        "version": version,
    }
    _atomic_json(root / RECEIPT_NAME, payload)
    _normalize_mtimes(root, source_date_epoch)
    return payload


def verify_tree(root: Path, version: str, revision: str) -> dict:
    root = root.resolve()
    version = _require_version(version)
    revision = _require_revision(revision)
    expected_name = f"Whisper Face {version}"
    if root.name != expected_name or not root.is_dir():
        raise PackageError(f"package must contain directory {expected_name!r}")
    receipt_path = root / RECEIPT_NAME
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"could not read {RECEIPT_NAME}: {exc}") from exc
    if not isinstance(receipt, dict):
        raise PackageError(f"{RECEIPT_NAME} must contain a JSON object")
    expected_fields = {
        "product": PRODUCT,
        "root_name": expected_name,
        "schema_version": SCHEMA_VERSION,
        "source_revision": revision,
        "version": version,
    }
    for key, expected in expected_fields.items():
        if receipt.get(key) != expected:
            raise PackageError(f"package receipt {key} mismatch")
    epoch = receipt.get("source_date_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise PackageError("package receipt source_date_epoch is invalid")
    entries = _tree_entries(root)
    if receipt.get("entry_count") != len(entries):
        raise PackageError("package receipt entry count mismatch")
    if receipt.get("tree_sha256") != _tree_digest(entries):
        raise PackageError("package receipt tree digest mismatch")
    return receipt


def verify_exact_checkout(root: Path, version: str, revision: str) -> None:
    """Prove a staged tree is the untouched checkout of one Git revision.

    Shared with ``scripts/windows_bundle.py``: both platforms ship the same
    exact-source claim, so they must prove it the same way.
    """
    metadata_path = root / RELEASE_METADATA_NAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"could not read {RELEASE_METADATA_NAME}: {exc}") from exc
    if metadata.get("version") != version:
        raise PackageError("release metadata version mismatch")
    if metadata.get("source_revision") != revision:
        raise PackageError("release metadata revision mismatch")
    if _run("git", "-C", str(root), "rev-parse", "HEAD") != revision:
        raise PackageError("packaged Git checkout revision mismatch")
    if _run("git", "-C", str(root), "remote", "get-url", "origin") != REPOSITORY:
        raise PackageError("packaged Git checkout origin is not the public repository")
    if _run("git", "-C", str(root), "rev-parse", "--is-shallow-repository") != "true":
        raise PackageError("packaged Git checkout must contain only shallow metadata")
    try:
        subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", "--no-ext-diff", "HEAD", "--"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackageError("packaged tracked source differs from its revision") from exc
    # The packager's two receipts are ignored by the revision's own
    # .gitignore, so a shipped checkout reads as clean. The in-app updater
    # refuses any checkout that does not.
    status_output = _run(
        "git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"
    )
    if status_output:
        raise PackageError("packaged checkout contains unexpected source files")
    for name in (RECEIPT_NAME, RELEASE_METADATA_NAME):
        if not (root / name).is_file():
            raise PackageError(f"packaged checkout is missing {name}")


def _check_zip_members(archive: Path, expected_root: str) -> None:
    try:
        with zipfile.ZipFile(archive) as handle:
            names = [item.filename for item in handle.infolist()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f"could not inspect source ZIP: {exc}") from exc
    if not names:
        raise PackageError("source ZIP is empty")
    for name in names:
        if "\\" in name:
            raise PackageError(f"source ZIP contains a non-POSIX path: {name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise PackageError(f"source ZIP contains an unsafe path: {name!r}")
        if not path.parts or path.parts[0] != expected_root:
            raise PackageError("source ZIP contains data outside its versioned root")


def _extract_zip(archive: Path, destination: Path) -> None:
    if shutil.which("ditto") is None:
        raise PackageError("ditto is required to verify the macOS source ZIP")
    _run("ditto", "-x", "-k", str(archive), str(destination))


def _attach_disk_image(disk_image: Path) -> tuple[Path, str]:
    if shutil.which("hdiutil") is None:
        raise PackageError("hdiutil is required to verify the macOS disk image")
    try:
        completed = subprocess.run(
            [
                "hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
                "-plist", str(disk_image),
            ],
            capture_output=True,
            check=True,
        )
        payload = plistlib.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException) as exc:
        raise PackageError(f"could not attach disk image: {exc}") from exc
    for entity in payload.get("system-entities", []):
        mount_point = entity.get("mount-point")
        device = entity.get("dev-entry")
        if mount_point and device:
            return Path(mount_point), device
    raise PackageError("attached disk image did not report a mounted filesystem")


def verify_volume_layout(root: Path) -> None:
    """The disk image opens to the app and a link to Applications, and to
    nothing else a person can see. The three hidden entries are bookkeeping
    macOS itself may add to a mounted volume."""
    entries = {path.name for path in root.iterdir()}
    visible = entries - VOLUME_BOOKKEEPING
    if visible != {LAUNCHER_NAME, APPLICATIONS_LINK}:
        unexpected = sorted(visible - {LAUNCHER_NAME, APPLICATIONS_LINK})
        missing = sorted({LAUNCHER_NAME, APPLICATIONS_LINK} - visible)
        raise PackageError(
            "disk image must hold exactly Whisper Face.app and Applications"
            + (f"; unexpected: {', '.join(unexpected)}" if unexpected else "")
            + (f"; missing: {', '.join(missing)}" if missing else ""))
    link = root / APPLICATIONS_LINK
    if not link.is_symlink() or os.readlink(link) != "/Applications":
        raise PackageError("disk image Applications entry must link to /Applications")
    app = root / LAUNCHER_NAME
    if app.is_symlink() or not app.is_dir():
        raise PackageError("disk image Whisper Face.app must be a real app bundle")


def _verify_drag_launcher(app: Path, source_root: Path, source_zip: Path,
                          version: str, revision: str) -> None:
    """Check the app with the launcher contract of the source it carries."""
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise PackageError("disk image app Info.plist is invalid") from exc
    if (
        info.get("CFBundleIdentifier") != "com.berg.whisper-face.launcher"
        or info.get("CFBundleExecutable") != "Whisper Face"
    ):
        raise PackageError("disk image app identity is invalid")
    _run(
        sys.executable,
        str(source_root / "scripts/macos_launcher_app.py"),
        "verify-drag",
        "--app", str(app),
        "--policy", str(source_root / "config/macos-signing-policy.json"),
        "--source-zip", str(source_zip),
        "--version", version,
        "--revision", revision,
    )


@contextlib.contextmanager
def _verified_source(source_zip: Path, version: str, revision: str):
    """Unpack a release's source ZIP to a scratch folder and prove it is the
    exact source of that release; yields its root and its tree receipt."""
    expected_root = f"Whisper Face {version}"
    _check_zip_members(source_zip, expected_root)
    with tempfile.TemporaryDirectory(prefix="whisper-face-package-verify.") as directory:
        extracted = Path(directory)
        _extract_zip(source_zip, extracted)
        zip_root = extracted / expected_root
        zip_receipt = verify_tree(zip_root, version, revision)
        verify_exact_checkout(zip_root, version, revision)
        yield zip_root, zip_receipt


def verify_artifacts(
    source_zip: Path, disk_image: Path, version: str, revision: str
) -> dict:
    version = _require_version(version)
    revision = _require_revision(revision)
    source_zip = source_zip.resolve()
    disk_image = disk_image.resolve()
    if not source_zip.is_file() or not disk_image.is_file():
        raise PackageError("source ZIP and disk image must both exist")
    with _verified_source(source_zip, version, revision) as (zip_root, zip_receipt):
        mount_point, device = _attach_disk_image(disk_image)
        try:
            verify_volume_layout(mount_point)
            _verify_drag_launcher(
                mount_point / LAUNCHER_NAME, zip_root, source_zip, version,
                revision)
        finally:
            _run("hdiutil", "detach", device)
    return zip_receipt


def verify_release_app(
    source_zip: Path, app: Path, version: str, revision: str
) -> dict:
    """Prove an app taken off a release's disk image, wherever it now is,
    is the app that release's own source describes: the ZIP is the exact
    source, and the app passes that source's launcher contract with the ZIP
    sealed inside it. The in-app update checks the copy it is about to put
    in place this way, after copying, so nothing can change in between."""
    version = _require_version(version)
    revision = _require_revision(revision)
    source_zip = source_zip.resolve()
    app = app.resolve()
    if not source_zip.is_file() or not app.is_dir():
        raise PackageError("source ZIP and app must both exist")
    with _verified_source(source_zip, version, revision) as (zip_root, zip_receipt):
        _verify_drag_launcher(app, zip_root, source_zip, version, revision)
    return zip_receipt


@contextlib.contextmanager
def attached_disk_image(disk_image: Path):
    """Attach a disk image read-only and out of sight, check it holds just
    the app and the Applications link, and detach it afterwards."""
    mount_point, device = _attach_disk_image(disk_image.resolve())
    try:
        verify_volume_layout(mount_point)
        yield mount_point
    finally:
        _run("hdiutil", "detach", device)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    stamp = commands.add_parser("stamp", help="write a deterministic tree receipt")
    stamp.add_argument("--root", required=True)
    stamp.add_argument("--version", required=True)
    stamp.add_argument("--revision", required=True)
    stamp.add_argument("--source-date-epoch", required=True, type=int)

    verify = commands.add_parser("verify-tree", help="verify one staged tree")
    verify.add_argument("--root", required=True)
    verify.add_argument("--version", required=True)
    verify.add_argument("--revision", required=True)

    artifacts = commands.add_parser(
        "verify-artifacts",
        help="prove the ZIP is the exact source and the DMG's app carries it",
    )
    artifacts.add_argument("--source-zip", required=True)
    artifacts.add_argument("--disk-image", required=True)
    artifacts.add_argument("--version", required=True)
    artifacts.add_argument("--revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "stamp":
            receipt = stamp_tree(
                Path(args.root), args.version, args.revision, args.source_date_epoch
            )
        elif args.command == "verify-tree":
            receipt = verify_tree(Path(args.root), args.version, args.revision)
        elif args.command == "verify-artifacts":
            receipt = verify_artifacts(
                Path(args.source_zip), Path(args.disk_image), args.version, args.revision
            )
        else:  # pragma: no cover - argparse owns the choices
            raise AssertionError(args.command)
    except PackageError as exc:
        print(f"macOS package verification error: {exc}", file=sys.stderr)
        return 2
    print(f"verified package tree {receipt['tree_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
