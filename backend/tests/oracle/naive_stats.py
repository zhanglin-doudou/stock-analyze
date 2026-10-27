"""独立 oracle：统计基元（§8.5 naive_stats）。

铁律：只 import 标准库；**禁止** import stock_core/stock_review/stock_ledger；
公式独立重写（不复用生产代码），同一算法规格的独立实现。

口径备忘：
- PRNG 用 xorshift64*（与 D-07「xorshift/SplitMix64 纯整数」规格一致；§8.4 小例的
  SplitMix64 字样属文档漂移——CI 三方比对容差为精确/1bp，PRNG 必须与引擎同规格，
  否则 CNY 0.01 容差下恒失败）；
- Wilson 用 ``Decimal.sqrt``；bootstrap B=1000、percentile 0-based 索引 25/974；
- seed = ``int(sha256("metric_id|version|inputs_hash")[:16], 16)``。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

MASK64 = (1 << 64) - 1
GOLDEN_GAMMA = 0x9E3779B97F4A7C15
BOOTSTRAP_B = 1000
CI_ALPHA_PERMILLE = (25, 974)


def div_round(numer: int, denom: int, scale: int = 0) -> int:
    if denom == 0:
        raise ZeroDivisionError("division by zero")
    sign = -1 if (numer < 0) != (denom < 0) else 1
    quotient, remainder = divmod(abs(numer) * 10**scale, abs(denom))
    if remainder * 2 >= abs(denom):
        quotient += 1
    return sign * quotient


class XorShift64Star:
    """xorshift64* 独立实现（与生产同规格，逐位一致）。"""

    def __init__(self, seed: int) -> None:
        self._state = (seed & MASK64) or GOLDEN_GAMMA

    def next_u64(self) -> int:
        state = self._state
        state ^= (state << 13) & MASK64
        state ^= state >> 7
        state ^= (state << 17) & MASK64
        self._state = state & MASK64
        return (self._state * 0x2545F4914F6CDD1D) & MASK64

    def below(self, upper: int) -> int:
        limit = (1 << 64) - ((1 << 64) % upper)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % upper


def derive_seed(metric_id: str, definition_version: str, inputs_hash: str) -> int:
    digest = hashlib.sha256(f"{metric_id}|{definition_version}|{inputs_hash}".encode()).hexdigest()
    return int(digest[:16], 16)


def wilson_interval(success: int, n: int, *, scale: int = 8) -> tuple[int, int]:
    """Wilson 95% CI（z=1.959963984540054）；Decimal.sqrt 独立推导。"""
    if n <= 0:
        raise ValueError("n must be positive")
    with localcontext() as ctx:
        ctx.prec = 60
        z = Decimal("1.959963984540054")
        phat = Decimal(success) / Decimal(n)
        z2 = z * z
        denom = Decimal(1) + z2 / Decimal(n)
        center = phat + z2 / Decimal(2 * n)
        margin = z * ((phat * (1 - phat) + z2 / Decimal(4 * n)) / Decimal(n)).sqrt()
        half = margin / denom
        mid = center / denom
        sf = Decimal(10) ** scale
        low = int(((mid - half) * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        high = int(((mid + half) * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return low, high


def percentile_index(size: int, q_permille: int) -> int:
    rank = (q_permille * size + 999) // 1000
    return min(max(rank - 1, 0), size - 1)


def bootstrap_percentile(
    observations: Sequence[tuple[int, ...]],
    stat: Callable[[Sequence[tuple[int, ...]]], int],
    *,
    b: int = BOOTSTRAP_B,
    seed: int = 0,
) -> tuple[int, int]:
    if not observations:
        raise ValueError("observations must not be empty")
    sample = tuple(tuple(item) for item in observations)
    size = len(sample)
    rng = XorShift64Star(seed)
    stats: list[int] = []
    for _ in range(b):
        resampled = tuple(sample[rng.below(size)] for _ in range(size))
        stats.append(stat(resampled))
    stats.sort()
    return stats[percentile_index(b, CI_ALPHA_PERMILLE[0])], stats[
        percentile_index(b, CI_ALPHA_PERMILLE[1])
    ]


def mean_stat(samples: Sequence[tuple[int, ...]]) -> int:
    """`mean` 统计量（观测首元素均值，scale=0 四舍五入）。"""
    if not samples:
        raise ValueError("samples must not be empty")
    return div_round(sum(item[0] for item in samples), len(samples))


def ratio_diff_pgr_plr_stat(samples: Sequence[tuple[int, ...]]) -> int:
    """`ratio_diff_pgr_plr` 统计量（0=已卖盈/1=已卖亏/2=纸面盈/3=纸面亏 → DE scale=8）。"""
    if not samples:
        raise ValueError("samples must not be empty")
    sold_gain = sum(1 for item in samples if item[0] == 0)
    sold_loss = sum(1 for item in samples if item[0] == 1)
    paper_gain = sum(1 for item in samples if item[0] == 2)
    paper_loss = sum(1 for item in samples if item[0] == 3)
    gain_total = sold_gain + paper_gain
    loss_total = sold_loss + paper_loss
    if gain_total == 0 or loss_total == 0:
        return 0
    numerator = sold_gain * loss_total - sold_loss * gain_total
    return div_round(numerator, gain_total * loss_total, scale=8)


__all__ = [
    "BOOTSTRAP_B",
    "XorShift64Star",
    "bootstrap_percentile",
    "derive_seed",
    "div_round",
    "mean_stat",
    "percentile_index",
    "ratio_diff_pgr_plr_stat",
    "wilson_interval",
]
