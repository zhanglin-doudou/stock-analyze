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
        # d1: mv=[6000,3000], cash=1000 → HHI_total=0.46
        # d2: mv=[5000,5000], cash=0 → total=10000, HHI_total = (25_000_000+25_000_000)/100_000_000 = 0.5
        # mean = (0.46+0.5)/2 = 0.48
        states = [
            ([6000, 3000], 1000),
            ([5000, 5000], 0),
        ]
        result = portfolio_concentration_mean(states, scale=8)
        assert result is not None
        # HHI_total 用 scale=8 → 但这里数值太小，内部 Decimal 计算
        # 等等，hhi_indices(6000, 3000, 1000) 的 HHI_total = 0.46 (scale 输出 8 → 46_000_000)
        # mean(46_000_000, 50_000_000) = 48_000_000 → 0.48
        assert abs(result - 48_000_000) <= 1_000_000

    def test_empty_returns_none(self) -> None:
        assert portfolio_concentration_mean([]) is None
