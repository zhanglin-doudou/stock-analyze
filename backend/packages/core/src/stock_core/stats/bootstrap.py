"""Bootstrap 百分位置信区间（Period2-05，纯整数 + 可复现随机）。

约定（D-07）：seed 由 `sha256(metric_id|definition_version|inputs_hash)[:8]` 派生，
因此同一输入双跑结果逐位一致；不同 seed 结果不同（用于敏感性测试）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from stock_core.stats.numeric import XorShift64Star, percentile_index

Observation = tuple[int, ...]
Estimator = Callable[[Sequence[Observation]], int]

BOOTSTRAP_B = 1000
BOOTSTRAP_CI_ALPHA_PERMILLE: tuple[int, int] = (25, 974)


def bootstrap_percentile(
    observations: Sequence[Observation],
    stat: Estimator,
    *,
    b: int = BOOTSTRAP_B,
    seed: int = 0,
    alpha_permille: tuple[int, int] = BOOTSTRAP_CI_ALPHA_PERMILLE,
) -> tuple[int, int]:
    """有放回重采样 `b` 次，返回统计量经验分布的 [2.5%, 97.5%] 分位。"""
    if not observations:
        raise ValueError("observations must not be empty")
    if b <= 0:
        raise ValueError("b must be positive")
    sample = tuple(tuple(observation) for observation in observations)
    size = len(sample)
    rng = XorShift64Star(seed)
    stats: list[int] = []
    for _ in range(b):
        resampled = tuple(sample[rng.below(size)] for _ in range(size))
        stats.append(stat(resampled))
    stats.sort()
    low = stats[percentile_index(b, alpha_permille[0])]
    high = stats[percentile_index(b, alpha_permille[1])]
    return low, high
