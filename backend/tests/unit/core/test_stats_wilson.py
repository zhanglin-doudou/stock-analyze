"""Wilson 比例 CI 单测（Period2-05）：闭式解、边界与非法输入。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from stock_core.stats.wilson import wilson_interval


def test_wilson_nominal_interval_is_sane() -> None:
    low, high = wilson_interval(7, 12, scale=8)
    assert 0 < low < 700_000_000 // 12
    assert 700_000_000 // 12 < high < 1_000_000_000
    assert low < high


def test_wilson_extremes_clamp_to_unit_interval() -> None:
    low_zero, high_zero = wilson_interval(0, 10, scale=8)
    assert low_zero == 0
    assert 0 < high_zero < 100_000_000

    low_full, high_full = wilson_interval(10, 10, scale=8)
    assert 0 < low_full < 100_000_000
    assert high_full == 100_000_000


def test_wilson_is_scale_aware() -> None:
    low, high = wilson_interval(3, 10, scale=4)
    assert 0 <= low <= 10_000
    assert 0 <= high <= 10_000
    assert len(str(low)) <= 5


def test_wilson_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        wilson_interval(1, 0)
    with pytest.raises(ValueError):
        wilson_interval(0, 0)  # n=0 且成功数也为 0 同样非法（不能进入 0/0）
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_wilson_zero_z_collapses_to_point_estimate() -> None:
    """z=0 时区间退化为点估计本身；1/8 = 0.125 在 scale=2 下按进位到 0.13。"""
    assert wilson_interval(1, 8, z=Decimal(0), scale=2) == (13, 13)


def test_wilson_single_observation() -> None:
    """n=1 是合法输入（样本量门槛必须 n>1 才拒绝）。"""
    low, high = wilson_interval(1, 1, scale=8)
    assert low == 20_654_931
    assert high == 100_000_000


def test_wilson_huge_n_does_not_floor_upper_bound() -> None:
    """0/1e10 的上界远小于 1 个最小刻度，量化后应如实为 0（不能强行抬到 1）。"""
    assert wilson_interval(0, 10**10, scale=8) == (0, 0)
