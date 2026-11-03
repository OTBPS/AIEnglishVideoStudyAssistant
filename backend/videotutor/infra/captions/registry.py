"""One decoder for every supported caption format (implements CaptionDecoder)."""

from __future__ import annotations

from collections.abc import Callable

from videotutor.domain.transcript import RawCue

from .bilibili import decode_bcc
from .youtube import MAX_BODY_CHARS, CaptionFormatError, decode_json3, decode_srv3

_DECODERS: dict[str, Callable[[str], list[RawCue]]] = {
    "json3": decode_json3,  # YouTube web player
    "srv3": decode_srv3,  # YouTube XML
    "bcc": decode_bcc,  # Bilibili CC / AI subtitles
}


class CaptionDecoders:
    def formats(self) -> frozenset[str]:
        return frozenset(_DECODERS)

    def decode(self, fmt: str, body: str) -> list[RawCue]:
        if len(body) > MAX_BODY_CHARS:
            raise CaptionFormatError("caption payload is too large")
        decoder = _DECODERS.get(fmt)
        if decoder is None:
            raise CaptionFormatError(f"unsupported caption format {fmt!r}")
        return decoder(body)
