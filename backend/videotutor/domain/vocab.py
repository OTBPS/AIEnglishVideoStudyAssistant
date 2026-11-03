"""Vocabulary: words in a transcript, what the learner knows, and how hard a video is.

Knowledge model (M2). A lemma is *known* when the learner said so (KNOWN),
asked us to ignore it (IGNORED, e.g. a name), or -- when they have not said
anything -- when its frequency rank is within their vocabulary level. LEARNING
always counts as unknown: those are the words being studied.

Difficulty follows lexical-coverage research (docs/ARCHITECTURE.md, 学习方法依据):
about 95% of running words known is the threshold for adequate listening
comprehension, 98% for comfortable comprehension.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
# "[Music]", "(Laughter)": sound tags, not speech
_SOUND_TAG = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_SENTENCE_END = frozenset('.!?"“”:')
_CLITICS = ("n't", "'s", "'re", "'ve", "'ll", "'d", "'m")
_IRREGULAR_NT = {"can't": "can", "won't": "will", "shan't": "shall", "ain't": "be"}
_NEVER_NAMES = frozenset({"i"})  # always capitalised, never a name


class WordStatus(StrEnum):
    KNOWN = "known"
    LEARNING = "learning"
    IGNORED = "ignored"


class Difficulty(StrEnum):
    EASY = "easy"  # >= 98% of words known
    SUITABLE = "suitable"  # >= 95%
    CHALLENGING = "challenging"  # >= 90%
    HARD = "hard"


class Lexicon(Protocol):
    def lookup(self, form: str) -> tuple[str, int | None]:
        """(lemma, frequency rank or None if not in the lexicon) for a lower-case form."""
        ...


@dataclass(frozen=True)
class Token:
    start: int  # character offsets into the segment text
    end: int
    lemma: str
    rank: int | None
    capitalized: bool
    sentence_start: bool


@dataclass(frozen=True)
class WordStat:
    lemma: str
    count: int
    rank: int | None
    status: WordStatus | None
    known: bool
    always_capitalized: bool = False  # probably a name, even if we could not prove it


@dataclass(frozen=True)
class VocabularyProfile:
    token_count: int  # running words, names excluded
    known_count: int
    words: tuple[WordStat, ...]  # every distinct lemma, by first appearance

    @property
    def coverage(self) -> float:
        return self.known_count / self.token_count if self.token_count else 1.0

    @property
    def difficulty(self) -> Difficulty:
        c = self.coverage
        if c >= 0.98:
            return Difficulty.EASY
        if c >= 0.95:
            return Difficulty.SUITABLE
        if c >= 0.90:
            return Difficulty.CHALLENGING
        return Difficulty.HARD

    @property
    def unknown(self) -> tuple[WordStat, ...]:
        return tuple(w for w in self.words if not w.known)


def split_clitic(form: str) -> str:
    """ "don't" -> "do", "it's" -> "it", "can't" -> "can". Input lower-case."""
    form = form.replace("’", "'")
    if form in _IRREGULAR_NT:
        return _IRREGULAR_NT[form]
    for suffix in _CLITICS:
        if form.endswith(suffix) and len(form) > len(suffix):
            return form[: -len(suffix)]
    return form


def tokenize(text: str, lexicon: Lexicon, *, starts_sentence: bool = True) -> list[Token]:
    """`starts_sentence`: whether `text` begins a sentence. Caption segments often
    begin mid-sentence; the caller knows from the previous segment's punctuation."""
    masked = _SOUND_TAG.sub(lambda m: " " * len(m.group()), text)
    tokens: list[Token] = []
    for m in WORD_RE.finditer(masked):
        form = m.group()
        lemma, rank = lexicon.lookup(split_clitic(form.lower()))
        before = masked[: m.start()].rstrip()
        sentence_start = before[-1] in _SENTENCE_END if before else starts_sentence
        tokens.append(Token(m.start(), m.end(), lemma, rank, form[0].isupper(), sentence_start))
    return tokens


def ends_sentence(text: str) -> bool:
    stripped = _SOUND_TAG.sub("", text).rstrip()
    return not stripped or stripped[-1] in _SENTENCE_END


def detect_names(tokens: Iterable[Token]) -> frozenset[str]:
    """Lemmas that are names in this text: capitalised every time they occur,
    at least once in the middle of a sentence ("... said Robinson").

    Decided per document because frequency lists contain common names
    (robinson, alice), so "not in the lexicon" cannot tell a name from a word.
    """
    always_capital: dict[str, bool] = {}
    mid_sentence: set[str] = set()
    for t in tokens:
        always_capital[t.lemma] = always_capital.get(t.lemma, True) and t.capitalized
        if t.capitalized and not t.sentence_start:
            mid_sentence.add(t.lemma)
    return frozenset(
        lemma
        for lemma, cap in always_capital.items()
        if cap and lemma in mid_sentence and lemma not in _NEVER_NAMES
    )


def is_known(rank: int | None, status: WordStatus | None, level: int) -> bool:
    if status in (WordStatus.KNOWN, WordStatus.IGNORED):
        return True
    if status is WordStatus.LEARNING:
        return False
    return rank is not None and rank <= level


def build_profile(
    tokens: Iterable[Token],
    statuses: Mapping[str, WordStatus],
    level: int,
    names: frozenset[str] = frozenset(),
) -> VocabularyProfile:
    """Names are neither counted nor taught."""
    counts: dict[str, int] = {}
    ranks: dict[str, int | None] = {}
    capital: dict[str, bool] = {}
    for t in tokens:
        if t.lemma in names:
            continue
        counts[t.lemma] = counts.get(t.lemma, 0) + 1
        ranks.setdefault(t.lemma, t.rank)
        capital[t.lemma] = capital.get(t.lemma, True) and t.capitalized
    words = tuple(
        WordStat(
            lemma,
            count,
            ranks[lemma],
            statuses.get(lemma),
            is_known(ranks[lemma], statuses.get(lemma), level),
            capital[lemma] and lemma not in _NEVER_NAMES,
        )
        for lemma, count in counts.items()
    )
    return VocabularyProfile(
        token_count=sum(counts.values()),
        known_count=sum(w.count for w in words if w.known),
        words=words,
    )


def preview_candidates(profile: VocabularyProfile, limit: int = 5) -> list[WordStat]:
    """The few unknown words worth teaching before watching.

    Research (ARCHITECTURE.md): pre-teaching many words distracts; 3-5 key
    words help. Prefer words this video repeats (they matter for following it),
    then more common words (more useful to learn). Words beyond the lexicon
    ("counterintuitive") still qualify, ranked as the rarest; words always
    written with a capital are skipped as probable names.
    """
    candidates = [w for w in profile.unknown if w.status is None and not w.always_capitalized]
    candidates.sort(key=lambda w: (-w.count, w.rank if w.rank is not None else 1_000_000))
    return candidates[:limit]
