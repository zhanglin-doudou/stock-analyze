"""behavior.hhi（§3.1 #15，口径 A19/D-13；公式 §7.3.4，exploratory）。

仓位集中度：逐日 `HHI_total=(Σmv_i²+cash²)/total²`（空仓日 HHI_total=1.0 计入）与
`HHI_eq=Σmv_i²/(Σmv_i)²`（空仓日不进 eq 均值）；`value` = 区间 HHI_total 均值。
持仓由 trades 内存重建（§6.5），mv = qty × close_on_or_before（逐标的 floor）；
对账不变量 `Σmv == asset_daily.market_value` 不符日剔除计 n_recon_mismatch；
超卖（负持仓）日剔除计 n_oversell；缺价日剔除计 n_missing_price。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.stats.concentration import hhi_indices
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
    close_ladders,
    close_on_or_before,
    money_units,
    trades_until,
)
from stock_review.metrics.gate import (
    REASON_DATA_DEGRADED,
    REASON_DATA_OUT_OF_RANGE,
    REASON_DATASET_UNAVAILABLE,
    REASON_MARKET_ASOF_LAG,
    REASON_RECON_BLOCKED,
)

_ADD_CATEGORIES = frozenset({"buy", "bonus", "rights", "new_stock"})


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    del filters
    return tuple(
        DrilldownItem(
            key=row.trade_date.isoformat(),
            value_units=row.market_value_units,
            unit="CNY",
            label=f"{row.trade_date.isoformat()} 持仓市值",
            event_date=row.trade_date,
        )
        for row in asset_days_in_period(ctx)
    )


class HhiDef:
    id = "behavior.hhi"
    definition_version = "1.0"
    domain = "behavior"
    scope = ScopeSpec(unit="day", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (
        DatasetDep("trades"),
        DatasetDep("bars_daily"),
        DatasetDep("asset_daily"),
    )
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_DATASET_UNAVAILABLE),
        StatusRule(kind="blocked", code=REASON_RECON_BLOCKED),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
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
        conclusion_zh="仓位越集中，单日波动对净值的影响越大。",
        conclusion_en="More concentrated holdings mean bigger single-name impact.",
        method_zh="逐日计算含现金的赫芬达尔指数并取区间均值。",
        method_en="Daily Herfindahl index including cash, averaged over the period.",
        what_to_do_zh="集中度过高时，考虑分散到相关性更低的标的。",
        what_to_do_en="When too concentrated, diversify into less correlated names.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/behavior.hhi/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每个资产日的持仓市值。",
        description_en="Daily holding market value behind HHI.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        ladders = close_ladders(ctx.bars)
        trades = trades_until(ctx)
        rows = asset_days_in_period(ctx)
        positions: dict[str, int] = {}
        cursor = 0
        totals: list[int] = []
        eqs: list[int] = []
        holding_counts: list[int] = []
        cash_weights: list[int] = []
        n_blocked = 0
        n_oversell = 0
        n_missing_price = 0
        n_recon_mismatch = 0
        n_empty = 0
        for row in rows:
            while cursor < len(trades) and trades[cursor].trade_date <= row.trade_date:
                trade = trades[cursor]
                cursor += 1
                if trade.category in _ADD_CATEGORIES:
                    positions[trade.security_code] = (
                        positions.get(trade.security_code, 0) + trade.quantity_units
                    )
                elif trade.category == "sell":
                    positions[trade.security_code] = (
                        positions.get(trade.security_code, 0) - trade.quantity_units
                    )
            if row.status == "blocked":
                n_blocked += 1
                continue
            if any(qty < 0 for qty in positions.values()):
                n_oversell += 1
                continue
            mvs: list[int] = []
            missing = False
            for symbol, qty in positions.items():
                if qty == 0:
                    continue
                close = close_on_or_before(ladders, symbol, row.trade_date)
                if close is None:
                    missing = True
                    break
                mvs.append(money_units(qty, close))
            if missing:
                n_missing_price += 1
                continue
            if sum(mvs) != row.market_value_units:
                n_recon_mismatch += 1
                continue
            if not mvs:
                n_empty += 1
            hhi = hhi_indices(mvs, row.cash_units)
            totals.append(hhi.hhi_total_units)
            if hhi.hhi_eq_units is not None:
                eqs.append(hhi.hhi_eq_units)
            holding_counts.append(len(mvs))
            total_assets = row.cash_units + sum(mvs)
            if total_assets > 0:
                cash_weights.append(div_round(row.cash_units, total_assets, scale=8))
        n_used = len(totals)
        if n_used == 0:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={
                    "n_days_used": 0,
                    "n_blocked_days": n_blocked,
                    "n_days_empty": n_empty,
                    "n_recon_mismatch": n_recon_mismatch,
                    "n_missing_price": n_missing_price,
                    "n_oversell": n_oversell,
                },
            )
        return MetricValue(
            value_units=div_round(sum(totals), n_used),
            n=n_used,
            decomposition={
                "hhi_total_max": max(totals),
                "hhi_total_end": totals[-1],
                "hhi_eq_mean": div_round(sum(eqs), len(eqs)) if eqs else None,
                "hhi_eq_max": max(eqs) if eqs else None,
                "hhi_eq_end": eqs[-1] if eqs else None,
                "avg_holding_count": div_round(sum(holding_counts) * 100, n_used),
                "avg_cash_weight": (
                    div_round(sum(cash_weights), len(cash_weights)) if cash_weights else None
                ),
                "n_days_used": n_used,
                "n_blocked_days": n_blocked,
                "n_days_empty": n_empty,
                "n_recon_mismatch": n_recon_mismatch,
                "n_missing_price": n_missing_price,
                "n_oversell": n_oversell,
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = HhiDef()
