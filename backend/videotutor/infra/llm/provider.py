"""Builds the configured LanguageModel from settings + the secret store.

Rebuilt only when the configuration actually changes, so a new key takes
effect immediately without restarting the app.
"""

from __future__ import annotations

import hashlib
import threading

from videotutor.app.llm_settings import secret_name
from videotutor.app.ports import LanguageModel, SecretStore
from videotutor.app.settings import SettingsStore

from .claude import ClaudeLanguageModel
from .openai_compat import OpenAICompatibleLanguageModel


class ConfiguredLanguageModelProvider:
    def __init__(self, settings: SettingsStore, secrets: SecretStore) -> None:
        self._settings = settings
        self._secrets = secrets
        self._lock = threading.Lock()
        self._cached: tuple[str, LanguageModel] | None = None

    def current(self) -> LanguageModel | None:
        s = self._settings.current()
        key = self._secrets.get(secret_name(s.llm_provider))
        if not key:
            return None
        # The fingerprint includes a hash of the key, never the key itself.
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        fingerprint = f"{s.llm_provider}|{s.llm_model}|{s.llm_base_url}|{digest}"
        with self._lock:
            if self._cached and self._cached[0] == fingerprint:
                return self._cached[1]
            model: LanguageModel
            if s.llm_provider == "claude":
                model = ClaudeLanguageModel(key, s.llm_model)
            else:
                model = OpenAICompatibleLanguageModel(s.llm_base_url, key, s.llm_model)
            self._cached = (fingerprint, model)
            return model
