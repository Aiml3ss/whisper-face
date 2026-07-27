"""Double Metaphone codes for acoustic token matching.

``voice_compiler.phonetic_similarity`` decides whether a token visible on
screen could plausibly be what the speaker said ("Gwen" for the visible
model name "Qwen3_5").  That gate needs an encoding that understands
English spelling: silent letters (knight, wright), digraphs (ph, sch,
th, gh), and letters whose sound depends on their neighbours (c, g, x).

This is Lawrence Philips' Double Metaphone (2000), implemented from the
published algorithm with no dependencies.  Every word yields a primary
and a secondary code so alternate pronunciations still meet: "Schmidt"
(XMT/SMT) crosses "Smith" (SM0/XMT) on XMT.

Deliberate deviations for dictation tokens rather than surnames:

* Codes are capped at 16 characters instead of the classic 4, because
  identifier-length tokens need the extra discrimination.
* Digits and identifier joiners (``_ . / + -``) pass through unchanged:
  ``Qwen3_5`` encodes to ``KN3_5`` and never collides with ``Qwen2_5``.
  Apostrophes are silent and simply dropped; any other separator ends
  the current word without leaving a mark.
* A tiny table of famous irregular pronunciations is applied per word
  before encoding.  Metaphone works on spelling and cannot recover
  "colonel" being spoken as "kernel" on its own.
"""

from __future__ import annotations

from functools import lru_cache


_VOWELS = frozenset("AEIOUY")
_PASSTHROUGH = frozenset("0123456789_./+-")
_IRREGULAR_PRONUNCIATIONS = {"colonel": "kernel"}
_ACCENT_FOLD = str.maketrans({
    "à": "a", "á": "a", "â": "a", "ä": "a", "ã": "a", "å": "a",
    "è": "e", "é": "e", "ê": "e", "ë": "e",
    "ì": "i", "í": "i", "î": "i", "ï": "i",
    "ò": "o", "ó": "o", "ô": "o", "ö": "o", "õ": "o",
    "ù": "u", "ú": "u", "û": "u", "ü": "u",
    "ç": "s", "ñ": "n",
})
_MAX_CODE = 16


@lru_cache(maxsize=2048)
def double_metaphone(value: str) -> tuple[str, str]:
    """Primary and secondary phonetic codes for one dictation token."""
    cleaned = value.casefold().translate(_ACCENT_FOLD)
    primary: list[str] = []
    secondary: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if not run:
            return
        word = "".join(run)
        run.clear()
        word = _IRREGULAR_PRONUNCIATIONS.get(word, word)
        first, second = _encode_word(word.upper())
        primary.append(first)
        secondary.append(second)

    for char in cleaned:
        if "a" <= char <= "z":
            run.append(char)
        elif char in _PASSTHROUGH:
            flush()
            primary.append(char)
            secondary.append(char)
        elif char != "'":
            flush()
    flush()
    return "".join(primary)[:_MAX_CODE], "".join(secondary)[:_MAX_CODE]


