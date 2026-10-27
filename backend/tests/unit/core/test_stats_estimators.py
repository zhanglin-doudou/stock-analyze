"""统计量注册表单测（Period2-05）：自动发现、注册冲突、重置。"""

from __future__ import annotations

import pytest
from stock_core.stats import estimators
from stock_core.stats.estimators import (
    get_stats,
    register,
    reset_registry_for_tests,
    stats_ids,
)


def test_builtin_mean_is_discovered() -> None:
    assert "mean" in stats_ids()
    assert get_stats("mean")([(100,), (300,), (500,)]) == 300
    with pytest.raises(ValueError):
        get_stats("mean")([])


def test_unknown_stat_raises() -> None:
    with pytest.raises(KeyError):
        get_stats("no_such_stat")


def test_duplicate_registration_is_rejected() -> None:
    with pytest.raises(ValueError, match="already registered"):

        @register("mean")
        def duplicate(samples: list[tuple[int, ...]]) -> int:  # pragma: no cover - 注册即失败
            return 0


def test_reset_restores_builtins() -> None:
    @register("temp_stat")
    def temp(samples: list[tuple[int, ...]]) -> int:  # pragma: no cover - 仅登记
        return 0

    assert "temp_stat" in stats_ids()
    reset_registry_for_tests()
    assert stats_ids() == ("mean", "ratio_diff_pgr_plr")
    assert estimators.mean_stat([(10,), (20,)]) == 15


def test_ratio_diff_pgr_plr_golden() -> None:
    stat = get_stats("ratio_diff_pgr_plr")
    # G=8 已卖盈、L=4 已卖亏、g=1 纸面盈、l=2 纸面亏 → DE = 12/54
    samples = [(0,)] * 8 + [(1,)] * 4 + [(2,)] * 1 + [(3,)] * 2
    assert stat(samples) == 22222222
    assert "ratio_diff_pgr_plr" in stats_ids()


def test_ratio_diff_pgr_plr_degenerate_and_empty() -> None:
    stat = get_stats("ratio_diff_pgr_plr")
    assert stat([(0,), (2,)]) == 0  # 无亏损侧 → 退化 replicate 记 0
    assert stat([(1,), (3,)]) == 0  # 无盈利侧
    with pytest.raises(ValueError):
        stat([])
