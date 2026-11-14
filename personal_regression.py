"""Local, platform-independent regression gate for learned corrections.

The lab retains only the exact corrected span and its optional application
scope. It deliberately knows nothing about audio, surrounding document text,
recognition engines, UI frameworks, or persistence locations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping

from shadow_candidate_gate import (
    CandidateDisposition,
    CandidateKind,
    ShadowCandidateGate,
    ShadowRegressionCase,
)


SCHEMA_VERSION = 1
MAX_CASES = 256
MAX_MAPPINGS = 128
MAX_QUARANTINED = 64
MAX_SPAN_CHARS = 80


# A learned mapping must never turn one common word into another. The
# repository carries no frequency list, so "common" is this built-in set: the
# few hundred most frequent English words (function words, contractions, the
# everyday verbs in their usual forms, plain nouns, adjectives and adverbs),
# day, month and number words, and the usual homophone sets. A correction
# between two of them is a choice the sentence makes ("there"/"their",
# "want"/"won't", "got"/"get"), not a stable mishearing, so no context-free
# rule may be built from it. Words are compared without their apostrophes, so
# "cant" and "wont" count as the contractions they are. A mapping that adds or
# removes a negation is refused whatever the other word is. Words outside the
# set rely on evidence instead: a revert demotes the mapping, and pastes of
# the heard word that were left alone, or sent as they were, count against
# promoting it.
def _plain(word: str) -> str:
    return word.casefold().replace("\u2019", "'").replace("'", "")


COMMON_WORDS = frozenset(_plain(word) for word in """
a an the and or nor but if so as at by for from in into of off on onto out
over under up down with without about after before between through during
until while than then that this these those there their theirs they them
they're they'll they've they'd there's he she it its it's him his her hers
he's he'll he'd she's she'll she'd we us our ours we're we'll we've we'd
you your yours you're you'll you've you'd i me my mine i'm i'll i've i'd
who whom whose who's what what's when where which why how is are am was
were be been being isn't aren't wasn't weren't do does did done doing
don't doesn't didn't have has had having haven't hasn't hadn't will would
shall should can could may might must won't wouldn't shouldn't can't
cannot couldn't mustn't ain't shan't needn't mightn't not no yes know
knows knew known new now here hear hears heard herd here's that's let's
lets all any some none each every both either neither more most much many
few less least other another such only own same very just also even still
again ever never always often well good right write rite too to two for
four fore one won ate eight buy bye by be bee see sea sees seas weather
whether wear where affect effect accept except lose loose lost past passed
piece peace break brake whole hole hour meet meat met week weak made maid
sent cent scent son sun wait weight way weigh wood threw plain plane
principal principle quiet quite lead led read reads red allowed aloud
advice advise site sight cite pair pear bare bear fair fare mail male sale
sail tail tale steal steel role roll sole soul great grate guessed guest
knight night knot knows nose some sum than seem seam stair stare toe tow
waist waste which witch die dye due dew cell sell board bored band banned
base bass been bean blew blue course coarse council counsel desert dessert
forth fourth lessen lesson loan lone mind mined scene seen so sew sow
shore sure sweet suite vary wave waive real reel road rode root route
say says said saying get gets got getting gotten make makes making go goes
went gone going take takes took taken taking saw seeing come comes came
coming think thinks thought thinking look looks looked looking want wants
wanted wanting give gives gave given giving use uses used using find finds
found finding tell tells told telling ask asks asked asking work works
worked working seems seemed feel feels felt feeling try tries tried trying
leave leaves left leaving call calls called calling need needs needed like
likes liked put puts putting mean means meant keep keeps kept keeping let
begin began begun help helps helped helping talk talks talked talking turn
turns turned start starts started starting show shows showed shown play
plays played run runs ran running move moves moved moving live lives lived
believe hold holds held bring brings brought happen happens happened
writes wrote written writing sit sat stand stood pay paid set sets learn
learned change changes changed understand understood watch follow stop
stopped create speak spoke allow add added spend spent grow open opened
walk win offer remember love consider appear bought send expect build
built stay fall fell cut reach remain check share
time times year years people person way ways day days man men woman women
thing things child children world life hand hands part place places case
weeks point home room side house morning name question questions end fact
lot kind line word words number numbers problem problems idea plan team
job money something anything nothing everything someone anyone everyone
nobody nowhere
better best bad worse worst old big small large little long short high
low next last early late young important different able true free full
easy hard clear ready possible
away back really already almost sometimes maybe perhaps soon later yet
together around enough once twice rather ok okay please thanks thank hello
hi hey sorry
monday tuesday wednesday thursday friday saturday sunday january february
march april june july august september october november december zero
three five six seven nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty
seventy eighty ninety hundred thousand million first second third today
tonight tomorrow yesterday
""".split())
COMMON_WORDS_REASON = "common words are not remapped"
_NEGATIONS = frozenset(
    "not no nor never none neither nobody nothing nowhere cannot without "
    "cant wont dont isnt arent wasnt werent doesnt didnt havent hasnt hadnt "
    "wouldnt shouldnt couldnt mustnt aint shant neednt mightnt".split())
_NUMBER = re.compile(r"\d[\d.,:/%-]*")


def is_common_word(word: str) -> bool:
    return _plain(word) in COMMON_WORDS


def is_negation(word: str) -> bool:
    spelled = word.casefold().replace("\u2019", "'")
    return spelled.endswith("n't") or _plain(word) in _NEGATIONS


def remaps_common_words(heard: str, preferred: str) -> bool:
    """Whether a mapping would rewrite one common word, or number, as
    another, or would add or remove a negation."""
    if is_common_word(heard) and is_common_word(preferred):
        return True
    if is_negation(heard) != is_negation(preferred):
        return True
    return bool(_NUMBER.fullmatch(heard) and _NUMBER.fullmatch(preferred))


def _in_heard_case(found: str, mapping: "LearnedMapping") -> str:
    """Keep a capital the recognizer placed on an all-lowercase mapping."""
    preferred = mapping.preferred
    if (found[:1].isupper() and not mapping.heard[:1].isupper()
            and preferred == preferred.lower()):
        return preferred[:1].upper() + preferred[1:]
    return preferred


def _valid_span(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_SPAN_CHARS


def _valid_app(value: Any) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= 255)


def _case_id(heard: str, preferred: str, app: str | None) -> str:
    payload = json.dumps(
        [heard.casefold(), preferred, app], ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


@dataclass(frozen=True)
class CorrectionCase:
    """One exact changed span; no audio or surrounding document content."""

    id: str
    heard: str
    preferred: str
    app: str | None = None


@dataclass(frozen=True)
class LearnedMapping:
    """A Personal Prior mapping that passed every applicable case."""

    heard: str
    preferred: str
    app: str | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    """Inspectable outcome of evaluating a proposed Personal Prior mapping."""

    heard: str
    preferred: str
    app: str | None
    passed: bool
    promoted: bool
    reasons: tuple[str, ...] = ()
    case_ids: tuple[str, ...] = ()


class PersonalRegressionLab:
    """Record correction cases and gate learned mappings against them."""

    def __init__(self) -> None:
        self._cases: dict[str, CorrectionCase] = {}
        self._promoted: dict[tuple[str, str | None], LearnedMapping] = {}
        self._quarantined: list[CandidateEvaluation] = []

    @property
    def cases(self) -> tuple[CorrectionCase, ...]:
        return tuple(sorted(self._cases.values(), key=lambda case: case.id))

    @property
    def promoted(self) -> tuple[LearnedMapping, ...]:
        return tuple(sorted(
            self._promoted.values(),
            key=lambda item: ((item.app or ""), item.heard.casefold(),
                              item.preferred),
        ))

    @property
    def quarantined(self) -> tuple[CandidateEvaluation, ...]:
        return tuple(self._quarantined)

    def record_correction(
        self, heard: str, preferred: str, *, app: str | None = None
    ) -> CorrectionCase:
        if (not _valid_span(heard) or not _valid_span(preferred)
                or not _valid_app(app)):
            raise ValueError("correction spans must contain 1-80 characters")
        case = CorrectionCase(
            _case_id(heard, preferred, app), heard, preferred, app)
        self._cases[case.id] = case
        while len(self._cases) > MAX_CASES:
            self._cases.pop(next(iter(self._cases)))
        # New evidence can invalidate a formerly safe Personal Prior. Demote
        # it immediately; the next proposal must pass the expanded suite.
        for key, mapping in tuple(self._promoted.items()):
            if mapping.heard.casefold() != heard.casefold():
                continue
            if mapping.app is not None and mapping.app != app:
                continue
            evaluation = self.evaluate(
                mapping.heard, mapping.preferred, app=mapping.app)
            if not evaluation.passed:
                self._promoted.pop(key, None)
                self._remember_quarantine(evaluation)
        return case

    def record_revert(
        self, heard: str, preferred: str, *, app: str | None = None
    ) -> bool:
        """Record that the user changed an active mapping's output back.

        The heard word was right after all, so it becomes a case that expects
        itself. That demotes the mapping and keeps it from being promoted
        again in this scope until the user forgets it.
        """
        if not any(
                mapping.heard.casefold() == heard.casefold()
                and mapping.preferred.casefold() == preferred.casefold()
                for mapping in self.active(app=app)):
            return False
        self.record_correction(heard, heard, app=app)
        return True

    def propose(
        self, heard: str, preferred: str, *, app: str | None = None,
        corrected: int | None = None, kept: int = 0,
    ) -> CandidateEvaluation:
        """Gate one candidate mapping against the suite and its evidence.

        ``corrected`` and ``kept`` are how often the heard word was corrected
        this way and how often it was pasted and left alone in the candidate's
        scope. Corrections have to outnumber the unedited pastes.
        """
        if (not _valid_span(heard) or not _valid_span(preferred)
                or not _valid_app(app)):
            raise ValueError("mapping spans must contain 1-80 characters")
        result = self.evaluate(heard, preferred, app=app)
        key = (heard.casefold(), app)
        mapping = LearnedMapping(heard, preferred, app)
        if result.passed and corrected is not None and kept >= corrected:
            result = replace(result, passed=False, reasons=(
                f"left unchanged {kept} times against "
                f"{corrected} corrections",))
            # The same evidence that holds a candidate back takes down the
            # mapping it would have confirmed.
            if self._promoted.get(key) == mapping:
                self._promoted.pop(key)
        if not result.passed:
            self._remember_quarantine(result)
            return result
        if self._promoted.get(key) == mapping:
            return replace(result, promoted=True)

        cases = [
            ShadowRegressionCase(
                case.id, case.heard, case.preferred, case.app)
            for case in self.cases
        ]
        # A global candidate can be proposed from repeated app-scoped
        # observations after the app-specific mapping is already active. Add a
        # transient scope-counterfactual so that material improvement outside
        # the existing app mapping is measurable without persisting synthetic
        # evidence into the user's regression suite.
        if not any(
                case.heard.casefold() == heard.casefold()
                and case.app == app
                for case in self.cases):
            cases.append(ShadowRegressionCase(
                "candidate-scope-" + _case_id(
                    heard, preferred, app),
                heard,
                preferred,
                app,
            ))
        suite_digest = hashlib.sha256(
            "\n".join(case.id for case in self.cases).encode("ascii")
        ).hexdigest()[:16]
        candidate_id = (
            "personal-prior-"
            + hashlib.sha256(json.dumps(
                [heard.casefold(), preferred, app, suite_digest],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()[:24]
        )

        def candidate_transform(text: str, case_app: str | None) -> str:
            return self._apply_mappings(
                text, app=case_app, candidate=mapping)

        def activate() -> bool:
            self._promoted[key] = mapping
            while len(self._promoted) > MAX_MAPPINGS:
                self._promoted.pop(next(iter(self._promoted)))
            return True

        receipt = ShadowCandidateGate().attempt(
            candidate_id,
            CandidateKind.PERSONAL_PRIOR,
            tuple(cases),
            lambda text, case_app: self.apply(text, app=case_app),
            candidate_transform,
            activate,
        )
        if receipt.disposition != CandidateDisposition.PROMOTED:
            reason = (
                "shadow suite found "
                f"{receipt.regression_count} regressions and "
                f"{receipt.error_count} errors across "
                f"{receipt.case_count} cases"
                if receipt.disposition == CandidateDisposition.QUARANTINED
                else "shadow suite found no material improvement"
            )
            held = replace(result, passed=False, reasons=(reason,))
            self._remember_quarantine(held)
            return held
        self._quarantined = [
            item for item in self._quarantined
            if not (item.heard.casefold() == heard.casefold()
                    and item.app == app)
        ]
        return replace(result, promoted=True)

    def _remember_quarantine(self, result: CandidateEvaluation) -> None:
        if result not in self._quarantined:
            self._quarantined.append(result)
        self._quarantined = self._quarantined[-MAX_QUARANTINED:]

    def evaluate(
        self, heard: str, preferred: str, *, app: str | None = None
    ) -> CandidateEvaluation:
        """Evaluate a candidate without promoting or quarantining it."""
        applicable = [
            case for case in self._cases.values()
            if case.heard.casefold() == heard.casefold()
            and (app is None or case.app == app)
        ]
        applicable.sort(key=lambda case: case.id)
        reasons = tuple(
            f"{case.id} ({case.app or 'global'}): expected "
            f"{case.preferred!r}, got {preferred!r}"
            for case in applicable
            if case.preferred != preferred
        )
        if not applicable:
            reasons = ("no applicable correction cases",)
        if remaps_common_words(heard, preferred):
            reasons = (COMMON_WORDS_REASON,)
        return CandidateEvaluation(
            heard=heard,
            preferred=preferred,
            app=app,
            passed=not reasons,
            promoted=False,
            reasons=reasons,
            case_ids=tuple(case.id for case in applicable),
        )

    def _chosen(
            self, *, app: str | None = None,
            candidate: LearnedMapping | None = None
    ) -> dict[str, LearnedMapping]:
        chosen: dict[str, LearnedMapping] = {}
        mappings = list(self._promoted.values())
        if candidate is not None:
            mappings = [
                mapping for mapping in mappings
                if not (
                    mapping.heard.casefold() == candidate.heard.casefold()
                    and mapping.app == candidate.app
                )
            ]
            mappings.append(candidate)
        mappings = sorted(
            mappings,
            key=lambda mapping: (
                0 if candidate is not None and mapping == candidate else 1,
                0 if mapping.app == app and app is not None else 1,
                -len(mapping.heard), mapping.heard.casefold()),
        )
        for mapping in mappings:
            if mapping.app is None or mapping.app == app:
                chosen.setdefault(mapping.heard.casefold(), mapping)
        return chosen

    def active(self, *, app: str | None = None) -> tuple[LearnedMapping, ...]:
        """The one mapping per heard span that applies in ``app``.

        An app-scoped mapping takes precedence over a global one.
        """
        return tuple(self._chosen(app=app).values())

    def _apply_mappings(
            self, text: str, *, app: str | None = None,
            candidate: LearnedMapping | None = None) -> str:
        chosen = self._chosen(app=app, candidate=candidate)
        if not chosen:
            return text
        alternatives = sorted(
            (mapping.heard for mapping in chosen.values()),
            key=lambda heard: (-len(heard), heard.casefold()),
        )
        pattern = re.compile(
            rf"(?<!\w)(?:{'|'.join(re.escape(v) for v in alternatives)})(?!\w)",
            flags=re.IGNORECASE,
        )
        # One substitution pass over the original text prevents mapping chains
        # such as Gwen->Qwen and Qwen->When from recursively changing Gwen.
        return pattern.sub(
            lambda match: _in_heard_case(
                match.group(0), chosen[match.group(0).casefold()]),
            text,
        )

    def apply(self, text: str, *, app: str | None = None) -> str:
        return self._apply_mappings(text, app=app)

    is_common_word = staticmethod(is_common_word)
    remaps_common_words = staticmethod(remaps_common_words)

    def forget(self, heard: str, *, app: str | None = None) -> int:
        """Forget all evidence and decisions for one mapping scope."""
        key = heard.casefold()
        kept_cases = {
            case_id: case for case_id, case in self._cases.items()
            if not (case.heard.casefold() == key and case.app == app)
        }
        removed = len(self._cases) - len(kept_cases)
        self._cases = kept_cases
        self._promoted.pop((key, app), None)
        self._quarantined = [
            item for item in self._quarantined
            if not (item.heard.casefold() == key and item.app == app)
        ]
        return removed

    def to_dict(self) -> dict[str, Any]:
        cases = [
            {
                "id": case.id,
                "heard": case.heard,
                "preferred": case.preferred,
                "app": case.app,
            }
            for case in self.cases
        ]
        promoted = [
            {
                "heard": mapping.heard,
                "preferred": mapping.preferred,
                "app": mapping.app,
            }
            for mapping in self.promoted
        ]
        quarantined = [
            {
                "heard": item.heard,
                "preferred": item.preferred,
                "app": item.app,
                "reasons": list(item.reasons),
                "case_ids": list(item.case_ids),
            }
            for item in self._quarantined
        ]
        quarantined.sort(
            key=lambda item: ((item["app"] or ""),
                              item["heard"].casefold(), item["preferred"],
                              item["reasons"]))
        return {
            "version": SCHEMA_VERSION,
            "cases": cases,
            "promoted": promoted,
            "quarantined": quarantined,
        }

    def dumps(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def loads(cls, payload: str) -> "PersonalRegressionLab":
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("personal regression state must be an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PersonalRegressionLab":
        lab = cls()
        version = payload.get("version", 0)
        if isinstance(version, int) and version > SCHEMA_VERSION:
            return lab
        raw_cases = payload.get("cases", [])
        if isinstance(raw_cases, list):
            for raw in raw_cases:
                if not isinstance(raw, dict):
                    continue
                heard = raw.get("heard", raw.get("from"))
                preferred = raw.get("preferred", raw.get("to"))
                app = raw.get("app", raw.get("bundle"))
                if _valid_span(heard) and _valid_span(preferred) \
                        and _valid_app(app):
                    try:
                        lab.record_correction(heard, preferred, app=app)
                    except ValueError:
                        pass
        raw_promoted = payload.get("promoted", [])
        if isinstance(raw_promoted, list):
            for raw in raw_promoted:
                if not isinstance(raw, dict):
                    continue
                heard = raw.get("heard", raw.get("from"))
                preferred = raw.get("preferred", raw.get("to"))
                app = raw.get("app", raw.get("bundle"))
                if _valid_span(heard) and _valid_span(preferred) \
                        and _valid_app(app):
                    # Restore, do not replay: the shadow gate's "material
                    # improvement" test is about activating something new,
                    # and replayed it held back every app mapping that has
                    # a global twin. A saved promotion only has to still
                    # agree with the saved cases and the current rules.
                    result = lab.evaluate(heard, preferred, app=app)
                    if not result.passed:
                        lab._remember_quarantine(result)
                        continue
                    lab._promoted[(heard.casefold(), app)] = LearnedMapping(
                        heard, preferred, app)
                    while len(lab._promoted) > MAX_MAPPINGS:
                        lab._promoted.pop(next(iter(lab._promoted)))
        raw_quarantined = payload.get("quarantined", [])
        if isinstance(raw_quarantined, list):
            for raw in raw_quarantined:
                if not isinstance(raw, dict):
                    continue
                heard = raw.get("heard")
                preferred = raw.get("preferred")
                app = raw.get("app")
                reasons = raw.get("reasons")
                case_ids = raw.get("case_ids", [])
                if _valid_span(heard) and _valid_span(preferred) \
                        and _valid_app(app) \
                        and isinstance(reasons, list) \
                        and len(reasons) <= 32 \
                        and all(isinstance(reason, str) for reason in reasons) \
                        and all(len(reason) <= 512 for reason in reasons) \
                        and isinstance(case_ids, list) \
                        and len(case_ids) <= MAX_CASES \
                        and all(isinstance(case_id, str)
                                and len(case_id) <= 64
                                for case_id in case_ids):
                    lab._remember_quarantine(CandidateEvaluation(
                        heard=heard,
                        preferred=preferred,
                        app=app,
                        passed=False,
                        promoted=False,
                        reasons=tuple(reasons),
                        case_ids=tuple(case_ids),
                    ))
        return lab
