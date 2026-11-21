"""工具平台的跨层契约：ToolSpec / ToolCall / ToolProvider / 运行上下文。

本模块只放纯数据与 Protocol，不做 I/O；工具实现由 stock_review（内置）或
stock_platform.mcp（部署级外部 Provider）提供，编排与执行在 stock_delivery。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from stock_contracts.chat.dto import ToolResult
from stock_contracts.chat.enums import ToolSource, ToolTrust


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """工具的不可变元数据；注册表、MCP schema、规划器都从这里取。"""

    id: str
    name: str
    title: str
    description: str
    when_to_use: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None
    capabilities: tuple[str, ...] = ()
    source: ToolSource = ToolSource.BUILTIN
    side_effect: Literal["read", "write", "external"] = "read"
    risk: Literal["low", "medium", "high"] = "low"
    trust: ToolTrust = ToolTrust.LOCAL
    version: str = "1"
    timeout_ms: int = 8_000
    max_result_bytes: int = 262_144
    max_calls_per_turn: int = 3
    cache_ttl_s: int = 0
    annotations: Mapping[str, Any] = field(default_factory=dict)
    deprecated: bool = False
    mcp_exposed: bool = False


@dataclass(frozen=True, slots=True)
class ToolCall:
    """一次工具调用请求；call_id 在单轮内唯一。"""

    call_id: str
    tool_id: str
    args: Mapping[str, Any]
    step: int = 1
    reason: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContext:
    """执行上下文；不持有 Settings/DB session/HTTP client，避免契约层依赖基础设施。"""

    user_id: UUID
    request_id: str
    turn_id: str | None
    scopes: frozenset[str]
    deadline: float
    locale: str = "zh-CN"


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    ok: bool
    code: str | None = None
    message: str | None = None


class ToolProvider(Protocol):
    """工具实现适配器：内置与外部 MCP 都实现此协议。"""

    source: ToolSource

    async def list_tools(self, ctx: ToolContext) -> Sequence[ToolSpec]: ...

    async def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult: ...

    async def health(self, ctx: ToolContext) -> ProviderHealth: ...
