"""Wilson 比例 CI 单测（Period2-05）：闭式解、边界与非法输入。"""

from __future__ import annotations

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
        wilson_interval(-1, 10)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)