def _encode_word(word: str) -> tuple[str, str]:
    """Philips' Double Metaphone for one uppercase A-Z word."""
    length = len(word)
    if not length:
        return "", ""
    last = length - 1
    # Trailing padding keeps every fixed-width lookahead comparison safe,
    # exactly like the original algorithm's space-padded buffer.
    padded = word + "      "
    slavo = "W" in word or "K" in word or "CZ" in word or "WITZ" in word
    primary: list[str] = []
    secondary: list[str] = []

    def add(pri: str, sec: str | None = None) -> None:
        primary.append(pri)
        secondary.append(pri if sec is None else sec)

    def seg(start: int, count: int) -> str:
        if start < 0:
            return ""
        return padded[start:start + count]

    def vowel(index: int) -> bool:
        return 0 <= index < length and word[index] in _VOWELS

    current = 0
    if seg(0, 2) in ("GN", "KN", "PN", "WR", "PS"):
        current = 1
    if word[0] == "X":
        add("S")
        current = 1

    while current < length:
        char = word[current]

        if char in _VOWELS:
            if current == 0:
                add("A")
            current += 1

        elif char == "B":
            add("P")
            current += 2 if seg(current + 1, 1) == "B" else 1

        elif char == "C":
            if current > 1 and not vowel(current - 2) \
                    and seg(current - 1, 3) == "ACH" \
                    and seg(current + 2, 1) != "I" \
                    and (seg(current + 2, 1) != "E"
                         or seg(current - 2, 6) in ("BACHER", "MACHER")):
                add("K")
                current += 2
            elif current == 0 and seg(current, 6) == "CAESAR":
                add("S")
                current += 2
            elif seg(current, 4) == "CHIA":
                add("K")
                current += 2
            elif seg(current, 2) == "CH":
                if current > 0 and seg(current, 4) == "CHAE":
                    add("K", "X")
                elif current == 0 \
                        and (seg(current + 1, 5) in ("HARAC", "HARIS")
                             or seg(current + 1, 3) in
                             ("HOR", "HYM", "HIA", "HEM")) \
                        and seg(0, 5) != "CHORE":
                    add("K")
                elif seg(0, 4) in ("VAN ", "VON ") or seg(0, 3) == "SCH" \
                        or seg(current - 2, 6) in \
                        ("ORCHES", "ARCHIT", "ORCHID") \
                        or seg(current + 2, 1) in ("T", "S") \
                        or ((seg(current - 1, 1) in ("A", "O", "U", "E")
                             or current == 0)
                            and seg(current + 2, 1) in
                            ("L", "R", "N", "M", "B", "H", "F", "V", "W",
                             " ")):
                    add("K")
                elif current > 0:
                    if seg(0, 2) == "MC":
                        add("K")
                    else:
                        add("X", "K")
                else:
                    add("X")
                current += 2
            elif seg(current, 2) == "CZ" \
                    and seg(current - 2, 4) != "WICZ":
                add("S", "X")
                current += 2
            elif seg(current + 1, 3) == "CIA":
                add("X")
                current += 3
            elif seg(current, 2) == "CC" \
                    and not (current == 1 and word[0] == "M"):
                if seg(current + 2, 1) in ("I", "E", "H") \
                        and seg(current + 2, 2) != "HU":
                    if (current == 1 and seg(current - 1, 1) == "A") \
                            or seg(current - 1, 5) in ("UCCEE", "UCCES"):
                        add("KS")
                    else:
                        add("X")
                    current += 3
                else:
                    add("K")
                    current += 2
            elif seg(current, 2) in ("CK", "CG", "CQ"):
                add("K")
                current += 2
            elif seg(current, 2) in ("CI", "CE", "CY"):
                if seg(current, 3) in ("CIO", "CIE", "CIA"):
                    add("S", "X")
                else:
                    add("S")
                current += 2
            else:
                add("K")
                if seg(current + 1, 2) in (" C", " Q", " G"):
                    current += 3
                elif seg(current + 1, 1) in ("C", "K", "Q") \
                        and seg(current + 1, 2) not in ("CE", "CI"):
                    current += 2
                else:
                    current += 1

        elif char == "D":
            if seg(current, 2) == "DG":
                if seg(current + 2, 1) in ("I", "E", "Y"):
                    add("J")
                    current += 3
                else:
                    add("TK")
                    current += 2
            elif seg(current, 2) in ("DT", "DD"):
                add("T")
                current += 2
            else:
                add("T")
                current += 1

        elif char == "F":
            add("F")
            current += 2 if seg(current + 1, 1) == "F" else 1

        elif char == "G":
            if seg(current + 1, 1) == "H":
                if current > 0 and not vowel(current - 1):
                    add("K")
                elif current == 0:
                    if seg(current + 2, 1) == "I":
                        add("J")
                    else:
                        add("K")
                elif (current > 1 and word[current - 2] in "BHD") \
                        or (current > 2 and word[current - 3] in "BHD") \
                        or (current > 3 and word[current - 4] in "BH"):
                    pass
                elif current > 2 and word[current - 1] == "U" \
                        and word[current - 3] in "CGLRT":
                    add("F")
                elif current > 0 and word[current - 1] != "I":
                    add("K")
                current += 2
            elif seg(current + 1, 1) == "N":
                if current == 1 and vowel(0) and not slavo:
                    add("KN", "N")
                elif seg(current + 2, 2) != "EY" \
                        and seg(current + 1, 1) != "Y" and not slavo:
                    add("N", "KN")
                else:
                    add("KN")
                current += 2
            elif seg(current + 1, 2) == "LI" and not slavo:
                add("KL", "L")
                current += 2
            elif current == 0 \
                    and (seg(current + 1, 1) == "Y"
                         or seg(current + 1, 2) in
                         ("ES", "EP", "EB", "EL", "EY", "IB", "IL", "IN",
                          "IE", "EI", "ER")):
                add("K", "J")
                current += 2
            elif (seg(current + 1, 2) == "ER"
                  or seg(current + 1, 1) == "Y") \
                    and seg(0, 6) not in ("DANGER", "RANGER", "MANGER") \
                    and seg(current - 1, 1) not in ("E", "I") \
                    and seg(current - 1, 3) not in ("RGY", "OGY"):
                add("K", "J")
                current += 2
            elif seg(current + 1, 1) in ("E", "I", "Y") \
                    or seg(current - 1, 4) in ("AGGI", "OGGI"):
                if seg(0, 4) in ("VAN ", "VON ") or seg(0, 3) == "SCH" \
                        or seg(current + 1, 2) == "ET":
                    add("K")
                elif seg(current + 1, 4) == "IER ":
                    add("J")
                else:
                    add("J", "K")
                current += 2
            else:
                add("K")
                current += 2 if seg(current + 1, 1) == "G" else 1

        elif char == "H":
            if (current == 0 or vowel(current - 1)) and vowel(current + 1):
                add("H")
                current += 2
            else:
                current += 1

        elif char == "J":
            if seg(current, 4) == "JOSE" or seg(0, 4) == "SAN ":
                if (current == 0 and seg(current + 4, 1) == " ") \
                        or seg(0, 4) == "SAN ":
                    add("H")
                else:
                    add("J", "H")
                current += 1
            else:
                if current == 0:
                    add("J", "A")
                elif vowel(current - 1) and not slavo \
                        and seg(current + 1, 1) in ("A", "O"):
                    add("J", "H")
                elif current == last:
                    add("J", "")
                elif seg(current + 1, 1) not in \
                        ("L", "T", "K", "S", "N", "M", "B", "Z") \
                        and seg(current - 1, 1) not in ("S", "K", "L"):
                    add("J")
                current += 2 if seg(current + 1, 1) == "J" else 1

        elif char == "K":
            add("K")
            current += 2 if seg(current + 1, 1) == "K" else 1

        elif char == "L":
            if seg(current + 1, 1) == "L":
                if (current == length - 3
                        and seg(current - 1, 4) in
                        ("ILLO", "ILLA", "ALLE")) \
                        or ((seg(last - 1, 2) in ("AS", "OS")
                             or seg(last, 1) in ("A", "O"))
                            and seg(current - 1, 4) == "ALLE"):
                    add("L", "")
                else:
                    add("L")
                current += 2
            else:
                add("L")
                current += 1

        elif char == "M":
            if (seg(current - 1, 3) == "UMB"
                    and (current + 1 == last
                         or seg(current + 2, 2) == "ER")) \
                    or seg(current + 1, 1) == "M":
                current += 2
            else:
                current += 1
            add("M")

        elif char == "N":
            add("N")
            current += 2 if seg(current + 1, 1) == "N" else 1

        elif char == "P":
            if seg(current + 1, 1) == "H":
                add("F")
                current += 2
            else:
                add("P")
                current += 2 if seg(current + 1, 1) in ("P", "B") else 1

        elif char == "Q":
            add("K")
            current += 2 if seg(current + 1, 1) == "Q" else 1

        elif char == "R":
            if current == last and not slavo \
                    and seg(current - 2, 2) == "IE" \
                    and seg(current - 4, 2) not in ("ME", "MA"):
                add("", "R")
            else:
                add("R")
            current += 2 if seg(current + 1, 1) == "R" else 1

        elif char == "S":
            if seg(current - 1, 3) in ("ISL", "YSL"):
                current += 1
            elif current == 0 and seg(current, 5) == "SUGAR":
                add("X", "S")
                current += 1
            elif seg(current, 2) == "SH":
                if seg(current + 1, 4) in \
                        ("HEIM", "HOEK", "HOLM", "HOLZ"):
                    add("S")
                else:
                    add("X")
                current += 2
            elif seg(current, 3) in ("SIO", "SIA") \
                    or seg(current, 4) == "SIAN":
                if not slavo:
                    add("S", "X")
                else:
                    add("S")
                current += 3
            elif (current == 0
                  and seg(current + 1, 1) in ("M", "N", "L", "W")) \
                    or seg(current + 1, 1) == "Z":
                add("S", "X")
                current += 2 if seg(current + 1, 1) == "Z" else 1
            elif seg(current, 2) == "SC":
                if seg(current + 2, 1) == "H":
                    if seg(current + 3, 2) in \
                            ("OO", "ER", "EN", "UY", "ED", "EM"):
                        if seg(current + 3, 2) in ("ER", "EN"):
                            add("X", "SK")
                        else:
                            add("SK")
                    elif current == 0 and not vowel(3) \
                            and seg(3, 1) != "W":
                        add("X", "S")
                    else:
                        add("X")
                elif seg(current + 2, 1) in ("I", "E", "Y"):
                    add("S")
                else:
                    add("SK")
                current += 3
            else:
                if current == last \
                        and seg(current - 2, 2) in ("AI", "OI"):
                    add("", "S")
                else:
                    add("S")
                current += 2 if seg(current + 1, 1) in ("S", "Z") else 1

        elif char == "T":
            if seg(current, 4) == "TION":
                add("X")
                current += 3
            elif seg(current, 3) in ("TIA", "TCH"):
                add("X")
                current += 3
            elif seg(current, 2) == "TH" or seg(current, 3) == "TTH":
                if seg(current + 2, 2) in ("OM", "AM") \
                        or seg(0, 4) in ("VAN ", "VON ") \
                        or seg(0, 3) == "SCH":
                    add("T")
                else:
                    add("0", "T")
                current += 2
            else:
                add("T")
                current += 2 if seg(current + 1, 1) in ("T", "D") else 1

        elif char == "V":
            add("F")
            current += 2 if seg(current + 1, 1) == "V" else 1

        elif char == "W":
            if seg(current, 2) == "WR":
                add("R")
                current += 2
            elif current == 0 \
                    and (vowel(current + 1) or seg(current, 2) == "WH"):
                if vowel(current + 1):
                    add("A", "F")
                else:
                    add("A")
                current += 1
            elif (current == last and vowel(current - 1)) \
                    or seg(current - 1, 5) in \
                    ("EWSKI", "EWSKY", "OWSKI", "OWSKY") \
                    or seg(0, 3) == "SCH":
                add("", "F")
                current += 1
            elif seg(current, 4) in ("WICZ", "WITZ"):
                add("TS", "FX")
                current += 4
            else:
                current += 1

        elif char == "X":
            if not (current == last
                    and (seg(current - 3, 3) in ("IAU", "EAU")
                         or seg(current - 2, 2) in ("AU", "OU"))):
                add("KS")
            current += 2 if seg(current + 1, 1) in ("C", "X") else 1

        elif char == "Z":
            if seg(current + 1, 1) == "H":
                add("J")
                current += 2
            else:
                if seg(current + 1, 2) in ("ZO", "ZI", "ZA") \
                        or (slavo and current > 0
                            and word[current - 1] != "T"):
                    add("S", "TS")
                else:
                    add("S")
                current += 2 if seg(current + 1, 1) == "Z" else 1

        else:
            current += 1

    return "".join(primary), "".join(secondary)
