"""CI 出口：estimator 派发 + wilson/bootstrap 封装（E-15/D-07）。

- `SamplePolicy.ci` 决定算法；`MetricValue.ci_stat_id` 指向 `stock_core.stats` 的 estimator；
- `n < ci_min_n` → 不出 CI；
- bootstrap 的 seed 由 `(metric_id, definition_version, inputs_hash)` 派生，逐位可复现；
- CI 标度与出口 `precision` 一致（ratio=8 → 1e8）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.stats import (
    Observation,
    bootstrap_percentile,
    get_stats,
    wilson_interval,
)

from stock_review.metrics.base import MetricDef, MetricValue
from stock_review.metrics.errors import RegistryError
from stock_review.metrics.hash import derive_seed

CI_MIN_N = 10


def _wilson(definition: MetricDef, observations: Sequence[Observation]) -> tuple[int, int]:
    for observation in observations:
        if len(observation) != 1 or observation[0] not in (0, 1):
            raise RegistryError(
                f"{definition.id}: wilson 观测必须是单元素 0/1 元组，实际 {observation!r}"
            )
    success = sum(observation[0] for observation in observations)
    return wilson_interval(success, len(observations), scale=definition.precision)


def compute_ci(
    definition: MetricDef,
    value: MetricValue,
    *,
    inputs_hash: str,
) -> tuple[int | None, int | None]:
    policy = definition.sample_policy
    if policy.ci == "none":
        return None, None
    samples = value.ci_sample
    if samples is None or len(samples) < max(policy.ci_min_n, CI_MIN_N):
        return None, None

    if policy.ci == "wilson":
        return _wilson(definition, samples)

    stat_id = value.ci_stat_id
    if stat_id is None:
        raise RegistryError(f"{definition.id}: ci=bootstrap 必须提供 ci_stat_id")
    try:
        estimator = get_stats(stat_id)
    except KeyError as exc:
        raise RegistryError(f"{definition.id}: estimator 未注册: {stat_id}") from exc

    ordered = tuple(sorted(samples))
    seed = derive_seed(definition.id, definition.definition_version, inputs_hash)
    return bootstrap_percentile(ordered, estimator, seed=seed)


__all__ = ["CI_MIN_N", "compute_ci"]
