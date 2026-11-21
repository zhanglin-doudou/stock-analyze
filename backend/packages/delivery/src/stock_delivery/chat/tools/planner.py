"""运行内存中的规则 Planner（LLM 不可用/离线/测试兜底）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from stock_contracts.chat.dto import Understanding
from stock_contracts.chat.enums import Intent
from stock_contracts.chat.tools import ToolCall

from .registry import RegisteredTool, ToolCatalog


@dataclass(frozen=True, slots=True)
class ToolPlan:
    action: Literal["call", "answer", "clarify"]
    calls: tuple[ToolCall, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    capability: str
    args: Mapping[str, Any] = field(default_factory=dict)
    fallback_capability: str | None = None
    fallback_args: Mapping[str, Any] = field(default_factory=dict)


# 路由表是运行内存常量，不落库；生产默认由 LLM Planner 生成 ToolPlan，
# 本表只在 LLM 不可用/离线/测试时兜底。
INTENT_ROUTES: dict[Intent, tuple[CapabilityRoute, ...]] = {
    Intent.PORTFOLIO: (
        CapabilityRoute("assets.read", {"limit": 1000}),
        CapabilityRoute("portfolio.read"),
    ),
    Intent.PERFORMANCE: (CapabilityRoute("metrics.read"),),
    Intent.ATTRIBUTION: (CapabilityRoute("attribution.read"),),
    Intent.ROUNDS: (CapabilityRoute("rounds.read", {"limit": 1000}),),
    Intent.ROUND_DETAIL: (
        CapabilityRoute(
            "rounds.detail",
            fallback_capability="rounds.read",
            fallback_args={"limit": 1000},
        ),
    ),
    Intent.TRADES: (CapabilityRoute("trades.read", {"limit": 1000}),),
    Intent.ASSETS: (
        CapabilityRoute("assets.read", {"limit": 1000}),
        CapabilityRoute("portfolio.read"),
    ),
    Intent.NAV: (CapabilityRoute("nav.read"),),
    Intent.SECTORS: (CapabilityRoute("sectors.read", {"taxonomy": "industry"}),),
    Intent.DATA_QUALITY: (CapabilityRoute("data.quality.read"),),
    Intent.METRIC_EXPLAIN: (CapabilityRoute("metrics.explain", {"metric_id": "win_rate"}),),
    Intent.UNKNOWN: (
        CapabilityRoute("data.quality.read"),
        CapabilityRoute("portfolio.read"),
    ),
}


class RulePlanner:
    """确定性兜底：意图 → 能力 → catalog；不调用 LLM。"""

    def plan(self, understanding: Understanding, catalog: ToolCatalog) -> ToolPlan:
        base = _base_args(understanding)
        calls: list[ToolCall] = []
        seen: set[str] = set()
        for intent in understanding.intents[:2]:
            for route in INTENT_ROUTES.get(intent, ()):
                tool = _pick(catalog, route.capability)
                route_args = route.args
                if tool is None and route.fallback_capability:
                    tool = _pick(catalog, route.fallback_capability)
                    route_args = route.fallback_args
                if tool is None:
                    continue
                if tool.spec.id in seen:
                    continue
                args = _merge_args(tool, route_args, base, understanding)
                required = tool.spec.input_schema.get("required") or []
                if (
                    isinstance(required, list)
                    and any(key not in args for key in required)
                    and route.fallback_capability
                ):
                    fallback = _pick(catalog, route.fallback_capability)
                    if fallback is not None and fallback.spec.id not in seen:
                        tool = fallback
                        route_args = route.fallback_args
                        args = _merge_args(tool, route_args, base, understanding)
                calls.append(
                    ToolCall(
                        call_id=f"call_{len(calls) + 1}",
                        tool_id=tool.spec.id,
                        args=args,
                        step=1,
                        reason=f"intent={intent.value} capability={route.capability}",
                    )
                )
                seen.add(tool.spec.id)
                if len(calls) >= 3:
                    break
            if len(calls) >= 3:
                break
        if not calls:
            return ToolPlan(
                action="clarify",
                reason="当前工具目录没有匹配的能力",
            )
        return ToolPlan(action="call", calls=tuple(calls), reason="rule_fallback")


def _pick(catalog: ToolCatalog, capability: str) -> RegisteredTool | None:
    matches = catalog.find_by_capability(capability)
    if not matches:
        return None
    # 内置优先；同源按注册顺序。
    return sorted(matches, key=lambda item: 0 if item.spec.source.value == "builtin" else 1)[0]


def _base_args(understanding: Understanding) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if understanding.period and understanding.period.value:
        args["period"] = understanding.period.value
    security = next(
        (item.resolved_id for item in understanding.entities if item.type == "security"), None
    )
    round_id = next(
        (item.resolved_id for item in understanding.entities if item.type == "round"), None
    )
    metric = next(
        (item.resolved_id for item in understanding.entities if item.type == "metric"), None
    )
    if security:
        args["security"] = security
    if round_id:
        args["round_id"] = round_id
    if metric:
        args["metric_id"] = metric
    for key in ("sort", "sort_dir", "limit", "taxonomy", "metric_id", "flag"):
        value = understanding.constraints.get(key)
        if value not in (None, ""):
            args[key] = value
    # 旧行为兼容：最赚/最亏从 resolved_query 提取；新路径优先 constraints。
    if "sort" not in args:
        if any(word in understanding.resolved_query for word in ("亏损最多", "亏得最多", "最亏")):
            args.update({"sort": "pnl", "sort_dir": "asc"})
        elif any(word in understanding.resolved_query for word in ("盈利最多", "赚得最多", "最赚")):
            args.update({"sort": "pnl", "sort_dir": "desc"})
    return args


def _merge_args(
    tool: RegisteredTool,
    route_args: Mapping[str, Any],
    base: Mapping[str, Any],
    understanding: Understanding,
) -> dict[str, Any]:
    merged = {**route_args}
    if tool.spec.capabilities and "rounds.detail" in tool.spec.capabilities:
        round_id = next(
            (item.resolved_id for item in understanding.entities if item.type == "round"), None
        )
        if round_id:
            merged["round_id"] = round_id
    merged.update(base)
    if tool.spec.id.endswith("metric_explain"):
        merged["metric_id"] = base.get("metric_id") or route_args.get("metric_id") or "win_rate"
    if tool.spec.id.endswith("sector_exposure"):
        merged["taxonomy"] = base.get("taxonomy") or "industry"
    return _filter_args(tool, merged)


def _filter_args(tool: RegisteredTool, args: Mapping[str, Any]) -> dict[str, Any]:
    properties = tool.spec.input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(args)
    allowed = set(properties)
    return {key: value for key, value in args.items() if key in allowed}
