"""择时域指标单测（Period2-07）。"""

from __future__ import annotations

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


class TestFlyRatio:
    def test_fly_detected(self) -> None:
        r = fly_ratio([(100_000_000, 105_000_000)], scale=8)
        assert r == 100_000_000

    def test_no_fly(self) -> None:
        r = fly_ratio([(100_000_000, 101_000_000)], scale=8, threshold_units=3_000_000)
        assert r == 0

    def test_empty_returns_none(self) -> None:
        assert fly_ratio([]) is None


class TestTimingBeta:
    def test_beta_matches_regression(self) -> None:
        r_b = [40_000, -30_000, 90_000, 20_000, -60_000, 70_000]
        r_p = [55_000, -20_000, 110_000, 30_000, -85_000, 60_000]
        ba = timing_beta(r_p, r_b)
        assert ba.beta_units is not None
        assert abs(ba.beta_units - 115_984_016) < 1_000


class TestTimingDrawdown:
    def test_returns_drawdown_family(self) -> None:
        returns = [0, 1_000_000, -2_000_000, -3_000_000, 1_000_000, -4_000_000]
        df = timing_drawdown(returns)
        assert df.mdd_units is not None
