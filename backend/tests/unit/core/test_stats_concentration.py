"""集中度族单测（Period2-07）：TopK / CR1 / Gini / HHI。"""

from __future__ import annotations

import pytest
from stock_core.stats.concentration import (
    cr1_units,
    gini_units,
    hhi_indices,
    top_k_share,
)

SCALE = 8
SF = 10**8


class TestTopKShare:
    def test_top2_of_known_sum(self) -> None:
        """sum=800, total=1000 → 0.8 → 80_000_000 scale8。"""
        assert top_k_share([500, 300, 200, 100], 1000, k=2, scale=SCALE) == 80_000_000

    def test_total_zero_returns_none(self) -> None:
        assert top_k_share([100, 50], 0, scale=SCALE) is None

    def test_k_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            top_k_share([1, 2], 100, k=0, scale=SCALE)

    def test_k_larger_than_list(self) -> None:
        """k=100 但只有 3 个元素 → 取全部 → 1.0。"""
        assert top_k_share([100, 200, 300], 600, k=100, scale=SCALE) == SF


class TestCR1:
    def test_cr1_equals_max_over_total(self) -> None:
        assert cr1_units([500, 300, 200], 1000, scale=SCALE) == 50_000_000

    def test_empty_or_zero_total(self) -> None:
        assert cr1_units([], 1000, scale=SCALE) is None
        assert cr1_units([100], 0, scale=SCALE) is None


class TestGini:
    def test_perfect_equality_is_zero(self) -> None:
        assert gini_units([100, 100, 100, 100], scale=SCALE) == 0

    def test_unequal_distribution_is_positive(self) -> None:
        """[100,100,100,400] → G ≈ 0.3214。"""
        g = gini_units([100, 100, 100, 400], scale=SCALE)
        assert g is not None
        assert 30_000_000 <= g <= 35_000_000

    def test_single_or_empty_returns_none(self) -> None:
        assert gini_units([100], scale=SCALE) is None
        assert gini_units([], scale=SCALE) is None

    def test_zero_sum_returns_none(self) -> None:
        assert gini_units([0, 0, 0], scale=SCALE) is None

    def test_gini_is_in_unit_interval(self) -> None:
        g = gini_units([100, 200, 300, 400, 500], scale=SCALE)
        assert g is not None
        assert 0 <= g <= SF


class TestHHI:
    def test_design_doc_two_day_example(self) -> None:
        """§8.4 精确值：d1 mv=[6000,3000], cash=1000 → HHI_total=0.46, HHI_eq≈0.5556。"""
        hhi = hhi_indices([6000, 3000], 1000, scale=8)
        assert hhi.hhi_total_units == 46_000_000
        assert hhi.hhi_eq_units is not None
        assert abs(hhi.hhi_eq_units - 55_555_556) <= 1

    def test_all_cash(self) -> None:
        hhi = hhi_indices([], 10_000, scale=8)
        assert hhi.hhi_total_units == 100_000_000  # 1.0
        assert hhi.hhi_eq_units is None

    def test_single_position(self) -> None:
        hhi = hhi_indices([5000], 5000, scale=8)
        assert hhi.hhi_eq_units == 100_000_000

    def test_zero_assets(self) -> None:
        hhi = hhi_indices([], 0, scale=8)
        assert hhi.hhi_total_units == 0
