"""Language-model adapters. No network: the Anthropic client is replaced by a
recorder, and the OpenAI-compatible provider by a local HTTP server."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import Any, ClassVar

import anthropic
import httpx2
import pytest

from videotutor.app.errors import (
    LlmAuthError,
    LlmOutputError,
    LlmRefusedError,
    LlmRequestError,
    LlmUnavailableError,
)
from videotutor.app.llm_settings import secret_name
from videotutor.app.settings import Settings
from videotutor.infra.llm.claude import FALLBACK_BETA, ClaudeLanguageModel
from videotutor.infra.llm.openai_compat import OpenAICompatibleLanguageModel
from videotutor.infra.llm.provider import ConfiguredLanguageModelProvider

from .conftest import InMemorySecretStore

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


# ---------------------------------------------------------------- Claude
def _message(text: str | None, stop: str = "end_turn") -> SimpleNamespace:
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        content=content,
        stop_reason=stop,
        stop_details=None,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class Recorder:
    def __init__(self, result: object) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _client(result: object) -> tuple[Any, Recorder, Recorder]:
    plain, beta = Recorder(result), Recorder(result)
    client = SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=beta))
    return client, plain, beta


def _api_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


def test_claude_sends_structured_request_with_fallback() -> None:
    client, plain, beta = _client(_message('{"ok": true}'))
    model = ClaudeLanguageModel("k", client=client)
    assert model.generate_json(system="s", prompt="p", schema=SCHEMA, max_tokens=100) == {
        "ok": True
    }
    sent = beta.kwargs
    assert plain.kwargs == {}
    assert sent["model"] == "claude-opus-5-5"
    assert sent["betas"] == [FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert sent["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert sent["messages"] == [{"role": "user", "content": "p"}] and sent["system"] == "s"
    assert "thinking" not in sent and "temperature" not in sent


def test_claude_models_without_fallback_use_the_plain_endpoint() -> None:
    client, plain, beta = _client(_message('{"ok": true}'))
    ClaudeLanguageModel("k", "claude-haiku-5-5", client=client).generate_json(
        system="s", prompt="p", schema=SCHEMA, max_tokens=100
    )
    assert plain.kwargs["model"] == "claude-haiku-5-5" and "fallbacks" not in plain.kwargs
    assert beta.kwargs == {}


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (_message('{"ok": true}', stop="refusal"), LlmRefusedError),
        (_message('{"ok": tr', stop="max_tokens"), LlmOutputError),
        (_message(None), LlmOutputError),
        (_message("not json"), LlmOutputError),
        (_message("[1, 2]"), LlmOutputError),
    ],
)
def test_claude_unusable_answers(result: object, error: type[Exception]) -> None:
    client, _, _ = _client(result)
    with pytest.raises(error):
        ClaudeLanguageModel("k", client=client).generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=100
        )


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (_api_error(anthropic.AuthenticationError, 401), LlmAuthError),
        (_api_error(anthropic.PermissionDeniedError, 403), LlmAuthError),
        (_api_error(anthropic.BadRequestError, 400), LlmRequestError),
        (_api_error(anthropic.NotFoundError, 404), LlmRequestError),
        (_api_error(anthropic.RateLimitError, 429), LlmUnavailableError),
        (_api_error(anthropic.InternalServerError, 529), LlmUnavailableError),
        (_api_error(anthropic.APIStatusError, 418), LlmRequestError),
        (
            anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            LlmUnavailableError,
        ),
    ],
)
def test_claude_errors_are_mapped(exc: BaseException, error: type[Exception]) -> None:
    client, _, _ = _client(exc)
    with pytest.raises(error):
        ClaudeLanguageModel("k", client=client).generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=100
        )


# ---------------------------------------------------------------- OpenAI-compatible
class _Handler(BaseHTTPRequestHandler):
    reply: ClassVar[tuple[int, Any]] = (200, {})
    replies: ClassVar[list[tuple[int, Any]]] = []  # consumed first, then `reply`
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        _Handler.seen.append(
            {
                "path": self.path,
                "auth": self.headers["Authorization"],
                "body": json.loads(self.rfile.read(length)),
            }
        )
        status, payload = _Handler.replies.pop(0) if _Handler.replies else _Handler.reply
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    _Handler.seen = []
    _Handler.replies = []
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()


def _chat(content: str, finish: str = "stop") -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {}}


def test_compat_request_and_fenced_json(server: str) -> None:
    _Handler.reply = (200, _chat('```json\n{"ok": true}\n```'))
    model = OpenAICompatibleLanguageModel(server, "secret", "deepseek-chat")
    assert model.generate_json(system="s", prompt="p", schema=SCHEMA, max_tokens=50) == {"ok": True}
    req = _Handler.seen[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer secret"
    assert req["body"]["response_format"] == {"type": "json_object"}
    assert '"required": ["ok"]' in req["body"]["messages"][1]["content"]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ((401, {}), LlmAuthError),
        ((429, {}), LlmUnavailableError),
        ((503, {}), LlmUnavailableError),
        ((400, {}), LlmRequestError),
        ((200, b"not json"), LlmOutputError),
        ((200, {"choices": []}), LlmOutputError),
        ((200, _chat('{"ok": tr', finish="length")), LlmOutputError),
        ((200, _chat("plain words")), LlmOutputError),
        ((200, _chat("[1]")), LlmOutputError),
    ],
)
def test_compat_failures(server: str, reply: tuple[int, Any], error: type[Exception]) -> None:
    _Handler.reply = reply
    with pytest.raises(error):
        OpenAICompatibleLanguageModel(server, "k", "m").generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=50
        )


def test_compat_unreachable_and_bad_url() -> None:
    with pytest.raises(LlmUnavailableError):
        OpenAICompatibleLanguageModel("http://127.0.0.1:9", "k", "m", timeout=2).generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=50
        )
    with pytest.raises(LlmRequestError, match="https"):
        OpenAICompatibleLanguageModel("ftp://x", "k", "m")


# ---------------------------------------------------------------- provider
class _Settings:
    def __init__(self, **values: Any) -> None:
        self.value = Settings(**values)

    @property
    def read_only(self) -> bool:
        return False

    def current(self) -> Settings:
        return self.value

    def update(self, **changes: Any) -> Settings:
        self.value = Settings.model_validate({**self.value.model_dump(), **changes})
        return self.value


def test_provider_builds_and_caches_by_configuration() -> None:
    secrets = InMemorySecretStore()
    settings = _Settings()
    provider = ConfiguredLanguageModelProvider(settings, secrets)
    assert provider.current() is None  # no key yet

    secrets.set(secret_name("claude"), "key-1")
    first = provider.current()
    assert isinstance(first, ClaudeLanguageModel) and provider.current() is first
    secrets.set(secret_name("claude"), "key-2")
    assert provider.current() is not first  # new key, new client

    settings.update(
        llm_provider="openai_compatible",
        llm_model="qwen-plus",
        llm_base_url="https://example.com/v1",
    )
    assert provider.current() is None  # each provider has its own key
    secrets.set(secret_name("openai_compatible"), "k")
    compat = provider.current()
    assert isinstance(compat, OpenAICompatibleLanguageModel) and compat.model_name == "qwen-plus"


def test_compat_retries_with_max_completion_tokens(server: str) -> None:
    rejected = {
        "error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'."}
    }
    _Handler.replies = [(400, rejected)]
    _Handler.reply = (200, _chat('{"ok": true}'))
    model = OpenAICompatibleLanguageModel(server, "k", "gpt-x")
    assert model.generate_json(system="s", prompt="p", schema=SCHEMA, max_tokens=50) == {"ok": True}
    first, second = (r["body"] for r in _Handler.seen)
    assert first["max_tokens"] == 50 and "max_completion_tokens" not in first
    assert second["max_completion_tokens"] == 50 and "max_tokens" not in second
