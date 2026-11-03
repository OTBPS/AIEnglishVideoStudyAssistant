"""Assembles the HTTP application: security guard, routers, error mapping, static pages.

Routes stay thin -- parse, call an app service, shape the reply -- and live in
routes/<area>.py. API compatibility: every route is under /api, and the
protocol number in /api/health (from product.toml) tells the extension what it
is talking to. See docs/ARCHITECTURE.md before changing a response shape.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from videotutor.app.errors import InvalidInputError, NotFoundError, StorageReadOnlyError
from videotutor.app.setup import SetupService
from videotutor.app.videos import VideoService
from videotutor.core.product import Product

from .routes import system, videos
from .security import local_only_guard


@dataclass(frozen=True)
class ApiDeps:
    product: Product
    setup: SetupService
    videos: VideoService
    on_activate: Callable[[], None]
    web_dir: Path


_ERRORS: dict[type[Exception], int] = {
    NotFoundError: 404,
    StorageReadOnlyError: 409,
    InvalidInputError: 422,
}


def create_app(deps: ApiDeps, port: int) -> FastAPI:
    product = deps.product
    app = FastAPI(
        title=product.name, version=product.version, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.middleware("http")(local_only_guard(port, product.extension_origin))

    for exc_type, status in _ERRORS.items():

        def handler(_: Request, exc: Exception, status: int = status) -> JSONResponse:
            return JSONResponse({"error": type(exc).__name__, "detail": str(exc)}, status)

        app.add_exception_handler(exc_type, handler)

    app.include_router(system.router(product, deps.setup, deps.on_activate))
    app.include_router(videos.router(deps.videos))

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=deps.web_dir, html=True), name="ui")
    return app
