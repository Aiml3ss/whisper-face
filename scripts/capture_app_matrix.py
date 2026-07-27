#!/usr/bin/env python3
"""Guided, resumable capture of the physical 50-app insertion matrix.

Ledger rows 26, 27, and 28 are code-complete and explicitly claim nothing
physical: the simulation reports `real_apps_exercised: 0`. This tool runs the
session that produces the missing evidence, one app at a time.

For every app it does exactly two things:

1. It reads what the running dictation process reported for that utterance.
   The only durable, externally readable per-utterance record is
   `transcripts.jsonl`, and only its transcript-free keys are read:
   `metrics.insertion_state`, `metrics.insertion_reason`,
   `metrics.paste_attempted`, `metrics.insertion_verified`,
   `metrics.insertion_s`, and whether the routing label began with `outbox/`.
   `raw`, `clean`, and `observed_text` are never read.
2. It asks the operator the one thing no machine can answer — did the intended
   text actually land in the intended place — as a closed choice.

The tool never decides an outcome. If the runtime logged nothing, logged more
than one utterance, or logged no integrity receipt at all, the app is recorded
as blocked with a closed reason instead of being scored. Coverage is reported
as measured: 31 recorded apps are reported as 31, never scaled to 50.

`observe` fills the same session from ordinary use instead of a scripted
sitting. It asks nothing, because there is nobody to ask: it reads the same
transcript-free keys for utterances the owner already dictated and records the
operator verdict as `not-asked-machine-observed`. That is not a weaker pass —
for a readable destination a `verified` receipt is mechanically proven
delivery — but it is a *different* kind of evidence, so it is counted apart
from operator-attested cases everywhere and never merged into them. Passive
evidence only covers the apps the owner actually dictates into, which is why
the summary names every category that still has none.

    uv run scripts/capture_app_matrix.py plan
    uv run scripts/capture_app_matrix.py run
    uv run scripts/capture_app_matrix.py observe
    uv run scripts/capture_app_matrix.py emit --out app_matrix.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_session_support import (  # noqa: E402
    ATTESTED_EVIDENCE_SCOPE,
    CAPABILITY_METRIC_KEYS,
    CAPABILITY_UNAVAILABLE_REASON,
    COMPATIBILITY_REASON_BY_RECEIPT_REASON,
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_TRANSCRIPTS,
    NOT_ASKED_VERDICT,
    OBSERVED_EVIDENCE_SCOPE,
    RECEIPT_STATES,
    ROOT,
    CaptureError,
    Choice,
    Session,
    SessionAborted,
    ask_choice,
    atomic_write_json,
    identifier,
    new_transcript_receipts,
    progress_line,
    read_transcript_receipts,
    transcript_baseline,
    utc_now,
    wait_for_enter,
)


TOOL = "capture_app_matrix"
ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_ID = "physical-app-insertion-matrix"
EVIDENCE_SCOPE = "operator-attested-physical-session"
OBSERVED_ARTIFACT_SCOPE = "runtime-observed-passive-use"
MIXED_ARTIFACT_SCOPE = "mixed-operator-attested-and-runtime-observed"
DEFAULT_SESSION = DEFAULT_EVIDENCE_DIR / "app-matrix-session.json"
FIFTY_APP_TARGET = 50
PROFILE_CORPUS = ROOT / "benchmarks" / "insertion_reliability_cases.json"

# Why a logged utterance could not become passive evidence. Each is a fact
# about the record, never a judgement about the app.
OBSERVE_SKIP_REASONS = frozenset({
    "no-event-id",
    "app-identity-withheld",
    "no-app-identity",
    "runtime-reported-no-receipt",
    "already-observed",
    "operator-already-answered",
})

CATEGORIES = (
    "native-cocoa",
    "electron-chromium",
    "browser",
    "web-text-area",
    "terminal",
    "ide",
    "office",
    "messaging",
    "notes",
    "mail",
)

# Fixed neutral phrases. Only the phrase *id* is ever written to an artifact;
# the words live here, in source, so no artifact can carry dictated text.
PHRASES = {
    "neutral-sentence": "The prototype is ready for review.",
    "neutral-numbers": "Set the sample rate to 48 kilohertz.",
    "neutral-two-sentence": (
        "The prototype is ready for review. Please add comments by Friday."),
}

TEXT_VERDICTS = (
    Choice("1", "correct-text-in-intended-target",
           "the intended text appeared, correct, in the intended field"),
    Choice("2", "altered-text-in-intended-target",
           "text appeared in the intended field but the words were wrong"),
    Choice("3", "partial-text-in-intended-target",
           "only part of the text appeared in the intended field"),
    Choice("4", "duplicated-text",
           "the text appeared more than once"),
    Choice("5", "correct-text-in-wrong-target",
           "the text appeared, but in a different field, window, or app"),
    Choice("6", "no-text-appeared",
           "nothing was inserted anywhere"),
    Choice("7", "could-not-judge",
           "the field is not readable enough to judge (record as blocked)"),
)

APP_BEHAVIORS = (
    Choice("1", "normal", "the app behaved normally"),
    Choice("2", "visible-delay", "the app stalled visibly but recovered"),
    Choice("3", "unresponsive", "the app became unresponsive or beachballed"),
    Choice("4", "crashed", "the app crashed or quit"),
    Choice("5", "focus-changed", "focus jumped somewhere the operator did not"),
)

BLOCKED_REASONS = frozenset({
    "app-not-installed",
    "operator-skipped",
    "no-runtime-record",
    "ambiguous-runtime-records",
    "runtime-reported-no-receipt",
    "operator-could-not-judge",
})

# Fifty curated targets. Categories name the surface class that actually
# predicts Accessibility behavior. `bundle_id` is the macOS bundle the runtime
# is expected to report; web surfaces record `null` because the reported bundle
# is whichever browser hosts them.
DEFAULT_APPS: tuple[dict[str, Any], ...] = (
    # native Cocoa text
    {"id": "textedit", "name": "TextEdit", "category": "native-cocoa",
     "bundle_id": "com.apple.TextEdit", "phrase": "neutral-sentence",
     "target_hint": "a new empty plain-text document"},
    {"id": "notes-app", "name": "Notes", "category": "native-cocoa",
     "bundle_id": "com.apple.Notes", "phrase": "neutral-sentence",
     "target_hint": "the body of a new note"},
    {"id": "stickies", "name": "Stickies", "category": "native-cocoa",
     "bundle_id": "com.apple.Stickies", "phrase": "neutral-sentence",
     "target_hint": "a new empty sticky"},
    {"id": "reminders", "name": "Reminders", "category": "native-cocoa",
     "bundle_id": "com.apple.reminders", "phrase": "neutral-sentence",
     "target_hint": "the title field of a new reminder"},
    {"id": "pages", "name": "Pages", "category": "native-cocoa",
     "bundle_id": "com.apple.iWork.Pages", "phrase": "neutral-two-sentence",
     "target_hint": "the body of a blank document"},
    {"id": "keynote", "name": "Keynote", "category": "native-cocoa",
     "bundle_id": "com.apple.iWork.Keynote", "phrase": "neutral-sentence",
     "target_hint": "a slide's title text box, in edit mode"},
    {"id": "calendar", "name": "Calendar", "category": "native-cocoa",
     "bundle_id": "com.apple.iCal", "phrase": "neutral-sentence",
     "target_hint": "the title field of a new event"},
    {"id": "finder-rename", "name": "Finder rename field",
     "category": "native-cocoa", "bundle_id": "com.apple.finder",
     "phrase": "neutral-sentence",
     "target_hint": "a file name field opened with Return"},
    # Electron / Chromium shells
    {"id": "slack", "name": "Slack", "category": "electron-chromium",
     "bundle_id": "com.tinyspeck.slackmacgap", "phrase": "neutral-sentence",
     "target_hint": "the message composer of a private channel"},
    {"id": "discord", "name": "Discord", "category": "electron-chromium",
     "bundle_id": "com.hnc.Discord", "phrase": "neutral-sentence",
     "target_hint": "the message box of a private server"},
    {"id": "obsidian", "name": "Obsidian", "category": "electron-chromium",
     "bundle_id": "md.obsidian", "phrase": "neutral-two-sentence",
     "target_hint": "an empty scratch note in edit mode"},
    {"id": "notion", "name": "Notion", "category": "electron-chromium",
     "bundle_id": "notion.id", "phrase": "neutral-sentence",
     "target_hint": "an empty paragraph block on a scratch page"},
    {"id": "figma", "name": "Figma", "category": "electron-chromium",
     "bundle_id": "com.figma.Desktop", "phrase": "neutral-sentence",
     "target_hint": "a text layer in edit mode on a scratch file"},
    {"id": "linear", "name": "Linear", "category": "electron-chromium",
     "bundle_id": "com.linear", "phrase": "neutral-sentence",
     "target_hint": "the title field of a new draft issue"},
    {"id": "signal-desktop", "name": "Signal", "category": "electron-chromium",
     "bundle_id": "org.whispersystems.signal-desktop",
     "phrase": "neutral-sentence",
     "target_hint": "the composer of a Note to Self chat"},
    {"id": "spotify", "name": "Spotify", "category": "electron-chromium",
     "bundle_id": "com.spotify.client", "phrase": "neutral-sentence",
     "target_hint": "the search field"},
    # browser chrome (the omnibox and native browser fields)
    {"id": "safari-address", "name": "Safari address bar",
     "category": "browser", "bundle_id": "com.apple.Safari",
     "phrase": "neutral-sentence", "target_hint": "the address bar of a new tab"},
    {"id": "chrome-address", "name": "Google Chrome omnibox",
     "category": "browser", "bundle_id": "com.google.Chrome",
     "phrase": "neutral-sentence", "target_hint": "the omnibox of a new tab"},
    {"id": "firefox-address", "name": "Firefox address bar",
     "category": "browser", "bundle_id": "org.mozilla.firefox",
     "phrase": "neutral-sentence", "target_hint": "the address bar of a new tab"},
    {"id": "arc-command", "name": "Arc command bar", "category": "browser",
     "bundle_id": "company.thebrowser.Browser", "phrase": "neutral-sentence",
     "target_hint": "the command bar of a new tab"},
    {"id": "edge-address", "name": "Microsoft Edge address bar",
     "category": "browser", "bundle_id": "com.microsoft.edgemac",
     "phrase": "neutral-sentence", "target_hint": "the address bar of a new tab"},
    # in-page web editors, hosted by whichever browser the operator uses
    {"id": "web-plain-textarea", "name": "plain HTML textarea",
     "category": "web-text-area", "bundle_id": None,
     "phrase": "neutral-sentence",
     "target_hint": "a bare <textarea> on a local scratch page"},
    {"id": "web-contenteditable", "name": "contenteditable div",
     "category": "web-text-area", "bundle_id": None,
     "phrase": "neutral-sentence",
     "target_hint": "a contenteditable block on a local scratch page"},
    {"id": "web-search-input", "name": "single-line search input",
     "category": "web-text-area", "bundle_id": None,
     "phrase": "neutral-sentence",
     "target_hint": "an <input type=search> on a local scratch page"},
    {"id": "web-rich-editor", "name": "rich-text web editor",
     "category": "web-text-area", "bundle_id": None,
     "phrase": "neutral-two-sentence",
     "target_hint": "a rich-text editor in a scratch document"},
    {"id": "web-code-editor", "name": "browser code editor",
     "category": "web-text-area", "bundle_id": None,
     "phrase": "neutral-numbers",
     "target_hint": "a CodeMirror/Monaco editor in a scratch page"},
    {"id": "web-comment-box", "name": "web comment box",
     "category": "web-text-area", "bundle_id": None,
     "phrase": "neutral-sentence",
     "target_hint": "a comment field on a scratch page or private draft"},
    # terminals
    {"id": "terminal-app", "name": "Terminal", "category": "terminal",
     "bundle_id": "com.apple.Terminal", "phrase": "neutral-sentence",
     "target_hint": "an empty shell prompt"},
    {"id": "iterm2", "name": "iTerm2", "category": "terminal",
     "bundle_id": "com.googlecode.iterm2", "phrase": "neutral-sentence",
     "target_hint": "an empty shell prompt"},
    {"id": "ghostty", "name": "Ghostty", "category": "terminal",
     "bundle_id": "com.mitchellh.ghostty", "phrase": "neutral-sentence",
     "target_hint": "an empty shell prompt"},
    {"id": "warp", "name": "Warp", "category": "terminal",
     "bundle_id": "dev.warp.Warp-Stable", "phrase": "neutral-sentence",
     "target_hint": "an empty command block"},
    {"id": "terminal-tui-editor", "name": "terminal TUI editor",
     "category": "terminal", "bundle_id": "com.apple.Terminal",
     "phrase": "neutral-sentence",
     "target_hint": "a full-screen terminal editor in insert mode"},
    # IDEs
    {"id": "vscode", "name": "Visual Studio Code", "category": "ide",
     "bundle_id": "com.microsoft.VSCode", "phrase": "neutral-numbers",
     "target_hint": "an untitled scratch buffer"},
    {"id": "xcode", "name": "Xcode", "category": "ide",
     "bundle_id": "com.apple.dt.Xcode", "phrase": "neutral-numbers",
     "target_hint": "a comment line in a scratch file"},
    {"id": "cursor", "name": "Cursor", "category": "ide",
     "bundle_id": "com.todesktop.230313mzl4w4u92", "phrase": "neutral-numbers",
     "target_hint": "an untitled scratch buffer"},
    {"id": "zed", "name": "Zed", "category": "ide", "bundle_id": "dev.zed.Zed",
     "phrase": "neutral-numbers", "target_hint": "an untitled scratch buffer"},
    {"id": "sublime-text", "name": "Sublime Text", "category": "ide",
     "bundle_id": "com.sublimetext.4", "phrase": "neutral-numbers",
     "target_hint": "an untitled scratch buffer"},
    {"id": "intellij-idea", "name": "IntelliJ IDEA", "category": "ide",
     "bundle_id": "com.jetbrains.intellij", "phrase": "neutral-numbers",
     "target_hint": "a scratch file editor"},
    # office suites
    {"id": "word", "name": "Microsoft Word", "category": "office",
     "bundle_id": "com.microsoft.Word", "phrase": "neutral-two-sentence",
     "target_hint": "the body of a blank document"},
    {"id": "excel", "name": "Microsoft Excel", "category": "office",
     "bundle_id": "com.microsoft.Excel", "phrase": "neutral-numbers",
     "target_hint": "a cell in edit mode"},
    {"id": "powerpoint", "name": "Microsoft PowerPoint", "category": "office",
     "bundle_id": "com.microsoft.Powerpoint", "phrase": "neutral-sentence",
     "target_hint": "a slide text box in edit mode"},
    {"id": "numbers", "name": "Numbers", "category": "office",
     "bundle_id": "com.apple.iWork.Numbers", "phrase": "neutral-numbers",
     "target_hint": "a cell in edit mode"},
    # messaging
    {"id": "messages", "name": "Messages", "category": "messaging",
     "bundle_id": "com.apple.MobileSMS", "phrase": "neutral-sentence",
     "target_hint": "the composer of a draft to yourself"},
    {"id": "whatsapp", "name": "WhatsApp", "category": "messaging",
     "bundle_id": "net.whatsapp.WhatsApp", "phrase": "neutral-sentence",
     "target_hint": "the composer of a message to yourself"},
    {"id": "telegram", "name": "Telegram", "category": "messaging",
     "bundle_id": "ru.keepcoder.Telegram", "phrase": "neutral-sentence",
     "target_hint": "the composer of a Saved Messages chat"},
    {"id": "teams", "name": "Microsoft Teams", "category": "messaging",
     "bundle_id": "com.microsoft.teams2", "phrase": "neutral-sentence",
     "target_hint": "the composer of a chat with yourself"},
    # notes apps that are neither stock Cocoa nor Electron shells
    {"id": "bear", "name": "Bear", "category": "notes",
     "bundle_id": "net.shinyfrog.bear", "phrase": "neutral-two-sentence",
     "target_hint": "the body of a new note"},
    {"id": "craft", "name": "Craft", "category": "notes",
     "bundle_id": "com.lukilabs.lukiapp", "phrase": "neutral-sentence",
     "target_hint": "an empty block in a new document"},
    # mail composers
    {"id": "mail-app", "name": "Mail", "category": "mail",
     "bundle_id": "com.apple.mail", "phrase": "neutral-two-sentence",
     "target_hint": "the body of a new unaddressed draft"},
    {"id": "outlook", "name": "Microsoft Outlook", "category": "mail",
     "bundle_id": "com.microsoft.Outlook", "phrase": "neutral-two-sentence",
     "target_hint": "the body of a new unaddressed draft"},
)


# ------------------------------- app lists --------------------------------


def validate_apps(apps: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Accept only a closed, non-empty, unique app plan."""
    if not isinstance(apps, Sequence) or not apps:
        raise CaptureError("the app list must be a non-empty list")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for entry in apps:
        if not isinstance(entry, Mapping) or set(entry) != {
                "id", "name", "category", "bundle_id", "phrase",
                "target_hint"}:
            raise CaptureError(
                "each app needs exactly id, name, category, bundle_id, "
                "phrase, target_hint")
        if not identifier(entry["id"]) or entry["id"] in seen:
            raise CaptureError(f"invalid or duplicate app id: {entry.get('id')!r}")
        if entry["category"] not in CATEGORIES:
            raise CaptureError(f"unknown category: {entry['category']!r}")
        if entry["phrase"] not in PHRASES:
            raise CaptureError(f"unknown phrase id: {entry['phrase']!r}")
        if entry["bundle_id"] is not None and not isinstance(
                entry["bundle_id"], str):
            raise CaptureError("bundle_id must be a string or null")
        for field in ("name", "target_hint"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise CaptureError(f"app {entry['id']} needs a {field}")
        seen.add(entry["id"])
        validated.append(dict(entry))
    return tuple(validated)


def load_apps(path: Path | None) -> tuple[dict[str, Any], ...]:
    if path is None:
        return validate_apps(DEFAULT_APPS)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CaptureError(f"cannot read app list {path}: {error}") from error
    if not isinstance(payload, Mapping) or set(payload) != {"apps"}:
        raise CaptureError("an app list file must contain only an 'apps' list")
    return validate_apps(payload["apps"])


def plan_digest(apps: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        [[item["id"], item["category"]] for item in apps],
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def category_plan(apps: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item["category"]) for item in apps)
    return {name: counts[name] for name in CATEGORIES if counts[name]}


# ------------------------------ the session -------------------------------


def _group_progress(apps: Sequence[Mapping[str, Any]],
                    session: Session) -> dict[str, tuple[int, int]]:
    planned = Counter(str(item["category"]) for item in apps)
    recorded = Counter(
        str(session.records[key]["category"]) for key in session.records)
    return {name: (recorded[name], planned[name]) for name in planned}


def run_session(apps: Sequence[Mapping[str, Any]], session: Session, *,
                transcripts: Path, reader: TextIO, writer: TextIO) -> int:
    """Walk the plan, reading the runtime and asking the operator."""
    writer.write(
        "\nPHYSICAL APP INSERTION MATRIX\n"
        f"session file: {session.path}\n"
        f"runtime record: {transcripts}\n"
        "Only transcript-free fields are read. Dictate the printed phrase and\n"
        "nothing else; the phrase text never enters the artifact.\n")
    if not transcripts.exists():
        writer.write(
            f"\n{transcripts} does not exist yet. Start Whisper Face and "
            "dictate once before running this session.\n")
        return 2

    remaining = [item for item in apps if not session.answered(str(item["id"]))]
    if not remaining:
        writer.write("\nEvery planned app is already answered. Nothing to do.\n")
        return 0

    try:
        for app in remaining:
            case_id = str(app["id"])
            writer.write(
                "\n" + "-" * 68 + "\n"
                + progress_line(len(session.records), len(apps),
                                _group_progress(apps, session)) + "\n"
                f"NEXT: {app['name']}  ({app['category']})\n"
                f"  1. Open {app['name']} and put the caret in "
                f"{app['target_hint']}.\n"
                f"  2. Dictate exactly: \"{PHRASES[str(app['phrase'])]}\"\n"
                "  3. Wait for the runtime to finish inserting.\n")
            availability = ask_choice(
                f"Is {app['name']} available to test right now?",
                (Choice("1", "ready", "yes, it is open and focused"),
                 Choice("2", "app-not-installed", "not installed on this Mac"),
                 Choice("3", "operator-skipped", "skip it for another reason")),
                reader=reader, writer=writer)
            if availability != "ready":
                session.block(case_id, availability,
                              {"category": app["category"]})
                continue

            seen_ids, baseline = transcript_baseline(transcripts)
            wait_for_enter(
                "Dictate the phrase now, then press Return here.",
                reader=reader, writer=writer)
            fresh = new_transcript_receipts(transcripts, seen_ids, baseline)
            if not fresh:
                writer.write(
                    "  The runtime logged no new utterance for this app.\n")
                session.block(case_id, "no-runtime-record",
                              {"category": app["category"]})
                continue
            if len(fresh) != 1:
                writer.write(
                    f"  The runtime logged {len(fresh)} utterances; a case "
                    "needs exactly one.\n")
                session.block(case_id, "ambiguous-runtime-records",
                              {"category": app["category"],
                               "records_observed": len(fresh)})
                continue
            receipt = fresh[0]
            if not receipt.has_receipt:
                writer.write(
                    "  The runtime produced no insertion receipt for that "
                    f"utterance (state={receipt.insertion_state!r}, "
                    f"reason={receipt.insertion_reason!r}).\n")
                session.block(case_id, "runtime-reported-no-receipt",
                              {"category": app["category"],
                               "runtime": receipt.as_payload()})
                continue

            writer.write(
                f"  Runtime reported: state={receipt.insertion_state}, "
                f"reason={receipt.insertion_reason}, "
                f"paste_attempted={receipt.paste_attempted}\n")
            verdict = ask_choice(
                "What actually appeared on screen?", TEXT_VERDICTS,
                reader=reader, writer=writer)
            if verdict == "could-not-judge":
                session.block(case_id, "operator-could-not-judge",
                              {"category": app["category"],
                               "runtime": receipt.as_payload()})
                continue
            behavior = ask_choice(
                f"How did {app['name']} itself behave?", APP_BEHAVIORS,
                reader=reader, writer=writer)
            bundle = receipt.app_bundle
            expected = app["bundle_id"]
            session.record(case_id, {
                "evidence_scope": ATTESTED_EVIDENCE_SCOPE,
                "category": app["category"],
                "phrase": app["phrase"],
                "recorded_utc": utc_now(),
                "runtime": {
                    "source": "transcripts-jsonl",
                    "expected_bundle_id": expected,
                    "bundle_matches_plan": (
                        None if expected is None or bundle is None
                        else bundle == expected),
                    **receipt.as_payload(),
                },
                "capabilities": receipt.capabilities,
                "operator": {
                    "text_verdict": verdict,
                    "app_behavior": behavior,
                },
            })
    except SessionAborted as stop:
        writer.write(f"\nSession paused ({stop}). Re-run to resume.\n")

    writer.write(
        "\n" + progress_line(len(session.records), len(apps),
                             _group_progress(apps, session)) + "\n"
        f"blocked: {len(session.blocked)}\n"
        f"session saved to {session.path}\n"
        "Build the artifact with: uv run scripts/capture_app_matrix.py emit\n")
    return 0


# --------------------------- passive observation --------------------------


def bundle_index(apps: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each unambiguous bundle id onto the curated app that claims it.

    A bundle two curated cases share names no single surface: Terminal hosts
    both a bare shell prompt and a full-screen TUI editor, and those behave
    differently under Accessibility. Passive evidence cannot say which of them
    the caret was in, so such a bundle claims neither case and is recorded
    off-plan instead of being guessed into one.
    """
    counts = Counter(str(app["bundle_id"]) for app in apps
                     if app["bundle_id"] is not None)
    return {
        str(app["bundle_id"]): dict(app)
        for app in apps
        if app["bundle_id"] is not None and counts[str(app["bundle_id"])] == 1
    }


def observed_case_id(bundle: str) -> str:
    """Name an off-plan case after the bundle that produced it.

    The digest keeps two bundles that slugify alike from merging into one
    case, which would silently overstate how many apps were exercised.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", bundle.lower()).strip("-")[:40]
    digest = hashlib.sha256(bundle.encode("utf-8")).hexdigest()[:8]
    return f"observed-{slug}-{digest}" if slug else f"observed-{digest}"


def _consumed_event_ids(session: Session) -> set[str]:
    """Every transcript id already folded into this session."""
    consumed: set[str] = set()
    for record in session.records.values():
        runtime = record.get("runtime")
        if isinstance(runtime, Mapping):
            consumed.update(
                str(item) for item in runtime.get("observed_event_ids") or ())
    return consumed


def _merged_counts(existing: Mapping[str, Any] | None, key: str,
                   fresh: Counter) -> dict[str, int]:
    base: Counter = Counter()
    if existing is not None:
        stored = existing.get(key)
        if isinstance(stored, Mapping):
            base.update({str(name): int(count)
                         for name, count in stored.items()})
    base.update(fresh)
    return dict(sorted(base.items()))


def _observed_payload(app: Mapping[str, Any] | None, bundle: str,
                      fresh: Sequence[Any],
                      existing: Mapping[str, Any] | None) -> dict[str, Any]:
    """Aggregate every observed utterance for one app into one case.

    An app is dictated into many times, so the case keeps counts rather than
    the last utterance: keeping only the newest would quietly discard every
    earlier outcome, including every failure.
    """
    previous = (existing or {}).get("runtime")
    previous = previous if isinstance(previous, Mapping) else None

    states = Counter(str(item.insertion_state) for item in fresh)
    reasons = Counter(str(item.insertion_reason) for item in fresh)
    shapes = Counter(str(item.readback_shape) for item in fresh
                     if item.readback_shape is not None)
    attempted = Counter(
        "attempted" if item.paste_attempted is True
        else "not-attempted" if item.paste_attempted is False
        else "unreported" for item in fresh)

    outcomes = list((existing or {}).get("compatibility_outcomes") or ())
    observations = list((existing or {}).get("capability_observations") or ())
    for item in fresh:
        outcome = item.compatibility_outcome()
        if outcome is None:
            continue
        outcomes.append(outcome)
        if item.capabilities:
            observations.append({
                "capabilities": item.capabilities,
                "outcome": outcome,
                "count": 1,
            })

    event_ids = sorted(
        set(str(item) for item in (previous or {}).get("observed_event_ids")
            or ())
        | {str(item.event_id) for item in fresh})
    latencies = [item.insertion_ms for item in fresh
                 if item.insertion_ms is not None]
    if previous is not None and previous.get("insertion_ms_max") is not None:
        latencies.append(float(previous["insertion_ms_max"]))

    expected = app["bundle_id"] if app is not None else None
    return {
        "category": app["category"] if app is not None else None,
        "observed_utc": utc_now(),
        "runtime": {
            "source": "transcripts-jsonl-passive",
            "expected_bundle_id": expected,
            "bundle_matches_plan": None if expected is None else (
                bundle == expected),
            "app_bundle": bundle,
            "app_identity_withheld": False,
            "utterances_observed": len(event_ids),
            "insertion_states": _merged_counts(
                previous, "insertion_states", states),
            "insertion_reasons": _merged_counts(
                previous, "insertion_reasons", reasons),
            "readback_shapes": _merged_counts(
                previous, "readback_shapes", shapes),
            "paste_attempts": _merged_counts(
                previous, "paste_attempts", attempted),
            "route_outbox": (
                int((previous or {}).get("route_outbox") or 0)
                + sum(1 for item in fresh if item.route_outbox is True)),
            "insertion_ms_max": max(latencies) if latencies else None,
            "observed_event_ids": event_ids,
        },
        "compatibility_outcomes": outcomes,
        "capability_observations": observations,
        # Passive use cannot ask the operator anything. The verdict is a third
        # value meaning "not asked", never one of the real human answers.
        "operator": {
            "text_verdict": NOT_ASKED_VERDICT,
            "app_behavior": NOT_ASKED_VERDICT,
        },
        "machine_verdict": _machine_verdict(
            _merged_counts(previous, "insertion_states", states)),
    }


def _machine_verdict(states: Mapping[str, int]) -> dict[str, Any]:
    """State plainly what the runtime proved, and what it did not.

    A `verified` receipt is mechanically proven delivery into a readable
    destination — stronger than an eyeball, not weaker. A `conflict` is a
    proven failure to land as intended. An `unverifiable` receipt is not a
    failure: it says the destination could not be read, so delivery stayed
    unproven in either direction, and it is reported as exactly that.
    """
    return {
        "basis": "runtime-insertion-receipts",
        "operator_asked": False,
        "proven_delivery": int(states.get("verified", 0)),
        "proven_not_delivered_as_intended": int(states.get("conflict", 0)),
        "delivery_unproven": int(states.get("unverifiable", 0)),
        "unresolved": int(states.get("unresolved", 0)),
    }


def observe_transcript(apps: Sequence[Mapping[str, Any]], session: Session, *,
                       transcripts: Path) -> dict[str, Any]:
    """Fold every not-yet-observed insertion in the transcript into a session.

    The transcript is re-read by path, never followed through a held handle:
    `dictate.py` atomically replaces the file when it trims history, so ids
    vanish from it over time. De-duplication is therefore by transcript id
    against what the session already consumed, which makes re-running this
    idempotent and makes a replaced file lose nothing already recorded.
    """
    receipts = read_transcript_receipts(transcripts)
    index = bundle_index(apps)
    consumed = _consumed_event_ids(session)
    skipped: Counter = Counter()
    grouped: dict[str, list[Any]] = {}
    bundles: dict[str, str] = {}
    fresh_ids: set[str] = set()

    for receipt in receipts:
        if receipt.event_id is None:
            # The phone endpoint writes no id, so this utterance cannot be
            # de-duplicated and must not be counted at all.
            skipped["no-event-id"] += 1
            continue
        if receipt.event_id in consumed or receipt.event_id in fresh_ids:
            skipped["already-observed"] += 1
            continue
        if receipt.app_identity_withheld:
            # On Windows the runtime writes a window title here, which can
            # carry a document name. Withheld identity means no app to name.
            skipped["app-identity-withheld"] += 1
            continue
        if receipt.app_bundle is None:
            skipped["no-app-identity"] += 1
            continue
        if not receipt.has_receipt:
            skipped["runtime-reported-no-receipt"] += 1
            continue
        bundle = str(receipt.app_bundle)
        planned = index.get(bundle)
        case_id = str(planned["id"]) if planned is not None else (
            observed_case_id(bundle))
        fresh_ids.add(str(receipt.event_id))
        grouped.setdefault(case_id, []).append(receipt)
        bundles[case_id] = bundle

    merged: list[str] = []
    protected: list[str] = []
    for case_id in sorted(grouped):
        existing = session.observed(case_id)
        if existing is None and session.answered(case_id):
            # An operator answered this app. Passive evidence cannot answer
            # the same question, so it never displaces the answer.
            protected.append(case_id)
            skipped["operator-already-answered"] += len(grouped[case_id])
            continue
        payload = _observed_payload(
            index.get(bundles[case_id]), bundles[case_id],
            grouped[case_id], existing)
        if session.observe(case_id, payload):
            merged.append(case_id)
        else:  # pragma: no cover - observed() already proved it is mergeable
            protected.append(case_id)

    if merged:
        session.save()
    unknown = set(skipped) - OBSERVE_SKIP_REASONS
    if unknown:  # pragma: no cover - guards the closed set against drift
        raise CaptureError(
            f"skip reason outside the closed set: {sorted(unknown)}")
    return {
        "utterances_read": len(receipts),
        "utterances_observed": sum(
            len(grouped[case_id]) for case_id in merged),
        "cases_merged": merged,
        "cases_protected_by_operator": protected,
        "skipped": dict(sorted(skipped.items())),
    }


def render_observation(outcome: Mapping[str, Any]) -> str:
    lines = [
        "PASSIVE OBSERVATION",
        f"utterances read: {outcome['utterances_read']} · newly observed: "
        f"{outcome['utterances_observed']} · cases written: "
        f"{len(outcome['cases_merged'])}",
    ]
    if outcome["cases_protected_by_operator"]:
        lines.append(
            "left alone (an operator already answered): "
            + ", ".join(outcome["cases_protected_by_operator"]))
    if outcome["skipped"]:
        lines.append("not usable as evidence: " + ", ".join(
            f"{key} {value}" for key, value in outcome["skipped"].items()))
    return "\n".join(lines)


# ------------------------------- artifacts --------------------------------


def build_artifact(apps: Sequence[Mapping[str, Any]],
                   session_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a recorded session into a coverage-honest matrix artifact.

    A session may hold both kinds of case. An operator-attested case answers
    "did the correct text appear in the intended target"; a runtime-observed
    case answers only what the runtime's own receipts prove. The two are
    counted separately everywhere, and never summed into one verdict.
    """
    records = list(session_payload.get("records") or ())
    blocked = list(session_payload.get("blocked") or ())
    planned = category_plan(apps)

    attested = [item for item in records if not _is_observed(item)]
    observed = [item for item in records if _is_observed(item)]
    # A case only counts against the curated matrix when it names one of the
    # plan's categories. An app the owner happens to dictate into that is not
    # on the list is real evidence, but it fills no planned slot.
    curated = [item for item in records if item.get("category") in planned]
    off_plan = [item for item in records if item.get("category") not in planned]

    recorded_by_category = Counter(str(item["category"]) for item in curated)
    blocked_by_category = Counter(str(item["category"]) for item in blocked)

    states: Counter = Counter()
    reasons: Counter = Counter()
    for item in attested:
        states[str(item["runtime"]["insertion_state"])] += 1
        reasons[str(item["runtime"]["insertion_reason"])] += 1
    observed_states: Counter = Counter()
    observed_reasons: Counter = Counter()
    observed_shapes: Counter = Counter()
    observed_utterances = 0
    for item in observed:
        runtime = item["runtime"]
        observed_states.update(_int_counts(runtime.get("insertion_states")))
        observed_reasons.update(_int_counts(runtime.get("insertion_reasons")))
        observed_shapes.update(_int_counts(runtime.get("readback_shapes")))
        observed_utterances += int(runtime.get("utterances_observed") or 0)
    states.update(observed_states)
    reasons.update(observed_reasons)

    verdicts = Counter(
        str(item["operator"]["text_verdict"]) for item in records)
    behaviors = Counter(
        str(item["operator"]["app_behavior"]) for item in records)
    blocked_reasons = Counter(str(item["reason"]) for item in blocked)

    agreed = sum(
        1 for item in attested
        if item["operator"]["text_verdict"] == "correct-text-in-intended-target"
        and item["runtime"]["insertion_state"] == "verified")
    disagreed = sum(
        1 for item in attested
        if (item["operator"]["text_verdict"]
            == "correct-text-in-intended-target")
        != (item["runtime"]["insertion_state"] == "verified"))

    outcomes = []
    capability_pairs = []
    for item in records:
        if _is_observed(item):
            outcomes.extend(item.get("compatibility_outcomes") or ())
            capability_pairs.extend(item.get("capability_observations") or ())
            continue
        outcome = item.get("compatibility_outcome")
        if outcome is None:
            outcome = _compatibility_outcome(item["runtime"])
        if outcome is not None:
            outcomes.append(outcome)
            if item.get("capabilities"):
                capability_pairs.append({
                    "capabilities": item["capabilities"],
                    "outcome": outcome,
                    "count": 1,
                })

    recorded = len(curated)
    exercised = len(records)
    empty_categories = sorted(
        name for name in planned if not recorded_by_category[name])
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact": ARTIFACT_ID,
        "privacy": "transcript-free",
        "evidence_scope": _artifact_scope(attested, observed),
        "physical_evidence": exercised > 0,
        "generated_utc": utc_now(),
        "plan_digest": session_payload.get("plan_digest"),
        "receipt_vocabulary": "insertion_integrity.ReceiptState/ReceiptReason",
        "insertion_profile_reference": str(
            PROFILE_CORPUS.relative_to(ROOT)),
        "coverage": {
            "apps_planned": len(apps),
            "apps_recorded": recorded,
            "apps_blocked": len(blocked),
            "apps_not_attempted": len(apps) - recorded - len(blocked),
            "apps_observed_off_plan": len(off_plan),
            "extrapolated": False,
            "categories_without_evidence": empty_categories,
            "by_category": {
                name: {
                    "planned": planned[name],
                    "recorded": recorded_by_category[name],
                    "blocked": blocked_by_category[name],
                    "not_attempted": (planned[name]
                                      - recorded_by_category[name]
                                      - blocked_by_category[name]),
                }
                for name in sorted(planned)
            },
        },
        "claims": {
            "real_apps_exercised": exercised,
            "real_apps_exercised_definition": (
                "distinct apps with at least one recorded insertion, whether "
                "the app was on the curated plan or merely used"),
            "fifty_app_claim": exercised >= FIFTY_APP_TARGET,
            "fifty_app_claim_reason": (
                "fifty-apps-recorded" if exercised >= FIFTY_APP_TARGET
                else f"{exercised}-of-{FIFTY_APP_TARGET}-apps-recorded"),
            "four_nines_claim": False,
            "four_nines_claim_reason": (
                "one-attempt-per-app-cannot-support-a-four-nines-rate"),
        },
        "runtime_receipts": {
            "states": dict(sorted(states.items())),
            "reasons": dict(sorted(reasons.items())),
        },
        "operator_observations": {
            "attested_cases": len(attested),
            "text_verdicts": dict(sorted(verdicts.items())),
            "app_behaviors": dict(sorted(behaviors.items())),
            "not_asked_verdict": NOT_ASKED_VERDICT,
        },
        "machine_observed": {
            "definition": (
                "cases harvested from ordinary use, where no operator was "
                "asked anything; a verified receipt is mechanically proven "
                "delivery into a readable destination, an unverifiable one "
                "means the destination could not be read either way"),
            "cases": len(observed),
            "utterances": observed_utterances,
            "insertion_states": dict(sorted(observed_states.items())),
            "insertion_reasons": dict(sorted(observed_reasons.items())),
            "readback_shapes": dict(sorted(observed_shapes.items())),
        },
        "agreement": {
            "definition": (
                "operator reported correct-text-in-intended-target and the "
                "runtime receipt state was verified"),
            "both": agreed,
            "disagreements": disagreed,
            "not_comparable": len(observed),
            "not_comparable_reason": (
                "no-operator-verdict-exists-for-a-machine-observed-case"),
        },
        "blocked": {
            "count": len(blocked),
            "reasons": dict(sorted(blocked_reasons.items())),
        },
        "compatibility": {
            "outcomes": outcomes,
            "observations": capability_pairs,
            "capability_buckets_available": bool(capability_pairs),
            "capability_blocked_reason": (
                None if capability_pairs else CAPABILITY_UNAVAILABLE_REASON),
            "required_runtime_metric_keys": list(CAPABILITY_METRIC_KEYS),
        },
        "records": records,
    }


def _is_observed(record: Mapping[str, Any]) -> bool:
    """True for a case harvested from use rather than answered by a human.

    A record written before this distinction existed carries no scope at all,
    and every such record came from a guided session, so absence means
    attested.
    """
    return record.get("evidence_scope") == OBSERVED_EVIDENCE_SCOPE


def _int_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for name, count in value.items():
        if isinstance(count, bool) or not isinstance(count, int):
            continue
        counts[str(name)] = count
    return counts


def _artifact_scope(attested: Sequence[Any], observed: Sequence[Any]) -> str:
    if observed and attested:
        return MIXED_ARTIFACT_SCOPE
    if observed:
        return OBSERVED_ARTIFACT_SCOPE
    return EVIDENCE_SCOPE


def _compatibility_outcome(runtime: Mapping[str, Any]) -> dict[str, Any] | None:
    """Translate one recorded receipt into closed compatibility buckets."""
    state = runtime.get("insertion_state")
    reason = COMPATIBILITY_REASON_BY_RECEIPT_REASON.get(
        str(runtime.get("insertion_reason")))
    attempted = runtime.get("paste_attempted")
    if state not in RECEIPT_STATES or reason is None or not isinstance(
            attempted, bool):
        return None
    return {"state": state, "reason": reason, "paste_attempted": attempted}


def render_summary(artifact: Mapping[str, Any]) -> str:
    coverage = artifact["coverage"]
    claims = artifact["claims"]
    machine = artifact["machine_observed"]
    operator = artifact["operator_observations"]
    lines = [
        "PHYSICAL APP INSERTION MATRIX",
        f"evidence scope: {artifact['evidence_scope']}",
        f"apps recorded: {coverage['apps_recorded']}/"
        f"{coverage['apps_planned']} · blocked: {coverage['apps_blocked']} · "
        f"not attempted: {coverage['apps_not_attempted']} · off-plan apps "
        f"observed: {coverage['apps_observed_off_plan']}",
        f"real apps exercised: {claims['real_apps_exercised']} · "
        f"50-app claim: {'yes' if claims['fifty_app_claim'] else 'no'} "
        f"({claims['fifty_app_claim_reason']}) · four-nines claim: no",
        f"operator-attested cases: {operator['attested_cases']} · "
        f"machine-observed cases: {machine['cases']} "
        f"({machine['utterances']} utterances, no operator asked)",
    ]
    for name, counts in coverage["by_category"].items():
        lines.append(
            f"  {name:<18} recorded {counts['recorded']}/{counts['planned']}"
            f" · blocked {counts['blocked']}")
    lines.append("categories with no evidence at all: " + (", ".join(
        coverage["categories_without_evidence"]) or "none"))
    lines.append("receipt states: " + (", ".join(
        f"{key} {value}"
        for key, value in artifact["runtime_receipts"]["states"].items())
        or "none"))
    lines.append("operator verdicts: " + (", ".join(
        f"{key} {value}"
        for key, value in operator["text_verdicts"].items()) or "none"))
    if machine["cases"]:
        lines.append("machine-observed states: " + (", ".join(
            f"{key} {value}"
            for key, value in machine["insertion_states"].items()) or "none"))
        lines.append("machine-observed readback shapes: " + (", ".join(
            f"{key} {value}"
            for key, value in machine["readback_shapes"].items()) or "none"))
    agreement = artifact["agreement"]
    lines.append(
        f"operator and runtime both clean: {agreement['both']} · "
        f"disagreements: {agreement['disagreements']} · "
        f"not comparable (no operator verdict): "
        f"{agreement['not_comparable']}")
    if artifact["blocked"]["reasons"]:
        lines.append("blocked: " + ", ".join(
            f"{key} {value}"
            for key, value in artifact["blocked"]["reasons"].items()))
    compatibility = artifact["compatibility"]
    lines.append(
        f"compatibility outcomes: {len(compatibility['outcomes'])} · "
        f"full observations: {len(compatibility['observations'])}"
        + ("" if compatibility["capability_buckets_available"]
           else f" ({compatibility['capability_blocked_reason']})"))
    return "\n".join(lines)


def render_plan(apps: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "PHYSICAL APP INSERTION MATRIX — SESSION PLAN",
        f"apps: {len(apps)} · plan digest: {plan_digest(apps)}",
        "estimated operator time: about 3 minutes per app "
        f"(~{round(len(apps) * 3 / 60, 1)} h), resumable at any point",
        "",
        f"{'app':<28} {'category':<18} phrase",
    ]
    for app in apps:
        lines.append(
            f"{str(app['name'])[:27]:<28} {str(app['category']):<18} "
            f"{app['phrase']}")
    lines.append("")
    lines.append("category totals: " + ", ".join(
        f"{name} {count}" for name, count in category_plan(apps).items()))
    lines.append(
        "phrases (never written to an artifact): " + "; ".join(
            f"{key}={value!r}" for key, value in sorted(PHRASES.items())))
    return "\n".join(lines)


# --------------------------------- cli ------------------------------------


def _load_session_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CaptureError(f"cannot read session {path}: {error}") from error
    if not isinstance(payload, Mapping) or payload.get("tool") != TOOL:
        raise CaptureError(f"{path} is not a {TOOL} session")
    return dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture_app_matrix.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apps", type=Path, default=None,
                        help="JSON file with an 'apps' list (default: built-in 50)")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION,
                        help=f"private session file (default: {DEFAULT_SESSION})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="print the session plan and exit")
    export = commands.add_parser(
        "export-apps", help="write the built-in app list for editing")
    export.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run", help="run or resume the guided session")
    run.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    observe = commands.add_parser(
        "observe",
        help="harvest evidence from ordinary use, asking the operator nothing")
    observe.add_argument("--transcripts", type=Path,
                         default=DEFAULT_TRANSCRIPTS)
    observe.add_argument("--out", type=Path, default=None,
                         help="also write the matrix artifact here")
    emit = commands.add_parser(
        "emit", help="build the matrix artifact from a recorded session")
    emit.add_argument("--out", type=Path, required=True)
    commands.add_parser(
        "summary", help="print the human-readable summary of a session")
    return parser


def main(argv: Sequence[str] | None = None, *,
         reader: TextIO | None = None, writer: TextIO | None = None) -> int:
    reader = reader or sys.stdin
    writer = writer or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        apps = load_apps(args.apps)
        if args.command == "plan":
            writer.write(render_plan(apps) + "\n")
            return 0
        if args.command == "export-apps":
            atomic_write_json(args.out, {"apps": list(apps)})
            writer.write(f"wrote {len(apps)} apps to {args.out}\n")
            return 0
        if args.command == "run":
            session = Session.load(
                args.session, TOOL, plan_digest=plan_digest(apps),
                blocked_reasons=BLOCKED_REASONS)
            return run_session(apps, session, transcripts=args.transcripts,
                               reader=reader, writer=writer)
        if args.command == "observe":
            session = Session.load(
                args.session, TOOL, plan_digest=plan_digest(apps),
                blocked_reasons=BLOCKED_REASONS)
            if not Path(args.transcripts).exists():
                writer.write(
                    f"\n{args.transcripts} does not exist yet. Use Whisper "
                    "Face normally, then run this again.\n")
                return 2
            outcome = observe_transcript(
                apps, session, transcripts=args.transcripts)
            artifact = build_artifact(apps, session.payload())
            writer.write(render_observation(outcome) + "\n\n")
            writer.write(render_summary(artifact) + "\n")
            if args.out is not None:
                atomic_write_json(args.out, artifact)
                writer.write(f"\nwrote {args.out}\n")
            writer.write(f"session saved to {session.path}\n")
            return 0
        payload = _load_session_payload(args.session)
        artifact = build_artifact(apps, payload)
        if args.command == "emit":
            atomic_write_json(args.out, artifact)
            writer.write(render_summary(artifact) + "\n")
            writer.write(f"\nwrote {args.out}\n")
            return 0
        writer.write(render_summary(artifact) + "\n")
        return 0
    except CaptureError as error:
        writer.write(f"capture_app_matrix: {error}\n")
        return 2
    except SessionAborted as stop:
        writer.write(f"capture_app_matrix: {stop}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
