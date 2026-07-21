#!/bin/bash
# One-command setup for the local dictation stack (dictate.py).
# Idempotent: safe to re-run any time, on a fresh Mac or this one.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# --server-only: phone endpoint + models only — no hotkey/HUD/mic and no
# permission prompts. The right mode for a headless always-on Mac.
MODE="full"
[ "${1:-}" = "--server-only" ] && MODE="server-only"

echo "== dictation setup in $DIR (mode: $MODE)"

if [ "$(uname -m)" != "arm64" ]; then
    echo "This stack needs Apple Silicon (MLX). Intel Macs won't work."
    exit 1
fi

reload_agent() {   # $1 = label, $2 = plist path; waits out the bootout race
    launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
    for _ in $(seq 1 10); do
        launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1 || break
        sleep 1
    done
    launchctl bootstrap "gui/$(id -u)" "$2"
}

# --- Homebrew ---------------------------------------------------------------
if ! command -v brew >/dev/null; then
    echo "Homebrew is required. Install it first:"
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
fi

# --- dependencies -----------------------------------------------------------
for pkg in uv ffmpeg ollama; do
    if ! command -v "$pkg" >/dev/null; then
        echo "== installing $pkg"
        brew install "$pkg"
    fi
done

# --- ollama service + model -------------------------------------------------
# Our own agent instead of brew services: bakes in the tuned env
# (flash attention measured +36% generation speed).
brew services stop ollama >/dev/null 2>&1 || true
mkdir -p "$HOME/Library/LaunchAgents"
OLLAMA="$(command -v ollama)"
sed -e "s|__OLLAMA__|$OLLAMA|g" -e "s|__DIR__|$DIR|g" \
    com.berg.ollama.plist.template \
    > "$HOME/Library/LaunchAgents/com.berg.ollama.plist"
reload_agent com.berg.ollama "$HOME/Library/LaunchAgents/com.berg.ollama.plist"
echo -n "== waiting for ollama"
for _ in $(seq 1 30); do
    curl -s -m 1 localhost:11434/api/tags >/dev/null && break
    echo -n "."; sleep 1
done
echo
if ! ollama list 2>/dev/null | grep -q "^qwen3.5:4b"; then
    echo "== pulling qwen3.5:4b (~3.4 GB)"
    ollama pull qwen3.5:4b
fi

# --- per-machine files ------------------------------------------------------
[ -f snippets.json ] || cp snippets.template.json snippets.json
[ -f tones.json ] || cp tones.template.json tones.json
[ -f preferences.json ] || cp preferences.template.json preferences.json
[ -f dictionary.txt ] || cp dictionary.template.txt dictionary.txt
chmod 600 snippets.json tones.json preferences.json dictionary.txt
for private_file in transcripts.jsonl learned.json dictate.log ollama.log .dictate.lock; do
    [ ! -e "$private_file" ] || chmod 600 "$private_file"
done

# --- LaunchAgent ------------------------------------------------------------
UV="$(command -v uv)"
mkdir -p "$HOME/Library/LaunchAgents"
if [ "$MODE" = "server-only" ]; then
    EXTRA_SED="s|__EXTRA_ARGS__|<string>--server-only</string>|"
else
    EXTRA_SED="/__EXTRA_ARGS__/d"
fi
sed -e "s|__UV__|$UV|g" -e "s|__DIR__|$DIR|g" -e "$EXTRA_SED" \
    com.berg.dictate.plist.template \
    > "$HOME/Library/LaunchAgents/com.berg.dictate.plist"
reload_agent com.berg.dictate \
    "$HOME/Library/LaunchAgents/com.berg.dictate.plist"

echo
echo "== done. First launch downloads Whisper models (~1.7 GB total) and resolves Python deps."
if [ "$MODE" = "full" ]; then
    echo "== macOS will ask for permissions — enable 'uv' under System Settings ->"
    echo "==   Privacy & Security -> Input Monitoring, Accessibility, Microphone."
    echo "==   (The app waits and restarts itself automatically once granted.)"
    echo "== Flight Recorder is off by default; enable its RAM-only buffer"
    echo "==   explicitly from the parrot menu when you want tap-after-talk."
else
    echo "== server-only mode: no permission prompts needed."
fi
echo "== If the firewall asks about incoming connections for Python: Allow"
echo "==   (that's the iPhone endpoint on port 8787)."
echo "== Phone URL for the Diction app's Self-Hosted tab:"
echo "==   http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this-mac-ip>'):8787/v1/audio/transcriptions"
echo "== Watch progress:  tail -f $DIR/dictate.log"
