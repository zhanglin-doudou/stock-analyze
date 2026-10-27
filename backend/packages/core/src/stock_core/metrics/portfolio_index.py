"""组合级收益 / TWR（Period2-07）。

提供从 DailyPortfolio 序列 → 日收益率 / 指数 / TWR 的纯函数转换。
TWR 也可直接调用 `stock_core.stats.drawdown.drawdown_family` 获取回撤族全部成员。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import SCALE_RATIO

from stock_core.metrics.holdings import DailyPortfolio


def daily_portfolio_returns(
    portfolios: list[DailyPortfolio],
    *,
    scale: int = SCALE_RATIO,
) -> list[int]:
    """从 DailyPortfolio 序列计算相邻日的**日收益率**（scale=8）。

    输入：按日期升序的 DailyPortfolio 序列（至少 2 个元素）。
    输出：len-1 个日收益率，r_t = (A_t − A_{t−1}) / A_{t−1}（A = 总资产）。

    边界：A_{t−1} = 0 → 该日收益率跳过（返回列表长度 < len-1 的情况
    由调用方按 blocked/unreliable 处理）。
    """
    if len(portfolios) < 2:
        return []

    with localcontext() as ctx:
        ctx.prec = 40
        Decimal(10) ** scale
        returns: list[int] = []
        for i in range(1, len(portfolios)):
            prev = portfolios[i - 1].total_assets_units
            cur = portfolios[i].total_assets_units
            if prev == 0:
                continue
            r_dec = (Decimal(cur) - Decimal(prev)) / Decimal(prev)
            quantum = Decimal(1).scaleb(-scale)
            r_u = int(r_dec.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))
            returns.append(r_u)
        return returns


def compound_index(
    daily_returns_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int:
    """计算 TWR（时间加权收益率）终值 = Π(1 + r_t) − 1，scale 整数。

    等价于 drawdown_family 的 twr_annual_units 但这里**不年化**、直接返回
    整个区间的累计 TWR。
    """
    if not daily_returns_units:
        return 0
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        product = Decimal(1)
        for r_u in daily_returns_units:
            product *= Decimal(1) + Decimal(r_u) / sf
        twr = product - Decimal(1)
        quantum = Decimal(1).scaleb(-scale)
        return int(twr.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))
