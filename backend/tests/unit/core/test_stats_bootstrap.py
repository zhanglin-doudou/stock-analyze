"""Bootstrap 百分位 CI 单测（Period2-05）：可复现随机与输入校验。"""

from __future__ import annotations

import pytest
from stock_core.stats.bootstrap import bootstrap_percentile
from stock_core.stats.estimators import get_stats


def _mean(samples: list[tuple[int, ...]]) -> int:
    return get_stats("mean")(samples)


def test_bootstrap_is_reproducible_for_same_seed() -> None:
    observations = [(10,), (20,), (30,), (40,), (50,)]
    first = bootstrap_percentile(observations, _mean, b=200, seed=42)
    second = bootstrap_percentile(observations, _mean, b=200, seed=42)
    assert first == second
    assert first[0] <= first[1]


def test_bootstrap_differs_for_different_seed() -> None:
    observations = [(10,), (20,), (30,), (40,), (50,)]
    left = bootstrap_percentile(observations, _mean, b=200, seed=1)
    right = bootstrap_percentile(observations, _mean, b=200, seed=2)
    assert left != right


def test_bootstrap_supports_pair_observations() -> None:
    observations = [(100, 200), (300, 400), (500, 600)]

    def first_element(samples: list[tuple[int, ...]]) -> int:
        return sum(observation[0] for observation in samples) // len(samples)

    low, high = bootstrap_percentile(observations, first_element, b=100, seed=7)
    assert 100 <= low <= high <= 500


def test_bootstrap_rejects_empty_and_bad_b() -> None:
    with pytest.raises(ValueError):
        bootstrap_percentile([], _mean)
    with pytest.raises(ValueError):
        bootstrap_percentile([(1,)], _mean, b=0)
