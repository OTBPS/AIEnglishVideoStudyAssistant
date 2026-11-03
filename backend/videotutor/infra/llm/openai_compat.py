"""Any provider with an OpenAI-compatible /chat/completions endpoint.

For learners who cannot reach the Claude API from where they live (the plan's
deployment constraint); e.g. DeepSeek or Qwen. Plain urllib: one POST does
not justify another HTTP dependency. Structured output support differs
between providers, so we ask for `json_object`, put the schema in the prompt,
and leave validation to the app layer (which rejects anything off-schema).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from videotutor.app.errors import (
    LlmAuthError,
    LlmOutputError,
    LlmRequestError,
    LlmUnavailableError,
)

log = logging.getLogger("llm.compat")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class OpenAICompatibleLanguageModel:
    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 90.0) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise LlmRequestError(f"base URL must start with https:// ({base_url!r})")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"{prompt}\n\nReply with one JSON object only, matching this "
                    f"JSON schema:\n{json.dumps(schema, ensure_ascii=False)}",
                },
            ],
        }
        req = urllib.request.Request(  # noqa: S310 -- scheme checked in __init__
            self._url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 -- https/http checked
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LlmAuthError(f"HTTP {exc.code}") from exc
            if exc.code == 429 or exc.code >= 500:
                raise LlmUnavailableError(f"HTTP {exc.code}") from exc
            raise LlmRequestError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LlmUnavailableError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise LlmOutputError("provider returned non-JSON") from exc

        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmOutputError("unexpected response shape") from exc
        if choice.get("finish_reason") == "length":
            raise LlmOutputError("answer was cut off at max_tokens")
        log.info("compat %s: usage=%s", self._model, payload.get("usage"))
        try:
            data = json.loads(_FENCE.sub("", str(text).strip()))
        except json.JSONDecodeError as exc:
            raise LlmOutputError(f"not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LlmOutputError("JSON answer is not an object")
        return data
