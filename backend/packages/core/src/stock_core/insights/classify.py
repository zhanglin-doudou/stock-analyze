"""回合洞察分类纯函数（设计 §3.5/§3.6；评审 P0-9 要求抽成 core 纯函数）。

只做判定，不做 I/O、不读时钟；阈值以标度整数表达，避免 float。
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from stock_contracts.enums import RoundBucket

# 阈值（比率 scale=1e-8；价格比较用百分比整数 100/103/95）
CONTINUED_DECLINE_B2 = -3 * 10**6  # -3%
WORST_CLOSE_DROP = 95  # P_net × 95%
SELL_FLY_PREMIUM = 103  # P_net × 103%


def bucket_of(holding_trading_days: int | None, *, status: str) -> str:
    """短/波段/趋势分桶；非 closed 或缺日历 → unknown。"""
    if status != "closed" or holding_trading_days is None:
        return RoundBucket.UNKNOWN
    if holding_trading_days <= 1:
        return RoundBucket.SHORT
    if holding_trading_days <= 20:
        return RoundBucket.SWING
    return RoundBucket.TREND


def is_addon(quantity_before_units: int) -> bool:
    """买入前已有持仓 → 加仓（含当日先买后卖，按发生顺序）。"""
    return quantity_before_units > 0


def is_below_avg(price_net_units: int, avg_cost_net_units: int | None) -> bool:
    """净价低于持仓均价；`None`（无成本数据）或负成本（异常）不判定，零成本恒不成立。"""
    if avg_cost_net_units is None or avg_cost_net_units < 0:
        return False
    return price_net_units < avg_cost_net_units


def dip_depth_units(price_net_units: int, avg_cost_net_units: int | None) -> int | None:
    """`(avg − P_net)/avg`（scale 1e-8）；成本缺失、为零（无法归一）或为负 → None。"""
    if avg_cost_net_units is None or avg_cost_net_units <= 0:
        return None
    return (avg_cost_net_units - price_net_units) * 10**8 // avg_cost_net_units


def is_dip_sequence(prices_net_units: Sequence[int]) -> bool:
    """越跌越买：至少 2 次加仓且净买价逐次更低。"""
    return len(prices_net_units) >= 2 and all(
        later < earlier for earlier, later in pairwise(prices_net_units)
    )


def is_rise_sequence(prices_net_units: Sequence[int]) -> bool:
    """越涨越卖：至少 2 次卖出且净卖价逐次更高。"""
    return len(prices_net_units) >= 2 and all(
        later > earlier for earlier, later in pairwise(prices_net_units)
    )


def is_profit_taking(price_net_sell_units: int, avg_cost_net_units: int | None) -> bool:
    """卖出净价高于持仓成本净价 → 止盈型卖出；零成本时任意正卖价即止盈。

    `None`（无成本数据）与负成本（异常）不判定。
    """
    if avg_cost_net_units is None or avg_cost_net_units < 0:
        return False
    return price_net_sell_units > avg_cost_net_units


def sold_too_early(max_close_20_units: int | None, price_net_sell_units: int) -> bool:
    """20 个交易日内最高收盘 > 卖出净价 × 1.03（唯一主口径）。"""
    if max_close_20_units is None or price_net_sell_units <= 0:
        return False
    return max_close_20_units * 100 > price_net_sell_units * SELL_FLY_PREMIUM


def continued_decline(
    b2_20_units: int | None, worst_close_20_units: int | None, price_net_buy_units: int
) -> bool:
    """买后 20D：B2 ≤ −3% 或最差收盘 ≤ 净买价 × 0.95。"""
    if b2_20_units is not None and b2_20_units <= CONTINUED_DECLINE_B2:
        return True
    return (
        worst_close_20_units is not None
        and price_net_buy_units > 0
        and worst_close_20_units * 100 <= price_net_buy_units * WORST_CLOSE_DROP
    )


def additional_loss_units(quantity_units: int, price_a_units: int, price_b_units: int) -> int:
    """`qty × (price_b − price_a)` 的金额影响（scale 2 分）；调用方决定 a/b。"""
    # Qty(4) × Price(6) → Money(2)：除以 1e8
    return quantity_units * (price_b_units - price_a_units) // 10**8
