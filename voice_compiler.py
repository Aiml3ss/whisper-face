"""Evidence-driven speech compilation for Whisper Face.

The public interface is intentionally small: build ``VoiceIR`` and call
``VoiceCompiler.compile``. Recognition fusion, contextual/personal ranking,
prosody formatting, stable-prefix calculation, protected anchors, and proof
edit verification stay local to this deep module.
"""

from __future__ import annotations

import difflib
import math
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from parrot_core import (
    HELPER_WORD_SOURCE,
    RecognitionWord,
    VocabularyCandidate,
    find_self_correction,
)
from personal_regression import is_common_word, is_negation
from phonetic_keys import double_metaphone
from process_verifier import RefusalReason, VerificationReceipt


TOKEN_RE = re.compile(
    r"https?://[^\s]+|[\w]+(?:['._+/-][\w]+)*|[^\w\s]",
    re.UNICODE,
)
ANCHOR_RE = re.compile(
    r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+|"
    r"(?<!\w)C(?:\+\+|#)(?!\w)|[$#@][A-Za-z0-9_]+|"
    r"\b\d+(?:\.\d+)?%|"
    r"(?:^|\s)(?:(?:[/~]|\.\.?/)[\w.@%+,:=-]+)+|"
    r"(?<!\w)--?[a-z][\w-]*|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b|"
    r"\b(?:\d[\w:./-]*|[A-Z]{2,}[\w-]*|"
    r"[A-Z][a-z]{1,}|"
    r"[A-Za-z]+\d[A-Za-z0-9_.-]*|"
    r"[A-Za-z]+[A-Z][A-Za-z0-9_-]*|[A-Za-z_]+_[A-Za-z0-9_]+)\b"
)
QUESTION_WORDS = {
    "who", "what", "when", "where", "why", "how", "is", "are", "am",
    "can", "could", "would", "should", "will", "did", "do", "does",
    "have", "has", "was", "were",
}
FILLER_WORDS = {
    "um", "uh", "erm", "hmm", "like", "basically", "literally",
    "actually", "just", "so", "well", "you", "know", "i", "mean",
}
PROVABLE_FILLER_WORDS = {"um", "uh", "erm", "hmm"}
COMMAND_WORDS = {
    "brew", "cargo", "docker", "git", "kubectl", "npm", "ollama",
    "pnpm", "pytest", "python", "uv",
}
CORRECTION_BLOCKLIST = {
    "am", "are", "can", "could", "did", "do", "does", "had", "has",
    "have", "he", "i", "is", "it", "she", "should", "that", "they",
    "this", "we", "were", "will", "would", "you",
}
CONTEXT_REWRITE_MAX_CONFIDENCE = 0.70
# Where the user is says nothing about what they said. These sources bias the
# recognizer prompt and are protected anchors; they are never candidates to
# replace a recognized word.
ANCHOR_ONLY_CONTEXT_SOURCES = frozenset({"app", "window", "dictionary"})
ENUMERATION_COUNTS = {
    "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "2", "3", "4", "5", "6", "7", "8", "9", "10",
}
ENUMERATION_NOUNS = {"ideas", "items", "points", "things"}
LIST_INTENT_NOUNS = ENUMERATION_NOUNS
ENUMERATION_ORDINALS = {
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth",
}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_IDENTIFIER_MARK_RE = re.compile(r"[0-9_./+-]")


def _metaphone_codes(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        code for code in double_metaphone(value) if code))


def _best_code_ratio(lefts: tuple[str, ...],
                     rights: tuple[str, ...]) -> float:
    return max((difflib.SequenceMatcher(None, left, right).ratio()
                for left in lefts for right in rights), default=0.0)


def _metaphone_ratio(left: str, right: str) -> float:
    best = _best_code_ratio(_metaphone_codes(left), _metaphone_codes(right))
    left_marked = bool(_IDENTIFIER_MARK_RE.search(left))
    right_marked = bool(_IDENTIFIER_MARK_RE.search(right))
    if left_marked != right_marked:
        # A speaker drops an identifier's version tail ("Gwen" for the
        # visible Qwen3_5), so a plain word also competes against the
        # marked side's letters alone.  Two marked identifiers keep
        # their marks: Qwen2_5 must never sound identical to Qwen3_5.
        marked, plain = (left, right) if left_marked else (right, left)
        stem = _IDENTIFIER_MARK_RE.sub("", marked)
        if stem:
            best = max(best, _best_code_ratio(
                _metaphone_codes(stem), _metaphone_codes(plain)))
    return best


def phonetic_similarity(left: str, right: str) -> float:
    """Acoustic confusability of two tokens on a 0..1 scale.

    The blend keeps the historical contract behind the span-graph
    context gate (thresholds 0.84/0.88): identical spelling can reach
    1.0 while a pure phonetic match tops out at 0.92, so sounding alike
    never outranks reading alike.  Double Metaphone replaces the old
    ad-hoc key without lowering any pair that legitimately passed the
    gate before -- the spelling term is untouched and the golden pairs
    (Gwen/Qwen, Gwen/Qwen3_5) keep their exact-code 0.92 -- while pairs
    the crude key confused only by shared consonant skeletons now fall
    away.
    """
    spelling = difflib.SequenceMatcher(
        None, left.casefold(), right.casefold()).ratio()
    return max(spelling, _metaphone_ratio(left, right) * 0.92)


def _safe_context_replacement(original: str,
                              candidate: "ContextCandidate") -> bool:
    """Whether context may compete with acoustic evidence for one token.

    Context still biases the recognizer through its initial prompt.  This
    stricter gate covers the riskier post-ASR path, where visible UI strings
    must never manufacture numbers, timings, or ordinary prose.
    """
    if candidate.source in ANCHOR_ONLY_CONTEXT_SOURCES:
        return False
    text = candidate.text.strip()
    # Screen text can repair a name the recognizer misspelled ("Gwen" for a
    # visible Qwen), never an ordinary word: "good", "not" and "who" sound
    # enough like Code, Nate and AI to pass the phonetic gate.
    if is_common_word(original) or is_common_word(text):
        return False
    # Progress bars and UI timings such as ``0.00s`` or ``2042.76it/s`` are
    # useful screen context but are not acoustic alternatives.  Underscored
    # or compact alphanumeric identifiers (Qwen3_5, D55) remain eligible.
    if any(char.isdigit() for char in text) \
            and any(char in text for char in ".:/%"):
        return False
    internal_upper = any(char.isupper() for char in text[1:])
    identifier_shaped = (
        "_" in text
        or internal_upper
        or (text.isupper() and len(text) >= 2)
        or (any(char.isdigit() for char in text)
            and any(char.isalpha() for char in text))
    )
    explicit_name_source = candidate.source in {
        "active-context", "selection", "document", "repository",
    } and text[:1].isupper()
    return identifier_shaped or explicit_name_source


@dataclass(frozen=True)
class WordEvidence:
    text: str
    start: float = 0.0
    end: float = 0.0
    confidence: float = 0.5
    engine: str = ""
    timing: str = "native"


@dataclass(frozen=True)
class RecognitionHypothesis:
    text: str
    confidence: float = 0.5
    engine: str = ""
    words: tuple[WordEvidence, ...] = ()


@dataclass(frozen=True)
class ContextCandidate:
    text: str
    weight: float = 1.0
    source: str = "context"


@dataclass(frozen=True)
class ContextPack:
    candidates: tuple[ContextCandidate, ...] = ()
    style: str | None = None
    constraints: tuple[str, ...] = ()

    def merged(self, other: "ContextPack") -> "ContextPack":
        best: dict[str, ContextCandidate] = {}
        for candidate in (*self.candidates, *other.candidates):
            key = candidate.text.casefold()
            if key not in best or candidate.weight > best[key].weight:
                best[key] = candidate
        return ContextPack(
            candidates=tuple(sorted(
                best.values(), key=lambda item: (-item.weight, item.text))),
            style=other.style or self.style,
            constraints=tuple(dict.fromkeys(
                (*self.constraints, *other.constraints))),
        )


@dataclass(frozen=True)
class ContextObservation:
    app: str = ""
    bundle: str = ""
    selected_text: str = ""
    field_text: str = ""
    window_title: str = ""
    document: str = ""
    clipboard: str = ""
    sibling_names: tuple[str, ...] = ()


class ContextAdapter(Protocol):
    def collect(self, observation: ContextObservation) -> ContextPack: ...


def _candidate_tokens(text: str, weight: float,
                      source: str) -> list[ContextCandidate]:
    found: dict[str, ContextCandidate] = {}
    for token in TOKEN_RE.findall(text[-6000:]):
        clean = token.strip("._-+/()[]{}\"'")
        if len(clean) < 2 or not any(char.isalpha() for char in clean):
            continue
        interesting = (
            clean[:1].isupper()
            or "_" in clean
            or any(char.isupper() for char in clean[1:])
            or any(char.isdigit() for char in clean)
            or len(clean) >= 8
        )
        if not interesting:
            continue
        candidate = ContextCandidate(clean, weight, source)
        key = clean.casefold()
        if key not in found or weight > found[key].weight:
            found[key] = candidate
    return list(found.values())


class GenericContextAdapter:
    """Extract candidates from the focused application's visible context."""

    def collect(self, observation: ContextObservation) -> ContextPack:
        candidates: list[ContextCandidate] = []
        for text, weight, source in (
            (observation.selected_text, 6.0, "selection"),
            (observation.window_title, 4.0, "window"),
            (observation.field_text, 2.5, "field"),
            (observation.app, 3.0, "app"),
            (observation.clipboard, 1.0, "clipboard"),
        ):
            candidates.extend(_candidate_tokens(text, weight, source))
        return ContextPack(tuple(candidates))


class DeveloperContextAdapter:
    """Extract code-shaped and repository candidates without reading secrets."""

    DEV_BUNDLES = (
        "codex", "cursor", "code", "xcode", "terminal", "iterm",
        "warp", "zed", "jetbrains", "pycharm", "webstorm",
    )

    def collect(self, observation: ContextObservation) -> ContextPack:
        is_developer = any(
            marker in observation.bundle.casefold()
            for marker in self.DEV_BUNDLES
        ) or bool(observation.sibling_names)
        if not is_developer:
            return ContextPack()
        candidates: list[ContextCandidate] = []
        candidates.extend(_candidate_tokens(
            observation.document, 4.5, "document"))
        candidates.extend(_candidate_tokens(
            " ".join(observation.sibling_names), 4.0, "repository"))
        return ContextPack(
            tuple(candidates), style="technical",
            constraints=("preserve_identifiers", "preserve_paths"),
        )


class ContextRouter:
    """Collect a Context Pack through every applicable Context Adapter."""

    def __init__(self, adapters: Sequence[ContextAdapter] | None = None):
        self.adapters = tuple(adapters or (
            GenericContextAdapter(), DeveloperContextAdapter()))

    def collect(self, observation: ContextObservation) -> ContextPack:
        pack = ContextPack()
        for adapter in self.adapters:
            pack = pack.merged(adapter.collect(observation))
        return pack


@dataclass(frozen=True)
class PersonalPrior:
    heard: str
    preferred: str
    count: int = 1
    apps: tuple[tuple[str, int], ...] = ()

    def app_count(self, bundle: str) -> int:
        target = bundle.casefold()
        return max((count for app, count in self.apps
                    if app.casefold() == target), default=0)


