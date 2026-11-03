"""The bundled English lexicon (assets/lexicon_en.tsv.gz, built by tools/build_lexicon.py).

Implements domain.vocab.Lexicon. ~60k entries, loaded once at startup in well
under a second, kept in two dicts.
"""

from __future__ import annotations

import gzip
from pathlib import Path


class LexiconFormatError(ValueError):
    pass


class FileLexicon:
    def __init__(self, ranks: dict[str, int], forms: dict[str, str]) -> None:
        self._ranks = ranks
        self._forms = forms

    @classmethod
    def load(cls, path: Path) -> FileLexicon:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError as exc:
            raise LexiconFormatError(f"cannot read lexicon {path}: {exc}") from exc
        try:
            split = lines.index("# forms")
        except ValueError as exc:
            raise LexiconFormatError("lexicon has no '# forms' section") from exc
        if not lines or lines[0] != "# lemmas":
            raise LexiconFormatError("lexicon does not start with '# lemmas'")
        ranks = {lemma: i for i, lemma in enumerate(lines[1:split], start=1)}
        forms: dict[str, str] = {}
        for line in lines[split + 1 :]:
            form, _, lemma = line.partition("\t")
            if form and lemma:
                forms[form] = lemma
        return cls(ranks, forms)

    def __len__(self) -> int:
        return len(self._ranks)

    def lookup(self, form: str) -> tuple[str, int | None]:
        lemma = self._forms.get(form, form)
        return lemma, self._ranks.get(lemma)
