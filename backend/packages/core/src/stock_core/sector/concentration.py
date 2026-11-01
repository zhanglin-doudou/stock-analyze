"""行业集中度（互斥分区口径，分母 `E_map`）。"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.stats.numeric import div_round


def sector_hhi_from_values(
    values: Sequence[int],
    total_units: int,
    *,
    scale: int = 8,
) -> int | None:
    """HHI = Σv² / total²（精确整数式，避免二次舍入）；`total<=0` → `None`。"""
    if total_units <= 0:
        return None
    sum_sq = sum(value * value for value in values)
    return div_round(sum_sq, total_units * total_units, scale=scale)


def topk_weights(
    values: Sequence[int],
    total_units: int,
    *,
    k: int = 3,
    scale: int = 8,
) -> int | None:
    """Top-K 份额（降序前 k 之和 / total）；`total<=0` → `None`，`k<=0` 抛 `ValueError`。"""
    if k <= 0:
        raise ValueError("k must be positive")
    if total_units <= 0:
        return None
    ordered = sorted(values, reverse=True)
    return div_round(sum(ordered[:k]), total_units, scale=scale)


def effective_n_units(hhi_units: int, *, scale: int = 8) -> int | None:
    """有效板块数 `1/HHI`（比率 scale=8）；HHI<=0 → `None`。

    `hhi_units` 是 scale=8 的标度整数，故 `1/HHI * 10^scale = 10^(2scale) / hhi_units`，
    这里用 `scale=0` 的整数除法（精确折半后即为目标标度整数，避免再放大 10^scale）。
    """
    if hhi_units <= 0:
        return None
    return div_round(10 ** (2 * scale), hhi_units, scale=0)