@dataclass(frozen=True)
class ProsodyEvent:
    kind: str
    at: float
    duration: float = 0.0
    strength: float = 1.0


@dataclass(frozen=True)
class VoiceIR:
    hypotheses: tuple[RecognitionHypothesis, ...]
    context: ContextPack = field(default_factory=ContextPack)
    personal_priors: tuple[PersonalPrior, ...] = ()
    prosody: tuple[ProsodyEvent, ...] = ()
    app_bundle: str = ""
    mode: str = "capture"
    finalized: bool = True
    # Whether a word's own confidence may clear a consequential span's
    # uncertainty. False for recognizer confidence nobody has calibrated:
    # its timings still place the span, and the span stays uncertain.
    trust_word_confidence: bool = True
    # Whether pauses between timed words may add commas and paragraphs. A
    # rising question contour is unaffected.
    pause_punctuation: bool = True

    def __post_init__(self):
        if not self.hypotheses:
            raise ValueError("VoiceIR requires at least one hypothesis")


@dataclass(frozen=True)
class Decision:
    source: str
    before: str
    after: str
    score_delta: float
    reason: str


@dataclass(frozen=True)
class EditProposal:
    kind: str
    before: str
    after: str


@dataclass(frozen=True)
class ProofEdit:
    kind: str
    before: str
    after: str
    start: int = -1
    end: int = -1
    accepted: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ProofResult:
    text: str
    edits: tuple[ProofEdit, ...]


@dataclass(frozen=True)
class CompileResult:
    text: str
    stable_prefix: str
    confidence: float
    anchors: tuple[str, ...]
    decisions: tuple[Decision, ...] = ()


@dataclass(frozen=True)
class ConsequenceRisk:
    """Transcript-free description of one consequential source span."""

    category: str
    severity: str
    char_start: int
    char_end: int
    word_start: int
    word_end: int
    confidence: float
    uncertainty: tuple[str, ...] = ()
    audio_start: float | None = None
    audio_end: float | None = None


@dataclass(frozen=True)
class RelistenRequest:
    """A bounded timed span; expected text remains outside this receipt."""

    risk_indexes: tuple[int, ...]
    start: float
    end: float

    @property
    def risk_index(self) -> int:
        """Compatibility accessor for the first covered risk."""
        return self.risk_indexes[0]


@dataclass(frozen=True)
class MicrospanVerification:
    """Transcript-free result produced by an independent local verifier."""

    outcome: str
    confidence: float
    engine: str


class MicrospanVerifier(Protocol):
    """Killable process boundary for one ephemeral audio microspan."""

    process_isolated: bool
    strict_deadline: bool
    retains_audio: bool

    def verify(
        self,
        samples: Sequence[float],
        sample_rate: int,
        expected: str,
        *,
        deadline_at: float,
    ) -> VerificationReceipt: ...


@dataclass(frozen=True)
class ConsequencePlan:
    risks: tuple[ConsequenceRisk, ...]
    relisten_requests: tuple[RelistenRequest, ...]
    relisten_skipped: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ConsequenceReceipt:
    """Closed, transcript-free decision evidence for Last Recognition."""

    route: str
    risk_counts: tuple[tuple[str, int], ...]
    total_risks: int
    high_risks: int
    uncertain_risks: int
    relisten_status: str
    relisten_selected: int
    relisten_attempted: int
    relisten_confirmed: int
    relisten_contradicted: int
    relisten_inconclusive: int
    relisten_skipped: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ContextFirewallReceipt:
    """Transcript-free shadow comparison for contextual influence.

    The receipt is deliberately aggregate-only. It can explain whether
    context or a Personal Prior would affect protected evidence without
    exposing the source span, replacement text, application, or context pack.
    """

    mode: str
    disposition: str
    counterfactual_changed: bool
    risky_spans: int
    influence_count: int
    context_influences: int
    personal_prior_influences: int
    protected_influences: int
    promotion_candidates: int
    quarantined: int
    reason_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int


def _tokens(text: str) -> list[_Token]:
    return [_Token(match.group(0), match.start(), match.end())
            for match in TOKEN_RE.finditer(text)]


def _proof_words(text: str, preserve_formatting: bool = False) -> list[str]:
    """Proof stream preserving operators, sigils, and internal punctuation."""
    proof: list[str] = []
    for token in _tokens(text):
        value = token.text if preserve_formatting else token.text.casefold()
        if any(char.isalnum() for char in value):
            proof.append(value)
            continue
        if preserve_formatting:
            proof.append(value)
            continue
        before = text[token.start - 1] if token.start else ""
        after = text[token.end] if token.end < len(text) else ""
        # Only clear written-format punctuation is ignorable. Operators,
        # sigils, percentages, and code-language suffixes remain evidence.
        if value in {".", "?", "!", ",", ";", ":"} \
                and (not after or after.isspace()):
            continue
        if value == "-" and (not before or before == "\n") \
                and after.isspace():
            continue
        proof.append(value)
    return proof


def _enumeration_payload(words: Sequence[str]) -> list[str] | None:
    """Remove only grammar proved by an explicit counted enumeration."""
    if (len(words) < 4 or words[0] not in ENUMERATION_COUNTS
            or words[1] not in ENUMERATION_NOUNS):
        return None
    output = list(words[:2])
    markers = 0
    index = 2
    while index < len(words):
        marker_start = index
        if words[index] in {"and", "the"}:
            index += 1
        if index < len(words) and words[index] in ENUMERATION_ORDINALS:
            index += 1
            if index < len(words) and words[index] in {
                    "item", "point", "thing"}:
                index += 1
            if index < len(words) and words[index] == "is":
                index += 1
            markers += 1
            continue
        index = marker_start
        output.append(words[index])
        index += 1
    return output if markers >= 2 else None


def _explicit_list_variants(words: Sequence[str]) -> tuple[list[str], ...]:
    """Bound header compression while preserving every spoken item token."""
    words = list(words)
    variants: list[list[str]] = []

    def add(noun_index: int, payload_index: int,
            extra_headers: Sequence[Sequence[str]] = ()):
        if payload_index >= len(words):
            return
        noun = words[noun_index]
        header = (["feedback", noun]
                  if noun_index and words[noun_index - 1] == "feedback"
                  else [noun])
        for candidate in (header, *extra_headers):
            variants.append([*candidate, *words[payload_index:]])

    # "Here's a list of ideas that I have ..." may become the shorter
    # written header "Here's a list of ideas:". Only that fixed header grammar
    # can disappear; the item payload remains byte-for-token identical.
    if len(words) >= 6 and words[0] in {"heres", "here's", "here’s"} \
            and words[1:4] == ["a", "list", "of"] \
            and words[4] in LIST_INTENT_NOUNS:
        payload = 5
        if words[payload:payload + 3] == ["that", "i", "have"]:
            payload += 3
        add(4, payload, (words[:5], words[1:5]))
    elif len(words) >= 7 and words[:5] == ["here", "is", "a", "list", "of"] \
            and words[5] in LIST_INTENT_NOUNS:
        payload = 6
        if words[payload:payload + 3] == ["that", "i", "have"]:
            payload += 3
        add(5, payload, (words[:6], words[2:6]))

    def noun_between(start: int, stop: int) -> int | None:
        for index in range(start, min(stop, len(words))):
            if words[index] in LIST_INTENT_NOUNS:
                return index
        return None

    if words[:2] in (["here", "are"], ["here", "is"]):
        noun_index = noun_between(2, 8)
        if noun_index is not None and all(word in {
                "a", "some", "the", "my", "few", "several", "feedback",
        } for word in words[2:noun_index]):
            add(noun_index, noun_index + 1)

    have_end = 0
    if words[:2] == ["i", "have"]:
        have_end = 2
    elif words[:3] == ["i", "have", "got"]:
        have_end = 3
    if have_end:
        noun_index = noun_between(have_end, have_end + 7)
        if noun_index is not None and all(word in {
                "a", "some", "the", "my", "few", "several", "feedback",
        } for word in words[have_end:noun_index]):
            add(noun_index, noun_index + 1,
                (words[have_end:noun_index + 1],))

    list_prefix = None
    for prefix in (
            ["let", "me", "list"], ["let", "me", "list", "out"],
            ["i", "want", "to", "list"],
            ["i", "would", "like", "to", "list"],
            ["id", "like", "to", "list"],
            ["i'd", "like", "to", "list"],
            ["i’d", "like", "to", "list"]):
        if words[:len(prefix)] == prefix:
            list_prefix = len(prefix)
            break
    if list_prefix is not None:
        if list_prefix < len(words) and words[list_prefix] == "out":
            list_prefix += 1
        noun_index = noun_between(list_prefix, list_prefix + 7)
        if noun_index is not None and all(word in {
                "a", "some", "the", "my", "few", "several",
        } for word in words[list_prefix:noun_index]):
            add(noun_index, noun_index + 1)

    noun_index = noun_between(1, 4) if words[:1] == ["my"] else None
    if noun_index is not None and noun_index + 1 < len(words) \
            and all(word == "feedback" for word in words[1:noun_index]) \
            and words[noun_index + 1] == "are":
        add(noun_index, noun_index + 2)

    return tuple(variants)


def _formatted_list_word_variants(text: str) -> tuple[list[str], ...]:
    """Restore an optional spoken "and" at proven dash-item boundaries."""
    lines = text.splitlines()
    first_item = next(
        (index for index, line in enumerate(lines)
         if re.match(r"^\s*-\s+\S", line)),
        None,
    )
    if first_item is None:
        return ()
    body = lines[first_item:]
    items = sum(1 for line in body if re.match(r"^\s*-\s+\S", line))
    if items < 2 or items > 8:
        return ()
    variants = [_proof_words("\n".join(lines[:first_item]))]
    seen_item = False
    for line in body:
        if not re.match(r"^\s*-\s+\S", line):
            # A line that is not an item is still candidate text. Its words
            # stay in the stream, so they must be proved from the source like
            # every other word.
            extra = _proof_words(line)
            variants = [words + extra for words in variants]
            continue
        item_words = _proof_words(re.sub(r"^\s*-\s+", "", line))
        if not item_words:
            return ()
        expanded = [words + item_words for words in variants]
        if seen_item and item_words[0] != "and":
            expanded.extend(words + ["and", *item_words] for words in variants)
        variants = expanded
        seen_item = True
    return tuple(variants)


def protected_anchors(text: str,
                      context: Iterable[ContextCandidate] = ()) -> tuple[str, ...]:
    anchors = [match.group(0).strip() for match in ANCHOR_RE.finditer(text)
               if _norm(match.group(0)) not in {
                   _norm(word) for word in FILLER_WORDS}]
    anchors.extend(token.text for token in _tokens(text)
                   if token.text.casefold() in COMMAND_WORDS)
    lower = text.casefold()
    for candidate in context:
        if candidate.weight >= 3.0 and candidate.text.casefold() in lower:
            anchors.append(candidate.text)
    return tuple(dict.fromkeys(anchor for anchor in anchors if anchor))


