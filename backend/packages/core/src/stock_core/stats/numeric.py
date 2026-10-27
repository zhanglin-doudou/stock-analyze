"""整数域统计基元（Period2-05，E-05 最小子集）。

为什么不用 float：CI 上下界与分位数会进入 `metric_result` 的标度整数列，
一旦用 float 就与全仓「禁止 float」的架构约束冲突，且随机重采样需要**逐位可复现**。

约定：
- 输入输出都是标度整数（scale = 10**precision）；
- 随机数用 xorshift64*（纯整数、跨平台逐位一致）；
- 所有除法四舍五入（half-up）到目标 scale。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import isqrt

MASK64 = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15


def div_round(numer: int, denom: int, *, scale: int = 0) -> int:
    """`numer / denom` 四舍五入到 `scale` 位小数（标度整数）。

    `scale=0` 时等价于对整数商四舍五入；符号按数学商处理（远离零取整）。
    """
    if denom == 0:
        raise ZeroDivisionError("division by zero")
    if scale < 0:
        raise ValueError("scale must be >= 0")
    sign = -1 if (numer < 0) != (denom < 0) else 1
    quotient, remainder = divmod(abs(numer) * 10**scale, abs(denom))
    if remainder * 2 >= abs(denom):
        quotient += 1
    return int(sign * quotient)


def sqrt_units(value_units: int, *, in_scale: int, out_scale: int) -> int:
    """`sqrt(value_units / 10**in_scale) * 10**out_scale` 的整数近似（向下取整）。

    比 out_scale 更细的部分会被截断（`isqrt` 天然向下取整），因此结果恒不为负。
    """
    if value_units < 0:
        raise ValueError("sqrt of negative value")
    shift = 2 * out_scale - in_scale
    if shift >= 0:
        return isqrt(value_units * 10**shift)
    return isqrt(value_units // 10 ** (-shift))


def median_units(values: Sequence[int]) -> int:
    """中位数（标度整数）；偶数个时对中间两数四舍五入。"""
    if not values:
        raise ValueError("median of empty sequence")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return div_round(ordered[middle - 1] + ordered[middle], 2)


def percentile_index(size: int, q_permille: int) -> int:
    """最近秩（nearest-rank）分位点下标，`q_permille` ∈ [0, 1000]。"""
    if size <= 0:
        raise ValueError("size must be positive")
    if not 0 <= q_permille <= 1000:
        raise ValueError("q_permille must be within [0, 1000]")
    rank = (q_permille * size + 999) // 1000
    return min(max(rank - 1, 0), size - 1)


class XorShift64Star:
    """纯整数 xorshift64* PRNG：同 seed 跨平台逐位可复现。"""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        state = seed & MASK64
        self._state = state or _GOLDEN_GAMMA

    def next_u64(self) -> int:
        state = self._state
        state ^= (state << 13) & MASK64
        state ^= state >> 7
        state ^= (state << 17) & MASK64
        self._state = state & MASK64
        return (self._state * 0x2545F4914F6CDD1D) & MASK64

    def below(self, upper: int) -> int:
        """`[0, upper)` 均匀整数（拒绝采样消除取模偏置）。"""
        if upper <= 0:
            raise ValueError("upper must be positive")
        limit = (1 << 64) - ((1 << 64) % upper)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % upper


def xorshift64_star(seed: int) -> Callable[[], int]:
    """返回无参数生成器（按需取下一个 u64），便于在 estimator 内部使用。"""
    rng = XorShift64Star(seed)
    return rng.next_u64
