"""择时域指标（Period2-07）：chase / fly / beta / drawdown。

- chase / fly：回合级，需要"买入后 / 卖出后 ×% 反向价出现"的信号
- beta：OLS β（复用 stats.regression.beta_alpha）
- drawdown：回撤族（复用 stats.drawdown.drawdown_family）
"""

from __future__ import annotations

from decimal import Decimal

from stock_contracts.constants import RF_ANNUAL_DEFAULT, SCALE_RATIO

from stock_core.metrics import policy
from stock_core.stats.drawdown import DrawdownFamily, drawdown_family
from stock_core.stats.numeric import div_round
from stock_core.stats.regression import BetaAlpha, beta_alpha, rf_daily_units


def chase_ratio(
    trades: list[tuple[int, int]],  # (entry_price_scale8, post_max_price_scale8)
    *,
    threshold_units: int = policy.CHASE_THRESHOLD_SCALE8,
    scale: int = SCALE_RATIO,
) -> int | None:
    """追涨率 = #(entry_price × (1 + threshold) < post_max) / N。

    语义：买入后窗口内的最高价是否超过了"阈值涨幅"——超过了意味着如果等一等可能买得更低，
    即"追涨"行为。

    Args:
        trades: 每回合 (买入价, 买入后窗口内最高价)，scale=8。
        threshold_units: 判定阈值（默认 3%），scale=8。
    """
    n = len(trades)
    if n == 0:
        return None
    # 追涨 = post_max > entry_price × (1 + threshold)
    threshold_factor = Decimal(10) ** scale + Decimal(threshold_units)
    sf = Decimal(10) ** scale
    count = 0
    for entry_u, post_max_u in trades:
        entry_dec = Decimal(entry_u) / sf
        post_max_dec = Decimal(post_max_u) / sf
        threshold_break = entry_dec * threshold_factor / sf
        if post_max_dec > threshold_break:
            count += 1
    return div_round(count, n, scale=scale)


def fly_ratio(
    trades: list[tuple[int, int]],  # (exit_price_scale8, post_max_price_scale8)
    *,
    threshold_units: int = policy.FLY_THRESHOLD_SCALE8,
    scale: int = SCALE_RATIO,
) -> int | None:
    """杀跌率 = #(post_max > exit_price × (1 + threshold)) / N。

    语义：卖出后窗口内最高价超过了"阈值涨幅"——卖早了（"杀跌"）。
    """
    n = len(trades)
    if n == 0:
        return None
    threshold_factor = Decimal(10) ** scale + Decimal(threshold_units)
    sf = Decimal(10) ** scale
    count = 0
    for exit_u, post_max_u in trades:
        exit_dec = Decimal(exit_u) / sf
        post_max_dec = Decimal(post_max_u) / sf
        threshold_break = exit_dec * threshold_factor / sf
        if post_max_dec > threshold_break:
            count += 1
    return div_round(count, n, scale=scale)


def timing_beta(
    portfolio_daily_returns_units: list[int],
    market_daily_returns_units: list[int],
    *,
    rf_annual: Decimal = RF_ANNUAL_DEFAULT,
    scale: int = SCALE_RATIO,
) -> BetaAlpha:
    """择时 β：OLS 超额收益回归（调用 stats.regression.beta_alpha）。"""
    rf_d = rf_daily_units(rf_annual, scale=12)
    return beta_alpha(
        portfolio_daily_returns_units,
        market_daily_returns_units,
        rf_daily_units=rf_d,
        scale=scale,
    )


def timing_drawdown(
    portfolio_daily_returns_units: list[int],
    *,
    natural_days: int | None = None,
    blocked: bool = False,
    scale: int = SCALE_RATIO,
) -> DrawdownFamily:
    """择时回撤族：调用 stats.drawdown.drawdown_family。"""
    return drawdown_family(
        portfolio_daily_returns_units,
        natural_days=natural_days,
        blocked=blocked,
        scale=scale,
    )
