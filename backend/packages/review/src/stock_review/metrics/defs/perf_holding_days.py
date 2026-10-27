"""perf.holding_days（§3.1 #3，口径 A13；公式 §7.1.3，exploratory）。

平均持仓天数 = Σ round.holding_days / n_closed（工作日近似，D-09；open 回合
holding_days=0 必须排除）；decomp 给中位数、自然日口径与资金占用加权口径。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.metrics.perf import holding_days_mean
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
from stock_review.metrics.defs._common import EmptyParams, closed_rounds
from stock_review.metrics.gate import REASON_DATA_OUT_OF_RANGE


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    items: list[DrilldownItem] = []
    for item in closed_rounds(ctx):
        if filters.round_id not in (None, item.id):
            continue
        if filters.security_code not in (None, item.security_code):
            continue
        items.append(
            DrilldownItem(
                key=item.id,
                value_units=item.holding_days * 100,
                unit="days",
                label=f"{item.security_code} 持仓 {item.holding_days} 个交易日",
                round_id=item.id,
                event_date=item.close_date,
                security_code=item.security_code,
            )
        )
    return items


def _mean_scale2(values: list[int]) -> int | None:
    if not values:
        return None
    return div_round(sum(values) * 100, len(values))


class HoldingDaysDef:
    id = "perf.holding_days"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("rounds"), DatasetDep("fills"))
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "days"
    precision = 2
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="本月平仓回合的平均持仓天数，越短说明资金周转越快。",
        conclusion_en="Average holding days of closed rounds; shorter means faster turnover.",
        method_zh="对已平仓回合的持仓交易日数取平均（工作日近似）。",
        method_en="Mean holding trading-days of closed rounds (weekday proxy).",
        what_to_do_zh="结合胜率看：持仓过长且胜率低，可能是被套后被动持有。",
        what_to_do_en="Read with win rate: long holds with low win rate may signal being stuck.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.holding_days/drilldown",
        params={"period": "{period}"},
        description_zh="查看每个已平仓回合的持仓天数。",
        description_en="Inspect holding days of each closed round.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        closed = closed_rounds(ctx)
        if not closed:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={
                    "median_holding_days": None,
                    "mean_natural_days": None,
                    "median_natural_days": None,
                    "capital_weighted_days": None,
                },
            )
        trading_days = [item.holding_days for item in closed]
        natural_days = [
            (item.close_date - item.open_date).days for item in closed if item.close_date
        ]
        round_ids = {item.id for item in closed}
        fills = [fill for fill in ctx.fills if fill.round_id in round_ids]
        capital = sum(fill.buy_cost_units for fill in fills)
        capital_weighted = (
            div_round(sum(fill.holding_days * fill.buy_cost_units for fill in fills) * 100, capital)
            if capital > 0
            else None
        )
        return MetricValue(
            value_units=holding_days_mean(trading_days, scale=2),
            n=len(closed),
            decomposition={
                "median_holding_days": median_units([days * 100 for days in trading_days]),
                "mean_natural_days": _mean_scale2(natural_days),
                "median_natural_days": (
                    median_units([days * 100 for days in natural_days]) if natural_days else None
                ),
                "capital_weighted_days": capital_weighted,
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = HoldingDaysDef()
