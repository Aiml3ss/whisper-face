#!/usr/bin/env python3
"""Write, package, and verify the Whisper Face Windows source bundle.

Whisper Face has no Authenticode certificate, so a Windows artifact cannot
prove who built it. This tool makes it prove what it *contains* instead: the
same deterministic logical-tree receipt the Mac packages carry, the exact Git
revision, an entry point Windows can actually run, and a path layout that
survives Windows filesystem rules.

It uses only the Python standard library and no Windows-only tooling, so the
bundle can be built and audited from macOS or Linux without Wine, PowerShell,
or a Windows machine.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath


BUNDLE_README = "START HERE.txt"
ENTRY_POINT = "Install.cmd"
SCRIPT_DIR = Path(__file__).resolve().parent
ZIP_EPOCH = 315532800  # 1980-01-01T00:00:00Z, the earliest timestamp a ZIP holds

# The entry point exists because Windows refuses to run a double-clicked
# ``.ps1``. Each fragment below is load bearing, so the verifier checks the
# shipped file rather than trusting that it was never edited.
ENTRY_POINT_CONTRACT = (
    'cd /d "%~dp0"',                 # run from the bundle, not from System32
    "-NoProfile",                    # ignore whatever the machine's profile does
    "-ExecutionPolicy Bypass",       # the default policy blocks an unsigned .ps1
    '-File "%~dp0setup.ps1"',        # the bundled installer, quoted for spaces
)

# What a Windows user must find in the bundle for the install to be possible at
# all, plus the receipts that bind it to one revision.
REQUIRED_BUNDLE_FILES = (
    ENTRY_POINT,
    "setup.ps1",
    "dictate.py",
    "dictate.py.lock",
    "LICENSE",
    "LICENSE_POLICY.md",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "PACKAGE-CONTENTS.json",
    "RELEASE-METADATA.json",
)

# Private state, capture output, and build residue. ``git archive`` already
# excludes every one of these; the verifier refuses to publish a bundle where
# that stopped being true.
FORBIDDEN_BUNDLE_ENTRIES = (
    ".evidence",
    ".models",
    ".probe-renders",
    ".windows",
    "__pycache__",
    ".DS_Store",
    ".dictate.lock",
    "acoustic_calibration_activation.json",
    "acoustic_keyword_activation.json",
    "acoustic_keyword_memory.json",
    "delayed_cleanup_activation.json",
    "demonstrations.json",
    "dictate.log",
    "dictionary.txt",
    "learned.json",
    "ollama-error.log",
    "ollama.log",
    "preferences.json",
    "relisten_activation.json",
    "snippets.json",
    "tones.json",
    "transcripts.jsonl",
    "voice_inbox.json",
)

# Names Windows cannot create, whatever the archive claims. A bundle carrying
# one of these extracts partially and leaves the user with a broken checkout.
WINDOWS_RESERVED_NAME = re.compile(
    r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$")
WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*\\') | frozenset(
    chr(code) for code in range(32))

SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
REVISION = re.compile(r"^[0-9a-f]{40}$")

README_TEMPLATE = """Whisper Face {version} for Windows
{underline}

1. Extract this whole folder somewhere you can write, such as
   C:\\Users\\<you>\\Whisper Face. Do not run it from inside the ZIP.
2. Open the "Whisper Face {version}" folder.
3. Double-click Install.cmd.

Install.cmd is the only thing you need to run. It starts the bundled
setup.ps1 for you, because Windows blocks a double-clicked PowerShell
script by default.

The folder also contains Install.command and setup.sh. Those are the
macOS versions of the same installer and do nothing on Windows. The
similar name is the only thing they share with Install.cmd.

If Windows shows "Windows protected your PC" or "The publisher could not
be verified", that is expected: this download is not signed with an
Authenticode certificate, so Windows cannot name a publisher. Choose
"More info" then "Run anyway" only if you trust where you got this file.
You can avoid the prompt entirely by right-clicking the downloaded ZIP,
choosing Properties, ticking Unblock, and extracting again.

The installer needs winget (App Installer, from the Microsoft Store) and
an internet connection the first time. It installs uv, ffmpeg, and Ollama,
then registers a "Whisper Face" task that starts dictation when you log
in. It is safe to run again: rerunning replaces the login task and keeps
your dictionary, snippets, tones, preferences, and transcripts.

To check an existing install without changing it, open PowerShell in the
"Whisper Face {version}" folder and run:

    .\\setup.ps1 --verify

To remove it, run the same way:

    .\\setup.ps1 --uninstall

That prints a plan and changes nothing until you add --yes.

