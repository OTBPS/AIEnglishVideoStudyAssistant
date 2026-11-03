"""Assembles the HTTP application: security guard, routers, error mapping, static pages.

Routes stay thin -- parse, call an app service, shape the reply -- and live in
routes/<area>.py. API compatibility: every route is under /api, and the
protocol number in /api/health (from product.toml) tells the extension what it
is talking to. See docs/ARCHITECTURE.md before changing a response shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from videotutor.app.comprehension import ComprehensionService
from videotutor.app.errors import (
    InvalidInputError,
    LlmError,
    LlmNotConfiguredError,
    LlmUnavailableError,
    NotFoundError,
    StorageReadOnlyError,
)
from videotutor.app.listening import ListeningService
from videotutor.app.llm_settings import LlmSettingsService
from videotutor.app.prewatch import PrewatchService
from videotutor.app.review import ReviewService
from videotutor.app.settings import SettingsStore
from videotutor.app.setup import SetupService
from videotutor.app.videos import VideoService
from videotutor.app.vocabulary import VocabularyService
from videotutor.core.product import Product
from videotutor.core.tasks import TaskSupervisor, TaskTimeout

from .routes import learning, listening, review, settings, system, videos
from .security import local_only_guard


@dataclass(frozen=True)
class ApiDeps:
    product: Product
    setup: SetupService
    videos: VideoService
    vocabulary: VocabularyService
    prewatch: PrewatchService
    review: ReviewService
    comprehension: ComprehensionService
    listening: ListeningService
    settings: SettingsStore
    llm_settings: LlmSettingsService
    tasks: TaskSupervisor
    on_activate: Callable[[], None]
    web_dir: Path


# Starlette uses the handler of the closest class in the exception's MRO.
_ERRORS: dict[type[Exception], int] = {
    NotFoundError: 404,
    StorageReadOnlyError: 409,
    InvalidInputError: 422,
    LlmNotConfiguredError: 409,
    LlmUnavailableError: 503,
    LlmError: 502,
    TaskTimeout: 504,
}


def _detail(exc: Exception) -> str:
    # Language-model errors carry a message written for the learner; the raw
    # provider text stays in the log.
    return exc.user_message if isinstance(exc, LlmError) else str(exc)


def create_app(deps: ApiDeps, port: int) -> FastAPI:
    product = deps.product
    app = FastAPI(
        title=product.name, version=product.version, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.middleware("http")(local_only_guard(port, product.extension_origin))

    @app.middleware("http")
    async def revalidate_pages(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # After an upgrade the browser must not keep running cached old page
        # scripts against the new API: always revalidate (cheap on localhost).
        response = await call_next(request)
        if request.url.path.startswith("/ui"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    for exc_type, status in _ERRORS.items():

        def handler(_: Request, exc: Exception, status: int = status) -> JSONResponse:
            return JSONResponse({"error": type(exc).__name__, "detail": _detail(exc)}, status)

        app.add_exception_handler(exc_type, handler)

    app.include_router(system.router(product, deps.setup, deps.on_activate))
    app.include_router(videos.router(deps.videos))
    app.include_router(learning.router(deps.vocabulary, deps.prewatch, deps.review, deps.tasks))
    app.include_router(review.router(deps.review, deps.comprehension, deps.tasks))
    app.include_router(listening.router(deps.listening))
    app.include_router(settings.router(deps.settings, deps.llm_settings, deps.tasks))

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=deps.web_dir, html=True), name="ui")
    return app
