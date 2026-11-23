"""默认 provider 装配与动态工厂单测（2026-11-22 设计 §5）。"""

from __future__ import annotations

import pytest
from stock_delivery.chat.provider import (
    FakeProvider,
    LlmProviderFactory,
    OpenAiCompatProvider,
    build_provider,
)
from stock_platform.config import Settings
from stock_platform.llm_registry import registry_from_dict

pytestmark = pytest.mark.no_db

_CATALOG: dict[str, object] = {
    "default_provider": "zhipu",
    "default_model": "glm-4.7-flash",
    "providers": [
        {
            "id": "zhipu",
            "name": "智谱",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_env": "GLM_API_KEY",
            "supports_thinking": True,
            "models": ["glm-4.7-flash", "glm-4-flash"],
        },
        {
            "id": "xiaomi",
            "name": "小米",
            "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "api_key_env": "MIMO_API_KEY",
            "models": ["mimo-v2.6-flash", "mimo-v2.6-pro"],
        },
    ],
}


def _registry(**keys: str | None):
    return registry_from_dict(_CATALOG, env_lookup=lambda name: keys.get(name))


def test_build_provider_disabled_and_auto() -> None:
    disabled = Settings(LLM_PROVIDER="none")
    assert isinstance(build_provider(disabled, _registry(GLM_API_KEY="k")), FakeProvider)

    local = Settings(LLM_PROVIDER="local")
    assert isinstance(build_provider(local, _registry(GLM_API_KEY="k")), FakeProvider)

    provider = build_provider(Settings(), _registry(GLM_API_KEY="k"))
    assert isinstance(provider, OpenAiCompatProvider)
    assert provider.provider == "zhipu"
    assert provider.model == "glm-4.7-flash"
    assert provider.supports_thinking is True


def test_build_provider_explicit_and_unknown() -> None:
    provider = build_provider(Settings(LLM_PROVIDER="xiaomi"), _registry(MIMO_API_KEY="k"))
    assert isinstance(provider, OpenAiCompatProvider)
    assert provider.provider == "xiaomi"
    assert provider.model == "mimo-v2.6-flash"

    with pytest.raises(ValueError, match=r"不在 config/llm_providers\.json"):
        build_provider(Settings(LLM_PROVIDER="nope"), _registry())


def test_factory_resolve_selection_and_cache() -> None:
    factory = LlmProviderFactory(Settings(), _registry(GLM_API_KEY="k", MIMO_API_KEY=None))

    assert factory.resolve(None) is None
    assert factory.resolve({}) is None
    assert factory.resolve({"provider": "", "model": ""}) is None
    assert factory.resolve({"provider": "nope", "model": "m"}) is None
    assert factory.resolve({"provider": "zhipu", "model": "nope"}) is None

    first = factory.resolve({"provider": "xiaomi", "model": "mimo-v2.6-pro"})
    assert isinstance(first, OpenAiCompatProvider)
    assert first.provider == "xiaomi"
    assert first.model == "mimo-v2.6-pro"
    # 缺 key 不阻断构造（请求期抛 LLM_NOT_CONFIGURED 走降级）
    assert factory.resolve({"provider": "xiaomi", "model": "mimo-v2.6-pro"}) is first

    again = factory.resolve({"provider": "zhipu", "model": "glm-4-flash"})
    assert isinstance(again, OpenAiCompatProvider)
    assert again is not first
    assert again.model == "glm-4-flash"
