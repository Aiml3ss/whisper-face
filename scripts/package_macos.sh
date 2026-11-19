#!/bin/bash
# Build the exact-source macOS release: the source ZIP, the drag-install disk
# image, the update manifest, and checksums.
#
# The disk image holds exactly two things: Whisper Face.app and a link to
# /Applications. The app carries the source ZIP built here, byte for byte,
# and a copy of uv; opened on a Mac with nothing installed, it unpacks that
# ZIP into a checkout and runs the checkout's own setup.sh --app.
#
# Local builds are unsigned by default, and an unsigned app must be a preview
# build. Release automation opts into Apple signing and notarization
# explicitly so missing credentials fail closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION=""
REVISION="HEAD"
OUTPUT_DIR="$REPO_DIR/dist"
CHANNEL="stable"
DOWNLOAD_BASE_URL=""
SIGN_RELEASE=0
NOTARIZE_RELEASE=0
PREVIOUS_VERSION=""
PREVIOUS_REVISION=""
PREVIOUS_MANIFEST_URL=""
UV_BINARY=""
UV_LICENSE_DIR=""

usage() {
    cat <<'EOF'
Usage: scripts/package_macos.sh --version X.Y.Z [options]

Options:
  --revision REF                 Git commit to package (default: HEAD)
  --output-dir DIR               Artifact directory (default: ./dist)
  --channel stable|preview       Update channel (default: stable)
  --download-base-url HTTPS_URL  Release asset base URL
  --sign                         Sign the launcher app and disk image
  --notarize                     Submit, wait, staple, and validate (implies --sign)
  --previous-version X.Y.Z       Previous safe release for rollback
  --previous-revision SHA        Previous release's full Git SHA
  --previous-manifest-url URL    Previous release manifest URL
  --uv PATH                      uv binary the app carries (default: uv on PATH)
  --uv-licenses DIR              Folder holding uv's LICENSE-MIT and
                                 LICENSE-APACHE (default: the folder above
                                 the uv binary's own, as Homebrew lays it out)
  -h, --help                     Show this help

Signing reads APPLE_DEVELOPER_ID_APPLICATION. Notarization reads either
APPLE_NOTARY_KEYCHAIN_PROFILE or APPLE_ID, APPLE_TEAM_ID, and
APPLE_APP_SPECIFIC_PASSWORD. Secrets are never written into artifacts.
EOF
}

fail() {
    echo "!! $*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:-}"; shift 2 ;;
        --revision) REVISION="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --channel) CHANNEL="${2:-}"; shift 2 ;;
        --download-base-url) DOWNLOAD_BASE_URL="${2:-}"; shift 2 ;;
        --sign) SIGN_RELEASE=1; shift ;;
        --notarize) NOTARIZE_RELEASE=1; SIGN_RELEASE=1; shift ;;
        --previous-version) PREVIOUS_VERSION="${2:-}"; shift 2 ;;
        --previous-revision) PREVIOUS_REVISION="${2:-}"; shift 2 ;;
        --previous-manifest-url) PREVIOUS_MANIFEST_URL="${2:-}"; shift 2 ;;
        --uv) UV_BINARY="${2:-}"; shift 2 ;;
        --uv-licenses) UV_LICENSE_DIR="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[ "$(uname -s)" = "Darwin" ] || fail "macOS packaging requires macOS"
[ -n "$VERSION" ] || fail "--version is required"
[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-+][0-9A-Za-z.-]+)?$ ]] \
    || fail "--version must be SemVer"
[ "$CHANNEL" = "stable" ] || [ "$CHANNEL" = "preview" ] \
    || fail "--channel must be stable or preview"
for command_name in git python3 ditto hdiutil shasum tar date codesign; do
    command -v "$command_name" >/dev/null 2>&1 \
        || fail "required command is unavailable: $command_name"
done
# The app carries uv, so setup on a new Mac needs no Homebrew. It must be a
# thin arm64 binary that runs on macOS 14; macos_launcher_app.py checks both.
if [ -z "$UV_BINARY" ]; then
    UV_BINARY="$(command -v uv 2>/dev/null || true)"
fi
[ -n "$UV_BINARY" ] && [ -x "$UV_BINARY" ] \
    || fail "uv is required: the drag-install app carries it (pass --uv PATH)"
