"""工具注册表与单轮目录（运行内存，不落库）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stock_contracts.chat.enums import ToolSource
from stock_contracts.chat.tools import ToolContext, ToolProvider, ToolSpec


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    provider: ToolProvider


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    """单轮不可变快照；规划器只能从这里的工具中选择。"""

    tools: tuple[RegisteredTool, ...]

    def get(self, tool_id: str) -> RegisteredTool | None:
        return next((item for item in self.tools if item.spec.id == tool_id), None)

    def all(self) -> tuple[RegisteredTool, ...]:
        return self.tools

    def find_by_capability(self, capability: str) -> list[RegisteredTool]:
        return [
            item
            for item in self.tools
            if capability in item.spec.capabilities and item.spec.side_effect == "read"
        ]

    def filter_capabilities(self, capabilities: Sequence[str]) -> list[RegisteredTool]:
        allowed = set(capabilities)
        return [
            item
            for item in self.tools
            if allowed.intersection(item.spec.capabilities) and item.spec.side_effect == "read"
        ]


class ToolRegistry:
    """进程内注册表；启动时注册内置/部署级 Provider，单轮生成快照。"""

    def __init__(self) -> None:
        self._providers: list[ToolProvider] = []
        self._static: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, provider: ToolProvider) -> None:
        if spec.id in self._static:
            raise ValueError(f"工具 id 重复: {spec.id}")
        if any(item.spec.name == spec.name for item in self._static.values()):
            raise ValueError(f"工具短名重复: {spec.name}")
        self._static[spec.id] = RegisteredTool(spec=spec, provider=provider)

    def register_many(self, items: Sequence[tuple[ToolSpec, ToolProvider]]) -> None:
        for spec, provider in items:
            self.register(spec, provider)

    def register_provider(self, provider: ToolProvider) -> None:
        if provider not in self._providers:
            self._providers.append(provider)

    def unregister_source(self, source: ToolSource) -> None:
        self._static = {
            key: value for key, value in self._static.items() if value.spec.source != source
        }
        self._providers = [item for item in self._providers if item.source != source]

    async def snapshot(
        self,
        ctx: ToolContext,
        *,
        include_external: bool = True,
        capabilities: Sequence[str] | None = None,
    ) -> ToolCatalog:
        allowed = set(capabilities or ())
        items: dict[str, RegisteredTool] = {}
        for registered in self._static.values():
            if not include_external and registered.spec.source != ToolSource.BUILTIN:
                continue
            if allowed and not allowed.intersection(registered.spec.capabilities):
                continue
            items[registered.spec.id] = registered
        for provider in self._providers:
            for spec in await provider.list_tools(ctx):
                if not include_external and spec.source != ToolSource.BUILTIN:
                    continue
                if allowed and not allowed.intersection(spec.capabilities):
                    continue
                if spec.side_effect != "read":
                    continue
                items.setdefault(spec.id, RegisteredTool(spec=spec, provider=provider))
        return ToolCatalog(tuple(items.values()))
