"""behavior.de（§3.1 #12，口径 A15 勘误/D-20；公式 §7.3.1）。

处置效应 `DE = PGR − PLR`（Odean）：
- `G/L` = period 内已平仓盈利/亏损回合数（pnl=0 计 n_zero，data_error 剔除）；
- `g/l` = 期末未平仓回合的纸面盈/亏数（直接取上游 rebuild 的 open RoundView.pnl_units，
  与 win_rate 全样本口径一致，不做 def 内持仓重建——差异已在 07 报告披露）；
- `DE = HALF_UP((G(L+l)−L(G+g))×1e8 / ((G+g)(L+l)))`（一次舍入）。
`G+g=0` 或 `L+l=0` → 结构性无定义。CI：bootstrap（观测码 0/1/2/3，
`ci_stat_id="ratio_diff_pgr_plr"`）。
"""

from __future__ import annotations

from collections.abc import Sequence

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
    for item in [*closed_rounds(ctx), *open_rounds(ctx)]:
        if filters.round_id not in (None, item.id):
            continue
        if filters.security_code not in (None, item.security_code):
            continue
        items.append(
            DrilldownItem(
                key=item.id,
                value_units=item.pnl_units,
                unit="CNY",
                label=f"{item.security_code} {'已实现' if item.status == 'closed' else '纸面'}盈亏",
                round_id=item.id,
                event_date=item.close_date or item.open_date,
                security_code=item.security_code,
            )
        )
    return items


class DispositionEffectDef:
    id = "behavior.de"
    definition_version = "1.0"
    domain = "behavior"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="bootstrap")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="DE 为正说明倾向于过早卖盈、过久抱亏；越大处置效应越明显。",
        conclusion_en="Positive DE means winners are sold too early and losers held too long.",
        method_zh="比较盈利与亏损的「实现比例」之差（PGR−PLR），附重抽样置信区间。",
        method_en="Difference between gain and loss realization rates (PGR−PLR), with CI.",
        what_to_do_zh="DE 偏高时，给亏损单预设止损、给盈利单更多持有时间。",
        what_to_do_en="When high, preset stops for losers and give winners more room.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/behavior.de/drilldown",
        params={"period": "{period}"},
        description_zh="查看计入处置效应的已实现与纸面盈亏。",
        description_en="Realized and paper pnl behind the disposition effect.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        closed = closed_rounds(ctx)
        opened = open_rounds(ctx)
        sold_gain = [item for item in closed if item.pnl_units > 0]
        sold_loss = [item for item in closed if item.pnl_units < 0]
        paper_gain = [item for item in opened if item.pnl_units > 0]
        paper_loss = [item for item in opened if item.pnl_units < 0]
        n_zero = len(closed) - len(sold_gain) - len(sold_loss)
        big_g, big_l = len(sold_gain), len(sold_loss)
        small_g, small_l = len(paper_gain), len(paper_loss)
        gain_total = big_g + small_g
        loss_total = big_l + small_l
        decomposition = {
            "n_sold_gain": big_g,
            "n_sold_loss": big_l,
            "n_paper_gain": small_g,
            "n_paper_loss": small_l,
            "n_zero": n_zero,
            "n_data_error": data_error_rounds_count(ctx),
            "pgr": div_round(big_g, gain_total, scale=8) if gain_total else None,
            "plr": div_round(big_l, loss_total, scale=8) if loss_total else None,
            "holding_days_diff": (
                None
                if not (sold_gain and sold_loss)
                else div_round(sum(item.holding_days for item in sold_loss) * 100, len(sold_loss))
                - div_round(sum(item.holding_days for item in sold_gain) * 100, len(sold_gain))
            ),
        }
        n = gain_total + loss_total
        if gain_total == 0 or loss_total == 0:
            return MetricValue(value_units=None, n=n, decomposition=decomposition)
        numerator = big_g * loss_total - big_l * gain_total
        codes = sorted([(0,)] * big_g + [(1,)] * big_l + [(2,)] * small_g + [(3,)] * small_l)
        return MetricValue(
            value_units=div_round(numerator, gain_total * loss_total, scale=8),
            n=n,
            decomposition=decomposition,
            ci_sample=tuple(codes),
            ci_stat_id="ratio_diff_pgr_plr",
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = DispositionEffectDef()
