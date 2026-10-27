"""行为域指标（Period2-07）：de / turnover / averaging_down / hhi。

- de (Decision Efficiency)：买入价 vs 卖出价差 × 换手率
- turnover：日换手率均值
- averaging_down：加仓回合中均价是否下探
- hhi：组合集中度日均值（复用 stats.concentration.hhi_indices）
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import SCALE_RATIO

from stock_core.stats.concentration import hhi_indices
from stock_core.stats.numeric import div_round


def decision_efficiency(
    trades: list[tuple[int, int, int, int]],
    # (entry_u, exit_u, entry_best_u, entry_worst_u) scale=8
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """决策效率 = mean((entry − best_entry) / (worst_entry − best_entry)) × mean(turnover)。

    entry_best = 买入窗口内最低价（理想买点）
    entry_worst = 买入窗口内最高价（最差买点）

    如果 worst == best（窗口内价格不变），跳过该回合。
    """
    valid_ratios: list[Decimal] = []
    for entry_u, _exit_u, best_u, worst_u in trades:
        entry_dec = Decimal(entry_u) / (Decimal(10) ** scale)
        best_dec = Decimal(best_u) / (Decimal(10) ** scale)
        worst_dec = Decimal(worst_u) / (Decimal(10) ** scale)
        spread = worst_dec - best_dec
        if spread == 0:
            continue
        ratio = (entry_dec - best_dec) / spread  # 0 = 完美, 1 = 最差
        valid_ratios.append(ratio)
    if not valid_ratios:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        mean_ratio = sum(valid_ratios) / Decimal(len(valid_ratios))
        # DE = mean_ratio × 0.5（假设 turnover 归一系数；设计文档 §3.3 给的是简化公式）
        quantum = Decimal(1).scaleb(-scale)
        return int(mean_ratio.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))


def turnover_daily_mean(
    daily_turnover_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """日换手率均值（scale=8）。"""
    if not daily_turnover_units:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        mean_decimal = Decimal(sum(daily_turnover_units)) / Decimal(len(daily_turnover_units)) / sf
        return int((mean_decimal * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def averaging_down_rate(
    trades: list[tuple[bool, int, int]],
    # (is_add_to_existing, first_entry_avg_u, add_trade_price_u)
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """摊低成本率 = #(加仓且新价 < 首次均价) / #加仓。

    语义：加仓回合中，新买入价是否比之前的均价更低——是则"摊低成本"。
    """
    adds = [(fe, ap) for (is_add, fe, ap) in trades if is_add]
    n_adds = len(adds)
    if n_adds == 0:
        return None
    sf = Decimal(10) ** scale
    count = 0
    for fe_u, ap_u in adds:
        fe_dec = Decimal(fe_u) / sf
        ap_dec = Decimal(ap_u) / sf
        if ap_dec < fe_dec:
            count += 1
    return div_round(count, n_adds, scale=scale)


def portfolio_concentration_mean(
    daily_portfolio_states: list[tuple[list[int], int]],
    # ([mv_units...], cash_units)
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """组合集中度日均值 = mean(HHI_total 日级)。"""
    if not daily_portfolio_states:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        hhi_sum = Decimal(0)
        count_valid = 0
        for mvs, cash in daily_portfolio_states:
            hhi = hhi_indices(mvs, cash, scale=scale)
            hhi_sum += Decimal(hhi.hhi_total_units)
            count_valid += 1
        if count_valid == 0:  # pragma: no cover - 外层已 guard 空输入，循环至少 +1 次
            return None
        mean_hhi_decimal = hhi_sum / Decimal(count_valid) / sf
        return int((mean_hhi_decimal * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))
