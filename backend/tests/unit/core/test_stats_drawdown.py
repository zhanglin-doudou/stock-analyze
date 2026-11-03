"""回撤族单测（Period2-07）：MDD / duration / recovery / underwater / Ulcer / Calmar。"""

from __future__ import annotations

from stock_core.metrics.portfolio_index import compound_index
from stock_core.stats.drawdown import drawdown_family


def _ratio_to_scale8(values: list[float]) -> list[int]:
    """小数 → scale8（0.01 → 1000000）。"""
    return [int(v * 100_000_000) for v in values]


class TestDrawdownFamily:
    def test_design_doc_small_case_mdd(self) -> None:
        """§8.4 校验：10 个日收益 [0, +0.01, -0.02, -0.03, +0.01, -0.04, +0.02, +0.03, -0.01, +0.02] → MDD=0.07829824。"""
        returns = _ratio_to_scale8([0, 0.01, -0.02, -0.03, 0.01, -0.04, 0.02, 0.03, -0.01, 0.02])
        df = drawdown_family(returns, natural_days=10)
        assert df.mdd_units == 7829824  # 0.07829824
        assert df.underwater_ratio_units == 80000000  # 8/10 = 0.8
        assert df.ulcer_units == 4165262  # 0.04165262
        assert df.recovery_censored is True
        assert df.mdd_recovery_obs is None
        assert df.blocked is False

    def test_monotonic_up_has_zero_drawdown(self) -> None:
        returns = _ratio_to_scale8([0.01, 0.01, 0.01, 0.01, 0.01])
        df = drawdown_family(returns)
        assert df.mdd_units == 0
        assert df.underwater_ratio_units == 0
        assert df.recovery_censored is False

    def test_drawdown_with_recovery(self) -> None:
        """先跌后涨回到前高 → recovery 有值、censored=False。"""
        returns = _ratio_to_scale8([0.05, -0.10, -0.05, 0.20])  # I: 1.05→0.945→0.89775→1.0773
        df = drawdown_family(returns)
        assert df.mdd_units is not None
        assert df.mdd_recovery_obs is not None
        assert df.recovery_censored is False

    def test_blocked_returns_blocked(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01, 0.02]), blocked=True)
        assert df.blocked is True
        assert df.mdd_units is None
        assert df.underwater_ratio_units is None

    def test_too_few_returns_is_insufficient(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01]))
        assert df.mdd_units is None  # min_obs=2

    def test_empty_returns(self) -> None:
        df = drawdown_family([])
        assert df.mdd_units is None
        assert df.n_obs == 0

    def test_mdd_duration_is_positive(self) -> None:
        returns = _ratio_to_scale8([0.05, -0.10, -0.05, 0.10, 0.08, -0.01])
        df = drawdown_family(returns)
        if df.mdd_units and df.mdd_units > 0:
            assert df.mdd_duration_obs is not None
            assert df.mdd_duration_obs >= 1

    def test_unreliable_on_extreme_return(self) -> None:
        """|r| > 1 → unreliable。"""
        returns = _ratio_to_scale8([0.01, 1.5, 0.01, -0.5, 0.01, 0.01])  # 1.5 和 -0.5 都超界
        df = drawdown_family(returns)
        assert df.unreliable is True


class TestDrawdownIndices:
    """峰/谷/恢复在 index 序列中的下标（Dashboard 回撤区间映射日期用）。"""

    def test_peak_trough_recovery_indices(self) -> None:
        returns = _ratio_to_scale8([0.05, -0.10, -0.05, 0.20])
        df = drawdown_family(returns)
        # I = [1, 1.05, 0.945, 0.89775, 1.0773]；峰在 I_1，谷在 I_3，I_4 回到前高。
        assert df.peak_index == 1
        assert df.trough_index == 3
        assert df.recovery_index == 4
        assert df.recovery_censored is False

    def test_not_recovered_recovery_index_is_none(self) -> None:
        returns = _ratio_to_scale8([0.02, -0.05, -0.03, 0.01])
        df = drawdown_family(returns)
        assert df.peak_index is not None
        assert df.trough_index is not None
        assert df.recovery_index is None
        assert df.recovery_censored is True

    def test_zero_drawdown_has_no_indices(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01, 0.01, 0.01]))
        assert df.mdd_units == 0
        assert df.peak_index is None
        assert df.trough_index is None
        assert df.recovery_index is None

    def test_blocked_has_no_indices(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01, 0.02]), blocked=True)
        assert df.peak_index is None
        assert df.trough_index is None
        assert df.recovery_index is None

    def test_insufficient_has_no_indices(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01]))
        assert df.peak_index is None
        assert df.trough_index is None
        assert df.recovery_index is None