Source revision: {revision}
Licence: AGPL-3.0-only. See LICENSE and LICENSE_POLICY.md in the folder.
This bundle is unsigned. Verify its SHA-256 against the SHA256SUMS file
published with the release before trusting it.
"""


class BundleError(ValueError):
    """The Windows bundle or one of its members is malformed."""


def _require_version(value: str) -> str:
    if not SEMVER.fullmatch(value):
        raise BundleError(f"version must be SemVer: {value!r}")
    return value


def _require_revision(value: str) -> str:
    value = value.casefold()
    if not REVISION.fullmatch(value):
        raise BundleError("revision must be a full 40-character Git SHA-1")
    return value


def _package_module():
    """Load the shared package verifier without importing it as a package."""
    source = SCRIPT_DIR / "verify_macos_package.py"
    specification = importlib.util.spec_from_file_location(
        "whisper_face_package_verifier", source)
    if specification is None or specification.loader is None:
        raise BundleError(f"could not load the package verifier: {source}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def render_readme(version: str, revision: str) -> str:
    version = _require_version(version)
    revision = _require_revision(revision)
    title = f"Whisper Face {version} for Windows"
    return README_TEMPLATE.format(
        version=version, revision=revision, underline="=" * len(title))


def _zip_info(relative: str, mode: int, epoch: int, *, directory: bool = False):
    stamp = time.gmtime(epoch)
    info = zipfile.ZipInfo(
        relative + "/" if directory else relative, date_time=stamp[:6])
    # ``create_system = 3`` (Unix) is what makes the high half of the external
    # attributes a POSIX mode, which is how the executable bit and the one
    # symlink survive a round trip back to a checkout we can digest.
    info.create_system = 3
    info.external_attr = (mode & 0xFFFF) << 16
    if directory:
        info.external_attr |= 0x10  # MS-DOS directory bit, for Windows tools
        info.compress_type = zipfile.ZIP_STORED
    else:
        info.compress_type = zipfile.ZIP_DEFLATED
    return info


def archive_bundle(root: Path, output: Path, source_date_epoch: int) -> dict:
    """Pack ``root`` into one deterministic ZIP, entries in sorted order."""
    root = root.resolve()
    output = output.resolve()
    if not root.is_dir():
        raise BundleError(f"staging root is not a directory: {root}")
    if source_date_epoch < ZIP_EPOCH:
        raise BundleError("source-date-epoch predates the 1980 ZIP epoch")
    entries = sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    if not entries:
        raise BundleError("staging root is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    counts = {"directories": 0, "files": 0, "symlinks": 0}
    with zipfile.ZipFile(output, "w", allowZip64=True) as handle:
        for path in entries:
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                handle.writestr(
                    _zip_info(
                        relative, stat.S_IFDIR | mode, source_date_epoch,
                        directory=True),
                    b"")
                counts["directories"] += 1
            elif stat.S_ISLNK(info.st_mode):
                handle.writestr(
                    _zip_info(relative, stat.S_IFLNK | 0o777, source_date_epoch),
                    os.readlink(path).encode("utf-8"))
                counts["symlinks"] += 1
            elif stat.S_ISREG(info.st_mode):
                member = _zip_info(
                    relative, stat.S_IFREG | mode, source_date_epoch)
                with path.open("rb") as source, handle.open(member, "w") as sink:
                    shutil.copyfileobj(source, sink)
                counts["files"] += 1
            else:
                raise BundleError(f"unsupported staged file type: {relative}")
    return {"entry_count": len(entries), **counts}


def _member_names(archive: Path) -> list[str]:
    try:
        with zipfile.ZipFile(archive) as handle:
            return [item.filename for item in handle.infolist()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleError(f"could not inspect the Windows bundle: {exc}") from exc


def _check_windows_paths(names: list[str]) -> None:
    """Refuse anything Windows would extract wrongly, or not at all."""
    if not names:
        raise BundleError("Windows bundle is empty")
    lowered: dict[str, str] = {}
    for name in names:
        member = name.rstrip("/")
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise BundleError(f"Windows bundle contains an unsafe path: {name!r}")
        for part in path.parts:
            bad = WINDOWS_FORBIDDEN_CHARACTERS & set(part)
            if bad:
                raise BundleError(
                    f"Windows cannot create {name!r}: "
                    f"reserved character {sorted(bad)[0]!r}")
            if part != part.rstrip(" .") or part != part.lstrip():
                raise BundleError(
                    f"Windows cannot create {name!r}: "
                    "a path component ends in a space or a dot")
            if WINDOWS_RESERVED_NAME.fullmatch(part):
                raise BundleError(
                    f"Windows cannot create {name!r}: reserved device name")
        folded = member.casefold()
        if folded in lowered:
            raise BundleError(
                "Windows bundle holds two paths that differ only in case: "
                f"{lowered[folded]!r} and {member!r}")
        lowered[folded] = member


def _check_layout(names: list[str], expected_root: str) -> None:
    """The ZIP root shows the versioned folder and its instructions, nothing else."""
    # Finder and ``ditto --keepParent`` add a resource-fork sidecar tree that is
    # meaningless on Windows and looks like a second copy of the download.
    for name in names:
        parts = PurePosixPath(name.rstrip("/")).parts
        if "__MACOSX" in parts or any(
            part.startswith("._") for part in parts
        ):
            raise BundleError(
                f"Windows bundle carries macOS resource-fork residue: {name!r}")
    roots = {PurePosixPath(name.rstrip("/")).parts[0] for name in names}
    if roots != {expected_root, BUNDLE_README}:
        raise BundleError(
            "Windows bundle must contain exactly "
            f"{expected_root!r} and {BUNDLE_README!r}, found "
            + ", ".join(sorted(repr(item) for item in roots)))


def _extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as handle:
        members = handle.infolist()
        for member in members:
            target = destination / PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.is_dir():
                target.mkdir(exist_ok=True)
            elif stat.S_ISLNK(mode):
                os.symlink(handle.read(member).decode("utf-8"), target)
            else:
                with handle.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
        for member in members:
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or not stat.S_IMODE(mode):
                continue
            os.chmod(destination / PurePosixPath(member.filename),
                     stat.S_IMODE(mode))


def _check_entry_point(root: Path) -> None:
    entry = root / ENTRY_POINT
    if not entry.is_file():
        raise BundleError(f"Windows bundle is missing {ENTRY_POINT}")
    text = entry.read_text(encoding="utf-8")
    for fragment in ENTRY_POINT_CONTRACT:
        if fragment not in text:
            raise BundleError(
                f"{ENTRY_POINT} no longer launches the bundled installer "
                f"correctly: {fragment!r} is missing")
    installer = root / "setup.ps1"
    if not installer.is_file():
        raise BundleError(f"{ENTRY_POINT} targets a setup.ps1 that is not bundled")


def _check_bundle_contents(root: Path) -> None:
    for relative in REQUIRED_BUNDLE_FILES:
        if not (root / relative).is_file():
            raise BundleError(f"Windows bundle is missing {relative}")
    present = {path.name for path in root.rglob("*")}
    found = sorted(present.intersection(FORBIDDEN_BUNDLE_ENTRIES))
    if found:
        raise BundleError(
            "Windows bundle carries private or generated state: "
            + ", ".join(found))


def _check_readme(readme: Path, version: str) -> None:
    if not readme.is_file():
        raise BundleError(f"Windows bundle is missing {BUNDLE_README}")
    text = readme.read_text(encoding="utf-8")
    for expected in (
        ENTRY_POINT, f"Whisper Face {version}", "Install.command", "unsigned"
    ):
        if expected not in text:
            raise BundleError(
                f"{BUNDLE_README} does not name {expected!r}")


def verify_bundle(bundle_zip: Path, version: str, revision: str) -> dict:
    version = _require_version(version)
    revision = _require_revision(revision)
    bundle_zip = bundle_zip.resolve()
    if not bundle_zip.is_file():
        raise BundleError(f"Windows bundle is not a file: {bundle_zip}")
    package = _package_module()
    expected_root = f"Whisper Face {version}"
    names = _member_names(bundle_zip)
    _check_windows_paths(names)
    _check_layout(names, expected_root)
    with tempfile.TemporaryDirectory(prefix="whisper-face-windows-verify.") as directory:
        extracted = Path(directory)
        _extract(bundle_zip, extracted)
        root = extracted / expected_root
        try:
            receipt = package.verify_tree(root, version, revision)
            package.verify_exact_checkout(root, version, revision)
        except package.PackageError as exc:
            raise BundleError(str(exc)) from exc
        _check_entry_point(root)
        _check_bundle_contents(root)
        _check_readme(extracted / BUNDLE_README, version)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    readme = commands.add_parser(
        "readme", help="write the plain-text instructions beside the bundle")
    readme.add_argument("--version", required=True)
    readme.add_argument("--revision", required=True)
    readme.add_argument("--output", required=True)

    archive = commands.add_parser(
        "archive", help="pack a staged tree into one deterministic ZIP")
    archive.add_argument("--root", required=True)
    archive.add_argument("--output", required=True)
    archive.add_argument("--source-date-epoch", required=True, type=int)

    verify = commands.add_parser(
        "verify", help="prove a Windows bundle carries the exact source")
    verify.add_argument("--bundle-zip", required=True)
    verify.add_argument("--version", required=True)
    verify.add_argument("--revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "readme":
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                render_readme(args.version, args.revision), encoding="utf-8")
            print(f"wrote {output.name}")
        elif args.command == "archive":
            counts = archive_bundle(
                Path(args.root), Path(args.output), args.source_date_epoch)
            print(
                "packed {entry_count} entries "
                "({files} files, {directories} directories, "
                "{symlinks} symlinks)".format(**counts))
        elif args.command == "verify":
            receipt = verify_bundle(
                Path(args.bundle_zip), args.version, args.revision)
            print(f"verified Windows bundle tree {receipt['tree_sha256']}")
        else:  # pragma: no cover - argparse owns the choices
            raise AssertionError(args.command)
    except BundleError as exc:
        print(f"Windows bundle error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
