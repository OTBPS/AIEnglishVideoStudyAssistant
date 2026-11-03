"""Make English subtitles for a video that has none: fetch its audio, run the
local speech recognizer, store the result as the video's transcript.

One recognition at a time (it uses every core, or the whole GPU); later
requests wait in the queue. Each job is a cancellable background task, so
cancelling, a timeout or quitting the app stops the download or kills the
recognizer process promptly.

Audio comes from the platform's own CDN, at a URL the extension read in the
page (signed for the learner's session, valid a couple of hours). Only https
URLs on the platform's known media hosts are fetched.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from videotutor.core.statemachine import StateMachine
from videotutor.core.tasks import TaskHandle, TaskState, TaskSupervisor
from videotutor.domain.speech import Word, lines_from_words
from videotutor.domain.video import Platform, VideoRef

from .errors import (
    Cancelled,
    GpuUnavailableError,
    InvalidInputError,
    SpeechError,
    SpeechNotReadyError,
)
from .ports import Downloader, SpeechRecognizer, TranscriptInfo
from .speech import RecognitionPlan, SpeechSetup
from .videos import VideoService

log = logging.getLogger("transcription")

# Where each platform serves media from, and the Referer its CDN insists on.
_MEDIA_HOSTS: dict[Platform, tuple[str, ...]] = {
    Platform.BILIBILI: (".bilivideo.com", ".bilivideo.cn", ".akamaized.net", ".szbdyd.com"),
}
_REFERER = {Platform.BILIBILI: "https://www.bilibili.com/"}
MAX_AUDIO_URLS = 6
MAX_AUDIO_BYTES = 600_000_000  # ~10 hours at Bilibili's lowest audio bitrate

# Timeout: generous enough for a slow CPU (about 1x real time), but finite.
BASE_TIMEOUT_S = 900.0
TIMEOUT_PER_AUDIO_S = 1.5
UNKNOWN_DURATION_TIMEOUT_S = 6 * 3600.0
KEEP_FINISHED = 50


class JobStatus(StrEnum):
    QUEUED = "queued"  # waiting for the recognizer
    DOWNLOADING = "downloading"  # fetching the audio
    RECOGNIZING = "recognizing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


_END = frozenset({JobStatus.FAILED, JobStatus.CANCELLED})
_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.DOWNLOADING}) | _END,
    JobStatus.DOWNLOADING: frozenset({JobStatus.RECOGNIZING}) | _END,
    JobStatus.RECOGNIZING: frozenset({JobStatus.DONE}) | _END,
    JobStatus.DONE: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class JobView:
    status: JobStatus
    progress: float  # 0..1 of the recognition (downloading counts as 0)
    model: str | None
    device: str | None
    error: str | None
    transcript: TranscriptInfo | None


class _Job:
    def __init__(self, ref: VideoRef) -> None:
        self.ref = ref
        self.sm = StateMachine(JobStatus.QUEUED, _TRANSITIONS)
        self.progress = 0.0
        self.model: str | None = None
        self.device: str | None = None
        self.error: str | None = None
        self.transcript: TranscriptInfo | None = None
        self.handle: TaskHandle[None] | None = None

    def view(self) -> JobView:
        status, error = self.sm.state, self.error
        if self.handle is not None and self.handle.state is TaskState.TIMED_OUT:
            status, error = JobStatus.FAILED, "识别时间过长，已停止。可以换用更快的模型再试。"
        return JobView(status, self.progress, self.model, self.device, error, self.transcript)


class TranscriptionService:
    def __init__(
        self,
        videos: VideoService,
        speech: SpeechSetup,
        downloader: Downloader,
        recognizer: SpeechRecognizer,
        tasks: TaskSupervisor,
        work_dir: Path,
    ) -> None:
        self._videos = videos
        self._speech = speech
        self._downloader = downloader
        self._recognizer = recognizer
        self._tasks = tasks
        self._work_dir = work_dir
        self._jobs: dict[VideoRef, _Job] = {}
        self._lock = threading.Lock()
        self._recognizer_busy = threading.Lock()

    def job(self, ref: VideoRef) -> JobView | None:
        with self._lock:
            job = self._jobs.get(ref)
        return job.view() if job is not None else None

    def start(self, ref: VideoRef, audio_urls: Sequence[str], duration_ms: int | None) -> JobView:
        self._videos.require_video(ref)
        urls = _checked_urls(ref.platform, audio_urls)
        if self._speech.plan() is None:
            raise SpeechNotReadyError("no speech model installed")
        with self._lock:
            current = self._jobs.get(ref)
            if current is not None and not current.view().status.finished:
                return current.view()
            job = _Job(ref)
            self._jobs[ref] = job
            self._prune()
        job.handle = self._tasks.run_cancellable(
            f"transcribe:{ref.external_id}",
            lambda stop: self._run(job, urls, stop),
            timeout=_timeout(duration_ms),
        )
        return job.view()

    def cancel(self, ref: VideoRef) -> JobView | None:
        with self._lock:
            job = self._jobs.get(ref)
        if job is not None and job.handle is not None:
            job.handle.cancel()
        return self.job(ref)

    # ------------------------------------------------------------ the job
    def _run(self, job: _Job, urls: list[str], stop: threading.Event) -> None:
        audio = self._work_dir / f"{job.ref.platform}-{job.ref.external_id}.audio"
        try:
            self._wait_for_recognizer(stop)
            try:
                job.sm.transition(JobStatus.DOWNLOADING)
                self._fetch_audio(job.ref.platform, urls, audio, stop)
                job.sm.transition(JobStatus.RECOGNIZING)
                job.transcript = self._recognize(job, audio, stop)
                job.progress = 1.0
                job.sm.transition(JobStatus.DONE)
            finally:
                self._recognizer_busy.release()
        except Cancelled:
            job.sm.transition(JobStatus.CANCELLED)
        except SpeechError as exc:
            log.error("transcription of %s failed: %s", job.ref.external_id, exc)
            job.error = exc.user_message
            job.sm.transition(JobStatus.FAILED)
        except Exception:
            log.exception("transcription of %s failed", job.ref.external_id)
            job.error = "语音识别出错了，详细信息在日志里。"
            job.sm.transition(JobStatus.FAILED)
            raise
        finally:
            audio.unlink(missing_ok=True)
            audio.with_name(audio.name + ".part").unlink(missing_ok=True)

    def _wait_for_recognizer(self, stop: threading.Event) -> None:
        while not self._recognizer_busy.acquire(timeout=0.5):
            if stop.is_set():
                raise Cancelled("queued")

    def _fetch_audio(
        self, platform: Platform, urls: list[str], dest: Path, stop: threading.Event
    ) -> None:
        def progress(n: int) -> None:
            if n > MAX_AUDIO_BYTES:
                raise SpeechError(
                    f"audio larger than {MAX_AUDIO_BYTES} bytes", "这个视频的音频太大了。"
                )

        self._downloader.download(
            urls,
            dest,
            size=None,
            sha256=None,
            headers={"Referer": _REFERER[platform]},
            progress=progress,
            stop=stop,
        )

    def _recognize(self, job: _Job, audio: Path, stop: threading.Event) -> TranscriptInfo:
        plan = self._speech.plan()
        if plan is None:
            raise SpeechNotReadyError("the speech model was removed")
        try:
            words = self._recognize_with(job, plan, audio, stop)
        except GpuUnavailableError as exc:
            self._speech.gpu_failed(str(exc))
            words = self._recognize_with(job, plan.on_cpu(), audio, stop)
        cues = lines_from_words(words)
        if not cues:
            raise SpeechError(
                "no speech recognized", "没有识别出英文语音（视频可能只有音乐，或不是英语）。"
            )
        return self._videos.store_recognized(job.ref, cues, f"local-{plan.model.id}")

    def _recognize_with(
        self, job: _Job, plan: RecognitionPlan, audio: Path, stop: threading.Event
    ) -> list[Word]:
        job.model, job.device, job.progress = plan.model.id, plan.device, 0.0

        def progress(position_ms: int, duration_ms: int) -> None:
            if duration_ms > 0:
                job.progress = min(position_ms / duration_ms, 0.99)

        return list(self._recognizer.recognize(plan.request(audio), progress, stop))

    def _prune(self) -> None:
        done = [r for r, j in self._jobs.items() if j.view().status.finished]
        for r in done[: max(0, len(done) - KEEP_FINISHED)]:
            del self._jobs[r]


def _checked_urls(platform: Platform, urls: Sequence[str]) -> list[str]:
    hosts = _MEDIA_HOSTS.get(platform)
    if hosts is None:
        raise InvalidInputError(f"speech recognition is not available for {platform} videos")
    out: list[str] = []
    for url in list(urls)[:MAX_AUDIO_URLS]:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme == "https" and any(host.endswith(h) for h in hosts):
            out.append(url)
    if not out:
        raise InvalidInputError("no usable audio URL")
    return out


def _timeout(duration_ms: int | None) -> float:
    if not duration_ms:
        return UNKNOWN_DURATION_TIMEOUT_S
    return BASE_TIMEOUT_S + TIMEOUT_PER_AUDIO_S * duration_ms / 1000