_RISK_URL_RE = re.compile(
    r"\b(?:(?:https?://|www\.)[^\s]+|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:ai|app|cloud|com|co|dev|edu|gov|io|me|net|org|tech|uk|us)"
    r"(?=\b|/)(?:/[^\s]*)?)",
    re.I,
)
_RISK_PATH_RE = re.compile(
    r"(?<!\w)(?:(?:/|~/|\.\.?/)[\w.@%+,:=~-]+"
    r"(?:/[\w.@%+,:=~-]+)*|"
    r"[A-Za-z]:\\[^\s]+)"
)
_RISK_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_RISK_HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z][A-Za-z0-9_.-]{1,63}\b")
_RISK_PHONE_RE = re.compile(r"(?<!\w)\+?\d(?:[() .-]*\d){6,}(?!\w)")
_RISK_CURRENCY_RE = re.compile(
    r"(?<!\w)(?:[+-]?\s*[$€£¥]\s*\d[\d,]*(?:\.\d{1,2})?|"
    r"[+-]?\s*\d[\d,]*(?:\.\d{1,2})?\s*"
    r"(?:dollars?|euros?|pounds?|yen))\b",
    re.I,
)
_RISK_DATE_RE = re.compile(
    r"\b(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,?\s+\d{4})?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b",
    re.I,
)
_RISK_TIME_RE = re.compile(
    r"\b(?:(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[ap]\.?m\.?)?|"
    r"(?:1[0-2]|0?[1-9])\s*[ap]\.?m\.?)\b",
    re.I,
)
_RISK_MEASUREMENT_UNIT = (
    r"(?:milliseconds?|seconds?|minutes?|hours?|"
    r"milligrams?|grams?|kilograms?|ounces?|pounds?|"
    r"millilit(?:er|re)s?|lit(?:er|re)s?|"
    r"millimeters?|centimeters?|meters?|kilometers?|inches?|feet|miles?|"
    r"bytes?|kilobytes?|megabytes?|gigabytes?|terabytes?|"
    r"percent(?:age)?|degrees?(?:\s+(?:celsius|fahrenheit))?)"
)
# Keep abbreviations closed and omit ambiguous single-token forms such as
# ``m``, ``s``, ``in``, and ``ft``.  The boundary below also prevents a known
# abbreviation from matching the prefix of an identifier such as ``5mlpack``.
_RISK_MEASUREMENT_ABBREVIATION = (
    r"(?:mcg|[µμ]g|ug|mg|kg|oz|lbs?|"
    r"ml|cl|dl|mm|cm|km|ms|secs?|mins?|hrs?|"
    r"kb|mb|gb|tb|°[cf])"
)
_RISK_NUMBER_RE = re.compile(
    rf"(?<![\w@])(?:[+-]\s*)?\d[\d,]*(?:\.\d+)?%?"
    rf"(?:(?:\s+{_RISK_MEASUREMENT_UNIT})|"
    rf"(?:\s*{_RISK_MEASUREMENT_ABBREVIATION}))?(?!\w)",
    re.I,
)
_SPOKEN_CARDINAL = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|million)"
)
_SPOKEN_ORDINAL = (
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|"
    r"sixteenth|seventeenth|eighteenth|nineteenth|twentieth|"
    r"thirtieth|fortieth|fiftieth|sixtieth|seventieth|eightieth|"
    r"ninetieth|hundredth|thousandth|millionth)"
)
_SPOKEN_CARDINAL_SEQUENCE = (
    rf"{_SPOKEN_CARDINAL}"
    rf"(?:[-\s]+(?:and\s+)?{_SPOKEN_CARDINAL})*"
)
_SPOKEN_DECIMAL_DIGIT = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine)"
)
# Keep compound decimal/fraction claims narrow: they only become one protected
# span when followed by a known measurement unit. This avoids treating prose
# such as "one point to make" as a decimal while keeping the changed unit
# inside the consequential claim.
_SPOKEN_COMPOUND_WITH_UNIT = (
    rf"(?:{_SPOKEN_CARDINAL_SEQUENCE}\s+point"
    rf"(?:\s+{_SPOKEN_DECIMAL_DIGIT})+|"
    rf"{_SPOKEN_CARDINAL_SEQUENCE}\s+and\s+(?:a|one)\s+"
    rf"(?:half|quarter))"
    rf"\s+(?:{_RISK_MEASUREMENT_UNIT}|"
    rf"{_RISK_MEASUREMENT_ABBREVIATION})"
)
_RISK_SPOKEN_NUMBER_RE = re.compile(
    rf"\b(?:(?:minus|negative|plus|positive)[-\s]+)?"
    rf"(?:{_SPOKEN_COMPOUND_WITH_UNIT}|"
    rf"(?:{_SPOKEN_CARDINAL_SEQUENCE}"
    rf"(?:[-\s]+{_SPOKEN_ORDINAL})?|{_SPOKEN_ORDINAL})"
    rf"(?:\s+(?:{_RISK_MEASUREMENT_UNIT}|"
    rf"{_RISK_MEASUREMENT_ABBREVIATION}))?)\b",
    re.I,
)
_RISK_RECIPIENT_RE = re.compile(
    r"(?i:\b(?:send(?:\s+(?:this|it|that))?\s+to|email|message|call|cc|bcc)"
    r"\s+)(?P<target>[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"@[A-Za-z][\w.-]*|[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})\b",
)
_RISK_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z'-]{1,40}(?:\s+[A-Z][A-Za-z'-]{1,40})+\b"
)
_ACTION_WORDS = {
    "approve", "call", "cancel", "cc", "delete", "deploy", "email",
    "execute", "message", "move", "open", "overwrite", "pay", "publish",
    "remove", "reset", "run", "save", "schedule", "send", "share",
    "submit", "transfer", "use",
} | COMMAND_WORDS
_RISK_SEVERITY = {
    "name": "high",
    "number": "high",
    "currency": "high",
    "date": "high",
    "time": "high",
    "recipient": "high",
    "contact": "high",
    "url": "high",
    "path": "high",
    "command": "high",
    "action": "high",
}
_RELISEN_MAX_SPANS = 2
_RELISEN_MAX_SECONDS = 2.4
_RELISEN_PADDING_SECONDS = 0.08


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _lexical_tokens(text: str) -> list[_Token]:
    return [token for token in _tokens(text)
            if any(character.isalnum() for character in token.text)]


def _evidence_token_key(value: str) -> str:
    """Case-fold evidence without erasing consequential punctuation.

    ASR word rows may carry surrounding whitespace, but decimal points,
    slashes, currency signs, email punctuation, and path separators are part
    of the claim being verified and must remain distinct.
    """
    return re.sub(r"\s+", "", value.casefold())


def _aligned_words(primary: RecognitionHypothesis,
                   tokens: Sequence[_Token]) -> tuple[WordEvidence, ...] | None:
    words = tuple(word for word in primary.words
                  if any(character.isalnum() for character in word.text))
    if len(words) != len(tokens):
        return None
    if any(_evidence_token_key(word.text) != _evidence_token_key(token.text)
           for word, token in zip(words, tokens)):
        return None
    return words


# Shortest span a projected piece may have; the harvest and the consequence
# plan both refuse an empty interval.
_PROJECTED_MINIMUM_SECONDS = 0.001


def _divide_interval(start: float, end: float,
                     weights: Sequence[int]) -> list[tuple[float, float]]:
    """Split ``[start, end]`` in proportion to ``weights``; exact ends."""
    total = sum(weights)
    bounds = [start]
    covered = 0
    for weight in weights[:-1]:
        covered += weight
        bounds.append(start + (end - start) * covered / total)
    bounds.append(end)
    return list(zip(bounds[:-1], bounds[1:]))


def project_word_evidence(
        text: str, helper_words: Sequence, duration: float) \
        -> tuple[RecognitionWord, ...] | None:
    """Turn the helper's words into this compiler's lexical tokens.

    The helper groups SentencePiece tokens into words with punctuation
    attached ("world,", "$1,250.50"), and gives every token emitted on one
    80 ms encoder frame the same start, so several words can share a start
    and have no length. The compiler aligns evidence to its own lexical
    tokens and the harvest refuses empty or overlapping spans, so used as
    they come the words would be thrown away without a trace. Here:

    - each end is clamped to the clip;
    - a run of words sharing one start shares out the run's span by
      character count;
    - each word is split into its lexical tokens ("world," gives "world";
      "$1,250.50" gives "1" and "250.50"), sharing out the word's span by
      character count, each keeping the word's confidence;
    - the pieces must be strictly ordered, at least 1 ms long, and spell
      exactly the text's own lexical tokens.

    Times stay labelled "native": decoder emission times at 80 ms
    resolution, interpolated within a frame. Any failure returns ``None``
    and the decode keeps no word evidence.
    """
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration)) or float(duration) <= 0.0
            or not helper_words):
        return None
    clip = float(duration)
    spans: list[tuple[float, float]] = []
    for word in helper_words:
        end = min(float(word.end), clip)
        start = min(float(word.start), end)
        spans.append((start, end))
    # Same-frame runs: one start shared by consecutive words.
    index = 0
    while index < len(spans):
        last = index
        while last + 1 < len(spans) and spans[last + 1][0] == spans[index][0]:
            last += 1
        if last > index:
            run_end = max(end for _start, end in spans[index:last + 1])
            weights = [max(1, len(helper_words[position].text))
                       for position in range(index, last + 1)]
            spans[index:last + 1] = _divide_interval(
                spans[index][0], run_end, weights)
        index = last + 1
    pieces: list[RecognitionWord] = []
    for word, (start, end) in zip(helper_words, spans):
        tokens = _lexical_tokens(word.text)
        if not tokens:
            continue
        for token, (piece_start, piece_end) in zip(tokens, _divide_interval(
                start, end, [len(token.text) for token in tokens])):
            pieces.append(RecognitionWord(
                token.text, piece_start, piece_end, float(word.confidence),
                "native", HELPER_WORD_SOURCE))
    cursor = 0.0
    for piece in pieces:
        if (piece.start < cursor
                or piece.end - piece.start < _PROJECTED_MINIMUM_SECONDS
                or piece.end > clip):
            return None
        cursor = piece.end
    expected = [_evidence_token_key(token.text)
                for token in _lexical_tokens(text)]
    if not expected or [
            _evidence_token_key(piece.text) for piece in pieces] != expected:
        return None
    return tuple(pieces)


def _alternative_disagreements(
        voice: VoiceIR) -> tuple[tuple[int, int], ...]:
    """Return changed primary character ranges using punctuation-safe tokens."""
    primary_tokens = _tokens(voice.hypotheses[0].text)
    primary = [_evidence_token_key(token.text) for token in primary_tokens]
    disagreements: list[tuple[int, int]] = []
    for hypothesis in voice.hypotheses[1:]:
        alternative_tokens = _tokens(hypothesis.text)
        alternative = [_evidence_token_key(token.text)
                       for token in alternative_tokens]
        matcher = difflib.SequenceMatcher(
            None, primary, alternative, autojunk=False)
        for tag, first, last, _other_first, _other_last in matcher.get_opcodes():
            if tag == "equal":
                continue
            if first < last:
                disagreements.append((
                    primary_tokens[first].start,
                    primary_tokens[last - 1].end,
                ))
            else:
                position = primary_tokens[first].start \
                    if first < len(primary_tokens) \
                    else len(voice.hypotheses[0].text)
                disagreements.append((position, position))
    return tuple(disagreements)


