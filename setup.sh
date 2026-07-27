#!/bin/bash
# One-command, repeatable installer. macOS stays native here; Windows shells
# are handed to setup.ps1 before any Unix-specific installation work begins.
# Safe to rerun: generated services are replaced; private user files survive.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

kernel="$(uname -s)"
case "$kernel" in
    MINGW*|MSYS*|CYGWIN*)
        windows_script="$(cygpath -w "$DIR/setup.ps1")"
        exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
            -File "$windows_script" "$@"
        ;;
    Linux)
        if grep -qi microsoft /proc/version 2>/dev/null \
                && command -v powershell.exe >/dev/null 2>&1; then
            windows_script="$(wslpath -w "$DIR/setup.ps1")"
            exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
                -File "$windows_script" "$@"
        fi
        echo "!! Whisper Face supports macOS and Windows; Linux is not supported." >&2
        exit 1
        ;;
    Darwin) ;;
    *)
        echo "!! unsupported operating system: $kernel" >&2
        exit 1
        ;;
esac

# Guarantee the standard system directories are searchable before anything
# looks for a tool. A self-update runs this installer from a detached launchd
# job, which inherits either launchd's own minimal PATH or whatever PATH the
# caller handed over -- and the dictation agent's PATH, for one, carries
# Homebrew but not /usr/sbin, where lsof lives. Losing lsof made every service
# identity probe fail in a way indistinguishable from an unhealthy service.
for system_path in /usr/bin /bin /usr/sbin /sbin; do
    case ":$PATH:" in
        *":$system_path:"*) ;;
        *) PATH="$PATH:$system_path" ;;
    esac
done
export PATH

MODE="full"
VERIFY_ONLY=0
# A default install downloads only what dictation needs to work at all:
# Parakeet Unified plus Whisper Tiny. The large Whisper fallback and the Qwen
# cleanup model are quality upgrades and are opt-in.
WITH_ALL_MODELS=0
MODELS_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --server-only) MODE="server-only" ;;
        --verify) VERIFY_ONLY=1 ;;
        --with-all-models) WITH_ALL_MODELS=1 ;;
        --models) MODELS_ONLY=1; WITH_ALL_MODELS=1 ;;
        -h|--help)
            echo "Usage: ./setup.sh [--server-only] [--verify]" \
                "[--with-all-models] [--models]"
            echo "  --server-only  install the headless endpoint without UI/mic"
            echo "  --verify       check an existing installation without changing it"
            echo "  --with-all-models  also download the optional quality models"
            echo "  --models       download only the optional models into an"
            echo "                 existing installation, then exit"
            echo
            echo "Required (always installed, ~650 MB):"
            echo "  Parakeet Unified 0.6B  primary recognition"
            echo "  Whisper Tiny           fast preview pass"
            echo "Optional (skipped unless requested, ~5 GB):"
            echo "  Whisper large-v3-turbo recognition fallback"
            echo "  qwen3.5:4b             semantic cleanup; deterministic"
            echo "                         cleanup runs without it"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

fail() {
    echo "!! $*" >&2
    exit 1
}

private_log_is_valid() {
    local path="$1"
    [ -f "$path" ] && [ ! -L "$path" ] \
        && [ "$(stat -f '%u' "$path")" = "$(id -u)" ] \
        && [ "$(stat -f '%Lp' "$path")" = "600" ]
}

provision_private_log() {
    local path="$1"
    if [ -L "$path" ]; then
        fail "private runtime log is not a regular file: $path"
    elif [ -e "$path" ]; then
        [ -f "$path" ] \
            || fail "private runtime log is not a regular file: $path"
    else
        (umask 077 && : > "$path") \
            || fail "could not create private runtime log: $path"
    fi
    chmod 600 "$path" \
        || fail "could not restrict private runtime log: $path"
    private_log_is_valid "$path" \
        || fail "private runtime log permissions could not be verified: $path"
}

confirm_writable_checkout() {
    if ! (
        umask 077
        proof="$(mktemp "$DIR/.whisper-face-write.XXXXXXXX")" || exit 1
        trap 'rm -f -- "$proof"' EXIT
        [ "$(stat -f '%Lp' "$proof")" = "600" ] || exit 1
        rm -f -- "$proof" || exit 1
        trap - EXIT
    ); then
        fail "checkout is not writable; copy or extract Whisper Face to a writable local folder, then rerun Install.command"
    fi
}

step() {
    echo
    echo "== $*"
}

