"""Pf-10 纯函数 microbench（§8.10）：18 个 MetricDef.compute 的中位数 ≤2× 基线。

基线 `perf/baseline/core.json` 由发布 PR 人工审批更新；PR-full 告警、release 超 2×
阻断。只测 `compute` 纯函数，不落库、不构造夹具（夹具在计时外构建一次）。
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from stock_review.metrics.context import MetricContextData
from stock_review.metrics.registry import build_registry

from golden.test_metrics_golden import FIXTURE_BUILDERS

pytestmark = pytest.mark.perf

BASELINE = Path(__file__).resolve().parent / "baseline" / "core.json"
REPEATS = 9
MAX_RATIO = 2.0
# 多数纯函数耗时在 µs 级，2× 相对阈值会被调度噪声放大；绝对 0.5ms 内不阻断。
FLOOR_MS = 0.5

# 每个指标选一份代表性夹具（与 golden 同源，但不比对数值）。
FIXTURE_FOR_METRIC: dict[str, str] = {
    "perf.win_rate": "fx_metrics_core",
    "perf.pnl_ratio": "fx_metrics_core",
    "perf.holding_days": "fx_metrics_core",
    "perf.concentration": "fx_metrics_core",
    "perf.fee": "fx_metrics_core",
    "perf.expectancy": "fx_metrics_core",
    "perf.fee_breakeven": "fx_fee_breakeven",
    "behavior.de": "fx_metrics_core",
    "behavior.turnover": "fx_turnover",
    "behavior.averaging_down": "fx_multilot",
    "behavior.hhi": "fx_hhi_main",
    "timing.chase": "fx_bars_chase",
    "timing.beta": "fx_beta",
    "timing.drawdown": "fx_dd_chain_60",
    "timing.fly": "fx_bars_fly",
    "selection.bhar_20": "fx_bhar20",
    "selection.bhar_60": "fx_bhar60",
    "selection.bhar_hit": "fx_bhar20",
}

_REGISTRY = build_registry()
_CASES = [
    (metric_id, FIXTURE_BUILDERS[fixture_id])
    for metric_id, fixture_id in sorted(FIXTURE_FOR_METRIC.items())
]


def measure(compute: Callable[[], object]) -> float:
    """中位数耗时（ms）；先跑一次预热，避免首轮 import/属性缓存干扰。"""
    compute()
    samples = []
    for _ in range(REPEATS):
        started = time.perf_counter_ns()
        compute()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return statistics.median(samples)


def _seed_baseline() -> dict[str, float]:
    values: dict[str, float] = {}
    for metric_id, builder in _CASES:
        definition = _REGISTRY.require(metric_id)
        ctx = builder()
        values[metric_id] = round(measure(lambda d=definition, c=ctx: d.compute(c)), 6)
    return values


@pytest.mark.parametrize("metric_id,builder", _CASES, ids=[case[0] for case in _CASES])
def test_compute_within_2x_baseline(
    metric_id: str, builder: Callable[[], MetricContextData]
) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]
    assert metric_id in baseline, f"{metric_id}: 基线缺失，需审批更新 {BASELINE}"
    definition = _REGISTRY.require(metric_id)
    ctx = builder()
    median_ms = measure(lambda: definition.compute(ctx))
    allowed = max(baseline[metric_id] * MAX_RATIO, FLOOR_MS)
    assert median_ms <= allowed, (
        f"{metric_id}: 中位数 {median_ms:.3f}ms > max(2× 基线 {baseline[metric_id]:.3f}ms, "
        f"{FLOOR_MS}ms)"
    )
