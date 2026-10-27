"""perf.expectancy（§3.1 #7，口径 A12；公式 §7.1.7，exploratory）。

期望值 `E = p·W − (1−p)·L`（p=胜率、W=平均盈利、L=平均亏损绝对值；有理数合并后
一次舍入，金额分）；零盈亏回合计入 p 的分母（不修 A12 原文），此时 E ≠ mean_pnl
（mean_pnl 放 decomp）。盈亏平衡胜率 `p* = L/(W+L)`：全胜 → null，全负 → 1.0。
CI：bootstrap（均值型，观测=逐回合 pnl，`ci_stat_id="mean"`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

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


class ExpectancyDef:
    id = "perf.expectancy"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="bootstrap")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "CNY"
    precision = 2
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="本月每个平仓回合平均赚的金额，长期为正才值得继续交易。",
        conclusion_en="Expected pnl per closed round; trading only makes sense if positive.",
        method_zh="胜率乘平均盈利减去败率乘平均亏损，附重抽样置信区间。",
        method_en="Win rate times average win minus loss rate times average loss, with CI.",
        what_to_do_zh="期望值为负时，对照盈亏比与胜率找出拖后腿的一侧。",
        what_to_do_en="When negative, compare win rate and pnl ratio to find the weak side.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.expectancy/drilldown",
        params={"period": "{period}"},
        description_zh="查看构成期望值的每个已平仓回合。",
        description_en="Inspect each closed round behind expectancy.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        closed = closed_rounds(ctx)
        if not closed:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={
                    "mean_pnl": None,
                    "p": None,
                    "win_loss_ratio": None,
                    "p_star": None,
                    "n_win": 0,
                    "n_loss": 0,
                    "n_zero": 0,
                },
            )
        wins = [item.pnl_units for item in closed if item.pnl_units > 0]
        losses = [-item.pnl_units for item in closed if item.pnl_units < 0]
        n = len(closed)
        n_zero = n - len(wins) - len(losses)
        with localcontext() as decimal_ctx:
            decimal_ctx.prec = 40
            p = Decimal(len(wins)) / Decimal(n)
            mean_win = Decimal(sum(wins)) / Decimal(len(wins)) if wins else Decimal(0)
            mean_loss = Decimal(sum(losses)) / Decimal(len(losses)) if losses else Decimal(0)
            expectation = p * mean_win - (Decimal(1) - p) * mean_loss
            value = int(expectation.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if not wins:
                p_star: int | None = 100_000_000  # 全负 → p*=1.0
            elif not losses:
                p_star = None  # 全胜 → 无定义
            else:
                p_star_ratio = mean_loss / (mean_win + mean_loss)
                p_star = int(
                    (p_star_ratio * Decimal(10**8)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                )
        win_loss_ratio = (
            div_round(sum(wins) * len(losses), sum(losses) * len(wins), scale=8)
            if wins and losses
            else None
        )
        samples = tuple(sorted((item.pnl_units,) for item in closed))
        return MetricValue(
            value_units=value,
            n=n,
            decomposition={
                "mean_pnl": div_round(sum(item.pnl_units for item in closed), n),
                "p": div_round(len(wins), n, scale=8),
                "win_loss_ratio": win_loss_ratio,
                "p_star": p_star,
                "n_win": len(wins),
                "n_loss": len(losses),
                "n_zero": n_zero,
            },
            ci_sample=samples,
            ci_stat_id="mean",
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = ExpectancyDef()
