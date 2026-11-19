#!/usr/bin/env python3
"""Build, install, and verify the Whisper Face macOS launcher app.

Two apps come from one Swift source. A folder install builds the *generic*
launcher locally (``build``/``install``): it only starts the service its
receipt names. ``install`` signs it with a Developer ID of the pinned team
when this Mac's keychain holds one, and ad hoc otherwise. A release packages the *drag-install* app (``package``): the
same launcher plus its own release's source archive and a copy of ``uv``, so
that opening it on a Mac with nothing installed unpacks that archive into
``~/Library/Application Support/Whisper Face/checkout`` and runs the
checkout's own ``setup.sh --app``. ``adopt`` is what that setup calls to bind
the receipt and the login service to the app.
"""

from __future__ import annotations

import argparse
import functools
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
from pathlib import Path

BUNDLE_ID = "com.berg.whisper-face.launcher"
PRODUCT = "Whisper Face"
EXECUTABLE = PRODUCT
REVISION = re.compile(r"^[0-9a-f]{40}$")
ICON_NAME = "WhisperFace"
ICON_FILE = f"Contents/Resources/{ICON_NAME}.icns"
# Installs built before the app carried an icon are still ours; keep their
# layouts nameable so ownership checks tolerate an upgrade in place.
PRE_ICON_FILES = {
    "Contents/Info.plist",
    f"Contents/MacOS/{EXECUTABLE}",
    "Contents/Resources/launcher-source-sha256",
}
BASE_FILES = PRE_ICON_FILES | {ICON_FILE}
SIGNATURE_FILES = {"Contents/_CodeSignature/CodeResources"}
LEGACY_FILES = PRE_ICON_FILES | {
    "Contents/Resources/checkout-path",
    "Contents/Resources/source-revision",
}
# What the drag-install app adds to the generic launcher. The archive is the
# published source ZIP, byte for byte; uv is the one program setup needs that
# a new Mac lacks, and its licences travel with it.
SOURCE_ARCHIVE = "Contents/Resources/WhisperFace-source.zip"
UV_HELPER = "Contents/Helpers/uv"
UV_LICENSES = ("LICENSE-APACHE", "LICENSE-MIT")
UV_LICENSE_FILES = {f"Contents/Resources/ThirdParty/uv/{name}" for name in UV_LICENSES}
DRAG_FILES = BASE_FILES | {SOURCE_ARCHIVE, UV_HELPER} | UV_LICENSE_FILES
# Stapling a notarization ticket to an app writes this file.
STAPLE_FILES = {"Contents/CodeResources"}
DRAG_CHANNELS = ("stable", "preview")
DEFAULT_RECEIPT = Path("Library/Application Support/Whisper Face/launcher-install.json")
REPO = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = REPO / "config/macos-signing-policy.json"
DEFAULT_ICON = REPO / f"icons/{ICON_NAME}.icns"
# A Developer ID release signs every program in the drag-install app with the
# hardened runtime and a secure timestamp, which notarization requires, and
# gives each exactly these entitlements. The launcher's are the file
# package_macos.sh signs it with: the microphone, because the launcher is the
# launchd job and so TCC's responsible process for the Python runtime it
# starts, and a hardened responsible process without it is denied the
# microphone without a prompt. uv gets none: it is a static program that loads
# only system libraries and starts Python as a separate process.
LAUNCHER_ENTITLEMENTS = REPO / "config/macos-launcher-entitlements.plist"
UV_IDENTIFIER = "com.berg.whisper-face.uv"
# A folder install signs the launcher it builds with a Developer ID from this
# Mac's keychain when one belongs to the pinned team. macOS then knows the
# launcher by team and bundle ID instead of by its code hash, so its
# Accessibility, Input Monitoring and microphone grants survive a rebuild.
SECURITY = "/usr/bin/security"
CODESIGN = "/usr/bin/codesign"
# "adhoc" keeps every build ad hoc, as before signing existed.
SIGNING_ENVIRONMENT = "WHISPER_FACE_LAUNCHER_SIGNING"
# `security find-identity -v` lists only valid identities, one per line:
#   2) <SHA-1> "Developer ID Application: Name (TEAMID)"
DEVELOPER_ID_LINE = re.compile(r'^\s*\d+\) ([0-9A-F]{40}) "(Developer ID Application: .+ \(([A-Z0-9]{10})\))"$')
# Listing identities never prompts. Signing can: a key whose access list does
# not yet allow codesign makes macOS ask, and in an unattended run nobody
# answers, so codesign would wait forever. It normally finishes in a few
# seconds, the secure timestamp from Apple included; past this it is stopped
# and the launcher is signed ad hoc instead.
IDENTITY_TIMEOUT = 15
SIGNING_TIMEOUT = 60
# The first four bytes of a Mach-O program, thin or universal, either byte order.
MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
# Containers Apple's notary service opens and scans for programs.
NESTED_ARCHIVE_MAGICS = (b"PK\x03\x04", b"xar!", b"\x1f\x8b")
SETUP_FACE = REPO / "icons/faces/color/parrot-idle.svg"
TEAM_IDENTIFIER = re.compile(r"^[A-Z0-9]{10}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
# Written into a release bundle by scripts/package_macos.sh; a release before
# the drag install also recorded the digest of the launcher shipped beside it.
RELEASE_METADATA = "RELEASE-METADATA.json"
MACOS_MINIMUM = (14, 0)

# The setup window's checklist, in the order setup.sh --app announces it with
# "@@ phase <name>" lines, and the two names that mean it finished.
SETUP_PHASES = ("prepare", "python", "speech-model", "helpers",
                "preview-model", "service")
FINISHED_PHASES = ("done", "permissions")
# setup.sh's log wrapper adds these after any failure; they are not reasons.
FAILURE_BANNERS = ("!! Whisper Face setup failed", "!! Full log:",
                   "!! Attach that file")
NETWORK_REASON = "installer.reason.network"
# A known reason, in setup.sh's or a download tool's words, and the catalog
# key the window shows for it. Anything else is installer.reason.generic.
FAILURE_REASONS = (
    ("free disk space", "installer.reason.space"),
    ("Apple Silicon Mac", "installer.reason.mac"),
    ("macOS 14 (Sonoma) or newer", "installer.reason.mac"),
    ("Command Line Tools", "installer.reason.tools"),
    ("Swift 6 or newer", "installer.reason.tools"),
    ("Swift compiler is unavailable", "installer.reason.tools"),
    ("installs a different release", "installer.reason.version"),
    ("Could not resolve host", NETWORK_REASON),
    ("Failed to fetch", NETWORK_REASON),
    ("error sending request", NETWORK_REASON),
    ("dns error", NETWORK_REASON),
    ("Network is unreachable", NETWORK_REASON),
    ("network connection was lost", NETWORK_REASON),
)
# The Python every uv call in setup.sh uses (its PYTHON_VERSION); the app
# runs its updater on the same one.
SETUP_PYTHON = "3.12"
# The theme roles the setup window draws with.
SETUP_TYPE_ROLES = {"title": "page_title", "row": "row_title",
                    "body": "row_caption"}
SWIFT_TEMPLATE = r'''import AppKit
import CryptoKit
import Foundation

enum LauncherFailure: Error { case invalidInstallation }

// Generated by scripts/macos_launcher_app.py from the window's string catalog
// (whisper_face_gui.STRING_CATALOGS) and the shared theme
// (whisper_face_theme). This app runs before Python exists on the Mac, so the
// copy and the colours are compiled in rather than read at run time.
let copyTable: [String: String] = [
/*COPY_TABLE*/
]
// Four RGBA quads: light, dark, light with Increase Contrast, dark with it.
let themeTable: [String: [CGFloat]] = [
/*THEME_TABLE*/
]
// Size, NSFont weight, and 1 for SF Rounded.
let typeTable: [String: [CGFloat]] = [
/*TYPE_TABLE*/
]
let failureReasons: [(String, String)] = [
/*FAILURE_REASONS*/
]
let networkReasonKey = /*NETWORK_REASON*/
let failureBanners: [String] = [/*FAILURE_BANNERS*/]
let setupPhases: [String] = [/*SETUP_PHASES*/]
let finishedPhases: [String] = [/*FINISHED_PHASES*/]
let faceSVG = #"""
/*FACE_SVG*/
"""#

let bundleIdentifier = "com.berg.whisper-face.launcher"
let requiredFreeBytes: Int64 = 5 * 1024 * 1024 * 1024
let windowWidth: CGFloat = 480

func supportDirectory() -> URL {
    FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Whisper Face")
}

func localized(_ key: String, _ values: [String: String] = [:]) -> String {
    var text = copyTable[key] ?? key
    for (name, value) in values { text = text.replacingOccurrences(of: "{\(name)}", with: value) }
    return text
}

func themeColor(_ name: String) -> NSColor {
    let values = themeTable[name] ?? [0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1]
    return NSColor(name: NSColor.Name(name)) { appearance in
        let match = appearance.bestMatch(from: [.aqua, .darkAqua, .accessibilityHighContrastAqua, .accessibilityHighContrastDarkAqua])
        var offset = 0
        if match == .darkAqua { offset = 4 } else if match == .accessibilityHighContrastAqua { offset = 8 } else if match == .accessibilityHighContrastDarkAqua { offset = 12 }
        return NSColor(srgbRed: values[offset], green: values[offset + 1], blue: values[offset + 2], alpha: values[offset + 3])
    }
}

func themeFont(_ role: String) -> NSFont {
    let spec = typeTable[role] ?? [13, 0, 0]
    let font = NSFont.systemFont(ofSize: spec[0], weight: NSFont.Weight(rawValue: spec[1]))
    if spec[2] > 0, let rounded = font.fontDescriptor.withDesign(.rounded) { return NSFont(descriptor: rounded, size: spec[0]) ?? font }
    return font
}

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

struct Receipt { let checkout: String; let revision: String; let app: String?; let version: String? }

func ownBundlePath() -> String { Bundle.main.bundleURL.resolvingSymlinksInPath().standardizedFileURL.path }

// Schema 1 is a folder install's receipt; schema 2 also names the app that
// set the install up, which is the only app that may start it, and its
// release's version.
func parseReceipt(_ object: [String: Any]) -> Receipt? {
    guard let schema = object["schema_version"] as? Int, schema == 1 || schema == 2,
          object["bundle_id"] as? String == bundleIdentifier,
          let checkout = object["checkout"] as? String,
          let revision = object["source_revision"] as? String,
          checkout.hasPrefix("/"), !checkout.contains("\n"),
          revision.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil else { return nil }
    if schema == 1 { return Receipt(checkout: checkout, revision: revision, app: nil, version: nil) }
    guard let app = object["app"] as? String, app.hasPrefix("/"), !app.contains("\n") else { return nil }
    return Receipt(checkout: checkout, revision: revision, app: app, version: object["version"] as? String)
}

func binding() throws -> Receipt {
    let files = FileManager.default
    let directory = supportDirectory()
    let receipt = directory.appendingPathComponent("launcher-install.json")
    let directoryAttributes = try files.attributesOfItem(atPath: directory.path)
    let receiptAttributes = try files.attributesOfItem(atPath: receipt.path)
    guard (directoryAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((directoryAttributes[.posixPermissions] as? NSNumber)?.intValue ?? -1) & 0o777 == 0o700,
          (receiptAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((receiptAttributes[.posixPermissions] as? NSNumber)?.intValue ?? -1) & 0o777 == 0o600,
          let object = try JSONSerialization.jsonObject(with: Data(contentsOf: receipt)) as? [String: Any],
          let parsed = parseReceipt(object) else {
        throw LauncherFailure.invalidInstallation
    }
    return parsed
}

// SemVer precedence, by the same rules as scripts/safe_update_advisor.py's
// _version and _compare: a release outranks its prereleases, numeric
// identifiers compare as numbers and rank below alphanumeric ones.
typealias VersionKey = (core: [Int], pre: [String])

func versionKey(_ value: String) -> VersionKey? {
    let pattern = #"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"#
    guard let regex = try? NSRegularExpression(pattern: pattern),
          let match = regex.firstMatch(in: value, range: NSRange(value.startIndex..., in: value)) else { return nil }
    func group(_ index: Int) -> String? {
        guard let range = Range(match.range(at: index), in: value) else { return nil }
        return String(value[range])
    }
    guard let major = group(1).flatMap({ Int($0) }), let minor = group(2).flatMap({ Int($0) }), let patch = group(3).flatMap({ Int($0) }) else { return nil }
    let pre = group(4).map { $0.split(separator: ".").map(String.init) } ?? []
    if pre.contains(where: { isNumericIdentifier($0) && $0.count > 1 && $0.hasPrefix("0") }) { return nil }
    return ([major, minor, patch], pre)
}

func isNumericIdentifier(_ value: String) -> Bool { !value.isEmpty && value.utf8.allSatisfy { $0 >= 48 && $0 <= 57 } }

func compareVersions(_ left: VersionKey, _ right: VersionKey) -> Int {
    if left.core != right.core { return left.core.lexicographicallyPrecedes(right.core) ? -1 : 1 }
    if left.pre.isEmpty || right.pre.isEmpty { return (left.pre.isEmpty ? 1 : 0) - (right.pre.isEmpty ? 1 : 0) }
    for (l, r) in zip(left.pre, right.pre) where l != r {
        let ln = isNumericIdentifier(l), rn = isNumericIdentifier(r)
        // Numeric identifiers carry no leading zero, so the longer is larger.
        if ln && rn && l.count != r.count { return l.count < r.count ? -1 : 1 }
        if ln && rn { return Array(l.utf8).lexicographicallyPrecedes(Array(r.utf8)) ? -1 : 1 }
        if ln != rn { return ln ? -1 : 1 }
        return Array(l.utf8).lexicographicallyPrecedes(Array(r.utf8)) ? -1 : 1
    }
    return left.pre.count == right.pre.count ? 0 : (left.pre.count > right.pre.count ? 1 : -1)
}

// How this app's release stands to the install its receipt describes.
// app_update.py applies the advisor's full rule again before it changes
// anything; this only chooses the window.
enum InstallMatch: Equatable { case current, update(String, String), older(String, String), conflict(String) }

func installMatch(_ receipt: Receipt, info: [String: Any]) -> InstallMatch? {
    guard receipt.app != nil, let ownRevision = info["WFSourceRevision"] as? String,
          let ownVersion = info["CFBundleShortVersionString"] as? String else { return nil }
    if receipt.revision == ownRevision { return .current }
    guard let installed = receipt.version, let mine = versionKey(ownVersion), let theirs = versionKey(installed) else { return .conflict(receipt.version ?? "") }
    let order = compareVersions(mine, theirs)
    if order > 0 { return .update(installed, ownVersion) }
    if order < 0 { return .older(installed, ownVersion) }
    return .conflict(installed)
}

// One line for scripts/macos_launcher_app.py's tests, from a receipt file.
func installMatchLine(_ path: String) -> String {
    guard let data = FileManager.default.contents(atPath: path),
          let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let receipt = parseReceipt(object),
          let match = installMatch(receipt, info: Bundle.main.infoDictionary ?? [:]) else { return "none" }
    switch match {
    case .current: return "current"
    case .update(let from, let to): return "update \(from) \(to)"
    case .older(let installed, let own): return "older \(installed) \(own)"
    case .conflict(let installed): return "conflict \(installed)"
    }
}

func launchExistingRuntime(start: Bool) throws {
    let receipt = try binding(); let files = FileManager.default
    let checkout = receipt.checkout, revision = receipt.revision
    if let app = receipt.app, app != ownBundlePath() { throw LauncherFailure.invalidInstallation }
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

func runWorker(_ command: [String]) -> Never {
    guard let executable = command.first, executable.hasPrefix("/"),
          FileManager.default.isExecutableFile(atPath: executable) else {
        FileHandle.standardError.write(Data("Whisper Face launcher: --run needs an absolute executable\n".utf8))
        exit(78)
    }
    let child = Process()
    child.executableURL = URL(fileURLWithPath: executable)
    child.arguments = Array(command.dropFirst())
    // Inherit cwd, stdio, and the environment from launchd; additionally publish
    // this launcher's PID so the supervised runtime binds its GUI activation
    // socket to the launchd job (this process), which macOS reports as the
    // responsible process for TCC.
    var environment = ProcessInfo.processInfo.environment
    environment["WHISPER_FACE_SERVICE_PID"] = String(getpid())
    child.environment = environment
    // Forward launchd's stop signals so KeepAlive observes the child's own exit
    // status instead of the supervisor dying first. posix_spawn resets the
    // child's dispositions to default, so ignoring them here never leaves the
    // child unkillable.
    var forwarders: [DispatchSourceSignal] = []
    for number in [SIGTERM, SIGINT] {
        signal(number, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: number, queue: .global())
        source.setEventHandler { let pid = child.processIdentifier; if pid > 0 { kill(pid, number) } }
        source.resume(); forwarders.append(source)
    }
    do { try child.run() } catch {
        FileHandle.standardError.write(Data("Whisper Face launcher: runtime failed to start: \(error)\n".utf8))
        exit(70)
    }
    child.waitUntilExit(); withExtendedLifetime(forwarders) {}
    exit(child.terminationStatus)
}

// What the setup window says about how far it has got: a fraction for the
// bar from the steps done, and one line with the step and the time so far.
// No estimate: nobody has measured how long a first setup takes, and most of
// it is downloading, which depends on the connection.
func setupProgress(done: Int, total: Int, seconds: Int) -> (Double, String) {
    let finished = max(0, min(done, total))
    let fraction = total > 0 ? Double(finished) / Double(total) : 0
    let values = ["step": String(min(finished + 1, total)), "total": String(total)]
    let minutes = max(0, seconds) / 60
    let text: String
    if minutes < 1 { text = localized("installer.progress.started", values) }
    else if minutes == 1 { text = localized("installer.progress.minute", values) }
    else { text = localized("installer.progress.minutes", values.merging(["minutes": String(minutes)]) { $1 }) }
    return (fraction, text)
}

// ---------------------------------------------------------------- setup ---
// Only an app built for drag install carries its release's source archive.
// The folder installs' locally built launcher has none, so it never sets
// anything up: it only says where its install lives.

func isDragApp() -> Bool { Bundle.main.object(forInfoDictionaryKey: "WFSourceArchiveSHA256") is String }

func receiptExists() -> Bool {
    FileManager.default.fileExists(atPath: supportDirectory().appendingPathComponent("launcher-install.json").path)
}

func needsMove() -> Bool {
    let url = Bundle.main.bundleURL
    let path = url.resolvingSymlinksInPath().standardizedFileURL.path
    if path.contains("/AppTranslocation/") { return true }
    if let values = try? url.resourceValues(forKeys: [.volumeIsReadOnlyKey]), values.volumeIsReadOnly == true { return true }
    let parent = (path as NSString).deletingLastPathComponent
    return parent != "/Applications" && parent != FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications").path
}

func toolsInstalled() -> Bool { ((try? run("/usr/bin/xcode-select", ["-p"]))?.0 ?? 1) == 0 }

func enoughSpace() -> Bool {
    let home = FileManager.default.homeDirectoryForCurrentUser
    guard let values = try? home.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
          let available = values.volumeAvailableCapacityForImportantUsage else { return true }
    return available >= requiredFreeBytes
}

func releaseRevision(of checkout: URL) -> String? {
    guard let data = try? Data(contentsOf: checkout.appendingPathComponent("RELEASE-METADATA.json")),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
    return object["source_revision"] as? String
}

// Turns what setup.sh printed into one catalog key. setup.sh ends every
// failure with a line starting "!! "; the three banner lines its log wrapper
// adds are not reasons. Anything not recognised is the generic reason, which
// points at Show Log rather than showing installer text.
func plainReason(_ lines: [String]) -> String {
    let reasons = lines.filter { line in line.hasPrefix("!! ") && !failureBanners.contains(where: { line.hasPrefix($0) }) }
    if let last = reasons.last {
        for (needle, key) in failureReasons where last.contains(needle) { return key }
    }
    let tail = lines.suffix(80).joined(separator: "\n")
    for (needle, key) in failureReasons where key == networkReasonKey && tail.contains(needle) { return key }
    return "installer.reason.generic"
}

struct SetupFailure: Error { let reasonKey: String }

// Verify the embedded archive against the digest sealed into Info.plist, then
// unpack it into the managed checkout, or for an update into
// checkout.incoming beside it. Nothing in it is ever run from here.
func prepareCheckout(named name: String = "checkout") throws -> URL {
    let damaged = SetupFailure(reasonKey: "installer.reason.damaged")
    let info = Bundle.main.infoDictionary ?? [:]
    guard let expected = info["WFSourceArchiveSHA256"] as? String, let revision = info["WFSourceRevision"] as? String,
          let archive = Bundle.main.url(forResource: "WhisperFace-source", withExtension: "zip") else { throw damaged }
    let data = try Data(contentsOf: archive, options: .alwaysMapped)
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    guard digest == expected else { throw damaged }
    let files = FileManager.default
    let support = supportDirectory()
    try files.createDirectory(at: support, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    try files.setAttributes([.posixPermissions: 0o700], ofItemAtPath: support.path)
    let checkout = support.appendingPathComponent(name)
    if files.fileExists(atPath: checkout.path) {
        // An earlier attempt's copy is kept, with everything it already
        // downloaded, as long as it is this release.
        if releaseRevision(of: checkout) == revision { return checkout }
        guard name == updateFolder else { throw SetupFailure(reasonKey: "installer.reason.version") }
        // A newer copy an earlier update left, of some other release.
        try files.removeItem(at: checkout)
    }
    let staging = support.appendingPathComponent(".checkout-unpacking")
    try? files.removeItem(at: staging)
    try files.createDirectory(at: staging, withIntermediateDirectories: false)
    guard try run("/usr/bin/ditto", ["-x", "-k", "--noqtn", archive.path, staging.path]).0 == 0 else { throw damaged }
    let roots = try files.contentsOfDirectory(atPath: staging.path).filter { !$0.hasPrefix(".") }
    guard roots.count == 1 else { throw damaged }
    let root = staging.appendingPathComponent(roots[0])
    guard releaseRevision(of: root) == revision else { throw damaged }
    try files.moveItem(at: root, to: checkout)
    try? files.removeItem(at: staging)
    return checkout
}

// Runs setup.sh in its own process group so stopping setup stops everything
// it started, and hands back its combined output line by line.
final class SetupRun {
    private(set) var pid: pid_t = 0

    func start(_ arguments: [String], onLine: @escaping (String) -> Void, onExit: @escaping (Int32) -> Void) throws {
        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else { throw LauncherFailure.invalidInstallation }
        var actions: posix_spawn_file_actions_t? = nil
        posix_spawn_file_actions_init(&actions)
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_adddup2(&actions, fds[1], 1)
        posix_spawn_file_actions_adddup2(&actions, fds[1], 2)
        posix_spawn_file_actions_addclose(&actions, fds[0])
        posix_spawn_file_actions_addclose(&actions, fds[1])
        var attributes: posix_spawnattr_t? = nil
        posix_spawnattr_init(&attributes)
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP))
        posix_spawnattr_setpgroup(&attributes, 0)
        let argv: [UnsafeMutablePointer<CChar>?] = arguments.map { strdup($0) } + [nil]
        defer { argv.forEach { free($0) }; posix_spawn_file_actions_destroy(&actions); posix_spawnattr_destroy(&attributes) }
        let status = posix_spawn(&pid, arguments[0], &actions, &attributes, argv, environ)
        close(fds[1])
        guard status == 0 else { close(fds[0]); throw LauncherFailure.invalidInstallation }
        let child = pid, reader = FileHandle(fileDescriptor: fds[0], closeOnDealloc: true)
        DispatchQueue.global().async {
            var pending = Data()
            while true {
                let chunk = reader.availableData
                if chunk.isEmpty { break }
                pending.append(chunk)
                while let newline = pending.firstIndex(of: 0x0A) {
                    let line = String(decoding: pending[pending.startIndex..<newline], as: UTF8.self)
                    pending.removeSubrange(pending.startIndex...newline)
                    DispatchQueue.main.async { onLine(line) }
                }
            }
            if !pending.isEmpty { let line = String(decoding: pending, as: UTF8.self); DispatchQueue.main.async { onLine(line) } }
            var raw: Int32 = 0
            waitpid(child, &raw, 0)
            let code: Int32 = (raw & 0x7f) == 0 ? (raw >> 8) & 0xff : 128 + (raw & 0x7f)
            DispatchQueue.main.async { onExit(code) }
        }
    }

    func stop() { if pid > 0 { kill(-pid, SIGTERM) } }
}

enum Screen: Equatable {
    case move, welcome, tools, toolsWaiting, space, running, failed(String), repair, folder(String), elsewhere(String), unset
    // Updating a drag install: (from, to) versions, and a failure's reason.
    case update(String, String), updating(String, String), updateFailed(String, String), updateStuck(String)
    case older(String, String), conflict(String)
    // Moving a folder install into this app: the folder, and a failure's reason.
    case adopt(String), adopting(String), adoptFailed(String, String), adoptStuck(String, String), adopted(String)
}

// Where an update unpacks the newer release, beside the install, and what
// it runs from there with the app's own uv.
let updateFolder = "checkout.incoming"
let updateScript = "scripts/app_update.py"
let pythonVersion = /*PYTHON_VERSION*/
// app_update.py's record of a move of a folder install that has not ended.
let adoptJournal = "move-in-progress.json"

// Draws the theme's window colour; a layer-free view so a cached render shows
// exactly what the window does.
final class BackgroundView: NSView {
    override func draw(_ dirtyRect: NSRect) { themeColor("window").setFill(); dirtyRect.fill() }
}

final class SetupController: NSObject, NSWindowDelegate {
    let window: NSWindow
    let face = NSImageView()
    let titleLabel = NSTextField(wrappingLabelWithString: "")
    let bodyLabel = NSTextField(wrappingLabelWithString: "")
    let noteLabel = NSTextField(wrappingLabelWithString: "")
    let checklist = NSStackView()
    // Two bars, never one switched between modes: a bar that has run its
    // indeterminate animation keeps drawing a sliver after it is made
    // determinate, whatever its value. `progress` only sweeps (waiting on
    // Apple's installer); `stepBar` only fills, a step at a time.
    let progress = NSProgressIndicator()
    let stepBar = NSProgressIndicator()
    let primary = NSButton(title: "", target: nil, action: nil)
    let secondary = NSButton(title: "", target: nil, action: nil)
    let quit = NSButton(title: "", target: nil, action: nil)
    var rows: [String: (NSImageView, NSTextField)] = [:]
    var screen: Screen = .welcome
    var currentPhase: String? = nil
    var completed: Set<String> = []
    var finishedPhase: String? = nil
    // When the current setup, update or move began, for the time so far.
    var startedAt: Date? = nil
    var progressTimer: Timer? = nil
    var output: [String] = []
    var checkout: URL? = nil
    var setupRun: SetupRun? = nil
    var stopping = false
    var toolsTimer: Timer? = nil
    // Set while this window updates an install: the two versions.
    var pendingUpdate: (from: String, to: String)? = nil
    // Set while this window offers or makes the move of a folder install.
    var pendingMove: String? = nil
    // Only the app built for drag install can take a folder install over.
    var canAdopt = isDragApp()

    override init() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: windowWidth, height: 400), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: true)
        super.init()
        window.title = localized("installer.window.title")
        window.isReleasedWhenClosed = false
        window.delegate = self
        let content = BackgroundView()
        window.contentView = content

        face.image = NSImage(data: Data(faceSVG.utf8))
        face.imageScaling = .scaleProportionallyUpOrDown
        face.setAccessibilityLabel(localized("installer.face.label"))
        titleLabel.font = themeFont("title"); titleLabel.textColor = themeColor("ink")
        titleLabel.setAccessibilityRole(.staticText)
        for label in [bodyLabel, noteLabel] { label.font = themeFont("body"); label.textColor = themeColor("ink_soft") }
        for label in [titleLabel, bodyLabel, noteLabel] { label.preferredMaxLayoutWidth = windowWidth - 56; label.isSelectable = false }

        checklist.orientation = .vertical; checklist.alignment = .leading; checklist.spacing = 8
        checklist.setAccessibilityElement(true); checklist.setAccessibilityRole(.list); checklist.setAccessibilityLabel(localized("installer.checklist.label"))
        for phase in setupPhases {
            let icon = NSImageView(); icon.setAccessibilityElement(false)
            icon.translatesAutoresizingMaskIntoConstraints = false
            icon.widthAnchor.constraint(equalToConstant: 16).isActive = true
            icon.heightAnchor.constraint(equalToConstant: 16).isActive = true
            let label = NSTextField(labelWithString: localized("installer.phase.\(phase)"))
            label.font = themeFont("row")
            let row = NSStackView(views: [icon, label]); row.orientation = .horizontal; row.spacing = 8
            checklist.addArrangedSubview(row); rows[phase] = (icon, label)
        }
        progress.style = .bar; progress.isIndeterminate = true; progress.controlSize = .regular
        stepBar.style = .bar; stepBar.isIndeterminate = false; stepBar.controlSize = .regular
        stepBar.minValue = 0; stepBar.maxValue = 1
        stepBar.setAccessibilityLabel(localized("installer.progress.label"))
        progress.setAccessibilityLabel(localized("installer.progress.label"))

        for button in [primary, secondary, quit] { button.bezelStyle = .rounded; button.target = self }
        primary.action = #selector(primaryPressed(_:)); primary.keyEquivalent = "\r"
        secondary.action = #selector(secondaryPressed(_:))
        quit.action = #selector(quitPressed(_:)); quit.title = localized("installer.quit")
        let spacer = NSView(); spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let buttons = NSStackView(views: [secondary, spacer, quit, primary]); buttons.orientation = .horizontal; buttons.spacing = 12

        let filler = NSView(); filler.setContentHuggingPriority(NSLayoutConstraint.Priority(1), for: .vertical)
        let stack = NSStackView(views: [face, titleLabel, bodyLabel, checklist, progress, stepBar, noteLabel, filler, buttons])
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 24, left: 28, bottom: 20, right: 28)
        stack.setCustomSpacing(16, after: face); stack.setCustomSpacing(6, after: titleLabel); stack.setCustomSpacing(16, after: bodyLabel)
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        face.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            stack.topAnchor.constraint(equalTo: content.topAnchor),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            face.widthAnchor.constraint(equalToConstant: 64), face.heightAnchor.constraint(equalToConstant: 64),
            buttons.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -56),
            progress.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -56),
            stepBar.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -56),
            content.widthAnchor.constraint(equalToConstant: windowWidth),
        ])
        // One fixed size for every screen: the tallest, the running checklist.
        show(.running)
        content.layoutSubtreeIfNeeded()
        window.setContentSize(NSSize(width: windowWidth, height: max(380, stack.fittingSize.height)))
    }

    func show(_ next: Screen) {
        screen = next
        var title = "", body = "", note = "", primaryTitle: String? = nil, secondaryTitle: String? = nil
        var listVisible = false, progressVisible = false
        switch next {
        case .move: title = localized("installer.move.title"); body = localized("installer.move.body"); primaryTitle = localized("installer.move.action")
        case .welcome: title = localized("installer.welcome.title"); body = localized("installer.welcome.body"); primaryTitle = localized("installer.welcome.action")
        case .tools: title = localized("installer.tools.title"); body = localized("installer.tools.body"); primaryTitle = localized("installer.tools.action")
        case .toolsWaiting: title = localized("installer.tools.title"); body = localized("installer.tools.body"); note = localized("installer.tools.waiting"); progressVisible = true
        case .space: title = localized("installer.space.title"); body = localized("installer.space.body"); primaryTitle = localized("installer.space.action")
        case .running: title = localized("installer.running.title"); body = localized("installer.running.body"); listVisible = true; progressVisible = true
        case .failed(let reason):
            title = localized("installer.failed.title"); body = localized(reason); listVisible = currentPhase != nil
            // A damaged copy or another release already set up is not
            // something trying again can fix, so neither is offered.
            if !["installer.reason.damaged", "installer.reason.version"].contains(reason) {
                note = localized("installer.failed.note"); primaryTitle = localized("installer.failed.retry")
            }
            if logURL() != nil { secondaryTitle = localized("installer.failed.log") }
        case .repair: title = localized("installer.repair.title"); body = localized("installer.repair.body"); primaryTitle = localized("installer.repair.action")
        case .folder(let folder):
            title = localized("installer.folder.title"); body = localized("installer.folder.body", ["folder": folder]); primaryTitle = localized("installer.folder.action")
            if canAdopt { pendingMove = folder; secondaryTitle = localized("installer.folder.adopt") }
        case .adopt(let folder):
            pendingMove = folder
            title = localized("installer.adopt.title"); body = localized("installer.adopt.body", ["folder": folder]); note = localized("installer.adopt.note")
            primaryTitle = localized("installer.adopt.action"); secondaryTitle = localized("installer.adopt.keep")
        case .adopting(let folder):
            title = localized("installer.adopting.title"); body = localized("installer.adopting.body", ["folder": folder]); listVisible = true; progressVisible = true
        case .adoptFailed(let reason, let folder):
            title = localized("installer.adopt.failed.title"); body = localized(reason); listVisible = currentPhase != nil
            note = localized("installer.adopt.failed.note", ["folder": folder])
            if !["installer.reason.damaged", "installer.reason.version"].contains(reason) { primaryTitle = localized("installer.failed.retry") }
            if logURL() != nil { secondaryTitle = localized("installer.failed.log") }
        case .adoptStuck(let reason, let folder):
            pendingMove = folder
            title = localized("installer.adopt.stuck.title"); body = localized(reason); listVisible = currentPhase != nil
            note = localized("installer.adopt.stuck.note"); primaryTitle = localized("installer.failed.retry")
            if logURL() != nil { secondaryTitle = localized("installer.failed.log") }
        case .adopted(let folder):
            title = localized("installer.adopted.title"); body = localized("installer.adopted.body", ["folder": folder]); note = localized("installer.adopted.note")
            primaryTitle = localized("installer.adopted.action"); secondaryTitle = localized("installer.adopted.show")
        case .elsewhere(let app): title = localized("installer.elsewhere.title"); body = localized("installer.elsewhere.body", ["app": app]); primaryTitle = localized("installer.elsewhere.action")
        case .unset: title = localized("installer.unset.title"); body = localized("installer.unset.body")
        case .update(let from, let to):
            pendingUpdate = (from, to)
            title = localized("installer.update.title"); body = localized("installer.update.body", ["from": from, "to": to]); primaryTitle = localized("installer.update.action")
        case .updating(let from, let to):
            title = localized("installer.updating.title"); body = localized("installer.updating.body", ["from": from, "to": to]); listVisible = true; progressVisible = true
        case .updateFailed(let reason, let from):
            title = localized("installer.update.failed.title"); body = localized(reason); listVisible = currentPhase != nil
            note = localized("installer.update.failed.note", ["from": from])
            if !["installer.reason.damaged", "installer.reason.version"].contains(reason) { primaryTitle = localized("installer.failed.retry") }
            if logURL() != nil { secondaryTitle = localized("installer.failed.log") }
        case .updateStuck(let reason):
            title = localized("installer.update.stuck.title"); body = localized(reason); listVisible = currentPhase != nil
            note = localized("installer.update.stuck.note"); primaryTitle = localized("installer.failed.retry")
            if logURL() != nil { secondaryTitle = localized("installer.failed.log") }
        case .older(let installed, let own):
            title = localized("installer.older.title"); body = localized("installer.older.body", ["installed": installed, "own": own])
        case .conflict(let installed):
            title = localized("installer.conflict.title"); body = localized("installer.conflict.body", ["installed": installed])
        }
        titleLabel.stringValue = title; bodyLabel.stringValue = body; noteLabel.stringValue = note
        noteLabel.isHidden = note.isEmpty
        checklist.isHidden = !listVisible
        // While the checklist runs, the step bar fills a step at a time and
        // the note says which step and how long so far; waiting on Apple's
        // installer has no steps, so its bar only sweeps.
        progress.isHidden = !(progressVisible && !listVisible)
        stepBar.isHidden = !(progressVisible && listVisible)
        primary.isHidden = primaryTitle == nil; primary.title = primaryTitle ?? ""
        secondary.isHidden = secondaryTitle == nil; secondary.title = secondaryTitle ?? ""
        if progress.isHidden { progress.stopAnimation(nil) } else { progress.startAnimation(nil) }
        progressTimer?.invalidate(); progressTimer = nil
        if listVisible && progressVisible {
            if startedAt == nil { startedAt = Date() }
            progressTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refreshProgress() }
        } else if !listVisible { startedAt = nil }
        refreshChecklist()
    }

    func refreshProgress() {
        guard !stepBar.isHidden else { return }
        let seconds = Int(Date().timeIntervalSince(startedAt ?? Date()))
        let (fraction, text) = setupProgress(done: finishedPhase != nil ? setupPhases.count : completed.count, total: setupPhases.count, seconds: seconds)
        stepBar.doubleValue = fraction
        stepBar.setAccessibilityValueDescription(text)
        noteLabel.stringValue = text; noteLabel.isHidden = false
    }

    func refreshChecklist() {
        let failed: Bool
        switch screen {
        case .failed, .updateFailed, .updateStuck, .adoptFailed, .adoptStuck: failed = true
        default: failed = false
        }
        for phase in setupPhases {
            guard let row = rows[phase] else { continue }
            let (icon, label) = row
            let name = localized("installer.phase.\(phase)")
            let symbol: String, tint: NSColor, state: String
            if completed.contains(phase) || finishedPhase != nil {
                symbol = "checkmark.circle.fill"; tint = themeColor("brand_ink"); state = "installer.phase.state.done"
            } else if phase == currentPhase {
                symbol = failed ? "xmark.circle.fill" : "arrow.right.circle.fill"
                tint = failed ? themeColor("error_ink") : themeColor("brand_ink")
                state = failed ? "installer.phase.state.failed" : "installer.phase.state.current"
            } else {
                symbol = "circle"; tint = themeColor("ink_soft"); state = "installer.phase.state.pending"
            }
            icon.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
            icon.contentTintColor = tint
            label.textColor = (state == "installer.phase.state.pending") ? themeColor("ink_soft") : themeColor("ink")
            label.setAccessibilityLabel(localized(state, ["phase": name]))
        }
        refreshProgress()
    }

    func logURL() -> URL? {
        // After an update is put back, the newer copy and its log wait in
        // checkout.incoming, which is where `checkout` points then. A move
        // logs in the app's own copy.
        let candidates = [checkout, pendingUpdate == nil && pendingMove == nil ? nil : supportDirectory().appendingPathComponent("checkout")]
        for folder in candidates.compactMap({ $0 }) {
            let log = folder.appendingPathComponent("install.log")
            if FileManager.default.fileExists(atPath: log.path) { return log }
        }
        return nil
    }

    @objc func primaryPressed(_ sender: Any?) {
        switch screen {
        case .move: moveToApplications()
        case .welcome, .repair, .failed: beginSetup()
        case .space: if pendingUpdate != nil { beginUpdate() } else if pendingMove != nil { beginAdopt() } else { beginSetup() }
        case .update, .updateFailed, .updateStuck: beginUpdate()
        case .adopt, .adoptFailed, .adoptStuck: beginAdopt()
        case .adopted:
            // The runtime takes over, and its permissions window asks for
            // what macOS gives this app afresh.
            try? launchExistingRuntime(start: true)
            NSApp.terminate(nil)
        case .tools: installTools()
        case .folder(let folder): NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: folder).appendingPathComponent("Install.command")])
        case .elsewhere(let app): NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: app)])
        default: break
        }
    }

    @objc func secondaryPressed(_ sender: Any?) {
        switch screen {
        case .adopt(let folder): keepFolder(folder)
        case .folder(let folder): show(.adopt(folder))
        case .adopted(let folder): NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: folder)])
        default: if let log = logURL() { NSWorkspace.shared.activateFileViewerSelecting([log]) }
        }
    }

    // Keep using the folder version: one that still starts runs as it always
    // has and this window steps aside; one that doesn't says how to set it
    // up again, and the move stays one click away.
    func keepFolder(_ folder: String) {
        if (try? launchExistingRuntime(start: true)) != nil { NSApp.terminate(nil); return }
        show(.folder(folder))
    }

    @objc func quitPressed(_ sender: Any?) { requestQuit() }

    func windowShouldClose(_ sender: NSWindow) -> Bool { requestQuit(); return false }

    func shouldTerminate() -> NSApplication.TerminateReply {
        if setupRun == nil || stopping { return .terminateNow }
        requestQuit(); return .terminateCancel
    }

    // Stopping a running setup asks first; opening the app again carries on.
    func requestQuit() {
        guard let running = setupRun, !stopping else { NSApp.terminate(nil); return }
        let alert = NSAlert()
        alert.messageText = localized("installer.stop.title")
        alert.informativeText = localized(pendingUpdate != nil ? "installer.update.stop.body" : pendingMove != nil ? "installer.adopt.stop.body" : "installer.stop.body")
        alert.addButton(withTitle: localized("installer.stop.cancel"))
        let stop = alert.addButton(withTitle: localized("installer.stop.action")); stop.hasDestructiveAction = true
        alert.beginSheetModal(for: window) { response in
            guard response == .alertSecondButtonReturn else { return }
            self.stopping = true; running.stop(); NSApp.terminate(nil)
        }
    }

    func installTools() {
        _ = try? run("/usr/bin/xcode-select", ["--install"])
        show(.toolsWaiting)
        toolsTimer?.invalidate()
        toolsTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] timer in
            guard toolsInstalled() else { return }
            timer.invalidate(); self?.toolsTimer = nil
            if let update = self?.pendingUpdate { self?.show(.update(update.from, update.to)) }
            else if let folder = self?.pendingMove { self?.show(.adopt(folder)) }
            else { self?.show(firstSetupScreen()) }
        }
    }

    func moveToApplications() {
        let files = FileManager.default
        let home = files.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
        for folder in [URL(fileURLWithPath: "/Applications"), home] {
            if folder == home { try? files.createDirectory(at: home, withIntermediateDirectories: true) }
            guard files.isWritableFile(atPath: folder.path) else { continue }
            let destination = folder.appendingPathComponent("Whisper Face.app")
            // Never replace an app that is already there: it may be the one a
            // working install runs from. The person moves it themselves.
            if files.fileExists(atPath: destination.path) { break }
            guard ((try? run("/usr/bin/ditto", [Bundle.main.bundleURL.path, destination.path]))?.0 ?? 1) == 0 else { continue }
            let configuration = NSWorkspace.OpenConfiguration(); configuration.createsNewApplicationInstance = true
            NSWorkspace.shared.openApplication(at: destination, configuration: configuration) { _, _ in
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
            return
        }
        bodyLabel.stringValue = localized("installer.move.failed")
        primary.isHidden = true
    }

    func beginSetup() {
        if !toolsInstalled() { show(.tools); return }
        if !enoughSpace() { show(.space); return }
        completed = []; finishedPhase = nil; output = []; startedAt = nil; currentPhase = setupPhases.first
        show(.running)
        DispatchQueue.global().async {
            do {
                let prepared = try prepareCheckout()
                DispatchQueue.main.async { self.checkout = prepared; self.startSetup(prepared) }
            } catch let failure as SetupFailure {
                DispatchQueue.main.async { self.show(.failed(failure.reasonKey)) }
            } catch {
                DispatchQueue.main.async { self.show(.failed("installer.reason.generic")) }
            }
        }
    }

    // Unpack this app's release beside the install, then let that copy's
    // own updater switch to it: app_update.py runs the new setup.sh, carries
    // the personal files across, and puts the old copy back if setup fails.
    func beginUpdate() {
        guard let update = pendingUpdate else { return }
        if !toolsInstalled() { show(.tools); return }
        if !enoughSpace() { show(.space); return }
        completed = []; finishedPhase = nil; output = []; startedAt = nil; currentPhase = setupPhases.first
        show(.updating(update.from, update.to))
        DispatchQueue.global().async {
            do {
                let incoming = try prepareCheckout(named: updateFolder)
                DispatchQueue.main.async { self.checkout = incoming; self.startUpdate(incoming) }
            } catch let failure as SetupFailure {
                DispatchQueue.main.async { self.currentPhase = nil; self.show(.updateFailed(failure.reasonKey, update.from)) }
            } catch {
                DispatchQueue.main.async { self.currentPhase = nil; self.show(.updateFailed("installer.reason.generic", update.from)) }
            }
        }
    }

    func startUpdate(_ incoming: URL) {
        let uv = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/uv").path
        let setup = SetupRun()
        setupRun = setup
        do {
            try setup.start([uv, "run", "--quiet", "--no-project", "--python", pythonVersion,
                             incoming.appendingPathComponent(updateScript).path, "apply", "--app", ownBundlePath()],
                            onLine: { [weak self] line in self?.receive(line) },
                            onExit: { [weak self] status in self?.updateFinished(status) })
        } catch {
            setupRun = nil; show(.updateFailed("installer.reason.generic", pendingUpdate?.from ?? ""))
        }
    }

    // app_update.py's exit status: 0 updated, 3 put back and running,
    // 4 could not be put back; anything else changed nothing.
    func updateFinished(_ status: Int32) {
        setupRun = nil
        if stopping { return }
        let from = pendingUpdate?.from ?? ""
        switch status {
        case 0:
            // The runtime's own window takes over, and asks for any
            // permission the new app's identity needs.
            try? launchExistingRuntime(start: true)
            NSApp.terminate(nil)
        case 4: show(.updateStuck(plainReason(output)))
        default: show(.updateFailed(plainReason(output), from))
        }
    }

    // Move a folder install into this app: unpack this app's release as its
    // own copy, then let that copy's updater take the folder install over.
    // app_update.py keeps what it replaces, runs setup.sh --app carrying the
    // personal files across from the folder, and puts the folder install
    // back if setup fails. The folder itself is only read.
    func beginAdopt() {
        guard let folder = pendingMove else { return }
        if !toolsInstalled() { show(.tools); return }
        if !enoughSpace() { show(.space); return }
        completed = []; finishedPhase = nil; output = []; startedAt = nil; currentPhase = setupPhases.first
        show(.adopting(folder))
        DispatchQueue.global().async {
            do {
                let prepared = try prepareCheckout()
                DispatchQueue.main.async { self.checkout = prepared; self.startAdopt(prepared) }
            } catch let failure as SetupFailure {
                DispatchQueue.main.async { self.currentPhase = nil; self.show(.adoptFailed(failure.reasonKey, folder)) }
            } catch {
                DispatchQueue.main.async { self.currentPhase = nil; self.show(.adoptFailed("installer.reason.generic", folder)) }
            }
        }
    }

    func startAdopt(_ checkout: URL) {
        let uv = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/uv").path
        let setup = SetupRun()
        setupRun = setup
        do {
            try setup.start([uv, "run", "--quiet", "--no-project", "--python", pythonVersion,
                             checkout.appendingPathComponent(updateScript).path, "adopt-folder", "--app", ownBundlePath()],
                            onLine: { [weak self] line in self?.receive(line) },
                            onExit: { [weak self] status in self?.adoptFinished(status) })
        } catch {
            setupRun = nil; show(.adoptFailed("installer.reason.generic", pendingMove ?? ""))
        }
    }

    // The same exit statuses as an update: 0 moved, 3 put back and running,
    // 4 could not be put back; anything else changed nothing.
    func adoptFinished(_ status: Int32) {
        setupRun = nil
        if stopping { return }
        let folder = pendingMove ?? ""
        switch status {
        case 0: show(.adopted(folder))
        case 4: show(.adoptStuck(plainReason(output), folder))
        default: show(.adoptFailed(plainReason(output), folder))
        }
    }

    func startSetup(_ checkout: URL) {
        let setup = SetupRun()
        setupRun = setup
        do {
            try setup.start(["/bin/bash", checkout.appendingPathComponent("setup.sh").path, "--app", ownBundlePath()],
                            onLine: { [weak self] line in self?.receive(line) },
                            onExit: { [weak self] status in self?.finished(status) })
        } catch {
            setupRun = nil; show(.failed("installer.reason.generic"))
        }
    }

    func receive(_ line: String) {
        output.append(line)
        if output.count > 400 { output.removeFirst(output.count - 400) }
        let marker = "@@ phase "
        guard line.hasPrefix(marker) else { return }
        let phase = String(line.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces)
        if finishedPhases.contains(phase) { finishedPhase = phase; refreshChecklist(); return }
        guard setupPhases.contains(phase) else { return }
        if let current = currentPhase, current != phase { completed.insert(current) }
        currentPhase = phase; refreshChecklist()
    }

    func finished(_ status: Int32) {
        setupRun = nil
        if stopping { return }
        guard status == 0, finishedPhase != nil else { show(.failed(plainReason(output))); return }
        // The runtime's own first-run guide takes over from here.
        try? launchExistingRuntime(start: true)
        NSApp.terminate(nil)
    }
}

final class SetupAppDelegate: NSObject, NSApplicationDelegate {
    let controller: SetupController
    init(_ controller: SetupController) { self.controller = controller }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { controller.shouldTerminate() }
}

func setupMenu() -> NSMenu {
    let menu = NSMenu(), appItem = NSMenuItem(), appMenu = NSMenu()
    appMenu.addItem(withTitle: localized("installer.menu.quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    appItem.submenu = appMenu; menu.addItem(appItem)
    return menu
}

// Before anything is downloaded: Apple's command line tools, then space.
func firstSetupScreen() -> Screen {
    if !toolsInstalled() { return .tools }
    return enoughSpace() ? .welcome : .space
}

// The folder a move into this app was taking over, when that move stopped
// before it finished or was undone.
func interruptedAdoption() -> String? {
    let journal = supportDirectory().appendingPathComponent(adoptJournal)
    guard let data = FileManager.default.contents(atPath: journal.path),
          let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        return FileManager.default.fileExists(atPath: journal.path) ? "" : nil
    }
    return object["folder"] as? String ?? ""
}

// A folder install can come along only while its folder still holds it.
func folderHoldsInstall(_ folder: String) -> Bool {
    let files = FileManager.default
    return files.fileExists(atPath: folder + "/dictate.py") && files.fileExists(atPath: folder + "/setup.sh")
}

func initialScreen() -> Screen? {
    let drag = isDragApp()
    if drag && needsMove() { return .move }
    // A move into this app that stopped part way is finished or undone
    // before anything else is decided.
    if drag, let folder = interruptedAdoption() { return .adoptStuck("installer.adopt.interrupted", folder) }
    // A drag install whose app is now another release: offer to move the
    // install forward to this one, or say why this one cannot run it.
    if drag, let receipt = try? binding(), let match = installMatch(receipt, info: Bundle.main.infoDictionary ?? [:]) {
        switch match {
        case .update(let from, let to): return .update(from, to)
        case .older(let installed, let own): return .older(installed, own)
        case .conflict(let installed): return .conflict(installed)
        case .current: break
        }
    }
    // A folder install (every Mac install before 0.6.0): offer to move it
    // into this app, whether or not it still starts. One whose folder is
    // gone has nothing to bring along, so this app sets up afresh.
    if drag, let receipt = try? binding(), receipt.app == nil {
        return folderHoldsInstall(receipt.checkout) ? .adopt(receipt.checkout) : firstSetupScreen()
    }
    if (try? launchExistingRuntime(start: false)) != nil { return nil }
    guard receiptExists() else { return drag ? firstSetupScreen() : .unset }
    if let receipt = try? binding() {
        if receipt.app == nil { return .folder(receipt.checkout) }
        if !drag, let app = receipt.app { return .elsewhere(app) }
    }
    return drag ? .repair : .unset
}

// How much of a bar's track a picture shows filled: along the bar's middle
// row, the run of pixels from its left end that keep the fill's colour.
func drawnFraction(_ rep: NSBitmapImageRep, of bar: NSView, in view: NSView) -> Double {
    let rect = bar.convert(bar.bounds, to: view)
    let scale = CGFloat(rep.pixelsWide) / view.bounds.width
    let y = Int((view.isFlipped ? rect.midY : view.bounds.height - rect.midY) * scale)
    let left = Int(rect.minX * scale), right = Int(rect.maxX * scale)
    guard right - left > 8, let fill = rep.colorAt(x: left + 4, y: y)?.usingColorSpace(.sRGB),
          let track = rep.colorAt(x: right - 4, y: y)?.usingColorSpace(.sRGB) else { return 0 }
    func distance(_ a: NSColor, _ b: NSColor) -> CGFloat {
        abs(a.redComponent - b.redComponent) + abs(a.greenComponent - b.greenComponent) + abs(a.blueComponent - b.blueComponent)
    }
    // An empty or a full bar is one colour end to end.
    if distance(fill, track) < 0.06 { return bar is NSProgressIndicator && (bar as! NSProgressIndicator).doubleValue >= 0.99 ? 1 : 0 }
    var x = left + 4
    while x < right, let colour = rep.colorAt(x: x, y: y)?.usingColorSpace(.sRGB), distance(colour, fill) < distance(colour, track) { x += 1 }
    return Double(x - left) / Double(right - left)
}

// Draws every screen into PNGs, in both appearances, without showing a
// window, for scripts/window_render_probe.py --installer.
func renderSetupWindow(to directory: String) -> Int32 {
    _ = NSApplication.shared
    let controller = SetupController()
    let samples: [(String, Screen, () -> Void)] = [
        ("welcome", .welcome, {}),
        ("move", .move, {}),
        ("tools", .tools, {}),
        ("tools-waiting", .toolsWaiting, {}),
        ("space", .space, {}),
        ("running", .running, { controller.completed = ["prepare", "python", "speech-model"]; controller.currentPhase = "helpers"; controller.startedAt = Date().addingTimeInterval(-7 * 60) }),
        ("failed", .failed("installer.reason.network"), { controller.completed = ["prepare"]; controller.currentPhase = "python" }),
        ("failed-before-start", .failed("installer.reason.damaged"), { controller.completed = []; controller.currentPhase = nil }),
        ("repair", .repair, {}),
        ("folder", .folder("/Users/you/Projects/whisper-face"), { controller.canAdopt = false }),
        ("folder-keep", .folder("/Users/you/Projects/whisper-face"), { controller.canAdopt = true }),
        ("adopt", .adopt("/Users/you/Projects/whisper-face"), {}),
        ("adopting", .adopting("/Users/you/Projects/whisper-face"), { controller.completed = ["prepare", "python", "speech-model"]; controller.currentPhase = "helpers" }),
        ("adopt-failed", .adoptFailed("installer.reason.tools", "/Users/you/Projects/whisper-face"), { controller.completed = ["prepare", "python", "speech-model"]; controller.currentPhase = "helpers" }),
        ("adopt-stuck", .adoptStuck("installer.adopt.interrupted", "/Users/you/Projects/whisper-face"), { controller.completed = []; controller.currentPhase = nil }),
        ("adopted", .adopted("/Users/you/Projects/whisper-face"), { controller.completed = []; controller.currentPhase = nil }),
        ("elsewhere", .elsewhere("/Applications/Whisper Face.app"), {}),
        ("unset", .unset, {}),
        ("update", .update("0.6.0", "0.6.1"), {}),
        ("updating", .updating("0.6.0", "0.6.1"), { controller.completed = ["prepare", "python"]; controller.currentPhase = "speech-model" }),
        ("update-failed", .updateFailed("installer.reason.network", "0.6.0"), { controller.completed = ["prepare"]; controller.currentPhase = "python" }),
        ("update-stuck", .updateStuck("installer.reason.generic"), { controller.completed = ["prepare", "python", "speech-model", "helpers", "preview-model"]; controller.currentPhase = "service" }),
        ("older", .older("0.6.1", "0.6.0"), {}),
        ("conflict", .conflict("0.6.1"), {}),
    ]
    guard let view = controller.window.contentView else { return 1 }
    for (name, screen, prepare) in samples {
        for (suffix, appearanceName) in [("light", NSAppearance.Name.aqua), ("dark", NSAppearance.Name.darkAqua)] {
            guard let appearance = NSAppearance(named: appearanceName) else { return 1 }
            controller.window.appearance = appearance
            prepare(); controller.show(screen)
            // The failure screen offers Show Log only once a log exists; the
            // sample shows the button the way a real failure would.
            switch screen {
            case .failed, .updateFailed, .updateStuck, .adoptFailed, .adoptStuck:
                if controller.currentPhase != nil { controller.secondary.isHidden = false; controller.secondary.title = localized("installer.failed.log") }
            default: break
            }
            view.layoutSubtreeIfNeeded()
            // cacheDisplay draws through the window's own appearance, so the
            // theme's dynamic colours resolve exactly as on screen.
            guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return 1 }
            view.cacheDisplay(in: view.bounds, to: rep)
            // What the step bar actually drew, beside the value it holds,
            // so a test can hold the picture to the steps done.
            if !controller.stepBar.isHidden {
                print("step-bar \(name)-\(suffix) value=\(String(format: "%.3f", controller.stepBar.doubleValue)) drawn=\(String(format: "%.3f", drawnFraction(rep, of: controller.stepBar, in: view)))")
            }
            guard let png = rep.representation(using: .png, properties: [:]) else { return 1 }
            do { try png.write(to: URL(fileURLWithPath: directory).appendingPathComponent("installer-\(name)-\(suffix).png")) } catch { return 1 }
        }
    }
    return 0
}

@main struct WhisperFaceLauncher {
    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.first == "--run" { runWorker(Array(arguments.dropFirst())) }
        if arguments.count == 2 && arguments[0] == "--render-setup-window" { exit(renderSetupWindow(to: arguments[1])) }
        if arguments.count == 2 && arguments[0] == "--install-match" { print(installMatchLine(arguments[1])); exit(0) }
        if arguments.count == 3 && arguments[0] == "--setup-progress", let done = Int(arguments[1]), let seconds = Int(arguments[2]) {
            let (fraction, text) = setupProgress(done: done, total: setupPhases.count, seconds: seconds)
            print(String(format: "%.3f", fraction), text); exit(0)
        }
        if arguments == ["--verify"] {
            do { try launchExistingRuntime(start: false); exit(0) } catch {
                FileHandle.standardError.write(Data("Whisper Face launcher: this installation does not verify\n".utf8)); exit(1)
            }
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        var screen = initialScreen()
        if screen == nil {
            do { try launchExistingRuntime(start: true); return } catch { screen = isDragApp() ? .repair : .unset }
        }
        // A window needs the Dock and Command-Tab while it is open.
        app.setActivationPolicy(.regular)
        app.mainMenu = setupMenu()
        let controller = SetupController()
        let delegate = SetupAppDelegate(controller)
        app.delegate = delegate
        controller.show(screen ?? .unset)
        controller.window.center(); controller.window.makeKeyAndOrderFront(nil)
        app.activate(ignoringOtherApps: true)
        // The menu's Download and Install opens the new app with --update:
        // the person already said yes, so it starts at once.
        if arguments == ["--update"], case .update = screen ?? .unset { controller.beginUpdate() }
        withExtendedLifetime(delegate) { app.run() }
    }
}
'''

class LauncherError(ValueError):
    pass


def plain_reason(lines: list[str]) -> str:
    """The catalog key the setup window shows for a failed setup's output.

    The Swift ``plainReason`` does exactly this with the same tables; this
    copy is what the tests hold it to.
    """
    reasons = [line for line in lines if line.startswith("!! ")
               and not any(line.startswith(banner) for banner in FAILURE_BANNERS)]
    if reasons:
        for needle, key in FAILURE_REASONS:
            if needle in reasons[-1]:
                return key
    tail = "\n".join(lines[-80:])
    for needle, key in FAILURE_REASONS:
        if key == NETWORK_REASON and needle in tail:
            return key
    return "installer.reason.generic"


def install_match(receipt: dict, info: dict) -> str:
    """How an app's release stands to the install a receipt describes.

    The Swift ``installMatch`` does exactly this, and ``--install-match``
    prints its answer in the same words, which is what the tests compare.
    The version order is the advisor's own (``safe_update_advisor``).
    """
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import safe_update_advisor as advisor

    if not isinstance(receipt, dict) or receipt.get("bundle_id") != BUNDLE_ID \
            or receipt.get("schema_version") != 2:
        return "none"
    checkout, revision, app = (receipt.get("checkout"), receipt.get("source_revision"),
                               receipt.get("app"))
    if not (isinstance(checkout, str) and checkout.startswith("/") and "\n" not in checkout
            and isinstance(revision, str) and REVISION.fullmatch(revision)
            and isinstance(app, str) and app.startswith("/") and "\n" not in app):
        return "none"
    own_revision, own_version = info.get("WFSourceRevision"), info.get("CFBundleShortVersionString")
    if not isinstance(own_revision, str) or not isinstance(own_version, str):
        return "none"
    if revision == own_revision:
        return "current"
    installed = receipt.get("version")
    try:
        mine = advisor._version(own_version, "own")
        theirs = advisor._version(installed, "installed") if isinstance(installed, str) else None
    except advisor.AdviceError:
        theirs = None
    if theirs is None:
        return f"conflict {installed if isinstance(installed, str) else ''}"
    order = advisor._compare(mine, theirs)
    if order > 0:
        return f"update {installed} {own_version}"
    if order < 0:
        return f"older {installed} {own_version}"
    return f"conflict {installed}"


def _swift_string(value: str) -> str:
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\t", "\\t"))
    return f'"{escaped}"'


def _swift_number(value: float) -> str:
    return f"{float(value):.4f}"


def setup_copy() -> dict[str, str]:
    """The setup window's strings, from the window's own catalog."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from whisper_face_gui import STRING_CATALOGS
    return {key: value for key, value in STRING_CATALOGS["en"].items()
            if key.startswith("installer.")}


def setup_theme() -> dict[str, list[float]]:
    """RGBA for light, dark and both Increase Contrast appearances."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from whisper_face_gui import ERROR_TEXT_ON_LIGHT
    from whisper_face_theme import COLOR_TOKENS, palette_for_appearance

    def plain(light, dark):
        return [*light, 1.0, *dark, 1.0, *light, 1.0, *dark, 1.0]

    light, dark = palette_for_appearance(False), palette_for_appearance(True)
    brand = COLOR_TOKENS["brand_ink"]
    brand_values = []
    for dark_mode, contrast in ((False, False), (True, False),
                                (False, True), (True, True)):
        shade = brand.shade(dark_mode, contrast)
        brand_values += [*shade.color, shade.alpha]
    return {
        "window": plain(light.bg, dark.bg),
        "ink": plain(light.ink, dark.ink),
        "ink_soft": plain(light.ink_soft, dark.ink_soft),
        "brand_ink": brand_values,
        "error_ink": plain(ERROR_TEXT_ON_LIGHT, dark.error),
    }


def _setup_face_svg() -> str:
    try:
        text = SETUP_FACE.read_text(encoding="utf-8")
    except OSError as exc:
        raise LauncherError("the setup window's face is missing from the checkout") from exc
    svg = text[text.index("<svg"):].strip()
    if '"""#' in svg or not svg.endswith("</svg>"):
        raise LauncherError("the setup window's face is not a plain SVG")
    return svg


@functools.lru_cache(maxsize=1)
def swift_source() -> str:
    """The launcher's Swift source with the catalog and theme compiled in."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from whisper_face_theme import TYPE_SPECS
    copy_rows = ",\n".join(
        f"    {_swift_string(key)}: {_swift_string(value)}"
        for key, value in sorted(setup_copy().items()))
    theme_rows = ",\n".join(
        f"    {_swift_string(name)}: [{', '.join(_swift_number(v) for v in values)}]"
        for name, values in sorted(setup_theme().items()))
    type_rows = ",\n".join(
        f"    {_swift_string(role)}: [{_swift_number(TYPE_SPECS[spec].size)}, "
        f"{_swift_number(TYPE_SPECS[spec].weight)}, "
        f"{_swift_number(1.0 if TYPE_SPECS[spec].rounded else 0.0)}]"
        for role, spec in sorted(SETUP_TYPE_ROLES.items()))
    reason_rows = ",\n".join(
        f"    ({_swift_string(needle)}, {_swift_string(key)})"
        for needle, key in FAILURE_REASONS)

    def strings(values):
        return ", ".join(_swift_string(value) for value in values)

    source = SWIFT_TEMPLATE
    for marker, value in (
            ("/*COPY_TABLE*/", copy_rows),
            ("/*THEME_TABLE*/", theme_rows),
            ("/*TYPE_TABLE*/", type_rows),
            ("/*FAILURE_REASONS*/", reason_rows),
            ("/*NETWORK_REASON*/", _swift_string(NETWORK_REASON)),
            ("/*FAILURE_BANNERS*/", strings(FAILURE_BANNERS)),
            ("/*SETUP_PHASES*/", strings(SETUP_PHASES)),
            ("/*FINISHED_PHASES*/", strings(FINISHED_PHASES)),
            ("/*PYTHON_VERSION*/", _swift_string(SETUP_PYTHON)),
            ("/*FACE_SVG*/", _setup_face_svg())):
        if source.count(marker) != 1:
            raise LauncherError(f"launcher template marker {marker} is not unique")
        source = source.replace(marker, value)
    return source


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
    return hashlib.sha256(swift_source().encode()).hexdigest()

def _compile(destination: Path) -> None:
    swiftc = shutil.which("swiftc")
    if not swiftc: raise LauncherError("swiftc is required to build the Mac launcher")
    source = destination.parent / ".WhisperFaceLauncher.swift"
    source.write_text(swift_source())
    try:
        result = subprocess.run([swiftc, "-parse-as-library", "-module-name", "WhisperFaceLauncher", "-target", "arm64-apple-macos14.0", "-O", "-whole-module-optimization", "-framework", "AppKit", "-o", str(destination), str(source)], text=True, capture_output=True)
        if result.returncode: raise LauncherError(result.stderr.strip() or "Swift compiler failed")
    finally:
        source.unlink(missing_ok=True)
    os.chmod(destination, 0o755)

def _adhoc_sign(app: Path) -> None:
    result = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(app)], text=True, capture_output=True)
    if result.returncode: raise LauncherError(result.stderr.strip() or "ad-hoc code signing failed")

def signing_identity(policy: Path = DEFAULT_POLICY) -> tuple[str, str] | None:
    """The Developer ID this Mac can sign the launcher with, as (SHA-1, name),
    or None: no valid "Developer ID Application" identity of the pinned team
    is in the keychain, no team is pinned, or signing is turned off."""
    if os.environ.get(SIGNING_ENVIRONMENT, "").strip().lower() == "adhoc": return None
    try: team = _pinned_team(policy)
    except (LauncherError, OSError, ValueError): return None
    try: listed = subprocess.run([SECURITY, "find-identity", "-v", "-p", "codesigning"], text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=IDENTITY_TIMEOUT)
    except (OSError, subprocess.SubprocessError): return None
    if listed.returncode: return None
    for line in listed.stdout.splitlines():
        found = DEVELOPER_ID_LINE.match(line)
        if found and found.group(3) == team: return found.group(1), found.group(2)
    return None

def _developer_id_sign(app: Path, identity: tuple[str, str]) -> bool:
    """Sign the staged launcher as package_macos.sh signs a release's: the
    hardened runtime, a secure timestamp and the microphone entitlement.
    Returns False, after one plain line, when codesign fails or does not
    finish in time; the caller then signs ad hoc."""
    sha1, name = identity
    command = [CODESIGN, "--force", "--options", "runtime", "--timestamp", "--entitlements", str(LAUNCHER_ENTITLEMENTS), "--identifier", BUNDLE_ID, "--sign", sha1, str(app)]
    try:
        # subprocess.run kills codesign when the timeout passes.
        result = subprocess.run(command, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=SIGNING_TIMEOUT)
    except subprocess.TimeoutExpired:
        reason = f"codesign did not finish within {SIGNING_TIMEOUT} seconds, perhaps waiting on a keychain prompt"
    except OSError as exc:
        reason = str(exc)
    else:
        if result.returncode == 0: return True
        reason = (result.stderr.strip().splitlines() or ["codesign failed"])[-1]
    print(f"macOS launcher: could not sign with {name} ({reason}); signing it ad hoc instead", file=sys.stderr)
    return False

def describe_signature(app: Path, policy: Path = DEFAULT_POLICY) -> str:
    """One line for setup.sh --verify: who signed the launcher."""
    app = app.expanduser().resolve()
    if _is_adhoc_signed(app):
        identity = signing_identity(policy)
        if identity: return f"ad hoc; this Mac holds {identity[1]}, so the next ./setup.sh signs it"
        return "ad hoc; macOS asks for its permissions again whenever it is rebuilt"
    fields, flags, _ = _signature_facts(app)
    runtime = ", hardened runtime" if "runtime" in flags else ""
    return f"{fields.get('Authority', 'unknown signer')}, team {fields.get('TeamIdentifier', 'not set')}{runtime}"

def _entries(app: Path) -> set[str]:
    return {p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file() or p.is_symlink()}

def _bundle_digest(app: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(_entries(app)):
        path = app / relative; digest.update(relative.encode() + b"\0")
        digest.update(str(stat.S_IMODE(path.lstat().st_mode)).encode() + b"\0")
        digest.update(os.readlink(path).encode() if path.is_symlink() else path.read_bytes())
    return digest.hexdigest()

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()

def _expected_plist() -> dict[str, object]:
    return {"CFBundleDisplayName": PRODUCT, "CFBundleExecutable": EXECUTABLE, "CFBundleIconFile": ICON_NAME, "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": PRODUCT, "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "14.0", "LSUIElement": True, "NSMicrophoneUsageDescription": "Whisper Face transcribes your speech into text on this Mac."}

def _drag_plist(version: str, revision: str, archive_sha256: str, channel: str) -> dict[str, object]:
    """The drag-install app names its real release, the revision its archive
    holds, and the archive's digest, which the app checks before unpacking."""
    if not SEMVER.fullmatch(version): raise LauncherError("drag-install version must be SemVer")
    if not REVISION.fullmatch(revision): raise LauncherError("drag-install revision must be a full Git SHA-1")
    if not SHA256.fullmatch(archive_sha256): raise LauncherError("source archive digest must be lowercase SHA-256")
    if channel not in DRAG_CHANNELS: raise LauncherError("drag-install channel must be stable or preview")
    core = re.match(r"^\d+\.\d+\.\d+", version).group(0)
    return {**_expected_plist(), "CFBundleShortVersionString": version, "CFBundleVersion": core, "WFReleaseChannel": channel, "WFSourceArchiveSHA256": archive_sha256, "WFSourceRevision": revision}

def _icon_bytes(icon: Path = DEFAULT_ICON) -> bytes:
    """Read the committed brand icon. It is a static repository file, so the
    staged bundle stays byte-identical across builds of the same revision."""
    try: data = icon.read_bytes()
    except OSError as exc: raise LauncherError("launcher app icon is missing from the checkout") from exc
    if data[:4] != b"icns" or len(data) < 8 or int.from_bytes(data[4:8], "big") != len(data): raise LauncherError("launcher app icon is not a well-formed Apple icon set")
    return data

def _pinned_team(policy: Path = DEFAULT_POLICY) -> str:
    try: payload = json.loads(policy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise LauncherError("macOS signing policy is missing or invalid") from exc
    if set(payload) != {"developer_id_team_identifier", "schema_version"} or payload["schema_version"] != 1: raise LauncherError("macOS signing policy contract mismatch")
    team = payload["developer_id_team_identifier"]
    if not isinstance(team, str) or not TEAM_IDENTIFIER.fullmatch(team): raise LauncherError("pinned Developer ID team is not configured")
    return team

def _developer_id_requirement(team: str, identifier: str) -> str:
    return (
        f'anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists '
        f'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
        f'and certificate leaf[subject.OU] = "{team}" and identifier "{identifier}"'
    )

def _verify_pinned_signature(app: Path, policy: Path = DEFAULT_POLICY) -> None:
    team = _pinned_team(policy)
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", f"-R={_developer_id_requirement(team, BUNDLE_ID)}", str(app)], text=True, capture_output=True)
    if result.returncode: raise LauncherError("launcher does not satisfy the pinned Developer ID requirement")

def _verify_signed_launcher(app: Path, policy: Path = DEFAULT_POLICY) -> None:
    """A Developer ID launcher, released or built here: the pinned team's
    designated requirement, the hardened runtime, a secure timestamp and
    exactly the microphone entitlement."""
    _verify_pinned_signature(app, policy)
    try: entitlements = plistlib.loads(LAUNCHER_ENTITLEMENTS.read_bytes())
    except Exception as exc: raise LauncherError("launcher entitlements file is missing or invalid") from exc
    _verify_release_program(app, team=_pinned_team(policy), identifier=BUNDLE_ID, entitlements=entitlements)

def _signature_facts(path: Path) -> tuple[dict[str, str], set[str], dict[str, object]]:
    """What codesign reports about one signature: its fields (the leaf
    Authority first), its CodeDirectory flags, and its entitlements."""
    shown = subprocess.run(["/usr/bin/codesign", "--display", "--verbose=4", str(path)], text=True, capture_output=True)
    if shown.returncode: raise LauncherError(f"{path.name} is not signed")
    fields: dict[str, str] = {}; flags: set[str] = set()
    for line in shown.stderr.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in ("Authority", "Identifier", "TeamIdentifier", "Timestamp"): fields.setdefault(key, value.strip())
        found = re.search(r"\bflags=0x[0-9a-f]+\(([^)]*)\)", line) if line.startswith("CodeDirectory ") else None
        if found: flags = set(found.group(1).split(","))
    shown = subprocess.run(["/usr/bin/codesign", "--display", "--entitlements", "-", "--xml", str(path)], capture_output=True)
    if shown.returncode: raise LauncherError(f"{path.name}'s entitlements are unreadable")
    try: entitlements = plistlib.loads(shown.stdout) if shown.stdout.strip() else {}
    except Exception as exc: raise LauncherError(f"{path.name}'s entitlements are unreadable") from exc
    return fields, flags, entitlements

def _verify_release_program(path: Path, *, team: str, identifier: str, entitlements: dict[str, object]) -> None:
    """One program in a Developer ID release: the pinned team's Developer ID,
    the hardened runtime, a secure timestamp, and exactly these entitlements.
    Notarization refuses a program without the first three."""
    name = path.name
    fields, flags, granted = _signature_facts(path)
    if not fields.get("Authority", "").startswith("Developer ID Application: ") or fields.get("TeamIdentifier") != team: raise LauncherError(f"{name} is not signed with the pinned Developer ID")
    if fields.get("Identifier") != identifier: raise LauncherError(f"{name} is signed as {fields.get('Identifier')!r}, not {identifier!r}")
    if "runtime" not in flags: raise LauncherError(f"{name} is not signed with the hardened runtime")
    if not fields.get("Timestamp"): raise LauncherError(f"{name} carries no secure timestamp")
    if granted != entitlements: raise LauncherError(f"{name} carries entitlements {sorted(granted)}, not {sorted(entitlements)}")
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", f"-R={_developer_id_requirement(team, identifier)}", str(path)], text=True, capture_output=True)
    if result.returncode: raise LauncherError(f"{name} does not satisfy the pinned Developer ID requirement")

def _verify_release_programs(app: Path, policy: Path = DEFAULT_POLICY) -> None:
    """Every Mach-O in a Developer ID drag-install app is the launcher or uv,
    and each is signed the way notarization and the microphone need."""
    team = _pinned_team(policy)
    try: launcher_entitlements = plistlib.loads(LAUNCHER_ENTITLEMENTS.read_bytes())
    except Exception as exc: raise LauncherError("launcher entitlements file is missing or invalid") from exc
    expected = {f"Contents/MacOS/{EXECUTABLE}": (app, BUNDLE_ID, launcher_entitlements), UV_HELPER: (app / UV_HELPER, UV_IDENTIFIER, {})}
    for relative in sorted(_entries(app)):
        path = app / relative
        if path.is_symlink() or relative in expected: continue
        with path.open("rb") as handle:
            if handle.read(4) in MACHO_MAGICS: raise LauncherError(f"drag-install app carries an unexpected program: {relative}")
    for _relative, (path, identifier, entitlements) in sorted(expected.items()):
        _verify_release_program(path, team=team, identifier=identifier, entitlements=entitlements)

def _verify_archive_holds_no_programs(archive: Path) -> None:
    """The source archive inside the app holds no program and no nested
    archive. Apple's notary service opens archives it finds and refuses any
    program in them that lacks a Developer ID signature; signing one would
    change the published source, so none may be there."""
    try:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                if member.is_dir(): continue
                with source.open(member) as handle: head = handle.read(4)
                if head in MACHO_MAGICS: raise LauncherError(f"source archive carries a program: {member.filename}")
                if any(head.startswith(magic) for magic in NESTED_ARCHIVE_MAGICS): raise LauncherError(f"source archive carries a nested archive: {member.filename}")
    except (zipfile.BadZipFile, OSError) as exc: raise LauncherError("drag-install app source archive is unreadable") from exc

def _is_adhoc_signed(app: Path) -> bool:
    result = subprocess.run(["/usr/bin/codesign", "--display", "--verbose=2", str(app)], text=True, capture_output=True)
    return "adhoc" in (result.stdout + result.stderr)

def _verify_adhoc_signature(app: Path) -> None:
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", f'-R=identifier "{BUNDLE_ID}"', str(app)], text=True, capture_output=True)
    if result.returncode: raise LauncherError("launcher ad-hoc signature is invalid")

def _macho_minimum_macos(data: bytes) -> tuple[int, int] | None:
    """The minimum macOS a thin arm64 Mach-O names, or None if it names none."""
    if data[:4] != b"\xcf\xfa\xed\xfe" or int.from_bytes(data[4:8], "little") != 0x0100000C: raise LauncherError("helper is not a thin arm64 Mach-O")
    count = int.from_bytes(data[16:20], "little"); offset = 32
    for _ in range(count):
        command = int.from_bytes(data[offset:offset + 4], "little"); size = int.from_bytes(data[offset + 4:offset + 8], "little")
        if command == 0x32:  # LC_BUILD_VERSION
            minos = int.from_bytes(data[offset + 12:offset + 16], "little")
            return (minos >> 16, (minos >> 8) & 0xFF)
        if size <= 0: break
        offset += size
    return None

def _stage_bundle(app: Path, identity: tuple[str, str] | None = None, policy: Path = DEFAULT_POLICY) -> None:
    executable = app / "Contents/MacOS" / EXECUTABLE; executable.parent.mkdir(parents=True)
    resources = app / "Contents/Resources"; resources.mkdir()
    _atomic_write(app / "Contents/Info.plist", plistlib.dumps(_expected_plist(), sort_keys=True), 0o644)
    _compile(executable)
    _atomic_write(resources / "launcher-source-sha256", (_source_digest() + "\n").encode(), 0o644)
    # Ship the brand icon inside the bundle: without one macOS draws a blank
    # placeholder next to "Whisper Face" in the microphone and privacy panes.
    _atomic_write(app / ICON_FILE, _icon_bytes(), 0o644)
    # Ad-hoc sign the fully staged bundle so its signature is well-formed: macOS
    # then records a Designated Requirement and lists "Whisper Face" as a
    # grantable app in the privacy panes instead of the underlying interpreter.
    # The signature is deterministic (no timestamp), so byte-identical source
    # still yields a byte-identical signed bundle. Release automation replaces
    # this with a Developer ID signature via `codesign --force`, and so does a
    # folder install on a Mac that holds one (signing_identity).
    if identity is not None and _developer_id_sign(app, identity):
        try: _verify_signed_launcher(app, policy); return
        except (LauncherError, OSError) as exc: print(f"macOS launcher: the launcher signed with {identity[1]} does not pass its checks ({exc}); signing it ad hoc instead", file=sys.stderr)
    _adhoc_sign(app)

def build_app(app: Path, identity: tuple[str, str] | None = None, policy: Path = DEFAULT_POLICY) -> None:
    app = app.expanduser().resolve(); app.parent.mkdir(parents=True, exist_ok=True)
    if app.name != f"{PRODUCT}.app": raise LauncherError("launcher target is invalid")
    staging_root = Path(tempfile.mkdtemp(prefix=f".{app.name}.", dir=app.parent)); staging = staging_root / app.name
    try:
        _stage_bundle(staging, identity, policy)
        verify_generic_app(staging, policy=policy)
        if app.exists(): verify_owned_app(app); shutil.rmtree(app)
        os.replace(staging, app)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

def verify_owned_app(app: Path) -> None:
    if not app.is_dir() or app.name != f"{PRODUCT}.app": raise LauncherError("existing launcher is not an owned app")
    entries = _entries(app)
    known = (BASE_FILES, LEGACY_FILES, BASE_FILES | SIGNATURE_FILES, PRE_ICON_FILES, PRE_ICON_FILES | SIGNATURE_FILES,
             DRAG_FILES | SIGNATURE_FILES, DRAG_FILES | SIGNATURE_FILES | STAPLE_FILES)
    if entries not in known: raise LauncherError("existing launcher contains unexpected files")
    try: plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except Exception as exc: raise LauncherError("existing launcher Info.plist is invalid") from exc
    if plist.get("CFBundleIdentifier") != BUNDLE_ID or plist.get("CFBundleExecutable") != EXECUTABLE: raise LauncherError("existing launcher ownership markers do not match")

def _verify_launcher_core(app: Path) -> None:
    try: recorded_source = (app / "Contents/Resources/launcher-source-sha256").read_text(encoding="utf-8").strip()
    except ValueError as exc: raise LauncherError("launcher source contract is unreadable") from exc
    if recorded_source != _source_digest(): raise LauncherError("launcher source contract mismatch")
    # The icon is part of the launcher like its source: an app carrying an
    # earlier icon is stale, so the installer builds it again and the
    # receipt binds the new bundle. The drag-install app is checked the same
    # way, so its download carries this checkout's icon.
    try: shipped_icon = (app / ICON_FILE).read_bytes()
    except OSError as exc: raise LauncherError("launcher icon is unreadable") from exc
    if shipped_icon != _icon_bytes(): raise LauncherError("launcher icon differs from this checkout's")
    executable = app / "Contents/MacOS" / EXECUTABLE
    if not executable.stat().st_mode & stat.S_IXUSR or executable.read_bytes()[:4] != b"\xcf\xfa\xed\xfe": raise LauncherError("launcher executable is not an arm64 Mach-O")

def verify_generic_app(app: Path, *, policy: Path = DEFAULT_POLICY, require_signed: bool = False) -> None:
    app = app.expanduser().resolve(); entries = _entries(app) if app.is_dir() else set()
    if entries != BASE_FILES | SIGNATURE_FILES: raise LauncherError("generic launcher contains missing or unexpected files")
    try: plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except ValueError as exc: raise LauncherError("launcher Info.plist is invalid") from exc
    if plist != _expected_plist(): raise LauncherError("launcher Info.plist contract mismatch")
    _verify_launcher_core(app)
    if _is_adhoc_signed(app):
        # An ad-hoc signature seals the bundle but proves nothing about who
        # built it, so it is never trusted here on its own: an installed app
        # is bound by its receipt's digest (verify_installation) and a
        # packaged one by the digest its release recorded (install_app).
        # Recompiling to compare bytes is not used, because swiftc output
        # carries the toolchain and SDK versions and no other Mac reproduces
        # the release builder's binary.
        if require_signed: raise LauncherError("release launcher must be signed")
        _verify_adhoc_signature(app)
    else:
        _verify_signed_launcher(app, policy)
    for forbidden in ("checkout-path", "source-revision", "dictate.py", "setup.sh"):
        if any(p.name == forbidden for p in app.rglob("*")): raise LauncherError("launcher must not embed runtime source or machine binding")

def drag_app_info(app: Path) -> dict[str, object]:
    try: return plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError) as exc: raise LauncherError("drag-install app Info.plist is invalid") from exc

def verify_drag_app(app: Path, *, policy: Path = DEFAULT_POLICY, require_signed: bool = False, source_zip: Path | None = None, version: str | None = None, revision: str | None = None) -> dict[str, object]:
    """Check an app built for drag install. Its source archive must be the
    one its Info.plist seals by digest (and, when given, the published ZIP);
    an unsigned one is accepted only from a preview build."""
    app = app.expanduser().resolve(); entries = _entries(app) if app.is_dir() else set()
    if entries not in (DRAG_FILES | SIGNATURE_FILES, DRAG_FILES | SIGNATURE_FILES | STAPLE_FILES): raise LauncherError("drag-install app contains missing or unexpected files")
    plist = drag_app_info(app)
    try: expected = _drag_plist(str(plist.get("CFBundleShortVersionString")), str(plist.get("WFSourceRevision")), str(plist.get("WFSourceArchiveSHA256")), str(plist.get("WFReleaseChannel")))
    except LauncherError as exc: raise LauncherError(f"drag-install app Info.plist contract mismatch: {exc}") from exc
    if plist != expected: raise LauncherError("drag-install app Info.plist contract mismatch")
    if version is not None and plist["CFBundleShortVersionString"] != version: raise LauncherError("drag-install app is a different version")
    if revision is not None and plist["WFSourceRevision"] != revision: raise LauncherError("drag-install app holds a different revision")
    _verify_launcher_core(app)
    helper = app / UV_HELPER
    if not helper.stat().st_mode & stat.S_IXUSR: raise LauncherError("bundled uv is not executable")
    minimum = _macho_minimum_macos(helper.read_bytes())
    if minimum is not None and minimum > MACOS_MINIMUM: raise LauncherError("bundled uv needs a newer macOS than Whisper Face supports")
    archive = app / SOURCE_ARCHIVE
    if _file_sha256(archive) != plist["WFSourceArchiveSHA256"]: raise LauncherError("drag-install app source archive does not match its recorded digest")
    if source_zip is not None and _file_sha256(source_zip) != plist["WFSourceArchiveSHA256"]: raise LauncherError("drag-install app does not carry the published source archive")
    _verify_archive_holds_no_programs(archive)
    if _is_adhoc_signed(app):
        if require_signed: raise LauncherError("release launcher must be signed")
        if plist["WFReleaseChannel"] != "preview": raise LauncherError("an unsigned drag-install app must be a preview build")
        _verify_adhoc_signature(app)
    else:
        _verify_pinned_signature(app, policy)
        _verify_release_programs(app, policy)
    for forbidden in ("checkout-path", "source-revision", "dictate.py", "setup.sh"):
        if any(p.name == forbidden for p in app.rglob("*")): raise LauncherError("launcher must not embed runtime source or machine binding")
    return plist

def is_drag_app(app: Path) -> bool:
    return (app / SOURCE_ARCHIVE).exists()

def package_drag_app(app: Path, *, source_zip: Path, uv: Path, uv_licenses: Path, version: str, revision: str, channel: str, launcher: Path | None = None) -> None:
    """Assemble the drag-install app: the generic launcher (built here, or the
    one given), the release's source archive, uv and uv's licences, then an
    ad-hoc signature from the inside out. A release re-signs it the same way
    with its Developer ID."""
    app = app.expanduser().resolve(); app.parent.mkdir(parents=True, exist_ok=True)
    if app.name != f"{PRODUCT}.app": raise LauncherError("launcher target is invalid")
    source_zip = source_zip.resolve(); uv = uv.resolve(); uv_licenses = uv_licenses.resolve()
    if not source_zip.is_file(): raise LauncherError("source archive is missing")
    if not uv.is_file(): raise LauncherError("uv binary is missing")
    staging_root = Path(tempfile.mkdtemp(prefix=f".{app.name}.", dir=app.parent)); staging = staging_root / app.name
    try:
        if launcher is not None:
            verify_generic_app(launcher)
            subprocess.run(["/usr/bin/ditto", str(launcher), str(staging)], check=True)
        else:
            _stage_bundle(staging)
        plist = _drag_plist(version, revision, _file_sha256(source_zip), channel)
        _atomic_write(staging / "Contents/Info.plist", plistlib.dumps(plist, sort_keys=True), 0o644)
        _atomic_write(staging / UV_HELPER, uv.read_bytes(), 0o755)
        for name in UV_LICENSES:
            try: text = (uv_licenses / name).read_bytes()
            except OSError as exc: raise LauncherError(f"uv licence {name} is missing beside the uv binary") from exc
            _atomic_write(staging / f"Contents/Resources/ThirdParty/uv/{name}", text, 0o644)
        _atomic_write(staging / SOURCE_ARCHIVE, source_zip.read_bytes(), 0o644)
        sign = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(staging / UV_HELPER)], text=True, capture_output=True)
        if sign.returncode: raise LauncherError(sign.stderr.strip() or "ad-hoc signing uv failed")
        _adhoc_sign(staging)
        verify_drag_app(staging, source_zip=source_zip, version=version, revision=revision)
        if app.exists(): verify_owned_app(app); shutil.rmtree(app)
        os.replace(staging, app)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