run_with_timeout() {
    # macOS does not ship GNU timeout. Bound verification without adding one.
    local timeout_seconds="$1"
    shift
    local command_pid elapsed child_pids
    "$@" &
    command_pid=$!
    elapsed=0
    while kill -0 "$command_pid" 2>/dev/null; do
        if [ "$elapsed" -ge "$timeout_seconds" ]; then
            child_pids="$(pgrep -P "$command_pid" 2>/dev/null || true)"
            if [ -n "$child_pids" ]; then
                kill -TERM $child_pids 2>/dev/null || true
            fi
            kill -TERM "$command_pid" 2>/dev/null || true
            sleep 1
            if [ -n "$child_pids" ]; then
                kill -KILL $child_pids 2>/dev/null || true
            fi
            kill -KILL "$command_pid" 2>/dev/null || true
            wait "$command_pid" 2>/dev/null || true
            return 124
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    wait "$command_pid"
}

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

agent_is_running() {
    launchctl print "gui/$(id -u)/$1" 2>/dev/null \
        | grep -q 'state = running'
}

agent_pid() {
    launchctl print "gui/$(id -u)/$1" 2>/dev/null \
        | awk '/pid =/ { print $3; exit }'
}

ollama_listener_pid() {
    local pids
    pids="$(lsof -nP -iTCP:11434 -sTCP:LISTEN -t 2>/dev/null \
        | sort -u || true)"
    case "$pids" in
        ""|*$'\n'*) return 1 ;;
    esac
    printf '%s\n' "$pids"
}

valid_sha256_digest() {
    local digest="$1"
    [ "${#digest}" -eq 64 ] || return 1
    case "$digest" in
        *[!0-9a-f]*) return 1 ;;
    esac
}

ollama_process_identity_is_valid() {
    local running_pid listening_pid
    agent_is_running com.berg.ollama || return 1
    running_pid="$(agent_pid com.berg.ollama || true)"
    listening_pid="$(ollama_listener_pid || true)"
    [ -n "$running_pid" ] || return 1
    [ "$running_pid" = "$listening_pid" ] || return 1
    curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags \
        >/dev/null 2>&1
}

ollama_service_identity_is_valid() {
    # $1 = freshly rendered desired plist, $2 = installed plist,
    # $3 = last-successful-load receipt, $4 = desired plist digest.
    local desired_plist="$1"
    local installed_plist="$2"
    local receipt="$3"
    local desired_digest="$4"
    local receipt_digest receipt_size
    [ -f "$desired_plist" ] && [ -f "$installed_plist" ] \
        && [ -f "$receipt" ] || return 1
    valid_sha256_digest "$desired_digest" || return 1
    receipt_size="$(wc -c < "$receipt" | tr -d '[:space:]')"
    [ "$receipt_size" = "65" ] || return 1
    IFS= read -r receipt_digest < "$receipt" || return 1
    valid_sha256_digest "$receipt_digest" || return 1
    [ "$receipt_digest" = "$desired_digest" ] || return 1
    cmp -s "$desired_plist" "$installed_plist" || return 1
    ollama_process_identity_is_valid
}

reload_agent() {
    # $1 = label, $2 = plist. Wait out launchd's asynchronous bootout.
    local label="$1"
    local plist="$2"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    for _ in $(seq 1 15); do
        launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || break
        sleep 1
    done
    launchctl bootstrap "gui/$(id -u)" "$plist"
    launchctl kickstart "gui/$(id -u)/$label" >/dev/null
}

render_plist() {
    # $1 = template, $2 = destination, $3... = sed expressions
    local template="$1"
    local destination="$2"
    shift 2
    local temporary
    temporary="$(mktemp "${destination}.XXXXXX")"
    sed "$@" "$template" > "$temporary"
    if grep -q '__[A-Z_][A-Z_]*__' "$temporary"; then
        rm -f "$temporary"
        fail "unresolved placeholder while rendering $template"
    fi
    if ! plutil -lint "$temporary" >/dev/null; then
        rm -f "$temporary"
        fail "invalid generated plist from $template"
    fi
    chmod 600 "$temporary"
    mv -f "$temporary" "$destination"
}

