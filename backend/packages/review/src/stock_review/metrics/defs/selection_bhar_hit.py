"""selection.bhar_hit（§3.1 #18；公式 §7.4.3，exploratory）。

BHAR_20 命中占比 = #(BHAR_20 > 0) / n_valid（BHAR=0 不计命中）；事件集与
selection.bhar_20 同口径复算（依赖 MetricDep 保证拓扑序与血缘）。CI：Wilson（0/1 观测）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.metrics.selection import bhar_hit_rate, bhar_mean
from stock_core.stats.numeric import median_units

from stock_review.metrics.base import (
    DatasetDep,
    DrilldownFilters,
    DrilldownItem,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricDep,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
    StatusRule,
)
from stock_review.metrics.defs._bhar_common import compute_bhar_events
from stock_review.metrics.defs._common import EmptyParams
from stock_review.metrics.gate import (
    REASON_BENCHMARK_MISSING,
    REASON_DATA_OUT_OF_RANGE,
    REASON_DATASET_UNAVAILABLE,
    REASON_MARKET_ASOF_LAG,
)

HORIZON = 20


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    result = compute_bhar_events(ctx, horizon=HORIZON)
    if result is None:
        return ()
    items: list[DrilldownItem] = []
    for event in result.events:
        if filters.security_code not in (None, event.security_code):
            continue
        items.append(
            DrilldownItem(
                key=event.trade_id,
                value_units=event.bhar_units,
                unit="ratio",
                label=f"{event.security_code} 买入后 {HORIZON} 日 BHAR",
                trade_id=event.trade_id,
                event_date=event.event_date,
                security_code=event.security_code,
            )
        )
    return items


class BharHitDef:
    id = "selection.bhar_hit"
    definition_version = "1.0"
    domain = "selection"
    scope = ScopeSpec(unit="trade", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (
        MetricDep("selection.bhar_20"),
        DatasetDep("trades"),
        DatasetDep("bars_daily"),
        DatasetDep("benchmark_series"),
    )
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="wilson")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_DATASET_UNAVAILABLE),
        StatusRule(kind="blocked", code=REASON_BENCHMARK_MISSING),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "async"
    benchmark_required = True
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="买入后 20 日跑赢基准的事件占比，越高说明选股命中率越好。",
        conclusion_en="Share of buy events beating the benchmark over 20 trading days.",
        method_zh="统计 BHAR_20 大于零的事件占比，并给出 95% 置信区间。",
        method_en="Share of events with positive 20-day BHAR, with a 95% confidence interval.",
        what_to_do_zh="命中率低但均值高时，说明少数大赢家在拉动，关注集中度风险。",
        what_to_do_en="Low hit with high mean means a few big winners drive the result.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/selection.bhar_hit/drilldown",
        params={"period": "{period}"},
        description_zh="查看每个买入事件的 BHAR 与命中情况。",
        description_en="Per-event BHAR and hit flag.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        result = compute_bhar_events(ctx, horizon=HORIZON)
        if result is None:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={"benchmark_id": ctx.benchmark, "n_pending": 0},
            )
        units = [event.bhar_units for event in result.events]
        n_hit = sum(1 for unit in units if unit > 0)
        decomposition: dict[str, int | None] = {
            "n_hit": n_hit,
            "mean": bhar_mean(units) if units else None,
            "median": median_units(units) if units else None,
            "n_pending": result.n_pending,
            "n_excluded_suspended": result.n_excluded_suspended,
            "n_excluded_delisted": result.n_excluded_delisted,
            "n_excluded_noprice": result.n_excluded_noprice,
        }
        if result.n_valid == 0:
            return MetricValue(
                value_units=None, n=0, decomposition=decomposition, n_pending=result.n_pending
            )
        samples = tuple(sorted([(1,)] * n_hit + [(0,)] * (result.n_valid - n_hit)))
        return MetricValue(
            value_units=bhar_hit_rate(units),
            n=result.n_valid,
            decomposition=decomposition,
            n_pending=result.n_pending,
            ci_sample=samples,
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = BharHitDef()
