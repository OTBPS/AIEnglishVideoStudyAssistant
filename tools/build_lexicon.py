"""Build the compact English lexicon shipped with the app.

    backend/.venv/Scripts/python tools/build_lexicon.py

Why a generated file instead of runtime libraries: wordfreq (55 MB) and
simplemma (18 MB) carry every language they support; the app needs English
only. This script derives two small tables from them once, and the app reads
the result with nothing but the standard library.

Output: backend/videotutor/assets/lexicon_en.tsv.gz (committed), with
  # lemmas                   one lemma per line, most frequent first (line = rank)
  # forms                    form<TAB>lemma for every form that is not its own lemma

Licences: wordfreq's data is CC BY-SA 4.0, simplemma's is MIT; the derived file
is therefore CC BY-SA 4.0 (see THIRD_PARTY_NOTICES.md). Rerun only to change
the vocabulary size or after upgrading either source.
"""

from __future__ import annotations

import gzip
import re
from collections import defaultdict
from pathlib import Path

import simplemma
from wordfreq import top_n_list, word_frequency

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "videotutor" / "assets" / "lexicon_en.tsv.gz"
TOP_FORMS = 80_000
MAX_LEMMAS = 40_000
# Letters only: contractions ("don't", "it's") are split by the app at
# runtime (domain/vocab.py split_clitic) -- simplemma maps "it's" to "its".
_WORD = re.compile(r"^[a-z]+$")


def build() -> tuple[list[str], dict[str, str]]:
    totals: dict[str, float] = defaultdict(float)
    form_lemma: dict[str, str] = {}
    for form in top_n_list("en", TOP_FORMS):
        if not _WORD.match(form):
            continue  # numbers, symbols, hyphenated and multi-token entries
        lemma = simplemma.lemmatize(form, lang="en").lower()
        if not _WORD.match(lemma):
            lemma = form
        totals[lemma] += word_frequency(form, "en")
        form_lemma[form] = lemma
    ranked = sorted(totals, key=lambda lemma: (-totals[lemma], lemma))[:MAX_LEMMAS]
    kept = set(ranked)
    forms = {f: lem for f, lem in form_lemma.items() if lem in kept and f != lem}
    return ranked, forms


def main() -> None:
    lemmas, forms = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# lemmas", *lemmas, "# forms", *(f"{f}\t{lem}" for f, lem in sorted(forms.items()))]
    # mtime=0: identical input gives a byte-identical file (reproducible builds).
    with OUT.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(("\n".join(lines) + "\n").encode("utf-8"))
    print(
        f"{len(lemmas)} lemmas, {len(forms)} inflected forms -> "
        f"{OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)"
    )


if __name__ == "__main__":
    main()