required=(
    dictate.py dictate.py.lock parrot_core.py voice_compiler.py
    process_verifier.py prewarmed_verifier.py whisper_verifier_adapter.py
    relisten_activation.py
    insertion_integrity.py personal_regression.py shadow_candidate_gate.py
    cleanup_circuit_breaker.py delayed_cleanup_activation.py
    delayed_cleanup_merge.py macos_delayed_cleanup_destination.py
    model_wallet.py model_wallet_shadow.py model_readiness_evidence.py
    acoustic_keyword_memory.py acoustic_keyword_activation.py
    benchmark_acoustic_keyword_activation.py
    acoustic_calibration.py acoustic_calibration_activation.py
    benchmark_acoustic_calibration_activation.py
    acoustic_time_machine.py voice_objects.py voice_object_command_parser.py
    voice_inbox.py voice_object_inbox_bridge.py macos_email_compose.py
    macos_voice_draft_clipboard.py
    demonstration_drafts.py
    risky_action_confirmation.py
    point_and_speak_resolver.py point_and_speak_transaction.py
    macos_point_and_speak_snapshot.py
    macos_drop_to_target_snapshot.py drop_to_target.py
    whisper_face_gui.py whisper_face_theme.py whisper_face_characters.py
    whisper_face_render.py
    support_bundle.py self_update.py
    scripts/macos_launcher_app.py scripts/side_by_side_update.py
    scripts/safe_update_advisor.py scripts/release_manifest.py
    config/macos-signing-policy.json
    native/ParrotASRHelper/Package.swift
    native/ParrotASRHelper/Package.resolved
    native/ParrotASRHelper/Sources/parrot-asr-helper/main.swift
    setup.ps1 Install.command Install.cmd
    com.berg.dictate.plist.template com.berg.ollama.plist.template
    snippets.template.json tones.template.json preferences.template.json
    acoustic_keyword_memory.template.json dictionary.template.txt
    icons/faces/parrot-idle.svg icons/faces/parrot-talk.svg
    icons/faces/fox-idle.svg icons/faces/fox-talk.svg
    icons/faces/owl-idle.svg icons/faces/owl-talk.svg
    icons/faces/cat-idle.svg icons/faces/cat-talk.svg
    icons/faces/bear-idle.svg icons/faces/bear-talk.svg
    icons/faces/dog-idle.svg icons/faces/dog-talk.svg
    icons/faces/wolf-idle.svg icons/faces/wolf-talk.svg
    icons/faces/pig-idle.svg icons/faces/pig-talk.svg
    icons/faces/panda-idle.svg icons/faces/panda-talk.svg
    icons/faces/tiger-idle.svg icons/faces/tiger-talk.svg
    icons/faces/color/parrot-idle.svg icons/faces/color/parrot-half.svg icons/faces/color/parrot-talk.svg
    icons/faces/color/fox-idle.svg icons/faces/color/fox-half.svg icons/faces/color/fox-talk.svg
    icons/faces/color/owl-idle.svg icons/faces/color/owl-half.svg icons/faces/color/owl-talk.svg
    icons/faces/color/cat-idle.svg icons/faces/color/cat-half.svg icons/faces/color/cat-talk.svg
    icons/faces/color/bear-idle.svg icons/faces/color/bear-half.svg icons/faces/color/bear-talk.svg
    icons/faces/color/dog-idle.svg icons/faces/color/dog-half.svg icons/faces/color/dog-talk.svg
    icons/faces/color/wolf-idle.svg icons/faces/color/wolf-half.svg icons/faces/color/wolf-talk.svg
    icons/faces/color/pig-idle.svg icons/faces/color/pig-half.svg icons/faces/color/pig-talk.svg
    icons/faces/color/panda-idle.svg icons/faces/color/panda-half.svg icons/faces/color/panda-talk.svg
    icons/faces/color/tiger-idle.svg icons/faces/color/tiger-half.svg icons/faces/color/tiger-talk.svg
)
for file in "${required[@]}"; do
    [ -f "$DIR/$file" ] || fail "repository is incomplete: missing $file"
done

[ "$(uname -m)" = "arm64" ] \
    || fail "this stack requires an Apple Silicon Mac (MLX)"

# Service identity is decided by lsof. Without it every probe reports "no
# unique listener", which reads exactly like a broken service and stalls the
# readiness wait until it times out. Refuse up front instead: an absent tool
# and an unhealthy service must never be the same symptom.
command -v lsof >/dev/null 2>&1 \
    || fail "lsof was not found on PATH; it is required to bind a service to its listening process"

macos_major="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$macos_major" -lt 14 ]; then
    echo "!! macOS 14 or newer is recommended and supported by Homebrew."