def _change_affects_span(
        change: tuple[int, int], span: tuple[int, int]) -> bool:
    if change[0] == change[1]:
        return span[0] <= change[0] <= span[1]
    return _overlaps(change, span)


def _risk_candidates(voice: VoiceIR) -> list[tuple[str, str, int, int]]:
    text = voice.hypotheses[0].text
    candidates: list[tuple[str, str, int, int]] = []

    def add_span(category: str, start: int, end: int,
                 severity: str | None = None) -> None:
        key = (category, start, end)
        if any((existing[0], existing[2], existing[3]) == key
               for existing in candidates):
            return
        if category == "name":
            for index, existing in enumerate(candidates):
                if existing[0] != "name" or not _overlaps(
                        (start, end), (existing[2], existing[3])):
                    continue
                if end - start < existing[3] - existing[2]:
                    candidates[index] = (
                        category, severity or _RISK_SEVERITY[category],
                        start, end)
                return
        candidates.append((
            category, severity or _RISK_SEVERITY[category], start, end))

    def add(category: str, match, severity: str | None = None) -> None:
        add_span(category, *match.span(), severity)

    for pattern, category in (
            (_RISK_URL_RE, "url"),
            (_RISK_PATH_RE, "path"),
            (_RISK_EMAIL_RE, "contact"),
            (_RISK_HANDLE_RE, "contact"),
            (_RISK_PHONE_RE, "contact"),
            (_RISK_RECIPIENT_RE, "recipient"),
            (_RISK_CURRENCY_RE, "currency"),
            (_RISK_DATE_RE, "date"),
            (_RISK_TIME_RE, "time")):
        for match in pattern.finditer(text):
            if category == "url" and match.start() > 0 \
                    and text[match.start() - 1] == "@":
                continue
            if category == "path" and any(
                    _overlaps(match.span(), (start, end))
                    for existing, _severity, start, end in candidates
                    if existing == "url"):
                continue
            if category == "recipient":
                add_span(category, *match.span("target"))
            else:
                add(category, match)

    specialized = [(start, end) for category, _severity, start, end in candidates
                   if category in {
                       "contact", "currency", "date", "time", "url", "path"}]
    for pattern in (_RISK_NUMBER_RE, _RISK_SPOKEN_NUMBER_RE):
        for match in pattern.finditer(text):
            if any(_overlaps(match.span(), span) for span in specialized):
                continue
            add("number", match)

    for match in _RISK_NAME_RE.finditer(text):
        name_words = match.group(0).split()
        if name_words[0].casefold() in _ACTION_WORDS:
            if len(name_words) >= 3:
                trailing_start = match.start() + match.group(0).find(
                    name_words[1])
                add_span("name", trailing_start, match.end())
            continue
        add("name", match)
    folded = text.casefold()
    for context in voice.context.candidates:
        value = context.text.strip()
        if (context.weight < 2.5 or len(value) < 2
                or not value[:1].isupper()
                or any(character.isdigit() for character in value)):
            continue
        start = folded.find(value.casefold())
        while start >= 0:
            end = start + len(value)
            if (start == 0 or not text[start - 1].isalnum()) and (
                    end == len(text) or not text[end:end + 1].isalnum()):
                add_span("name", start, end)
            start = folded.find(value.casefold(), end)

    lexical = _lexical_tokens(text)
    for index, token in enumerate(lexical):
        word = token.text.casefold()
        if word not in _ACTION_WORDS:
            continue
        if (index + 1 < len(lexical)
                and lexical[index + 1].text.casefold() in {
                    "is", "was", "seems", "looks", "contains", "says",
                }):
            continue
        prefix = " ".join(item.text.casefold() for item in lexical[:index])
        imperative_prefix = prefix in {
            "", "please", "then", "and then", "please then",
            "can you", "could you", "would you", "please can you",
            "please could you", "please would you",
        }
        if not imperative_prefix and voice.mode not in {"command", "code"}:
            continue
        category = "command" if word in COMMAND_WORDS \
            or voice.mode == "command" else "action"
        add_span(category, token.start, token.end)
        break

    return sorted(candidates, key=lambda item: (item[2], item[3], item[0]))


def build_consequence_plan(
        voice: VoiceIR, *, audio_duration: float | None = None) \
        -> ConsequencePlan:
    """Classify consequential spans and select only precise microspans."""
    primary = voice.hypotheses[0]
    tokens = _lexical_tokens(primary.text)
    words = _aligned_words(primary, tokens)
    disagreements = _alternative_disagreements(voice)
    duration = audio_duration if isinstance(audio_duration, (int, float)) \
        and not isinstance(audio_duration, bool) \
        and math.isfinite(float(audio_duration)) \
        and float(audio_duration) > 0.0 else None
    risks: list[ConsequenceRisk] = []
    for category, severity, char_start, char_end in _risk_candidates(voice):
        indexes = [index for index, token in enumerate(tokens)
                   if _overlaps((token.start, token.end),
                                (char_start, char_end))]
        if not indexes:
            continue
        evidence = tuple(words[index] for index in indexes) \
            if words is not None else ()
        confidence = min(
            [primary.confidence, *(word.confidence for word in evidence)])
        uncertainty: list[str] = []
        if primary.confidence < 0.82:
            uncertainty.append("hypothesis-confidence")
        if not evidence:
            uncertainty.append("word-evidence-unavailable")
        elif not voice.trust_word_confidence:
            uncertainty.append("word-confidence-uncalibrated")
        elif min(word.confidence for word in evidence) < 0.78:
            uncertainty.append("word-confidence")
        if any(_change_affects_span(
                change, (char_start, char_end)) for change in disagreements):
            uncertainty.append("hypothesis-disagreement")
        audio_start = audio_end = None
        if evidence and duration is not None and all(
                word.timing == "native"
                and math.isfinite(word.start) and math.isfinite(word.end)
                and 0.0 <= word.start < word.end <= duration
                for word in evidence):
            audio_start = min(word.start for word in evidence)
            audio_end = max(word.end for word in evidence)
        risks.append(ConsequenceRisk(
            category=category,
            severity=severity,
            char_start=char_start,
            char_end=char_end,
            word_start=indexes[0],
            word_end=indexes[-1] + 1,
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            uncertainty=tuple(uncertainty),
            audio_start=audio_start,
            audio_end=audio_end,
        ))

    candidates: list[RelistenRequest] = []
    skipped: dict[str, int] = {}
    for risk_index, risk in enumerate(risks):
        if risk.severity != "high" or not risk.uncertainty:
            continue
        if risk.audio_start is None or risk.audio_end is None \
                or duration is None:
            skipped["timing-unavailable"] = \
                skipped.get("timing-unavailable", 0) + 1
            continue
        start = max(0.0, risk.audio_start - _RELISEN_PADDING_SECONDS)
        end = min(duration, risk.audio_end + _RELISEN_PADDING_SECONDS)
        span_duration = end - start
        if (span_duration <= 0.0 or span_duration > _RELISEN_MAX_SECONDS
                or span_duration >= duration * 0.75
                or (start <= 0.0 and end >= duration)):
            skipped["span-not-micro"] = skipped.get("span-not-micro", 0) + 1
            continue
        candidates.append(RelistenRequest((risk_index,), start, end))

    # Padding commonly makes an imperative verb and its consequential payload
    # overlap ("pay $500", "open /path"). Verify that single acoustic region
    # once and resolve every covered risk rather than spending the budget on
    # the verb while silently dropping the payload.
    merged: list[RelistenRequest] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, item.end)):
        if merged and candidate.start <= merged[-1].end:
            previous = merged[-1]
            combined_end = max(previous.end, candidate.end)
            combined_indexes = tuple(dict.fromkeys(
                (*previous.risk_indexes, *candidate.risk_indexes)))
            if (combined_end - previous.start <= _RELISEN_MAX_SECONDS
                    and combined_end - previous.start < duration * 0.75
                    and not (previous.start <= 0.0
                             and combined_end >= duration)):
                merged[-1] = RelistenRequest(
                    combined_indexes, previous.start, combined_end)
                continue
        merged.append(candidate)

    def priority(request: RelistenRequest) -> tuple[int, float, float]:
        payload = any(risks[index].category not in {"action", "command"}
                      for index in request.risk_indexes)
        return (0 if payload else 1, request.start, request.end)

    ranked = sorted(merged, key=priority)
    requests = ranked[:_RELISEN_MAX_SPANS]
    for request in ranked[_RELISEN_MAX_SPANS:]:
        skipped["selection-limit"] = skipped.get("selection-limit", 0) \
            + len(request.risk_indexes)
    requests.sort(key=lambda item: (item.start, item.end))
    return ConsequencePlan(
        tuple(risks), tuple(requests), tuple(sorted(skipped.items())))


