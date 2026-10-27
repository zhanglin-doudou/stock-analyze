"""集中度族（Period2-07）：Gini / CR1 / TopK / HHI。

全部为纯函数、零 I/O、零时钟；输入为标度整数或原始整数权重，输出为标度整数。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_core.stats.numeric import div_round


def top_k_share(
    sorted_desc: list[int],
    total_units: int,
    *,
    k: int = 3,
    scale: int = 8,
) -> int | None:
    """Top-K 份额（降序列表前 k 个之和 / total）。

    `sorted_desc` 必须是**按降序排列**的值（标度整数），`total_units` 是总和（同 scale）。
    total=0 返回 None（结构性无定义）；k<=0 抛 ValueError。
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if total_units == 0:
        return None
    top_sum = sum(sorted_desc[:k])
    return div_round(top_sum, total_units, scale=scale)


def cr1_units(
    sorted_desc: list[int],
    total_units: int,
    *,
    scale: int = 8,
) -> int | None:
    """CR1 = 最大值 / 总和。"""
    if not sorted_desc or total_units == 0:
        return None
    return div_round(sorted_desc[0], total_units, scale=scale)


def gini_units(
    sorted_asc: list[int],
    *,
    scale: int = 8,
) -> int | None:
    """Gini 系数（盈利升序，标度整数出入）。

    公式：`G = (2·Σ i·x_i) / (n·Σx) − (n+1)/n`，其中 x_i 已按升序排列（i=1..n）。
    边界：n<2 或 Σx=0 → None。

    注意：Gini 定义在**非负**值上（如盈利回合 pnl>0），调用方负责过滤负值。
    """
    n = len(sorted_asc)
    if n < 2:
        return None
    total = sum(sorted_asc)
    if total == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        Decimal(10) ** scale
        # 升序遍历；i 从 1 开始（1-indexed）
        weighted_sum = Decimal(0)
        for i, v in enumerate(sorted_asc, start=1):
            weighted_sum += Decimal(i) * Decimal(v)
        total_dec = Decimal(total)
        n_dec = Decimal(n)
        gini = (Decimal(2) * weighted_sum) / (n_dec * total_dec) - (n_dec + Decimal(1)) / n_dec
        quantum = Decimal(1).scaleb(-scale)
        return int(gini.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))


@dataclass(frozen=True)
class HHI:
    """赫芬达尔指数输出（两种口径）。"""

    hhi_total_units: int  # 含现金：(Σmv_i² + cash²) / total_assets²  scale 8
    hhi_eq_units: int | None  # 仅权益：Σmv_i² / (Σmv_i)²   scale 8；Σmv=0 → None


def hhi_indices(
    market_values_units: list[int],
    cash_units: int,
    *,
    scale: int = 8,
) -> HHI:
    """HHI_total 与 HHI_eq。

    所有输入为**同 scale 的金额标度整数**（scale=2 分 或 scale=8 比率元），函数内部统一
    处理。对空持仓日，`hhi_eq_units=None`（调用方决定是否计入均值）。
    """
    sum_mv = sum(market_values_units)
    total_assets = sum_mv + cash_units
    Decimal(10) ** scale

    with localcontext() as ctx:
        ctx.prec = 50
        # Σ mv_i²
        sum_mv_sq = sum(Decimal(m) * Decimal(m) for m in market_values_units)
        # HHI_total = (Σmv² + cash²) / total²
        cash_dec = Decimal(cash_units)
        total_dec = Decimal(total_assets)
        numerator_total = sum_mv_sq + cash_dec * cash_dec
        hhi_total = Decimal(0) if total_dec == 0 else numerator_total / (total_dec * total_dec)
        # HHI_eq = Σmv² / (Σmv)²
        if sum_mv == 0:
            hhi_eq_dec: Decimal | None = None
        else:
            sum_mv_dec = Decimal(sum_mv)
            hhi_eq_dec = sum_mv_sq / (sum_mv_dec * sum_mv_dec)

        quantum = Decimal(1).scaleb(-scale)

        def q(value: Decimal) -> int:
            return int(value.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))

        hhi_total_u = q(hhi_total)
        hhi_eq_u = q(hhi_eq_dec) if hhi_eq_dec is not None else None
        return HHI(hhi_total_units=hhi_total_u, hhi_eq_units=hhi_eq_u)
