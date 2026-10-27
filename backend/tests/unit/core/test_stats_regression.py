"""OLS 回归族单测（Period2-07）：β/α/IR/TE + 边界。

校验：§5.5 示例 r_b=[0.0040,−0.0030,0.0090,0.0020,−0.0060,0.0070]、r_p=[0.0055,−0.0020,0.0110,0.0030,−0.0085,0.0060]
→ β=1.15984016（rf_a=0 时精确），OLS 闭式公式确定。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from stock_contracts.constants import RF_ANNUAL_DEFAULT
from stock_core.stats.regression import (
    beta_alpha,
    rf_daily_units,
    stdev_units,
)


def _pct_to_scale8(values: list[float]) -> list[int]:
    """百分比 → scale8（0.40% → 小数 0.0040 → 40000）。"""
    return [int(v / 100 * 100_000_000) for v in values]


class TestRfDailyUnits:
    def test_rf_daily_is_positive_for_positive_rf(self) -> None:
        rf = rf_daily_units(RF_ANNUAL_DEFAULT)
        assert rf > 0

    def test_rf_daily_zero_rate_returns_zero(self) -> None:
        rf = rf_daily_units(Decimal(0))
        assert rf == 0

    def test_rf_daily_is_scale_aware(self) -> None:
        rf10 = rf_daily_units(Decimal("0.015"), scale=10)
        rf12 = rf_daily_units(Decimal("0.015"), scale=12)
        assert rf10 < rf12  # scale 更大，数值更大


class TestBetaAlpha:
    def test_perfect_regression_matches_design_doc_beta(self) -> None:
        """§5.5 beta 校验：rf_a=0 时 beta 不依赖 rf，精确匹配 OLS 闭式。"""
        r_b = _pct_to_scale8([0.40, -0.30, 0.90, 0.20, -0.60, 0.70])
        r_p = _pct_to_scale8([0.55, -0.20, 1.10, 0.30, -0.85, 0.60])
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        # beta = 1.15984016 → scale8 = 115984016；允许±1000 舍入差异
        assert ba.beta_units is not None
        assert abs(ba.beta_units - 115_984_016) < 1_000

    def test_beta_with_positive_rf_still_matches(self) -> None:
        """β 对 rf 扣减不敏感（两组序列同减常数，协方差不变）。"""
        r_b = _pct_to_scale8([0.40, -0.30, 0.90, 0.20, -0.60, 0.70])
        r_p = _pct_to_scale8([0.55, -0.20, 1.10, 0.30, -0.85, 0.60])
        rf_d = rf_daily_units(RF_ANNUAL_DEFAULT, scale=12)
        ba = beta_alpha(r_p, r_b, rf_daily_units=rf_d)
        assert ba.beta_units is not None
        # beta 应该和 rf=0 时几乎一样（rf_d ≈ 6e-5 很小）
        assert abs(ba.beta_units - 115984015) < 100  # 差异 < 1e-6

    def test_short_series_is_insufficient(self) -> None:
        ba = beta_alpha([100, 200], [50, 150])
        assert ba.insufficient is True
        assert ba.beta_units is None
        assert ba.n_obs == 2

    def test_sxx_zero_is_blocked(self) -> None:
        """基准恒定 → Sxx=0 → blocked。"""
        r_b = [100_000_000] * 5  # 全部 1.00000000
        r_p = [50_000_000, 60_000_000, 55_000_000, 58_000_000, 52_000_000]
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        assert ba.blocked is True
        assert ba.beta_units is None

    def test_mismatched_lengths_raises(self) -> None:
        with pytest.raises(ValueError):
            beta_alpha([1, 2, 3], [1, 2])

    def test_excess_geo_matches_design_doc(self) -> None:
        """excess_geo 校验：§5.5 期望 ≈ 0.08302747 → scale8 = 8302747。"""
        r_b = _pct_to_scale8([0.40, -0.30, 0.90, 0.20, -0.60, 0.70])
        r_p = _pct_to_scale8([0.55, -0.20, 1.10, 0.30, -0.85, 0.60])
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        assert ba.excess_geo_units is not None
        expected = 8_302_747  # 0.08302747 scale8
        assert abs(ba.excess_geo_units - expected) <= 1000  # 允许舍入差异

    def test_all_values_present_for_normal_case(self) -> None:
        r_b = _pct_to_scale8([0.40, -0.30, 0.90, 0.20, -0.60, 0.70])
        r_p = _pct_to_scale8([0.55, -0.20, 1.10, 0.30, -0.85, 0.60])
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        for field in (
            "beta_units", "alpha_daily_units", "alpha_annual_units",
            "se_alpha_units", "t_alpha_units", "te_units",
            "ir_units", "excess_arith_units", "excess_geo_units",
        ):
            assert getattr(ba, field) is not None, f"{field} should be present"
        assert ba.insufficient is False
        assert ba.blocked is False
        assert ba.unreliable is False

    def test_perfect_fit_is_not_blocked(self) -> None:
        """完全相关的两组序列 → SSE=0 被哨兵替代、se=0→t=0。"""
        y = [100, 200, 300, 400, 500, 600]
        x = [50, 100, 150, 200, 250, 300]  # x = y/2，完美相关
        ba = beta_alpha(y, x, rf_daily_units=0)
        assert ba.blocked is False
        assert ba.beta_units is not None  # beta=2.0
        assert ba.se_alpha_units is not None  # 哨兵值 1e-28 scale8
        assert ba.t_alpha_units == 0  # se=0 → t=0

    def test_zero_variance_excess_is_zero_ir(self) -> None:
        """excess 全相同 → var_excess=0 被哨兵替代、stdev=0→ir=0。"""
        y = [0, 100, 200, 300, 400, 500]
        x = y  # beta=1, alpha=0
        ba = beta_alpha(y, x, rf_daily_units=0)
        assert ba.ir_units == 0  # stdev_excess=0

    def test_negative_total_return_b(self) -> None:
        """total_return_b ≈ 0 → excess_geo 回退到 0。"""
        # 要让 Π(1+xt) ≈ 0 需要累计非常负，这里不现实；
        # 但我们可以测 6 个序列都是 -0.20（20% 日跌 → 累计 (0.8)^6 ≈ 0.262）
        # 不会 ≤0。用一个极端：x 序列包含 r=-1.0（-100%）
        x = [-1_000_000_000] + [0] * 5  # 第一个日收益 -1.0
        y = [0] * 6
        ba = beta_alpha(y, x, rf_daily_units=0)
        # 不应 crash；excess_geo 处理 total_return_b ≈ 0
        assert ba.excess_geo_units is not None


class TestStdevUnits:
    def test_sd_of_constant_is_zero(self) -> None:
        assert stdev_units([1000, 1000, 1000]) == 0

    def test_too_short_returns_none(self) -> None:
        assert stdev_units([1000]) is None
        assert stdev_units([]) is None

    def test_positive_dispersion(self) -> None:
        sd = stdev_units([0, 1000_000_000])  # 0 和 1.0
        assert sd is not None
        assert sd > 0
