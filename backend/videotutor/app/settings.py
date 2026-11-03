"""User settings: schema, defaults, validation and migrations.

Rules for changing this file (backward compatibility):

* Adding a field: give it a default. No migration needed.
* Renaming / removing / changing the meaning of a field: bump SCHEMA_VERSION
  and add MIGRATIONS[old_version], a pure dict -> dict function. Never edit a
  migration that has shipped.
* The store (infra/settings_store.py) handles reading, corruption recovery
  and refusing to overwrite a file written by a newer version.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# MIGRATIONS[n] turns a version-n document into a version-(n + 1) document.
MIGRATIONS: dict[int, Migration] = {}


class Settings(BaseModel):
    # strict: "yes" must not silently become True; extra="ignore": fields
    # from a newer version are dropped on read instead of failing the load.
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    open_home_on_start: bool = True
    """Open the home page when the app is launched by hand (not by autostart)."""

    first_run_done: bool = False
    """The setup wizard has been completed once."""

    # Added in 0.3.0 (schema 1 still: new fields with defaults need no migration).
    vocabulary_level: int = Field(default=3000, ge=500, le=40000)
    """Words up to this frequency rank count as known unless marked otherwise.
    3000 word families give ~95% coverage of TV English (Webb & Rodgers)."""

    auto_prewatch: bool = True
    """Generate glosses and questions as soon as a video's transcript is stored."""

    llm_provider: Literal["claude", "openai_compatible"] = "claude"
    llm_model: str = Field(default="claude-opus-5-5", min_length=1, max_length=100)
    llm_base_url: str = Field(default="", max_length=500)
    """Only for openai_compatible, e.g. https://api.deepseek.com/v1."""


class SettingsStore(Protocol):
    @property
    def read_only(self) -> bool: ...

    def current(self) -> Settings: ...

    def update(self, **changes: Any) -> Settings: ...
