"""测试用指标 def：期望值（Bootstrap CI，成对观测形状的占位）。

仅测试注入（E-10）；`ci_stat_id="mean"` 走 `stock_core.stats.estimators` 注册表。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict
from stock_core.stats.numeric import div_round
from stock_review.metrics.base import (
    DatasetDep,
    DrilldownFilters,
    DrilldownItem,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
)


class ExpectancyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_open: bool = False


def _rounds(
    ctx: MetricContext,
    filters: DrilldownFilters | None = None,
    *,
    include_open: bool = False,
):
    statuses = {"closed", "open"} if include_open else {"closed"}
    return [
        item
        for item in ctx.rounds
        if item.status in statuses
        and item.return_rate_units is not None
        and (filters is None or filters.round_id in (None, item.round_id))
    ]


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    return [
        DrilldownItem(
            key=item.round_id,
            value_units=item.return_rate_units,
            unit="ratio",
            label=f"{item.round_id} 回合收益",
            round_id=item.round_id,
            event_date=item.close_date or item.open_date,
        )
        for item in _rounds(ctx, filters)
    ]


def _include_open(ctx: MetricContext) -> bool:
    """参数从 ctx.params 读取；下钻函数无法访问 params 时按默认 False 处理。"""
    return bool(getattr(ctx, "params", {}).get("include_open", False))


class ExpectancyStubDef:
    id = "perf.expectancy_stub"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = ExpectancyParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="bootstrap")
    status_policy = ()
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="本月每个回合的平均收益越高，期望值越好。",
        conclusion_en="A higher average return per round means better expectancy.",
        method_zh="对本月已结束回合的收益率取平均，并用重抽样给出置信区间。",
        method_en="Average return per closed round, with a resampling confidence interval.",
        what_to_do_zh="对比不同标的的平均收益，找出拖累期望值的回合。",
        what_to_do_en="Compare average return by symbol to find rounds dragging expectancy.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/rounds",
        params={"period": "{period}"},
        description_zh="查看构成期望值的每个回合收益。",
        description_en="Inspect each round return behind expectancy.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        rounds = _rounds(ctx, None, include_open=_include_open(ctx))
        if not rounds:
            return MetricValue(value_units=None, n=0, decomposition={})
        total = sum(item.return_rate_units or 0 for item in rounds)
        observations = tuple(sorted((item.return_rate_units or 0,) for item in rounds))
        return MetricValue(
            value_units=div_round(total, len(rounds)),
            n=len(rounds),
            decomposition={"sum_units": total},
            ci_sample=observations,
            ci_stat_id="mean",
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = ExpectancyStubDef()
