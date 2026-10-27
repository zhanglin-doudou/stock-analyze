"""behavior.averaging_down（§3.1 #14，口径 D-25；公式 §7.3.3，exploratory）。

摊平占比 = 加仓中「买价 < 加仓前含费均价（严格）」的比例：
- 候选 = period 内 `category=buy` 且加仓前持仓 qty>0（首次建仓计 n_first_build 不入样）；
- 含费均价由 trades 内存重建（buy/bonus/rights/new_stock 加 qty 与 |amount|，sell 按比例
  扣成本，与 §6.5 重建规则一致）；`cost=0 ∧ qty>0`（纯送转）计 n_zero_cost 不入样；
- `price=0` 剔除计 n_invalid_price；同日多笔按稳定序 (trade_date, line_no, id)。
decomp 给平均下探深度；fwd20 超额留空（M1 未实现，07 报告披露）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.metrics.behavior import averaging_down_rate
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
from stock_review.metrics.defs._common import EmptyParams, trades_until
from stock_review.metrics.gate import (
    REASON_DATA_OUT_OF_RANGE,
    REASON_DATASET_UNAVAILABLE,
    REASON_MARKET_ASOF_LAG,
)

_QTY_PRICE_TO_PRICE6 = 10**8  # cost(fen) × 1e8 / qty(1e4) → 均价 scale=6
_ADD_CATEGORIES = frozenset({"buy", "bonus", "rights", "new_stock"})


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    items: list[DrilldownItem] = []
    for item in trades_until(ctx):
        if item.category != "buy" or item.trade_date < ctx.period_start:
            continue
        if filters.security_code not in (None, item.security_code):
            continue
        items.append(
            DrilldownItem(
                key=item.id,
                value_units=None,
                unit="ratio",
                label=f"{item.security_code} 买入价 {item.price_units}",
                trade_id=item.id,
                event_date=item.trade_date,
                security_code=item.security_code,
            )
        )
    return items


class AveragingDownDef:
    id = "behavior.averaging_down"
    definition_version = "1.0"
    domain = "behavior"
    scope = ScopeSpec(unit="trade", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("trades"), DatasetDep("bars_daily"))
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_DATASET_UNAVAILABLE),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="加仓中买在既有成本之下的比例，反映是否在「越跌越买」。",
        conclusion_en="Share of add-ons bought below the existing average cost.",
        method_zh="对持仓大于零的加仓，比较买价与加仓前含费均价。",
        method_en="For add-on buys with an open position, compare price with prior avg cost.",
        what_to_do_zh="摊平比例高且亏损扩大时，检查是否在逆势加仓。",
        what_to_do_en="When high with growing losses, check for averaging into weakness.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/behavior.averaging_down/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每笔买入与加仓判定。",
        description_en="Each buy and its add-on classification.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        positions: dict[str, list[int]] = {}
        candidates: list[tuple[bool, int, int]] = []
        depths: list[int] = []
        n_first_build = 0
        n_zero_cost = 0
        n_invalid_price = 0
        for trade in trades_until(ctx):
            symbol = trade.security_code
            qty_before, cost_before = positions.get(symbol, [0, 0])
            in_period = trade.trade_date >= ctx.period_start
            if trade.category in _ADD_CATEGORIES:
                if trade.category == "buy" and in_period:
                    if qty_before <= 0:
                        n_first_build += 1
                    elif cost_before <= 0:
                        n_zero_cost += 1
                    elif trade.price_units <= 0:
                        n_invalid_price += 1
                    else:
                        avg_price6 = div_round(
                            cost_before * _QTY_PRICE_TO_PRICE6, qty_before
                        )
                        is_down = trade.price_units < avg_price6
                        candidates.append((True, avg_price6, trade.price_units))
                        if is_down:
                            depths.append(
                                div_round(avg_price6 - trade.price_units, avg_price6, scale=8)
                            )
                qty = qty_before + trade.quantity_units
                cost = cost_before + abs(trade.amount_units)
                positions[symbol] = [qty, cost]
            elif trade.category == "sell":
                qty = qty_before - trade.quantity_units
                if qty_before > 0:
                    cost = cost_before - cost_before * min(trade.quantity_units, qty_before) // qty_before
                else:
                    cost = cost_before
                if qty <= 0:
                    qty, cost = 0, 0
                positions[symbol] = [qty, cost]
        n_addon = len(candidates)
        n_down = len(depths)
        return MetricValue(
            value_units=averaging_down_rate(candidates) if candidates else None,
            n=n_addon,
            decomposition={
                "n_avg_down": n_down,
                "n_addon": n_addon,
                "avg_depth": div_round(sum(depths), n_down) if n_down else None,
                "fwd20_excess_mean": None,
                "n_first_build": n_first_build,
                "n_zero_cost": n_zero_cost,
                "n_invalid_price": n_invalid_price,
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = AveragingDownDef()
