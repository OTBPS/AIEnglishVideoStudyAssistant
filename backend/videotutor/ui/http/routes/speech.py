"""Speech recognition: installing its components (home page) and transcribing
a video that has no English subtitles (side panel)."""

from __future__ import annotations

from fastapi import APIRouter

from videotutor.app.ports import TranscriptInfo
from videotutor.app.speech import ComponentView, SpeechSetup
from videotutor.app.transcription import JobView, TranscriptionService
from videotutor.domain.video import Platform

from ..schemas import (
    ComponentOut,
    GpuOut,
    SpeechOut,
    TranscriptInfoOut,
    TranscriptionIn,
    TranscriptionOut,
)
from .common import video_ref


def _component(v: ComponentView) -> ComponentOut:
    c = v.component
    return ComponentOut(
        id=c.id,
        kind=c.kind.value,
        title=c.title,
        description=c.description,
        download_bytes=c.download_bytes,
        status=v.status.value,
        downloaded_bytes=v.downloaded_bytes,
        error=v.error,
        recommended=v.recommended,
    )


def _info(i: TranscriptInfo) -> TranscriptInfoOut:
    return TranscriptInfoOut(
        language=i.language, kind=i.kind.value, source=i.source, segment_count=i.segment_count
    )


def _job(j: JobView) -> TranscriptionOut:
    return TranscriptionOut(
        status=j.status.value,
        progress=round(j.progress, 3),
        model=j.model,
        device=j.device,
        error=j.error,
        transcript=None if j.transcript is None else _info(j.transcript),
    )


def router(speech: SpeechSetup, transcription: TranscriptionService) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/speech")
    def overview() -> SpeechOut:
        o = speech.overview()
        gpu = o.gpu
        return SpeechOut(
            ready=o.ready,
            model=o.model.id if o.model else None,
            device=None if o.model is None else ("cuda" if gpu.usable else "cpu"),
            gpu=GpuOut(
                name=gpu.gpu.name if gpu.gpu else None,
                memory_mb=gpu.gpu.memory_mb if gpu.gpu else None,
                usable=gpu.usable,
                failure=gpu.failure,
            ),
            components=[_component(c) for c in o.components],
        )

    @r.post("/speech/components/{component_id}/install")
    def install(component_id: str) -> ComponentOut:
        return _component(speech.install(component_id))

    @r.post("/speech/components/{component_id}/cancel")
    def cancel_install(component_id: str) -> ComponentOut:
        return _component(speech.cancel(component_id))

    @r.delete("/speech/components/{component_id}")
    def remove(component_id: str) -> ComponentOut:
        return _component(speech.remove(component_id))

    prefix = "/videos/{platform}/{external_id}/transcription"

    @r.post(prefix)
    def start(platform: Platform, external_id: str, body: TranscriptionIn) -> TranscriptionOut:
        ref = video_ref(platform, external_id)
        return _job(transcription.start(ref, body.checked_urls(), body.duration_ms))

    @r.get(prefix)
    def status(platform: Platform, external_id: str) -> TranscriptionOut | None:
        job = transcription.job(video_ref(platform, external_id))
        return None if job is None else _job(job)

    @r.delete(prefix)
    def cancel(platform: Platform, external_id: str) -> TranscriptionOut | None:
        job = transcription.cancel(video_ref(platform, external_id))
        return None if job is None else _job(job)

    return r
