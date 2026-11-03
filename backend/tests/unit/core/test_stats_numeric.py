"""数值基元单测（Period2-05）：整数除法/开方/中位数/分位点/PRNG。"""

from __future__ import annotations

import pytest
from stock_core.stats.numeric import (
    XorShift64Star,
    div_round,
    median_units,
    percentile_index,
    sqrt_units,
    xorshift64_star,
)


def test_div_round_half_up_and_sign() -> None:
    assert div_round(5, 2) == 3
    assert div_round(-5, 2) == -3
    assert div_round(5, -2) == -3
    assert div_round(-5, -2) == 3
    assert div_round(4, 3) == 1
    assert div_round(1, 3, scale=2) == 33
    assert div_round(2, 3, scale=2) == 67
    assert div_round(1, 3, scale=0) == 0


def test_div_round_rejects_invalid_inputs() -> None:
    with pytest.raises(ZeroDivisionError):
        div_round(1, 0)
    with pytest.raises(ValueError):
        div_round(1, 2, scale=-1)


def test_sqrt_units_both_shifts() -> None:
    # in_scale=0,out_scale=0：sqrt(16)=4
    assert sqrt_units(16, in_scale=0, out_scale=0) == 4
    # shift = 2*4 - 8 = 0：sqrt(1e8 / 1e8)=1 → 1e4
    assert sqrt_units(100_000_000, in_scale=8, out_scale=4) == 10_000
    # shift < 0：out_scale 更细，需先整除
    assert sqrt_units(100, in_scale=4, out_scale=0) == 0
    assert sqrt_units(100, in_scale=2, out_scale=0) == 1
    # shift > 0：先放大再开方；sqrt(16)×10 = 40
    assert sqrt_units(16, in_scale=0, out_scale=1) == 40
    # sqrt(0) = 0 是合法边界（不能被当作负值拒绝）
    assert sqrt_units(0, in_scale=0, out_scale=0) == 0
    with pytest.raises(ValueError):
        sqrt_units(-1, in_scale=0, out_scale=0)


def test_median_units_odd_even_and_empty() -> None:
    assert median_units([3, 1, 2]) == 2
    assert median_units([1, 2, 3, 4]) == 3  # (2+3)/2 四舍五入
    assert median_units([-4, -1]) == -3  # (-1 + -4)/2 = -2.5 → 远离零
    with pytest.raises(ValueError):
        median_units([])


def test_percentile_index_bounds_and_validation() -> None:
    assert percentile_index(10, 0) == 0
    assert percentile_index(10, 250) == 2
    assert percentile_index(10, 1000) == 9
    assert percentile_index(1, 500) == 0
    # ceil(q·n/1000) 的进位边界：7×143‰=1001‰ → 排名 2（索引 1）
    assert percentile_index(7, 143) == 1
    with pytest.raises(ValueError):
        percentile_index(0, 500)
    with pytest.raises(ValueError):
        percentile_index(10, -1)
    with pytest.raises(ValueError):
        percentile_index(10, 1001)


def test_xorshift_first_value_is_stable() -> None:
    """PRNG 输出是跨版本契约（bootstrap 可复现性依赖它），固定 seed=42 first value。"""
    assert XorShift64Star(42).next_u64() == 620_241_905_386_665_794


def test_below_one_always_returns_zero() -> None:
    """上界为 1 时唯一合法值就是 0（不能因 upper<=1 抛错）。"""
    assert XorShift64Star(7).below(1) == 0


def test_xorshift_is_deterministic_and_seed_zero_is_remapped() -> None:
    first = XorShift64Star(42)
    second = XorShift64Star(42)
    assert [first.next_u64() for _ in range(5)] == [second.next_u64() for _ in range(5)]
    # seed=0 → 黄金比例常量，避免全零状态卡死
    assert XorShift64Star(0).next_u64() == XorShift64Star(0x9E3779B97F4A7C15).next_u64()


def test_below_rejects_non_positive_and_stays_in_range() -> None:
    rng = XorShift64Star(7)
    with pytest.raises(ValueError):
        rng.below(0)
    values = [rng.below(10) for _ in range(200)]
    assert all(0 <= value < 10 for value in values)
    assert len(set(values)) > 5


def test_xorshift64_star_returns_callable() -> None:
    left = xorshift64_star(123)
    right = xorshift64_star(123)
    assert [left() for _ in range(4)] == [right() for _ in range(4)]
