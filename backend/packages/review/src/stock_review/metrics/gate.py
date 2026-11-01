"""门禁状态机（§6.3）：`blocked > stale > unreliable > ok`。

门禁只读冻结视图/上游结果，不做 I/O；`sample_status` 由引擎在 compute 之后判定。
reason 为机读白名单码（新增码必须登记到设计文档 §6.3）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stock_review.metrics.base import DatasetDep, MetricContext, MetricDef, MetricDep

STATUS_BLOCKED = "blocked"
STATUS_STALE = "stale"
STATUS_UNRELIABLE = "unreliable"
STATUS_OK = "ok"

REASON_UPSTREAM_BLOCKED = "UPSTREAM_BLOCKED"
REASON_DATASET_UNAVAILABLE = "DATASET_UNAVAILABLE"
REASON_RECON_BLOCKED = "RECON_BLOCKED"
REASON_ASSET_BUILD_BLOCKED = "ASSET_BUILD_BLOCKED"
REASON_BENCHMARK_MISSING = "BENCHMARK_MISSING"
REASON_DATA_OUT_OF_RANGE = "DATA_OUT_OF_RANGE"
REASON_BAR_DENSITY_LOW = "BAR_DENSITY_LOW"
REASON_DATA_DEGRADED = "DATA_DEGRADED"
REASON_ASSET_BUILD_STALE = "ASSET_BUILD_STALE"
REASON_MARKET_ASOF_LAG = "MARKET_ASOF_LAG"
REASON_PENDING_WINDOW = "PENDING_WINDOW"

REASON_WHITELIST: frozenset[str] = frozenset(
    {
        REASON_RECON_BLOCKED,
        REASON_ASSET_BUILD_BLOCKED,
        REASON_BENCHMARK_MISSING,
        REASON_DATA_OUT_OF_RANGE,
        REASON_BAR_DENSITY_LOW,
        REASON_DATA_DEGRADED,
        REASON_ASSET_BUILD_STALE,
        REASON_MARKET_ASOF_LAG,
        REASON_PENDING_WINDOW,
    }
)


@dataclass(frozen=True, slots=True)
class GateDecision:
    status: str
    reason: str | None
    compute: bool

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def dataset_dependencies(definition: MetricDef) -> frozenset[str]:
    return frozenset(
        dependency.name
        for dependency in definition.dependencies
        if isinstance(dependency, DatasetDep)
    )


def metric_dependencies(definition: MetricDef) -> tuple[str, ...]:
    return tuple(
        dependency.metric_id
        for dependency in definition.dependencies
        if isinstance(dependency, MetricDep)
    )


def _blocked(definition: MetricDef, ctx: MetricContext) -> GateDecision | None:
    for metric_id in metric_dependencies(definition):
        upstream = ctx.upstream.get(metric_id)
        if upstream is not None and upstream.status == STATUS_BLOCKED:
            return GateDecision(STATUS_BLOCKED, f"{REASON_UPSTREAM_BLOCKED}:{metric_id}", False)

    datasets = dataset_dependencies(definition)
    flags: Mapping[str, bool] = ctx.flags
    if "bars_daily" in datasets and flags.get("dataset_missing:bars_daily"):
        return GateDecision(STATUS_BLOCKED, f"{REASON_DATASET_UNAVAILABLE}:bars_daily", False)
    if "benchmark_series" in datasets and flags.get("dataset_missing:benchmark_series"):
        return GateDecision(STATUS_BLOCKED, REASON_BENCHMARK_MISSING, False)
    if "asset_build" in datasets and (
        flags.get("asset_build_missing") or flags.get("asset_build_blocked")
    ):
        return GateDecision(STATUS_BLOCKED, REASON_ASSET_BUILD_BLOCKED, False)
    if "asset_daily" in datasets and flags.get("recon_blocked"):
        return GateDecision(STATUS_BLOCKED, REASON_RECON_BLOCKED, False)
    return None


def _stale(definition: MetricDef, ctx: MetricContext) -> GateDecision | None:
    datasets = dataset_dependencies(definition)
    if "asset_build" in datasets and ctx.flags.get("asset_build_stale"):
        return GateDecision(STATUS_STALE, REASON_ASSET_BUILD_STALE, False)
    market_datasets = {"bars_daily", "benchmark_series"} & datasets
    if market_datasets and ctx.flags.get("market_asof_lag"):
        return GateDecision(STATUS_STALE, REASON_MARKET_ASOF_LAG, False)
    return None


def _unreliable(definition: MetricDef, ctx: MetricContext) -> GateDecision | None:
    behavior = definition.sample_policy
    datasets = dataset_dependencies(definition)
    if ctx.flags.get("data_out_of_range"):
        return GateDecision(STATUS_UNRELIABLE, REASON_DATA_OUT_OF_RANGE, True)
    # bar_density 只描述「行情数据覆盖」，只应惩罚依赖 bars_daily 的指标；回撤/β 等
    # 资产日指标不应因为某标的缺 bar 而整指标不可用（2026-10-30 修复）。
    if (
        "bars_daily" in datasets
        and behavior.min_trading_days > 0
        and ctx.flags.get("bar_density_low")
    ):
        return GateDecision(STATUS_UNRELIABLE, REASON_BAR_DENSITY_LOW, True)
    if "asset_daily" in datasets and ctx.flags.get("degraded_rows"):
        return GateDecision(STATUS_UNRELIABLE, REASON_DATA_DEGRADED, True)
    return None


def decide(definition: MetricDef, ctx: MetricContext) -> GateDecision:
    """按优先级返回门禁决定；ok 时 `compute=True`。"""
    for check in (_blocked, _stale, _unreliable):
        decision = check(definition, ctx)
        if decision is not None:
            return decision
    return GateDecision(STATUS_OK, None, True)


__all__ = [
    "REASON_ASSET_BUILD_BLOCKED",
    "REASON_ASSET_BUILD_STALE",
    "REASON_BAR_DENSITY_LOW",
    "REASON_BENCHMARK_MISSING",
    "REASON_DATASET_UNAVAILABLE",
    "REASON_DATA_DEGRADED",
    "REASON_DATA_OUT_OF_RANGE",
    "REASON_MARKET_ASOF_LAG",
    "REASON_PENDING_WINDOW",
    "REASON_RECON_BLOCKED",
    "REASON_UPSTREAM_BLOCKED",
    "REASON_WHITELIST",
    "STATUS_BLOCKED",
    "STATUS_OK",
    "STATUS_STALE",
    "STATUS_UNRELIABLE",
    "GateDecision",
    "dataset_dependencies",
    "decide",
    "metric_dependencies",
]
