"""behavior.turnover（§3.1 #13，口径 D-25；公式 §7.3.2，exploratory）。

换手率（年化）= 双边成交额 × 244 / (逐日平均持仓市值 × 期间交易天数)：
- `num = Σ|amount_units|`（category∈{buy,sell}，period 内；与 perf.fee 共用费用/成交额原语）；
- `avg_mv = HALF_UP(Σ asset_daily.market_value / n_used)`（剔除 blocked 行）；
- decomp 同时给未年化 raw = num/avg_mv。
`avg_mv=0 且 num>0` → 不出值（引擎侧 unreliable）；`num=0` → value=0。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_contracts.constants import A_SHUFFLE_DAYS
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
    period_trades,
)
from stock_review.metrics.gate import (
    REASON_DATA_DEGRADED,
    REASON_DATA_OUT_OF_RANGE,
    REASON_RECON_BLOCKED,
)


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    items: list[DrilldownItem] = []
    for item in period_trades(ctx):
        if item.category not in ("buy", "sell"):
            continue
        if filters.security_code not in (None, item.security_code):
            continue
        items.append(
            DrilldownItem(
                key=item.id,
                value_units=abs(item.amount_units),
                unit="CNY",
                label=f"{item.security_code} {item.category} 成交额",
                trade_id=item.id,
                event_date=item.trade_date,
                security_code=item.security_code,
            )
        )
    return items


class TurnoverDef:
    id = "behavior.turnover"
    definition_version = "1.0"
    domain = "behavior"
    scope = ScopeSpec(unit="day", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("trades"), DatasetDep("asset_daily"))
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_RECON_BLOCKED),
        StatusRule(kind="unreliable", code=REASON_DATA_DEGRADED),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="年化换手率越高，持仓周转越快，摩擦成本也越高。",
        conclusion_en="Higher annualized turnover means faster rotation and more friction.",
        method_zh="本期双边成交额相对日均持仓市值，按 244 个交易日年化。",
        method_en="Double-sided amount over average holding value, annualized by 244 days.",
        what_to_do_zh="换手率高但收益没跟上时，先降频率再优化买卖点。",
        what_to_do_en="When turnover outpaces returns, cut frequency before timing.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/behavior.turnover/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每笔成交的成交额。",
        description_en="Per-trade amount behind turnover.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        legs = [item for item in period_trades(ctx) if item.category in ("buy", "sell")]
        num = sum(abs(item.amount_units) for item in legs)
        buy_amount = sum(abs(item.amount_units) for item in legs if item.category == "buy")
        sell_amount = sum(abs(item.amount_units) for item in legs if item.category == "sell")
        rows = asset_days_in_period(ctx)
        n_blocked = sum(1 for row in rows if row.status == "blocked")
        used = [row for row in rows if row.status != "blocked"]
        n_used = len(used)
        avg_mv = div_round(sum(row.market_value_units for row in used), n_used) if n_used else 0
        raw = div_round(num, avg_mv, scale=8) if avg_mv > 0 else None
        if avg_mv == 0 and num > 0:
            value = None
        elif avg_mv == 0 or n_used == 0:
            value = 0
        else:
            value = div_round(num * A_SHUFFLE_DAYS, avg_mv * n_used, scale=8)
        return MetricValue(
            value_units=value,
            n=n_used,
            decomposition={
                "double_sided_amount": num,
                "buy_amount": buy_amount,
                "sell_amount": sell_amount,
                "avg_market_value": avg_mv,
                "raw_turnover": raw,
                "n_trading_days": sum(1 for row in used if row.trade_date.weekday() < 5),
                "n_natural_days_used": n_used,
                "n_blocked_days": n_blocked,
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = TurnoverDef()
