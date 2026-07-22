#!/usr/bin/env python3
"""Build, install, and verify the generic Whisper Face macOS launcher."""

from __future__ import annotations

import argparse
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
from pathlib import Path

BUNDLE_ID = "com.berg.whisper-face.launcher"
PRODUCT = "Whisper Face"
EXECUTABLE = PRODUCT
REVISION = re.compile(r"^[0-9a-f]{40}$")
BASE_FILES = {
    "Contents/Info.plist",
    f"Contents/MacOS/{EXECUTABLE}",
    "Contents/Resources/launcher-source-sha256",
}
SIGNATURE_FILES = {"Contents/_CodeSignature/CodeResources"}
LEGACY_FILES = BASE_FILES | {
    "Contents/Resources/checkout-path",
    "Contents/Resources/source-revision",
}
DEFAULT_RECEIPT = Path("Library/Application Support/Whisper Face/launcher-install.json")
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config/macos-signing-policy.json"
TEAM_IDENTIFIER = re.compile(r"^[A-Z0-9]{10}$")
SWIFT_SOURCE = r'''import AppKit
import Foundation

enum LauncherFailure: Error { case invalidInstallation }

func run(_ executable: String, _ arguments: [String]) throws -> (Int32, String) {
    let process = Process(); let output = Pipe()
    process.executableURL = URL(fileURLWithPath: executable); process.arguments = arguments
    process.standardOutput = output; process.standardError = FileHandle.nullDevice
    try process.run(); process.waitUntilExit()
    return (process.terminationStatus, String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self))
}

func requestExistingGUI(at socketPath: String) -> Bool {
    guard FileManager.default.isExecutableFile(atPath: "/usr/bin/nc") else { return false }
    let process = Process(); let input = Pipe()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/nc"); process.arguments = ["-U", socketPath]
    process.standardInput = input; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
    do { try process.run(); input.fileHandleForWriting.write(Data([0x01])); try input.fileHandleForWriting.close() } catch { return false }
    let deadline = Date().addingTimeInterval(0.2)
    while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.01) }
    if process.isRunning { process.terminate() }; process.waitUntilExit()
    return process.terminationStatus == 0
}

func binding() throws -> (String, String) {
    let files = FileManager.default
    let directory = files.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Whisper Face")
    let receipt = directory.appendingPathComponent("launcher-install.json")
    let directoryAttributes = try files.attributesOfItem(atPath: directory.path)
    let receiptAttributes = try files.attributesOfItem(atPath: receipt.path)
    guard (directoryAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((directoryAttributes[.posixPermissions] as? NSNumber)?.intValue ?? -1) & 0o777 == 0o700,
          (receiptAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((receiptAttributes[.posixPermissions] as? NSNumber)?.intValue ?? -1) & 0o777 == 0o600,
          let object = try JSONSerialization.jsonObject(with: Data(contentsOf: receipt)) as? [String: Any],
          object["schema_version"] as? Int == 1,
          object["bundle_id"] as? String == "com.berg.whisper-face.launcher",
          let checkout = object["checkout"] as? String,
          let revision = object["source_revision"] as? String,
          checkout.hasPrefix("/"), !checkout.contains("\n"),
          revision.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil else {
        throw LauncherFailure.invalidInstallation
    }
    return (checkout, revision)
}

func launchExistingRuntime(start: Bool) throws {
    let (checkout, revision) = try binding(); let files = FileManager.default
    guard files.fileExists(atPath: checkout + "/dictate.py"), files.fileExists(atPath: checkout + "/setup.sh") else { throw LauncherFailure.invalidInstallation }
    let git = try run("/usr/bin/git", ["-C", checkout, "rev-parse", "HEAD"])
    guard git.0 == 0, git.1.trimmingCharacters(in: .whitespacesAndNewlines) == revision else { throw LauncherFailure.invalidInstallation }
    let agent = files.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents/com.berg.dictate.plist")
    let plist = try PropertyListSerialization.propertyList(from: Data(contentsOf: agent), options: [], format: nil) as? [String: Any]
    guard plist?["WorkingDirectory"] as? String == checkout else { throw LauncherFailure.invalidInstallation }
    if !start { return }
    let domain = "gui/\(getuid())/com.berg.dictate"
    guard try run("/bin/launchctl", ["kickstart", domain]).0 == 0 else { throw LauncherFailure.invalidInstallation }
    let deadline = Date().addingTimeInterval(5.0)
    repeat {
        let state = try run("/bin/launchctl", ["print", domain])
        if let match = state.1.range(of: #"pid = ([0-9]+)"#, options: .regularExpression),
           let pid = Int32(state.1[match].split(separator: " ").last ?? ""),
           requestExistingGUI(at: "/tmp/whisper-face-gui-\(getuid())-\(pid)-\(revision).sock") { return }
        Thread.sleep(forTimeInterval: 0.05)
    } while Date() < deadline
}

@main struct WhisperFaceLauncher {
    static func main() {
        NSApplication.shared.setActivationPolicy(.accessory)
        do { try launchExistingRuntime(start: CommandLine.arguments.dropFirst() != ["--verify"]) }
        catch {
            let alert = NSAlert(); alert.messageText = "Whisper Face could not start"
            alert.informativeText = "Run Install.command again from the installed checkout, then retry."
            alert.alertStyle = .critical; alert.runModal(); exit(1)
        }
    }
}
'''