fi

launch_dir="$HOME/Library/LaunchAgents"
dictate_plist="$launch_dir/com.berg.dictate.plist"
ollama_plist="$launch_dir/com.berg.ollama.plist"
launcher_app="$HOME/Applications/Whisper Face.app"
launcher_receipt="$HOME/Library/Application Support/Whisper Face/launcher-install.json"
service_receipt_dir="$HOME/Library/Application Support/Whisper Face"
ollama_service_receipt="$service_receipt_dir/ollama-service.sha256"
parakeet_helper="$DIR/.models/bin/parrot-asr-helper"
dictate_log="$DIR/dictate.log"
ollama_log="$DIR/ollama.log"

verify_install() {
    step "verifying installation"
    local uv_bin ollama_bin verify_ollama_plist verify_ollama_digest
    local verify_cleanup verify_qwen
    local escaped_verify_ollama escaped_verify_dir
    uv_bin="$(command -v uv 2>/dev/null || true)"
    ollama_bin="$(command -v ollama 2>/dev/null || true)"
    [ -n "$uv_bin" ] || fail "uv is not installed"
    command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is not installed"
    [ -n "$ollama_bin" ] || fail "ollama is not installed"
    command -v swift >/dev/null 2>&1 || fail "Swift toolchain is not installed"
    command -v swiftc >/dev/null 2>&1 \
        || fail "Swift compiler is not installed"
    [ -x "$parakeet_helper" ] || fail "Parakeet ASR helper is missing"
    "$parakeet_helper" --verify >/dev/null \
        || fail "Parakeet Unified model/helper verification failed"
    [ -f "$dictate_plist" ] || fail "dictation LaunchAgent is missing"
    [ -f "$ollama_plist" ] || fail "Ollama LaunchAgent is missing"
    for private_log in "$dictate_log" "$ollama_log"; do
        private_log_is_valid "$private_log" \
            || fail "private runtime log is missing or has unsafe permissions"
    done
    plutil -lint "$dictate_plist" "$ollama_plist" >/dev/null
    escaped_verify_ollama="$(escape_sed_replacement "$ollama_bin")"
    escaped_verify_dir="$(escape_sed_replacement "$DIR")"
    verify_ollama_plist="$(mktemp \
        "${TMPDIR:-/tmp}/whisper-face-ollama-verify.XXXXXX")"
    printf -v verify_cleanup 'rm -f -- %q' "$verify_ollama_plist"
    trap "$verify_cleanup" EXIT
    render_plist "$DIR/com.berg.ollama.plist.template" \
        "$verify_ollama_plist" \
        -e "s|__OLLAMA__|$escaped_verify_ollama|g" \
        -e "s|__DIR__|$escaped_verify_dir|g"
    verify_ollama_digest="$(shasum -a 256 "$verify_ollama_plist" \
        | awk '{print $1}')"
    if ! ollama_service_identity_is_valid "$verify_ollama_plist" \
            "$ollama_plist" "$ollama_service_receipt" \
            "$verify_ollama_digest"; then
        rm -f "$verify_ollama_plist"
        trap - EXIT
        fail "Ollama LaunchAgent configuration or process identity is stale"
    fi
    rm -f "$verify_ollama_plist"
    trap - EXIT
    if [ "$MODE" = "full" ]; then
        python3 "$DIR/scripts/macos_launcher_app.py" verify \
            --app "$launcher_app" --checkout "$DIR" \
            --receipt "$launcher_receipt" \
            --installed-runtime >/dev/null \
            || fail "Whisper Face launcher app is missing or stale"
    fi
    "$uv_bin" lock --check --script "$DIR/dictate.py" >/dev/null
    "$uv_bin" sync --locked --script "$DIR/dictate.py" --check >/dev/null
    if [ "$MODE" = "full" ]; then
        run_with_timeout 30 "$uv_bin" run --locked \
            --script "$DIR/dictate.py" --native-gui-smoke-test >/dev/null \
            || fail "native AppKit construction smoke test failed or timed out"
    fi
    "$uv_bin" run --locked --script "$DIR/dictate.py" \
        --verify-parakeet-model >/dev/null \
        || fail "Parakeet Unified model revision verification failed"
    # qwen3.5:4b is an optional cleanup upgrade. Audit it when it is installed;
    # a minimal install without it is a supported configuration, not a failure.
    verify_qwen=0
    if "$ollama_bin" show qwen3.5:4b >/dev/null 2>&1; then
        verify_qwen=1
    fi
    if [ "$verify_qwen" -eq 1 ]; then
        "$uv_bin" run --locked --script "$DIR/dictate.py" \
            --verify-ollama-model >/dev/null \
            || fail "Qwen model manifest verification failed"
    fi
    agent_is_running com.berg.ollama || fail "Ollama LaunchAgent is not running"
    agent_is_running com.berg.dictate \
        || fail "dictation LaunchAgent is not running"
    curl -fsS --max-time 3 http://127.0.0.1:8787/health >/dev/null \
        || fail "dictation process is not ready; inspect $DIR/dictate.log"
    if [ "$verify_qwen" -eq 1 ]; then
        echo "== verified: locked Python environment, Whisper + Parakeet + Qwen models, services, and health"
    else
        echo "== verified: locked Python environment, Whisper + Parakeet models, services, and health"
        echo "== qwen3.5:4b is not installed; cleanup stays deterministic (add it with ./setup.sh --models)"
    fi
}

