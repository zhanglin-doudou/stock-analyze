"""绩效域指标单测（Period2-07）。"""

from __future__ import annotations

from decimal import Decimal

from stock_core.metrics.perf import (
    _mean_dec,
    concentration_top3,
    expectancy_annual,
    fee_breakeven_rate,
    fee_per_round_mean,
    holding_days_mean,
    pnl_ratio,
    win_rate,
)


class TestWinRate:
    def test_known_ratio(self) -> None:
        # 3 wins / 5 → 0.6 → 60_000_000 scale8
        assert win_rate([100, -50, 200, -30, 150]) == 60_000_000

    def test_all_wins(self) -> None:
        assert win_rate([1, 2, 3]) == 100_000_000

    def test_all_losses(self) -> None:
        assert win_rate([-1, -2, -3]) == 0

    def test_empty_returns_none(self) -> None:
        assert win_rate([]) is None


class TestPnlRatio:
    def test_known_ratio(self) -> None:
        # mean_profit = 450/3 = 150, mean_loss = -80/2 = -40, ratio = 150/40 = 3.75
        assert pnl_ratio([100, -50, 200, -30, 150]) == 375_000_000

    def test_no_profit_returns_zero(self) -> None:
        assert pnl_ratio([-100, -50, -30]) == 0

    def test_no_loss_returns_none(self) -> None:
        assert pnl_ratio([100, 50, 30]) is None

    def test_empty_returns_none(self) -> None:
        assert pnl_ratio([]) is None


class TestHoldingDaysMean:
    def test_known_mean(self) -> None:
        # mean(10, 20, 30, 40) = 25 → scale8 25.0 = 2_500_000_000
        # 但注意 holding_days_mean 的 scale 默认是 8，天数 25 → 25.0 → 250_000_000
        # 让我用 scale=2 测试更合理
        assert holding_days_mean([10, 20, 30, 40], scale=2) == 25_00  # 25.00

    def test_empty_returns_none(self) -> None:
        assert holding_days_mean([]) is None


class TestConcentrationTop3:
    def test_known_top3(self) -> None:
        # top3 = 500, 300, 200; total = 1100; ratio = 1000/1100 ≈ 0.9091
        assert concentration_top3([500, 300, 200, 100], scale=8) == 90_909_091

    def test_empty_returns_none(self) -> None:
        assert concentration_top3([], scale=8) is None


class TestFeePerRoundMean:
    def test_known_mean(self) -> None:
        assert fee_per_round_mean([100, 200, 300]) == 200

    def test_empty_returns_none(self) -> None:
        assert fee_per_round_mean([]) is None


class TestFeeBreakevenRate:
    def test_losing_period_returns_positive(self) -> None:
        # net < 0 → 需要 breakeven
        fbe = fee_breakeven_rate([-100, -50, -30])
        assert fbe is not None
        assert fbe > 0

    def test_profitable_period_returns_none(self) -> None:
        assert fee_breakeven_rate([100, 50, 30]) is None

    def test_empty_returns_none(self) -> None:
        assert fee_breakeven_rate([]) is None


class TestExpectancyAnnual:
    def test_known_annual(self) -> None:
        """L169-174: expectancy_annual 主体逻辑覆盖。"""
        # mean_pnl = (100 + 50 + -30 + 80 + -20) / 5 = 180 / 5 = 36
        # 年化 = 36 × 244 = 8784 (scale=8) → 8_784_000_000？不对，scale 转换
        # expectancy_annual: mean_pnl = sum/n/sf, annual = mean * rounds_per_year, return = annual * sf
        # 所以 return = (sum/n/sf) * rounds_per_year * sf = sum/n * rounds_per_year = mean_pnl * rounds_per_year
        # mean_pnl = 36, rounds_per_year = 244 → 36 * 244 = 8784 scale=8 → 8_784
        result = expectancy_annual([100, 50, -30, 80, -20])
        assert result is not None
        assert result == 8784

    def test_empty_returns_none(self) -> None:
        assert expectancy_annual([]) is None


class TestMeanDec:
    def test_empty_returns_zero(self) -> None:
        """L20: _mean_dec 空输入边界。"""
        assert _mean_dec([]) == Decimal(0)

    def test_non_empty_returns_mean(self) -> None:
        assert _mean_dec([Decimal(10), Decimal(20), Decimal(30)]) == Decimal(20)
