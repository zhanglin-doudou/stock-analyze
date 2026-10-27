"""selection.bhar_60（§3.1 #17，口径 A24/D-26；公式 §5.5/§7.4.2）。

与 selection.bhar_20 逐项相同，仅窗口 `h=60` 个交易日（样本更少、pending 更多）。
CI：bootstrap（均值型，`ci_stat_id="mean"`）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.metrics.selection import bhar_cluster_t, bhar_hit_rate, bhar_mean
from stock_core.stats.numeric import div_round, median_units

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

HORIZON = 60


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


class Bhar60Def:
    id = "selection.bhar_60"
    definition_version = "1.0"
    domain = "selection"
    scope = ScopeSpec(unit="trade", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (
        DatasetDep("trades"),
        DatasetDep("bars_daily"),
        DatasetDep("benchmark_series"),
    )
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="bootstrap")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_DATASET_UNAVAILABLE),
        StatusRule(kind="blocked", code=REASON_BENCHMARK_MISSING),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "async"
    benchmark_required = True
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="买入后 60 个交易日相对基准的平均超额，看中期选股能力。",
        conclusion_en="Mean 60-trading-day post-buy excess vs benchmark for mid-term skill.",
        method_zh="对每个买入事件计算 60 日买入持有超额收益并取均值，附重抽样置信区间。",
        method_en="60-day buy-and-hold abnormal return per buy event, averaged, with CI.",
        what_to_do_zh="与 20 日口径对比，判断超额是转瞬即逝还是持续累积。",
        what_to_do_en="Compare with the 20-day variant to see whether alpha persists.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/selection.bhar_60/drilldown",
        params={"period": "{period}"},
        description_zh="查看每个买入事件的 60 日 BHAR。",
        description_en="Per-event 60-day BHAR behind the mean.",
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
        decomposition: dict[str, int | None] = {
            "median": median_units(units) if units else None,
            "t": bhar_cluster_t([(event.bench_index, event.bhar_units) for event in result.events]),
            "hit_rate": bhar_hit_rate(units) if units else None,
            "n": result.n_valid,
            "n_pending": result.n_pending,
            "n_excluded_suspended": result.n_excluded_suspended,
            "n_excluded_delisted": result.n_excluded_delisted,
            "n_excluded_noprice": result.n_excluded_noprice,
            "mean_benchmark_return": (
                div_round(sum(event.bench_cum_units for event in result.events), result.n_valid)
                if result.n_valid
                else None
            ),
        }
        if result.n_valid == 0:
            return MetricValue(
                value_units=None, n=0, decomposition=decomposition, n_pending=result.n_pending
            )
        return MetricValue(
            value_units=bhar_mean(units),
            n=result.n_valid,
            decomposition=decomposition,
            n_pending=result.n_pending,
            ci_sample=tuple(sorted((unit,) for unit in units)),
            ci_stat_id="mean",
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = Bhar60Def()
