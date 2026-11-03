from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from videotutor.domain.vocab import (
    Difficulty,
    VocabularyProfile,
    WordStat,
    WordStatus,
    build_profile,
    detect_names,
    is_known,
    preview_candidates,
    split_clitic,
    tokenize,
)
from videotutor.infra.lexicon import FileLexicon, LexiconFormatError

from .conftest import REPO

LEXICON = FileLexicon.load(REPO / "backend" / "videotutor" / "assets" / "lexicon_en.tsv.gz")


# ---------------------------------------------------------------- lexicon
def test_bundled_lexicon_maps_forms_to_lemmas_with_ranks() -> None:
    assert len(LEXICON) == 40_000
    the, rank_the = LEXICON.lookup("the")
    assert (the, rank_the) == ("the", 1)
    assert LEXICON.lookup("went")[0] == "go"
    assert LEXICON.lookup("children")[0] == "child"
    went_rank = LEXICON.lookup("went")[1]
    rare_rank = LEXICON.lookup("serendipity")[1]
    assert went_rank is not None and rare_rank is not None and went_rank < rare_rank
    assert LEXICON.lookup("qwxyzzy") == ("qwxyzzy", None)


def test_malformed_lexicon_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LexiconFormatError, match="cannot read"):
        FileLexicon.load(tmp_path / "missing.gz")
    for content, message in (("# lemmas\nthe\n", "forms"), ("the\n# forms\n", "start")):
        f = tmp_path / "bad.gz"
        f.write_bytes(gzip.compress(content.encode()))
        with pytest.raises(LexiconFormatError, match=message):
            FileLexicon.load(f)


# ---------------------------------------------------------------- tokenizing
@pytest.mark.parametrize(
    ("form", "base"),
    [
        ("don't", "do"),
        ("it’s", "it"),
        ("can't", "can"),
        ("won't", "will"),
        ("they're", "they"),
        ("o'clock", "o'clock"),
        ("s", "s"),
    ],
)
def test_split_clitic(form: str, base: str) -> None:
    assert split_clitic(form) == base


def test_tokenize_skips_sound_tags() -> None:
    text = "[Music] So Ken went to Stratford. Children don't wait (Laughter)"
    tokens = tokenize(text, LEXICON)
    words = [(text[t.start : t.end], t.lemma, t.sentence_start) for t in tokens]
    assert all(w not in ("Music", "Laughter") for w, _, _ in words)
    assert ("So", "so", True) in words
    assert ("went", "go", False) in words
    assert ("Children", "child", True) in words  # "." before it
    assert ("don't", "do", False) in words


def test_names_are_detected_per_document() -> None:
    text = (
        "Ken Robinson spoke. Then Robinson said children matter. "
        "Alice arrived. I think Alice and I agree. Mark the date. We mark it. Stop."
    )
    names = detect_names(tokenize(text, LEXICON))
    assert {"robinson", "alice"} <= names
    assert "ken" not in names  # only seen at a sentence start: undecidable, so counted
    assert "mark" not in names  # also written in lower case
    assert "i" not in names
    assert "stop" not in names  # only ever at a sentence start


# ---------------------------------------------------------------- knowledge & coverage
def test_is_known_rules() -> None:
    assert is_known(100, None, 3000)
    assert not is_known(5000, None, 3000)
    assert not is_known(None, None, 3000)
    assert is_known(5000, WordStatus.KNOWN, 3000)
    assert is_known(None, WordStatus.IGNORED, 3000)
    assert not is_known(10, WordStatus.LEARNING, 3000)


def _profile(
    text: str, statuses: dict[str, WordStatus] | None = None, level: int = 3000
) -> VocabularyProfile:
    tokens = tokenize(text, LEXICON)
    return build_profile(tokens, statuses or {}, level, detect_names(tokens))


def test_profile_counts_lemmas_and_coverage() -> None:
    p = _profile("The children went home. The child goes ubiquitous ubiquitous.")
    by_lemma = {w.lemma: w for w in p.words}
    assert by_lemma["child"].count == 2 and by_lemma["go"].count == 2
    assert by_lemma["ubiquitous"].count == 2 and not by_lemma["ubiquitous"].known
    assert p.token_count == 9 and p.known_count == 7
    assert p.coverage == pytest.approx(7 / 9)
    assert p.difficulty is Difficulty.HARD
    assert [w.lemma for w in p.unknown] == ["ubiquitous"]


def test_marks_change_coverage_and_names_do_not_count() -> None:
    text = "Yesterday Alice met ubiquitous people. Then Alice said ubiquitous things."
    marked = _profile(text, {"ubiquitous": WordStatus.KNOWN})
    assert "alice" not in {w.lemma for w in marked.words}
    assert marked.coverage == 1.0 and marked.difficulty is Difficulty.EASY
    learning = _profile(
        " ".join(["the"] * 19 + ["ubiquitous"]), {"ubiquitous": WordStatus.LEARNING}
    )
    assert learning.difficulty is Difficulty.SUITABLE  # 19/20 = 95%
    assert _profile("").coverage == 1.0


def test_difficulty_bands() -> None:
    def prof(known: int, total: int) -> VocabularyProfile:
        return VocabularyProfile(total, known, ())

    assert prof(98, 100).difficulty is Difficulty.EASY
    assert prof(95, 100).difficulty is Difficulty.SUITABLE
    assert prof(90, 100).difficulty is Difficulty.CHALLENGING
    assert prof(89, 100).difficulty is Difficulty.HARD


def test_preview_prefers_repeated_then_common_words() -> None:
    words = (
        WordStat("rare", 1, 30000, None, False),
        WordStat("repeated", 3, 9000, None, False),
        WordStat("common", 1, 4000, None, False),
        WordStat("counterintuitive", 1, None, None, False),  # beyond the lexicon: rarest
        WordStat("gillian", 5, None, None, False, always_capitalized=True),  # probable name
        WordStat("studying", 9, 5000, WordStatus.LEARNING, False),  # already being studied
        WordStat("easy", 9, 10, None, True),
    )
    profile = VocabularyProfile(30, 9, words)
    assert [w.lemma for w in preview_candidates(profile, limit=2)] == ["repeated", "common"]
    assert [w.lemma for w in preview_candidates(profile)][-1] == "counterintuitive"
