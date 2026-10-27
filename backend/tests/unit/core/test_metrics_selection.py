"""选股域指标单测（Period2-07）。"""

from __future__ import annotations

from stock_core.metrics.selection import (
    bhar_cluster_t,
    bhar_hit_rate,
    bhar_mean,
)


class TestBharMean:
    def test_known_mean(self) -> None:
        # mean(0.05, -0.03, 0.01) = 0.01 → 1_000_000 scale8
        vals = [5_000_000, -3_000_000, 1_000_000]
        assert bhar_mean(vals, scale=8) == 1_000_000

    def test_empty_returns_none(self) -> None:
        assert bhar_mean([]) is None


class TestBharHitRate:
    def test_known_rate(self) -> None:
        # 2 of 3 positive → 0.6667 → 66_666_667
        vals = [5_000_000, -3_000_000, 1_000_000]
        assert bhar_hit_rate(vals, scale=8) == 66_666_667

    def test_all_positive(self) -> None:
        assert bhar_hit_rate([1, 2, 3], scale=8) == 100_000_000

    def test_all_negative(self) -> None:
        assert bhar_hit_rate([-1, -2, -3], scale=8) == 0

    def test_empty_returns_none(self) -> None:
        assert bhar_hit_rate([]) is None

    def test_custom_threshold(self) -> None:
        # threshold > 0，只有 > threshold 的才算 hit
        vals = [5_000_000, 4_000_000, 3_000_000]
        # threshold = 4_500_000 → 只有 5_000_000 算 hit → 1/3
        result = bhar_hit_rate(vals, threshold_units=4_500_000, scale=8)
        assert result == 33_333_333


class TestBharClusterT:
    def test_two_events_same_day(self) -> None:
        events = [(0, 5_000_000), (0, -3_000_000), (1, 1_000_000)]
        t = bhar_cluster_t(events, scale=8)
        assert t is not None

    def test_single_cluster_returns_none(self) -> None:
        events = [(0, 5_000_000), (0, -3_000_000)]
        assert bhar_cluster_t(events, scale=8) is None

    def test_empty_returns_none(self) -> None:
        assert bhar_cluster_t([]) is None
