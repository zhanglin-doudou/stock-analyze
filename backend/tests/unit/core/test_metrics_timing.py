"""择时域指标单测（Period2-07）。"""

from __future__ import annotations

from decimal import Decimal

from stock_core.metrics.timing import (
    chase_ratio,
    fly_ratio,
    timing_beta,
    timing_drawdown,
)


class TestChaseRatio:
    def test_chase_detected(self) -> None:
        # scale=8, threshold=3% (300000)
        # entry=1.00 (1e8), post_max=1.05 (1.05e8) → 追涨
        r = chase_ratio([(100_000_000, 105_000_000)], scale=8)
        assert r == 100_000_000  # 1/1

    def test_no_chase(self) -> None:
        r = chase_ratio([(100_000_000, 101_000_000)], scale=8, threshold_units=3_000_000)
        assert r == 0

    def test_empty_returns_none(self) -> None:
        assert chase_ratio([]) is None

    def test_threshold_equality_is_not_chase(self) -> None:
        """最高价恰好等于阈值线时不算追涨（严格大于）。"""
        assert chase_ratio([(100_000_000, 100_000_000)], threshold_units=0, scale=8) == 0

    def test_each_round_is_counted_once(self) -> None:
        """两回合都超过阈值 → 2/2，不能只记 1 次。"""
        trades = [(100_000_000, 200_000_000), (200_000_000, 400_000_000)]
        assert chase_ratio(trades, scale=8) == 100_000_000


class TestFlyRatio:
    def test_fly_detected(self) -> None:
        r = fly_ratio([(100_000_000, 105_000_000)], scale=8)
        assert r == 100_000_000

    def test_no_fly(self) -> None:
        r = fly_ratio([(100_000_000, 101_000_000)], scale=8, threshold_units=3_000_000)
        assert r == 0

    def test_empty_returns_none(self) -> None:
        assert fly_ratio([]) is None

    def test_threshold_equality_is_not_fly(self) -> None:
        """最高价恰好等于阈值线时不算杀跌（严格大于）。"""
        assert fly_ratio([(100_000_000, 100_000_000)], threshold_units=0, scale=8) == 0

    def test_each_round_is_counted_once(self) -> None:
        """两回合都超过阈值 → 2/2，不能只记 1 次。"""
        trades = [(100_000_000, 200_000_000), (200_000_000, 400_000_000)]
        assert fly_ratio(trades, scale=8) == 100_000_000


class TestTimingBeta:
    def test_beta_matches_regression(self) -> None:
        r_b = [40_000, -30_000, 90_000, 20_000, -60_000, 70_000]
        r_p = [55_000, -20_000, 110_000, 30_000, -85_000, 60_000]
        ba = timing_beta(r_p, r_b)
        assert ba.beta_units is not None
        assert abs(ba.beta_units - 115_984_016) < 1_000

    def test_rf_annual_is_deducted_from_excess(self) -> None:
        """β≠1 时无风险利率必须改变 α：α_d = c + (β−1)·rf_d。

        y = 2x + 0.1 → β=2（精确）；rf_annual=5% 时 α_d = 0.1 + rf_d。
        """
        rf = Decimal("0.05")
        r_b = [4_000_000, -3_000_000, 9_000_000, 2_000_000, -6_000_000, 7_000_000]
        r_p = [2 * v + 10_000_000 for v in r_b]
        ba = timing_beta(r_p, r_b, rf_annual=rf)
        assert ba.beta_units == 200_000_000
        assert ba.alpha_daily_units == 10_019_998

    def test_scale_parameter_is_forwarded(self) -> None:
        """scale=4 的输入/输出必须是 scale=4（β=2 → 20000）。"""
        r_b = [400, -300, 900, 200, -600, 700]
        r_p = [2 * v + 1000 for v in r_b]
        assert timing_beta(r_p, r_b, scale=4).beta_units == 20_000


class TestTimingDrawdown:
    def test_returns_drawdown_family(self) -> None:
        returns = [0, 1_000_000, -2_000_000, -3_000_000, 1_000_000, -4_000_000]
        df = timing_drawdown(returns)
        assert df.mdd_units is not None

    def test_blocked_flag_is_passed_through(self) -> None:
        """blocked=True 必须透传到 drawdown_family（不能丢参数或改成 None）。"""
        df = timing_drawdown([1_000_000, 2_000_000], blocked=True)
        assert df.blocked is True
        assert df.mdd_duration_obs is None
