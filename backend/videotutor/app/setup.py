"""First-run setup use cases: what the wizard page and the tray menu need."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from videotutor.core.product import Product

from .ports import Desktop, Locations
from .settings import SettingsStore


@dataclass(frozen=True)
class SetupInfo:
    extension_dir: str
    extension_id: str
    home_dir: str
    first_run_done: bool
    settings_read_only: bool
    database_read_only: bool


class SetupService:
    def __init__(
        self,
        product: Product,
        locations: Locations,
        desktop: Desktop,
        settings: SettingsStore,
        database_read_only: Callable[[], bool] = lambda: False,
    ) -> None:
        self._database_read_only = database_read_only
        self._product = product
        self._loc = locations
        self._desktop = desktop
        self._settings = settings

    def info(self) -> SetupInfo:
        return SetupInfo(
            extension_dir=str(self._loc.extension_dir),
            extension_id=self._product.extension_id,
            home_dir=str(self._loc.home),
            first_run_done=self._settings.current().first_run_done,
            settings_read_only=self._settings.read_only,
            database_read_only=self._database_read_only(),
        )

    def open_extension_folder(self) -> None:
        self._desktop.open_path(self._loc.extension_dir)

    def open_logs_folder(self) -> None:
        self._desktop.open_path(self._loc.logs_dir)
