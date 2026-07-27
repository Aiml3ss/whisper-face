"""Session-scoped measurement mode: run a candidate arm without a receipt.

Three features ship off and unlock only from a receipt built out of physical
A/B evidence.  Until this module existed the runtime produced the candidate
arm's behavior *only after* that receipt, so the evidence the receipt needs
could not be recorded at all: the calibration candidate pass could not run
calibrated, the biased keyword pass could not reach the Whisper prompt, and
delayed cleanup never scheduled a pass to time.  The gates were circular.

Measurement mode closes that loop and does nothing else:

* **It applies the real candidate path.**  The runtime's own calibrated front
  end, the real Whisper prompt, the real delayed-cleanup transaction.  Not an
  approximation of them.
* **It grants no authority.**  This module cannot write, read, validate, or
  even name an activation receipt.  It imports no activation module, opens no
  file, and has no persistence of any kind.  Evidence recorded under it still
  faces the full manual-review gate.
* **It labels its own evidence.**  Every artifact recorded while an arm is
  active carries that arm's label, and the activation validators read the
  label and carry it into the receipt, so an override-derived corpus is never
  mistaken for ordinary-path evidence.
* **It is session-scoped and obvious.**  It comes from process arguments only,
  is never persisted, is printed at startup, is visible in runtime status, and
  ends when the process does.
* **It fails closed.**  One malformed argument disables every arm, exactly as
  a malformed receipt disables the feature it would have authorized.

The runtime enables an arm with a repeatable ``--measure`` argument::

    --measure calibration:gain=2.5,noise=0.008,vad=0.012,end-silence=280
    --measure keyword:Qwen
    --measure delayed-cleanup
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Sequence
import unicodedata

# Read-only policy bounds. `acoustic_calibration` is a pure numeric policy with
# no persistence and no runtime hook, so importing it cannot create authority.
# Nothing that can write a receipt is imported here, and a test asserts it.
from acoustic_calibration import (
    END_SILENCE_BOUNDS_MS,
    GAIN_CEILING_BOUNDS,
    NOISE_GATE_BOUNDS,
    VAD_THRESHOLD_BOUNDS,
)


SCHEMA_VERSION = 1
FLAG = "--measure"

# The one key every manifest, case record, report, and receipt uses to say how
# its evidence was produced.
EVIDENCE_KEY = "measurement_mode"

# The closed label vocabulary. An artifact says either "ordinary path" or the
# single arm that produced it; there is no third, softer claim.
ORDINARY_PATH = "ordinary-path"
CALIBRATION_LABEL = "measured-calibration-candidate"
KEYWORD_LABEL = "measured-keyword-priority"
DELAYED_CLEANUP_LABEL = "measured-delayed-cleanup"
ARM_LABELS = (CALIBRATION_LABEL, KEYWORD_LABEL, DELAYED_CLEANUP_LABEL)
LABELS = frozenset((ORDINARY_PATH,) + ARM_LABELS)

CALIBRATION_ARM = "calibration"
KEYWORD_ARM = "keyword"
DELAYED_CLEANUP_ARM = "delayed-cleanup"
ARMS = (CALIBRATION_ARM, KEYWORD_ARM, DELAYED_CLEANUP_ARM)

LABEL_BY_ARM = {
    CALIBRATION_ARM: CALIBRATION_LABEL,
    KEYWORD_ARM: KEYWORD_LABEL,
    DELAYED_CLEANUP_ARM: DELAYED_CLEANUP_LABEL,
}

MAX_KEYWORD_CHARS = 80

# What measurement mode is not, stated once so every consumer can quote it.
AUTHORITY_STATEMENT = (
    "measurement mode applies a candidate code path for one process session; "
    "it is not a receipt, is never persisted, and grants no runtime authority")


class MeasurementModeError(ValueError):
    """A measurement-mode label or argument violated the closed contract."""


# ----------------------------- evidence labels -----------------------------


def evidence_label(value: Any, *, arm: str) -> str:
    """Normalize one artifact's measurement-mode flag.

    An absent flag means the ordinary path, the same fail-closed default a
    missing receipt gets.  The only other accepted value is the label of the
    arm that artifact could have been recorded under: a calibration manifest
    cannot claim to have been recorded under the keyword arm.
    """
    if arm not in ARM_LABELS:
        raise MeasurementModeError("unknown measurement arm")
    if value is None:
        return ORDINARY_PATH
    if value in (ORDINARY_PATH, arm):
        return str(value)
    raise MeasurementModeError("measurement_mode label is invalid")


def used_measurement_mode(value: Any) -> bool:
    """True only for a label that names an actual measurement arm."""
    return value in ARM_LABELS


# ------------------------------ parsed state -------------------------------


@dataclass(frozen=True)
class MeasuredCalibration:
    """Candidate front-end settings for one measurement session.

    Deliberately a separate type from the receipt's ``CalibrationSettings``.
    This module must not be able to produce the type the runtime treats as
    proof, so the runtime converts explicitly at the point of use.
    """

    gain_ceiling: float
    noise_gate: float
    vad_threshold: float
    end_silence_ms: int

    def as_tuple(self) -> tuple[float, float, float, int]:
        return (self.gain_ceiling, self.noise_gate, self.vad_threshold,
                self.end_silence_ms)


@dataclass(frozen=True)
class MeasurementMode:
    """One process session's measurement arms. Immutable, never persisted."""

    calibration: MeasuredCalibration | None = None
    keyword: str | None = None
    delayed_cleanup: bool = False
    refusals: tuple[str, ...] = ()

    @classmethod
    def inert(cls, refusals: Iterable[str] = ()) -> "MeasurementMode":
        return cls(None, None, False, tuple(refusals))

    @property
    def active(self) -> bool:
        return bool(
            self.calibration is not None
            or self.keyword is not None
            or self.delayed_cleanup)

    @property
    def arms(self) -> tuple[str, ...]:
        names = []
        if self.calibration is not None:
            names.append(CALIBRATION_ARM)
        if self.keyword is not None:
            names.append(KEYWORD_ARM)
        if self.delayed_cleanup:
            names.append(DELAYED_CLEANUP_ARM)
        return tuple(names)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(LABEL_BY_ARM[arm] for arm in self.arms)

    def label_for(self, arm: str) -> str:
        """Return the label an artifact from ``arm`` should carry right now."""
        if arm not in ARMS:
            raise MeasurementModeError("unknown measurement arm")
        return LABEL_BY_ARM[arm] if arm in self.arms else ORDINARY_PATH

    def status_snapshot(self) -> dict[str, Any]:
        """Content-free projection for runtime status surfaces.

        Carries no keyword text: an active keyword arm is private the same way
        keyword memory is, so status reports only that one is in force.
        """
        return {
            "active": self.active,
            "arms": list(self.arms),
            "labels": list(self.labels),
            "grants_authority": False,
            "scope": "process-session-only",
            "persisted": False,
            "refusals": list(self.refusals),
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if self.refusals:
            return ("Measurement mode refused (" + "; ".join(self.refusals)
                    + "); every arm is off")
        if not self.active:
            return "Off — ordinary path"
        return ("MEASUREMENT MODE: " + ", ".join(self.arms)
                + " — evidence only, no runtime authority")

    def banner(self) -> tuple[str, ...]:
        """Startup lines so a session can never be unknowingly measured."""
        if self.refusals:
            return tuple(
                [f"[measurement] refused: {reason}"
                 for reason in self.refusals]
                + ["[measurement] every arm is off; ordinary behavior applies"])
        if not self.active:
            return ()
        return tuple(
            [f"[measurement] ACTIVE: {', '.join(self.arms)}",
             f"[measurement] {AUTHORITY_STATEMENT}",
             "[measurement] evidence recorded now is labelled "
             f"{', '.join(self.labels)} and still needs manual review",
             "[measurement] restart without --measure for ordinary use"])


# -------------------------------- parsing ----------------------------------


def _flag_values(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """Collect every ``--measure`` value; a valueless flag is a refusal."""
    values: list[str] = []
    refusals: list[str] = []
    index = 0
    items = [str(item) for item in argv]
    while index < len(items):
        item = items[index]
        if item == FLAG:
            if index + 1 >= len(items):
                refusals.append(f"{FLAG} needs a value")
                break
            values.append(items[index + 1])
            index += 2
            continue
        if item.startswith(FLAG + "="):
            values.append(item[len(FLAG) + 1:])
        index += 1
    return values, refusals


def _finite(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded(value: str, bounds: tuple[float, float]) -> float | None:
    number = _finite(value)
    if number is None or not bounds[0] <= number <= bounds[1]:
        return None
    return number


def _parse_calibration(params: str) -> tuple[MeasuredCalibration | None, str]:
    """Parse the four candidate settings the calibration receipt would carry.

    The bounds are the policy's own, so measurement mode can never apply a
    front end an approved receipt would have been refused for.
    """
    fields: dict[str, str] = {}
    for chunk in params.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, separator, raw = chunk.partition("=")
        if not separator:
            return None, f"{chunk!r} is not name=value"
        name = name.strip()
        if name in fields:
            return None, f"{name} given twice"
        fields[name] = raw.strip()
    expected = {"gain", "noise", "vad", "end-silence"}
    if set(fields) != expected:
        return None, ("needs exactly gain, noise, vad, end-silence "
                      f"(got {sorted(fields) or 'nothing'})")
    gain = _bounded(fields["gain"], GAIN_CEILING_BOUNDS)
    noise = _bounded(fields["noise"], NOISE_GATE_BOUNDS)
    vad = _bounded(fields["vad"], VAD_THRESHOLD_BOUNDS)
    end_silence = _finite(fields["end-silence"])
    if gain is None:
        return None, f"gain must be within {GAIN_CEILING_BOUNDS}"
    if noise is None:
        return None, f"noise must be within {NOISE_GATE_BOUNDS}"
    if vad is None:
        return None, f"vad must be within {VAD_THRESHOLD_BOUNDS}"
    if (end_silence is None or end_silence != int(end_silence)
            or not END_SILENCE_BOUNDS_MS[0] <= int(end_silence)
            <= END_SILENCE_BOUNDS_MS[1]):
        return None, ("end-silence must be a whole number of milliseconds "
                      f"within {END_SILENCE_BOUNDS_MS}")
    if noise >= vad:
        return None, "noise must stay below vad"
    return MeasuredCalibration(gain, noise, vad, int(end_silence)), ""


def _parse_keyword(params: str) -> tuple[str | None, str]:
    """Normalize exactly the way the keyword activation state would."""
    if not isinstance(params, str):
        return None, "keyword is invalid"
    normalized = " ".join(unicodedata.normalize("NFKC", params).split())
    if (not normalized or len(normalized) > MAX_KEYWORD_CHARS
            or any(unicodedata.category(char) == "Cc"
                   for char in normalized)):
        return None, "needs one printable term of 1-80 characters"
    return normalized, ""


def parse_measurement_mode(argv: Sequence[str]) -> MeasurementMode:
    """Build one session's measurement mode from process arguments.

    Total and fail-closed: any unknown arm, repeated arm, malformed parameter,
    or out-of-policy value disables *every* arm rather than leaving a session
    half-configured and measuring something other than what the operator
    believes.  The refusals are carried so startup can print them.
    """
    values, refusals = _flag_values(argv or ())
    if not values and not refusals:
        return MeasurementMode.inert()
    calibration: MeasuredCalibration | None = None
    keyword: str | None = None
    delayed_cleanup = False
    seen: set[str] = set()
    for value in values:
        arm, _separator, params = str(value).partition(":")
        arm = arm.strip()
        if arm in seen:
            refusals.append(f"{arm} given twice")
            continue
        seen.add(arm)
        if arm == CALIBRATION_ARM:
            calibration, reason = _parse_calibration(params)
            if reason:
                refusals.append(f"{CALIBRATION_ARM}: {reason}")
        elif arm == KEYWORD_ARM:
            keyword, reason = _parse_keyword(params)
            if reason:
                refusals.append(f"{KEYWORD_ARM}: {reason}")
        elif arm == DELAYED_CLEANUP_ARM:
            if params.strip():
                refusals.append(
                    f"{DELAYED_CLEANUP_ARM}: takes no parameters")
            else:
                delayed_cleanup = True
        else:
            refusals.append(
                f"{arm or '<empty>'} is not a measurement arm "
                f"({', '.join(ARMS)})")
    if refusals:
        return MeasurementMode.inert(refusals)
    return MeasurementMode(calibration, keyword, delayed_cleanup, ())


__all__ = [
    "ARMS",
    "ARM_LABELS",
    "AUTHORITY_STATEMENT",
    "CALIBRATION_ARM",
    "CALIBRATION_LABEL",
    "DELAYED_CLEANUP_ARM",
    "DELAYED_CLEANUP_LABEL",
    "EVIDENCE_KEY",
    "FLAG",
    "KEYWORD_ARM",
    "KEYWORD_LABEL",
    "LABELS",
    "LABEL_BY_ARM",
    "ORDINARY_PATH",
    "SCHEMA_VERSION",
    "MeasuredCalibration",
    "MeasurementMode",
    "MeasurementModeError",
    "evidence_label",
    "parse_measurement_mode",
    "used_measurement_mode",
]
