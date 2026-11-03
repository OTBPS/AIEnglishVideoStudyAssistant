"""Caption decoding. Fixtures are synthetic but mirror the real payload shapes
(json3 captured from the YouTube web player, 2026-11); no real transcript text
is stored in this public repository."""

from __future__ import annotations

import json

import pytest

from videotutor.domain.transcript import RawCue, normalize
from videotutor.infra.captions.youtube import (
    MAX_BODY_CHARS,
    CaptionFormatError,
    YouTubeCaptionDecoder,
)

MANUAL_JSON3 = json.dumps(
    {
        "wireMagic": "pb3",
        "pens": [{}],
        "wsWinStyles": [{}],
        "wpWinPositions": [{}],
        "events": [
            {"tStartMs": 0, "dDurationMs": 7000, "segs": [{"utf8": "Good morning.\nHow are you?"}]},
            {"tStartMs": 7000, "dDurationMs": 3000, "segs": [{"utf8": "(Laughter)"}]},
            {"tStartMs": 10000, "dDurationMs": 100},
        ],
    }
)

AUTO_JSON3 = json.dumps(
    {
        "wireMagic": "pb3",
        "events": [
            {"tStartMs": 0, "dDurationMs": 90000, "id": 1, "wpWinPosId": 1, "wsWinStyleId": 1},
            {
                "tStartMs": 1000,
                "dDurationMs": 4000,
                "wWinId": 1,
                "segs": [
                    {"utf8": "so", "acAsrConf": 0},
                    {"utf8": " today", "tOffsetMs": 300, "acAsrConf": 0},
                    {"utf8": " we", "tOffsetMs": 700, "acAsrConf": 0},
                ],
            },
            {
                "tStartMs": 3500,
                "dDurationMs": 1500,
                "wWinId": 1,
                "aAppend": 1,
                "segs": [{"utf8": "\n"}],
            },
            {
                "tStartMs": 3500,
                "dDurationMs": 3000,
                "wWinId": 1,
                "segs": [{"utf8": "talk"}, {"utf8": " about", "tOffsetMs": 200}],
            },
        ],
    }
)

SRV3 = """<?xml version="1.0" encoding="utf-8" ?><timedtext format="3"><body>
<p t="0" d="2000">Hello <s>there</s></p>
<p t="2000" d="1500">it&#39;s &amp; fine</p>
<p t="bad" d="1">skipped</p>
<p t="4000" d="500">   </p>
</body></timedtext>"""

decoder = YouTubeCaptionDecoder()


def test_manual_json3() -> None:
    cues = decoder.decode("json3", MANUAL_JSON3)
    assert cues == [
        RawCue(0, 7000, "Good morning.\nHow are you?"),
        RawCue(7000, 3000, "(Laughter)"),
    ]
    segs = normalize(cues)
    assert segs[0].text == "Good morning. How are you?"


def test_auto_json3_joins_words_and_skips_line_breaks() -> None:
    cues = decoder.decode("json3", AUTO_JSON3)
    assert [c.text for c in cues] == ["so today we", "talk about"]
    segs = normalize(cues)
    assert (segs[0].start_ms, segs[0].end_ms) == (1000, 3500)  # clipped at the next line


def test_srv3() -> None:
    cues = decoder.decode("srv3", SRV3)
    assert [c.text for c in cues] == ["Hello there", "it's & fine"]


@pytest.mark.parametrize(
    ("fmt", "body", "message"),
    [
        ("json3", "{not json", "not JSON"),
        ("json3", "[]", "no events"),
        ("json3", '{"events": "x"}', "no events"),
        ("srv3", "<p", "not XML"),
        ("vtt", "WEBVTT", "unsupported"),
        ("json3", " " * (MAX_BODY_CHARS + 1), "too large"),
    ],
)
def test_bad_payloads_are_rejected(fmt: str, body: str, message: str) -> None:
    with pytest.raises(CaptionFormatError, match=message):
        decoder.decode(fmt, body)


def test_formats() -> None:
    assert decoder.formats() == {"json3", "srv3"}
