"""perf.fee_breakeven（§3.1 #6，口径 A22/D-11；公式 §7.1.6）。

费用回本门槛 = 年化费用 / 期间日均持仓市值：
- `annualized_fee = HALF_UP(fee × 365 / D)`（D=期间自然日数）；
- `avg_holding_mv = HALF_UP(Σ asset_daily.market_value / n_ok)`（只取 status≠blocked 行）；
- `twr_eaten_pct = annualized_fee / (|twr_amount| + annualized_fee)`（仅 TWR>0 时给出）。

注意：公式口径经人工确认以 §7.1.6 为准（2026-10-27，07 报告歧义点 b）；
core 层曾有的「净亏损/(总绝对盈亏×2)」近似已删除。区间含 blocked 行 → 不出值。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_core.metrics.portfolio_index import compound_index
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
    asset_days_in_period,
    fee_breakdown,
    period_trades,
)
from stock_review.metrics.gate import (
    REASON_ASSET_BUILD_BLOCKED,
    REASON_ASSET_BUILD_STALE,
    REASON_DATA_DEGRADED,
    REASON_DATA_OUT_OF_RANGE,
    REASON_RECON_BLOCKED,
)

_RATIO = Decimal(10) ** 8


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    del filters
    return tuple(
        DrilldownItem(
            key=row.trade_date.isoformat(),
            value_units=row.daily_return_units,
            unit="ratio",
            label=f"{row.trade_date.isoformat()} 日收益",
            event_date=row.trade_date,
        )
        for row in asset_days_in_period(ctx)
        if row.status != "blocked"
    )


def _annualized_twr_units(twr_units: int, natural_days: int) -> int | None:
    if natural_days <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        base = Decimal(1) + Decimal(twr_units) / _RATIO
        if base <= 0:
            return None
        annualized = base ** (Decimal(365) / Decimal(natural_days)) - Decimal(1)
        quantum = Decimal(1).scaleb(-8)
        return int(annualized.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(8))


class FeeBreakevenDef:
    id = "perf.fee_breakeven"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="day", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("trades"), DatasetDep("asset_daily"), DatasetDep("asset_build"))
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_RECON_BLOCKED),
        StatusRule(kind="blocked", code=REASON_ASSET_BUILD_BLOCKED),
        StatusRule(kind="stale", code=REASON_ASSET_BUILD_STALE),
        StatusRule(kind="unreliable", code=REASON_DATA_DEGRADED),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="按本月成交成本折算成年化，相当于每年吃掉持仓市值的一个比例。",
        conclusion_en="Annualized trading cost as a share of average holding market value.",
        method_zh="本期费用年化后除以期间日均持仓市值，并给出费用吃掉收益的比例。",
        method_en="Annualized fees over average holding value, plus share of TWR eaten.",
        what_to_do_zh="门槛高于你的历史收益水平时，先降低交易频率。",
        what_to_do_en="When the hurdle exceeds your historical returns, trade less first.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.fee_breakeven/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每个资产日的收益与市值。",
        description_en="Daily asset rows behind the breakeven hurdle.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        rows = asset_days_in_period(ctx)
        n_blocked = sum(1 for row in rows if row.status == "blocked")
        breakdown = fee_breakdown(period_trades(ctx))
        base = {"fee_amount": breakdown.fee_amount, "n_blocked_days": n_blocked}
        if n_blocked > 0 or not rows:
            return MetricValue(value_units=None, n=0, decomposition=base)

        natural_days = (ctx.period_end - ctx.period_start).days + 1
        annualized_fee = div_round(breakdown.fee_amount * 365, natural_days)
        avg_holding_mv = div_round(sum(row.market_value_units for row in rows), len(rows))
        returns = [row.daily_return_units for row in rows if row.daily_return_units is not None]
        twr_units = compound_index(returns)
        span_days = (rows[-1].trade_date - rows[0].trade_date).days
        annualized_twr = _annualized_twr_units(twr_units, span_days)
        avg_total = div_round(sum(row.total_assets_units for row in rows), len(rows))
        twr_eaten: int | None = None
        if annualized_twr is not None and twr_units > 0:
            twr_amount = div_round(avg_total * annualized_twr, 10**8)
            denominator = abs(twr_amount) + annualized_fee
            if denominator > 0:
                twr_eaten = div_round(annualized_fee, denominator, scale=8)

        value = div_round(annualized_fee, avg_holding_mv, scale=8) if avg_holding_mv > 0 else None
        return MetricValue(
            value_units=value,
            n=len(rows),
            decomposition={
                **base,
                "annualized_fee": annualized_fee,
                "avg_holding_mv": avg_holding_mv,
                "twr": twr_units,
                "annualized_twr": annualized_twr,
                "twr_eaten_pct": twr_eaten,
                "n_days": len(rows),
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = FeeBreakevenDef()