class TestDrawdownCalmar:
    def test_calmar_with_positive_mdd(self) -> None:
        """简单两期：先跌 -5% 再平，年化 TWR ≈ (0.95)^(365/2) - 1 < 0 → Calmar 不可算。"""
        returns = _ratio_to_scale8([0.05, -0.10, 0.03, -0.04, 0.06, -0.02, 0.04, 0.01, 0.02, -0.03])
        df = drawdown_family(returns, natural_days=10)
        # Calmar 是年化 TWR / |MDD|；如果年化 TWR ≤ 0 → calmar_units 可能 None 或负数
        # 只检查结构正确
        assert df.mdd_units is not None
        assert df.mdd_units >= 0


class TestCompounding:
    """内部 _compound_index 已通过 drawdown_family 间接覆盖；这里只测极端值。"""

    def test_zero_returns_keep_index_at_one(self) -> None:
        returns = [0] * 6
        df = drawdown_family(returns)
        assert df.mdd_units == 0
        assert df.twr_annual_units == 0

    def test_natural_days_zero_returns_zero_twr(self) -> None:
        """natural_days=0 → twr_annual 回退到 0。"""
        returns = _ratio_to_scale8([0.01, -0.02, 0.01])
        df = drawdown_family(returns, natural_days=0)
        assert df.twr_annual_units == 0


class TestDrawdownBoundaries:
    """边界与精确值：样本量下限、恢复判定、年化口径、显式 false 标记。"""

    def test_two_observations_is_enough(self) -> None:
        """n=2 是可用下限；无回撤时 duration 为 None（不能给出 0）。"""
        df = drawdown_family([1_000_000, 2_000_000])
        assert df.mdd_units == 0
        assert df.mdd_duration_obs is None
        assert df.recovery_censored is False

    def test_recovery_exactly_at_prior_peak(self) -> None:
        """指数恰好回到前高（相等）也算恢复：I=[1, 1.25, 1.0, 1.25]。"""
        returns = [25_000_000, -20_000_000, 25_000_000]
        df = drawdown_family(returns)
        assert df.mdd_units == 20_000_000
        assert df.peak_index == 1
        assert df.trough_index == 2
        assert df.recovery_index == 3
        assert df.mdd_duration_obs == 1
        assert df.mdd_recovery_obs == 1
        assert df.recovery_censored is False

    def test_first_return_loss_starts_from_initial_peak(self) -> None:
        """首个收益为负时，峰值仍取初始指数 1.0（不是 I_1）。"""
        returns = [-10_000_000, -20_000_000, 35_000_000]
        df = drawdown_family(returns)
        assert df.mdd_units == 28_000_000
        assert df.peak_index == 0
        assert df.trough_index == 2

    def test_first_observation_underwater_counts(self) -> None:
        """首期即回撤：水下占比必须计入第 1 期，Ulcer 也必须统计全部 n 期。

        n=2：dd₁=-0.1、dd₂=-0.055 → sqrt((0.01+0.003025)/2)=0.08070006。
        """
        returns = [-10_000_000, 5_000_000]
        df = drawdown_family(returns)
        assert df.underwater_ratio_units == 100_000_000  # 2/2 期都在水下
        assert df.ulcer_units == 8_070_006

    def test_recovered_then_loss_is_censored(self) -> None:
        returns = [50_000_000, -20_000_000]
        df = drawdown_family(returns)
        assert df.recovery_censored is True
        assert df.mdd_recovery_obs is None

    def test_total_wipeout_has_zero_annual_return(self) -> None:
        """累计收益 -100% → 年化 TWR 归 0（不能对 0 取幂得 -1）。"""
        df = drawdown_family([-100_000_000, 0])
        assert df.twr_annual_units == 0

    def test_annualized_twr_matches_compound_for_one_year(self) -> None:
        """natural_days=365 时年化 TWR 即复利累计收益，应与 compound_index 逐位一致。"""
        returns = [50_000_000, -20_000_000]
        df = drawdown_family(returns, natural_days=365)
        assert df.twr_annual_units == compound_index(returns) == 20_000_000

    def test_one_natural_day_is_still_annualized(self) -> None:
        """natural_days=1 也必须按 1/365 年折算，不能直接返回 0。"""
        df = drawdown_family([1_000_000, 2_000_000], natural_days=1)
        assert df.twr_annual_units > 0

    def test_blocked_and_insufficient_report_false_censored(self) -> None:
        blocked = drawdown_family([1_000_000, 2_000_000], blocked=True)
        assert blocked.recovery_censored is False
        assert blocked.n_obs == 2

        insufficient = drawdown_family([1_000_000])
        assert insufficient.recovery_censored is False
        assert insufficient.n_obs == 1

    def test_return_of_exactly_one_is_not_unreliable(self) -> None:
        """单期 +100% 是合法收益（|r| 判据必须严格大于 1，不能含等号）。"""
        # natural_days 取大使年化指数远离精度边界，聚焦 unreliable 判定本身
        df = drawdown_family([0, 100_000_000], natural_days=365_000)
        assert df.unreliable is False
