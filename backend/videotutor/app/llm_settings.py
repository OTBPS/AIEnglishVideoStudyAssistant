"""Configuring the AI provider: which model, which key, does it work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .errors import InvalidInputError, LlmNotConfiguredError
from .ports import LanguageModelProvider, SecretStore
from .settings import SettingsStore

Provider = Literal["claude", "openai_compatible"]

_PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def secret_name(provider: str) -> str:
    """Name of the API key in the secret store (one key per provider)."""
    return f"llm-api-key:{provider}"


@dataclass(frozen=True)
class LlmConfig:
    provider: Provider
    model: str
    base_url: str
    has_key: bool


class LlmSettingsService:
    def __init__(
        self, settings: SettingsStore, secrets: SecretStore, models: LanguageModelProvider
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._models = models

    def config(self) -> LlmConfig:
        s = self._settings.current()
        return LlmConfig(
            s.llm_provider,
            s.llm_model,
            s.llm_base_url,
            bool(self._secrets.get(secret_name(s.llm_provider))),
        )

    def configure(
        self, provider: Provider, model: str, base_url: str, api_key: str | None
    ) -> LlmConfig:
        model, base_url = model.strip(), base_url.strip()
        if not model:
            raise InvalidInputError("model name is required")
        if provider == "openai_compatible" and not base_url.startswith("https://"):
            raise InvalidInputError("an OpenAI-compatible provider needs an https:// base URL")
        if api_key is not None:
            key = api_key.strip()
            if not key or any(c.isspace() for c in key):
                raise InvalidInputError("the API key looks malformed")
            self._secrets.set(secret_name(provider), key)
        self._settings.update(llm_provider=provider, llm_model=model, llm_base_url=base_url)
        return self.config()

    def remove_key(self) -> LlmConfig:
        self._secrets.delete(secret_name(self._settings.current().llm_provider))
        return self.config()

    def test(self) -> None:
        """One tiny request. Raises an LlmError subclass describing what is wrong."""
        model = self._models.current()
        if model is None:
            raise LlmNotConfiguredError("no API key configured")
        model.generate_json(
            system="You are a connectivity check.",
            prompt='Reply with {"ok": true}.',
            schema=_PING_SCHEMA,
            max_tokens=2000,
        )
