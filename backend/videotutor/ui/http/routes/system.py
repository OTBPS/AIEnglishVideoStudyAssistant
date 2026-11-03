"""Health, second-launch activation, first-run setup."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from videotutor.app.setup import SetupService
from videotutor.core.product import Product


def router(product: Product, setup: SetupService, on_activate: Callable[[], None]) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health() -> dict[str, Any]:
        return {"app": product.name, "version": product.version, "protocol": product.protocol}

    @r.post("/activate")
    def activate() -> dict[str, bool]:
        """A second launch asks the running instance to show itself."""
        on_activate()
        return {"ok": True}

    @r.get("/setup/info")
    def setup_info() -> dict[str, Any]:
        return asdict(setup.info())

    @r.post("/setup/open-extension-folder")
    def open_extension_folder() -> dict[str, bool]:
        setup.open_extension_folder()
        return {"ok": True}

    return r
