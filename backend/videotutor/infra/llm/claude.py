"""Claude via the official Anthropic SDK, with structured JSON output.

Choices (see the claude-api reference used to write this):

* `output_config.format` (JSON schema) guarantees the first text block is
  valid JSON matching the schema; we still validate it in the app layer.
* `effort: "low"`: glossing words and writing three questions is simple work;
  lower effort is cheaper and faster on the same model.
* Server-side refusal fallback (`fallbacks: "default"`) is opted into where the
  model supports it, so a classifier false positive on a transcript does not
  just fail. A final `stop_reason == "refusal"` is still handled.
* The SDK retries connection errors, 408/409/429 and 5xx itself (max_retries).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from videotutor.app.errors import (
    LlmAuthError,
    LlmOutputError,
    LlmRefusedError,
    LlmRequestError,
    LlmUnavailableError,
)

log = logging.getLogger("llm.claude")

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept `fallbacks: "default"` on the Claude API (Haiku has none).
_FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
)


class ClaudeLanguageModel:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        effort: str = "low",
        timeout: float = 90.0,
        max_retries: int = 2,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self._effort = effort
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    @property
    def model_name(self) -> str:
        return self._model

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        try:
            if self._model in _FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    **request, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LlmAuthError(str(exc)) from exc
        except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
            raise LlmRequestError(str(exc)) from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise LlmUnavailableError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LlmUnavailableError(str(exc)) from exc
            raise LlmRequestError(str(exc)) from exc

        log.info(
            "claude %s: %s in / %s out tokens, stop=%s, request=%s",
            response.model,
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.stop_reason,
            getattr(response, "_request_id", "?"),
        )
        if response.stop_reason == "refusal":
            raise LlmRefusedError(f"refused ({getattr(response, 'stop_details', None)})")
        if response.stop_reason == "max_tokens":
            raise LlmOutputError("answer was cut off at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LlmOutputError("no text block in the response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LlmOutputError(f"not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LlmOutputError("JSON answer is not an object")
        return data
