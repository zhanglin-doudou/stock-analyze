"""GLM（OpenAI-compatible）与测试用 FakeProvider。

只使用 LangChain 的 ChatOpenAI + messages；不引入 AgentExecutor/Memory。
429/超时等由 ChatRuntime 统一重试/降级，SDK 侧 `max_retries=0`。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from stock_contracts.chat.dto import LlmResult
from stock_contracts.chat.enums import DegradeReason
from stock_platform.config import Settings
from stock_platform.obs import get_logger

log = get_logger("stock_delivery.chat.provider")


class LlmError(Exception):
    def __init__(
        self,
        reason: DegradeReason,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        raw_request: Mapping[str, Any] | None = None,
        raw_response: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.raw_request = dict(raw_request or {})
        self.raw_response = dict(raw_response or {})


def _messages(items: Sequence[Mapping[str, str]]) -> list[Any]:
    out: list[Any] = []
    for item in items:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


class GlmProvider:
    """GLM-4.7-Flash：OpenAI-compatible /chat/completions。"""

    provider = "glm"

    def __init__(self, settings: Settings) -> None:
        if not settings.glm_api_key:
            raise LlmError(DegradeReason.LLM_NOT_CONFIGURED, "GLM_API_KEY 未配置")
        self.settings = settings

    def _client(self, *, json_mode: bool) -> ChatOpenAI:
        model_kwargs: dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        extra_body: dict[str, Any] = {}
        if self.settings.llm_thinking == "disabled":
            extra_body["thinking"] = {"type": "disabled"}
        return ChatOpenAI(
            model=self.settings.glm_model,
            api_key=SecretStr(self.settings.glm_api_key or ""),
            base_url=self.settings.glm_base_url,
            temperature=self.settings.llm_temperature,
            max_completion_tokens=self.settings.llm_max_output_tokens,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=0,
            model_kwargs=model_kwargs,
            extra_body=extra_body or None,
        )

    async def complete(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int,
        json_mode: bool = True,
    ) -> LlmResult:
        return await self.complete_messages(
            purpose=purpose,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    async def complete_messages(
        self,
        *,
        purpose: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        json_mode: bool = True,
    ) -> LlmResult:
        client = self._client(json_mode=json_mode)
        raw_request = {
            "purpose": purpose,
            "model": self.settings.glm_model,
            "messages": [dict(item) for item in messages],
            "max_tokens": max_tokens,
            "json_mode": json_mode,
        }
        started = time.monotonic()
        try:
            msg = await client.ainvoke(_messages(messages), max_tokens=max_tokens)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            text = str(exc)
            low = text.lower()
            if status == 429 or "rate limit" in low or "1302" in text:
                reason, retryable = DegradeReason.LLM_RATE_LIMITED, True
            elif status in (401, 403) or "authentication" in low or "api key" in low:
                reason, retryable = DegradeReason.LLM_AUTH_FAILED, False
            elif "timeout" in low or "timed out" in low:
                reason, retryable = DegradeReason.LLM_TIMEOUT, False
            elif "content" in low and "filter" in low:
                reason, retryable = DegradeReason.LLM_CONTENT_FILTER, False
            else:
                reason, retryable = DegradeReason.LLM_UNAVAILABLE, False
            latency_ms = int((time.monotonic() - started) * 1000)
            log.error(
                "chat.llm.call",
                status="error",
                purpose=purpose,
                provider=self.provider,
                model=self.settings.glm_model,
                error_code=reason.value,
                status_code=status if isinstance(status, int) else None,
                latency_ms=latency_ms,
            )
            raise LlmError(
                reason,
                text[:300] or reason.value,
                retryable=retryable,
                status_code=status if isinstance(status, int) else None,
                raw_request=raw_request,
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        metadata = dict(getattr(msg, "response_metadata", {}) or {})
        usage = dict(getattr(msg, "usage_metadata", {}) or {})
        if not usage:
            usage = dict(metadata.get("token_usage") or metadata.get("usage") or {})
        prompt_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
        completion_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        raw_response = {
            "content": content,
            "usage": usage,
            "metadata": metadata,
        }
        log.info(
            "chat.llm.call",
            status="ok",
            purpose=purpose,
            provider=self.provider,
            model=self.settings.glm_model,
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
            completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            latency_ms=latency_ms,
        )
        return LlmResult(
            content=content,
            provider=self.provider,
            model=self.settings.glm_model,
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
            completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            latency_ms=latency_ms,
            raw_request=raw_request,
            raw_response=raw_response,
        )


class FakeProvider:
    """测试/无网环境：按预设顺序返回内容，并记录调用。"""

    provider = "fake"
    model = "fake-chat"

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int,
        json_mode: bool = True,
    ) -> LlmResult:
        return await self.complete_messages(
            purpose=purpose,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    async def complete_messages(
        self,
        *,
        purpose: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        json_mode: bool = True,
    ) -> LlmResult:
        self.calls.append(
            {
                "purpose": purpose,
                "messages": [dict(item) for item in messages],
                "json_mode": json_mode,
            }
        )
        content = self._responses.pop(0) if self._responses else "{}"
        return LlmResult(
            content=content,
            provider=self.provider,
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=0,
            raw_request={"purpose": purpose, "messages": [dict(item) for item in messages]},
            raw_response={"content": content},
        )


LlmProvider = GlmProvider | FakeProvider


def build_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "glm" and settings.glm_api_key:
        return GlmProvider(settings)
    return FakeProvider()