# Adopt an already-installed Homebrew that is merely absent from PATH, before
# anything looks for brew-installed tools. The self-updater runs this installer
# from a detached launchd job, and launchd hands such a job the minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) rather than the login PATH. Without this,
# `command -v brew` misses an installed Homebrew, setup tries to reinstall it
# with no TTY and no sudo, that installer aborts in about a second, and `set -e`
# ends the run before the recovery below could ever be reached -- which is what
# turned every background self-update into an unexplained rollback.
adopt_installed_homebrew() {
    local candidate
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$candidate" ] || continue
        eval "$("$candidate" shellenv)"
        command -v brew >/dev/null 2>&1 && return 0
    done
    return 1
}

command -v brew >/dev/null 2>&1 || adopt_installed_homebrew || true

if [ "$VERIFY_ONLY" -eq 1 ]; then
    verify_install
    exit 0
fi

step "Whisper Face setup in $DIR (mode: $MODE)"
if [ "$MODELS_ONLY" -eq 1 ]; then
    echo "== models: adding the optional Whisper large-v3-turbo and qwen3.5:4b"
    echo "==         to this installation; models already present are reused"
elif [ "$WITH_ALL_MODELS" -eq 1 ]; then
    echo "== models: Parakeet Unified + Whisper Tiny + Whisper large-v3-turbo"
    echo "==         + qwen3.5:4b (already-downloaded models are reused)"
else
    echo "== models: Parakeet Unified + Whisper Tiny only. The optional"
    echo "==         large-v3-turbo and qwen3.5:4b models are skipped unless"
    echo "==         already present; add them later with ./setup.sh --models"
fi

# Keep enough headroom for Ollama, the Whisper models, Python wheels, caches,
# and model expansion. Existing cached files make reruns much cheaper, and a
# default install without the optional models needs far less room.
required_kb=5242880
required_gb=5
if [ "$WITH_ALL_MODELS" -eq 1 ]; then
    required_kb=8388608
    required_gb=8
fi
available_kb="$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')"
if [ "${available_kb:-0}" -lt "$required_kb" ]; then
    fail "at least $required_gb GB of free disk space is required"
fi

confirm_writable_checkout

# Create and lock logs before either service can create them with a default
# mode. Existing logs are retained; rerunning setup repairs their mode.
provision_private_log "$dictate_log"
provision_private_log "$ollama_log"

# --- Homebrew and native dependencies --------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    step "installing Homebrew (its official installer may request a password)"
    # Never let a failed bootstrap die silently under `set -e`: the exit code
    # alone stranded the self-updater with an unexplained rollback.
    /bin/bash -c "$(curl -fsSL \
        https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || fail "the Homebrew installer did not complete (it needs a terminal and an administrator password); open Terminal, run ./setup.sh once to finish it, then retry"
    adopt_installed_homebrew \
        || fail "Homebrew installed but its brew binary was not found"
fi

BREW="$(command -v brew)"
missing_packages=()
for package in uv ffmpeg ollama; do
    "$BREW" list --versions "$package" >/dev/null 2>&1 \
        || missing_packages+=("$package")
done
if [ "${#missing_packages[@]}" -gt 0 ]; then
    step "installing ${missing_packages[*]}"
    "$BREW" install "${missing_packages[@]}"
fi

UV="$(command -v uv)"
OLLAMA="$(command -v ollama)"
mkdir -p "$launch_dir"

