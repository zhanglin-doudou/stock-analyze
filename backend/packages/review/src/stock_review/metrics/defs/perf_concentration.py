"""perf.concentration（§3.1 #4，口径 A14/D-10；公式 §7.1.4，exploratory）。

盈亏集中度 = Top3 盈利回合盈利合计 / 全部盈利回合盈利合计（仅 pnl>0 入样）；
decomp 给 CR1、Gini（盈利升序）、n_pos 等。n_pos=0 → 结构性无定义；n_pos<2 → Gini null。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.stats.concentration import cr1_units, gini_units, top_k_share

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

_TOP_K = 3


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    items: list[DrilldownItem] = []
    winners = [item for item in closed_rounds(ctx) if item.pnl_units > 0]
    winners.sort(key=lambda item: (-item.pnl_units, item.id))
    for item in winners:
        if filters.round_id not in (None, item.id):
            continue
        if filters.security_code not in (None, item.security_code):
            continue
        items.append(
            DrilldownItem(
                key=item.id,
                value_units=item.pnl_units,
                unit="CNY",
                label=f"{item.security_code} 盈利贡献",
                round_id=item.id,
                event_date=item.close_date,
                security_code=item.security_code,
            )
        )
    return items


class ConcentrationDef:
    id = "perf.concentration"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="盈利越集中在少数几个回合，业绩对单次运气的依赖越大。",
        conclusion_en="The more profit concentrates in a few rounds, the luckier the result.",
        method_zh="盈利最多的 3 个回合占全部盈利回合盈利的比例（仅统计盈利回合）。",
        method_en="Share of the top-3 winning rounds in total profits (winners only).",
        what_to_do_zh="集中度过高时，回看这几个回合是否可复制。",
        what_to_do_en="When concentration is high, check whether those rounds are repeatable.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.concentration/drilldown",
        params={"period": "{period}"},
        description_zh="按盈利贡献降序查看盈利回合。",
        description_en="Winning rounds sorted by profit contribution.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        winners = sorted(
            (item.pnl_units for item in closed_rounds(ctx) if item.pnl_units > 0), reverse=True
        )
        n_pos = len(winners)
        if n_pos == 0:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={
                    "cr1": None,
                    "gini": None,
                    "n_pos": 0,
                    "top3_k": 0,
                    "top3_amount": 0,
                    "positive_total": 0,
                },
            )
        total = sum(winners)
        top_k = min(_TOP_K, n_pos)
        return MetricValue(
            value_units=top_k_share(winners, total, k=_TOP_K, scale=8),
            n=n_pos,
            decomposition={
                "cr1": cr1_units(winners, total, scale=8),
                "gini": gini_units(sorted(winners), scale=8),
                "n_pos": n_pos,
                "top3_k": top_k,
                "top3_amount": sum(winners[:top_k]),
                "positive_total": total,
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = ConcentrationDef()
