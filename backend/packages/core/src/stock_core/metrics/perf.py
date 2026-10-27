"""绩效域指标（Period2-07）：win_rate / pnl_ratio / holding_days / concentration / fee / expectancy。

全部为纯函数，零 I/O、零时钟、零墙钟依赖；输入为回合级元组（pnl_units, holding_days, fee_units...），
输出为标度整数（scale=8 比率或 scale=2 金额）。

注：perf.fee_breakeven 口径（§7.1.6：年化费用/日均持仓市值）依赖 asset_daily 视图，
由 review 层 def 直接实现，不在本模块。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import A_SHUFFLE_DAYS, SCALE_RATIO

# —— 回合级数据结构（纯函数用的输入容器）——

def _mean_dec(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    return sum(values) / Decimal(len(values))


# —— 通用分位锚点 ——

def win_rate(
    pnl_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """胜率 = #(pnl > 0) / N（pnl 标度不限，只要同 scale 即可比较）。"""
    n = len(pnl_units)
    if n == 0:
        return None
    wins = sum(1 for p in pnl_units if p > 0)
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        result = Decimal(wins) / Decimal(n)
        return int((result * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pnl_ratio(
    pnl_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """盈亏比 = mean_profit / |mean_loss|（负值取均值后取绝对值）。

    边界：空列表 → None；无盈利 → 0；无亏损 → None（除零）。
    """
    if not pnl_units:
        return None
    profits = [Decimal(p) for p in pnl_units if p > 0]
    losses = [Decimal(p) for p in pnl_units if p < 0]
    if not profits:
        return 0
    if not losses:
        return None  # 无亏损，盈亏比无定义
    mean_p = _mean_dec(profits)
    mean_l = abs(_mean_dec(losses))
    if mean_l == 0:  # pragma: no cover - losses 非空且全为负，abs(mean) > 0
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        pr = mean_p / mean_l
        sf = Decimal(10) ** scale
        return int((pr * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def holding_days_mean(
    holding_days: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """平均持仓天数（返回 scale=2 小数，如 25.5 天 → 2550）。

    设计文档约定 scale=2 用于天数小数（精度到 0.01 天）。
    """
    if not holding_days:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        mean = Decimal(sum(holding_days)) / Decimal(len(holding_days))
        sf = Decimal(10) ** scale
        return int((mean * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def concentration_top3(
    profitable_pnls_desc: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """集中度 = Top3 盈利占总盈利比（复用 stats.concentration.top_k_share 的语义）。

    `profitable_pnls_desc` 必须是**仅盈利回合**的 pnl，已按**降序**排列。
    """
    total = sum(profitable_pnls_desc)
    if total <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        top_sum = sum(profitable_pnls_desc[:3])
        result = Decimal(top_sum) / Decimal(total)
        return int((result * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fee_per_round_mean(
    fee_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """平均每回合费用（scale=2 分，返回实际分值）。"""
    if not fee_units:
        return None
    return sum(fee_units) // len(fee_units)  # 整数平均（scale=2 分是整数）


def expectancy_annual(
    pnl_units: list[int],
    *,
    rounds_per_year: int = A_SHUFFLE_DAYS,  # 默认按 244 回合/年近似
    scale: int = SCALE_RATIO,
) -> int | None:
    """年化期望值 = mean(pnl) × rounds_per_year / 账户净资产。

    简化版（§3.1）：年化期望值 = mean(pnl_units) × rounds_per_year（scale 转换后是比率）。
    **注**：rounds_per_year 默认 A_SHUFFLE_DAYS=244，调用方可根据实际频率调整。

    输出：scale=8 的年化小数（实际金额仍需乘本金）。
    """
    n = len(pnl_units)
    if n == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        mean_pnl = Decimal(sum(pnl_units)) / Decimal(n) / sf
        annual = mean_pnl * Decimal(rounds_per_year)
        return int((annual * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))