# --- Model inventory --------------------------------------------------------
# Probe the caches before announcing any size. Hugging Face and Ollama both
# skip files they already have, so a rerun that printed "downloading ~1.7 GB"
# was lying about work it never did.
model_inventory=""
skipped_models=()

refresh_model_inventory() {
    model_inventory="$("$UV" run --locked --script "$DIR/dictate.py" \
        --model-inventory)" \
        || fail "could not inspect the local model cache"
}

model_state() {
    printf '%s\n' "$model_inventory" \
        | awk -F= -v key="$1" '$1 == key { print $2; exit }'
}

qwen_is_installed() {
    "$OLLAMA" show qwen3.5:4b >/dev/null 2>&1
}

install_optional_models() {
    # Fetch only the opt-in quality models into an existing installation.
    step "checking which optional models are already installed"
    refresh_model_inventory
    if [ "$(model_state whisper-large)" = "present" ]; then
        echo "== Whisper large-v3-turbo already installed"
    else
        step "downloading Whisper large-v3-turbo (~1.6 GB)"
        "$UV" run --locked --script "$DIR/dictate.py" --preload-models
    fi
    if qwen_is_installed; then
        echo "== qwen3.5:4b already installed"
    else
        curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags \
            >/dev/null 2>&1 \
            || fail "Ollama is not running; run ./setup.sh first, then ./setup.sh --models"
        step "downloading qwen3.5:4b (~3.4 GB)"
        "$OLLAMA" pull qwen3.5:4b
    fi
    "$UV" run --locked --script "$DIR/dictate.py" --verify-ollama-model
    if agent_is_running com.berg.dictate; then
        step "restarting Whisper Face so it loads the new models"
        launchctl kickstart -k "gui/$(id -u)/com.berg.dictate" >/dev/null 2>&1 \
            || echo "!! could not restart Whisper Face; rerun ./setup.sh"
    fi
    echo
    echo "== optional models are installed"
}

if [ "$MODELS_ONLY" -eq 1 ]; then
    install_optional_models
    exit 0
fi

step "checking which models are already installed"
refresh_model_inventory

# --- Native Parakeet helper ------------------------------------------------
# Both FluidAudio and its Core ML model are pinned. The warm helper receives
# Float32 audio over a pipe, so the RAM-only application contract remains intact.
if [ "$(model_state parakeet)" = "present" ]; then
    step "Parakeet Unified model already installed (~565 MB, cached)"
else
    step "downloading the pinned Parakeet Unified model (~565 MB, first run only)"
fi
"$UV" run --locked --script "$DIR/dictate.py" --preload-parakeet-model

step "building the native Parakeet Unified helper"
command -v swift >/dev/null 2>&1 && command -v swiftc >/dev/null 2>&1 \
    || fail "Swift is unavailable; install the Xcode Command Line Tools"
mkdir -p "$DIR/.models/bin"
swift build -c release \
    --package-path "$DIR/native/ParrotASRHelper" \
    --scratch-path "$DIR/.models/swift-build"
install -m 755 \
    "$DIR/.models/swift-build/release/parrot-asr-helper" \
    "$parakeet_helper"
step "verifying the pinned Parakeet Unified model and helper"
"$parakeet_helper" --preload

# --- Ollama service and cleanup model --------------------------------------
step "configuring the tuned local Ollama service"
escaped_ollama="$(escape_sed_replacement "$OLLAMA")"
escaped_dir="$(escape_sed_replacement "$DIR")"
desired_ollama_plist="${ollama_plist}.candidate.$$"
trap 'rm -f "$desired_ollama_plist"' EXIT
render_plist com.berg.ollama.plist.template "$desired_ollama_plist" \
    -e "s|__OLLAMA__|$escaped_ollama|g" \
    -e "s|__DIR__|$escaped_dir|g"
desired_ollama_digest="$(shasum -a 256 "$desired_ollama_plist" \
    | awk '{print $1}')"

# A warm update normally leaves Ollama's exact service definition running.
# Keep that process and its loaded model hot only when the freshly rendered
# plist is byte-identical, a prior successful reload receipt binds its digest,
# and the running launchd PID owns the healthy endpoint. Any missing evidence,
# drift, stopped agent, or failed health probe takes the replace/reload path.
if ollama_service_identity_is_valid "$desired_ollama_plist" \
        "$ollama_plist" "$ollama_service_receipt" \
        "$desired_ollama_digest"; then
    rm -f "$desired_ollama_plist"
    echo "== reusing healthy Ollama service (configuration unchanged)"
