"""内置工具 Provider：11 个只读 ToolSpec + 统一 ChatQueryFacade 适配。

本模块是工具元数据的单一来源；MCP Server 复用同一批 ToolSpec，新增工具只需
在这里加 spec + handler（handler 可继续复用 ChatQueryFacade）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from stock_contracts.chat.dto import ToolResult
from stock_contracts.chat.enums import ChatStatus, ToolName, ToolSource, ToolTrust
from stock_contracts.chat.ports import ChatDataPort
from stock_contracts.chat.tools import (
    ProviderHealth,
    ToolCall,
    ToolContext,
    ToolSpec,
)


def _schema(
    properties: Mapping[str, Any] | None = None,
    required: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties or {}),
        "required": list(required),
    }


_PERIOD = {"type": "string", "pattern": r"^[0-9]{4}(-[0-9]{2})?$"}
_SECURITY = {"type": "string", "pattern": r"^[0-9A-Za-z.]{1,16}$"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 1000}


def _spec(
    name: str,
    title: str,
    description: str,
    when_to_use: str,
    capabilities: tuple[str, ...],
    input_schema: Mapping[str, Any],
    *,
    timeout_ms: int = 8_000,
    max_calls_per_turn: int = 3,
) -> ToolSpec:
    return ToolSpec(
        id=f"builtin.{name}",
        name=name,
        title=title,
        description=description,
        when_to_use=when_to_use,
        input_schema=input_schema,
        output_schema={"type": "object"},
        capabilities=capabilities,
        source=ToolSource.BUILTIN,
        side_effect="read",
        risk="low",
        trust=ToolTrust.LOCAL,
        timeout_ms=timeout_ms,
        max_calls_per_turn=max_calls_per_turn,
        mcp_exposed=True,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )


BUILTIN_SPECS: tuple[ToolSpec, ...] = (
    _spec(
        "assets_query",
        "资产表",
        "查询每日资产快照（现金/市值/总资产/盈亏），只读、不触发构建。",
        "用户问现在有多少资产、现金、仓位，或某天资产多少时使用。",
        ("assets.read",),
        _schema(
            {
                "period": _PERIOD,
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "status": {"type": "string"},
                "limit": _LIMIT,
                "cursor": {"type": "string"},
                "sort": {"type": "string"},
            }
        ),
    ),
    _spec(
        "trades_query",
        "流水查询",
        "查询指定期间/证券的交易流水，返回旗标与旗标解释。",
        "用户问交易、操作、买卖、成交、流水、旗标时使用。",
        ("trades.read",),
        _schema(
            {
                "period": _PERIOD,
                "security": _SECURITY,
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "category": {"type": "string"},
                "limit": _LIMIT,
                "cursor": {"type": "string"},
            }
        ),
    ),
    _spec(
        "rounds_query",
        "回合列表",
        "查询交易回合列表，支持按期间/证券/旗标/盈亏排序。",
        "用户问回合、最赚/最亏、交易次数时使用。",
        ("rounds.read",),
        _schema(
            {
                "period": _PERIOD,
                "security": _SECURITY,
                "flag": {"type": "string"},
                "bucket": {"type": "string"},
                "sort": {"type": "string"},
                "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
                "limit": _LIMIT,
            }
        ),
    ),
    _spec(
        "round_detail",
        "回合详情",
        "查询单个回合的买入/卖出配对、旗标与持有天数。",
        "用户问「这个回合/那笔交易」的详情时使用，需要 round_id。",
        ("rounds.detail",),
        _schema({"round_id": {"type": "string", "minLength": 1}}, required=("round_id",)),
        max_calls_per_turn=2,
    ),
    _spec(
        "portfolio_snapshot",
        "资产概览",
        "资产/持仓/仓位/盈亏概览。",
        "用户问总资产、仓位、累计盈亏概览时使用。",
        ("portfolio.read",),
        _schema({"period": _PERIOD}),
        max_calls_per_turn=2,
    ),
    _spec(
        "performance_metrics",
        "绩效指标",
        "返回收益率/胜率/回撤/夏普等指标。",
        "用户问收益率、胜率、最大回撤、夏普等指标口径时使用。",
        ("metrics.read",),
        _schema(
            {
                "period": _PERIOD,
                "ids": {"type": "array", "items": {"type": "string"}},
                "domain": {"type": "string"},
            }
        ),
    ),
    _spec(
        "attribution_summary",
        "五因子归因",
        "返回收益归因与因子贡献。",
        "用户问为什么赚/为什么亏、归因、拖累/贡献时使用。",
        ("attribution.read",),
        _schema({"period": _PERIOD, "benchmark": {"type": "string"}}),
        max_calls_per_turn=2,
    ),
    _spec(
        "nav_summary",
        "净值摘要",
        "返回 TWR/XIRR/回撤等净值摘要。",
        "用户问净值、年化、TWR、XIRR 时使用。",
        ("nav.read",),
        _schema({"period": _PERIOD}),
        max_calls_per_turn=2,
    ),
    _spec(
        "sector_exposure",
        "板块暴露",
        "返回行业/概念暴露与集中度。",
        "用户问持仓集中在哪些行业/概念时使用。",
        ("sectors.read",),
        _schema(
            {
                "taxonomy": {"type": "string", "enum": ["industry", "concept"]},
                "period": _PERIOD,
                "limit": _LIMIT,
            }
        ),
    ),
    _spec(
        "data_quality",
        "数据质量",
        "返回对账、构建、行情覆盖与口径状态。",
        "用户问数据可不可信、是否断链、是否缺失时使用。",
        ("data.quality.read",),
        _schema({"period": _PERIOD}),
        max_calls_per_turn=2,
    ),
    _spec(
        "metric_explain",
        "指标口径",
        "解释指标定义与计算口径。",
        "用户问某个指标怎么算、口径、定义时使用。",
        ("metrics.explain",),
        _schema(
            {
                "metric_id": {"type": "string"},
                "period": _PERIOD,
            }
        ),
        max_calls_per_turn=2,
    ),
)

_BY_NAME = {spec.name: spec for spec in BUILTIN_SPECS}
_BY_ID = {spec.id: spec for spec in BUILTIN_SPECS}


class BuiltinToolProvider:
    """把 ChatDataPort 适配成 ToolProvider；不复制业务口径。"""

    source = ToolSource.BUILTIN

    def __init__(self, data_port: ChatDataPort) -> None:
        self._data_port = data_port

    def specs(self) -> tuple[ToolSpec, ...]:
        return BUILTIN_SPECS

    async def list_tools(self, ctx: ToolContext) -> Sequence[ToolSpec]:
        return BUILTIN_SPECS

    async def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        spec = _BY_ID.get(call.tool_id)
        if spec is None:
            return _error(call.tool_id, "TOOL_NOT_ALLOWED", "未注册的内置工具")
        try:
            result = await self._data_port.invoke(ToolName(spec.name), call.args)
        except Exception as exc:
            return _error(spec.id, "TOOL_UNAVAILABLE", str(exc)[:200] or "工具执行失败", spec)
        return replace(
            result,
            tool_id=spec.id,
            tool=spec.name,
            title=spec.title,
            source=ToolSource.BUILTIN,
            trust=ToolTrust.LOCAL,
        )

    async def health(self, ctx: ToolContext) -> ProviderHealth:
        return ProviderHealth(ok=True)


def _error(
    tool_id: str,
    code: str,
    message: str,
    spec: ToolSpec | None = None,
) -> ToolResult:
    return ToolResult(
        tool=spec.name if spec else tool_id.rsplit(".", 1)[-1],
        tool_id=tool_id,
        title=spec.title if spec else tool_id,
        source=ToolSource.BUILTIN,
        trust=ToolTrust.LOCAL,
        status=ChatStatus.ERROR,
        data={},
        error_code=code,
        error_message=message,
        retryable=True,
    )


def build_builtin_provider(data_port: ChatDataPort) -> BuiltinToolProvider:
    return BuiltinToolProvider(data_port)
