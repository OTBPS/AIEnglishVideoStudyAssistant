"""Helpers shared by route modules."""

from __future__ import annotations

from fastapi import HTTPException

from videotutor.domain.video import Platform, VideoRef


def video_ref(platform: Platform, external_id: str) -> VideoRef:
    try:
        return VideoRef(platform, external_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
