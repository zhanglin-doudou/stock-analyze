"""因子排序与比率（设计 §3.7 D-08 / §3.7 取整）。

- `ratio = amount / total_pnl`（贡献率，可超 ±100%、可负；`total_pnl==0` → ``None``）。
- `share = |amount| / Σ|amount|`（构成比 0..1，用于条形宽度）。
- `|total_pnl| < max(100 分, 0.5%×Σ|amount|)` → ratio 整组置空 + `PNL_NEAR_ZERO`。
- `rank` 按 `|amount|` 降序，tie-break 固定因子序；`residual` 不参与排序。

⚠️ 不得复用 `portfolio.ratio_units`（分母 ≤0 返回 ``None`` 会吞掉负 total_pnl）；
`ratio_of` 允许负分母并按数学符号取整。
"""

from __future__ import annotations

from collections.abc import Mapping

from stock_core.attribution.types import FACTOR_CODES, FactorCode
from stock_core.stats.numeric import div_round

RATIO_SCALE = 100_000_000
# PNL_NEAR_ZERO 阈值（§3.7）：|total_pnl| < max(100 分, 0.5%×Σ|amount|) → ratio=null。
PNL_NEAR_ZERO_MIN_UNITS = 100
PNL_NEAR_ZERO_SHARE_NUM = 1
PNL_NEAR_ZERO_SHARE_DEN = 200

RATIO_NA_PNL_NEAR_ZERO = "PNL_NEAR_ZERO"


def ratio_of(numer: int, denom: int) -> int | None:
    """`numer/denom`（Ratio scale=8，half-up、按数学符号）；`denom==0` → ``None``。"""
    if denom == 0:
        return None
    return div_round(numer * RATIO_SCALE, denom)


def share_of(amount_units: int, total_abs_units: int) -> int | None:
    """`|amount| / Σ|amount|`（Ratio scale=8）；分母为 0 → ``None``。"""
    if total_abs_units == 0:
        return None
    return div_round(abs(amount_units) * RATIO_SCALE, total_abs_units)


def shares_of(amounts: Mapping[FactorCode, int]) -> dict[FactorCode, int | None]:
    """5 因子构成比，**Σshare == 1e8 精确**（§3.7/D-08；验收④）。

    逐项 half-up 后的尾差（|diff| ≤ 2）补给 `|amount|` 最大的因子
    （tie-break 固定因子序，与 `rank_factors` 一致）；`Σ|amount| == 0` → 全 ``None``。
    """
    total_abs = total_abs_amounts(amounts)
    if total_abs == 0:
        return dict.fromkeys(amounts)
    shares: dict[FactorCode, int | None] = {
        code: div_round(abs(amount) * RATIO_SCALE, total_abs) for code, amount in amounts.items()
    }
    diff = RATIO_SCALE - sum(v for v in shares.values() if v is not None)
    if diff != 0:
        order = {code: index for index, code in enumerate(FACTOR_CODES)}
        target = min(amounts, key=lambda code: (-abs(amounts[code]), order[code]))
        shares[target] += diff  # type: ignore[operator]
    return shares


def total_abs_amounts(factors: Mapping[FactorCode, int]) -> int:
    """Σ|因子金额|（share 分母；仅 5 因子，不含 residual）。"""
    return sum(abs(amount) for amount in factors.values())


def ratio_na_reason(total_pnl_units: int, total_abs: int) -> str | None:
    """`total_pnl` 近零判定；返回 ``None`` 表示 ratio 可给。"""
    threshold = max(
        PNL_NEAR_ZERO_MIN_UNITS,
        div_round(total_abs * PNL_NEAR_ZERO_SHARE_NUM, PNL_NEAR_ZERO_SHARE_DEN),
    )
    if abs(total_pnl_units) < threshold:
        return RATIO_NA_PNL_NEAR_ZERO
    return None


def rank_factors(factors: Mapping[FactorCode, int]) -> tuple[FactorCode, ...]:
    """按 |amount| 降序返回因子序（tie-break 固定因子序）；rank = 位置 + 1。"""
    order = {code: index for index, code in enumerate(FACTOR_CODES)}
    return tuple(
        sorted(factors, key=lambda code: (-abs(factors[code]), order[code])),
    )


__all__ = [
    "PNL_NEAR_ZERO_MIN_UNITS",
    "RATIO_NA_PNL_NEAR_ZERO",
    "RATIO_SCALE",
    "rank_factors",
    "ratio_na_reason",
    "ratio_of",
    "share_of",
    "shares_of",
    "total_abs_amounts",
]
