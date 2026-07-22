#!/bin/bash
# Build the exact-source macOS release bundle, update manifest, and checksums.
# Local builds are unsigned by default. Release automation opts into Apple
# signing and notarization explicitly so missing credentials fail closed.
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
for command_name in git python3 ditto hdiutil shasum tar date; do
    command -v "$command_name" >/dev/null 2>&1 \
        || fail "required command is unavailable: $command_name"
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
        scripts/macos_launcher_app.py config/macos-signing-policy.json; do
    git -C "$REPO_DIR" cat-file -e "$FULL_REVISION:$required" \
        || fail "release revision is missing $required"
done

if [ -z "$DOWNLOAD_BASE_URL" ]; then
    DOWNLOAD_BASE_URL="https://github.com/Aiml3ss/whispering-parrot/releases/download/v$VERSION"
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
LAUNCHER_APP="$TEMP_ROOT/Whisper Face.app"
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
git -C "$BUNDLE_DIR" fetch -q --depth 1 "$REPO_DIR" "$FULL_REVISION"
git -C "$BUNDLE_DIR" update-ref refs/heads/packaged-release "$FULL_REVISION"
git -C "$BUNDLE_DIR" symbolic-ref HEAD refs/heads/packaged-release
git -C "$BUNDLE_DIR" read-tree "$FULL_REVISION"
rm -f "$BUNDLE_DIR/.git/FETCH_HEAD"
git -C "$BUNDLE_DIR" remote add origin \
    "https://github.com/Aiml3ss/whispering-parrot.git"
[ "$(git -C "$BUNDLE_DIR" rev-parse HEAD)" = "$FULL_REVISION" ] \
    || fail "packaged checkout lost its immutable source revision"
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

echo "== compiling generic launcher from the selected revision"
python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" build \
    --app "$LAUNCHER_APP"
if [ "$SIGN_RELEASE" -eq 1 ]; then
    echo "== signing generic launcher app"
    codesign --force --options runtime --timestamp \
        --sign "$APPLE_DEVELOPER_ID_APPLICATION" "$LAUNCHER_APP"
    codesign --verify --deep --strict --verbose=2 "$LAUNCHER_APP"
    python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" verify \
        --app "$LAUNCHER_APP" --require-signed
else
    python3 "$BUNDLE_DIR/scripts/macos_launcher_app.py" verify \
        --app "$LAUNCHER_APP"
fi

echo "== creating macOS disk image"
hdiutil create -quiet -fs APFS -format UDZO \
    -volname "$BUNDLE_NAME" -srcfolder "$TEMP_ROOT" "$DMG_PATH"

if [ "$SIGN_RELEASE" -eq 1 ]; then
    echo "== signing disk image containing the signed launcher"
    codesign --force --timestamp --sign "$APPLE_DEVELOPER_ID_APPLICATION" "$DMG_PATH"
    codesign --verify --strict --verbose=2 "$DMG_PATH"
    DMG_SIGNED=1
else
    DMG_SIGNED=0
    echo "== unsigned local build (not for public release)"
fi

if [ "$NOTARIZE_RELEASE" -eq 1 ]; then
    echo "== submitting disk image for Apple notarization"
    if [ -n "${APPLE_NOTARY_KEYCHAIN_PROFILE:-}" ]; then
        notary_auth_args=(--keychain-profile "$APPLE_NOTARY_KEYCHAIN_PROFILE")
    else
        notary_auth_args=(
            --apple-id "$APPLE_ID" \
            --team-id "$APPLE_TEAM_ID" \
            --password "$APPLE_APP_SPECIFIC_PASSWORD"
        )
    fi
    notary_result="$TEMP_ROOT/notary-result.json"
    notary_log="$TEMP_ROOT/notary-log.json"
    xcrun notarytool submit "$DMG_PATH" --wait --output-format json \
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
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$DMG_PATH"
    codesign --verify --strict --verbose=2 "$DMG_PATH"
    DMG_NOTARIZED=1
else
    DMG_NOTARIZED=0
fi

echo "== verifying ZIP and disk image contain the same exact source"
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