UV_BINARY="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$UV_BINARY")"
if [ -z "$UV_LICENSE_DIR" ]; then
    UV_LICENSE_DIR="$(dirname "$(dirname "$UV_BINARY")")"
fi
for license_name in LICENSE-MIT LICENSE-APACHE; do
    [ -f "$UV_LICENSE_DIR/$license_name" ] \
        || fail "uv's $license_name is not in $UV_LICENSE_DIR; pass --uv-licenses DIR"
done

FULL_REVISION="$(git -C "$REPO_DIR" rev-parse --verify "${REVISION}^{commit}")" \
    || fail "revision does not identify a commit: $REVISION"
[[ "$FULL_REVISION" =~ ^[0-9a-f]{40}$ ]] \
    || fail "release revision must resolve to a full Git SHA-1"
SOURCE_DATE_EPOCH="$(git -C "$REPO_DIR" show -s --format=%ct "$FULL_REVISION")" \
    || fail "could not read the release revision timestamp"
[[ "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]] \
    || fail "release revision timestamp is invalid"
git -C "$REPO_DIR" cat-file -e "$FULL_REVISION:Install.command" \
    || fail "release revision does not contain Install.command"
for required in LICENSE LICENSE_POLICY.md NOTICE THIRD_PARTY_NOTICES.md setup.sh \
        scripts/macos_launcher_app.py config/macos-signing-policy.json \
        config/macos-launcher-entitlements.plist icons/WhisperFace.icns; do
    git -C "$REPO_DIR" cat-file -e "$FULL_REVISION:$required" \
        || fail "release revision is missing $required"
done

if [ -z "$DOWNLOAD_BASE_URL" ]; then
    DOWNLOAD_BASE_URL="https://github.com/Aiml3ss/whisper-face/releases/download/v$VERSION"
fi
[[ "$DOWNLOAD_BASE_URL" = https://* ]] \
    || fail "--download-base-url must use HTTPS"

previous_count=0
[ -z "$PREVIOUS_VERSION" ] || previous_count=$((previous_count + 1))
[ -z "$PREVIOUS_REVISION" ] || previous_count=$((previous_count + 1))
[ -z "$PREVIOUS_MANIFEST_URL" ] || previous_count=$((previous_count + 1))
[ "$previous_count" -eq 0 ] || [ "$previous_count" -eq 3 ] \
    || fail "all three previous-release options are required for rollback"

if [ "$SIGN_RELEASE" -eq 1 ]; then
    command -v codesign >/dev/null 2>&1 || fail "codesign is unavailable"
    [ -n "${APPLE_DEVELOPER_ID_APPLICATION:-}" ] \
        || fail "APPLE_DEVELOPER_ID_APPLICATION is required with --sign"
    [ -n "${APPLE_TEAM_ID:-}" ] \
        || fail "APPLE_TEAM_ID is required with --sign"
fi
if [ "$NOTARIZE_RELEASE" -eq 1 ]; then
    command -v xcrun >/dev/null 2>&1 || fail "xcrun is unavailable"
    command -v spctl >/dev/null 2>&1 || fail "spctl is unavailable"
    if [ -z "${APPLE_NOTARY_KEYCHAIN_PROFILE:-}" ]; then
        [ -n "${APPLE_ID:-}" ] \
            && [ -n "${APPLE_TEAM_ID:-}" ] \
            && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ] \
            || fail "notarization needs a keychain profile or all Apple ID credentials"
    fi
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/whisper-face-release.XXXXXX")"
trap 'rm -rf "$TEMP_ROOT"' EXIT

BUNDLE_NAME="Whisper Face $VERSION"
BUNDLE_DIR="$TEMP_ROOT/$BUNDLE_NAME"
ZIP_NAME="WhisperFace-$VERSION-source.zip"
DMG_NAME="WhisperFace-$VERSION-macOS-arm64.dmg"
ZIP_PATH="$OUTPUT_DIR/$ZIP_NAME"
DMG_PATH="$OUTPUT_DIR/$DMG_NAME"
# Everything in this folder is on the disk image, and nothing else is.
DMG_ROOT="$TEMP_ROOT/disk-image"
LAUNCHER_APP="$DMG_ROOT/Whisper Face.app"
# The volume name carries no version, so it stays the same from release to
# release.
VOLUME_NAME="Whisper Face"
MANIFEST_PATH="$OUTPUT_DIR/update-manifest.json"
CHECKSUM_PATH="$OUTPUT_DIR/SHA256SUMS"
PUBLISHED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "== exporting exact source revision $FULL_REVISION"
mkdir -p "$BUNDLE_DIR"
git -C "$REPO_DIR" archive "$FULL_REVISION" | tar -x -C "$BUNDLE_DIR"
# A bare source archive is insufficient for the runtime's immutable /source
# response. Add only the selected commit's shallow Git metadata, discard the
# local fetch path, and identify the public repository as origin.
git -C "$BUNDLE_DIR" init -q
git -C "$BUNDLE_DIR" config core.logAllRefUpdates false
# Receive the objects loose, not as the pack upload-pack sends. That pack's
# bytes follow the source repository's own packing state and a threaded delta
# search, so two builds of one revision shipped different .git/objects/pack
# files and PACKAGE-CONTENTS.json, which hashes every shipped file, was not
# reproducible. A loose object's bytes follow only its content and the pinned
# zlib level. Automatic maintenance stays off so nothing repacks the objects,
# in the background or otherwise, while the tree is staged and stamped.
git -C "$BUNDLE_DIR" \
    -c fetch.unpackLimit=2147483647 \
    -c core.looseCompression=1 \
    -c fetch.writeCommitGraph=false \
    -c gc.auto=0 \
    -c maintenance.auto=false \
    fetch -q --depth 1 "$REPO_DIR" "$FULL_REVISION"
git -C "$BUNDLE_DIR" update-ref refs/heads/packaged-release "$FULL_REVISION"
git -C "$BUNDLE_DIR" symbolic-ref HEAD refs/heads/packaged-release
git -C "$BUNDLE_DIR" read-tree "$FULL_REVISION"
# .gitattributes keeps development-only trees out of the archive. Mark those
# index entries skip-worktree so the shipped checkout reads as clean to the
# package verifier and to the in-app updater.
git -C "$BUNDLE_DIR" ls-files -z \
    | git -C "$BUNDLE_DIR" check-attr -z --stdin export-ignore \
    | python3 -c 'import sys
fields = sys.stdin.buffer.read().split(b"\0")
for index in range(0, len(fields) - 2, 3):
    if fields[index + 2] == b"set":
        sys.stdout.buffer.write(fields[index] + b"\0")' \
    | git -C "$BUNDLE_DIR" update-index -z --skip-worktree --stdin
rm -f "$BUNDLE_DIR/.git/FETCH_HEAD"
git -C "$BUNDLE_DIR" remote add origin \
    "https://github.com/Aiml3ss/whisper-face.git"
[ "$(git -C "$BUNDLE_DIR" rev-parse HEAD)" = "$FULL_REVISION" ] \
    || fail "packaged checkout lost its immutable source revision"
unstable_objects="$(cd "$BUNDLE_DIR/.git/objects" \
    && find . -type f ! -path './[0-9a-f][0-9a-f]/*')"
[ -z "$unstable_objects" ] \
    || fail "packaged Git metadata holds objects that are not loose: $unstable_objects"
if [ "$SIGN_RELEASE" -eq 1 ]; then
    python3 - "$BUNDLE_DIR/config/macos-signing-policy.json" \
            "$APPLE_DEVELOPER_ID_APPLICATION" "$APPLE_TEAM_ID" <<'PY' \
        || fail "release credentials do not match the selected revision's pinned Developer ID policy"
import json, re, sys
policy = json.load(open(sys.argv[1], encoding="utf-8"))
team = policy.get("developer_id_team_identifier")
identity, supplied_team = sys.argv[2:]
if not isinstance(team, str) or not re.fullmatch(r"[A-Z0-9]{10}", team):
    raise SystemExit("pinned Developer ID team is not configured")
if supplied_team != team:
    raise SystemExit("APPLE_TEAM_ID does not match the pinned team")
if not identity.startswith("Developer ID Application: ") or not identity.endswith(f" ({team})"):
    raise SystemExit("signing identity is not the pinned Developer ID Application identity")
PY
fi
python3 "$SCRIPT_DIR/release_manifest.py" source-metadata \
    --version "$VERSION" \
    --revision "$FULL_REVISION" \
    --output "$BUNDLE_DIR/RELEASE-METADATA.json"
python3 "$SCRIPT_DIR/verify_macos_package.py" stamp \
    --root "$BUNDLE_DIR" \
    --version "$VERSION" \
    --revision "$FULL_REVISION" \
    --source-date-epoch "$SOURCE_DATE_EPOCH"

echo "== creating source archive"
rm -f "$ZIP_PATH" "$DMG_PATH" "$MANIFEST_PATH" "$CHECKSUM_PATH"
ditto -c -k --norsrc --keepParent "$BUNDLE_DIR" "$ZIP_PATH"

if [ "$NOTARIZE_RELEASE" -eq 1 ]; then
    if [ -n "${APPLE_NOTARY_KEYCHAIN_PROFILE:-}" ]; then
        notary_auth_args=(--keychain-profile "$APPLE_NOTARY_KEYCHAIN_PROFILE")
    else
        notary_auth_args=(
            --apple-id "$APPLE_ID" \
            --team-id "$APPLE_TEAM_ID" \
            --password "$APPLE_APP_SPECIFIC_PASSWORD"
        )
    fi
fi

notarize_submission() {
    # $1 = file to submit, $2 = short name for its result files.
    local submission="$1" label="$2" notary_result notary_log notary_id
    notary_result="$TEMP_ROOT/notary-$label-result.json"
    notary_log="$TEMP_ROOT/notary-$label-log.json"
    xcrun notarytool submit "$submission" --wait --output-format json \
        "${notary_auth_args[@]}" > "$notary_result"
    notary_id="$(python3 -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("status") != "Accepted" or not payload.get("id"):
    raise SystemExit("Apple did not accept the notarization submission")
print(payload["id"])
' "$notary_result")"
    xcrun notarytool log "$notary_id" \
        "${notary_auth_args[@]}" "$notary_log"
    python3 -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
issues = payload.get("issues", [])
if not isinstance(issues, list):
    raise SystemExit("Apple notarization log has an unexpected format")
for issue in issues:
    print("notary issue:", issue, file=sys.stderr)
if issues:
    raise SystemExit("Apple accepted the submission but reported issues")
' "$notary_log"
}

# The app is built from the selected revision's own launcher source, around
# the source archive just made: the archive's digest is sealed into its
# Info.plist, and the app checks it before unpacking anything.
echo "== building the drag-install app from the selected revision"
mkdir -p "$DMG_ROOT"
python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" package \
    --app "$LAUNCHER_APP" \
    --source-zip "$ZIP_PATH" \
    --uv "$UV_BINARY" \
    --uv-licenses "$UV_LICENSE_DIR" \
    --version "$VERSION" \
    --revision "$FULL_REVISION" \
    --channel "$CHANNEL"
if [ "$SIGN_RELEASE" -eq 1 ]; then
    # Inside out: uv first, then the app that seals it. Both get the
    # hardened runtime and a secure timestamp, which notarization requires;
    # the app also gets the microphone entitlement, without which a hardened
    # app cannot hear, and nothing else. uv gets no entitlement and its own
    # identifier rather than one codesign would derive from the file name.
    # verify-drag --require-signed then checks every program in the app for
    # exactly this.
    echo "== signing the drag-install app"
    codesign --force --options runtime --timestamp \
        --identifier com.berg.whisper-face.uv \
        --sign "$APPLE_DEVELOPER_ID_APPLICATION" \
        "$LAUNCHER_APP/Contents/Helpers/uv"
    codesign --force --options runtime --timestamp \
        --entitlements "$BUNDLE_DIR/config/macos-launcher-entitlements.plist" \
        --identifier com.berg.whisper-face.launcher \
        --sign "$APPLE_DEVELOPER_ID_APPLICATION" "$LAUNCHER_APP"
    codesign --verify --deep --strict --verbose=2 "$LAUNCHER_APP"
    python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" verify-drag \
        --app "$LAUNCHER_APP" --require-signed --source-zip "$ZIP_PATH" \
        --version "$VERSION" --revision "$FULL_REVISION"
else
    python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" verify-drag \
        --app "$LAUNCHER_APP" --source-zip "$ZIP_PATH" \
        --version "$VERSION" --revision "$FULL_REVISION"
fi
if [ "$NOTARIZE_RELEASE" -eq 1 ]; then
    # Staple the app itself, not only the disk image: once it is dragged
    # out of the image, the app's own ticket is what Gatekeeper reads.
    echo "== notarizing and stapling the app before it goes on the disk image"
    app_submission="$TEMP_ROOT/WhisperFace-app-notarize.zip"
    ditto -c -k --keepParent "$LAUNCHER_APP" "$app_submission"
    notarize_submission "$app_submission" app
    xcrun stapler staple "$LAUNCHER_APP"
    xcrun stapler validate "$LAUNCHER_APP"
    # Gatekeeper's own verdict on the app a person drags out of the image.
    spctl --assess --type execute --verbose=2 "$LAUNCHER_APP"
    python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" verify-drag \
        --app "$LAUNCHER_APP" --require-signed --source-zip "$ZIP_PATH" \
        --version "$VERSION" --revision "$FULL_REVISION"
fi

echo "== creating macOS disk image"
ln -s /Applications "$DMG_ROOT/Applications"
hdiutil create -quiet -fs APFS -format UDZO \
    -volname "$VOLUME_NAME" -srcfolder "$DMG_ROOT" "$DMG_PATH"

if [ "$SIGN_RELEASE" -eq 1 ]; then
    echo "== signing disk image containing the signed app"
    codesign --force --timestamp --sign "$APPLE_DEVELOPER_ID_APPLICATION" "$DMG_PATH"
    codesign --verify --strict --verbose=2 "$DMG_PATH"
    DMG_SIGNED=1
else
    DMG_SIGNED=0
    echo "== unsigned local build (not for public release)"
fi

if [ "$NOTARIZE_RELEASE" -eq 1 ]; then
    echo "== submitting disk image for Apple notarization"
    notarize_submission "$DMG_PATH" disk-image
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$DMG_PATH"
    codesign --verify --strict --verbose=2 "$DMG_PATH"
    spctl --assess --type open --context context:primary-signature \
        --verbose=2 "$DMG_PATH"
    DMG_NOTARIZED=1
else
    DMG_NOTARIZED=0
fi

echo "== verifying the ZIP and the app on the disk image carry the same exact source"
python3 "$SCRIPT_DIR/verify_macos_package.py" verify-artifacts \
    --source-zip "$ZIP_PATH" \
    --disk-image "$DMG_PATH" \
    --version "$VERSION" \
    --revision "$FULL_REVISION"

echo "== creating update and rollback metadata"
manifest_args=(
    create
    --version "$VERSION"
    --revision "$FULL_REVISION"
    --channel "$CHANNEL"
    --published-at "$PUBLISHED_AT"
    --download-base-url "$DOWNLOAD_BASE_URL"
    --artifact "$ZIP_PATH"
    --artifact "$DMG_PATH"
    --output "$MANIFEST_PATH"
)
if [ "$DMG_SIGNED" -eq 1 ]; then
    manifest_args+=(--signed-artifact "$DMG_NAME")
fi
if [ "$DMG_NOTARIZED" -eq 1 ]; then
    manifest_args+=(--notarized-artifact "$DMG_NAME")
fi
if [ "$previous_count" -eq 3 ]; then
    manifest_args+=(
        --previous-version "$PREVIOUS_VERSION"
        --previous-revision "$PREVIOUS_REVISION"
        --previous-manifest-url "$PREVIOUS_MANIFEST_URL"
    )
fi
python3 "$SCRIPT_DIR/release_manifest.py" "${manifest_args[@]}"
python3 "$SCRIPT_DIR/release_manifest.py" verify \
    --manifest "$MANIFEST_PATH" --artifact-dir "$OUTPUT_DIR"
python3 "$SCRIPT_DIR/release_manifest.py" checksums \
    --file "$ZIP_PATH" \
    --file "$DMG_PATH" \
    --file "$MANIFEST_PATH" \
    --output "$CHECKSUM_PATH"

echo "== release artifacts"
shasum -a 256 "$ZIP_PATH" "$DMG_PATH" "$MANIFEST_PATH"
echo "== checksum file: $CHECKSUM_PATH"
echo "== source revision: $FULL_REVISION"
