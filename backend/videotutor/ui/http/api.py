"""HTTP routes. Thin: parse the request, call an app service, shape the reply.

API compatibility: every route lives under /api and the protocol number in
/api/health (from product.toml) tells the extension what it is talking to.
See docs/ARCHITECTURE.md before changing a response shape.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from videotutor.app.setup import SetupService
from videotutor.core.product import Product

from .security import local_only_guard


@dataclass(frozen=True)
class ApiDeps:
    product: Product
    setup: SetupService
    on_activate: Callable[[], None]
    web_dir: Path


def create_app(deps: ApiDeps, port: int) -> FastAPI:
    product = deps.product
    app = FastAPI(
        title=product.name, version=product.version, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.middleware("http")(local_only_guard(port, product.extension_origin))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"app": product.name, "version": product.version, "protocol": product.protocol}

    @app.post("/api/activate")
    def activate() -> dict[str, bool]:
        """A second launch asks the running instance to show itself."""
        deps.on_activate()
        return {"ok": True}

    @app.get("/api/setup/info")
    def setup_info() -> dict[str, Any]:
        return asdict(deps.setup.info())

    @app.post("/api/setup/open-extension-folder")
    def open_extension_folder() -> dict[str, bool]:
        deps.setup.open_extension_folder()
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=deps.web_dir, html=True), name="ui")
    return app
