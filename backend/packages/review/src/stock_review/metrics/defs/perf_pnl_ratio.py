"""perf.pnl_ratio（§3.1 #2，口径 A11；公式 §7.1.2）。

盈亏比 = 平均盈利 / |平均亏损|（一次舍入由 core 层完成）；全胜或全亏 → 结构性无定义
（value=None，由引擎判 insufficient）。无 CI（D-15）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.metrics.perf import pnl_ratio as _pnl_ratio
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
                value_units=item.pnl_units,
                unit="CNY",
                label=f"{item.security_code} 回合盈亏",
                round_id=item.id,
                event_date=item.close_date,
                security_code=item.security_code,
            )
        )
    return items


class PnlRatioDef:
    id = "perf.pnl_ratio"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="赚钱回合平均赚的金额与亏钱回合平均亏的金额之比，越大越好。",
        conclusion_en="Average win divided by average loss per round; higher is better.",
        method_zh="对本月已平仓回合，用平均盈利除以平均亏损的绝对值。",
        method_en="Mean profit of winning rounds divided by the absolute mean loss.",
        what_to_do_zh="比值偏低时，检查是否盈利过早了结、亏损过晚止损。",
        what_to_do_en="When low, check whether wins are cut early and losses late.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.pnl_ratio/drilldown",
        params={"period": "{period}"},
        description_zh="查看构成盈亏比的每个已平仓回合。",
        description_en="Inspect each closed round behind the pnl ratio.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        closed = closed_rounds(ctx)
        wins = [item.pnl_units for item in closed if item.pnl_units > 0]
        losses = [item.pnl_units for item in closed if item.pnl_units < 0]
        n_zero = sum(1 for item in closed if item.pnl_units == 0)
        decomposition = {
            "n_win": len(wins),
            "n_loss": len(losses),
            "n_zero": n_zero,
            "avg_win": div_round(sum(wins), len(wins)) if wins else None,
            "avg_loss": div_round(-sum(losses), len(losses)) if losses else None,
            "median_win": median_units(wins) if wins else None,
            "median_loss": median_units([-pnl for pnl in losses]) if losses else None,
        }
        if not closed or not wins or not losses:
            return MetricValue(
                value_units=None,
                n=len(closed),
                decomposition={**decomposition, "pnl_ratio_median": None},
            )
        median_win = median_units(wins)
        median_loss = median_units([-pnl for pnl in losses])
        pnl_ratio_median = div_round(median_win, median_loss, scale=8) if median_loss > 0 else None
        return MetricValue(
            value_units=_pnl_ratio([item.pnl_units for item in closed]),
            n=len(closed),
            decomposition={**decomposition, "pnl_ratio_median": pnl_ratio_median},
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = PnlRatioDef()
