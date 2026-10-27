"""perf.win_rate（§3.1 #1，口径 A10；公式 §7.1.1）。

已平仓胜率 = wins / n_closed（`pnl>0` 计胜；`pnl=0` 计入分母但既非胜也非负）；
decomp 并列全样本胜率（含 open 回合浮盈亏，直接取上游 rebuild 的 `RoundView.pnl_units`，
不做 def 内持仓重建）。CI：Wilson（0/1 观测，引擎代算）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.metrics.perf import win_rate as _win_rate

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
from stock_review.metrics.defs._common import (
    EmptyParams,
    closed_rounds,
    data_error_rounds_count,
    open_rounds,
)
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
                value_units=item.pnl_units,
                unit="CNY",
                label=f"{item.security_code} 回合盈亏",
                round_id=item.id,
                event_date=item.close_date,
                security_code=item.security_code,
            )
        )
    return items


class WinRateDef:
    id = "perf.win_rate"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="wilson")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="本月平仓的回合里，盈利回合占比越高，说明出手质量越好。",
        conclusion_en="A higher share of profitable closed rounds means better trade quality.",
        method_zh="统计本月已平仓回合中盈利回合的占比，并给出 95% 置信区间。",
        method_en="Share of closed rounds with positive pnl, with a 95% confidence interval.",
        what_to_do_zh="查看下钻里的亏损回合，确认亏损是否集中在同一标的。",
        what_to_do_en="Inspect losing rounds in the drilldown to see whether losses cluster.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.win_rate/drilldown",
        params={"period": "{period}"},
        description_zh="查看构成胜率的每个已平仓回合。",
        description_en="Inspect each closed round behind the win rate.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        closed = closed_rounds(ctx)
        opened = open_rounds(ctx)
        n_data_error = data_error_rounds_count(ctx)
        base_decomposition = {
            "n_closed": len(closed),
            "n_open": len(opened),
            "n_data_error": n_data_error,
        }
        if not closed:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={**base_decomposition, "wins": 0, "n_zero": 0, "n_all": len(opened)},
            )
        wins = sum(1 for item in closed if item.pnl_units > 0)
        n_zero = sum(1 for item in closed if item.pnl_units == 0)
        all_rounds = [*closed, *opened]
        win_rate_all = _win_rate([item.pnl_units for item in all_rounds]) if all_rounds else None
        samples = tuple(sorted([(1,)] * wins + [(0,)] * (len(closed) - wins)))
        return MetricValue(
            value_units=_win_rate([item.pnl_units for item in closed]),
            n=len(closed),
            decomposition={
                **base_decomposition,
                "wins": wins,
                "n_zero": n_zero,
                "n_all": len(all_rounds),
                "win_rate_all": win_rate_all,
            },
            ci_sample=samples,
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = WinRateDef()
