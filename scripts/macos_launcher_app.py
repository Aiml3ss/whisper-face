#!/usr/bin/env python3
"""Create and verify the unsigned, checkout-backed Whisper Face launcher app."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


BUNDLE_ID = "com.berg.whisper-face.launcher"
PRODUCT = "Whisper Face"
REVISION = re.compile(r"^[0-9a-f]{40}$")
EXECUTABLE = "Whisper Face"
EXPECTED_FILES = {
    "Contents/Info.plist",
    f"Contents/MacOS/{EXECUTABLE}",
    "Contents/Resources/checkout-path",
    "Contents/Resources/launcher-source-sha256",
    "Contents/Resources/source-revision",
}
LEGACY_EXPECTED_FILES = EXPECTED_FILES - {
    "Contents/Resources/launcher-source-sha256"
}
SWIFT_SOURCE = r'''import AppKit
import Foundation

enum LauncherFailure: Error {
    case invalidInstallation
}

func readBoundValue(_ name: String, resources: URL) throws -> String {
    let value = try String(
        contentsOf: resources.appendingPathComponent(name), encoding: .utf8
    ).trimmingCharacters(in: .whitespacesAndNewlines)
    guard !value.isEmpty, !value.contains("\n"), !value.contains("\r") else {
        throw LauncherFailure.invalidInstallation
    }
    return value
}

func run(_ executable: String, _ arguments: [String]) throws -> (Int32, String) {
    let process = Process()
    let output = Pipe()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.standardOutput = output
    process.standardError = FileHandle.nullDevice
    try process.run()
    process.waitUntilExit()
    let data = output.fileHandleForReading.readDataToEndOfFile()
    return (process.terminationStatus, String(decoding: data, as: UTF8.self))
}

func launchExistingRuntime(start: Bool) throws {
    guard let resources = Bundle.main.resourceURL else {
        throw LauncherFailure.invalidInstallation
    }
    let checkout = try readBoundValue("checkout-path", resources: resources)
    let revision = try readBoundValue("source-revision", resources: resources)
    guard revision.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil else {
        throw LauncherFailure.invalidInstallation
    }
    let files = FileManager.default
    guard files.fileExists(atPath: checkout + "/dictate.py"),
          files.fileExists(atPath: checkout + "/setup.sh") else {
        throw LauncherFailure.invalidInstallation
    }
    let git = try run("/usr/bin/git", ["-C", checkout, "rev-parse", "HEAD"])
    guard git.0 == 0, git.1.trimmingCharacters(in: .whitespacesAndNewlines) == revision else {
        throw LauncherFailure.invalidInstallation
    }
    let agent = files.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/LaunchAgents/com.berg.dictate.plist")
    let plistData = try Data(contentsOf: agent)
    guard let plist = try PropertyListSerialization.propertyList(
        from: plistData, options: [], format: nil
    ) as? [String: Any], plist["WorkingDirectory"] as? String == checkout else {
        throw LauncherFailure.invalidInstallation
    }
    if !start {
        return
    }
    let domain = "gui/\(getuid())/com.berg.dictate"
    let kickstart = try run("/bin/launchctl", ["kickstart", domain])
    guard kickstart.0 == 0 else {
        throw LauncherFailure.invalidInstallation
    }

    // Bring forward an existing runtime-owned window when one is already open.
    // The launcher never constructs a second settings UI or embeds runtime code.
    for _ in 0..<10 {
        let state = try run("/bin/launchctl", ["print", domain])
        if let match = state.1.range(of: #"pid = ([0-9]+)"#, options: .regularExpression),
           let pid = Int32(state.1[match].split(separator: " ").last ?? "") {
            NSRunningApplication(processIdentifier: pid)?.activate(
                options: [.activateAllWindows, .activateIgnoringOtherApps]
            )
            break
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
}

@main
struct WhisperFaceLauncher {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        do {
            let verifyOnly = CommandLine.arguments.dropFirst() == ["--verify"]
            try launchExistingRuntime(start: !verifyOnly)
        } catch {
            let alert = NSAlert()
            alert.messageText = "Whisper Face could not start"
            alert.informativeText = "Run Install.command again from the installed checkout, then retry."
            alert.alertStyle = .critical
            alert.runModal()
            exit(1)
        }
    }
}
'''


class LauncherError(ValueError):
    """The launcher bundle or its checkout binding is invalid."""


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _revision(checkout: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError("checkout must have an immutable Git revision") from exc
    if not REVISION.fullmatch(result):
        raise LauncherError("checkout revision must be a full Git SHA-1")
    return result


def _paths(app: Path) -> tuple[Path, Path, Path, Path, Path]:
    return (
        app / "Contents" / "Info.plist",
        app / "Contents" / "MacOS" / EXECUTABLE,
        app / "Contents" / "Resources" / "checkout-path",
        app / "Contents" / "Resources" / "launcher-source-sha256",
        app / "Contents" / "Resources" / "source-revision",
    )


def _source_digest() -> str:
    return hashlib.sha256(SWIFT_SOURCE.encode("utf-8")).hexdigest()


def _compile_launcher(destination: Path) -> None:
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise LauncherError("swiftc is required to build the Mac launcher")
    source = destination.parent / ".WhisperFaceLauncher.swift"
    source.write_text(SWIFT_SOURCE, encoding="utf-8")
    try:
        subprocess.run(
            [
                swiftc,
                "-parse-as-library",
                "-module-name", "WhisperFaceLauncher",
                "-target", "arm64-apple-macos14.0",
                "-O",
                "-whole-module-optimization",
                "-framework", "AppKit",
                "-o", str(destination),
                str(source),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or "Swift compiler failed"
        raise LauncherError(detail.strip()) from exc
    finally:
        source.unlink(missing_ok=True)
    os.chmod(destination, 0o755)


def create_app(app: Path, checkout: Path) -> None:
    app = app.expanduser().resolve()
    checkout = checkout.resolve()
    if app.name != f"{PRODUCT}.app" or "\n" in str(checkout):
        raise LauncherError("launcher target or checkout path is invalid")
    revision = _revision(checkout)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{app.name}.", dir=app.parent))
    staging = staging_root / app.name
    try:
        info, executable, checkout_path, source_hash_path, revision_path = _paths(
            staging
        )
        executable.parent.mkdir(parents=True)
        checkout_path.parent.mkdir(parents=True)
        plist = {
            "CFBundleDisplayName": PRODUCT,
            "CFBundleExecutable": EXECUTABLE,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": PRODUCT,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "14.0",
            "LSUIElement": True,
        }
        _atomic_write(info, plistlib.dumps(plist, sort_keys=True), 0o644)
        _compile_launcher(executable)
        _atomic_write(checkout_path, f"{checkout}\n".encode("utf-8"), 0o644)
        _atomic_write(source_hash_path, f"{_source_digest()}\n".encode("ascii"), 0o644)
        _atomic_write(revision_path, f"{revision}\n".encode("ascii"), 0o644)
        verify_app(staging, checkout)
        if app.exists():
            verify_owned_app(app)
            backup = app.with_name(f".{app.name}.previous-{os.getpid()}")
            os.replace(app, backup)
            try:
                os.replace(staging, app)
            except BaseException:
                os.replace(backup, app)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(staging, app)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    verify_app(app, checkout)


def verify_owned_app(app: Path) -> None:
    """Fail closed before replacing an older launcher contract we own."""
    app = app.expanduser().resolve()
    if not app.is_dir() or app.name != f"{PRODUCT}.app":
        raise LauncherError("existing launcher target is not an owned app bundle")
    actual = {
        path.relative_to(app).as_posix()
        for path in app.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual not in (EXPECTED_FILES, LEGACY_EXPECTED_FILES):
        raise LauncherError("existing launcher contains unexpected files")
    info, executable, checkout_path, _source_hash_path, revision_path = _paths(app)
    try:
        plist = plistlib.loads(info.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise LauncherError("existing launcher Info.plist is invalid") from exc
    if (
        plist.get("CFBundleIdentifier") != BUNDLE_ID
        or plist.get("CFBundleExecutable") != EXECUTABLE
        or plist.get("CFBundlePackageType") != "APPL"
    ):
        raise LauncherError("existing launcher ownership markers do not match")
    recorded_checkout = checkout_path.read_text(encoding="utf-8").strip()
    recorded_revision = revision_path.read_text(encoding="ascii").strip()
    if not Path(recorded_checkout).is_absolute() or "\n" in recorded_checkout:
        raise LauncherError("existing launcher checkout binding is invalid")
    if not REVISION.fullmatch(recorded_revision):
        raise LauncherError("existing launcher revision is invalid")
    if not executable.is_file() or not executable.stat().st_mode & stat.S_IXUSR:
        raise LauncherError("existing launcher executable is invalid")


def verify_app(
    app: Path,
    checkout: Path,
    *,
    require_checkout_binding: bool = True,
    require_current_revision: bool = True,
    verify_installed_runtime: bool = False,
) -> None:
    app = app.expanduser().resolve()
    checkout = checkout.resolve()
    if not app.is_dir() or app.name != f"{PRODUCT}.app":
        raise LauncherError("launcher app bundle is missing")
    actual = {
        path.relative_to(app).as_posix()
        for path in app.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != EXPECTED_FILES:
        raise LauncherError("launcher app contains missing or unexpected files")
    info, executable, checkout_path, source_hash_path, revision_path = _paths(app)
    try:
        plist = plistlib.loads(info.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise LauncherError("launcher Info.plist is invalid") from exc
    expected_plist = {
        "CFBundleDisplayName": PRODUCT,
        "CFBundleExecutable": EXECUTABLE,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": PRODUCT,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,
    }
    if plist != expected_plist:
        raise LauncherError("launcher Info.plist contract mismatch")
    if source_hash_path.read_text(encoding="ascii").strip() != _source_digest():
        raise LauncherError("launcher source contract mismatch")
    if not executable.stat().st_mode & stat.S_IXUSR:
        raise LauncherError("launcher executable is not executable")
    if executable.read_bytes()[:4] != b"\xcf\xfa\xed\xfe":
        raise LauncherError("launcher executable is not an arm64 Mach-O binary")
    with tempfile.TemporaryDirectory(prefix="whisper-face-launcher-verify.") as directory:
        expected_executable = Path(directory) / EXECUTABLE
        _compile_launcher(expected_executable)
        if executable.read_bytes() != expected_executable.read_bytes():
            raise LauncherError("launcher compiled binary mismatch")
    if verify_installed_runtime:
        try:
            subprocess.run(
                [str(executable), "--verify"], capture_output=True, check=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LauncherError("compiled launcher runtime verification failed") from exc
    recorded_checkout = checkout_path.read_text(encoding="utf-8").strip()
    if not Path(recorded_checkout).is_absolute() or "\n" in recorded_checkout:
        raise LauncherError("launcher checkout binding is invalid")
    if require_checkout_binding and recorded_checkout != str(checkout):
        raise LauncherError("launcher points at a different checkout")
    recorded_revision = revision_path.read_text(encoding="ascii").strip()
    if not REVISION.fullmatch(recorded_revision):
        raise LauncherError("launcher source revision is invalid")
    if require_current_revision and recorded_revision != _revision(checkout):
        raise LauncherError("launcher source revision is stale")
    for forbidden in ("dictate.py", "dictate.py.lock", "setup.sh"):
        if any(path.name == forbidden for path in app.rglob("*")):
            raise LauncherError("launcher must not embed runtime source")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("--app", required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--installed-runtime", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            Path(args.app).expanduser().parent.mkdir(parents=True, exist_ok=True)
            create_app(Path(args.app), Path(args.checkout))
        else:
            verify_app(
                Path(args.app),
                Path(args.checkout),
                verify_installed_runtime=args.installed_runtime,
            )
    except (LauncherError, OSError) as exc:
        print(f"macOS launcher error: {exc}", file=sys.stderr)
        return 2
    print(f"verified unsigned launcher app: {Path(args.app).expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
