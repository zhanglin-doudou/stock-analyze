"""整数分摊原语（设计 §3.3/§3.7：禁止逐段独立 floor，尾差给最后一段）。

`allocate(total, weights)` 与 `stock_core.fifo.engine._allocate` 同规则：
按权重比例取整、尾差全部归最后一个元素，保证 `Σ result == total` 精确成立。
"""

from __future__ import annotations

from collections.abc import Sequence


def allocate(total: int, weights: Sequence[int]) -> tuple[int, ...]:
    """按权重把 `total` 分摊为整数序列，尾差归最后一个元素。

    - `weights` 为空或权重和为 0 时返回 ``()``（调用方须先过滤空权重场景）。
    - 负权重无意义：出现时按 0 处理（权重和仍须为正，否则返回 ``()``）。
    """
    if not weights:
        return ()
    total_weight = sum(weights)
    if total_weight <= 0:
        return ()
    shares: list[int] = []
    allocated = 0
    for weight in weights[:-1]:
        share = total * max(weight, 0) // total_weight
        shares.append(share)
        allocated += share
    shares.append(total - allocated)
    return tuple(shares)


__all__ = ["allocate"]