else
    "$BREW" services stop ollama >/dev/null 2>&1 || true
    mv -f "$desired_ollama_plist" "$ollama_plist"
    reload_agent com.berg.ollama "$ollama_plist"
fi
trap - EXIT

echo -n "== waiting for Ollama"
ollama_ready=0
for _ in $(seq 1 60); do
    # Wait for the identity the next line asserts, not merely for the endpoint
    # to answer. A reload leaves the previous process bound to 11434 while the
    # replacement starts, so a healthy /api/tags is not yet evidence that the
    # launchd job owns the sole listener. Polling only the endpoint let the
    # wait break during that window and hard-failed the install -- which, from
    # the self-updater, meant a rollback. This check subsumes the health probe.
    if ollama_process_identity_is_valid; then
        ollama_ready=1
        break
    fi
    echo -n "."
    sleep 1
done
echo
[ "$ollama_ready" -eq 1 ] \
    || fail "Ollama did not become ready; inspect $DIR/ollama.log"

ollama_process_identity_is_valid \
    || fail "Ollama service identity could not be verified"
install -d -m 700 "$service_receipt_dir"
receipt_temporary="${ollama_service_receipt}.tmp.$$"
printf '%s\n' "$desired_ollama_digest" > "$receipt_temporary"
chmod 600 "$receipt_temporary"
mv -f "$receipt_temporary" "$ollama_service_receipt"

# The cleanup model only rewrites text the deterministic compiler refuses to
# touch, so it is optional: an install without it cleans up deterministically.
qwen_installed=0
if qwen_is_installed; then
    echo "== qwen3.5:4b already installed"
    qwen_installed=1
elif [ "$WITH_ALL_MODELS" -eq 1 ]; then
    step "downloading qwen3.5:4b (~3.4 GB)"
    "$OLLAMA" pull qwen3.5:4b
    qwen_installed=1
else
    echo "== skipping qwen3.5:4b (~3.4 GB): semantic cleanup upgrade"
    skipped_models+=(
        "qwen3.5:4b (~3.4 GB) — semantic cleanup; deterministic cleanup runs without it")
fi

# --- Reproducible Python environment and Whisper model cache ---------------
step "installing the locked Python environment"
"$UV" sync --locked --script "$DIR/dictate.py"
if [ "$qwen_installed" -eq 1 ]; then
    "$UV" run --locked --script "$DIR/dictate.py" --verify-ollama-model
fi

# Whisper Tiny is the fast preview pass every dictation runs; it is required.
if [ "$(model_state whisper-fast)" = "present" ]; then
    step "Whisper Tiny already installed (~75 MB, cached)"
else
    step "downloading Whisper Tiny (~75 MB)"
fi
"$UV" run --locked --script "$DIR/dictate.py" --preload-fast-model

# large-v3-turbo only decodes what Parakeet declines, so it is an upgrade.
if [ "$(model_state whisper-large)" = "present" ]; then
    step "Whisper large-v3-turbo already installed (~1.6 GB, cached)"
elif [ "$WITH_ALL_MODELS" -eq 1 ]; then
    step "downloading Whisper large-v3-turbo (~1.6 GB)"
    "$UV" run --locked --script "$DIR/dictate.py" --preload-models
else
    echo "== skipping Whisper large-v3-turbo (~1.6 GB): recognition fallback"
    skipped_models+=(
        "Whisper large-v3-turbo (~1.6 GB) — recognition fallback behind Parakeet")
fi

# --- Private, per-machine state --------------------------------------------
step "creating private per-machine files (existing files are preserved)"
for name in snippets tones preferences acoustic_keyword_memory dictionary; do
    destination="$DIR/$name.json"
    template="$DIR/$name.template.json"
    if [ "$name" = "dictionary" ]; then
        destination="$DIR/dictionary.txt"
        template="$DIR/dictionary.template.txt"
    fi
    [ -f "$destination" ] || install -m 600 "$template" "$destination"
    chmod 600 "$destination"
done
for private_file in transcripts.jsonl learned.json voice_inbox.json demonstrations.json \
        delayed_cleanup_activation.json \
        acoustic_keyword_activation.json acoustic_calibration_activation.json \
        relisten_activation.json dictate.log ollama.log \
        .dictate.lock; do
    [ ! -e "$DIR/$private_file" ] || chmod 600 "$DIR/$private_file"
done