def _receipt_payload(app: Path, checkout: Path) -> dict[str, object]:
    payload: dict[str, object] = {"app_bundle_sha256": _bundle_digest(app), "bundle_id": BUNDLE_ID, "checkout": str(checkout), "schema_version": 1, "source_revision": _revision(checkout)}
    if is_drag_app(app):
        info = drag_app_info(app)
        # Schema 2: the app that set this checkout up is the only one that
        # starts it, and setup.sh stays in app mode for this checkout.
        payload.update({"app": str(app), "schema_version": 2, "signed": not _is_adhoc_signed(app), "version": info.get("CFBundleShortVersionString")})
    return payload

def _write_receipt(receipt: Path, checkout: Path, app: Path) -> None:
    receipt = receipt.expanduser().resolve(); directory = receipt.parent
    directory.mkdir(parents=True, exist_ok=True); os.chmod(directory, 0o700)
    if directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) != 0o700: raise LauncherError("launcher receipt directory must be owned 0700")
    payload = _receipt_payload(app, checkout)
    _atomic_write(receipt, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(), 0o600)

def recorded_launcher_digest(checkout: Path) -> str | None:
    """The launcher digest a release bundle recorded, or None for a clone."""
    try: payload = json.loads((checkout / RELEASE_METADATA).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None
    digest = payload.get("launcher_bundle_sha256") if isinstance(payload, dict) else None
    return digest if isinstance(digest, str) and SHA256.fullmatch(digest) else None

def _verify_packaged_app(source_app: Path, checkout: Path, policy: Path) -> None:
    verify_generic_app(source_app, policy=policy)
    if _is_adhoc_signed(source_app):
        recorded = recorded_launcher_digest(checkout)
        if recorded is None: raise LauncherError("this checkout records no launcher digest")
        if _bundle_digest(source_app) != recorded: raise LauncherError("packaged launcher differs from the one its release recorded")

def _is_current(app: Path, policy: Path) -> bool:
    try:
        verify_owned_app(app)
        verify_generic_app(app, policy=policy)
    except (LauncherError, OSError):
        return False
    return True

def install_app(app: Path, checkout: Path, receipt: Path, source_app: Path | None, policy: Path) -> None:
    app = app.expanduser().resolve(); checkout = checkout.resolve(); app.parent.mkdir(parents=True, exist_ok=True)
    if source_app:
        source_app = source_app.expanduser().resolve()
        try:
            _verify_packaged_app(source_app, checkout, policy)
        except (LauncherError, OSError, ValueError) as exc:
            # A packaged app that cannot be accepted is not a reason to stop
            # an install that has already downloaded everything else.
            print(f"macOS launcher: not using the app beside this folder ({exc}); building one here instead", file=sys.stderr)
            source_app = None
    identity = signing_identity(policy)
    if source_app and identity and _is_adhoc_signed(source_app):
        # This Mac can build a launcher macOS knows by team and bundle ID,
        # which an ad-hoc copy can never be.
        source_app = None
    current = app.exists() and _is_current(app, policy)
    # On a Mac that can sign, a working ad-hoc launcher is replaced by a
    # signed one. That costs its permissions once; every later rebuild keeps
    # them, because the signed launcher's identity does not change.
    to_sign = current and identity is not None and _is_adhoc_signed(app)
    if source_app and current and not to_sign and (_is_adhoc_signed(source_app) or _bundle_digest(source_app) == _bundle_digest(app)):
        # An ad-hoc app's privacy identity is its code hash, so swapping one
        # working ad-hoc app for another costs the user every permission
        # grant. Only a Developer ID app, whose identity survives, replaces it.
        source_app = None
    if not source_app and current and not to_sign:
        # Keep the exact working bundle when launcher source is unchanged.
        _write_receipt(receipt, checkout, app)
        verify_installation(app, checkout, receipt, policy=policy)
        return
    staging_root = Path(tempfile.mkdtemp(prefix=f".{app.name}.", dir=app.parent)); staging = staging_root / app.name
    try:
        if source_app:
            subprocess.run(["/usr/bin/ditto", str(source_app), str(staging)], check=True)
            if _bundle_digest(source_app) != _bundle_digest(staging): raise LauncherError("installed launcher differs from packaged app")
        else:
            build_app(staging, identity, policy)
            if to_sign and _is_adhoc_signed(staging):
                # Signing failed. Swapping one working ad-hoc launcher for
                # another would cost its permissions for nothing: keep it,
                # and the next run tries to sign again.
                _write_receipt(receipt, checkout, app)
                verify_installation(app, checkout, receipt, policy=policy)
                return
            if not _is_adhoc_signed(staging): print(f"macOS launcher: signed with {identity[1]}")
        verify_generic_app(staging, policy=policy)
        if app.exists(): verify_owned_app(app); backup = app.with_name(f".{app.name}.previous-{os.getpid()}"); os.replace(app, backup)
        else: backup = None
        try: os.replace(staging, app)
        except BaseException:
            if backup: os.replace(backup, app)
            raise
        if backup: shutil.rmtree(backup)
    finally: shutil.rmtree(staging_root, ignore_errors=True)
    _write_receipt(receipt, checkout, app); verify_installation(app, checkout, receipt, policy=policy)

def adopt_app(app: Path, checkout: Path, receipt: Path, folder_launcher: Path | None, policy: Path, *, dry_run: bool = False) -> list[str]:
    """Bind a drag install to the app that set it up, where the person put it.

    Nothing is built or copied: the app is verified where it is, a locally
    built launcher left by an earlier folder install is retired (only once
    it proves to be ours), and the schema 2 receipt is written. A dry run
    reports the same plan and changes nothing. Returns the plan's lines.
    """
    app = app.expanduser().resolve(); checkout = checkout.resolve()
    info = verify_drag_app(app, policy=policy)
    revision = _revision(checkout)
    if info["WFSourceRevision"] != revision: raise LauncherError("this app installs a different release than the checkout holds")
    plan = [f"app: {app} ({'signed' if not _is_adhoc_signed(app) else 'unsigned preview'}, {info['CFBundleShortVersionString']})"]
    if folder_launcher is not None:
        folder_launcher = folder_launcher.expanduser()
        if folder_launcher.exists() and folder_launcher.resolve() != app:
            try:
                verify_owned_app(folder_launcher)
            except LauncherError:
                plan.append(f"keeping {folder_launcher}: it is not a launcher this installer built")
            else:
                plan.append(f"{'would remove' if dry_run else 'removing'} the locally built launcher {folder_launcher}")
                if not dry_run: shutil.rmtree(folder_launcher)
    plan.append(f"{'would write' if dry_run else 'writing'} receipt {receipt.expanduser()} binding {checkout} at {revision} to this app")
    if not dry_run:
        _write_receipt(receipt, checkout, app)
        verify_installation(app, checkout, receipt, policy=policy)
    return plan

def verify_installation(app: Path, checkout: Path, receipt: Path, *, policy: Path = DEFAULT_POLICY, runtime: bool = False) -> None:
    app = app.expanduser().resolve()
    if is_drag_app(app): verify_drag_app(app, policy=policy)
    else: verify_generic_app(app, policy=policy)
    receipt = receipt.expanduser().resolve(); directory = receipt.parent
    if stat.S_IMODE(directory.stat().st_mode) != 0o700 or directory.stat().st_uid != os.getuid(): raise LauncherError("launcher receipt directory is not strict 0700")
    if stat.S_IMODE(receipt.stat().st_mode) != 0o600 or receipt.stat().st_uid != os.getuid(): raise LauncherError("launcher receipt is not strict 0600")
    payload = json.loads(receipt.read_text()); checkout = checkout.resolve()
    if payload != _receipt_payload(app, checkout): raise LauncherError("launcher receipt binding is stale or invalid")
    if runtime:
        result = subprocess.run([str(app / "Contents/MacOS" / EXECUTABLE), "--verify"], capture_output=True)
        if result.returncode: raise LauncherError("compiled launcher runtime verification failed")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("command", choices=("build", "install", "verify", "digest", "package", "verify-drag", "adopt", "source", "signature")); parser.add_argument("--app"); parser.add_argument("--checkout"); parser.add_argument("--receipt"); parser.add_argument("--source-app"); parser.add_argument("--policy", default=str(DEFAULT_POLICY)); parser.add_argument("--installed-runtime", action="store_true"); parser.add_argument("--require-signed", action="store_true")
    parser.add_argument("--source-zip"); parser.add_argument("--uv"); parser.add_argument("--uv-licenses"); parser.add_argument("--version"); parser.add_argument("--revision"); parser.add_argument("--channel", default="stable"); parser.add_argument("--launcher"); parser.add_argument("--folder-launcher"); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "source": sys.stdout.write(swift_source()); return 0
        if not args.app: raise LauncherError("--app is required")
        if args.command == "digest": print(_bundle_digest(Path(args.app).expanduser().resolve())); return 0
        if args.command == "signature": print(describe_signature(Path(args.app), Path(args.policy))); return 0
        if args.command == "build": build_app(Path(args.app)); verify_generic_app(Path(args.app), policy=Path(args.policy), require_signed=args.require_signed)
        elif args.command == "install":
            if not args.checkout or not args.receipt: raise LauncherError("install requires --checkout and --receipt")
            install_app(Path(args.app), Path(args.checkout), Path(args.receipt), Path(args.source_app) if args.source_app else None, Path(args.policy))
        elif args.command == "package":
            if not (args.source_zip and args.uv and args.version and args.revision): raise LauncherError("package requires --source-zip, --uv, --version and --revision")
            uv = Path(args.uv).resolve()
            package_drag_app(Path(args.app), source_zip=Path(args.source_zip), uv=uv, uv_licenses=Path(args.uv_licenses) if args.uv_licenses else uv.parent.parent, version=args.version, revision=args.revision, channel=args.channel, launcher=Path(args.launcher) if args.launcher else None)
            print(f"packaged drag-install app: {Path(args.app).expanduser()}"); return 0
        elif args.command == "verify-drag":
            verify_drag_app(Path(args.app), policy=Path(args.policy), require_signed=args.require_signed, source_zip=Path(args.source_zip) if args.source_zip else None, version=args.version, revision=args.revision)
            print(f"verified drag-install app: {Path(args.app).expanduser()}"); return 0
        elif args.command == "adopt":
            if not args.checkout or not args.receipt: raise LauncherError("adopt requires --checkout and --receipt")
            for line in adopt_app(Path(args.app), Path(args.checkout), Path(args.receipt), Path(args.folder_launcher) if args.folder_launcher else None, Path(args.policy), dry_run=args.dry_run): print(f"== {line}")
            return 0
        else:
            if args.checkout and args.receipt: verify_installation(Path(args.app), Path(args.checkout), Path(args.receipt), policy=Path(args.policy), runtime=args.installed_runtime)
            else: verify_generic_app(Path(args.app), policy=Path(args.policy), require_signed=args.require_signed)
    except (LauncherError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"macOS launcher error: {exc}", file=sys.stderr); return 2
    print(f"verified launcher app: {Path(args.app).expanduser()}"); return 0

if __name__ == "__main__": raise SystemExit(main())
