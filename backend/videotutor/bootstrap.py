"""Composition root: the only module that knows every concrete class.

It builds the infrastructure adapters, hands them to the app services
through their ports, mounts the UI, and registers each component's stop
hook with the lifecycle in start order, so shutdown runs in reverse.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from videotutor.app.lifecycle import AppState, Lifecycle
from videotutor.app.ports import Desktop, Locations
from videotutor.app.setup import SetupService
from videotutor.app.videos import VideoService
from videotutor.core.product import Product
from videotutor.core.tasks import TaskSupervisor
from videotutor.infra.captions.youtube import YouTubeCaptionDecoder
from videotutor.infra.db.database import Database
from videotutor.infra.db.repositories import (
    SqliteTranscriptRepository,
    SqliteVideoRepository,
    SqliteWatchRepository,
)
from videotutor.infra.desktop import WindowsDesktop
from videotutor.infra.http_server import HttpServer, bind_first_free
from videotutor.infra.settings_store import JsonSettingsStore
from videotutor.infra.single_instance import RuntimeFile
from videotutor.ui.http.api import ApiDeps, create_app

if TYPE_CHECKING:
    from videotutor.ui.tray import Tray

log = logging.getLogger("bootstrap")


@dataclass(frozen=True)
class Options:
    tray: bool = True
    open_browser: bool = True  # False when launched by autostart


class Application:
    def __init__(
        self,
        product: Product,
        locations: Locations,
        options: Options,
        *,
        desktop: Desktop | None = None,
    ) -> None:
        self.product = product
        self.locations = locations
        self.options = options
        self.desktop: Desktop = desktop or WindowsDesktop()
        self.lifecycle = Lifecycle()
        self.settings = JsonSettingsStore(locations.settings_file)
        self.tasks = TaskSupervisor()
        self.setup = SetupService(
            product, locations, self.desktop, self.settings, self._database_read_only
        )
        self._db: Database | None = None
        self._videos: VideoService | None = None
        self._runtime = RuntimeFile(locations.runtime_file)
        self._server: HttpServer | None = None
        self._tray: Tray | None = None
        self._quit = threading.Event()

    @property
    def videos(self) -> VideoService:
        if self._videos is None:
            raise RuntimeError("application not started")
        return self._videos

    def _database_read_only(self) -> bool:
        return self._db is not None and self._db.read_only

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("application not started")
        return self._server.url

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.lifecycle.begin_start()
        try:
            self.settings.load()

            db, report = Database.open(self.locations.database_file)
            self._db = db
            self.lifecycle.add_stop_hook("database", db.close)
            self._videos = VideoService(
                SqliteVideoRepository(db),
                SqliteTranscriptRepository(db),
                SqliteWatchRepository(db),
                YouTubeCaptionDecoder(),
            )
            if not db.read_only:
                closed = self._videos.close_unfinished_sessions()
                if closed:
                    log.info("closed %d watch session(s) left open by the last run", closed)
            log.info("database %s (schema %d)", db.path, report.version)

            self.tasks.start()
            self.lifecycle.add_stop_hook("tasks", self.tasks.shutdown)

            sock = bind_first_free(self.product.ports)
            port = sock.getsockname()[1]
            api = create_app(
                ApiDeps(
                    self.product, self.setup, self._videos, self.activate, self.locations.web_dir
                ),
                port,
            )
            server = HttpServer(api, sock)
            self.lifecycle.add_stop_hook("http", server.stop)
            server.start()
            self._server = server

            self._runtime.write(port)
            self.lifecycle.add_stop_hook("runtime-file", self._runtime.remove_if_ours)
        except BaseException:
            log.exception("startup failed")
            self.lifecycle.mark_failed()
            self.lifecycle.stop()
            raise
        self.lifecycle.mark_running()

    def stop(self) -> list[str]:
        return self.lifecycle.stop()

    def run(self) -> int:
        """Start, block until the user quits, stop. Returns the exit code."""
        self.start()
        try:
            if self.options.open_browser and self.settings.current().open_home_on_start:
                self.activate()
            if self.options.tray:
                self._run_tray()
            else:
                self._wait_for_quit()
        finally:
            failed = self.stop()
        return 1 if failed else 0

    # ------------------------------------------------------------ actions
    def activate(self) -> None:
        """Bring the app to the user's attention: open its home page."""
        self.desktop.open_url(self.url)

    def request_quit(self) -> None:
        self._quit.set()
        if self._tray is not None:
            self._tray.stop()

    # ------------------------------------------------------------ blocking
    def _run_tray(self) -> None:
        from videotutor.ui.tray import Tray, TrayActions

        self._tray = Tray(
            self.product.name,
            self.product.version,
            self.locations.icon_file,
            TrayActions(
                open_home=self.activate,
                open_extension_folder=self.setup.open_extension_folder,
                open_logs=self.setup.open_logs_folder,
                quit=self.request_quit,
            ),
        )
        if not self._quit.is_set():
            self._tray.run()

    def _wait_for_quit(self) -> None:
        try:
            while not self._quit.wait(0.5):
                pass
        except KeyboardInterrupt:
            log.info("interrupted")


