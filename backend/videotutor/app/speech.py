"""Speech-recognition setup: which components are installed, installing and
removing them in the background, and how a recognition should run (which
model, CPU or GPU).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from videotutor.core.tasks import TaskHandle, TaskState, TaskSupervisor
from videotutor.domain.speech import Component, ComponentKind, choose_model

from .errors import Cancelled, DiskFullError, InvalidInputError, NotFoundError, SpeechError
from .ports import ComponentStore, Downloader, HardwareProbe, NvidiaGpu, RecognitionRequest

log = logging.getLogger("speech")

# Room for unpacking archives next to the download, plus a margin.
SPACE_FACTOR = 2.0
SPACE_MARGIN = 500_000_000
MIN_GPU_MEMORY_MB = 3500  # large-v3-turbo in float16 needs about 3 GB


class InstallStatus(StrEnum):
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"


@dataclass(frozen=True)
class ComponentView:
    component: Component
    status: InstallStatus
    downloaded_bytes: int
    error: str | None
    recommended: bool


@dataclass(frozen=True)
class GpuStatus:
    gpu: NvidiaGpu | None
    usable: bool  # present, big enough, runtime installed, and not known to fail
    failure: str | None  # why the last attempt to use it failed, this run


@dataclass(frozen=True)
class SpeechOverview:
    components: tuple[ComponentView, ...]
    gpu: GpuStatus
    model: Component | None  # what a recognition would use now; None = not ready

    @property
    def ready(self) -> bool:
        return self.model is not None


@dataclass(frozen=True)
class RecognitionPlan:
    model: Component
    model_dir: Path
    gpu_runtime_dir: Path | None  # None: run on the CPU

    @property
    def device(self) -> str:
        return "cpu" if self.gpu_runtime_dir is None else "cuda"

    def request(self, audio: Path) -> RecognitionRequest:
        return RecognitionRequest(audio, self.model_dir, self.device, self.gpu_runtime_dir)

    def on_cpu(self) -> RecognitionPlan:
        return RecognitionPlan(self.model, self.model_dir, None)


class _Install:
    def __init__(self) -> None:
        self.handle: TaskHandle[None] | None = None
        self.downloaded = 0

    @property
    def running(self) -> bool:
        return self.handle is not None and not self.handle.state.finished


class SpeechSetup:
    def __init__(
        self,
        models: Sequence[Component],
        gpu_runtime: Component,
        store: ComponentStore,
        downloader: Downloader,
        hardware: HardwareProbe,
        tasks: TaskSupervisor,
    ) -> None:
        self._models = tuple(models)
        self._gpu_runtime = gpu_runtime
        self._catalog = {c.id: c for c in (*self._models, gpu_runtime)}
        self._store = store
        self._downloader = downloader
        self._hardware = hardware
        self._tasks = tasks
        self._installs: dict[str, _Install] = {}
        self._gpu_failure: str | None = None
        self._lock = (
            threading.Lock()
        )  # guards _installs and _gpu_failure; never held while calling out
        self._install_lock = threading.Lock()  # serializes install() itself

    # ------------------------------------------------------------ queries
    def overview(self) -> SpeechOverview:
        gpu = self._gpu_status()
        recommended = self._recommended(gpu.gpu)
        views = tuple(self._view(c, c.id in recommended) for c in self._catalog.values())
        return SpeechOverview(views, gpu, self._choose(gpu.usable))

    def plan(self) -> RecognitionPlan | None:
        """How to recognize speech right now, or None if no model is installed."""
        gpu = self._gpu_status().usable
        model = self._choose(gpu)
        if model is None:
            return None
        runtime = self._store.directory(self._gpu_runtime) if gpu else None
        return RecognitionPlan(model, self._store.directory(model), runtime)

    def gpu_failed(self, reason: str) -> None:
        """The GPU could not run the model: use the CPU for the rest of this run."""
        log.warning("GPU recognition unavailable, using the CPU: %s", reason)
        with self._lock:
            self._gpu_failure = reason

    # ------------------------------------------------------------ commands
    def install(self, component_id: str) -> ComponentView:
        component = self._require(component_id)
        with self._install_lock:  # check-then-start is one step: a double click starts one job
            if self._running(component_id) or self._store.is_installed(component):
                return self._view(component, False)
            needed = int(component.download_bytes * SPACE_FACTOR) + SPACE_MARGIN
            if self._store.free_bytes() < needed:
                raise DiskFullError(
                    f"{component.id} needs {needed} bytes",
                    user_message=f"磁盘空间不足：安装需要约 {needed / 1e9:.1f} GB 可用空间。",
                )
            job = _Install()

            def run(stop: threading.Event) -> None:
                def progress(n: int) -> None:
                    job.downloaded = n

                self._store.install(component, self._downloader, progress, stop)
                if component.kind is ComponentKind.GPU_RUNTIME:
                    with self._lock:
                        self._gpu_failure = None  # a fresh runtime deserves a fresh try

            job.handle = self._tasks.run_cancellable(f"install:{component.id}", run, timeout=None)
            with self._lock:
                self._installs[component_id] = job
        return self._view(component, False)

    def cancel(self, component_id: str) -> ComponentView:
        component = self._require(component_id)
        with self._lock:
            job = self._installs.get(component_id)
        if job is not None and job.handle is not None:
            job.handle.cancel()
        return self._view(component, False)

    def remove(self, component_id: str) -> ComponentView:
        component = self._require(component_id)
        with self._lock:
            job = self._installs.get(component_id)
            if job is not None and job.running:
                raise InvalidInputError(f"{component_id} is being installed")
            self._installs.pop(component_id, None)
        self._store.remove(component)
        return self._view(component, False)

    # ------------------------------------------------------------ internals
    def _running(self, component_id: str) -> bool:
        with self._lock:
            job = self._installs.get(component_id)
        return job is not None and job.running

    def _require(self, component_id: str) -> Component:
        component = self._catalog.get(component_id)
        if component is None:
            raise NotFoundError(f"unknown component {component_id!r}")
        return component

    def _view(self, component: Component, recommended: bool) -> ComponentView:
        with self._lock:
            job = self._installs.get(component.id)
        if job is not None and job.running:
            return ComponentView(
                component, InstallStatus.INSTALLING, job.downloaded, None, recommended
            )
        if self._store.is_installed(component):
            return ComponentView(
                component, InstallStatus.INSTALLED, component.download_bytes, None, recommended
            )
        error = _install_error(job.handle) if job is not None and job.handle else None
        status = InstallStatus.FAILED if error else InstallStatus.NOT_INSTALLED
        downloaded = job.downloaded if job is not None else 0
        return ComponentView(component, status, downloaded, error, recommended)

    def _gpu_status(self) -> GpuStatus:
        gpu = self._hardware.nvidia_gpu()
        with self._lock:
            failure = self._gpu_failure
        usable = (
            gpu is not None
            and gpu.memory_mb >= MIN_GPU_MEMORY_MB
            and failure is None
            and self._store.is_installed(self._gpu_runtime)
        )
        return GpuStatus(gpu, usable, failure)

    def _choose(self, gpu: bool) -> Component | None:
        installed = [m for m in self._models if self._store.is_installed(m)]
        return choose_model(installed, gpu=gpu)

    def _recommended(self, gpu: NvidiaGpu | None) -> set[str]:
        if gpu is not None and gpu.memory_mb >= MIN_GPU_MEMORY_MB:
            best = ([m for m in self._models if m.prefers_gpu] or list(self._models))[:1]
            return {*(m.id for m in best), self._gpu_runtime.id}
        return {m.id for m in [m for m in self._models if not m.prefers_gpu][:1]}


def _install_error(handle: TaskHandle[None]) -> str | None:
    if handle.state in (TaskState.SUCCEEDED, TaskState.CANCELLED):
        return None
    try:
        handle.result(timeout=0)
    except Cancelled:
        return None
    except SpeechError as exc:
        return exc.user_message
    except Exception:  # noqa: BLE001 -- details are in the log
        return "安装失败，详细信息在日志里。"
    return None
