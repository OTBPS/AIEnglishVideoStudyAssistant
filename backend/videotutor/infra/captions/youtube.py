"""YouTube timedtext payloads: json3 (what the web player requests) and srv3 (XML).

The extension does not parse captions; it forwards the player's own response
verbatim. YouTube's timedtext URLs need a proof-of-origin token that only the
player can mint, so re-fetching them from anywhere else returns an empty body
(verified 2026-11). Decoding here keeps the format knowledge in one tested place.

json3 shape (only the fields used):
    {"events": [{"tStartMs": 0, "dDurationMs": 2000,
                 "segs": [{"utf8": "Hello"}, {"utf8": " world", "tOffsetMs": 400}]},
                {"tStartMs": 2000, "aAppend": 1, "segs": [{"utf8": "\\n"}]}]}
Auto-generated tracks split lines into words (tOffsetMs) and insert append
events that only carry line breaks; both collapse naturally once the segs of
an event are joined and whitespace-only events are dropped.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from xml.etree import ElementTree as ET

from videotutor.domain.transcript import RawCue

MAX_BODY_CHARS = 8_000_000

_TAG = re.compile(r"<[^>]+>")


class CaptionFormatError(ValueError):
    pass


class YouTubeCaptionDecoder:
    def formats(self) -> frozenset[str]:
        return frozenset({"json3", "srv3"})

    def decode(self, fmt: str, body: str) -> list[RawCue]:
        if len(body) > MAX_BODY_CHARS:
            raise CaptionFormatError("caption payload is too large")
        if fmt == "json3":
            return decode_json3(body)
        if fmt == "srv3":
            return decode_srv3(body)
        raise CaptionFormatError(f"unsupported caption format {fmt!r}")


def decode_json3(body: str) -> list[RawCue]:
    try:
        doc: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CaptionFormatError(f"not JSON: {exc}") from exc
    events = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(events, list):
        raise CaptionFormatError("json3 payload has no events list")
    cues: list[RawCue] = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("aAppend"):
            continue
        segs = ev.get("segs")
        if not isinstance(segs, list):
            continue
        text = "".join(s.get("utf8", "") for s in segs if isinstance(s, dict))
        start, duration = ev.get("tStartMs"), ev.get("dDurationMs", 0)
        if not isinstance(start, int) or not isinstance(duration, int) or not text.strip():
            continue
        cues.append(RawCue(start, duration, text))
    return cues


def decode_srv3(body: str) -> list[RawCue]:
    """<timedtext><body><p t="0" d="2000">Hello <s>world</s></p></body></timedtext>"""
    try:
        root = ET.fromstring(body)  # noqa: S314 -- no DTDs/entities in stdlib parser by default
    except ET.ParseError as exc:
        raise CaptionFormatError(f"not XML: {exc}") from exc
    cues: list[RawCue] = []
    for p in root.iter("p"):
        try:
            start, duration = int(p.get("t", "")), int(p.get("d", "0"))
        except ValueError:
            continue
        text = html.unescape(_TAG.sub("", "".join(p.itertext())))
        if text.strip():
            cues.append(RawCue(start, duration, text))
    return cues
