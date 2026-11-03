"""Bilibili CC / AI subtitles ("BCC" JSON).

    {"body": [{"from": 0.5, "to": 2.1, "location": 2, "content": "Hello"}, ...]}

Times are seconds (floats). The extension fetches the file with the learner's
own Bilibili session (subtitles require a logged-in account, verified 2026-11)
and forwards it verbatim.
"""

from __future__ import annotations

import json
from typing import Any

from videotutor.domain.transcript import RawCue

from .youtube import MAX_BODY_CHARS, CaptionFormatError


def decode_bcc(body: str) -> list[RawCue]:
    if len(body) > MAX_BODY_CHARS:
        raise CaptionFormatError("caption payload is too large")
    try:
        doc: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CaptionFormatError(f"not JSON: {exc}") from exc
    items = doc.get("body") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise CaptionFormatError("bcc payload has no body list")
    cues: list[RawCue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start, end, text = item.get("from"), item.get("to"), item.get("content")
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        start_ms = round(start * 1000)
        cues.append(RawCue(start_ms, max(0, round(end * 1000) - start_ms), text))
    return cues
