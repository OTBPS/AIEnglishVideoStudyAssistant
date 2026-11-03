"""Errors the application layer exposes. The HTTP layer maps them to status codes."""

from __future__ import annotations


class NotFoundError(LookupError):
    """The requested video, transcript or session does not exist."""


class StorageReadOnlyError(RuntimeError):
    """The learning database belongs to a newer VideoTutor and must not be written."""


class InvalidInputError(ValueError):
    """Input that is well-formed but cannot be used (e.g. captions with no text)."""


# ---------------------------------------------------------------- language model
class LlmError(RuntimeError):
    """Base for every language-model failure; `user_message` is shown in the UI."""

    user_message = "AI 服务出错了，请稍后再试。"


class LlmNotConfiguredError(LlmError):
    user_message = "还没有填写 AI 密钥：请在 VideoTutor 主页完成设置。"


class LlmAuthError(LlmError):
    user_message = "AI 密钥无效或没有权限，请在 VideoTutor 主页重新填写。"


class LlmRequestError(LlmError):
    """The provider rejected the request itself (wrong model name, bad base URL...)."""

    user_message = "AI 服务拒绝了请求：请检查主页里的模型名称和接口地址。"


class LlmUnavailableError(LlmError):
    """Network, rate limit or provider outage: worth retrying later."""

    user_message = "暂时连不上 AI 服务（网络、限流或服务繁忙），请稍后再试。"


class LlmRefusedError(LlmError):
    user_message = "AI 拒绝处理这段内容。"


class LlmOutputError(LlmError):
    """The model answered, but not in the shape we asked for."""

    user_message = "AI 返回的内容无法使用，请重试。"
