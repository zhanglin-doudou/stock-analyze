"""模型供应商目录（2026-11-22 动态切换设计 §2）。

从 JSON 目录文件加载可用供应商/模型与 API key 的**环境变量名**；key 本身只从
环境解析（`os.environ` 优先，本地开发回落仓库根 `.env`），**禁止**打印/入库/进响应。

- 目录文件默认 `config/llm_providers.json`，`LLM_PROVIDERS_FILE` 可覆盖路径；
- 解析校验 fail-fast：非法目录直接抛 `ValueError`，宁可启动失败也不静默降级；
- `catalog()` 是给 API/前端的快照：只含 id/name/available/models 与默认目标，
  不含 base_url 与 api_key_env。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from stock_platform.config import REPO_ROOT, Settings

DEFAULT_RELATIVE_PATH = Path("config") / "llm_providers.json"
_ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_MAX_ID_LEN = 64


@dataclass(frozen=True, slots=True)
class LlmProviderSpec:
    """单个供应商声明（不含密钥）。"""

    id: str
    name: str
    base_url: str
    api_key_env: str
    models: tuple[str, ...]
    supports_thinking: bool = False


@dataclass(frozen=True, slots=True)
class LlmTarget:
    """解析后的调用目标：供应商 + 模型 + 解析到的 key（可能为空）。"""

    provider: LlmProviderSpec
    model: str
    api_key: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@lru_cache(maxsize=1)
def _dotenv_values() -> Mapping[str, str | None]:
    """仓库根 `.env`（本地开发注入）；进程级缓存，改文件需重启（与 Settings 同规）。"""
    return dotenv_values(REPO_ROOT / ".env")


def default_env_lookup(name: str) -> str | None:
    """key 解析：真实环境变量优先，回落 `.env` 文件（pydantic-settings 不写 os.environ）。"""
    value = os.environ.get(name)
    if value:
        return value
    return _dotenv_values().get(name) or None


EnvLookup = Callable[[str], str | None]


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"llm_providers.json：{field} 必须是非空字符串")
    cleaned = value.strip()
    if len(cleaned) > _MAX_ID_LEN:
        raise ValueError(f"llm_providers.json：{field} 超过 {_MAX_ID_LEN} 字符")
    return cleaned


class LlmRegistry:
    """供应商/模型目录 + key 可用性（纯读；无 I/O 之外的状态）。"""

    def __init__(
        self,
        providers: tuple[LlmProviderSpec, ...],
        default_provider: str,
        default_model: str,
        *,
        env_lookup: EnvLookup | None = None,
    ) -> None:
        if not providers:
            raise ValueError("llm_providers.json：providers 不能为空")
        ids = [spec.id for spec in providers]
        if len(set(ids)) != len(ids):
            raise ValueError(f"llm_providers.json：provider id 重复：{ids}")
        self._providers: dict[str, LlmProviderSpec] = {spec.id: spec for spec in providers}
        if default_provider not in self._providers:
            raise ValueError(
                f"llm_providers.json：default_provider {default_provider!r} 不在目录内"
            )
        if default_model not in self._providers[default_provider].models:
            raise ValueError(
                f"llm_providers.json：default_model {default_model!r} 不属于 {default_provider}"
            )
        self.default_provider = default_provider
        self.default_model = default_model
        self._env_lookup: EnvLookup = env_lookup or default_env_lookup

    # ── 目录读取 ──────────────────────────────────────────
    @property
    def providers(self) -> tuple[LlmProviderSpec, ...]:
        return tuple(self._providers.values())

    def get_provider(self, provider_id: str) -> LlmProviderSpec | None:
        return self._providers.get(provider_id)

    def has_model(self, provider_id: str, model: str) -> bool:
        spec = self._providers.get(provider_id)
        return spec is not None and model in spec.models

    # ── key 解析（只在此处碰环境）────────────────────────────
    def api_key(self, provider_id: str) -> str | None:
        spec = self._providers.get(provider_id)
        if spec is None:
            return None
        return self._env_lookup(spec.api_key_env)

    def is_available(self, provider_id: str) -> bool:
        return bool(self.api_key(provider_id))

    # ── 目标解析 ──────────────────────────────────────────
    def default_target(self) -> LlmTarget:
        spec = self._providers[self.default_provider]
        return LlmTarget(
            provider=spec,
            model=self.default_model,
            api_key=self._env_lookup(spec.api_key_env),
        )

    def resolve_target(self, provider_id: str, model: str) -> LlmTarget | None:
        """命中目录返回目标（key 可能为空，由调用侧决定降级）；未命中返回 None。"""
        spec = self._providers.get(provider_id)
        if spec is None or model not in spec.models:
            return None
        return LlmTarget(provider=spec, model=model, api_key=self._env_lookup(spec.api_key_env))

    # ── API/前端快照（不含 base_url / api_key_env / key）─────
    def catalog(self) -> dict[str, Any]:
        return {
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "providers": [
                {
                    "id": spec.id,
                    "name": spec.name,
                    "available": bool(self._env_lookup(spec.api_key_env)),
                    "models": list(spec.models),
                }
                for spec in self._providers.values()
            ],
        }


def _parse_provider(raw: object, index: int) -> LlmProviderSpec:
    if not isinstance(raw, Mapping):
        raise ValueError(f"llm_providers.json：providers[{index}] 必须是对象")
    provider_id = _require_str(raw.get("id"), f"providers[{index}].id")
    name = _require_str(raw.get("name"), f"providers[{index}].name")
    base_url = _require_str(raw.get("base_url"), f"providers[{index}].base_url")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"llm_providers.json：providers[{index}].base_url 必须是 http(s) URL")
    api_key_env = _require_str(raw.get("api_key_env"), f"providers[{index}].api_key_env")
    if not _ENV_NAME_PATTERN.match(api_key_env):
        raise ValueError(
            f"llm_providers.json：providers[{index}].api_key_env 必须匹配 {_ENV_NAME_PATTERN.pattern}"
        )
    models_raw = raw.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise ValueError(f"llm_providers.json：providers[{index}].models 必须是非空数组")
    models: list[str] = []
    for model_index, item in enumerate(models_raw):
        model = _require_str(item, f"providers[{index}].models[{model_index}]")
        if model in models:
            raise ValueError(f"llm_providers.json：providers[{index}].models 重复：{model}")
        models.append(model)
    supports_thinking = raw.get("supports_thinking", False)
    if not isinstance(supports_thinking, bool):
        raise ValueError(f"llm_providers.json：providers[{index}].supports_thinking 必须是布尔值")
    return LlmProviderSpec(
        id=provider_id,
        name=name,
        base_url=base_url,
        api_key_env=api_key_env,
        models=tuple(models),
        supports_thinking=supports_thinking,
    )


def registry_from_dict(data: object, *, env_lookup: EnvLookup | None = None) -> LlmRegistry:
    if not isinstance(data, Mapping):
        raise ValueError("llm_providers.json：根节点必须是对象")
    providers_raw = data.get("providers")
    if not isinstance(providers_raw, list):
        raise ValueError("llm_providers.json：providers 必须是数组")
    providers = tuple(_parse_provider(item, index) for index, item in enumerate(providers_raw))
    default_provider = _require_str(data.get("default_provider"), "default_provider")
    default_model = _require_str(data.get("default_model"), "default_model")
    return LlmRegistry(
        providers,
        default_provider,
        default_model,
        env_lookup=env_lookup,
    )


def registry_from_file(path: Path, *, env_lookup: EnvLookup | None = None) -> LlmRegistry:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"llm_providers.json 无法读取：{path}（{exc}）") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"llm_providers.json 不是合法 JSON：{path}（{exc}）") from exc
    return registry_from_dict(data, env_lookup=env_lookup)


def llm_providers_path(settings: Settings) -> Path:
    return settings.llm_providers_file or (REPO_ROOT / DEFAULT_RELATIVE_PATH)


def load_llm_registry(settings: Settings, *, env_lookup: EnvLookup | None = None) -> LlmRegistry:
    """按 Settings 加载目录；文件缺失/非法一律 fail-fast（ValueError）。"""
    return registry_from_file(llm_providers_path(settings), env_lookup=env_lookup)


__all__ = [
    "DEFAULT_RELATIVE_PATH",
    "EnvLookup",
    "LlmProviderSpec",
    "LlmRegistry",
    "LlmTarget",
    "default_env_lookup",
    "llm_providers_path",
    "load_llm_registry",
    "registry_from_dict",
    "registry_from_file",
]
