"""板块偏好：行业偏离度 `D = Σ|Δ|/2`。"""

from __future__ import annotations

from collections.abc import Mapping

from stock_core.stats.numeric import div_round


def deviation_units(active: Mapping[str, int]) -> int | None:
    """Σ|主动权重|/2。

    输入是 scale=8 的主动权重标度整数；输出**仍是 scale=8 的比率标度整数**
    （用 `scale=0` 除以 2 只是精确折半，不改变量纲）。
    空映射 → `None`（无板块可比较）。
    """
    if not active:
        return None
    return div_round(sum(abs(value) for value in active.values()), 2, scale=0)
