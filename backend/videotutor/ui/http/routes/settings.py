"""Learner settings and AI provider configuration (used by the home page wizard).

The API key travels in one direction only: it can be written, never read back.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from videotutor.app.errors import LlmError
from videotutor.app.llm_settings import LlmConfig, LlmSettingsService
from videotutor.app.settings import SettingsStore
from videotutor.core.tasks import TaskSupervisor, TaskTimeout

from ..schemas import LlmConfigIn, LlmConfigOut, LlmTestOut, SettingsIn, SettingsOut

TEST_TIMEOUT_S = 60.0


def _config(c: LlmConfig) -> LlmConfigOut:
    return LlmConfigOut(provider=c.provider, model=c.model, base_url=c.base_url, has_key=c.has_key)


def router(settings: SettingsStore, llm: LlmSettingsService, tasks: TaskSupervisor) -> APIRouter:
    r = APIRouter(prefix="/api/settings")

    def _settings_out() -> SettingsOut:
        s = settings.current()
        return SettingsOut(vocabulary_level=s.vocabulary_level, auto_prewatch=s.auto_prewatch)

    @r.get("")
    def get_settings() -> SettingsOut:
        return _settings_out()

    @r.put("")
    def put_settings(body: SettingsIn) -> SettingsOut:
        changes = body.model_dump(exclude_none=True)
        if changes:
            settings.update(**changes)
        return _settings_out()

    @r.get("/llm")
    def get_llm() -> LlmConfigOut:
        return _config(llm.config())

    @r.put("/llm")
    async def put_llm(body: LlmConfigIn) -> LlmTestOut:
        """Save, then prove the configuration works with one tiny request."""
        config = llm.configure(body.provider, body.model, body.base_url, body.api_key)
        handle = tasks.run_blocking("llm-test", llm.test, timeout=TEST_TIMEOUT_S)
        try:
            await asyncio.wrap_future(handle.future)
        except LlmError as exc:
            return LlmTestOut(ok=False, error=exc.user_message, config=_config(config))
        except TaskTimeout:
            return LlmTestOut(
                ok=False, error="AI 服务响应超时，请检查网络后重试。", config=_config(config)
            )
        return LlmTestOut(ok=True, error=None, config=_config(config))

    @r.delete("/llm/key")
    def delete_llm_key() -> LlmConfigOut:
        return _config(llm.remove_key())

    return r
