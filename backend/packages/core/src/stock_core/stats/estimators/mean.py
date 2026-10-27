"""内置统计量 `mean`：多观测型指标的默认统计量（取每条观测首元素的均值）。"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.stats.estimators import register
from stock_core.stats.numeric import div_round


@register("mean")
def mean_stat(samples: Sequence[tuple[int, ...]]) -> int:
    """多观测样本首元素的算术平均（标度整数，四舍五入）。"""
    if not samples:
        raise ValueError("samples must not be empty")
    total = sum(observation[0] for observation in samples)
    return div_round(total, len(samples))
