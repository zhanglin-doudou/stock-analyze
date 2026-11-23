from __future__ import annotations

from types import SimpleNamespace

import pytest
from stock_delivery.chat.provider import LlmError, OpenAiCompatProvider
from stock_platform.config import Settings
from structlog.testing import capture_logs

pytestmark = pytest.mark.no_db


class _OkClient:
    async def ainvoke(self, *_args, **_kwargs):
        return SimpleNamespace(
            content='{"ok": true}',
            response_metadata={},
            usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        )


class _RateLimitedClient:
    status_code = 429

    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError("rate limit 1302")


class _LengthLimitedClient:
    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError(
            "Could not parse response content as the length limit was reached "
            "- CompletionUsage(completion_tokens=1024, prompt_tokens=13555)"
        )


def _provider(api_key: str | None = "test-key") -> OpenAiCompatProvider:
    return OpenAiCompatProvider(
        provider="zhipu",
        model="glm-4-flash",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key=api_key,
        settings=Settings(),
        supports_thinking=True,
    )


async def test_missing_key_raises_not_configured() -> None:
    provider = _provider(api_key=None)
    with pytest.raises(LlmError) as exc:
        await provider.complete(purpose="answer", system="system", user="user", max_tokens=32)
    assert exc.value.reason.value == "LLM_NOT_CONFIGURED"


async def test_llm_success_logs_info(monkeypatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_client", lambda **_kwargs: _OkClient())
    with capture_logs() as logs:
        result = await provider.complete(
            purpose="answer", system="system", user="user", max_tokens=32
        )
    assert result.content == '{"ok": true}'
    assert logs[0]["event"] == "chat.llm.call"
    assert logs[0]["log_level"] == "info"
    assert logs[0]["status"] == "ok"
    assert logs[0]["purpose"] == "answer"
    assert logs[0]["total_tokens"] == 7


async def test_llm_length_limit_is_bad_response(monkeypatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_client", lambda **_kwargs: _LengthLimitedClient())
    with pytest.raises(LlmError) as exc:
        await provider.complete(purpose="answer", system="system", user="user", max_tokens=4096)
    assert exc.value.reason.value == "LLM_BAD_RESPONSE"
    assert "length limit" in str(exc.value)
    assert exc.value.raw_response["error"]


async def test_llm_failure_logs_error(monkeypatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_client", lambda **_kwargs: _RateLimitedClient())
    with capture_logs() as logs, pytest.raises(LlmError) as exc:
        await provider.complete(purpose="answer", system="system", user="user", max_tokens=32)
    assert exc.value.reason.value == "LLM_RATE_LIMITED"
    assert logs[0]["event"] == "chat.llm.call"
    assert logs[0]["log_level"] == "error"
    assert logs[0]["status"] == "error"
    assert logs[0]["error_code"] == "LLM_RATE_LIMITED"
    assert "test-key" not in str(logs)
