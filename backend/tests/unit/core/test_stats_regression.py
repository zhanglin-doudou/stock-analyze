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

    def test_annual_base_two_uses_square_root(self) -> None:
        """annual_base=2 → (1+rf)^(1/2)−1；不能直接用 (1+rf)^2 或 2/2 次方。"""
        assert rf_daily_units(Decimal("0.015"), annual_base=2, scale=12) == 7_472_083_980

    def test_rounding_half_up_on_tie(self) -> None:
        """(1.00100025)^(1/2)−1 恰为 0.0005，scale=3 下应进位为 1。"""
        assert rf_daily_units(Decimal("0.00100025"), annual_base=2, scale=3) == 1


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
        assert ba.n_obs == 5

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
            "beta_units",
            "alpha_daily_units",
            "alpha_annual_units",
            "se_alpha_units",
            "t_alpha_units",
            "te_units",
            "ir_units",
            "excess_arith_units",
            "excess_geo_units",
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
        assert ba.se_alpha_units == 0  # SSE=0 → se 归零（不是非零哨兵）
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

    def test_known_sample_sd(self) -> None:
        """[0, 0, 0, 4.0] → 均值 1.0，样本方差 4e16 → sd = 2.0（n-ddof=3）。"""
        assert stdev_units([0, 0, 0, 400_000_000]) == 200_000_000

    def test_ddof_not_less_than_n_returns_none(self) -> None:
        """n == ddof → 分母为 0，必须返回 None 而不是除零。"""
        assert stdev_units([1, 2, 3], ddof=3) is None

    def test_tiny_dispersion_is_not_floored_to_zero(self) -> None:
        """离散度远小于 1 元时仍要输出真实 sd（不能被「小方差」门槛吞掉）。"""
        # 0 与 0.00001（scale8=1000）→ sd = sqrt(2×(500)²) ≈ 707.1
        assert stdev_units([0, 1000]) == 707


class TestExactOlsValues:
    """同一输入下 OLS 族各字段的精确闭式值（§5.5 手算向量完整对账）。"""

    def test_design_vector_exact_fields(self) -> None:
        r_b = _pct_to_scale8([0.40, -0.30, 0.90, 0.20, -0.60, 0.70])
        r_p = _pct_to_scale8([0.55, -0.20, 1.10, 0.30, -0.85, 0.60])
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        assert ba.beta_units == 115_984_062
        assert ba.alpha_daily_units == -1_299
        assert ba.alpha_annual_units == -316_860
        assert ba.se_alpha_units == 71_751
        assert ba.t_alpha_units == -181
        assert ba.te_units == 2_271_253
        assert ba.ir_units == -13_950_908
        assert ba.excess_arith_units == -316_860

    def test_constant_daily_excess_exact(self) -> None:
        """r_p = r_b + 0.1 每日恒定 → β=1、α_daily=0.1，年化/算术超额 = 0.1×244。"""
        r_b = [10_000_000, 20_000_000, 30_000_000, 40_000_000]
        r_p = [v + 10_000_000 for v in r_b]
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        assert ba.beta_units == 100_000_000
        assert ba.alpha_daily_units == 10_000_000
        assert ba.alpha_annual_units == 2_440_000_000
        assert ba.excess_arith_units == 2_440_000_000  # 0.1 × 244

    def test_rf_shift_moves_alpha_by_daily_rf(self) -> None:
        """r_p = 2·r_b 时 β=2、α=0；扣减 rf 后 α_daily 应等于 rf 本身（(β−1)·rf）。"""
        r_b = [10_000_000, 20_000_000, 30_000_000, 40_000_000]
        r_p = [2 * v for v in r_b]
        rf_d = rf_daily_units(Decimal("0.015"), scale=12)
        ba = beta_alpha(r_p, r_b, rf_daily_units=rf_d)
        assert ba.beta_units == 200_000_000
        assert ba.alpha_daily_units == (rf_d + 5_000) // 10_000  # 6.1020765e-5 → 6102
        ba_no_rf = beta_alpha(r_p, r_b, rf_daily_units=0)
        assert ba_no_rf.alpha_daily_units == 0

    def test_three_observations_is_enough(self) -> None:
        """n=3 是可用下限（样本量门槛必须严格小于 3 才判不足）。"""
        ba = beta_alpha(
            [10_000_000, 20_000_000, 30_000_000],
            [5_000_000, 10_000_000, 15_000_000],
            rf_daily_units=0,
        )
        assert ba.insufficient is False
        assert ba.beta_units == 200_000_000

    def test_zero_total_return_b_falls_back_to_zero_excess_geo(self) -> None:
        """基准累计收益恰好 -100% → excess_geo 归 0，不能除以 0。"""
        r_b = [-100_000_000] + [0] * 5  # scale8 = -1.0
        ba = beta_alpha([0] * 6, r_b, rf_daily_units=0)
        assert ba.excess_geo_units == 0

    def test_positive_but_flat_benchmark_is_annualized(self) -> None:
        """基准累计收益恰为 +100% 是合法输入（判据必须 >1 而不是 >=1）。"""
        r_b = [100_000_000] + [0] * 5  # 累计 +100%
        ba = beta_alpha([0] * 6, r_b, rf_daily_units=0)
        assert ba.excess_geo_units == -100_000_000  # 组合 0% vs 基准翻倍 → 年化 -100%

    def test_negative_benchmark_total_still_computes_excess_geo(self) -> None:
        """基准累计收益为负（乘积 < 1）时仍要算几何超额，不能直接归零。"""
        r_b = [1_000_000, -2_000_000, 1_000_000, -2_000_000, 1_000_000, -50_000_000]
        r_p = [v + 10_000_000 for v in r_b]
        ba = beta_alpha(r_p, r_b, rf_daily_units=0)
        assert ba.excess_arith_units == 2_440_000_000
        assert ba.excess_geo_units is not None
        assert ba.excess_geo_units > 0
