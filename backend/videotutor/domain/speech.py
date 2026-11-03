"""Local speech recognition, for videos that have no English subtitles at all.

Two things live here: the downloadable parts recognition needs (speech
models, the optional NVIDIA runtime) and how recognized words become
subtitle lines a learner can read at a glance.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .transcript import RawCue

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{1,40}$")


# ---------------------------------------------------------------- components
class ComponentKind(StrEnum):
    SPEECH_MODEL = "speech_model"
    GPU_RUNTIME = "gpu_runtime"


@dataclass(frozen=True)
class RemoteFile:
    """One file of a component, pinned by content hash.

    Every URL must serve exactly the same bytes: the hash, not the host, is
    what is trusted, so a mirror can never substitute anything else.
    """

    name: str  # file name inside the component folder (the archive's own name if extract)
    size: int
    sha256: str
    urls: tuple[str, ...]  # tried in order; later ones are mirrors
    extract: tuple[str, ...] = ()  # zip members to keep (fnmatch patterns); archive then dropped

    def __post_init__(self) -> None:
        if not self.name or "/" in self.name or "\\" in self.name or self.name.startswith("."):
            raise ValueError(f"bad file name {self.name!r}")
        if self.size <= 0:
            raise ValueError(f"{self.name}: size must be positive")
        if not _SHA256.match(self.sha256):
            raise ValueError(f"{self.name}: not a sha256 hex digest")
        if not self.urls or not all(u.startswith("https://") for u in self.urls):
            raise ValueError(f"{self.name}: needs https URLs")


@dataclass(frozen=True)
class Component:
    id: str  # also its folder name
    kind: ComponentKind
    title: str
    description: str
    files: tuple[RemoteFile, ...]
    prefers_gpu: bool = False  # far too slow on a CPU to be the automatic choice there

    def __post_init__(self) -> None:
        if not _COMPONENT_ID.match(self.id):
            raise ValueError(f"bad component id {self.id!r}")
        if not self.files:
            raise ValueError(f"{self.id}: no files")

    @property
    def download_bytes(self) -> int:
        return sum(f.size for f in self.files)


def choose_model(installed: Sequence[Component], *, gpu: bool) -> Component | None:
    """The speech model to use. `installed` is in catalog order, best first.

    With a working GPU the best model wins; on a CPU, models that need a GPU
    are used only when nothing else is installed.
    """
    models = [c for c in installed if c.kind is ComponentKind.SPEECH_MODEL]
    if not gpu:
        models.sort(key=lambda c: c.prefers_gpu)  # stable: catalog order otherwise
    return models[0] if models else None


# ---------------------------------------------------------------- recognized speech → lines
@dataclass(frozen=True)
class Word:
    start_ms: int
    end_ms: int
    text: str  # as the recognizer emits it, usually with a leading space


MAX_LINE_MS = 6000
MAX_LINE_CHARS = 84
PAUSE_MS = 800  # a silence this long ends the line, whatever the punctuation
_SENTENCE_END = re.compile(r"[.?!…][\"')\]]*$")
_CLAUSE_END = re.compile(r"[,;:—-][\"')\]]*$")
MIN_SPLIT_WORDS = 3  # never leave a one- or two-word scrap behind at a comma


def lines_from_words(words: Iterable[Word]) -> list[RawCue]:
    """Break recognized speech into subtitle lines.

    A line ends at the end of a sentence or at a pause. A line that grows
    too long (in characters or time) is split at its last comma if there is
    one far enough in, otherwise just before the word that made it too long.
    """
    out: list[RawCue] = []
    line: list[Word] = []

    def emit(ws: list[Word]) -> None:
        text = "".join(w.text for w in ws).strip()
        if text:
            out.append(RawCue(ws[0].start_ms, max(ws[-1].end_ms - ws[0].start_ms, 0), text))

    for word in words:
        if not word.text.strip():
            continue
        if line and word.start_ms - line[-1].end_ms >= PAUSE_MS:
            emit(line)
            line = []
        line.append(word)
        if _too_long(line):
            cut = _split_point(line)
            emit(line[:cut])
            line = line[cut:]
        if _SENTENCE_END.search(word.text.strip()):
            emit(line)
            line = []
    if line:
        emit(line)
    return out


def _too_long(line: list[Word]) -> bool:
    chars = len("".join(w.text for w in line).strip())
    return len(line) > 1 and (
        chars > MAX_LINE_CHARS or line[-1].end_ms - line[0].start_ms > MAX_LINE_MS
    )


def _split_point(line: list[Word]) -> int:
    """How many words of an over-long line to emit now."""
    for i in range(len(line) - 2, MIN_SPLIT_WORDS - 2, -1):
        if _CLAUSE_END.search(line[i].text.strip()):
            return i + 1
    return len(line) - 1
