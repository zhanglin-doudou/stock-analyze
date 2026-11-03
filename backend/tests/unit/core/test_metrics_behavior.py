"""行为域指标单测（Period2-07）。"""

from __future__ import annotations

from stock_core.metrics.behavior import (
    averaging_down_rate,
    decision_efficiency,
    portfolio_concentration_mean,
    turnover_daily_mean,
)


class TestDecisionEfficiency:
    def test_perfect_entry_is_zero(self) -> None:
        # entry == best → ratio 0
        trades = [(100_000_000, 110_000_000, 100_000_000, 120_000_000)]
        assert decision_efficiency(trades) == 0

    def test_worst_entry_is_one(self) -> None:
        trades = [(120_000_000, 110_000_000, 100_000_000, 120_000_000)]
        result = decision_efficiency(trades)
        assert result is not None
        assert result >= 99_000_000  # 接近 1.0

    def test_empty_returns_none(self) -> None:
        assert decision_efficiency([]) is None

    def test_zero_spread_skipped(self) -> None:
        # worst == best → 跳过
        trades = [(100_000_000, 110_000_000, 100_000_000, 100_000_000)]
        assert decision_efficiency(trades) is None


class TestTurnoverDailyMean:
    def test_known_mean(self) -> None:
        # mean(0.05, 0.10, 0.15) = 0.10 → 10_000_000 scale8
        assert turnover_daily_mean([5_000_000, 10_000_000, 15_000_000]) == 10_000_000

    def test_empty_returns_none(self) -> None:
        assert turnover_daily_mean([]) is None


class TestAveragingDownRate:
    def test_all_adds_down(self) -> None:
        # 3 加仓，新价都低于首次均价
        trades = [
            (True, 100_000_000, 90_000_000),  # 100 → 90: 低
            (True, 100_000_000, 95_000_000),  # 低
            (True, 100_000_000, 85_000_000),  # 低
            (False, 100_000_000, 110_000_000),  # 不是加仓，跳过
        ]
        assert averaging_down_rate(trades) == 100_000_000

    def test_no_adds_returns_none(self) -> None:
        trades = [(False, 100_000_000, 110_000_000)]
        assert averaging_down_rate(trades) is None

    def test_empty_returns_none(self) -> None:
        assert averaging_down_rate([]) is None


class TestPortfolioConcentrationMean:
    def test_design_doc_two_days(self) -> None:
        # d1: mv=[6000,3000], cash=1000 → total=10000 → HHI = (6000²+3000²)/10000² = 0.45
        # 等等：hhi_indices 含现金？d1 HHI_total=0.46（见 TestHHI 单测），d2=0.5 → mean=0.48
        states = [
            ([6000, 3000], 1000),
            ([5000, 5000], 0),
        ]
        assert portfolio_concentration_mean(states, scale=8) == 48_000_000

    def test_empty_returns_none(self) -> None:
        assert portfolio_concentration_mean([]) is None

    def test_single_day_is_a_valid_average(self) -> None:
        """只有 1 个有效日也应给出均值，不能当作「无数据」。"""
        assert portfolio_concentration_mean([([100, 0], 0)], scale=8) == 100_000_000

    def test_all_cash_day_contributes_zero(self) -> None:
        """全现金日 HHI_total=0 是有效观测（不能跳过）。"""
        assert portfolio_concentration_mean([([], 0), ([], 1)], scale=0) == 1  # mean 0.5

    def test_scale_parameter_is_forwarded_to_hhi(self) -> None:
        """scale=4 时输出应是 4 位标度，而不是按 8 位算完再乘。"""
        states = [([6000, 3000], 1000), ([5000, 5000], 0)]
        assert portfolio_concentration_mean(states, scale=4) == 4800


class TestBehaviorBoundaries:
    def test_decision_efficiency_averages_ratios(self) -> None:
        """两笔有效率 0 与 0.5 → 均值 0.25（不是比值之和 × 笔数）。"""
        trades = [
            (100_000_000, 100_000_000, 100_000_000, 200_000_000),  # 0
            (150_000_000, 100_000_000, 100_000_000, 200_000_000),  # 0.5
        ]
        assert decision_efficiency(trades) == 25_000_000

    def test_zero_spread_trade_does_not_abort_the_scan(self) -> None:
        """跳过零价差笔后仍要继续统计后续有效笔。"""
        trades = [
            (100_000_000, 100_000_000, 100_000_000, 100_000_000),  # 零价差 → 跳过
            (150_000_000, 100_000_000, 100_000_000, 200_000_000),  # 0.5
        ]
        assert decision_efficiency(trades) == 50_000_000

    def test_decision_efficiency_half_up_on_tie(self) -> None:
        assert (
            decision_efficiency([(150_000_000, 100_000_000, 100_000_000, 200_000_000)], scale=0)
            == 1
        )

    def test_turnover_half_up_on_tie(self) -> None:
        assert turnover_daily_mean([2, 3], scale=0) == 3

    def test_equal_price_is_not_averaging_down(self) -> None:
        """新价与首次均价相等不构成「下跌加仓」。"""
        assert averaging_down_rate([(True, 100_000_000, 100_000_000)]) == 0
