"""Pure decision logic for Whispering Parrot's Mac voice pipeline.

This module deliberately has no macOS, audio-device, MLX, or network imports.
Its small interface concentrates context ranking, deterministic cleanup,
correction isolation, phonetic learning, recognition confidence, and hotkey
mode selection so those behaviours can be tested without launching the app.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable


STOP_WORDS = {
    "about", "after", "again", "also", "and", "app", "are", "before",
    "being", "but", "can", "could", "document", "for", "from", "have",
    "here", "into", "just", "more", "not", "that", "the", "their",
    "then", "there", "these", "they", "this", "through", "was", "were",
    "what", "when", "where", "which", "while", "will", "with", "would",
    "you", "your",
}
TOKEN_RE = re.compile(r"(?<![\w])([A-Za-z][A-Za-z0-9_+.-]{2,})(?![\w])")
SIMPLE_FILLER_RE = re.compile(
    r"(?:,\s*)?\b(?:um+|uh+|erm|hmm)\b(?:\s*,)?", re.I)
DISCOURSE_FILLER_RE = re.compile(
    r"(?:,\s*)?\b(?:you know|I mean|basically|kind of|sort of)\b(?:\s*,)?",
    re.I,
)
STRUCTURE_RE = re.compile(r"\bnew (line|paragraph)\b", re.I)
CORRECTION_RE = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_'’-]{0,30})\s+"
    r"(?:—\s*)?actually\s+"
    r"([A-Za-z0-9][A-Za-z0-9_'’-]{0,30})\b",
    re.I,
)


@dataclass(frozen=True)
class CleanupEdit:
    kind: str
    before: str
    after: str


@dataclass
class CleanupPlan:
    text: str
    edits: list[CleanupEdit] = field(default_factory=list)
    needs_semantic_cleanup: bool = False

    @property
    def edit_kinds(self) -> list[str]:
        return list(dict.fromkeys(edit.kind for edit in self.edits))


@dataclass
class Recognition:
    text: str
    confidence: float = 1.0
    alternative: str | None = None
    verified: bool = False


def _interesting_token(token: str) -> bool:
    lower = token.casefold().strip("._-+")
    if len(lower) < 3 or lower in STOP_WORDS or lower.isdigit():
        return False
    return (
        token[:1].isupper()
        or "_" in token
        or any(char.isupper() for char in token[1:])
        or any(char.isdigit() for char in token)
        or token.casefold() not in STOP_WORDS and len(token) >= 7
    )


def rank_context_terms(
        sources: Iterable[tuple[str | None, float]], limit: int = 24) -> list[str]:
    """Rank ephemeral recognition terms from weighted nearby context."""
    scores: dict[str, float] = {}
    display: dict[str, str] = {}
    for text, weight in sources:
        if not text:
            continue
        for token in TOKEN_RE.findall(text[-6000:]):
            token = token.strip("._-+")
            if not _interesting_token(token):
                continue
            key = token.casefold()
            casing_bonus = 1.5 if token[:1].isupper() else 1.0
            identifier_bonus = 1.4 if "_" in token or any(
                char.isupper() for char in token[1:]) else 1.0
            scores[key] = scores.get(key, 0.0) + weight * casing_bonus \
                * identifier_bonus
            display.setdefault(key, token)
    ranked = sorted(scores, key=lambda key: (-scores[key], key))
    return [display[key] for key in ranked[:limit]]


def recognition_prompt(global_terms: Iterable[str], context_terms: Iterable[str],
                       max_terms: int = 60, max_chars: int = 700) -> str | None:
    """Merge stable and ephemeral vocabulary into one closed Whisper prompt."""
    stable = [str(term).strip() for term in global_terms if str(term).strip()]
    stable_casing = {term.casefold(): term for term in stable}
    ephemeral = [stable_casing.get(str(term).strip().casefold(), str(term).strip())
                 for term in context_terms if str(term).strip()]
    out: list[str] = []
    seen: set[str] = set()
    chars = 0
    # Current visible context outranks the long-term glossary. Canonical casing
    # still comes from the glossary when both contain the same term.
    for term in [*ephemeral, *stable]:
        clean = str(term).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        if len(out) >= max_terms or chars + len(clean) > max_chars:
            break
        out.append(clean)
        seen.add(key)
        chars += len(clean) + 2
    return f"Common terms: {', '.join(out)}." if out else None


def phonetic_key(value: str) -> str:
    """Small dependency-free phonetic key for learned ASR confusions."""
    word = re.sub(r"[^a-z]", "", value.casefold())
    if not word:
        return ""
    replacements = (
        (r"^kn", "n"), (r"^wr", "r"), (r"ph", "f"), (r"ght", "t"),
        (r"qu", "k"), (r"ck", "k"), (r"[cq]", "k"), (r"x", "ks"),
        (r"[aeiouy]", ""), (r"(.)\1+", r"\1"),
    )
    first = word[0]
    reduced = word
    for pattern, replacement in replacements:
        reduced = re.sub(pattern, replacement, reduced)
    return (first + reduced)[:12]


def correction_similarity(old: str, new: str) -> float:
    spelling = difflib.SequenceMatcher(
        None, old.casefold(), new.casefold()).ratio()
    phonetic = difflib.SequenceMatcher(
        None, phonetic_key(old), phonetic_key(new)).ratio()
    return max(spelling, phonetic * 0.9)


def compile_cleanup(raw: str) -> CleanupPlan:
    """Compile safe spoken transformations into explicit, reversible edits."""
    text = raw.strip()
    edits: list[CleanupEdit] = []

    for regex, kind in (
        (SIMPLE_FILLER_RE, "remove_filler"),
        (DISCOURSE_FILLER_RE, "remove_discourse_filler"),
    ):
        matches = [match.group(0) for match in regex.finditer(text)]
        if matches:
            before = text
            text = regex.sub(" ", text)
            edits.append(CleanupEdit(kind, before, text))

    if STRUCTURE_RE.search(text):
        before = text
        text = STRUCTURE_RE.sub(
            lambda match: "\n" if match.group(1).lower() == "line" else "\n\n",
            text,
        )
        edits.append(CleanupEdit("spoken_structure", before, text))

    while True:
        match = CORRECTION_RE.search(text)
        if not match:
            break
        before = text
        text = text[:match.start()] + match.group(2) + text[match.end():]
        edits.append(CleanupEdit("self_correction", before, text))

    lowered = text.casefold()
    scratch_at = lowered.rfind("scratch that")
    if scratch_at >= 0:
        before = text
        left, right = text[:scratch_at].rstrip(), text[scratch_at + 12:].lstrip()
        sentence = max(left.rfind("."), left.rfind("!"), left.rfind("?"),
                       left.rfind("\n"))
        clause = max(left.rfind(","), left.rfind(";"), left.rfind("—"))
        cut = max(sentence + 1, clause + 1)
        text = (left[:cut].rstrip() + " " + right).strip()
        edits.append(CleanupEdit("scratch_that", before, text))

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text).strip()
    needs_semantic = bool(re.search(
        r"\b(?:first|second|third|lastly)\b|"
        r"\b(?:two|three|four|five) (?:things|points|items|ideas)\b",
        text,
        re.I,
    ))
    return CleanupPlan(text=text, edits=edits,
                       needs_semantic_cleanup=needs_semantic)


def infer_revised_insertion(before: str, selection: tuple[int, int],
                            pasted: str, current: str) -> str | None:
    """Isolate edits to the exact range replaced by a paste.

    Returns the revised inserted text, or None when surrounding content also
    changed and the observation is therefore unsafe to learn from.
    """
    start, length = selection
    if start < 0 or length < 0 or start + length > len(before):
        return None
    prefix = before[:start]
    suffix = before[start + length:]
    expected = prefix + pasted + suffix
    if current == expected:
        return None
    if not current.startswith(prefix):
        return None
    if suffix:
        suffix_at = current.rfind(suffix)
        if suffix_at < len(prefix):
            return None
        revised = current[len(prefix):suffix_at]
        if current[suffix_at:] != suffix:
            return None
        return revised if revised != pasted else None

    matcher = difflib.SequenceMatcher(None, expected, current, autojunk=False)
    expected_end = len(prefix) + len(pasted)
    current_end = None
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if i1 == expected_end and tag == "insert":
            current_end = j1
            break
        if i1 <= expected_end <= i2:
            if tag == "equal":
                current_end = j1 + (expected_end - i1)
            else:
                current_end = j2
            break
    if current_end is None or current_end < len(prefix):
        return None
    revised = current[len(prefix):current_end]
    return revised if revised != pasted else None


def confidence_from_segments(segments: Iterable[dict]) -> float:
    """Convert Whisper log probabilities into a stable 0..1 confidence."""
    weighted = []
    for segment in segments or []:
        logprob = segment.get("avg_logprob")
        if isinstance(logprob, (int, float)):
            weight = max(1, len(str(segment.get("text", "")).split()))
            weighted.extend([float(logprob)] * weight)
    if not weighted:
        return 0.5
    mean_logprob = sum(weighted) / len(weighted)
    return max(0.0, min(1.0, math.exp(mean_logprob)))


def mode_from_modifiers(shift: bool, command: bool, control: bool,
                        code_app: bool = False) -> str:
    """Map explicit modifier contracts to one discoverable voice mode."""
    if command and control:
        return "command"
    if command:
        return "edit"
    if control:
        return "reply"
    if shift:
        return "compose"
    if code_app:
        return "code"
    return "capture"


def should_start_speculation(voiced: bool, segment_samples: int,
                             silent_samples: int, sample_rate: int,
                             has_future: bool, minimum_seconds: float = 0.8,
                             silence_seconds: float = 0.25) -> bool:
    """Decide whether a pause is strong enough to pre-decode before release."""
    return bool(
        voiced
        and not has_future
        and segment_samples >= minimum_seconds * sample_rate
        and silent_samples >= silence_seconds * sample_rate
    )


def can_reuse_speculation(has_future: bool, invalid: bool,
                          speculative_start: int, current_cut: int) -> bool:
    """A speculative result is valid only for the unchanged current segment."""
    return bool(
        has_future and not invalid and speculative_start == current_cut)
