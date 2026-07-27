"""Pure decision logic for Whisper Face's voice pipeline.

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
    r"^[ \t]*(?:you know|I mean|basically|kind of|sort of)\b\s*,|"
    r",\s*(?:you know|I mean|basically|kind of|sort of)\b\s*,",
    re.I | re.M,
)
AMBIGUOUS_FILLER_RE = re.compile(r"\b(?:you know|I mean)\b", re.I)
LITERAL_FILLER_RE = re.compile(
    r"\b(?:phrase|words?|expression)\s+(?:you know|I mean)\b", re.I)
STRUCTURE_RE = re.compile(r"\bnew (line|paragraph)\b", re.I)
CORRECTION_RE = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_'’-]{0,30})\s+"
    r"(?:—\s*)?actually\s+"
    r"([A-Za-z0-9][A-Za-z0-9_'’-]{0,30})\b",
    re.I,
)
LIST_INTENT_RE = re.compile(
    r"^(?:(?:okay|ok|alright)[, ]+)?(?:so\s+)?(?:"
    r"here(?:['’]s|\s+is)\s+(?:a|my)\s+list\b|"
    r"here\s+(?:is|are)\s+(?:(?:a|some|the|my)\s+)?"
    r"(?:(?:few|several)\s+)?(?:feedback\s+)?"
    r"(?:items|ideas|points|things)\b|"
    r"I\s+(?:have|have\s+got|['’]ve\s+got)\s+"
    r"(?:(?:a|some|the|my)\s+)?(?:few\s+|several\s+)?"
    r"(?:feedback\s+)?(?:items|ideas|points|things)\b|"
    r"(?:let\s+me|I\s+(?:want|would\s+like|['’]d\s+like)\s+to)\s+"
    r"list(?:\s+out)?\s+(?:(?:a|some|the|my)\s+)?"
    r"(?:few\s+|several\s+)?(?:items|ideas|points|things)\b|"
    r"my\s+(?:feedback\s+)?(?:items|ideas|points)\s+are\b"
    r")",
    re.I,
)
LIST_MARKER_PATTERN = (
    r"one|first|two|second|three|third|four|fourth|five|fifth|"
    r"six|sixth|seven|seventh|eight|eighth|nine|ninth|ten|tenth"
)
NUMBERED_LIST_MARKER_RE = re.compile(
    r"\bhere(?:['’]s|\s+is)\s+(" + LIST_MARKER_PATTERN + r")\b",
    re.I,
)
PLAIN_NUMBERED_LIST_MARKER_RE = re.compile(
    r"([.!?:;,])\s*(?:and\s+)?"
    r"(" + LIST_MARKER_PATTERN + r")\b"
    r"(?:\s*[,;:–—-]\s*|\s+)",
    re.I,
)
LIST_SIGNAL_RE = re.compile(
    r"(?:"
    r"\b(?:here(?:['’]s|\s+is|\s+are)|these\s+are|my)\b.*"
    r"\b(?:feedback\s+)?(?:ideas|items|points|things)|"
    r"\b(?:make|create|start|write|format)\s+(?:a|the|this)\s+list|"
    r"\b(?:so\s+)?listing|"
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:ideas|items|points|things)"
    r")\s*$",
    re.I,
)
LIST_NUMBER = {
    word: index
    for index, pair in enumerate((
        ("one", "first"), ("two", "second"), ("three", "third"),
        ("four", "fourth"), ("five", "fifth"), ("six", "sixth"),
        ("seven", "seventh"), ("eight", "eighth"),
        ("nine", "ninth"), ("ten", "tenth"),
    ), start=1)
    for word in pair
}
COUNTED_LIST_SIZE = {
    "two": 2, "three": 3, "four": 4, "five": 5,
}
COUNTED_LIST_RE = re.compile(
    r"^(?P<header>(?P<count>two|three|four|five)\s+"
    r"(?:things|points|items|ideas))\s+",
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
class RecognitionWord:
    """Word-level evidence retained without coupling core logic to an ASR SDK."""
    text: str
    start: float = 0.0
    end: float = 0.0
    confidence: float = 0.5
    timing: str = "native"


@dataclass
class Recognition:
    text: str
    confidence: float = 1.0
    alternative: str | None = None
    verified: bool = False
    engine: str = ""
    words: tuple[RecognitionWord, ...] = ()
    audio_duration: float = 0.0
    native_processing_s: float | None = None


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
    """Merge stable and ephemeral vocabulary into one closed Whisper prompt.

    ``max_chars`` is a character stand-in for Whisper's ~224-token prompt
    ceiling and is only a fair proxy at Latin's ~3 characters per token.
    Callers dictating a dense script pass a proportionally smaller budget;
    see ``glossary_char_budget`` in dictate.py.
    """
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


def _format_numbered_list_markers(text: str) -> str | None:
    """Format explicit numbered-item speech without an LLM."""
    if "\n- " in text:
        return None
    markers = [
        (match, match.group(1), match.start())
        for match in NUMBERED_LIST_MARKER_RE.finditer(text)
    ]
    if len(markers) < 2:
        markers = [
            (match, match.group(2), match.end())
            for match in PLAIN_NUMBERED_LIST_MARKER_RE.finditer(text)
        ]
    if len(markers) < 2:
        return None
    numbers = [LIST_NUMBER[number.casefold()]
               for _match, number, _item_start in markers]
    if numbers != list(range(1, len(markers) + 1)):
        return None
    header = re.sub(
        r"[\s,;:.!?–—-]+$", "", text[:markers[0][0].start()]).strip()
    if not header or not LIST_SIGNAL_RE.search(header):
        return None

    items: list[str] = []
    for index, (_marker, _number, item_start) in enumerate(markers):
        end = markers[index + 1][0].start() \
            if index + 1 < len(markers) else len(text)
        item = text[item_start:end]
        if index + 1 < len(markers):
            item = re.sub(
                r"[\s,;:–—-]+(?:and\s+)?$", "", item, flags=re.I)
        item = item.strip(" \t,;:–—-")
        if not item:
            return None
        item = item[:1].upper() + item[1:]
        if item[-1] not in ".!?…":
            item += "."
        items.append(item)

    if header[-1] not in ".!?…:":
        header += ":"
    return header + "\n" + "\n".join(f"- {item}" for item in items)


def _format_counted_inline_list(text: str) -> str | None:
    """Format an exact counted header plus sequential inline ordinals."""
    header_match = COUNTED_LIST_RE.match(text)
    if header_match is None:
        return None
    expected = COUNTED_LIST_SIZE[header_match.group("count").casefold()]
    markers = [
        match for match in re.finditer(
            r"\b(?:first|second|third|fourth|fifth)\b", text, re.I)
        if match.start() >= header_match.end()
    ]
    numbers = [LIST_NUMBER[match.group(0).casefold()] for match in markers]
    if numbers != list(range(1, expected + 1)):
        return None
    items = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() \
            if index + 1 < len(markers) else len(text)
        item = text[marker.end():end].strip(" \t,;:–—-")
        if index + 1 < len(markers):
            item = re.sub(r"\s+and\s*$", "", item, flags=re.I).rstrip()
        if not item:
            return None
        item = item[:1].upper() + item[1:]
        if item[-1] not in ".!?…":
            item += "."
        items.append(item)
    header = header_match.group("header")
    return header + ":\n" + "\n".join(f"- {item}" for item in items)


def _has_ambiguous_filler(text: str) -> bool:
    literal_free = LITERAL_FILLER_RE.sub("", text)
    return bool(AMBIGUOUS_FILLER_RE.search(literal_free))


# Scripts written without inter-word spaces. Whitespace is the one cleanup
# rule that survives translation, and even it changes shape here.
SPACELESS_SCRIPTS = frozenset({"ja", "zh"})


def normalize_spacing(text: str, spaced: bool = True) -> str:
    """The only whitespace rule that holds in every supported language.

    Collapsing runs of horizontal space and tidying line breaks is safe
    everywhere. Closing the gap before ASCII sentence punctuation is applied
    only where that punctuation is what the language actually writes; a script
    that ends sentences with a different mark keeps its own spacing.
    """
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    if spaced:
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def compile_cleanup(raw: str, language: str = "en") -> CleanupPlan:
    """Compile safe spoken transformations into explicit, reversible edits.

    Every rule below is English by construction: the filler and
    self-correction patterns are English words, "scratch that" is an English
    phrase whose length is baked into the offset arithmetic, and the list
    formatter reads English cardinals and ordinals. Applied to another
    language they cannot help and can silently delete real words, so a
    non-English utterance gets whitespace normalization and nothing else.
    """
    code = str(language or "en").strip().casefold()
    if code != "en":
        return CleanupPlan(
            text=normalize_spacing(
                raw.strip(), spaced=code not in SPACELESS_SCRIPTS),
            edits=[],
            needs_semantic_cleanup=False,
        )
    text = raw.strip()
    edits: list[CleanupEdit] = []

    # Expand spoken line boundaries first so discourse fillers at the start of
    # a newly dictated line can be distinguished from meaningful prose.
    had_spoken_structure = bool(STRUCTURE_RE.search(text))
    if had_spoken_structure:
        before = text
        text = STRUCTURE_RE.sub(
            lambda match: "\n" if match.group(1).lower() == "line" else "\n\n",
            text,
        )
        edits.append(CleanupEdit("spoken_structure", before, text))

    for regex, kind in (
        (SIMPLE_FILLER_RE, "remove_filler"),
        (DISCOURSE_FILLER_RE, "remove_discourse_filler"),
    ):
        matches = [match.group(0) for match in regex.finditer(text)]
        if matches:
            before = text
            text = regex.sub(" ", text)
            edits.append(CleanupEdit(kind, before, text))

    while True:
        match = CORRECTION_RE.search(text)
        if not match:
            break
        before = text
        text = text[:match.start()] + match.group(2) + text[match.end():]
        edits.append(CleanupEdit("self_correction", before, text))

    lowered = text.casefold()
    scratch_needs_semantic = False
    scratch_at = lowered.rfind("scratch that")
    if scratch_at >= 0:
        before = text
        left, right = text[:scratch_at].rstrip(), text[scratch_at + 12:].lstrip()
        sentence = max(left.rfind("."), left.rfind("!"), left.rfind("?"),
                       left.rfind("\n"))
        clause = max(left.rfind(","), left.rfind(";"), left.rfind("—"))
        boundary = max(sentence + 1, clause + 1)
        if boundary:
            text = (left[:boundary].rstrip() + " " + right).strip()
            edits.append(CleanupEdit("scratch_that", before, text))
        else:
            right_word = re.match(r"[A-Za-z0-9_'’-]+", right)
            repeated = tuple(re.finditer(
                rf"\b{re.escape(right_word.group(0))}\b", left, re.I)) \
                if right_word else ()
            # Without a repeated boundary, preserve the source and route it
            # to semantic cleanup instead of guessing how much context to delete.
            if repeated:
                cut = repeated[-1].start()
                text = (left[:cut].rstrip() + " " + right).strip()
                edits.append(CleanupEdit("scratch_that", before, text))
            else:
                scratch_needs_semantic = True

    text = normalize_spacing(text)
    deterministic_list = (
        _format_numbered_list_markers(text)
        or _format_counted_inline_list(text))
    if deterministic_list is not None:
        before = text
        text = deterministic_list
        edits.append(CleanupEdit("spoken_enumeration", before, text))
    needs_semantic = scratch_needs_semantic or _has_ambiguous_filler(text)
    if deterministic_list is None:
        ordinal_markers = re.findall(
            r"\b(?:first|second|third|lastly)\b", text, re.I)
        needs_semantic = needs_semantic or bool(
            LIST_INTENT_RE.search(text)
            or (not had_spoken_structure and len(ordinal_markers) >= 2)
            or re.search(
                r"\b(?:two|three|four|five) "
                r"(?:things|points|items|ideas)\b",
                text,
                re.I,
            ))
    return CleanupPlan(text=text, edits=edits,
                       needs_semantic_cleanup=needs_semantic)


# Spoken edit commands: a closed, whole-utterance grammar. A phrase only counts
# when the entire normalized utterance is exactly a command, so a lone spoken
# command can act on already-dictated text while ordinary prose that merely
# contains the words ("lets scratch that plan") flows through as normal
# dictation. This classifier stays pure; the keyboard and text effects live in
# dictate.py's dispatcher.
EDIT_COMMAND_UNDO = "undo"
EDIT_COMMAND_DELETE_WORD = "delete_word"
EDIT_COMMAND_DELETE_SENTENCE = "delete_sentence"
EDIT_COMMAND_NEWLINE = "newline"
EDIT_COMMAND_NEWPARAGRAPH = "newparagraph"
EDIT_COMMAND_UPPERCASE_LAST = "uppercase_last"
EDIT_COMMAND_CAPITALIZE_LAST = "capitalize_last"
EDIT_COMMAND_LOWERCASE_LAST = "lowercase_last"

_EDIT_COMMAND_PHRASES = {
    "scratch that": EDIT_COMMAND_UNDO,
    "undo that": EDIT_COMMAND_UNDO,
    "undo": EDIT_COMMAND_UNDO,
    "delete last word": EDIT_COMMAND_DELETE_WORD,
    "delete last sentence": EDIT_COMMAND_DELETE_SENTENCE,
    "delete that": EDIT_COMMAND_DELETE_SENTENCE,
    "new line": EDIT_COMMAND_NEWLINE,
    "new paragraph": EDIT_COMMAND_NEWPARAGRAPH,
    "all caps": EDIT_COMMAND_UPPERCASE_LAST,
    "uppercase that": EDIT_COMMAND_UPPERCASE_LAST,
    "capitalize that": EDIT_COMMAND_CAPITALIZE_LAST,
    "lowercase that": EDIT_COMMAND_LOWERCASE_LAST,
}


def classify_edit_command(raw: str) -> str | None:
    """Classify a whole utterance as one closed-set spoken edit command.

    Normalizes with the same rule as dictate.py's execute_voice_command, then
    matches the entire normalized string against a fixed phrase table. A command
    word embedded in a longer utterance never matches, so ordinary dictation is
    untouched. No inference: anything outside the closed set returns None.
    """
    if not isinstance(raw, str):
        return None
    normalized = re.sub(r"[^a-z ]", "", raw.casefold()).strip()
    return _EDIT_COMMAND_PHRASES.get(normalized)


def _uppercase_first_cased(text: str) -> str:
    """Uppercase the first cased character, leaving everything else untouched.

    Sentence-style, not title-case: only the first character that actually has a
    case distinction changes, so leading spaces, quotes, or digits are skipped
    and the remainder keeps whatever casing the speaker already produced.
    """
    for index, char in enumerate(text):
        if char.upper() != char.lower():
            return text[:index] + char.upper() + text[index + 1:]
    return text


def transform_last_insertion(command: str, text: str) -> str | None:
    """Apply a spoken case command to the exact last-inserted text.

    UPPERCASE_LAST upper-cases; LOWERCASE_LAST lower-cases; CAPITALIZE_LAST
    upper-cases only the first cased character. Returns the rewritten text, or
    None when the command is not a case transform or when applying it would not
    change anything. A None result tells the caller to issue no keystroke at
    all, so a case command spoken over text already in that case never triggers
    a destructive in-place edit.
    """
    if command == EDIT_COMMAND_UPPERCASE_LAST:
        transformed = text.upper()
    elif command == EDIT_COMMAND_LOWERCASE_LAST:
        transformed = text.lower()
    elif command == EDIT_COMMAND_CAPITALIZE_LAST:
        transformed = _uppercase_first_cased(text)
    else:
        return None
    return transformed if transformed != text else None


CODE_PHRASES = (
    (r"\bopen paren(?:thesis)?\b", "("),
    (r"\bclose paren(?:thesis)?\b", ")"),
    (r"\bopen bracket\b", "["),
    (r"\bclose bracket\b", "]"),
    (r"\bopen brace\b", "{"),
    (r"\bclose brace\b", "}"),
    (r"\bdouble quote\b", '"'),
    (r"\bsingle quote\b", "'"),
    (r"\bsemicolon\b", ";"),
    (r"\bcolon\b", ":"),
    (r"\barrow\b", "->"),
    (r"\bequals\b", "="),
    (r"\bunderscore\b", "_"),
)


def compile_code_dictation(raw: str, language: str = "en") -> CleanupPlan:
    """Compile spoken code punctuation while preserving ordinary identifiers.

    CODE_PHRASES are English names for ASCII glyphs, and the spacing rules
    below are Latin code conventions, so a non-English utterance keeps
    ``compile_cleanup``'s language-safe result untouched.
    """
    plan = compile_cleanup(raw, language)
    if str(language or "en").strip().casefold() != "en":
        return plan
    text = plan.text
    edits = list(plan.edits)
    for pattern, token in CODE_PHRASES:
        if re.search(pattern, text, re.I):
            before = text
            text = re.sub(pattern, token, text, flags=re.I)
            edits.append(CleanupEdit("spoken_code_token", before, text))
    text = re.sub(r"\s+([)\]}:,;])", r"\1", text)
    text = re.sub(r"\s+([(\[{])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s*=\s*", " = ", text)
    text = re.sub(r"\s*->\s*", " -> ", text)
    return CleanupPlan(
        text=text.strip(), edits=edits,
        needs_semantic_cleanup=plan.needs_semantic_cleanup,
    )


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


AGREEMENT_TOKEN_RE = re.compile(r"[\w']+")

# Parakeet exposes no calibrated confidence, so the route confidence is
# derived from cross-engine agreement with an independent Whisper Tiny decode
# of the same audio.  The linear map is anchored to the two runtime gates it
# must drive:
#   * context-candidate rewrites unlock below 0.70
#     (CONTEXT_REWRITE_MAX_CONFIDENCE), which this map crosses when
#     agreement drops under ~0.52 — engines that mostly agree keep the
#     conservative no-rewrite behavior;
#   * the low-confidence region below 0.52 (LOW_CONFIDENCE) is reached only
#     under ~0.15 agreement, i.e. the engines heard different utterances.
# Full agreement lands near the previous fixed routing prior so downstream
# thresholds keep their measured behavior.
PARAKEET_AGREEMENT_FLOOR = 0.45
PARAKEET_AGREEMENT_CEILING = 0.93
# Escalate to the independent fallback recognizer only when the engines
# disagree badly and the audio is short enough that a Turbo decode cannot
# stall the paste path (Turbo measured ~4.4x realtime).
ESCALATION_MAX_AGREEMENT = 0.35
ESCALATION_MAX_SECONDS = 12.0


def hypothesis_agreement(primary: str, secondary: str) -> float:
    """Token-level agreement between two hypotheses of the same audio, 0..1.

    Case-insensitive over every word-shaped token (short words and numbers
    included — dropping them would hide real disagreement).  Matched-token
    count is normalized by the longer hypothesis so insertions and deletions
    count against agreement, not just substitutions.
    """
    a = [t.casefold() for t in AGREEMENT_TOKEN_RE.findall(primary or "")]
    b = [t.casefold() for t in AGREEMENT_TOKEN_RE.findall(secondary or "")]
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / max(len(a), len(b))


def parakeet_confidence_from_agreement(agreement: float) -> float:
    """Map cross-engine agreement to a route confidence for Parakeet."""
    agreement = min(1.0, max(0.0, float(agreement)))
    return PARAKEET_AGREEMENT_FLOOR + (
        PARAKEET_AGREEMENT_CEILING - PARAKEET_AGREEMENT_FLOOR) * agreement


def should_escalate_uncertain(agreement: float, duration_s: float) -> bool:
    """Whether disagreement warrants one independent fallback decode."""
    return (float(agreement) < ESCALATION_MAX_AGREEMENT
            and 0.0 < float(duration_s) <= ESCALATION_MAX_SECONDS)


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


def recognition_words_from_segments(
        segments: Iterable[dict]) -> tuple[RecognitionWord, ...]:
    """Normalize SDK word evidence, interpolating only when it is absent.

    MLX Whisper and faster-whisper expose slightly different segment/word
    shapes.  The runtime converts those shapes to dictionaries before calling
    this dependency-free seam.  Segment interpolation preserves useful timing
    without enabling a slower alignment pass on the latency-critical path.
    """
    evidence: list[RecognitionWord] = []
    for segment in segments or []:
        segment_confidence = confidence_from_segments([segment])
        sdk_words = segment.get("words") or []
        for word in sdk_words:
            text = str(word.get("word", word.get("text", ""))).strip()
            if not text:
                continue
            probability = word.get("probability", segment_confidence)
            confidence = float(probability) \
                if isinstance(probability, (int, float)) \
                else segment_confidence
            evidence.append(RecognitionWord(
                text=text,
                start=float(word.get("start", 0.0) or 0.0),
                end=float(word.get("end", 0.0) or 0.0),
                confidence=max(0.0, min(1.0, confidence)),
                timing="native",
            ))
        if sdk_words:
            continue
        tokens = str(segment.get("text", "")).split()
        if not tokens:
            continue
        start = float(segment.get("start", 0.0) or 0.0)
        end = float(segment.get("end", start) or start)
        width = max(0.0, end - start) / len(tokens)
        evidence.extend(RecognitionWord(
            text=token,
            start=start + index * width,
            end=start + (index + 1) * width,
            confidence=segment_confidence,
            timing="segment",
        ) for index, token in enumerate(tokens))
    return tuple(evidence)


def mode_from_modifiers(shift: bool, command: bool, control: bool) -> str:
    """Map explicit modifier contracts to one discoverable voice mode."""
    if command and control:
        return "command"
    if shift and control:
        return "code"
    if command:
        return "edit"
    if control:
        return "reply"
    if shift:
        return "compose"
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
