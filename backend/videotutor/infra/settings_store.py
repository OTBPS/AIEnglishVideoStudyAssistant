"""settings.json on disk: migrations, validation, and recovery.

The file is the user's, not ours, so loading never fails:

* missing          -> defaults, written out;
* unreadable / not a JSON object / bad schema_version
                   -> moved aside as settings.json.corrupt-<time>, defaults;
* an invalid field -> only that field falls back to its default;
* older schema     -> migrated step by step; the original is kept as
                      settings.json.v<N>.bak;
* newer schema     -> written by a newer VideoTutor (the user downgraded):
                      read what we understand, and never write the file, so
                      upgrading again finds the newer settings intact.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from videotutor.app.settings import MIGRATIONS, SCHEMA_VERSION, Migration, Settings

from .atomic import write_text_atomic

log = logging.getLogger("settings")


class SettingsReadOnlyError(RuntimeError):
    """The settings file belongs to a newer version and must not be overwritten."""


@dataclass(frozen=True)
class LoadReport:
    created: bool = False
    migrated_from: int | None = None
    corrupt_backup: Path | None = None
    dropped_fields: tuple[str, ...] = ()
    read_only: bool = False
    notes: list[str] = field(default_factory=list)


class JsonSettingsStore:
    def __init__(
        self,
        path: Path,
        *,
        schema_version: int = SCHEMA_VERSION,
        migrations: dict[int, Migration] | None = None,
    ) -> None:
        self.path = path
        self._schema_version = schema_version
        self._migrations = MIGRATIONS if migrations is None else migrations
        self._settings = Settings()
        self._read_only = False
        self._lock = threading.Lock()

    @property
    def read_only(self) -> bool:
        return self._read_only

    def current(self) -> Settings:
        return self._settings

    def update(self, **changes: Any) -> Settings:
        with self._lock:
            merged = {**self._settings.model_dump(), **changes}
            new = Settings.model_validate(merged)  # raises on bad input
            self._save(new)
            self._settings = new
            return new

    # ------------------------------------------------------------------ load
    def load(self) -> LoadReport:
        with self._lock:
            report = self._load()
        for note in report.notes:
            log.warning("%s", note)
        return report

    def _load(self) -> LoadReport:
        if not self.path.exists():
            self._settings = Settings()
            self._save(self._settings)
            return LoadReport(created=True)

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("top level is not a JSON object")
            version = raw.pop("schema_version", None)
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise ValueError(f"bad schema_version {version!r}")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return self._recover_corrupt(exc)

        notes: list[str] = []
        migrated_from: int | None = None
        if version > self._schema_version:
            self._read_only = True
            notes.append(
                f"{self.path.name} has schema {version}, newer than this version "
                f"understands ({self._schema_version}); it will not be overwritten"
            )
        elif version < self._schema_version:
            try:
                raw = self._migrate(raw, version)
            except Exception as exc:  # noqa: BLE001 -- any migration failure means: recover
                return self._recover_corrupt(exc)
            migrated_from = version
            notes.append(
                f"migrated {self.path.name} from schema {version} to {self._schema_version}"
            )

        settings, dropped = _validate_leniently(raw)
        if dropped:
            notes.append(f"invalid setting(s) reset to default: {', '.join(dropped)}")
        self._settings = settings
        if not self._read_only and (migrated_from is not None or dropped):
            self._save(settings)
        return LoadReport(
            migrated_from=migrated_from,
            dropped_fields=dropped,
            read_only=self._read_only,
            notes=notes,
        )

    def _migrate(self, raw: dict[str, Any], version: int) -> dict[str, Any]:
        backup = self.path.with_name(f"{self.path.name}.v{version}.bak")
        shutil.copy2(self.path, backup)
        for v in range(version, self._schema_version):
            step = self._migrations.get(v)
            if step is None:
                raise LookupError(f"no settings migration from schema {v}")
            raw = step(dict(raw))
        return raw

    def _recover_corrupt(self, exc: BaseException) -> LoadReport:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        moved: Path | None = backup
        try:
            os.replace(self.path, backup)
        except OSError:
            moved = None
        self._settings = Settings()
        self._read_only = False
        self._save(self._settings)
        return LoadReport(
            corrupt_backup=moved,
            notes=[
                f"{self.path.name} was unreadable ({exc}); defaults restored, "
                f"original kept as {moved.name if moved else '(could not move)'}"
            ],
        )

    # ------------------------------------------------------------------ save
    def _save(self, settings: Settings) -> None:
        if self._read_only:
            raise SettingsReadOnlyError(
                f"{self.path} was written by a newer VideoTutor; refusing to overwrite it"
            )
        doc = {"schema_version": self._schema_version, **settings.model_dump()}
        write_text_atomic(self.path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def _validate_leniently(raw: dict[str, Any]) -> tuple[Settings, tuple[str, ...]]:
    """Validate; a field that fails falls back to its default, not the whole file."""
    data = dict(raw)
    dropped: list[str] = []
    for _ in range(len(Settings.model_fields) + 1):
        try:
            return Settings.model_validate(data), tuple(dropped)
        except ValidationError as exc:
            bad = {str(err["loc"][0]) for err in exc.errors() if err["loc"]}
            bad &= set(data)
            if not bad:
                break
            for key in sorted(bad):
                data.pop(key)
                dropped.append(key)
    return Settings(), tuple(sorted(raw))
