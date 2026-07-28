#!/bin/bash
# Build the exact-source Windows release bundle, update manifest, and checksums.
#
# Whisper Face has no Authenticode certificate, so every artifact this script
# produces is unsigned and always will be until one exists. There is no --sign
# or --notarize counterpart to scripts/package_macos.sh, and nothing here
# claims a publisher.
#
# It uses only cross-platform tooling so the Windows download can be built and
# audited from macOS: no Wine, no PowerShell, no Windows machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION=""
REVISION="HEAD"
OUTPUT_DIR="$REPO_DIR/dist/windows"
CHANNEL="stable"
DOWNLOAD_BASE_URL=""
PREVIOUS_VERSION=""
PREVIOUS_REVISION=""
PREVIOUS_MANIFEST_URL=""

usage() {
    cat <<'EOF'
Usage: scripts/package_windows.sh --version X.Y.Z [options]

Options:
  --revision REF                 Git commit to package (default: HEAD)
  --output-dir DIR               Artifact directory (default: ./dist/windows)
  --channel stable|preview       Update channel (default: stable)
  --download-base-url HTTPS_URL  Release asset base URL
  --previous-version X.Y.Z       Previous release for rollback
  --previous-revision SHA        Previous release's full Git SHA
  --previous-manifest-url URL    Previous release manifest URL
  -h, --help                     Show this help

The Windows bundle is never signed. Nothing here reads a credential, and the
manifest records signed=false so no consumer can mistake it for a trusted
publisher claim.
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
        --previous-version) PREVIOUS_VERSION="${2:-}"; shift 2 ;;
        --previous-revision) PREVIOUS_REVISION="${2:-}"; shift 2 ;;
        --previous-manifest-url) PREVIOUS_MANIFEST_URL="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[ -n "$VERSION" ] || fail "--version is required"
[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-+][0-9A-Za-z.-]+)?$ ]] \
    || fail "--version must be SemVer"
[ "$CHANNEL" = "stable" ] || [ "$CHANNEL" = "preview" ] \
    || fail "--channel must be stable or preview"
for command_name in git python3 shasum tar date; do
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
# Install.cmd is the only thing a Windows user is asked to run, and setup.ps1
# is the only thing it may run. A revision missing either is not shippable.
git -C "$REPO_DIR" cat-file -e "$FULL_REVISION:Install.cmd" \
    || fail "release revision does not contain Install.cmd"
for required in LICENSE LICENSE_POLICY.md NOTICE THIRD_PARTY_NOTICES.md \
        setup.ps1 dictate.py dictate.py.lock; do
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

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/whisper-face-windows-release.XXXXXX")"
trap 'rm -rf "$TEMP_ROOT"' EXIT

BUNDLE_NAME="Whisper Face $VERSION"
BUNDLE_DIR="$TEMP_ROOT/$BUNDLE_NAME"
ZIP_NAME="WhisperFace-$VERSION-windows-x64.zip"
ZIP_PATH="$OUTPUT_DIR/$ZIP_NAME"
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
    "https://github.com/Aiml3ss/whisper-face.git"
[ "$(git -C "$BUNDLE_DIR" rev-parse HEAD)" = "$FULL_REVISION" ] \
    || fail "packaged checkout lost its immutable source revision"

python3 "$SCRIPT_DIR/release_manifest.py" source-metadata \
    --version "$VERSION" \
    --revision "$FULL_REVISION" \
    --output "$BUNDLE_DIR/RELEASE-METADATA.json"
python3 "$SCRIPT_DIR/verify_macos_package.py" stamp \
    --root "$BUNDLE_DIR" \
    --version "$VERSION" \
    --revision "$FULL_REVISION" \
    --source-date-epoch "$SOURCE_DATE_EPOCH"

echo "== writing the extracted folder's instructions"
python3 "$SCRIPT_DIR/windows_bundle.py" readme \
    --version "$VERSION" \
    --revision "$FULL_REVISION" \
    --output "$TEMP_ROOT/START HERE.txt"

echo "== creating Windows source bundle"
rm -f "$ZIP_PATH" "$MANIFEST_PATH" "$CHECKSUM_PATH"
python3 "$SCRIPT_DIR/windows_bundle.py" archive \
    --root "$TEMP_ROOT" \
    --output "$ZIP_PATH" \
    --source-date-epoch "$SOURCE_DATE_EPOCH"

echo "== verifying the bundle carries the exact source and a usable entry point"
python3 "$SCRIPT_DIR/windows_bundle.py" verify \
    --bundle-zip "$ZIP_PATH" \
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
    --output "$MANIFEST_PATH"
)
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
    --file "$MANIFEST_PATH" \
    --output "$CHECKSUM_PATH"

echo "== release artifacts"
shasum -a 256 "$ZIP_PATH" "$MANIFEST_PATH"
echo "== checksum file: $CHECKSUM_PATH"
echo "== source revision: $FULL_REVISION"
echo "== unsigned: Windows will warn that the publisher cannot be verified"