def execute_consequence_plan(
        voice: VoiceIR,
        plan: ConsequencePlan,
        *,
        audio: Sequence[float] | None = None,
        sample_rate: int = 16_000,
        verifier: MicrospanVerifier | None = None,
        deadline_seconds: float = 0.75,
        clock=time.monotonic,
) -> ConsequenceReceipt:
    """Execute selected re-listens under a closed, transcript-free contract."""
    if (not isinstance(sample_rate, int) or isinstance(sample_rate, bool)
            or sample_rate <= 0):
        raise ValueError("sample_rate must be a positive integer")
    if (isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not 0.0 < float(deadline_seconds) <= 2.0):
        raise ValueError("deadline_seconds must be between 0 and 2 seconds")

    skipped = dict(plan.relisten_skipped)
    selected = len(plan.relisten_requests)
    attempted = confirmed = contradicted = inconclusive = 0
    confirmed_risks: set[int] = set()
    contradicted_risks: set[int] = set()
    if selected and verifier is None:
        skipped["verifier-unavailable"] = selected
    elif selected:
        safe_contract = (
            getattr(verifier, "process_isolated", None) is True
            and getattr(verifier, "strict_deadline", None) is True
            and getattr(verifier, "retains_audio", None) is False
        )
        if not safe_contract:
            skipped["unsafe-verifier-contract"] = selected
        elif audio is None:
            skipped["audio-unavailable"] = selected
        else:
            deadline_at = clock() + float(deadline_seconds)
            try:
                audio_count = len(audio)
            except Exception:
                audio_count = 0
            primary = voice.hypotheses[0]
            for request_index, request in enumerate(
                    plan.relisten_requests):
                remaining = selected - request_index
                if clock() >= deadline_at:
                    skipped["deadline-expired"] = \
                        skipped.get("deadline-expired", 0) + remaining
                    break
                start_index = max(
                    0, int(math.floor(request.start * sample_rate)))
                end_index = min(
                    audio_count, int(math.ceil(request.end * sample_rate)))
                expected_words = [
                    word.text for word in primary.words
                    if word.timing == "native"
                    and word.end > request.start
                    and word.start < request.end
                ]
                if (audio_count <= 0 or end_index <= start_index
                        or not expected_words):
                    skipped["audio-unavailable"] = \
                        skipped.get("audio-unavailable", 0) + 1
                    continue
                try:
                    samples = tuple(
                        float(value)
                        for value in audio[start_index:end_index]
                    )
                except Exception:
                    skipped["audio-unavailable"] = \
                        skipped.get("audio-unavailable", 0) + 1
                    continue
                if not samples:
                    skipped["audio-unavailable"] = \
                        skipped.get("audio-unavailable", 0) + 1
                    continue
                attempted += 1
                try:
                    verification = verifier.verify(
                        samples,
                        sample_rate,
                        " ".join(expected_words),
                        deadline_at=deadline_at,
                    )
                except Exception:
                    skipped["verifier-error"] = \
                        skipped.get("verifier-error", 0) + 1
                    continue
                finally:
                    samples = ()
                if clock() > deadline_at:
                    skipped["deadline-expired"] = \
                        skipped.get("deadline-expired", 0) + 1
                    break
                if not isinstance(verification, VerificationReceipt):
                    skipped["invalid-verifier-result"] = skipped.get(
                        "invalid-verifier-result", 0) + 1
                    continue
                if verification.refusal is not None:
                    reason = (
                        "deadline-expired"
                        if verification.refusal is RefusalReason.TIMEOUT
                        else "invalid-verifier-result"
                        if verification.refusal
                            is RefusalReason.MALFORMED_RESULT
                        else "verifier-error"
                    )
                    skipped[reason] = skipped.get(reason, 0) + 1
                    continue
                result = verification.result
                if result is None:
                    skipped["invalid-verifier-result"] = skipped.get(
                        "invalid-verifier-result", 0) + 1
                    continue
                if result.engine.casefold() == primary.engine.casefold():
                    skipped["verifier-not-independent"] = skipped.get(
                        "verifier-not-independent", 0) + 1
                    continue
                if result.outcome == "confirmed":
                    confirmed += 1
                    confirmed_risks.update(request.risk_indexes)
                elif result.outcome == "contradicted":
                    contradicted += 1
                    contradicted_risks.update(request.risk_indexes)
                elif result.outcome == "inconclusive":
                    inconclusive += 1
                else:
                    skipped["invalid-verifier-result"] = skipped.get(
                        "invalid-verifier-result", 0) + 1

    risk_counts: dict[str, int] = {}
    for risk in plan.risks:
        risk_counts[risk.category] = risk_counts.get(risk.category, 0) + 1
    uncertain = sum(bool(risk.uncertainty) for risk in plan.risks)
    high = sum(risk.severity == "high" for risk in plan.risks)
    uncertain_high_indexes = {
        index for index, risk in enumerate(plan.risks)
        if risk.severity == "high" and bool(risk.uncertainty)
    }
    uncertain_high = len(uncertain_high_indexes)
    unresolved_high = uncertain_high_indexes - confirmed_risks
    if not plan.risks:
        route = "standard"
    elif contradicted_risks or unresolved_high:
        route = "review"
    elif uncertain_high:
        route = "verified"
    else:
        route = "protected"

    if not uncertain_high:
        relisten_status = "not-needed"
    elif contradicted:
        relisten_status = "contradicted"
    elif attempted == 0:
        relisten_status = "skipped"
    elif confirmed == attempted and not skipped:
        relisten_status = "confirmed"
    elif skipped.get("deadline-expired"):
        relisten_status = "timed-out"
    elif inconclusive:
        relisten_status = "inconclusive"
    else:
        relisten_status = "mixed"
    return ConsequenceReceipt(
        route=route,
        risk_counts=tuple(sorted(risk_counts.items())),
        total_risks=len(plan.risks),
        high_risks=high,
        uncertain_risks=uncertain,
        relisten_status=relisten_status,
        relisten_selected=selected,
        relisten_attempted=attempted,
        relisten_confirmed=confirmed,
        relisten_contradicted=contradicted,
        relisten_inconclusive=inconclusive,
        relisten_skipped=tuple(sorted(skipped.items())),
    )


def consequence_receipt(
        voice: VoiceIR, *, audio: Sequence[float] | None = None,
        sample_rate: int = 16_000,
        audio_duration: float | None = None,
        verifier: MicrospanVerifier | None = None,
        deadline_seconds: float = 0.75,
        clock=time.monotonic) -> ConsequenceReceipt:
    """Build and execute one bounded consequence-routing decision."""
    duration = audio_duration
    if duration is None and audio is not None:
        duration = len(audio) / sample_rate
    plan = build_consequence_plan(voice, audio_duration=duration)
    return execute_consequence_plan(
        voice, plan, audio=audio, sample_rate=sample_rate,
        verifier=verifier, deadline_seconds=deadline_seconds, clock=clock)


def _engine_bonus(engine: str) -> float:
    name = engine.casefold()
    if "turbo" in name or "large" in name:
        return 0.10
    if "kyutai" in name or "parakeet" in name:
        return 0.08
    return 0.0


