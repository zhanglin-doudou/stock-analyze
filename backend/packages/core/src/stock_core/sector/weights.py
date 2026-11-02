"""板块权重与主动权重（比率 scale=8）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from stock_core.stats.numeric import div_round


def weights_from_values(
    values: Sequence[int],
    total_units: int,
    *,
    scale: int = 8,
) -> list[int]:
    """逐个 `value/total` → 比率标度整数；`total<=0` 抛 `ZeroDivisionError`（调用方先判空）。"""
    if total_units <= 0:
        raise ZeroDivisionError("total must be positive")
    return [div_round(value, total_units, scale=scale) for value in values]


def normalize_weights(
    weights: Mapping[str, int],
    *,
    scale: int = 8,
) -> dict[str, int]:
    """把同 scale 权重归一到 Σ=1；分母为 0 抛 `ZeroDivisionError`。"""
    total = sum(weights.values())
    if total <= 0:
        raise ZeroDivisionError("sum of weights must be positive")
    return {key: div_round(value, total, scale=scale) for key, value in weights.items()}


def active_weights(
    portfolio: Mapping[str, int],
    benchmark: Mapping[str, int],
    *,
    scale: int = 8,
) -> dict[str, int]:
    """主动权重 `w_p - w_b`（并集；缺席一侧按 0）。两侧须同为 scale=8。"""
    del scale  # 同 scale 直接相减，保留参数以对齐签名
    keys = set(portfolio) | set(benchmark)
    return {key: portfolio.get(key, 0) - benchmark.get(key, 0) for key in keys}
