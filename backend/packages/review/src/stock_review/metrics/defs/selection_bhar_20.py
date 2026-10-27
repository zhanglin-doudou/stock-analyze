"""selection.bhar_20（§3.1 #16，口径 A24/D-26；公式 §5.5/§7.4.1）。

买入事件 BHAR（h=20 交易日）：两腿统一锚在 `bench_date[j0]`，个股腿
`close_on_or_before` 一次整数除法、基准腿 `return_units` 复利；`value=mean(BHAR)`。
剔除（D-26）：窗口未走完 → n_pending；覆盖率<50% → n_excluded_suspended；
尾部 5 日无 bar 且累计<90% → n_excluded_delisted；事件日无价 → n_excluded_noprice。
缺基准 / 无事件 / 全部剔除 → value=None（全部剔除应出 blocked，状态归引擎，07 报告披露）。
CI：bootstrap（均值型，`ci_stat_id="mean"`）；t 值为事件日聚类 t（§5.5）。
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
from stock_review.metrics.defs._bhar_common import BharEvents, compute_bhar_events
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


def _decomposition(result: BharEvents) -> dict[str, int | None]:
    units = [event.bhar_units for event in result.events]
    return {
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


class Bhar20Def:
    id = "selection.bhar_20"
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
        conclusion_zh="买入后 20 个交易日相对基准的平均超额，为正说明选股有增量。",
        conclusion_en="Mean 20-trading-day post-buy excess vs benchmark; positive adds value.",
        method_zh="对每个买入事件计算买入持有超额收益并取均值，附重抽样置信区间。",
        method_en="Buy-and-hold abnormal return per buy event, averaged, with bootstrap CI.",
        what_to_do_zh="买入后超额收益持续为负时，检查买点是否系统性偏晚。",
        what_to_do_en="When persistently negative, check whether entries are systematically late.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/selection.bhar_20/drilldown",
        params={"period": "{period}"},
        description_zh="查看每个买入事件的 BHAR。",
        description_en="Per-event BHAR behind the mean.",
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
        decomposition = _decomposition(result)
        if result.n_valid == 0:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition=decomposition,
                n_pending=result.n_pending,
            )
        units = [event.bhar_units for event in result.events]
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


DEF = Bhar20Def()
