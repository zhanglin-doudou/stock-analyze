"""OpenAI-compatible LLM Provider（2026-11-22 供应商目录化）与测试用 FakeProvider。

供应商/模型/key 来自 `stock_platform.llm_registry`（`config/llm_providers.json`），
按 `(provider_id, model)` 动态构建；只使用 LangChain 的 ChatOpenAI + messages，
不引入 AgentExecutor/Memory。429/超时等由 ChatRuntime 统一重试/降级，SDK 侧
`max_retries=0`。
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
from stock_platform.llm_registry import LlmRegistry, LlmTarget, load_llm_registry
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


class OpenAiCompatProvider:
    """任意 OpenAI-compatible `/chat/completions` 供应商（智谱/小米/…）。

    `api_key` 缺失不阻断构造（请求期抛 `LLM_NOT_CONFIGURED` 走降级），保证
    装配路径不因配置缺失 500。
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
        settings: Settings,
        supports_thinking: bool = False,
    ) -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self._api_key = api_key or ""
        self.settings = settings
        self.supports_thinking = supports_thinking

    def _client(self, *, json_mode: bool) -> ChatOpenAI:
        model_kwargs: dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        extra_body: dict[str, Any] = {}
        if self.supports_thinking and self.settings.llm_thinking == "disabled":
            extra_body["thinking"] = {"type": "disabled"}
        return ChatOpenAI(
            model=self.model,
            api_key=SecretStr(self._api_key),
            base_url=self.base_url,
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
        if not self._api_key:
            raise LlmError(
                DegradeReason.LLM_NOT_CONFIGURED,
                f"{self.provider} 的 API key 未配置（环境变量见 config/llm_providers.json）",
            )
        client = self._client(json_mode=json_mode)
        raw_request = {
            "purpose": purpose,
            "model": self.model,
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
            elif "length limit" in low or "maximum context length" in low or "truncated" in low:
                reason, retryable = DegradeReason.LLM_BAD_RESPONSE, False
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
                model=self.model,
                error_code=reason.value,
                status_code=status if isinstance(status, int) else None,
                error=text[:300] or reason.value,
                latency_ms=latency_ms,
            )
            raise LlmError(
                reason,
                text[:300] or reason.value,
                retryable=retryable,
                status_code=status if isinstance(status, int) else None,
                raw_request=raw_request,
                raw_response={"error": text[:300]},
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
            model=self.model,
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
            completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            latency_ms=latency_ms,
        )
        return LlmResult(
            content=content,
            provider=self.provider,
            model=self.model,
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


LlmProvider = OpenAiCompatProvider | FakeProvider


class LlmProviderFactory:
    """按 `(provider_id, model)` 缓存构建 provider；解析用户偏好选择（设计 §5）。

    `resolve(selection)` 只认有效目录命中；无效/为空返回 `None`（调用侧回落默认）。
    """

    def __init__(self, settings: Settings, registry: LlmRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._cache: dict[tuple[str, str], OpenAiCompatProvider] = {}

    def build(self, target: LlmTarget) -> OpenAiCompatProvider:
        cache_key = (target.provider.id, target.model)
        cached = self._cache.get(cache_key)
        if cached is None:
            cached = OpenAiCompatProvider(
                provider=target.provider.id,
                model=target.model,
                base_url=target.provider.base_url,
                api_key=target.api_key,
                settings=self.settings,
                supports_thinking=target.provider.supports_thinking,
            )
            self._cache[cache_key] = cached
        return cached

    def resolve(self, selection: Mapping[str, Any] | None) -> OpenAiCompatProvider | None:
        """用户选择（`{"provider": ..., "model": ...}`，空 = 跟随默认）→ provider。"""
        if not selection:
            return None
        provider_id = selection.get("provider")
        model = selection.get("model")
        if not isinstance(provider_id, str) or not isinstance(model, str):
            return None
        if not provider_id or not model:
            return None
        target = self.registry.resolve_target(provider_id, model)
        if target is None:
            log.warning("chat.llm.unknown_selection", provider=provider_id, model=model)
            return None
        return self.build(target)


def build_provider(settings: Settings, registry: LlmRegistry | None = None) -> LlmProvider:
    """装配默认 provider：`none`/`local` → Fake；否则按 LLM_PROVIDER 解析目录目标。

    key 缺失不回退 Fake（避免静默假响应）：目标 provider 保留，请求期抛
    `LLM_NOT_CONFIGURED` 走降级。
    """
    if not settings.llm_enabled:
        return FakeProvider()
    if registry is None:
        registry = load_llm_registry(settings)
    if settings.llm_provider == "auto":
        target = registry.default_target()
    else:
        spec = registry.get_provider(settings.llm_provider)
        if spec is None:
            raise ValueError(
                f"LLM_PROVIDER={settings.llm_provider!r} 不在 config/llm_providers.json 目录内"
            )
        selected = registry.resolve_target(spec.id, spec.models[0])
        assert selected is not None  # 目录内首个模型必然命中
        target = selected
    return OpenAiCompatProvider(
        provider=target.provider.id,
        model=target.model,
        base_url=target.provider.base_url,
        api_key=target.api_key,
        settings=settings,
        supports_thinking=target.provider.supports_thinking,
    )


__all__ = [
    "FakeProvider",
    "LlmError",
    "LlmProvider",
    "LlmProviderFactory",
    "OpenAiCompatProvider",
    "build_provider",
]
