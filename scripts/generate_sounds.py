# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate Whisper Face's first-party advisory cues into ``sounds/``.

The app used to borrow macOS system sounds, which meant it never sounded like
itself and could not be muted. These four cues are the alternative: short,
quiet, and deliberately unremarkable, because a dictation cue that draws
attention to itself is a cue you end up turning off.

Everything here is stdlib and deterministic — the same command reproduces
byte-identical files on any machine, which is the only reason committing
binary assets is acceptable at all.

Usage:
    uv run scripts/generate_sounds.py [output-dir]
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 44_100
BIT_DEPTH = 16
PEAK = 0.34            # advisory, not attention-seeking

# Each cue is a list of (start_seconds, duration_seconds, hz_from, hz_to,
# gain) partials. Frequencies glide linearly so a two-note cue reads as one
# gesture rather than two beeps.
CUES: dict[str, tuple[tuple[float, float, float, float, float], ...]] = {
    # Capture is ready: a small upward step, the sound of a door opening.
    "start": (
        (0.000, 0.045, 587.33, 587.33, 0.70),   # D5
        (0.040, 0.070, 880.00, 880.00, 0.85),   # A5
    ),
    # Text landed: the same interval closing downward, and slightly softer.
    "finish": (
        (0.000, 0.045, 880.00, 880.00, 0.75),   # A5
        (0.040, 0.080, 587.33, 587.33, 0.80),   # D5
    ),
    # Landed, but worth a look: two even pips on one note, no resolution.
    "review": (
        (0.000, 0.045, 739.99, 739.99, 0.72),   # F#5
        (0.065, 0.060, 739.99, 739.99, 0.72),
    ),
    # Something failed: a short downward slide, low enough to read as "no".
    "error": (
        (0.000, 0.130, 392.00, 261.63, 0.85),   # G4 down to C4
    ),
}


def _envelope(position: float, length: float) -> float:
    """A fast attack and a smooth decay, so nothing clicks at either edge."""
    if length <= 0:
        return 0.0
    attack = min(0.006, length * 0.25)
    if position < attack:
        return position / attack
    remaining = (length - position) / max(length - attack, 1e-9)
    return max(0.0, remaining) ** 1.7


def render(partials) -> bytes:
    """Mix one cue down to 16-bit mono PCM."""
    total = max(start + length for start, length, *_ in partials)
    frames = int(round(total * SAMPLE_RATE))
    samples = [0.0] * frames
    for start, length, hz_from, hz_to, gain in partials:
        first = int(round(start * SAMPLE_RATE))
        count = int(round(length * SAMPLE_RATE))
        phase = 0.0
        for index in range(count):
            if first + index >= frames:
                break
            progress = index / max(count - 1, 1)
            hz = hz_from + (hz_to - hz_from) * progress
            phase += 2.0 * math.pi * hz / SAMPLE_RATE
            # A touch of third harmonic keeps a pure sine from sounding thin
            # on laptop speakers without turning the cue into a buzz.
            value = math.sin(phase) + 0.12 * math.sin(3.0 * phase)
            samples[first + index] += (
                value * gain * _envelope(index / SAMPLE_RATE, length))
    ceiling = max((abs(value) for value in samples), default=1.0) or 1.0
    scale = PEAK * ((1 << (BIT_DEPTH - 1)) - 1) / ceiling
    return b"".join(
        struct.pack("<h", int(round(value * scale))) for value in samples)


def write_cue(name: str, out: Path) -> Path:
    target = out / f"{name}.wav"
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(BIT_DEPTH // 8)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(render(CUES[name]))
    return target


def main() -> int:
    args = sys.argv[1:]
    out = Path(args[0]) if args else REPO / "sounds"
    out.mkdir(parents=True, exist_ok=True)
    for name in sorted(CUES):
        target = write_cue(name, out)
        milliseconds = 1000 * target.stat().st_size / (
            SAMPLE_RATE * BIT_DEPTH // 8)
        print(f"{target.relative_to(REPO) if target.is_relative_to(REPO) else target}"
              f"  {target.stat().st_size} bytes  ~{milliseconds:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