class NullDesktop:
    """Desktop that does nothing: smoke tests must not open windows."""

    def open_path(self, path: object) -> None:
        pass

    def open_url(self, url: str) -> None:
        pass

    def message(self, text: str, title: str, *, error: bool = False) -> None:
        pass


def smoke_test(product: Product, locations: Locations) -> int:
    """Start everything headless, exercise it end to end, stop. Exit code 0 = healthy.

    Run against the *packaged* exe by tools/build.py: it catches what unit
    tests cannot -- a module PyInstaller left out, web assets or product.toml
    missing from the bundle.
    """
    app = Application(
        product, locations, Options(tray=False, open_browser=False), desktop=NullDesktop()
    )
    app.start()
    problems: list[str] = []
    try:
        with urllib.request.urlopen(app.url + "api/health", timeout=5) as resp:  # noqa: S310
            health = json.load(resp)
        if health.get("version") != product.version:
            problems.append(f"health reports {health!r}")
        with urllib.request.urlopen(app.url + "ui/", timeout=5) as resp:  # noqa: S310
            if b"<html" not in resp.read(2048).lower():
                problems.append("home page is not HTML")

        async def ping() -> str:
            return "pong"

        if app.tasks.submit("smoke-ping", ping).result(timeout=5) != "pong":
            problems.append("task supervisor round trip failed")
        problems += _smoke_database(app.videos)
        if not locations.icon_file.exists():
            problems.append(f"missing {locations.icon_file}")
    finally:
        failed_hooks = app.stop()
    problems += [f"stop hook failed: {h}" for h in failed_hooks]
    if app.lifecycle.state is not AppState.STOPPED:
        problems.append(f"lifecycle ended in {app.lifecycle.state}")
    for p in problems:
        log.error("smoke test: %s", p)
    log.info("smoke test %s", "FAILED" if problems else "passed")
    return 1 if problems else 0


def _smoke_database(videos: VideoService) -> list[str]:
    """Open a video, store captions, read them back, follow a little playback."""
    from datetime import UTC, datetime

    from videotutor.domain.video import CaptionTrack, Platform, TrackKind, VideoRef
    from videotutor.domain.watch import WatchEvent, WatchEventType, WatchState

    ref = VideoRef(Platform.YOUTUBE, "smoketest00")
    track = CaptionTrack(".en", "en", TrackKind.MANUAL)
    opened = videos.open_video(ref, title="smoke", url="", duration_ms=10_000, tracks=[track])
    body = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]})
    videos.store_captions(ref, track, "json3", body)
    problems: list[str] = []
    if videos.transcript(ref).segments[0].text != "hi":
        problems.append("transcript round trip failed")
    now = datetime.now(UTC)
    session = videos.record_events(
        opened.session_id,
        [WatchEvent(WatchEventType.PLAY, 0, now), WatchEvent(WatchEventType.PAUSE, 1000, now)],
    )
    if (session.state, session.watched_ms) != (WatchState.PAUSED, 1000):
        problems.append(f"watch session ended as {session.state}/{session.watched_ms}")
    return problems
