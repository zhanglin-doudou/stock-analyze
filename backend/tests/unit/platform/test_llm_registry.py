"""模型供应商目录注册表单测（`stock_platform.llm_registry`，2026-11-22 设计 §2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from stock_platform import llm_registry as lr
from stock_platform.config import Settings

pytestmark = pytest.mark.no_db

_VALID: dict[str, object] = {
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


def _env(**values: str | None):
    return lambda name: values.get(name)


def test_registry_from_dict_resolves_targets_and_catalog() -> None:
    registry = lr.registry_from_dict(
        _VALID, env_lookup=_env(GLM_API_KEY="secret", MIMO_API_KEY=None)
    )
    assert registry.default_provider == "zhipu"
    assert registry.default_model == "glm-4.7-flash"
    assert [spec.id for spec in registry.providers] == ["zhipu", "xiaomi"]

    target = registry.default_target()
    assert target.provider.id == "zhipu"
    assert target.model == "glm-4.7-flash"
    assert target.api_key == "secret"
    assert target.configured is True

    override = registry.resolve_target("xiaomi", "mimo-v2.6-pro")
    assert override is not None
    assert override.provider.base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert override.provider.supports_thinking is False
    assert override.configured is False

    assert registry.resolve_target("xiaomi", "glm-4-flash") is None
    assert registry.resolve_target("nope", "glm-4-flash") is None
    assert registry.get_provider("nope") is None
    assert registry.has_model("zhipu", "glm-4-flash") is True
    assert registry.has_model("zhipu", "nope") is False
    assert registry.has_model("nope", "glm-4-flash") is False
    assert registry.api_key("nope") is None
    assert registry.is_available("zhipu") is True
    assert registry.is_available("xiaomi") is False

    catalog = registry.catalog()
    assert catalog["default_provider"] == "zhipu"
    assert catalog["default_model"] == "glm-4.7-flash"
    assert catalog["providers"][0] == {
        "id": "zhipu",
        "name": "智谱",
        "available": True,
        "models": ["glm-4.7-flash", "glm-4-flash"],
    }
    assert catalog["providers"][1]["available"] is False
    # 快照不得泄露内部配置
    assert "base_url" not in catalog["providers"][0]
    assert "api_key_env" not in catalog["providers"][0]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (None, "根节点必须是对象"),
        ({}, "providers 必须是数组"),
        ({**_VALID, "providers": []}, "providers 不能为空"),
        ({**_VALID, "providers": [_VALID["providers"][0], _VALID["providers"][0]]}, "重复"),  # type: ignore[list-item]
        ({**_VALID, "default_provider": "nope"}, "default_provider"),
        ({**_VALID, "default_model": "nope"}, "default_model"),
    ],
)
def test_registry_rejects_invalid_top_level(data: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        lr.registry_from_dict(data, env_lookup=_env())


def _provider_with(**overrides: object) -> dict[str, object]:
    base = {
        "id": "zhipu",
        "name": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "GLM_API_KEY",
        "models": ["glm-4-flash"],
    }
    return {**base, **overrides}


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("not-a-object", "必须是对象"),
        (_provider_with(id=""), ".id"),
        (_provider_with(id="x" * 65), ".id"),
        (_provider_with(name=5), ".name"),
        (_provider_with(base_url="ftp://x"), ".base_url"),
        (_provider_with(api_key_env="glm-key"), ".api_key_env"),
        (_provider_with(models="glm-4-flash"), ".models"),
        (_provider_with(models=[]), ".models"),
        (_provider_with(models=["a", "a"]), "重复"),
        (_provider_with(supports_thinking="yes"), ".supports_thinking"),
    ],
)
def test_registry_rejects_invalid_provider(provider: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        lr.registry_from_dict(
            {"default_provider": "zhipu", "default_model": "glm-4-flash", "providers": [provider]},
            env_lookup=_env(),
        )


def test_registry_from_file_and_errors(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_VALID), encoding="utf-8")
    registry = lr.registry_from_file(good, env_lookup=_env())
    assert registry.default_provider == "zhipu"

    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="无法读取"):
        lr.registry_from_file(missing, env_lookup=_env())

    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        lr.registry_from_file(bad, env_lookup=_env())


def test_default_env_lookup_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY_API", "from-env")
    monkeypatch.setattr(lr, "_dotenv_values", lambda: {"SOME_KEY_API": "from-dotenv"})
    assert lr.default_env_lookup("SOME_KEY_API") == "from-env"
    monkeypatch.delenv("SOME_KEY_API")
    assert lr.default_env_lookup("SOME_KEY_API") == "from-dotenv"
    assert lr.default_env_lookup("MISSING_KEY_API") is None


def test_load_llm_registry_uses_settings_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps(_VALID), encoding="utf-8")
    settings = Settings(LLM_PROVIDERS_FILE=str(custom))
    registry = lr.load_llm_registry(settings, env_lookup=_env())
    assert registry.default_model == "glm-4.7-flash"
    assert lr.llm_providers_path(settings) == custom


def test_shipped_default_catalog_is_valid() -> None:
    """仓库内置 `config/llm_providers.json` 必须始终可加载（首发智谱 + 小米）。"""
    registry = lr.load_llm_registry(Settings(), env_lookup=_env())
    ids = [spec.id for spec in registry.providers]
    assert ids == ["zhipu", "xiaomi"]
    assert registry.has_model("zhipu", "glm-4.7-flash")
    assert registry.has_model("zhipu", "glm-4-flash")
    assert registry.has_model("xiaomi", "mimo-v2.6-flash")
    assert registry.has_model("xiaomi", "mimo-v2.6-pro")
    assert registry.get_provider("xiaomi") is not None
    assert registry.get_provider("xiaomi").base_url == "https://token-plan-cn.xiaomimimo.com/v1"  # type: ignore[union-attr]
