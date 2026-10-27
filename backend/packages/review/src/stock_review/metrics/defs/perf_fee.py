"""perf.fee（§3.1 #5，口径 A21/D-27；公式 §7.1.5）。

费用占比 = 费用合计 / 双边成交额（毛额，qty×price 口径）；费用含佣金/印花税/其他费
三列与独立费用行（category=fee 且三列全 0 的 |amount|）；amount_units 已含费，
不做二次扣费。turnover=0 → 结构性无定义。
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
    TRADE_LEGS,
    EmptyParams,
    fee_breakdown,
    period_trades,
)
from stock_review.metrics.gate import REASON_DATA_OUT_OF_RANGE


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    items: list[DrilldownItem] = []
    for item in period_trades(ctx):
        if item.category not in TRADE_LEGS and item.category != "fee":
            continue
        if filters.security_code not in (None, item.security_code):
            continue
        fee = item.commission_units + item.stamp_tax_units + item.other_fee_units
        if item.category == "fee" and fee == 0:
            fee = abs(item.amount_units)
        items.append(
            DrilldownItem(
                key=item.id,
                value_units=fee,
                unit="CNY",
                label=f"{item.security_code} {item.category} 费用",
                trade_id=item.id,
                event_date=item.trade_date,
                security_code=item.security_code,
            )
        )
    return items


class FeeDef:
    id = "perf.fee"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="trade", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("trades"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),)
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="本月交易成本占双边成交额的比例越低，摩擦损耗越小。",
        conclusion_en="Lower trading cost share of gross turnover means less friction.",
        method_zh="佣金、印花税、其他费与独立费用行合计，除以本期双边成交毛额。",
        method_en="Commission, stamp tax, other and standalone fees over gross turnover.",
        what_to_do_zh="费用占比偏高时，检查是否交易过于频繁或单笔金额过小。",
        what_to_do_en="When high, check for over-trading or too-small order sizes.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/perf.fee/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每笔成交的费用明细。",
        description_en="Per-trade fee breakdown behind the ratio.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        breakdown = fee_breakdown(period_trades(ctx))
        decomposition = {
            "fee_amount": breakdown.fee_amount,
            "commission": breakdown.commission,
            "stamp_tax": breakdown.stamp_tax,
            "other_fee": breakdown.other_fee,
            "standalone_fee": breakdown.standalone_fee,
            "turnover_gross": breakdown.turnover_gross,
            "turnover_net": breakdown.turnover_net,
            "fee_ratio_net": (
                div_round(breakdown.fee_amount, breakdown.turnover_net, scale=8)
                if breakdown.turnover_net > 0
                else None
            ),
            "n_fee_rows": breakdown.n_fee_rows,
        }
        if breakdown.turnover_gross == 0:
            return MetricValue(
                value_units=None, n=breakdown.n_trade_legs, decomposition=decomposition
            )
        return MetricValue(
            value_units=div_round(breakdown.fee_amount, breakdown.turnover_gross, scale=8),
            n=breakdown.n_trade_legs,
            decomposition=decomposition,
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = FeeDef()