def _replace_spans(text: str,
                   replacements: Sequence[tuple[int, int, str]]) -> str:
    out = text
    for start, end, replacement in sorted(
            replacements, key=lambda item: item[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


class VoiceCompiler:
    """Compile VoiceIR into faithful, explainable text."""

    def compile(self, voice: VoiceIR) -> CompileResult:
        primary = voice.hypotheses[0]
        fused, decisions = self._fuse(primary, voice)
        formatted, prosody_decisions = self._format_prosody(
            fused, primary.words, voice.prosody,
            pauses=voice.pause_punctuation)
        decisions.extend(prosody_decisions)
        stable = formatted if voice.finalized else self._stable_prefix(voice)
        # Keep acoustic confidence calibrated and separate from formatting or
        # context decisions. Decision count is not evidence of recognition.
        confidence = min(1.0, max(0.0, primary.confidence))
        return CompileResult(
            text=formatted,
            stable_prefix=stable,
            confidence=confidence,
            anchors=protected_anchors(formatted, voice.context.candidates),
            decisions=tuple(decisions),
        )

    def verify_edits(self, source: str,
                     proposals: Iterable[EditProposal],
                     context: Iterable[ContextCandidate] = (),
                     mode: str = "capture",
                     self_correction: bool = False) -> ProofResult:
        """Apply independently validated bounded edits to capture text.

        ``self_correction`` is the user's opt-in to the "X, actually Y"
        rule, the same one ``compile_cleanup`` takes. Without it no edit is
        accepted as a self-correction.
        """
        current = source
        proofs: list[ProofEdit] = []
        global_anchors = protected_anchors(source, context)
        for proposal in proposals:
            before = proposal.before
            after = proposal.after
            start, end = self._locate_edit(current, before, mode)
            reason = self._validate_edit(
                current, proposal, start, end, global_anchors, mode,
                self_correction)
            if reason == "accepted":
                reason = self._validate_in_context(
                    current, proposal, start, end, global_anchors, mode)
            accepted = reason == "accepted"
            if accepted:
                current = current[:start] + after + current[end:]
            proofs.append(ProofEdit(
                proposal.kind, before, after, start, end, accepted, reason))
        return ProofResult(current, tuple(proofs))

    @staticmethod
    def _covers_whole_tokens(text: str, start: int, end: int,
                             fragment: str, preserve_formatting: bool) -> bool:
        """Whether ``fragment`` at ``text[start:end]`` splits no token."""
        return (_proof_words(text[:start], preserve_formatting)
                + _proof_words(fragment, preserve_formatting)
                + _proof_words(text[end:], preserve_formatting)
                ) == _proof_words(text, preserve_formatting)

    def _locate_edit(self, current: str, before: str,
                     mode: str) -> tuple[int, int]:
        """Find the span an edit names, preferring one made of whole tokens.

        "um" is also inside "document"; the edit means the word. Offsets come
        from the text itself: case folding can change its length.
        """
        if not before:
            return -1, -1
        first = None
        # A lookahead, so an occurrence that starts inside an earlier one
        # ("um um" after "album um") is still considered.
        pattern = f"(?=({re.escape(before)}))"
        for flags in (0, re.IGNORECASE):
            matches = re.finditer(pattern, current, flags)
            for _attempt, match in zip(range(64), matches):
                span = match.span(1)
                if self._covers_whole_tokens(
                        current, *span, before, mode == "code"):
                    return span
                first = first or span
        return first or (-1, -1)

    def _validate_in_context(self, current: str, proposal: EditProposal,
                             start: int, end: int,
                             global_anchors: Sequence[str], mode: str) -> str:
        """Check what an edit proved on its fragment does to the whole text.

        A fragment can end inside a token, where "3." is a number and a full
        stop and removing the stop turns 3.5 into 35. The fragment proof only
        holds when the edit replaces whole tokens and leaves every token
        outside it as it was.
        """
        candidate = current[:start] + proposal.after + current[end:]

        def words(text: str) -> list[str]:
            return [token.text.casefold() for token in _lexical_tokens(text)]

        remaining = words(candidate)
        outside = words(current)
        for word in words(proposal.before):
            if word in outside:
                outside.remove(word)
        protected = {
            word for anchor in global_anchors for word in words(anchor)
        } | {word for word in outside
             if any(char.isdigit() for char in word)}
        for word in dict.fromkeys(outside):
            if word in protected \
                    and remaining.count(word) < outside.count(word):
                return f"protected anchor changed: {word}"
        preserve_formatting = mode == "code"
        if not self._covers_whole_tokens(
                current, start, end, proposal.before, preserve_formatting) \
                or not self._covers_whole_tokens(
                    candidate, start, start + len(proposal.after),
                    proposal.after, preserve_formatting):
            return "edit does not cover whole tokens"
        return "accepted"

    def _validate_edit(self, source: str, proposal: EditProposal,
                       start: int, end: int,
                       global_anchors: Sequence[str], mode: str,
                       self_correction: bool = False) -> str:
        if not proposal.before or start < 0:
            return "source span not found"
        if len(proposal.before) > 240:
            return "edit span is not bounded"
        before_lower = proposal.before.casefold()
        after_lower = proposal.after.casefold()
        preserve_formatting = mode == "code"
        before_words = _proof_words(proposal.before, preserve_formatting)
        after_words = _proof_words(proposal.after, preserve_formatting)
        # The model may claim a self-correction only where the deterministic
        # pass would have made the same one: the user turned the rule on, and
        # both sides of "actually" are the same kind of item. Any other
        # "actually" is the speaker's adverb.
        correction = find_self_correction(
            proposal.before, source[:start], source[end:]) \
            if self_correction else None
        explicit_correction = False
        if correction is not None:
            corrected_from, corrected_to, replacement = correction
            # The edit must begin at the corrected item, so the exemption
            # from the anchor check below covers that item and nothing else.
            explicit_correction = bool(
                after_words
                and not _proof_words(
                    proposal.before[:corrected_from], preserve_formatting)
                and _proof_words(
                    replacement + proposal.before[corrected_to:],
                    preserve_formatting) == after_words)
        for anchor in global_anchors:
            if anchor.casefold() in before_lower \
                    and anchor.casefold() not in after_lower \
                    and not explicit_correction:
                return f"protected anchor removed: {anchor}"
        if len(proposal.after) > max(280, len(proposal.before) * 3):
            return "edit expands source excessively"
        # Punctuation, whitespace, and capitalization may change freely only
        # when the ordered lexical content is identical.
        if before_words == after_words:
            return "accepted"
        safe_removed = {_norm(word) for word in PROVABLE_FILLER_WORDS}
        if [word for word in before_words if word not in safe_removed] \
                == after_words:
            return "accepted"
        # A self-correction is proved by the literal spoken marker and only
        # the words after that marker may survive.
        if explicit_correction:
            return "accepted"
        # Spoken layout commands may disappear, but every other lexical token
        # must remain in order.
        structure_free: list[str] = []
        index = 0
        while index < len(before_words):
            if (index + 1 < len(before_words)
                    and before_words[index] == "new"
                    and before_words[index + 1] in {"line", "paragraph"}):
                index += 2
                continue
            structure_free.append(before_words[index])
            index += 1
        if structure_free == after_words:
            return "accepted"
        enumeration_payload = _enumeration_payload(before_words)
        if enumeration_payload is not None and enumeration_payload == after_words:
            return "accepted"
        explicit_list_variants = _explicit_list_variants(before_words)
        if after_words in explicit_list_variants or any(
                variant in explicit_list_variants
                for variant in _formatted_list_word_variants(proposal.after)):
            return "accepted"
        if proposal.before == source and len(source.split()) > 12:
            return "whole-message rewrite is not a bounded edit"
        return "unproved lexical transformation"

    def _fuse(self, primary: RecognitionHypothesis,
              voice: VoiceIR) -> tuple[str, list[Decision]]:
        primary_tokens = _tokens(primary.text)
        if not primary_tokens:
            return primary.text, []
        candidates: list[dict[str, tuple[str, float, str]]] = [
            {_norm(token.text): (
                token.text,
                primary.confidence + _engine_bonus(primary.engine),
                primary.engine or "primary",
            )} for token in primary_tokens
        ]
        primary_norm = [_norm(token.text) for token in primary_tokens]

        for hypothesis in voice.hypotheses[1:]:
            alternative = _tokens(hypothesis.text)
            alternative_norm = [_norm(token.text) for token in alternative]
            matcher = difflib.SequenceMatcher(
                None, primary_norm, alternative_norm, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for offset, index in enumerate(range(i1, i2)):
                        token = alternative[j1 + offset].text
                        key = _norm(token)
                        old = candidates[index].get(key)
                        agreement = 0.07
                        score = hypothesis.confidence \
                            + _engine_bonus(hypothesis.engine) + agreement
                        if old is None or score > old[1]:
                            candidates[index][key] = (
                                token, score, hypothesis.engine or "agreement")
                elif tag == "replace" and i2 - i1 == j2 - j1:
                    for offset, index in enumerate(range(i1, i2)):
                        token = alternative[j1 + offset].text
                        key = _norm(token)
                        if not key:
                            continue
                        score = hypothesis.confidence \
                            + _engine_bonus(hypothesis.engine)
                        old = candidates[index].get(key)
                        if old is None or score > old[1]:
                            candidates[index][key] = (
                                token, score, hypothesis.engine or "alternative")

        context_by_key = {
            _norm(candidate.text): candidate
            for candidate in voice.context.candidates
            if _norm(candidate.text)
        }
        for index, token in enumerate(primary_tokens):
            original = token.text
            if primary.confidence > CONTEXT_REWRITE_MAX_CONFIDENCE:
                continue
            # Only a word the recognizer itself wrote as a name or an
            # identifier may be repaired from the screen. Its phonetic gate
            # ignores vowels, so eye/AI, fine/Finn and code/Git all pass it;
            # an ordinary word, lower case or capitalized only because it
            # starts a sentence, stays as spoken.
            previous = primary_tokens[index - 1].text if index else "."
            written_as_name = (
                (original[:1].isupper()
                 and previous not in {".", "!", "?", ":", ";"})
                or any(char.isdigit() or char == "_" for char in original)
                or any(char.isupper() for char in original[1:]))
            if not written_as_name:
                continue
            for context in voice.context.candidates:
                if len(TOKEN_RE.findall(context.text)) != 1:
                    continue
                if not _safe_context_replacement(original, context):
                    continue
                distinctive = (
                    any(char.isdigit() for char in context.text)
                    or "_" in context.text
                    or any(char.isupper() for char in context.text[1:])
                    or (context.text[:1].isupper() and len(context.text) <= 7)
                    or context.source in {"selection", "document", "repository"}
                )
                if not distinctive:
                    continue
                similarity = phonetic_similarity(original, context.text)
                if similarity < (0.84 if context.weight >= 2.5 else 0.88):
                    continue
                key = _norm(context.text)
                score = primary.confidence + min(0.42, context.weight * 0.045) \
                    + similarity * 0.12
                old = candidates[index].get(key)
                if old is None or score > old[1]:
                    candidates[index][key] = (
                        context.text, score, f"context:{context.source}")

        # A Personal Prior reaches the compiler only after the Personal
        # Regression Lab activated it. It is a decided replacement of the
        # exact heard word, never a score: scored, it lost to the engine
        # bonus of every recognizer except Tiny.
        decided: dict[int, str] = {}
        for index, token in enumerate(primary_tokens):
            for prior in voice.personal_priors:
                if token.text.casefold() != prior.heard.casefold():
                    continue
                if prior.apps and not prior.app_count(voice.app_bundle):
                    continue
                # A word inside an email address, a handle or a URL is part
                # of an identifier, not the word the prior was learned for.
                run = (re.search(r"\S*\Z", primary.text[:token.start]).group()
                       + re.match(r"\S*", primary.text[token.start:]).group())
                if "@" in run or "://" in run:
                    break
                preferred = prior.preferred
                # A capital the recognizer placed, such as a sentence start,
                # survives an all-lowercase preferred spelling.
                if (token.text[:1].isupper()
                        and not prior.heard[:1].isupper()
                        and preferred == preferred.lower()):
                    preferred = preferred[:1].upper() + preferred[1:]
                decided[index] = preferred
                break

        replacements: list[tuple[int, int, str]] = []
        decisions: list[Decision] = []
        original_anchors = set(protected_anchors(primary.text))
        for index, options in enumerate(candidates):
            original = primary_tokens[index]
            if index in decided:
                if decided[index] != original.text:
                    replacements.append(
                        (original.start, original.end, decided[index]))
                    decisions.append(Decision(
                        "span-graph", original.text, decided[index], 1.0,
                        "personal-prior"))
                continue
            original_key = _norm(original.text)
            base = options.get(original_key, (original.text,
                                               primary.confidence, "primary"))
            winner = max(options.values(), key=lambda item: item[1])
            delta = winner[1] - base[1]
            if winner[0] == original.text:
                continue
            context_exact = _norm(winner[0]) in context_by_key
            is_anchor = original.text in original_anchors
            threshold = 0.12 if not is_anchor else (
                0.18 if context_exact else 0.24)
            if delta < threshold:
                continue
            if is_anchor and not context_exact and delta < 0.30:
                continue
            replacements.append((original.start, original.end, winner[0]))
            decisions.append(Decision(
                "span-graph", original.text, winner[0], delta, winner[2]))
        return _replace_spans(primary.text, replacements), decisions

    def _format_prosody(
            self, text: str, words: Sequence[WordEvidence],
            events: Sequence[ProsodyEvent], *,
            pauses: bool = True) -> tuple[str, list[Decision]]:
        if not text or not events:
            return text, []
        decisions: list[Decision] = []
        output = text
        timed_words = [word for word in words
                       if word.end > word.start and word.timing == "native"
                       ] if pauses else []
        text_tokens = [token for token in _tokens(output) if _norm(token.text)]
        insertions: list[tuple[int, int, str, str]] = []
        # One-to-one alignment is required. Segment interpolation and partial
        # SDK word lists are not precise enough to place semantic punctuation.
        if timed_words and len(timed_words) == len(text_tokens):
            for event in events:
                if event.kind != "pause" or event.duration < 0.45:
                    continue
                word_index = max((i for i, word in enumerate(timed_words)
                                  if word.end <= event.at), default=-1)
                if word_index < 0 or word_index >= len(text_tokens) - 1:
                    continue
                position = text_tokens[word_index].end
                tail = output[position:position + 2]
                if "\n" in tail:
                    continue
                if event.duration >= 0.9:
                    punctuation_end = position
                    if output[position:position + 1] in ".!?":
                        punctuation_end += 1
                    insertion_end = punctuation_end + (
                        1 if output[punctuation_end:punctuation_end + 1] == " "
                        else 0)
                    insertions.append((
                        punctuation_end, insertion_end, "\n\n", "paragraph"))
                elif output[position - 1:position] not in ",.;:!?":
                    insertions.append((
                        position, position, ",", "pause-punctuation"))
        for start, end, insertion, reason in sorted(insertions, reverse=True):
            output = output[:start] + insertion + output[end:]
            decisions.append(Decision(
                "prosody", "", insertion, 0.2, reason))
        if any(event.kind == "rising_end" and event.strength >= 0.6
               for event in events):
            first = next(iter(TOKEN_RE.findall(output)), "").casefold()
            if first in QUESTION_WORDS and output.rstrip().endswith("."):
                output = output.rstrip()[:-1] + "?"
                decisions.append(Decision(
                    "prosody", ".", "?", 0.25, "rising question contour"))
        return output, decisions

    def _stable_prefix(self, voice: VoiceIR) -> str:
        hypotheses = [
            [token.text for token in _tokens(hypothesis.text)]
            for hypothesis in voice.hypotheses
        ]
        if not hypotheses:
            return ""
        if len(hypotheses) == 1:
            safe = hypotheses[0][:-2] if len(hypotheses[0]) > 2 else []
        else:
            safe = []
            for group in zip(*hypotheses):
                if len({_norm(token) for token in group}) != 1:
                    break
                safe.append(group[0])
        if not safe:
            return ""
        end = _tokens(voice.hypotheses[0].text)[len(safe) - 1].end
        return voice.hypotheses[0].text[:end].rstrip()


def context_firewall_receipt(
        voice: VoiceIR, *, compiled: CompileResult | None = None,
        compiler: VoiceCompiler | None = None) -> ContextFirewallReceipt:
    """Compare active compilation with a context-free shadow baseline.

    This function cannot promote, quarantine, or route anything. The active
    result is accepted only as an input to the comparison, and the baseline is
    compiled from a copy of VoiceIR with both Context Candidates and Personal
    Priors removed. Only fixed aggregate evidence leaves this boundary.
    """
    worker = compiler or VoiceCompiler()
    active = compiled or worker.compile(voice)
    baseline_voice = replace(
        voice, context=ContextPack(), personal_priors=())
    baseline = worker.compile(baseline_voice)
    changed = active.text != baseline.text

    source_decisions = tuple(
        decision for decision in active.decisions
        if decision.reason == "personal-prior"
        or decision.reason.startswith("context:")
    ) if changed else ()
    primary_text = voice.hypotheses[0].text
    primary_tokens = _tokens(primary_text)
    plan = build_consequence_plan(voice)
    risk_ranges = tuple(
        (risk.char_start, risk.char_end) for risk in plan.risks)
    anchor_keys = {
        _norm(anchor)
        for anchor in protected_anchors(
            primary_text, voice.context.candidates)
        if _norm(anchor)
    }

    def is_protected(decision: Decision) -> bool:
        before_key = _norm(decision.before)
        matching = tuple(
            token for token in primary_tokens
            if _norm(token.text) == before_key)
        if any(any(_overlaps(
                (token.start, token.end), risk_range)
                   for risk_range in risk_ranges)
               for token in matching):
            return True
        if before_key and before_key in anchor_keys:
            return True
        # A contextual replacement that introduces a new factual/code-shaped
        # anchor is consequential even when the original acoustic token was
        # ordinary. Keep that candidate in shadow quarantine as well.
        return bool(protected_anchors(decision.after))

    reasons: dict[str, int] = {}
    context_count = prior_count = protected_count = 0
    for decision in source_decisions:
        source = "personal-prior" \
            if decision.reason == "personal-prior" else "context"
        if source == "context":
            context_count += 1
        else:
            prior_count += 1
        protected = is_protected(decision)
        protected_count += int(protected)
        reason = f"{source}-{'protected' if protected else 'unprotected'}"
        reasons[reason] = reasons.get(reason, 0) + 1

    influence_count = len(source_decisions)
    quarantined = protected_count
    promotion_candidates = max(0, influence_count - protected_count)
    if quarantined:
        disposition = "quarantine"
    elif promotion_candidates:
        disposition = "promotion-candidate"
    else:
        disposition = "no-effect"
        reasons["no-influence"] = 1
    return ContextFirewallReceipt(
        mode="shadow-only",
        disposition=disposition,
        counterfactual_changed=changed,
        risky_spans=len(plan.risks),
        influence_count=influence_count,
        context_influences=context_count,
        personal_prior_influences=prior_count,
        protected_influences=protected_count,
        promotion_candidates=promotion_candidates,
        quarantined=quarantined,
        reason_counts=tuple(sorted(reasons.items())),
    )


def analyze_prosody(samples: Sequence[float], sample_rate: int = 16_000,
                     frame_ms: int = 20) -> tuple[ProsodyEvent, ...]:
    """Extract conservative pause/end events without another model."""
    if sample_rate <= 0 or not samples:
        return ()
    frame = max(1, int(sample_rate * frame_ms / 1000))
    rms: list[float] = []
    for start in range(0, len(samples), frame):
        chunk = samples[start:start + frame]
        if len(chunk) < frame // 2:
            break
        rms.append(math.sqrt(sum(float(value) ** 2 for value in chunk)
                             / len(chunk)))
    if not rms:
        return ()
    ordered = sorted(rms)
    noise = ordered[max(0, int(len(ordered) * 0.2) - 1)]
    peak = max(rms)
    threshold = max(0.002, noise * 2.5, peak * 0.055)
    voiced = [value >= threshold for value in rms]
    events: list[ProsodyEvent] = []
    index = 0
    while index < len(voiced):
        if voiced[index]:
            index += 1
            continue
        start = index
        while index < len(voiced) and not voiced[index]:
            index += 1
        duration = (index - start) * frame / sample_rate
        has_voice_before = any(voiced[:start])
        has_voice_after = any(voiced[index:])
        if has_voice_before and has_voice_after and duration >= 0.35:
            events.append(ProsodyEvent(
                "pause", start * frame / sample_rate,
                duration, min(1.0, duration / 1.2)))
        elif has_voice_before and not has_voice_after and duration >= 0.18:
            events.append(ProsodyEvent(
                "end", start * frame / sample_rate,
                duration, min(1.0, duration / 0.6)))
    return tuple(events)


# --- dictionary spelling: the clean-up's deterministic rule -----------------
#
# When the words heard equal a term the user typed into the dictionary,
# ignoring only spaces, hyphens and letter case, the term is written in its
# own form: "git hub" and "Git-Hub" become "GitHub", "whisperface" becomes
# "Whisper Face", "iphone" becomes "iPhone". Nothing else is compared, so no
# sound-alike, no other letter and no punctuation between the words can
# make a match.

# Ordinary words that are also names, which a dictionary holds as names.
# They join personal_regression's common words for this rule only, so that
# "Mark" in the dictionary never capitalises "mark" and "Grace" never
# capitalises "grace".
NAME_LIKE_ORDINARY_WORDS = frozenset("""
mark bill frank grace rose will may june april august art bob jack pat sue
joy hope faith dawn rich rob gene earl guy max ray rod don dean drew lane
chase hunter mason grant page reed wade wells summer autumn winter amber
ruby pearl iris violet lily daisy holly ivy heather olive penny sandy
crystal brook river sky storm robin jay wren finch bishop baker cook
miller smith carter porter cooper fisher hunt price rice young king
""".split())
# The longest run of written words one term may be heard across.
DICTIONARY_SPELLING_MAX_WORDS = 4
_SPELLING_WORD_RE = re.compile(r"[0-9A-Za-z]+(?:[._+#-][0-9A-Za-z]+)*")
_SPELLING_SPACE_RE = re.compile(r"[ \t]+")
# A word touching one of these is part of an address, a path or a handle.
_SPELLING_IDENTIFIER_NEIGHBOURS = frozenset("@/\\:_~=")


def is_ordinary_word(word: str) -> bool:
    """A common English word, or an ordinary word that is also a name."""
    return is_common_word(word) or word.casefold() in NAME_LIKE_ORDINARY_WORDS


def _spelling_key(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).casefold()


def _ordinary_term(term: str) -> bool:
    """Whether a term is an ordinary word: no digit, no capital after a
    word's first letter, and every word of it ordinary."""
    if any(char.isdigit() for char in term):
        return False
    words = [word for word in re.split(r"[\s-]+", term) if word]
    if any(any(char.isupper() for char in word[1:]) for word in words):
        return False
    return all(is_ordinary_word(word) for word in words)


def apply_dictionary_spelling(
        text: str, terms: Iterable[str],
        evidence: Iterable[VocabularyCandidate] = ()) \
        -> tuple[str, list[str]]:
    """Write each dictionary term in its own form where it was heard.

    Returns the text and, for each change, the heard words it replaced.
    ``evidence`` is the term pass's report for this dictation
    (``Recognition.vocabulary_evidence``), when there is one. The guards,
    all of them deliberate:

    - A change of letter case alone is made only for a term that is not an
      ordinary word, and never to a heard word that is one: "Mark" listed
      leaves "mark" alone and "US" listed leaves "us" alone. It only raises
      letters, as the casing rule before it did, so "GITHUB" and a capital
      the recognizer placed stay.
    - Joining words needs at least two heard words ("git hub", "co-pilot");
      no join or split writes an ordinary word, or starts from one heard
      word that is ordinary, and none takes away a negation ("not able").
    - A join that takes in an ordinary word ("tail scale" for "Tailscale")
      is the near miss itself: someone saying "a shiny tail scale" said
      those words. It is written only where the term pass found the term
      over those same words within its debt budget
      (``_supported_joins``); otherwise the words stay as heard, for the
      term pass to decide. Parts that are none of them ordinary words
      ("Chi Din Ma" for "Chidinma", "git hub") join as before.
    - Only spaces may separate the heard words: a comma, a full stop or a
      line break between them means they were not said as one term.
    - A word touching ``@``, ``/``, ``:`` or ``_`` belongs to an address,
      a path or a handle and is never part of a match.
    - At the start of a sentence a lower-case term keeps the capital
      ("Kube ctl" there is written "Kubectl").
    """
    by_key: dict[str, str] = {}
    for term in terms:
        if not isinstance(term, str):
            continue
        term = term.strip()
        key = _spelling_key(term)
        if key and key not in by_key and _SPELLING_WORD_RE.fullmatch(
                re.sub(r"[\s-]+", "", term) or "-"):
            by_key[key] = term
    if not text or not by_key:
        return text, []
    supported = _supported_joins(evidence)
    words = list(_SPELLING_WORD_RE.finditer(text))
    replacements: list[tuple[int, int, str]] = []
    changed: list[str] = []
    index = 0
    while index < len(words):
        matched = 0
        for count in range(min(DICTIONARY_SPELLING_MAX_WORDS,
                               len(words) - index), 0, -1):
            span = words[index:index + count]
            if any(text[left.end():right.start()] != ""
                   and not _SPELLING_SPACE_RE.fullmatch(
                       text[left.end():right.start()])
                   for left, right in zip(span, span[1:])):
                continue
            start, end = span[0].start(), span[-1].end()
            before = text[start - 1] if start else " "
            after = text[end] if end < len(text) else " "
            if (before in _SPELLING_IDENTIFIER_NEIGHBOURS
                    or after in _SPELLING_IDENTIFIER_NEIGHBOURS):
                continue
            heard = text[start:end]
            term = by_key.get(_spelling_key(heard))
            if term is None:
                continue
            written = _spelling_for(text, start, heard, term, supported)
            if written is not None and written != heard:
                replacements.append((start, end, written))
                changed.append(heard)
            matched = count
            break
        index += matched or 1
    return _replace_spans(text, replacements), changed


def _evidence_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.casefold())


def _supported_joins(
        evidence: Iterable[VocabularyCandidate]) -> frozenset:
    """The (term, heard words) pairs the term pass found within budget,
    each compared on its letters and digits alone."""
    supported = set()
    for candidate in evidence or ():
        try:
            if float(candidate.debt) <= VOCABULARY_DEBT_BUDGET:
                supported.add((_evidence_key(candidate.term),
                               _evidence_key(candidate.heard)))
        except (AttributeError, TypeError, ValueError):
            continue
    return frozenset(supported)


def _spelling_for(text: str, start: int, heard: str, term: str,
                  supported: frozenset = frozenset()) -> str | None:
    """The form to write for ``heard``, or None when a guard refuses."""
    heard_words = [word for word in re.split(r"[\s-]+", heard) if word]
    term_words = [word for word in re.split(r"[\s-]+", term) if word]
    if [word.casefold() for word in heard_words] \
            == [word.casefold() for word in term_words]:
        # The same words: only letter case, or a hyphen for a space,
        # differs. Never for an ordinary term or ordinary heard words.
        if _ordinary_term(term) or all(
                is_ordinary_word(word) for word in heard_words):
            return None
        if heard.casefold() != term.casefold():
            return _sentence_case(text, start, heard, term)
        if any(have.isupper() and not want.isupper()
               for have, want in zip(heard, term)):
            return None
        return term
    # A join or a split.
    if _ordinary_term(term):
        return None
    if len(heard_words) == 1 and is_ordinary_word(heard_words[0]):
        return None
    if any(is_negation(word) for word in heard_words):
        return None
    if (len(heard_words) > 1
            and any(is_ordinary_word(word) for word in heard_words)
            and (_evidence_key(term), _evidence_key(heard)) not in supported):
        return None
    return _sentence_case(text, start, heard, term)


def _sentence_case(text: str, start: int, heard: str, term: str) -> str:
    """Keep a sentence-start capital on a term typed in lower case."""
    preceding = text[:start].rstrip()
    sentence_start = not preceding or preceding[-1] in ".!?\n"
    if (sentence_start and heard[:1].isupper() and term[:1].islower()
            and not any(char.isupper() for char in term)):
        return term[:1].upper() + term[1:]
    return term


# --- vocabulary evidence: the decision policy, in shadow --------------------
#
# The speech runtime's term pass reports where a listed term could have been
# emitted instead of what was heard (parrot_core.VocabularyCandidate). This
# decides, for each report, whether the app would write the term. In shadow
# it applies nothing: the decisions are counted and shown, and the text is
# the recognizer's. Each threshold is a named constant beside the measurement
# it came from (research-2026-11-17/names-probe, on the owner's 40
# recordings and 60 synthetic control clips; a best-timing search, so an
# upper bound for what the runtime's pass finds).

# Summed logit gap a detour may pay. Probe: 5 fixed 15 of the 21 missed
# terms with no false insertion on the owner's 10 ordinary recordings and
# 1 on 60 synthetic control clips (2 fixed 5; 10 fixed 20 with 3 false).
VOCABULARY_DEBT_BUDGET = 5.0
# What the runtime is asked to report. The pass's time grows with it: on
# the owner's 40 recordings, 8 took 19 ms at p95 and 5 took 9 ms, with the
# same decisions at the budget.
VOCABULARY_REPORT_DEBT = VOCABULARY_DEBT_BUDGET
# Letter-based sound-alike similarity the heard words must reach (below).
# Probe: at 0.7 with the budget of 5 it removed the one synthetic false
# insertion and kept all 15 fixes. Chosen after seeing that data: a
# hypothesis until a held-out set confirms it.
VOCABULARY_SOUND_ALIKE_MIN = 0.7
# The normal pass must have been unsure of the heard words: their lowest
# token probability below this. Probe: below 0.99 lost no fix at the budget
# of 5; below 0.9 lost one. A weak guard while the model's probabilities are
# uncalibrated (DESIGN-accuracy.md section 6.1).
VOCABULARY_UNSURE_BELOW = 0.99
# Blast radius (DESIGN-accuracy.md section 3.0).
VOCABULARY_MAX_PER_DICTATION = 2
VOCABULARY_MAX_HEARD_WORDS = 3
# A heard word's start and the report's agree to the runtime's 1 ms rounding.
_VOCABULARY_TIME_TOLERANCE = 0.0015
VOCABULARY_SOURCES = frozenset({"typed", "corrected"})


@dataclass(frozen=True)
class VocabularyTerm:
    """A term the term pass may look for: typed into the dictionary, or the
    target of a correction the Personal Regression Lab proved. A heard form
    the user corrected to it is an alias."""
    text: str
    source: str = "typed"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class VocabularyDecision:
    term: str
    heard: str
    start: int
    end: int
    debt: float
    similarity: float
    accepted: bool
    reason: str
    # What would be written over text[start:end]: the term, keeping the
    # heard words' leading and trailing punctuation and a possessive.
    written: str = ""


_VOCABULARY_EDGES_RE = re.compile(
    r"\A([^\w\s]*)(.*?)((?:['\u2019]s)?[^\w\s]*)\Z", re.S)


def _letter_sound_key(value: str) -> str:
    """A crude English sound key from letters, as the probe measured it."""
    key = re.sub(r"[^a-z0-9]", "", value.lower())
    for old, new in (("tch", "ch"), ("sch", "sk"), ("ph", "f"), ("gh", ""),
                     ("ck", "k"), ("qu", "kw"), ("q", "k"), ("x", "ks"),
                     ("wh", "w"), ("dg", "j"), ("th", "t"), ("sh", "s"),
                     ("ch", "s"), ("z", "s"), ("v", "f"), ("w", "u"),
                     ("y", "i")):
        key = key.replace(old, new)
    key = re.sub(r"c(?=[eiy])", "s", key).replace("c", "k")
    key = re.sub(r"[aeiou]+", "a", key)
    return re.sub(r"(.)\1+", r"\1", key)


def letter_sound_similarity(heard: str, term: str) -> float:
    """1 minus the edit distance of the two sound keys over the longer."""
    left, right = _letter_sound_key(heard), _letter_sound_key(term)
    if not left or not right:
        return 0.0
    row = list(range(len(right) + 1))
    for i in range(1, len(left) + 1):
        previous, row[0] = row[0], i
        for j in range(1, len(right) + 1):
            current = row[j]
            row[j] = min(row[j] + 1, row[j - 1] + 1,
                         previous + (left[i - 1] != right[j - 1]))
            previous = current
    return 1 - row[len(right)] / max(len(left), len(right))


def _word_spans(text: str, words: Sequence) -> list[tuple[int, int]] | None:
    """Each word's character span in ``text``, found in order."""
    spans, cursor = [], 0
    for word in words:
        found = text.find(word.text, cursor)
        if found < 0:
            return None
        spans.append((found, found + len(word.text)))
        cursor = found + len(word.text)
    return spans


def apply_vocabulary_evidence(
        text: str,
        words: Sequence,
        candidates: Iterable[VocabularyCandidate],
        terms: Iterable[VocabularyTerm],
        *,
        mode: str = "capture",
        budget: float = VOCABULARY_DEBT_BUDGET,
        sound_alike_min: float = VOCABULARY_SOUND_ALIKE_MIN,
        unsure_below: float = VOCABULARY_UNSURE_BELOW,
) -> tuple[VocabularyDecision, ...]:
    """Decide, for each reported detour, whether the term would be written.

    Pure, and in shadow: it returns decisions and changes nothing; the text
    a decision would give is ``vocabulary_alternative``'s. ``words`` are the
    recognition's timed words (``.text``, ``.start``) on the same clock as
    the candidates. A candidate is accepted only if every one of these
    holds, checked in this order, and the first that fails is its reason:

    - its heard words are the recognition's own words at those times
      (``not-placed``), at most ``VOCABULARY_MAX_HEARD_WORDS`` of them
      (``too-long``), in capture mode (``mode``);
    - the term is listed, typed or proved by a correction
      (``not-listed``), and is not what the dictionary spelling rule
      already writes (``spelling-rule``);
    - the debt is within ``budget`` (``over-budget``);
    - no digit, negation, address, handle or path in the heard words
      (``protected``), and not one heard word that is a common word
      unless it is an alias (``common-word``);
    - the heard words sound like the term, or are an alias
      (``not-sound-alike``);
    - the normal pass was unsure of them (``sure``);
    - and it is among the ``VOCABULARY_MAX_PER_DICTATION`` cheapest that
      do not overlap (``overlap``, ``limit``).
    """
    listed = {term.text: term for term in terms
              if term.source in VOCABULARY_SOURCES}
    spans = _word_spans(text, words) if words else None
    pending: list[tuple[VocabularyCandidate, int, int, float, str]] = []
    decisions: list[VocabularyDecision] = []

    def refuse(candidate, start, end, similarity, reason):
        decisions.append(VocabularyDecision(
            candidate.term, candidate.heard, start, end, candidate.debt,
            similarity, False, reason))

    for candidate in candidates:
        indices = [
            index for index, word in enumerate(words or ())
            if any(abs(float(word.start) - start) <= _VOCABULARY_TIME_TOLERANCE
                   for start in candidate.heard_word_starts)]
        start = end = -1
        if (spans is not None and indices
                and len(indices) == len(candidate.heard_word_starts)
                and indices == list(range(indices[0], indices[-1] + 1))):
            start, end = spans[indices[0]][0], spans[indices[-1]][1]
            if re.sub(r"\s+", "", text[start:end]).casefold() \
                    != re.sub(r"\s+", "", candidate.heard).casefold():
                start = end = -1
        heard = text[start:end] if start >= 0 else candidate.heard
        lead, core, tail = _VOCABULARY_EDGES_RE.match(heard).groups()
        heard_words = heard.split()
        similarity = letter_sound_similarity(core, candidate.term)
        term = listed.get(candidate.term)
        aliases = {alias.casefold() for alias in term.aliases} if term else set()
        run = (re.search(r"\S*\Z", text[:start]).group()
               + heard + re.match(r"\S*", text[end:]).group()) \
            if start >= 0 else heard
        if start < 0:
            reason = "not-placed"
        elif len(heard_words) > VOCABULARY_MAX_HEARD_WORDS:
            reason = "too-long"
        elif mode != "capture":
            reason = "mode"
        elif term is None:
            reason = "not-listed"
        elif _spelling_key(core) == _spelling_key(candidate.term):
            reason = "spelling-rule"
        elif candidate.debt > budget:
            reason = "over-budget"
        elif (any(char.isdigit() for char in heard)
              or any(is_negation(word.strip(".,;:!?")) for word in heard_words)
              or "@" in run or "://" in run or "/" in run):
            reason = "protected"
        elif (len(heard_words) == 1
              and is_ordinary_word(heard_words[0].strip(".,;:!?"))
              and core.casefold() not in aliases):
            reason = "common-word"
        elif similarity < sound_alike_min and core.casefold() not in aliases:
            reason = "not-sound-alike"
        elif candidate.heard_min_prob >= unsure_below:
            reason = "sure"
        else:
            pending.append((candidate, start, end, similarity,
                            lead + candidate.term + tail))
            continue
        refuse(candidate, start, end, similarity, reason)

    taken: list[tuple[int, int]] = []
    for candidate, start, end, similarity, written in sorted(
            pending, key=lambda item: (item[0].debt, item[1])):
        if any(start < right and left < end for left, right in taken):
            refuse(candidate, start, end, similarity, "overlap")
        elif len(taken) >= VOCABULARY_MAX_PER_DICTATION:
            refuse(candidate, start, end, similarity, "limit")
        else:
            taken.append((start, end))
            decisions.append(VocabularyDecision(
                candidate.term, candidate.heard, start, end, candidate.debt,
                similarity, True, "would-write", written))
    return tuple(sorted(decisions, key=lambda item: (item.start, item.debt)))


VOCABULARY_MAX_TERMS = 100
_VOCABULARY_TERM_RE = re.compile(r"[A-Za-z]+(?:[ '\u2019-][A-Za-z]+)*")


def vocabulary_terms(typed: Iterable[str],
                     corrected: Iterable[tuple[str, str]] = ()) \
        -> tuple[VocabularyTerm, ...]:
    """The terms the term pass may look for (DESIGN-accuracy.md 3.0):
    the dictionary's typed terms, then targets of corrections the Personal
    Regression Lab proved (``(heard, preferred)``), whose heard form becomes
    an alias. Left out: a term of fewer than 4 letters, an ordinary word, a
    term with a digit or a symbol (this first version), and anything past
    ``VOCABULARY_MAX_TERMS``. Screen, app and window text, selections, the
    clipboard and mined terms never reach this list."""
    found: dict[str, VocabularyTerm] = {}

    def eligible(term: str) -> bool:
        return (bool(_VOCABULARY_TERM_RE.fullmatch(term))
                and sum(char.isalpha() for char in term) >= 4
                and not is_ordinary_word(term))

    for term in typed:
        term = " ".join(str(term).split())
        if eligible(term) and term.casefold() not in found:
            found[term.casefold()] = VocabularyTerm(term, "typed")
    for heard, preferred in corrected:
        preferred = " ".join(str(preferred).split())
        if not eligible(preferred):
            continue
        known = found.get(preferred.casefold())
        if known is None:
            found[preferred.casefold()] = VocabularyTerm(
                preferred, "corrected", (heard,))
        elif heard not in known.aliases:
            found[preferred.casefold()] = replace(
                known, aliases=known.aliases + (heard,))
    return tuple(found.values())[:VOCABULARY_MAX_TERMS]


def vocabulary_alternative(
        text: str, decisions: Iterable[VocabularyDecision]) -> str:
    """The text the accepted decisions would give: the "Alt:" a shadow run
    shows."""
    return _replace_spans(text, [
        (decision.start, decision.end, decision.written or decision.term)
        for decision in decisions if decision.accepted])