class LauncherError(ValueError):
    pass

def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, mode); os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def _revision(checkout: Path) -> str:
    try:
        value = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError("checkout must have an immutable Git revision") from exc
    if not REVISION.fullmatch(value): raise LauncherError("checkout revision must be a full Git SHA-1")
    return value

def _source_digest() -> str:
    return hashlib.sha256(SWIFT_SOURCE.encode()).hexdigest()

def _compile(destination: Path) -> None:
    swiftc = shutil.which("swiftc")
    if not swiftc: raise LauncherError("swiftc is required to build the Mac launcher")
    source = destination.parent / ".WhisperFaceLauncher.swift"
    source.write_text(SWIFT_SOURCE)
    try:
        result = subprocess.run([swiftc, "-parse-as-library", "-module-name", "WhisperFaceLauncher", "-target", "arm64-apple-macos14.0", "-O", "-whole-module-optimization", "-framework", "AppKit", "-o", str(destination), str(source)], text=True, capture_output=True)
        if result.returncode: raise LauncherError(result.stderr.strip() or "Swift compiler failed")
    finally:
        source.unlink(missing_ok=True)
    os.chmod(destination, 0o755)

def _entries(app: Path) -> set[str]:
    return {p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file() or p.is_symlink()}

def _bundle_digest(app: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(_entries(app)):
        path = app / relative; digest.update(relative.encode() + b"\0")
        digest.update(str(stat.S_IMODE(path.lstat().st_mode)).encode() + b"\0")
        digest.update(os.readlink(path).encode() if path.is_symlink() else path.read_bytes())
    return digest.hexdigest()

def _expected_plist() -> dict[str, object]:
    return {"CFBundleDisplayName": PRODUCT, "CFBundleExecutable": EXECUTABLE, "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": PRODUCT, "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "14.0", "LSUIElement": True}

def _pinned_team(policy: Path = DEFAULT_POLICY) -> str:
    try: payload = json.loads(policy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise LauncherError("macOS signing policy is missing or invalid") from exc
    if set(payload) != {"developer_id_team_identifier", "schema_version"} or payload["schema_version"] != 1: raise LauncherError("macOS signing policy contract mismatch")
    team = payload["developer_id_team_identifier"]
    if not isinstance(team, str) or not TEAM_IDENTIFIER.fullmatch(team): raise LauncherError("pinned Developer ID team is not configured")
    return team

def _verify_pinned_signature(app: Path, policy: Path = DEFAULT_POLICY) -> None:
    team = _pinned_team(policy)
    requirement = (
        f'anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists '
        f'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
        f'and certificate leaf[subject.OU] = "{team}" and identifier "{BUNDLE_ID}"'
    )
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", f"-R={requirement}", str(app)], text=True, capture_output=True)
    if result.returncode: raise LauncherError("launcher does not satisfy the pinned Developer ID requirement")

def build_app(app: Path) -> None:
    app = app.expanduser().resolve(); app.parent.mkdir(parents=True, exist_ok=True)
    if app.name != f"{PRODUCT}.app": raise LauncherError("launcher target is invalid")
    staging_root = Path(tempfile.mkdtemp(prefix=f".{app.name}.", dir=app.parent)); staging = staging_root / app.name
    try:
        executable = staging / "Contents/MacOS" / EXECUTABLE; executable.parent.mkdir(parents=True)
        resources = staging / "Contents/Resources"; resources.mkdir()
        _atomic_write(staging / "Contents/Info.plist", plistlib.dumps(_expected_plist(), sort_keys=True), 0o644)
        _compile(executable)
        _atomic_write(resources / "launcher-source-sha256", (_source_digest() + "\n").encode(), 0o644)
        verify_generic_app(staging)
        if app.exists(): verify_owned_app(app); shutil.rmtree(app)
        os.replace(staging, app)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

def verify_owned_app(app: Path) -> None:
    if not app.is_dir() or app.name != f"{PRODUCT}.app": raise LauncherError("existing launcher is not an owned app")
    entries = _entries(app)
    if entries not in (BASE_FILES, LEGACY_FILES, BASE_FILES | SIGNATURE_FILES): raise LauncherError("existing launcher contains unexpected files")
    try: plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except Exception as exc: raise LauncherError("existing launcher Info.plist is invalid") from exc
    if plist.get("CFBundleIdentifier") != BUNDLE_ID or plist.get("CFBundleExecutable") != EXECUTABLE: raise LauncherError("existing launcher ownership markers do not match")

def verify_generic_app(app: Path, *, policy: Path = DEFAULT_POLICY, require_signed: bool = False) -> None:
    app = app.expanduser().resolve(); entries = _entries(app) if app.is_dir() else set()
    signed = entries == BASE_FILES | SIGNATURE_FILES
    if entries not in (BASE_FILES, BASE_FILES | SIGNATURE_FILES): raise LauncherError("generic launcher contains missing or unexpected files")
    if plistlib.loads((app / "Contents/Info.plist").read_bytes()) != _expected_plist(): raise LauncherError("launcher Info.plist contract mismatch")
    if (app / "Contents/Resources/launcher-source-sha256").read_text().strip() != _source_digest(): raise LauncherError("launcher source contract mismatch")
    executable = app / "Contents/MacOS" / EXECUTABLE
    if not executable.stat().st_mode & stat.S_IXUSR or executable.read_bytes()[:4] != b"\xcf\xfa\xed\xfe": raise LauncherError("launcher executable is not an arm64 Mach-O")
    if signed:
        _verify_pinned_signature(app, policy)
    if require_signed and not signed: raise LauncherError("release launcher must be signed")
    if not signed:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / EXECUTABLE; _compile(expected)
            if executable.read_bytes() != expected.read_bytes(): raise LauncherError("launcher compiled binary mismatch")
    for forbidden in ("checkout-path", "source-revision", "dictate.py", "setup.sh"):
        if any(p.name == forbidden for p in app.rglob("*")): raise LauncherError("launcher must not embed runtime source or machine binding")

def _write_receipt(receipt: Path, checkout: Path, app: Path) -> None:
    receipt = receipt.expanduser().resolve(); directory = receipt.parent
    directory.mkdir(parents=True, exist_ok=True); os.chmod(directory, 0o700)
    if directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) != 0o700: raise LauncherError("launcher receipt directory must be owned 0700")
    payload = {"app_bundle_sha256": _bundle_digest(app), "bundle_id": BUNDLE_ID, "checkout": str(checkout), "schema_version": 1, "source_revision": _revision(checkout)}
    _atomic_write(receipt, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(), 0o600)

def install_app(app: Path, checkout: Path, receipt: Path, source_app: Path | None, policy: Path) -> None:
    app = app.expanduser().resolve(); checkout = checkout.resolve(); app.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{app.name}.", dir=app.parent)); staging = staging_root / app.name
    try:
        if source_app:
            source_app = source_app.expanduser().resolve(); verify_generic_app(source_app, policy=policy)
            subprocess.run(["/usr/bin/ditto", str(source_app), str(staging)], check=True)
            if _bundle_digest(source_app) != _bundle_digest(staging): raise LauncherError("installed launcher differs from packaged app")
        else:
            build_app(staging)
        verify_generic_app(staging, policy=policy)
        if app.exists(): verify_owned_app(app); backup = app.with_name(f".{app.name}.previous-{os.getpid()}"); os.replace(app, backup)
        else: backup = None
        try: os.replace(staging, app)
        except BaseException:
            if backup: os.replace(backup, app)
            raise
        if backup: shutil.rmtree(backup)
    finally: shutil.rmtree(staging_root, ignore_errors=True)
    _write_receipt(receipt, checkout, app); verify_installation(app, checkout, receipt)

def verify_installation(app: Path, checkout: Path, receipt: Path, *, policy: Path = DEFAULT_POLICY, runtime: bool = False) -> None:
    verify_generic_app(app, policy=policy)
    receipt = receipt.expanduser().resolve(); directory = receipt.parent
    if stat.S_IMODE(directory.stat().st_mode) != 0o700 or directory.stat().st_uid != os.getuid(): raise LauncherError("launcher receipt directory is not strict 0700")
    if stat.S_IMODE(receipt.stat().st_mode) != 0o600 or receipt.stat().st_uid != os.getuid(): raise LauncherError("launcher receipt is not strict 0600")
    payload = json.loads(receipt.read_text()); checkout = checkout.resolve()
    expected = {"app_bundle_sha256": _bundle_digest(app), "bundle_id": BUNDLE_ID, "checkout": str(checkout), "schema_version": 1, "source_revision": _revision(checkout)}
    if payload != expected: raise LauncherError("launcher receipt binding is stale or invalid")
    if runtime:
        result = subprocess.run([str(app / "Contents/MacOS" / EXECUTABLE), "--verify"], capture_output=True)
        if result.returncode: raise LauncherError("compiled launcher runtime verification failed")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("command", choices=("build", "install", "verify")); parser.add_argument("--app", required=True); parser.add_argument("--checkout"); parser.add_argument("--receipt"); parser.add_argument("--source-app"); parser.add_argument("--policy", default=str(DEFAULT_POLICY)); parser.add_argument("--installed-runtime", action="store_true"); parser.add_argument("--require-signed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "build": build_app(Path(args.app)); verify_generic_app(Path(args.app), policy=Path(args.policy), require_signed=args.require_signed)
        elif args.command == "install":
            if not args.checkout or not args.receipt: raise LauncherError("install requires --checkout and --receipt")
            install_app(Path(args.app), Path(args.checkout), Path(args.receipt), Path(args.source_app) if args.source_app else None, Path(args.policy))
        else:
            if args.checkout and args.receipt: verify_installation(Path(args.app), Path(args.checkout), Path(args.receipt), policy=Path(args.policy), runtime=args.installed_runtime)
            else: verify_generic_app(Path(args.app), policy=Path(args.policy), require_signed=args.require_signed)
    except (LauncherError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"macOS launcher error: {exc}", file=sys.stderr); return 2
    print(f"verified generic launcher app: {Path(args.app).expanduser()}"); return 0

if __name__ == "__main__": raise SystemExit(main())
