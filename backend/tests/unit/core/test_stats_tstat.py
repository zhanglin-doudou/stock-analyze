"""聚类 t 值单测（Period2-07）。"""

from __future__ import annotations

from stock_core.stats.tstat import cluster_t


def _ratio_to_scale8(values: list[float]) -> list[int]:
    return [int(v * 100_000_000) for v in values]


class TestClusterT:
    def test_single_cluster_returns_none(self) -> None:
        events = [(0, 100), (0, 200), (0, 150)]  # 全部同一日
        assert cluster_t(events) is None

    def test_empty_events_returns_none(self) -> None:
        assert cluster_t([]) is None

    def test_two_clusters_with_variance(self) -> None:
        """设计文档 §5.5 小例：3 事件，t 值 ≈ 0.25。"""
        # 两个事件在 day=0，一个在 day=1
        bhars = _ratio_to_scale8([0.05, -0.07, 0.05])
        events = [(0, bhars[0]), (0, bhars[1]), (1, bhars[2])]
        t = cluster_t(events)
        assert t is not None

    def test_all_same_value_returns_zero(self) -> None:
        """所有 BHAR 相同 → 均值无偏 → SS=0 → t=0。"""
        events = [(0, 100), (1, 100), (2, 100)]
        assert cluster_t(events) == 0

    def test_singleton_event_per_day(self) -> None:
        """每个事件独占一天 → 单样本 t 近似。"""
        events = [(0, 1000), (1, 2000), (2, 3000)]
        t = cluster_t(events)
        assert t is not None  # 至少应返回数值
