"""板块因子金额：单步取整（设计 §3.4/§3.7 D-10）。

`sector = div_round(base × (I_end − I_start), I_start)`——**不**先算 `r_sec` 再乘
（二次取整在 1 亿持仓上可差 50 分）；`r_sec` 仅作展示。
中间量 `base×(I_end−I_start)` 可超 int64，全程 Python int（禁下推 SQL 乘法）。
"""

from __future__ import annotations

from stock_core.stats.numeric import div_round

_RATIO_SCALE = 100_000_000


def sector_amount_units(base_units: int, index_start: int, index_end: int) -> int | None:
    """板块因子金额（分）；指数起点非法（≤0）时返回 ``None``。"""
    if index_start <= 0:
        return None
    return div_round(base_units * (index_end - index_start), index_start)


def sector_return_units(index_start: int, index_end: int) -> int | None:
    """行业指数区间收益率（Ratio scale=8，仅展示用）；起点非法时 ``None``。"""
    if index_start <= 0:
        return None
    return div_round((index_end - index_start) * _RATIO_SCALE, index_start)


__all__ = ["sector_amount_units", "sector_return_units"]