# --- Dictation LaunchAgent --------------------------------------------------
step "installing the login LaunchAgent"
escaped_uv="$(escape_sed_replacement "$UV")"
if [ "$MODE" = "server-only" ]; then
    extra_sed="s|__EXTRA_ARGS__|<string>--server-only</string>|"
    # Server-only stays raw uv and headless: no launcher, no TCC surface.
    launcher_prefix_sed="/__LAUNCHER_PREFIX__/d"
else
    extra_sed="/__EXTRA_ARGS__/d"
    # Full mode runs the login job through the signed launcher app. launchd
    # execs the launcher, which supervises "uv run ..." as a fork+exec child,
    # so macOS attributes Input Monitoring and Accessibility to "Whisper Face"
    # (one grantable toggle that survives reinstalls) while KeepAlive still sees
    # the child's real exit status.
    escaped_launcher_exe="$(escape_sed_replacement \
        "$HOME/Applications/Whisper Face.app/Contents/MacOS/Whisper Face")"
    launcher_prefix_sed="s|__LAUNCHER_PREFIX__|<string>$escaped_launcher_exe</string>\\
        <string>--run</string>|"
fi
render_plist com.berg.dictate.plist.template "$dictate_plist" \
    -e "$launcher_prefix_sed" \
    -e "s|__UV__|$escaped_uv|g" \
    -e "s|__DIR__|$escaped_dir|g" \
    -e "$extra_sed"

if [ "$MODE" = "full" ]; then
    step "installing the generic Whisper Face app launcher"
    packaged_launcher_app="$(cd "$DIR/.." && pwd)/Whisper Face.app"
    launcher_install_args=(
        install --app "$launcher_app" --checkout "$DIR"
        --receipt "$launcher_receipt"
    )
    if [ -d "$packaged_launcher_app" ] \
            && [ "$packaged_launcher_app" != "$launcher_app" ]; then
        launcher_install_args+=(--source-app "$packaged_launcher_app")
    fi
    python3 "$DIR/scripts/macos_launcher_app.py" "${launcher_install_args[@]}"
fi

log_start=1
if [ -f "$DIR/dictate.log" ]; then
    log_start=$(( $(wc -l < "$DIR/dictate.log") + 1 ))
fi
reload_agent com.berg.dictate "$dictate_plist"

echo -n "== waiting for dictation service"
ready=0
permissions_needed=0
for _ in $(seq 1 120); do
    fresh_log="$(tail -n "+$log_start" "$DIR/dictate.log" 2>/dev/null || true)"
    if printf '%s\n' "$fresh_log" | grep -q '^Ready'; then
        ready=1
        break
    fi
    if printf '%s\n' "$fresh_log" | grep -q '^Waiting for permissions:'; then
        permissions_needed=1
        break
    fi
    if ! launchctl print "gui/$(id -u)/com.berg.dictate" >/dev/null 2>&1; then
        fail "dictation LaunchAgent exited; inspect $DIR/dictate.log"
    fi
    echo -n "."
    sleep 1
done
echo

if [ "$ready" -eq 1 ]; then
    curl -fsS --max-time 3 http://127.0.0.1:8787/health >/dev/null \
        || fail "dictation process launched but its health check failed"
    verify_install
elif [ "$permissions_needed" -eq 1 ] && [ "$MODE" = "full" ]; then
    echo "== software and models are installed; macOS permissions remain:"
    echo "== System Settings -> Privacy & Security -> enable Whisper Face under"
    echo "==   Input Monitoring, Accessibility, and Microphone."
    echo "== The LaunchAgent rechecks automatically; then run ./setup.sh --verify"
else
    fail "dictation service did not become ready; inspect $DIR/dictate.log"
fi

echo
echo "== installation complete"
if [ "$MODE" = "full" ]; then
    echo "== Hold Right Option, speak, and release to paste."
    echo "== Flight Recorder defaults off on a fresh install; an existing"
    echo "== preference is preserved. Toggle it from the Whisper Face menu."
    echo "== Launcher: $launcher_app"
else
    echo "== server-only installation is ready."
fi
if [ "${#skipped_models[@]}" -gt 0 ]; then
    echo "== These optional models were skipped to keep the install small:"
    for skipped_model in "${skipped_models[@]}"; do
        echo "==   $skipped_model"
    done
    echo "== Dictation works without them. Add them with: ./setup.sh --models"
fi
echo "== Logs: $DIR/dictate.log"
