"""Evidence-driven speech compilation for Whispering Parrot.

The public interface is intentionally small: build ``VoiceIR`` and call
``VoiceCompiler.compile``. Recognition fusion, contextual/personal ranking,
prosody formatting, stable-prefix calculation, protected anchors, and proof
edit verification stay local to this deep module.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Protocol, Sequence


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
CONTEXT_REPLACEMENT_BLOCKLIST = CORRECTION_BLOCKLIST | {
    "a", "an", "and", "as", "at", "be", "been", "being", "best",
    "but", "by", "for", "from", "in", "into", "my", "of", "on",
    "or", "our", "right", "sure", "the", "their", "then", "to",
    "with", "your",
}
CONTEXT_REWRITE_MAX_CONFIDENCE = 0.70
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


def _phonetic_key(value: str) -> str:
    word = re.sub(r"[^a-z]", "", value.casefold())
    if not word:
        return ""
    for pattern, replacement in (
        (r"^qw", "gw"), (r"^kn", "n"), (r"^wr", "r"),
        (r"ph", "f"), (r"ght", "t"),
        (r"qu", "k"), (r"ck", "k"), (r"[cq]", "k"), (r"x", "ks"),
        (r"[aeiouy]", ""), (r"(.)\1+", r"\1"),
    ):
        word = re.sub(pattern, replacement, word)
    return word[:16]


def phonetic_similarity(left: str, right: str) -> float:
    spelling = difflib.SequenceMatcher(
        None, left.casefold(), right.casefold()).ratio()
    phonetic = difflib.SequenceMatcher(
        None, _phonetic_key(left), _phonetic_key(right)).ratio()
    return max(spelling, phonetic * 0.92)


def _safe_context_replacement(original: str,
                              candidate: "ContextCandidate") -> bool:
    """Whether context may compete with acoustic evidence for one token.

    Context still biases the recognizer through its initial prompt.  This
    stricter gate covers the riskier post-ASR path, where visible UI strings
    must never manufacture numbers, timings, or ordinary prose.
    """
    original_word = original.casefold()
    text = candidate.text.strip()
    folded = text.casefold()
    if original_word in CONTEXT_REPLACEMENT_BLOCKLIST \
            or folded in CONTEXT_REPLACEMENT_BLOCKLIST:
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
        "active-context", "app", "window", "selection", "document",
        "repository",
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
    item_lines = [line for line in lines[first_item:]
                  if re.match(r"^\s*-\s+\S", line)]
    if len(item_lines) < 2 or len(item_lines) > 8:
        return ()
    variants = [_proof_words("\n".join(lines[:first_item]))]
    for index, line in enumerate(item_lines):
        item_words = _proof_words(re.sub(r"^\s*-\s+", "", line))
        if not item_words:
            return ()
        expanded = [words + item_words for words in variants]
        if index and item_words[0] != "and":
            expanded.extend(words + ["and", *item_words] for words in variants)
        variants = expanded
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
            fused, primary.words, voice.prosody)
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
                     mode: str = "capture") -> ProofResult:
        """Apply independently validated bounded edits to capture text."""
        current = source
        proofs: list[ProofEdit] = []
        global_anchors = protected_anchors(source, context)
        for proposal in proposals:
            before = proposal.before
            after = proposal.after
            start = current.find(before) if before else -1
            if start < 0 and before:
                folded = current.casefold().find(before.casefold())
                start = folded
            end = start + len(before) if start >= 0 else -1
            reason = self._validate_edit(
                current, proposal, start, end, global_anchors, mode)
            accepted = reason == "accepted"
            if accepted:
                current = current[:start] + after + current[end:]
            proofs.append(ProofEdit(
                proposal.kind, before, after, start, end, accepted, reason))
        return ProofResult(current, tuple(proofs))

    def _validate_edit(self, source: str, proposal: EditProposal,
                       start: int, end: int,
                       global_anchors: Sequence[str], mode: str) -> str:
        if not proposal.before or start < 0:
            return "source span not found"
        if len(proposal.before) > 240:
            return "edit span is not bounded"
        before_lower = proposal.before.casefold()
        after_lower = proposal.after.casefold()
        preserve_formatting = mode == "code"
        before_words = _proof_words(proposal.before, preserve_formatting)
        after_words = _proof_words(proposal.after, preserve_formatting)
        correction_marker = None
        if "actually" in before_words:
            correction_marker = (len(before_words) - 1
                                 - before_words[::-1].index("actually"))
        explicit_correction = bool(
            correction_marker is not None
            and correction_marker == 1
            and before_words[0] not in CORRECTION_BLOCKLIST
            and before_words[correction_marker + 1:] == after_words
            and len(after_words) == 1
            and after_words)
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

        for index, token in enumerate(primary_tokens):
            for prior in voice.personal_priors:
                if _norm(token.text) != _norm(prior.heard):
                    continue
                key = _norm(prior.preferred)
                score = primary.confidence + min(0.35, prior.count * 0.055) \
                    + min(0.28, prior.app_count(voice.app_bundle) * 0.10)
                old = candidates[index].get(key)
                if old is None or score > old[1]:
                    candidates[index][key] = (
                        prior.preferred, score, "personal-prior")

        replacements: list[tuple[int, int, str]] = []
        decisions: list[Decision] = []
        original_anchors = set(protected_anchors(primary.text))
        for index, options in enumerate(candidates):
            original = primary_tokens[index]
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
                0.18 if context_exact or winner[2] == "personal-prior" else 0.24)
            if delta < threshold:
                continue
            if (is_anchor and not context_exact
                    and winner[2] != "personal-prior" and delta < 0.30):
                continue
            replacements.append((original.start, original.end, winner[0]))
            decisions.append(Decision(
                "span-graph", original.text, winner[0], delta, winner[2]))
        return _replace_spans(primary.text, replacements), decisions

    def _format_prosody(
            self, text: str, words: Sequence[WordEvidence],
            events: Sequence[ProsodyEvent]) -> tuple[str, list[Decision]]:
        if not text or not events:
            return text, []
        decisions: list[Decision] = []
        output = text
        timed_words = [word for word in words
                       if word.end > word.start and word.timing == "native"]
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
