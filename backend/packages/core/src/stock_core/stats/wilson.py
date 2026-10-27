"""Wilson 比例置信区间（Period2-05，闭式精确解，无随机）。

适用：胜率、兑现率等 Bernoulli 型指标（`ci="wilson"`）。
输出与输入同为**标度整数**（默认 1e-8），不做 float 运算。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import SCALE_RATIO, Z_95


def wilson_interval(
    success: int,
    n: int,
    *,
    z: Decimal = Z_95,
    scale: int = SCALE_RATIO,
) -> tuple[int, int]:
    """返回 `(ci_low_units, ci_high_units)`，闭区间 [0, 10**scale]。"""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= success <= n:
        raise ValueError("success must be within [0, n]")
    with localcontext() as ctx:
        ctx.prec = 50
        proportion = Decimal(success) / Decimal(n)
        nn = Decimal(n)
        z2 = z * z
        denominator = Decimal(1) + z2 / nn
        center = (proportion + z2 / (Decimal(2) * nn)) / denominator
        variance = proportion * (Decimal(1) - proportion) / nn + z2 / (Decimal(4) * nn * nn)
        half = z * variance.sqrt() / denominator
        quantum = Decimal(1).scaleb(-scale)
        low = (center - half).quantize(quantum, rounding=ROUND_HALF_UP)
        high = (center + half).quantize(quantum, rounding=ROUND_HALF_UP)
    bound = 10**scale
    low_units = min(max(int(low.scaleb(scale)), 0), bound)
    high_units = min(max(int(high.scaleb(scale)), 0), bound)
    return low_units, high_units
